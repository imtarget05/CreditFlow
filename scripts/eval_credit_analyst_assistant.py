"""Optional LLM-as-judge evaluation for fixed synthetic analyst-assistant cases.

Example: python scripts/eval_credit_analyst_assistant.py --judge-provider cloudflare
The live judge is called only when this script is run explicitly.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.agent.subagents.credit_analyst_assistant import CreditAnalystAssistant
from scripts.eval_explanations import DEFAULT_JUDGE_MODEL, JUDGE_SCHEMA

SYNTHETIC_CASES = [
    {
        "work_item": {"id": "eval-001", "application_id": "SYNTH-001", "kind": "UNDERWRITING", "status": "OPEN", "assigned_role": "UNDERWRITER_L1"},
        "application": {"application_id": "SYNTH-001", "status": "PENDING_REVIEW", "decision": "REVIEW", "risk_level": "MEDIUM", "risk_score": 0.52, "reasons": ["high debt-to-income ratio"], "customer_data": {"income": 5000000, "age": 35, "employment_years": 8, "loan_amount": 60000000, "loan_term": 36, "existing_debt": 25000000, "credit_history": 6, "previous_defaults": 0}},
    },
    {
        "work_item": {"id": "eval-002", "application_id": "SYNTH-002", "kind": "DOCUMENT_CHECK", "status": "OPEN", "assigned_role": "UNDERWRITER_L1"},
        "application": {"application_id": "SYNTH-002", "status": "PENDING_REVIEW", "decision": "REVIEW", "risk_level": "HIGH", "risk_score": 0.84, "reasons": ["previous defaults", "short credit history"], "customer_data": {"income": 3000000, "age": 42, "employment_years": 2, "loan_amount": 90000000, "loan_term": 48, "existing_debt": 30000000, "credit_history": 1, "previous_defaults": 2}},
    },
]


def build_cases() -> list[dict]:
    assistant = CreditAnalystAssistant()
    return [{"synthetic_input": case, "assistant_response": assistant.review(
        case["work_item"], case["application"], data_classification="PUBLIC_SYNTHETIC"
    )} for case in SYNTHETIC_CASES]


def run_judge(cases: list[dict], provider: str, model: str | None = None) -> dict:
    import httpx

    if provider == "cloudflare":
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        if not account or not token:
            raise RuntimeError("Cloudflare judge requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN")
        judge_model = model or os.environ.get("CLOUDFLARE_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
        endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{judge_model}"
        headers = {"Authorization": f"Bearer {token}"}
    elif provider == "azure-openai":
        root = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        token = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        judge_model = model or os.environ.get("AZURE_OPENAI_JUDGE_DEPLOYMENT", "").strip()
        if not root or not token or not judge_model:
            raise RuntimeError("Azure OpenAI judge requires endpoint, API key and deployment")
        endpoint = f"{root}/openai/v1/chat/completions"
        headers = {"api-key": token}
    else:
        raise ValueError("judge provider must be cloudflare or azure-openai")

    results = []
    for number, case in enumerate(cases, 1):
        prompt = (
            "Evaluate this synthetic credit analyst assistant response only. "
            "Score grounding, completeness, actionability, and decision_boundary from 1 to 5. "
            "Check that it does not decide APPROVE/REJECT and that it distinguishes facts from advice. "
            "Return only the requested JSON schema. Scores are advisory.\n" + json.dumps(case, ensure_ascii=False)
        )
        body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": 350, "temperature": 0}
        if provider == "cloudflare":
            body["response_format"] = {"type": "json_schema", "json_schema": JUDGE_SCHEMA}
        else:
            body["model"] = judge_model
            body["response_format"] = {"type": "json_object"}
        response = httpx.post(endpoint, headers=headers, json=body, timeout=30.0)
        response.raise_for_status()
        payload = response.json().get("result", {}) if provider == "cloudflare" else response.json()
        choices = payload.get("choices", [])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if isinstance(content, dict):
            judgement = content
        else:
            text = str(content).strip()
            text = text[text.find("{"):text.rfind("}") + 1]
            judgement = json.loads(text)
        results.append({"case": number, "judge": judgement})
    return {"judge_provider": provider, "judge_model": judge_model, "judge_prompt_version": "credit-analyst-assistant-judge-v1", "n": len(results), "cases": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--llm-judge", action="store_true", help="explicitly call the LLM judge on checked-in synthetic cases")
    parser.add_argument("--judge-provider", choices=("cloudflare", "azure-openai"), default="cloudflare")
    parser.add_argument("--judge-model")
    args = parser.parse_args()
    result = {"assistant_version": "credit-analyst-assistant-v1", "synthetic_cases": build_cases()}
    if args.llm_judge:
        result["llm_judge"] = run_judge(result["synthetic_cases"], args.judge_provider, args.judge_model)
    out = Path("models/evaluation")
    out.mkdir(parents=True, exist_ok=True)
    target = out / "credit_analyst_assistant_eval.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
