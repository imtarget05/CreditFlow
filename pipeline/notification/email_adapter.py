"""SMTP email notification adapter with a demo-mode logging fallback."""

from __future__ import annotations

import logging
import os
import re
import smtplib
import uuid
from email.message import EmailMessage

from backend.security import is_production
from pipeline.notification.adapter import ChannelAdapter, NotificationChannel

logger = logging.getLogger(__name__)


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


class EmailNotificationAdapter(ChannelAdapter):
    """Delivers notifications by email via SMTP.

    Configuration (all optional — SMTP absent means demo mode):
      SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
      SMTP_USE_TLS (default "true"), SMTP_FROM_EMAIL, SMTP_FROM_NAME

    When SMTP is not configured the message is written to the structured log
    and a synthetic message_id is returned (demo mode).
    """

    channel = NotificationChannel.EMAIL

    def __init__(self) -> None:
        self.host = os.environ.get("SMTP_HOST", "").strip()
        self.port = _env_int("SMTP_PORT", 587)
        self.user = os.environ.get("SMTP_USER", "").strip()
        self.password = os.environ.get("SMTP_PASSWORD", "").strip()
        self.use_tls = _env_bool("SMTP_USE_TLS", True)
        self.from_email = os.environ.get(
            "SMTP_FROM_EMAIL", "noreply@creditflow.local"
        ).strip()
        self.from_name = os.environ.get("SMTP_FROM_NAME", "CreditFlow").strip()

    def _is_configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    def send_email(
        self,
        to: str,
        subject: str,
        html_content: str,
        text_content: str | None = None,
    ) -> str:
        """Send one email and return the provider message_id.

        Falls back to logging (demo mode) when SMTP is not configured.
        """
        message_id = f"msg_{uuid.uuid4().hex}"
        if not self._is_configured():
            if is_production():
                raise RuntimeError("EMAIL_CHANNEL_NOT_CONFIGURED")
            logger.info("[demo] email delivery skipped message_id=%s", message_id)
            return message_id
        if is_production() and not self.use_tls:
            raise RuntimeError("EMAIL_TLS_REQUIRED")

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{self.from_name} <{self.from_email}>"
        msg["To"] = to
        msg["Message-ID"] = f"<{message_id}@creditflow.local>"
        msg.set_content(text_content or _html_to_text(html_content))
        msg.add_alternative(html_content, subtype="html")

        with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
            if self.use_tls:
                smtp.starttls()
            if self.user and self.password:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)
        return message_id

    # ChannelAdapter interface
    def deliver(self, recipient: str, subject: str, content: str) -> str:
        return self.send_email(
            to=recipient, subject=subject, html_content=content, text_content=None
        )


def _html_to_text(html: str) -> str:
    """Crude dependency-free HTML -> text fallback for the plain-text part."""
    text = re.sub(r"<[^>]+>", "\n", html)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
