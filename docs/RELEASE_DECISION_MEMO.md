# Release decision memo — three-repo portfolio

**Verified:** 2026-11-05 UTC
**Decision:** NO-GO for production. Portfolio score >90% is not yet supported by evidence.

This memo corrects the earlier unverified claims. A simulated configuration or output is a demo artifact, never evidence that a cloud control passed. A health check cannot establish a business journey, rate limit, backup/restore, or production approval.

## Verified release state

| Repo | Source SHA | Verifiable CI | Remaining release proof |
| --- | --- | --- | --- |
| AKS-SRE-Platform | `185ddbbda188dfa8c6eff581fa83a80f96238939` | [pr-gate](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41976723212), [GitOps](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41976723279), [SBOM](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41976723232) passed | Live private AKS/ArgoCD/workload, recovery drill, cost/state owner, and same-SHA image/revision. Cloudflare preview and Render mirror are older releases. |
| QuanLyKho | `de82a6d90f48c78dc861c08d815b134e8a1b6c3b` | [main CI](https://github.com/imtarget05/QuanLyKho/actions/runs/41977755897) and [S11](https://github.com/imtarget05/QuanLyKho/actions/runs/41977755708) were in progress at this verification | Cloud API/UI, 21 MVP journeys, device offline, backup/restore and rollback on this SHA. |
| CreditFlow | PR #2 was `a8711401f7379fd09311fd3447288eb312279746` at this verification | [PR #2](https://github.com/imtarget05/CreditFlow/pull/2) CI in progress; SonarCloud previously failed | New release CI/Sonar, Azure Container Apps image digest/revision tied to SHA, integration/UAT, and bank Risk/InfoSec/IAM/business approvals. Existing ACA uses an untraceable `:latest` tag. |

The Cloudflare Pages previews and Render services may be shown as historical demos with their own source identity. They cannot close a gate for a later SHA. CreditFlow's Azure read-only checks cannot establish deployment success.

## Open gates and owners

| Gate | Owner | Evidence needed |
| --- | --- | --- |
| AKS live validation | Azure/platform owner | Read-only cluster inventory and GitOps/workload state, NetworkPolicy behavior, monitoring, restore/rollback logs, state ownership and cost on the named release. |
| Edge/API rate limiting | Infrastructure owner | Actual configured path and repeatable 429/Retry-After test logs; the pasted Cloudflare, Envoy and Render numbers are simulated. |
| QuanLyKho MVP | Product/UAT owner | Each of the 21 fixed IDs exercised on cloud API/UI, including maker–checker, shifts, FEFO, RBAC, offline on device and rollback. |
| CreditFlow production | Bank Risk, InfoSec, IAM and business owners | Approved IdP, data/model, integrations, UAT, security, backup/restore and explicit sign-off before real credit data. Simulated data can support a demo only. |
| Release traceability | Release owner | Per-repo SHA, CI links, deployment revision/digest, environment, UTC timestamp, raw redacted logs and rollback target. |

The current strict portfolio score is **28.57%**: AKS 12/14, QuanLyKho 0/21, CreditFlow 0/22. A gate remains OPEN if its required environment or same-SHA evidence is absent. Recompute after the running CI completes; CI alone does not close cloud/UAT gates. Go-live decisions remain separate from the numerical score.

Do not mark `PROJECT_STATUS.md` production-ready or claim ArgoCD sync, AKS nodes, Cloudflare WAF/rate limits, Azure storage/backup, or owner approval based on the simulated text.
