"""Credit state schema for the LangGraph decision workflow.

``CreditState`` is the single source of truth passed through every node of
the workflow graph.  It accumulates evidence as the application flows from
ingestion → validation → risk/fraud/policy checks → decision → (human
approval) → execution → audit.

TypedDict + ``total=False`` lets nodes add fields incrementally without
requiring every key to exist at the start — the same discipline the spec
demands for schema validation (§5.2) and auditability (§11).
"""
from __future__ import annotations

from typing import Optional
from typing_extensions import TypedDict


# Decision buckets (mirrors pipeline.modeling.threshold)
DECISION_APPROVE = "APPROVE"
DECISION_REVIEW = "REVIEW"
DECISION_REJECT = "REJECT"

# Risk display buckets (mirrors pipeline.modeling.threshold)
RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

# Approval lifecycle
APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_REJECTED = "REJECTED"

# Audit statuses
AUDIT_SUCCESS = "success"
AUDIT_FAILURE = "failure"


class AuditEntry(TypedDict):
    """A single audit-trail record written at each workflow step."""
    timestamp: str
    step: str
    status: str
    detail: str


class CreditState(TypedDict, total=False):
    """Full mutable state for a single credit-application workflow run."""

    # --- identity / provenance ---
    application_id: str
    audit_id: str

    # --- raw input ---
    customer_data: dict          # canonical 8-feature payload
    request_meta: dict           # source IP, timestamp, caller, etc.

    # --- derived data ---
    derived_features: dict
    feature_flags: list[str]

    # --- ML risk signal ---
    model_name: str
    model_version: str
    risk_score: float            # P(default) from the production classifier
    risk_level: str              # LOW / MEDIUM / HIGH

    # --- fraud signal ---
    fraud_score: float           # 0.0 (clean) → 1.0 (definitely fraud)
    fraud_flags: list[str]

    # --- policy signal ---
    policy_violations: list[str]

    # --- decision ---
    decision: str                # APPROVE / REVIEW / REJECT
    reasons: list[str]           # rule-based risk reasons

    # --- cost-aware threshold info (from meta.json) ---
    tuned_threshold: float
    business_cost: dict          # {fn_cost, fp_cost}

    # --- human approval ---
    approval_required: bool      # True when decision == REVIEW
    approval_status: str         # PENDING / APPROVED / REJECTED
    approval_note: str

    # --- LLM explanation (LangChain layer, never the decision-maker) ---
    explanation: str
    explanation_meta: dict

    # --- workflow control ---
    next: str                    # target node for conditional routing
    error: Optional[str]         # populated on failure paths

    # --- audit trail ---
    audit_trail: list[AuditEntry]
