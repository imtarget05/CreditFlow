"""Tests for GitHub Pages deployment artifacts (frontend + Azure backend).

No live Cloudflare API calls. Network-dependent tests are marked
``pytest.mark.integration`` so they still run, but are identifiable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FRONTEND_DIR = ROOT / "frontend"
ENV_PROD = FRONTEND_DIR / ".env.production"
REDIRECTS_SRC = FRONTEND_DIR / "public" / "_redirects"
REDIRECTS_BUILD = FRONTEND_DIR / "dist" / "_redirects"
DEPLOY_DOCS = ROOT / "docs" / "deployment.md"

AZURE_BASE = "https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io"
# Money fields follow the API money-unit contract (VND).
SAMPLE_PAYLOAD = {
    "income": 8000000,
    "age": 35,
    "employment_years": 8,
    "loan_amount": 120000000,
    "loan_term": 36,
    "existing_debt": 15000000,
    "credit_history": 9,
    "previous_defaults": 0,
}


# ---------------------------------------------------------------------------
# GitHub Pages frontend artifacts
# ---------------------------------------------------------------------------

def test_pages_env_production_uses_https_backend_url():
    assert ENV_PROD.exists(), f"missing {ENV_PROD}"
    content = ENV_PROD.read_text(encoding="utf-8")
    configured_base = next(
        (
            line.partition("=")[2]
            for line in content.splitlines()
            if line.startswith("VITE_API_BASE=")
        ),
        "",
    )
    assert configured_base == AZURE_BASE


def test_pages_workflow_uses_azure_as_the_canonical_backend():
    workflow = ROOT / ".github" / "workflows" / "cd.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "API_BASE: https://creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io" in content
    assert "VITE_API_BASE: ${{ env.API_BASE }}" in content
    assert "deploy-backend-render:" not in content


def test_creditflow_docs_and_production_checks_only_use_azure_backend():
    docs = (ROOT / "README.md").read_text(encoding="utf-8")
    smoke = (ROOT / "scripts/smoke_production.py").read_text(encoding="utf-8")
    assert "creditflow-api-9z1v.onrender.com" not in docs
    assert "creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io" in smoke


def test_production_smoke_requires_readiness_not_only_liveness():
    smoke = (ROOT / "scripts/smoke_production.py").read_text(encoding="utf-8")

    assert 'req("/health/ready")' in smoke
    assert 'readiness_status == "ready"' in smoke
    assert "health/ready reports ready" in smoke


def test_azure_deploy_workflow_runs_production_smoke_after_deploy():
    workflow = ROOT / ".github" / "workflows" / "deploy-azure.yml"
    content = workflow.read_text(encoding="utf-8")
    deploy_script = (ROOT / "deploy/scripts/deploy-azure.sh").read_text(encoding="utf-8")

    assert "Azure production readiness and API smoke" in content
    assert "production must fail closed on simulated gateways" in content
    assert "AZURE_CREDENTIALS" in content
    assert 'run: PYTHONPATH=. pytest tests/ -q -m "not integration and not slow and not live and not infra" --strict-markers' in content
    assert "production must fail closed on simulated gateways" in content
    assert 'az containerapp secret set' in deploy_script
    assert 'CREDITFLOW_API_KEY=secretref:creditflow-api-key' in deploy_script
    assert '"CREDITFLOW_ENV=production"' not in deploy_script.split('--set-env-vars', 1)[-1].split('--output table', 1)[0]
    assert '--env-vars "CREDITFLOW_ENV=production" "${ACA_ENV_VARS[@]}"' in deploy_script
    assert 'if [ -z "$API_KEY" ]; then' in deploy_script
    assert 'API_KEY="${CREDITFLOW_API_KEY:-}"' in deploy_script
    assert "workflow_dispatch:" in content
    assert "workflow_call:" in content
    assert "Production smoke (Azure API)" not in (ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")
    assert "push:" not in content
    assert "Check the test, login, deploy and smoke step results above" in content


def test_creditflow_keepalive_targets_azure():
    workflow = ROOT / ".github" / "workflows" / "keepalive.yml"
    content = workflow.read_text(encoding="utf-8")

    assert "creditflow-api.blackisland-5a3f0246.southeastasia.azurecontainerapps.io" in content
    assert "creditflow-api-9z1v.onrender.com" not in content
    assert "schedule:" not in content


def test_pages_build_does_not_bake_shared_api_key_into_browser_bundle():
    workflow = ROOT / ".github" / "workflows" / "cd.yml"
    app_source = FRONTEND_DIR / "src" / "App.jsx"
    build_config = workflow.read_text(encoding="utf-8")

    assert "VITE_API_KEY" not in ENV_PROD.read_text(encoding="utf-8")
    assert "VITE_API_KEY" not in app_source.read_text(encoding="utf-8")
    assert "VITE_API_KEY" not in build_config
    assert "X-CreditFlow-API-Key" not in app_source.read_text(encoding="utf-8")


def test_creditflow_cors_is_limited_to_the_github_pages_origin():
    security = (ROOT / "backend/security.py").read_text(encoding="utf-8")
    app_source = (ROOT / "backend/app.py").read_text(encoding="utf-8")

    assert '"https://imtarget05.github.io"' in security
    assert "creditflow-1cg.pages.dev" not in security
    assert "allow_origin_regex=None" in app_source


def test_azure_workflow_is_gated_by_the_full_suite_and_pages_workflow():
    content = (ROOT / ".github/workflows/deploy-azure.yml").read_text(encoding="utf-8")
    pages_workflow = (ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")

    assert 'run: PYTHONPATH=. pytest tests/ -q -m "not integration and not slow and not live and not infra" --strict-markers' in content
    assert "uses: ./.github/workflows/deploy-azure.yml" in pages_workflow
    assert "secrets: inherit" in pages_workflow
    assert "needs: [deploy-azure]" in pages_workflow
    assert "production must fail closed on simulated gateways" in content


def test_production_ui_keeps_ledger_and_human_approval_fail_closed():
    app_source = (FRONTEND_DIR / "src" / "App.jsx").read_text(encoding="utf-8")

    assert "API_KEY_NOT_CONFIGURED" in app_source
    assert 'const ledgerAuthorized = false;' in app_source
    assert "disabled={!ledgerAuthorized}" in app_source
    assert "if (!ledgerAuthorized) return;" in app_source


def test_azure_deployment_requires_key_and_never_places_it_in_pages_build():
    deploy_script = (ROOT / "deploy/scripts/deploy-azure.sh").read_text(encoding="utf-8")
    deploy_workflow = (ROOT / ".github/workflows/deploy-azure.yml").read_text(encoding="utf-8")
    pages_workflow = (ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")

    assert 'if [ -z "$API_KEY" ]; then' in deploy_script
    assert "CREDITFLOW_API_KEY: ${{ secrets.CREDITFLOW_API_KEY }}" in deploy_workflow
    assert "CREDITFLOW_API_KEY" not in pages_workflow
    assert "CREDITFLOW_API_KEY" not in (ROOT / "render.yaml").read_text(encoding="utf-8")


def test_pages_redirects_file_exists_for_spa_routing():
    assert REDIRECTS_SRC.exists(), f"missing {REDIRECTS_SRC}"
    content = REDIRECTS_SRC.read_text(encoding="utf-8")
    assert "/*    /index.html   200" in content, "_redirects must contain SPA catch-all rule"


@pytest.mark.integration
def test_pages_build_output_contains_redirects():
    """Run the real frontend build and verify _redirects is copied to dist/."""
    import shutil
    if not shutil.which("npm"):
        pytest.skip("npm not installed or not in PATH")
    try:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=FRONTEND_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        pytest.skip(f"npm run build failed (e.g. node_modules absent): {exc}")
    assert REDIRECTS_BUILD.exists(), (
        f"npm run build did not produce {REDIRECTS_BUILD}"
    )


# ---------------------------------------------------------------------------
# Azure backend live checks
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_azure_backend_health_endpoint_reachable():
    try:
        resp = requests.get(f"{AZURE_BASE}/health/live", timeout=10)
    except Exception as exc:
        pytest.skip(f"Azure backend unreachable: {exc}")
    assert resp.status_code == 200, f"/health/live returned {resp.status_code}"
    body = resp.json()
    assert body.get("status") in {"ok", "alive", "live"}


@pytest.mark.integration
def test_azure_backend_predict_endpoint_reachable():
    try:
        resp = requests.post(
            f"{AZURE_BASE}/predict",
            json=SAMPLE_PAYLOAD,
            timeout=10,
        )
    except Exception as exc:
        pytest.skip(f"Azure backend unreachable: {exc}")
    assert resp.status_code == 200, f"/predict returned {resp.status_code}"
    body = resp.json()
    for field in ("risk_probability", "decision", "model_version"):
        assert field in body, f"/predict response missing {field}"


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

def test_deployment_documents_azure_and_github_pages():
    assert DEPLOY_DOCS.exists(), f"missing {DEPLOY_DOCS}"
    content = DEPLOY_DOCS.read_text(encoding="utf-8")
    assert "Azure Container Apps" in content, "deployment.md must document the canonical Azure backend"
    assert AZURE_BASE in content
    assert "GitHub Pages" in content, "deployment.md must mention GitHub Pages"
    assert "creditflow-api-9z1v.onrender.com" not in content
    assert "Render" not in content
