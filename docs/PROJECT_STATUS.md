# PROJECT STATUS — 3 Repo Portfolio Interview

**Ngày:** 2026-11-04 · **Trạng thái:** Portfolio-ready, production blocked by external infra governance  
**Tác giả:** Kilo · **Mục đích:** Portfolio cho phỏng vấn SA / AI Engineer / Backend-Platform

---

## 1. Tóm tắt 3 repository

| Repo | Vai trò | Stack chính | Kết quả test | CI/Sonar | Mức độ |
|------|---------|-------------|--------------|----------|--------|
| **CreditFlow** | ML credit decisioning + backend + platform | FastAPI, LangGraph, scikit-learn/XGBoost, React 18, PostgreSQL | 621 passed / 3 skipped, ruff 0, gate8a 13/13 | ✅ Green | Hoàn thành code, chưa production |
| **AKS-SRE-Platform** | Platform / SRE | Terraform (AzureRM), K8s, Argo CD v3.5.5, Envoy Gateway | terraform test 16/16, gates 62/62, GitOps validator 42/42 | ✅ Green | Validate cục bộ, chưa live |
| **QuanLyKho** | Full-stack / backend | FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16, Flutter | 975 passed / 8 skipped / 1 xfailed, Flutter 350/350, ruff 0 | ✅ Green | Hoàn thành local, chưa production |

---

## 2. By-design evidence

- 3 DEMO.md đã tạo (chạy offline, không cần Azure)
- `sonar-project.properties` + job `sonar` trong 3 CI workflow
- Cloud deploy targets: `render.yaml`, `azure.yaml`, `wrangler.toml`, `cloud-deploy-targets.md`
- Tag release `v1.0.0-interview` trên 3 repo

---

## 3. Production go-live

**Không tuyên production go-live.**

Nguyên nhân: 5 P0 gates open (theo docs/qa/ENTERPRISE_READINESS_2026-11-03.md), dependency bên ngoài (Sacombank IAM, core banking sandbox, legal, pen test) out-of-scope, và ràng buộc `no_azure_production_mutations` (kind-local-platform là target duy nhất, không mutate Azure/production).

---

## 4. Open items còn lại (ngoài repo, không fix bằng code)

| # | Vấn đề | Mức độ | Lý do chặn | Cần gì để unblock |
|---|--------|--------|------------|-------------------|
| O1 | AKS live apply/sync không thể từ máy local | P0 | Cluster Azure private-link / Private DNS, không kubectl từ local | Tenant Azure thật + quyền quản trị cluster / Private Link / Private DNS; test connectivity từ approved subnet hoặc bastion |
| O4 | GitOps external validation chưa có evidence live | P1 | External repos / tenant / GitOps repo chưa có quyền truy cập thật | Quyền vào external repos, ArgoCD sync + drift reconciliation, backup/restore + DR drill, HPA / cluster-autoscaler, NetworkPolicy trên Azure CNI, Terraform remote backend thực tế |

---

## 5. Kết luận

Phần code và CI/Sonar đã xong, có bằng chứng xanh trên 3 repo. Phần còn lại là **ngoài repo** (môi trường và quyền truy cập thật), không thể đi tiếp nếu không có infrastructure và tenant thực tế. Không thêm code để “bypass” O1/O4 — đó sẽ là fake/unsafe.

**Status cuối:** `portfolio-ready, production blocked by external infra governance`.
