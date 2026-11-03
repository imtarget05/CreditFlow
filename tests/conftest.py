"""Shared pytest configuration for CreditFlow tests."""
import pytest


@pytest.fixture(autouse=True)
def _explicit_anonymous_workflow_demo(monkeypatch):
    """Legacy synthetic workflow tests opt in to the isolated demo plane."""
    monkeypatch.setenv("CREDITFLOW_ALLOW_ANONYMOUS_DEMO", "1")


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
