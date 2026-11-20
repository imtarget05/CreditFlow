"""Exercise the synthetic REVIEW -> approve -> replay -> ledger path on localhost."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROFILE = {
    "income": 8_000_000.0,
    "age": 35,
    "employment_years": 0.5,
    "loan_amount": 120_000_000.0,
    "loan_term": 36,
    "existing_debt": 2_000_000.0,
    "credit_history": 9.0,
    "previous_defaults": 1,
}


def wait_for_health(port: int, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            pass
        time.sleep(0.5)
    raise TimeoutError("Local CreditFlow API did not become healthy")


def request(base_url: str, path: str, api_key: str, body: dict | None = None) -> tuple[int, dict]:
    if not re.fullmatch(r"/(?:api/disbursements|predict/graph(?:/run-[a-f0-9]{12}/approve)?|audit/run-[a-f0-9]{12})", path):
        raise ValueError("Unexpected demo API path")
    headers = {"X-CreditFlow-API-Key": api_key}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = Request(
        base_url + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(req, timeout=60) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)


def run(port: int, api_key: str) -> dict:
    if port < 1024 or port > 65535:
        raise ValueError("Port must be between 1024 and 65535")
    base_url = f"http://127.0.0.1:{port}"
    if not api_key:
        raise ValueError("CREDITFLOW_API_KEY is required for the approval and evidence routes")
    if os.environ.get("CREDITFLOW_ENV", "").lower() == "production":
        raise ValueError("Refusing a synthetic workflow demo in production mode")
    wait_for_health(port)

    before_status, before = request(base_url, "/api/disbursements", api_key)
    workflow_started_at = time.monotonic()
    started_at = time.monotonic()
    start_status, start = request(base_url, "/predict/graph", api_key, {"customer_data": PROFILE})
    start_ms = round((time.monotonic() - started_at) * 1000)
    if start_status != 200 or start.get("decision") != "REVIEW" or not start.get("approval_required"):
        raise RuntimeError(f"Expected a paused REVIEW; received HTTP {start_status}, {start.get('decision')}")

    thread_id = start["thread_id"]
    if not isinstance(thread_id, str) or not re.fullmatch(r"run-[a-f0-9]{12}", thread_id):
        raise ValueError("Unexpected thread ID returned by local API")
    idem_key = "local-demo-" + uuid.uuid4().hex
    approval = {"action": "approve", "idempotency_key": idem_key}
    approval_started_at = time.monotonic()
    approve_status, approved = request(base_url, f"/predict/graph/{thread_id}/approve", api_key, approval)
    approval_ms = round((time.monotonic() - approval_started_at) * 1000)
    replay_status, replayed = request(base_url, f"/predict/graph/{thread_id}/approve", api_key, approval)
    conflict_status, conflict = request(base_url, f"/predict/graph/{thread_id}/approve", api_key, {
        "action": "reject", "idempotency_key": idem_key,
    })
    different_key_status, _ = request(base_url, f"/predict/graph/{thread_id}/approve", api_key, {
        "action": "approve", "idempotency_key": "other-" + uuid.uuid4().hex,
    })
    after_status, after = request(base_url, "/api/disbursements", api_key)
    audit_status, audit = request(base_url, f"/audit/{thread_id}", api_key)
    workflow_ms = round((time.monotonic() - workflow_started_at) * 1000)
    disbursement = approved.get("disbursement") or {}
    matching = [row for row in after.get("disbursements", []) if row.get("id") == disbursement.get("id")]

    assert before_status == after_status == audit_status == approve_status == replay_status == 200
    assert approved.get("ledger_status") == "APPROVED"
    assert disbursement.get("status") == "COMPLETED"
    assert len(disbursement.get("ledger_hash", "")) == 64
    assert replayed.get("replay") is True
    assert (replayed.get("disbursement") or {}).get("id") == disbursement["id"]
    assert conflict_status == 409 and conflict.get("detail") == "IDEMPOTENCY_KEY_CONFLICT"
    assert different_key_status == 409
    assert after["count"] == before["count"] + 1
    assert len(matching) == 1 and matching[0].get("hash_valid") is True

    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "working_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True).strip()),
        "scope": "Synthetic single-process localhost API; no real lending or cloud deployment",
        "start_status": start_status,
        "start_latency_ms": start_ms,
        "approval_latency_ms": approval_ms,
        "full_workflow_latency_ms": workflow_ms,
        "task_completion": 1.0,
        "decision_before_approval": start["decision"],
        "risk_score": start.get("risk_score"),
        "explanation_source": (start.get("explanation_meta") or {}).get("source", "unrecorded"),
        "approval_required": start["approval_required"],
        "authority_level": start.get("authority_level"),
        "approval_status": approve_status,
        "ledger_status": approved["ledger_status"],
        "replay_status": replay_status,
        "replay": replayed["replay"],
        "conflicting_action_status": conflict_status,
        "different_key_after_terminal_status": different_key_status,
        "disbursement_count_before": before["count"],
        "disbursement_count_after": after["count"],
        "matching_disbursement_rows": len(matching),
        "ledger_hash_valid": matching[0]["hash_valid"],
        "audit_status": audit_status,
        "audit_decision": audit.get("decision"),
        "complete": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    report = run(args.port, os.environ.get("CREDITFLOW_API_KEY", ""))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
