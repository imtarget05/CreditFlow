"""Tests for the LangGraph decision workflow (pipeline/agent/) and FastAPI integration.

Covers:
- Policy engine (pipeline/agent/policy.py)
- Fraud detection (pipeline/agent/fraud.py)
- Explanation layer (pipeline/agent/explanations.py)
- Graph routing (pipeline/agent/graph.py)
- FastAPI graph endpoints (backend/app.py)

Architecture discipline (user argument #2):
  - LangGraph = workflow spine (state/checkpoint/human approval)
  - LangChain = LLM explanation layer only (never decides)
  - LlamaIndex = NOT in core path
"""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import ExitStack

from fastapi.testclient import TestClient

from backend.app import app
from backend.predict_service import load_production_model

_stack = ExitStack()
client = _stack.enter_context(TestClient(app))

# Profiles follow the API/UI money-unit contract: VND (đồng).
# Training-scale numbers (e.g. income=2500) are rejected by
# pipeline/validation/schemas.py:validate_money_unit_contract — every money
# field here is a real VND amount, and all ratios (DTI/LTI) are unchanged.
LOW_RISK = {"income":5000000,"age":35,"employment_years":10,"loan_amount":20000000,"loan_term":36,"existing_debt":3000000,"credit_history":8,"previous_defaults":0}
MID_RISK = {"income":2500000,"age":32,"employment_years":4,"loan_amount":12000000,"loan_term":36,"existing_debt":1000000,"credit_history":5,"previous_defaults":2}
HIGH_RISK = {"income":2500000,"age":32,"employment_years":4,"loan_amount":12000000,"loan_term":36,"existing_debt":3500000,"credit_history":5,"previous_defaults":0}


# ---------------------------------------------------------------------------
# Policy engine tests
# ---------------------------------------------------------------------------
def test_policy_no_violations_for_clean_profile():
    from pipeline.agent.policy import evaluate_policy
    violations = evaluate_policy(LOW_RISK)
    assert isinstance(violations, list)
    assert "policy_debt_to_income_excessive" not in violations


def test_policy_blocks_excessive_dti():
    from pipeline.agent.policy import evaluate_policy, has_blocking_violation
    high_dti = dict(LOW_RISK, existing_debt=6000000)
    violations = evaluate_policy(high_dti)
    assert "policy_debt_to_income_excessive" in violations
    assert has_blocking_violation(violations) is True


def test_policy_blocks_excessive_lti():
    from pipeline.agent.policy import evaluate_policy, has_blocking_violation
    high_lti = dict(LOW_RISK, loan_amount=150000000)
    violations = evaluate_policy(high_lti)
    assert "policy_loan_to_income_extreme" in violations
    assert has_blocking_violation(violations) is True


def test_policy_blocks_previous_defaults():
    from pipeline.agent.policy import evaluate_policy, has_blocking_violation
    many_defaults = dict(LOW_RISK, previous_defaults=4)
    violations = evaluate_policy(many_defaults)
    assert "policy_existing_defaults" in violations
    assert has_blocking_violation(violations) is True


def test_policy_warn_only_for_employment_stability():
    from pipeline.agent.policy import evaluate_policy, has_blocking_violation
    violations = evaluate_policy(MID_RISK)
    assert "policy_employment_stability_low" in violations
    assert has_blocking_violation(violations) is False


# ---------------------------------------------------------------------------
# Money-unit contract (VND) enforcement in the workflow
# ---------------------------------------------------------------------------
TRAINING_SCALE_PROFILE = {
    "income": 2500, "age": 32, "employment_years": 4, "loan_amount": 12000,
    "loan_term": 36, "existing_debt": 3500, "credit_history": 5,
    "previous_defaults": 0,
}


def test_validate_input_rejects_training_scale_money():
    """A payload that cannot be VND must be rejected with an explicit message."""
    from pipeline.agent.nodes import make_validate_input

    out = make_validate_input()({"customer_data": TRAINING_SCALE_PROFILE, "audit_trail": []})
    assert out["decision"] == "REJECT"
    assert out["next"] == "reject"
    assert "unit_contract" in out["error"] or "VND" in out["error"]


def test_policy_and_fraud_use_vnd_as_is():
    """No magnitude heuristic: policy/fraud compare the contract VND values."""
    from pipeline.agent.policy import evaluate_policy
    from pipeline.agent.fraud import compute_fraud_flags

    # 900,000 VND loan is below the contract floor but must NOT be silently
    # multiplied by 1000 — the ratio rules simply see the given value.
    small_loan = dict(LOW_RISK, loan_amount=900000, credit_history=0)
    assert "policy_credit_history_short" not in evaluate_policy(small_loan)
    assert "zero_credit_history" not in compute_fraud_flags(small_loan)
    assert "zero_credit_history" in compute_fraud_flags(
        dict(LOW_RISK, loan_amount=600000000, credit_history=0)
    )


# ---------------------------------------------------------------------------
# Fraud detection tests
# ---------------------------------------------------------------------------
def test_fraud_clean_profile():
    from pipeline.agent.fraud import compute_fraud_flags, fraud_score
    flags = compute_fraud_flags(LOW_RISK)
    score = fraud_score(flags)
    assert score == 0.0
    assert len(flags) == 0


def test_fraud_detects_extreme_debt_burden():
    from pipeline.agent.fraud import compute_fraud_flags
    extreme_debt = dict(LOW_RISK, existing_debt=30000000)
    flags = compute_fraud_flags(extreme_debt)
    assert "debt_burden_extreme" in flags


def test_fraud_detects_zero_credit_history():
    from pipeline.agent.fraud import compute_fraud_flags
    no_history = dict(LOW_RISK, credit_history=0, loan_amount=60000000000)
    flags = compute_fraud_flags(no_history)
    assert "zero_credit_history" in flags


def test_fraud_score_capped_at_one():
    from pipeline.agent.fraud import fraud_score
    all_flags = ["rapid_application_sequence", "income_exceeds_loan_by_unusual_ratio",
                 "zero_credit_history", "employment_zero_with_large_loan",
                 "debt_burden_extreme", "loan_amount_suspiciously_round"]
    score = fraud_score(all_flags)
    assert score <= 1.0


# ---------------------------------------------------------------------------
# Explanation layer tests
# ---------------------------------------------------------------------------
def test_explanation_deterministic_fallback(monkeypatch):
    # Env isolation: this test must hit the deterministic template even when
    # real Cloudflare credentials exist in the developer's shell environment.
    for var in (
        "CREDITFLOW_LLM_PROVIDER",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    from pipeline.agent.explanations import generate_explanation, explanation_to_text
    from pipeline.agent.state import CreditState

    state: CreditState = {
        "customer_data": LOW_RISK,
        "risk_score": 0.01,
        "risk_level": "LOW",
        "decision": "APPROVE",
        "model_name": "logistic_regression",
        "reasons": [],
        "fraud_score": 0.0,
        "fraud_flags": [],
        "policy_violations": [],
        "data_classification": "PUBLIC",
    }
    expl = generate_explanation(state)
    assert "summary" in expl
    assert "risk_factors" in expl
    assert expl.get("prompt_version") == "credit-explain-v1"
    assert expl.get("_llm") in (None, False)
    text = explanation_to_text(expl)
    assert len(text) > 0


def test_explanation_high_risk(monkeypatch):
    # Env isolation: force the deterministic template even when real
    # Cloudflare credentials exist in the developer's shell environment.
    for var in (
        "CREDITFLOW_LLM_PROVIDER",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    from pipeline.agent.explanations import generate_explanation
    from pipeline.agent.state import CreditState

    state: CreditState = {
        "customer_data": HIGH_RISK,
        "risk_score": 0.95,
        "risk_level": "HIGH",
        "decision": "REJECT",
        "model_name": "logistic_regression",
        "reasons": ["high debt-to-income"],
        "fraud_score": 0.3,
        "fraud_flags": ["debt_burden_extreme"],
        "policy_violations": ["policy_debt_to_income_excessive"],
    }
    expl = generate_explanation(state)
    summary = expl.get("summary", "")
    assert "HIGH" in summary or "cao" in summary.lower()

def test_explain_node_wires_rag(monkeypatch):
    # Env isolation: force the deterministic template even when real
    # Cloudflare credentials exist in the developer's shell environment.
    for var in (
        "CREDITFLOW_LLM_PROVIDER",
        "CLOUDFLARE_ACCOUNT_ID",
        "CLOUDFLARE_API_TOKEN",
        "CLOUDFLARE_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    from pipeline.agent.nodes import explain
    from pipeline.agent.state import CreditState

    state: CreditState = {
        "customer_data": LOW_RISK,
        "risk_score": 0.01,
        "risk_level": "LOW",
        "decision": "APPROVE",
        "model_name": "logistic_regression",
        "reasons": ["low debt-to-income ratio"],
        "fraud_score": 0.0,
        "fraud_flags": [],
        "policy_violations": [],
        "data_classification": "PUBLIC",
        "audit_trail": [],
    }
    result = explain(state)
    assert "explanation_meta" in result
    assert "rag_sources" in result["explanation_meta"]
    assert isinstance(result["explanation_meta"]["rag_sources"], list)
    assert result["explanation_meta"]["latency_ms"] >= 0
    assert "explanation" in result
    assert len(result["explanation"]) > 0


def test_explanation_langchain_provenance(monkeypatch):
    from pipeline.agent.explanations import generate_explanation
    from pipeline.agent.state import CreditState

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_MODEL", "@cf/test")

    class DummyResp:
        status_code = 200
        def json(self_inner):
            return {"result": {"response": '{"summary": "ok", "risk_factors": ["x"], "recommendation_note": "y", "confidence": "high"}'}}
    import pipeline.agent.llm_provider as lp
    monkeypatch.setattr(lp, "httpx", type("httpx", (), {"post": lambda *a, **kw: DummyResp()})())

    state: CreditState = {
        "customer_data": LOW_RISK,
        "risk_score": 0.5,
        "risk_level": "MEDIUM",
        "decision": "REVIEW", "data_classification": "PUBLIC",
        "model_name": "logistic_regression",
        "reasons": ["high debt-to-income ratio"],
        "fraud_score": 0.0,
        "fraud_flags": [],
        "policy_violations": [],
        "data_classification": "PUBLIC",
    }
    expl = generate_explanation(state)
    assert expl.get("_llm") is True
    assert expl.get("prompt_version") == "credit-explain-v1"
    assert expl.get("llm_model") == "@cf/test"



# ---------------------------------------------------------------------------
# Graph workflow tests
# ---------------------------------------------------------------------------
def test_graph_approve_path(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    from pipeline.agent.graph import run_credit_workflow
    pipeline, meta = load_production_model()
    result = run_credit_workflow(pipeline, meta, LOW_RISK)
    assert result.get("decision") == "APPROVE"
    assert result.get("workflow_complete") is True
    assert result.get("approval_required") is False
    assert len(result.get("audit_trail", [])) >= 9


def test_graph_reject_path(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    from pipeline.agent.graph import run_credit_workflow
    pipeline, meta = load_production_model()
    result = run_credit_workflow(pipeline, meta, HIGH_RISK)
    assert result.get("decision") == "REJECT"
    assert result.get("workflow_complete") is True
    violations = result.get("policy_violations", [])
    assert len(violations) > 0


def test_graph_review_path_pauses(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    from pipeline.agent.graph import run_credit_workflow
    pipeline, meta = load_production_model()
    result = run_credit_workflow(pipeline, meta, MID_RISK)
    assert result.get("decision") == "REVIEW"
    assert result.get("approval_required") is True
    assert result.get("workflow_complete") is None


# ---------------------------------------------------------------------------
# FastAPI graph endpoint tests — SLOW (each spins the full 12-node LangGraph
# + TestClient lifespan; ~18-26s per test). CI fast job excludes them via
# `-m "not integration and not slow"`; CI slow job runs them separately.
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_graph_endpoint_start_approve(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": LOW_RISK})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "APPROVE"
    assert body["workflow_complete"] is True
    assert "thread_id" in body
    assert "audit_trail" in body


@pytest.mark.slow
def test_graph_endpoint_start_reject(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": HIGH_RISK})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "REJECT"
    assert body["workflow_complete"] is True


@pytest.mark.slow
def test_graph_endpoint_start_review_and_resume(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": MID_RISK})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "REVIEW"
    assert body["approval_required"] is True
    assert body["workflow_complete"] is False

    thread_id = body["thread_id"]

    r2 = client.post("/predict/graph/" + thread_id + "/approve", json={"action": "approve"})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["decision"] == "APPROVE"
    assert body2["approval_status"] == "APPROVED"
    assert body2["workflow_complete"] is True


@pytest.mark.slow
def test_graph_endpoint_start_review_and_reject(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": MID_RISK})
    assert r.status_code == 200
    body = r.json()
    assert body["approval_required"] is True

    thread_id = body["thread_id"]

    r2 = client.post("/predict/graph/" + thread_id + "/approve", json={"action": "reject"})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["decision"] == "REJECT"
    assert body2["approval_status"] == "REJECTED"
    assert body2["workflow_complete"] is True


@pytest.mark.slow
def test_graph_endpoint_get_state(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": MID_RISK})
    body = r.json()
    thread_id = body["thread_id"]

    r2 = client.get("/predict/graph/" + thread_id)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["thread_id"] == thread_id
    assert body2["decision"] == "REVIEW"


def test_graph_endpoint_get_state_not_found():
    r = client.get("/predict/graph/run-nonexistent123")
    assert r.status_code == 404


@pytest.mark.slow
def test_graph_endpoint_audit_trail(monkeypatch):
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": LOW_RISK})
    body = r.json()
    app_id = body["application_id"]

    r2 = client.get("/audit/" + app_id)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["application_id"] == app_id
    assert len(body2["audit_trail"]) > 0
    assert body2["workflow_complete"] is True


def test_graph_endpoint_audit_not_found():
    r = client.get("/audit/APP-nonexistent123")
    assert r.status_code == 404


@pytest.mark.slow
def test_graph_endpoint_rejects_training_scale_money(monkeypatch):
    """POST /predict/graph with training-scale money -> REJECT + clear message."""
    for var in ("CREDITFLOW_LLM_PROVIDER", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    r = client.post("/predict/graph", json={"customer_data": TRAINING_SCALE_PROFILE})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "REJECT"
    assert "VND" in body["error"]
    assert body["workflow_complete"] is True


def test_decoupled_subagents_direct_execution():
    """Verify each decoupled sub-agent in CreditFlow can run independently."""
    from pipeline.agent.subagents import (
        UnderwritingRiskAgent,
        FraudComplianceAgent,
        ExplainabilityAgent,
        DisbursementExecutionAgent,
    )
    from pipeline.agent.graph import CreditDecisionOrchestrator
    from backend.predict_service import load_production_model

    pipeline, meta = load_production_model()
    underwriting = UnderwritingRiskAgent(pipeline, meta)
    fraud = FraudComplianceAgent()
    explainer = ExplainabilityAgent()
    disbursement = DisbursementExecutionAgent()

    state = {
        "customer_data": LOW_RISK,
        "request_meta": {},
    }

    # 1. Underwriting
    val_out = underwriting.validate(state)
    assert val_out["audit_trail"][-1]["status"] == "success"
    state.update(val_out)


    score_out = underwriting.score_risk(state)
    assert "risk_score" in score_out
    state.update(score_out)

    # 2. Fraud & Compliance
    fraud_out = fraud.check_fraud(state)
    assert "fraud_score" in fraud_out
    state.update(fraud_out)

    policy_out = fraud.check_policy(state)
    assert "policy_violations" in policy_out
    state.update(policy_out)

    # 3. Explainability
    exp_out = explainer.explain_decision(state)
    assert "explanation" in exp_out
    state.update(exp_out)

    # 4. Disbursement
    dec_out = disbursement.decide(state)
    assert dec_out["decision"] == "APPROVE"
    state.update(dec_out)

    exec_out = disbursement.execute_disbursement(state)
    assert "audit_trail" in exec_out
    state.update(exec_out)

    audit_out = disbursement.log_audit(state)
    assert audit_out["workflow_complete"] is True


    # 5. Orchestrator alias compiled graph
    graph = CreditDecisionOrchestrator(pipeline, meta)
    assert graph is not None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])

