"""Repeated feature vectors are review signals, never customer identity proof."""

from pipeline.storage.ledger import init_db, record_application
from pipeline.workflow.duplicates import find_possible_profile_matches

PROFILE = {
    "income": 8000000, "age": 35, "employment_years": 8,
    "loan_amount": 120000000, "loan_term": 36, "existing_debt": 15000000,
    "credit_history": 9, "previous_defaults": 0,
}


def test_exact_repeat_flags_prior_case_without_merging(tmp_path):
    db = tmp_path / "cases.db"
    init_db(db)
    record_application("thread-a", PROFILE, .2, "LOW", "REVIEW", "PENDING_REVIEW", application_id="APP-A", db_path=db)
    first = find_possible_profile_matches({**PROFILE, "income": 8000000.0}, db_path=db)
    assert first == ["APP-A"]
    assert find_possible_profile_matches(PROFILE, exclude_thread_id="thread-a", db_path=db) == []


def test_incomplete_or_different_profile_is_not_called_duplicate(tmp_path):
    db = tmp_path / "cases.db"
    init_db(db)
    record_application("thread-a", PROFILE, .2, "LOW", "REVIEW", "PENDING_REVIEW", application_id="APP-A", db_path=db)
    assert find_possible_profile_matches({"income": 8000000}, db_path=db) == []
    assert find_possible_profile_matches({**PROFILE, "loan_amount": 130000000}, db_path=db) == []
