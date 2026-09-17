"""LangChain explanation layer for the LangGraph decision workflow.

User argument #2 — architecture discipline:
  - **LangChain** is used ONLY at the LLM/tool layer to produce a *structured
    natural-language explanation* of the risk decision.
  - **The LLM never makes the decision.**  It explains.  Policy + cost-tuned
    ML threshold + (human, when required) decide.
  - **LlamaIndex is NOT in the core path.**

This module exposes:
  - ``ExplanationOutput`` — structured schema (TypedDict) the LLM must fill.
  - ``generate_explanation`` — tries an LLM via LangChain; if no provider /
    API key is configured, falls back to a deterministic template so the
    workflow runs fully offline (spec §19.1: no training/LLM unless owner
    enables it).
"""
from __future__ import annotations

import math
import os
import re
from typing import Any

from typing_extensions import TypedDict

from pipeline.agent.state import CreditState
from pipeline.agent.llm_provider import PROMPT_VERSION


class ExplanationOutput(TypedDict, total=False):
    """Structured explanation contract returned to the caller."""
    summary: str          # one-sentence TL;DR
    risk_factors: list[str]  # ordered list of contributing factors
    recommendation_note: str  # what humans should look at
    confidence: str       # "high" / "medium" / "low"


# Risk-level thresholds mirrored from pipeline.modeling.threshold
_RISK_LOW_MAX = 0.50
_RISK_HIGH_MIN = 0.80


def _fmt_money(v: float) -> str:
    """Format a VND amount with thousands separator."""
    if v is None:
        return "N/A"
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _build_context(state: CreditState) -> dict[str, Any]:
    """Extract the fields an explanation prompt needs from *state*."""
    data = state.get("customer_data", {})
    data_classification = state.get("data_classification", "CONFIDENTIAL")

    fe = state.get("derived_features", {})
    return {
        "risk_score": state.get("risk_score", 0.0),
        "risk_level": state.get("risk_level", "UNKNOWN"),
        "decision": state.get("decision", "UNKNOWN"),
        "model_name": state.get("model_name", "unknown"),
        "reasons": state.get("reasons", []),
        "fraud_score": state.get("fraud_score", 0.0),
        "fraud_flags": state.get("fraud_flags", []),
        "policy_violations": state.get("policy_violations", []),
        "rag_context": state.get("rag_context", ""),
        "rag_sources": state.get("rag_sources", []),
        "income": data.get("income", "N/A"),
        "age": data.get("age", "N/A"),
        "employment_years": data.get("employment_years", "N/A"),
        "loan_amount": data.get("loan_amount", "N/A"),
        "loan_term": data.get("loan_term", "N/A"),
        "existing_debt": data.get("existing_debt", "N/A"),
        "data_classification": data_classification,
        "credit_history": data.get("credit_history", "N/A"),
        "previous_defaults": data.get("previous_defaults", "N/A"),
        "debt_to_income": fe.get("debt_to_income", "N/A"),
        "loan_to_income": fe.get("loan_to_income", "N/A"),
    }


_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
_CONFIDENCE_LEVELS = ("low", "medium", "high")
# Fields an LLM is allowed to fill. Anything else it returns (for example a
# "decision" or a new "risk_score") is dropped, so prose can never move the
# decision that policy + the cost-tuned threshold already made.
_EXPLANATION_FIELDS = ("summary", "risk_factors", "recommendation_note", "confidence")


def _number_interpretations(token: str) -> set[float]:
    """Plausible numeric readings of one token ("8.000.000", "1,875", "0.9").

    Separator conventions are ambiguous between thousands-grouping (vi-VN) and
    decimal notation (en-US), so both readings are offered. Being permissive here
    only widens what counts as grounded; it never invents a value.
    """
    values: set[float] = set()
    try:
        values.add(float(re.sub(r"[.,]", "", token)))
    except ValueError:
        pass
    head, tail = max(
        ((token.rfind("."), token[token.rfind(".") + 1 :]), (token.rfind(","), token[token.rfind(",") + 1 :])),
        key=lambda pair: pair[0],
    )
    if head > 0 and 0 < len(tail) <= 3:
        try:
            values.add(float(re.sub(r"[.,]", "", token[:head]) + "." + tail))
        except ValueError:
            pass
    return values


def _numbers_in(value: Any) -> set[float]:
    """Every number reachable from *value*, including digits inside strings."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, dict):
        found: set[float] = set()
        for item in value.values():
            found |= _numbers_in(item)
        return found
    if isinstance(value, (list, tuple, set)):
        found = set()
        for item in value:
            found |= _numbers_in(item)
        return found
    if isinstance(value, (int, float)):
        return {float(value)}
    if isinstance(value, str):
        found = set()
        for token in _NUMBER_RE.findall(value):
            found |= _number_interpretations(token)
        return found
    return set()


def _is_grounded(value: float, allowed: set[float]) -> bool:
    for candidate in allowed:
        if math.isclose(value, candidate, rel_tol=0.0, abs_tol=1e-9):
            return True
        for places in range(0, 5):
            if math.isclose(round(candidate, places), value, rel_tol=0.0, abs_tol=1e-9):
                return True
    return False


def _ungrounded_numbers(texts: list[str], allowed: set[float]) -> list[str]:
    """Number tokens in *texts* that cannot be traced back to the case data.

    Scope: detects invented figures (the concrete form of "fabricated profile
    information") and blocks them. It does **not** verify that a sentence is
    semantically true, and an ambiguous token is accepted if any reading is
    grounded; this is a numeric grounding gate, not a faithfulness proof.
    """
    ungrounded: list[str] = []
    for text in texts:
        for token in _NUMBER_RE.findall(text):
            readings = _number_interpretations(token)
            if not readings or not any(_is_grounded(v, allowed) for v in readings):
                ungrounded.append(token)
    return ungrounded


def _llm_fields_are_well_typed(fields: dict) -> bool:
    summary = fields.get("summary")
    note = fields.get("recommendation_note")
    factors = fields.get("risk_factors")
    if not isinstance(summary, str) or not summary.strip():
        return False
    if not isinstance(note, str) or not note.strip():
        return False
    if fields.get("confidence") not in _CONFIDENCE_LEVELS:
        return False
    return isinstance(factors, list) and bool(factors) and all(
        isinstance(item, str) and item.strip() for item in factors
    )


_PROMPT_TEMPLATE = """\
Bạn là chuyên gia tài chính AI giải thích quyết định tín dụng. Nhiệm vụ của bạn
là **giải thích** tại sao hồ sơ vay này có điểm rủi ro như vậy — **KHÔNG phải
đưa ra quyết định**. Dưới đây là các tín hiệu đã tính toán:

Rủi ro (P(default)): {risk_score}
Mức độ rủi ro: {risk_level}
Quyết định (từ cost-tuned threshold): {decision}
Model: {model_name}
Lý do rủi ro (rule-based): {reasons}
Điểm gian lận: {fraud_score} (cờ: {fraud_flags})
Vi phạm chính sách: {policy_violations}
Bối cảnh chính sách (RAG, có thể rỗng): {rag_context}
Thu nhập: {income} VND/tháng
Tuổi: {age}
Năm làm việc: {employment_years}
Số tiền vay: {loan_amount} VND (kỳ hạn {loan_term} tháng)
Nợ hiện có: {existing_debt} VND
Lịch sử tín dụng: {credit_history} năm
Số lần vỡ nợ trước: {previous_defaults}
Tỷ lệ nợ/thu nhập: {debt_to_income}
Tỷ lệ vay/thu nhập: {loan_to_income}

Hãy cung cấp:
1. summary: một câu tóm tắt ngắn gọn (1-2 câu) vì sao điểm rủi ro như vậy.
2. risk_factors: danh sách các yếu tố góp phần (theo thứ tự quan trọng nhất).
3. recommendation_note: gợi ý cho người review tiếp theo nên kiểm tra gì.
4. confidence: "high" nếu các tín hiệu rõ rệt, "medium" nếu có một số bất định, "low" nếu thiếu dữ liệu.

Trả lời bằng JSON chính xác với các khóa: summary, risk_factors, recommendation_note, confidence.
"""


def _try_llm_explanation(ctx: dict[str, Any]) -> ExplanationOutput | None:
    """Attempt an LLM explanation via LangChain.

    Returns ``None`` if no provider/API key is configured, so the caller can
    fall back to the deterministic template.
    """
    provider = os.environ.get("CREDITFLOW_LLM_PROVIDER", "").lower().strip()
    if not provider:
        return None

    classification = ctx.get("data_classification", "CONFIDENTIAL")
    is_public_cloud = provider in ["cloudflare", "groq", "openai", "anthropic", "google"]
    
    if classification == "CONFIDENTIAL" and is_public_cloud:
        print(f"[Policy Guard] Blocked sending CONFIDENTIAL data to public cloud provider: {provider}")
        return None

    if provider == "local_vllm":
        from pipeline.agent.llm_provider import try_local_vllm_explain
        return try_local_vllm_explain(ctx)

    if provider == "cloudflare":
        from pipeline.agent.llm_provider import try_cloudflare_explain
        return try_cloudflare_explain(ctx)

    if provider == "groq":
        from pipeline.agent.llm_provider import try_groq_explain
        return try_groq_explain(ctx)

    try:
        from langchain_core.prompts import ChatPromptTemplate
    except ImportError:
        return None

    prompt = ChatPromptTemplate.from_template(_PROMPT_TEMPLATE)

    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            temperature=0.3,
            max_tokens=500,
            api_key=api_key,
        )
    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
            temperature=0.3,
            max_tokens=500,
            api_key=api_key,
        )
    elif provider == "google":
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return None
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash"),
            temperature=0.3,
            max_tokens=500,
            api_key=api_key,
        )
    else:
        return None

    structured = llm.with_structured_output(ExplanationOutput)
    chain = prompt | structured
    result = chain.invoke(ctx)
    result["_llm"] = True
    result["prompt_version"] = PROMPT_VERSION
    model_name = "unknown"
    if provider == "openai":
        model_name = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    elif provider == "anthropic":
        model_name = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
    elif provider == "google":
        model_name = os.environ.get("GOOGLE_MODEL", "gemini-2.0-flash")
    result["llm_model"] = model_name
    return result


def _template_explanation(ctx: dict[str, Any]) -> ExplanationOutput:
    """Deterministic fallback explanation — no LLM, no API key needed."""
    risk_score = ctx.get("risk_score", 0.0)
    risk_level = ctx.get("risk_level", "UNKNOWN")
    reasons = ctx.get("reasons", []) or ["low overall risk profile"]
    fraud_flags = ctx.get("fraud_flags", [])
    policy_violations = ctx.get("policy_violations", [])
    model_name = ctx.get("model_name", "unknown")

    parts: list[str] = []
    income = _fmt_money(ctx.get("income", 0))
    loan_amount = _fmt_money(ctx.get("loan_amount", 0))
    dti = ctx.get("debt_to_income", "N/A")
    lti = ctx.get("loan_to_income", "N/A")

    if risk_level == "HIGH":
        summary = (
            f"Hồ sơ được đánh dấu rủi ro cao (P={risk_score:.4f}) bởi model {model_name}. "
            f"Thu nhập {income} VND, vay {loan_amount} VND, DTI={dti}, LTI={lti}."
        )
        parts = list(reasons)
    elif risk_level == "MEDIUM":
        summary = (
            f"Hồ sơ nằm trong vùng thủ đột (P={risk_score:.4f}). "
            f"Yêu cầu review thêm — thu nhập {income} VND, vay {loan_amount} VND."
        )
        parts = list(reasons)
    else:
        summary = (
            f"Hồ sơ có rủi ro thấp (P={risk_score:.4f}). "
            f"Thu nhập {income} VND, vay {loan_amount} VND, hồ sơ sạch."
        )
        parts = ["low overall risk profile"] if not reasons else list(reasons)

    if fraud_flags:
        parts.append("cảnh dương gian lập dụng: " + ", ".join(fraud_flags))
    if policy_violations:
        parts.append("vi phạm chính sách: " + ", ".join(policy_violations))

    confidence = "high" if risk_level in ("LOW", "HIGH") else "medium"
    if not reasons and risk_level == "UNKNOWN":
        confidence = "low"

    rec_parts: list[str] = []
    if risk_level == "HIGH":
        rec_parts.append("Xem xét từ chối nếu các cờ rủi ro/gian lận không được giải thích")
    if risk_level == "MEDIUM":
        rec_parts.append("Yêu cầu chứng minh khả năng hoàn trả hoặc báo cáo thu nhập bổ sung")
    if fraud_flags:
        rec_parts.append("Đối chiếu thông tin cá nhân và lịch sử ứng dụng để kiểm tra gian lập")
    rec_parts.append("Xác nhận tính chính xác của thu nhập và nợ hiện có với hồ sơ ngân hàng")

    return ExplanationOutput(
        summary=summary,
        risk_factors=parts,
        recommendation_note="; ".join(rec_parts) if rec_parts else "Hồ sơ đủ thông tin",
        confidence=confidence,
        prompt_version=PROMPT_VERSION,
    )


def generate_explanation(state: CreditState) -> ExplanationOutput:
    """Produce a structured explanation for the current *state*.

    Tries LangChain + configured LLM provider first; falls back to a
    deterministic template (no API key required) so the workflow runs offline.
    """
    ctx = _build_context(state)
    verified = _template_explanation(ctx)

    try:
        llm_result = _try_llm_explanation(ctx)
    except Exception:
        verified["fallback_reason"] = "provider_error"
        return verified
    if llm_result is None:
        verified["fallback_reason"] = "provider_unavailable"
        return verified
    if not isinstance(llm_result, dict):
        verified["fallback_reason"] = "malformed_output"
        return verified

    # (1) Drop every key the contract does not define. A "decision" or
    # "risk_score" produced by the model is never copied into the graph state.
    fields = {key: llm_result.get(key) for key in _EXPLANATION_FIELDS}
    if not _llm_fields_are_well_typed(fields):
        verified["fallback_reason"] = "malformed_output"
        return verified

    # (2) Every number in the prose must be traceable to the case context or to
    # the deterministic narrative built from that same context.
    allowed = _numbers_in(ctx) | _numbers_in([verified[field] for field in ("summary", "risk_factors", "recommendation_note")])
    texts = [fields["summary"], fields["recommendation_note"], *fields["risk_factors"]]
    ungrounded = _ungrounded_numbers(texts, allowed)
    if ungrounded:
        verified["fallback_reason"] = "ungrounded_numbers"
        verified["ungrounded_numbers"] = sorted(set(ungrounded))[:8]
        return verified

    result = dict(fields)
    for key in ("_llm", "prompt_version", "llm_model"):
        if key in llm_result:
            result[key] = llm_result[key]
    return result


def explanation_to_text(expl: ExplanationOutput) -> str:
    """Render an ``ExplanationOutput`` into a single human-readable string."""
    lines = []
    if expl.get("summary"):
        lines.append(f"Tóm tắt: {expl['summary']}")
    if expl.get("risk_factors"):
        lines.append("Yếu tố rủi ro:")
        for i, rf in enumerate(expl["risk_factors"], 1):
            lines.append(f"  {i}. {rf}")
    if expl.get("recommendation_note"):
        lines.append(f"Đề xuất: {expl['recommendation_note']}")
    if expl.get("confidence"):
        lines.append(f"Độ tin cậy: {expl['confidence']}")
    return "\n".join(lines)
