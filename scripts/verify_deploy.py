#!/usr/bin/env python3
"""Local deploy verification for CreditFlow.

Builds the Docker image, runs the container, probes the required endpoints,
and writes evidence to docs/evidence/deploy-check.json.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs" / "evidence"
EVIDENCE_FILE = EVIDENCE_DIR / "deploy-check.json"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def wait_for_health(base: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/health", timeout=2)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def main() -> dict:
    evidence: dict = {
        "docker_build": {},
        "docker_run": {},
        "endpoints": {},
        "overall": "FAIL",
    }

    build = run(["docker", "build", "-t", "creditflow-api", "."])
    evidence["docker_build"] = {
        "returncode": build.returncode,
        "stdout_tail": build.stdout[-400:],
        "stderr_tail": build.stderr[-400:],
    }
    if build.returncode != 0:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return evidence

    run(["docker", "rm", "-f", "creditflow-api"], check=False)
    run_proc = run([
        "docker", "run", "-d",
        "-p", "8080:8080",
        "--env-file", ".env.example",
        "--name", "creditflow-api",
        "creditflow-api",
    ], check=False)
    evidence["docker_run"] = {
        "returncode": run_proc.returncode,
        "stdout_tail": run_proc.stdout[-400:],
        "stderr_tail": run_proc.stderr[-400:],
    }
    if run_proc.returncode != 0:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return evidence

    base = "http://localhost:8080"
    healthy = wait_for_health(base)
    evidence["endpoints"]["health"] = {"ok": healthy}
    if not healthy:
        evidence["overall"] = "FAIL"
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return evidence

    payload = {
        "income": 5000, "age": 35, "employment_years": 10,
        "loan_amount": 20000, "loan_term": 36, "existing_debt": 3000,
        "credit_history": 8, "previous_defaults": 0,
    }
    r = requests.post(f"{base}/predict", json=payload, timeout=10)
    evidence["endpoints"]["predict"] = {
        "status": r.status_code,
        "body_keys": list(r.json().keys()) if r.status_code == 200 else r.text[:200],
    }

    r = requests.get(f"{base}/model/info", timeout=10)
    evidence["endpoints"]["model_info"] = {
        "status": r.status_code,
        "body_keys": list(r.json().keys()) if r.status_code == 200 else r.text[:200],
    }

    evidence["overall"] = "PASS"
    print(json.dumps(evidence, indent=2, ensure_ascii=False))

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(f"wrote {EVIDENCE_FILE}")
    return evidence


if __name__ == "__main__":
    main()
