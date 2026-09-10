"""Cloudflare Workers AI backend for the explain layer.

Reads credentials from env only — never hardcode secrets:
  CREDITFLOW_LLM_PROVIDER=cloudflare
  CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN
  CLOUDFLARE_MODEL (default @cf/meta/llama-3.2-1b-instruct — smallest Meta
  chat model, cheapest per free-tier Neuron budget)

Returns None when not configured/unreachable so the caller falls back
to the deterministic template (offline-safe).
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

PROMPT_VERSION = "credit-explain-v1"
DEFAULT_MODEL = "@cf/meta/llama-3.2-1b-instruct"


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
    # Compact English prompt — small instruct models (e.g. llama-3.2-1b) follow
    # JSON shape reliably only with a minimal, braces-anchored instruction.
    # The summary text itself is requested in Vietnamese; only the scaffold
    # language is English to maximise instruction-following at 1B scale.
    prompt = (
        "STRICT JSON ONLY. Explain this credit case: "
        + json.dumps(ctx, default=str)[:1500] + "\n"
        "Reply with EXACTLY this JSON shape and nothing else: "
        '{"summary": "<1-2 sentences IN VIETNAMESE>", '
        '"risk_factors": ["<factor1>", "<factor2>"], '
        '"recommendation_note": "<one sentence IN VIETNAMESE>", '
        '"confidence": "<low|medium|high>"}. '
        "Start your reply with { and end with }."
    )
    url = f"https://api.cloudflare.com/client/v4/accounts/{cfg['account']}/ai/run/{cfg['model']}"
    try:
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {cfg['token']}"},
            json={
                "messages": [
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 300,
                "temperature": 0.1,
            },
            timeout=20.0,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        response = r.json().get("result", {}).get("response")
        if isinstance(response, dict):
            # Structured output: Workers AI already parsed the model JSON.
            data = response
        elif isinstance(response, str) and response:
            # Raw text: repair common small-model defects (unquoted keys,
            # stray fences) then extract the first balanced {...} block.
            import re

            text = response
            # Repair 1: unquoted keys — {summary: ...} -> {"summary": ...}
            # (1B models often drop quotes around keys).
            text = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', text)
            # Repair 2: scalar where a list is expected —
            # "risk_factors": "x" -> "risk_factors": ["x"] (common 1B defect).
            start = text.index("{")
            depth = 1
            end = start
            for i in range(start + 1, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            data = json.loads(text[start : end + 1])
            if isinstance(data.get("risk_factors"), str):
                data["risk_factors"] = [data["risk_factors"]]
        else:
            # Chat-completion fallback: choices[0].message.content
            choices = r.json().get("result", {}).get("choices") or []
            content = (choices[0].get("message", {}).get("content", "") if choices else "") or ""
            if not content:
                return None
            start = content.index("{")
            depth = 1
            end = start
            for i in range(start + 1, len(content)):
                if content[i] == "{":
                    depth += 1
                elif content[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            data = json.loads(content[start : end + 1])
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
