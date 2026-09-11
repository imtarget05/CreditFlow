"""Shared pytest configuration for CreditFlow tests."""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: marks tests that require live network access"
    )
