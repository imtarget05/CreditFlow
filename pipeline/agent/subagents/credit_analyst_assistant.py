#!/usr/bin/env python3
"""Read-only analyst copilot grounded in a persisted case and policy corpus.

This agent can explain and organize evidence for a human reviewer. It has no
write tools and never returns an approve/reject recommendation.
"""

from __future__ import annotations

import json
from typing import Any

from pipeline.agent.explanations import generate_explanation
from pipeline.agent.retriever import retrieve


def _decode_customer_data(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


class CreditAnalystAssistant:
    """Prepare an evidence-backed review brief without changing case state."""

    def review(
        self, work_item: dict, application: dict,
        data_classification: str = "CONFIDENTIAL",
    ) -> dict:
        customer_data = _decode_customer_data(application.get("customer_data"))
        facts = {
            "application_id": application.get("application_id") or work_item.get("application_id"),
            "work_item_kind": work_item.get("kind"),
            "work_item_status": work_item.get("status"),
            "application_status": application.get("status"),
            "decision": application.get("decision"),
            "risk_level": application.get("risk_level"),
            "risk_probability": application.get("risk_score"),
            "reasons": application.get("reasons") or [],
        }
        current_model = application.get("current_model_evidence") or {}
        reasons = facts["reasons"] or current_model.get("reasons", [])
        query = " ".join(str(reason) for reason in reasons) or (
            f"{work_item.get('kind', '')} {application.get('risk_level', '')} credit review"
        )
        policy_sources = retrieve(query, k=3)
        state = {
            "customer_data": customer_data,
            "data_classification": data_classification,
            "risk_score": application.get("risk_score") or 0.0,
            "risk_level": application.get("risk_level") or "UNKNOWN",
            "decision": application.get("decision") or "REVIEW",
            "model_name": "persisted-creditflow-model",
            "reasons": reasons,
            "fraud_score": 0.0,
            "fraud_flags": [],
            "policy_violations": [],
            "rag_context": "\n".join(item["chunk"] for item in policy_sources),
            "rag_sources": [item["doc_id"] for item in policy_sources],
        }
        generated = generate_explanation(state)
        decision = application.get("decision") or "NOT_RECORDED"
        risk_level = application.get("risk_level") or "UNKNOWN"
        status = application.get("status") or "UNKNOWN"
        fallback_summary = (
            f"Hồ sơ đang ở trạng thái {status}; kết quả đã lưu là {decision}, "
            f"mức rủi ro {risk_level}. Đây là kết quả hiện có để chuyên viên rà soát, "
            "không phải quyết định mới từ trợ lý."
        )
        used_llm = bool(generated.get("_llm"))
        summary = generated.get("summary") if used_llm else fallback_summary
        checklist = [
            "Đối chiếu giấy tờ thu nhập và nghĩa vụ nợ với dữ liệu đã khai.",
            "Kiểm tra các yếu tố rủi ro được liệt kê cùng nguồn chính sách liên quan.",
            "Ghi nhận kết luận và căn cứ bằng quy trình thẩm định của ngân hàng.",
        ]
        return {
            "application_id": facts["application_id"],
            "work_item": {key: work_item.get(key) for key in ("id", "kind", "status", "assigned_role")},
            "case_facts": facts,
            "summary": summary,
            "evidence_factors": generated.get("risk_factors", reasons) if used_llm else reasons,
            "evidence_origin": (
                "persisted_workflow" if facts["reasons"] else
                "recomputed_with_current_model" if current_model else "unavailable"
            ),
            "current_model_evidence": current_model or None,
            "review_checklist": checklist,
            "policy_sources": [
                {"document": item["doc_id"], "excerpt": item["chunk"], "relevance": item["score"]}
                for item in policy_sources
            ],
            "limitations": "Trợ lý chỉ tổng hợp thông tin đã lưu và tài liệu chính sách; chuyên viên phải tự thẩm định. Trợ lý không phê duyệt, từ chối hoặc cập nhật hồ sơ.",
            "assistant_version": "credit-analyst-assistant-v1",
            "generation_mode": "configured_llm" if used_llm else "grounded_template",
        }
