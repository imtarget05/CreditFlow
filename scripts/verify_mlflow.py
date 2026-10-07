#!/usr/bin/env python3
"""Verify the self-hosted MLflow tracking stack (read-only client check).

Mirrors the training structure in scripts/train_models.py:_log_mlflow:
  Experiment 'creditflow-risk'
    Parent run benchmark_<utc> (dataset/cost/production params + business metrics)
      4 child runs (logistic_regression, decision_tree, random_forest, xgboost)

Exit nonzero when any required piece is missing. No secrets printed.
"""
from __future__ import annotations

import os
import sys

URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5050")
EXPERIMENT = "creditflow-risk"
MODELS = {"logistic_regression", "decision_tree", "random_forest", "xgboost"}
METRIC_COLS = (
    "val_accuracy", "val_precision", "val_recall", "val_f1",
    "test_accuracy", "test_precision", "test_recall", "test_f1",
    "business_cost",
)


def main() -> int:
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=URI)
    exp = client.get_experiment_by_name(EXPERIMENT)
    if exp is None:
        print(f"FAIL: experiment {EXPERIMENT!r} missing at {URI}")
        return 1
    print(f"experiment_id={exp.experiment_id} name={exp.name}")
    runs = client.search_runs([exp.experiment_id], order_by=["start_time DESC"], max_results=30)
    parents = [r for r in runs if str(r.info.run_name).startswith("benchmark_")]
    if not parents:
        print("FAIL: no benchmark_* parent run")
        return 1
    parent = parents[0]
    pid = parent.info.run_id
    print(f"parent_run={run_name(parent)} id={pid} status={parent.info.status}")
    params = dict(parent.data.params)
    missing_p = {"dataset", "dataset_sha256", "rows", "fn_cost", "fp_cost", "production_model"} - set(params)
    if missing_p:
        print(f"FAIL: parent missing params {sorted(missing_p)}")
        return 1
    print(f"parent_params={sorted(params)}")
    print(f"parent_metrics={sorted(parent.data.metrics)}")
    children = [r for r in runs if dict(r.data.tags).get("mlflow.parentRunId") == pid]
    child_names = {str(r.info.run_name) for r in children}
    if child_names != MODELS:
        print(f"FAIL: child runs {sorted(child_names)} != expected {sorted(MODELS)}")
        return 1
    for child in sorted(children, key=lambda r: str(r.info.run_name)):
        missing_m = set(METRIC_COLS) - set(child.data.metrics)
        if missing_m:
            print(f"FAIL: child {child.info.run_name} missing metrics {sorted(missing_m)}")
            return 1
    print(f"children={sorted(child_names)} metrics_ok")
    arts = {a.path for a in client.list_artifacts(pid)}
    print(f"parent_artifacts={sorted(arts)}")
    if not arts:
        print("FAIL: no artifacts on parent run")
        return 1
    try:
        vers = client.get_latest_versions(EXPERIMENT)
        print(f"registry={EXPERIMENT} versions={[v.version for v in vers]}")
    except Exception as exc:  # registry optional on some stores
        print(f"registry skipped: {exc}")
    print("MLFLOW_VERIFY=OK")
    return 0


def run_name(run) -> str:
    return str(run.info.run_name)


if __name__ == "__main__":
    sys.exit(main())
