#!/usr/bin/env python3
"""Fraud compliance agent for CreditFlow credit decision engine."""

from __future__ import annotations

from pipeline.agent.nodes import fetch_gateways, fraud_model, policy_engine


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
