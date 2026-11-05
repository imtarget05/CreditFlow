"""Durable notification delivery and retry contracts with a local provider fake."""

import sqlite3

from pipeline.notification.adapter import (
    ChannelAdapter,
    NotificationAdapter,
    NotificationChannel,
)


class RecordingEmail(ChannelAdapter):
    channel = NotificationChannel.EMAIL

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, str, str]] = []

    def deliver(self, recipient: str, subject: str, content: str) -> str:
        self.calls.append((recipient, subject, content))
        if len(self.calls) <= self.failures:
            raise RuntimeError("provider unavailable")
        return f"provider-{len(self.calls)}"


def test_failed_delivery_retries_without_duplicate_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_RETRY_BASE_SECONDS", "0")
    db_path = tmp_path / "notifications.db"
    email = RecordingEmail(failures=1)
    adapter = NotificationAdapter(
        db_path=db_path,
        channel_adapters={NotificationChannel.EMAIL: email},
    )

    notification_id = adapter.send(
        recipient_id="staff-1",
        recipient_type="user",
        channel=NotificationChannel.EMAIL,
        subject="Assignment",
        content="Synthetic case assigned",
        idempotency_key="assignment:case-1:staff-1",
    )
    first = adapter.get_status(notification_id)
    assert first is not None
    assert first["status"] == "FAILED"
    assert first["attempts"] == 1
    assert first["next_retry_at"] is not None

    assert adapter.retry_failed() == 1
    final = adapter.get_status(notification_id)
    assert final is not None
    assert final["status"] == "SENT"
    assert final["message_id"] == "provider-2"
    assert final["sent_at"] is not None
    assert adapter.retry_failed() == 0

    replay_id = adapter.send(
        recipient_id="staff-1",
        recipient_type="user",
        channel=NotificationChannel.EMAIL,
        subject="Assignment",
        content="Synthetic case assigned",
        idempotency_key="assignment:case-1:staff-1",
    )
    assert replay_id == notification_id
    assert len(email.calls) == 2


def test_delivery_stops_after_retry_budget_is_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFICATION_RETRY_BASE_SECONDS", "0")
    monkeypatch.setenv("NOTIFICATION_MAX_RETRY_ATTEMPTS", "2")
    email = RecordingEmail(failures=99)
    adapter = NotificationAdapter(
        db_path=tmp_path / "notifications.db",
        channel_adapters={NotificationChannel.EMAIL: email},
    )

    notification_id = adapter.send(
        recipient_id="staff-1",
        recipient_type="user",
        channel=NotificationChannel.EMAIL,
        subject="Synthetic assignment",
        content="Synthetic case assigned",
    )
    assert adapter.retry_failed() == 0
    exhausted = adapter.get_status(notification_id)
    assert exhausted is not None
    assert exhausted["status"] == "FAILED"
    assert exhausted["attempts"] == 2
    assert adapter.retry_failed() == 0
    assert len(email.calls) == 2


def test_case_notifications_route_to_distinct_recipients_and_replay_safely(tmp_path):
    email = RecordingEmail()
    adapter = NotificationAdapter(
        db_path=tmp_path / "notifications.db",
        channel_adapters={NotificationChannel.EMAIL: email},
    )

    assignment_id = adapter.send_work_item_assignment(
        work_item_id="work-1",
        assignee_id="analyst-1",
        task_type="document review",
        due_at="2026-11-06T12:00:00Z",
        channel=NotificationChannel.EMAIL,
    )
    reminder_id = adapter.send_reminder(
        work_item_id="work-1", recipient_id="analyst-1", overdue_hours=2
    )
    approval_id = adapter.send_approval_request(
        application_id="application-1",
        approver_id="manager-1",
        risk_score=0.42,
        decision_context="Synthetic case requires review",
    )
    decision_id = adapter.send_decision_notice(
        application_id="application-1",
        applicant_id="applicant-1",
        decision="approved",
        reason="Synthetic score within policy",
    )

    assert len({assignment_id, reminder_id, approval_id, decision_id}) == 4
    assert [call[0] for call in email.calls] == [
        "analyst-1",
        "analyst-1",
        "manager-1",
        "applicant-1",
    ]
    assert "work-1" in email.calls[0][2]
    assert "2" in email.calls[1][2]
    assert "application-1" in email.calls[2][2]
    assert "approved" in email.calls[3][2]
    assert adapter.send_work_item_assignment(
        work_item_id="work-1",
        assignee_id="analyst-1",
        task_type="document review",
        due_at="2026-11-06T12:00:00Z",
        channel=NotificationChannel.EMAIL,
    ) == assignment_id
    assert len(email.calls) == 4


def test_drift_alert_renders_feature_names_for_risk_committee(tmp_path):
    db_path = tmp_path / "notifications.db"
    email = RecordingEmail()
    adapter = NotificationAdapter(
        db_path=db_path,
        channel_adapters={NotificationChannel.EMAIL: email},
    )

    adapter.send_drift_alert(
        model_version="model-v2",
        drifted_features=["debt_ratio", "income"],
        psi_values={"debt_ratio": 0.4},
    )

    assert len(email.calls) == 1
    recipient, subject, content = email.calls[0]
    assert recipient == "risk-committee"
    assert "model-v2" in subject
    assert "debt_ratio, income" in content
    assert "'debt_ratio'" not in content
    with sqlite3.connect(db_path) as conn:
        stored = conn.execute("SELECT status, content FROM notifications").fetchone()
    assert stored == ("SENT", content)
