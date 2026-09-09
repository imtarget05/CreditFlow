from __future__ import annotations

from typing import Tuple

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

from pipeline.validation.schemas import CANONICAL_COLUMNS, TARGET_COLUMN
from pipeline.feature_engineering.features import DERIVED_FEATURES


NUMERIC_COLUMNS = list(CANONICAL_COLUMNS) + list(DERIVED_FEATURES)


def stratified_train_val_test_split(
    df: pd.DataFrame,
    target: str = TARGET_COLUMN,
    test_size: float = 0.15,
    random_state: int = 42,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    y = df[target]
    X = df.drop(columns=[target])

    X_train, X_tmp, y_train, y_tmp = train_test_split(
        X, y, test_size=test_size * 2, random_state=random_state, stratify=y
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_tmp, y_tmp, test_size=0.5, random_state=random_state, stratify=y_tmp
    )
    return X_train, X_val, X_test, y_train, y_val, y_test
