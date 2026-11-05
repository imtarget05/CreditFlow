"""The scheduler delegates drift decisions to the serving API."""
import ast
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

DAG_FILE = Path(__file__).resolve().parents[1] / "dags/creditflow_data_and_drift_pipeline.py"


def _load_dag():
    os.environ["CREDITFLOW_DAG_NO_INSTANTIATE"] = "1"
    airflow_dir = str(Path(__file__).resolve().parents[1])
    if airflow_dir not in sys.path:
        sys.path.insert(0, airflow_dir)
    if "airflow" not in sys.modules:
        airflow = types.ModuleType("airflow")
        decorators = types.ModuleType("airflow.decorators")
        decorators.dag = lambda *args, **kwargs: lambda fn: fn
        decorators.task = lambda fn: fn
        airflow.decorators = decorators
        sys.modules["airflow"] = airflow
        sys.modules["airflow.decorators"] = decorators
    from dags import creditflow_data_and_drift_pipeline
    return creditflow_data_and_drift_pipeline


def test_dag_is_valid_and_contains_no_independent_approval_rules():
    source = DAG_FILE.read_text()
    ast.parse(source)
    assert "approved =" not in source
    assert "COMPLIANT" not in source
    assert "ref_incomes" not in source


def test_monitor_calls_canonical_api_with_service_key(monkeypatch):
    module = _load_dag()
    monkeypatch.setenv("CREDITFLOW_API_URL", "https://creditflow.internal")
    monkeypatch.setenv("CREDITFLOW_API_KEY", "test-service-key")
    response = MagicMock()
    response.read.return_value = json.dumps({"drift": {"status": "DRIFT_DETECTED", "n_recent": 100}, "work_item": {"id": "work-1"}}).encode()
    response.__enter__.return_value = response
    with patch.object(module.urllib.request, "urlopen", return_value=response) as urlopen:
        result = module._run_monitoring_check("daily-2026-11-03")
    request = urlopen.call_args.args[0]
    assert request.full_url == "https://creditflow.internal/api/monitoring/check"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"run_id": "daily-2026-11-03"}
    assert request.headers["X-creditflow-api-key"] == "test-service-key"
    assert result == {"status": "DRIFT_DETECTED", "n_recent": 100, "work_item_id": "work-1"}


def test_monitor_fails_closed_without_configuration(monkeypatch):
    module = _load_dag()
    monkeypatch.delenv("CREDITFLOW_API_URL", raising=False)
    monkeypatch.delenv("CREDITFLOW_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CREDITFLOW_API_URL"):
        module._run_monitoring_check("daily-2026-11-03")


def test_monitor_rejects_unknown_status(monkeypatch):
    module = _load_dag()
    monkeypatch.setenv("CREDITFLOW_API_URL", "https://creditflow.internal")
    monkeypatch.setenv("CREDITFLOW_API_KEY", "test-service-key")
    response = MagicMock()
    response.read.return_value = b'{"drift":{"status":"COMPLIANT"}}'
    response.__enter__.return_value = response
    with patch.object(module.urllib.request, "urlopen", return_value=response), pytest.raises(
        RuntimeError, match="Unexpected drift status"
    ):
        module._run_monitoring_check("daily-2026-11-03")


def test_monitor_propagates_api_failure(monkeypatch):
    module = _load_dag()
    monkeypatch.setenv("CREDITFLOW_API_URL", "https://creditflow.internal")
    monkeypatch.setenv("CREDITFLOW_API_KEY", "test-service-key")
    error = HTTPError("https://creditflow.internal", 503, "Unavailable", {}, None)
    with patch.object(module.urllib.request, "urlopen", side_effect=error), pytest.raises(HTTPError):
        module._run_monitoring_check("daily-2026-11-03")


def test_hourly_reminders_call_machine_endpoint(monkeypatch):
    _load_dag()
    from dags import creditflow_work_item_reminders as module
    monkeypatch.setenv("CREDITFLOW_API_URL", "https://creditflow.internal")
    monkeypatch.setenv("CREDITFLOW_API_KEY", "test-service-key")
    response = MagicMock()
    response.read.return_value = b'{"queued":2}'
    response.__enter__.return_value = response
    with patch.object(module.urllib.request, "urlopen", return_value=response) as urlopen:
        assert module._enqueue_due() == 2
    assert urlopen.call_args.args[0].full_url == "https://creditflow.internal/api/work-items/reminders/run"
