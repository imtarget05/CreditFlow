"""Approval idempotency via the shared idempotency_keys table: a replayed
(thread_id, key) returns the stored response without a second disbursement."""
from pipeline.storage import ledger


def _seed(tmp_path):
    path = tmp_path / "ledger.db"
    ledger.init_db(path)
    return path


def test_replay_returns_stored_without_side_effect(tmp_path):
    path = _seed(tmp_path)
    stored = {"decision": "APPROVE", "contract_code": "HDTD-20240101-0001"}
    ledger.record_idempotent_approval("thr-1", "key-1", stored, db_path=path)
    found = ledger.find_idempotent_approval("thr-1", "key-1", db_path=path)
    assert found == stored


def test_record_also_writes_outbox_event(tmp_path):
    import sqlite3

    path = _seed(tmp_path)
    ledger.record_idempotent_approval("thr-9", "key-9", {"a": 1}, db_path=path)
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT destination FROM outbox_events WHERE event_id = ?",
            ("approval-done:thr-9:key-9",),
        ).fetchone()
    assert row is not None and row[0] == "approvals"


def test_unknown_key_returns_none(tmp_path):
    path = _seed(tmp_path)
    assert ledger.find_idempotent_approval("thr-1", "nope", db_path=path) is None


def test_empty_key_never_matches(tmp_path):
    path = _seed(tmp_path)
    assert ledger.find_idempotent_approval("thr-1", "", db_path=path) is None
