"""Fraud & Compliance Sub-Agent – Validation Agent verifying fraud signals & credit policies."""

from __future__ import annotations

from pipeline.agent.nodes import fraud_model, policy_engine, fetch_gateways


class FraudComplianceAgent:
    """Specialist sub-agent for fraud detection, regulatory compliance rules, and external data gateways."""

    def __init__(self):
        pass

    def run_gateways(self, state: dict) -> dict:
        """Call external data gateways (CIC, Bank Statement parser)."""
        return fetch_gateways(state)

    def check_fraud(self, state: dict) -> dict:
        """Run deterministic upstream fraud checks."""
        return fraud_model(state)

    def check_policy(self, state: dict) -> dict:
        """Evaluate business lending policy constraints."""
        return policy_engine(state)
