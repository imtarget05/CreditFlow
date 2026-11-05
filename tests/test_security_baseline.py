"""Production boundaries for applicant data and demo authentication."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app import (
    CoreBankingInitiateRequest,
    GraphStartRequest,
    PredictRequest,
    app,
    core_banking_initiate,
    core_banking_transactions,
    decide_loan_application,
    predict,
    start_graph_workflow,
    upload_application_documents,
)
from backend.security import (
    Principal,
    authenticate_user,
    create_access_token,
    require_configured_key,
)

PROFILE = {
    "income": 8000000,
    "age": 35,
    "employment_years": 8,
    "loan_amount": 120000000,
    "loan_term": 36,
    "existing_debt": 15000000,
    "credit_history": 9,
    "previous_defaults": 0,
}


def test_builtin_user_is_not_available_without_demo_configuration(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_DEMO_USERS_JSON", raising=False)
    assert authenticate_user("underwriter1", "Underwriter@123") is None


def test_local_token_cannot_be_signed_without_configured_secret(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="CREDITFLOW_JWT_SECRET"):
        create_access_token("analyst", "UNDERWRITER_L1")


def test_login_is_disabled_unless_demo_is_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_ENABLE_DEMO_LOGIN", raising=False)
    response = TestClient(app).post(
        "/api/auth/login", json={"username": "underwriter1", "password": "Underwriter@123"}
    )
    assert response.status_code == 404


def test_graph_start_requires_authentication(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_API_KEY", raising=False)
    monkeypatch.delenv("CREDITFLOW_ALLOW_ANONYMOUS_DEMO", raising=False)
    response = TestClient(app).post("/predict/graph", json={"customer_data": PROFILE})
    assert response.status_code == 401


def test_production_predict_requires_authentication(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.delenv("CREDITFLOW_API_KEY", raising=False)
    response = TestClient(app).post("/predict", json=PROFILE)
    assert response.status_code == 401


def test_production_refuses_to_start_without_oidc_configuration(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setenv("CREDITFLOW_API_KEY", "service-key-for-tests")
    for name in ("CREDITFLOW_OIDC_ISSUER", "CREDITFLOW_OIDC_AUDIENCE", "CREDITFLOW_OIDC_JWKS_URL", "CREDITFLOW_OIDC_ROLE_MAP_JSON"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(RuntimeError, match="CREDITFLOW_OIDC"):
        require_configured_key()


def test_production_scoring_refuses_synthetic_model(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    with pytest.raises(HTTPException) as exc:
        predict(PredictRequest(**PROFILE))
    assert exc.value.status_code == 503
    assert exc.value.detail == "MODEL_NOT_VALIDATED_FOR_PRODUCTION"


def test_production_graph_cannot_create_demo_credit_decision(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    with pytest.raises(HTTPException) as exc:
        start_graph_workflow(GraphStartRequest(customer_data=PROFILE), principal=None)
    assert exc.value.status_code == 503
    assert exc.value.detail == "PRODUCTION_WORKFLOW_NOT_INTEGRATED"


def test_production_decision_route_cannot_replay_demo_snapshot(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    with pytest.raises(HTTPException) as exc:
        decide_loan_application("demo-application", {"action": "approve"}, principal=None)
    assert exc.value.status_code == 503
    assert exc.value.detail == "PRODUCTION_APPROVAL_NOT_INTEGRATED"


def test_production_document_upload_requires_approved_storage(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    with pytest.raises(HTTPException) as exc:
        upload_application_documents("demo-application", {"file_name": "private.pdf"}, principal=None)
    assert exc.value.status_code == 503
    assert exc.value.detail == "DOCUMENT_STORAGE_NOT_INTEGRATED"


def test_demo_stp_cannot_execute_without_human_checker(monkeypatch):
    from pipeline.agent import nodes

    class ForbiddenAdapter:
        def initiate_disbursement(self, **kwargs):
            raise AssertionError("No transfer adapter call without a human checker")

    monkeypatch.setattr(nodes, "_core_banking", ForbiddenAdapter())
    result = nodes.execute({"application_id": "APP-DEMO", "customer_data": PROFILE, "audit_trail": []})
    assert result["core_banking_status"] == "PENDING_HUMAN_CHECKER"
    assert result["disbursement_executed"] is False


def test_core_banking_sandbox_cannot_start_in_production(monkeypatch):
    from pipeline.disbursement.core_banking_adapter import CoreBankingAdapter

    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    with pytest.raises(RuntimeError, match="SANDBOX_DISABLED_IN_PRODUCTION"):
        CoreBankingAdapter().initiate_disbursement(
            application_id="APP-1", loan_amount=1000000,
            account_number="0000000001", account_name="Demo",
            idempotency_key="production-guard",
        )


def test_core_banking_api_is_closed_in_production(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    actor = Principal("analyst", "UNDERWRITER_L1")
    body = CoreBankingInitiateRequest(
        application_id="APP-1", loan_amount=1000000,
        account_number="0000000001", account_name="Demo",
        idempotency_key="production-guard",
    )
    with pytest.raises(HTTPException) as initiate:
        core_banking_initiate(body, principal=actor)
    assert initiate.value.detail == "CORE_BANKING_NOT_INTEGRATED"
    with pytest.raises(HTTPException) as read:
        core_banking_transactions(principal=actor)
    assert read.value.detail == "CORE_BANKING_NOT_INTEGRATED"


def test_demo_core_banking_rejects_self_asserted_maker(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "development")
    body = CoreBankingInitiateRequest(
        application_id="APP-1", loan_amount=1000000,
        account_number="0000000001", account_name="Demo",
        idempotency_key="maker-spoof", maker_id="another-person",
    )
    with pytest.raises(HTTPException) as exc:
        core_banking_initiate(body, principal=Principal("analyst", "UNDERWRITER_L1"))
    assert exc.value.detail == "MAKER_ID_NOT_AUTHORIZED"
