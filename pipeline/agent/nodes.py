"""LangGraph node functions for the CreditFlow decision workflow.

Each node is a callable ``(state: CreditState) -> dict`` that returns a
*partial* state — LangGraph merges the dict into the running state.  This
keeps nodes pure and independently testable (spec §10: reproducible pipeline).

Workflow topology (user argument #2 — the user's architecture):

    START → load_application → fetch_gateways → validate_input → risk_model
            → financial_engineering → fraud_model → policy_engine → explain
            → decision
            ↳ REJECT  → audit → END
            ↳ APPROVE → execute → audit → END
            ↳ REVIEW  → human_approval ──► [approve] → execute → audit → END
                                      └────► [reject] → audit → END

12 nodes: load_application, fetch_gateways (CIC + bank-statement),
validate_input, risk_model, financial_engineering (Basel/pricing/amortization),
fraud_model, policy_engine, explain, decision, human_approval, execute, audit.

Key discipline: **the LLM (explain node) never decides; it explains.**
Policy + cost-tuned ML threshold decide; a human only confirms REVIEW.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
import time
from typing import Any, Callable

from langgraph.types import interrupt

from pipeline.agent.state import (
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REVIEW,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    AUDIT_FAILURE,
    AUDIT_SUCCESS,
    AuditEntry,
    CreditState,
)
from pipeline.agent.fraud import run_fraud_check
from pipeline.agent.policy import run_policy_check, has_blocking_violation
from pipeline.agent.explanations import generate_explanation, explanation_to_text
from pipeline.agent.retriever import retrieve
from pipeline.validation.schemas import validate_dataframe

from pipeline.gateways.cic_gateway import query_cic_report, cross_validate_with_cic
from pipeline.gateways.statement_parser import analyze_bank_statement
from pipeline.financial.credit_risk import calculate_basel_metrics, calculate_risk_based_pricing, generate_amortization_schedule
from pipeline.disbursement.core_banking import determine_authority_level, generate_vietqr_disbursement, generate_loan_agreement

from pipeline.modeling.threshold import (
    business_decision,
    DEFAULT_APPROVE_MAX,
    DEFAULT_REVIEW_MAX,
)


# ---------------------------------------------------------------------------
# Audit helpers
# ---------------------------------------------------------------------------
def _audit_entry(step: str, status: str, detail: str = "") -> AuditEntry:
    return AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        step=step,
        status=status,
        detail=detail,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Node 1: load_application
# ---------------------------------------------------------------------------
def load_application(state: CreditState) -> dict:
    """Initialiser — assign IDs and store the raw customer payload."""
    data = state.get("customer_data", {})
    if not data:
        # Allow the caller to pre-set customer_data; otherwise this is an error
        # handled by validate_input.
        return {
            "application_id": str(uuid.uuid4()),
            "audit_id": f"AUDIT-{uuid.uuid4().hex[:12]}",
            "audit_trail": [_audit_entry("load_application", AUDIT_FAILURE, "no customer_data provided")],
        }

    application_id = state.get("application_id") or f"APP-{uuid.uuid4().hex[:12]}"
    audit_id = state.get("audit_id") or f"AUDIT-{uuid.uuid4().hex[:12]}"
    return {
        "application_id": application_id,
        "audit_id": audit_id,
        "audit_trail": [_audit_entry("load_application", AUDIT_SUCCESS, f"application {application_id} received")],
    }


# ---------------------------------------------------------------------------
# Node 2: validate_input
# ---------------------------------------------------------------------------
def make_validate_input() -> Callable:
    """Factory — returns a validate_input closure.

    Kept as a factory so the node can be unit-tested with a custom validator
    (e.g. mock) without touching the real schema.
    """
    def validate_input(state: CreditState) -> dict:
        from pipeline.validation.schemas import (
            validate_dataframe,
            validate_money_unit_contract,
        )
        import pandas as pd

        data: dict = state.get("customer_data", {})
        df = pd.DataFrame([data])
        _, violations = validate_dataframe(df)
        # Money-unit contract: a payload that cannot be VND (e.g. training-scale
        # numbers) must be rejected with a clear message, never silently
        # rescaled by a magnitude guess.
        violations = violations + validate_money_unit_contract(data)

        updates: dict = {}
        trail = state.get("audit_trail", []) + []
        if violations:
            updates["error"] = f"validation_failed: {'; '.join(violations)}"
            updates["audit_trail"] = trail + [_audit_entry(
                "validate_input", AUDIT_FAILURE,
                "violations: " + "; ".join(violations),
            )]
            # Hard validation failure → skip to REJECT path
            updates["decision"] = DECISION_REJECT
            updates["next"] = "reject"
        else:
            updates["audit_trail"] = trail + [_audit_entry(
                "validate_input", AUDIT_SUCCESS, "schema passed",
            )]
        return updates

    return validate_input


def validate_input(state: CreditState) -> dict:
    """Default validate_input node (uses canonical schema)."""
    return make_validate_input()(state)


# ---------------------------------------------------------------------------
# Node 3: risk_model — delegates to the production ML pipeline
# ---------------------------------------------------------------------------
def make_risk_model(pipeline: Any, meta: dict) -> Callable:
    """Create a risk_model node bound to the loaded production pipeline + meta.

    *pipeline* / *meta* come from ``predict_service.load_production_model()``
    (loaded once at app startup, reused across runs — no per-request reload).
    """

    def risk_model(state: CreditState) -> dict:
        from backend.predict_service import _build_reasons
        import pandas as pd
        from pipeline.feature_engineering.features import add_derived_features

        data: dict = state.get("customer_data", {})

        # --- featurize (same units path as predict_service.predict_risk) ---
        from backend.predict_service import to_model_units
        try:
            df = pd.DataFrame([to_model_units(data, meta)])
        except ValueError as exc:
            # Unit-contract / artifact-contract failure → controlled REJECT with
            # an explicit message (never a silent mis-scaled score).
            return {
                "error": str(exc),
                "decision": DECISION_REJECT,
                "next": "reject",
                "audit_trail": state.get("audit_trail", []) + [_audit_entry(
                    "risk_model", AUDIT_FAILURE, f"unit/artifact contract: {exc}",
                )],
            }
        _, violations = validate_dataframe(df)
        if violations:
            # validate_input should have caught this, but guard anyway
            return {
                "error": f"validation_failed: {'; '.join(violations)}",
                "decision": DECISION_REJECT,
                "next": "reject",
                "audit_trail": state.get("audit_trail", []) + [_audit_entry(
                    "risk_model", AUDIT_FAILURE, "validation failure in risk_model",
                )],
            }

        fe, _flags = add_derived_features(df)
        feature_cols = [c for c in fe.columns if c != "default"]
        proba = float(pipeline.predict_proba(fe[feature_cols])[0, 1])

        # --- risk level (display buckets: LOW/MEDIUM/HIGH by 0.5/0.8) ---
        if proba < DEFAULT_APPROVE_MAX:
            risk_level = RISK_LOW
        elif proba < DEFAULT_REVIEW_MAX:
            risk_level = RISK_MEDIUM
        else:
            risk_level = RISK_HIGH

        # --- reasons (rule-based from engineered features) ---
        reasons = _build_reasons(fe.iloc[0])

        # --- derived features for explanation ---
        derived = {}
        for col in ("debt_to_income", "loan_to_income", "debt_to_loan",
                     "employment_stability", "credit_history_year_ratio"):
            if col in fe.columns:
                val = fe.iloc[0][col]
                derived[col] = float(val) if val is not None and not pd.isna(val) else None

        return {
            "risk_score": round(proba, 4),
            "risk_level": risk_level,
            "reasons": reasons,
            "derived_features": derived,
            "model_name": meta.get("model_name", "unknown"),
            "model_version": meta.get("version", "unknown"),
            "tuned_threshold": float(meta.get("threshold", DEFAULT_APPROVE_MAX)),
            "business_cost": {
                "fn_cost": meta.get("fn_cost", 5.0),
                "fp_cost": meta.get("fp_cost", 1.0),
            },
            "audit_trail": state.get("audit_trail", []) + [_audit_entry(
                "risk_model", AUDIT_SUCCESS,
                f"P(default)={proba:.4f}, level={risk_level}, model={meta.get('model_name','?')}",
            )],
        }

    return risk_model


# ---------------------------------------------------------------------------
# Node 4: fraud_model
# ---------------------------------------------------------------------------
def fraud_model(state: CreditState) -> dict:
    """Run rule-based fraud checks and attach score + flags."""
    updates = run_fraud_check(state)
    trail = state.get("audit_trail", []) + [_audit_entry(
        "fraud_model", AUDIT_SUCCESS,
        f"fraud_score={updates['fraud_score']:.4f}, flags={updates['fraud_flags']}",
    )]
    updates["audit_trail"] = trail
    return updates


# ---------------------------------------------------------------------------
# Node 5: policy_engine
# ---------------------------------------------------------------------------
def policy_engine(state: CreditState) -> dict:
    """Run business policy rules and attach violations."""
    updates = run_policy_check(state)
    trail = state.get("audit_trail", []) + [_audit_entry(
        "policy_engine", AUDIT_SUCCESS,
        f"violations={updates['policy_violations']}",
    )]
    updates["audit_trail"] = trail
    return updates



# ---------------------------------------------------------------------------
# Node 5.1: data_gateways (CIC & Bank Statement)
# ---------------------------------------------------------------------------
def fetch_gateways(state: CreditState) -> dict:
    """Call external data gateways (CIC, Bank Statement parser)."""
    import os as _os
    if _os.environ.get("CREDITFLOW_ENV", "development").lower() == "production":
        raise RuntimeError("GATEWAY_UNAVAILABLE: simulated gateways disabled in production")
    updates: dict = {}
    trail = state.get("audit_trail", []) + []
    
    national_id = state.get("national_id", state.get("customer_data", {}).get("national_id", "001002003004"))
    cic_report = query_cic_report(national_id)
    _cic_dump = cic_report.model_dump()
    _cic_dump["source_mode"] = "SIMULATION"
    updates["cic_report"] = _cic_dump
    trail.append(_audit_entry("gateways", AUDIT_SUCCESS, f"CIC report fetched, score={cic_report.cic_score}"))
    
    income = state.get("customer_data", {}).get("income", 50000)
    raw_statement_text = f"Salary deposit: {income}\nExpense: 5000"
    parsed = analyze_bank_statement(declared_income=income)
    _stmt_dump = parsed.model_dump()
    _stmt_dump["source_mode"] = "SIMULATION"
    updates["bank_statement"] = _stmt_dump
    trail.append(_audit_entry("gateways", AUDIT_SUCCESS, "Bank statement parsed"))
    
    updates["audit_trail"] = trail
    return updates

# ---------------------------------------------------------------------------
# Node 5.2: financial_engineering (Basel & Pricing)
# ---------------------------------------------------------------------------
def financial_engineering(state: CreditState) -> dict:
    """Calculate Basel II/III metrics, Pricing and Amortization."""
    updates: dict = {}
    trail = state.get("audit_trail", []) + []
    
    customer_data = state.get("customer_data", {})
    loan_amount = state.get("loan_amount", customer_data.get("loan_amount", 10000))
    loan_tenure_months = state.get("loan_tenure_months", customer_data.get("loan_term", 36))
    collateral_value = state.get("collateral_value", customer_data.get("collateral_value", 0.0))
    collateral_type = state.get("collateral_type", customer_data.get("collateral_type", "NONE"))
    
    risk_score = state.get("risk_score", 0.05)
    
    basel = calculate_basel_metrics(
        pd=risk_score,
        loan_amount=loan_amount,
        collateral_type=collateral_type,
        collateral_value=collateral_value,
        committed_undrawn=0.0
    )
    updates["basel_metrics"] = basel
    trail.append(_audit_entry("financial", AUDIT_SUCCESS, "Basel metrics computed"))
    
    pricing = calculate_risk_based_pricing(
        pd=risk_score,
        expected_loss=basel["expected_loss"],
        loan_amount=loan_amount
    )
    updates["pricing"] = pricing
    
    amort = generate_amortization_schedule(
        loan_amount=loan_amount,
        annual_rate=pricing["recommended_annual_rate"],
        term_months=loan_tenure_months
    )
    updates["amortization_schedule"] = amort
    
    updates["audit_trail"] = trail
    return updates

# ---------------------------------------------------------------------------
# Node 6: explain (LangChain / LLM layer — explanations only, never decides)
# ---------------------------------------------------------------------------
def explain(state: CreditState) -> dict:
    """Generate a structured LLM explanation (LangChain, with template fallback)."""
    query = " ".join([
        " ".join(state.get("reasons", []) or []),
        state.get("risk_level", ""),
        " ".join(state.get("fraud_flags", []) or []),
        " ".join(state.get("policy_violations", []) or []),
    ])
    rag_hits = retrieve(query, k=2)
    rag_context = "\n".join(hit.get("chunk", "") for hit in rag_hits)
    rag_sources = [hit.get("doc_id") for hit in rag_hits]

    ctx_state = {**state, "rag_context": rag_context, "rag_sources": rag_sources}
    t0 = time.perf_counter()
    expl = generate_explanation(ctx_state)
    latency = (time.perf_counter() - t0) * 1000

    txt = explanation_to_text(expl)
    risk_factors = state.get("reasons", []) or []
    meta = {
        "source": "langchain_llm" if _llm_was_used(expl) else "template",
        "risk_factors_count": len(risk_factors),
        "latency_ms": round(latency, 2),
        "llm_model": expl.get("llm_model", ""),
        "prompt_version": expl.get("prompt_version", ""),
        "rag_sources": rag_sources,
    }
    return {
        "rag_context": rag_context,
        "rag_sources": rag_sources,
        "explanation": txt,
        "explanation_meta": meta,
        "audit_trail": state.get("audit_trail", []) + [_audit_entry(
            "explain", AUDIT_SUCCESS, f"explanation generated (source={meta['source']})",
        )],
    }


def _llm_was_used(expl: dict) -> bool:
    """Heuristic: if confidence is set and there are risk factors, assume LLM
    was used — but in fallback mode we can't truly know.  We check a marker."""
    return expl.get("_llm", False)


# ---------------------------------------------------------------------------
# Node 7: decision — combines ML risk, fraud, policy → final decision
# ---------------------------------------------------------------------------
def make_decision_node() -> Callable:
    """Factory so tests can override the decision logic if needed."""

    def decision(state: CreditState) -> dict:
        trail = list(state.get("audit_trail", []))
        updates: dict = {}

        # --- hard validation failure already set decision=REJECT ---
        if state.get("error") or state.get("next") == "reject":
            updates["decision"] = DECISION_REJECT
            updates["approval_required"] = False
            updates["audit_trail"] = trail + [_audit_entry(
                "decision", AUDIT_SUCCESS, "REJECT (input validation failure)",
            )]
            return updates

        # --- policy block override ---
        if has_blocking_violation(state.get("policy_violations", [])):
            updates["decision"] = DECISION_REJECT
            updates["approval_required"] = False
            updates["audit_trail"] = trail + [_audit_entry(
                "decision", AUDIT_SUCCESS,
                "REJECT (blocking policy violation)",
            )]
            return updates

        # --- fraud override (high fraud score → escalate to REVIEW even if ML low) ---
        fraud_score = state.get("fraud_score", 0.0)
        risk_score = state.get("risk_score", 0.0)
        tuned_threshold = state.get("tuned_threshold", DEFAULT_APPROVE_MAX)

        # Base decision from cost-tuned threshold (Phase 4 logic)
        base_decision = business_decision(risk_score, tuned_threshold, DEFAULT_REVIEW_MAX)

        # Fraud escalation: high fraud_score bumps anything to REVIEW (not direct REJECT)
        if fraud_score >= 0.5 and base_decision == DECISION_APPROVE:
            base_decision = DECISION_REVIEW

        # High risk + fraud flags → REVIEW (give human a chance)
        if risk_score >= DEFAULT_REVIEW_MAX and fraud_score > 0:
            base_decision = DECISION_REVIEW

        # Determine authority level based on the ML risk level + loan size.
        # determine_authority_level(loan_amount, risk_level, ...) returns a
        # detail dict — store the role string on authority_level (API contract)
        # plus the full matrix detail for audit.
        risk_level = state.get("risk_level", RISK_LOW)
        policy_violations = state.get("policy_violations", [])
        loan_amount_auth = state.get("loan_amount", state.get("customer_data", {}).get("loan_amount", 10000))
        cic_group = (state.get("cic_report", {}) or {}).get("bad_debt_group", 1)
        authority = determine_authority_level(
            loan_amount_auth,
            risk_level,
            has_policy_violations=has_blocking_violation(policy_violations),
            is_cic_clean=(cic_group or 1) < 3,
        )
        updates["authority_level"] = authority.get("role", "SYSTEM_STP")
        updates["authority_detail"] = authority

        # Override decision if Risk Committee is required
        if authority.get("role") == "RISK_COMMITTEE_L2":
            base_decision = DECISION_REVIEW

        updates["decision"] = base_decision
        updates["approval_required"] = (base_decision == DECISION_REVIEW)

        trail = trail + [_audit_entry(
            "decision", AUDIT_SUCCESS,
            f"decision={base_decision}, approval_required={updates['approval_required']}",
        )]
        updates["audit_trail"] = trail
        return updates

    return decision


def decision(state: CreditState) -> dict:
    """Default decision node."""
    return make_decision_node()(state)


# ---------------------------------------------------------------------------
# Node 8: human_approval — LangGraph interrupt (pause/resume)
# ---------------------------------------------------------------------------
def human_approval(state: CreditState) -> dict:
    """Pause the workflow for human review (only reached when decision == REVIEW).

    Uses ``langgraph.types.interrupt`` so the framework persists state and
    resumes only when an external `/approve` or `/reject` call is made.
    """
    decision_val = state.get("decision", DECISION_REVIEW)
    if decision_val != DECISION_REVIEW or not state.get("approval_required"):
        # Should not happen — called via conditional edge — but guard anyway.
        return {"approval_status": APPROVAL_PENDING}

    prompt_msg = (
        f"Application {state.get('application_id', '?')} requires human review.\n"
        f"Risk score: {state.get('risk_score', 0):.4f}\n"
        f"Risk level: {state.get('risk_level', 'UNKNOWN')}\n"
        f"Fraud flags: {state.get('fraud_flags', [])}\n"
        f"Policy violations: {state.get('policy_violations', [])}\n"
        f"ML reasons: {state.get('reasons', [])}"
    )

    action = interrupt({
        "type": "human_approval",
        "prompt": prompt_msg,
        "application_id": state.get("application_id"),
        "current_decision": decision_val,
    })

    # The human responds: "approve" or "reject" (possibly with a note)
    human_choice = str(action).lower().strip()
    approved = human_choice in ("approve", "approved", "accept", "yes")

    updates: dict = {}
    if approved:
        updates["approval_status"] = APPROVAL_APPROVED
        updates["decision"] = DECISION_APPROVE
        updates["next"] = "execute"
    else:
        updates["approval_status"] = APPROVAL_REJECTED
        updates["decision"] = DECISION_REJECT
        updates["next"] = "reject"
    updates["approval_note"] = human_choice

    trail = list(state.get("audit_trail", []))
    updates["audit_trail"] = trail + [_audit_entry(
        "human_approval", AUDIT_SUCCESS,
        f"human decision={updates['approval_status']}",
    )]
    return updates


# ---------------------------------------------------------------------------
# Node 9: execute — action taken on an APPROVE (e.g. disburse, log)
# ---------------------------------------------------------------------------
def execute(state: CreditState) -> dict:
    """Execute the approved action — generates VietQR and formal Loan Agreement."""
    app_id = state.get("application_id", "unknown")
    trail = state.get("audit_trail", []) + []
    
    loan_amount = state.get("loan_amount", state.get("customer_data", {}).get("loan_amount", 10000))
    customer_name = state.get("customer_data", {}).get("name", "CUSTOMER")
    national_id = state.get("national_id", "001002003004")

    vietqr_data = generate_vietqr_disbursement(
        contract_code=app_id,
        loan_amount=loan_amount
    )
    vietqr_url = vietqr_data.qr_quicklink

    agreement = generate_loan_agreement(
        contract_code=app_id,
        borrower_name=customer_name,
        national_id=national_id,
        loan_amount=loan_amount,
        loan_term_months=state.get("loan_tenure_months", state.get("customer_data", {}).get("loan_term", 36)),
        annual_interest_rate=state.get("pricing", {}).get("recommended_annual_rate", 0.15)
    )
    # The current tests likely expect a PDF string or some dict. We'll return agreement as dict or string
    agreement_pdf = agreement.legal_text if hasattr(agreement, "legal_text") else str(agreement)
    
    trail.append(_audit_entry("execute", AUDIT_SUCCESS, "Generated VietQR & Loan Agreement"))
    trail.append(_audit_entry("execute", AUDIT_SUCCESS, f"approved application {app_id} — action logged (disbursement stub)"))
    
    return {
        "vietqr_url": vietqr_url,
        "loan_agreement_pdf": agreement_pdf,
        "audit_trail": trail
    }


# ---------------------------------------------------------------------------
# Node 10: audit — terminal audit trail consolidation
# ---------------------------------------------------------------------------
def audit(state: CreditState) -> dict:
    """Final audit trail consolidation — marks END of workflow."""
    trail = list(state.get("audit_trail", []))
    trail = trail + [_audit_entry(
        "audit", AUDIT_SUCCESS,
        f"workflow complete, decision={state.get('decision', '?')}, "
        f"audit_id={state.get('audit_id', '?')}",
    )]
    return {
        "audit_trail": trail,
        "workflow_complete": True,
    }


# ---------------------------------------------------------------------------
# Edge routing helpers
# ---------------------------------------------------------------------------
def route_decision(state: CreditState) -> str:
    """Single routing function from the decision node.

    Returns one of: "execute", "human_approval", "audit_reject".
    """
    if state.get("error") or state.get("decision") == DECISION_REJECT:
        return "audit_reject"
    if state.get("decision") == DECISION_APPROVE:
        return "execute"
    if state.get("decision") == DECISION_REVIEW and state.get("approval_required"):
        return "human_approval"
    return "audit_reject"


def after_human_approval(state: CreditState) -> str:
    """Conditional edge after human_approval: approved → execute, rejected → audit."""
    next_step = state.get("next", "reject")
    if next_step == "execute":
        return "execute"
    return "audit_reject"
