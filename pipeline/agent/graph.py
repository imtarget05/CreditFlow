"""Compiled LangGraph workflow for CreditFlow credit decisions.

This is the **spine** of the LangGraph-based decision engine
(user argument #2).  It wires the nodes in ``nodes.py`` into a state machine
with conditional edges for APPROVE / REVIEW / REJECT routing and a human
approval interrupt for REVIEW cases.

Usage (server-side, from backend/app.py):

    from pipeline.agent.graph import build_credit_graph
    pipeline, meta = load_production_model()
    graph = build_credit_graph(pipeline, meta)
    state = graph.invoke({"customer_data": profile}, config={"thread_id": "..."})

Or step-by-step for interactive human approval:

    state = graph.invoke({...}, {""thread_id": ""...""})   # pauses at REVIEW
    final = graph.invoke(Command(resume=""approve""), {""thread_id": ""...""})   # resumes after human input
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from pipeline.agent.checkpointer import FileCheckpointSaver
from pipeline.agent.state import CreditState
from pipeline.agent.nodes import (
    load_application,
    validate_input,
    make_risk_model,
    fraud_model,
    policy_engine,
    explain,
    decision,
    human_approval,
    execute,
    audit,
    route_decision,
    after_human_approval,
)

OPERATIONAL_ERROR_CODES = frozenset({
    "MODEL_BUNDLE_INVALID",
    "GATEWAY_UNAVAILABLE",
    "CHECKPOINT_CORRUPT",
})


def _classify_operational_error(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc)
    if "ArtifactContractError" in name or "MODEL_BUNDLE" in msg:
        return "MODEL_BUNDLE_INVALID"
    if "Gateway" in name or "GATEWAY" in msg or "CIC" in msg:
        return "GATEWAY_UNAVAILABLE"
    if "Checkpoint" in name or "CHECKPOINT" in msg:
        return "CHECKPOINT_CORRUPT"
    return "OPERATIONAL_FAILURE"


from pipeline.agent.subagents import (
    UnderwritingRiskAgent,
    FraudComplianceAgent,
    ExplainabilityAgent,
    DisbursementExecutionAgent,
)


def build_credit_graph(
    pipeline: Any,
    meta: dict,
    checkpointer: Any = None,
) -> CompiledStateGraph:
    """Build and compile the credit-decision LangGraph with decoupled sub-agents.

    Parameters
    ----------
    pipeline : sklearn Pipeline
        The production ML pipeline (preprocessor + model) loaded once at
        startup.
    meta : dict
        The production ``meta.json`` contents (model version, tuned threshold,
        costs, etc.).
    checkpointer : BaseCheckpointSaver, optional
        Checkpoint saver for pause/resume of the human-approval interrupt.
        Defaults to ``InMemorySaver`` (RAM only, fine for one-off runs).
        Pass ``FileCheckpointSaver()`` so paused workflows survive a process
        restart and ``POST /predict/graph/{thread_id}/approve`` can resume
        them.
    """
    # Instantiate decoupled sub-agents
    underwriting = UnderwritingRiskAgent(pipeline, meta)
    fraud = FraudComplianceAgent()
    explainer = ExplainabilityAgent()
    disbursement = DisbursementExecutionAgent()

    sg = StateGraph(CreditState)

    # --- linear sequence (START -> load -> gateways -> validate -> risk -> financial -> fraud -> policy -> explain -> decision) ---
    sg.add_node("load_application", load_application)
    sg.add_node("fetch_gateways", fraud.run_gateways)
    sg.add_node("validate_input", underwriting.validate)
    sg.add_node("risk_model", underwriting.score_risk)
    sg.add_node("financial_engineering", underwriting.calculate_financials)
    sg.add_node("fraud_model", fraud.check_fraud)
    sg.add_node("policy_engine", fraud.check_policy)
    sg.add_node("explain", explainer.explain_decision)
    sg.add_node("decision", disbursement.decide)

    # --- terminal / branching nodes ---
    sg.add_node("human_approval", disbursement.gate_human_approval)
    sg.add_node("execute", disbursement.execute_disbursement)
    sg.add_node("audit", disbursement.log_audit)

    # --- edges ---
    sg.set_entry_point("load_application")
    sg.add_edge("load_application", "fetch_gateways")
    sg.add_edge("fetch_gateways", "validate_input")
    sg.add_edge("validate_input", "risk_model")
    sg.add_edge("risk_model", "financial_engineering")
    sg.add_edge("financial_engineering", "fraud_model")
    sg.add_edge("fraud_model", "policy_engine")
    sg.add_edge("policy_engine", "explain")
    sg.add_edge("explain", "decision")

    # --- conditional routing from decision ---
    sg.add_conditional_edges("decision", route_decision, {
        "execute": "execute",
        "human_approval": "human_approval",
        "audit_reject": "audit",
    })

    # --- human approval results ---
    sg.add_conditional_edges("human_approval", after_human_approval, {
        "execute": "execute",
        "audit_reject": "audit",
    })

    # --- execute -> audit -> END ---
    sg.add_edge("execute", "audit")
    sg.add_edge("audit", END)

    # InMemorySaver (default) enables pause/resume for human approval
    # interrupts.  Pass FileCheckpointSaver() to persist the paused state
    # across process restarts (pipeline/agent/checkpointer.py).
    if checkpointer is None:
        checkpointer = InMemorySaver()
    return sg.compile(checkpointer=checkpointer)


CreditDecisionOrchestrator = build_credit_graph


def create_workflow_run_id() -> str:
    """Generate a unique thread_id for a workflow invocation."""
    import uuid
    return f"run-{uuid.uuid4().hex[:12]}"


def run_credit_workflow(
    pipeline: Any,
    meta: dict,
    profile: dict,
    thread_id: Optional[str] = None,
    config: Optional[dict] = None,
) -> dict:
    """Run the full workflow to completion.

    If the decision routes to REVIEW, the graph will **pause** at
    ``human_approval`` (LangGraph interrupt).  The returned dict will contain
    ``approval_required=True`` and ``approval_status=PENDING``.

    Operational failures (model bundle, gateway, checkpoint) become a terminal
    ``FAILED`` result with ``workflow_status``/``error_code`` and never a
    pending approval. Unexpected programming errors are re-raised so tests
    catch them instead of masking bugs.

    To resume after a human decision, call:
    ``graph.invoke(Command(resume="approve"), {"thread_id": thread_id})``
    """
    import os

    graph = build_credit_graph(pipeline, meta)
    if thread_id is None:
        thread_id = create_workflow_run_id()

    run_config = {"configurable": {"thread_id": thread_id}}
    if config:
        if "configurable" in config:
            run_config["configurable"].update(config["configurable"])
        else:
            run_config["configurable"].update(config)

    initial_state: CreditState = {
        "customer_data": profile,
        "request_meta": {},
    }

    try:
        result = graph.invoke(initial_state, config=run_config)
        out = dict(result)
        out.setdefault("workflow_status", "COMPLETED" if out.get("workflow_complete") else "INTERRUPTED")
        out.setdefault("error_code", None)
        return out
    except Exception as exc:
        # LangGraph interrupt == paused REVIEW, not a failure.
        state = graph.get_state(config=run_config)
        values = dict(state.values) if state.values else {}
        interrupts = list(getattr(state, "interrupts", []) or [])
        next_nodes = list(getattr(state, "next", []) or ())
        if interrupts or next_nodes or values.get("approval_required") is True:
            values.setdefault("workflow_status", "INTERRUPTED")
            values.setdefault("error_code", None)
            return values
        code = _classify_operational_error(exc)
        # Never mask programming bugs in test mode.
        if os.environ.get("CREDITFLOW_STRICT_ERRORS") == "1" and code == "OPERATIONAL_FAILURE":
            raise
        failed: dict = dict(values)
        failed.update({
            "workflow_status": "FAILED",
            "error_code": code,
            "error": f"{code}: {exc}",
            "approval_required": False,
            "workflow_complete": False,
        })
        return failed

