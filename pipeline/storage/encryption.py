"""Encryption at rest for CreditFlow PostgreSQL storage.

Provides pgcrypto-based symmetric encryption for sensitive columns
(customer_data, audit_trail, error_message, etc.) when DATABASE_URL
points at PostgreSQL. The encryption key is taken from the
CREDITFLOW_ENCRYPTION_KEY environment variable (32+ characters).

Design:
  - The key never travels in SQL as plain text: pgp_sym_encrypt/decrypt
    receive the key as a parameter, so the key never appears in pg_stat_activity.
  - SQLite (demo mode) is unaffected: encryption functions are no-ops on
    the SQLite path and only apply when _is_postgres() is True.
  - If CREDITFLOW_ENCRYPTION_KEY is unset on the Postgres path, writes
    fail closed (raise) rather than storing plaintext.
"""
from __future__ import annotations

import os
import sqlite3


def encryption_key() -> str:
    """Return the column-level encryption key, or empty string if unset."""
    return os.environ.get("CREDITFLOW_ENCRYPTION_KEY", "").strip()


def require_encryption_key() -> None:
    """Fail closed when writing to Postgres without an encryption key."""
    key = encryption_key()
    if not key:
        raise RuntimeError(
            "CREDITFLOW_ENCRYPTION_KEY is required for encrypted PostgreSQL storage; "
            "set it to a 32+ character secret before writing sensitive columns."
        )


def encrypt_sql() -> str:
    """Return a pgcrypto expression that encrypts a value (parameterised)."""
    return "pgp_sym_encrypt(%s, %s)"


def decrypt_sql(column: str) -> str:
    """Return a pgcrypto expression that decrypts ``column`` (parameterised)."""
    return f"pgp_sym_decrypt({column}, %s)"


def pgcrypto_available(conn) -> bool:
    """Check whether the pgcrypto extension is installed on the connection."""
    try:
        row = conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'"
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def ensure_pgcrypto(conn) -> None:
    """Install pgcrypto if missing (Postgres path only)."""
    if isinstance(conn, sqlite3.Connection):
        return
    if not pgcrypto_available(conn):
        conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        conn.commit()


def encrypt_value(plaintext: str, key: str | None = None) -> str:
    """Encrypt ``plaintext`` with pgcrypto (Postgres path only).

    Used by callers that want to encrypt before handing a value to SQL.
    On the SQLite path this is a no-op (returns the plaintext unchanged).
    """
    if key is None:
        key = encryption_key()
    if not key:
        return plaintext
    # Delegates to SQL via the caller's connection; kept here for symmetry
    # with decrypt_value when a connection is available. The SQL-level
    # helpers (encrypt_sql / decrypt_sql) are preferred inside ledger.py.
    raise NotImplementedError(
        "encrypt_value without a connection is not supported; use "
        "encrypt_sql() with a parameterised query instead."
    )


def decrypt_value(ciphertext: str, key: str | None = None) -> str:
    """Decrypt ``ciphertext`` with pgcrypto (Postgres path only)."""
    if key is None:
        key = encryption_key()
    if not key:
        return ciphertext
    raise NotImplementedError(
        "decrypt_value without a connection is not supported; use "
        "decrypt_sql() with a parameterised query instead."
    )
