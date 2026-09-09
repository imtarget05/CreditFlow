"""Cloudflare Workers AI backend for the explain layer.

Reads credentials from env only — never hardcode secrets:
  CREDITFLOW_LLM_PROVIDER=cloudflare
  CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN
  CLOUDFLARE_MODEL (default @cf/meta/llama-3.1-8b-instruct)

Returns None when not configured/unreachable so the caller falls back
to the deterministic template (offline-safe).
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

PROMPT_VERSION = "credit-explain-v1"
DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct"


class LLMUnavailable(Exception):
    """Raised only for internal signalling; callers map to template fallback."""


def _cfg() -> dict[str, str] | None:
    if os.environ.get("CREDITFLOW_LLM_PROVIDER", "").lower().strip() != "cloudflare":
        return None
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not account or not token:
        return None
    return {
        "account": account,
        "token": token,
        "model": os.environ.get("CLOUDFLARE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
    }


def try_cloudflare_explain(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt a structured explanation via Workers AI. None = fall back."""
    cfg = _cfg()
    if cfg is None:
        return None
    prompt = (
        "Giải thích rủi ro tín dụng (JSON keys: summary, risk_factors, "
        "recommendation_note, confidence). Dữ liệu: " + json.dumps(ctx, default=str)[:3000]
    )
    url = f"https://api.cloudflare.com/client/v4/accounts/{cfg['account']}/ai/run/{cfg['model']}"
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {cfg['token']}"},
            json={"messages": [{"role": "user", "content": prompt}]},
            timeout=20.0,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        text = r.json()["result"]["response"]
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except Exception:
        return None
    return {
        "summary": str(data.get("summary", ""))[:500],
        "risk_factors": list(data.get("risk_factors", []))[:8],
        "recommendation_note": str(data.get("recommendation_note", ""))[:500],
        "confidence": str(data.get("confidence", "medium")),
        "_llm": True,
        "prompt_version": PROMPT_VERSION,
        "llm_model": cfg["model"],
    }
