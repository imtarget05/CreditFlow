"""In-app notification adapter backed by the notifications table."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

from pipeline.notification.adapter import ChannelAdapter, NotificationChannel
from pipeline.storage.ledger import _connect, _now_iso, _write_lock

logger = logging.getLogger(__name__)


class InAppNotificationAdapter(ChannelAdapter):
    """Stores in-app notifications in the ``notifications`` table."""

    channel = NotificationChannel.IN_APP

    def __init__(self, db_path: Any | None = None) -> None:
        self.db_path = db_path

    def deliver(self, recipient: str, subject: str, content: str) -> str:
        """Persist one in-app notification as SENT and return its id."""
        notification_id = f"not_{uuid.uuid4().hex}"
        now = _now_iso()
        with _write_lock, _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO notifications
                    (id, recipient_id, recipient_type, channel, subject, content,
                     template_code, variables, status, idempotency_key, message_id,
                     attempts, next_retry_at, error_message, created_at, updated_at,
                     sent_at, delivered_at, read_at)
                VALUES (?, ?, ?, 'IN_APP', ?, ?, NULL, '{}', 'SENT', NULL, ?, 0, NULL, NULL, ?, ?, ?, NULL, NULL)
                """,
                (
                    notification_id,
                    recipient,
                    "user",
                    subject,
                    content,
                    notification_id,
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
        return notification_id

    def get_user_notifications(
        self, user_id: str, unread_only: bool = False
    ) -> list[dict[str, Any]]:
        """Return in-app notifications for a user, newest first."""
        with _connect(self.db_path) as conn:
            if unread_only:
                rows = conn.execute(
                    """
                    SELECT * FROM notifications
                    WHERE recipient_id = ? AND channel = 'IN_APP' AND read_at IS NULL
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM notifications
                    WHERE recipient_id = ? AND channel = 'IN_APP'
                    ORDER BY created_at DESC
                    """,
                    (user_id,),
                ).fetchall()
        return [_row_to_dict(r) for r in rows]

    def mark_as_read(self, notification_id: str, user_id: str) -> bool:
        """Mark one in-app notification as read for a user.

        Returns True when a row was updated.
        """
        now = _now_iso()
        with _write_lock, _connect(self.db_path) as conn:
            cur = conn.execute(
                """
                UPDATE notifications
                SET read_at = ?, updated_at = ?
                WHERE id = ? AND recipient_id = ? AND channel = 'IN_APP' AND read_at IS NULL
                """,
                (now, now, notification_id, user_id),
            )
            conn.commit()
        return cur.rowcount > 0


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if isinstance(data.get("variables"), str):
        try:
            data["variables"] = json.loads(data["variables"] or "{}")
        except (TypeError, json.JSONDecodeError):
            data["variables"] = {}
    return data
