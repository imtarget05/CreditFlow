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

``ledger_hash`` is a SHA-256 over the four hashed disbursement fields
(application_id, contract_code, loan_amount, disbursed_at). It is **tamper
evidence, not immutability**: SQLite rows can still be edited or deleted by
anyone with file access, but recomputing the digest (``verify_disbursement_hash``
— surfaced as ``hash_valid`` by ``list_disbursements`` / ``GET /disbursements``)
detects an edit of any hashed field. ``status`` and ``id`` are outside the digest.
Demo scope: single-file SQLite, stdlib only.
"""
from __future__ import annotations

import hashlib
import hmac
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


def _psycopg_connect(url: str):
    """Postgres connection for Neon staging (Plan 02). Import is lazy so
    local-only installs without psycopg keep working on SQLite."""
    import psycopg

    return psycopg.connect(url)


def _postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return url
    return ""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    if db_path is None:
        pg_url = _postgres_url()
        if pg_url:
            return _psycopg_connect(pg_url)
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
                application_id TEXT,
                customer_data TEXT NOT NULL,           -- JSON blob
                risk_score    REAL,
                risk_level    TEXT,
                decision      TEXT,
                status        TEXT NOT NULL CHECK (
                    status IN ('PENDING_REVIEW', 'APPROVED', 'REJECTED')
                ),
                audit_trail   TEXT,                    -- JSON blob
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS disbursements (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER NOT NULL UNIQUE REFERENCES loan_applications(id),
                contract_code TEXT NOT NULL UNIQUE,
                loan_amount   REAL NOT NULL,
                disbursed_at  TEXT NOT NULL,
                status        TEXT NOT NULL CHECK (status = 'COMPLETED'),
                ledger_hash   TEXT NOT NULL,
                idempotency_key TEXT
            );

            CREATE TABLE IF NOT EXISTS inference_logs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at        TEXT NOT NULL,
                income            REAL,
                loan_amount       REAL,
                existing_debt     REAL,
                age               INTEGER,
                employment_years  REAL,
                loan_term         INTEGER,
                credit_history    REAL,
                previous_defaults INTEGER,
                risk_probability  REAL
            );

            CREATE INDEX IF NOT EXISTS idx_applications_status
                ON loan_applications(status);
            CREATE INDEX IF NOT EXISTS idx_disbursements_application
                ON disbursements(application_id);
            CREATE INDEX IF NOT EXISTS idx_inference_logs_id
                ON inference_logs(id DESC);

            CREATE TABLE IF NOT EXISTS idempotency_keys (
                caller_scope TEXT NOT NULL,
                idem_key     TEXT NOT NULL,
                fingerprint  TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'pending',
                response     TEXT NOT NULL DEFAULT '',
                expires_at   TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (caller_scope, idem_key)
            );

            CREATE TABLE IF NOT EXISTS outbox_events (
                event_id      TEXT PRIMARY KEY,
                destination   TEXT NOT NULL DEFAULT '',
                payload       TEXT NOT NULL DEFAULT '',
                version       TEXT NOT NULL DEFAULT 'v1',
                attempts      INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT NOT NULL DEFAULT '',
                delivered_at  TEXT NOT NULL DEFAULT ''
            );
            """
        )
        try:
            conn.execute("ALTER TABLE loan_applications ADD COLUMN application_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE loan_applications ADD COLUMN audit_trail TEXT")
        except sqlite3.OperationalError:
            pass
        for _ddl in (
            "ALTER TABLE loan_applications ADD COLUMN approver_id TEXT",
            "ALTER TABLE loan_applications ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE disbursements ADD COLUMN idempotency_key TEXT",
        ):
            try:
                conn.execute(_ddl)
            except sqlite3.OperationalError:
                pass
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_disb_app_uid ON disbursements(application_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_applications_app_id ON loan_applications(application_id)")


_PG_DURABLE_DDL = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    caller_scope TEXT NOT NULL,
    idem_key     TEXT NOT NULL,
    fingerprint  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    response     TEXT NOT NULL DEFAULT '',
    expires_at   TIMESTAMPTZ,
    PRIMARY KEY (caller_scope, idem_key)
);
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    kind             TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'queued',
    attempt          INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    input_ref        TEXT NOT NULL DEFAULT '',
    result_ref       TEXT NOT NULL DEFAULT '',
    error_class      TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox_events (
    event_id      TEXT PRIMARY KEY,
    destination   TEXT NOT NULL DEFAULT '',
    payload       TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT 'v1',
    attempts      INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    delivered_at  TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS dead_letters (
    job_id          TEXT PRIMARY KEY,
    input_ref       TEXT NOT NULL DEFAULT '',
    diagnosis       TEXT NOT NULL DEFAULT '',
    owner           TEXT NOT NULL DEFAULT '',
    replay_decision TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS audit_events (
    id         BIGSERIAL PRIMARY KEY,
    actor      TEXT NOT NULL DEFAULT '',
    action     TEXT NOT NULL DEFAULT '',
    object     TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    outcome    TEXT NOT NULL DEFAULT '',
    reason     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS model_registry (
    version        TEXT PRIMARY KEY,
    fingerprint    TEXT NOT NULL DEFAULT '',
    corpus_version TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'staged',
    promoted_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);
CREATE INDEX IF NOT EXISTS idx_outbox_next_retry ON outbox_events (next_retry_at)
    WHERE delivered_at IS NULL;
"""


def init_db_pg() -> None:
    """Create the shared durable tables on Neon (Plan 02). SQLite DDL in
    init_db() is untouched; this runs only against postgres."""
    pg_url = _postgres_url()
    if not pg_url:
        raise RuntimeError("DATABASE_URL must be a postgresql:// URL for init_db_pg")
    conn = _psycopg_connect(pg_url)
    try:
        with conn.cursor() as cur:
            cur.execute(_PG_DURABLE_DDL)
        conn.commit()
    finally:
        conn.close()


def find_idempotent_approval(
    thread_id: str, idempotency_key: str, db_path: Path | None = None
) -> dict | None:
    """Return the stored approval response for a replayed (thread, key).

    Empty keys never match. Uses the shared idempotency_keys table with
    caller_scope 'approval:<thread_id>' so a key can never approve a
    different thread.
    """
    if not (idempotency_key or "").strip():
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT response FROM idempotency_keys "
            "WHERE caller_scope = ? AND idem_key = ? AND status = 'completed'",
            (f"approval:{thread_id}", idempotency_key.strip()),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["response"])


def record_idempotent_approval(
    thread_id: str,
    idempotency_key: str,
    response: dict,
    db_path: Path | None = None,
) -> None:
    """Persist the terminal approval response for future replays, plus an
    outbox event in the same transaction."""
    body = json.dumps(response, default=str)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO idempotency_keys "
            "(caller_scope, idem_key, fingerprint, status, response, expires_at) "
            "VALUES (?, ?, ?, 'completed', ?, '')",
            (
                f"approval:{thread_id}",
                idempotency_key.strip(),
                thread_id,
                body,
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO outbox_events "
            "(event_id, destination, payload, version, attempts, next_retry_at, delivered_at) "
            "VALUES (?, 'approvals', ?, 'v1', 0, '', '')",
            (f"approval-done:{thread_id}:{idempotency_key.strip()}", body),
        )
        conn.commit()


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
    if "audit_trail" in data and isinstance(data["audit_trail"], str):
        try:
            data["audit_trail"] = json.loads(data["audit_trail"] or "[]")
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
    application_id: str | None = None,
    audit_trail: list | None = None,
    db_path: Path | None = None,
) -> int:
    """Insert (or refresh) the durable application row for a workflow run.

    Returns the ``loan_applications.id`` (FK target for disbursements).
    """
    now = _now_iso()
    blob = json.dumps(customer_data, ensure_ascii=False)
    trail_blob = json.dumps(audit_trail or [], ensure_ascii=False)
    with _write_lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO loan_applications
                (thread_id, application_id, customer_data, risk_score, risk_level, decision,
                 status, audit_trail, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                application_id = COALESCE(excluded.application_id, loan_applications.application_id),
                customer_data  = excluded.customer_data,
                risk_score     = excluded.risk_score,
                risk_level     = excluded.risk_level,
                decision       = excluded.decision,
                status         = excluded.status,
                audit_trail    = excluded.audit_trail,
                updated_at     = excluded.updated_at
            """,
            (thread_id, application_id, blob, float(risk_score), risk_level, decision, status, trail_blob, now, now),
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
    audit_trail: list | None = None,
    db_path: Path | None = None,
) -> bool:
    """Update the lifecycle status of the application for a thread_id.

    Returns True when a row was updated, False when the thread_id is unknown
    (e.g. the server restarted before persistence existed).
    """
    now = _now_iso()
    trail_blob = json.dumps(audit_trail or [], ensure_ascii=False) if audit_trail is not None else None
    with _write_lock, _connect(db_path) as conn:
        if decision is None and trail_blob is None:
            cur = conn.execute(
                "UPDATE loan_applications SET status = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (status, now, thread_id),
            )
        elif decision is not None and trail_blob is not None:
            cur = conn.execute(
                "UPDATE loan_applications SET status = ?, decision = ?, audit_trail = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (status, decision, trail_blob, now, thread_id),
            )
        elif decision is not None:
            cur = conn.execute(
                "UPDATE loan_applications SET status = ?, decision = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (status, decision, now, thread_id),
            )
        else:
            cur = conn.execute(
                "UPDATE loan_applications SET status = ?, audit_trail = ?, updated_at = ? "
                "WHERE thread_id = ?",
                (status, trail_blob, now, thread_id),
            )
        return cur.rowcount > 0


def get_audit_trail_record(
    app_or_thread_id: str, db_path: Path | None = None
) -> dict | None:
    """Fetch audit trail for an application_id or thread_id in O(1)."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT id, thread_id, application_id, decision, status, audit_trail
            FROM loan_applications
            WHERE application_id = ? OR thread_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (app_or_thread_id, app_or_thread_id),
        ).fetchone()
    if row is None:
        return None
    res = dict(row)
    if res.get("audit_trail"):
        try:
            res["audit_trail"] = json.loads(res["audit_trail"])
        except (TypeError, json.JSONDecodeError):
            res["audit_trail"] = []
    else:
        res["audit_trail"] = []
    return res


def record_inference(
    record: dict, db_path: Path | None = None
) -> None:
    """Store an inference sample for drift monitoring."""
    now = _now_iso()
    with _write_lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO inference_logs
                (created_at, income, loan_amount, existing_debt, age,
                 employment_years, loan_term, credit_history, previous_defaults,
                 risk_probability)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                float(record.get("income", 0)),
                float(record.get("loan_amount", 0)),
                float(record.get("existing_debt", 0)),
                int(record.get("age", 0)),
                float(record.get("employment_years", 0)),
                int(record.get("loan_term", 0)),
                float(record.get("credit_history", 0)),
                int(record.get("previous_defaults", 0)),
                float(record.get("risk_probability", 0.0)),
            ),
        )


def list_recent_inferences(limit: int = 500, db_path: Path | None = None) -> list[dict]:
    """Return recent inference logs for drift monitoring."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT income, loan_amount, existing_debt, age,
                   employment_years, loan_term, credit_history, previous_defaults,
                   risk_probability
            FROM inference_logs
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]
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
    """SHA-256 over the four hashed disbursement fields (tamper evidence)."""
    payload = f"{application_id}|{contract_code}|{loan_amount:.2f}|{disbursed_at}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_disbursement_hash(record: dict) -> bool:
    """Recompute the ledger digest of a disbursement row and compare.

    Returns True when the stored ``ledger_hash`` still matches the four hashed
    fields — i.e. the row has not been edited. Returns False when it was edited
    (or the digest is missing/malformed). This is the mechanism behind every
    "tamper-evident" claim: without it a hash is only a stored string.
    """
    if not record or not record.get("ledger_hash"):
        return False
    try:
        expected = _ledger_hash(
            int(record["application_id"]),
            str(record["contract_code"]),
            float(record["loan_amount"]),
            str(record["disbursed_at"]),
        )
    except (KeyError, TypeError, ValueError):
        return False
    return hmac.compare_digest(str(record["ledger_hash"]), expected)


def record_disbursement(
    application_id: int,
    loan_amount: float,
    db_path: Path | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """Write a completed disbursement (contract + ledger entry) for a loan.

    ``application_id`` is the ``loan_applications.id``. Raises ``ValueError``
    if the application row does not exist (no orphan money movement).
    The ``UNIQUE(application_id)`` constraint guarantees replay safety:
    a second approval for the same application returns the existing row
    instead of writing duplicate money movement.
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
        # Replay-safe: same application -> same row, no duplicate money.
        if idempotency_key:
            row = conn.execute(
                "SELECT * FROM disbursements WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                return _to_row(row)
        row = conn.execute(
            "SELECT * FROM disbursements WHERE application_id = ?",
            (application_id,),
        ).fetchone()
        if row is not None:
            return _to_row(row)
        contract_code = _next_contract_code(conn)
        digest = _ledger_hash(application_id, contract_code, loan_amount, disbursed_at)
        try:
            cur = conn.execute(
                """
                INSERT INTO disbursements
                    (application_id, contract_code, loan_amount, disbursed_at,
                     status, ledger_hash, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    application_id,
                    contract_code,
                    float(loan_amount),
                    disbursed_at,
                    DISBURSEMENT_COMPLETED,
                    digest,
                    idempotency_key,
                ),
            )
        except sqlite3.IntegrityError:
            # Lost a concurrent race: the winner's row is the single truth.
            row = conn.execute(
                "SELECT * FROM disbursements WHERE application_id = ?",
                (application_id,),
            ).fetchone()
            return _to_row(row)
        return {
            "id": int(cur.lastrowid),
            "application_id": application_id,
            "contract_code": contract_code,
            "loan_amount": float(loan_amount),
            "disbursed_at": disbursed_at,
            "status": DISBURSEMENT_COMPLETED,
            "ledger_hash": digest,
            "idempotency_key": idempotency_key,
        }


def approve_pending_application(
    thread_id: str,
    approver_id: str,
    idempotency_key: str,
    db_path: Path | None = None,
) -> dict:
    """Reserve a PENDING_REVIEW application for approval (one transaction).

    Atomically transitions ``PENDING_REVIEW -> APPROVED`` only when the row
    is still pending and records approver identity + idempotency key.
    Returns ``{"application_id": int, "replay": bool}`` where ``replay``
    is True when the same idempotency key was already recorded.
    Raises ``LookupError`` for unknown/non-pending threads and
    ``ValueError`` when a *different* key retries an approved application.
    """
    if not approver_id or not idempotency_key:
        raise ValueError("approver_id and idempotency_key are required")
    now = _now_iso()
    with _write_lock, _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM loan_applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"unknown application thread {thread_id}")
        data = dict(row)
        if data.get("idempotency_key") == idempotency_key and data.get("status") == STATUS_APPROVED:
            return {"application_id": int(data["id"]), "replay": True}
        if data.get("status") != STATUS_PENDING_REVIEW:
            raise LookupError(f"application {thread_id} is not pending review")
        cur = conn.execute(
            "UPDATE loan_applications SET status = ?, approver_id = ?, "
            "idempotency_key = ?, updated_at = ? "
            "WHERE thread_id = ? AND status = ?",
            (STATUS_APPROVED, approver_id, idempotency_key, now, thread_id, STATUS_PENDING_REVIEW),
        )
        if cur.rowcount == 0:
            raise LookupError(f"application {thread_id} is not pending review")
        return {"application_id": int(data["id"]), "replay": False}


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
    """List the actual disbursement ledger, newest first.

    Every row carries ``hash_valid``: the stored SHA-256 recomputed from the
    hashed fields. ``hash_valid: false`` means the row was edited after being
    written (tamper evidence).
    """
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM disbursements ORDER BY id DESC"
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        row = _to_row(r)
        row["hash_valid"] = verify_disbursement_hash(row)
        out.append(row)
    return out
