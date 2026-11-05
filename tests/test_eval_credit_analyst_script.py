"""Synthetic analyst evaluation never needs a live judge in default CI."""

import json
import sys

import pytest

from scripts import eval_credit_analyst_assistant as evaluation


def test_build_cases_preserves_synthetic_classification(monkeypatch):
    seen = []

    def review(self, item, application, *, data_classification):
        seen.append((item["application_id"], application["application_id"], data_classification))
        return {"summary": "Human reviewer decides", "decision": None}

    monkeypatch.setattr(evaluation.CreditAnalystAssistant, "review", review)
    cases = evaluation.build_cases()
    assert len(cases) == len(evaluation.SYNTHETIC_CASES)
    assert all(left == right and label == "PUBLIC_SYNTHETIC" for left, right, label in seen)
    assert all(case["assistant_response"]["summary"] == "Human reviewer decides" for case in cases)


def test_default_cli_writes_synthetic_output_without_judge(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["eval_credit_analyst_assistant.py"])
    monkeypatch.setattr(evaluation, "build_cases", lambda: [{"synthetic_input": {"id": "SYNTH-1"}}])
    monkeypatch.setattr(evaluation, "run_judge", lambda *args: pytest.fail("live judge must be opt-in"))

    evaluation.main()

    output = json.loads((tmp_path / "models/evaluation/credit_analyst_assistant_eval.json").read_text())
    assert output["assistant_version"] == "credit-analyst-assistant-v1"
    assert output["synthetic_cases"][0]["synthetic_input"]["id"] == "SYNTH-1"
    assert "llm_judge" not in output


@pytest.mark.parametrize("provider", ["cloudflare", "azure-openai"])
def test_judge_requires_explicit_credentials(monkeypatch, provider):
    for name in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_JUDGE_DEPLOYMENT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="requires"):
        evaluation.run_judge([{"synthetic_input": {}}], provider)


def test_judge_rejects_unknown_provider_without_network():
    with pytest.raises(ValueError, match="judge provider"):
        evaluation.run_judge([{"synthetic_input": {}}], "unknown")


@pytest.mark.parametrize("provider", ["cloudflare", "azure-openai"])
def test_judge_records_advisory_result_from_stubbed_provider(monkeypatch, provider):
    import httpx

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://test.example")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "test-token")
    monkeypatch.setenv("AZURE_OPENAI_JUDGE_DEPLOYMENT", "test-model")
    requests = []

    class StubResponse:
        def raise_for_status(self):
            pass

        def json(self):
            choices = [{"message": {"content": [{"text": '{"grounding": 5}'}]}}]
            return {"result": {"choices": choices}} if provider == "cloudflare" else {"choices": choices}

    def fake_post(endpoint, *, headers, json, timeout):
        requests.append((endpoint, headers, json, timeout))
        return StubResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result = evaluation.run_judge([{"synthetic_input": {"id": "SYNTH-1"}}], provider)

    assert result["n"] == 1
    assert result["cases"][0]["judge"] == {"grounding": 5}
    assert result["judge_provider"] == provider
    assert requests[0][3] == 30.0
    assert "SYNTH-1" in requests[0][2]["messages"][0]["content"]
    if provider == "cloudflare":
        assert requests[0][2]["response_format"]["type"] == "json_schema"
    else:
        assert requests[0][2]["response_format"] == {"type": "json_object"}
