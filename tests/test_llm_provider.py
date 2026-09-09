"""Tests for the Cloudflare Workers AI explain backend (TDD RED first).

No real API calls: transport is monkeypatched. CI needs no secrets.
"""
from __future__ import annotations


def test_missing_key_returns_none(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    from pipeline.agent.llm_provider import try_cloudflare_explain
    assert try_cloudflare_explain({"risk_score": 0.9}) is None


def test_200_parses_structured(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "cloudflare")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-id")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    import pipeline.agent.llm_provider as P

    class R:
        status_code = 200

        def json(self):
            return {"result": {"response": '{"summary":"ok","risk_factors":["a"],"recommendation_note":"b","confidence":"high"}'}}

    monkeypatch.setattr(P.httpx, "post", lambda *a, **k: R())
    out = P.try_cloudflare_explain({"risk_score": 0.9, "risk_level": "HIGH"})
    assert out["summary"] == "ok" and out["_llm"] is True
