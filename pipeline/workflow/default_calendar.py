"""Default Sacombank business calendar configuration.

Falls back to CREDITFLOW_WORK_CALENDAR_JSON env var when present; otherwise
uses the hardcoded Sacombank defaults (Asia/Ho_Chi_Minh, Mon-Fri, 08:00-17:00,
Vietnamese public holidays 2026).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from pipeline.workflow.business_calendar import business_deadline

DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"
DEFAULT_WEEKDAYS = [1, 2, 3, 4, 5]
DEFAULT_START = "08:00:00"
DEFAULT_END = "17:00:00"
DEFAULT_HOLIDAYS = [
    "2026-01-01",
    "2026-01-28",
    "2026-01-29",
    "2026-01-30",
    "2026-01-31",
    "2026-02-01",
    "2026-02-02",
    "2026-02-03",
    "2026-04-30",
    "2026-05-01",
    "2026-09-02",
    "2026-09-03",
]
DEFAULT_SLA_HOURS_BY_KIND = {
    "DOCUMENT_CHECK": 24,
    "APPROVAL": 48,
    "REVIEW": 72,
    "EXCEPTION": 24,
}


def get_default_calendar() -> dict:
    """Return the default Sacombank calendar dict."""
    raw = os.environ.get("CREDITFLOW_WORK_CALENDAR_JSON", "").strip()
    if raw:
        return json.loads(raw)
    return {
        "timezone": DEFAULT_TIMEZONE,
        "weekdays": list(DEFAULT_WEEKDAYS),
        "start": DEFAULT_START,
        "end": DEFAULT_END,
        "holidays": list(DEFAULT_HOLIDAYS),
        "sla_hours_by_kind": dict(DEFAULT_SLA_HOURS_BY_KIND),
    }


def get_sla_hours(task_kind: str) -> float | None:
    """Return SLA hours for a task kind, or None if not configured."""
    return get_default_calendar().get("sla_hours_by_kind", {}).get(task_kind)


def compute_due_at(task_kind: str, start_iso: str | None = None) -> str:
    """Compute due datetime for a task kind using the default calendar."""
    calendar = get_default_calendar()
    hours = calendar.get("sla_hours_by_kind", {}).get(task_kind)
    if hours is None:
        raise ValueError(f"SLA is not configured for {task_kind}")
    if start_iso is None:
        start_iso = datetime.now(ZoneInfo(calendar["timezone"])).isoformat()
    return business_deadline(start_iso, float(hours), calendar)


def is_business_day(date_iso: str) -> bool:
    """Check if a date is a business day."""
    calendar = get_default_calendar()
    d = date.fromisoformat(date_iso)
    if d.isoformat() in set(calendar.get("holidays", [])):
        return False
    return d.weekday() in set(calendar["weekdays"])


def is_business_hours(dt: datetime) -> bool:
    """Check if a datetime is within business hours."""
    calendar = get_default_calendar()
    zone = ZoneInfo(calendar["timezone"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    else:
        dt = dt.astimezone(zone)
    weekdays = set(calendar["weekdays"])
    holidays = set(calendar.get("holidays", []))
    start_time = time.fromisoformat(calendar["start"])
    end_time = time.fromisoformat(calendar["end"])
    if dt.weekday() not in weekdays:
        return False
    if dt.date().isoformat() in holidays:
        return False
    return start_time <= dt.time() < end_time


def next_business_day(dt: datetime) -> date:
    """Return the next business day strictly after dt's date."""
    calendar = get_default_calendar()
    zone = ZoneInfo(calendar["timezone"])
    weekdays = set(calendar["weekdays"])
    holidays = set(calendar.get("holidays", []))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=zone)
    else:
        dt = dt.astimezone(zone)
    candidate = dt.date() + timedelta(days=1)
    for _ in range(370):
        if candidate.weekday() in weekdays and candidate.isoformat() not in holidays:
            return candidate
        candidate += timedelta(days=1)
    raise ValueError("No business day found within one calendar year")
