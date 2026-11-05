# Portfolio status — 2026-11-04

**Decision: cloud evidence >90% has not been established. No production go-live.**

The frozen [portfolio gate matrix](/Users/mainguyenbinhtan/Downloads/AKS-SRE-Platform/docs/evidence/2026-11-04-portfolio-gate-matrix.md) accepts 12/14 AKS, 0/21 QuanLyKho and 0/22 CreditFlow gates at this checkpoint. The resulting evidence score is 28.57%. This is not a measure of code completeness: local AKS gates have release-SHA evidence, while the three systems still lack complete cloud runtime, image digest/revision and all required-environment evidence.

| Repository | Candidate SHA | Internal CI | External SonarCloud | Cloud acceptance |
| --- | --- | --- | --- | --- |
| AKS-SRE-Platform | `8437b0f` | [PR gate green](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472078), [SBOM green](https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41849472108) | [PR #18 green](https://sonarcloud.io/dashboard?id=imtarget05_AKS-SRE-Platform&pullRequest=18) | Private AKS runtime and state-owner verification open; Pages preview only |
| QuanLyKho | `18bd344` | [CI green](https://github.com/imtarget05/QuanLyKho/actions/runs/41848609393), [S9 kind green](https://github.com/imtarget05/QuanLyKho/actions/runs/41848609354) | [PR #11 green](https://sonarcloud.io/dashboard?id=imtarget05_BankOps&pullRequest=11) | 21 MVP cloud journeys, migration and rollback open |
| CreditFlow | `b5b69ea` | [CI green](https://github.com/imtarget05/CreditFlow/actions/runs/41846011816) | [PR #2 green](https://sonarcloud.io/dashboard?id=imtarget05_CreditFlow&pullRequest=2) | Bank IdP, approvals, UAT and matching ACA/frontend revision open |

A green internal job named “SonarCloud scan” does not prove a scan ran when `SONAR_TOKEN` is empty and the scanner step was skipped. The `v1.0.0-interview` tags exist, but the AKS tag points to `dcea79c`, behind the current candidate. Tags and local demos do not certify a cloud release.

The later [CI run on `c3d51db`](https://github.com/imtarget05/CreditFlow/actions/runs/41853409578) correctly failed the new Sonar token validation. The credential exposed in chat must be replaced through SonarCloud and GitHub Secrets before this branch can have a fully green internal scanner job. The earlier `b5b69ea` run remains the verified green candidate for code checks; no cloud CreditFlow control is accepted yet.

The signed-in GitHub, Cloudflare and Azure accounts establish owner access. They do not establish Risk, InfoSec, IAM or business approval for real credit data. Synthetic fixtures in `data/demo/app_cases.json` support demos only. Keep production use of real applicant data OPEN until bank-owned controls have named approvers and evidence.

---

## Appendix: Cloud deployment completion plan (`.kilo/plans/1793770000000-cloud-deploy-completion.md`)

Status: **sẵn sàng thực hiện, chờ chủ tài khoản tự thực hiện các bước cần credential.**

| Bước | Công việc | Nơi thực hiện | Trạng thái | Ghi chú |
|------|-----------|---------------|------------|---------|
| T1 | Push branch + tag `v1.0.0-interview` lên 3 repo | Máy local (`gh CLI`) | ✅ Done | Đã push thành công |
| T2 | Thêm GitHub Secrets (SONAR_TOKEN, CLOUDFLARE_API_TOKEN, RENDER_API_KEY, SONAR_ORGANIZATION) | GitHub Repo Settings → Secrets | ⏳ Chờ user | SONAR_TOKEN đã set; còn CLOUDFLARE_API_TOKEN, RENDER_API_KEY, SONAR_ORGANIZATION |
| T3 | Chạy lại CI/Sonar workflows, verify xanh | GitHub Actions | ✅ Done | CreditFlow CI ✅, QuanLyKho CI ✅, AKS pr-gate ✅ (tất cả job xanh incl. SonarCloud). Root cause: `secrets` context không available ở job-level `if` → xóa dòng đó. |
| T4 | Deploy Cloudflare Pages (`apps/sre-console`) | Máy local (`wrangler`) | ⏳ Chờ user | Cần `CLOUDFLARE_API_TOKEN` |
| T5 | Deploy Render (`app/sre-demo-api`) | Render Dashboard | ⏳ Chờ user | Connect GitHub repo, đọc `render.yaml` |
| T6 | Cập nhật file này sau khi T3–T5 xong | `CreditFlow/docs/PROJECT_STATUS.md` | 🔄 Đang cập nhật | T3 đã xong; T4, T5 còn chờ user |

### Out-of-scope (không làm trong plan này)
- **Azure AKS terraform apply:** bị chặn bởi `constraint.no_azure_production_mutations`. Cần infra team mở quyền tenant/cluster/Private Link.
- **Production go-live claim:** bị chặn bởi 5 P0 gates mở (`no_go_live_while_p0_gates_open`).

### Lệnh T1 (chạy trên máy bạn)

```bash
cd /Users/mainguyenbinhtan/Downloads/CreditFlow
git push origin codex/portfolio-cloud-evidence-20261104
git push origin v1.0.0-interview

cd /Users/mainguyenbinhtan/Downloads/QuanLyKho
git push origin codex/portfolio-cloud-evidence-20261104
git push origin v1.0.0-interview

cd /Users/mainguyenbinhtan/Downloads/AKS-SRE-Platform
git push origin codex/portfolio-cloud-evidence-20261104
git push origin v1.0.0-interview
```

### Lệnh T4 (chạy trên máy bạn, sau khi đã có token)

```bash
cd /Users/mainguyenbinhtan/Downloads/AKS-SRE-Platform/apps/sre-console
export CLOUDFLARE_API_TOKEN="<token của bạn>"
wrangler pages deploy dist/ --project-name=sre-console
```

### Ràng buộc cốt lõi
- Không nhận/sử dụng token qua chat (bảo mật)
- Không mutate Azure từ local (`no_azure_production_mutations`)
- Không claim production readiness khi 5 P0 gates còn mở
