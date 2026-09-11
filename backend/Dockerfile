# CreditFlow — inference service image (backend service).
# Build context is the REPO ROOT (needs backend/ + pipeline/ + models/):
#   docker compose build api
#   docker build -f backend/Dockerfile -t creditflow-api .
# Run via compose (preferred — đúng network/service layout của repo):
#   docker compose up api
# Secrets (CLOUDFLARE_*) are NOT baked in — pass via --env-file or -e flags.
# Health:  curl http://localhost:8080/health
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    MLFLOW_DISABLE_AGENT_HINT=1

WORKDIR /app

# Python + backend deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + trained pipeline/model + benchmark artifacts
COPY backend/ backend/
COPY pipeline/ pipeline/
COPY models/production/ models/production/
EXPOSE 8080

CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8080"]