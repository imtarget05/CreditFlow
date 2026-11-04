"""Durable, role-scoped review queue for human loan processing."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.security import Principal, is_production
from pipeline.storage.ledger import _connect, _schema_change_errors, _to_row
from pipeline.workflow.business_calendar import configured_due_at

KINDS = {"DOCUMENT_CHECK", "UNDERWRITING", "APPROVAL", "EXCEPTION"}
ROLES = {"UNDERWRITER_L1", "RISK_COMMITTEE_L2"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def init_work_items(db_path: Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS work_items (
                id TEXT PRIMARY KEY,
                application_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                assigned_role TEXT NOT NULL,
                assigned_to TEXT NOT NULL DEFAULT '',
                due_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_request_id TEXT NOT NULL UNIQUE
            );
            CREATE INDEX IF NOT EXISTS idx_work_items_queue
                ON work_items (assigned_role, status, due_at);
            CREATE TABLE IF NOT EXISTS work_item_events (
                request_id TEXT PRIMARY KEY,
                work_item_id TEXT NOT NULL,
                action TEXT NOT NULL,
                actor TEXT NOT NULL,
                reason TEXT NOT NULL,
                target_identity TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                response TEXT NOT NULL DEFAULT ''
            );
        """)
        try:
            conn.execute("ALTER TABLE work_item_events ADD COLUMN response TEXT NOT NULL DEFAULT ''")
        except _schema_change_errors():
            pass
        try:
            conn.execute("ALTER TABLE work_item_events ADD COLUMN target_identity TEXT NOT NULL DEFAULT ''")
        except _schema_change_errors():
            pass


def get_work_item(work_item_id: str, db_path: Path | None = None) -> dict | None:
    with _connect(db_path) as conn:
        return _to_row(conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone())


def create_work_item(
    application_id: str,
    kind: str,
    assigned_role: str,
    request_id: str,
    db_path: Path | None = None,
    due_at: str = "",
) -> dict:
    if kind not in KINDS or assigned_role not in ROLES or not application_id or not request_id:
        raise ValueError("Invalid work item fields")
    with _connect(db_path) as conn:
        existing = conn.execute("SELECT * FROM work_items WHERE created_request_id = ?", (request_id,)).fetchone()
        if existing:
            result = _to_row(existing)
            if (result["application_id"], result["kind"], result["assigned_role"]) != (application_id, kind, assigned_role):
                raise ValueError("Request id reused for a different work item")
            return result
        due_at = due_at or configured_due_at(kind)
        if due_at:
            parsed_due = datetime.fromisoformat(due_at)
            if parsed_due.tzinfo is None:
                raise ValueError("Due time must include a timezone")
            due_at = parsed_due.astimezone(UTC).isoformat()
        now = _now()
        item_id = f"work_{uuid4().hex}"
        conn.execute(
            "INSERT INTO work_items (id, application_id, kind, status, assigned_role, assigned_to, due_at, created_at, updated_at, created_request_id) VALUES (?, ?, ?, 'OPEN', ?, '', ?, ?, ?, ?) ON CONFLICT(created_request_id) DO NOTHING",
            (item_id, application_id, kind, assigned_role, due_at, now, now, request_id),
        )
        result = _to_row(conn.execute("SELECT * FROM work_items WHERE created_request_id = ?", (request_id,)).fetchone())
        if (result["application_id"], result["kind"], result["assigned_role"]) != (application_id, kind, assigned_role):
            raise ValueError("Request id reused for a different work item")
        return result


def enqueue_due_reminders(now_iso: str, db_path: Path | None = None) -> int:
    """Queue one internal notification per overdue work item and deadline."""
    parsed_now = datetime.fromisoformat(now_iso)
    if parsed_now.tzinfo is None:
        raise ValueError("Current time must include a timezone")
    now_utc = parsed_now.astimezone(UTC).isoformat()
    count = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, application_id, assigned_role, assigned_to, due_at FROM work_items WHERE status != 'DONE' AND due_at != '' AND due_at <= ?",
            (now_utc,),
        ).fetchall()
        for row in rows:
            event_id = f"work-due:{row['id']}:{row['due_at']}"
            payload = json.dumps({"work_item_id": row["id"], "application_id": row["application_id"], "assigned_role": row["assigned_role"], "assigned_to": row["assigned_to"], "due_at": row["due_at"]})
            count += conn.execute(
                "INSERT INTO outbox_events (event_id, destination, payload, version) VALUES (?, 'internal_notifications', ?, 'v1') ON CONFLICT(event_id) DO NOTHING",
                (event_id, payload),
            ).rowcount
    return count


def list_work_items(principal: Principal, db_path: Path | None = None) -> list[dict]:
    with _connect(db_path) as conn:
        if principal.role == "RISK_COMMITTEE_L2" and not is_production():
            rows = conn.execute("SELECT * FROM work_items ORDER BY created_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM work_items WHERE assigned_to = ? OR (assigned_to = '' AND assigned_role = ?) ORDER BY created_at DESC",
                (principal.identity, principal.role),
            ).fetchall()
    return [_to_row(row) for row in rows]


def list_due_notifications(principal: Principal, db_path: Path | None = None) -> list[dict]:
    """In-app reminder feed; authorization follows the task's current owner."""
    visible = {item["id"]: item for item in list_work_items(principal, db_path) if item["status"] != "DONE"}
    if not visible:
        return []
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT event_id, payload FROM outbox_events WHERE destination = 'internal_notifications' ORDER BY event_id DESC"
        ).fetchall()
    notifications = []
    for row in rows:
        payload = json.loads(row["payload"])
        item = visible.get(payload.get("work_item_id"))
        if item and item["due_at"] == payload.get("due_at"):
            notifications.append({
                "event_id": row["event_id"],
                "work_item_id": item["id"],
                "application_id": item["application_id"],
                "due_at": item["due_at"],
            })
    return notifications


def _configured_pilot_staff() -> dict[str, str]:
    try:
        staff = json.loads(os.environ.get("CREDITFLOW_PILOT_STAFF_JSON", "{}"))
    except (ValueError, TypeError):
        raise ValueError("Pilot staff configuration is invalid")
    if not isinstance(staff, dict) or any(not isinstance(identity, str) or role not in ROLES for identity, role in staff.items()):
        raise ValueError("Pilot staff configuration is invalid")
    return staff


def transition_work_item(
    work_item_id: str,
    action: str,
    principal: Principal,
    request_id: str,
    reason: str,
    db_path: Path | None = None,
    assignee: str = "",
) -> dict:
    if action not in {"claim", "complete", "assign", "escalate", "request_supplement", "resume"} or not request_id or not reason.strip():
        raise ValueError("Invalid work item action")
    assignee = assignee.strip()
    if action != "assign" and assignee:
        raise ValueError("Assignee only applies to assignment")
    with _connect(db_path) as conn:
        prior = conn.execute("SELECT work_item_id, actor, action, reason, target_identity, response FROM work_item_events WHERE request_id = ?", (request_id,)).fetchone()
        if prior:
            if (prior["work_item_id"], prior["actor"], prior["action"], prior["reason"], prior["target_identity"]) != (work_item_id, principal.identity, action, reason.strip(), assignee):
                raise ValueError("Request id reused for a different action")
            return json.loads(prior["response"]) if prior["response"] else _to_row(conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone())
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
        if row is None:
            raise LookupError("Work item not found")
        item = _to_row(row)
        if action == "assign":
            if is_production() or principal.role != "RISK_COMMITTEE_L2":
                raise PermissionError("Assignment requires the pilot manager role")
            staff = _configured_pilot_staff()
            if assignee not in staff:
                raise ValueError("Assignee is not configured staff")
            if staff[assignee] != item["assigned_role"]:
                raise ValueError("Assignee role does not match work item role")
        elif item["assigned_role"] != principal.role or item["assigned_to"] not in ("", principal.identity):
            raise PermissionError("Work item is outside the actor's assignment")
        now = _now()
        if action == "claim" and item["status"] == "OPEN":
            changed = conn.execute(
                "UPDATE work_items SET status = 'IN_PROGRESS', assigned_to = ?, updated_at = ? WHERE id = ? AND status = 'OPEN' AND assigned_to = ''",
                (principal.identity, now, work_item_id),
            ).rowcount
        elif action == "complete" and item["status"] == "IN_PROGRESS" and item["assigned_to"] == principal.identity:
            changed = conn.execute(
                "UPDATE work_items SET status = 'DONE', updated_at = ? WHERE id = ? AND status = 'IN_PROGRESS' AND assigned_to = ?",
                (now, work_item_id, principal.identity),
            ).rowcount
        elif action == "assign" and item["status"] in {"OPEN", "IN_PROGRESS", "WAITING_SUPPLEMENT"}:
            changed = conn.execute(
                "UPDATE work_items SET status = CASE WHEN status = 'OPEN' THEN 'IN_PROGRESS' ELSE status END, assigned_to = ?, updated_at = ? WHERE id = ? AND status = ? AND assigned_to = ?",
                (assignee, now, work_item_id, item["status"], item["assigned_to"]),
            ).rowcount
        elif action == "escalate" and item["assigned_role"] == "UNDERWRITER_L1" and item["status"] == "IN_PROGRESS" and item["assigned_to"] == principal.identity:
            changed = conn.execute(
                "UPDATE work_items SET status = 'OPEN', assigned_role = 'RISK_COMMITTEE_L2', assigned_to = '', updated_at = ? WHERE id = ? AND status = 'IN_PROGRESS' AND assigned_to = ? AND assigned_role = 'UNDERWRITER_L1'",
                (now, work_item_id, principal.identity),
            ).rowcount
        elif action == "request_supplement" and item["status"] == "IN_PROGRESS" and item["assigned_to"] == principal.identity:
            changed = conn.execute(
                "UPDATE work_items SET status = 'WAITING_SUPPLEMENT', updated_at = ? WHERE id = ? AND status = 'IN_PROGRESS' AND assigned_to = ?",
                (now, work_item_id, principal.identity),
            ).rowcount
        elif action == "resume" and item["status"] == "WAITING_SUPPLEMENT" and item["assigned_to"] == principal.identity:
            changed = conn.execute(
                "UPDATE work_items SET status = 'IN_PROGRESS', updated_at = ? WHERE id = ? AND status = 'WAITING_SUPPLEMENT' AND assigned_to = ?",
                (now, work_item_id, principal.identity),
            ).rowcount
        else:
            raise ValueError("Work item cannot take this action in its current state")
        if changed != 1:
            raise ValueError("Concurrent work item update; refresh and retry")
        if action == "complete":
            next_step = {
                "DOCUMENT_CHECK": ("UNDERWRITING", "UNDERWRITER_L1"),
                "UNDERWRITING": ("APPROVAL", "RISK_COMMITTEE_L2"),
            }.get(item["kind"])
            if next_step:
                next_kind, next_role = next_step
                next_due = configured_due_at(next_kind)
                if next_due:
                    next_due = datetime.fromisoformat(next_due).astimezone(UTC).isoformat()
                conn.execute(
                    "INSERT INTO work_items (id, application_id, kind, status, assigned_role, assigned_to, due_at, created_at, updated_at, created_request_id) VALUES (?, ?, ?, 'OPEN', ?, '', ?, ?, ?, ?) ON CONFLICT(created_request_id) DO NOTHING",
                    (f"work_{uuid4().hex}", item["application_id"], next_kind, next_role, next_due, now, now, f"followup:{work_item_id}"),
                )
        result = _to_row(conn.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone())
        conn.execute(
            "INSERT INTO work_item_events (request_id, work_item_id, action, actor, reason, target_identity, created_at, response) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, work_item_id, action, principal.identity, reason.strip(), assignee, now, json.dumps(result)),
        )
        return result
