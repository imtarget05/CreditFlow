"""Identity boundaries for CreditFlow.

Production human requests require an IdP-signed OIDC bearer token with a
configured issuer, audience, JWKS endpoint, and group-to-role mapping. The
deployment API key authenticates machine jobs only. A local password login
exists solely behind explicit non-production demo configuration; no users or
JWT signing secrets are built into the service.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

API_KEY_HEADER_NAME = "X-CreditFlow-API-Key"
API_KEY_ENV = "CREDITFLOW_API_KEY"
API_KEY_ID_ENV = "CREDITFLOW_API_KEY_ID"
API_KEY_ROLE_ENV = "CREDITFLOW_API_KEY_ROLE"
JWT_SECRET_ENV = "CREDITFLOW_JWT_SECRET"
ENV_ENV = "CREDITFLOW_ENV"
CORS_ORIGINS_ENV = "CREDITFLOW_CORS_ORIGINS"
SACOMBANK_OIDC_ISSUER_ENV = "SACOMBANK_OIDC_ISSUER"
SACOMBANK_OIDC_AUDIENCE_ENV = "SACOMBANK_OIDC_AUDIENCE"
SACOMBANK_OIDC_JWKS_URL_ENV = "SACOMBANK_OIDC_JWKS_URL"
SACOMBANK_OIDC_ROLE_MAP_ENV = "SACOMBANK_OIDC_ROLE_MAP_JSON"

DEFAULT_APPROVER_ID = "supervisor_on_duty"
DEFAULT_APPROVER_ROLE = "UNDERWRITER_L1"

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",  # vite dev server (frontend/vite.config.js:8)
    "http://localhost:8080",  # docker compose web (nginx -> api)
    "https://imtarget05.github.io",  # GitHub Pages production frontend
)


def _oidc_url_ok(url: str) -> bool:
    """True if an OIDC URL is acceptable: https always, http only on localhost."""
    parsed = urlparse(url)
    return parsed.scheme == "https" or (
        parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1")
    )

AUTHORITY_RANK = {
    "SYSTEM_STP": 0,
    "UNDERWRITER_L1": 1,
    "RISK_COMMITTEE_L2": 2,
    "ADMIN": 3,
}
HIGHEST_AUTHORITY = "ADMIN"
ROLES_FOR_HUMANS = {"UNDERWRITER_L1", "RISK_COMMITTEE_L2", "ADMIN"}

SACOMBANK_DEFAULT_ROLE_MAP = {
    "sacombank-underwriter": "UNDERWRITER_L1",
    "sacombank-risk-committee": "RISK_COMMITTEE_L2",
    "sacombank-admin": "ADMIN",
}

def demo_login_enabled() -> bool:
    """Local demo credentials are never accepted in production."""
    return not is_production() and os.environ.get("CREDITFLOW_ENABLE_DEMO_LOGIN") == "1"


def hash_demo_password(password: str, salt: str) -> str:
    """Create a salted hash for explicitly configured local demo users."""
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 310000)
    return f"pbkdf2_sha256$310000${salt}${digest.hex()}"


def _verify_demo_password(password: str, encoded: str) -> bool:
    try:
        algorithm, rounds, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256" or int(rounds) < 310000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
        return secrets.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False

def jwt_secret() -> str:
    secret = os.environ.get(JWT_SECRET_ENV, "").strip()
    if len(secret) < 32:
        raise RuntimeError(f"{JWT_SECRET_ENV} must contain at least 32 characters")
    return secret

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def _b64url_decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s.encode())

def create_access_token(
    identity: str, role: str, expires_in: int = 86400, session_id: str | None = None
) -> str:
    """Create a signed JWT token with identity and authority role."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": identity,
        "role": role,
        "exp": int(time.time()) + expires_in,
        "iat": int(time.time()),
    }
    if session_id:
        payload["session_id"] = session_id
    encoded_header = _b64url_encode(json.dumps(header).encode())
    encoded_payload = _b64url_encode(json.dumps(payload).encode())
    msg = f"{encoded_header}.{encoded_payload}".encode()
    signature = hmac.new(jwt_secret().encode(), msg, hashlib.sha256).digest()
    encoded_sig = _b64url_encode(signature)
    return f"{encoded_header}.{encoded_payload}.{encoded_sig}"

def decode_access_token(token: str) -> dict | None:
    """Verify and decode a signed JWT token."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    encoded_header, encoded_payload, encoded_sig = parts
    msg = f"{encoded_header}.{encoded_payload}".encode()
    try:
        expected_sig = hmac.new(jwt_secret().encode(), msg, hashlib.sha256).digest()
        sig = _b64url_decode(encoded_sig)
        if not secrets.compare_digest(sig, expected_sig):
            return None
        payload = json.loads(_b64url_decode(encoded_payload).decode())
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None

def authenticate_user(username: str, password: str) -> dict | None:
    """Authenticate only explicitly configured, non-production demo users."""
    if not demo_login_enabled():
        return None
    try:
        users = json.loads(os.environ.get("CREDITFLOW_DEMO_USERS_JSON", "{}"))
        user = users.get(username) if isinstance(users, dict) else None
    except (ValueError, TypeError):
        return None
    if not isinstance(user, dict) or user.get("role") not in AUTHORITY_RANK:
        return None
    if not _verify_demo_password(password, user.get("password_hash", "")):
        return None
    return {"username": username, "role": user["role"], "name": user.get("name", username)}


@lru_cache(maxsize=4)
def _jwks_client(url: str):
    return jwt.PyJWKClient(url)


def validate_oidc_token(token: str) -> Principal:
    """Verify an IdP-signed token and map configured groups to bank roles."""
    issuer = os.environ.get("CREDITFLOW_OIDC_ISSUER", "").strip()
    audience = os.environ.get("CREDITFLOW_OIDC_AUDIENCE", "").strip()
    jwks_url = os.environ.get("CREDITFLOW_OIDC_JWKS_URL", "").strip()
    if not issuer or not audience or not _oidc_url_ok(jwks_url):
        raise HTTPException(status_code=503, detail="OIDC_NOT_CONFIGURED")
    try:
        creditflow_role_map = json.loads(os.environ.get("CREDITFLOW_OIDC_ROLE_MAP_JSON", "{}"))
        sacombank_role_map = json.loads(os.environ.get("SACOMBANK_OIDC_ROLE_MAP_JSON", "{}"))
        # Merge: SACOMBANK defaults override, then CreditFlow custom mappings
        role_map = {**SACOMBANK_DEFAULT_ROLE_MAP, **creditflow_role_map, **sacombank_role_map}
        if not isinstance(role_map, dict) or not role_map:
            raise ValueError("Missing role mapping")
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            audience=audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWKClientConnectionError:
        raise HTTPException(status_code=503, detail="OIDC_IDP_UNAVAILABLE")
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="INVALID_OIDC_TOKEN")
    groups = claims.get("groups", [])
    if not isinstance(groups, list) or not all(isinstance(group, str) for group in groups):
        raise HTTPException(status_code=403, detail="OIDC_ROLE_NOT_MAPPED")
    roles = [role_map[group] for group in groups if group in role_map and role_map[group] in AUTHORITY_RANK]
    if not roles:
        raise HTTPException(status_code=403, detail="OIDC_ROLE_NOT_MAPPED")
    role = max(roles, key=lambda value: AUTHORITY_RANK[value])
    principal = Principal(identity=claims["sub"], role=role)
    if is_user_revoked(principal.identity):
        raise HTTPException(status_code=403, detail="USER_REVOKED")
    return principal


def require_oidc_configuration() -> None:
    """Fail startup if the bank identity contract is incomplete."""
    issuer = os.environ.get("CREDITFLOW_OIDC_ISSUER", "").strip()
    audience = os.environ.get("CREDITFLOW_OIDC_AUDIENCE", "").strip()
    jwks_url = os.environ.get("CREDITFLOW_OIDC_JWKS_URL", "").strip()
    try:
        role_map = json.loads(os.environ.get("CREDITFLOW_OIDC_ROLE_MAP_JSON", "{}"))
    except ValueError:
        role_map = {}
    if (
        not _oidc_url_ok(issuer)
        or not _oidc_url_ok(jwks_url)
        or not audience
        or not isinstance(role_map, dict)
        or not role_map
        or any(role not in ROLES_FOR_HUMANS for role in role_map.values())
    ):
        raise RuntimeError("CREDITFLOW_OIDC_* configuration is required in production")


_REVOKED_USERS_ENV = "CREDITFLOW_REVOKED_USERS_JSON"


def _load_revoked_users() -> set[str]:
    try:
        data = json.loads(os.environ.get(_REVOKED_USERS_ENV, "[]"))
    except (ValueError, TypeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item) for item in data}


# In-memory revocation state (demo scope; production checks the PostgreSQL
# ``users`` table from the 003_user_management migration).
_revoked_users: set[str] = _load_revoked_users()
_revoked_sessions: dict[str, str] = {}  # session_id -> reason


def is_user_revoked(identity: str) -> bool:
    """Return True if the identity has been revoked and must be rejected.

    Runtime revocations are recorded in the in-memory set; environment
    revocations are loaded when the module is initialized.
    """
    return identity in _revoked_users


def revoke_user_identity(identity: str) -> None:
    """Revoke every token issued to an identity until it is reactivated."""
    _revoked_users.add(identity)


def unrevoke_user_identity(identity: str) -> None:
    """Remove a runtime revocation after the account has been reactivated."""
    _revoked_users.discard(identity)


def revoke_user_session(session_id: str, reason: str = "revoked") -> bool:
    """Mark a session as revoked. Returns False if already revoked."""
    if session_id in _revoked_sessions:
        return False
    _revoked_sessions[session_id] = reason
    return True


def is_session_revoked(session_id: str) -> bool:
    """Return True if the session has been marked as revoked."""
    return session_id in _revoked_sessions


def require_sacombank_oidc_configuration() -> None:
    """Fail startup if the Sacombank IdP contract is incomplete in production."""
    if not is_production():
        return
    issuer = os.environ.get(SACOMBANK_OIDC_ISSUER_ENV, "").strip()
    audience = os.environ.get(SACOMBANK_OIDC_AUDIENCE_ENV, "").strip()
    jwks_url = os.environ.get(SACOMBANK_OIDC_JWKS_URL_ENV, "").strip()
    try:
        role_map = json.loads(os.environ.get(SACOMBANK_OIDC_ROLE_MAP_ENV, "{}"))
    except ValueError:
        role_map = {}
    if (
        not _oidc_url_ok(issuer)
        or not _oidc_url_ok(jwks_url)
        or not audience
        or not isinstance(role_map, dict)
        or not role_map
        or any(role not in AUTHORITY_RANK for role in role_map.values())
    ):
        raise RuntimeError("SACOMBANK_OIDC_* configuration is required in production")

api_key_header = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)
bearer_scheme = HTTPBearer(auto_error=False)
_MISSING_CREDENTIALS = {"WWW-Authenticate": "ApiKey, Bearer"}

@dataclass(frozen=True)
class Principal:
    """The authenticated caller: who it is and what it may authorise."""

    identity: str
    role: str

    @property
    def level(self) -> int:
        return AUTHORITY_RANK.get(self.role, 0)


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
        if is_production():
            require_oidc_configuration()
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


def require_principal(
    api_key: str | None = Security(api_key_header),
    bearer_creds: HTTPAuthorizationCredentials | None = Security(bearer_scheme),  # noqa: B008
) -> Principal:
    """Authenticate via JWT Bearer token or shared API key secret."""
    # 1. First priority: Check JWT Bearer token if provided
    token_str = getattr(bearer_creds, "credentials", None)
    if is_production():
        if not token_str:
            raise HTTPException(status_code=401, detail="OIDC_BEARER_REQUIRED")
        return validate_oidc_token(token_str)
    if token_str:
        payload = decode_access_token(token_str)
        if payload and "sub" in payload and "role" in payload:
            if is_user_revoked(payload["sub"]):
                raise HTTPException(status_code=403, detail="USER_REVOKED")
            if "session_id" in payload and is_session_revoked(payload["session_id"]):
                raise HTTPException(status_code=403, detail="USER_REVOKED")
            return Principal(identity=payload["sub"], role=payload["role"])
        raise HTTPException(
            status_code=401,
            detail="INVALID_BEARER_TOKEN: token is invalid or expired.",
            headers=_MISSING_CREDENTIALS,
        )

    # 2. Second priority: Fall back to shared API Key
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
            detail=f"INVALID_API_KEY: supply valid {API_KEY_HEADER_NAME} or Authorization Bearer token.",
            headers=_MISSING_CREDENTIALS,
        )
    return Principal(identity=configured_approver_id(), role=configured_approver_role())


def require_service_principal(api_key: str | None = Security(api_key_header)) -> Principal:
    """A deployment key can run machine jobs but has no human authority."""
    expected = configured_api_key()
    if not expected or not api_key or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="INVALID_SERVICE_KEY")
    return Principal(identity="creditflow-monitor", role="SYSTEM_STP")


def require_scoring_access(
    api_key: str | None = Security(api_key_header),
    bearer_creds: HTTPAuthorizationCredentials | None = Security(bearer_scheme),  # noqa: B008
) -> Principal | None:
    """The stateless scoring API stays public only outside production."""
    if is_production():
        return require_principal(api_key, bearer_creds)
    return None


def require_workflow_principal(
    api_key: str | None = Security(api_key_header),
    bearer_creds: HTTPAuthorizationCredentials | None = Security(bearer_scheme),  # noqa: B008
) -> Principal | None:
    """Persistent applicant workflows require identity, except explicit demo runs."""
    if not is_production() and os.environ.get("CREDITFLOW_ALLOW_ANONYMOUS_DEMO") == "1":
        return None
    return require_principal(api_key, bearer_creds)


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
