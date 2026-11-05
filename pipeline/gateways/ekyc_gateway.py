"""Provider-neutral eKYC result contracts and an offline-only sandbox."""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdentityDocumentVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chip_authentic: bool
    identity_match: bool
    masked_document_number: str = Field(min_length=8, max_length=32)

    @field_validator("masked_document_number")
    @classmethod
    def require_masked_document_number(cls, value: str) -> str:
        if not re.search(r"\*{4}", value) or not re.search(r"\d{4}$", value):
            raise ValueError("document number must be masked except for its last four digits")
        return value


class BiometricVerification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    similarity_score: float = Field(ge=0.0, le=1.0)
    liveness_score: float = Field(ge=0.0, le=1.0)
    liveness_passed: bool


class EkycVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_reference: str = Field(min_length=1, max_length=128)
    status: Literal["verified", "review", "rejected"]
    document: IdentityDocumentVerification
    biometric: BiometricVerification
    source_mode: Literal["SANDBOX", "PRODUCTION"]
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EkycSandboxGateway:
    """Return synthetic eKYC results offline; never stand in for a live provider."""

    def verify(
        self,
        *,
        provider_reference: str,
        masked_document_number: str,
    ) -> EkycVerificationResult:
        if os.environ.get("CREDITFLOW_ENV", "").strip().lower() == "production":
            raise RuntimeError("SANDBOX_DISABLED_IN_PRODUCTION")

        return EkycVerificationResult(
            provider_reference=provider_reference,
            status="verified",
            document=IdentityDocumentVerification(
                chip_authentic=True,
                identity_match=True,
                masked_document_number=masked_document_number,
            ),
            biometric=BiometricVerification(
                similarity_score=0.98,
                liveness_score=0.99,
                liveness_passed=True,
            ),
            source_mode="SANDBOX",
        )
