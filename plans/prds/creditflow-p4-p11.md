# PRD — CreditFlow P4-P11: Model Benchmark → Monitoring

> **Slice**: P4 through P11 / 11 phases (spec §8-§15)
> **Spec source**: `docs/spec.md` v0.1
> **Previous PRDs**: `plans/prds/creditflow-p1.md`, `plans/prds/creditflow-p2-p3.md`
> **Status**: Draft — ready for implementation
> **Principle**: Training code is Colab-ready. Agent prepares code + requirements + instructions. Owner controls GPU execution on Colab.

---

## 1. Goals (Definition of Done for P4-P11)

* [ ] **P4**: Benchmark 4 models (Logistic, Decision Tree, RandomForest, XGBoost) with comparison table
* [ ] **P5**: Evaluation with confusion matrix, business cost justification, threshold selection
* [ ] **P6**: Reproducible sklearn Pipeline (preprocessing + feature engineering + model)
* [ ] **P7**: MLflow tracking + model registry for experiment reproducibility
* [ ] **P8**: FastAPI inference service with 4 endpoints (`/predict`, `/health`, `/model/info`, `/metrics`)
* [ ] **P9**: Docker containerization of inference service
* [ ] **P10**: Cloud deployment with acceptance criteria (`/health=200`, `/predict` valid, version visible)
* [ ] **P11**: Monitoring — system metrics + data drift + prediction drift detection

## 2. Non-goals

* No frontend React (optional after backend stable, spec §16)
* No local GPU training (constraint §19 — Colab only)
* No real bank deployment (proxy dataset disclaimer §19.2)
* No heavy hyperparameter tuning (benchmark focus, not optimization)

## 3. Phase Breakdown

### P4: Model Benchmark

**Objective**: Train and compare 4 model types to establish baseline performance.

**Models**:
1. Logistic Regression (baseline, interpretable)
2. Decision Tree (interpretable, non-linear)
3. Random Forest (ensemble, robust)
4. XGBoost (gradient boosting, state-of-art tabular)

**Deliverables**:
- `notebooks/creditflow_model_benchmark.ipynb` — Colab-ready notebook
- `pipeline/modeling/train.py` — training functions (train_model, cross_validate)
- `pipeline/modeling/models.py` — model factory (get_models() → dict of configured models)
- `tests/test_models.py` — unit tests for model factory, input validation

**Acceptance Criteria**:
- [ ] All 4 models train without error on Colab
- [ ] Comparison table shows Accuracy, Precision, Recall, F1, ROC-AUC per model
- [ ] Training uses stratified k-fold cross-validation (k=5)
- [ ] Class imbalance handled via `class_weight='balanced'`
- [ ] No `.fit()` called in local test environment (only syntax/structure validation)

**Key Metrics** (business-driven per spec §9):
- Primary: **Recall** (detect defaults) + **F1** (balance)
- Secondary: Precision, ROC-AUC
- NOT Accuracy alone (imbalanced dataset)

---

### P5: Evaluation

**Objective**: Select best model and threshold based on business cost, not just metrics.

**Deliverables**:
- `notebooks/creditflow_evaluation.ipynb` — Colab-ready evaluation notebook
- `pipeline/modeling/evaluate.py` — evaluation functions (compute_metrics, confusion_matrix, find_optimal_threshold)
- `pipeline/modeling/threshold.py` — threshold selection logic (cost-sensitive)

**Acceptance Criteria**:
- [ ] Confusion matrix visualized for each model
- [ ] Business cost matrix defined: FN cost > FP cost (e.g., FN=5×FP per spec §9)
- [ ] Optimal threshold selected per model using F1 + cost analysis
- [ ] Final model + threshold selection justified in writing
- [ ] Evaluation report section in notebook documents decision

**Threshold Logic** (spec §4.4):
- Default: `>0.80 REJECT` / `0.50-0.80 REVIEW` / `<0.50 APPROVE`
- P5 validates/adjusts these based on confusion matrix + cost

---

### P6: ML Pipeline

**Objective**: Reproducible end-to-end pipeline (preprocessing → features → model).

**Deliverables**:
- `pipeline/ml_pipeline.py` — sklearn Pipeline with ColumnTransformer
- `pipeline/preprocessing.py` — preprocessing steps (scaling, encoding)
- `tests/test_pipeline.py` — pipeline integration tests

**Pipeline Architecture**:
```
Raw Data → Validation → Imputation → Feature Engineering → Scaling → Model
```

**Components**:
1. `DataValidator` — uses existing `validate_dataframe()`
2. `FeatureEngineer` — uses existing `add_derived_features()`
3. `Preprocessor` — StandardScaler for numeric features
4. `Classifier` — best model from P4-P5

**Acceptance Criteria**:
- [ ] Single `pipeline.fit(X, y)` and `pipeline.predict(X)` interface
- [ ] Pipeline serializable (joblib) for inference
- [ ] No data leakage: split before fit, all transforms in pipeline
- [ ] Reproducible with `random_state=42`

---

### P7: MLflow Tracking + Registry

**Objective**: Experiment tracking and model versioning for auditability.

**Deliverables**:
- `pipeline/modeling/tracking.py` — MLflow logging wrapper
- `mlruns/` — local tracking directory (gitignored, artifact store)
- `notebooks/creditflow_mlflow_demo.ipynb` — demonstrates tracking + registry

**Tracked Per Run**:
- Parameters: model type, hyperparameters, threshold
- Metrics: Accuracy, Precision, Recall, F1, ROC-AUC
- Artifacts: confusion matrix plot, model artifact, pipeline artifact
- Tags: dataset version, split seed, experiment name

**Model Registry**:
- Register best model as `creditflow-model`
- Version staging: `Staging` → `Production`
- Model version visible via `/model/info` endpoint (P8)

**Acceptance Criteria**:
- [ ] All P4 experiments logged to MLflow
- [ ] Best model registered with version
- [ ] MLflow UI accessible locally (`mlflow ui`)

---

### P8: FastAPI Inference Service

**Objective**: Production API serving model predictions with explainability.

**Deliverables**:
- `api/main.py` — FastAPI application
- `api/schemas.py` — Pydantic request/response models
- `api/prediction.py` — prediction service (load model, predict, explain)
- `api/routes.py` — endpoint definitions
- `tests/test_api.py` — API integration tests (TestClient)

**API Endpoints** (spec §12):

| Method | Endpoint | Response |
|--------|----------|----------|
| POST | `/predict` | `{decision, risk_probability, model_version, reasons}` |
| GET | `/health` | `{status: "ok", model_version}` |
| GET | `/model/info` | `{model_name, model_version, model_type, threshold}` |
| GET | `/metrics` | Prometheus-compatible metrics |

**Request Schema** (spec §4.1):
```json
{
  "income": 2500,
  "age": 32,
  "employment_years": 4,
  "loan_amount": 12000,
  "loan_term": 36,
  "existing_debt": 3500,
  "credit_history": 5,
  "previous_defaults": 0
}
```

**Response Schema** (spec §4.3):
```json
{
  "decision": "REVIEW",
  "risk_probability": 0.78,
  "model_version": "xgboost-v12",
  "reasons": ["high debt-to-income ratio", "short credit history"]
}
```

**Acceptance Criteria**:
- [ ] All 4 endpoints return correct response format
- [ ] Input validation rejects invalid data (income≤0, age<18, etc.)
- [ ] `/predict` returns risk_probability + decision + reasons
- [ ] `reasons` derived from engineered features (rule-based for P8)
- [ ] API runs locally: `uvicorn api.main:app --reload`
- [ ] Tests pass: `pytest tests/test_api.py`

---

### P9: Docker Containerization

**Objective**: Package inference service into container for deployment.

**Deliverables**:
- `Dockerfile` — multi-stage build (builder + runtime)
- `docker-compose.yml` — local orchestration (api + mlflow)
- `.dockerignore` — exclude unnecessary files
- `deploy/scripts/build.sh` — build script
- `deploy/scripts/run.sh` — run script

**Dockerfile Structure**:
```dockerfile
# Stage 1: Builder
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY api/ ./api/
COPY pipeline/ ./pipeline/
COPY models/ ./models/
ENV PATH=/root/.local/bin:$PATH
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Acceptance Criteria**:
- [ ] Image builds successfully: `docker build -t creditflow-api .`
- [ ] Container runs: `docker run -p 8000:8000 creditflow-api`
- [ ] `/health` returns 200 from container
- [ ] `/predict` returns valid prediction from container
- [ ] Image size < 500MB (slim base + multi-stage)

---

### P10: Cloud Deployment

**Objective**: Deploy containerized API to cloud platform.

**Deliverables**:
- `deploy/cloud/` — cloud deployment configs (AWS/GCP/Azure)
- `deploy/cloud/README.md` — deployment instructions
- `deploy/cloud/deploy.sh` — deployment script

**Target Platform**: AWS (ECS Fargate or Elastic Beanstalk) — free tier eligible

**Architecture**:
```
Internet → Cloud Load Balancer → FastAPI Container → ML Model
```

**Acceptance Criteria** (spec §14):
- [ ] `GET /health` returns 200 from cloud URL
- [ ] `POST /predict` returns valid prediction from cloud URL
- [ ] `GET /model/info` shows model version from cloud URL
- [ ] Deployment documented with steps to reproduce

---

### P11: Monitoring

**Objective**: System + ML monitoring for production observability.

**Deliverables**:
- `monitoring/metrics.py` — Prometheus metrics wrapper
- `monitoring/drift.py` — data drift + prediction drift detection
- `monitoring/prometheus.yml` — Prometheus config
- `monitoring/grafana-dashboard.json` — Grafana dashboard template
- `docker-compose.monitoring.yml` — full stack (api + prometheus + grafana)

**System Metrics** (spec §15):
- Request count, latency (p50/p95/p99), error rate
- CPU, memory usage

**ML Metrics** (spec §15):
- **Data drift**: Compare production feature distributions vs training (PSI, KS test)
- **Prediction drift**: Monitor `P(HIGH)` rate over time
- **Performance degradation**: Track F1 when delayed labels available

**Acceptance Criteria**:
- [ ] `/metrics` endpoint exposes Prometheus-format metrics
- [ ] Data drift detection flags when production distribution shifts
- [ ] Prediction drift monitors HIGH-RISK rate over time
- [ ] Grafana dashboard visualizes key metrics
- [ ] Alert rules defined for drift thresholds

---

## 4. File Structure (Target)

```
CreditFlow/
├── api/                          # P8: FastAPI service
│   ├── __init__.py
│   ├── main.py
│   ├── schemas.py
│   ├── prediction.py
│   └── routes.py
├── pipeline/                     # P6: ML Pipeline
│   ├── __init__.py
│   ├── ml_pipeline.py
│   ├── preprocessing.py
│   ├── modeling/                 # P4-P5: Training + Evaluation
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── train.py
│   │   ├── evaluate.py
│   │   ├── threshold.py
│   │   └── tracking.py           # P7: MLflow
│   ├── validation/               # P2: Existing
│   │   ├── __init__.py
│   │   └── schemas.py
│   └── feature_engineering/      # P3: Existing
│       ├── __init__.py
│       └── features.py
├── monitoring/                   # P11
│   ├── __init__.py
│   ├── metrics.py
│   ├── drift.py
│   ├── prometheus.yml
│   └── grafana-dashboard.json
├── notebooks/                    # Colab-ready
│   ├── creditflow_eda.ipynb      # P2: Existing
│   ├── creditflow_model_benchmark.ipynb  # P4
│   ├── creditflow_evaluation.ipynb       # P5
│   └── creditflow_mlflow_demo.ipynb      # P7
├── tests/                        # All phases
│   ├── __init__.py
│   ├── test_features.py          # P3: Existing
│   ├── test_models.py            # P4
│   ├── test_pipeline.py          # P6
│   ├── test_api.py               # P8
│   └── test_monitoring.py        # P11
├── deploy/                       # P9-P10
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── docker-compose.monitoring.yml
│   ├── .dockerignore
│   ├── scripts/
│   │   ├── build.sh
│   │   └── run.sh
│   └── cloud/
│       ├── README.md
│       └── deploy.sh
├── models/                       # Serialized models (gitignored)
│   └── .gitkeep
├── docs/
│   ├── spec.md
│   └── architecture/
├── requirements.txt              # API + pipeline dependencies
├── requirements-colab.txt        # Colab training dependencies
└── README.md
```

---

## 5. Dependencies

**`requirements.txt`** (API + Pipeline — local):
```
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
pydantic>=2.0.0
pandas>=1.3.0
numpy>=1.21.0
scikit-learn>=1.0.0
joblib>=1.3.0
mlflow>=2.8.0
prometheus-client>=0.17.0
```

**`requirements-colab.txt`** (Training — Colab):
```
pandas>=1.3.0
numpy>=1.21.0
matplotlib>=3.4.0
seaborn>=0.11.0
scikit-learn>=1.0.0
xgboost>=1.7.0
mlflow>=2.8.0
```

---

## 6. Implementation Order

| Order | Phase | Depends On | Est. Complexity |
|-------|-------|------------|-----------------|
| 1 | P4 Model Benchmark | P1-P3 complete | Medium |
| 2 | P5 Evaluation | P4 complete | Medium |
| 3 | P6 ML Pipeline | P4-P5 complete | High |
| 4 | P7 MLflow | P6 complete | Medium |
| 5 | P8 FastAPI | P6-P7 complete | High |
| 6 | P9 Docker | P8 complete | Medium |
| 7 | P10 Cloud | P9 complete | Medium |
| 8 | P11 Monitoring | P8 complete | High |

**Recommended Execution**: Sequential (each phase builds on previous). P11 can run parallel to P9-P10.

---

## 7. Testing Strategy

| Phase | Test Type | Tool | What to Test |
|-------|-----------|------|--------------|
| P4 | Unit | pytest | Model factory returns 4 models, correct config |
| P4 | Integration | Colab | All 4 models train, metrics computed |
| P5 | Unit | pytest | Threshold logic, cost matrix calculation |
| P6 | Unit | pytest | Pipeline fit/predict interface, serialization |
| P6 | Integration | pytest | End-to-end pipeline with sample data |
| P7 | Integration | Colab | MLflow logs params/metrics/artifacts |
| P8 | Unit | pytest | Pydantic schema validation |
| P8 | Integration | TestClient | All endpoints return correct format |
| P9 | Integration | Docker | Container builds, endpoints accessible |
| P10 | Manual | curl | Cloud endpoints return correct responses |
| P11 | Unit | pytest | Metrics collection, drift detection logic |

---

## 8. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Colab GPU unavailable | Training blocked | Use CPU-compatible code; fallback to smaller dataset |
| XGBoost install issues | Model fails | Provide `pip install xgboost` in Colab cell; fallback to sklearn GradientBoosting |
| MLflow UI conflicts | Tracking fails | Use local `mlruns/` directory; document port configuration |
| Docker image too large | Slow deployment | Multi-stage build, slim base image, `.dockerignore` |
| Cloud costs exceed free tier | Deployment blocked | Document cost estimates; use free-tier eligible services |
| Data drift false positives | Alert fatigue | Tune PSI thresholds; document baseline distributions |

---

## 9. Constraints (from spec §19)

1. **Training on Colab**: All heavy training runs on Colab via extension. Agent prepares code only.
2. **Proxy dataset disclaimer**: All artifacts must note simulation nature.
3. **No steady-state compatibility**: Old paths removed when spec changes.
4. **Portfolio discipline**: CreditFlow = classical ML productionized (complements MAIA + Hermes).

---

## 10. Acceptance Scenarios (Definition of Done)

- [ ] All 4 models benchmarked with comparison table (P4)
- [ ] Best model selected with business cost justification (P5)
- [ ] Reproducible pipeline serializable to joblib (P6)
- [ ] MLflow tracks all experiments, model registered (P7)
- [ ] FastAPI serves predictions with all 4 endpoints (P8)
- [ ] Docker container runs inference service (P9)
- [ ] Cloud deployment accessible with valid responses (P10)
- [ ] Monitoring detects drift and exposes metrics (P11)
- [ ] All tests pass: `pytest tests/`
- [ ] Notebooks are Colab-ready with clear execution instructions
- [ ] Model version retrievable for inference