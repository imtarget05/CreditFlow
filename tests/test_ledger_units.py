"""Offline ledger round-trip and hash scope, using a temporary SQLite file."""
import sqlite3

from pipeline.storage import ledger


def test_ledger_roundtrip_and_hash_detects_changed_amount(tmp_path):
    path = tmp_path / "ledger.db"
    ledger.init_db(path)
    profile = {"income": 8000000, "loan_amount": 120000000, "existing_debt": 15000000}
    trail = [{"node": "decision", "status": "SUCCESS"}]
    identifier = ledger.record_application(
        "audit-demo", profile, 0.0186, "LOW", "APPROVE", "APPROVED",
        audit_trail=trail, db_path=path,
    )
    stored = ledger.get_application_by_thread("audit-demo", db_path=path)
    assert stored["customer_data"] == profile
    assert stored["audit_trail"] == trail
    assert stored["risk_score"] == 0.0186
    row = ledger.record_disbursement(identifier, 120000000, db_path=path)
    # A new SQLite connection reads the committed data, not an in-memory cache.
    reread = ledger.list_disbursements(db_path=path)[0]
    assert reread["contract_code"] == row["contract_code"]
    assert reread["loan_amount"] == 120000000
    assert reread["hash_valid"] is True
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE disbursements SET loan_amount = loan_amount + 1")
    assert ledger.list_disbursements(db_path=path)[0]["hash_valid"] is False
