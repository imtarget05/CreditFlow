"""Tests for the mock Sacombank OIDC IdP (Gate 1 — demo data demo).

Starts the mock IdP as a subprocess, configures the backend OIDC contract
against it, issues RS256-signed tokens, and verifies ``validate_oidc_token``
accepts them and maps groups to bank roles.

These are synthetic demo tests: the mock IdP is not Sacombank's real IdP,
and the tokens are not real Sacombank tokens.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.security import validate_oidc_token
from scripts.mock_oidc_idp import app as mock_idp_app


def test_mock_idp_rejects_cross_origin_token_request():
    client = TestClient(mock_idp_app)
    response = client.post("/token", data={"grant_type": "client_credentials"}, headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403
    assert response.json() == {"error": "cross_origin_request"}

MOCK_IDP_PORT = 9098
MOCK_IDP_URL = f"http://localhost:{MOCK_IDP_PORT}"

ROLE_MAP = {
    "sacombank-underwriter": "UNDERWRITER_L1",
    "sacombank-risk-committee": "RISK_COMMITTEE_L2",
    "sacombank-admin": "ADMIN",
}


@pytest.fixture(scope="module")
def mock_idp():
    """Start the mock OIDC IdP as a subprocess for the test module."""
    env = os.environ.copy()
    env["MOCK_OIDC_ISSUER"] = MOCK_IDP_URL
    env["MOCK_OIDC_AUDIENCE"] = "creditflow"
    env["MOCK_OIDC_PORT"] = str(MOCK_IDP_PORT)
    proc = subprocess.Popen(
        [sys.executable, "scripts/mock_oidc_idp.py"],
        env=env,
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for the IdP to be ready
    for _ in range(50):
        try:
            r = requests.get(f"{MOCK_IDP_URL}/.well-known/openid-configuration", timeout=1)
            if r.status_code == 200:
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError(f"Mock OIDC IdP did not start on {MOCK_IDP_URL}")
    yield MOCK_IDP_URL
    proc.terminate()
    proc.wait(timeout=5)


def _issue_token(idp_url: str, groups: list[str], sub: str = "demo-user") -> str:
    """Issue a token from the mock IdP."""
    r = requests.post(
        f"{idp_url}/token",
        data={
            "grant_type": "client_credentials",
            "groups": ",".join(groups),
            "sub": sub,
        },
        timeout=5,
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(autouse=True)
def _oidc_env(monkeypatch, mock_idp):
    """Configure backend OIDC env to point at the mock IdP."""
    monkeypatch.setenv("CREDITFLOW_OIDC_ISSUER", mock_idp)
    monkeypatch.setenv("CREDITFLOW_OIDC_AUDIENCE", "creditflow")
    monkeypatch.setenv("CREDITFLOW_OIDC_JWKS_URL", f"{mock_idp}/keys")
    monkeypatch.setenv("CREDITFLOW_OIDC_ROLE_MAP_JSON", json.dumps(ROLE_MAP))


def test_oidc_token_underwriter(mock_idp):
    """An underwriter group token validates to UNDERWRITER_L1 principal."""
    token = _issue_token(mock_idp, ["sacombank-underwriter"], "analyst-001")
    principal = validate_oidc_token(token)
    assert principal.identity == "analyst-001"
    assert principal.role == "UNDERWRITER_L1"


def test_oidc_token_risk_committee(mock_idp):
    """A risk committee group token validates to RISK_COMMITTEE_L2 principal."""
    token = _issue_token(mock_idp, ["sacombank-risk-committee"], "risk-001")
    principal = validate_oidc_token(token)
    assert principal.identity == "risk-001"
    assert principal.role == "RISK_COMMITTEE_L2"


def test_oidc_token_admin(mock_idp):
    """An admin group token validates to ADMIN principal."""
    token = _issue_token(mock_idp, ["sacombank-admin"], "admin-001")
    principal = validate_oidc_token(token)
    assert principal.identity == "admin-001"
    assert principal.role == "ADMIN"


def test_oidc_token_multiple_groups_picks_highest(mock_idp):
    """Multiple groups resolve to the highest-authority role."""
    token = _issue_token(
        mock_idp, ["sacombank-underwriter", "sacombank-admin"], "multi-001"
    )
    principal = validate_oidc_token(token)
    assert principal.role == "ADMIN"


def test_oidc_token_unmapped_group_rejected(mock_idp):
    """A group not in the role map is rejected."""
    token = _issue_token(mock_idp, ["unknown-group"], "unknown-001")
    with pytest.raises(Exception) as exc:
        validate_oidc_token(token)
    assert exc.value.detail == "OIDC_ROLE_NOT_MAPPED"


def test_oidc_token_invalid_rejected(mock_idp):
    """A malformed token is rejected."""
    with pytest.raises(Exception) as exc:
        validate_oidc_token("not-a-valid-token")
    assert exc.value.detail == "INVALID_OIDC_TOKEN"


def test_oidc_discovery_document(mock_idp):
    """The mock IdP serves a valid OIDC discovery document."""
    r = requests.get(f"{mock_idp}/.well-known/openid-configuration", timeout=5)
    assert r.status_code == 200
    doc = r.json()
    assert doc["issuer"] == mock_idp
    assert doc["jwks_uri"] == f"{mock_idp}/keys"
    assert "RS256" in doc["id_token_signing_alg_values_supported"]


def test_oidc_jwks(mock_idp):
    """The mock IdP serves a valid JWKS with an RSA key."""
    r = requests.get(f"{mock_idp}/keys", timeout=5)
    assert r.status_code == 200
    jwks = r.json()
    assert len(jwks["keys"]) == 1
    assert jwks["keys"][0]["kty"] == "RSA"
    assert jwks["keys"][0]["alg"] == "RS256"
