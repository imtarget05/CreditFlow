# JD Coverage — CreditFlow

> **Status**: Interview package evidence
> **Disclaimer**: CreditFlow uses a public proxy dataset and is a simulated ML pipeline, not a production banking system.

## Emphasis

- **MLOps pipeline**: end-to-end reproducible pipeline (`pipeline/`) with validation, feature engineering, sklearn Pipeline, 4-model benchmark, cost-aware threshold, MLflow tracking + registry.
- **Production API**: FastAPI service with 7 endpoints, Pydantic validation, runtime metrics, drift monitoring.
- **Containerization**: Dockerfile + compose + render.yaml ready for cloud-style deployment.
- **Monitoring**: `/metrics` for system + `/drift` for PSI-based data/prediction drift.
- **Explainability**: rule-based reasons + LangGraph audit trail + optional LLM explanation with grounded RAG over policy corpus.

## Downplay / caveats

- **Cloud deployment**: `render.yaml` committed and container verified locally; actual Render deploy is not run in this repo (owner action required).
- **Frontend**: React UI is present and demoable, but the project's primary signal is backend + ML engineering, not frontend craftsmanship.
- **Domain**: tabular credit risk on proxy data; not automotive/vision/IoT domain unless explicitly framed as transferable ML engineering skills.

## Evidence commands

```bash
# Backend
python scripts/train_models.py
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080
curl http://localhost:8080/health

# Tests
python3 -m pytest tests/ -q

# Frontend
npm run build --prefix frontend

# Docker
docker build -t creditflow-api .
docker run -p 8080:8080 --env-file .env.example creditflow-api
```

## Talking points

1. Threshold chosen by business cost (FN=5, FP=1), not accuracy.
2. Model registry + experiment tracking with MLflow.
3. Drift monitoring with PSI, documented limitation: no performance degradation without labels.
4. LLM never decides; explain node only, with template fallback.
