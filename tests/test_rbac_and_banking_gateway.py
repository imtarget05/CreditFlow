"""Tests for RBAC per-user authentication and Core Banking Sandbox Gateway."""

import time
import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.security import (
    Principal,
    authenticate_user,
    create_access_token,
    decode_access_token,
    ensure_authority,
    require_principal,
)
from pipeline.gateways.banking_sandbox import CoreBankingSandboxGateway


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. User Directory & Password Verification
# ---------------------------------------------------------------------------

def test_authenticate_user_valid_credentials():
    u1 = authenticate_user("underwriter1", "Underwriter@123")
    assert u1 is not None
    assert u1["username"] == "underwriter1"
    assert u1["role"] == "UNDERWRITER_L1"

    u2 = authenticate_user("risk_lead", "RiskLead@123")
    assert u2 is not None
    assert u2["username"] == "risk_lead"
    assert u2["role"] == "RISK_COMMITTEE_L2"


def test_authenticate_user_invalid_credentials():
    assert authenticate_user("underwriter1", "WrongPassword") is None
    assert authenticate_user("nonexistent_user", "Pass123") is None


# ---------------------------------------------------------------------------
# 2. JWT Generation & Verification
# ---------------------------------------------------------------------------

def test_jwt_token_roundtrip():
    token = create_access_token("test_analyst", "UNDERWRITER_L1", expires_in=300)
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "test_analyst"
    assert payload["role"] == "UNDERWRITER_L1"


def test_jwt_token_tampered():
    token = create_access_token("test_analyst", "UNDERWRITER_L1")
    tampered = token[:-4] + "fake"
    assert decode_access_token(tampered) is None


def test_jwt_token_expired():
    token = create_access_token("test_analyst", "UNDERWRITER_L1", expires_in=-10)
    assert decode_access_token(token) is None


# ---------------------------------------------------------------------------
# 3. /api/auth/login Endpoint
# ---------------------------------------------------------------------------

def test_login_endpoint_success(client):
    res = client.post("/api/auth/login", json={"username": "underwriter1", "password": "Underwriter@123"})
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["role"] == "UNDERWRITER_L1"


def test_login_endpoint_failure(client):
    res = client.post("/api/auth/login", json={"username": "underwriter1", "password": "WrongPassword"})
    assert res.status_code == 401
    assert "INVALID_CREDENTIALS" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 4. Bearer Token vs API Key in Principal Resolution
# ---------------------------------------------------------------------------

def test_require_principal_with_bearer_token():
    token = create_access_token("analyst_jane", "UNDERWRITER_L1")
    class DummyCreds:
        credentials = token

    principal = require_principal(api_key=None, bearer_creds=DummyCreds())
    assert principal.identity == "analyst_jane"
    assert principal.role == "UNDERWRITER_L1"
    assert principal.level == 1


def test_require_principal_with_api_key_backward_compatible(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_API_KEY", "test-secret-shared-key-123")
    monkeypatch.setenv("CREDITFLOW_API_KEY_ID", "legacy_batch_agent")
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "UNDERWRITER_L1")

    principal = require_principal(api_key="test-secret-shared-key-123", bearer_creds=None)
    assert principal.identity == "legacy_batch_agent"
    assert principal.role == "UNDERWRITER_L1"


# ---------------------------------------------------------------------------
# 5. RBAC Authority Enforcement
# ---------------------------------------------------------------------------

def test_ensure_authority_enforces_hierarchy():
    l1 = Principal(identity="underwriter1", role="UNDERWRITER_L1")
    l2 = Principal(identity="risk_lead", role="RISK_COMMITTEE_L2")

    # L1 can approve L1 tasks
    ensure_authority(l1, "UNDERWRITER_L1")

    # L2 can approve L1 and L2 tasks
    ensure_authority(l2, "UNDERWRITER_L1")
    ensure_authority(l2, "RISK_COMMITTEE_L2")

    # L1 cannot approve L2 tasks
    with pytest.raises(Exception) as excinfo:
        ensure_authority(l1, "RISK_COMMITTEE_L2")
    assert "INSUFFICIENT_AUTHORITY" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 6. Core Banking Sandbox Gateway
# ---------------------------------------------------------------------------

def test_banking_sandbox_transfer_and_signature():
    gw = CoreBankingSandboxGateway()
    order = gw.create_disbursement_order(
        application_id=5001,
        contract_code="HDTD-20261009-5001",
        loan_amount=45000000.0,
        beneficiary_name="LE VAN C",
    )
    result = gw.execute_transfer(order)
    assert result["settled"] is True
    assert result["disbursement"]["status"] == "SETTLED"
    assert result["disbursement"]["loan_amount"] == 45000000.0
    assert "vietqr://" in result["qr_payload"]

    # Verify cryptographic signature
    assert gw.verify_signature(result["disbursement"], result["signature"]) is True

    # Tampered payload fails verification
    tampered_payload = dict(result["disbursement"])
    tampered_payload["loan_amount"] = 99999999.0
    assert gw.verify_signature(tampered_payload, result["signature"]) is False
