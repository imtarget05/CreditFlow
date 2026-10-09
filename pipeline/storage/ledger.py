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
import re
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
    local-only installs without psycopg keep working on SQLite.

    ``row_factory=dict_row`` reproduces the ``sqlite3.Row`` access every
    read below relies on (``row['col']`` / ``dict(row)``)."""
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(url, row_factory=dict_row)


def _postgres_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return url
    return ""


def _is_postgres(conn) -> bool:
    """True for a psycopg connection (or its SQLite-dialect adapter below)."""
    return not isinstance(conn, sqlite3.Connection)


def _schema_change_errors() -> tuple[type[BaseException], ...]:
    """Errors swallowed by the idempotent ``ALTER TABLE`` migrations.

    SQLite reports a duplicate column as ``sqlite3.OperationalError``;
    psycopg reports ``psycopg.errors.DuplicateColumn`` (an ``psycopg.Error``).
    Import stays lazy so SQLite-only installs keep working without psycopg.
    """
    errors: list[type[BaseException]] = [sqlite3.OperationalError]
    try:
        import psycopg
    except ImportError:
        pass
    else:
        errors.append(psycopg.Error)
    return tuple(errors)


def _integrity_errors() -> tuple[type[BaseException], ...]:
    """UNIQUE/PK violations in both dialects (disbursement replay race)."""
    errors: list[type[BaseException]] = [sqlite3.IntegrityError]
    try:
        import psycopg
    except ImportError:
        pass
    else:
        errors.append(psycopg.errors.IntegrityError)
    return tuple(errors)


# Conflict targets for the tables written with SQLite's ``INSERT OR REPLACE``
# — the Postgres translation needs them to build ``ON CONFLICT (...)``.
_REPLACE_CONFLICT_TARGET: dict[str, tuple[str, ...]] = {
    "idempotency_keys": ("caller_scope", "idem_key"),
    "outbox_events": ("event_id",),
    "decision_snapshots": ("decision_id",),
}

_INSERT_OR_REPLACE_RE = re.compile(
    r"\s*INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\((.*?)\)\s*(VALUES\b.*)",
    re.IGNORECASE | re.DOTALL,
)


def _to_postgres(sql: str) -> str:
    """Translate SQLite-dialect SQL to the Postgres (psycopg) dialect.

    Only the constructs this module uses are handled — everything else
    passes through unchanged:

    * ``INSERT OR REPLACE`` → ``INSERT … ON CONFLICT (pk) DO UPDATE``;
    * ``INTEGER PRIMARY KEY AUTOINCREMENT`` → ``SERIAL PRIMARY KEY``;
    * ``ADD COLUMN`` → ``ADD COLUMN IF NOT EXISTS`` (idempotent boot);
    * ``REAL`` → ``DOUBLE PRECISION`` (Postgres ``real`` is float4 — it
      would corrupt ``loan_amount`` on read-back and make
      ``verify_disbursement_hash`` report false tampering);
    * ``?`` placeholders → ``%s`` (psycopg paramstyle).
    """
    match = _INSERT_OR_REPLACE_RE.match(sql)
    if match:
        table, columns, values = match.group(1), match.group(2), match.group(3)
        target = _REPLACE_CONFLICT_TARGET.get(table)
        if target is None:
            raise ValueError(f"no Postgres conflict target for table {table!r}")
        names = [c.strip() for c in columns.split(",")]
        updates = ", ".join(f"{c} = excluded.{c}" for c in names if c not in target)
        sql = (
            f"INSERT INTO {table} ({columns}) {values} "
            f"ON CONFLICT ({', '.join(target)}) DO UPDATE SET {updates}"
        )
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    sql = sql.replace("ADD COLUMN ", "ADD COLUMN IF NOT EXISTS ")
    return sql.replace("?", "%s")


class _PostgresConnection:
    """Adapter serving this module's SQLite-dialect SQL over psycopg.

    Every ``conn.execute(...)`` call site below keeps its SQLite SQL
    verbatim (the SQLite path is byte-identical); statements are translated
    on the way to the wire. Commit/rollback/close delegate to the psycopg
    connection — its context manager commits *and* closes, so the
    ``with _connect(...) as conn:`` pattern neither leaks connections nor
    loses writes on Postgres.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params=None):
        return self._conn.execute(_to_postgres(sql), params)

    def executescript(self, script: str) -> None:
        """sqlite3's ``executescript`` (psycopg has none): run statement by
        statement. This module's DDL never embeds ``;`` inside a string
        literal or comment, so a plain split is safe."""
        for statement in script.split(";"):
            lines = [line.strip() for line in statement.splitlines()]
            if not any(line and not line.startswith("--") for line in lines):
                continue  # empty or comment-only chunk
            self.execute(statement.strip())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _connect(db_path: Path | None = None) -> sqlite3.Connection | _PostgresConnection:
    if db_path is None:
        pg_url = _postgres_url()
        if pg_url:
            return _PostgresConnection(_psycopg_connect(pg_url))
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

            CREATE TABLE IF NOT EXISTS application_documents (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id  TEXT NOT NULL,             -- loan_applications.application_id
                document_id     TEXT NOT NULL UNIQUE,
                document_type   TEXT NOT NULL,
                file_name       TEXT NOT NULL,             -- sanitised base name only, never a path
                uploaded_by     TEXT NOT NULL DEFAULT '',
                uploaded_at     TEXT NOT NULL,
                checksum_sha256 TEXT NOT NULL DEFAULT '',
                file_size_bytes INTEGER NOT NULL DEFAULT 0,
                mime_type       TEXT NOT NULL DEFAULT 'application/pdf',
                storage_path    TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_documents_application
                ON application_documents(application_id);

            CREATE TABLE IF NOT EXISTS decision_snapshots (
                decision_id            TEXT PRIMARY KEY,
                application_id         TEXT NOT NULL,
                thread_id              TEXT NOT NULL,
                decision               TEXT NOT NULL,
                model_version          TEXT NOT NULL,
                model_checksum_sha256  TEXT NOT NULL,
                features_version       TEXT NOT NULL DEFAULT 'v1',
                prediction_probability REAL NOT NULL,
                reviewer               TEXT NOT NULL,
                reason                 TEXT NOT NULL,
                decided_at             TEXT NOT NULL,
                snapshot_hash          TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_decision_snapshots_app
                ON decision_snapshots(application_id);

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
        if _is_postgres(conn):
            # Plan 02 durable extras (jobs/dead_letters/audit_events/…),
            # folded in from the old caller-less init_db_pg(). Runs after the
            # main script so idempotency_keys/outbox_events keep their TEXT
            # timestamps (the durable DDL's IF NOT EXISTS then skips them).
            conn.executescript(_PG_DURABLE_DDL)
        try:
            conn.execute("ALTER TABLE loan_applications ADD COLUMN application_id TEXT")
        except _schema_change_errors():
            pass
        try:
            conn.execute("ALTER TABLE loan_applications ADD COLUMN audit_trail TEXT")
        except _schema_change_errors():
            pass
        for _ddl in (
            "ALTER TABLE loan_applications ADD COLUMN approver_id TEXT",
            "ALTER TABLE loan_applications ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE disbursements ADD COLUMN idempotency_key TEXT",
            "ALTER TABLE application_documents ADD COLUMN checksum_sha256 TEXT DEFAULT ''",
            "ALTER TABLE application_documents ADD COLUMN file_size_bytes INTEGER DEFAULT 0",
            "ALTER TABLE application_documents ADD COLUMN mime_type TEXT DEFAULT 'application/pdf'",
            "ALTER TABLE application_documents ADD COLUMN storage_path TEXT DEFAULT ''",
        ):
            try:
                conn.execute(_ddl)
            except _schema_change_errors():
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

# Postgres-only durable tables (Plan 02). Executed by init_db() when
# DATABASE_URL is a postgres URL — never on SQLite (byte-identical schema).


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


def get_application_by_id(
    app_id: int | str, db_path: Path | None = None
) -> dict | None:
    """Fetch the application row by numeric id, application_id, or thread_id."""
    with _connect(db_path) as conn:
        if isinstance(app_id, int) or (isinstance(app_id, str) and str(app_id).isdigit()):
            row = conn.execute(
                "SELECT * FROM loan_applications WHERE id = ? OR application_id = ? OR thread_id = ?",
                (int(app_id), str(app_id), str(app_id)),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM loan_applications WHERE application_id = ? OR thread_id = ?",
                (str(app_id), str(app_id)),
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


DOCUMENT_TYPES = frozenset(
    {
        "income_verification",
        "identity",
        "bank_statement",
        "collateral",
        "credit_report",
        "other",
    }
)


def sanitise_file_name(raw: str) -> str:
    """Reduce a client-supplied file name to a safe base name.

    Strips directories, rejects empty/blank names and path separators that
    survive basenaming (defence against ``..`` / absolute-path smuggling —
    only metadata is stored, but the name must still be traversal-free).
    Raises ValueError on an unacceptable name.
    """
    import os as _os

    name = (_os.path.basename((raw or "").strip())).strip()
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"invalid file_name: {raw!r}")
    if len(name) > 255:
        raise ValueError("file_name exceeds 255 characters")
    return name


def record_document(
    application_id: str,
    document_id: str,
    document_type: str,
    file_name: str,
    uploaded_by: str = "",
    file_content: bytes | str | None = None,
    file_size_bytes: int | None = None,
    checksum_sha256: str | None = None,
    mime_type: str = "application/pdf",
    db_path: Path | None = None,
) -> dict:
    """Persist one document row for an application with SHA-256 checksum and size.

    Validates document type, sanitises file name, computes cryptographic digest,
    and stores physical artifact if content is provided.
    """
    doc_type = (document_type or "income_verification").strip().lower()
    if doc_type not in DOCUMENT_TYPES:
        raise ValueError(
            f"invalid document_type: {document_type!r}; "
            f"expected one of {', '.join(sorted(DOCUMENT_TYPES))}"
        )
    safe_name = sanitise_file_name(file_name)
    now = _now_iso()

    storage_path = ""
    if file_content is not None:
        content_bytes = (
            file_content.encode("utf-8")
            if isinstance(file_content, str)
            else file_content
        )
        digest = hashlib.sha256(content_bytes).hexdigest()
        size_bytes = len(content_bytes)

        storage_dir = ROOT / "data" / "documents" / application_id
        storage_dir.mkdir(parents=True, exist_ok=True)
        target_file = storage_dir / f"{document_id}_{safe_name}"
        target_file.write_bytes(content_bytes)
        storage_path = str(target_file)
    else:
        digest = checksum_sha256 or hashlib.sha256(
            f"{application_id}:{document_id}:{safe_name}:{now}".encode()
        ).hexdigest()
        size_bytes = file_size_bytes or 0

    with _write_lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO application_documents
                (application_id, document_id, document_type, file_name,
                 uploaded_by, uploaded_at, checksum_sha256, file_size_bytes, mime_type, storage_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (application_id, document_id, doc_type, safe_name, uploaded_by, now, digest, size_bytes, mime_type, storage_path),
        )
    return {
        "document_id": document_id,
        "application_id": application_id,
        "document_type": doc_type,
        "file_name": safe_name,
        "uploaded_by": uploaded_by,
        "uploaded_at": now,
        "checksum_sha256": digest,
        "file_size_bytes": size_bytes,
        "mime_type": mime_type,
    }


def list_documents(
    application_id: str, db_path: Path | None = None
) -> list[dict]:
    """List persisted document rows for an application (oldest first)."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT document_id, application_id, document_type, file_name,
                   uploaded_by, uploaded_at, checksum_sha256, file_size_bytes, mime_type
            FROM application_documents
            WHERE application_id = ?
            ORDER BY id ASC
            """,
            (application_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def compute_decision_snapshot_hash(
    decision_id: str,
    application_id: str,
    decision: str,
    model_version: str,
    model_checksum_sha256: str,
    prediction_probability: float,
    reviewer: str,
    decided_at: str,
) -> str:
    body = (
        f"{decision_id}|{application_id}|{decision}|{model_version}|"
        f"{model_checksum_sha256}|{prediction_probability:.4f}|{reviewer}|{decided_at}"
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def record_decision_snapshot(
    application_id: str,
    thread_id: str,
    decision: str,
    model_version: str,
    model_checksum_sha256: str,
    prediction_probability: float,
    reviewer: str,
    reason: str,
    features_version: str = "v1",
    decision_id: str | None = None,
    decided_at: str | None = None,
    db_path: Path | None = None,
) -> dict:
    import uuid
    dec_id = decision_id or f"dec_{uuid.uuid4().hex[:12]}"
    now = decided_at or _now_iso()
    snapshot_hash = compute_decision_snapshot_hash(
        dec_id, application_id, decision, model_version,
        model_checksum_sha256, prediction_probability, reviewer, now
    )

    with _write_lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO decision_snapshots
                (decision_id, application_id, thread_id, decision, model_version,
                 model_checksum_sha256, features_version, prediction_probability,
                 reviewer, reason, decided_at, snapshot_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dec_id, application_id, thread_id, decision, model_version,
                model_checksum_sha256, features_version, prediction_probability,
                reviewer, reason, now, snapshot_hash
            ),
        )
        conn.commit()

    return {
        "decision_id": dec_id,
        "application_id": application_id,
        "thread_id": thread_id,
        "decision": decision,
        "model_version": model_version,
        "model_checksum_sha256": model_checksum_sha256,
        "features_version": features_version,
        "prediction_probability": prediction_probability,
        "reviewer": reviewer,
        "reason": reason,
        "decided_at": now,
        "snapshot_hash": snapshot_hash,
    }


def get_decision_snapshot(
    app_or_decision_id: str, db_path: Path | None = None
) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT decision_id, application_id, thread_id, decision, model_version,
                   model_checksum_sha256, features_version, prediction_probability,
                   reviewer, reason, decided_at, snapshot_hash
            FROM decision_snapshots
            WHERE application_id = ? OR decision_id = ? OR thread_id = ?
            ORDER BY decided_at DESC LIMIT 1
            """,
            (app_or_decision_id, app_or_decision_id, app_or_decision_id),
        ).fetchone()
    return dict(row) if row else None


def verify_decision_snapshot_hash(snapshot: dict) -> bool:
    expected = compute_decision_snapshot_hash(
        snapshot["decision_id"],
        snapshot["application_id"],
        snapshot["decision"],
        snapshot["model_version"],
        snapshot["model_checksum_sha256"],
        float(snapshot["prediction_probability"]),
        snapshot["reviewer"],
        snapshot["decided_at"],
    )
    return hmac.compare_digest(expected, snapshot.get("snapshot_hash", ""))


def emit_domain_event(
    event_type: str,
    payload: dict,
    destination: str = "domain_events",
    db_path: Path | None = None,
) -> str:
    import uuid
    event_id = f"evt_{event_type}_{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    with _write_lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO outbox_events
                (event_id, destination, payload, version, attempts, next_retry_at, delivered_at)
            VALUES (?, ?, ?, 'v1', 0, '', '')
            """,
            (
                event_id,
                destination,
                json.dumps({"event_type": event_type, "occurred_at": now, **payload}, default=str),
            ),
        )
        conn.commit()
    return event_id


def get_audit_trail_record(    app_or_thread_id: str, db_path: Path | None = None
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
        insert_sql = """
                INSERT INTO disbursements
                    (application_id, contract_code, loan_amount, disbursed_at,
                     status, ledger_hash, idempotency_key)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """
        insert_params = (
            application_id,
            contract_code,
            float(loan_amount),
            disbursed_at,
            DISBURSEMENT_COMPLETED,
            digest,
            idempotency_key,
        )
        if _is_postgres(conn):
            # psycopg has no cursor.lastrowid — ask Postgres for the new id.
            insert_sql += " RETURNING id"
        try:
            cur = conn.execute(insert_sql, insert_params)
        except _integrity_errors():
            # Lost a concurrent race: the winner's row is the single truth.
            if _is_postgres(conn):
                # The error aborted the Postgres transaction — roll it back
                # so the winner lookup below can execute.
                conn.rollback()
            row = conn.execute(
                "SELECT * FROM disbursements WHERE application_id = ?",
                (application_id,),
            ).fetchone()
            return _to_row(row)
        new_id = (
            int(cur.fetchone()["id"]) if _is_postgres(conn) else int(cur.lastrowid)
        )
        return {
            "id": new_id,
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
