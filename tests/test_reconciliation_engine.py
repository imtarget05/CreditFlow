"""Reconciliation against a durable local ledger and simulated bank statements."""

import json

import pytest

from pipeline.disbursement.reconciliation import (
    DiscrepancyType,
    ReconciliationEngine,
    ReconciliationStatus,
)
from pipeline.storage import ledger


def _disburse(path, thread, amount):
    application_id = ledger.record_application(
        thread,
        {"loan_amount": amount, "income": 10_000_000},
        0.1,
        "LOW",
        "APPROVE",
        "APPROVED",
        db_path=path,
    )
    return ledger.record_disbursement(application_id, amount, db_path=path)


def _statement(reference, amount, *, status="POSTED"):
    return {
        "reference_code": reference,
        "amount": amount,
        "posted_at": "2026-11-05T08:00:00Z",
        "status": status,
    }


def test_report_reconciles_match_mismatch_missing_and_duplicate(tmp_path):
    path = tmp_path / "ledger.db"
    ledger.init_db(path)
    matched = _disburse(path, "matched", 1000)
    mismatch = _disburse(path, "mismatch", 2000)
    missing = _disburse(path, "missing", 3000)
    engine = ReconciliationEngine(
        db_path=path,
        external_statements=[
            _statement(matched["contract_code"], 1000),
            _statement(mismatch["contract_code"], 1999),
            _statement(mismatch["contract_code"], 1999),
            _statement("BANK-ONLY", 500),
        ],
    )

    report = engine.generate_reconciliation_report()

    assert (report.internal_count, report.external_count) == (3, 4)
    assert (report.matched_count, report.mismatch_count, report.pending_count) == (1, 1, 1)
    assert report.discrepancy_count == 4
    assert {item.type for item in report.discrepancies} == {
        DiscrepancyType.AMOUNT_MISMATCH,
        DiscrepancyType.MISSING_EXTERNAL,
        DiscrepancyType.MISSING_INTERNAL,
        DiscrepancyType.DUPLICATE_EXTERNAL,
    }
    assert engine.reconcile_transaction(str(matched["application_id"])).status == ReconciliationStatus.MATCHED
    assert engine.reconcile_transaction(str(mismatch["application_id"])).status == ReconciliationStatus.MISMATCH
    assert engine.reconcile_transaction(str(missing["application_id"])).status == ReconciliationStatus.PENDING
    with pytest.raises(LookupError, match="no internal disbursement"):
        engine.reconcile_transaction("unknown")


def test_pending_statement_and_account_mismatch_never_match(tmp_path):
    path = tmp_path / "ledger.db"
    ledger.init_db(path)
    disbursement = _disburse(path, "account", 2500)
    ref = disbursement["contract_code"]
    pending = ReconciliationEngine(db_path=path, external_statements=[_statement(ref, 2500, status="PENDING")])
    assert pending.reconcile_transaction(ref).status == ReconciliationStatus.PENDING

    wrong_account = _statement(ref, 2500)
    wrong_account["account_number"] = "different-bank-account"
    engine = ReconciliationEngine(db_path=path, external_statements=[wrong_account])
    assert engine.reconcile_transaction(ref).status == ReconciliationStatus.MISMATCH


@pytest.mark.parametrize("suffix", [".json", ".csv"])
def test_load_bank_statements_from_file(tmp_path, suffix):
    records = [_statement("BANK-1", 100), _statement("BANK-2", 200)]
    file = tmp_path / f"statement{suffix}"
    if suffix == ".json":
        file.write_text(json.dumps({"records": records}), encoding="utf-8")
    else:
        file.write_text(
            "reference_code,amount,posted_at,status\n"
            "BANK-1,100,2026-11-05T08:00:00Z,POSTED\n"
            "BANK-2,200,2026-11-05T08:00:00Z,POSTED\n",
            encoding="utf-8",
        )

    engine = ReconciliationEngine()
    assert engine.load_bank_statements(file) == 2
    assert [item.amount for item in engine._all_external()] == [100, 200]
