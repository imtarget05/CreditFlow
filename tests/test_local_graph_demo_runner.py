"""Exercise the local graph demo's assertions without a network service."""

import io
import json
from urllib.error import HTTPError

import pytest

from scripts import run_local_graph_demo as demo


def test_runner_checks_review_replay_and_single_disbursement(monkeypatch):
    calls = {"approval": 0, "ledger": 0}
    disbursement = {"id": "one", "status": "COMPLETED", "ledger_hash": "a" * 64}

    def fake_request(_base_url, path, _api_key, body=None):
        if path == "/api/disbursements":
            calls["ledger"] += 1
            if calls["ledger"] == 1:
                return 200, {"count": 0, "disbursements": []}
            return 200, {"count": 1, "disbursements": [{"id": "one", "hash_valid": True}]}
        if path == "/predict/graph":
            return 200, {
                "thread_id": "run-123456abcdef", "decision": "REVIEW",
                "approval_required": True, "authority_level": "RISK_COMMITTEE_L2",
            }
        if path.endswith("/approve"):
            calls["approval"] += 1
            if calls["approval"] == 1:
                return 200, {"ledger_status": "APPROVED", "disbursement": disbursement}
            if calls["approval"] == 2:
                return 200, {"replay": True, "disbursement": disbursement}
            if body["action"] == "reject":
                return 409, {"detail": "IDEMPOTENCY_KEY_CONFLICT"}
            return 409, {"detail": "APPLICATION_NOT_PENDING"}
        if path == "/audit/run-123456abcdef":
            return 200, {"decision": "APPROVE"}
        raise AssertionError(path)

    monkeypatch.setattr(demo, "request", fake_request)
    monkeypatch.setattr(demo, "wait_for_health", lambda port: None)
    report = demo.run(18081, "local-test-key")
    assert report["complete"] is True
    assert report["disbursement_count_after"] == 1
    assert report["replay"] is True
    assert report["conflicting_action_status"] == 409
    assert report["different_key_after_terminal_status"] == 409


@pytest.mark.parametrize("port,key", [(0, "local-test-key"), (65536, "local-test-key"), (18081, "")])
def test_runner_rejects_bad_local_configuration(port, key):
    with pytest.raises(ValueError):
        demo.run(port, key)


def test_request_rejects_unexpected_path_without_network():
    with pytest.raises(ValueError, match="Unexpected demo API path"):
        demo.request("http://127.0.0.1:18081", "//evil.example/path", "key")


def test_request_reads_local_success_and_http_error(monkeypatch):
    class Response(io.BytesIO):
        status = 200

    response = Response(json.dumps({"count": 0}).encode())
    monkeypatch.setattr(demo, "urlopen", lambda request, timeout: response)
    assert demo.request("http://127.0.0.1:18081", "/api/disbursements", "key") == (200, {"count": 0})

    def raise_conflict(request, timeout):
        raise HTTPError(request.full_url, 409, "conflict", None, io.BytesIO(b'{"detail":"conflict"}'))

    monkeypatch.setattr(demo, "urlopen", raise_conflict)
    assert demo.request("http://127.0.0.1:18081", "/predict/graph", "key", {}) == (
        409, {"detail": "conflict"},
    )


def test_wait_for_health_retries_until_local_api_responds(monkeypatch):
    attempts = 0

    class Healthy(io.BytesIO):
        status = 200

    def fake_open(_url, timeout):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("connection refused")
        return Healthy()

    monkeypatch.setattr(demo, "urlopen", fake_open)
    monkeypatch.setattr(demo.time, "sleep", lambda _seconds: None)
    demo.wait_for_health(18081)
    assert attempts == 2
