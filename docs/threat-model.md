# Threat Model: CreditFlow

This document presents a STRIDE threat model evaluating the attack surface of the CreditFlow underwriting system and specifying security countermeasures.

---

## 1. System Assets & Threat Landscape

**High-Value Assets:**
- Applicant financial dossiers (tax returns, bank statements, SSN/Identity documents).
- Credit evaluation rules, risk scores, and adverse action determinations.
- Proprietary ML scoring models, feature weights, and MLflow artifacts.
- Underwriting decision ledger and regulatory compliance records.

---

## 2. STRIDE Threat Analysis

### S - Spoofing Identity
* **Threat**: Malicious applicant fabricates credit history or impersonates an underwriter to self-approve a loan.
* **Mitigation**:
  - Cryptographic token authentication on all administrative and underwriter endpoints.
  - Segregation of duties: API tokens authorized to submit applications are cryptographically prohibited from calling `/approve` or `/disburse`.
  - Underwriter identity recorded permanently against the decision record.

### T - Tampering
* **Threat**: Attacker tampers with calculated risk scores or modifies ML weights in storage.
* **Mitigation**:
  - Model artifacts are stored in private Azure Blob Storage containers accessible only by Managed Identity with write-protection.
  - Checksums of model binaries are verified at startup.
  - Database schema enforces immutable append-only records for historical audit events.

### R - Repudiation
* **Threat**: An underwriter denies approving a high-risk delinquent loan, or an applicant denies submitting false documentation.
* **Mitigation**:
  - Complete request payload, client IP, timestamp, and underwriter user identifier recorded in the decision audit ledger.
  - Document hashes stored alongside application records.

### I - Information Disclosure
* **Threat**: PII leaks from uploaded documents or applicant details are exposed via public endpoints.
* **Mitigation**:
  - Azure Storage containers reject public anonymous requests (`allowBlobPublicAccess = false`).
  - Temporary SAS URLs expire within 15 minutes and require authenticated session generation.
  - Sensitive applicant PII is masked in application logs and metrics.

### D - Denial of Service (DoS)
* **Threat**: Adversary floods the scoring engine with heavy document OCR requests or batch scoring calls.
* **Mitigation**:
  - Cloudflare Edge rate limiting and WAF rules filter abusive IP subnets.
  - Azure Container Apps scale horizontally with concurrency limits (30 concurrent requests per replica).
  - Heavy document extraction is decoupled from synchronous HTTP request processing.

### E - Elevation of Privilege
* **Threat**: Applicant manipulates API parameters to force a direct approval without underwriter intervention.
* **Mitigation**:
  - State machine transitions strictly validated: `SUBMITTED` → `SCORED` → `UNDERWRITING_REVIEW` → `APPROVED` / `REJECTED`.
  - Scoring service enforces hard business rule validation before allowing state transitions.
