"""Concurrent approval race: exactly-one-disbursement invariant (stdlib only).

Mirrors the temp-DB pattern of ``tests/test_ledger_property.py`` (isolated
``tmp_path`` SQLite file, ``ledger.init_db``) and exercises the REAL
``pipeline.storage.ledger`` module (stdlib ``sqlite3`` only — no
pandas/sklearn). It does NOT duplicate the ledger module.

Race surface: ``ledger.record_disbursement`` guards replay safety two ways:

1. ``UNIQUE(application_id)`` + ``SELECT``-then-``INSERT`` under the
   process-wide ``_write_lock``;
2. ``except sqlite3.IntegrityError`` fallback that returns the winner's row
   ("Lost a concurrent race: the winner's row is the single truth").

So N threads hammering the SAME ``application_id`` at the same instant
(synchronised with a ``threading.Barrier`` for a true collision) must leave
EXACTLY ONE disbursement row, and every thread must observe the SAME
contract code.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.storage import ledger  # noqa: E402

pytestmark = [pytest.mark.race]

N_THREADS = 8
LOAN_AMOUNT = 120_000_000.0


def _seed_approved_app(db, thread_id: str, amount: float = LOAN_AMOUNT) -> int:
    """One durable application row; returns its loan_applications.id."""
    profile = {"income": 8_000_000, "loan_amount": amount, "existing_debt": 0}
    return ledger.record_application(
        thread_id, profile, 0.01, "LOW", "APPROVE", "APPROVED", db_path=db
    )


def _run_barrier_collision(fn, n: int = N_THREADS, timeout: float = 30.0) -> list:
    """Run ``fn(slot)`` on ``n`` threads released simultaneously via Barrier."""
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def _worker(slot: int) -> None:
        try:
            barrier.wait(timeout=timeout)
            results[slot] = ("ok", fn(slot))
        except Exception as exc:  # noqa: BLE001 — collected, asserted by caller
            results[slot] = ("error", exc)

    threads = [threading.Thread(target=_worker, args=(s,)) for s in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)
    assert not any(t.is_alive() for t in threads), "worker thread hung"
    return results


def test_concurrent_same_application_yields_exactly_one_disbursement(tmp_path):
    """N threads approve/disburse the SAME application at once -> ONE row."""
    db = tmp_path / "ledger-race.db"
    ledger.init_db(db)
    app_id = _seed_approved_app(db, "race-same-app")

    results = _run_barrier_collision(
        lambda _slot: ledger.record_disbursement(
            app_id, LOAN_AMOUNT, db_path=db, idempotency_key="race-same-key"
        )
    )
    errors = [r[1] for r in results if r[0] == "error"]
    assert not errors, f"concurrent disburse raised: {errors!r}"

    contracts = [r[1]["contract_code"] for r in results]
    assert len(set(contracts)) == 1, (
        f"threads observed divergent contracts (duplicate money?): {contracts!r}"
    )
    rows = ledger.list_disbursements(db_path=db)
    assert len(rows) == 1, (
        f"idempotency invariant violated: {len(rows)} disbursement rows "
        "for one application (expected exactly 1)"
    )
    assert rows[0]["contract_code"] == contracts[0]
    assert rows[0]["loan_amount"] == LOAN_AMOUNT
    assert rows[0]["hash_valid"] is True


def test_replay_same_key_sequential_never_duplicates(tmp_path):
    """Sequential replay with the same key (even a changed amount) adds no row."""
    db = tmp_path / "ledger-replay.db"
    ledger.init_db(db)
    app_id = _seed_approved_app(db, "race-replay-app")

    first = ledger.record_disbursement(
        app_id, LOAN_AMOUNT, db_path=db, idempotency_key="race-replay-key"
    )
    again = ledger.record_disbursement(
        app_id, LOAN_AMOUNT, db_path=db, idempotency_key="race-replay-key"
    )
    drifted = ledger.record_disbursement(
        app_id, LOAN_AMOUNT + 999_999.0, db_path=db, idempotency_key="race-replay-key"
    )
    bare = ledger.record_disbursement(app_id, LOAN_AMOUNT + 1.0, db_path=db)

    assert again["contract_code"] == first["contract_code"]
    assert drifted["contract_code"] == first["contract_code"]
    assert drifted["loan_amount"] == LOAN_AMOUNT, (
        "replay must not overwrite the original amount"
    )
    assert bare["contract_code"] == first["contract_code"]

    rows = ledger.list_disbursements(db_path=db)
    assert len(rows) == 1, (
        f"replay created a second money-movement row: got {len(rows)}, want 1"
    )


def test_concurrent_different_keys_all_succeed(tmp_path):
    """N threads disbursing N DISTINCT applications/keys -> all N rows exist."""
    db = tmp_path / "ledger-race-distinct.db"
    ledger.init_db(db)
    app_ids = [_seed_approved_app(db, f"race-distinct-{s}") for s in range(N_THREADS)]

    results = _run_barrier_collision(
        lambda slot: ledger.record_disbursement(
            app_ids[slot], LOAN_AMOUNT, db_path=db, idempotency_key=f"race-key-{slot}"
        )
    )
    errors = [r[1] for r in results if r[0] == "error"]
    assert not errors, f"distinct-key disburse raised: {errors!r}"

    rows = ledger.list_disbursements(db_path=db)
    assert len(rows) == N_THREADS, (
        f"expected {N_THREADS} disbursement rows, got {len(rows)}"
    )
    codes = [r["contract_code"] for r in rows]
    assert len(set(codes)) == N_THREADS, (
        f"contract codes must be unique: {codes!r}"
    )
    assert all(r["hash_valid"] is True for r in rows)
    assert sum(r["loan_amount"] for r in rows) == N_THREADS * LOAN_AMOUNT
