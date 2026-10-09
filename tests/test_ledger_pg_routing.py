"""Neon routing for the ledger: postgres DATABASE_URL goes to psycopg,
explicit file paths and empty env stay on SQLite (stdlib only).

Extended for the user-approved "Neon free + sửa ledger" decision — a *real*
``postgresql://`` DATABASE_URL must boot and write, not just route:

* ``init_db()`` must not use the sqlite3-only ``executescript`` on psycopg
  (today it raises ``AttributeError: 'Connection' object has no attribute
  'executescript'``, so setting DATABASE_URL crashes API boot);
* SQL on the wire must be Postgres-dialect: no ``INSERT OR REPLACE``, no
  ``?`` placeholders (psycopg wants ``%s``), no ``AUTOINCREMENT``/``REAL``
  (float4 would break the ledger hash round-trip → false tamper alarms);
* error-tolerant paths must catch psycopg errors, not only sqlite3 ones;
* ``init_db_pg()``'s durable DDL (jobs/dead_letters/audit_events/…) is
  folded into ``init_db()`` for Postgres and must NOT appear on SQLite.

No live database is required: psycopg-shaped connection doubles record the
statements that would hit the wire.
"""
import re
import sqlite3

import psycopg
import pytest

from pipeline.storage import ledger

# ---------------------------------------------------------------------------
# Existing routing contract: postgres URL → psycopg, file/empty env → sqlite
# ---------------------------------------------------------------------------


def test_connect_routes_postgres_url_to_psycopg(monkeypatch):
    calls = {}

    class FakeConn:
        def close(self):
            calls["closed"] = True

    def fake_connect(url):
        calls["url"] = url
        return FakeConn()

    monkeypatch.setattr(ledger, "_psycopg_connect", fake_connect)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://u:p@host/db?sslmode=require"
    )
    conn = ledger._connect()
    conn.close()
    assert calls["url"].startswith("postgresql://")
    assert calls.get("closed") is True


def test_connect_keeps_sqlite_when_explicit_path(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = tmp_path / "ledger.db"
    with ledger._connect(path) as conn:
        assert isinstance(conn, sqlite3.Connection)


def test_connect_keeps_sqlite_when_no_database_url(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(tmp_path / "ledger.db"))
    with ledger._connect() as conn:
        assert isinstance(conn, sqlite3.Connection)


# ---------------------------------------------------------------------------
# psycopg-shaped connection double (dict rows, commit/rollback/close,
# NO executescript, NO lastrowid — exactly what psycopg provides)
# ---------------------------------------------------------------------------
class FakePgCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    @property
    def rowcount(self):
        return len(self.rows)

    # deliberately no `lastrowid`: psycopg cursors have none


class FakePgConn:
    """A psycopg duck-type: execute/commit/rollback/close only."""

    def __init__(self, responder=None):
        self.statements = []  # (sql, params) after dialect translation
        self.events = []  # ordered log: ("execute", sql) / ("rollback", None)…
        self.committed = 0
        self.rolled_back = 0
        self.closed = False
        self._responder = responder

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        self.events.append(("execute", sql))
        rows = [] if self._responder is None else self._responder(sql, params)
        return FakePgCursor(rows)

    def commit(self):
        self.committed += 1
        self.events.append(("commit", None))

    def rollback(self):
        self.rolled_back += 1
        self.events.append(("rollback", None))

    def close(self):
        self.closed = True
        self.events.append(("close", None))

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # psycopg semantics: commit (rollback on error), then close.
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()
        return False


def use_fake_postgres(monkeypatch, responder=None):
    """Route DATABASE_URL → psycopg into a FakePgConn double."""
    fake = FakePgConn(responder)
    monkeypatch.setattr(ledger, "_psycopg_connect", lambda url: fake)
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql://user:pw@localhost:5432/creditflow"
    )
    return fake


def statements_containing(fake, needle):
    return [sql for sql, _ in fake.statements if needle in sql]


def assert_postgres_dialect(sql, params=None):
    """Nothing sqlite-only may reach the psycopg wire."""
    assert "INSERT OR REPLACE" not in sql, sql
    assert "AUTOINCREMENT" not in sql, sql
    assert "?" not in sql, sql
    assert not re.search(r"\bREAL\b", sql), sql
    if params is not None:
        assert sql.count("%s") == len(params), (sql, params)


# ---------------------------------------------------------------------------
# (a) init_db() boots on a psycopg connection
# ---------------------------------------------------------------------------
def test_init_db_boots_on_postgres_without_executescript(monkeypatch):
    fake = use_fake_postgres(monkeypatch)
    # The double is psycopg-shaped on purpose: no executescript attribute.
    assert not hasattr(fake, "executescript")

    ledger.init_db()  # pre-fix: AttributeError: no attribute 'executescript'

    all_sql = "\n".join(sql for sql, _ in fake.statements)
    for table in (
        "loan_applications",
        "disbursements",
        "inference_logs",
        "application_documents",
        "decision_snapshots",
        "idempotency_keys",
        "outbox_events",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in all_sql, table
    # Durable extras formerly behind the caller-less init_db_pg().
    for table in ("jobs", "dead_letters", "audit_events", "model_registry"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in all_sql, table
    # Identity + numeric types translated for Postgres.
    assert "SERIAL PRIMARY KEY" in all_sql
    assert "DOUBLE PRECISION" in all_sql
    for sql, params in fake.statements:
        assert_postgres_dialect(sql, params)
    assert fake.committed == 1 and fake.closed is True


def test_init_db_postgres_add_column_is_idempotent_on_the_wire(monkeypatch):
    fake = use_fake_postgres(monkeypatch)
    ledger.init_db()
    alters = [
        sql
        for sql, _ in fake.statements
        if sql.strip().upper().startswith("ALTER TABLE")
    ]
    assert alters, "init_db issued no ALTER TABLE migrations"
    for sql in alters:
        assert "ADD COLUMN IF NOT EXISTS" in sql, sql


def test_init_db_tolerates_postgres_duplicate_column_errors(monkeypatch):
    def responder(sql, params):
        if sql.strip().upper().startswith("ALTER TABLE"):
            raise psycopg.errors.DuplicateColumn(
                'column "application_id" of relation "loan_applications" '
                "already exists"
            )
        return []

    fake = use_fake_postgres(monkeypatch, responder)
    ledger.init_db()  # must not raise: sqlite3.OperationalError alone misses this
    # the migration loop kept going after the swallowed errors
    assert statements_containing(fake, "idx_disb_app_uid")
    assert statements_containing(fake, "idx_applications_app_id")


def test_sqlite_schema_is_unchanged_by_the_postgres_work(tmp_path):
    path = tmp_path / "ledger.db"
    ledger.init_db(path)
    with sqlite3.connect(path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        app_ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='loan_applications'"
        ).fetchone()[0]
    assert {
        "loan_applications",
        "disbursements",
        "inference_logs",
        "application_documents",
        "decision_snapshots",
        "idempotency_keys",
        "outbox_events",
    } <= tables
    # Postgres-only durable extras must not leak into the SQLite schema…
    assert "jobs" not in tables
    assert "audit_events" not in tables
    assert "dead_letters" not in tables
    # …and SQLite keeps its native identity syntax.
    assert "AUTOINCREMENT" in app_ddl
    assert "SERIAL" not in app_ddl


# ---------------------------------------------------------------------------
# (b) upsert / write paths under both dialects
# ---------------------------------------------------------------------------
def test_upsert_writes_speak_postgres_dialect(monkeypatch):
    fake = use_fake_postgres(monkeypatch)
    ledger.record_idempotent_approval("thread-1", "key-1", {"status": "APPROVED"})
    ledger.record_decision_snapshot(
        "app-1", "thread-1", "APPROVE", "m1", "c" * 64, 0.42, "rev", "ok"
    )
    ledger.emit_domain_event("LoanApproved", {"thread_id": "thread-1"})

    for sql, params in fake.statements:
        assert_postgres_dialect(sql, params)
    idem = statements_containing(fake, "INTO idempotency_keys")[0]
    assert "ON CONFLICT (caller_scope, idem_key) DO UPDATE" in idem
    outbox = statements_containing(fake, "INTO outbox_events")[0]
    assert "ON CONFLICT (event_id) DO UPDATE" in outbox
    snap = statements_containing(fake, "INTO decision_snapshots")[0]
    assert "ON CONFLICT (decision_id) DO UPDATE" in snap


def test_read_paths_serve_percent_s_placeholders(monkeypatch):
    def responder(sql, params):
        if "FROM idempotency_keys" in sql:
            return [{"response": '{"ok": true}'}]
        if "FROM loan_applications" in sql:
            return [{"id": 3, "thread_id": "thread-1", "customer_data": "{}"}]
        return []

    fake = use_fake_postgres(monkeypatch, responder)
    assert ledger.find_idempotent_approval("thread-1", "key-1") == {"ok": True}
    app = ledger.get_application_by_thread("thread-1")
    assert app["id"] == 3 and app["customer_data"] == {}
    for sql, params in fake.statements:
        assert_postgres_dialect(sql, params)


def test_postgres_translation_helpers():
    upsert = ledger._to_postgres(
        "INSERT OR REPLACE INTO outbox_events "
        "(event_id, destination, payload, version, attempts, next_retry_at, delivered_at) "
        "VALUES (?, ?, ?, 'v1', 0, '', '')"
    )
    assert upsert.startswith("INSERT INTO outbox_events")
    assert "INSERT OR REPLACE" not in upsert
    assert (
        "ON CONFLICT (event_id) DO UPDATE SET destination = excluded.destination"
        in upsert
    )
    assert upsert.count("%s") == 3
    assert "?" not in upsert

    ddl = ledger._to_postgres(
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, amount REAL, note TEXT)"
    )
    assert "SERIAL PRIMARY KEY" in ddl
    assert "AUTOINCREMENT" not in ddl
    assert "DOUBLE PRECISION" in ddl
    assert not re.search(r"\bREAL\b", ddl)

    alter = ledger._to_postgres("ALTER TABLE t ADD COLUMN c TEXT DEFAULT ''")
    assert "ADD COLUMN IF NOT EXISTS c" in alter


def test_psycopg_connect_configures_dict_rows(monkeypatch):
    """Every read does row['col']/dict(row) — sqlite3.Row behaviour must be
    reproduced on psycopg via row_factory=dict_row."""
    captured = {}

    def fake_psycopg_connect(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(psycopg, "connect", fake_psycopg_connect)
    ledger._psycopg_connect("postgresql://u:p@host/db")
    from psycopg.rows import dict_row

    assert captured["url"] == "postgresql://u:p@host/db"
    assert captured.get("row_factory") is dict_row


# ---------------------------------------------------------------------------
# (c) error-tolerant paths catch psycopg errors
# ---------------------------------------------------------------------------
def test_disbursement_race_recovers_after_psycopg_integrity_error(monkeypatch):
    winner = {
        "id": 3,
        "application_id": 7,
        "contract_code": "HDTD-20261009-0001",
        "loan_amount": 1000.0,
        "disbursed_at": "2026-10-09T00:00:00+00:00",
        "status": "COMPLETED",
        "ledger_hash": "0" * 64,
        "idempotency_key": None,
    }
    state = {"won": False}

    def responder(sql, params):
        if "INSERT INTO disbursements" in sql:
            state["won"] = True
            raise psycopg.errors.UniqueViolation(
                'duplicate key value violates unique constraint '
                '"disbursements_application_id_key"'
            )
        if "COUNT(*)" in sql:
            return [{"n": 0}]
        if "FROM loan_applications" in sql:
            return [{"id": 7}]
        if "FROM disbursements" in sql:
            return [winner] if state["won"] else []
        return []

    fake = use_fake_postgres(monkeypatch, responder)
    row = ledger.record_disbursement(7, 1000.0)  # pre-fix: UniqueViolation escapes
    assert row["contract_code"] == "HDTD-20261009-0001"

    # Postgres needs a rollback (aborted transaction) before the winner lookup.
    rollback_at = fake.events.index(("rollback", None))
    disbursement_selects = [
        i
        for i, ev in enumerate(fake.events)
        if ev[0] == "execute"
        and "FROM disbursements" in ev[1]
        and "COUNT(*)" not in ev[1]  # contract-code counter is not a row read
    ]
    assert len(disbursement_selects) == 2  # replay check + post-race lookup
    assert disbursement_selects[1] > rollback_at


def test_disbursement_insert_uses_returning_id_on_postgres(monkeypatch):
    def responder(sql, params):
        if "INSERT INTO disbursements" in sql:
            return [{"id": 42}]
        if "COUNT(*)" in sql:
            return [{"n": 0}]
        if "FROM loan_applications" in sql:
            return [{"id": 7}]
        if "FROM disbursements" in sql:
            return []
        return []

    fake = use_fake_postgres(monkeypatch, responder)
    row = ledger.record_disbursement(7, 1000.0)  # pre-fix: cur.lastrowid → AttributeError
    assert row["id"] == 42
    insert, params = next(
        (sql, p) for sql, p in fake.statements if "INSERT INTO disbursements" in sql
    )
    assert "RETURNING id" in insert
    assert_postgres_dialect(insert, params)
