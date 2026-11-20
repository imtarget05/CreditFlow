"""Check that the restart drill verifies both state files and exactly one effect."""

import pytest

from scripts import run_restart_drill as drill


def test_restart_drill_requires_two_restarts_and_one_disbursement(monkeypatch):
    calls = {"restarts": 0, "approvals": 0, "ledger": 0}

    def fake_docker(args):
        if args[0] == "restart":
            calls["restarts"] += 1
        if args[0] == "run":
            assert "CREDITFLOW_CHECKPOINT_DB=/app/data/checkpoints.pkl" in args
            assert "CREDITFLOW_LEDGER_DB=/app/data/ledger.db" in args
        if args[0] == "image":
            return "sha256:test"
        return "container-id"

    def fake_request(_base, path, _key, body=None):
        if path == "/api/disbursements":
            calls["ledger"] += 1
            if calls["ledger"] == 1:
                return 200, {"count": 0, "disbursements": []}
            return 200, {"count": 1, "disbursements": [{"id": 1, "hash_valid": True}]}
        if path == "/predict/graph":
            return 200, {"thread_id": "run-123456abcdef", "decision": "REVIEW", "approval_required": True}
        if path.endswith("/approve"):
            calls["approvals"] += 1
            assert calls["restarts"] == calls["approvals"]
            return 200, {"ledger_status": "APPROVED", "disbursement": {"id": 1},
                         "replay": calls["approvals"] == 2}
        if path == "/audit/run-123456abcdef":
            return 200, {"decision": "APPROVE"}
        raise AssertionError(path)

    monkeypatch.setattr(drill, "docker_command", fake_docker)
    monkeypatch.setattr(drill, "request", fake_request)
    monkeypatch.setattr(drill, "wait_for_health", lambda port: None)
    monkeypatch.setattr(drill.subprocess, "check_output", lambda *args, **kwargs: "test-sha")
    monkeypatch.setattr(drill.subprocess, "run", lambda *args, **kwargs: None)

    result = drill.run(18083, "test-image:local")
    assert calls == {"restarts": 2, "approvals": 2, "ledger": 2}
    assert result["replay"] is True
    assert result["matching_disbursement_rows"] == 1
    assert result["ledger_hash_valid"] is True


@pytest.mark.parametrize("port", [0, 1023, 65536])
def test_restart_drill_rejects_bad_port_before_docker(monkeypatch, port):
    monkeypatch.setattr(drill, "docker_command", lambda args: pytest.fail("docker called"))
    with pytest.raises(ValueError):
        drill.run(port, "test-image")
