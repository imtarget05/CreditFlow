import os
import sys
from contextlib import ExitStack

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from backend.app import app
from tests.auth_support import AUTH_HEADERS, configure_auth_env

_stack = ExitStack()
client = _stack.enter_context(TestClient(app))


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    configure_auth_env(monkeypatch)


VALID_PROFILE = {
    "income": 12000000.0,
    "age": 30,
    "employment_years": 5.0,
    "loan_amount": 50000000.0,
    "loan_term": 24,
    "existing_debt": 5000000.0,
    "credit_history": 5.0,
    "previous_defaults": 0,
}


def test_applications_lifecycle():
    # 1. POST /applications creates/starts an application
    res = client.post("/applications", json=VALID_PROFILE, headers=AUTH_HEADERS)
    assert res.status_code == 200, res.text
    data = res.json()
    app_id = data.get("application_id")
    thread_id = data.get("thread_id")
    assert app_id or thread_id

    lookup_id = app_id or thread_id

    # 2. GET /applications/{id}
    res_get = client.get(f"/applications/{lookup_id}", headers=AUTH_HEADERS)
    assert res_get.status_code == 200, res_get.text
    app_data = res_get.json()
    assert app_data["status"] in ("PENDING_REVIEW", "APPROVED", "REJECTED")

    # 3. POST /applications/{id}/documents
    res_doc = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "income_verification", "file_name": "payslip.pdf"},
        headers=AUTH_HEADERS,
    )
    assert res_doc.status_code == 200
    assert res_doc.json()["status"] == "uploaded"

    # 4. POST /applications/{id}/score
    res_score = client.post(
        f"/applications/{lookup_id}/score",
        json={},
        headers=AUTH_HEADERS,
    )
    assert res_score.status_code == 200
    score_data = res_score.json()
    assert "risk_probability" in score_data

    # 5. GET /applications/{id}/explanation
    res_exp = client.get(f"/applications/{lookup_id}/explanation", headers=AUTH_HEADERS)
    assert res_exp.status_code == 200
    exp_data = res_exp.json()
    assert exp_data["application_id"] == lookup_id
    assert "reasons" in exp_data

    # 6. GET /applications/{id}/audit
    res_audit = client.get(f"/applications/{lookup_id}/audit", headers=AUTH_HEADERS)
    assert res_audit.status_code == 200


def _create_application():
    res = client.post("/applications", json=VALID_PROFILE, headers=AUTH_HEADERS)
    assert res.status_code == 200, res.text
    data = res.json()
    return data.get("application_id") or data.get("thread_id")


def test_documents_persist_and_list():
    lookup_id = _create_application()
    r1 = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "identity", "file_name": "cccd.png"},
        headers=AUTH_HEADERS,
    )
    assert r1.status_code == 200, r1.text
    doc_id = r1.json()["document_id"]
    r2 = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "bank_statement", "file_name": "stmt.pdf"},
        headers=AUTH_HEADERS,
    )
    assert r2.status_code == 200, r2.text

    listed = client.get(f"/applications/{lookup_id}/documents", headers=AUTH_HEADERS)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["count"] == 2
    assert [d["document_id"] for d in body["documents"]] == [
        doc_id,
        r2.json()["document_id"],
    ]
    assert body["documents"][0]["uploaded_by"] != ""


def test_documents_reject_unknown_application():
    r = client.post(
        "/applications/APP-does-not-exist/documents",
        json={"document_type": "identity", "file_name": "x.png"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404
    r = client.get(
        "/applications/APP-does-not-exist/documents", headers=AUTH_HEADERS
    )
    assert r.status_code == 404


def test_documents_reject_bad_type_and_traversal_name():
    lookup_id = _create_application()
    r = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "passport_xxx", "file_name": "p.pdf"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422
    r = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "identity", "file_name": "../../etc/passwd"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200, r.text
    assert "/" not in r.json()["file_name"]
    r = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "identity", "file_name": "   "},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422


def test_documents_require_api_key():
    lookup_id = _create_application()
    r = client.post(
        f"/applications/{lookup_id}/documents",
        json={"document_type": "identity", "file_name": "x.png"},
    )
    assert r.status_code == 401
    r = client.get(f"/applications/{lookup_id}/documents")
    assert r.status_code == 401
