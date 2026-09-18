"""Explainability Sub-Agent – Writing Agent synthesizing credit risk explanations with RAG grounding."""

from __future__ import annotations

from pipeline.agent.nodes import explain


class ExplainabilityAgent:
    """Specialist sub-agent for transparent AI explanations grounded in policy docs."""

    def __init__(self):
        pass

    def explain_decision(self, state: dict) -> dict:
        """Synthesize natural language explanations in Vietnamese/English with RAG context."""
        return explain(state)
