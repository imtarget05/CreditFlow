"""Neon routing for the ledger: postgres DATABASE_URL goes to psycopg,
explicit file paths and empty env stay on SQLite (stdlib only)."""
import sqlite3

from pipeline.storage import ledger


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
