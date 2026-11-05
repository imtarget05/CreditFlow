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
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import pandas as pd
from fastapi import FastAPI, HTTPException, Response, Security
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.predict_service import (
    ArtifactContractError,
    load_production_model,
    predict_risk,
    to_model_units,
)
from backend.security import (
    Principal,
    allowed_origins,
    authenticate_user,
    create_access_token,
    demo_login_enabled,
    ensure_authority,
    is_production,
    is_user_revoked,
    require_configured_key,
    require_principal,
    require_sacombank_oidc_configuration,
    require_scoring_access,
    require_service_principal,
    require_workflow_principal,
)
from backend.user_management import (
    RoleHistoryEntry,
    User,
    UserCreate,
    UserSession,
    UserUpdate,
    user_manager,
)
from pipeline.agent.subagents.credit_analyst_assistant import CreditAnalystAssistant
from pipeline.disbursement.core_banking_adapter import CoreBankingAdapter
from pipeline.disbursement.reconciliation import ReconciliationEngine
from pipeline.monitoring.drift import detect_drift
from pipeline.monitoring.outcome_tracking import (
    CohortMetrics,
    LoanOutcome,
    OutcomeTracker,
    OutcomeUpdate,
    OverallMetrics,
)
from pipeline.notification.adapter import NotificationAdapter
from pipeline.storage.ledger import (
    STATUS_APPROVED,
    STATUS_PENDING_REVIEW,
    STATUS_REJECTED,
    approve_pending_application,
    find_idempotent_approval,
    get_application_by_id,
    get_application_by_thread,
    get_audit_trail_record,
    list_applications,
    list_disbursements,
    list_recent_inferences,
    record_application,
    record_disbursement,
    record_idempotent_approval,
    record_inference,
    update_application_status,
)
from pipeline.storage.ledger import (
    init_db as ledger_init_db,
)
from pipeline.storage.work_items import (
    create_work_item,
    enqueue_due_reminders,
    get_work_item,
    init_work_items,
    list_due_notifications,
    list_work_items,
    transition_work_item,
)
from pipeline.workflow.default_calendar import (
    compute_due_at,
    get_default_calendar,
    get_sla_hours,
)
from pipeline.workflow.documents import (
    evaluate_document_checklist,
    required_document_types,
)
from pipeline.workflow.duplicates import find_possible_profile_matches

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
    runtime_mode: str
    data_classification: str
    real_integrations_enabled: bool


class PredictResponse(BaseModel):
    risk_probability: float
    risk_level: str
    decision: str
    recommendation: str | None = None
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

outcome_tracker = OutcomeTracker()
metrics.start()

# Recent inference window for drift monitoring (P11): the last N successful
# predictions, in-process (demo scope — same as Metrics; no external store).
prediction_window: deque = deque(maxlen=500)


# ---------------------------------------------------------------------------
# App + lifespan (load model once)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed: a production deployment without a shared API key must not
    # start at all, rather than come up with every protected route open.
    require_configured_key()
    require_sacombank_oidc_configuration()
    app.state.pipeline, app.state.meta = load_production_model()
    # Durable ledger for applications + disbursements (core-banking slice).
    ledger_init_db()
    init_work_items()
    # Initialize production Core Banking Adapter + Notification Adapter
    app.state.core_banking = CoreBankingAdapter(default_maker_id="creditflow-system")
    app.state.notifications = NotificationAdapter()
    # Warm up prediction_window from durable SQLite inference_logs
    try:
        for rec in list_recent_inferences(500):
            prediction_window.append(rec)
    except Exception:
        logger.exception("Failed to warm prediction_window from inference logs")
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
    # Explicit allow-list (backend/security.py:allowed_origins). A wildcard
    # origin on a money-moving API is refused there. No Pages wildcard regex:
    # only the explicitly configured GitHub Pages origin may call this API.
    allow_origins=allowed_origins(),
    allow_origin_regex=None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization", "X-CreditFlow-API-Key"],
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
    chk = getattr(app.state, "checkpointer", None)
    corrupt = bool(getattr(chk, "checkpoint_corrupt", False))
    # Read-only: bundle + checkpoint quarantine gates.
    # Production rejects the synthetic agent gateways and demo model scoring;
    # /health/ready and /predict below also fail closed until model approval.
    status = "ok" if (model_loaded and not corrupt) else (
        "degraded" if model_loaded else "unready"
    )
    showcase = os.environ.get("CREDITFLOW_SHOWCASE", "").strip().lower() in {"1", "true", "yes"}
    return HealthResponse(
        status=status,
        model_loaded=model_loaded,
        model_version=meta.get("version", "unknown"),
        model_name=meta.get("model_name", "unknown"),
        runtime_mode="showcase" if showcase else ("production" if is_production() else "development"),
        data_classification="synthetic demo data" if showcase else "application-configured",
        real_integrations_enabled=False if showcase else is_production(),
    )


@app.get("/health/live")
def health_live():
    """Liveness: process can serve requests. No dependency checks."""
    return {"status": "ok", "service": "creditflow-api", "version": app.version}


@app.get("/health/ready")
def health_ready(response: Response):
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
    # The bundled model was trained on synthetic proxy data and has not been
    # independently approved for bank use. Keep production unready until a
    # separately reviewed model release replaces this demo path.
    production = _os.environ.get("CREDITFLOW_ENV", "development").lower() == "production"
    if production:
        checks["model"] = "not-ready: model validation and release approval required"
    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = 503
    return {
        "status": "ready" if ready else "not-ready",
        "checks": checks,
        # Which store actually keeps the paused workflows: "postgres" |
        # "sqlite" (shared, DATABASE_URL) or "file" (single-process local file).
        "checkpoint_backend": getattr(app.state, "checkpoint_backend", "unset"),
        "model_version": meta.get("version", "unknown"),
        "runtime_mode": "production (demo model disabled)" if production else "development",
    }


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest, principal: Principal | None = Security(require_scoring_access)):  # noqa: B008
    if is_production():
        raise HTTPException(status_code=503, detail="MODEL_NOT_VALIDATED_FOR_PRODUCTION")
    t0 = time.perf_counter()
    try:
        payload = predict_risk(app.state.pipeline, req.model_dump(), app.state.meta)
    except ValueError as exc:
        metrics.error()
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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

    if report.get("status") == "DRIFT_DETECTED":
        try:
            from pipeline.storage.ledger import emit_domain_event
            drifted_cols = [
                f for f, v in report.get("features", {}).items()
                if isinstance(v, dict) and v.get("status") == "DRIFT_DETECTED"
            ]
            emit_domain_event(
                "ModelDriftDetected",
                {
                    "status": "DRIFT_DETECTED",
                    "drifted_features": drifted_cols,
                    "n_recent": report.get("n_recent"),
                },
                destination="drift_alerts",
            )
        except Exception:  # noqa: BLE001
            metrics.error()

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

    from pipeline.agent.explanations import PROMPT_VERSION
    from pipeline.agent.llm_provider import (
        DEFAULT_GROQ_MODEL,
        DEFAULT_LMSTUDIO_MODEL,
        DEFAULT_MODEL,
        DEFAULT_OLLAMA_MODEL,
    )
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
        except Exception:  # noqa: BLE001
            offline_ok = False
    elif provider in ("lmstudio", "local_openai", "lms"):
        # Local LAN OpenAI-compatible endpoint (LM Studio / llm-gateway).
        from pipeline.agent.llm_provider import (
            DEFAULT_LMSTUDIO_CHAT_URL,
            LMSTUDIO_TIMEOUT,
        )

        model = os.environ.get("LMSTUDIO_MODEL", DEFAULT_LMSTUDIO_MODEL).strip() or DEFAULT_LMSTUDIO_MODEL
        configured = True  # local endpoint needs no key
        offline_ok = False
        try:
            import httpx

            url = os.environ.get("LMSTUDIO_BASE_URL", DEFAULT_LMSTUDIO_CHAT_URL).strip() or DEFAULT_LMSTUDIO_CHAT_URL
            base = url.rsplit("/chat/completions", 1)[0]
            r = httpx.get(f"{base}/models", timeout=min(LMSTUDIO_TIMEOUT, 5.0))
            offline_ok = r.status_code == 200
        except Exception:  # noqa: BLE001
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


@app.get("/metrics/prometheus")
def metrics_prometheus():
    """Prometheus text exposition (scraped by observability/prometheus).

    Kept alongside the JSON GET /metrics for backward compatibility.
    Exposes request counters, error counters and average predict latency.
    """
    from fastapi.responses import PlainTextResponse

    snap = metrics.snapshot()
    lines = [
        "# HELP creditflow_requests_total Total HTTP requests handled.",
        "# TYPE creditflow_requests_total counter",
        f"creditflow_requests_total {snap['requests']['total']}",
    ]
    for endpoint, count in snap["requests"].items():
        if endpoint == "total":
            continue
        lines.append(f'creditflow_requests_by_endpoint{{endpoint="{endpoint}"}} {count}')
    lines += [
        "# HELP creditflow_errors_total Total handler errors.",
        "# TYPE creditflow_errors_total counter",
        f"creditflow_errors_total {snap['errors']['total']}",
        "# HELP creditflow_avg_predict_latency_ms Average /predict latency.",
        "# TYPE creditflow_avg_predict_latency_ms gauge",
        f"creditflow_avg_predict_latency_ms {snap['avg_predict_latency_ms']}",
        "# HELP creditflow_uptime_seconds Process uptime.",
        "# TYPE creditflow_uptime_seconds gauge",
        f"creditflow_uptime_seconds {snap['uptime_seconds']}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


class LoginRequest(BaseModel):
    username: str
    password: str


class WorkItemActionRequest(BaseModel):
    action: str = Field(pattern="^(claim|complete|assign|escalate|request_supplement|resume)$")
    request_id: str = Field(min_length=8, max_length=100)
    reason: str = Field(min_length=1, max_length=1000)
    assignee: str = Field(default="", max_length=100)


class MonitoringCheckRequest(BaseModel):
    run_id: str = Field(min_length=8, max_length=100)


class ReminderRunRequest(BaseModel):
    now: str | None = None


@app.post("/api/auth/login")
def login(req: LoginRequest):
    """Authenticate underwriter credentials and issue a signed JWT token."""
    if not demo_login_enabled():
        raise HTTPException(status_code=404, detail="Demo login is disabled")
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="INVALID_CREDENTIALS: check username or password.",
        )
    token = create_access_token(user["username"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": user,
    }


# ---------------------------------------------------------------------------
# LangGraph decision workflow endpoints (spec §16 architecture)
# ---------------------------------------------------------------------------
# One shared compiled graph.  Workflow state lives in the checkpointer
# (keyed by thread_id), NOT in RAM — a server restart restores every paused
# workflow, so POST /predict/graph/{thread_id}/approve works across restarts.
# The store is selected by create_checkpointer(): DATABASE_URL pointing at
# Postgres/SQLite gives shared storage every replica can reach; without it the
# single-process FileCheckpointSaver (pipeline/agent/checkpointer.py) is used
# and the store path is the local checkpoint file.
_shared_graph: Any = None


def _build_shared_graph():
    """Build the workflow graph once, on a shared or file checkpointer."""
    global _shared_graph
    if _shared_graph is None:
        from pipeline.agent import checkpointer as checkpointer_module
        from pipeline.agent.graph import build_credit_graph

        checkpointer = getattr(app.state, "checkpointer", None)
        if checkpointer is None:
            checkpointer, backend = checkpointer_module.create_checkpointer()
            app.state.checkpointer = checkpointer
            app.state.checkpoint_backend = backend
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
    if hasattr(app.state, "checkpoint_backend"):
        del app.state.checkpoint_backend


class GraphStartRequest(BaseModel):
    """Request body to start a LangGraph credit decision workflow."""
    customer_data: dict


class GraphApprovalRequest(BaseModel):
    """Request body to resume a paused workflow with human approval."""
    action: str = Field(..., description="'approve' or 'reject'")
    note: str = ""
    approver_id: str = Field(
        default="",
        description=(
            "Deprecated and no longer trusted: the approver identity comes "
            "from the authenticated API key. A value that disagrees with the "
            "authenticated identity is rejected with 422."
        ),
    )
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
def start_graph_workflow(
    req: GraphStartRequest,
    principal: Principal | None = Security(require_workflow_principal),  # noqa: B008
):
    """Start a LangGraph credit decision workflow.

    Returns the workflow state. If the decision routes to REVIEW, the workflow
    pauses at human_approval — use ``POST /predict/graph/{thread_id}/approve``
    to resume.
    """
    if is_production():
        raise HTTPException(status_code=503, detail="PRODUCTION_WORKFLOW_NOT_INTEGRATED")
    from pipeline.agent.graph import create_workflow_run_id

    graph = _build_shared_graph()
    thread_id = create_workflow_run_id()

    initial_state = {"customer_data": req.customer_data, "request_meta": {}}
    run_config = {"configurable": {"thread_id": thread_id}}

    try:
        result = graph.invoke(initial_state, config=run_config)
    except Exception:  # noqa: BLE001
        # Interrupt raised when the graph pauses at human_approval.
        state = graph.get_state(config=run_config)
        result = dict(state.values)

    # A model/gateway/graph failure is terminal FAILED: never persist a
    # PENDING_REVIEW row and surface HTTP 503 with a stable detail code.
    if result.get("workflow_status") == "FAILED" or isinstance(result.get("error"), str) and result.get("error", "").startswith(("MODEL_BUNDLE_INVALID", "GATEWAY_UNAVAILABLE", "CHECKPOINT_CORRUPT", "OPERATIONAL_FAILURE")) and not result.get("decision"):
        code = result.get("error_code") or "OPERATIONAL_FAILURE"
        metrics.error()
        raise HTTPException(status_code=503, detail=code)
    try:
        possible_duplicates = find_possible_profile_matches(req.customer_data)
    except Exception:  # noqa: BLE001
        metrics.error()
        possible_duplicates = []
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
    except Exception:  # noqa: BLE001
        metrics.error()
        ledger_row_id = None

    if ledger_row_id is not None:
        try:
            create_work_item(
                result.get("application_id") or thread_id,
                "DOCUMENT_CHECK",
                "UNDERWRITER_L1",
                f"intake:{thread_id}",
            )
            if possible_duplicates:
                create_work_item(
                    result.get("application_id") or thread_id,
                    "EXCEPTION",
                    "RISK_COMMITTEE_L2",
                    f"duplicate-review:{thread_id}",
                )
        except Exception:  # noqa: BLE001
            metrics.error()
            raise HTTPException(status_code=503, detail="WORK_QUEUE_UNAVAILABLE")

    try:
        inference_rec = {
            **to_model_units(req.customer_data, app.state.meta),
            "risk_probability": float(result.get("risk_score", 0.0)),
        }
        prediction_window.append(inference_rec)
        record_inference(inference_rec)
    except Exception:  # noqa: BLE001
        metrics.error()

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
        "possible_duplicate_application_ids": possible_duplicates,
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
def approve_graph_workflow(
    thread_id: str,
    req: GraphApprovalRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Resume a paused workflow with a human approval decision.

    Replay-safe single transaction: reserve PENDING_REVIEW with approver +
    idempotency key, resume the graph, disburse only on final APPROVE.
    The ``action`` string is informational; persisted state is authority.

    Requires the shared API key; ``approver_id`` is taken from the
    authenticated identity and the key must hold at least the credit
    authority this application was routed to.
    """
    from langgraph.types import Command

    if is_production():
        raise HTTPException(status_code=503, detail="PRODUCTION_APPROVAL_NOT_INTEGRATED")

    # A self-asserted approver is a spoof attempt, not a compatibility
    # nicety: reject it loudly instead of silently dropping it, so a client
    # can never believe its own identity was recorded.
    claimed = (req.approver_id or "").strip()
    if claimed and claimed != principal.identity:
        raise HTTPException(
            status_code=422,
            detail=(
                "APPROVER_ID_NOT_AUTHORIZED: approver_id is derived from the "
                "authenticated API key, not from the request body."
            ),
        )

    graph = _get_graph(thread_id)
    run_config = {"configurable": {"thread_id": thread_id}}
    # Authorise against the authority the workflow itself demanded.
    ensure_authority(
        principal, graph.get_state(config=run_config).values.get("authority_level")
    )

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
        except Exception:  # noqa: BLE001
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
            thread_id, principal.identity, idem_key,
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
        result = graph.invoke(Command(resume={"action": "approve", "approver_id": principal.identity}), config=run_config)
    except Exception as exc:  # noqa: BLE001
        try:
            update_application_status(thread_id, STATUS_PENDING_REVIEW)
        except Exception:
            logger.exception("Failed to update status")
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
        except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        metrics.error()
    return response


@app.get("/predict/graph/{thread_id}")
def get_graph_state(
    thread_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Get the current state of a workflow (including paused workflows)."""
    if is_production():
        app_row = get_application_by_thread(thread_id)
        if app_row is None:
            raise HTTPException(status_code=404, detail="APPLICATION_NOT_FOUND")
        _ensure_application_scope(principal, app_row)
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
def get_audit_trail(
    application_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Get the audit trail for a completed or in-progress workflow.

    First queries the SQLite ledger in O(1) by application_id or thread_id.
    Falls back to walking the checkpointer only if not found in the ledger.
    """
    if is_production():
        app_row = get_application_by_id(application_id)
        if app_row is None:
            raise HTTPException(status_code=404, detail="APPLICATION_NOT_FOUND")
        _ensure_application_scope(principal, app_row)
    rec = get_audit_trail_record(application_id)
    if rec is not None and rec.get("audit_trail"):
        return {
            "application_id": rec.get("application_id") or application_id,
            "thread_id": rec.get("thread_id", ""),
            "audit_trail": rec.get("audit_trail", []),
            "decision": rec.get("decision", "UNKNOWN"),
            "workflow_complete": rec.get("status") in (STATUS_APPROVED, STATUS_REJECTED),
        }

    # Postgres path: checkpointer has no threads() method. Query the durable
    # loan_applications ledger instead to find the thread for this application.
    app_row = get_application_by_id(application_id)
    if app_row is not None:
        thread_id = app_row.get("thread_id", "")
        if thread_id:
            graph = _build_shared_graph()
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
def _ensure_application_scope(principal: Principal, app_row: dict) -> None:
    """Hide a bank case unless it is in the staff member's work queue."""
    if not is_production():
        return
    visible = {item["application_id"] for item in list_work_items(principal)}
    case_ids = {app_row.get("application_id"), app_row.get("thread_id")}
    if not visible.intersection(case_ids):
        raise HTTPException(status_code=404, detail="APPLICATION_NOT_FOUND")


@app.get("/api/work-items")
def get_my_work_items(principal: Principal = Security(require_principal)):  # noqa: B008
    """Return only work assigned to this identity or its unclaimed role queue."""
    items = list_work_items(principal)
    return {"count": len(items), "work_items": items}


@app.get("/api/notifications")
def get_my_notifications(principal: Principal = Security(require_principal)):  # noqa: B008
    """Return overdue in-app reminders visible to the current reviewer."""
    notifications = list_due_notifications(principal)
    return {"count": len(notifications), "notifications": notifications}


@app.post("/api/work-items/reminders/run")
def run_work_item_reminders(
    body: ReminderRunRequest,
    principal: Principal = Security(require_service_principal),  # noqa: B008
):
    now = body.now or datetime.now(UTC).isoformat()
    try:
        return {"queued": enqueue_due_reminders(now)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"INVALID_REMINDER_TIME: {exc}")


@app.post("/api/monitoring/check")
def run_monitoring_check(
    body: MonitoringCheckRequest,
    principal: Principal = Security(require_service_principal),  # noqa: B008
):
    """Use the serving service's drift calculation; create one risk task per run."""
    result = drift_report()
    work_item = None
    if result["drift"]["status"] == "DRIFT_DETECTED":
        version = getattr(app.state, "meta", {}).get("version", "unknown")
        work_item = create_work_item(
            f"model:{version}", "EXCEPTION", "RISK_COMMITTEE_L2", f"monitor:{body.run_id}"
        )
    return {**result, "work_item": work_item}


@app.post("/api/work-items/{work_item_id}/actions")
def act_on_work_item(
    work_item_id: str,
    body: WorkItemActionRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    try:
        return transition_work_item(
            work_item_id, body.action, principal, body.request_id, body.reason,
            assignee=body.assignee,
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="WORK_ITEM_NOT_FOUND")
    except PermissionError:
        raise HTTPException(status_code=403, detail="WORK_ITEM_FORBIDDEN")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=f"WORK_ITEM_CONFLICT: {exc}")


@app.post("/api/work-items/{work_item_id}/assistant")
def get_credit_analyst_brief(
    work_item_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Return a read-only, role-scoped evidence brief for a reviewer."""
    item = get_work_item(work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="WORK_ITEM_NOT_FOUND")
    if item["assigned_role"] != principal.role or item["assigned_to"] not in ("", principal.identity):
        raise HTTPException(status_code=404, detail="WORK_ITEM_NOT_FOUND")
    application = get_application_by_id(item["application_id"])
    if application is None:
        raise HTTPException(status_code=404, detail="APPLICATION_NOT_FOUND")
    customer_data = application.get("customer_data") or {}
    if isinstance(customer_data, dict) and customer_data:
        try:
            current_prediction = predict(PredictRequest(**customer_data))
            application = {
                **application,
                "current_model_evidence": {
                    "model_name": current_prediction.model_name,
                    "model_version": current_prediction.model_version,
                    "risk_probability": current_prediction.risk_probability,
                    "reasons": current_prediction.reasons,
                    "matches_recorded_probability": abs(
                        float(application.get("risk_score") or 0.0)
                        - current_prediction.risk_probability
                    ) < 1e-6,
                },
            }
        except (TypeError, ValueError, ArtifactContractError):
            # Keep the brief available, but don't present a partial/current score
            # as historical evidence if the saved profile cannot be rescored.
            pass
    return CreditAnalystAssistant().review(item, application)


@app.get("/applications")
@app.get("/api/applications")
def list_loan_applications(
    status: str | None = None,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Evidence endpoint: all loan applications in the durable ledger.

    Optional filter: ``?status=PENDING_REVIEW|APPROVED|REJECTED``.

    Authenticated rather than redacted: every row carries the full
    ``customer_data`` blob, so redaction would gut the endpoint while still
    leaking which applications exist.
    """
    rows = list_applications(status=status)
    if is_production():
        visible = {item["application_id"] for item in list_work_items(principal)}
        rows = [row for row in rows if row.get("application_id") in visible or row.get("thread_id") in visible]
    return {"count": len(rows), "applications": rows}


@app.get("/disbursements")
@app.get("/api/disbursements")
def list_disbursement_ledger(principal: Principal = Security(require_principal)):  # noqa: B008
    """Evidence endpoint: the actual disbursement general ledger.

    Each row carries ``hash_valid`` (SHA-256 recomputed from the hashed fields),
    so an edited row is visible here — tamper evidence, not immutability.

    Authenticated rather than redacted: the money totals are the sensitive
    part, so hiding them would not protect anything.
    """
    if is_production():
        raise HTTPException(status_code=503, detail="CORE_BANKING_LEDGER_NOT_INTEGRATED")
    rows = list_disbursements()
    total = sum(float(r.get("loan_amount") or 0.0) for r in rows)
    return {
        "count": len(rows),
        "total_disbursed": round(total, 2),
        "all_hashes_valid": all(bool(r.get("hash_valid")) for r in rows),
        "disbursements": rows,
    }


class OutcomeRecordRequest(BaseModel):
    """Request body for recording a loan outcome (application_id is in the path)."""

    actual_default: bool = False
    default_date: date | None = None
    actual_repayment_amount: float | None = None
    total_repaid: float | None = None
    num_missed_payments: int = 0
    last_payment_date: date | None = None
    outcome_status: str = "PERFORMING"
    cohort_month: str
    source: str = "CORE_BANKING"


@app.post("/api/outcomes/{application_id}", response_model=LoanOutcome)
def record_loan_outcome(
    application_id: int,
    body: OutcomeRecordRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Record (or replace) the realised outcome for a loan application.

    Requires ADMIN or RISK_COMMITTEE_L2 authority (ADMIN outranks L2, so a
    single ``RISK_COMMITTEE_L2`` check admits both).
    """
    ensure_authority(principal, "RISK_COMMITTEE_L2")
    try:
        return outcome_tracker.record_outcome(
            application_id=application_id,
            actual_default=body.actual_default,
            default_date=body.default_date,
            actual_repayment_amount=body.actual_repayment_amount,
            total_repaid=body.total_repaid,
            num_missed_payments=body.num_missed_payments,
            last_payment_date=body.last_payment_date,
            outcome_status=body.outcome_status,
            cohort_month=body.cohort_month,
            source=body.source,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/api/outcomes/{application_id}", response_model=LoanOutcome)
def get_loan_outcome(
    application_id: int,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Return the outcome record for a loan application, or 404."""
    ensure_authority(principal, "RISK_COMMITTEE_L2")
    outcome = outcome_tracker.get_outcome(application_id)
    if outcome is None:
        raise HTTPException(
            status_code=404, detail=f"Outcome for application {application_id} not found"
        )
    return outcome


@app.get("/api/outcomes", response_model=list[LoanOutcome])
def list_loan_outcomes(
    cohort_month: str | None = None,
    outcome_status: str | None = None,
    source: str | None = None,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """List outcomes, optionally filtered by cohort month, status, or source."""
    ensure_authority(principal, "RISK_COMMITTEE_L2")
    return outcome_tracker.list_outcomes(
        cohort_month=cohort_month,
        outcome_status=outcome_status,
        source=source,
    )


@app.put("/api/outcomes/{application_id}", response_model=LoanOutcome)
def update_loan_outcome(
    application_id: int,
    body: OutcomeUpdate,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Partially update an existing outcome (ADMIN or RISK_COMMITTEE_L2)."""
    ensure_authority(principal, "RISK_COMMITTEE_L2")
    updates = body.model_dump(exclude_unset=True)
    outcome = outcome_tracker.update_outcome(application_id, **updates)
    if outcome is None:
        raise HTTPException(
            status_code=404, detail=f"Outcome for application {application_id} not found"
        )
    return outcome


@app.get("/api/monitoring/cohort", response_model=list[CohortMetrics])
def cohort_metrics_endpoint(
    cohort_month: str | None = None,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Cohort metrics: default rate, repayment rate, avg missed payments.

    Omit ``cohort_month`` to aggregate every cohort.
    """
    ensure_authority(principal, "RISK_COMMITTEE_L2")
    return outcome_tracker.compute_cohort_metrics(cohort_month=cohort_month)


@app.get("/api/monitoring/overall", response_model=OverallMetrics)
def overall_metrics_endpoint(
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Portfolio-wide outcome metrics: default rate, NPL rate, repayment rate."""
    ensure_authority(principal, "RISK_COMMITTEE_L2")
    return outcome_tracker.compute_overall_metrics()


@app.post("/applications")
@app.post("/api/applications")
def create_loan_application(
    body: dict,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Create and start a loan application workflow.

    Accepts customer profile data directly or under {"customer_data": ...}.
    """
    customer_data = body.get("customer_data", body)
    req = GraphStartRequest(customer_data=customer_data)
    return start_graph_workflow(req)


@app.get("/applications/{application_id}")
@app.get("/api/applications/{application_id}")
def get_loan_application(
    application_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Fetch loan application by id, application_id, or thread_id."""
    app_row = get_application_by_id(application_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application {application_id} not found",
        )
    _ensure_application_scope(principal, app_row)
    return app_row


@app.post("/applications/{application_id}/documents")
@app.post("/api/applications/{application_id}/documents")
def upload_application_documents(
    application_id: str,
    body: dict,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Attach document metadata to a loan application and persist it.

    Stores one row in the ``application_documents`` ledger table keyed by
    the canonical application_id, so the record survives restarts and is
    listable via ``GET .../documents``. Only metadata is stored (no file
    bytes); the file name is sanitised to a base name.
    """
    if is_production():
        raise HTTPException(status_code=503, detail="DOCUMENT_STORAGE_NOT_INTEGRATED")
    from pipeline.storage.ledger import emit_domain_event as _emit_event
    from pipeline.storage.ledger import record_document as _record_doc

    app_row = get_application_by_id(application_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application {application_id} not found",
        )
    _ensure_application_scope(principal, app_row)
    canonical_id = app_row.get("application_id") or application_id
    import uuid
    doc_id = f"doc_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    try:
        stored = _record_doc(
            application_id=canonical_id,
            document_id=doc_id,
            document_type=(body or {}).get("document_type", "income_verification"),
            file_name=(body or {}).get("file_name", "document.pdf"),
            uploaded_by=principal.identity,
            file_content=(body or {}).get("file_content") or (body or {}).get("content_base64"),
            file_size_bytes=(body or {}).get("file_size_bytes"),
            checksum_sha256=(body or {}).get("checksum_sha256"),
            mime_type=(body or {}).get("mime_type", "application/pdf"),
        )
        _emit_event(
            "DocumentUploaded",
            {
                "application_id": canonical_id,
                "document_id": doc_id,
                "document_type": stored["document_type"],
                "checksum_sha256": stored["checksum_sha256"],
                "file_size_bytes": stored["file_size_bytes"],
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"status": "uploaded", **stored}


@app.get("/applications/{application_id}/documents")
@app.get("/api/applications/{application_id}/documents")
def list_application_documents(
    application_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """List persisted document metadata for a loan application."""
    from pipeline.storage.ledger import list_documents as _list_docs

    app_row = get_application_by_id(application_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application {application_id} not found",
        )
    _ensure_application_scope(principal, app_row)
    canonical_id = app_row.get("application_id") or application_id
    docs = _list_docs(canonical_id)
    return {"application_id": canonical_id, "count": len(docs), "documents": docs}


@app.get("/api/applications/{application_id}/document-checklist")
def get_document_checklist(
    application_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    record = list_application_documents(application_id, principal)
    try:
        checklist = evaluate_document_checklist(record["documents"], required_document_types())
    except ValueError:
        raise HTTPException(status_code=503, detail="DOCUMENT_POLICY_INVALID")
    return {"application_id": record["application_id"], **checklist}


@app.post("/applications/{application_id}/score")
@app.post("/api/applications/{application_id}/score")
def score_loan_application(
    application_id: str,
    body: dict | None = None,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Thin adapter over /predict service: score an application."""
    if is_production():
        app_row = get_application_by_id(application_id)
        if app_row is None:
            raise HTTPException(status_code=404, detail="APPLICATION_NOT_FOUND")
        _ensure_application_scope(principal, app_row)
    if not is_production() and body and "income" in body:
        pred_req = PredictRequest(**body)
        return predict(pred_req)

    app_row = get_application_by_id(application_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application {application_id} not found",
        )
    cust_data = app_row.get("customer_data") or {}
    pred_req = PredictRequest(**cust_data)
    return predict(pred_req)


@app.post("/applications/{application_id}/decision")
@app.post("/api/applications/{application_id}/decision")
def decide_loan_application(
    application_id: str,
    body: dict,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Make human approval or rejection decision with cryptographic snapshot evidence."""
    if is_production():
        raise HTTPException(status_code=503, detail="PRODUCTION_APPROVAL_NOT_INTEGRATED")
    from backend.predict_service import get_model_artifact_checksum
    from pipeline.storage.ledger import (
        emit_domain_event,
        get_decision_snapshot,
        record_decision_snapshot,
    )

    app_row = get_application_by_id(application_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application {application_id} not found",
        )
    _ensure_application_scope(principal, app_row)
    thread_id = app_row.get("thread_id") or application_id
    canonical_id = app_row.get("application_id") or application_id

    # Anti-double-click / idempotency gate
    idem_key = (body.get("idempotency_key") or "").strip()
    existing_snapshot = get_decision_snapshot(canonical_id)
    if existing_snapshot is not None and (idem_key or app_row.get("status") in ("APPROVED", "REJECTED")):
        return {
            "status": "decided",
            "decision": existing_snapshot["decision"],
            "replay": True,
            **existing_snapshot,
        }

    # Determine action
    is_approved = (
        body.get("approved") is True
        or str(body.get("action", "")).lower() in ("approve", "approved", "accept", "yes")
        or str(body.get("decision", "")).upper() == "APPROVE"
    )
    action = "approve" if is_approved else "reject"
    notes = body.get("notes") or body.get("note") or body.get("reason") or f"Manual review: {action}"

    approval_req = GraphApprovalRequest(
        action=action,
        note=notes,
        idempotency_key=idem_key or f"dec-{canonical_id}",
    )
    res = approve_graph_workflow(thread_id, approval_req, principal=principal)

    # Persist decision snapshot evidence
    model_version = getattr(app.state, "meta", {}).get("version", "v1.0.0")
    model_checksum = get_model_artifact_checksum()
    risk_score = float(app_row.get("risk_score") or 0.0)

    snapshot = record_decision_snapshot(
        application_id=canonical_id,
        thread_id=thread_id,
        decision="APPROVE" if is_approved else "REJECT",
        model_version=model_version,
        model_checksum_sha256=model_checksum,
        features_version="v1",
        prediction_probability=risk_score,
        reviewer=principal.identity,
        reason=notes,
    )

    emit_domain_event(
        "ApplicationApproved" if is_approved else "ApplicationRejected",
        {
            "application_id": canonical_id,
            "decision": snapshot["decision"],
            "decision_id": snapshot["decision_id"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "reviewer": principal.identity,
        },
    )

    return {
        "status": "decided",
        "decision": snapshot["decision"],
        "replay": bool(res.get("replay")),
        **snapshot,
        "workflow_result": res,
    }


@app.get("/applications/{application_id}/explanation")
@app.get("/api/applications/{application_id}/explanation")
def explain_loan_application(
    application_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Explanation adapter: return risk factors and model explanation."""
    app_row = get_application_by_id(application_id)
    if app_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Application {application_id} not found",
        )
    _ensure_application_scope(principal, app_row)
    cust_data = app_row.get("customer_data") or {}
    if cust_data and "income" in cust_data:
        pred_req = PredictRequest(**cust_data)
        res = predict(pred_req)
        return {
            "application_id": application_id,
            "risk_score": app_row.get("risk_score"),
            "risk_level": app_row.get("risk_level"),
            "decision": app_row.get("decision"),
            "reasons": res.reasons,
            "model_version": res.model_version,
            "model_name": res.model_name,
        }
    return {
        "application_id": application_id,
        "risk_score": app_row.get("risk_score"),
        "risk_level": app_row.get("risk_level"),
        "decision": app_row.get("decision"),
        "reasons": [],
    }


@app.get("/applications/{application_id}/audit")
@app.get("/api/applications/{application_id}/audit")
def get_application_audit(
    application_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Alias for GET /audit/{application_id}."""
    return get_audit_trail(application_id, principal)


# ---------------------------------------------------------------------------
# Core Banking API (production disbursement integration)
# ---------------------------------------------------------------------------


def _require_core_banking_sandbox() -> None:
    showcase = os.environ.get("CREDITFLOW_SHOWCASE", "").strip().lower() in {"1", "true", "yes"}
    if is_production() or showcase:
        raise HTTPException(status_code=503, detail="CORE_BANKING_NOT_INTEGRATED")


class CoreBankingInitiateRequest(BaseModel):
    application_id: str = Field(min_length=1)
    loan_amount: float = Field(gt=0)
    account_number: str = Field(min_length=1)
    account_name: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    maker_id: str | None = None


class CoreBankingApproveRequest(BaseModel):
    transaction_id: str = Field(min_length=1)
    approver_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class CoreBankingExecuteRequest(BaseModel):
    transaction_id: str = Field(min_length=1)
    idempotency_key: str | None = None


class CoreBankingRollbackRequest(BaseModel):
    transaction_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    approver_id: str = Field(min_length=1)
    idempotency_key: str | None = None


@app.post("/api/core-banking/initiate")
def core_banking_initiate(
    body: CoreBankingInitiateRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Initiate a disbursement (maker side): creates a PENDING transaction."""
    _require_core_banking_sandbox()
    if body.maker_id and body.maker_id != principal.identity:
        raise HTTPException(status_code=403, detail="MAKER_ID_NOT_AUTHORIZED")
    try:
        result = app.state.core_banking.initiate_disbursement(
            application_id=body.application_id,
            loan_amount=body.loan_amount,
            account_number=body.account_number,
            account_name=body.account_name,
            idempotency_key=body.idempotency_key,
            maker_id=principal.identity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_initiate_failed: {exc}")
    return {
        "transaction_id": result.transaction_id,
        "status": result.status,
        "reference_code": result.reference_code,
        "qr_quicklink": result.qr_quicklink,
    }


@app.post("/api/core-banking/approve")
def core_banking_approve(
    body: CoreBankingApproveRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Approve a PENDING disbursement (checker side, dual control: maker != checker)."""
    _require_core_banking_sandbox()
    if body.approver_id != principal.identity:
        raise HTTPException(status_code=403, detail="APPROVER_ID_NOT_AUTHORIZED")
    try:
        result = app.state.core_banking.approve_disbursement(
            transaction_id=body.transaction_id,
            approver_id=principal.identity,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_approve_failed: {exc}")
    return {
        "transaction_id": result.transaction_id,
        "status": result.status,
        "checker_id": result.checker_id,
        "approved_at": result.approved_at,
    }


@app.post("/api/core-banking/execute")
def core_banking_execute(
    body: CoreBankingExecuteRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Execute an APPROVED disbursement and durably record it in the ledger."""
    _require_core_banking_sandbox()
    try:
        result = app.state.core_banking.execute_disbursement(
            transaction_id=body.transaction_id,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_execute_failed: {exc}")

    # Also record to the durable disbursement ledger (best-effort).
    try:
        tx = next(
            (t for t in app.state.core_banking.list_transactions()
             if t.get("transaction_id") == body.transaction_id),
            None,
        )
        if tx is not None:
            app_row = get_application_by_id(tx.get("application_id") or body.transaction_id)
            if app_row is not None:
                record_disbursement(
                    application_id=int(app_row["id"]),
                    loan_amount=float(tx.get("loan_amount") or 0.0),
                    idempotency_key=body.idempotency_key,
                )
                # Wave 3a: initial outcome tracking record for the newly
                # disbursed loan. Starts PERFORMING with no realised default.
                try:
                    disbursed_at = str(
                        tx.get("completed_at") or result.completed_at or ""
                    )
                    cohort_month = disbursed_at[:7] if disbursed_at else ""
                    if cohort_month:
                        outcome_tracker.record_outcome(
                            application_id=int(app_row["id"]),
                            actual_default=False,
                            default_date=None,
                            actual_repayment_amount=None,
                            total_repaid=None,
                            num_missed_payments=0,
                            last_payment_date=None,
                            outcome_status="PERFORMING",
                            cohort_month=cohort_month,
                            source="CORE_BANKING",
                        )
                except Exception:  # noqa: BLE001
                    metrics.error()
    except Exception:  # noqa: BLE001
        metrics.error()

    return {
        "transaction_id": result.transaction_id,
        "status": result.status,
        "bank_reference": result.bank_reference,
        "completed_at": result.completed_at,
    }


@app.post("/api/core-banking/rollback")
def core_banking_rollback(
    body: CoreBankingRollbackRequest,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Reverse a COMPLETED disbursement (dual control: approver != maker)."""
    _require_core_banking_sandbox()
    if body.approver_id != principal.identity:
        raise HTTPException(status_code=403, detail="APPROVER_ID_NOT_AUTHORIZED")
    try:
        result = app.state.core_banking.rollback_disbursement(
            transaction_id=body.transaction_id,
            reason=body.reason,
            approver_id=principal.identity,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_rollback_failed: {exc}")
    return {
        "transaction_id": result.transaction_id,
        "status": result.status,
        "approver_id": result.approver_id,
        "reversed_at": result.reversed_at,
        "reason": result.reason,
    }


@app.get("/api/core-banking/status/{transaction_id}")
def core_banking_status(
    transaction_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Return the current status and key fields of a transaction."""
    _require_core_banking_sandbox()
    try:
        result = app.state.core_banking.get_transaction_status(transaction_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_status_failed: {exc}")
    return result


@app.get("/api/core-banking/audit/{transaction_id}")
def core_banking_audit(
    transaction_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Return the full audit trail for a transaction."""
    _require_core_banking_sandbox()
    try:
        entries = app.state.core_banking.get_audit_trail(transaction_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_audit_failed: {exc}")
    return [entry.model_dump() for entry in entries]


@app.post("/api/core-banking/reconcile/{transaction_id}")
def core_banking_reconcile(
    transaction_id: str,
    principal: Principal = Security(require_service_principal),  # noqa: B008
):
    """Reconcile a single disbursement against its external bank statement."""
    _require_core_banking_sandbox()
    try:
        engine = ReconciliationEngine()
        entry = engine.reconcile_transaction(transaction_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_reconcile_failed: {exc}")
    return entry.model_dump()


@app.post("/api/core-banking/reconcile/daily")
def core_banking_reconcile_daily(
    principal: Principal = Security(require_service_principal),  # noqa: B008
):
    """Reconcile all disbursements for today."""
    _require_core_banking_sandbox()
    try:
        today = datetime.now(UTC).date().isoformat()
        engine = ReconciliationEngine()
        report = engine.generate_reconciliation_report(date=today)
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_reconcile_daily_failed: {exc}")
    return report.model_dump()


@app.get("/api/core-banking/transactions")
def core_banking_transactions(
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """List all core banking transactions."""
    _require_core_banking_sandbox()
    try:
        transactions = app.state.core_banking.list_transactions()
    except Exception as exc:  # noqa: BLE001
        metrics.error()
        raise HTTPException(status_code=500, detail=f"core_banking_transactions_failed: {exc}")
    return {"count": len(transactions), "transactions": transactions}
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# User lifecycle management (Wave 2: Sacombank IdP + user lifecycle)
# ---------------------------------------------------------------------------


def _require_admin(principal: Principal) -> None:
    ensure_authority(principal, "ADMIN")


def _require_read(principal: Principal) -> None:
    ensure_authority(principal, "RISK_COMMITTEE_L2")


class RevokeUserRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@app.get("/api/users", response_model=list[User])
def list_users_endpoint(principal: Principal = Security(require_principal)):  # noqa: B008
    """List all users (ADMIN or RISK_COMMITTEE_L2)."""
    _require_read(principal)
    return user_manager.list_users()


@app.get("/api/users/{user_id}", response_model=User)
def get_user_endpoint(user_id: str, principal: Principal = Security(require_principal)):  # noqa: B008
    """Get a user by internal id (ADMIN or RISK_COMMITTEE_L2)."""
    _require_read(principal)
    user = user_manager.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return user


@app.post("/api/users", response_model=User, status_code=201)
def create_user_endpoint(
    req: UserCreate, principal: Principal = Security(require_principal)  # noqa: B008
):
    """Create a new user (ADMIN only)."""
    _require_admin(principal)
    try:
        return user_manager.create_user(
            username=req.username,
            email=req.email,
            password_hash=req.password_hash,
            role=req.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.put("/api/users/{user_id}", response_model=User)
def update_user_endpoint(
    user_id: str, req: UserUpdate, principal: Principal = Security(require_principal)  # noqa: B008
):
    """Update a user (ADMIN only)."""
    _require_admin(principal)
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    try:
        user = user_manager.update_user(user_id, **updates)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if user is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return user


@app.post("/api/users/{user_id}/revoke", response_model=User)
def revoke_user_endpoint(
    user_id: str, req: RevokeUserRequest, principal: Principal = Security(require_principal)  # noqa: B008
):
    """Revoke a user and all of their sessions (ADMIN only)."""
    _require_admin(principal)
    if user_manager.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    user = user_manager.revoke_user(
        user_id, revoked_by=principal.identity, reason=req.reason
    )
    if user is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return user


@app.post("/api/users/{user_id}/unrevoke", response_model=User)
def unrevoke_user_endpoint(user_id: str, principal: Principal = Security(require_principal)):  # noqa: B008
    """Reactivate a revoked user (ADMIN only)."""
    _require_admin(principal)
    user = user_manager.unrevoke_user(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return user


@app.get("/api/users/{user_id}/sessions", response_model=list[UserSession])
def list_user_sessions_endpoint(
    user_id: str, principal: Principal = Security(require_principal)  # noqa: B008
):
    """List all sessions for a user (ADMIN or RISK_COMMITTEE_L2)."""
    _require_read(principal)
    if user_manager.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return user_manager.list_sessions(user_id)


@app.post("/api/users/{user_id}/sessions/{session_id}/revoke", response_model=dict)
def revoke_user_session_endpoint(
    user_id: str,
    session_id: str,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Revoke a specific session belonging to a user (ADMIN only)."""
    _require_admin(principal)
    if user_manager.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    session = next(
        (s for s in user_manager.list_sessions(user_id) if s.id == session_id),
        None,
    )
    if session is None:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND_OR_ALREADY_REVOKED")
    ok = user_manager.revoke_session(session_id, reason="admin revoked session")
    if not ok:
        raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND_OR_ALREADY_REVOKED")
    return {"revoked": True}


@app.get("/api/users/{user_id}/role-history", response_model=list[RoleHistoryEntry])
def user_role_history_endpoint(
    user_id: str, principal: Principal = Security(require_principal)  # noqa: B008
):
    """Get the role change history for a user (ADMIN or RISK_COMMITTEE_L2)."""
    _require_read(principal)
    if user_manager.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")
    return user_manager.get_role_history(user_id)


@app.get("/api/auth/sessions", response_model=list[UserSession])
def my_sessions_endpoint(principal: Principal = Security(require_principal)):  # noqa: B008
    """List the current user's own sessions (self-service)."""
    user = user_manager.get_user_by_username(principal.identity)
    if user is None or is_user_revoked(principal.identity):
        return []
    return user_manager.list_sessions(user.id)


@app.get("/api/auth/revoke", response_model=dict)
def revoke_my_session_endpoint(
    principal: Principal = Security(require_principal),  # noqa: B008
    session_id: str | None = None,
):
    """Revoke the current user's own session (self-service).

    Without ``session_id`` all of the caller's sessions are revoked.
    """
    user = user_manager.get_user_by_username(principal.identity)
    if user is None:
        return {"revoked": 0}
    if session_id is not None:
        session = next(
            (s for s in user_manager.list_sessions(user.id) if s.id == session_id),
            None,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="SESSION_NOT_FOUND")
        ok = user_manager.revoke_session(session_id, reason="user revoked session")
        return {"revoked": 1 if ok else 0}
    count = 0
    for session in user_manager.list_sessions(user.id):
        if user_manager.revoke_session(session.id, reason="user revoked session"):
            count += 1
    return {"revoked": count}



# ---------------------------------------------------------------------------
# Business Calendar API (Wave 3b)
# ---------------------------------------------------------------------------

class DueAtRequest(BaseModel):
    start_iso: str | None = None


@app.get("/api/business-calendar")
def get_business_calendar(principal: Principal = Security(require_principal)):  # noqa: B008
    """Return the current business calendar config."""
    return {"calendar": get_default_calendar()}


@app.post("/api/business-calendar/due-at/{task_kind}")
def post_business_calendar_due_at(
    task_kind: str,
    body: DueAtRequest | None = None,
    principal: Principal = Security(require_principal),  # noqa: B008
):
    """Compute due datetime for a task kind using the default calendar."""
    sla_hours = get_sla_hours(task_kind)
    if sla_hours is None:
        raise HTTPException(status_code=404, detail=f"SLA_NOT_CONFIGURED_FOR_{task_kind}")
    try:
        due_at = compute_due_at(task_kind, body.start_iso if body else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "task_kind": task_kind,
        "sla_hours": sla_hours,
        "start_iso": body.start_iso if body else None,
        "due_at": due_at,
    }
