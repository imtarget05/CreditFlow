"""Keep the shareable synthetic app cases executable against the scoring API."""

from fastapi.testclient import TestClient

from backend.app import app
from scripts.run_demo_app_cases import load_cases


def test_demo_app_cases_match_local_scoring_contract(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "development")
    cases = load_cases()
    assert len(cases) >= 10
    assert {case["expected_http_status"] for case in cases} == {200, 422}

    with TestClient(app) as client:
        for case in cases:
            response = client.post("/predict", json=case["profile"])
            assert response.status_code == case["expected_http_status"], (
                case["id"], response.text
            )
            if response.status_code == 200:
                body = response.json()
                assert 0 <= body["risk_probability"] <= 1
                assert body["model_version"]
