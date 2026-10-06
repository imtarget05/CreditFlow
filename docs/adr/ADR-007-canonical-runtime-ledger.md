# ADR-007: pipeline/storage/ledger.py Is the Canonical Runtime Ledger

## Status
Accepted

## Context
Repo chứa hai ledger không liên quan runtime với nhau:

- `pipeline/storage/ledger.py` (~1000 dòng) — SQLite/Postgres ledger được `backend/app.py` import trực tiếp: `loan_applications`, `disbursements`, contract code `HDTD-YYYYMMDD-XXXX`, SHA-256 tamper-evidence, document persistence, decision snapshots, idempotency-key anti-double-click. Được bảo vệ bởi `tests/test_api_auth.py` (32), `tests/test_approval_idempotency.py`, `tests/test_approve_race.py`, `tests/test_e2e_disbursement.py`, `tests/test_ledger_*`.
- `src/creditflow/ledger/` (77 dòng: `format.py`, `checkpoint.py`, `recovery.py`) — prototype JSON-checkpoint/recovery, chỉ được `tests/creditflow/*` import. Không có endpoint nào gọi; không nằm trong canonical business flow.

Cả hai cùng tên "ledger" là nguồn nhầm lẫn: người đọc mới không biết đường runtime đi qua đâu.

## Decision
1. `pipeline/storage/ledger.py` là **canonical runtime ledger** — mọi nghiệp vụ tiền tệ (disbursement, contract, snapshot) chỉ đi qua đây.
2. `src/creditflow/ledger/` là **EXPERIMENTAL / NON-CANONICAL** — đã được ghi nhãn trong docstring của từng file.
3. **Lý do giữ prototype:** nó là artifact nghiên cứu nhỏ chứng minh ngữ nghĩa checkpoint/recovery, có test riêng (`tests/creditflow/`), cost duy trì = 77 dòng + 3 test. Xoá sẽ phá history/verification không cần thiết; chuyển thư mục chỉ tạo churn. Giữ nguyên vị trí, gắn nhãn.
4. **Không nhân bản implementation:** không port code từ cây này sang cây kia, không gộp, không viết ledger thứ ba. Hai ledger tồn tại song song có chủ đích; ranh giới được ghi ở đây thay vì nằm trong đầu ai đó.

## Consequences
### Positive
- Một câu chuyện kiến trúc duy nhất: đọc `docs/architecture.md` + ADR này là biết đường tiền đi đâu.
- Developer mới không mất giờ phân định hai module cùng tên.
- Prototype vẫn có test pass mà không cần được coi là production code.

### Negative / Trade-offs
- Hai package cùng khái niệm "ledger" vẫn tồn tại trong cây thư mục — được xử lý bằng nhãn + ADR này, không phải bằng xoá.
- Nếu prototype từng được "thăng hạng" thành runtime, phải mở ADR mới, không được âm thầm gộp.
