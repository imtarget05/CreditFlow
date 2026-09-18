# DEPLOYMENT CHỐT — Private Cloud / Enterprise On-Premise

> Deployment duy nhất: **PRIVATE ON-PREM**. Banking Secrecy / GDPR / SBV — **CONFIDENTIAL never leaks**.

## 1. Kiến trúc chốt

- **Training**: Traditional ML Training from Scratch trên Colab
  (`colab/train_credit_T4.ipynb` — LogReg/XGBoost tabular).
- **Lõi serving**: Deterministic ML + Rule Engine nội bộ.
- **Processing**: Rule Filter DTI/LTI + TF-IDF cosine inference (local, offline-safe).
- **LLM memo**: chỉ chạy **Private vLLM nội bộ (Local VPC)**:
  `CREDITFLOW_LLM_PROVIDER=ollama | local_vllm` (private-vllm).
  Rỗng (`""`) = deterministic template fallback — mặc định an toàn nhất cho CONFIDENTIAL.
- **Public cloud** (OpenAI / Groq / Cloudflare / Anthropic / Google):
  **chỉ dùng cho data PUBLIC đã tách PII, hoặc tắt hẳn**. Không bao giờ nhận CONFIDENTIAL.

## 2. Phân loại dữ liệu

| Lớp | Ví dụ | Đi đâu được |
|-----|-------|-------------|
| `PUBLIC` | Policy docs `docs/policy/*.md`, synthetic demo, eval fixtures đã tách PII | Private vLLM, template, hoặc public cloud (opt-in) |
| `CONFIDENTIAL` (mặc định) | Hồ sơ vay thật: thu nhập, nợ, CIC, PII khách hàng (`data_classification` vắng mặt = CONFIDENTIAL) | **Chỉ Private vLLM nội bộ hoặc template. TUYỆT ĐỐI KHÔNG ra public cloud.** |

## 3. Luồng chặn (Policy Guard)

`pipeline/agent/explanations.py::_try_llm_explanation`:

1. Đọc `CREDITFLOW_LLM_PROVIDER` → tính `is_public_cloud`
   (`cloudflare`, `groq`, `openai`, `anthropic`, `google`).
   `ollama` / `local*` / `local_vllm` là LOCAL — không bị chặn.
2. Nếu `data_classification == "CONFIDENTIAL"` **và** `is_public_cloud`
   **và** không có opt-in `CREDITFLOW_ALLOW_EXTERNAL_LLM=1` →
   `print("[Policy Guard] Blocked ...")` + `return None`
   → `generate_explanation` rơi về **deterministic template fallback**
   (`fallback_reason=provider_unavailable`), decision/threshold giữ nguyên.
3. LLM chỉ *explain*, không bao giờ *decide* (output lọc qua
   `_EXPLANATION_FIELDS` + numeric grounding gate).

```
CONFIDENTIAL ──public provider──▶ [Policy Guard] BLOCK ──▶ template fallback (offline)
CONFIDENTIAL ──private (ollama/local_vllm)──▶ Private vLLM (Local VPC)
PUBLIC ──opt-in──▶ public cloud (tách PII) │ mặc định vẫn template/private
```

## 4. Cấu hình

```bash
# An toàn nhất / mặc định CONFIDENTIAL:
CREDITFLOW_LLM_PROVIDER=
# Private nội bộ:
CREDITFLOW_LLM_PROVIDER=local_vllm   # hoặc ollama
# Public cloud: CHỈ cho data PUBLIC, mặc định tắt:
# CREDITFLOW_ALLOW_EXTERNAL_LLM=1
```

## 5. Verify

```bash
grep -rn "Private\|CONFIDENTIAL\|Policy Guard" README.md plans/plan-genai-cloudflare.md pipeline/agent/explanations.py .env.example tasks/todos.md docs/DEPLOYMENT_PRIVATE_ONPREM.md
pytest tests/test_llm_provider.py -q
```

## 6. Model bundle manifest + approval an toàn (cập nhật plan 2026-09-18)

### Model bundle (serving contract)

- `models/production/` là **một bundle version bất biến**: `pipeline.joblib`,
  `meta.json`, `reference_stats.json`, benchmark outputs, và `manifest.json`
  (bundle_version=1, metadata khớp `meta.json`, SHA-256 cho từng artifact).
- Service validate bundle lúc khởi động (`validate_model_bundle()` trong
  `backend/predict_service.py`): thiếu/sai metadata (`feature_order`,
  `vnd_per_model_unit`), lệch scikit-learn major/minor, hoặc sai hash → từ chối
  serve (`ArtifactContractError` → HTTP 503 `MODEL_BUNDLE_INVALID`), không dự
  đoán trên artifact thiếu hợp đồng.
- Retrain: `python3 scripts/train_models.py` ghi bundle vào thư mục tạm,
  validate rồi mới swap nguyên khối vào `models/production/`.

### Approval + disbursement replay-safe

- `POST /predict/graph/{thread_id}/approve` là **một transaction duy nhất**:
  reserve hồ sơ `PENDING_REVIEW` kèm `approver_id` + `idempotency_key`
  (SQLite unique index), resume graph, chỉ ghi `disbursements` khi graph kết
  thúc ở `APPROVE`. `UNIQUE(disbursements.application_id)` đảm bảo tối đa một
  dòng tiền cho mỗi hồ sơ — duplicate approval trả về receipt cũ (`replay: true`).
- Authority là **hồ sơ đã persist + resumed graph state**, không phải text
  `req.action` từ request; lượng giải ngân lấy từ `customer_data.loan_amount`
  đã lưu lúc nộp đơn.
- Gateway failure (model/gateway/checkpoint) → `workflow_status=FAILED` +
  `error_code`, không bao giờ tạo row `PENDING_REVIEW`; gateway simulator ghi
  `source_mode=SIMULATION` và bị chặn cứng khi `CREDITFLOW_ENV=production`.
- Checkpoint là JSON-only: file hỏng được quarantine với timestamp và health
  báo `degraded` (`CHECKPOINT_CORRUPT`) cho đến khi vận hành xử lý xong.

### Verify

```bash
pytest tests/test_predict_units.py tests/test_models.py tests/test_ledger_units.py tests/test_checkpoint_persistence.py -q
CREDITFLOW_ENV=production pytest tests/test_api.py -q
```
