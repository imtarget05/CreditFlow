"""Tests for the explanation eval harness (offline, no secrets)."""
from __future__ import annotations


def test_eval_schema_offline(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setenv("CREDITFLOW_LLM_PROVIDER", "")
    from scripts.eval_explanations import run_eval
    out = run_eval([{"income": 5000, "age": 35, "employment_years": 10,
                     "loan_amount": 20000, "loan_term": 36, "existing_debt": 3000,
                     "credit_history": 8, "previous_defaults": 0}])
    assert out["n"] == 1 and out["json_valid_rate"] == 1.0 and "faithfulness" in out


def test_llm_info_no_secret_leak():
    from fastapi.testclient import TestClient
    from backend.app import app
    with TestClient(app) as c:
        r = c.get("/llm/info")
        assert r.status_code == 200
        body = r.json()
        assert "provider" in body and "configured" in body
        assert "TOKEN" not in r.text and "token" not in body
