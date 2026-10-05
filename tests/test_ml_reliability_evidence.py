"""Tests for ML Reliability, Artifact Checksums, Decision Snapshots, and Drift Events."""

import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import ExitStack
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.predict_service import get_model_artifact_checksum
from pipeline.storage.ledger import (
    get_decision_snapshot,
    list_documents,
    verify_decision_snapshot_hash,
    _connect,
)
from tests.auth_support import AUTH_HEADERS, configure_auth_env

_stack = ExitStack()
client = _stack.enter_context(TestClient(app))


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    configure_auth_env(monkeypatch)


REVIEW_APPLICATION = {
    "income": 8000000.0,
    "age": 35,
    "employment_years": 2.0,
    "loan_amount": 120000000.0,
    "loan_term": 36,
    "existing_debt": 2000000.0,
    "credit_history": 9.0,
    "previous_defaults": 1,
}


def test_model_artifact_checksum():
    """Production model artifact has a verifiable SHA-256 checksum."""
    checksum = get_model_artifact_checksum()
    assert checksum != "unknown"
    assert len(checksum) == 64
    sums_file = Path(__file__).resolve().parents[1] / "models" / "production" / "SHA256SUMS"
    if sums_file.exists():
        assert checksum in sums_file.read_text()


def test_document_persistence_with_real_sha256_checksum():
    """POST /applications/{id}/documents computes SHA-256 and persists document metadata."""
    res_app = client.post("/applications", json=REVIEW_APPLICATION, headers=AUTH_HEADERS)
    app_id = res_app.json().get("application_id") or res_app.json().get("thread_id")

    sample_content = b"%PDF-1.4 Mock payslip content with employee income details..."
    expected_hash = hashlib.sha256(sample_content).hexdigest()

    res_doc = client.post(
        f"/applications/{app_id}/documents",
        json={
            "document_type": "income_verification",
            "file_name": "verified_payslip.pdf",
            "file_content": sample_content.decode("latin1"),
            "mime_type": "application/pdf",
        },
        headers=AUTH_HEADERS,
    )
    assert res_doc.status_code == 200
    doc_data = res_doc.json()
    assert doc_data["status"] == "uploaded"
    assert doc_data["checksum_sha256"] == expected_hash
    assert doc_data["file_size_bytes"] == len(sample_content)

    # Verify listing reflects the stored checksum
    res_list = client.get(f"/applications/{app_id}/documents", headers=AUTH_HEADERS)
    assert res_list.status_code == 200
    docs = res_list.json().get("documents", [])
    assert any(d["checksum_sha256"] == expected_hash for d in docs)


def test_decision_snapshot_tamper_evidence():
    """POST /applications/{id}/decision creates tamper-evident decision snapshot evidence."""
    res_app = client.post("/applications", json=REVIEW_APPLICATION, headers=AUTH_HEADERS)
    app_data = res_app.json()
    app_id = app_data.get("application_id") or app_data.get("thread_id")

    res_dec = client.post(
        f"/applications/{app_id}/decision",
        json={
            "approved": True,
            "notes": "Verified salary slip and zero recent default history",
            "idempotency_key": f"idem-test-{app_id}",
        },
        headers=AUTH_HEADERS,
    )
    assert res_dec.status_code == 200, res_dec.text
    dec_data = res_dec.json()
    assert dec_data["status"] == "decided"
    assert dec_data["decision"] == "APPROVE"
    assert "decision_id" in dec_data
    assert "model_checksum_sha256" in dec_data
    assert len(dec_data["model_checksum_sha256"]) == 64
    assert "snapshot_hash" in dec_data

    # Verify cryptographic snapshot hash
    snapshot = get_decision_snapshot(app_id)
    assert snapshot is not None
    assert verify_decision_snapshot_hash(snapshot) is True


def test_decision_anti_double_click_idempotency():
    """Replaying decision with the same idempotency key returns stored snapshot without re-processing."""
    res_app = client.post("/applications", json=REVIEW_APPLICATION, headers=AUTH_HEADERS)
    app_id = res_app.json().get("application_id") or res_app.json().get("thread_id")
    idem_key = f"anti-click-{app_id}"

    # First decision submission
    r1 = client.post(
        f"/applications/{app_id}/decision",
        json={"approved": True, "notes": "First approval click", "idempotency_key": idem_key},
        headers=AUTH_HEADERS,
    )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    dec_id1 = d1["decision_id"]

    # Second submission (accidental double-click / network retry)
    r2 = client.post(
        f"/applications/{app_id}/decision",
        json={"approved": True, "notes": "Second accidental click", "idempotency_key": idem_key},
        headers=AUTH_HEADERS,
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()
    assert d2.get("replay") is True
    assert d2["decision_id"] == dec_id1
    assert d2["decision"] == "APPROVE"


def test_model_drift_triggers_domain_event(monkeypatch):
    """When detect_drift reports DRIFT_DETECTED, a ModelDriftDetected outbox event is emitted."""
    from backend import app as backend_app

    def fake_detect_drift(reference, recent):
        return {
            "status": "DRIFT_DETECTED",
            "n_recent": 60,
            "features": {"income": {"status": "DRIFT_DETECTED", "psi": 0.35}},
        }

    monkeypatch.setattr("backend.app.detect_drift", fake_detect_drift)

    res_drift = client.get("/drift", headers=AUTH_HEADERS)
    assert res_drift.status_code == 200
    assert res_drift.json()["drift"]["status"] == "DRIFT_DETECTED"

    # Verify event reached outbox_events in SQLite ledger
    with _connect() as conn:
        rows = conn.execute(
            "SELECT event_id, destination, payload FROM outbox_events WHERE destination = 'drift_alerts'"
        ).fetchall()
    assert len(rows) >= 1
    latest_payload = json.loads(rows[-1]["payload"])
    assert latest_payload["event_type"] == "ModelDriftDetected"
    assert "income" in latest_payload["drifted_features"]
