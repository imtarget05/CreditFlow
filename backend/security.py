"""Authentication + authorisation for the money-moving and PII-bearing routes.

The honest minimum for this service is **one shared secret** plus a configured
approver identity.  There is no user table, no OAuth provider and no role
database here, and inventing one would be a worse answer than a small honest
one: a single secret is auditable, rotatable and reviewable.

Limitation, stated honestly (mirrors the README "Production status" note): what
this module implements is **one shared API key per deployment**, not per-user
identity or RBAC.  There is no user table and no per-caller role, so every
holder of the key presents the same ``Principal``, and ``approver_id`` is a
single deployment-wide configuration value (``CREDITFLOW_API_KEY_ID``) rather
than the identity of the human who clicked approve.  Attributing an approval to
an individual reviewer, revoking one user without rotating the key for
everyone, and giving two people different authority all need the layer this
module deliberately does not invent: an identity provider (OIDC/OAuth2) or user
table, per-session credentials, RBAC mapping an authenticated user to a credit
authority level, and audit logging of the authenticated user id.  Until that
layer exists, treat reviewer identity as a configuration label, not as evidence
of who approved.

Configuration (environment only — never a committed value):

  CREDITFLOW_API_KEY        the shared secret presented in X-CreditFlow-API-Key
  CREDITFLOW_API_KEY_ID     identity recorded as ``approver_id`` (default
                            ``supervisor_on_duty``)
  CREDITFLOW_API_KEY_ROLE   credit authority of that key, drawn from the
                            authority matrix that already exists in
                            ``pipeline/disbursement/core_banking.py:159``
                            (default ``UNDERWRITER_L1`` — least privilege that
                            can still clear a human-review pause)
  CREDITFLOW_CORS_ORIGINS   explicit, comma-separated CORS origin allow-list
  CREDITFLOW_ENV            the project's existing production switch, reused
                            here rather than inventing a second one (see
                            ``pipeline/agent/nodes.py:278``)

Design decisions, and why:

* ``APIKeyHeader`` rather than ``HTTPBearer`` — a bearer token implies a
  per-user session minted by an identity provider, which this project does not
  have.  A key in a named header is also documented in OpenAPI's Authorize
  dialog, which means an operator can find the one place it belongs.
* ``secrets.compare_digest`` on the encoded bytes, never ``==`` — a
  short-circuiting comparison leaks the key one byte at a time through
  response timing.
* **Fail closed.**  An unset key never degrades to "no auth": in a
  production-like environment ``require_configured_key()`` raises so the app
  refuses to start, and everywhere else ``require_principal`` answers 401
  because no caller can authenticate.
* Identity and role come from configuration, never from the request body, so
  ``approver_id`` can no longer be self-asserted.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

API_KEY_HEADER_NAME = "X-CreditFlow-API-Key"
API_KEY_ENV = "CREDITFLOW_API_KEY"
API_KEY_ID_ENV = "CREDITFLOW_API_KEY_ID"
API_KEY_ROLE_ENV = "CREDITFLOW_API_KEY_ROLE"
ENV_ENV = "CREDITFLOW_ENV"
CORS_ORIGINS_ENV = "CREDITFLOW_CORS_ORIGINS"

DEFAULT_APPROVER_ID = "supervisor_on_duty"
# Least privilege that can still approve a workflow paused at human_approval
# (a level-0 STP decision is auto-approved and never reaches this endpoint).
DEFAULT_APPROVER_ROLE = "UNDERWRITER_L1"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",  # vite dev server (frontend/vite.config.js:8)
    "http://localhost:8080",  # docker compose web (nginx -> api)
    "https://imtarget05.github.io",  # GitHub Pages production frontend
)

# Seniority of the credit approval authority matrix that the workflow already
# computes (``determine_authority_level``).  Reusing that vocabulary keeps
# authorisation aligned with the business rule instead of adding a second,
# parallel role system that nothing else in the codebase knows about.
AUTHORITY_RANK = {
    "SYSTEM_STP": 0,
    "UNDERWRITER_L1": 1,
    "RISK_COMMITTEE_L2": 2,
}
HIGHEST_AUTHORITY = "RISK_COMMITTEE_L2"

api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)
_MISSING_CREDENTIALS = {"WWW-Authenticate": "ApiKey"}


@dataclass(frozen=True)
class Principal:
    """The authenticated caller: who it is and what it may authorise."""

    identity: str
    role: str

    @property
    def level(self) -> int:
        return AUTHORITY_RANK[self.role]


def is_production() -> bool:
    """Reuse the project's single production switch (``CREDITFLOW_ENV``)."""
    return os.environ.get(ENV_ENV, "development").strip().lower() == "production"


def configured_api_key() -> str:
    return os.environ.get(API_KEY_ENV, "").strip()


def configured_approver_id() -> str:
    return os.environ.get(API_KEY_ID_ENV, "").strip() or DEFAULT_APPROVER_ID


def configured_approver_role() -> str:
    role = os.environ.get(API_KEY_ROLE_ENV, "").strip().upper() or DEFAULT_APPROVER_ROLE
    if role not in AUTHORITY_RANK:
        raise RuntimeError(
            f"{API_KEY_ROLE_ENV}={role!r} is not a credit authority level; "
            f"expected one of {', '.join(sorted(AUTHORITY_RANK))}."
        )
    return role


def require_configured_key() -> None:
    """Refuse to serve a production deployment with authentication disabled."""
    if configured_api_key():
        # A role outside the authority matrix is a misconfiguration, not a
        # detail: never come up holding an identity we cannot rank.
        configured_approver_role()
        return
    if is_production():
        raise RuntimeError(
            f"{API_KEY_ENV} is unset while {ENV_ENV}=production: refusing to "
            "start with authentication disabled. Set the shared API key in the "
            "deployment environment (never in the repository)."
        )


def allowed_origins() -> list[str]:
    """Explicit CORS allow-list.  A wildcard is refused, not silently kept."""
    raw = os.environ.get(CORS_ORIGINS_ENV, "").strip()
    if raw:
        origins = [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]
    else:
        origins = list(DEFAULT_CORS_ORIGINS)
    if any(origin == "*" for origin in origins):
        raise RuntimeError(
            f"{CORS_ORIGINS_ENV} may not contain '*': list the origins that are "
            f"allowed to call this API (default: {', '.join(DEFAULT_CORS_ORIGINS)})."
        )
    return origins


def require_principal(api_key: str | None = Security(api_key_header)) -> Principal:
    """Authenticate the shared secret.  401 on absent, wrong or unconfigured."""
    expected = configured_api_key()
    if not expected:
        # Fail closed: no configured secret means nobody can authenticate.
        raise HTTPException(
            status_code=401,
            detail=f"API_KEY_NOT_CONFIGURED: {API_KEY_ENV} is not set on the server.",
            headers=_MISSING_CREDENTIALS,
        )
    if not api_key or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail=f"INVALID_API_KEY: supply {API_KEY_HEADER_NAME}.",
            headers=_MISSING_CREDENTIALS,
        )
    return Principal(identity=configured_approver_id(), role=configured_approver_role())


def ensure_authority(principal: Principal, required_role: str | None) -> None:
    """Authorise an approval against the authority the workflow actually needs.

    An unrecognised or missing required level is treated as the highest level,
    so an unreadable state can never lower the bar.
    """
    required = (required_role or "").strip().upper()
    if required not in AUTHORITY_RANK:
        required = HIGHEST_AUTHORITY
    if principal.level < AUTHORITY_RANK[required]:
        raise HTTPException(
            status_code=403,
            detail=(
                f"INSUFFICIENT_AUTHORITY: this approval requires {required} "
                f"authority, the presented key is {principal.role}."
            ),
        )