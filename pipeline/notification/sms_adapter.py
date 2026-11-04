"""SMS notification adapter with pluggable gateway and demo-mode logging fallback."""

from __future__ import annotations

import json
import logging
import os
import uuid

from backend.security import is_production
from pipeline.notification.adapter import ChannelAdapter, NotificationChannel

logger = logging.getLogger(__name__)


class SMSNotificationAdapter(ChannelAdapter):
    """Delivers notifications by SMS through a configurable gateway.

    Supported gateways (SMS_GATEWAY): twilio, vnpt, log (demo default).
    Env vars:
      SMS_GATEWAY
      TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER
      VNPT_API_KEY, VNPT_API_URL, VNPT_SENDER_ID

    When no gateway is configured the message is written to the structured
    log and a synthetic message_id is returned (demo mode).
    """

    channel = NotificationChannel.SMS

    def __init__(self) -> None:
        self.gateway = os.environ.get("SMS_GATEWAY", "log").strip().lower()
        self.twilio_account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
        self.twilio_auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
        self.twilio_from = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
        self.vnpt_api_key = os.environ.get("VNPT_API_KEY", "").strip()
        self.vnpt_api_url = os.environ.get("VNPT_API_URL", "").strip()
        self.vnpt_sender = os.environ.get("VNPT_SENDER_ID", "").strip()

    def _is_configured(self) -> bool:
        return self.gateway not in {"log", "demo", ""}

    def send_sms(self, to: str, message: str) -> str:
        """Send one SMS and return the provider message_id.

        Falls back to logging (demo mode) when no gateway is configured.
        """
        message_id = f"sms_{uuid.uuid4().hex}"
        if self.gateway == "twilio":
            return self._send_twilio(to, message, message_id)
        if self.gateway == "vnpt":
            return self._send_vnpt(to, message, message_id)
        if is_production():
            raise RuntimeError("SMS_CHANNEL_NOT_CONFIGURED")
        logger.info("[demo] sms delivery skipped message_id=%s", message_id)
        return message_id

    def _send_twilio(self, to: str, message: str, message_id: str) -> str:
        try:
            from twilio.rest import Client  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("twilio package is not installed") from exc
        client = Client(self.twilio_account_sid, self.twilio_auth_token)
        msg = client.messages.create(body=message, from_=self.twilio_from, to=to)
        return str(msg.sid or message_id)

    def _send_vnpt(self, to: str, message: str, message_id: str) -> str:
        import urllib.request

        if is_production() and not self.vnpt_api_url.startswith("https://"):
            raise RuntimeError("SMS_HTTPS_REQUIRED")

        payload = json.dumps(
            {
                "apiKey": self.vnpt_api_key,
                "sender": self.vnpt_sender,
                "to": to,
                "message": message,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            self.vnpt_api_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}
        return str(data.get("messageId") or data.get("message_id") or message_id)

    # ChannelAdapter interface
    def deliver(self, recipient: str, subject: str, content: str) -> str:
        return self.send_sms(to=recipient, message=f"{subject}\n{content}")
