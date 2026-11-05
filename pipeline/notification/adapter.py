"""Notification dispatch adapter for CreditFlow.

Sends notifications over EMAIL, SMS and IN_APP channels with idempotent
delivery, status tracking (PENDING -> SENT -> DELIVERED | FAILED) and
exponential-backoff retry for failed dispatches.

Persistence lives in the CreditFlow ledger database
(``pipeline.storage.ledger``) in a ``notifications`` table. Message bodies are
rendered from the built-in ``notification_templates`` registry keyed by
``template_code``.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from string import Formatter
from typing import Any

from pydantic import BaseModel, Field

from pipeline.storage.ledger import _connect, _now_iso, _write_lock

logger = logging.getLogger(__name__)


class NotificationChannel(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"
    IN_APP = "IN_APP"


class NotificationStatus(str, Enum):
    PENDING = "PENDING"
    SENT = "SENT"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Notification(BaseModel):
    """Durable notification record."""

    notification_id: str
    recipient_id: str
    recipient_type: str
    channel: NotificationChannel
    subject: str
    content: str
    template_code: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)
    status: NotificationStatus = NotificationStatus.PENDING
    idempotency_key: str | None = None
    message_id: str | None = None
    attempts: int = 0
    next_retry_at: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    sent_at: str | None = None
    delivered_at: str | None = None


class NotificationResult(BaseModel):
    notification_id: str
    status: NotificationStatus
    message_id: str | None = None
    channel: NotificationChannel
    error_message: str | None = None


# ---------------------------------------------------------------------------
# notification_templates registry
# ---------------------------------------------------------------------------

_NOTIFICATION_TEMPLATES: dict[str, dict[str, str]] = {
    "work_item_assignment": {
        "subject": "Mới có nhiệm vụ được giao: {task_type}",
        "content": (
            "Xin chào {assignee_id},\n"
            "Nhiệm vụ {work_item_id} ({task_type}) đã được giao cho bạn.\n"
            "Hạn hoàn thành: {due_at}.\n"
        ),
    },
    "work_item_reminder": {
        "subject": "Nhiệm vụ quá hạn {overdue_hours} giờ: {work_item_id}",
        "content": (
            "Xin chào {recipient_id},\n"
            "Nhiệm vụ {work_item_id} đã quá hạn {overdue_hours} giờ và vẫn chưa hoàn thành.\n"
            "Vui lòng xử lý ngay.\n"
        ),
    },
    "approval_request": {
        "subject": "Yêu cầu phê duyệt đơn vay {application_id} (rủi ro {risk_score})",
        "content": (
            "Kính gửi {approver_id},\n"
            "Đơn vay {application_id} cần bạn phê duyệt.\n"
            "Điểm rủi ro: {risk_score}.\n"
            "Ngữ cảnh quyết định: {decision_context}\n"
        ),
    },
    "decision_notice": {
        "subject": "Kết quả xét duyệt đơn vay {application_id}: {decision}",
        "content": (
            "Kính gửi {applicant_id},\n"
            "Đơn vay {application_id} của bạn đã được xét duyệt: {decision}.\n"
            "Lý do: {reason}\n"
        ),
    },
    "drift_alert": {
        "subject": "Cảnh báo drift mô hình {model_version}",
        "content": (
            "Kính gửi Ủy ban Rủi ro,\n"
            "Mô hình {model_version} phát hiện drift trên các đặc trưng: {drifted_features}.\n"
            "Giá trị PSI: {psi_values}.\n"
            "Vui lòng xem xét tái huấn luyện.\n"
        ),
    },
}


def template_codes() -> list[str]:
    """Return every registered notification template code."""
    return sorted(_NOTIFICATION_TEMPLATES)


def render_template(template_code: str, variables: dict[str, Any]) -> tuple[str, str]:
    """Render (subject, content) from a registered template.

    Missing variables are left as-is (no KeyError) so a partial render still
    ships instead of crashing the dispatch path.
    """
    spec = _NOTIFICATION_TEMPLATES.get(template_code)
    if spec is None:
        raise ValueError(f"unknown notification template: {template_code!r}")
    return _safe_format(spec["subject"], variables), _safe_format(
        spec["content"], variables
    )


class _SafeFormatter(Formatter):
    def get_value(self, key: Any, args: tuple, kwargs: dict[str, Any]) -> Any:
        if isinstance(key, str):
            return kwargs.get(key, "{" + key + "}")
        return super().get_value(key, args, kwargs)


_FORMATTER = _SafeFormatter()


def _safe_format(template: str, variables: dict[str, Any]) -> str:
    return _FORMATTER.format(template, **{k: str(v) for k, v in variables.items()})


# ---------------------------------------------------------------------------
# Channel adapter base
# ---------------------------------------------------------------------------


class ChannelAdapter(ABC):
    """Abstract channel adapter — one concrete implementation per channel."""

    channel: NotificationChannel

    @abstractmethod
    def deliver(self, recipient: str, subject: str, content: str) -> str:
        """Deliver one message and return the provider message_id.

        Raises when the provider cannot accept the message so the caller can
        record FAILED and schedule a retry.
        """


# ---------------------------------------------------------------------------
# Env configuration helpers
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def notification_db_path() -> Path | None:
    env = os.environ.get("CREDITFLOW_NOTIFICATION_DB", "").strip()
    return (
        Path(env) if env else None
    )  # None => ledger default (data/creditflow_ledger.db)


def init_notification_db(db_path: Path | None = None) -> None:
    """Create the notifications table (idempotent) in the ledger database."""
    path = db_path or notification_db_path()
    with _connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id             TEXT PRIMARY KEY,
                recipient_id   TEXT NOT NULL,
                recipient_type TEXT NOT NULL,
                channel        TEXT NOT NULL,
                subject        TEXT NOT NULL,
                content        TEXT NOT NULL,
                template_code  TEXT,
                variables      TEXT NOT NULL DEFAULT '{}',
                status         TEXT NOT NULL CHECK (
                    status IN ('PENDING', 'SENT', 'DELIVERED', 'FAILED')
                ),
                idempotency_key TEXT,
                message_id     TEXT,
                attempts       INTEGER NOT NULL DEFAULT 0,
                next_retry_at  TEXT,
                error_message  TEXT,
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                sent_at        TEXT,
                delivered_at   TEXT,
                read_at        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_notifications_recipient
                ON notifications(recipient_id, channel);
            CREATE INDEX IF NOT EXISTS idx_notifications_status
                ON notifications(status);
            CREATE INDEX IF NOT EXISTS idx_notifications_idempotency
                ON notifications(idempotency_key) WHERE idempotency_key IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_notifications_next_retry
                ON notifications(next_retry_at) WHERE status = 'FAILED';
            """
        )


def _row_to_notification(row: sqlite3.Row) -> Notification:
    return Notification(
        notification_id=row["id"],
        recipient_id=row["recipient_id"],
        recipient_type=row["recipient_type"],
        channel=NotificationChannel(row["channel"]),
        subject=row["subject"],
        content=row["content"],
        template_code=row["template_code"],
        variables=json.loads(row["variables"] or "{}"),
        status=NotificationStatus(row["status"]),
        idempotency_key=row["idempotency_key"],
        message_id=row["message_id"],
        attempts=int(row["attempts"]),
        next_retry_at=row["next_retry_at"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        sent_at=row["sent_at"],
        delivered_at=row["delivered_at"],
    )


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------


class NotificationAdapter:
    """Central notification dispatch adapter.

    ``send`` creates a PENDING notification record, renders the message body
    (template or raw content), dispatches via the channel adapter and stores
    the terminal status. ``retry_failed`` re-dispatches FAILED notifications
    with exponential backoff.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        channel_adapters: dict[NotificationChannel, ChannelAdapter] | None = None,
    ) -> None:
        from .email_adapter import EmailNotificationAdapter
        from .in_app_adapter import InAppNotificationAdapter
        from .sms_adapter import SMSNotificationAdapter

        self.db_path = Path(db_path) if db_path is not None else notification_db_path()
        init_notification_db(self.db_path)
        self._lock = threading.RLock()
        self.email = EmailNotificationAdapter()
        self.sms = SMSNotificationAdapter()
        self.in_app = InAppNotificationAdapter(db_path=self.db_path)
        self._channel_adapters: dict[NotificationChannel, ChannelAdapter] = {
            NotificationChannel.EMAIL: self.email,
            NotificationChannel.SMS: self.sms,
            NotificationChannel.IN_APP: self.in_app,
        }
        if channel_adapters:
            self._channel_adapters.update(channel_adapters)

    # -- public API -------------------------------------------------------

    def send(
        self,
        recipient_id: str,
        recipient_type: str,
        channel: NotificationChannel | str,
        subject: str,
        content: str,
        template_code: str | None = None,
        variables: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Create and dispatch one notification.

        Returns the notification_id. A replay with the same idempotency_key
        returns the id of the original notification without re-dispatching.
        """
        channel = NotificationChannel(channel)
        variables = dict(variables or {})

        with self._lock:
            if idempotency_key:
                existing = self._find_by_idempotency_key(idempotency_key)
                if existing is not None:
                    return existing.notification_id

            if template_code:
                subject, content = render_template(template_code, variables)

            now = _now_iso()
            notification_id = f"not_{uuid.uuid4().hex}"
            self._insert(
                {
                    "id": notification_id,
                    "recipient_id": recipient_id,
                    "recipient_type": recipient_type,
                    "channel": channel.value,
                    "subject": subject,
                    "content": content,
                    "template_code": template_code,
                    "variables": json.dumps(variables, ensure_ascii=False),
                    "status": NotificationStatus.PENDING.value,
                    "idempotency_key": idempotency_key,
                    "message_id": None,
                    "attempts": 0,
                    "next_retry_at": None,
                    "error_message": None,
                    "created_at": now,
                    "updated_at": now,
                    "sent_at": None,
                    "delivered_at": None,
                    "read_at": None,
                }
            )

        status, message_id, error = self._deliver(
            channel,
            {
                "id": notification_id,
                "recipient_id": recipient_id,
                "recipient_type": recipient_type,
                "subject": subject,
                "content": content,
                "template_code": template_code,
                "variables": variables,
                "idempotency_key": idempotency_key,
            },
        )
        self._finalize(
            notification_id,
            status,
            message_id,
            error,
            failed_attempts=1 if status == NotificationStatus.FAILED else 0,
        )
        return notification_id

    def send_work_item_assignment(
        self,
        work_item_id: str,
        assignee_id: str,
        task_type: str,
        due_at: str,
        channel: NotificationChannel | str = NotificationChannel.IN_APP,
        recipient_id: str | None = None,
        recipient_type: str = "user",
    ) -> str:
        """Send a work item assignment notification."""
        recipient = recipient_id or assignee_id
        variables = {
            "work_item_id": work_item_id,
            "assignee_id": assignee_id,
            "task_type": task_type,
            "due_at": due_at,
        }
        subject, content = render_template("work_item_assignment", variables)
        return self.send(
            recipient_id=recipient,
            recipient_type=recipient_type,
            channel=channel,
            subject=subject,
            content=content,
            template_code="work_item_assignment",
            variables=variables,
            idempotency_key=f"work-item-assignment:{work_item_id}:{assignee_id}",
        )

    def send_reminder(
        self,
        work_item_id: str,
        recipient_id: str,
        overdue_hours: float,
        channel: NotificationChannel | str = NotificationChannel.EMAIL,
        recipient_type: str = "user",
    ) -> str:
        """Send an overdue reminder for a work item."""
        variables = {
            "work_item_id": work_item_id,
            "recipient_id": recipient_id,
            "overdue_hours": overdue_hours,
        }
        subject, content = render_template("work_item_reminder", variables)
        return self.send(
            recipient_id=recipient_id,
            recipient_type=recipient_type,
            channel=channel,
            subject=subject,
            content=content,
            template_code="work_item_reminder",
            variables=variables,
            idempotency_key=f"work-item-reminder:{work_item_id}:{recipient_id}",
        )

    def send_approval_request(
        self,
        application_id: str,
        approver_id: str,
        risk_score: float,
        decision_context: str,
        channel: NotificationChannel | str = NotificationChannel.EMAIL,
        recipient_type: str = "manager",
    ) -> str:
        """Send an approval request to a manager."""
        variables = {
            "application_id": application_id,
            "approver_id": approver_id,
            "risk_score": risk_score,
            "decision_context": decision_context,
        }
        subject, content = render_template("approval_request", variables)
        return self.send(
            recipient_id=approver_id,
            recipient_type=recipient_type,
            channel=channel,
            subject=subject,
            content=content,
            template_code="approval_request",
            variables=variables,
            idempotency_key=f"approval-request:{application_id}:{approver_id}",
        )

    def send_decision_notice(
        self,
        application_id: str,
        applicant_id: str,
        decision: str,
        reason: str,
        channel: NotificationChannel | str = NotificationChannel.EMAIL,
        recipient_type: str = "applicant",
    ) -> str:
        """Send a decision notice to an applicant."""
        variables = {
            "application_id": application_id,
            "applicant_id": applicant_id,
            "decision": decision,
            "reason": reason,
        }
        subject, content = render_template("decision_notice", variables)
        return self.send(
            recipient_id=applicant_id,
            recipient_type=recipient_type,
            channel=channel,
            subject=subject,
            content=content,
            template_code="decision_notice",
            variables=variables,
            idempotency_key=f"decision-notice:{application_id}:{applicant_id}",
        )

    def send_drift_alert(
        self,
        model_version: str,
        drifted_features: list[str],
        psi_values: dict[str, float],
        channel: NotificationChannel | str = NotificationChannel.EMAIL,
        recipient_id: str = "risk-committee",
        recipient_type: str = "committee",
    ) -> str:
        """Send a model drift alert to the risk committee."""
        variables = {
            "model_version": model_version,
            "drifted_features": ", ".join(drifted_features),
            "psi_values": json.dumps(psi_values, ensure_ascii=False),
        }
        subject, content = render_template("drift_alert", variables)
        return self.send(
            recipient_id=recipient_id,
            recipient_type=recipient_type,
            channel=channel,
            subject=subject,
            content=content,
            template_code="drift_alert",
            variables=variables,
            idempotency_key=f"drift-alert:{model_version}",
        )

    def get_status(self, notification_id: str) -> dict[str, Any] | None:
        """Return the delivery status record for a notification_id."""
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM notifications WHERE id = ?", (notification_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "notification_id": row["id"],
            "status": row["status"],
            "channel": row["channel"],
            "message_id": row["message_id"],
            "attempts": int(row["attempts"]),
            "error_message": row["error_message"],
            "created_at": row["created_at"],
            "sent_at": row["sent_at"],
            "delivered_at": row["delivered_at"],
            "next_retry_at": row["next_retry_at"],
        }

    def retry_failed(self) -> int:
        """Retry FAILED notifications due for exponential-backoff retry.

        Returns the number of notifications re-dispatched.
        """
        now = _now_iso()
        base = _env_float("NOTIFICATION_RETRY_BASE_SECONDS", 60.0)
        max_attempts = _env_int("NOTIFICATION_MAX_RETRY_ATTEMPTS", 5)
        max_seconds = _env_float("NOTIFICATION_RETRY_MAX_SECONDS", 86400.0)

        with self._lock, _connect(self.db_path) as conn:
            due = conn.execute(
                """
                SELECT id, recipient_id, recipient_type, channel, subject, content,
                       template_code, variables, idempotency_key, attempts
                FROM notifications
                WHERE status = ? AND attempts < ? AND next_retry_at IS NOT NULL
                  AND next_retry_at <= ?
                ORDER BY next_retry_at ASC
                """,
                (NotificationStatus.FAILED.value, max_attempts, now),
            ).fetchall()

        retried = 0
        for row in due:
            notification_id = row["id"]
            channel = NotificationChannel(row["channel"])
            variables = json.loads(row["variables"] or "{}")
            attempts = int(row["attempts"])
            delay = min(base * (2**attempts), max_seconds)
            next_retry = (
                datetime.now(UTC) + timedelta(seconds=delay)
            ).isoformat()

            try:
                status, message_id, error = self._deliver(
                    channel,
                    {
                        "id": notification_id,
                        "recipient_id": row["recipient_id"],
                        "recipient_type": row["recipient_type"],
                        "subject": row["subject"],
                        "content": row["content"],
                        "template_code": row["template_code"],
                        "variables": variables,
                        "idempotency_key": row["idempotency_key"],
                    },
                )
                if status == NotificationStatus.SENT:
                    self._finalize(
                        notification_id, status, message_id, None, failed_attempts=0
                    )
                    retried += 1
                    continue
                attempts += 1
                self._retry_failure(
                    notification_id,
                    attempts,
                    next_retry,
                    error or "delivery returned non-terminal status",
                )
                if attempts < max_attempts:
                    retried += 1
            except Exception as exc:  # noqa: BLE001 — delivery faults are handled here
                attempts += 1
                self._retry_failure(notification_id, attempts, next_retry, str(exc))
                if attempts < max_attempts:
                    retried += 1
        return retried

    # -- internals --------------------------------------------------------

    def _deliver(
        self, channel: NotificationChannel, row: dict[str, Any]
    ) -> tuple[NotificationStatus, str | None, str | None]:
        adapter = self._channel_adapters.get(channel)
        if adapter is None:
            return (
                NotificationStatus.FAILED,
                None,
                f"no adapter for channel {channel.value}",
            )
        try:
            message_id = adapter.deliver(
                recipient=row["recipient_id"],
                subject=row["subject"],
                content=row["content"],
            )
            return NotificationStatus.SENT, message_id, None
        except Exception as exc:  # noqa: BLE001
            return NotificationStatus.FAILED, None, str(exc)

    def _finalize(
        self,
        notification_id: str,
        status: NotificationStatus,
        message_id: str | None,
        error_message: str | None,
        failed_attempts: int = 0,
    ) -> None:
        now = _now_iso()
        updates: list[str] = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status.value, now]
        if message_id is not None:
            updates.append("message_id = ?")
            params.append(message_id)
        if status == NotificationStatus.SENT:
            updates.append("sent_at = ?")
            params.append(now)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if status == NotificationStatus.FAILED and failed_attempts:
            base = _env_float("NOTIFICATION_RETRY_BASE_SECONDS", 60.0)
            max_seconds = _env_float("NOTIFICATION_RETRY_MAX_SECONDS", 86400.0)
            delay = min(base * (2**failed_attempts), max_seconds)
            updates.append("next_retry_at = ?")
            params.append(
                (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()
            )
        if failed_attempts:
            updates.append("attempts = ?")
            params.append(failed_attempts)
        params.append(notification_id)
        with _write_lock, _connect(self.db_path) as conn:
            conn.execute(
                f"UPDATE notifications SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            conn.commit()

    def _retry_failure(
        self,
        notification_id: str,
        attempts: int,
        next_retry_at: str,
        error_message: str,
    ) -> None:
        max_attempts = _env_int("NOTIFICATION_MAX_RETRY_ATTEMPTS", 5)
        with _write_lock, _connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE notifications
                SET status = ?, attempts = ?, next_retry_at = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    NotificationStatus.FAILED.value,
                    attempts,
                    next_retry_at,
                    error_message,
                    _now_iso(),
                    notification_id,
                ),
            )
            conn.commit()
        if attempts >= max_attempts:
            logger.warning(
                "notification %s exhausted %d retries; last error: %s",
                notification_id,
                attempts,
                error_message,
            )

    def _insert(self, row: dict[str, Any]) -> None:
        with _write_lock, _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notifications
                    (id, recipient_id, recipient_type, channel, subject, content,
                     template_code, variables, status, idempotency_key, message_id,
                     attempts, next_retry_at, error_message, created_at, updated_at,
                     sent_at, delivered_at, read_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["recipient_id"],
                    row["recipient_type"],
                    row["channel"],
                    row["subject"],
                    row["content"],
                    row["template_code"],
                    row["variables"],
                    row["status"],
                    row["idempotency_key"],
                    row["message_id"],
                    row["attempts"],
                    row["next_retry_at"],
                    row["error_message"],
                    row["created_at"],
                    row["updated_at"],
                    row["sent_at"],
                    row["delivered_at"],
                    row["read_at"],
                ),
            )
            conn.commit()

    def _find_by_idempotency_key(self, idempotency_key: str) -> Notification | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM notifications WHERE idempotency_key = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (idempotency_key,),
            ).fetchone()
        return _row_to_notification(row) if row is not None else None


def __getattr__(name: str):
    """Expose concrete adapters lazily so direct imports cannot recurse."""
    if name == "EmailNotificationAdapter":
        from .email_adapter import EmailNotificationAdapter
        return EmailNotificationAdapter
    if name == "InAppNotificationAdapter":
        from .in_app_adapter import InAppNotificationAdapter
        return InAppNotificationAdapter
    if name == "SMSNotificationAdapter":
        from .sms_adapter import SMSNotificationAdapter
        return SMSNotificationAdapter
    raise AttributeError(name)
