"""Calculate task deadlines from an explicitly configured business calendar."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


def business_deadline(start_iso: str, hours: float, calendar: dict) -> str:
    if hours <= 0:
        raise ValueError("SLA hours must be positive")
    zone = ZoneInfo(calendar["timezone"])
    current = datetime.fromisoformat(start_iso)
    if current.tzinfo is None:
        raise ValueError("Start time must include a timezone")
    current = current.astimezone(zone)
    weekdays = set(calendar["weekdays"])
    holidays = set(calendar.get("holidays", []))
    start_time = time.fromisoformat(calendar["start"])
    end_time = time.fromisoformat(calendar["end"])
    if not weekdays or not weekdays.issubset(set(range(7))) or start_time >= end_time:
        raise ValueError("Invalid work calendar")
    remaining = timedelta(hours=hours)
    for _ in range(370):
        day_start = datetime.combine(current.date(), start_time, zone)
        day_end = datetime.combine(current.date(), end_time, zone)
        if current.weekday() in weekdays and current.date().isoformat() not in holidays:
            current = max(current, day_start)
            if current < day_end:
                available = day_end - current
                if remaining <= available:
                    return (current + remaining).isoformat()
                remaining -= available
        current = datetime.combine(current.date() + timedelta(days=1), start_time, zone)
    raise ValueError("SLA exceeds one calendar year")


def configured_due_at(kind: str) -> str:
    """No implicit bank SLA: absent configuration means no automatic deadline."""
    raw = os.environ.get("CREDITFLOW_WORK_CALENDAR_JSON", "").strip()
    if not raw:
        return ""
    calendar = json.loads(raw)
    hours = calendar.get("sla_hours_by_kind", {}).get(kind)
    if hours is None:
        raise ValueError(f"SLA is not configured for {kind}")
    return business_deadline(datetime.now(UTC).isoformat(), float(hours), calendar)
