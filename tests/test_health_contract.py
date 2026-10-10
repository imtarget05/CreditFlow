"""Health contract: /health/live is dependency-free, /health/ready reports
per-dependency checks, /metrics keeps serving runtime counters."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import ExitStack

from fastapi.testclient import TestClient

from backend.app import app

_stack = ExitStack()
client = _stack.enter_context(TestClient(app))


def test_live_ok_without_dependencies():
    r = client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ready_reports_checks():
    r = client.get("/health/ready")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "checks" in body
    assert "model" in body["checks"]
    assert "checkpoint" in body["checks"]


def test_production_stays_ready_but_agent_pipeline_is_fail_closed(monkeypatch):
    """CREDITFLOW_ENV=production must fail the *agent pipeline* closed while
    the ML scoring path (/health, /health/ready, /predict) stays ready."""
    from pipeline.agent.nodes import fetch_gateways

    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    # Readiness is not conflated with the simulation gate.
    r = client.get("/health/ready")
    assert r.json()["status"] == "ready", r.json()
    # But simulated gateways refuse to run in production.
    try:
        fetch_gateways({"customer_data": {}, "audit_trail": []})
    except RuntimeError as exc:
        assert "GATEWAY_UNAVAILABLE" in str(exc)
    else:
        raise AssertionError("production must fail closed on simulated gateways")
    finally:
        monkeypatch.delenv("CREDITFLOW_ENV", raising=False)


def test_metrics_still_served():
    r = client.get("/metrics")
    assert r.status_code == 200
