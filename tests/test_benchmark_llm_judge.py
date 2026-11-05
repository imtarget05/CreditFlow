"""A contrast pass must reflect ranking of known synthetic defects."""

from pathlib import Path

import pytest

from scripts.benchmark_llm_judge import OUTPUT_DIR, check_result, resolve_output_path


@pytest.mark.parametrize("name", ["../outside.json", "/tmp/outside.json", "sub/file.json", "report.txt"])
def test_benchmark_rejects_output_outside_evaluation_directory(name):
    with pytest.raises(ValueError):
        resolve_output_path(Path(name))


def test_benchmark_accepts_json_filename_in_evaluation_directory():
    assert resolve_output_path(Path("benchmark.json")) == OUTPUT_DIR / "benchmark.json"


def test_judge_contrast_marks_missed_hallucination_and_boundary():
    cases = [
        {"judge": {
            "grounding": 4, "completeness": 5, "actionability": 3,
            "decision_boundary": 2, "issues": [], "suggested_prompt_revision": "none",
        }},
        {"judge": {
            "grounding": 4, "completeness": 3, "actionability": 5,
            "decision_boundary": 2, "issues": [], "suggested_prompt_revision": "none",
        }},
        {"judge": {
            "grounding": 2, "completeness": 1, "actionability": 1,
            "decision_boundary": 1, "issues": [], "suggested_prompt_revision": "none",
        }},
    ]

    result = check_result(cases)

    assert result["schema_valid"] is True
    assert result["checks"] == {
        "fabricated_income_lowers_grounding": False,
        "auto_approval_lowers_decision_boundary": False,
        "missing_content_lowers_completeness": True,
        "missing_action_lowers_actionability": True,
    }
