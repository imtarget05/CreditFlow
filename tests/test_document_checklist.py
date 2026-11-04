"""Missing-document checks never claim that metadata proves document validity."""

from backend.app import get_document_checklist
from backend.security import Principal
from pipeline.workflow.documents import evaluate_document_checklist


def test_missing_required_document_is_flagged():
    result = evaluate_document_checklist(
        [{"document_type": "identity", "file_size_bytes": 0}],
        ["identity", "income_verification"],
    )
    assert result["status"] == "MISSING"
    assert result["missing"] == ["income_verification"]


def test_metadata_complete_still_requires_human_verification():
    result = evaluate_document_checklist(
        [{"document_type": "identity", "file_size_bytes": 0}], ["identity"]
    )
    assert result["status"] == "REVIEW_REQUIRED"


def test_no_policy_is_not_a_pass():
    assert evaluate_document_checklist([], [])["status"] == "UNCONFIGURED"


def test_checklist_endpoint_uses_configured_policy(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_REQUIRED_DOCUMENT_TYPES", "identity,income_verification")
    monkeypatch.setattr("backend.app.list_application_documents", lambda case_id, principal: {
        "application_id": case_id,
        "documents": [{"document_type": "identity"}],
    })
    response = get_document_checklist("APP-1", Principal("staff", "UNDERWRITER_L1"))
    assert response["missing"] == ["income_verification"]
