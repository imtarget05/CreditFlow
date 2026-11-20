import pytest
import json
import sys

from scripts import benchmark_local_graph as bench


def test_nearest_rank_uses_completed_case_distribution():
    assert bench.nearest_rank([], 0.95) is None
    assert bench.nearest_rank([5, 1, 4, 2, 3], 0.95) == 5


def test_benchmark_counts_failures_and_separates_latency_phases(monkeypatch):
    calls = 0

    def fake_run(port, key):
        nonlocal calls
        calls += 1
        assert port == 18084
        assert key == "local-key"
        if calls == 2:
            raise RuntimeError("simulated outage")
        return {
            "full_workflow_latency_ms": calls * 10,
            "start_latency_ms": calls * 3,
            "approval_latency_ms": calls * 2,
            "explanation_source": "template",
        }

    monkeypatch.setattr(bench, "run", fake_run)
    report = bench.benchmark(18084, "local-key", 3)
    assert report["task_completion_rate"] == pytest.approx(2 / 3)
    assert report["full_workflow_p50_ms"] == 20
    assert report["full_workflow_p95_ms"] == 30
    assert report["approval_p95_ms"] == 6
    assert report["results"][1]["complete"] is False


@pytest.mark.parametrize("count", [0, 101])
def test_benchmark_rejects_invalid_case_count(count):
    with pytest.raises(ValueError, match="cases must"):
        bench.benchmark(18084, "local-key", count)


def test_cli_prints_complete_raw_report(monkeypatch, capsys):
    monkeypatch.setenv("CREDITFLOW_API_KEY", "local-key")
    monkeypatch.setattr(sys, "argv", ["benchmark", "--port", "18084", "--cases", "2"])
    monkeypatch.setattr(bench, "benchmark", lambda port, key, cases: {
        "total_cases": cases, "completed_cases": cases, "results": [{"complete": True}] * cases,
    })
    bench.main()
    report = json.loads(capsys.readouterr().out)
    assert report["total_cases"] == 2
    assert len(report["results"]) == 2


def test_cli_fails_when_any_case_incomplete(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["benchmark", "--cases", "2"])
    monkeypatch.setattr(bench, "benchmark", lambda port, key, cases: {
        "total_cases": cases, "completed_cases": 1, "results": [{"complete": True}, {"complete": False}],
    })
    with pytest.raises(SystemExit) as error:
        bench.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["completed_cases"] == 1
