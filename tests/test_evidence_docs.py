"""Tests for JD coverage evidence docs (TDD — failing first)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_jd_coverage_doc_exists():
    """docs/interview/jd-coverage.md must exist and cover key JD topics."""
    path = ROOT / "docs" / "interview" / "jd-coverage.md"
    assert path.exists(), f"Missing {path}"
    content = path.read_text(encoding="utf-8")
    assert "MLOps" in content or "mlops" in content.lower()
    assert "FastAPI" in content or "fastapi" in content.lower()
    assert "Docker" in content or "docker" in content.lower()
    assert "MLflow" in content or "mlflow" in content.lower()


def test_jd_coverage_notes_exist():
    """tasks/notes/jd-coverage.md must exist with tradeoff decisions."""
    path = ROOT / "tasks" / "notes" / "jd-coverage.md"
    assert path.exists(), f"Missing {path}"
    content = path.read_text(encoding="utf-8")
    assert "Tradeoff" in content or "tradeoff" in content.lower() or "downplay" in content.lower() or "emphasize" in content.lower()
