"""Conservative pilot signal for repeated synthetic application profiles.

These features do not identify a customer. A match opens a manual review task;
it must never merge cases or decide a credit application automatically.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from pipeline.storage.ledger import list_applications

PROFILE_FIELDS = (
    "income", "age", "employment_years", "loan_amount", "loan_term",
    "existing_debt", "credit_history", "previous_defaults",
)


def _profile_key(data: dict) -> tuple[str, ...] | None:
    if not isinstance(data, dict):
        return None
    values = []
    for field in PROFILE_FIELDS:
        raw = data.get(field)
        if raw is None or isinstance(raw, bool):
            return None
        try:
            number = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return None
        if not number.is_finite():
            return None
        values.append(str(number.normalize()))
    return tuple(values)


def find_possible_profile_matches(
    customer_data: dict,
    exclude_thread_id: str = "",
    db_path: Path | None = None,
) -> list[str]:
    """Return prior case IDs with identical scoring features, capped at ten."""
    key = _profile_key(customer_data)
    if key is None:
        return []
    matches = []
    for row in list_applications(db_path=db_path):
        if row.get("thread_id") == exclude_thread_id:
            continue
        if _profile_key(row.get("customer_data") or {}) == key:
            matches.append(row.get("application_id") or row["thread_id"])
            if len(matches) == 10:
                break
    return matches
