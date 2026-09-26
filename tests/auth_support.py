"""Test-only credentials for the API-key protected endpoints.

The real shared secret is read from ``CREDITFLOW_API_KEY`` in the deployment
environment and is never committed; the values below exist only inside the
test suite so a test can authenticate the way a real caller does.
"""
from __future__ import annotations

API_KEY = "test-only-key-3f9c1d2e8b6a4f50"
API_KEY_HEADER = "X-CreditFlow-API-Key"
AUTH_HEADERS = {API_KEY_HEADER: API_KEY}
# Identity the key is bound to; the API records it as ``approver_id``.
APPROVER_ID = "test_approver_key"
# The 120M VND profiles used by the workflow tests route to the Risk
# Committee level of the authority matrix, so the test key holds L2.
AUTHORITY_ROLE = "RISK_COMMITTEE_L2"


def configure_auth_env(monkeypatch) -> None:
    """Point the auth layer at the test key for the duration of a test."""
    monkeypatch.setenv("CREDITFLOW_API_KEY", API_KEY)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ID", APPROVER_ID)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", AUTHORITY_ROLE)