# Portfolio Interview — 3 Repo (QuanLyKho, AKS-SRE-Platform, CreditFlow)

**Tác giả:** Kilo · **Ngày:** 2026-11-04 · **Mục đích:** Portfolio có bằng chứng, dễ demo, trung thực cho phỏng vấn SA / AI Engineer / Backend-Platform.

> **Nguyên lý trung thực:** Không claim production nếu chưa có live evidence. CreditFlow dùng synthetic data. AKS cluster private-link không verify được từ local. QuanLyKho local test xanh nhưng chưa có bằng chứng production deploy.

---

## 1. Tổng quan 3 dự án

| Dự án | Vai trò | Stack chính | Điểm kỹ thuật | Kết quả test | Giới hạn trung thực | Mức độ |
|-------|---------|-------------|---------------|--------------|---------------------|--------|
| **CreditFlow** | ML credit decisioning + backend + platform | FastAPI, LangGraph, scikit-learn/XGBoost, React 18, PostgreSQL | OIDC RS256, LangGraph 12-node HITL, maker-checker, idempotency, SHA-256 ledger, pgcrypto encryption, model validation (backtest/ECE/subgroup), backup/restore drill | 621 passed / 3 skipped, ruff 0, gate8a 13/13 | Synthetic 5k rows (seed 42), mock OIDC + mock core banking, chưa deploy production (AKS private-link) | Hoàn thành code, chưa production |
| **AKS-SRE-Platform** | Platform / SRE | Terraform (AzureRM), K8s, Argo CD v3.5.5, Envoy Gateway | Terraform reviewed-before-apply, remote backend azurerm, ArgoCD GitOps, policy-as-code, negative controls, kustomize render, HPA/cluster-autoscaler config | terraform test 16/16, policy gate PASS (6 root, 121 objects), test_gates 62/62, GitOps validator 42/42 | Cluster private-link (không kubectl từ local), ArgoCD sync chưa verify live, external validation không có evidence live, artifacts đã dọn, owner đã chỉ định | Hoàn thành validate cục bộ, chưa live |
| **QuanLyKho** | Full-stack / backend | FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16, Flutter | Maker-checker 4 mắt, dual-entry ledger, idempotency, audit stamp SHA-256, keyset pagination, SSE | 975 passed / 8 skipped / 1 xfailed, Flutter 350/350, ruff 0, Alembic 34 | Chưa có bằng chứng production deploy, Cloudflare Worker giữ tạm (không phải đích production) | Hoàn thành local, chưa production |

---

## 2. Cách giới thiệu portfolio trong 2 phút

> "Tôi có 3 dự án trong portfolio, mỗi dự án giải quyết một bài toán khác nhau và đều có bằng chứng test chạy xanh:
>
> **CreditFlow** là engine quyết định tín dụng ML với human-in-the-loop review và maker-checker disbursement. Tôi implement OIDC IdP giả, encrypted storage, model validation pipeline, và idempotent approval. Test: 621 passed, ruff 0. Ràng buộc: dữ liệu synthetic, chưa kết nối ngân hàng thật, chưa deploy production.
>
> **AKS-SRE-Platform** là nền tảng SRE trên Azure AKS với Terraform reviewed-before-apply, ArgoCD GitOps, policy gates. Tôi restore azurerm backend, chạy validation gates. Test: terraform test 16/16, gates 62/62, GitOps validator 42/42. Ràng buộc: cluster private-link, không verify Argo sync từ local, artifacts đã dọn sạch, owner vận hành đã chỉ định.
>
> **QuanLyKho** là hệ thống tồn kho & quỹ mô phỏng với maker-checker 4 mắt và dual-entry ledger. Tôi implement P0–P1 backlog, idempotency, audit trail. Test: 975 backend passed, Flutter 350/350, ruff 0. Ràng buộc: chưa có bằng chứng production deploy."

---

## 3. File demo & evidence mỗi repo

| Repo | Demo script | Interview release | Raw test results |
|------|-------------|-------------------|------------------|
| CreditFlow | `DEMO.md` | `docs/INTERVIEW_RELEASE.md`, `docs/INTERVIEW_EVIDENCE.md`, `docs/PORTFOLIO_INTERVIEW.md` | `docs/evidence/interview/creditflow_test_results.txt` (621 passed, 3 skipped), `creditflow_ruff_results.txt` (0 errors) |
| AKS-SRE-Platform | `DEMO.md` | `docs/INTERVIEW_RELEASE.md`, `docs/INTERVIEW_KIT.md`, `docs/PORTFOLIO_INTERVIEW_CHEATSHEET.md` | `docs/evidence/interview/aks_test_gates.txt` (62/62), `aks_policy_gate.txt` (PASS), `aks_gitops_validator.txt` (42/42) |
| QuanLyKho | `DEMO.md` | README §🎤 Interview Release | `docs/evidence/interview/quanlykho_test_results.txt` (975/8/1) |

---

## 4. Open items (cần xử lý trước khi claim production-ready)

| # | Open item | Mức độ | Trạng thái | Owner | Ghi chú |
|---|-----------|--------|------------|-------|---------|
| O1 | **AKS live apply/sync không thể từ máy hiện tại** do private-link/VNet | P0 | Mở | Platform team | Cluster Azure nằm private-link, không thể `kubectl` từ máy local. Terraform đã apply nhưng không verify ArgoCD sync từ môi trường hiện tại. Không claim live Azure deployment. |
| O2 | **CI / Sonar / security gate chưa green hoàn toàn** | P0 | Đã chuẩn bị config, chờ push | Platform team | Sonar config đã thêm (sonar-project.properties + job trong CI cho 3 repo). Cần cấu hình `SONAR_TOKEN` + `SONAR_ORGANIZATION` secrets trên GitHub, rồi push để workflow chạy và thu bằng chứng green. |
| O3 | **AKS repo artifact dirty đã dọn sạch** | P1 | ✅ Xong | Platform team | Đã xóa `.coverage`, `apps/agent-hub/.coverage` khỏi tracking; cập nhật `.gitignore` (coverage, `.agents/`, `apps/agent-hub.dropped/`, `apps/sre-console/dist/`). |
| O4 | **GitOps external validation chưa có evidence live** | P1 | Mở | Platform team | Hai external repository chưa checkout → validator báo NOT_PRESENT (không tính fail, nhưng không có live evidence). Cần checkout external repos để có bằng chứng thực. |
| O5 | **Open items vận hành AKS đã chỉ định owner** | P1 | ✅ Xong | Platform team | Owner: Platform team (tất cả 6 vùng: monitoring, alerting, scaling, DR, NetworkPolicy, ArgoCD sync, Terraform state). |

---

## 5. Câu trả lời điểm weakness (trung thực, không over-claim)

| Điểm yếu | Câu trả lời |
|----------|-------------|
| Live AKS sync chưa chạy từ local | "Cluster Azure nằm private-link, không thể kubectl từ máy local. Tôi đã chạy terraform apply thành công nhưng không claim live sync/apply từ môi trường hiện tại." |
| GitOps external repo NOT_PRESENT | "Hai external repository chưa được checkout nên validator báo NOT_PRESENT, không tính là pass. Đây là giới hạn của môi trường local, không phải lỗi code." |
| QuanLyKho TestClient event-loop | "Một số historical TestClient flow có vấn đề event-loop; tôi giữ nguyên thay vì che giấu. Suite chính (pytest) chạy xanh 975/8/1." |
| CreditFlow synthetic data | "CreditFlow dùng dữ liệu synthetic 5k rows (seed 42), chưa phải hệ thống lending thật, không dùng cho quyết định tín dụng thật." |
| CreditFlow chưa production | "Mock OIDC IdP và mock core banking adapter dùng để đóng gates trong demo. Thực tế cần Sacombank IAM tenant, core banking sandbox, legal approval, pen test." |
| QuanLyKho chưa production deploy | "Local test xanh (975 passed) nhưng chưa có bằng chứng production deploy. Không claim production deployment." |
| AKS artifacts dirty | "Đã dọn sạch: xóa coverage khỏi tracking, cập nhật .gitignore, commit riêng cho cleanup." |
| CI/Sonar/security chưa green | "Đã thêm sonar-project.properties + job Sonar vào 3 CI workflow. Cần push lên GitHub, cấu hình SONAR_TOKEN + SONAR_ORGANIZATION, rồi chạy workflow để thu bằng chứng green." |

---

## 6. Câu trả lời câu hỏi kỹ thuật 8 chủ đề

| Chủ đề | Câu trả lời |
|--------|-------------|
| Vì sao dùng maker-checker? | Ngăn một người alone tạo và duyệt giao dịch; giảm rủi ro gian lận/lỗi; phù hợp dual-control compliance. |
| Idempotency chống double-submit thế nào? | `idempotency_key` + atomic `PENDING_REVIEW` reservation; replay trả receipt cũ (`replay: true`); `UNIQUE(disbursements.application_id)`. |
| Audit trail tamper-evident ra sao? | SHA-256 audit stamp (`SHA256(request_id:maker:amount:risk_score)`), ledger hash, decision snapshot hash. |
| OIDC group mapping hoạt động thế nào? | IdP JWT RS256 → verify issuer/audience/JWKS → map groups → role (UNDERWRITER_L1/RISK_COMMITTEE_L2/ADMIN) → take highest authority. |
| Terraform state remote backend và recovery? | AzureRM backend trong storage `stakssre`; `terraform init -backend-config`; state force-unlock nếu lease conflict; errored.tfstate recovery. |
| ArgoCD drift/self-heal xử lý ra sao? | Argo CD reconcile desired state; manual `spec.replicas` change hoặc deleted Deployment tự sync lại; bad Git state cũng được reconcile faithfully → CI review cần thiết. |
| PostgreSQL migration rollback/backup restore? | Alembic upgrade/downgrade; `pg_dump -Fc` + `pg_restore -1` (single transaction); restore drill test; RPO 15p/RTO 4h. |
| Vì sao model không tự quyết định khoản vay? | Model chỉ score; REVIEW route qua human_approval (LangGraph interrupt); execute() là mô phỏng; LLM chỉ explain, không decide. |

---

## 7. Definition of done để đi phỏng interview

| # | Item | Trạng thái | Commit / Tag |
|---|------|------------|--------------|
| D1 | 3 DEMO.md đã tạo (chạy offline, không cần Azure) | ✅ Xong | — |
| D2 | README mỗi repo có interview section (2 phút đọc) | ✅ Xong | — |
| D3 | PORTFOLIO_INTERVIEW.md đã tạo (phiên bản đầy đủ) | ✅ Xong | `CreditFlow/docs/PORTFOLIO_INTERVIEW.md` |
| D4 | Raw test results đã lưu (docs/evidence/interview/ mỗi repo) | ✅ Xong | — |
| D5 | 3 commit/release được chọn lọc | ✅ Xong | CreditFlow `b161674`, QuanLyKho `d5caf99`, AKS `dcea79c` |
| D6 | Không claim live cloud nếu chưa có live evidence | ✅ Xong | — |
| D7 | Có câu trả lời cho 8 chủ đề kỹ thuật và 6+ điểm weakness | ✅ Xong | — |
| D8 | Dọn sạch AKS artifacts dirty (O3) | ✅ Xong | `47e05a9` |
| D9 | Xác định owner cho open items vận hành AKS (O5) | ✅ Xong | Platform team |

---

## 8. Quyết định §9 (đã xác định)

| Câu hỏi | Quyết định |
|---------|------------|
| 1. Vị trí lưu PORTFOLIO_INTERVIEW.md | `CreditFlow/docs/PORTFOLIO_INTERVIEW.md` (repo chính) |
| 2. Commit message convention | `feat(<scope>): <summary>` + body + `Signed-off-by` |
| 3. Tự commit hay chỉ diff | Tự commit (đã commit tất cả thay đổi trên 3 repo) |
| 4. Owner AKS vận hành (O5) | Platform team (tất cả 6 vùng: monitoring, alerting, scaling, DR, NetworkPolicy, ArgoCD sync, Terraform state) |
| 5. Deploy cloud trước | Cloudflare Pages (`apps/sre-console`) trước, rồi Render (`app/sre-demo-api`), rồi Azure fallback |

---

## 9. Kế hoạch deploy web/app (Cloudflare / Render / Azure)

Theo `docs/architecture/cloud-deploy-targets.md` và các config file (`render.yaml`, `azure.yaml`, `apps/sre-console/wrangler.toml`):

### Mục tiêu deploy

| Workload | Primary target | Config file | Trạng thái |
|----------|---------------|-------------|------------|
| `apps/sre-console` (frontend Vite) | Cloudflare Pages | `apps/sre-console/wrangler.toml` | Chưa deploy |
| `app/sre-demo-api` (Node API) | Render | `render.yaml` | Chưa deploy |
| Azure fallback / managed hosting | Azure App Service / Container Apps | `azure.yaml` | Chưa deploy |

### Luồng deploy đề xuất

1. **Cloudflare Pages / Workers**: Public web frontend và edge routing. TLS, WAF, caching, edge access control terminate ở đây.
2. **Render**: Application API runtime cho business services. Dockerized, health probes, OTEL config. Deploy đơn giản và validate được ngoài private VNet.
3. **Azure**: Managed control plane, identity, observability. Azure Developer CLI + App Service làm fallback. Giữ Azure-first approval path chứ không block local validation.

### Điều kiện trước khi deploy production

- Environment-specific secrets (Không hardcode trong repo).
- Image digest thực tế (không placeholder `@sha256:0000...00000`).
- SBOM + Trivy scan + ký + publish trước khi triển khai prod.
- Owner sign-off cho từng workload.
- CI/Sonar/security gate xanh hoàn toàn (O2).

---

## 10. Lệnh push để chạy CI (bạn thực hiện trên GitHub)

```bash
# CreditFlow
cd /Users/mainguyenbinhtan/Downloads/CreditFlow
git push origin codex/portfolio-cloud-evidence-20261104
git tag v1.0.0-interview b161674
git push origin v1.0.0-interview

# QuanLyKho
cd /Users/mainguyenbinhtan/Downloads/QuanLyKho
git push origin codex/portfolio-cloud-evidence-20261104
git tag v1.0.0-interview d5caf99
git push origin v1.0.0-interview

# AKS-SRE-Platform
cd /Users/mainguyenbinhtan/Downloads/AKS-SRE-Platform
git push origin codex/portfolio-cloud-evidence-20261104
git tag v1.0.0-interview dcea79c
git push origin v1.0.0-interview
```

Sau khi push, cấu hình secrets `SONAR_TOKEN` + `SONAR_ORGANIZATION` trên từng repo GitHub, rồi trigger workflow để CI/Sonar chạy green (O2).

---

Lưu tại: `CreditFlow/docs/PORTFOLIO_INTERVIEW.md` (đang copy từ `/Users/mainguyenbinhtan/Downloads/PORTFOLIO_INTERVIEW.md`).
