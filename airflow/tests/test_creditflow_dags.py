"""Unit tests for CreditFlow Airflow DAG integrity and task computation logic."""
import ast
from pathlib import Path
import numpy as np
import pytest
import json
from datetime import datetime, timedelta, timezone

from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
import io

DAG_FILE = Path(__file__).resolve().parents[1] / "dags" / "creditflow_data_and_drift_pipeline.py"


def test_creditflow_dag_syntax():
    """Verify CreditFlow DAG has valid python syntax."""
    assert DAG_FILE.exists(), f"DAG file missing: {DAG_FILE}"
    source = DAG_FILE.read_text(encoding="utf-8")
    ast.parse(source)


def _stub_airflow():
    import os
    os.environ["CREDITFLOW_DAG_NO_INSTANTIATE"] = "1"
    import sys
    import types
    airflow_dir = str(Path(__file__).resolve().parents[1])
    if airflow_dir not in sys.path:
        sys.path.insert(0, airflow_dir)
    if "airflow" not in sys.modules:
        airflow = types.ModuleType("airflow")
        decorators = types.ModuleType("airflow.decorators")
        decorators.dag = lambda *a, **k: (lambda f: f)
        def task_stub(fn=None, **k):
            if fn is not None:
                return fn
            return lambda f: f
        decorators.task = task_stub
        airflow.decorators = decorators
        sys.modules["airflow"] = airflow
        sys.modules["airflow.decorators"] = decorators

_stub_airflow()


def test_psi_computation():
    """Verify Population Stability Index math implementation."""
    from dags.creditflow_data_and_drift_pipeline import _calculate_psi

    ref = [10.0, 20.0, 30.0, 40.0, 50.0]
    cur = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _calculate_psi(ref, cur) == 0.0

    cur_drifted = [100.0, 200.0, 300.0, 400.0, 500.0]
    psi_drift = _calculate_psi(ref, cur_drifted)
    assert psi_drift > 0.20
    assert isinstance(psi_drift, float)


def test_ingest_calls_applications_endpoint_with_api_key():
    """ingest_and_validate must call GET /applications with the API-key header."""
    import dags.creditflow_data_and_drift_pipeline as mod
    from dags.creditflow_data_and_drift_pipeline import ingest_and_validate

    fake_app = {
        "id": "app_123",
        "income": 100000000,
        "loan_amount": 500000000,
        "credit_score": 750,
        "debt_to_income": 0.3,
        "loan_term_months": 36,
        "age": 35,
        "application_date": (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"),
        "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT10:00:00Z"),
    }

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"applications": [fake_app]}).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch.object(mod, "CREDITFLOW_API_KEY", "cf_sk_test_live_abc123"), \
         patch("dags.creditflow_data_and_drift_pipeline.urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
        result = ingest_and_validate()

    call_args = mock_urlopen.call_args
    request = call_args[0][0]
    assert "/applications" in request.full_url
    header_keys = [k.lower() for k in request.headers.keys()]
    assert "x-creditflow-api-key" in header_keys
    assert request.headers.get("X-creditflow-api-key") == "cf_sk_test_live_abc123"
    assert len(result) == 1
    assert result[0]["id"] == "app_123"


def test_ingest_raises_when_api_key_missing():
    """Fail-closed: missing API key must raise, not return sample data."""
    import dags.creditflow_data_and_drift_pipeline as mod
    from dags.creditflow_data_and_drift_pipeline import ingest_and_validate

    with patch.object(mod, "CREDITFLOW_API_KEY", ""), \
         pytest.raises(RuntimeError, match="CREDITFLOW_API_KEY"):
        ingest_and_validate()


def test_ingest_rejects_stale_data():
    """Applications older than stale_threshold_days must be rejected."""
    import dags.creditflow_data_and_drift_pipeline as mod
    from dags.creditflow_data_and_drift_pipeline import ingest_and_validate

    stale_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fake_app = {
        "id": "stale_app",
        "income": 50000000,
        "loan_amount": 100000000,
        "credit_score": 650,
        "debt_to_income": 0.5,
        "loan_term_months": 24,
        "age": 30,
        "application_date": stale_date,
        "created_at": stale_date,
    }

    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"applications": [fake_app]}).encode("utf-8")
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch.object(mod, "CREDITFLOW_API_KEY", "cf_sk_test_live"), \
         patch.object(mod, "STALE_THRESHOLD_DAYS", 2), \
         patch("dags.creditflow_data_and_drift_pipeline.urllib.request.urlopen", return_value=mock_response):
        result = ingest_and_validate()

    assert result == [], "Stale applications must be filtered out"


def test_ingest_propagates_auth_failure():
    """HTTP 401 from API must propagate as an Airflow failure, not silently degrade."""
    import dags.creditflow_data_and_drift_pipeline as mod
    from dags.creditflow_data_and_drift_pipeline import ingest_and_validate

    err = HTTPError(
        url="http://test/applications",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"detail": "INVALID_API_KEY"}'),
    )

    with patch.object(mod, "CREDITFLOW_API_KEY", "cf_sk_test_live"), \
         patch("dags.creditflow_data_and_drift_pipeline.urllib.request.urlopen", side_effect=err):
        with pytest.raises((HTTPError, Exception)) as exc_info:
            ingest_and_validate()
    assert exc_info.type == HTTPError or "401" in str(exc_info.value)