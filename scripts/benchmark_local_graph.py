"""Measure repeated local synthetic graph workflows without external services."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from datetime import UTC, datetime

from scripts.run_local_graph_demo import run


def nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def benchmark(port: int, api_key: str, cases: int) -> dict:
    if cases < 1 or cases > 100:
        raise ValueError("cases must be between 1 and 100")
    results: list[dict] = []
    for index in range(cases):
        try:
            evidence = run(port, api_key)
            results.append({"case": index + 1, "complete": True, "evidence": evidence})
        except Exception as exc:  # keep failed cases in the denominator
            results.append({"case": index + 1, "complete": False, "error": str(exc)[:300]})

    completed = [item["evidence"] for item in results if item["complete"]]
    full_ms = [item["full_workflow_latency_ms"] for item in completed]
    start_ms = [item["start_latency_ms"] for item in completed]
    approve_ms = [item["approval_latency_ms"] for item in completed]
    return {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "scope": "Synthetic localhost API and SQLite; one process, sequential cases",
        "total_cases": cases, "completed_cases": len(completed),
        "task_completion_rate": len(completed) / cases,
        "latency_method": "p50 is median; p95 is nearest-rank among completed cases only",
        "full_workflow_p50_ms": statistics.median(full_ms) if full_ms else None,
        "full_workflow_p95_ms": nearest_rank(full_ms, 0.95),
        "start_p50_ms": statistics.median(start_ms) if start_ms else None,
        "start_p95_ms": nearest_rank(start_ms, 0.95),
        "approval_p50_ms": statistics.median(approve_ms) if approve_ms else None,
        "approval_p95_ms": nearest_rank(approve_ms, 0.95),
        "explanation_sources": sorted({item["explanation_source"] for item in completed}),
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18084)
    parser.add_argument("--cases", type=int, default=20)
    args = parser.parse_args()
    import os

    result = benchmark(args.port, os.environ.get("CREDITFLOW_API_KEY", ""), args.cases)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["completed_cases"] != args.cases:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
