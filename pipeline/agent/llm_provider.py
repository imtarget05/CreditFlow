"""LLM backends for the explain layer (HTTP + JSON only, no extra dependencies).

Both backends return ``None`` when not configured/unreachable so the caller falls
back to the deterministic template (offline-safe). Credentials come from the
environment only — never hardcode secrets.

Cloudflare Workers AI:
    CREDITFLOW_LLM_PROVIDER=cloudflare
    CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_API_TOKEN
    CLOUDFLARE_MODEL (default @cf/meta/llama-3.2-1b-instruct — cheapest model
    in Neurons/M tokens, so the 10,000 free Neurons/day go furthest)

Groq (OpenAI-compatible chat completions, verified live 2026-09-17):
    CREDITFLOW_LLM_PROVIDER=groq
    GROQ_API_KEY
    GROQ_MODEL (default openai/gpt-oss-20b)

Policy guard (privacy): state classified CONFIDENTIAL (the default) is blocked from all
public-cloud providers; set CREDITFLOW_ALLOW_EXTERNAL_LLM=1 to opt in consciously (used
for the synthetic-data demo).

Whatever a provider returns is still passed through the explanation guard in
``pipeline/agent/explanations.py`` (contract-field whitelist + numeric grounding)
before it can reach graph state, so a bad completion cannot change a decision.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

PROMPT_VERSION = "credit-explain-v1"
DEFAULT_MODEL = "@cf/meta/llama-3.2-1b-instruct"
# Verified-good fallback for harder cases (costs ~10x more Neurons/output):
# "@cf/meta/llama-3.1-8b-instruct". Owner can set CLOUDFLARE_MODEL to it.
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"

# Local Ollama (M1 Pro 16GB, personal, offline-safe): qwen2.5:3b is the default
# local model. Ollama counts as LOCAL, not public cloud, so the Policy Guard in
# explanations.py never blocks it. Cloud (cloudflare/groq/openai) is optional.
OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_TIMEOUT = 30.0


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


def _groq_cfg() -> dict[str, str] | None:
    if os.environ.get("CREDITFLOW_LLM_PROVIDER", "").lower().strip() != "groq":
        return None
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return None
    return {
        "key": key,
        "model": os.environ.get("GROQ_MODEL", DEFAULT_GROQ_MODEL).strip() or DEFAULT_GROQ_MODEL,
    }


def _compact_prompt(ctx: dict[str, Any]) -> str:
    """Compact English-scaffold prompt that asks for the prose in Vietnamese.

    Small instruct models follow a minimal, braces-anchored instruction far more
    reliably than a long template, so the scaffold stays English while the
    summary/recommendation text itself is requested in Vietnamese.
    """
    return (
        "STRICT JSON ONLY. Explain this credit case: "
        + json.dumps(ctx, default=str)[:1500] + "\n"
        "Reply with EXACTLY this JSON shape and nothing else: "
        '{"summary": "<1-2 sentences IN VIETNAMESE>", '
        '"risk_factors": ["<factor1>", "<factor2>"], '
        '"recommendation_note": "<one sentence IN VIETNAMESE>", '
        '"confidence": "<low|medium|high>"}. '
        "Start your reply with { and end with }."
    )


def _balanced_json(text: str) -> dict:
    """Parse the first balanced ``{...}`` block, repairing common model defects.

    Repair 1: unquoted keys — ``{summary: ...}`` -> ``{"summary": ...}`` (small
    models often drop quotes around keys).
    Repair 2: scalar where a list is expected — ``"risk_factors": "x"`` ->
    ``"risk_factors": ["x"]``.
    Raises on malformed input; callers map that to the template fallback.
    """
    if not isinstance(text, str) or "{" not in text:
        raise ValueError("no JSON object in provider response")
    text = re.sub(r'([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)', r'\1"\2"\3', text)
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
    if not isinstance(data, dict):
        raise ValueError("provider response is not a JSON object")
    if isinstance(data.get("risk_factors"), str):
        data["risk_factors"] = [data["risk_factors"]]
    return data


def _normalize_explanation(data: dict, model: str) -> dict[str, Any]:
    """Project a provider payload onto the explanation contract fields."""
    return {
        "summary": str(data.get("summary", ""))[:500],
        "risk_factors": list(data.get("risk_factors", []))[:8],
        "recommendation_note": str(data.get("recommendation_note", ""))[:500],
        "confidence": str(data.get("confidence", "medium")),
        "_llm": True,
        "prompt_version": PROMPT_VERSION,
        "llm_model": model,
    }


def try_cloudflare_explain(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt a structured explanation via Workers AI. None = fall back."""
    cfg = _cfg()
    if cfg is None:
        return None
    prompt = _compact_prompt(ctx)
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
            # Raw text: repair small-model defects, extract first {...}.
            data = _balanced_json(response)
        else:
            # Chat-completion fallback: choices[0].message.content
            choices = r.json().get("result", {}).get("choices") or []
            content = (choices[0].get("message", {}).get("content", "") if choices else "") or ""
            data = _balanced_json(content)
    except Exception:
        return None
    return _normalize_explanation(data, cfg["model"])


def try_groq_explain(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt a structured explanation via Groq. ``None`` = fall back.

    OpenAI-compatible chat-completions endpoint. NOTE (verified live 2026-09-17
    against this key's account): gpt-oss models return HTTP 400
    ``json_validate_failed`` with ``response_format={"type": "json_object"}``,
    so the request uses plain mode and the JSON is recovered from the text by
    ``_balanced_json`` (same repair path as the Cloudflare backend).
    """
    cfg = _groq_cfg()
    if cfg is None:
        return None
    try:
        r = httpx.post(
            GROQ_CHAT_URL,
            headers={"Authorization": f"Bearer {cfg['key']}"},
            json={
                "model": cfg["model"],
                "messages": [{"role": "user", "content": _compact_prompt(ctx)}],
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
        choices = r.json().get("choices") or []
        content = (choices[0].get("message", {}).get("content", "") if choices else "") or ""
        data = _balanced_json(content)
    except Exception:
        return None
    return _normalize_explanation(data, cfg["model"])

def try_ollama_explain(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt a structured explanation via local Ollama. ``None`` = fall back.

    Enabled only when ``CREDITFLOW_LLM_PROVIDER=ollama`` (aliases: local,
    local_ollama); otherwise returns ``None`` immediately. On failure returns
    ``None`` so the caller falls back to template — it does not auto-chain
    to cloud. Local-only, so CONFIDENTIAL data is never sent to a public
    cloud. Default model qwen2.5:3b (override with ``OLLAMA_MODEL``).
    """
    provider = os.environ.get("CREDITFLOW_LLM_PROVIDER", "").lower().strip()
    if provider not in ("ollama", "local", "local_ollama"):
        return None
    model = os.environ.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL).strip() or DEFAULT_OLLAMA_MODEL
    url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_CHAT_URL).strip() or OLLAMA_CHAT_URL
    try:
        r = httpx.post(
            url,
            json={
                "model": model,
                "messages": [{"role": "user", "content": _compact_prompt(ctx)}],
                "format": "json",
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 300},
            },
            timeout=OLLAMA_TIMEOUT,
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        content = (r.json().get("message") or {}).get("content", "") or ""
        data = _balanced_json(content)
    except Exception:
        return None
    return _normalize_explanation(data, model)


def try_local_vllm_explain(ctx: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt a structured explanation via local vLLM. ``None`` = fall back.
    
    This is an example stub demonstrating Private Enterprise LLM integration.
    """
    url = os.environ.get("VLLM_API_URL", "http://localhost:8000/v1/chat/completions")
    model = os.environ.get("VLLM_MODEL", "meta-llama/Llama-3-8B-Instruct")
    
    try:
        r = httpx.post(
            url,
            json={
                "model": model,
                "messages": [{"role": "user", "content": _compact_prompt(ctx)}],
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
        choices = r.json().get("choices") or []
        content = (choices[0].get("message", {}).get("content", "") if choices else "") or ""
        data = _balanced_json(content)
    except Exception:
        return None
        
    return _normalize_explanation(data, model)
