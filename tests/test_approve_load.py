"""Load/concurrency test on the approve path (money-moving endpoint).

N distinct threads each start a review workflow and concurrently POST
/approve. Expects every approve to return 200 and exactly N disbursement
rows (one per application), proving the approve path holds under concurrency
without duplicating money movement.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app import app  # noqa: E402
from pipeline.storage.ledger import init_db  # noqa: E402

from tests.auth_support import (  # noqa: E402
    API_KEY,
    API_KEY_HEADER,
    APPROVER_ID,
    AUTHORITY_ROLE,
    AUTH_HEADERS,
)

pytestmark = [pytest.mark.load, pytest.mark.slow]

N_THREADS = 8
LOAN_AMOUNT = 120_000_000.0

REVIEW_PROFILE = {
    "income": 8_000_000,
    "age": 35,
    "employment_years": 8,
    "loan_amount": LOAN_AMOUNT,
    "loan_term": 36,
    "existing_debt": 2_000_000,
    "credit_history": 9,
    "previous_defaults": 1,
}


def _configure_env(monkeypatch, db_path: str, checkpoint_path: str) -> None:
    """Point auth + ledger + checkpointer at isolated files for this test."""
    monkeypatch.setenv("CREDITFLOW_API_KEY", API_KEY)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ID", APPROVER_ID)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", AUTHORITY_ROLE)
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", db_path)
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", checkpoint_path)


def _start_workflow(c: TestClient) -> str:
    r = c.post("/predict/graph", json={"customer_data": REVIEW_PROFILE})
    assert r.status_code == 200, r.text
    started = r.json()
    assert started.get("approval_required") is True, started
    return started["thread_id"]


def _approve(thread_id: str) -> int:
    # Each worker builds its own client but shares the process env + ledger DB.
    with TestClient(app) as c:
        r = c.post(
            f"/predict/graph/{thread_id}/approve",
            json={"action": "approve"},
            headers=AUTH_HEADERS,
        )
    return r.status_code


def test_concurrent_approves_hold_under_load(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ledger_load.db")
    checkpoint_path = str(tmp_path / "checkpoint_load.pkl")
    _configure_env(monkeypatch, db_path, checkpoint_path)
    init_db()

    with TestClient(app) as bootstrap:
        thread_ids = [_start_workflow(bootstrap) for _ in range(N_THREADS)]
    assert len(set(thread_ids)) == N_THREADS, "expected distinct workflows"

    results: list[int] = []
    with ThreadPoolExecutor(max_workers=N_THREADS) as pool:
        futures = [pool.submit(_approve, tid) for tid in thread_ids]
        for fut in as_completed(futures):
            results.append(fut.result())

    assert all(code == 200 for code in results), f"non-200 statuses: {results!r}"

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM disbursements").fetchone()[0]
    assert count == N_THREADS, (
        f"expected {N_THREADS} disbursement rows under concurrent approve, got {count}"
    )
