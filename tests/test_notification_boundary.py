"""Notification adapters never leak message bodies or fake production delivery."""

import logging

import pytest

from pipeline.notification.adapter import NotificationAdapter
from pipeline.notification.email_adapter import EmailNotificationAdapter
from pipeline.notification.sms_adapter import SMSNotificationAdapter


def test_demo_fallback_logs_no_message_content(monkeypatch, caplog):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SMS_GATEWAY", "log")
    with caplog.at_level(logging.INFO):
        EmailNotificationAdapter().send_email("staff@example.test", "Private case", "secret-applicant-data")
        SMSNotificationAdapter().send_sms("+84000000000", "secret-applicant-data")
    assert "secret-applicant-data" not in caplog.text
    assert "staff@example.test" not in caplog.text
    assert "+84000000000" not in caplog.text


def test_production_rejects_unconfigured_notification_channels(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SMS_GATEWAY", "log")
    with pytest.raises(RuntimeError, match="EMAIL_CHANNEL_NOT_CONFIGURED"):
        EmailNotificationAdapter().send_email("staff@example.test", "Case", "Body")
    with pytest.raises(RuntimeError, match="SMS_CHANNEL_NOT_CONFIGURED"):
        SMSNotificationAdapter().send_sms("+84000000000", "Body")


def test_production_refuses_plaintext_smtp(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "sender")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    with pytest.raises(RuntimeError, match="EMAIL_TLS_REQUIRED"):
        EmailNotificationAdapter().send_email("staff@example.test", "Case", "Body")


def test_notification_adapter_initializes_without_circular_import(tmp_path):
    adapter = NotificationAdapter(db_path=tmp_path / "notifications.db")
    assert adapter.in_app is not None
