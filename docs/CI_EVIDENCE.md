# CI EVIDENCE — Workflow URLs (Green Status)

**Ngày:** 2026-11-04 · **Trạng thái:** Tất cả CI/Sonar job đã chạy xanh trên 3 repo

---

## 1. Workflow run URLs

| Repo | Workflow | Run ID | URL | Trạng thái |
|------|----------|--------|-----|------------|
| **AKS-SRE-Platform** | pr-gate | 41785379829 | https://github.com/imtarget05/AKS-SRE-Platform/actions/runs/41785379829 | ✅ Success |
| **CreditFlow** | ci | 41786056513 | https://github.com/imtarget05/CreditFlow/actions/runs/41786056513 | ✅ Success |
| **QuanLyKho** | ci | 41786122244 | https://github.com/imtarget05/QuanLyKho/actions/runs/41786122244 | ✅ Success |

---

## 2. Chi tiết job (từ `gh run view`)

### AKS-SRE-Platform — pr-gate (run 41785379829)
- terraform: fmt, init, validate, test, tflint — pass
- kubernetes: render, kubeconform, manifest inventory, policy gate — pass
- console-smoke: sre-console smoke test — pass
- security: trivy config + fs — pass
- python: gate test matrix G1-G16, cloud cutover, origin mTLS, destroy contract — pass
- sonar: SonarCloud scan — pass

### CreditFlow — ci (run 41786056513)
- lint-airflow: ruff check — pass
- test: pytest (not integration/slow/live/infra) — pass
- test-slow: pytest slow workflow — pass
- coverage: pytest-cov @75% — pass
- gitleaks: secret scanning — pass
- trivy: vulnerability scan CRITICAL,HIGH — pass
- pip-audit: dependency audit — pass
- build-frontend: npm ci + production build — pass
- sonar: SonarCloud scan — pass

### QuanLyKho — ci (run 41786122244)
- secret-guard: check_no_secrets.py — pass
- lint: ruff 0.17.0 — pass
- test: pytest với PostgreSQL 16 — pass (975/8/1)
- frontend-web: Flutter analyze + test + build web — pass
- worker-typecheck: Cloudflare Worker typecheck — pass
- build: Docker build + compose validate — pass
- sonar: SonarCloud scan — pass

---

## 3. Tag release

| Repo | Tag | Commit |
|------|-----|--------|
| CreditFlow | `v1.0.0-interview` | `fe1af08` |
| QuanLyKho | `v1.0.0-interview` | `d5caf99` |
| AKS-SRE-Platform | `v1.0.0-interview` | `dcea79c` |

---

## 4. Branch

Tất cả trên branch `codex/portfolio-cloud-evidence-20261104` của organization `imtarget05`.
