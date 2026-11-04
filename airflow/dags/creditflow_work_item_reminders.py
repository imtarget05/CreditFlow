"""Hourly work-item due check; delivery remains an internal outbox integration."""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import UTC, datetime, timedelta

from airflow.decorators import dag, task


def _enqueue_due() -> int:
    base_url = os.environ.get("CREDITFLOW_API_URL", "").strip().rstrip("/")
    api_key = os.environ.get("CREDITFLOW_API_KEY", "").strip()
    if not base_url or not api_key:
        raise RuntimeError("CREDITFLOW_API_URL and CREDITFLOW_API_KEY are required")
    request = urllib.request.Request(
        f"{base_url}/api/work-items/reminders/run",
        data=b"{}",
        headers={"Content-Type": "application/json", "X-CreditFlow-API-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    queued = result.get("queued")
    if not isinstance(queued, int) or queued < 0:
        raise RuntimeError("Invalid reminder response")
    return queued


@dag(
    dag_id="creditflow_work_item_reminders",
    description="Queue one internal reminder for each overdue work item.",
    schedule="0 * * * *",
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "credit-operations", "retries": 1, "retry_delay": timedelta(minutes=5)},
    tags=["creditflow", "operations", "sla"],
)
def creditflow_work_item_reminders() -> None:
    @task
    def enqueue_due() -> int:
        return _enqueue_due()

    enqueue_due()


if not os.environ.get("CREDITFLOW_DAG_NO_INSTANTIATE", ""):
    creditflow_work_item_reminders()
