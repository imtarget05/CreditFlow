"""A contrast pass must reflect ranking of known synthetic defects."""

from scripts.benchmark_llm_judge import check_result


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
