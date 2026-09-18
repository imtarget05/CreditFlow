"""CreditFlow prediction service — prediction logic + decision engineering.

Wraps the production model so the HTTP layer stays thin. Loads the persisted
Pipeline (preprocessor + model), runs validation + feature engineering, then
returns risk_probability / risk_level / decision / model_version + reasons.

Money-unit contract (see pipeline/validation/schemas.py — do NOT re-derive):
  * the API/UI contract is **VND** for income / loan_amount / existing_debt;
  * the training artifact is in **nghìn VND**;
  * the divisor is read from ``meta["vnd_per_model_unit"]`` (written by
    scripts/train_models.py), never hardcoded here;
  * payloads that cannot be VND (values below the contract floor) are rejected
    with a clear error instead of being rescaled by a magnitude guess.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import joblib
import pandas as pd
import sklearn

from pipeline.validation.schemas import (
    MONEY_COLUMNS,
    validate_dataframe,
    validate_money_unit_contract,
)
from pipeline.feature_engineering.features import add_derived_features
from pipeline.modeling.train import NUMERIC_COLUMNS
from pipeline.modeling.threshold import (
    business_decision,
    DEFAULT_APPROVE_MAX,
    DEFAULT_REVIEW_MAX,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL_FILE = ROOT / "models" / "production" / "pipeline.joblib"
META_FILE = ROOT / "models" / "production" / "meta.json"
MANIFEST_FILE = ROOT / "models" / "production" / "manifest.json"

REQUIRED_METADATA_FIELDS = frozenset({
    "model_name",
    "version",
    "trained_at",
    "threshold",
    "feature_order",
    "money_unit",
    "training_money_unit",
    "vnd_per_model_unit",
    "sklearn_version",
})
REQUIRED_ARTIFACTS = frozenset({
    "pipeline.joblib",
    "meta.json",
    "reference_stats.json",
    "benchmark_results.csv",
    "benchmark_results.json",
})


class ModelUnavailableError(RuntimeError):
    """Raised when the production model cannot be loaded."""


class ArtifactContractError(RuntimeError):
    """Raised when the artifact does not declare a serving contract field."""


class UnitContractViolation(ValueError):
    """Raised when money inputs violate the VND unit contract."""


def _major_minor(version: str) -> tuple[str, str]:
    parts = str(version).split(".")
    if len(parts) < 2 or not all(part.isdigit() for part in parts[:2]):
        raise ArtifactContractError(f"invalid scikit-learn version: {version!r}")
    return parts[0], parts[1]


def validate_model_bundle(bundle_dir: Path | None = None) -> dict:
    """Validate all files required to serve one immutable model bundle."""
    bundle_dir = Path(bundle_dir or MODEL_FILE.parent)
    manifest_path = bundle_dir / "manifest.json"
    meta_path = bundle_dir / "meta.json"
    try:
        manifest = json.loads(manifest_path.read_text())
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError) as exc:
        raise ArtifactContractError(f"invalid model bundle metadata: {exc}") from exc

    if manifest.get("bundle_version") != 1:
        raise ArtifactContractError("unsupported or missing bundle_version")
    if manifest.get("metadata") != meta:
        raise ArtifactContractError("manifest metadata does not match meta.json")

    missing = sorted(REQUIRED_METADATA_FIELDS.difference(meta))
    if missing:
        raise ArtifactContractError(f"meta.json missing required fields: {', '.join(missing)}")
    if not isinstance(meta["feature_order"], list) or not meta["feature_order"]:
        raise ArtifactContractError("meta.json feature_order must be a non-empty list")
    if meta["feature_order"] != list(NUMERIC_COLUMNS):
        raise ArtifactContractError("meta.json feature_order does not match the serving schema")
    money_scale(meta)

    artifact_hashes = manifest.get("artifacts")
    if not isinstance(artifact_hashes, dict) or not artifact_hashes:
        raise ArtifactContractError("manifest artifacts must be a non-empty mapping")
    missing_artifacts = sorted(REQUIRED_ARTIFACTS.difference(artifact_hashes))
    if missing_artifacts:
        raise ArtifactContractError(
            f"manifest missing required artifacts: {', '.join(missing_artifacts)}"
        )
    import hashlib
    for filename, expected_hash in artifact_hashes.items():
        path = bundle_dir / filename
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ArtifactContractError(f"invalid artifact filename: {filename!r}")
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ArtifactContractError(f"required artifact missing: {filename}") from exc
        if actual_hash != expected_hash:
            raise ArtifactContractError(f"artifact SHA-256 mismatch: {filename}")

    if _major_minor(meta["sklearn_version"]) != _major_minor(sklearn.__version__):
        raise ArtifactContractError(
            "scikit-learn major/minor mismatch: "
            f"bundle={meta['sklearn_version']}, runtime={sklearn.__version__}"
        )
    return meta


def money_scale(meta: dict) -> float:
    """Money divisor declared by the training artifact (VND -> training units)."""
    try:
        scale = float(meta["vnd_per_model_unit"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactContractError(
            "meta.json has no 'vnd_per_model_unit' — retrain "
            "(`python scripts/train_models.py`) so the artifact declares its "
            "money-unit contract; serving refuses to guess the scale."
        ) from exc
    if not math.isfinite(scale) or scale <= 0:
        raise ArtifactContractError(f"invalid vnd_per_model_unit: {scale}")
    return scale


def to_model_units(features: dict, meta: dict) -> dict:
    """Map contract-VND money fields into the model's training scale.

    Enforces the unit contract first: values that cannot be VND raise
    ``UnitContractViolation`` (a ``ValueError``) so the API answers 422 with a
    clear message instead of silently scoring a mis-scaled profile.
    Only income/loan_amount/existing_debt are touched; the other 5 fields and
    every ratio (DTI/LTI/debt-to-loan) are scale-invariant under this divisor.
    """
    violations = validate_money_unit_contract(features)
    if violations:
        raise UnitContractViolation("unit_contract_violation: " + "; ".join(violations))

    scale = money_scale(meta)
    out = dict(features)
    for key in MONEY_COLUMNS:
        if key in out:
            out[key] = float(out[key]) / scale
    return out


def load_production_model():
    meta = validate_model_bundle()
    try:
        pipe = joblib.load(MODEL_FILE)
    except OSError as exc:
        raise ModelUnavailableError(
            f"Production model not found at {MODEL_FILE}. Run `python3 scripts/train_models.py` first."
        ) from exc
    return pipe, meta


def predict_risk(pipeline, features: dict, meta: dict) -> dict:
    """Validate, featurize, and predict from raw feature dict -> decision payload."""
    # -- validation (business rules -> reject clearly invalid input) --
    df = pd.DataFrame([features])
    _violations, violations = validate_dataframe(df)
    if violations:
        raise ValueError(f"invalid_credit_profile: {'; '.join(violations)}")

    model_df = pd.DataFrame([to_model_units(features, meta)])
    fe, _flags = add_derived_features(model_df)
    # Feature order comes from the artifact when declared, so inference follows
    # exactly what was trained (the ColumnTransformer also selects by name).
    feature_order = meta.get("feature_order") or [c for c in fe.columns if c != "default"]
    missing = [c for c in feature_order if c not in fe.columns]
    if missing:
        raise ValueError(f"artifact_contract_error: missing features {missing}")
    proba = float(pipeline.predict_proba(fe[feature_order])[0, 1])

    # Decision engineering (Phase 4): the DECISION is driven by the cost-tuned
    # threshold trained into meta.json (e.g. 0.2), while the displayed
    # risk_level uses the fixed LOW/MEDIUM/HIGH buckets (0.5 / 0.8).
    tuned_threshold = float(meta.get("threshold", DEFAULT_APPROVE_MAX))
    risk_level = _risk_bucket(proba)
    decision = business_decision(proba, tuned_threshold, DEFAULT_REVIEW_MAX)
    reasons = _build_reasons(fe.iloc[0])

    return {
        "risk_probability": round(proba, 4),
        "risk_level": risk_level,
        "decision": decision,
        "model_version": meta.get("version", "unknown"),
        "model_name": meta.get("model_name", "unknown"),
        "threshold": {
            "tuned_threshold": tuned_threshold,
            "approve_max": DEFAULT_APPROVE_MAX,
            "review_max": DEFAULT_REVIEW_MAX,
        },
        "reasons": reasons,
        "deployment": "API",
    }


def _risk_bucket(probability: float) -> str:
    if probability < DEFAULT_APPROVE_MAX:
        return "LOW"
    if probability < DEFAULT_REVIEW_MAX:
        return "MEDIUM"
    return "HIGH"


def _build_reasons(row: pd.Series) -> list[str]:
    reasons: list[str] = []
    dti = float(row.get("debt_to_income", 0) or 0)
    lti = float(row.get("loan_to_income", 0) or 0)
    hist = float(row.get("credit_history", 0) or 0)
    prev = float(row.get("previous_defaults", 0) or 0)
    if dti > 0.4:
        reasons.append("high debt-to-income ratio")
    if lti > 5.0:
        reasons.append("high loan-to-income ratio")
    if hist < 2.0:
        reasons.append("short credit history")
    if prev > 0:
        reasons.append("history of missed payments")
    return reasons or ["low overall risk profile"]
