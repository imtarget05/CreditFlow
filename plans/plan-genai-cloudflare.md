# GenAI Slice Cloudflare Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bật LLM thật Cloudflare (`@cf/meta/llama-3.1-8b-instruct`) chỉ để explain trong LangGraph workflow, kèm mini-RAG policy + eval + observability, giữ decision thuộc threshold/policy/human.

**Architecture:** `retriever.py` (TF-IDF local) → `_build_context` + `rag_context` → `llm_provider.py` (REST Workers AI, không hardcode token) → `generate_explanation()` (LLM, fallback template) → `explain` node ghi `explanation_meta {source, llm_model, prompt_version, latency_ms, rag_sources}` → `/predict/graph*`, `/llm/info`, `/metrics`, MLflow nested run.

**Tech Stack:** Python 3.12, LangChain Core (`ChatPromptTemplate`), httpx REST `.../accounts/{id}/ai/run/{model}`, scikit-learn TF-IDF, FastAPI, MLflow SQLite, pytest + TestClient. Verify bằng `venv/bin/python -m pytest`.

**Spec:** `docs/spec.md` §3/§9 (FN=5/FP=1, recall-centric), §11 (MLflow audit), §12 (API), §16 (LangGraph spine; LLM explains never decides — `pipeline/agent/explanations.py:1-16`, `pipeline/agent/nodes.py:238-258`), §19 (proxy data; template offline fallback).

## Global Constraints

- LLM never decides; chỉ `explain` node dùng LLM.
- `.env` gitignored, không commit token; chỉ đọc `CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN / CLOUDFLARE_MODEL / CREDITFLOW_LLM_PROVIDER`.
- Template fallback offline luôn xanh khi thiếu key/mạng.
- Prompt version `credit-explain-v1`, temp 0.3, max_tokens 500.
- Không thêm vector DB/FAISS ở slice này; không đưa LlamaIndex vào core path.
- Mỗi task 2–5 phút, có test → verify → commit riêng.

---

## File Structure

- NEW `pipeline/agent/llm_provider.py` — đọc env, gọi Workers AI REST, trả dict structured + `_llm=True`.
- MODIFY `pipeline/agent/explanations.py` — nhánh `cloudflare`, `PROMPT_VERSION`, `rag_context/sources` trong prompt + context.
- NEW `pipeline/agent/retriever.py` — TF-IDF trên `docs/policy/*.md`, `retrieve(query,k=2)`.
- NEW `docs/policy/dti.md, lti.md, defaults.md, fraud.md` — tách từ `pipeline/agent/policy.py:25-45`.
- MODIFY `pipeline/agent/nodes.py:explain` — retrieve + latency + full meta (fix `_llm_was_used`).
- NEW `scripts/eval_explanations.py`, NEW `tests/test_llm_provider.py`, `tests/test_retriever.py`, `tests/test_eval.py`.
- MODIFY `backend/app.py` — response thêm `explanation_meta`, NEW `GET /llm/info`, `Metrics` thêm llm counters.
- MODIFY `.env.example` — thêm 4 biến CLOUDFLARE_* rỗng; NEW `.github/workflows/ci.yml`.

---

### Task 1: Cloudflare provider — failing test trước

**Files:**
- Create: `tests/test_llm_provider.py`

- [ ] **Step 1: Viết failing test** (2 tests như Chunk 1: missing-key→None, mock-200→structured+`_llm`)
- [ ] **Step 2: Chạy, xác nhận FAIL** — Run: `venv/bin/python -m pytest tests/test_llm_provider.py -v` — Expected: FAIL `ModuleNotFoundError`
- [ ] **Step 3: Không commit riêng (gộp vào commit Task 2)**

### Task 2: `llm_provider.py` minimal cho test xanh

**Files:**
- Create: `pipeline/agent/llm_provider.py` — `_cfg()`, `try_cloudflare_explain()`, `PROMPT_VERSION`, `DEFAULT_MODEL`, `LLMUnavailable`

- [ ] **Step 1: Viết implementation tối thiểu** (code đầy đủ trong Chunk 2)
- [ ] **Step 2: Chạy** `venv/bin/python -m pytest tests/test_llm_provider.py -v` — Expected: 2 passed
- [ ] **Step 3: Regression** `venv/bin/python -m pytest tests/test_agent.py -q` — Expected: PASS
- [ ] **Step 4: Commit** `git add pipeline/agent/llm_provider.py tests/test_llm_provider.py && git commit -m "feat(llm): add Cloudflare Workers AI explain backend with offline fallback"`

### Task 3: Đấu nối `explanations.py` + fix bug `_llm`

**Files:**
- Modify: `pipeline/agent/explanations.py` (nhánh cloudflare, `PROMPT_VERSION`, template gán `prompt_version`, prompt thêm `{rag_context}`)

- [ ] **Step 1: Thêm failing assert** `test_generate_marks_llm_source`
- [ ] **Step 2: Chạy, xác nhận FAIL** (chưa có nhánh cloudflare → fallback template)
- [ ] **Step 3: Sửa `explanations.py`** (3 điểm trong Chunk 2)
- [ ] **Step 4: Chạy** `venv/bin/python -m pytest tests/test_llm_provider.py tests/test_agent.py -q` — Expected: PASS
- [ ] **Step 5: Commit** `feat(llm): wire Cloudflare branch, tag prompt_version, fix _llm source marker`

### Task 4: `docs/policy/*.md`

**Files:** Create `docs/policy/dti.md, lti.md, defaults.md, fraud.md` (nội dung Chunk 3)
- [ ] Viết 4 file → Verify `ls docs/policy/ && grep -c policy_ docs/policy/*.md` → Commit `docs(rag): add policy corpus split from policy.py rules`

### Task 5: `retriever.py` TF-IDF

**Files:** Create `pipeline/agent/retriever.py` + `tests/test_retriever.py`
- [ ] Failing test → FAIL `ModuleNotFoundError` → implementation → 2 passed → Commit `feat(rag): add TF-IDF policy retriever`

### Task 6: Sửa `explain` node

**Files:** Modify `pipeline/agent/nodes.py:explain` + `_build_context` thêm `rag_context/rag_sources`
- [ ] Regression assert → sửa hàm (code Chunk 3) → `pytest tests/test_retriever.py tests/test_agent.py tests/test_llm_provider.py -q` PASS → Commit `feat(rag): ground explain node with policy retrieval + latency meta`

### Task 7: `scripts/eval_explanations.py`

**Files:** Create `scripts/eval_explanations.py` + `tests/test_eval.py`
- [ ] Failing test → implementation (code Chunk 4) → test PASS + CLI ghi `models/evaluation/llm_eval_*.json` → Commit `feat(eval): add offline explanation eval harness`

### Task 8: Log eval vào MLflow

**Files:** Modify `scripts/eval_explanations.py` (thêm `_log_eval_mlflow`, gọi trong `__main__`)
- [ ] Verify `MLFLOW_TRACKING_URI=sqlite:///mlflow.db venv/bin/python scripts/eval_explanations.py` (offline: file JSON + `[mlflow] eval logged.` hoặc skip note, không crash) → Commit `feat(eval): log explanation eval to MLflow`

### Task 9: `GET /llm/info` + llm counters

**Files:** Modify `backend/app.py` (Metrics.llm, snapshot keys, endpoint) + append `tests/test_eval.py`
- [ ] Failing test `test_llm_info_no_secret_leak` → FAIL 404 → sửa app → PASS → Commit `feat(api): add GET /llm/info + llm counters (no secret leak)`

### Task 10: `.env.example` + CI

**Files:** Modify `.env.example` (4 dòng), Create `.github/workflows/ci.yml`, comment `Dockerfile`, đoạn env `README.md`
- [ ] Verify `venv/bin/python -m pytest tests/ -q` PASS toàn bộ → Commit `chore: cloudflare env template + CI without secrets`

---

## Self-Review

1. **Spec coverage:** GenAI explain ✅ T1–T3; RAG ✅ T4–T6; eval+MLflow ✅ T7–T8; observability ✅ T9; CI ✅ T10.
2. **Placeholder scan:** không TBD/TODO; code đầy đủ mỗi task.
3. **Type consistency:** `try_cloudflare_explain(ctx)->dict|None` T2→T3; `retrieve()->[{doc_id,chunk,score}]` T5→T6; `explanation_meta` keys T6=T9; `PROMPT_VERSION` single-source `llm_provider.py`.

## Tradeoffs (ghi nhận)

- Direct REST httpx thay vì `langchain-cloudflare`: tránh dep chưa verify, vẫn giữ interface LangChain-prompt ở caller; nâng cấp sau không đổi signature.
- TF-IDF local thay vì FAISS/embeddings: đủ cho corpus 4 docs, zero-dep mới; follow-up Cloudflare embeddings nếu eval đòi.
- Test dùng mock transport, không gọi API thật trong CI (không cần key).
