"""Tests for the TF-IDF policy retriever (TDD RED first)."""
from __future__ import annotations


def test_dti_query_hits_dti_doc():
    from pipeline.agent.retriever import retrieve
    hits = retrieve("existing_debt income debt-to-income exceeds 1.0", k=2)
    assert hits and hits[0]["doc_id"] == "dti"


def test_empty_query_returns_empty():
    from pipeline.agent.retriever import retrieve
    assert retrieve("", k=2) == []


def test_retrieve_returns_score_and_chunk():
    from pipeline.agent.retriever import retrieve
    hits = retrieve("loan_amount income extreme 20", k=2)
    assert hits and hits[0]["doc_id"] == "lti"
    assert "score" in hits[0] and "chunk" in hits[0]
    assert isinstance(hits[0]["score"], float)