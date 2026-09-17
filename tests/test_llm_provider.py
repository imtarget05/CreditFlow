"""Tests for the Cloudflare Workers AI explain backend (TDD RED first).

No real API calls: transport is monkeypatched. CI needs no secrets.
"""
from __future__ import annotations


import pytest


CASE_STATE = {
    "data_classification": "PUBLIC",  # synthetic demo data, not real customer PII
    "customer_data": {
        "income": 8000000,
        "loan_amount": 120000000,
        "existing_debt": 15000000,
        "age": 35,
        "loan_term": 36,
    },
    "derived_features": {"debt_to_income": 1.875, "loan_to_income": 15.0},
    "risk_score": 0.9,
    "risk_level": "HIGH",
    "decision": "REJECT",
    "model_name": "logistic_regression",
    "reasons": ["high debt-to-income ratio"],
    "fraud_score": 0.3,
    "fraud_flags": ["debt_burden_extreme"],
    "policy_violations": ["policy_debt_to_income_excessive"],
}


def _serve(monkeypatch, response):
    """Make the Cloudflare provider return *response* without any network call."""
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    monkeypatch.setenv("CLOUDFLARE_MODEL", "@cf/test")
    import pipeline.agent.llm_provider as provider

    class Resp:
        status_code = 200

        def json(self):
            return {"result": {"response": response}}

    monkeypatch.setattr(provider.httpx, "post", lambda *a, **k: Resp())


def test_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    from pipeline.agent.llm_provider import try_cloudflare_explain
    assert try_cloudflare_explain({"risk_score": 0.9}) is None


def test_groq_provider_parses_openai_style_completion(monkeypatch):
    import json

    from pipeline.agent.explanations import generate_explanation

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_MODEL", "openai/gpt-oss-20b")
    import pipeline.agent.llm_provider as provider

    class Resp:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "summary": "Rủi ro cao: thu nhập 8.000.000 VND nhưng vay 120.000.000 VND.",
                                    "risk_factors": ["DTI 1.875 vượt ngưỡng"],
                                    "recommendation_note": "Kiểm tra nguồn trả nợ kỳ hạn 36 tháng.",
                                    "confidence": "high",
                                }
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(provider.httpx, "post", lambda *a, **k: Resp())
    output = generate_explanation(dict(CASE_STATE))
    assert output["_llm"] is True
    assert output["llm_model"] == "openai/gpt-oss-20b"
    assert output["prompt_version"] == "credit-explain-v1"
    assert "8.000.000" in output["summary"]
    assert "fallback_reason" not in output


def test_groq_request_uses_plain_mode_not_json_object(monkeypatch):
    """Groq gpt-oss rejects response_format=json_object (verified live) — plain only."""
    import pipeline.agent.llm_provider as provider

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        raise RuntimeError("stop before network")

    monkeypatch.setattr(provider.httpx, "post", fake_post)
    provider.try_groq_explain({"risk_score": 0.9})
    assert captured["url"] == provider.GROQ_CHAT_URL
    assert "response_format" not in captured["payload"]
    assert captured["payload"]["model"] == "openai/gpt-oss-20b"


def test_groq_missing_key_falls_back_to_template(monkeypatch):
    from pipeline.agent.explanations import generate_explanation

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    output = generate_explanation(dict(CASE_STATE))
    assert not output.get("_llm")
    assert output["summary"]


def test_balanced_json_repairs_common_small_model_defects():
    import json

    from pipeline.agent.llm_provider import _balanced_json

    assert _balanced_json('{summary: "ok", confidence: "high"}') == {"summary": "ok", "confidence": "high"}
    assert _balanced_json('```json\n{"summary": "ok"}\n```') == {"summary": "ok"}
    assert _balanced_json('{"summary": "ok", "risk_factors": "DTI cao"}') == {
        "summary": "ok",
        "risk_factors": ["DTI cao"],
    }
    with pytest.raises(ValueError):
        _balanced_json("không có JSON nào ở đây")


def test_200_parses_structured(monkeypatch):
    _serve(monkeypatch, '{"summary": "ok", "risk_factors": ["a"], "recommendation_note": "b", "confidence": "high"}')
    import pipeline.agent.llm_provider as P

    out = P.try_cloudflare_explain({"risk_score": 0.9})
    assert out == {
        "summary": "ok",
        "risk_factors": ["a"],
        "recommendation_note": "b",
        "confidence": "high",
        "_llm": True,
        "prompt_version": "credit-explain-v1",
        "llm_model": "@cf/test",
    }


def test_grounded_llm_text_is_accepted_with_provenance(monkeypatch):
    import json

    from pipeline.agent.explanations import generate_explanation

    _serve(
        monkeypatch,
        json.dumps(
            {
                "summary": "Rủi ro cao: thu nhập 8.000.000 VND nhưng vay 120.000.000 VND.",
                "risk_factors": ["DTI 1.875 vượt ngưỡng", "Nợ hiện có 15.000.000 VND"],
                "recommendation_note": "Kiểm tra lại nguồn trả nợ cho kỳ hạn 36 tháng.",
                "confidence": "high",
            }
        ),
    )
    output = generate_explanation(dict(CASE_STATE))
    assert output["_llm"] is True
    assert output["prompt_version"] == "credit-explain-v1"
    assert output["llm_model"] == "@cf/test"
    assert "8.000.000" in output["summary"]
    assert "fallback_reason" not in output


def test_fabricated_profile_numbers_fall_back_to_template(monkeypatch):
    import json

    from pipeline.agent.explanations import _build_context, _template_explanation, generate_explanation

    _serve(
        monkeypatch,
        json.dumps(
            {
                "summary": "Hồ sơ tốt với thu nhập 50.000.000 VND và 3 lần vỡ nợ.",
                "risk_factors": ["Thu nhập 50.000.000 VND"],
                "recommendation_note": "Phê duyệt ngay.",
                "confidence": "high",
            }
        ),
    )
    output = generate_explanation(dict(CASE_STATE))
    expected_template = _template_explanation(_build_context(dict(CASE_STATE)))
    assert not output.get("_llm")
    assert output["fallback_reason"] == "ungrounded_numbers"
    assert "50.000.000" in output["ungrounded_numbers"]
    assert "50.000.000" not in output["summary"]
    assert output["summary"] == expected_template["summary"]


def test_injected_decision_and_risk_score_are_dropped(monkeypatch):
    import json

    import pipeline.agent.explanations as explanations

    monkeypatch.setattr(
        explanations,
        "_try_llm_explanation",
        lambda ctx: {
            "summary": "Hồ sơ rủi ro cao, đề nghị xem xét thủ công.",
            "risk_factors": ["DTI 1.875"],
            "recommendation_note": "Kiểm tra chứng từ thu nhập.",
            "confidence": "medium",
            "decision": "APPROVE",
            "risk_score": 0.01,
            "risk_level": "LOW",
            "_llm": True,
            "prompt_version": "credit-explain-v1",
            "llm_model": "@cf/test",
        },
    )
    state = dict(CASE_STATE)
    original = json.loads(json.dumps(state))
    output = explanations.generate_explanation(state)
    assert output["_llm"] is True
    assert "decision" not in output
    assert "risk_score" not in output
    assert "risk_level" not in output
    assert state == original
    assert state["decision"] == "REJECT"


def test_malformed_llm_output_falls_back(monkeypatch):
    import pipeline.agent.explanations as explanations

    monkeypatch.setattr(explanations, "_try_llm_explanation", lambda ctx: {"summary": ""})
    output = explanations.generate_explanation(dict(CASE_STATE))
    assert output["fallback_reason"] == "malformed_output"
    assert not output.get("_llm")


def test_provider_exception_is_contained_and_decision_untouched(monkeypatch):
    import pipeline.agent.explanations as explanations
    import pipeline.agent.llm_provider as provider
    from pipeline.agent.nodes import explain

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")

    def explode(*args, **kwargs):
        raise RuntimeError("isolated provider failure")

    monkeypatch.setattr(provider.httpx, "post", explode)
    state = {**CASE_STATE, "audit_trail": []}
    output = explanations.generate_explanation(dict(state))
    assert output["summary"]
    assert not output.get("_llm")
    assert output["fallback_reason"] == "provider_unavailable"

    updates = explain(state)
    assert "decision" not in updates
    assert updates["explanation_meta"]["source"] == "template"
    assert state["decision"] == "REJECT"

    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    import pipeline.agent.llm_provider as P

    class R:
        status_code = 200

        def json(self):
            return {"result": {"response": '{"summary":"ok","risk_factors":["a"],"recommendation_note":"b","confidence":"high"}'}}

    monkeypatch.setattr(P.httpx, "post", lambda *a, **k: R())
    out = P.try_cloudflare_explain({"risk_score": 0.9, "risk_level": "HIGH"})
    assert out["summary"] == "ok" and out["_llm"] is True


def test_generate_marks_llm_source(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "x")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "y")
    import pipeline.agent.llm_provider as P
    import pipeline.agent.explanations as E
    marker = {"summary": "s", "risk_factors": ["r"], "recommendation_note": "n", "confidence": "high", "_llm": True}
    monkeypatch.setattr(P, "try_cloudflare_explain", lambda ctx: marker)
    out = E._try_llm_explanation({"risk_score": 0.9, "data_classification": "PUBLIC"})
    assert out is marker


def test_policy_guard_blocks_confidential_data_to_public_cloud(monkeypatch, capsys):
    """Default CONFIDENTIAL state must never leave the process via a public provider."""
    import pipeline.agent.explanations as E

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "groq")
    monkeypatch.delenv("CREDITFLOW_ALLOW_EXTERNAL_LLM", raising=False)
    assert E._try_llm_explanation({"risk_score": 0.9}) is None
    assert "Policy Guard" in capsys.readouterr().out

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    assert E._try_llm_explanation({"risk_score": 0.9}) is None


def test_policy_guard_allow_external_env_opt_in(monkeypatch, capsys):
    """CREDITFLOW_ALLOW_EXTERNAL_LLM=1 is the explicit, documented opt-in."""
    import pipeline.agent.llm_provider as P
    import pipeline.agent.explanations as E

    marker = {"summary": "s", "risk_factors": ["r"], "recommendation_note": "n", "confidence": "high", "_llm": True}
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "groq")
    monkeypatch.setenv("CREDITFLOW_ALLOW_EXTERNAL_LLM", "1")
    monkeypatch.setattr(P, "try_groq_explain", lambda ctx: marker)
    out = E._try_llm_explanation({"risk_score": 0.9})  # no data_classification -> CONFIDENTIAL
    assert out is marker


def test_provider_timeout_uses_template_without_changing_decision(monkeypatch):
    from copy import deepcopy
    import pipeline.agent.explanations as explanations
    import pipeline.agent.llm_provider as provider

    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test")
    def timeout(*args, **kwargs):
        raise TimeoutError("isolated provider failure")
    monkeypatch.setattr(provider.httpx, "post", timeout)
    state = {"customer_data": {"income": 8000000, "loan_amount": 120000000},
             "risk_score": 0.9, "risk_level": "HIGH", "decision": "REJECT"}
    original = deepcopy(state)
    output = explanations.generate_explanation(state)
    assert output["summary"]
    assert not output.get("_llm")
    assert state == original
    assert "decision" not in output and "risk_score" not in output
