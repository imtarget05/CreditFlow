"""CreditFlow FastAPI service.

Endpoints (spec §12):
  GET  /health       -> service + model status
  POST /predict      -> real ML prediction from a credit profile
  GET  /model/info   -> production model version + metrics + selection reasoning
  GET  /metrics      -> runtime request/latency/error counters + benchmark

Runtime metrics are held in-process (demo scope). The 4-model benchmark is served
from models/production/benchmark_results.json so the comparison is reproducible and real.
"""
from __future__ import annotations

import json
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from backend.predict_service import load_production_model, predict_risk
from pipeline.monitoring.drift import detect_drift

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
    return HealthResponse(
        status="ok",
        model_loaded=True,
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
    prediction_window.append({**req.model_dump(), "risk_probability": payload["risk_probability"]})
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


@app.get("/metrics")
def runtime_metrics():
    metrics.bump("metrics")
    return {
        "runtime": metrics.snapshot(),
        "model": getattr(app.state, "meta", {}),
        "benchmark": _benchmark_payload(),
    }
# ---------------------------------------------------------------------------