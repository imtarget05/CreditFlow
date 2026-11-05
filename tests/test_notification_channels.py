"""Channel adapters preserve recipient boundaries and fail closed in production."""

import json

import pytest

from pipeline.notification.adapter import init_notification_db
from pipeline.notification.email_adapter import EmailNotificationAdapter
from pipeline.notification.in_app_adapter import InAppNotificationAdapter
from pipeline.notification.sms_adapter import SMSNotificationAdapter


def test_in_app_notifications_are_recipient_scoped_and_read_once(tmp_path):
    path = tmp_path / "ledger.db"
    init_notification_db(path)
    adapter = InAppNotificationAdapter(db_path=path)
    notification_id = adapter.deliver("reviewer-a", "Case ready", "Synthetic case A")
    adapter.deliver("reviewer-b", "Other case", "Synthetic case B")

    unread = adapter.get_user_notifications("reviewer-a", unread_only=True)
    assert len(unread) == 1
    assert unread[0]["id"] == notification_id
    assert unread[0]["content"] == "Synthetic case A"
    assert adapter.get_user_notifications("reviewer-b")[0]["recipient_id"] == "reviewer-b"
    assert adapter.mark_as_read(notification_id, "reviewer-b") is False
    assert adapter.mark_as_read(notification_id, "reviewer-a") is True
    assert adapter.mark_as_read(notification_id, "reviewer-a") is False
    assert adapter.get_user_notifications("reviewer-a", unread_only=True) == []


def test_email_uses_tls_and_never_sends_when_production_tls_disabled(monkeypatch):
    import smtplib

    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append((host, port, timeout))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self):
            calls.append("tls")

        def login(self, user, password):
            calls.append("login")

        def send_message(self, message):
            calls.append((message["To"], message["Subject"], message.get_content_type()))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "test-user")
    monkeypatch.setenv("SMTP_PASSWORD", "test-password")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    message_id = EmailNotificationAdapter().send_email("reviewer@example.test", "Case", "<b>Review</b>")
    assert message_id.startswith("msg_")
    assert calls == [("smtp.example.test", 587, 30), "tls", "login", ("reviewer@example.test", "Case", "multipart/alternative")]

    calls.clear()
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    with pytest.raises(RuntimeError, match="EMAIL_TLS_REQUIRED"):
        EmailNotificationAdapter().send_email("reviewer@example.test", "Case", "<b>Review</b>")
    assert calls == []


def test_vnpt_sms_requires_https_in_production_and_reads_message_id(monkeypatch):
    import urllib.request

    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"messageId":"provider-42"}'

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setenv("SMS_GATEWAY", "vnpt")
    monkeypatch.setenv("VNPT_API_URL", "http://insecure.example.test")
    with pytest.raises(RuntimeError, match="SMS_HTTPS_REQUIRED"):
        SMSNotificationAdapter().send_sms("+84000000000", "Synthetic case")
    assert calls == []

    monkeypatch.setenv("VNPT_API_URL", "https://sms.example.test/send")
    monkeypatch.setenv("VNPT_API_KEY", "test-key")
    monkeypatch.setenv("VNPT_SENDER_ID", "CF")
    assert SMSNotificationAdapter().send_sms("+84000000000", "Synthetic case") == "provider-42"
    assert calls[0][1]["to"] == "+84000000000"
    assert calls[0][1]["message"] == "Synthetic case"
