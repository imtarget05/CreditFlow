# Portfolio status — 2026-11-04

**Decision: cloud evidence >90% has not been established. No production go-live.**

The frozen [portfolio gate matrix](/Users/mainguyenbinhtan/Downloads/AKS-SRE-Platform/docs/evidence/2026-11-04-portfolio-gate-matrix.md) accepts 0/14 AKS, 0/21 QuanLyKho and 0/22 CreditFlow gates at this checkpoint. The resulting evidence score is 0%. This is not a measure of code completeness: no single release has matching cloud runtime, image digest/revision and all required-environment evidence.

| Repository | Candidate SHA | Internal CI | External SonarCloud | Cloud acceptance |
| --- | --- | --- | --- | --- |
| AKS-SRE-Platform | `f5cf945` | [PR #18](https://github.com/imtarget05/AKS-SRE-Platform/pull/18), latest run pending | Red: security and duplication | Private AKS runtime and state-owner verification open |
| QuanLyKho | `d5caf99` | [CI green](https://github.com/imtarget05/QuanLyKho/actions/runs/41786122244) | Red: duplication | 21 MVP cloud journeys, migration and rollback open |
| CreditFlow | `bdfc689` | [CI green](https://github.com/imtarget05/CreditFlow/actions/runs/41789071666) | Red: security | Bank IdP, approvals, UAT and matching ACA/frontend revision open |

A green internal job named “SonarCloud scan” does not prove a scan ran when `SONAR_TOKEN` is empty and the scanner step was skipped. The `v1.0.0-interview` tags exist, but the AKS tag points to `dcea79c`, behind the current candidate. Tags and local demos do not certify a cloud release.

The signed-in GitHub, Cloudflare and Azure accounts establish owner access. They do not establish Risk, InfoSec, IAM or business approval for real credit data. Synthetic fixtures in `data/demo/app_cases.json` support demos only. Keep production use of real applicant data OPEN until bank-owned controls have named approvers and evidence.
