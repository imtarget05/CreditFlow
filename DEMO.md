# CreditFlow — Interview Demo Script (5–10 phút, chạy hoàn toàn local)

Mục đích: minh họa quy trìnhCredit Risk Decisioning từ submit → score → REVIEW → maker/checker approval → audit trail, không phụ thuộc Azure/private link.

## Yêu cầu trước khi demo

- Python 3.12+, venv đã cài (`pip install -r requirements.txt`)
- PostgreSQL local chạy (hoặc dùng SQLite nếu không có Postgres)
- `models/production/pipeline.joblib` đã tồn tại (đã có sẵn trong repo)

## Bước 1: Khởi động backend

```bash
cd /Users/mainguyenbinhtan/Downloads/CreditFlow

# Nếu dùng PostgreSQL (khuyên dùng để verify encrypted storage):
export DATABASE_URL="postgresql://creditflow:creditflow@localhost:5432/creditflow"
export CREDITFLOW_ENCRYPTION_KEY="creditflow-demo-key-32chars!!!!"

uvicorn backend.app:app --reload --port 8080
```

Backend chạy tại `http://localhost:8000`.

## Bước 2: Submit application → score

```bash
curl -s -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"customer_data": {"income": 8000000, "age": 35, "loan_amount": 50000000, "loan_term": 36, "existing_debt": 2000000, "credit_history": 9, "previous_defaults": 0}}'
```

Kết quả mong đợi: JSON có `score`, `decision` (APPROVE/REVIEW/REJECT), `risk_score`.

## Bước 3: Borderline case → REVIEW (LangGraph interrupt)

```bash
curl -s -X POST http://localhost:8000/predict/graph \
  -H "Content-Type: application/json" \
  -d '{"customer_data": {"income": 5000000, "age": 28, "loan_amount": 120000000, "loan_term": 60, "existing_debt": 5000000, "credit_history": 4, "previous_defaults": 1}}'
```

Kết quả: `approval_required: true`, workflow paused tại `human_approval`.

## Bước 4: Maker/checker approval (idempotent)

```bash
curl -s -X POST http://localhost:8000/predict/graph/{thread_id}/approve \
  -H "Content-Type: application/json" \
  -H "X-CreditFlow-API-Key: demo-key" \
  -d '{"action": "approve", "idempotency_key": "demo-001"}'
```

Kết quả: `ledger_status: APPROVED`, `disbursement` có `contract_code` (HDTD-YYYYMMDD-XXXX), `ledger_hash` (SHA-256).

## Bước 5: Replay approval → không double disburse

```bash
curl -s -X POST http://localhost:8000/predict/graph/{thread_id}/approve \
  -H "Content-Type: application/json" \
  -H "X-CreditFlow-API-Key: demo-key" \
  -d '{"action": "approve", "idempotency_key": "demo-001"}'
```

Kết quả: `replay: true`, cùng contract_code, số dòng disbursement không đổi.

## Bước 6: Xem audit trail

```bash
curl -s http://localhost:8000/audit/{application_id}
```

## Bước 7: Chạy toàn bộ test suite

```bash
python -m pytest tests/ -q
# Kết quả mong đợi: 621 passed, 3 skipped (trên PostgreSQL)
```

## Bước 8: Ruff check

```bash
ruff check .
# Kết quả mong đợi: 0 errors
```

## Các điểm nhấn để trình bày phỏng vấn

| Điểm | Nơi chứng minh |
|---|---|
| ML scoring có business-cost threshold (t=0.20) | `pipeline/modeling/train.py`, `gate8a_model_quality.py` |
| LangGraph 12-node state machine, REVIEW interrupt | `pipeline/agent/graph.py`, `pipeline/agent/nodes.py` |
| Maker-checker, idempotency, disbursement ledger | `backend/app.py` (approve endpoint), `pipeline/disbursement/core_banking.py` |
| Tamper-evident SHA-256 ledger hash | `backend/app.py` (disbursement ledger_hash) |
| Mock OIDC IdP (RS256, JWKS, group→role) | `scripts/mock_oidc_idp.py`, `tests/test_mock_oidc_idp.py` |
| Encrypted PostgreSQL storage (pgcrypto, fail-closed) | `pipeline/storage/encryption.py`, `pipeline/storage/ledger.py` |
| Model validation (backtest, ECE, subgroup) | `scripts/model_validation.py`, `tests/test_model_validation.py` |
| Backup/restore drill (RPO 15p, RTO 4h) | `scripts/backup_restore.py`, `tests/test_backup_restore_drill.py` |

## Giới hạn trung thực (không claim)

- Dữ liệu là synthetic (5k dòng, seed 42), không phải hồ sơ khách hàng thật.
- `execute()` là mô phỏng core banking, không chuyển tiền thật.
- Chưa kết nối Sacombank IdP thật, chưa deploy production từ máy này (AKS private-link).
- Không claim là hệ thống lending production của Sacombank.
