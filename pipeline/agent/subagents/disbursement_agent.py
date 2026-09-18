"""Disbursement Execution Sub-Agent – Action Agent executing ledger disbursement & audit records."""

from __future__ import annotations

from pipeline.agent.nodes import decision, execute, audit, human_approval


class DisbursementExecutionAgent:
    """Specialist sub-agent for loan disbursement execution, ledger persistence, and audit logging."""

    def __init__(self):
        pass

    def decide(self, state: dict) -> dict:
        """Synthesize overall decision routing (APPROVE, REVIEW, REJECT)."""
        return decision(state)

    def execute_disbursement(self, state: dict) -> dict:
        """Issue contract number and commit to tamper-evident SQLite disbursement ledger."""
        return execute(state)

    def log_audit(self, state: dict) -> dict:
        """Append an audit-trail entry for the workflow run (durable row, not immutable)."""
        return audit(state)

    def gate_human_approval(self, state: dict) -> dict:
        """Pause execution at human approval interrupt."""
        return human_approval(state)
