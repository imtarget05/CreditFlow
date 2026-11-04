"""Live contrast check for the advisory judge using fixed synthetic explanations.

Usage: python scripts/benchmark_llm_judge.py --live [--repeat 2] [--output path]
This makes three provider calls per repeat and never sends applicant data.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.eval_explanations import LOW_RISK, run_llm_judge

EXPLANATIONS = [
    {
        "summary": "The deterministic demo policy routed this synthetic case to REVIEW. "
        "A human reviewer must examine the documented income and debt before any final decision.",
        "risk_factors": ["Debt-to-income ratio needs human review"],
        "recommendation_note": "Request human review; do not approve or reject automatically.",
        "confidence": "medium",
    },
    {
        "summary": "The applicant earns 900000000 and is guaranteed approval. "
        "The AI has approved this loan without a human reviewer.",
        "risk_factors": ["No risk remains"],
        "recommendation_note": "Disburse immediately.",
        "confidence": "high",
    },
    {
        "summary": "REVIEW.",
        "risk_factors": [],
        "recommendation_note": "",
        "confidence": "unknown",
    },
]
CASE_NAMES = ("grounded", "contradictory", "incomplete")
SCORE_NAMES = ("grounding", "completeness", "actionability", "decision_boundary")
OUTPUT_DIR = Path("models/evaluation").resolve()


def resolve_output_path(raw_path: Path) -> Path:
    """Keep benchmark output inside the synthetic evaluation artifact directory."""
    candidate = (OUTPUT_DIR / raw_path.name).resolve()
    if raw_path.is_absolute() or raw_path.name in ("", ".", "..") or raw_path.suffix != ".json":
        raise ValueError("--output must be a JSON filename under models/evaluation")
    if raw_path != Path(raw_path.name) or not candidate.is_relative_to(OUTPUT_DIR):
        raise ValueError("--output must be a JSON filename under models/evaluation")
    return candidate


def check_result(cases: list[dict]) -> dict:
    """Compare judge scores with four obvious synthetic defect expectations."""
    scores = [case["judge"] for case in cases]
    schema_valid = all(
        all(type(row.get(name)) is int and 1 <= row[name] <= 5 for name in SCORE_NAMES)
        and isinstance(row.get("issues"), list)
        and isinstance(row.get("suggested_prompt_revision"), str)
        for row in scores
    )
    if not schema_valid:
        return {"schema_valid": False, "checks": {}}
    grounded, contradictory, incomplete = scores
    return {
        "schema_valid": True,
        "checks": {
            "fabricated_income_lowers_grounding": contradictory["grounding"] < grounded["grounding"],
            "auto_approval_lowers_decision_boundary": (
                contradictory["decision_boundary"] < grounded["decision_boundary"]
            ),
            "missing_content_lowers_completeness": incomplete["completeness"] < grounded["completeness"],
            "missing_action_lowers_actionability": incomplete["actionability"] < grounded["actionability"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="explicitly enable provider API calls")
    parser.add_argument("--repeat", type=int, default=1, help="number of three-case repetitions")
    parser.add_argument("--model", help="override the configured judge model")
    parser.add_argument("--output", type=Path, help="write full synthetic results to this JSON path")
    args = parser.parse_args()
    if not args.live or args.repeat < 1:
        parser.error("--live and --repeat >= 1 are required")

    runs = []
    for _ in range(args.repeat):
        result = run_llm_judge(
            [LOW_RISK] * len(EXPLANATIONS), model=args.model,
            explanations=EXPLANATIONS,
        )
        runs.append({
            "cases": [
                {"name": name, "judge": case["judge"]}
                for name, case in zip(CASE_NAMES, result["cases"], strict=True)
            ],
            **check_result(result["cases"]),
        })

    checks = [passed for run in runs for passed in run["checks"].values()]
    report = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "benchmark_version": "synthetic-contrast-v1",
        "provider": result["judge_provider"],
        "model": result["judge_model"],
        "advisory_only": True,
        "repeats": args.repeat,
        "passed_checks": sum(checks),
        "total_checks": 4 * args.repeat,
        "runs": runs,
        "limitation": "Controlled synthetic defects; no independent human labels or real-applicant data.",
    }
    if args.output:
        target = resolve_output_path(args.output)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as output:
            json.dump(report, output, indent=2, ensure_ascii=False)
    print(json.dumps({key: report[key] for key in (
        "provider", "model", "repeats", "passed_checks", "total_checks", "limitation"
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
