"""OIDC bearer validation is the only human identity path in production."""

import json
import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend.security import _jwks_client, require_principal, validate_oidc_token


@pytest.fixture
def configured_oidc(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setenv("CREDITFLOW_OIDC_ISSUER", "https://idp.bank.internal")
    monkeypatch.setenv("CREDITFLOW_OIDC_AUDIENCE", "creditflow")
    monkeypatch.setenv("CREDITFLOW_OIDC_JWKS_URL", "https://idp.bank.internal/keys")
    monkeypatch.setenv("CREDITFLOW_OIDC_ROLE_MAP_JSON", json.dumps({"credit-reviewers": "UNDERWRITER_L1"}))

    class FakeJwksClient:
        def __init__(self, url):
            assert url == "https://idp.bank.internal/keys"

        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=key.public_key())

    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwksClient)
    _jwks_client.cache_clear()
    return key


def _token(key, **overrides):
    claims = {"sub": "staff-123", "iss": "https://idp.bank.internal", "aud": "creditflow", "exp": int(time.time()) + 300, "iat": int(time.time()), "groups": ["credit-reviewers"]}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})


def test_valid_oidc_token_maps_group_to_role(configured_oidc):
    principal = validate_oidc_token(_token(configured_oidc))
    assert (principal.identity, principal.role) == ("staff-123", "UNDERWRITER_L1")


def test_invalid_issuer_and_unmapped_group_are_rejected(configured_oidc):
    with pytest.raises(HTTPException) as bad_issuer:
        validate_oidc_token(_token(configured_oidc, iss="https://attacker.example"))
    assert bad_issuer.value.status_code == 401
    with pytest.raises(HTTPException) as no_role:
        validate_oidc_token(_token(configured_oidc, groups=["unknown"]))
    assert no_role.value.status_code == 403


def test_shared_api_key_cannot_act_as_human_in_production(configured_oidc, monkeypatch):
    monkeypatch.setenv("CREDITFLOW_API_KEY", "service-only-test-key")
    with pytest.raises(HTTPException) as rejected:
        require_principal(api_key="service-only-test-key", bearer_creds=None)
    assert rejected.value.status_code == 401


def test_idp_unavailable_is_service_failure_not_bad_credentials(configured_oidc, monkeypatch):
    def unavailable(url):
        raise jwt.PyJWKClientConnectionError("IdP unavailable")
    monkeypatch.setattr("backend.security._jwks_client", unavailable)
    with pytest.raises(HTTPException) as failure:
        validate_oidc_token(_token(configured_oidc))
    assert failure.value.status_code == 503
