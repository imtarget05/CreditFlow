"""Authentication + authorisation on the money-moving and PII endpoints.

Regression tests for the anonymous-approval hole: before this layer,
``POST /predict/graph/{thread_id}/approve`` accepted any caller, took
``approver_id`` straight from the request body, and wrote a ``disbursements``
row.

Conventions follow tests/test_api.py (TestClient over the real app, real
ledger) and tests/test_approval_idempotency.py (assert on ledger rows, not
just status codes).
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contextlib import ExitStack

import pytest
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from backend import security
from backend.app import app
from backend.security import (
    AUTHORITY_RANK,
    Principal,
    allowed_origins,
    ensure_authority,
    require_configured_key,
    require_principal,
)
from tests.auth_support import (
    API_KEY,
    API_KEY_HEADER,
    APPROVER_ID,
    AUTH_HEADERS,
    configure_auth_env,
)

# The real app, lifespan open for the whole module (model + ledger loaded once)
# so the slow approve tests below exercise the same object the deploy runs.
_stack = ExitStack()
client = _stack.enter_context(TestClient(app))

PROTECTED_READS = (
    "/applications",
    "/api/applications",
    "/disbursements",
    "/api/disbursements",
)

APPROVE = "/predict/graph/{thread_id}/approve"


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    """A known key/identity/role for every test in this module."""
    configure_auth_env(monkeypatch)
    monkeypatch.delenv("CREDITFLOW_ENV", raising=False)
    monkeypatch.delenv("CREDITFLOW_CORS_ORIGINS", raising=False)


# ---------------------------------------------------------------------------
# 401: absent key, wrong key
# ---------------------------------------------------------------------------
def test_approve_without_api_key_is_401():
    r = client.post(APPROVE.format(thread_id="no-such-thread"), json={"action": "approve"})
    assert r.status_code == 401
    assert "INVALID_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize(
    "bad_key",
    [
        "wrong-key",
        API_KEY[:-1],          # same length, last byte differs
        "X" + API_KEY[1:],     # same length, first byte differs
        API_KEY + "x",         # correct key as a prefix
    ],
)
def test_approve_with_wrong_api_key_is_401(bad_key):
    r = client.post(
        APPROVE.format(thread_id="no-such-thread"),
        json={"action": "approve"},
        headers={API_KEY_HEADER: bad_key},
    )
    assert r.status_code == 401, r.text
    assert "INVALID_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize("path", PROTECTED_READS)
def test_evidence_endpoints_require_a_key(path):
    assert client.get(path).status_code == 401
    assert client.get(path, headers={API_KEY_HEADER: "wrong-key"}).status_code == 401


# ---------------------------------------------------------------------------
# 200: the correct key is allowed through
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", PROTECTED_READS)
def test_correct_key_allows_evidence_endpoints(path):
    r = client.get(path, headers=AUTH_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = body["applications"] if "applications" in body else body["disbursements"]
    assert body["count"] == len(rows)


# ---------------------------------------------------------------------------
# The two formerly public read routes: workflow state + audit trail.
# Both expose PII-bearing workflow data and must require the shared key.
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_graph_state_and_audit_require_api_key(authed_client):
    _profile, started = _start_review_workflow(authed_client)
    thread_id = started["thread_id"]
    application_id = started["application_id"]

    for path in (f"/predict/graph/{thread_id}", f"/audit/{application_id}"):
        r = authed_client.get(path)
        assert r.status_code == 401, (path, r.text)
        assert "INVALID_API_KEY" in r.json()["detail"] or "API_KEY_NOT_CONFIGURED" in r.json()["detail"]
        r = authed_client.get(path, headers={API_KEY_HEADER: "wrong-key"})
        assert r.status_code == 401, (path, r.text)

    for path in (f"/predict/graph/{thread_id}", f"/audit/{application_id}"):
        r = authed_client.get(path, headers=AUTH_HEADERS)
        assert r.status_code == 200, (path, r.text)


def test_correct_key_can_read_and_filter_applications():
    r = client.get("/applications", params={"status": "APPROVED"}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert all(a["status"] == "APPROVED" for a in r.json()["applications"])


# ---------------------------------------------------------------------------
# Fail closed: an unset key never means "authentication disabled"
# ---------------------------------------------------------------------------
def test_unset_key_closes_protected_routes_outside_production(monkeypatch):
    """No configured secret => nobody authenticates (not: everybody does)."""
    monkeypatch.delenv("CREDITFLOW_API_KEY", raising=False)
    assert client.post(
        APPROVE.format(thread_id="no-such-thread"), json={"action": "approve"}
    ).status_code == 401
    for path in PROTECTED_READS:
        assert client.get(path).status_code == 401


def test_production_without_api_key_refuses_to_start(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.delenv("CREDITFLOW_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        require_configured_key()
    message = str(exc.value)
    assert "CREDITFLOW_API_KEY" in message and "production" in message
    # ... and the app itself will not come up.
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass


def test_production_with_api_key_starts(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    require_configured_key()  # must not raise
    with TestClient(app):
        pass


def test_require_principal_rejects_missing_key_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CREDITFLOW_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_principal(API_KEY)
    assert exc.value.status_code == 401
    assert "API_KEY_NOT_CONFIGURED" in exc.value.detail


# ---------------------------------------------------------------------------
# approver_id comes from the authenticated identity, never the body
# ---------------------------------------------------------------------------
def test_approver_id_cannot_be_spoofed_via_the_body():
    r = client.post(
        APPROVE.format(thread_id="no-such-thread"),
        json={"action": "approve", "approver_id": "ceo_approver"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422, r.text
    assert "APPROVER_ID_NOT_AUTHORIZED" in r.json()["detail"]


def test_approver_id_matching_the_authenticated_identity_is_accepted():
    """The field is only a mirror of the identity, never a source of it."""
    r = client.post(
        APPROVE.format(thread_id="run-never-started-authz"),
        json={"action": "approve", "approver_id": APPROVER_ID},
        headers=AUTH_HEADERS,
    )
    # The thread does not exist, so the request fails on the workflow, not auth.
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Authorisation: the credit authority matrix, not a request field
# ---------------------------------------------------------------------------
def test_authority_matrix_orders_the_existing_roles():
    assert AUTHORITY_RANK["SYSTEM_STP"] < AUTHORITY_RANK["UNDERWRITER_L1"]
    assert AUTHORITY_RANK["UNDERWRITER_L1"] < AUTHORITY_RANK["RISK_COMMITTEE_L2"]


def test_underwriter_cannot_authorise_a_risk_committee_approval():
    principal = Principal(identity=APPROVER_ID, role="UNDERWRITER_L1")
    with pytest.raises(HTTPException) as exc:
        ensure_authority(principal, "RISK_COMMITTEE_L2")
    assert exc.value.status_code == 403
    assert "INSUFFICIENT_AUTHORITY" in exc.value.detail


def test_underwriter_can_authorise_an_underwriter_approval():
    ensure_authority(Principal(identity=APPROVER_ID, role="UNDERWRITER_L1"), "UNDERWRITER_L1")


def test_unknown_or_missing_required_authority_is_treated_as_highest():
    principal = Principal(identity=APPROVER_ID, role="UNDERWRITER_L1")
    for required in (None, "", "SOME_OTHER_ROLE"):
        with pytest.raises(HTTPException) as exc:
            ensure_authority(principal, required)
        assert exc.value.status_code == 403


def test_configured_role_must_be_a_known_authority(monkeypatch):
    """A misconfigured role is refused loudly, never silently downgraded."""
    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "root")
    with pytest.raises(RuntimeError):
        security.configured_approver_role()
    with pytest.raises(RuntimeError):
        require_principal(API_KEY)
    with pytest.raises(RuntimeError):
        require_configured_key()


# ---------------------------------------------------------------------------
# CORS: explicit origins, never a wildcard, never with credentials
# ---------------------------------------------------------------------------
def _cors_middleware():
    return next(m for m in app.user_middleware if m.cls is CORSMiddleware)


def test_cors_uses_an_explicit_origin_list():
    options = _cors_middleware().kwargs
    assert "*" not in options["allow_origins"]
    assert options["allow_origins"] == list(security.DEFAULT_CORS_ORIGINS)
    # A wildcard is never combined with credentials.
    assert options["allow_credentials"] is False
    assert "*" not in options["allow_methods"]


def test_cors_origins_are_configurable(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_CORS_ORIGINS", "https://app.example.com, https://ops.example.com/")
    assert allowed_origins() == ["https://app.example.com", "https://ops.example.com"]


def test_cors_wildcard_is_refused(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError) as exc:
        allowed_origins()
    assert "CREDITFLOW_CORS_ORIGINS" in str(exc.value)


# ---------------------------------------------------------------------------
# End-to-end through the real workflow — SLOW (full 12-node LangGraph).
# The money assertion is on the disbursements row count, not the status code.
# ---------------------------------------------------------------------------
BASE_PROFILE = {
    "income": 8000000.0,
    "age": 35,
    "employment_years": 8.0,
    "loan_amount": 120000000.0,
    "loan_term": 36,
    "existing_debt": 2000000.0,
    "credit_history": 9.0,
    "previous_defaults": 0,
}
REVIEW_CANDIDATES = [
    dict(BASE_PROFILE, previous_defaults=d, employment_years=e)
    for d in (1, 2)
    for e in (0.5, 2.0)
]


@pytest.fixture()
def authed_client(tmp_path, monkeypatch):
    """Isolated ledger + a live app, with the test key configured."""
    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(tmp_path / "ledger_auth.db"))
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", str(tmp_path / "checkpoints_auth.pkl"))
    from pipeline.storage.ledger import init_db

    init_db()
    with TestClient(app) as c:
        yield c


def _start_review_workflow(c: TestClient) -> tuple[dict, dict]:
    last = None
    for profile in REVIEW_CANDIDATES:
        r = c.post("/predict/graph", json={"customer_data": profile})
        assert r.status_code == 200, r.text
        last = (profile, r.json())
        if last[1].get("approval_required"):
            return last
    pytest.fail(f"no candidate profile produced a REVIEW workflow; last: {last}")


def _disbursement_count(db_path) -> int:
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM disbursements").fetchone()[0]


@pytest.mark.slow
def test_anonymous_caller_cannot_disburse(authed_client, tmp_path):
    _profile, started = _start_review_workflow(authed_client)
    thread_id = started["thread_id"]
    db = tmp_path / "ledger_auth.db"

    r = authed_client.post(
        APPROVE.format(thread_id=thread_id), json={"action": "approve"}
    )
    assert r.status_code == 401, r.text
    assert _disbursement_count(db) == 0, "an anonymous approval must never disburse"
    # Still pending: the workflow is untouched, not half-approved.
    from pipeline.storage.ledger import get_application_by_thread

    assert get_application_by_thread(thread_id)["status"] == "PENDING_REVIEW"


@pytest.mark.slow
def test_insufficient_authority_is_403_and_writes_no_disbursement(
    authed_client, tmp_path, monkeypatch
):
    _profile, started = _start_review_workflow(authed_client)
    thread_id = started["thread_id"]
    db = tmp_path / "ledger_auth.db"

    # 120M VND routes to the Risk Committee level of the authority matrix.
    state = authed_client.get(f"/predict/graph/{thread_id}", headers=AUTH_HEADERS).json()
    assert state["authority_level"] == "RISK_COMMITTEE_L2", state["authority_level"]

    monkeypatch.setenv("CREDITFLOW_API_KEY_ROLE", "UNDERWRITER_L1")
    r = authed_client.post(
        APPROVE.format(thread_id=thread_id),
        json={"action": "approve"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 403, r.text
    assert "INSUFFICIENT_AUTHORITY" in r.json()["detail"]
    assert _disbursement_count(db) == 0, "a refused approval must write no disbursement"

    from pipeline.storage.ledger import get_application_by_thread

    assert get_application_by_thread(thread_id)["status"] == "PENDING_REVIEW"


@pytest.mark.slow
def test_authorised_approval_records_the_authenticated_approver(authed_client, tmp_path):
    profile, started = _start_review_workflow(authed_client)
    thread_id = started["thread_id"]
    db = tmp_path / "ledger_auth.db"

    r = authed_client.post(
        APPROVE.format(thread_id=thread_id),
        json={"action": "approve", "note": "checked the payslips"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ledger_status"] == "APPROVED"
    assert body["disbursement"] is not None
    assert body["disbursement"]["loan_amount"] == profile["loan_amount"]
    assert _disbursement_count(db) == 1

    with sqlite3.connect(str(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT approver_id FROM loan_applications WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    assert row["approver_id"] == APPROVER_ID, (
        "approver_id must come from the authenticated key, not the request body"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))