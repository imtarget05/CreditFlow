"""CreditFlow model training + benchmarking.

Produces (all reproducible / committed as artifacts):
  - models/production/benchmark_results.csv : 4-model Accuracy/Precision/Recall/F1 on val & test
  - models/production/benchmark_results.json: machine-readable copy
  - models/production/pipeline.joblib       : selected best model (preprocessor + model) for serving
  - models/production/meta.json              : model version + metrics + selection reasoning
  - MLflow experiment logging                : graceful — skipped if mlflow is not installed

Selection: cost-aware. FN (missed default, cost 5) >> FP (rejected good, cost 1), so we
pick the threshold that minimises total business cost on the validation set (spec §3/§9).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from pipeline.data.generate_dataset import write_dataset
from pipeline.validation.schemas import CANONICAL_COLUMNS, TARGET_COLUMN, validate_dataframe
from pipeline.feature_engineering.features import add_derived_features, DERIVED_FEATURES
from pipeline.modeling.models import get_model_factory
from pipeline.modeling.train import stratified_train_val_test_split, NUMERIC_COLUMNS
from pipeline.modeling.evaluate import compute_metrics, calculate_confusion_matrix
from pipeline.modeling.threshold import find_optimal_threshold, DEFAULT_FN_COST, DEFAULT_FP_COST
from pipeline.monitoring.drift import compute_reference_stats, compute_prediction_reference

DATASET_PATH = ROOT / "data" / "creditflow_dataset.csv"
PROD_DIR = ROOT / "models" / "production"
BENCHMARK_CSV = PROD_DIR / "benchmark_results.csv"
BENCHMARK_JSON = PROD_DIR / "benchmark_results.json"


def _sha256(path: Path) -> str:
    """Streaming SHA-256 of a file (hex digest), for dataset provenance."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _prepare_data() -> pd.DataFrame:
    if not DATASET_PATH.exists():
        write_dataset(DATASET_PATH, n=5000, random_state=42)
    raw = pd.read_csv(DATASET_PATH)
    _, violations = validate_dataframe(raw)
    if violations:
        sys.stdout.write(f"[warning] validation violations: {violations}\n")
    df, _ = add_derived_features(raw)
    return df.dropna().reset_index(drop=True)


def train(force_regenerate: bool = False) -> None:
    if force_regenerate or not DATASET_PATH.exists():
        write_dataset(DATASET_PATH, n=5000, random_state=42)
    df = _prepare_data()
    dataset_sha256 = _sha256(DATASET_PATH)

    X_train, X_val, X_test, y_train, y_val, y_test = stratified_train_val_test_split(df)

    preprocessor = ColumnTransformer(
        transformers=[("num", StandardScaler(), NUMERIC_COLUMNS)]
    )

    rows = []
    fitted: dict[str, Pipeline] = {}
    for name, model in get_model_factory():
        pipe = Pipeline(steps=[("preprocessor", preprocessor), ("model", model)])
        pipe.fit(X_train, y_train)

        # Cost-aware optimal threshold on validation set (FN>FP)
        proba_val = pipe.predict_proba(X_val)[:, 1]
        thr = find_optimal_threshold(
            y_val, proba_val, fn_cost=DEFAULT_FN_COST, fp_cost=DEFAULT_FP_COST
        )
        t = thr["best_threshold"]
        pred_val = (proba_val >= t).astype(int)
        m_val = compute_metrics(y_val, pred_val, proba_val)

        pred_test = (pipe.predict_proba(X_test)[:, 1] >= t).astype(int)
        proba_test = pipe.predict_proba(X_test)[:, 1]
        m_test = compute_metrics(y_test, pred_test, proba_test)

        rows.append(
            {
                "model": name,
                "best_threshold": round(float(t), 4),
                "business_cost": round(float(thr["best_cost"]), 2),
                "val_accuracy": round(float(m_val["accuracy"]), 4),
                "val_precision": round(float(m_val["precision"]), 4),
                "val_recall": round(float(m_val["recall"]), 4),
                "val_f1": round(float(m_val["f1"]), 4),
                "val_roc_auc": round(float(m_val["roc_auc"]), 4) if m_val.get("roc_auc") is not None else None,
                "test_accuracy": round(float(m_test["accuracy"]), 4),
                "test_precision": round(float(m_test["precision"]), 4),
                "test_recall": round(float(m_test["recall"]), 4),
                "test_f1": round(float(m_test["f1"]), 4),
                "test_roc_auc": round(float(m_test["roc_auc"]), 4) if m_test.get("roc_auc") is not None else None,
            }
        )
        fitted[name] = pipe

    results = pd.DataFrame(rows).sort_values("business_cost").reset_index(drop=True)

    BENCHMARK_CSV.parent.mkdir(parents=True, exist_ok=True)
    results.to_csv(BENCHMARK_CSV, index=False)
    BENCHMARK_JSON.write_text(results.to_json(orient="records", indent=2))

    # Select production model: lowest business cost, tie-break by val F1.
    best = results.sort_values(["business_cost", "val_f1"], ascending=[True, False]).iloc[0]
    model_name = best["model"]
    best_pipe = fitted[model_name]
    version = f"{model_name}_v001"

    PROD_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(best_pipe, PROD_DIR / "pipeline.joblib")

    # Confusion matrix on test set at tuned threshold (spec §9 / PRD P4)
    best_proba_test = best_pipe.predict_proba(X_test)[:, 1]
    best_pred_test = (best_proba_test >= float(best["best_threshold"])).astype(int)
    cm = calculate_confusion_matrix(y_test, best_pred_test)
    tn, fp, fn, tp = cm.ravel()

    meta = {
        "model_name": model_name,
        "version": version,
        "trained_at": pd.Timestamp.now("UTC").isoformat(),
        "threshold": float(best["best_threshold"]),
        "business_cost": float(best["business_cost"]),
        "fn_cost": DEFAULT_FN_COST,
        "fp_cost": DEFAULT_FP_COST,
        "val_metrics": {
            "accuracy": float(best["val_accuracy"]),
            "precision": float(best["val_precision"]),
            "recall": float(best["val_recall"]),
            "f1": float(best["val_f1"]),
            "roc_auc": float(best["val_roc_auc"]) if best.get("val_roc_auc") is not None else None,
        },
        "test_metrics": {
            "accuracy": float(best["test_accuracy"]),
            "precision": float(best["test_precision"]),
            "recall": float(best["test_recall"]),
            "f1": float(best["test_f1"]),
            "roc_auc": float(best["test_roc_auc"]) if best.get("test_roc_auc") is not None else None,
        },
        "confusion_matrix": {
            "true_positive": int(tp),
            "false_positive": int(fp),
            "true_negative": int(tn),
            "false_negative": int(fn),
            "note": "Test set at tuned threshold (spec §9).",
        },
        "selection_reason": (
            f"Lowest normalized business cost on validation "
            f"({best['business_cost']:.2f}) with FN cost {DEFAULT_FN_COST} > FP cost "
            f"{DEFAULT_FP_COST}; recall-centric objective (spec §3/§9)."
        ),
        "dataset": str(DATASET_PATH.relative_to(ROOT)),
        "dataset_name": "synthetic_proxy",
        "mapping_version": "0.1",  # synthetic generator; real raw->canonical mapping lives in pipeline/data/mapping.yaml
        "dataset_sha256": dataset_sha256,
        "seed": 42,
        "rows": int(len(df)),
    }
    (PROD_DIR / "meta.json").write_text(json.dumps(meta, indent=2))

    # Reference statistics for ML drift monitoring (P11): snapshot of the exact
    # training feature distributions + the production model's validation
    # probability distribution. Served/compared by GET /drift at inference time.
    reference = compute_reference_stats(df, list(NUMERIC_COLUMNS))
    reference["prediction"] = compute_prediction_reference(
        best_pipe.predict_proba(X_val)[:, 1]
    )
    (PROD_DIR / "reference_stats.json").write_text(json.dumps(reference, indent=2))

    _log_mlflow(model_name, version, meta, results, best_pipe, PROD_DIR / "pipeline.joblib")

    print(results.to_string(index=False))
    print("\n=== PRODUCTION MODEL ===")
    print(json.dumps(meta, indent=2))


def _log_mlflow(model_name, version, meta, results, best_pipe, artifact_path) -> None:
    """Log the 4-model benchmark to MLflow (spec P7).

    Structure (interview-demoable):
      Experiment: creditflow-risk
        Parent run : benchmark_<utc-timestamp>
          Child run: <model> (per model: params + val/test metrics + threshold + cost)
        Best model artifact + registry entry (registered only when a tracking
        store supports it; file store registers locally).

    Graceful: if mlflow is not installed, print a skip note and return.
    """
    try:
        import mlflow
        import mlflow.sklearn
    except Exception as exc:  # graceful degradation
        sys.stdout.write(f"\n[mlflow] not available, skipping tracking ({exc})\n")
        return
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    if tracking_uri:
        mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment("creditflow-risk")
    ts = pd.Timestamp.now("UTC").strftime("%Y%m%d-%H%M%S")
    with mlflow.start_run(run_name=f"benchmark_{ts}") as parent:
        mlflow.log_param("dataset", meta["dataset"])
        mlflow.log_param("dataset_sha256", meta["dataset_sha256"])
        mlflow.log_param("dataset_name", meta["dataset_name"])
        mlflow.log_param("rows", meta["rows"])
        mlflow.log_param("fn_cost", meta["fn_cost"])
        mlflow.log_param("fp_cost", meta["fp_cost"])
        mlflow.log_param("production_model", model_name)
        mlflow.log_param("production_version", version)
        for _, row in results.iterrows():
            with mlflow.start_run(run_name=str(row["model"]), nested=True):
                mlflow.log_param("model_name", str(row["model"]))
                mlflow.log_param("best_threshold", float(row["best_threshold"]))
                mlflow.log_param("fn_cost", meta["fn_cost"])
                mlflow.log_param("fp_cost", meta["fp_cost"])
                for col in (
                    "val_accuracy", "val_precision", "val_recall", "val_f1",
                    "test_accuracy", "test_precision", "test_recall", "test_f1",
                    "business_cost",
                ):
                    mlflow.log_metric(col, float(row[col]))
        mlflow.log_metric("business_cost", meta["business_cost"])
        mlflow.log_metric("rows", meta["rows"])
        try:
            mlflow.log_artifact(str(artifact_path))
            mlflow.log_artifact(str(BENCHMARK_CSV))
            mlflow.log_text(json.dumps(meta, indent=2), "meta.json")
            mlflow.sklearn.log_model(best_pipe, name="pipeline")
        except Exception as exc:
            sys.stdout.write(f"[mlflow] artifact logging skipped: {exc}\n")
        # Register the production model so the registry answers
        # "which model is currently serving?" (file store registers locally).
        try:
            mv = mlflow.register_model(
                f"runs:/{parent.info.run_id}/pipeline",
                "creditflow-risk",
            )
            sys.stdout.write(f"[mlflow] registered model version: {mv.version}\n")
        except Exception as exc:
            sys.stdout.write(f"[mlflow] model registry skipped: {exc}\n")
    sys.stdout.write("[mlflow] experiment 'creditflow-risk' logged.\n")


if __name__ == "__main__":
    train(force_regenerate=os.environ.get("CREDITFLOW_REGENERATE") == "1")