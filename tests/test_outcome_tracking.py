from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from pipeline.monitoring.outcome_tracking import (
    OutcomeCreate,
    OutcomeTracker,
    OutcomeUpdate,
)
from pipeline.storage.ledger import init_db, record_application


@pytest.fixture
def tracker(tmp_path):
    db_path = tmp_path / "outcomes.sqlite3"
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE loan_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL UNIQUE
                    REFERENCES loan_applications(id),
                actual_default BOOLEAN NOT NULL DEFAULT FALSE,
                default_date DATE,
                actual_repayment_amount REAL,
                total_repaid REAL,
                num_missed_payments INTEGER NOT NULL DEFAULT 0,
                last_payment_date DATE,
                outcome_status TEXT NOT NULL,
                cohort_month TEXT NOT NULL,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
    return OutcomeTracker(db_path), db_path


def _application(db_path, label, status="APPROVED"):
    return record_application(
        thread_id=f"thread-{label}",
        application_id=f"APP-{label}",
        customer_data={},
        risk_score=0.25,
        risk_level="LOW",
        decision="APPROVE",
        status=status,
        db_path=db_path,
    )


def _record(tracker, application_id, **overrides):
    values = {
        "application_id": application_id,
        "actual_default": False,
        "default_date": None,
        "actual_repayment_amount": None,
        "total_repaid": 0,
        "num_missed_payments": 0,
        "last_payment_date": None,
        "outcome_status": "PERFORMING",
        "cohort_month": "2026-01",
        "source": "CORE_BANKING",
    }
    return tracker.record_outcome(**(values | overrides))


def test_record_get_and_upsert_outcome(tracker):
    outcome_tracker, db_path = tracker
    application_id = _application(db_path, "upsert")

    first = _record(outcome_tracker, application_id, total_repaid=1000)
    second = _record(
        outcome_tracker,
        application_id,
        actual_default=True,
        default_date=date(2026, 2, 1),
        total_repaid=1500,
        outcome_status="DEFAULTED",
    )
    fetched = outcome_tracker.get_outcome(application_id)

    assert first.id == second.id == fetched.id
    assert fetched.actual_default
    assert fetched.default_date == date(2026, 2, 1)
    assert fetched.total_repaid == 1500
    assert len(outcome_tracker.list_outcomes()) == 1


def test_record_outcome_rejects_unknown_application_status_and_source(tracker):
    outcome_tracker, _db_path = tracker

    with pytest.raises(ValueError, match="not found"):
        _record(outcome_tracker, 99999)
    application_id = _application(_db_path, "invalid")
    with pytest.raises(ValueError, match="invalid outcome_status"):
        _record(outcome_tracker, application_id, outcome_status="UNKNOWN")
    with pytest.raises(ValueError, match="invalid source"):
        _record(outcome_tracker, application_id, source="UNKNOWN")


def test_outcome_models_validate_status_and_source():
    with pytest.raises(ValueError, match="invalid outcome_status"):
        OutcomeCreate(
            application_id=1,
            cohort_month="2026-01",
            source="CORE_BANKING",
            outcome_status="UNKNOWN",
        )
    with pytest.raises(ValueError, match="invalid source"):
        OutcomeCreate(
            application_id=1,
            cohort_month="2026-01",
            source="UNKNOWN",
        )
    with pytest.raises(ValueError, match="invalid outcome_status"):
        OutcomeUpdate(outcome_status="UNKNOWN")


def test_list_filters_and_rejects_invalid_filters(tracker):
    outcome_tracker, db_path = tracker
    performing_id = _application(db_path, "performing")
    defaulted_id = _application(db_path, "defaulted")
    _record(outcome_tracker, performing_id, source="CORE_BANKING")
    _record(
        outcome_tracker,
        defaulted_id,
        actual_default=True,
        default_date="2026-02-01",
        outcome_status="DEFAULTED",
        cohort_month="2026-02",
        source="BANK_STATEMENT",
    )

    assert [row.application_id for row in outcome_tracker.list_outcomes(
        cohort_month="2026-02", outcome_status="DEFAULTED", source="BANK_STATEMENT"
    )] == [defaulted_id]
    with pytest.raises(ValueError, match="invalid outcome_status"):
        outcome_tracker.list_outcomes(outcome_status="UNKNOWN")
    with pytest.raises(ValueError, match="invalid source"):
        outcome_tracker.list_outcomes(source="UNKNOWN")


def test_partial_update_preserves_cohort_and_checks_fields(tracker):
    outcome_tracker, db_path = tracker
    application_id = _application(db_path, "update")
    initial = _record(outcome_tracker, application_id)

    updated = outcome_tracker.update_outcome(
        application_id,
        outcome_status="NON_PERFORMING",
        num_missed_payments=2,
        last_payment_date=date(2026, 3, 1),
    )

    assert updated.id == initial.id
    assert updated.cohort_month == "2026-01"
    assert updated.outcome_status == "NON_PERFORMING"
    assert updated.num_missed_payments == 2
    assert updated.last_payment_date == date(2026, 3, 1)


def test_partial_update_rejects_unknown_invalid_and_missing_outcomes(tracker):
    outcome_tracker, db_path = tracker
    application_id = _application(db_path, "update-invalid")
    _record(outcome_tracker, application_id)

    with pytest.raises(ValueError, match="unknown outcome update"):
        outcome_tracker.update_outcome(application_id, arbitrary="x")
    with pytest.raises(ValueError, match="invalid outcome_status"):
        outcome_tracker.update_outcome(application_id, outcome_status="UNKNOWN")
    with pytest.raises(ValueError, match="invalid source"):
        outcome_tracker.update_outcome(application_id, source="UNKNOWN")
    assert outcome_tracker.update_outcome(99999, actual_default=True) is None


def test_cohort_overall_metrics_and_outstanding_loans(tracker):
    outcome_tracker, db_path = tracker
    performing_id = _application(db_path, "metrics-performing")
    npl_id = _application(db_path, "metrics-npl")
    default_id = _application(db_path, "metrics-default")
    _record(
        outcome_tracker,
        performing_id,
        total_repaid=100,
    )
    _record(
        outcome_tracker,
        npl_id,
        total_repaid=50,
        num_missed_payments=2,
        outcome_status="NON_PERFORMING",
    )
    _record(
        outcome_tracker,
        default_id,
        actual_default=True,
        default_date="2026-02-01",
        total_repaid=25,
        num_missed_payments=4,
        outcome_status="DEFAULTED",
        cohort_month="2026-02",
    )

    january, february = outcome_tracker.compute_cohort_metrics()
    overall = outcome_tracker.compute_overall_metrics()

    assert (january.total_loans, january.non_performing_count) == (2, 1)
    assert january.repayment_rate == 0.5
    assert january.avg_missed_payments == 1
    assert january.total_repaid == 150
    assert (february.total_loans, february.defaulted_count, february.default_rate) == (
        1, 1, 1
    )
    assert (overall.total_loans, overall.defaulted_count) == (3, 1)
    assert overall.npl_rate == round(2 / 3, 4)
    assert overall.total_repaid == 175
    assert outcome_tracker.compute_cohort_metrics("missing") == []

    outstanding_id = _application(db_path, "outstanding")
    assert [row["id"] for row in outcome_tracker.get_outstanding_loans()] == [
        outstanding_id
    ]
