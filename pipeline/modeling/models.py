from __future__ import annotations

from typing import List, Tuple

from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.ensemble import RandomForestClassifier
from xgboost import XGBClassifier


def get_model_factory() -> List[Tuple[str, object]]:
    return [
        ("logistic_regression", LogisticRegression(max_iter=1000, random_state=42)),
        ("decision_tree", DecisionTreeClassifier(random_state=42)),
        ("random_forest", RandomForestClassifier(n_estimators=300, random_state=42)),
        ("xgboost", XGBClassifier(eval_metric="logloss", random_state=42)),
    ]
