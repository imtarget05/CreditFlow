"""The scheduled monitor must use the serving drift result, not a second scorer."""

from fastapi.testclient import TestClient

from backend.app import app
from tests.auth_support import AUTH_HEADERS, configure_auth_env


def test_monitoring_check_requires_authentication():
    response = TestClient(app).post("/api/monitoring/check", json={"run_id": "daily-2026-11-03"})
    assert response.status_code == 401


def test_detected_drift_creates_one_risk_task(monkeypatch, tmp_path):
    configure_auth_env(monkeypatch)
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(tmp_path / "monitor.db"))
    from pipeline.storage.work_items import init_work_items
    init_work_items(tmp_path / "monitor.db")
    monkeypatch.setattr("backend.app.drift_report", lambda: {"drift": {"status": "DRIFT_DETECTED", "n_recent": 100}})
    monkeypatch.setattr(app.state, "meta", {"version": "model-v1"}, raising=False)
    client = TestClient(app)
    one = client.post("/api/monitoring/check", json={"run_id": "daily-2026-11-03"}, headers=AUTH_HEADERS)
    two = client.post("/api/monitoring/check", json={"run_id": "daily-2026-11-03"}, headers=AUTH_HEADERS)
    assert one.status_code == two.status_code == 200
    assert one.json()["work_item"]["id"] == two.json()["work_item"]["id"]
    assert one.json()["work_item"]["assigned_role"] == "RISK_COMMITTEE_L2"
