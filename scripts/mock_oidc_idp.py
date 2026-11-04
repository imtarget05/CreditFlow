"""Mock Sacombank OIDC Identity Provider (demo data demo).

Serves a minimal OIDC IdP for local development and production-readiness
gate validation. Generates an RSA-256 key pair and exposes:

  GET  /.well-known/openid-configuration  — OIDC discovery document
  GET  /keys                               — JWKS public key set
  POST /token                              — issues RS256-signed JWT access tokens

The token endpoint accepts ``grant_type=client_credentials`` with a ``groups``
form field (comma-separated). The issued JWT carries ``iss``, ``aud``, ``sub``,
``groups``, ``iat``, ``exp`` claims signed with RS256.

Usage:
  CREDITFLOW_OIDC_ISSUER=http://localhost:9099 \
  CREDITFLOW_OIDC_AUDIENCE=creditflow \
  CREDITFLOW_OIDC_JWKS_URL=http://localhost:9099/keys \
  CREDITFLOW_OIDC_ROLE_MAP_JSON='{"sacombank-underwriter":"UNDERWRITER_L1","sacombank-risk-committee":"RISK_COMMITTEE_L2","sacombank-admin":"ADMIN"}' \
  python scripts/mock_oidc_idp.py

WARNING: This is a synthetic demo IdP. The private key is generated in memory
and never persisted. Do not use in production with real Sacombank data.
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask, jsonify, request

app = Flask(__name__)

# ---------------------------------------------------------------------------
# RSA key pair (generated in memory, never persisted — demo scope only)
# ---------------------------------------------------------------------------
_private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048,
    backend=default_backend(),
)
_public_key = _private_key.public_key()
_public_pem = _public_key.public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("ascii")

ISSUER = os.environ.get("MOCK_OIDC_ISSUER", "http://localhost:9099")
AUDIENCE = os.environ.get("MOCK_OIDC_AUDIENCE", "creditflow")
PORT = int(os.environ.get("MOCK_OIDC_PORT", "9099"))

# JWKS: convert public PEM to JWK format

_numbers = _public_key.public_numbers()
_n = _numbers.n
_e = _numbers.e


def _int_to_b64u(value: int) -> str:
    """Encode a positive integer as base64url-encoded (no padding)."""
    import base64

    byte_length = (value.bit_length() + 7) // 8
    bytes_value = value.to_bytes(byte_length, byteorder="big")
    return base64.urlsafe_b64encode(bytes_value).rstrip(b"=").decode("ascii")


_JWKS = {
    "keys": [
        {
            "kty": "RSA",
            "kid": "mock-sacombank-key-1",
            "use": "sig",
            "alg": "RS256",
            "n": _int_to_b64u(_n),
            "e": _int_to_b64u(_e),
        }
    ]
}

# In-memory token store (demo scope; revocation supported)
_issued_tokens: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/.well-known/openid-configuration")
def discovery():
    return jsonify(
        {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/keys",
            "response_types_supported": ["id_token"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }
    )


@app.get("/keys")
def jwks():
    return jsonify(_JWKS)


@app.post("/token")
def token():
    grant_type = request.form.get("grant_type", "")
    groups_raw = request.form.get("groups", "")
    subject = request.form.get("sub", "demo-user")

    if grant_type != "client_credentials":
        return jsonify({"error": "unsupported_grant_type"}), 400

    groups = [g.strip() for g in groups_raw.split(",") if g.strip()]

    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=1)

    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": subject,
        "groups": groups,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(uuid.uuid4()),
    }

    token = jwt.encode(
        payload,
        _private_key,
        algorithm="RS256",
        headers={"kid": "mock-sacombank-key-1"},
    )

    _issued_tokens[token] = {
        "subject": subject,
        "groups": groups,
        "issued_at": now.isoformat(),
        "revoked": False,
    }

    return jsonify(
        {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "openid profile",
        }
    )


@app.post("/token/revoke")
def revoke():
    """Revoke a previously issued token (demo scope)."""
    token = request.form.get("token", "")
    if token in _issued_tokens:
        _issued_tokens[token]["revoked"] = True
        return jsonify({"result": "success"})
    return jsonify({"error": "token_not_found"}), 404


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=PORT, debug=False)
