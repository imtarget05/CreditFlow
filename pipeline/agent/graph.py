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


def build_credit_graph(
    pipeline: Any,
    meta: dict,
    checkpointer: Any = None,
) -> CompiledStateGraph:
    """Build and compile the credit-decision LangGraph.

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
    # Bind the model/pipeline to the risk_model node via closure
    risk_node = make_risk_model(pipeline, meta)

    sg = StateGraph(CreditState)

    # --- linear sequence (START -> load -> validate -> risk -> fraud -> policy -> explain -> decision) ---
    sg.add_node("load_application", load_application)
    sg.add_node("validate_input", validate_input)
    sg.add_node("risk_model", risk_node)
    sg.add_node("fraud_model", fraud_model)
    sg.add_node("policy_engine", policy_engine)
    sg.add_node("explain", explain)
    sg.add_node("decision", decision)

    # --- terminal / branching nodes ---
    sg.add_node("human_approval", human_approval)
    sg.add_node("execute", execute)
    sg.add_node("audit", audit)

    # --- edges ---
    sg.set_entry_point("load_application")
    sg.add_edge("load_application", "validate_input")
    sg.add_edge("validate_input", "risk_model")
    sg.add_edge("risk_model", "fraud_model")
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

    To resume after a human decision, call:
    ``graph.invoke(Command(resume="approve"), {"thread_id": thread_id})``
    """
    graph = build_credit_graph(pipeline, meta)
    if thread_id is None:
        thread_id = create_workflow_run_id()

    run_config = {"thread_id": thread_id}
    if config:
        run_config.update(config)

    initial_state: CreditState = {
        "customer_data": profile,
        "request_meta": {},
    }

    try:
        return graph.invoke(initial_state, config=run_config)
    except Exception:
        # Interrupt raised when the graph pauses at human_approval.
        # Retrieve current state instead — caller can inspect approval_required.
        state = graph.get_state(config=run_config)
        return dict(state.values)
