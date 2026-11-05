# CI evidence checkpoint — 2026-11-04

| Repo | Verified internal run | SHA | Result | External SonarCloud |
| --- | --- | --- | --- | --- |
| AKS-SRE-Platform | [pr-gate](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472078), [SBOM](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472108), [GitOps](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472157) | `8437b0f` | Green | [PR #18](https://sonarcloud.io/dashboard?id=imtarget05_AKS-SRE-Platform&pullRequest=18) green |
| QuanLyKho | [CI](https://github.com/imtarget05/QuanLyKho/actions/runs/41848609393), [S9 kind](https://github.com/imtarget05/QuanLyKho/actions/runs/41848609354) | `18bd344` | Green | [PR #11](https://sonarcloud.io/dashboard?id=imtarget05_BankOps&pullRequest=11) green |
| CreditFlow | [CI](https://github.com/imtarget05/CreditFlow/actions/runs/41846011816), [LLM gateway](https://github.com/imtarget05/CreditFlow/actions/runs/41846011762) | `b5b69ea` | Green | [PR #2](https://sonarcloud.io/dashboard?id=imtarget05_CreditFlow&pullRequest=2) green |

These runs show local/CI verification, not deploy, cloud E2E, backup/restore, rollback or bank approval. Recheck SHA before release acceptance. A green scanner job with empty `SONAR_TOKEN` and skipped scanner is not completed scan evidence.
