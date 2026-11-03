"""Eval harness: template (offline) vs cloudflare LLM.

Run by hand with a real key to get the LLM arm:
  CREDITFLOW_LLM_PROVIDER=cloudflare \
  CLOUDFLARE_ACCOUNT_ID=... CLOUDFLARE_API_TOKEN=... \
  python scripts/eval_explanations.py

Offline (no key) it exercises the template arm only.
"""
from __future__ import annotations

import json
import argparse
import os
import sys
import time
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.agent.explanations import generate_explanation
from pipeline.agent.llm_provider import DEFAULT_MODEL


LOW_RISK = {"income": 5000000, "age": 35, "employment_years": 10, "loan_amount": 20000000, "loan_term": 36, "existing_debt": 3000000, "credit_history": 8, "previous_defaults": 0}

def run_eval(profiles: list[dict]) -> dict:
    """Evaluate explanation quality across profiles (offline-safe).

    Returns dict with keys: n, json_valid_rate, reason_keyword_alignment_rate, latency_ms_p50,
    guard_blocked_rate. ``guard_blocked_rate`` counts responses where the LLM
    prose was refused by the explanation guard (ungrounded numbers / malformed /
    provider failure) and the deterministic template was served instead.
    """
    lat, valid, keyword_aligned, blocked = [], 0, 0, 0
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
        if expl.get("fallback_reason"):
            blocked += 1
        rf = " ".join(expl.get("risk_factors", [])).lower()
        keyword_aligned += int("debt-to-income" in rf or "nợ" in rf)
    n = len(profiles)
    return {
        "n": n,
        "json_valid_rate": round(valid / max(n, 1), 4),
        "reason_keyword_alignment_rate": round(keyword_aligned / max(n, 1), 4),
        "latency_ms_p50": round(median(lat) if lat else 0, 2),
        "guard_blocked_rate": round(blocked / max(n, 1), 4),
    }


def run_llm_judge(
    profiles: list[dict], provider: str = "cloudflare", model: str | None = None
) -> dict:
    """Optionally score explanations with a configured LLM on synthetic cases.

    This is an advisory evaluation harness, not a serving feature or a credit
    model gate. The judge sees no external customer input; callers should keep
    the fixture set synthetic. It returns scores and suggested prompt edits,
    never changes prompts or model artifacts automatically.
    """
    import httpx

    if provider == "cloudflare":
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not account or not token:
            raise RuntimeError("Cloudflare judge requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN")
        judge_model = (
            model
            or os.environ.get("CLOUDFLARE_JUDGE_MODEL", "").strip()
            or os.environ.get("CLOUDFLARE_MODEL", "").strip()
            or DEFAULT_MODEL
        )
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{judge_model}"
        headers = {"Authorization": f"Bearer {token}"}
    elif provider == "azure-openai":
        endpoint_root = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        deployment = model or os.environ.get("AZURE_OPENAI_JUDGE_DEPLOYMENT", "").strip()
        if not endpoint_root or not api_key or not deployment:
            raise RuntimeError(
                "Azure OpenAI judge requires AZURE_OPENAI_ENDPOINT, "
                "AZURE_OPENAI_API_KEY, and AZURE_OPENAI_JUDGE_DEPLOYMENT"
            )
        judge_model = deployment
        endpoint = f"{endpoint_root}/openai/v1/chat/completions"
        headers = {"api-key": api_key}
    else:
        raise ValueError("judge provider must be 'cloudflare' or 'azure-openai'")
    results = []
    for index, profile in enumerate(profiles, start=1):
        explanation = generate_explanation({
            "customer_data": profile, "data_classification": "PUBLIC_SYNTHETIC",
            "risk_score": 0.5, "risk_level": "MEDIUM", "decision": "REVIEW",
            "model_name": "logistic_regression", "reasons": ["high debt-to-income ratio"],
            "fraud_score": 0.0, "fraud_flags": [], "policy_violations": [],
        })
        case = {
            "synthetic_case": profile,
            "authoritative_decision": "REVIEW",
            "authoritative_reason": "The deterministic demo policy routed this profile to human review.",
            "explanation": {key: explanation.get(key) for key in (
                "summary", "risk_factors", "recommendation_note", "confidence"
            )},
        }
        prompt = (
            "You are an evaluator of a synthetic credit analyst explanation. "
            "Do not decide credit eligibility. Evaluate only the supplied evidence. "
            "Return JSON with integer scores 1-5 for grounding, completeness, "
            "actionability, and decision_boundary; a short list of issues; and "
            "one suggested prompt revision. A score is advisory and may be wrong.\n"
            + json.dumps(case, ensure_ascii=False)
        )
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 350,
            "temperature": 0,
        }
        if provider == "azure-openai":
            body["model"] = judge_model
            body["response_format"] = {"type": "json_object"}
        response = httpx.post(
            endpoint,
            headers=headers,
            json=body,
            timeout=30.0,
        )
        response.raise_for_status()
        response_json = response.json()
        payload = response_json.get("result", {}) if provider == "cloudflare" else response_json
        content = payload.get("response")
        if isinstance(content, dict):
            judgement = content
        elif isinstance(content, str):
            judgement = json.loads(content)
        else:
            choices = payload.get("choices") or []
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            judgement = json.loads(content)
        results.append({"case": index, "judge": judgement})
    return {
        "judge_provider": "cloudflare_workers_ai" if provider == "cloudflare" else "azure_openai",
        "judge_model": judge_model,
        "judge_prompt_version": "credit-explanation-judge-v1",
        "advisory_only": True,
        "cases": results,
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-judge", action="store_true", help="explicitly call the selected LLM judge on fixed synthetic fixtures")
    parser.add_argument("--judge-provider", choices=("cloudflare", "azure-openai"), default="cloudflare")
    parser.add_argument("--judge-model", help="Workers AI model or Azure OpenAI deployment for the advisory judge")
    args = parser.parse_args()
    profiles = [
        {"income": 5000000, "age": 35, "employment_years": 10, "loan_amount": 20000000, "loan_term": 36, "existing_debt": 3000000, "credit_history": 8, "previous_defaults": 0},
        {"income": 2500000, "age": 32, "employment_years": 4, "loan_amount": 12000000, "loan_term": 36, "existing_debt": 1000000, "credit_history": 5, "previous_defaults": 2},
        {"income": 2500000, "age": 32, "employment_years": 4, "loan_amount": 12000000, "loan_term": 36, "existing_debt": 3500000, "credit_history": 5, "previous_defaults": 0},
    ]
    out = run_eval(profiles)
    if args.llm_judge:
        out["llm_judge"] = run_llm_judge(profiles, args.judge_provider, args.judge_model)
    d = Path("models/evaluation")
    d.mkdir(parents=True, exist_ok=True)
    import datetime
    f = d / f"llm_eval_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    f.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps(out, indent=2, ensure_ascii=False), f"\nwrote {f}")
    _log_eval_mlflow(out)
