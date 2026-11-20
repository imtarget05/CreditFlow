"""Restart the local API before and after approval and verify durable effects."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime

try:
    from .run_local_graph_demo import PROFILE, request, wait_for_health
except ImportError:  # direct `python3 scripts/run_restart_drill.py`
    from run_local_graph_demo import PROFILE, request, wait_for_health


def docker_command(arguments: list[str]) -> str:
    """Execute a validated, local-only Docker command without a shell."""
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=True
    ).stdout.strip()


def run(port: int, image: str) -> dict:
    if not 1024 <= port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    if os.environ.get("CREDITFLOW_ENV", "").lower() == "production":
        raise ValueError("Synthetic restart drill cannot run in production mode")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]{0,127}:[a-z0-9][a-z0-9._-]{0,63}", image):
        raise ValueError("Image must be a local name:tag without registry options")

    key = "local-restart-" + uuid.uuid4().hex
    idem_key = "restart-" + uuid.uuid4().hex
    container = "creditflow-restart-" + uuid.uuid4().hex[:10]
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="creditflow-restart-") as data_dir:
        docker_command([
            "run", "--rm", "-d", "--name", container,
            # Root is limited to this disposable localhost drill so it can
            # write the host-owned temporary mount without world-writable mode.
            "--user", "0",
            "-p", f"127.0.0.1:{port}:8000", "-v", f"{data_dir}:/app/data",
            "-e", "CREDITFLOW_ENV=development",
            "-e", "CREDITFLOW_ALLOW_ANONYMOUS_DEMO=1",
            "-e", f"CREDITFLOW_API_KEY={key}",
            "-e", "CREDITFLOW_API_KEY_ID=demo-reviewer",
            "-e", "CREDITFLOW_API_KEY_ROLE=RISK_COMMITTEE_L2",
            "-e", "CREDITFLOW_CHECKPOINT_DB=/app/data/checkpoints.pkl",
            "-e", "CREDITFLOW_LEDGER_DB=/app/data/ledger.db",
            image,
        ])
        try:
            wait_for_health(port)
            before_status, before = request(base, "/api/disbursements", key)
            start_status, started = request(base, "/predict/graph", key, {"customer_data": PROFILE})
            assert before_status == start_status == 200
            assert started["decision"] == "REVIEW" and started["approval_required"]
            thread_id = started["thread_id"]

            docker_command(["restart", container])
            wait_for_health(port)
            approval = {"action": "approve", "idempotency_key": idem_key}
            approve_status, approved = request(base, f"/predict/graph/{thread_id}/approve", key, approval)
            assert approve_status == 200 and approved["ledger_status"] == "APPROVED"
            disbursement_id = approved["disbursement"]["id"]

            docker_command(["restart", container])
            wait_for_health(port)
            replay_status, replayed = request(base, f"/predict/graph/{thread_id}/approve", key, approval)
            after_status, after = request(base, "/api/disbursements", key)
            audit_status, audit = request(base, f"/audit/{thread_id}", key)
            rows = [row for row in after["disbursements"] if row["id"] == disbursement_id]
            assert replay_status == after_status == audit_status == 200
            assert replayed["replay"] is True
            assert replayed["disbursement"]["id"] == disbursement_id
            assert after["count"] == before["count"] + 1
            assert len(rows) == 1 and rows[0]["hash_valid"] is True
            assert audit["decision"] == "APPROVE"

            return {
                "timestamp_utc": datetime.now(UTC).isoformat(),
                "source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "docker_image_id": docker_command(["image", "inspect", image, "--format", "{{.Id}}"]),
                "scope": "Synthetic single-container localhost API; both checkpoint and ledger on a persistent bind mount",
                "initial_decision": started["decision"],
                "restart_before_approval": True,
                "approval_http_status": approve_status,
                "restart_after_approval": True,
                "replay_http_status": replay_status,
                "replay": replayed["replay"],
                "disbursement_count_delta": after["count"] - before["count"],
                "matching_disbursement_rows": len(rows),
                "ledger_hash_valid": rows[0]["hash_valid"],
                "audit_decision": audit["decision"],
                "checkpoint_scope": "single-container persistent file, not multi-replica shared storage",
            }
        finally:
            subprocess.run(["docker", "stop", container], stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18083)
    parser.add_argument("--image", default="creditflow-restart-drill:20261120")
    args = parser.parse_args()
    print(json.dumps(run(args.port, args.image), indent=2))
