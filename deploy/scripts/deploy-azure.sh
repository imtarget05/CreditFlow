#!/usr/bin/env bash
# ==============================================================================
# CreditFlow — Automated Deployment Script for Azure Container Apps (ACA)
# Deploys backend API service with scale-to-zero (minReplicas=0) for cost efficiency
# ==============================================================================

set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:-rg-portfolio-prod}"
LOCATION="${LOCATION:-southeastasia}"
ACR_NAME="${ACR_NAME:-}"
CONTAINERAPPS_ENVIRONMENT="${CONTAINERAPPS_ENVIRONMENT:-cae-portfolio-env}"
APP_NAME="creditflow-api"
TARGET_PORT=8080
IMAGE_TAG="${IMAGE_TAG:-latest}"
API_KEY="${CREDITFLOW_API_KEY:-}"
# Canonical frontend is Cloudflare Pages; GitHub Pages kept during transition.
# backend/security.py:allowed_origins() accepts a comma-separated list.
API_ORIGIN="https://imtarget05.github.io"
ACA_SECRETS=("creditflow-api-key=${API_KEY}")
ACA_ENV_VARS=(
    "PORT=8080"
    "CREDITFLOW_API_KEY=secretref:creditflow-api-key"
    "CREDITFLOW_CORS_ORIGINS=${API_ORIGIN}"
)

if [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] && [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    ACA_SECRETS+=("cf-account-id=${CLOUDFLARE_ACCOUNT_ID}")
    ACA_SECRETS+=("cf-api-token=${CLOUDFLARE_API_TOKEN}")
    ACA_ENV_VARS+=("CREDITFLOW_LLM_PROVIDER=cloudflare")
    ACA_ENV_VARS+=("CLOUDFLARE_MODEL=@cf/meta/llama-3.2-1b-instruct")
    ACA_ENV_VARS+=("CLOUDFLARE_ACCOUNT_ID=secretref:cf-account-id")
    ACA_ENV_VARS+=("CLOUDFLARE_API_TOKEN=secretref:cf-api-token")
elif [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] || [ -n "${CLOUDFLARE_API_TOKEN:-}" ]; then
    echo "❌ Error: Set both CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN, or neither."
    exit 1
fi

if [ -z "$API_KEY" ]; then
    echo "❌ Error: CREDITFLOW_API_KEY must be supplied from a secret store."
    exit 1
fi

echo "=========================================================="
echo "🚀 Deploying CreditFlow API to Azure Container Apps"
echo "Resource Group: $RESOURCE_GROUP | Location: $LOCATION"
echo "=========================================================="

if ! command -v az &> /dev/null; then
    echo "❌ Error: Azure CLI (az) is not installed."
    echo "💡 Install via Homebrew: brew install azure-cli"
    exit 1
fi

echo "🔍 Verifying Azure login status..."
az account show --output none || {
    echo "⚠️ Not logged in. Prompting for Azure login..."
    az login
}

echo "📦 Ensuring Resource Group [$RESOURCE_GROUP] exists..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output table

if [ -z "$ACR_NAME" ]; then
    SUBSCRIPTION_ID=$(az account show --query id -o tsv | tr -d '-' | cut -c1-6)
    ACR_NAME="crportfolio${SUBSCRIPTION_ID}"
fi

echo "🏭 Ensuring Azure Container Registry [$ACR_NAME] exists..."
if ! az acr show --name "$ACR_NAME" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    az acr create \
        --name "$ACR_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --sku Basic \
        --admin-enabled true \
        --location "$LOCATION" \
        --output table
fi

echo "🔨 Building and pushing image [$APP_NAME:$IMAGE_TAG] using ACR cloud build..."
az acr build \
    --registry "$ACR_NAME" \
    --image "${APP_NAME}:${IMAGE_TAG}" \
    --file backend/Dockerfile \
    . \
    --output table

ACR_LOGIN_SERVER=$(az acr show --name "$ACR_NAME" --query loginServer -o tsv)
ACR_ADMIN_PASSWORD=$(az acr credential show --name "$ACR_NAME" --query "passwords[0].value" -o tsv)
ACR_ADMIN_USERNAME=$(az acr credential show --name "$ACR_NAME" --query username -o tsv)

echo "🌐 Ensuring Container Apps Environment [$CONTAINERAPPS_ENVIRONMENT] exists..."
if ! az containerapp env show --name "$CONTAINERAPPS_ENVIRONMENT" --resource-group "$RESOURCE_GROUP" &> /dev/null; then
    az containerapp env create \
        --name "$CONTAINERAPPS_ENVIRONMENT" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --output table
fi

echo "🚀 Deploying Container App [$APP_NAME] with Scale-to-Zero (min-replicas=0)..."

if az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --output none 2>/dev/null; then
    echo "🔐 Updating the API secret and production environment..."
    az containerapp secret set \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --secrets "${ACA_SECRETS[@]}" \
        --output none
    az containerapp update \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --image "${ACR_LOGIN_SERVER}/${APP_NAME}:${IMAGE_TAG}" \
        --set-env-vars \
            "${ACA_ENV_VARS[@]}" \
        --output table
else
    az containerapp create \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --environment "$CONTAINERAPPS_ENVIRONMENT" \
        --image "${ACR_LOGIN_SERVER}/${APP_NAME}:${IMAGE_TAG}" \
        --registry-server "$ACR_LOGIN_SERVER" \
        --registry-username "$ACR_ADMIN_USERNAME" \
        --registry-password "$ACR_ADMIN_PASSWORD" \
        --target-port "$TARGET_PORT" \
        --ingress external \
        --min-replicas 0 \
        --max-replicas 1 \
        --cpu 0.5 \
        --memory 1.0Gi \
        --secrets "${ACA_SECRETS[@]}" \
        --env-vars "CREDITFLOW_ENV=production" "${ACA_ENV_VARS[@]}" \
        --output table
fi

APP_URL=$(az containerapp show --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --query "properties.configuration.ingress.fqdn" -o tsv)

echo "=========================================================="
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "🔗 Public Live API: https://${APP_URL}/docs"
echo "=========================================================="
