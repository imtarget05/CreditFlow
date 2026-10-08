"""Unit tests for CreditFlow Airflow DAG integrity and task computation logic."""
import ast
from pathlib import Path
import numpy as np
import pytest

DAG_FILE = Path(__file__).resolve().parents[1] / "dags" / "creditflow_data_and_drift_pipeline.py"


def test_creditflow_dag_syntax():
    """Verify CreditFlow DAG has valid python syntax."""
    assert DAG_FILE.exists(), f"DAG file missing: {DAG_FILE}"
    source = DAG_FILE.read_text(encoding="utf-8")
    ast.parse(source)


def _stub_airflow():
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
            def dec(f):
                def placeholder(*args, **kwargs):
                    return []
                return placeholder
            if fn is not None:
                return dec(fn)
            return dec
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
