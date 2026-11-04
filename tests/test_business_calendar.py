from __future__ import annotations

import json
from datetime import datetime

import pytest

from pipeline.workflow.business_calendar import business_deadline, configured_due_at
from pipeline.workflow.default_calendar import (
    compute_due_at,
    get_default_calendar,
    get_sla_hours,
    is_business_day,
    is_business_hours,
    next_business_day,
)


@pytest.fixture
def calendar():
    return {
        "timezone": "Asia/Ho_Chi_Minh",
        "weekdays": [0, 1, 2, 3, 4],
        "start": "08:00:00",
        "end": "17:00:00",
        "holidays": ["2026-01-01", "2026-01-05"],
        "sla_hours_by_kind": {"REVIEW": 2},
    }


def test_business_deadline_rolls_remaining_hours_across_workdays(calendar):
    due = business_deadline("2026-01-02T16:00:00+07:00", 2, calendar)

    assert due == "2026-01-06T09:00:00+07:00"


def test_business_deadline_clamps_before_hours_and_handles_exact_close(calendar):
    assert business_deadline("2026-01-02T06:00:00+07:00", 1, calendar) == (
        "2026-01-02T09:00:00+07:00"
    )
    assert business_deadline("2026-01-02T08:00:00+07:00", 9, calendar) == (
        "2026-01-02T17:00:00+07:00"
    )


@pytest.mark.parametrize(
    ("start_iso", "hours", "message"),
    [
        ("2026-01-02T08:00:00+07:00", 0, "positive"),
        ("2026-01-02T08:00:00", 1, "timezone"),
    ],
)
def test_business_deadline_rejects_invalid_inputs(calendar, start_iso, hours, message):
    with pytest.raises(ValueError, match=message):
        business_deadline(start_iso, hours, calendar)


@pytest.mark.parametrize(
    "updates",
    [
        {"weekdays": []},
        {"weekdays": [7]},
        {"start": "17:00:00", "end": "08:00:00"},
    ],
)
def test_business_deadline_rejects_invalid_calendar(calendar, updates):
    with pytest.raises(ValueError, match="Invalid work calendar"):
        business_deadline(
            "2026-01-02T08:00:00+07:00", 1, {**calendar, **updates}
        )


def test_default_calendar_uses_environment_and_computes_due_at(monkeypatch, calendar):
    monkeypatch.setenv("CREDITFLOW_WORK_CALENDAR_JSON", json.dumps(calendar))

    assert get_default_calendar() == calendar
    assert get_sla_hours("REVIEW") == 2
    assert compute_due_at("REVIEW", "2026-01-02T16:00:00+07:00") == (
        "2026-01-06T09:00:00+07:00"
    )


def test_default_calendar_has_sacombank_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_WORK_CALENDAR_JSON", raising=False)

    config = get_default_calendar()

    assert config["timezone"] == "Asia/Ho_Chi_Minh"
    assert config["weekdays"] == [1, 2, 3, 4, 5]
    assert get_sla_hours("APPROVAL") == 48
    assert "2026-04-30" in config["holidays"]


def test_unconfigured_sla_is_explicit_and_unknown_kind_fails(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_WORK_CALENDAR_JSON", raising=False)

    assert configured_due_at("REVIEW") == ""
    with pytest.raises(ValueError, match="SLA is not configured"):
        compute_due_at("UNKNOWN", "2026-01-02T08:00:00+07:00")


def test_configured_due_at_requires_configured_kind(monkeypatch, calendar):
    monkeypatch.setenv("CREDITFLOW_WORK_CALENDAR_JSON", json.dumps(calendar))

    with pytest.raises(ValueError, match="SLA is not configured"):
        configured_due_at("UNKNOWN")


def test_business_day_hours_and_next_business_day_use_calendar(monkeypatch, calendar):
    monkeypatch.setenv("CREDITFLOW_WORK_CALENDAR_JSON", json.dumps(calendar))

    assert is_business_day("2026-01-02")
    assert not is_business_day("2026-01-03")
    assert not is_business_day("2026-01-05")
    assert is_business_hours(datetime.fromisoformat("2026-01-02T16:59:00+07:00"))
    assert not is_business_hours(datetime.fromisoformat("2026-01-02T17:00:00+07:00"))
    assert is_business_hours(datetime.fromisoformat("2026-01-02T09:00:00"))
    assert next_business_day(datetime.fromisoformat("2026-01-02T12:00:00+07:00")).isoformat() == (
        "2026-01-06"
    )
