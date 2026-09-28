from __future__ import annotations

from typing import List, Tuple

from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier


def get_model_factory() -> List[Tuple[str, object]]:
    models: List[Tuple[str, object]] = [
        ("logistic_regression", LogisticRegression(max_iter=1000, random_state=42)),
        ("decision_tree", DecisionTreeClassifier(random_state=42)),
        ("random_forest", RandomForestClassifier(n_estimators=300, random_state=42)),
    ]
    # XGBoost import deferred: libxgboost.dylib link against libomp (macOS) và
    # dlopen thất bại sẽ ném XGBoostError (không phải ImportError). Nếu để ở
    # top-level thì MỘT thư viện hệ thống thiếu sẽ làm `pytest` không collect
    # được cả suite (2 collection errors) — CI/CD trên máy sạch cũng sẽ chết ở
    # đúng chỗ này. Khi import được, danh sách model giữ nguyên như cũ.
    try:
        from xgboost import XGBClassifier
    except Exception as exc:  # ImportError *or* the dlopen/XGBoostError it raises
        import logging

        logging.getLogger(__name__).warning(
            "xgboost unavailable, factory returns sklearn-only models: %s", exc
        )
    else:
        models.append(("xgboost", XGBClassifier(eval_metric="logloss", random_state=42)))
    return models
