import pytest
from pydantic import ValidationError

from pipeline.gateways.ekyc_gateway import (
    BiometricVerification,
    EkycSandboxGateway,
    EkycVerificationResult,
)


def test_biometric_verification_accepts_boundary_scores():
    result = BiometricVerification(
        similarity_score=1.0,
        liveness_score=0.0,
        liveness_passed=False,
    )

    assert result.similarity_score == 1.0
    assert result.liveness_score == 0.0


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_biometric_verification_rejects_scores_outside_unit_interval(score):
    with pytest.raises(ValidationError):
        BiometricVerification(
            similarity_score=score,
            liveness_score=0.9,
            liveness_passed=True,
        )


def test_ekyc_result_rejects_unmasked_document_number():
    with pytest.raises(ValidationError):
        EkycVerificationResult.model_validate({
            "provider_reference": "provider-ref-1",
            "status": "verified",
            "document": {
                "chip_authentic": True,
                "identity_match": True,
                "masked_document_number": "001088012345",
            },
            "biometric": {
                "similarity_score": 0.98,
                "liveness_score": 0.99,
                "liveness_passed": True,
            },
        })


def test_sandbox_gateway_returns_synthetic_verification():
    result = EkycSandboxGateway().verify(
        provider_reference="sandbox-ref-1",
        masked_document_number="**** **** 2345",
    )

    assert result.status == "verified"
    assert result.document.masked_document_number == "**** **** 2345"
    assert result.biometric.liveness_passed is True
    assert result.source_mode == "SANDBOX"


def test_sandbox_gateway_refuses_to_run_in_production(monkeypatch):
    monkeypatch.setenv("CREDITFLOW_ENV", "production")

    with pytest.raises(RuntimeError, match="SANDBOX_DISABLED_IN_PRODUCTION"):
        EkycSandboxGateway().verify(
            provider_reference="sandbox-ref-1",
            masked_document_number="**** **** 2345",
        )
