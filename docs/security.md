# Security Architecture & Compliance: CreditFlow

This document defines the security architecture, regulatory compliance controls, and data protection measures for CreditFlow's automated credit decisioning pipeline.

---

## 1. Regulatory Context & Financial Security Boundaries

Credit underwriting platforms process high-risk Personally Identifiable Information (PII) and financial records. CreditFlow enforces strict architectural boundaries to comply with fair lending, data privacy, and audit standards:

```
[Loan Applicant / Broker]
        │
        ▼ (HTTPS / TLS 1.3)
[Cloudflare Edge] (WAF, DDoS, Geo-Fencing, Rate Limiting)
        │
        ▼
[CreditFlow API] (FastAPI / ACA)
   • Strict payload validation (Pydantic models)
   • Mutual authentication / Bearer Token validation
   • Segregation of duties: Submission vs Scoring vs Approval
        │
        ├──────────────────────┬──────────────────────┐
        ▼                      ▼                      ▼
[Azure Blob Storage]   [ML Decision Engine]    [Audit & Policy Store]
• Private containers   • Deterministic rules   • Immutable ledger
• Encryption at rest   • Model drift checks    • Versioned weights
• SAS temporary URLs   • HITL approval gate    • Full feature snapshot
```

---

## 2. Sensitive Document & Storage Security

### Azure Blob Storage Protection
- **Zero Public Access**: Containers `credit-documents` and `ml-models` enforce `publicAccess = 'None'` and reject anonymous blob access.
- **Encryption at Rest**: Server-Side Encryption (SSE) enforced with AES-256 and minimum TLS version 1.2 for all data in transit.
- **Least Privilege Access**: Compute instances authenticate to blob storage using Azure User-Assigned Managed Identity (`UserAssignedIdentity`) granted the `Storage Blob Data Contributor` role. No storage account keys or connection strings are stored in code or environment variables.
- **Short-Lived Ephemeral Links**: Document retrieval for underwriter review utilizes time-bound Shared Access Signatures (SAS) with a maximum validity window of 15 minutes.

---

## 3. ML Governance, Fairness & Integrity

### Decision Lineage & Reproducibility
- Every credit decision records an immutable snapshot consisting of:
  1. Full raw applicant input data and extracted document features.
  2. The exact model ID and checkpoint version executed (tracked via MLflow / Model Registry).
  3. Calculated credit score, probability of default, and policy decision tree path.
  4. Decision rationale and adverse action codes (for Fair Credit Reporting Act compliance).

### Adversarial Inputs & Data Poisoning Prevention
- **Schema Validation & Range Checks**: Applicant attributes undergo strict boundary verification prior to feature transformation.
- **Drift Monitoring**: Offline and online population stability index (PSI) checks detect feature distribution drift, preventing degradation or manipulation of the scoring pipeline.
- **Mandatory Human-in-the-Loop (HITL)**:
  - Applications falling in the borderline score band [620, 680] or involving high-exposure loan amounts automatically trigger an approval lock.
  - The system prohibits programmatic bypass of human underwriter confirmation for flagged cases.

---

## 4. Operational & Network Security

### Dual Deployment Profile
- `PROFILE=portfolio`: Configured for recruiter demonstrations on Render Free with synthesized, anonymized mock loan applications. Real applicant PII is prohibited.
- `PROFILE=production`: Targeted for Azure Container Apps with Private Endpoints, Log Analytics workspace audit streaming, and Key Vault secret integration.
