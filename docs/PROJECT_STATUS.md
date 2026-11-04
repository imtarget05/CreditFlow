# Portfolio status — 2026-11-04

**Decision: cloud evidence >90% has not been established. No production go-live.**

The frozen [portfolio gate matrix](/Users/mainguyenbinhtan/Downloads/AKS-SRE-Platform/docs/evidence/2026-11-04-portfolio-gate-matrix.md) accepts 11/14 AKS, 0/21 QuanLyKho and 0/22 CreditFlow gates at this checkpoint. The resulting evidence score is 26.19%. This is not a measure of code completeness: local AKS gates have release-SHA evidence, while the three systems still lack complete cloud runtime, image digest/revision and all required-environment evidence.

| Repository | Candidate SHA | Internal CI | External SonarCloud | Cloud acceptance |
| --- | --- | --- | --- | --- |
| AKS-SRE-Platform | `8437b0f` | [PR gate green](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472078), [SBOM green](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472108) | [PR #18 green](https://sonarcloud.io/dashboard?id=imtarget05_AKS-SRE-Platform&pullRequest=18) | Private AKS runtime and state-owner verification open; Pages preview only |
| QuanLyKho | `18bd344` | [CI green](https://github.com/imtarget05/QuanLyKho/actions/runs/41848609393), [S9 kind green](https://github.com/imtarget05/QuanLyKho/actions/runs/41848609354) | [PR #11 green](https://sonarcloud.io/dashboard?id=imtarget05_BankOps&pullRequest=11) | 21 MVP cloud journeys, migration and rollback open |
| CreditFlow | `b5b69ea` | [CI green](https://github.com/imtarget05/CreditFlow/actions/runs/41846011816) | [PR #2 green](https://sonarcloud.io/dashboard?id=imtarget05_CreditFlow&pullRequest=2) | Bank IdP, approvals, UAT and matching ACA/frontend revision open |

A green internal job named “SonarCloud scan” does not prove a scan ran when `SONAR_TOKEN` is empty and the scanner step was skipped. The `v1.0.0-interview` tags exist, but the AKS tag points to `dcea79c`, behind the current candidate. Tags and local demos do not certify a cloud release.

The signed-in GitHub, Cloudflare and Azure accounts establish owner access. They do not establish Risk, InfoSec, IAM or business approval for real credit data. Synthetic fixtures in `data/demo/app_cases.json` support demos only. Keep production use of real applicant data OPEN until bank-owned controls have named approvers and evidence.
