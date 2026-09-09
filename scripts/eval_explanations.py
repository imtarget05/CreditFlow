"""Eval harness: template (offline) vs cloudflare LLM.

Run by hand with a real key to get the LLM arm:
  CREDITFLOW_LLM_PROVIDER=cloudflare \
  CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_API_TOKEN=... \
  python scripts/eval_explanations.py

Offline (no key) it exercises the template arm only.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.agent.explanations import generate_explanation


def run_eval(profiles: list[dict]) -> dict:
    """Evaluate explanation quality across profiles (offline-safe).

    Returns dict with keys: n, json_valid_rate, faithfulness, latency_ms_p50.
    """
    lat, valid, faith = [], 0, []
    for p in profiles:
        t0 = time.perf_counter()
        expl = generate_explanation({
            "customer_data": p, "risk_score": 0.5, "risk_level": "MEDIUM",
            "decision": "REVIEW", "model_name": "logistic_regression",
            "reasons": ["high debt-to-income ratio"],
            "fraud_score": 0.0, "fraud_flags": [], "policy_violations": [],
        })
        lat.append((time.perf_counter() - t0) * 1000)
        if expl.get("summary") and isinstance(expl.get("risk_factors"), list):
            valid += 1
        rf = " ".join(expl.get("risk_factors", [])).lower()
        faith.append(1.0 if "debt-to-income" in rf or "nợ" in rf else 0.0)
    n = len(profiles)
    return {
        "n": n,
        "json_valid_rate": round(valid / max(n, 1), 4),
        "faithfulness": round(sum(faith) / max(n, 1), 4),
        "latency_ms_p50": round(median(lat) if lat else 0, 2),
    }


def _log_eval_mlflow(out: dict) -> None:
    """Log eval results to MLflow experiment creditflow-risk (graceful)."""
    try:
        import mlflow
        import os
    except Exception:
        print("[mlflow] not available, skipping eval log")
        return
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("creditflow-risk")
    with mlflow.start_run(run_name="eval_explain", nested=False):
        mlflow.log_param("llm_model", os.environ.get("CLOUDFLARE_MODEL", "template"))
        mlflow.log_param("prompt_version", "credit-explain-v1")
        for k, v in out.items():
            if isinstance(v, (int, float)):
                mlflow.log_metric(k, float(v))
    print("[mlflow] eval logged.")


if __name__ == "__main__":
    from tests.test_agent import LOW_RISK, MID_RISK, HIGH_RISK
    out = run_eval([LOW_RISK, MID_RISK, HIGH_RISK])
    d = Path("models/evaluation")
    d.mkdir(parents=True, exist_ok=True)
    import datetime
    f = d / f"llm_eval_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False), f"\nwrote {f}")
    _log_eval_mlflow(out)
