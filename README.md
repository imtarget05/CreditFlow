# 🏦 CreditFlow — ML Credit Risk Decision Engine

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-18-blue.svg)](https://reactjs.org/)
[![XGBoost](https://img.shields.io/badge/XGBoost-Enabled-blue.svg)](https://xgboost.readthedocs.io/)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-Enabled-orange.svg)](https://scikit-learn.org/)
[![MLflow](https://img.shields.io/badge/MLflow-3.16-blue.svg)](https://mlflow.org/)
[![Docker](https://img.shields.io/badge/Docker-Supported-blue.svg)](https://www.docker.com/)
[![Tests](https://img.shields.io/badge/Tests-426%20passing-success.svg)]()
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**CreditFlow** is a production-grade ML credit risk decision support system tailored for financial lending. Moving beyond simple notebook exercises, this is a fully deployable decision engine with a complete pipeline: **data validation → feature engineering → model training → evaluation → serving → monitoring**.

Designed with enterprise requirements in mind, it features tamper-evident audit trails, human-in-the-loop approval workflows, explainability, cryptographic decision snapshots, and rigorous cost-sensitive model selection.

## 🚦 Production status (audited 2026-10-06)

| Component | URL | State |
|---|---|---|
| Frontend (GitHub Pages) | https://imtarget05.github.io/CreditFlow/ | Deployed by `cd.yml` (`actions/deploy-pages`) |
| API (Azure Container Apps) | https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io | `creditflow-api` container app, scale 0-1, /health/live + /health/ready + /model/info verified 2026-10-07 |
| API (Render, canonical) | https://creditflow-api-9z1v.onrender.com | `/health/live`, `/health/ready`, `/model/info` respond (logistic_regression_v001, threshold 0.20); business endpoints return `401` until `CREDITFLOW_API_KEY` is configured — fail-closed by design |

Owner actions (dashboards — pipeline code is ready):

- Render: set `CREDITFLOW_API_KEY` + `CREDITFLOW_CORS_ORIGINS=https://imtarget05.github.io`.
- GitHub: add secret `CREDITFLOW_API_KEY` (same demo token; baked into the Pages
  bundle as `VITE_API_KEY` — a **demo access token**, not a security boundary)
  and `RENDER_DEPLOY_HOOK` (the CD backend job fails loudly until it exists).

---

## ✨ Key Features & Engineering Decisions

1. **Business-Driven Model Selection**: Evaluated Logistic Regression, Decision Tree, Random Forest, and XGBoost. The model was selected based on a **custom business cost metric**, not raw accuracy. 
2. **Cost-Sensitive Threshold Tuning**: Optimized for asymmetric costs (False Negative cost=5.0 for missed defaults, False Positive cost=1.0 for wrong rejections), resulting in an optimal decision threshold of `0.20`.
3. **Winning Model**: **Logistic Regression** (Business Cost=201, Recall=0.80, F1=0.57) outperformed XGBoost on the business cost metric and was selected for production.
4. **Robust Feature Engineering**: Engineered 5 derived features (`debt_to_income`, `loan_to_income`, `debt_to_loan`, `employment_stability`, `credit_history_year_ratio`) with zero-division guards and robust handling of edge cases.
5. **Stateful Decision Workflow**: Leverages **LangGraph** for a 12-node state machine (load → gateways → validate → risk → financials → fraud → policy → explain → decision → [human_approval] → execute → audit) that handles human interrupts for `REVIEW` decisions, pausing and resuming workflows asynchronously.
6. **Core-Banking Ledger & Tamper-Evident Decision Snapshots**: Employs SQLite/Postgres ledger with deterministic contract codes (`HDTD-YYYYMMDD-XXXX`), document persistence with SHA-256 artifact verification, and **cryptographic decision snapshots** (`snapshot_hash` SHA-256 digest locking decision ID, application ID, decision, probability, reviewer, timestamp, and model bundle checksum).
7. **Anti-Double-Click Approval Idempotency**: HITL approval endpoint enforces atomic `PENDING_REVIEW` reservation and `idempotency_key` deduplication, preventing race conditions and replay submissions.
8. **Explainable AI**: Deployment duy nhất: **PRIVATE ON-PREM** — lõi Deterministic ML + Rule Engine nội bộ (Rule Filter DTI/LTI + TF-IDF cosine inference), LLM memo chạy Private vLLM nội bộ (Local VPC: `ollama`/`local_vllm`/`private-vllm`). **Policy Guard cứng: data `CONFIDENTIAL` tuyệt đối chặn, không fallback Public Cloud** (OpenAI/Groq/Cloudflare/Anthropic/Google). The LLM *explains*, it does *not* decide. Training = Traditional ML Training from Scratch (LogReg/XGBoost tabular, `colab/train_credit_T4.ipynb`).
9. **Production Monitoring & Drift Domain Events**: Includes **PSI (Population Stability Index)** drift detection against baselines; emits `ModelDriftDetected` domain events to the outbox when distribution shift exceeds threshold.
10. **Model Integrity Verification**: Load-time gate checks model bundle checksum against `SHA256SUMS` and `manifest.json`.
11. **Comprehensive MLOps & Cloud-Native Topology**: Experiment tracking via MLflow; containerized deployments with Kubernetes manifests (`k8s/20-creditflow-api.yaml`) featuring HPA and hardened security profiles (dev-stub manifest — not a live cluster).


---

## 🏗 Architecture

The system orchestrates the flow of a loan application through validation, feature engineering, ML scoring, policy rules, and final disbursement.

```mermaid
graph TD
    A[Loan Application VND] --> B[Schema Validation]
    B --> C[Feature Engineering <br/> 5 derived features]
    C --> D[ML Pipeline <br/> StandardScaler + LogReg]
    D --> E[Cost-Aware Threshold Engine <br/> t=0.20]
    
    E -->|Score < 0.20| F[APPROVE]
    E -->|0.20 <= Score < 0.50| G[REVIEW]
    E -->|Score >= 0.50| H[REJECT]
    
    G --> I[LangGraph Interrupt <br/> Human Approval]
    
    F --> J[Disbursement Engine]
    I -->|Approved| J
    I -->|Rejected| H
    
    J --> K[Contract: HDTD-YYYYMMDD-XXXX]
    K --> L[SHA-256 Ledger Hash]
```

---

## 🛠 Tech Stack

| Category | Technologies |
|---|---|
| **Backend** | Python 3.12, FastAPI, Pydantic v2, Uvicorn |
| **ML & Data** | scikit-learn, XGBoost, Pandas, NumPy, Joblib |
| **MLOps & Monitoring** | MLflow 3.16, PSI Drift Detection |
| **Workflow & GenAI** | LangGraph, LangChain, Cloudflare Workers AI (Llama 3.2-1b) |
| **Frontend** | React 18, Vite, Vanilla CSS |
| **Storage & Ledger** | SQLite (with SHA-256 tamper-evident hashing) |
| **DevOps** | Docker, Docker Compose, GitHub Actions, Render, GitHub Pages |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Node.js 18+
- Docker & Docker Compose (optional but recommended)

### Option 1: Docker Compose (Recommended)
```bash
# Spin up the entire stack (Backend + Frontend + DB)
docker compose up --build -d
```

### Option 2: Local Development
#### Backend
```bash
# Install dependencies
pip install -r requirements.txt

# Start the FastAPI server
uvicorn backend.app:app --reload --port 8080
```

#### Frontend
```bash
# Navigate to frontend and start the dev server
cd frontend
npm install
npm run dev
```

#### MLOps (Training & Tracking)
```bash
# Start the operational MLflow tracking stack (PostgreSQL backend store + artifact volume)
docker compose --profile mlflow up -d

# Run model training pipeline with MLflow tracking (data persists across restarts)
MLFLOW_TRACKING_URI=http://localhost:5050 python scripts/train_models.py

# Or just browse the server that compose started:
# http://localhost:5050
```

---

## 🔌 API Reference

The backend provides a comprehensive REST API for predictions, workflow management, and monitoring.

### Core Endpoints
- `GET /health` - Service health and production model status
- `POST /predict` - Immediate ML prediction (synchronous)
- `GET /model/info` - Production model metadata and benchmark metrics
- `GET /metrics` - Runtime metrics and benchmark table
- `GET /drift` - PSI drift report for monitoring distribution shift
- `GET /llm/info` - Explanation provider status

### Workflow & State Management (LangGraph)
- `POST /predict/graph` - Initiate a full LangGraph decision workflow
- `POST /predict/graph/{thread_id}/approve` - Human approval (resumes a paused `REVIEW` workflow) — **API key required**
- `GET /predict/graph/{thread_id}` - Get current workflow state
- `GET /audit/{application_id}` - Full audit trail for an application

### Ledger & Records
- `GET /api/applications` - List all processed loan applications — **API key required**
- `GET /api/disbursements` - View the core-banking ledger (contracts and hashes) — **API key required**

### Authentication
The money-moving and PII endpoints above take one shared secret in the
`X-CreditFlow-API-Key` header. It comes from the environment only — never
from the repository — and the app **refuses to start** when
`CREDITFLOW_ENV=production` and no key is configured.

```bash
curl -H "X-CreditFlow-API-Key: $CREDITFLOW_API_KEY" \
  -H "Content-Type: application/json" \
  -X POST http://localhost:8080/predict/graph/<thread_id>/approve \
  -d '{"action":"approve"}'
```

| Variable | Purpose | Default |
| --- | --- | --- |
| `CREDITFLOW_API_KEY` | Shared secret for the protected endpoints | none — protected routes answer 401 without it |
| `CREDITFLOW_API_KEY_ID` | Identity recorded as `approver_id` (never taken from the request body) | `supervisor_on_duty` |
| `CREDITFLOW_API_KEY_ROLE` | Credit authority of the key: `UNDERWRITER_L1` or `RISK_COMMITTEE_L2` (from the authority matrix) | `UNDERWRITER_L1` |
| `CREDITFLOW_CORS_ORIGINS` | Comma-separated CORS allow-list; a wildcard is refused | `http://localhost:5173,http://localhost:8080` |

An approval is recorded against the authority the workflow itself demanded:
a `RISK_COMMITTEE_L2` application cannot be approved with an `UNDERWRITER_L1`
key (403, no disbursement written).

---

## 📂 Project Structure

```text
├── backend/
│   ├── app.py              # FastAPI application (CORS, routers, metrics)
│   ├── predict_service.py  # Prediction logic, VND→model units, thresholding
│   └── Dockerfile
├── pipeline/
│   ├── agent/              # LangGraph workflow (12 nodes)
│   │   ├── graph.py        # StateGraph builder
│   │   ├── nodes.py        # 12 discrete workflow nodes
│   │   ├── subagents/      # decoupled specialist agents (underwriting, fraud/compliance, explainability, disbursement)
│   │   ├── policy.py       # Financial policy rules
│   │   ├── fraud.py        # Rule-based fraud detection
│   │   ├── explanations.py # LLM Vietnamese explanations + template fallback
│   │   ├── retriever.py    # TF-IDF policy doc retriever
│   │   └── checkpointer.py # File-backed state persistence
│   ├── financial/          # Basel II/III metrics, risk-based pricing, amortization
│   ├── gateways/           # CIC bureau + bank-statement simulators
│   ├── disbursement/       # VietQR + loan-agreement + authority matrix
│   ├── data/               # Dataset generation + real-data ingestion
│   ├── feature_engineering/ # 5 derived features with edge-case guards
│   ├── modeling/           # Models factory, evaluation, threshold tuning
│   ├── monitoring/         # PSI drift detection
│   ├── storage/            # SQLite core-banking ledger
│   └── validation/         # Canonical schema + validation rules
├── frontend/               # React banking UI with print styling
├── models/production/      # Serialized pipeline + benchmark results + meta
├── notebooks/              # EDA + model benchmark notebooks
├── scripts/                # Training, deploy verification, eval
├── tests/                  # 426 tests (405 fast + 18 slow + 3 production integration; verified baseline below)
├── data/                   # Synthetic dataset (5k rows, seed 42)
├── docker-compose.yml      # Full-stack orchestration
└── render.yaml             # Cloud deployment configurations
```

---

## 🧪 Testing

The project maintains a high standard of reliability with a comprehensive test suite.

```bash
python -m pytest tests/ -v
```

**Current verified baseline (2026-10-07, clean clone of `b4b031b`):**
426 passed, 0 failed, 0 skipped on Python 3.12.13 with npm available and the
production Render backend warm/reachable.

| Tier | Tests |
|---|---|
| Fast (`not integration and not slow and not live and not infra`) | 405 |
| Slow (full LangGraph + model runs) | 18 |
| Production integration (`-m integration`) | 3 |

The three production integration tests (`tests/test_deploy_pages.py`) may skip
when `npm` is unavailable or when the Render free-tier backend is cold or
unreachable (first request after idle can take ~20-60 s, longer than the test
timeout). That is an infrastructure-availability condition, not a code
regression. `live` / `infra` tests only run with `LIVE_TESTS=1`.

---

## 📦 Data & Reproducibility

The system was trained on a **5,000-row synthetic proxy dataset** (seed `42`, ~12% default rate). The data generation process is entirely deterministic and reproducible, ensuring honest positioning (acting as a proxy, not real proprietary bank data) while allowing full end-to-end pipeline validation.

---

## ☁️ Deployment — CHỐT: PRIVATE ON-PREM (ENTERPRISE)

- **Deployment duy nhất: PRIVATE ON-PREM** — lõi Deterministic ML + Rule Engine nội bộ, LLM memo chạy Private vLLM nội bộ (Local VPC). Không fallback Public Cloud cho data CONFIDENTIAL.
- **Training**: Traditional ML Training from Scratch trên Colab (`colab/train_credit_T4.ipynb`, LogReg/XGBoost tabular).
- **Processing**: Rule Filter DTI/LTI + TF-IDF cosine inference (local, offline-safe).
- **Policy Guard cứng** (`pipeline/agent/explanations.py:is_public_cloud`): `CONFIDENTIAL` tuyệt đối chặn gửi ra public cloud (OpenAI/Groq/Cloudflare/Anthropic/Google) → route về Private vLLM (`ollama`/`local_vllm`) hoặc deterministic template fallback. Public cloud chỉ dùng cho data PUBLIC hoặc tắt hẳn.
- **Compliance**: Banking Secrecy / GDPR / SBV — CONFIDENTIAL never leaks. Xem `docs/DEPLOYMENT_PRIVATE_ONPREM.md`.
- **Planes**: `render.yaml` + GitHub Pages = **portfolio demo plane** (free tier,
  PUBLIC data only — see §Production status); enterprise production =
  **PRIVATE ON-PREM** như trên. Public cloud env chỉ dành cho data PUBLIC.

---

## 🛡️ Decision Safety (plan 2026-09-18)

- **Versioned model bundle**: `models/production/manifest.json` khoá SHA-256 của
  từng artifact + metadata hợp đồng (`feature_order`, `vnd_per_model_unit`).
  Startup validate bundle — thiếu/sai hợp đồng → 503 `MODEL_BUNDLE_INVALID`.
- **Replay-safe approval**: `POST /predict/graph/{thread}/approve` là một
  transaction (reserve `PENDING_REVIEW` + `approver_id` + `idempotency_key`,
  resume graph, chỉ disburse khi `APPROVE`). `UNIQUE(disbursements.application_id)`
  + idempotency key đảm bảo **một hồ sơ = một dòng tiền**; duplicate approval
  trả receipt cũ (`replay: true`).
- **Explicit failures**: model/gateway/checkpoint lỗi → `workflow_status=FAILED`
  + `error_code` (503), không bao giờ tạo row `PENDING_REVIEW`. Simulator gắn
  `source_mode=SIMULATION` và bị chặn khi `CREDITFLOW_ENV=production`.
- **Checkpoint JSON-only**: file hỏng được quarantine (timestamp) và health
  báo `CHECKPOINT_CORRUPT` thay vì âm thầm bỏ qua.

Chi tiết vận hành: `docs/DEPLOYMENT_PRIVATE_ONPREM.md` (§6).

---

## 🗃 MLflow Tracking (Self-Hosted, Local)

Self-hosted MLflow 3.16 tracking stack for local development: **PostgreSQL 16** as
the backend store + **MLflow server** as the artifact/metadata store. Persists
experiments, runs, params, metrics, tags, registry entries and model artifacts across
container restarts (Postgres + artifact volume survive `restart`/`down`-`up`).

### Compose

```bash
# start only the MLflow profile
docker compose --profile mlflow up -d

# tracking URI the training client must use
export MLFLOW_TRACKING_URI=http://localhost:5050
```

Services (compose `profiles: ["mlflow"]`):
- `postgres-mlflow` — PostgreSQL 16, named volume `mlflow-postgres` → backend store.
- `mlflow` — image `ghcr.io/mlflow/mlflow:v3.16.0`, runs `mlflow server --host 0.0.0.0 --port 5000`
  with `--backend-store-uri postgresql+psycopg2://...@postgres-mlflow:5432/...` and
  `--default-artifact-root /tmp/creditflow-mlflow-artifacts` (host bind mount).

Server URL: `http://localhost:5050` (host port 5050 → container port 5000).

### Verification

```bash
MLFLOW_TRACKING_URI=http://localhost:5050 scripts/verify_mlflow.py
# exit 0 when experiment / runs / parameters / metrics / artifacts / registry all present
```

### Env variables (`mlflow` profile only; gitignored placeholders)

See `.env.example`. `MLFLOW_TRACKING_URI` is a **client** env var for the Python training
process — it is not server configuration:

| Variable | Default | Meaning |
|---|---|---|
| `MLFLOW_TRACKING_URI` | `http://localhost:5050` | server URI used by the training client |
| `MLFLOW_DB_USER` | `mlflow` | Postgres backend store user |
| `MLFLOW_DB_PASSWORD` | `mlflow-dev-only` | **never commit real value**; set in `.env` |
| `MLFLOW_DB_NAME` | `mlflow` | Postgres backend store database |

### Notes / gotchas

- `MLFLOW_TRACKING_URI` must be exported on the **client** (the machine running
  `scripts/train_models.py`), not inside the compose file.
- On each training run the production bundle is regenerated; commit
  `models/production/manifest.json`, `meta.json`, `SHA256SUMS` and `reference_stats.json`
  alongside the model artifact (`pipeline.joblib`) — use
  `scripts/check_artifacts.py --build --dir models/production` to refresh the SHA-256 manifest.

