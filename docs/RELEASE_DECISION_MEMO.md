# Release decision memo — three-repo portfolio

**Verified:** 2026-11-05 UTC
**Decision:** NO-GO for production. Portfolio score >90% is not yet supported by evidence.

This memo corrects the earlier unverified claims. A simulated configuration or output is a demo artifact, never evidence that a cloud control passed. A health check cannot establish a business journey, rate limit, backup/restore, or production approval.

## Verified release state

| Repo | Source SHA | Verifiable CI | Remaining release proof |
| --- | --- | --- | --- |
| AKS-SRE-Platform | `185ddbbda188dfa8c6eff581fa83a80f96238939` | [pr-gate](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41976723212), [GitOps](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41976723279), [SBOM](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41976723232) passed | [Pages preview](https://328cb431.aks-sre-platform.pages.dev/) and [Render mirror deployment](https://dashboard.render.com/web/srv-dbm14kpsrm7s73eo9p40/deploys/dep-dbm23gd9fdbs73bk2seg) use this SHA. Live private AKS/ArgoCD/workload, recovery drill, cost/state owner remain OPEN. |
| QuanLyKho | `de82a6d90f48c78dc861c08d815b134e8a1b6c3b` | [main CI](https://github.com/imtarget05/QuanLyKho/actions/runs/41977755897) and [S11](https://github.com/imtarget05/QuanLyKho/actions/runs/41977755708) passed | [Flutter Pages preview](https://c7f29aa4.quan-ly-kho-tra-sua.pages.dev/) embeds this SHA. Modal API `/health/live` returned HTTP 200 without release SHA. Cloud API/UI, 21 MVP journeys, device offline, backup/restore and rollback on this SHA remain OPEN. |
| CreditFlow | `3a5b0c8af520c2585fd755d2defb2c80eaeece37` | [main CI/SonarCloud](https://github.com/imtarget05/CreditFlow/actions/runs/41980744311) passed; [GHCR image build](https://github.com/imtarget05/CreditFlow/actions/runs/41980744305) passed | [Pages preview](https://f9933cc8.creditflow-1cg.pages.dev/) embeds this SHA but labels API revision unverified. Azure Container Apps digest/revision tied to SHA, integration/UAT, and bank Risk/InfoSec/IAM/business approvals remain OPEN. |

The [public preview observation log](evidence/2026-11-05-public-preview-observations.json) records UTC timestamps, HTTP status, response hash and SHA metadata. The Render mirror was rebuilt with cache cleared from AKS SHA `185ddbbd` and is Live; `/health/live` returned HTTP 200, but its response itself has no SHA. CreditFlow's GHCR image digest is `sha256:c281f1064f574a17fb025d06082f5e9bb953cc72a13cd9204c7a7197815b124a` for this main SHA; publishing an image is not deploying it to Azure Container Apps. Preview/health checks do not close cloud business, restore or go-live gates.

## Open gates and owners

| Gate | Owner | Evidence needed |
| --- | --- | --- |
| AKS live validation | Azure/platform owner | Read-only cluster inventory and GitOps/workload state, NetworkPolicy behavior, monitoring, restore/rollback logs, state ownership and cost on the named release. |
| Edge/API rate limiting | Infrastructure owner | Actual configured path and repeatable 429/Retry-After test logs; the pasted Cloudflare, Envoy and Render numbers are simulated. |
| QuanLyKho MVP | Product/UAT owner | Each of the 21 fixed IDs exercised on cloud API/UI, including maker–checker, shifts, FEFO, RBAC, offline on device and rollback. |
| CreditFlow production | Bank Risk, InfoSec, IAM and business owners | Approved IdP, data/model, integrations, UAT, security, backup/restore and explicit sign-off before real credit data. Simulated data can support a demo only. |
| Release traceability | Release owner | Per-repo SHA, CI links, deployment revision/digest, environment, UTC timestamp, raw redacted logs and rollback target. |

The current strict portfolio score is **28.57%**: AKS 12/14, QuanLyKho 0/21, CreditFlow 0/22. A gate remains OPEN if its required environment or same-SHA evidence is absent. CI, Sonar and frontend preview are now verified on the SHAs above, but they do not by themselves close cloud/UAT gates. Go-live decisions remain separate from the numerical score.

Do not mark `PROJECT_STATUS.md` production-ready or claim ArgoCD sync, AKS nodes, Cloudflare WAF/rate limits, Azure storage/backup, or owner approval based on the simulated text.
