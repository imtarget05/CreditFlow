"""Shared pytest configuration for CreditFlow tests."""
import os

import pytest

# Production storage requires an encryption key; tests using DATABASE_URL
# (Postgres) need one set so writes are not fail-closed.
if os.environ.get("DATABASE_URL", "").startswith("postgresql://"):
    os.environ.setdefault(
        "CREDITFLOW_ENCRYPTION_KEY",
        "creditflow-test-encryption-key-32chars!!",
    )


@pytest.fixture(autouse=True)
def _explicit_anonymous_workflow_demo(monkeypatch):
    """Legacy synthetic workflow tests opt in to the isolated demo plane."""
    monkeypatch.setenv("CREDITFLOW_ALLOW_ANONYMOUS_DEMO", "1")


@pytest.fixture(autouse=True)
def _clean_postgres_tables():
    """Truncate shared Postgres tables after each test for test isolation.

    Only active when DATABASE_URL points at Postgres. Without this, every test
    shares the same database and data from one test leaks into the next.
    """
    yield
    if os.environ.get("DATABASE_URL", "").startswith("postgresql://"):
        from pipeline.storage.ledger import _connect

        with _connect() as conn:
            for table in (
                "audit_events",
                "decision_snapshots",
                "application_documents",
                "disbursements",
                "idempotency_keys",
                "outbox_events",
                "inference_logs",
                "loan_applications",
                "jobs",
                "dead_letters",
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:  # noqa: BLE001, S110  # table may not exist yet — skip
                    pass
            conn.commit()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: marks tests that require live network access"
    )
    config.addinivalue_line(
        "markers", "slow: marks tests that take tens of seconds (full workflow runs)"
    )
    config.addinivalue_line(
        "markers", "load: marks load/concurrency tests (e.g. approve path under load)"
    )
