# MLflow Self-Hosted Stack (Local / On-Prem)

Minimal, persistent MLflow 3.16 tracking stack for CreditFlow local development:
**PostgreSQL 16** as the backend (experiments / runs / params / metrics / tags / registry)
plus an **MLflow server** as the artifact store.

It is a compose `profile` (`mlflow`), so it never starts by accident when you run the
full stack.

---

## 1. Quick start

```bash
# 1) Start only the MLflow profile
docker compose --profile mlflow up -d

# 2> Wait until the MLflow server answers
docker compose --profile mlflow ps          # state=running, health=healthy
curl -s http://localhost:5050/              # 200

# 3) Point the training client at it
export MLFLOW_TRACKING_URI=http://localhost:5050

# 4) Train + log (parent benchmark_<utc> + 4 child runs + artifacts + registry)
python scripts/train_models.py --reason "reason here"

# 5) Read-only client verification (exit 0 = everything present)
scripts/verify_mlflow.py
```

---

## 2. Services & compose layout

`docker-compose.yml` defines, under `profiles: ["mlflow"]`:

| Service | Image | Role |
|---|---|---|
| `postgres-mlflow` | `postgres:16-alpine` | Backend store (experiments, runs, metrics, registry). Named volume `mlflow-postgres`. |
| `mlflow` | `ghcr.io/mlflow/mlflow:v3.16.0` | Metrics/params tags + artifact store. |

`mlflow` command:

```bash
pip install --no-cache-dir psycopg2-binary &&
mlflow server --host 0.0.0.0 --port 5000 \
  --backend-store-uri postgresql+psycopg2://${MLFLOW_DB_USER:-mlflow}:${MLFLOW_DB_PASSWORD:-mlflow-dev-only}@postgres-mlflow:5432/${MLFLOW_DB_NAME:-mlflow} \
  --default-artifact-root /tmp/creditflow-mlflow-artifacts
```

Server URL: `http://localhost:5050` (host `5050` → container `5000`).

### Artifact root (important)

`--default-artifact-root /tmp/creditflow-mlflow-artifacts` is **not** a docker volume—it is a
**host bind mount** to a directory on the machine running Docker:

```yaml
volumes:
  - /tmp/creditflow-mlflow-artifacts:/tmp/creditflow-mlflow-artifacts
```

- Files logged by the client land here and persist across container restarts (as long as the
  host `/tmp` is not wiped).
- The MLflow server reads artifacts directly from this directory, so no `artifacts` docker
  volume is required.
- `models/production/SHA256SUMS` + `reference_stats.json` are regenerated on every training run;
  refresh with `scripts/check_artifacts.py --build --dir models/production`.

---

## 3. Environment variables

Only the `mlflow` profile uses these, and only as **gitignored placeholders** (see `.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `MLFLOW_TRACKING_URI` | `http://localhost:5050` | **client-only**. Export it in the shell before running `scripts/train_models.py`. Not a compose/env setting. |
| `MLFLOW_DB_USER` | `mlflow` | Postgres backend store user. |
| `MLFLOW_DB_PASSWORD` | `mlflow-dev-only` | Real value goes in `.env` (gitignored); never commit. |
| `MLFLOW_DB_NAME` | `mlflow` | Postgres backend store database. |

---

## 4. Healthcheck & recovery

- `postgres-mlflow` healthcheck: `pg_isready -U ${MLFLOW_DB_USER:-mlflow}`.
- `mlflow` healthcheck: `GET http://localhost:5050/` returns `200`.
  (An earlier version pointed the healthcheck at `experiments/search` which 400s with POST;
  it was corrected to `/`.)

### Restart / purge (persistence test)

```bash
# restart the mlflow service only (Postgres + artifacts survive)
docker compose --profile mlflow restart mlflow

# or tear down and bring back up (data kept because of named volumes)
docker compose --profile mlflow down
docker compose --profile mlflow up -d
```

After restart, the existing experiments, runs, metrics and artifacts survive. Verified with
`scripts/verify_mlflow.py` (same `experiment_id`, same parent run id, identical artifacts,
registry version intact).

---

## 5. Verification script

`scripts/verify_mlflow.py` is a **read-only** client check (no writes):

- experiment `creditflow-risk` exists;
- the latest `benchmark_*` parent run is FINISHED with all business params
  (`dataset`, `dataset_sha256`, `rows`, `fn_cost`, `fp_cost`, `production_model`);
- the 4 children (`decision_tree`, `logistic_regression`, `random_forest`, `xgboost`) exist and
  all carry the 9 metrics (`val_accuracy`, `val_precision`, `val_recall`, `val_f1`,
  `test_accuracy`, `test_precision`, `test_recall`, `test_f1`, `business_cost`);
- parent artifacts are non-empty (`benchmark_results.csv`, `meta.json`, `pipeline.joblib`);
- registered model versions exist in the `creditflow-risk` registry.

Exits non-zero with the first missing piece; otherwise prints `MLFLOW_VERIFY=OK`.

---

## 6. Security & production notes

- **Local / dev only.** Not production-HA. `postgres-mlflow` uses a plaintext default
  password for localhost convenience; put a real value in `.env` and keep it out of git.
- `MLFLOW_TRACKING_URI` is passed to the **client**, never stored or echoed.
- The MLflow server does not need a static token for local dev, but treat
  `http://localhost:5050` as internal-only.
