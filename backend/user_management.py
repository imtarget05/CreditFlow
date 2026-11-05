"""User lifecycle management (Wave 2: Sacombank IdP + user lifecycle).

In-memory storage bootstrapped from ``CREDITFLOW_USERS_JSON``. In production
this layer should be backed by the PostgreSQL ``users``, ``user_sessions`` and
``user_role_history`` tables from ``deploy/sql/003_user_management.sql``.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.security import (
    revoke_user_identity,
    revoke_user_session,
    unrevoke_user_identity,
)


class User(BaseModel):
    id: str
    username: str
    email: str
    role: str
    status: str = "ACTIVE"
    created_at: str
    updated_at: str
    last_login_at: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None
    revocation_reason: str | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    email: str
    password_hash: str = Field(min_length=1)
    role: str


class UserUpdate(BaseModel):
    email: str | None = None
    role: str | None = None
    status: str | None = Field(default=None, pattern="^(ACTIVE|SUSPENDED|REVOKED)$")


class UserSession(BaseModel):
    id: str
    user_id: str
    token_hash: str
    expires_at: str
    created_at: str
    revoked_at: str | None = None


class RoleHistoryEntry(BaseModel):
    id: str
    user_id: str
    old_role: str | None
    new_role: str
    changed_by: str | None = None
    changed_at: str
    reason: str | None = None


class UserManager:
    """In-memory user lifecycle store (demo scope; PostgreSQL in production)."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._by_username: dict[str, str] = {}
        self._by_email: dict[str, str] = {}
        self._sessions: dict[str, UserSession] = {}
        self._sessions_by_user: dict[str, list[str]] = {}
        self._history: dict[str, RoleHistoryEntry] = {}
        self._history_by_user: dict[str, list[str]] = {}
        self._next_user = 1
        self._next_session = 1
        self._next_history = 1
        self._bootstrap()

    # -- bootstrap -------------------------------------------------------
    def _bootstrap(self) -> None:
        raw = os.environ.get("CREDITFLOW_USERS_JSON", "").strip()
        if not raw:
            return
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        if not isinstance(data, list):
            return
        for entry in data:
            if not isinstance(entry, dict):
                continue
            self.create_user(
                username=str(entry.get("username", "")),
                email=str(entry.get("email", "")),
                password_hash=str(entry.get("password_hash", "")),
                role=str(entry.get("role", "VIEWER")),
            )

    # -- users -----------------------------------------------------------
    def list_users(self) -> list[User]:
        """Return every known user."""
        return list(self._users.values())

    def get_user(self, user_id: str) -> User | None:
        """Get a user by internal id."""
        return self._users.get(user_id)

    def get_user_by_username(self, username: str) -> User | None:
        """Get a user by login username (OIDC ``sub`` / demo username)."""
        user_id = self._by_username.get(username)
        if user_id is None:
            return None
        return self._users.get(user_id)

    def create_user(self, username: str, email: str, password_hash: str, role: str) -> User:
        """Create a new user and record the initial role history entry."""
        if username in self._by_username:
            raise ValueError(f"USER_ALREADY_EXISTS: {username}")
        if email in self._by_email:
            raise ValueError(f"EMAIL_ALREADY_EXISTS: {email}")
        now = datetime.now(UTC).isoformat()
        user_id = str(self._next_user)
        self._next_user += 1
        user = User(
            id=user_id,
            username=username,
            email=email,
            role=role,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
        )
        self._users[user_id] = user
        self._by_username[username] = user_id
        self._by_email[email] = user_id
        self._record_history(user_id, None, role, changed_by=None, reason="initial")
        return user

    def update_user(self, user_id: str, **updates: Any) -> User | None:
        """Update mutable user fields; record role history on role change."""
        user = self._users.get(user_id)
        if user is None:
            return None
        old_role = user.role
        data = user.model_dump()
        for key, value in updates.items():
            if key in ("email", "role", "status") and value is not None:
                data[key] = value
        if "email" in updates and updates["email"] is not None and updates["email"] != user.email:
            new_email = updates["email"]
            if new_email in self._by_email and self._by_email[new_email] != user_id:
                raise ValueError(f"EMAIL_ALREADY_EXISTS: {new_email}")
            del self._by_email[user.email]
            self._by_email[new_email] = user_id
        data["updated_at"] = datetime.now(UTC).isoformat()
        updated = User(**data)
        self._users[user_id] = updated
        new_role = updates.get("role")
        if new_role is not None and new_role != old_role:
            self._record_history(user_id, old_role, new_role, changed_by=None, reason="role update")
        return updated

    def revoke_user(
        self, user_id: str, revoked_by: str = "system", reason: str = "revoked"
    ) -> User | None:
        """Revoke a user and all of their active sessions."""
        user = self._users.get(user_id)
        if user is None:
            return None
        if user.status == "REVOKED":
            return user
        now = datetime.now(UTC).isoformat()
        data = user.model_dump()
        data["status"] = "REVOKED"
        data["revoked_at"] = now
        data["revoked_by"] = revoked_by
        data["revocation_reason"] = reason
        data["updated_at"] = now
        revoked = User(**data)
        self._users[user_id] = revoked
        username_owner = self._by_username.get(user.id)
        if username_owner is None or username_owner == user.id:
            revoke_user_identity(user.id)
        revoke_user_identity(user.username)
        for session_id in list(self._sessions_by_user.get(user_id, [])):
            self.revoke_session(session_id, reason=reason)
        return revoked

    def unrevoke_user(self, user_id: str) -> User | None:
        """Reactivate a revoked user."""
        user = self._users.get(user_id)
        if user is None:
            return None
        if user.status != "REVOKED":
            return user
        data = user.model_dump()
        data["status"] = "ACTIVE"
        data["revoked_at"] = None
        data["revoked_by"] = None
        data["revocation_reason"] = None
        data["updated_at"] = datetime.now(UTC).isoformat()
        restored = User(**data)
        self._users[user_id] = restored
        username_owner = self._by_username.get(user.id)
        if username_owner is None or username_owner == user.id:
            unrevoke_user_identity(user.id)
        unrevoke_user_identity(user.username)
        return restored

    def is_user_active(self, user_id: str) -> bool:
        """Return True if the user exists and is ACTIVE."""
        user = self._users.get(user_id)
        return user is not None and user.status == "ACTIVE"

    def is_user_revoked(self, identity: str) -> bool:
        """Return True if the user identified by username or id is revoked."""
        user = self.get_user_by_username(identity) or self.get_user(identity)
        if user is None:
            return False
        return user.status == "REVOKED"

    # -- sessions --------------------------------------------------------
    def list_sessions(self, user_id: str) -> list[UserSession]:
        """List all sessions (including revoked) for a user."""
        return [
            self._sessions[sid]
            for sid in self._sessions_by_user.get(user_id, [])
            if sid in self._sessions
        ]

    def create_session(self, user_id: str, token_hash: str, expires_at: str) -> UserSession:
        """Register a new session for a user (used by the OIDC/login flow)."""
        session_id = str(self._next_session)
        self._next_session += 1
        now = datetime.now(UTC).isoformat()
        session = UserSession(
            id=session_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
        )
        self._sessions[session_id] = session
        self._sessions_by_user.setdefault(user_id, []).append(session_id)
        return session

    def revoke_session(self, session_id: str, reason: str = "revoked") -> bool:
        """Mark a session as revoked. Returns False if missing or already revoked."""
        session = self._sessions.get(session_id)
        if session is None:
            return False
        if session.revoked_at is not None:
            return False
        data = session.model_dump()
        data["revoked_at"] = datetime.now(UTC).isoformat()
        self._sessions[session_id] = UserSession(**data)
        revoke_user_session(session_id, reason=reason)
        return True

    # -- role history ----------------------------------------------------
    def get_role_history(self, user_id: str) -> list[RoleHistoryEntry]:
        """Return the role change history for a user, newest first."""
        entries = [
            self._history[hid]
            for hid in self._history_by_user.get(user_id, [])
            if hid in self._history
        ]
        entries.sort(key=lambda entry: entry.changed_at, reverse=True)
        return entries

    def _record_history(
        self,
        user_id: str,
        old_role: str | None,
        new_role: str,
        changed_by: str | None,
        reason: str | None,
    ) -> RoleHistoryEntry:
        history_id = str(self._next_history)
        self._next_history += 1
        entry = RoleHistoryEntry(
            id=history_id,
            user_id=user_id,
            old_role=old_role,
            new_role=new_role,
            changed_by=changed_by,
            changed_at=datetime.now(UTC).isoformat(),
            reason=reason,
        )
        self._history[history_id] = entry
        self._history_by_user.setdefault(user_id, []).append(history_id)
        return entry


# Module-level default manager used by backend.app.
user_manager = UserManager()
