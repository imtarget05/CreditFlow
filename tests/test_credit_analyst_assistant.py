"""Read-only analyst brief and role-scoped assistant API contract."""
from fastapi.testclient import TestClient

from backend.app import app
from pipeline.agent.subagents.credit_analyst_assistant import CreditAnalystAssistant


def test_assistant_brief_is_grounded_and_never_decides(monkeypatch):
    monkeypatch.setattr("pipeline.agent.subagents.credit_analyst_assistant.retrieve", lambda query, k: [
        {"doc_id": "underwriting_policy", "chunk": "Verify documented income.", "score": 0.7}
    ])
    monkeypatch.setattr("pipeline.agent.subagents.credit_analyst_assistant.generate_explanation", lambda state: {
        "summary": "Hồ sơ đang chờ chuyên viên rà soát.", "risk_factors": ["high debt-to-income ratio"]
    })
    item = {"id": "work-1", "kind": "UNDERWRITING", "status": "OPEN", "assigned_role": "UNDERWRITER_L1", "application_id": "APP-1"}
    application = {
        "application_id": "APP-1", "status": "PENDING_REVIEW", "decision": "REVIEW",
        "risk_level": "MEDIUM", "risk_score": 0.52, "reasons": ["high debt-to-income ratio"],
        "customer_data": '{"income": 5000000}',
    }
    result = CreditAnalystAssistant().review(item, application)
    assert result["case_facts"]["decision"] == "REVIEW"
    assert result["evidence_factors"] == ["high debt-to-income ratio"]
    assert result["policy_sources"][0]["document"] == "underwriting_policy"
    assert "không phê duyệt" in result["limitations"]
    assert "decision" not in result


def test_assistant_marks_recomputed_current_model_evidence(monkeypatch):
    monkeypatch.setattr("pipeline.agent.subagents.credit_analyst_assistant.retrieve", lambda query, k: [])
    monkeypatch.setattr("pipeline.agent.subagents.credit_analyst_assistant.generate_explanation", lambda state: {})
    item = {"id": "work-2", "kind": "UNDERWRITING", "status": "OPEN", "assigned_role": "UNDERWRITER_L1", "application_id": "APP-2"}
    application = {
        "application_id": "APP-2", "status": "PENDING_REVIEW", "decision": "REVIEW",
        "risk_level": "MEDIUM", "risk_score": .5, "reasons": [], "customer_data": {},
        "current_model_evidence": {"model_version": "v2", "risk_probability": .5, "reasons": ["high debt-to-income ratio"]},
    }
    result = CreditAnalystAssistant().review(item, application)
    assert result["evidence_factors"] == ["high debt-to-income ratio"]
    assert result["evidence_origin"] == "recomputed_with_current_model"


def test_assistant_endpoint_requires_authentication():
    response = TestClient(app).post("/api/work-items/not-real/assistant")
    assert response.status_code == 401


def test_assistant_endpoint_is_role_scoped(monkeypatch):
    from tests.auth_support import AUTH_HEADERS, configure_auth_env
    configure_auth_env(monkeypatch)
    monkeypatch.setattr("backend.app.get_work_item", lambda _id: {
        "id": "work-1", "application_id": "APP-1", "assigned_role": "RISK_COMMITTEE_L2",
        "assigned_to": "someone-else", "kind": "UNDERWRITING", "status": "OPEN",
    })
    monkeypatch.setattr("backend.app.get_application_by_id", lambda _id: {"application_id": "APP-1"})
    response = TestClient(app).post("/api/work-items/work-1/assistant", headers=AUTH_HEADERS)
    assert response.status_code == 404


def test_assistant_endpoint_returns_brief_without_mutating_work_item(monkeypatch):
    from tests.auth_support import AUTH_HEADERS, configure_auth_env

    configure_auth_env(monkeypatch)
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "UNDERWRITER_L1")
    item = {"id": "work-1", "application_id": "APP-1", "assigned_role": "UNDERWRITER_L1",
            "assigned_to": "", "kind": "UNDERWRITING", "status": "OPEN"}
    monkeypatch.setattr("backend.app.get_work_item", lambda _id: item)
    monkeypatch.setattr("backend.app.get_application_by_id", lambda _id: {
        "application_id": "APP-1", "status": "PENDING_REVIEW", "decision": "REVIEW",
        "risk_level": "MEDIUM", "risk_score": .4, "reasons": [], "customer_data": {},
    })
    monkeypatch.setattr(CreditAnalystAssistant, "review", lambda self, work_item, application: {
        "application_id": "APP-1", "summary": "Brief", "limitations": "Reviewer decides."
    })
    response = TestClient(app).post("/api/work-items/work-1/assistant", headers=AUTH_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "Brief"
    assert item["status"] == "OPEN"
