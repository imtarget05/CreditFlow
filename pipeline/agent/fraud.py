"""Fraud detection node for the LangGraph decision workflow.

This is a *rule-based* fraud scorer used by the workflow's ``fraud_model``
node.  It produces a ``fraud_score`` in [0, 1] and a list of triggered
``fraud_flags``.

Design notes (user argument #2):
  - CreditFlow is a *financial decision workflow*, not a general knowledge
    assistant.  Fraud checks here are deterministic business rules over the
    credit profile — no LLM, no RAG.  LlamaIndex is NOT involved.
  - The score is one *input* to the ``decision`` node; it never alone
    approves/rejects.  The ML risk model + policy + fraud together drive the
    final decision, and only REVIEW ever waits for a human.
"""
from __future__ import annotations

from typing import Any

from pipeline.agent.state import CreditState


# Each rule: (flag_label, description, weight) — weight ∈ [0, 1]
# Scores are clipped so the aggregate never exceeds 1.0.
FRAUD_RULES: list[tuple[str, str, float]] = [
    ("rapid_application_sequence", "Multiple applications submitted within minutes", 0.30),
    ("income_exceeds_loan_by_unusual_ratio", "Stated income is implausibly high relative to loan", 0.25),
    ("zero_credit_history", "Applicant has zero credit history yet requests large loan", 0.35),
    ("employment_zero_with_large_loan", "Unemployed applicant requesting a large loan", 0.40),
    ("debt_burden_extreme", "Existing debt exceeds 5× monthly income", 0.30),
    ("loan_amount_suspiciously_round", "Loan amount is a suspiciously round number (e.g. 10,000,000)", 0.10),
]

_MAX_SCORE = 1.0


def compute_fraud_flags(data: dict[str, Any]) -> list[str]:
    """Return the list of fraud flag labels triggered by *data*."""
    flags: list[str] = []

    income = float(data.get("income", 0))
    loan_amount = float(data.get("loan_amount", 0))
    existing_debt = float(data.get("existing_debt", 0))
    credit_history = float(data.get("credit_history", 0))
    employment_years = float(data.get("employment_years", 0))

    # rapid_application_sequence — caller-supplied meta flag (UI may set it)
    if data.get("_rapid_sequence"):
        flags.append("rapid_application_sequence")

    # income_exceeds_loan_by_unusual_ratio
    if income > 0 and loan_amount > 0 and (loan_amount / income) > 500:
        flags.append("income_exceeds_loan_by_unusual_ratio")

    # zero_credit_history
    if credit_history == 0 and loan_amount > 50_000_000:
        flags.append("zero_credit_history")

    # employment_zero_with_large_loan
    if employment_years == 0 and loan_amount > 100_000_000:
        flags.append("employment_zero_with_large_loan")

    # debt_burden_extreme
    if income > 0 and (existing_debt / income) > 5.0:
        flags.append("debt_burden_extreme")

    # loan_amount_suspiciously_round
    if loan_amount > 0 and loan_amount % 10_000_000 == 0 and loan_amount >= 10_000_000:
        flags.append("loan_amount_suspiciously_round")

    return flags


def fraud_score(flags: list[str]) -> float:
    """Aggregate fraud flag weights into a [0, 1] score."""
    weight_map = dict((label, w) for label, _, w in FRAUD_RULES)
    raw = sum(weight_map.get(f, 0.0) for f in flags)
    return min(raw, _MAX_SCORE)


def run_fraud_check(state: CreditState) -> dict:
    """Node function: populate ``fraud_score`` and ``fraud_flags`` on *state*.

    Returns a partial state dict to be merged by LangGraph's ``update`` logic.
    """
    data: dict = state.get("customer_data", {})
    flags = compute_fraud_flags(data)
    score = fraud_score(flags)
    if flags:
        score = min(score, _MAX_SCORE)
    return {
        "fraud_score": round(score, 4),
        "fraud_flags": flags,
    }
