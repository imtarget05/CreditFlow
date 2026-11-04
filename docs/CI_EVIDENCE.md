# CI evidence checkpoint — 2026-11-04

| Repo | Verified internal run | SHA | Result | External SonarCloud |
| --- | --- | --- | --- | --- |
| AKS-SRE-Platform | [pr-gate](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41787134648), [SBOM](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41787134773), [GitOps](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41787134834) | `a6d1470` | Internal jobs green; newer SHA pending | [PR #18](https://sonarcloud.io/dashboard?id=imtarget05_AKS-SRE-Platform&pullRequest=18) red |
| QuanLyKho | [CI](https://github.com/imtarget05/QuanLyKho/actions/runs/41786122244), [S9 kind](https://github.com/imtarget05/QuanLyKho/actions/runs/41786122328) | `d5caf99` | Green | [PR #11](https://sonarcloud.io/dashboard?id=imtarget05_BankOps&pullRequest=11) red |
| CreditFlow | [CI](https://github.com/imtarget05/CreditFlow/actions/runs/41789071666), [LLM gateway](https://github.com/imtarget05/CreditFlow/actions/runs/41789071665) | `bdfc689` | Green | [PR #2](https://sonarcloud.io/dashboard?id=imtarget05_CreditFlow&pullRequest=2) red |

These runs show local/CI verification, not deploy, cloud E2E, backup/restore, rollback or bank approval. Recheck SHA before release acceptance. A green scanner job with empty `SONAR_TOKEN` and skipped scanner is not completed scan evidence.
