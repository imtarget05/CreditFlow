"""CreditFlow LangGraph decision workflow agent.

This package implements the production credit-decision workflow as a
LangGraph state machine (spec §16 architecture — Decision Engineering layer
on top of the tabular ML model).

Core principle (user argument #2):
  - LangGraph = workflow orchestration (state, retry, checkpoint, human
    approval, audit trail).  This is the spine of CreditFlow.
  - LangChain  = LLM/tool abstraction layer used ONLY for structured
    explanations ("High risk because ...").  The LLM never makes the final
    decision.
  - LlamaIndex = NOT in the core path.  Only added later if bank policy PDFs /
    regulations need RAG grounding.
"""
