"""Persistent Core Banking Adapter — dual control, idempotency, rollback, audit."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from pipeline.disbursement.core_banking_adapter import (
    CoreBankingAdapter,
    DisbursementStatus,
    init_core_banking_db,
)


@pytest.fixture
def db_path():
    fd, name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_core_banking_db(name)
    yield Path(name)
    Path(name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# In-memory mode (backward compatible, no db_path)
# ---------------------------------------------------------------------------


def test_lifecycle_initiate_approve_execute_rollback_in_memory():
    a = CoreBankingAdapter(default_maker_id="maker1", rng=lambda: 0.5)
    r = a.initiate_disbursement(
        "APP-1", 100_000_000, "123456789", "NGUYEN VAN A", "idem-1", maker_id="maker1"
    )
    assert r.status == DisbursementStatus.PENDING
    assert r.reference_code

    ra = a.approve_disbursement(r.transaction_id, "checker1", "idem-approve-1")
    assert ra.status == DisbursementStatus.APPROVED
    assert ra.checker_id == "checker1"

    re_ = a.execute_disbursement(r.transaction_id, "idem-execute-1")
    assert re_.status == DisbursementStatus.COMPLETED
    assert re_.bank_reference

    rb = a.rollback_disbursement(
        r.transaction_id, "duy bo loi", "checker1", "idem-rollback-1"
    )
    assert rb.status == DisbursementStatus.REVERSED
    assert rb.reason == "duy bo loi"


# ---------------------------------------------------------------------------
# Dual control, error handling
# ---------------------------------------------------------------------------


def test_dual_control_maker_cannot_approve_own_tx():
    a = CoreBankingAdapter(default_maker_id="maker1")
    r = a.initiate_disbursement(
        "APP-2", 50_000_000, "987654321", "TRAN VAN B", "idem-2", maker_id="maker1"
    )
    with pytest.raises(ValueError, match="maker and checker must be different"):
        a.approve_disbursement(r.transaction_id, "maker1", "idem-approve-2")


def test_rollback_approver_must_differ_from_maker(db_path):
    a = CoreBankingAdapter(
        default_maker_id="maker1", db_path=str(db_path), rng=lambda: 0.5
    )
    r = a.initiate_disbursement(
        "APP-3", 80_000_000, "555666777", "LE VAN C", "idem-3", maker_id="maker1"
    )
    a.approve_disbursement(r.transaction_id, "checker1", "idem-a-3")
    a.execute_disbursement(r.transaction_id, "idem-e-3")
    with pytest.raises(ValueError, match="rollback approver must differ from the maker"):
        a.rollback_disbursement(
            r.transaction_id, "reason", "maker1", "idem-rb-3"
        )


def test_execute_requires_approved():
    a = CoreBankingAdapter()
    r = a.initiate_disbursement(
        "APP-4", 10_000_000, "111222333", "PHAM VAN D", "idem-4", maker_id="maker1"
    )
    with pytest.raises(ValueError, match="is not APPROVED"):
        a.execute_disbursement(r.transaction_id, "idem-e-4")


def test_rollback_requires_completed():
    a = CoreBankingAdapter()
    r = a.initiate_disbursement(
        "APP-5", 10_000_000, "111222333", "PHAM VAN D", "idem-5", maker_id="maker1"
    )
    a.approve_disbursement(r.transaction_id, "checker1", "idem-a-5")
    with pytest.raises(ValueError, match="is not COMPLETED"):
        a.rollback_disbursement(
            r.transaction_id, "reason", "checker1", "idem-rb-5"
        )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_replay_returns_same_result():
    a = CoreBankingAdapter()
    r1 = a.initiate_disbursement(
        "APP-6", 10_000_000, "111222333", "PHAM VAN D", "idem-6", maker_id="maker1"
    )
    r2 = a.initiate_disbursement(
        "APP-6", 10_000_000, "111222333", "PHAM VAN D", "idem-6", maker_id="maker1"
    )
    assert r1.transaction_id == r2.transaction_id
    assert r1.status == r2.status


def test_idempotency_key_reused_with_diff_params_raises():
    a = CoreBankingAdapter()
    a.initiate_disbursement(
        "APP-7", 10_000_000, "111222333", "PHAM VAN D", "idem-7", maker_id="maker1"
    )
    with pytest.raises(ValueError, match="reused with different parameters"):
        a.initiate_disbursement(
            "APP-7", 999_000_000, "111222333", "PHAM VAN D", "idem-7", maker_id="maker1"
        )


def test_unknown_transaction_raises():
    a = CoreBankingAdapter()
    with pytest.raises(LookupError, match="unknown transaction"):
        a.get_transaction_status("TXN-does-not-exist")


def test_audit_trail_records_transitions():
    a = CoreBankingAdapter(rng=lambda: 0.5)
    r = a.initiate_disbursement(
        "APP-8", 10_000_000, "111222333", "PHAM VAN D", "idem-8", maker_id="maker1"
    )
    a.approve_disbursement(r.transaction_id, "checker1", "idem-a-8")
    a.execute_disbursement(r.transaction_id, "idem-e-8")
    trail = a.get_audit_trail(r.transaction_id)
    actions = [e.action for e in trail]
    assert "initiate" in actions
    assert "approve" in actions
    assert "execute" in actions
    assert trail[0].actor == "maker1"
    assert trail[1].actor == "checker1"


def test_rollback_persists_reversal_reason_with_db(db_path):
    a = CoreBankingAdapter(
        default_maker_id="maker1", db_path=str(db_path), rng=lambda: 0.5
    )
    r = a.initiate_disbursement(
        "APP-REV-1", 50_000_000, "123456", "NGUYEN VAN A", "idem-rev-1", maker_id="maker1"
    )
    a.approve_disbursement(r.transaction_id, "checker1", "idem-rev-2")
    a.execute_disbursement(r.transaction_id, "idem-rev-3")
    a.rollback_disbursement(
        r.transaction_id, "Reason: Fraudulent Account", "checker1", "idem-rev-4"
    )

    tx = next(t for t in a.list_transactions() if t["transaction_id"] == r.transaction_id)
    assert tx["reversal_reason"] == "Reason: Fraudulent Account"

    # Ensure re-upsert preserves the reversal reason
    a._db_tx_upsert(tx)
    tx_reloaded = a._get(r.transaction_id)
    assert tx_reloaded["reversal_reason"] == "Reason: Fraudulent Account"
