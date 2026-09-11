# CreditFlow — inference service image
# Build:   docker build -t creditflow-api .
# Run:     docker run -p 8080:8080 --env-file .env creditflow-api
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