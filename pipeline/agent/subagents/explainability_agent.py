#!/usr/bin/env python3
"""Explainability agent for CreditFlow credit decision engine."""

from __future__ import annotations

from pipeline.agent.nodes import explain


class ExplainabilityAgent:
    """Specialist sub-agent for transparent AI explanations grounded in policy docs."""

    def __init__(self):
        pass

    def explain_decision(self, state: dict) -> dict:
        """Synthesize natural language explanations in Vietnamese/English with RAG context."""
        return explain(state)
