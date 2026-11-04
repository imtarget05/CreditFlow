"""Policy-configured document metadata checks for human review queues."""
from __future__ import annotations

import os

from pipeline.storage.ledger import DOCUMENT_TYPES


def required_document_types() -> list[str]:
    raw = os.environ.get("CREDITFLOW_REQUIRED_DOCUMENT_TYPES", "").strip()
    if not raw:
        return []
    required = sorted({item.strip().lower() for item in raw.split(",") if item.strip()})
    unknown = set(required) - DOCUMENT_TYPES
    if unknown:
        raise ValueError(f"Unknown required document type: {', '.join(sorted(unknown))}")
    return required


def evaluate_document_checklist(documents: list[dict], required: list[str]) -> dict:
    """Check presence only; authenticity and content remain human controls."""
    if not required:
        return {"status": "UNCONFIGURED", "required": [], "missing": []}
    present = {doc.get("document_type") for doc in documents}
    missing = sorted(set(required) - present)
    return {
        "status": "MISSING" if missing else "REVIEW_REQUIRED",
        "required": sorted(set(required)),
        "missing": missing,
        "note": "Metadata presence does not verify document content or authenticity.",
    }
