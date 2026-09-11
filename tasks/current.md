# Current Status Snapshot

<!-- generated-by: repo-harness refresh-current-status v1 -->

> **Status**: FINAL — 100/100 LOCAL INTERVIEW READY + LangGraph + GenAI Cloudflare slice.
> GenAI slice complete: Cloudflare Workers AI explain backend, TF-IDF policy RAG,
> eval harness, MLflow logging, `/llm/info` endpoint, CI — all 98 tests passing.
> Free-tier default: `@cf/meta/llama-3.2-1b-instruct` (cheapest/Neuron, 10k free/day).

## Current State

- **Completed Phases**: P1–P4 + Serving readiness + LangGraph + GenAI Cloudflare slice + **P10 Cloud (live)**
- **Cloud deploy (this work, 2026-09-11)**:
  - Backend Render: `https://creditflow-api-ko2h.onrender.com` — LIVE, model loaded
  - Frontend Cloudflare Pages: `https://creditflow-4nu.pages.dev` — LIVE
    (project `creditflow` id `4cccbd67-c0c1-44f6-a987-1c6c7ba6e4db`, deployed via
    wrangler direct upload with account-owned API token; `VITE_API_BASE` baked to Render URL)
  - `frontend/_redirects` added (SPA fallback, was missing from docs claim)
  - E2E verified: CORS OK from pages.dev origin, `POST /predict` → 200 APPROVE

## P11 — Print slip trắng trang: PLAN → IMPLEMENT → REVIEW → APPLY (2026-09-11)

**Owner feedback:** "không có thông tin gì quan trọng khi in phiếu" — PDF in ra chỉ có
header/footer trình duyệt, thân trang trắng (đã bóc text PDF: đúng vậy).

**Root cause (chứng minh bằng code):** `.slip` (App.jsx:717) là CON của `.app`;
`@media print` (styles.css:495) ẩn `.app` bằng `display:none !important` → con của
phần tử ẩn không thể hiển thị → bản in trắng. CSS `.slip { display:block !important }`
vô nghĩa vì ancestor đã ẩn.

**Plan:**
1. App.jsx: đóng `.app` trước `.slip`, wrap return bằng fragment → `.slip` thành sibling.
2. Bổ sung info quan trọng còn thiếu vào phiếu: **Số phiếu** (CF-YYYYMMDD-xxxxx) +
   **Mức rủi ro** (LOW/MEDIUM/HIGH) — 2 trường chưa có trong phiếu cũ.
3. styles.css: thêm `@page { size: A4 portrait; margin: 14mm }` vào `@media print`.
**Review:** build → chạy app thật bằng headless Chrome (playwright-core + system Chrome)
→ bấm Chấm rủi ro → page.pdf() (dùng print CSS thật) → bóc text PDF khẳng định phiếu có nội dung.
**Apply:** redeploy Cloudflare Pages → chạy lại print-test trên URL live.

### P11 KẾT QUẢ — DONE ✅
- Implement: App.jsx wrap fragment, `.slip` chuyển ra NGOÀI `.app` (sibling của root);
  thêm 2 dòng phiếu: **Số phiếu** `CF-YYYYMMDD-xxxxx` (dòng đầu bảng + trong slip-head) và
  **Mức rủi ro** (LOW/MEDIUM/HIGH); styles.css thêm `@page { size: A4 portrait; margin: 14mm }`.
- Build: `dist/assets/index-DthHexEv.js` (166.99 kB) chứa đủ chuỗi phiếu mới.
- Review (bằng chứng headless-Chrome page.pdf + pypdf, print CSS thật):
  - Build cũ: PDF in trắng (bản của owner) — root cause tái hiện đúng.
  - Build mới local: PDF 31.7 kB chứa toàn bộ phiếu — Số phiếu CF-20260911-33313,
    8 trường hồ sơ, xác suất 1.9%, Mức rủi ro LOW, APPROVE, 2 ô ký tên. 6/7 assert PASS,
    mục còn lại là false-negative do needle gõ nhầm + uppercase CSS.
  - LIVE sau deploy: print-test trên https://creditflow-4nu.pages.dev → PDF chứa
    "PHIẾU ĐÁNH GIÁ RỦI RO TÍN DỤNG · Số phiếu CF-20260911-87217 · … · Mức rủi ro · APPROVE".
    Live bundle xác nhận = index-DthHexEv.js (bản mới).
- Apply: wrangler deploy thành công — deployment https://c4b6799c.creditflow-4nu.pages.dev
  (production alias https://creditflow-4nu.pages.dev cập nhật theo).
- Lưu ý pypdf: bóc text tiếng Việt có glyph tổ hợp → luôn normalize (NFC + bỏ \s) trước khi assert.
- **GenAI Slice (this work)**:
  - `pipeline/agent/llm_provider.py` — Cloudflare Workers AI REST backend, offline fallback
  - `pipeline/agent/explanations.py` — wired cloudflare branch, `PROMPT_VERSION`, RAG context
  - `pipeline/agent/retriever.py` — TF-IDF on `docs/policy/*.md`
  - `docs/policy/{dti,lti,defaults,fraud}.md` — policy corpus split from policy.py
  - `pipeline/agent/nodes.py` — explain node with RAG + latency + full meta
  - `scripts/eval_explanations.py` — offline eval harness + MLflow log
  - `backend/app.py` — `GET /llm/info`, llm counters in `/metrics`
  - `.env.example` — 4 CLOUDFLARE_* vars, `.github/workflows/ci.yml`
- **Test Coverage**: 98 tests passing (`python -m pytest tests/ -q`; was 79 baseline → +19: LangGraph/GenAI slices + VND contract)

## Phase Status

| Phase | Status | Evidence |
|-------|--------|----------|
| P1 Business + Data | ✅ Complete | `pipeline/validation/schemas.py`, data dictionary, synthetic dataset |
| P2 EDA | ✅ Complete | `notebooks/creditflow_eda.ipynb` |
| P3 Feature Engineering | ✅ Complete | `pipeline/feature_engineering/features.py` |
| P4 Model Benchmark | ✅ Complete | `pipeline/modeling/`, `models/production/benchmark_results.csv` |
| P5 Evaluation + Threshold | ✅ Complete | `pipeline/modeling/evaluate.py`, `threshold.py` |
| P6 Model artifact | ✅ Complete | `models/production/pipeline.joblib` + `meta.json` |
| P7 MLflow | ✅ Complete | Real tracking verified |
| P8 FastAPI | ✅ Complete | `backend/app.py` — 12 endpoints (health, predict, drift, model/info, llm/info, metrics, 4× graph/audit) |
| P9 Docker | ✅ Config | `Dockerfile` + compose |
| P10 Cloud | 📋 Config ready | `render.yaml` committed |
| P11 Monitoring | ✅ Complete | `GET /metrics` + `GET /drift` |
| GenAI Cloudflare | ✅ Complete | llm_provider + retriever + eval + /llm/info + CI |

## Next Recommended Action

1. **Owner visual pass**: open `http://localhost:5173` → walk Predict/Model/Monitor tabs.
2. **Docker runtime**: `docker build -t creditflow-api .` → verify in container.
3. **MLflow UI visual**: `mlflow ui --port 5000` → verify experiment/registry.
4. **STOP DEVELOPMENT** — project scope is LOCAL INTERVIEW-READY. Cloud permanently OUT OF SCOPE.
