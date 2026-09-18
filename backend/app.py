"""CreditFlow FastAPI service.

Endpoints (spec §12):
  GET  /health       -> service + model status
  GET  /health/live  -> liveness, no dependency checks
  GET  /health/ready -> readiness (model bundle + checkpoint gates, no writes)
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
from backend.predict_service import ArtifactContractError
from pipeline.monitoring.drift import detect_drift
from pipeline.storage.ledger import (
    init_db as ledger_init_db,
    record_application,
    record_disbursement,
    approve_pending_application,
    update_application_status,
    get_application_by_thread,
    get_audit_trail_record,
    record_inference,
    find_idempotent_approval,
    record_idempotent_approval,
    list_recent_inferences,
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
    # Warm up prediction_window from durable SQLite inference_logs
    try:
        for rec in list_recent_inferences(500):
            prediction_window.append(rec)
    except Exception:
        pass
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
    import os as _os

    meta = getattr(app.state, "meta", {})
    model_loaded = hasattr(app.state, "pipeline") and app.state.pipeline is not None
    chk = getattr(app.state, "checkpointer", None)
    corrupt = bool(getattr(chk, "checkpoint_corrupt", False))
    prod_sim = _os.environ.get("CREDITFLOW_ENV", "development").lower() == "production"
    # Readiness without customer data: bundle + checkpoint quarantine gates.
    status = "ok" if (model_loaded and not corrupt and not prod_sim) else (
        "degraded" if model_loaded else "unready"
    )
    return HealthResponse(
        status=status,
        model_loaded=model_loaded,
        model_version=meta.get("version", "unknown"),
        model_name=meta.get("model_name", "unknown"),
    )


@app.get("/health/live")
def health_live():
    """Liveness: process can serve requests. No dependency checks."""
    return {"status": "ok", "service": "creditflow-api", "version": app.version}


@app.get("/health/ready")
def health_ready():
    """Readiness: model bundle + checkpoint quarantine gates.

    Read-only: inspects app.state only, never touches customer data,
    performs no writes, opens no new connections.
    """
    import os as _os

    meta = getattr(app.state, "meta", {})
    model_loaded = hasattr(app.state, "pipeline") and app.state.pipeline is not None
    chk = getattr(app.state, "checkpointer", None)
    corrupt = bool(getattr(chk, "checkpoint_corrupt", False))
    checks = {
        "model": "ok" if model_loaded else "not-ready: pipeline not loaded",
        "checkpoint": "not-ready: checkpoint corrupt" if corrupt else "ok",
        "config": "ok",
    }
    prod_sim = _os.environ.get("CREDITFLOW_ENV", "development").lower() == "production"
    if prod_sim:
        checks["config"] = "not-ready: CREDITFLOW_ENV=production simulation gate"
    ready = all(value == "ok" for value in checks.values())
    return {
        "status": "ready" if ready else "not-ready",
        "checks": checks,
        "model_version": meta.get("version", "unknown"),
    }


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
    try:
        inference_rec = {
            **to_model_units(req.model_dump(), app.state.meta),
            "risk_probability": payload["risk_probability"],
        }
        prediction_window.append(inference_rec)
        record_inference(inference_rec)
    except Exception:
        metrics.error()
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
    from pipeline.agent.llm_provider import (
        DEFAULT_MODEL,
        DEFAULT_OLLAMA_MODEL,
        DEFAULT_GROQ_MODEL,
    )
    from pipeline.agent.explanations import PROMPT_VERSION
    provider = os.environ.get("CREDITFLOW_LLM_PROVIDER", "").lower().strip() or "template"
    if provider in ("ollama", "local", "local_ollama"):
        model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
        configured = True  # local Ollama needs no key; offline_ok probed below
        offline_ok = False
        try:
            import httpx
            from pipeline.agent.llm_provider import OLLAMA_CHAT_URL, OLLAMA_TIMEOUT

            url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_CHAT_URL).strip() or OLLAMA_CHAT_URL
            base = url.rsplit("/api/chat", 1)[0]
            r = httpx.get(f"{base}/api/tags", timeout=min(OLLAMA_TIMEOUT, 5.0))
            offline_ok = r.status_code == 200
        except Exception:
            offline_ok = False
    elif provider == "groq":
        model = os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL
        configured = bool(os.environ.get("GROQ_API_KEY", "").strip())
        offline_ok = False
    elif provider == "cloudflare":
        model = os.environ.get("CLOUDFLARE_MODEL", DEFAULT_MODEL)
        configured = bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN"))
        offline_ok = False
    else:
        model = os.environ.get("CLOUDFLARE_MODEL", DEFAULT_MODEL)
        configured = False
        offline_ok = True  # deterministic template fallback works fully offline
    return {
        "provider": provider,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "configured": configured,
        "offline_ok": offline_ok,
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
    approver_id: str = Field(default="supervisor_on_duty", description="Supervisor identity approving the disbursement")
    idempotency_key: str = Field(default="", description="Client-supplied idempotency key; required for approval")


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

    # A model/gateway/graph failure is terminal FAILED: never persist a
    # PENDING_REVIEW row and surface HTTP 503 with a stable detail code.
    if result.get("workflow_status") == "FAILED" or isinstance(result.get("error"), str) and result.get("error", "").startswith(("MODEL_BUNDLE_INVALID", "GATEWAY_UNAVAILABLE", "CHECKPOINT_CORRUPT", "OPERATIONAL_FAILURE")) and not result.get("decision"):
        code = result.get("error_code") or "OPERATIONAL_FAILURE"
        metrics.error()
        raise HTTPException(status_code=503, detail=code)
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
            application_id=result.get("application_id", ""),
            audit_trail=result.get("audit_trail", []),
        )
    except Exception:
        metrics.error()
        ledger_row_id = None

    try:
        inference_rec = {
            **to_model_units(req.customer_data, app.state.meta),
            "risk_probability": float(result.get("risk_score", 0.0)),
        }
        prediction_window.append(inference_rec)
        record_inference(inference_rec)
    except Exception:
        pass

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
        "error": result.get("error", ""),
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
        "basel_metrics": result.get("basel_metrics", {}),
        "pricing": result.get("pricing", {}),
        "cic_report": result.get("cic_report", {}),
        "bank_statement": result.get("bank_statement", {}),
        "authority_level": result.get("authority_level", "STP"),
        "amortization_schedule": result.get("amortization_schedule", []),
        "vietqr_url": result.get("vietqr_url", ""),
        "loan_agreement_pdf": result.get("loan_agreement_pdf", ""),
        "tuned_threshold": result.get("tuned_threshold"),
        "business_cost": result.get("business_cost", {}),
        "reasons": result.get("reasons", []),
    }


@app.post("/predict/graph/{thread_id}/approve")
def approve_graph_workflow(thread_id: str, req: GraphApprovalRequest):
    """Resume a paused workflow with a human approval decision.

    Replay-safe single transaction: reserve PENDING_REVIEW with approver +
    idempotency key, resume the graph, disburse only on final APPROVE.
    The ``action`` string is informational; persisted state is authority.
    """
    from langgraph.types import Command

    graph = _get_graph(thread_id)
    run_config = {"configurable": {"thread_id": thread_id}}

    persisted = get_application_by_thread(thread_id)
    if persisted is None or persisted.get("status") != STATUS_PENDING_REVIEW:
        raise HTTPException(status_code=409, detail="APPLICATION_NOT_PENDING")
    action_approved = req.action.strip().lower() in (
        "approve", "approved", "accept", "yes",
    )
    # Backwards-compatible: old clients omit the key; the server mints a
    # per-thread key so approval still succeeds exactly once.
    idem_key = (req.idempotency_key or "").strip() or f"auto-{thread_id}"
    # Idempotency gate (Plan 03): a replayed key returns the stored approval
    # without resuming the graph or disbursing twice.
    replayed = find_idempotent_approval(thread_id, idem_key)
    if replayed is not None:
        replayed = dict(replayed)
        replayed["replay"] = True
        return replayed
    if not action_approved:
        try:
            update_application_status(
                thread_id, STATUS_REJECTED, decision="REJECT",
                audit_trail=(persisted.get("audit_trail") or []),
            )
        except Exception:
            metrics.error()
        return {
            "thread_id": thread_id,
            "application_id": persisted.get("application_id", ""),
            "decision": "REJECT",
            "approval_required": False,
            "approval_status": "REJECTED",
            "workflow_complete": True,
            "ledger_status": STATUS_REJECTED,
            "disbursement": None,
        }
    try:
        reservation = approve_pending_application(
            thread_id, req.approver_id.strip() or "supervisor_on_duty",
            idem_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        match = next(
            (d for d in list_disbursements()
             if d.get("idempotency_key") == idem_key),
            None,
        )
        if match is not None:
            return {
                "thread_id": thread_id,
                "application_id": persisted.get("application_id", ""),
                "decision": "APPROVE",
                "approval_required": False,
                "approval_status": "APPROVED",
                "workflow_complete": True,
                "ledger_status": STATUS_APPROVED,
                "disbursement": match,
                "replay": True,
            }
        raise HTTPException(status_code=409, detail="APPROVAL_CONFLICT")
    if reservation.get("replay"):
        match = next(
            (d for d in list_disbursements()
             if d.get("idempotency_key") == idem_key),
            None,
        )
        return {
            "thread_id": thread_id,
            "application_id": persisted.get("application_id", ""),
            "decision": "APPROVE",
            "approval_required": False,
            "approval_status": "APPROVED",
            "workflow_complete": True,
            "ledger_status": STATUS_APPROVED,
            "disbursement": match,
            "replay": True,
        }
    try:
        result = graph.invoke(Command(resume="approve"), config=run_config)
    except Exception as exc:
        try:
            update_application_status(thread_id, STATUS_PENDING_REVIEW)
        except Exception:
            pass
        metrics.error()
        raise HTTPException(status_code=502, detail=f"GRAPH_RESUME_FAILED: {exc}")

    meta = result.get("explanation_meta", {})
    if meta.get("source") == "langchain_llm":
        metrics.llm["requests"] += 1
        metrics.llm["latency_sum_ms"] += meta.get("latency_ms", 0)
    else:
        metrics.llm["errors"] += 1
    if result.get("decision") != "APPROVE":
        try:
            update_application_status(
                thread_id, STATUS_REJECTED,
                decision=result.get("decision", "REJECT"),
                audit_trail=result.get("audit_trail", []),
            )
        except Exception:
            metrics.error()
        raise HTTPException(status_code=409, detail="GRAPH_RESUME_NOT_APPROVED")
    disbursement = None
    try:
        app_row = get_application_by_thread(thread_id)
        if app_row is not None:
            disbursement = record_disbursement(
                application_id=app_row["id"],
                loan_amount=float(app_row["customer_data"].get("loan_amount", 0.0)),
                idempotency_key=idem_key,
            )
            update_application_status(
                thread_id, STATUS_APPROVED,
                decision=result.get("decision", "APPROVE"),
                audit_trail=result.get("audit_trail", []),
            )
    except Exception:
        metrics.error()
        disbursement = None

    response = {
        "thread_id": thread_id,
        "application_id": result.get("application_id", ""),
        "decision": result.get("decision", "UNKNOWN"),
        "approval_required": result.get("approval_required", False),
        "approval_status": result.get("approval_status", ""),
        "explanation": result.get("explanation", ""),
        "explanation_meta": meta,
        "audit_trail": result.get("audit_trail", []),
        "workflow_complete": result.get("workflow_complete", False),
        "ledger_status": STATUS_APPROVED,
        "disbursement": disbursement,
        "basel_metrics": result.get("basel_metrics", {}),
        "pricing": result.get("pricing", {}),
        "cic_report": result.get("cic_report", {}),
        "bank_statement": result.get("bank_statement", {}),
        "authority_level": result.get("authority_level", "STP"),
        "amortization_schedule": result.get("amortization_schedule", []),
        "vietqr_url": result.get("vietqr_url", ""),
        "loan_agreement_pdf": result.get("loan_agreement_pdf", ""),
        "tuned_threshold": result.get("tuned_threshold"),
        "reasons": result.get("reasons", []),
    }
    # Terminal outcome: persist idempotent record + outbox in one call so a
    # replay returns exactly this response without a second disbursement.
    try:
        record_idempotent_approval(thread_id, idem_key, response)
    except Exception:
        metrics.error()
    return response


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
        "error": result.get("error", ""),
        "risk_score": result.get("risk_score", 0.0),
        "risk_level": result.get("risk_level", "UNKNOWN"),
        "approval_required": result.get("approval_required", False),
        "approval_status": result.get("approval_status", ""),
        "explanation": result.get("explanation", ""),
        "explanation_meta": result.get("explanation_meta", {}),
        "audit_trail": result.get("audit_trail", []),
        "workflow_complete": result.get("workflow_complete", False),
        "basel_metrics": result.get("basel_metrics", {}),
        "pricing": result.get("pricing", {}),
        "cic_report": result.get("cic_report", {}),
        "bank_statement": result.get("bank_statement", {}),
        "authority_level": result.get("authority_level", "STP"),
        "amortization_schedule": result.get("amortization_schedule", []),
        "vietqr_url": result.get("vietqr_url", ""),
        "loan_agreement_pdf": result.get("loan_agreement_pdf", ""),
        "tuned_threshold": result.get("tuned_threshold"),
        "reasons": result.get("reasons", []),
    }


@app.get("/audit/{application_id}")
def get_audit_trail(application_id: str):
    """Get the audit trail for a completed or in-progress workflow.

    First queries the SQLite ledger in O(1) by application_id or thread_id.
    Falls back to walking the checkpointer only if not found in the ledger.
    """
    rec = get_audit_trail_record(application_id)
    if rec is not None and rec.get("audit_trail"):
        return {
            "application_id": rec.get("application_id") or application_id,
            "thread_id": rec.get("thread_id", ""),
            "audit_trail": rec.get("audit_trail", []),
            "decision": rec.get("decision", "UNKNOWN"),
            "workflow_complete": rec.get("status") in (STATUS_APPROVED, STATUS_REJECTED),
        }

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
@app.get("/applications")
@app.get("/api/applications")
def list_loan_applications(status: str | None = None):
    """Evidence endpoint: all loan applications in the durable ledger.

    Optional filter: ``?status=PENDING_REVIEW|APPROVED|REJECTED``.
    """
    rows = list_applications(status=status)
    return {"count": len(rows), "applications": rows}


@app.get("/disbursements")
@app.get("/api/disbursements")
def list_disbursement_ledger():
    """Evidence endpoint: the actual disbursement general ledger.

    Each row carries ``hash_valid`` (SHA-256 recomputed from the hashed fields),
    so an edited row is visible here — tamper evidence, not immutability.
    """
    rows = list_disbursements()
    total = sum(float(r.get("loan_amount") or 0.0) for r in rows)
    return {
        "count": len(rows),
        "total_disbursed": round(total, 2),
        "all_hashes_valid": all(bool(r.get("hash_valid")) for r in rows),
        "disbursements": rows,
    }
# ---------------------------------------------------------------------------