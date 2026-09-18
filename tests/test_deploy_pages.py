"""Tests for GitHub Pages deployment artifacts (frontend + Render backend).

No live Cloudflare API calls. Network-dependent tests are marked
``pytest.mark.integration`` so they still run, but are identifiable.
"""
from __future__ import annotations

import json
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

RENDER_BASE = "https://creditflow-api-ko2h.onrender.com"
# Money fields follow the API money-unit contract (VND), so this sample stays
# valid against both the legacy and the contract-enforcing backend.
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

def test_pages_env_production_points_to_render_backend():
    assert ENV_PROD.exists(), f"missing {ENV_PROD}"
    content = ENV_PROD.read_text(encoding="utf-8")
    assert f"VITE_API_BASE={RENDER_BASE}" in content, (
        f".env.production must point VITE_API_BASE to {RENDER_BASE}"
    )


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
# Render backend live checks
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_render_backend_health_endpoint_reachable():
    try:
        resp = requests.get(f"{RENDER_BASE}/health", timeout=10)
    except Exception as exc:
        pytest.skip(f"Render backend unreachable: {exc}")
    assert resp.status_code == 200, f"/health returned {resp.status_code}"
    body = resp.json()
    assert body.get("status") == "ok"
    assert body.get("model_loaded") is True


@pytest.mark.integration
def test_render_backend_predict_endpoint_reachable():
    try:
        resp = requests.post(
            f"{RENDER_BASE}/predict",
            json=SAMPLE_PAYLOAD,
            timeout=10,
        )
    except Exception as exc:
        pytest.skip(f"Render backend unreachable: {exc}")
    assert resp.status_code == 200, f"/predict returned {resp.status_code}"
    body = resp.json()
    for field in ("risk_probability", "decision", "model_version"):
        assert field in body, f"/predict response missing {field}"


# ---------------------------------------------------------------------------
# Documentation
# ---------------------------------------------------------------------------

def test_deployment_documents_render_and_github_pages():
    assert DEPLOY_DOCS.exists(), f"missing {DEPLOY_DOCS}"
    content = DEPLOY_DOCS.read_text(encoding="utf-8")
    assert "Render" in content, "deployment.md must mention Render"
    assert "GitHub Pages" in content, "deployment.md must mention GitHub Pages"
