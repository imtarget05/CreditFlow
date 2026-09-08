# Implementation Plan — CreditFlow EDA Notebook

## Overview

Build one clean, reproducible EDA **`.ipynb`** for CreditFlow on synthetic data (proxy). The notebook is the single deliverable: self-contained, Colab-ready, reproducible (`random_state=42`), and free of `.fit()`/training per `docs/spec.md §19`.

## Types

- Synthetic dataset schema (8 raw columns + `default` target), matching `CANONICAL_COLUMNS`.
- Constants: `n=1000`, `random_state=42`, `P(default)≈0.12`.

## Files

| Action | Path | Purpose |
|--------|------|---------|
| new | `notebooks/creditflow_eda.ipynb` | clean EDA notebook, the deliverable |
| modify | `requirements-colab.txt` | ensure pandas/numpy/matplotlib/seaborn pinned |
| keep | `pipeline/validation/schemas.py` | imported by notebook for validation demo |
| keep | `pipeline/feature_engineering/features.py` | imported by notebook for FE demo |

## Notebook Structure (cells)

1. **Setup** — `%pip install -q -r requirements-colab.txt`; imports; display options; `random_state=42`.
2. **Generate synthetic data** — 8 raw features + `default`; print shape, columns, head, global `P(default)`.
3. **Q1 — Characteristics of default customers** — group-by-default stats.
4. **Q2 — Feature correlation with default** — correlation table/heatmap.
5. **Q3 — Class imbalance** — class distribution + `P(default)` per split.
6. **Q4 — Leakage investigation** — `previous_defaults` vs target; time-based flag check.
7. **Q5 — Missing / outliers / duplicates** — validation via `validate_dataframe`.
8. **Q6 — Feature engineering demo** — `add_derived_features` on sample rows.
9. **Q7 — Validation demo** — clean vs bad row, show violations.
10. **Requirements check** — print library versions; assert no `.fit()` / no forbidden imports.

## Functions

- Notebook cells: dataset generation + section prints. Reuse `validate_dataframe` and `add_derived_features` from `pipeline/`.

## Classes

None. Pure function/notebook code; no new classes.

## Dependencies

- `pandas`, `numpy`, `matplotlib`, `seaborn` (already in `requirements-colab.txt`).
- No `sklearn`, `xgboost`, `torch`, `tensorflow` in this notebook.

## Testing

- Run every cell top-to-bottom in a fresh kernel; verify no exceptions.
- Assert: shape `(1000, 9)`, no `inf`, `P(default)` printed, all sections produce output.
- `python -m compileall` on imported `pipeline/` modules.

## Implementation Order

1. Write `notebooks/creditflow_eda.ipynb` cells in the order above.
2. Pin `requirements-colab.txt`.
3. Execute all cells in a fresh kernel and confirm outputs.
4. Add `notebooks` outputs to git and commit.

## Acceptance

- `notebooks/creditflow_eda.ipynb` runs clean end-to-end with all outputs.
- No `.fit()`, no training, no GPU, no forbidden imports.
- Reproducible with `random_state=42`.