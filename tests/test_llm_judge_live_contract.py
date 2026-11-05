"""Contract checks for the optional Workers AI explanation judge."""

from __future__ import annotations


def test_cloudflare_judge_requests_supported_structured_output(monkeypatch):
    from scripts.eval_explanations import LOW_RISK, run_llm_judge

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "synthetic-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-token")
    monkeypatch.delenv("CLOUDFLARE_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("CLOUDFLARE_MODEL", raising=False)
    request = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": {"response": {
                "grounding": 4,
                "completeness": 3,
                "actionability": 3,
                "decision_boundary": 5,
                "issues": [],
                "suggested_prompt_revision": "Clarify the review step.",
            }}}

    def fake_post(url, **kwargs):
        request.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    result = run_llm_judge([LOW_RISK])

    assert result["judge_model"] == "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert request["url"].endswith("/@cf/meta/llama-3.3-70b-instruct-fp8-fast")
    response_format = request["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]
    assert set(schema["required"]) == {
        "grounding", "completeness", "actionability", "decision_boundary",
        "issues", "suggested_prompt_revision",
    }
    assert result["cases"][0]["judge"]["decision_boundary"] == 5


def test_judge_accepts_controlled_synthetic_explanation(monkeypatch):
    from scripts.eval_explanations import LOW_RISK, run_llm_judge

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "synthetic-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "synthetic-token")
    monkeypatch.setattr(
        "scripts.eval_explanations.generate_explanation",
        lambda _: (_ for _ in ()).throw(AssertionError("unexpected generation")),
    )
    sent = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": {"response": {
                "grounding": 1, "completeness": 1, "actionability": 1,
                "decision_boundary": 1, "issues": ["fabricated income"],
                "suggested_prompt_revision": "Check the supplied facts.",
            }}}

    def fake_post(url, **kwargs):
        sent.update(kwargs)
        return Response()

    monkeypatch.setattr("httpx.post", fake_post)
    explanation = {
        "summary": "The applicant earns 900000000 and is approved automatically.",
        "risk_factors": [], "recommendation_note": "Disburse now.",
        "confidence": "high",
    }
    result = run_llm_judge([LOW_RISK], explanations=[explanation])

    assert "900000000" in sent["json"]["messages"][0]["content"]
    assert result["cases"][0]["judge"]["grounding"] == 1
