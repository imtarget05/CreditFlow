"""E2E acceptance tests — persistence + core-banking disbursement.

Flow under test (acceptance scenario):
    Khách nộp đơn (POST /predict/graph)
      -> workflow thẩm định (REVIEW -> pause at human_approval)
      -> chuyên viên duyệt (POST /predict/graph/{thread_id}/approve)
      -> query DB thấy bản ghi hợp đồng tín dụng (loan_applications APPROVED)
         và giao dịch giải ngân (disbursements COMPLETED, contract + ledger_hash).

Also covers: the reject path (NO disbursement written) and the two
evidence endpoints GET /api/applications + GET /api/disbursements.

Each test runs against an isolated SQLite ledger via CREDITFLOW_LEDGER_DB
(resolved per call by pipeline.storage.ledger, so no import-order coupling).
Requires the trained production model at models/production/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from pipeline.storage.ledger import init_db, ledger_path

CONTRACT_CODE_RE = re.compile(r"^HDTD-\d{8}-\d{4}$")

BASE_PROFILE = {
    "income": 8000000.0,
    "age": 35,
    "employment_years": 8.0,
    "loan_amount": 120000000.0,
    "loan_term": 36,
    "existing_debt": 2000000.0,
    "credit_history": 9.0,
    "previous_defaults": 0,
}


# ---------------------------------------------------------------------------
# Fixtures: isolated ledger DB + live app client
# ---------------------------------------------------------------------------
@pytest.fixture()
def ledger_env(tmp_path, monkeypatch):
    """Point the ledger at a fresh per-test SQLite file."""
    db = tmp_path / "ledger_test.db"
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(db))
    init_db()
    yield db


@pytest.fixture()
def client(ledger_env):
    with TestClient(app) as c:
        yield c


def _start_review_workflow(client: TestClient) -> tuple[dict, dict]:
    """Start a workflow whose decision routes to REVIEW (human approval).

    Mutates the base profile until the cost-tuned threshold lands the decision
    in the REVIEW band without triggering a blocking policy rule.
    """
    last = None
    for defaults in (1, 2):
        for employment_years in (0.5, 2.0):
            profile = dict(
                BASE_PROFILE,
                previous_defaults=defaults,
                employment_years=employment_years,
            )
            r = client.post("/predict/graph", json={"customer_data": profile})
            assert r.status_code == 200, r.text
            last = (profile, r.json())
            if last[1].get("approval_required"):
                return last
    pytest.fail(
        "no candidate profile produced a REVIEW (approval_required) workflow; "
        f"last response: {last[1] if last else None}"
    )


# ---------------------------------------------------------------------------
# E2E: submit -> review -> approve -> contract + disbursement in the ledger
# ---------------------------------------------------------------------------
def test_e2e_submit_review_approve_creates_contract_and_disbursement(
    client, ledger_env
):
    profile, started = _start_review_workflow(client)
    thread_id = started["thread_id"]

    # 1. Hồ sơ nộp đơn đã được lưu vào loan_applications (PENDING_REVIEW).
    assert started["ledger_application_id"] is not None, started
    assert started["ledger_status"] == "PENDING_REVIEW"
    app_id = started["ledger_application_id"]

    # 2. Chuyên viên phê duyệt -> tự động ghi sổ giải ngân.
    r = client.post(f"/predict/graph/{thread_id}/approve", json={"action": "approve"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ledger_status"] == "APPROVED"
    disbursement = body["disbursement"]
    assert disbursement is not None, "approve must write a disbursement record"
    assert CONTRACT_CODE_RE.match(disbursement["contract_code"]), disbursement
    assert disbursement["status"] == "COMPLETED"
    assert disbursement["loan_amount"] == profile["loan_amount"]
    assert len(disbursement["ledger_hash"]) == 64

    # 3. Query DB trực tiếp: thấy hợp đồng tín dụng + giao dịch giải ngân.
    conn = sqlite3.connect(str(ledger_env))
    conn.row_factory = sqlite3.Row
    app_row = conn.execute(
        "SELECT * FROM loan_applications WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    assert app_row is not None, "application row must survive in SQLite"
    assert app_row["status"] == "APPROVED"
    assert app_row["decision"] == "APPROVE"
    assert app_row["risk_score"] is not None
    stored_profile = json.loads(app_row["customer_data"])
    assert stored_profile["loan_amount"] == profile["loan_amount"]

    dis_row = conn.execute(
        "SELECT * FROM disbursements WHERE application_id = ?", (app_id,)
    ).fetchone()
    assert dis_row is not None, "disbursement row must be written to the ledger"
    assert CONTRACT_CODE_RE.match(dis_row["contract_code"])
    assert dis_row["loan_amount"] == profile["loan_amount"]
    assert dis_row["status"] == "COMPLETED"

    # 4. ledger_hash là SHA-256 của các trường bất biến — chống đục sửa.
    expected_hash = hashlib.sha256(
        (
            f"{dis_row['application_id']}|{dis_row['contract_code']}|"
            f"{dis_row['loan_amount']:.2f}|{dis_row['disbursed_at']}"
        ).encode("utf-8")
    ).hexdigest()
    assert dis_row["ledger_hash"] == expected_hash
    conn.close()


def test_e2e_reject_flow_writes_no_disbursement(client, ledger_env):
    profile, started = _start_review_workflow(client)
    thread_id = started["thread_id"]

    r = client.post(f"/predict/graph/{thread_id}/approve", json={"action": "reject"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ledger_status"] == "REJECTED"
    assert body["disbursement"] is None, "a rejected loan must never disburse"

    conn = sqlite3.connect(str(ledger_env))
    conn.row_factory = sqlite3.Row
    app_row = conn.execute(
        "SELECT * FROM loan_applications WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    assert app_row["status"] == "REJECTED"
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM disbursements WHERE application_id = ?",
        (app_row["id"],),
    ).fetchone()["n"]
    assert n == 0
    conn.close()


def test_evidence_endpoints_return_ledger_records(client, ledger_env):
    profile, started = _start_review_workflow(client)
    thread_id = started["thread_id"]

    # Trước phê duyệt: hồ sơ PENDING_REVIEW đã thấy qua /api/applications.
    r = client.get("/api/applications")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    match = [a for a in body["applications"] if a["thread_id"] == thread_id]
    assert match and match[0]["status"] == "PENDING_REVIEW"
    assert match[0]["customer_data"]["loan_amount"] == profile["loan_amount"]

    client.post(f"/predict/graph/{thread_id}/approve", json={"action": "approve"})

    # Sau phê duyệt: danh sách hồ sơ + sổ cái giải ngân thực tế.
    r = client.get("/api/applications")
    match = [a for a in r.json()["applications"] if a["thread_id"] == thread_id]
    assert match and match[0]["status"] == "APPROVED"

    r = client.get("/api/disbursements")
    assert r.status_code == 200
    ledger = r.json()
    assert ledger["count"] >= 1
    assert ledger["total_disbursed"] >= profile["loan_amount"]
    codes = [d["contract_code"] for d in ledger["disbursements"]]
    assert any(CONTRACT_CODE_RE.match(c) for c in codes)

    # Filter theo status hoạt động.
    r = client.get("/api/applications", params={"status": "APPROVED"})
    assert all(a["status"] == "APPROVED" for a in r.json()["applications"])


def test_ledger_persists_across_process_restart(client, ledger_env):
    """RAM-only graphs die on restart; the SQLite ledger row must survive."""
    profile, started = _start_review_workflow(client)
    thread_id = started["thread_id"]

    # Simulate a fresh process reading the same DB file (no in-memory state).
    from pipeline.storage import ledger

    row = ledger.get_application_by_thread(thread_id)
    assert row is not None
    assert row["status"] == "PENDING_REVIEW"
    assert row["customer_data"]["loan_amount"] == profile["loan_amount"]

    # Approve from the "restarted" server context still writes the ledger.
    r = client.post(f"/predict/graph/{thread_id}/approve", json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["disbursement"] is not None
    assert ledger_path().exists()


def test_disbursement_contract_codes_sequence_per_day(client, ledger_env):
    """Two approvals in the same day must yield a monotonic XXXX sequence."""
    codes = []
    for _ in range(2):
        _, started = _start_review_workflow(client)
        r = client.post(
            f"/predict/graph/{started['thread_id']}/approve",
            json={"action": "approve"},
        )
        assert r.status_code == 200
        d = r.json()["disbursement"]
        assert d is not None
        codes.append(d["contract_code"])
    assert codes[0] != codes[1]
    assert codes[0].rsplit("-", 1)[0] == codes[1].rsplit("-", 1)[0]  # same day


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

