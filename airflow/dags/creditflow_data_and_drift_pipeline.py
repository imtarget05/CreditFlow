"""Daily drift check against CreditFlow's canonical serving-side monitor.

This DAG never scores or approves applicants. The API owns model thresholds,
reference statistics, and creation of a risk team's exception work item.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task


def _run_monitoring_check(run_id: str | None = None) -> dict:
    base_url = os.environ.get("CREDITFLOW_API_URL", "").strip().rstrip("/")
    api_key = os.environ.get("CREDITFLOW_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("CREDITFLOW_API_URL and CREDITFLOW_API_KEY are required")
    run_id = run_id or f"daily-{datetime.now(UTC).date().isoformat()}"
    request = urllib.request.Request(
        f"{base_url}/api/monitoring/check",
        data=json.dumps({"run_id": run_id}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-CreditFlow-API-Key": api_key,
            "User-Agent": "CreditFlow-Monitor/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    status = result.get("drift", {}).get("status")
    if status not in {"NO_DRIFT", "DRIFT_DETECTED", "INSUFFICIENT_DATA"}:
        raise RuntimeError("Unexpected drift status from CreditFlow API")
    return {
        "status": status,
        "n_recent": result["drift"].get("n_recent"),
        "work_item_id": (result.get("work_item") or {}).get("id"),
    }


@dag(
    dag_id="creditflow_data_and_drift_pipeline",
    description="Check serving-side drift and open a risk review task on detection.",
    schedule="0 4 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "risk-mlops",
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=30),
    },
    tags=["creditflow", "risk", "drift", "mlops"],
)
def creditflow_data_and_drift_pipeline() -> None:
    @task
    def check_drift() -> dict:
        return _run_monitoring_check()

    check_drift()


if not os.environ.get("CREDITFLOW_DAG_NO_INSTANTIATE", ""):
    creditflow_data_and_drift_pipeline()
