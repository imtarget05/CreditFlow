"""Underwriting Risk Sub-Agent – Analysis Agent for schema validation & ML risk scoring."""

from __future__ import annotations

from typing import Any
from pipeline.agent.nodes import validate_input, make_risk_model, financial_engineering


class UnderwritingRiskAgent:
    """Specialist sub-agent for applicant underwriting, statistical ML risk assessment, and financial engineering."""

    def __init__(self, pipeline: Any, meta: dict):
        self.pipeline = pipeline
        self.meta = meta
        self._risk_node = make_risk_model(pipeline, meta)

    def validate(self, state: dict) -> dict:
        """Validate applicant payload schema and values."""
        return validate_input(state)

    def score_risk(self, state: dict) -> dict:
        """Run ML scoring pipeline and cost-aware threshold evaluation."""
        return self._risk_node(state)
        
    def calculate_financials(self, state: dict) -> dict:
        """Calculate Basel II/III metrics, Pricing and Amortization."""
        return financial_engineering(state)
