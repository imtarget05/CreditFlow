from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import backend.app as app_module
from backend import security
from backend.security import create_access_token, hash_demo_password
from backend.user_management import UserManager

ADMIN_KEY = "test-user-admin-key"
ADMIN_HEADERS = {"X-CreditFlow-API-Key": ADMIN_KEY}


@pytest.fixture
def management_client(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_ENV", raising=False)
    monkeypatch.setenv("CREDITFLOW_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ID", "user-admin")
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "ADMIN")
    monkeypatch.setenv(
        "CREDITFLOW_JWT_SECRET", "test-user-management-secret-at-least-32-chars"
    )
    monkeypatch.setattr(security, "_revoked_users", set())
    monkeypatch.setattr(security, "_revoked_sessions", {})
    manager = UserManager()
    monkeypatch.setattr(app_module, "user_manager", manager)
    with TestClient(app_module.app) as client:
        yield client, manager


def _create_user(client, username="reviewer-1", email="reviewer@example.test"):
    response = client.post(
        "/api/users",
        headers=ADMIN_HEADERS,
        json={
            "username": username,
            "email": email,
            "password_hash": "test-hash",
            "role": "UNDERWRITER_L1",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_user_list_get_create_update_and_role_history(management_client):
    client, _manager = management_client
    created = _create_user(client)
    user_id = created["id"]

    assert client.get("/api/users", headers=ADMIN_HEADERS).json() == [created]
    assert client.get(f"/api/users/{user_id}", headers=ADMIN_HEADERS).json() == created
    updated = client.put(
        f"/api/users/{user_id}",
        headers=ADMIN_HEADERS,
        json={"email": "reviewer-updated@example.test", "role": "RISK_COMMITTEE_L2"},
    )
    assert updated.status_code == 200
    assert updated.json()["role"] == "RISK_COMMITTEE_L2"
    history = client.get(
        f"/api/users/{user_id}/role-history", headers=ADMIN_HEADERS
    )
    assert history.status_code == 200
    assert [entry["new_role"] for entry in history.json()] == [
        "RISK_COMMITTEE_L2",
        "UNDERWRITER_L1",
    ]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/api/users/missing", None),
        ("put", "/api/users/missing", {"email": "nobody@example.test"}),
        ("get", "/api/users/missing/sessions", None),
        ("get", "/api/users/missing/role-history", None),
        ("post", "/api/users/missing/revoke", {"reason": "test"}),
        ("post", "/api/users/missing/unrevoke", None),
    ],
)
def test_unknown_user_endpoints_return_404(management_client, method, path, body):
    client, _manager = management_client
    response = getattr(client, method)(
        path, headers=ADMIN_HEADERS, **({"json": body} if body is not None else {})
    )

    assert response.status_code == 404


def test_create_rejects_duplicate_username_and_email(management_client):
    client, _manager = management_client
    _create_user(client)

    duplicate_username = client.post(
        "/api/users",
        headers=ADMIN_HEADERS,
        json={
            "username": "reviewer-1",
            "email": "new@example.test",
            "password_hash": "test-hash",
            "role": "UNDERWRITER_L1",
        },
    )
    duplicate_email = client.post(
        "/api/users",
        headers=ADMIN_HEADERS,
        json={
            "username": "reviewer-2",
            "email": "reviewer@example.test",
            "password_hash": "test-hash",
            "role": "UNDERWRITER_L1",
        },
    )

    assert duplicate_username.status_code == 409
    assert duplicate_email.status_code == 409


def test_account_revoke_bridges_token_and_all_session_revocation(management_client):
    client, manager = management_client
    user = _create_user(client, "revoke-target", "revoke@example.test")
    numeric_identity_user = _create_user(
        client, user["id"], "numeric-identity@example.test"
    )
    session = manager.create_session(user["id"], "token-hash", "2099-01-01T00:00:00Z")
    token = create_access_token(user["username"], user["role"])
    numeric_identity_token = create_access_token(
        numeric_identity_user["username"], numeric_identity_user["role"]
    )
    assert security.decode_access_token(numeric_identity_token)["sub"] == (
        numeric_identity_user["username"]
    )

    response = client.post(
        f"/api/users/{user['id']}/revoke",
        headers=ADMIN_HEADERS,
        json={"reason": "access removed"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "REVOKED"
    assert security.is_user_revoked(user["username"])
    assert not security.is_user_revoked(numeric_identity_user["username"])
    numeric_identity_response = client.get(
        "/api/auth/sessions",
        headers={"Authorization": "Bearer " + numeric_identity_token},
    )
    assert numeric_identity_response.status_code == 200, numeric_identity_response.text
    assert session.id in security._revoked_sessions
    assert security._revoked_sessions[session.id] == "access removed"
    rejected = client.get(
        "/api/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert rejected.status_code == 403
    assert rejected.json()["detail"] == "USER_REVOKED"

    restored = client.post(
        f"/api/users/{user['id']}/unrevoke", headers=ADMIN_HEADERS
    )
    assert restored.status_code == 200
    assert restored.json()["status"] == "ACTIVE"
    assert not security.is_user_revoked(user["username"])
    assert manager.get_user_by_username(user["username"]) is not None
    assert len(manager.list_sessions(user["id"])) == 1
    allowed = client.get(
        "/api/auth/sessions", headers={"Authorization": f"Bearer {token}"}
    )
    assert allowed.status_code == 200
    assert len(allowed.json()) == 1
    assert allowed.json()[0]["revoked_at"] is not None


def test_admin_session_revocation_checks_owner_and_records_revocation(management_client):
    client, manager = management_client
    user = _create_user(client, "session-target", "session@example.test")
    other = _create_user(client, "other-target", "other@example.test")
    session = manager.create_session(user["id"], "token-hash", "2099-01-01T00:00:00Z")
    other_session = manager.create_session(
        other["id"], "other-token-hash", "2099-01-01T00:00:00Z"
    )

    wrong_owner = client.post(
        f"/api/users/{other['id']}/sessions/{session.id}/revoke",
        headers=ADMIN_HEADERS,
    )
    assert wrong_owner.status_code == 404
    revoked = client.post(
        f"/api/users/{user['id']}/sessions/{session.id}/revoke",
        headers=ADMIN_HEADERS,
    )
    already_revoked = client.post(
        f"/api/users/{user['id']}/sessions/{session.id}/revoke",
        headers=ADMIN_HEADERS,
    )

    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}
    assert session.id in security._revoked_sessions
    assert already_revoked.status_code == 404
    sessions = client.get(
        f"/api/users/{other['id']}/sessions", headers=ADMIN_HEADERS
    )
    assert sessions.status_code == 200
    assert sessions.json()[0]["id"] == other_session.id


def test_self_service_lists_and_revokes_sessions(management_client):
    client, manager = management_client
    user = manager.create_user(
        "self-service", "self@example.test", "test-hash", "UNDERWRITER_L1"
    )
    first = manager.create_session(user.id, "first-hash", "2099-01-01T00:00:00Z")
    second = manager.create_session(user.id, "second-hash", "2099-01-01T00:00:00Z")
    token = create_access_token(user.username, user.role)
    headers = {"Authorization": f"Bearer {token}"}

    assert [item["id"] for item in client.get(
        "/api/auth/sessions", headers=headers
    ).json()] == [first.id, second.id]
    single = client.get("/api/auth/revoke", params={"session_id": first.id}, headers=headers)
    all_remaining = client.get("/api/auth/revoke", headers=headers)

    assert single.status_code == 200
    assert single.json() == {"revoked": 1}
    assert all_remaining.status_code == 200
    assert all_remaining.json() == {"revoked": 1}
    assert first.id in security._revoked_sessions
    assert second.id in security._revoked_sessions
    assert not security.is_user_revoked(user.username)
    assert len(manager.list_sessions(user.id)) == 2
    sessions = client.get("/api/auth/sessions", headers=headers).json()
    assert len(sessions) == 2
    assert all(session["revoked_at"] is not None for session in sessions)


def test_user_management_authorization_requires_admin_or_risk_role(
    management_client, monkeypatch
):
    client, _manager = management_client
    underwriter_headers = {"X-CreditFlow-API-Key": ADMIN_KEY}

    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "UNDERWRITER_L1")
    assert client.get("/api/users", headers=underwriter_headers).status_code == 403
    assert client.post(
        "/api/users",
        headers=underwriter_headers,
        json={
            "username": "forbidden",
            "email": "forbidden@example.test",
            "password_hash": "test-hash",
            "role": "UNDERWRITER_L1",
        },
    ).status_code == 403


def test_demo_login_endpoint_returns_bearer_token(management_client, monkeypatch):
    client, _manager = management_client
    monkeypatch.setenv("CREDITFLOW_ENABLE_DEMO_LOGIN", "1")
    monkeypatch.setenv(
        "CREDITFLOW_DEMO_USERS_JSON",
        json.dumps(
            {
                "login-user": {
                    "password_hash": hash_demo_password("test-password", "test-salt"),
                    "role": "UNDERWRITER_L1",
                }
            }
        ),
    )

    response = client.post(
        "/api/auth/login",
        json={"username": "login-user", "password": "test-password"},
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["user"]["username"] == "login-user"
