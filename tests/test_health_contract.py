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


def test_metrics_still_served():
    r = client.get("/metrics")
    assert r.status_code == 200
