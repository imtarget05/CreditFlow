"""Tests for scripts/verify_deploy.py (TDD — failing first)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def test_verify_deploy_produces_pass_evidence():
    """When docker build/run succeeds and endpoints return 200, evidence should be PASS."""
    from scripts.verify_deploy import main

    build_proc = MagicMock()
    build_proc.returncode = 0
    build_proc.stdout = ""
    build_proc.stderr = ""

    run_proc = MagicMock()
    run_proc.returncode = 0
    run_proc.stdout = "abc123\n"
    run_proc.stderr = ""

    health_resp = MagicMock()
    health_resp.status_code = 200

    predict_resp = MagicMock()
    predict_resp.status_code = 200
    predict_resp.json.return_value = {"risk_probability": 0.1}

    model_info_resp = MagicMock()
    model_info_resp.status_code = 200
    model_info_resp.json.return_value = {"model": "x"}

    def fake_run(cmd, **kwargs):
        if cmd[1] == "build":
            return build_proc
        if cmd[1] == "run":
            return run_proc
        if cmd[1] == "rm":
            return MagicMock(returncode=0)
        return MagicMock(returncode=0)

    with patch("scripts.verify_deploy.subprocess.run", side_effect=fake_run):
        with patch("scripts.verify_deploy.requests.get", side_effect=[health_resp, model_info_resp]):
            with patch("scripts.verify_deploy.requests.post", return_value=predict_resp):
                with patch("scripts.verify_deploy.time.sleep"):
                    evidence = main()

    assert evidence["overall"] == "PASS"
    assert evidence["docker_build"]["returncode"] == 0
    assert evidence["docker_run"]["returncode"] == 0
    assert evidence["endpoints"]["health"]["ok"] is True
    assert evidence["endpoints"]["predict"]["status"] == 200
    assert evidence["endpoints"]["model_info"]["status"] == 200


def test_verify_deploy_marks_fail_on_build_error():
    """When docker build fails, evidence should be FAIL."""
    from scripts.verify_deploy import main

    build_proc = MagicMock()
    build_proc.returncode = 1
    build_proc.stdout = ""
    build_proc.stderr = "build failed"

    with patch("scripts.verify_deploy.subprocess.run", return_value=build_proc):
        with patch("scripts.verify_deploy.EVIDENCE_DIR"):
            with patch.object(Path, "write_text"):
                evidence = main()

    assert evidence["overall"] == "FAIL"
    assert evidence["docker_build"]["returncode"] == 1
