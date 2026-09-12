"""Policy engine node for the LangGraph decision workflow.

Business policy rules are hard constraints that can escalate a decision to
REJECT regardless of the ML risk score.  They are *deterministic* and fully
auditable — no LLM, no RAG (spec §19.4: keep portfolio discipline).

Design (user argument #2):
  - The final decision is **never** made by the LLM.
  - Policy validates; if policy fires → REJECT immediately.
  - Otherwise the cost-tuned ML threshold (from ``meta.json``) decides
    APPROVE / REVIEW / REJECT.
"""
from __future__ import annotations

from typing import Any

from pipeline.agent.state import CreditState


# ---------------------------------------------------------------------------
# Policy rule definitions
# ---------------------------------------------------------------------------
# Each rule: (code, description, severity)
# severity: "block" → immediate REJECT, "warn" → adds to violations but doesn't block
POLICY_RULES: list[tuple[str, str, str]] = [
    ("policy_debt_to_income_excessive",
     "Debt-to-income ratio exceeds 100% — total obligations exceed income", "block"),
    ("policy_loan_to_income_extreme",
     "Loan-to-income ratio exceeds 20× — loan far exceeds repayment capacity", "block"),
    ("policy_existing_defaults",
     "Applicant has 3+ previous defaults — automatic reject per policy", "block"),
    ("policy_credit_history_short",
     "Credit history less than 1 year with high loan request", "block"),
    ("policy_age_young_high_risk",
     "Applicant under 25 with thin credit history — elevated risk segment", "warn"),
    ("policy_employment_stability_low",
     "Employment stability below 0.5 (≤5 years) — requires additional review", "warn"),
]

# Thresholds used by the rules
DTI_BLOCK_THRESHOLD = 1.0        # existing_debt / income > 1.0 → block
LTI_BLOCK_THRESHOLD = 20.0       # loan_amount / income > 20 → block
PREV_DEFAULTS_BLOCK = 3          # previous_defaults >= 3 → block
CREDIT_HISTORY_MIN = 1.0         # credit_history < 1 year with large loan → block
CREDIT_HISTORY_LARGE_LOAN = 50_000_000


def evaluate_policy(data: dict[str, Any]) -> list[str]:
    """Evaluate business policy rules against *data*.

    Returns a list of violation descriptions.  The caller (decision node)
    checks whether any **block** rule fired.
    """
    violations: list[str] = []

    income = float(data.get("income", 0))
    loan_amount = float(data.get("loan_amount", 0))
    existing_debt = float(data.get("existing_debt", 0))
    credit_history = float(data.get("credit_history", 0))
    previous_defaults = int(data.get("previous_defaults", 0))
    age = int(data.get("age", 0))
    employment_years = float(data.get("employment_years", 0))

    # policy_debt_to_income_excessive (block)
    if income > 0 and (existing_debt / income) > DTI_BLOCK_THRESHOLD:
        violations.append("policy_debt_to_income_excessive")

    # policy_loan_to_income_extreme (block)
    if income > 0 and (loan_amount / income) > LTI_BLOCK_THRESHOLD:
        violations.append("policy_loan_to_income_extreme")

    # policy_existing_defaults (block)
    if previous_defaults >= PREV_DEFAULTS_BLOCK:
        violations.append("policy_existing_defaults")

    # policy_credit_history_short (block)
    if credit_history < CREDIT_HISTORY_MIN and loan_amount > CREDIT_HISTORY_LARGE_LOAN:
        violations.append("policy_credit_history_short")

    # policy_age_young_high_risk (warn)
    if age < 25 and credit_history < 2.0:
        violations.append("policy_age_young_high_risk")

    # policy_employment_stability_low (warn)
    if income > 0 and employment_years < 5:
        violations.append("policy_employment_stability_low")

    return violations


def has_blocking_violation(violations: list[str]) -> bool:
    """Return True if any violation is a *blocking* rule (severity == block)."""
    block_codes = {code for code, _, sev in POLICY_RULES if sev == "block"}
    return any(v in block_codes for v in violations)


def run_policy_check(state: CreditState) -> dict:
    """Node function: evaluate policy rules and populate ``policy_violations``.

    Returns a partial state dict to be merged by LangGraph.
    """
    data: dict = state.get("customer_data", {})
    violations = evaluate_policy(data)
    return {
        "policy_violations": violations,
    }
