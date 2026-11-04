"""Test for model validation pipeline (Plan 05, Gate 2)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_model_validation_runs_and_passes():
    """The validation pipeline must run against the frozen model and pass."""
    result = subprocess.run(
        [sys.executable, "scripts/model_validation.py"],
        capture_output=True, text=True, cwd=str(ROOT), check=False,
    )
    assert result.returncode == 0, result.stderr

    evidence_path = ROOT / "docs" / "evidence" / "e2e" / "model_validation_evidence.json"
    assert evidence_path.exists(), "evidence file not produced"
    evidence = json.loads(evidence_path.read_text())
    assert evidence["gate"] == "5"
    assert evidence["passed"] is True
    assert evidence["metrics"]["recall"] >= 0.60
    assert evidence["metrics"]["f1"] >= 0.45
    assert evidence["metrics"]["normalized_business_cost"] <= 0.42
    assert "subgroups" in evidence
    assert len(evidence["subgroups"]) > 0
    assert "expected_calibration_error" in evidence["metrics"]
