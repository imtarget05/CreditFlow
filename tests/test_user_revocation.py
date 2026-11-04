"""Automated tests for Token Revocation Bridge.

Verifies the integration between user lifecycle management (user_manager),
API revocation endpoints (/api/users/{id}/revoke), and authentication enforcement
(security.is_user_revoked and require_principal).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend import security
from backend.security import (
    create_access_token,
    is_session_revoked,
    is_user_revoked,
)
from backend.user_management import UserManager

ADMIN_CREDENTIAL = "local-test"
ADMIN_HEADERS = {"X-CreditFlow-API-Key": ADMIN_CREDENTIAL}
JWT_SECRET = "demo-jwt-token-value"


@pytest.fixture
def auth_client(monkeypatch):
    """Isolated client and security state for testing revocation."""
    monkeypatch.delenv("CREDITFLOW_ENV", raising=False)
    monkeypatch.setenv("CREDITFLOW_API_KEY", ADMIN_CREDENTIAL)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ID", "test-admin")
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "ADMIN")
    monkeypatch.setenv("CREDITFLOW_JWT_SECRET", JWT_SECRET)

    # Fresh in-memory stores for each test
    monkeypatch.setattr(security, "_revoked_users", set())
    monkeypatch.setattr(security, "_revoked_sessions", {})
    manager = UserManager()
    monkeypatch.setattr(app_module, "user_manager", manager)

    with TestClient(app_module.app) as client:
        yield client, manager


def _create_test_user(
    client, username="underwriter_test", email="underwriter@sacombank.test"
):
    res = client.post(
        "/api/users",
        headers=ADMIN_HEADERS,
        json={
            "username": username,
            "email": email,
            "password_hash": "hash123",
            "role": "UNDERWRITER_L1",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_token_authentication_succeeds_for_active_user(auth_client):
    """An active user's bearer token authenticates successfully."""
    client, _manager = auth_client
    user = _create_test_user(client, "active_user", "active@example.test")
    token = create_access_token(user["username"], user["role"])

    assert not is_user_revoked(user["id"])
    assert not is_user_revoked(user["username"])

    response = client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_api_revocation_blocks_token_and_sets_is_user_revoked(auth_client):
    """Revoking a user via /api/users/{id}/revoke updates security state and blocks tokens."""
    client, manager = auth_client
    user = _create_test_user(client, "revoke_me", "revokeme@example.test")
    session = manager.create_session(user["id"], "tok_hash", "2099-01-01T00:00:00Z")
    token = create_access_token(user["username"], user["role"])

    # Confirm token works before revocation
    assert client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200

    # Revoke user via API
    res = client.post(
        f"/api/users/{user['id']}/revoke",
        headers=ADMIN_HEADERS,
        json={"reason": "security audit violation"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "REVOKED"

    # Both user ID and username must be recorded as revoked
    assert is_user_revoked(user["id"])
    assert is_user_revoked(user["username"])

    # User's session must be recorded in revoked sessions
    assert session.id in security._revoked_sessions
    assert security._revoked_sessions[session.id] == "security audit violation"

    # Subsequent requests with the revoked user's token receive 403 USER_REVOKED
    blocked_app = client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert blocked_app.status_code == 403
    assert blocked_app.json()["detail"] == "USER_REVOKED"

    blocked_disb = client.get(
        "/disbursements", headers={"Authorization": f"Bearer {token}"}
    )
    assert blocked_disb.status_code == 403
    assert blocked_disb.json()["detail"] == "USER_REVOKED"

    blocked_sessions = client.get(
        "/api/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert blocked_sessions.status_code == 403
    assert blocked_sessions.json()["detail"] == "USER_REVOKED"


def test_api_unrevocation_restores_token_authentication(auth_client):
    """Reactivating a user via /api/users/{id}/unrevoke allows token authentication again."""
    client, _manager = auth_client
    user = _create_test_user(client, "rebound_user", "rebound@example.test")
    token = create_access_token(user["username"], user["role"])

    # Revoke
    client.post(
        f"/api/users/{user['id']}/revoke",
        headers=ADMIN_HEADERS,
        json={"reason": "temporary freeze"},
    )
    assert is_user_revoked(user["username"])
    assert client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 403

    # Unrevoke
    unrev_res = client.post(
        f"/api/users/{user['id']}/unrevoke", headers=ADMIN_HEADERS
    )
    assert unrev_res.status_code == 200
    assert unrev_res.json()["status"] == "ACTIVE"

    # Verify is_user_revoked returns False
    assert not is_user_revoked(user["id"])
    assert not is_user_revoked(user["username"])

    # Token succeeds again
    restored = client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert restored.status_code == 200


def test_user_manager_direct_revocation_bridges_to_security(auth_client):
    """Direct invocation of user_manager.revoke_user bridges to security.is_user_revoked."""
    client, manager = auth_client
    user = manager.create_user("direct_user", "direct@test.com", "hash", "UNDERWRITER_L1")
    token = create_access_token(user.username, user.role)

    # Initial state
    assert not is_user_revoked(user.id)
    assert not is_user_revoked(user.username)

    # Direct revoke
    revoked = manager.revoke_user(user.id, revoked_by="auditor", reason="direct revocation")
    assert revoked is not None
    assert revoked.status == "REVOKED"

    # Must be recognized by security module
    assert is_user_revoked(user.id)
    assert is_user_revoked(user.username)

    # Token must be rejected
    rejected = client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "USER_REVOKED"

    # Direct unrevoke
    restored = manager.unrevoke_user(user.id)
    assert restored is not None
    assert restored.status == "ACTIVE"
    assert not is_user_revoked(user.id)
    assert not is_user_revoked(user.username)


def test_user_manager_single_arg_revoke_user_interface(auth_client):
    """user_manager.revoke_user(user_id) contract functions with default arguments."""
    _client, manager = auth_client
    user = manager.create_user("single_arg", "single@test.com", "hash", "UNDERWRITER_L1")

    manager.revoke_user(user.id)
    assert is_user_revoked(user.id)
    assert is_user_revoked(user.username)


def test_token_with_user_id_sub_is_blocked_when_revoked(auth_client):
    """A token whose 'sub' is the internal user_id is blocked when that user is revoked."""
    client, manager = auth_client
    user = manager.create_user("id_sub_user", "idsub@test.com", "hash", "UNDERWRITER_L1")
    token = create_access_token(user.id, user.role)

    assert client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200

    manager.revoke_user(user.id)
    assert client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 403


def test_session_revocation_blocks_session_bound_token(auth_client):
    """A token carrying a session_id is blocked when that session is revoked."""
    client, manager = auth_client
    user = manager.create_user("session_token_user", "sess@test.com", "hash", "UNDERWRITER_L1")
    session = manager.create_session(user.id, "hash", "2099-01-01T00:00:00Z")
    token = create_access_token(user.username, user.role, session_id=session.id)

    # Token valid before session revocation
    assert client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    ).status_code == 200

    # Revoke the session
    ok = manager.revoke_session(session.id, reason="session killed")
    assert ok is True
    assert is_session_revoked(session.id)

    # Token rejected
    rejected = client.get(
        "/applications", headers={"Authorization": f"Bearer {token}"}
    )
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "USER_REVOKED"


def test_multiple_sessions_all_revoked_on_account_revocation(auth_client):
    """When a user with multiple sessions is revoked, all sessions are registered in _revoked_sessions."""
    client, manager = auth_client
    user = _create_test_user(client, "multi_sess", "multi@example.test")
    s1 = manager.create_session(user["id"], "h1", "2099-01-01T00:00:00Z")
    s2 = manager.create_session(user["id"], "h2", "2099-01-01T00:00:00Z")
    s3 = manager.create_session(user["id"], "h3", "2099-01-01T00:00:00Z")

    client.post(
        f"/api/users/{user['id']}/revoke",
        headers=ADMIN_HEADERS,
        json={"reason": "compromised"},
    )

    for s in (s1, s2, s3):
        assert s.id in security._revoked_sessions
        assert security._revoked_sessions[s.id] == "compromised"


def test_revoke_nonexistent_user_returns_404(auth_client):
    """Revoking or unrevoking a non-existent user returns 404."""
    client, _manager = auth_client
    r1 = client.post(
        "/api/users/999999/revoke",
        headers=ADMIN_HEADERS,
        json={"reason": "does not exist"},
    )
    assert r1.status_code == 404

    r2 = client.post(
        "/api/users/999999/unrevoke",
        headers=ADMIN_HEADERS,
    )
    assert r2.status_code == 404
