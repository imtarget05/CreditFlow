# Implementation Plan — CreditFlow EDA Notebook (Clean Architecture)

## Overview

Produce a single clean, reproducible EDA notebook `notebooks/creditflow_eda.ipynb` for CreditFlow (synthetic proxy data), executed with real outputs and versioned in git. Architecture is layered so notebook cells are thin views over tested pipeline modules. Scope is EDA only — no `.fit()`, no training, no GPU (constraint `docs/spec.md §19`).

## Goals
- Notebook is the deliverable: self-contained, Colab-ready, runnable, reproducible (`random_state=42`).
- Reuses existing `pipeline/validation/schemas.py` and `pipeline/feature_engineering/features.py` instead of duplicating logic.
- Empty Stub-friendly: cells depend only on `pandas`, `numpy`, `matplotlib`, `seaborn`, and `pipeline/`.

## Non-goals
- No model training, no models benchmark, no MLflow, no FastAPI in this slice.
- No dependency on user's manual notebook state — all cells run top-to-bottom.

## Architecture
```text
[pipeline/]  data validation + feature engineering (already implemented, imported)
     │
[notebook]  cells = dataset-gen + EDA sections  →  each cell clean, one responsibility
     │
[outputs]   committed cell outputs (tables, heatmap, P(default)) = evidence
```
- `data layer` (transform/validate) lives in `pipeline/`; the notebook only orchestrates + displays.
- `config` (constants, random_state, target rate) is declared once in a setup cell and reused.
- `reproducibility`: every random draw is seeded with `random_state=42`.

## Types
- `n` constant: `1000` rows.
- `random_state`: `42`.
- Target rate: `P(default) ≈ 0.12`.
- Canonical columns (match `CANONICAL_COLUMNS`): `income, age, employment_years, loan_amount, loan_term, existing_debt, credit_history, previous_defaults`.

## Files

| Action | Path | Purpose |
|--------|------|---------|
| new | `notebooks/creditflow_eda.ipynb` | clean EDA notebook, tuple deliverable |
| modify | `.gitignore` | already ignores `__pycache__/*.pyc` — kept |
| keep | `requirements-colab.txt` | provide pinned pandas/numpy/matplotlib/seaborn |
| keep | `pipeline/validation/schemas.py` | `validate_dataframe` reused by notebook |
| keep | `pipeline/feature_engineering/features.py` | `add_derived_features` reused by notebook |

## Notebook Structure (cells, one responsibility each)

1. setup — `%pip install -q -r requirements-colab.txt`; imports; display options; `random_state=42`; `n`.
2. data gen — generate synthetic df (7 raw + target); log shape `(1000,9)`, columns, head, global `P(default)`.
3. q1 — default vs non-default characteristics (group-by stats).
4. q2 — feature correlation with target (correlation table + heatmap).
5. q3 — class imbalance (value_counts; P(default) overall).
6. q4 — leakage suspects (`previous_defaults` vs target; time-based flags).
7. q5 — missing/outliers/duplicates => `validate_dataframe` on clean data.
8. q6 — feature-engineering demo => `add_derived_features` on sample rows.
9. q7 — validation demo => clean vs bad row, print violations.
10. check — requirements check; assert no `.fit()`/forbidden import.

## Functions
- Notebook provides: `generate(df → df_with_target)` (in-cell), plus section prints.
- Reuse (imported, not copied): `validate_dataframe`, `add_derived_features`, `DERIVED_FEATURES`.

## Classes
- None. Pure modules/notebook functions; no new classes in this slice.

## Dependencies
- `pandas`, `numpy`, `matplotlib`, `seaborn` (in `requirements-colab.txt`).
- Imports internal `pipeline.validation.schemas`, `pipeline.feature_engineering.features`.
- No `sklearn`, `xgboost`, `torch`, `tensorflow`.

## Testing / Validation
- Run every cell top-to-bottom in a fresh kernel via `jupyter nbconvert --to notebook --execute`; assert no exception.
- Checks: shape `(1000, 9)`, no `inf` in derived columns, `P(default)` printed per split, all sections have non-empty output.
- `python -m compileall pipeline/ tests/` clean (already passing).

## Implementation Order
1. Write `notebooks/creditflow_eda.ipynb` cells in the listed order.
2. Pin `requirements-colab.txt`.
3. Execute notebook fully and confirm outputs (nbconvert execute).
4. Commit `notebooks/creditflow_eda.ipynb` + outputs; verify git-tracked.

## Acceptance
- `notebooks/creditflow_eda.ipynb` runs clean end-to-end with real outputs (all cells have output).
- No `.fit()`, no training, no GPU, no forbidden imports.
- Reproducible with `random_state=42`.
- Cells are thin over `pipeline/` modules, no duplicated logic.