"""CreditFlow FastAPI service.

Endpoints (spec §12):
  GET  /health       -> service + model status
  POST /predict      -> real ML prediction from a credit profile
  GET  /model/info   -> production model version + metrics + selection reasoning
  GET  /metrics      -> runtime request/latency/error counters + benchmark

LangGraph decision workflow (spec §16 architecture):
  POST /predict/graph           -> start a credit decision workflow
  POST /predict/graph/{thread_id}/approve -> resume with human approval
  GET  /predict/graph/{thread_id}        -> workflow state
  GET  /audit/{application_id}           -> audit trail

Runtime metrics are held in-process (demo scope). The 4-model benchmark is served
from models/production/benchmark_results.json so the comparison is reproducible and real.
"""
from __future__ import annotations

import json
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.predict_service import load_production_model, predict_risk, to_model_units
from pipeline.monitoring.drift import detect_drift
from pipeline.storage.ledger import (
    init_db as ledger_init_db,
    record_application,
    record_disbursement,
    update_application_status,
    get_application_by_thread,
    list_applications,
    list_disbursements,
    STATUS_APPROVED,
    STATUS_PENDING_REVIEW,
    STATUS_REJECTED,
)

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_JSON = ROOT / "models" / "production" / "benchmark_results.json"
REFERENCE_STATS = ROOT / "models" / "production" / "reference_stats.json"


# ---------------------------------------------------------------------------
# Request / response schemas (spec §4.1 / §4.3)
# ---------------------------------------------------------------------------
class PredictRequest(BaseModel):
    income: float = Field(gt=0, description="Monthly income (VND/tháng)")
    age: int = Field(ge=18, le=100, description="Age in years")
    employment_years: float = Field(ge=0, description="Years employed continuously")
    loan_amount: float = Field(gt=0, description="Loan amount (VND)")
    loan_term: int = Field(gt=0, description="Loan term in months")
    existing_debt: float = Field(ge=0, description="Total existing debt (VND)")
    credit_history: float = Field(ge=0, description="Years of credit history")
    previous_defaults: int = Field(ge=0, description="Count of past defaults")

    @field_validator("employment_years")
    @classmethod
    def employment_plausible(cls, v, info):
        age = info.data.get("age")
        if age is not None and v > age - 18 + 1e-6:
            raise ValueError("employment_years must be <= age - 18")
        return v

    @field_validator("credit_history")
    @classmethod
    def credit_history_plausible(cls, v, info):
        age = info.data.get("age")
        if age is not None and v > age - 18 + 1e-6:
            raise ValueError("credit_history must be <= age - 18")
        return v


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    model_version: str
    model_name: str


class PredictResponse(BaseModel):
    risk_probability: float
    risk_level: str
    decision: str
    model_version: str
    model_name: str
    threshold: dict
    reasons: list[str]
    deployment: str


# ---------------------------------------------------------------------------
# Runtime metrics to be appended next
# ---------------------------------------------------------------------------
# In-process runtime metrics (demo-level system monitoring)
# ---------------------------------------------------------------------------
class Metrics:
    def __init__(self):
        self.requests = {"total": 0, "predict": 0, "health": 0, "model_info": 0, "metrics": 0}
        self.errors = {"total": 0}
        self.latency = {"predict_sum_ms": 0.0, "predict_count": 0}
        self.llm = {"requests": 0, "errors": 0, "latency_sum_ms": 0.0}

    def bump(self, endpoint: str, latency_ms: float = 0.0):
        self.requests["total"] += 1
        if endpoint in self.requests:
            self.requests[endpoint] += 1
        if endpoint == "predict":
            self.latency["predict_sum_ms"] += latency_ms
            self.latency["predict_count"] += 1

    def error(self):
        self.errors["total"] += 1

    def snapshot(self):
        count = self.latency["predict_count"] or 1
        return {
            "requests": dict(self.requests),
            "errors": dict(self.errors),
            "avg_predict_latency_ms": round(self.latency["predict_sum_ms"] / count, 3),
            "llm_requests": self.llm["requests"],
            "llm_errors": self.llm["errors"],
            "uptime_seconds": round(time.time() - self.started, 3),
        }

    def start(self):
        self.started = time.time()


metrics = Metrics()
metrics.start()

# Recent inference window for drift monitoring (P11): the last N successful
# predictions, in-process (demo scope — same as Metrics; no external store).
prediction_window: deque = deque(maxlen=500)


# ---------------------------------------------------------------------------
# App + lifespan (load model once)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pipeline, app.state.meta = load_production_model()
    # Durable ledger for applications + disbursements (core-banking slice).
    ledger_init_db()
    yield


app = FastAPI(
    title="CreditFlow Risk Decision API",
    description="ML risk decision support for credit profiles — validation, feature "
    "engineering, cost-aware threshold decision, model versioning, runtime metrics.",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _benchmark_payload():
    if BENCHMARK_JSON.exists():
        return json.loads(BENCHMARK_JSON.read_text())
    return []


@app.get("/health", response_model=HealthResponse)
def health():
    metrics.bump("health")
    meta = getattr(app.state, "meta", {})
    model_loaded = hasattr(app.state, "pipeline") and app.state.pipeline is not None
    return HealthResponse(
        status="ok",
        model_loaded=model_loaded,
        model_version=meta.get("version", "unknown"),
        model_name=meta.get("model_name", "unknown"),
    )


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    t0 = time.perf_counter()
    try:
        payload = predict_risk(app.state.pipeline, req.model_dump(), app.state.meta)
    except ValueError as exc:
        metrics.error()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        metrics.error()
        raise HTTPException(status_code=500, detail=f"prediction_failed: {exc}")
    finally:
        metrics.bump("predict", (time.perf_counter() - t0) * 1000)
    # Store training-scale values so PSI compares like-with-like against
    # reference_stats.json (which was written at training time, pre-VND).
    prediction_window.append({**to_model_units(req.model_dump()), "risk_probability": payload["risk_probability"]})
    return PredictResponse(**payload)


@app.get("/drift")
def drift_report():
    """ML drift monitoring (P11): recent inference window vs training reference.

    Statuses: NO_DRIFT | DRIFT_DETECTED | INSUFFICIENT_DATA (< min_samples).
    Reports DATA/PREDICTION distribution drift (PSI) only — NOT model
    performance degradation (that would require ground-truth labels).
    """
    metrics.bump("metrics")
    if not REFERENCE_STATS.exists():
        raise HTTPException(
            status_code=404,
            detail="reference_stats.json not found — retrain with "
            "`python scripts/train_models.py` to generate the drift reference.",
        )
    reference = json.loads(REFERENCE_STATS.read_text())
    recent = pd.DataFrame(list(prediction_window))
    report = detect_drift(reference, recent)
    return {
        "drift": report,
        "window_max": prediction_window.maxlen,
        "reference": {
            "n_rows": reference.get("n_rows"),
            "features": sorted(reference.get("features", {}).keys()),
            "source": "models/production/reference_stats.json (written at training time)",
        },
    }


@app.get("/model/info")
def model_info():
    metrics.bump("model_info")
    meta = getattr(app.state, "meta", {})
    return {"model": meta}


@app.get("/llm/info")
def llm_info():
    """Report which LLM provider is configured (never leaks secrets)."""
    import os
    from pipeline.agent.llm_provider import DEFAULT_MODEL
    from pipeline.agent.explanations import PROMPT_VERSION
    provider = os.environ.get("CREDITFLOW_LLM_PROVIDER", "").lower().strip()
    return {
        "provider": provider or "template",
        "model": os.environ.get("CLOUDFLARE_MODEL", DEFAULT_MODEL),
        "prompt_version": PROMPT_VERSION,
        "configured": bool(provider == "cloudflare" and os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN")),
    }


@app.get("/metrics")
def runtime_metrics():
    metrics.bump("metrics")
    return {
        "runtime": metrics.snapshot(),
        "model": getattr(app.state, "meta", {}),
        "benchmark": _benchmark_payload(),
    }


# ---------------------------------------------------------------------------
# LangGraph decision workflow endpoints (spec §16 architecture)
# ---------------------------------------------------------------------------
# One shared compiled graph.  Workflow state lives in the file-backed
# checkpointer (keyed by thread_id), NOT in RAM — a server restart restores
# every paused workflow from the checkpoint file, so
# POST /predict/graph/{thread_id}/approve works across restarts.
_shared_graph: Any = None


def _build_shared_graph():
    """Build the workflow graph once, with a durable FileCheckpointSaver."""
    global _shared_graph
    if _shared_graph is None:
        from pipeline.agent.graph import build_credit_graph
        from pipeline.agent.checkpointer import FileCheckpointSaver

        checkpointer = getattr(app.state, "checkpointer", None)
        if checkpointer is None:
            checkpointer = FileCheckpointSaver()
            app.state.checkpointer = checkpointer
        _shared_graph = build_credit_graph(
            app.state.pipeline, app.state.meta, checkpointer=checkpointer
        )
    return _shared_graph


def _reset_shared_graph() -> None:
    """Drop the shared graph + checkpointer (tests / simulated restart)."""
    global _shared_graph
    _shared_graph = None
    if hasattr(app.state, "checkpointer"):
        del app.state.checkpointer


class GraphStartRequest(BaseModel):
    """Request body to start a LangGraph credit decision workflow."""
    customer_data: dict


class GraphApprovalRequest(BaseModel):
    """Request body to resume a paused workflow with human approval."""
    action: str = Field(..., description="'approve' or 'reject'")
    note: str = ""


def _get_graph(thread_id: str):
    """Return the shared graph after verifying the thread has checkpoint state.

    A thread with no checkpoint has never started (or predates checkpoint
    persistence) — resume cannot work, so 404 as before.
    """
    graph = _build_shared_graph()
    state = graph.get_state(config={"configurable": {"thread_id": thread_id}})
    if not state.values:
        raise HTTPException(
            status_code=404,
            detail=f"workflow {thread_id} not found — may have expired or never started",
        )
    return graph


@app.post("/predict/graph")
def start_graph_workflow(req: GraphStartRequest):
    """Start a LangGraph credit decision workflow.

    Returns the workflow state. If the decision routes to REVIEW, the workflow
    pauses at human_approval — use ``POST /predict/graph/{thread_id}/approve``
    to resume.
    """
    from pipeline.agent.graph import create_workflow_run_id

    graph = _build_shared_graph()
    thread_id = create_workflow_run_id()

    initial_state = {"customer_data": req.customer_data, "request_meta": {}}
    run_config = {"configurable": {"thread_id": thread_id}}

    try:
        result = graph.invoke(initial_state, config=run_config)
    except Exception:
        # Interrupt raised when the graph pauses at human_approval.
        state = graph.get_state(config=run_config)
        result = dict(state.values)

    # --- durable persistence: record the application in the SQLite ledger ---
    decision = result.get("decision", "UNKNOWN")
    app_status = {
        "REVIEW": STATUS_PENDING_REVIEW,
        "APPROVE": STATUS_APPROVED,
        "REJECT": STATUS_REJECTED,
    }.get(decision, STATUS_PENDING_REVIEW)
    try:
        ledger_row_id = record_application(
            thread_id=thread_id,
            customer_data=req.customer_data,
            risk_score=float(result.get("risk_score", 0.0)),
            risk_level=result.get("risk_level", "UNKNOWN"),
            decision=decision,
            status=app_status,
        )
    except Exception:
        metrics.error()
        ledger_row_id = None

    meta = result.get("explanation_meta", {})
    if meta.get("source") == "langchain_llm":
        metrics.llm["requests"] += 1
        metrics.llm["latency_sum_ms"] += meta.get("latency_ms", 0)
    else:
        metrics.llm["errors"] += 1
    return {
        "thread_id": thread_id,
        "application_id": result.get("application_id", ""),
        "decision": result.get("decision", "UNKNOWN"),
        "risk_score": result.get("risk_score", 0.0),
        "risk_level": result.get("risk_level", "UNKNOWN"),
        "approval_required": result.get("approval_required", False),
        "approval_status": result.get("approval_status", ""),
        "explanation": result.get("explanation", ""),
        "explanation_meta": meta,
        "audit_trail": result.get("audit_trail", []),
        "workflow_complete": result.get("workflow_complete", False),
        "ledger_application_id": ledger_row_id,
        "ledger_status": app_status,
    }


@app.post("/predict/graph/{thread_id}/approve")
def approve_graph_workflow(thread_id: str, req: GraphApprovalRequest):
    """Resume a paused workflow with a human approval decision.

    The workflow must be in REVIEW state (approval_required=True).
    """
    from langgraph.types import Command

    graph = _get_graph(thread_id)
    run_config = {"configurable": {"thread_id": thread_id}}

    try:
        result = graph.invoke(Command(resume=req.action), config=run_config)
    except Exception:
        state = graph.get_state(config=run_config)
        result = dict(state.values)

    meta = result.get("explanation_meta", {})
    if meta.get("source") == "langchain_llm":
        metrics.llm["requests"] += 1
        metrics.llm["latency_sum_ms"] += meta.get("latency_ms", 0)
    else:
        metrics.llm["errors"] += 1

    # --- durable persistence: update ledger + write the disbursement entry ---
    action_approved = req.action.strip().lower() in (
        "approve", "approved", "accept", "yes",
    )
    new_status = STATUS_APPROVED if action_approved else STATUS_REJECTED
    try:
        update_application_status(
            thread_id,
            new_status,
            decision=result.get("decision", "UNKNOWN"),
        )
    except Exception:
        metrics.error()

    disbursement = None
    if action_approved:
        # The application row persisted at workflow start is the source of
        # truth for the loan amount (the approve request carries none).
        try:
            app_row = get_application_by_thread(thread_id)
            if app_row is not None:
                disbursement = record_disbursement(
                    application_id=app_row["id"],
                    loan_amount=float(
                        app_row["customer_data"].get("loan_amount", 0.0)
                    ),
                )
        except Exception:
            metrics.error()
            disbursement = None

    return {
        "thread_id": thread_id,
        "application_id": result.get("application_id", ""),
        "decision": result.get("decision", "UNKNOWN"),
        "approval_required": result.get("approval_required", False),
        "approval_status": result.get("approval_status", ""),
        "explanation": result.get("explanation", ""),
        "explanation_meta": meta,
        "audit_trail": result.get("audit_trail", []),
        "workflow_complete": result.get("workflow_complete", False),
        "ledger_status": new_status,
        "disbursement": disbursement,
    }


@app.get("/predict/graph/{thread_id}")
def get_graph_state(thread_id: str):
    """Get the current state of a workflow (including paused workflows)."""
    graph = _get_graph(thread_id)
    run_config = {"configurable": {"thread_id": thread_id}}

    state = graph.get_state(config=run_config)
    result = dict(state.values)

    return {
        "thread_id": thread_id,
        "application_id": result.get("application_id", ""),
        "decision": result.get("decision", "UNKNOWN"),
        "risk_score": result.get("risk_score", 0.0),
        "risk_level": result.get("risk_level", "UNKNOWN"),
        "approval_required": result.get("approval_required", False),
        "approval_status": result.get("approval_status", ""),
        "explanation": result.get("explanation", ""),
        "audit_trail": result.get("audit_trail", []),
        "workflow_complete": result.get("workflow_complete", False),
    }


@app.get("/audit/{application_id}")
def get_audit_trail(application_id: str):
    """Get the audit trail for a completed or in-progress workflow.

    Walks every thread in the (file-backed) checkpointer, so the trail is
    available for workflows started before the current process too.
    """
    graph = _build_shared_graph()
    checkpointer = app.state.checkpointer
    for thread_id in checkpointer.threads():
        run_config = {"configurable": {"thread_id": thread_id}}
        state = graph.get_state(config=run_config)
        if state.values.get("application_id") == application_id:
            return {
                "application_id": application_id,
                "thread_id": thread_id,
                "audit_trail": state.values.get("audit_trail", []),
                "decision": state.values.get("decision", "UNKNOWN"),
                "workflow_complete": state.values.get("workflow_complete", False),
            }
    raise HTTPException(
        status_code=404,
        detail=f"application {application_id} not found",
    )


# ---------------------------------------------------------------------------
# Core-banking evidence endpoints (durable SQLite ledger)
# ---------------------------------------------------------------------------
@app.get("/api/applications")
def list_loan_applications(status: str | None = None):
    """Evidence endpoint: all loan applications in the durable ledger.

    Optional filter: ``?status=PENDING_REVIEW|APPROVED|REJECTED``.
    """
    rows = list_applications(status=status)
    return {"count": len(rows), "applications": rows}


@app.get("/api/disbursements")
def list_disbursement_ledger():
    """Evidence endpoint: the actual disbursement general ledger."""
    rows = list_disbursements()
    total = sum(float(r.get("loan_amount") or 0.0) for r in rows)
    return {
        "count": len(rows),
        "total_disbursed": round(total, 2),
        "disbursements": rows,
    }
# ---------------------------------------------------------------------------