"""SQLite ledger for loan applications and disbursements (core-banking slice).

Solves two problems from the handoff:

1. ``_active_graphs`` in ``backend/app.py`` is RAM-only — a restart loses the
   workflow. The ``loan_applications`` table gives every started workflow a
   durable row keyed by ``thread_id``.
2. ``POST /predict/graph/{thread_id}/approve`` used to return JSON only. The
   approve handler now also writes a real ``disbursements`` row (contract +
   ledger entry) so the approval produces auditable money movement.

Schema (acceptance contract):

    loan_applications: id, thread_id, customer_data(JSON), risk_score,
        risk_level, decision, status(PENDING_REVIEW|APPROVED|REJECTED),
        created_at, updated_at
    disbursements: id, application_id(FK->loan_applications.id),
        contract_code(HDTD-YYYYMMDD-XXXX), loan_amount, disbursed_at,
        status(COMPLETED), ledger_hash

``ledger_hash`` is a SHA-256 over the immutable disbursement fields — any
manual edit of the row breaks the hash, giving tamper evidence for the
disbursement ledger. Demo scope: single-file SQLite, stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER_PATH = ROOT / "data" / "creditflow_ledger.db"

# Application lifecycle statuses (acceptance contract)
STATUS_PENDING_REVIEW = "PENDING_REVIEW"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

# Disbursement status
DISBURSEMENT_COMPLETED = "COMPLETED"

# Single-process write lock — FastAPI sync endpoints run in a threadpool.
_write_lock = threading.Lock()


def ledger_path() -> Path:
    """Resolve the DB path per call (env override lets tests isolate)."""
    env = os.environ.get("CREDITFLOW_LEDGER_DB")
    return Path(env) if env else DEFAULT_LEDGER_PATH


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create the ledger tables if they do not exist (idempotent)."""
    with _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS loan_applications (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id     TEXT NOT NULL UNIQUE,
                customer_data TEXT NOT NULL,           -- JSON blob
                risk_score    REAL,
                risk_level    TEXT,
                decision      TEXT,
                status        TEXT NOT NULL CHECK (
                    status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')
                ),
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS disbursements (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL REFERENCES loan_applications(id),
                contract_code TEXT NOT NULL UNIQUE,
                loan_amount   REAL NOT NULL,
                disbursed_at  TEXT NOT NULL,
                status        TEXT NOT NULL CHECK (status = 'COMPLETED'),
                ledger_hash   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_applications_status
                ON loan_applications(status);
            CREATE INDEX IF NOT EXISTS idx_disbursements_application
                ON disbursements(application_id);
            """
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    if "customer_data" in data:
        try:
            data["customer_data"] = json.loads(data.get("customer_data") or "{}")
        except (TypeError, json.JSONDecodeError):
            pass
    return data


# ---------------------------------------------------------------------------
# loan_applications
# ---------------------------------------------------------------------------
def record_application(
    thread_id: str,
    customer_data: dict,
    risk_score: float,
    risk_level: str,
    decision: str,
    status: str,
    db_path: Path | None = None,
) -> int:
    """Insert (or refresh) the durable application row for a workflow run.

    Returns the ``loan_applications.id`` (FK target for disbursements).
    """
    now = _now_iso()
    blob = json.dumps(customer_data, ensure_ascii=False)
    with _write_lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO loan_applications
                (thread_id, customer_data, risk_score, risk_level, decision,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                customer_data = excluded.customer_data,
                risk_score    = excluded.risk_score,
                risk_level    = excluded.risk_level,
                decision      = excluded.decision,
                status        = excluded.status,
                updated_at    = excluded.updated_at
            """,
            (thread_id, blob, float(risk_score), risk_level, decision, status, now, now),
        )
        row = conn.execute(
            "SELECT id FROM loan_applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return int(row["id"])


def get_application_by_thread(
    thread_id: str, db_path: Path | None = None
) -> dict | None:
    """Fetch the application row for a workflow thread_id (or None)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM loan_applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    return _to_row(row)


def update_application_status(
    thread_id: str,
    status: str,
    decision: str | None = None,
    db_path: Path | None = None,
) -> bool:
    """Update the lifecycle status of the application for a thread_id.

    Returns True when a row was updated, False when the thread_id is unknown
    (e.g. the server restarted before persistence existed).
    """
    now = _now_iso()
    with _write_lock, _connect(db_path) as conn:
        if decision is None:
            cur = conn.execute(
                "UPDATE loan_applications SET status = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (status, now, thread_id),
            )
        else:
            cur = conn.execute(
                "UPDATE loan_applications SET status = ?, decision = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (status, decision, now, thread_id),
            )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# disbursements — the actual general-ledger entry for money movement
# ---------------------------------------------------------------------------
def _next_contract_code(conn: sqlite3.Connection) -> str:
    """Generate HDTD-YYYYMMDD-XXXX where XXXX is the per-day sequence."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    prefix = f"HDTD-{today}-"
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM disbursements WHERE contract_code LIKE ?",
        (prefix + "%",),
    ).fetchone()
    seq = int(row["n"]) + 1
    return f"{prefix}{seq:04d}"


def _ledger_hash(
    application_id: int, contract_code: str, loan_amount: float, disbursed_at: str
) -> str:
    """SHA-256 over the immutable disbursement fields (tamper evidence)."""
    payload = f"{application_id}|{contract_code}|{loan_amount:.2f}|{disbursed_at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_disbursement(
    application_id: int,
    loan_amount: float,
    db_path: Path | None = None,
) -> dict:
    """Write a completed disbursement (contract + ledger entry) for a loan.

    ``application_id`` is the ``loan_applications.id``. Raises ``ValueError``
    if the application row does not exist (no orphan money movement).
    """
    disbursed_at = _now_iso()
    with _write_lock, _connect(db_path) as conn:
        exists = conn.execute(
            "SELECT id FROM loan_applications WHERE id = ?", (application_id,)
        ).fetchone()
        if exists is None:
            raise ValueError(
                f"cannot disburse: loan application {application_id} not found"
            )
        contract_code = _next_contract_code(conn)
        digest = _ledger_hash(application_id, contract_code, loan_amount, disbursed_at)
        cur = conn.execute(
            """
            INSERT INTO disbursements
                (application_id, contract_code, loan_amount, disbursed_at,
                 status, ledger_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                application_id,
                contract_code,
                float(loan_amount),
                disbursed_at,
                DISBURSEMENT_COMPLETED,
                digest,
            ),
        )
        return {
            "id": int(cur.lastrowid),
            "application_id": application_id,
            "contract_code": contract_code,
            "loan_amount": float(loan_amount),
            "disbursed_at": disbursed_at,
            "status": DISBURSEMENT_COMPLETED,
            "ledger_hash": digest,
        }


# ---------------------------------------------------------------------------
# Evidence endpoints backing store
# ---------------------------------------------------------------------------
def list_applications(
    status: str | None = None, db_path: Path | None = None
) -> list[dict]:
    """List loan applications, newest first, optionally filtered by status."""
    with _connect(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM loan_applications WHERE status = ? ORDER BY id DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM loan_applications ORDER BY id DESC"
            ).fetchall()
    return [_to_row(r) for r in rows]


def list_disbursements(db_path: Path | None = None) -> list[dict]:
    """List the actual disbursement ledger, newest first."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM disbursements ORDER BY id DESC"
        ).fetchall()
    return [_to_row(r) for r in rows]
