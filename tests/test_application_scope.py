"""Bank staff can see only cases in their assigned role/identity queue."""

import pytest
from fastapi import HTTPException

from backend.app import (
    _ensure_application_scope,
    get_loan_application,
    list_loan_applications,
)
from backend.security import Principal


def test_production_case_access_requires_visible_work_item(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    principal = Principal("staff-a", "UNDERWRITER_L1")
    monkeypatch.setattr("backend.app.list_work_items", lambda actor: [{"application_id": "APP-1"}])
    _ensure_application_scope(principal, {"application_id": "APP-1", "thread_id": "run-1"})
    with pytest.raises(HTTPException) as denied:
        _ensure_application_scope(principal, {"application_id": "APP-2", "thread_id": "run-2"})
    assert denied.value.status_code == 404


def test_production_application_list_filters_unassigned_cases(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setattr("backend.app.list_work_items", lambda actor: [{"application_id": "APP-1"}])
    monkeypatch.setattr("backend.app.list_applications", lambda status=None: [
        {"application_id": "APP-1", "customer_data": {"income": 1}},
        {"application_id": "APP-2", "customer_data": {"income": 2}},
    ])
    result = list_loan_applications(principal=Principal("staff-a", "UNDERWRITER_L1"))
    assert [row["application_id"] for row in result["applications"]] == ["APP-1"]


def test_production_application_detail_hides_other_staff_case(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")
    monkeypatch.setattr("backend.app.get_application_by_id", lambda case_id: {"application_id": case_id, "customer_data": {"income": 100}})
    monkeypatch.setattr("backend.app.list_work_items", lambda actor: [{"application_id": "APP-1"}])
    principal = Principal("staff-a", "UNDERWRITER_L1")
    assert get_loan_application("APP-1", principal)["application_id"] == "APP-1"
    with pytest.raises(HTTPException) as hidden:
        get_loan_application("APP-2", principal)
    assert hidden.value.status_code == 404
