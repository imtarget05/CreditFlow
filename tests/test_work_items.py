"""Durable review queue and role-scoped action contract."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.security import Principal
from pipeline.storage.ledger import _connect, init_db
from pipeline.storage.work_items import (
    create_work_item,
    enqueue_due_reminders,
    get_work_item,
    init_work_items,
    list_due_notifications,
    list_work_items,
    transition_work_item,
)
from pipeline.workflow.business_calendar import business_deadline


def test_work_item_is_durable_and_creation_is_idempotent(tmp_path):
    db = tmp_path / "work.db"
    init_work_items(db)
    first = create_work_item("APP-1", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-001", db)
    again = create_work_item("APP-1", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-001", db)
    assert first == again
    assert get_work_item(first["id"], db)["status"] == "OPEN"


def test_creation_replay_ignores_later_calendar_change(tmp_path, monkeypatch):
    db = tmp_path / "work.db"
    init_work_items(db)
    first = create_work_item("APP-1", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-001", db)
    monkeypatch.setenv("CREDITFLOW_WORK_CALENDAR_JSON", "invalid-json")
    assert create_work_item("APP-1", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-001", db) == first


def test_claim_and_complete_require_actor_and_idempotency_key(tmp_path):
    db = tmp_path / "work.db"
    init_work_items(db)
    task = create_work_item("APP-2", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-002", db)
    actor = Principal("analyst_a", "UNDERWRITER_L1")
    claimed = transition_work_item(task["id"], "claim", actor, "claim-001", "Taking case", db)
    assert claimed["assigned_to"] == "analyst_a"
    assert transition_work_item(task["id"], "claim", actor, "claim-001", "Taking case", db) == claimed
    with pytest.raises(ValueError, match="Request id reused"):
        transition_work_item(task["id"], "claim", actor, "claim-001", "Changed reason", db)
    done = transition_work_item(task["id"], "complete", actor, "complete-001", "Documents checked", db)
    assert done["status"] == "DONE"
    assert transition_work_item(task["id"], "claim", actor, "claim-001", "Taking case", db) == claimed
    assert done in list_work_items(actor, db)
    followup = next(item for item in list_work_items(actor, db) if item["kind"] == "UNDERWRITING")
    assert followup["status"] == "OPEN"


def test_other_actor_cannot_complete_assigned_task(tmp_path):
    db = tmp_path / "work.db"
    init_work_items(db)
    task = create_work_item("APP-3", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-003", db)
    transition_work_item(task["id"], "claim", Principal("a", "UNDERWRITER_L1"), "claim-003", "Taking case", db)
    with pytest.raises(PermissionError):
        transition_work_item(task["id"], "complete", Principal("b", "UNDERWRITER_L1"), "complete-003", "Not mine", db)


def test_manager_assigns_configured_staff_with_replay_and_audit(tmp_path, monkeypatch):
    db = tmp_path / "work.db"
    init_work_items(db)
    monkeypatch.setenv("CREDITFLOW_PILOT_STAFF_JSON", json.dumps({"analyst_a": "UNDERWRITER_L1", "analyst_b": "UNDERWRITER_L1"}))
    task = create_work_item("APP-ASSIGN", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-assign", db)
    manager = Principal("manager", "RISK_COMMITTEE_L2")
    assigned = transition_work_item(task["id"], "assign", manager, "assign-001", "Workload balance", db, assignee="analyst_a")
    assert assigned["assigned_to"] == "analyst_a"
    assert assigned["status"] == "IN_PROGRESS"
    assert transition_work_item(task["id"], "assign", manager, "assign-001", "Workload balance", db, assignee="analyst_a") == assigned
    with pytest.raises(ValueError, match="Request id reused"):
        transition_work_item(task["id"], "assign", manager, "assign-001", "Workload balance", db, assignee="analyst_b")
    with _connect(db) as conn:
        event = conn.execute("SELECT actor, reason, target_identity FROM work_item_events WHERE request_id = ?", ("assign-001",)).fetchone()
    assert (event["actor"], event["reason"], event["target_identity"]) == ("manager", "Workload balance", "analyst_a")


def test_assignment_rejects_unknown_staff_and_wrong_role(tmp_path, monkeypatch):
    db = tmp_path / "work.db"
    init_work_items(db)
    monkeypatch.setenv("CREDITFLOW_PILOT_STAFF_JSON", json.dumps({"risk_a": "RISK_COMMITTEE_L2"}))
    task = create_work_item("APP-ASSIGN", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-assign", db)
    manager = Principal("manager", "RISK_COMMITTEE_L2")
    with pytest.raises(ValueError, match="configured staff"):
        transition_work_item(task["id"], "assign", manager, "assign-002", "Assign", db, assignee="missing")
    with pytest.raises(ValueError, match="role"):
        transition_work_item(task["id"], "assign", manager, "assign-003", "Assign", db, assignee="risk_a")
    with pytest.raises(PermissionError):
        transition_work_item(task["id"], "assign", Principal("analyst", "UNDERWRITER_L1"), "assign-004", "Assign", db, assignee="risk_a")


def test_pilot_assignment_is_closed_in_production(tmp_path, monkeypatch):
    db = tmp_path / "work.db"
    init_work_items(db)
    task = create_work_item("APP-PROD", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-prod", db)
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setenv("CREDITFLOW_PILOT_STAFF_JSON", json.dumps({"analyst_a": "UNDERWRITER_L1"}))
    with pytest.raises(PermissionError):
        transition_work_item(task["id"], "assign", Principal("manager", "RISK_COMMITTEE_L2"), "assign-prod", "Assign", db, assignee="analyst_a")


def test_owner_escalates_task_to_risk_queue(tmp_path):
    db = tmp_path / "work.db"
    init_work_items(db)
    task = create_work_item("APP-ESC", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-esc", db)
    analyst = Principal("analyst_a", "UNDERWRITER_L1")
    transition_work_item(task["id"], "claim", analyst, "claim-esc", "Review", db)
    escalated = transition_work_item(task["id"], "escalate", analyst, "escalate-001", "Policy exception", db)
    assert (escalated["assigned_role"], escalated["assigned_to"], escalated["status"]) == ("RISK_COMMITTEE_L2", "", "OPEN")
    assert escalated in list_work_items(Principal("risk_a", "RISK_COMMITTEE_L2"), db)
    assert escalated not in list_work_items(analyst, db)
    assert transition_work_item(task["id"], "escalate", analyst, "escalate-001", "Policy exception", db) == escalated


def test_supplement_request_pauses_completion_until_resumed(tmp_path):
    db = tmp_path / "work.db"
    init_work_items(db)
    task = create_work_item("APP-SUP", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-sup", db)
    analyst = Principal("analyst_a", "UNDERWRITER_L1")
    transition_work_item(task["id"], "claim", analyst, "claim-sup", "Review", db)
    waiting = transition_work_item(task["id"], "request_supplement", analyst, "supplement-001", "Missing income proof", db)
    assert waiting["status"] == "WAITING_SUPPLEMENT"
    with pytest.raises(ValueError):
        transition_work_item(task["id"], "complete", analyst, "complete-too-early", "Done", db)
    resumed = transition_work_item(task["id"], "resume", analyst, "resume-001", "Income proof received", db)
    assert resumed["status"] == "IN_PROGRESS"
    assert transition_work_item(task["id"], "complete", analyst, "complete-sup", "Checked", db)["status"] == "DONE"


def test_completion_chains_review_tasks_once_without_credit_decision(tmp_path):
    db = tmp_path / "work.db"
    init_work_items(db)
    analyst = Principal("analyst_a", "UNDERWRITER_L1")
    risk = Principal("risk_a", "RISK_COMMITTEE_L2")
    intake = create_work_item("APP-FLOW", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-flow", db)
    transition_work_item(intake["id"], "claim", analyst, "claim-flow", "Take", db)
    transition_work_item(intake["id"], "complete", analyst, "complete-flow", "Docs verified", db)
    underwriting = next(item for item in list_work_items(analyst, db) if item["kind"] == "UNDERWRITING")
    transition_work_item(underwriting["id"], "claim", analyst, "claim-underwrite", "Review", db)
    transition_work_item(underwriting["id"], "complete", analyst, "complete-underwrite", "Recommendation recorded", db)
    approval_review = next(item for item in list_work_items(risk, db) if item["kind"] == "APPROVAL")
    assert approval_review["status"] == "OPEN"
    assert approval_review["application_id"] == "APP-FLOW"
    with _connect(db) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM work_items WHERE application_id = ?", ("APP-FLOW",)).fetchone()["n"]
    assert count == 3


def test_work_queue_endpoint_requires_authentication():
    assert TestClient(app).get("/api/work-items").status_code == 401
    assert TestClient(app).get("/api/notifications").status_code == 401


def test_business_deadline_skips_nonworking_hours():
    calendar = {"timezone": "Asia/Ho_Chi_Minh", "weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00", "holidays": []}
    assert business_deadline("2026-11-02T16:00:00+07:00", 2, calendar) == "2026-11-03T10:00:00+07:00"


def test_due_task_enqueues_one_internal_reminder(tmp_path):
    db = tmp_path / "work.db"
    init_db(db)
    init_work_items(db)
    create_work_item("APP-4", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-004", db, due_at="2026-11-03T01:00:00+00:00")
    assert enqueue_due_reminders("2026-11-03T02:00:00+00:00", db) == 1
    assert enqueue_due_reminders("2026-11-03T02:00:00+00:00", db) == 0
    with _connect(db) as conn:
        row = conn.execute("SELECT destination FROM outbox_events WHERE event_id LIKE 'work-due:%'").fetchone()
    assert row["destination"] == "internal_notifications"


def test_in_app_reminders_follow_current_assignment_and_close_with_task(tmp_path):
    db = tmp_path / "work.db"
    init_db(db)
    init_work_items(db)
    task = create_work_item("APP-REM", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-rem", db, due_at="2026-11-03T01:00:00+00:00")
    assert enqueue_due_reminders("2026-11-03T02:00:00+00:00", db) == 1
    analyst_a = Principal("analyst_a", "UNDERWRITER_L1")
    analyst_b = Principal("analyst_b", "UNDERWRITER_L1")
    assert len(list_due_notifications(analyst_a, db)) == 1
    transition_work_item(task["id"], "claim", analyst_a, "claim-rem", "Review", db)
    assert len(list_due_notifications(analyst_a, db)) == 1
    assert list_due_notifications(analyst_b, db) == []
    transition_work_item(task["id"], "complete", analyst_a, "complete-rem", "Done", db)
    assert list_due_notifications(analyst_a, db) == []


def test_reminder_api_is_machine_only(monkeypatch, tmp_path):
    from tests.auth_support import AUTH_HEADERS, configure_auth_env
    configure_auth_env(monkeypatch)
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(tmp_path / "reminders.db"))
    init_db(tmp_path / "reminders.db")
    init_work_items(tmp_path / "reminders.db")
    create_work_item("APP-5", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-005", due_at="2026-11-03T01:00:00+00:00")
    client = TestClient(app)
    url = "/api/work-items/reminders/run"
    assert client.post(url, json={"now": "2026-11-03T02:00:00+00:00"}).status_code == 401
    response = client.post(url, json={"now": "2026-11-03T02:00:00+00:00"}, headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["queued"] == 1


def test_manager_assignment_api_uses_configured_staff(monkeypatch, tmp_path):
    from tests.auth_support import AUTH_HEADERS, configure_auth_env
    configure_auth_env(monkeypatch)
    db = tmp_path / "assign-api.db"
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(db))
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "RISK_COMMITTEE_L2")
    monkeypatch.setenv("CREDITFLOW_PILOT_STAFF_JSON", json.dumps({"analyst_a": "UNDERWRITER_L1"}))
    # SQLite-local test: ensure DATABASE_URL does not redirect to Postgres.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    init_db(db)
    init_work_items(db)
    task = create_work_item("APP-API", "DOCUMENT_CHECK", "UNDERWRITER_L1", "create-api", db)
    response = TestClient(app).post(
        f"/api/work-items/{task['id']}/actions",
        json={"action": "assign", "request_id": "assign-api-001", "reason": "Pilot allocation", "assignee": "analyst_a"},
        headers=AUTH_HEADERS,
    )
    assert response.status_code == 200, response.text
    assert response.json()["assigned_to"] == "analyst_a"
