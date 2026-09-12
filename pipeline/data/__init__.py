"""Data generation & persistence for CreditFlow.

The dataset is a **synthetic proxy** (spec §19, constraint 2) used to exercise the
production pipeline end-to-end: validate → clean → EDA → feature engineering →
train → evaluate → select → register (MLflow) → serve (FastAPI).
"""