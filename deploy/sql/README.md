# Hướng dẫn áp dụng migration PostgreSQL — CreditFlow

Thư mục này chứa các tập tin SQL dùng để cài đặt bảng lưu trữ sản xuất trên
PostgreSQL cho dự án CreditFlow, bổ sung cho `pipeline/storage/ledger.py`
(dual SQLite/Postgres qua `DATABASE_URL`).

## 1. Điều kiện tiên quyết

- Server PostgreSQL (khuyên dùng PostgreSQL 14+).
- Client `psql` đã cài đặt và có trong `PATH`.
- Biến môi trường `DATABASE_URL` dưới dạng `postgresql://user:pass@host:5432/dbname`.
- Bảng `loan_applications` phải đã tồn tại trước khi áp dụng migration này
  (tự động tạo bởi `pipeline/storage/ledger.py` hàm `init_db()` khi
  `DATABASE_URL` trỏ tới Postgres, hoặc áp dụng trước schema-ledger).

## 2. Cách áp dụng

Áp dụng từng tập tin theo thứ tự (xem mục 3), ví dụ:

```bash
export DATABASE_URL="postgresql://creditflow:password@localhost:5432/creditflow"

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f deploy/sql/001_core_banking.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f deploy/sql/002_outcome_tracking.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f deploy/sql/003_user_management.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f deploy/sql/004_notification.sql
```

Áp dụng toàn bộ theo thứ tự trong một lần:

```bash
export DATABASE_URL="postgresql://creditflow:password@localhost:5432/creditflow"
for f in deploy/sql/00*.sql; do
  echo "==> Áp dụng $f"
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"
done
```

`-v ON_ERROR_STOP=1` khiến `psql` dừng ngay khi có lỗi, tránh áp dụng một phần.

Kiểm tra sau khi áp dụng:

```bash
psql "$DATABASE_URL" -c "\dt"
psql "$DATABASE_URL" -c "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename;"
```

## 3. Thứ tự áp dụng

Phải áp dụng theo thứ tự số thứ tự trong tên tập tin:

| Thứ tự | Tập tin | Nội dung | Phụ thuộc |
|--------|---------|----------|-----------|
| 1 | `001_core_banking.sql` | `core_banking_transactions`, `core_banking_audit` | `loan_applications` |
| 2 | `002_outcome_tracking.sql` | `loan_outcomes` | `loan_applications` |
| 3 | `003_user_management.sql` | `users`, `user_sessions`, `user_role_history` | — (tự chứa) |
| 4 | `004_notification.sql` | `notifications`, `notification_templates` | — (tự chứa) |

Không nên bỏ qua hoặc thay đổi thứ tự vì các ràng buộc khóa ngoại (FK)
giữa các bảng và tới `loan_applications`.

## 4. Idempotency

Tất cả các lệnh định nghĩa schema dùng `IF NOT EXISTS`:

- `CREATE TABLE IF NOT EXISTS`
- `CREATE INDEX IF NOT EXISTS`

Do đó, các tập tin này **an toàn khi chạy lại nhiều lần** — chạy lại không
thay đổi dữ liệu hiện tại và không gây lỗi khi bảng/chỉ mục đã tồn tại.
Khóa chính, CHECK, UNIQUE, FK chỉ được tạo một lần.

Lưu ý: các tập tin này không chứa `ALTER TABLE` sửa đổi cột đã tồn tại,
vì vậy nếu schema cũ không khớp (kiểu cột khác, thiếu ràng buộc), migration
không tự động khắc phục — cần kiểm tra thủ công trước khi áp dụng trên sản
phẩm.

## 5. Mã hóa lưu trữ (Encryption at rest)

PostgreSQL không có Transparent Data Encryption (TDE) built-in. Khuyến khích
kết hợp hai lớp:

**a. Mã hóa cột nhạy cảm với `pgcrypto`**

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Ví dụ mã hóa cột chứa PII (ví dụ error_message có thể chứa thông tin khách hàng)
ALTER TABLE notifications ADD COLUMN error_message_encrypted TEXT;
UPDATE notifications SET error_message_encrypted = pgp_sym_encrypt(error_message, 'mua-key-tu-bien-moi-truong');
SELECT pgp_sym_decrypt(error_message_encrypted, 'mua-key-tu-bien-moi-truong') FROM notifications;
```

Khóa mã hóa không được lưu trong mã nguồn hoặc cùng cơ sở dữ liệu — lấy từ
biến môi trường / secret manager (HashiCorp Vault, AWS Secrets Manager, Azure Key Vault).

**b. Mã hóa toàn ổ (TDE相当)**

Mã hóa ở lớp lưu trữ/dưới hệ điều hành:

- Linux: LUKS / dm-crypt trên ổ chứa dữ liệu PostgreSQL (`PGDATA`).
- Cloud: EBS encryption (AWS), Managed Disks encryption (Azure), Persistent Disk encryption (GCP).
- Backup file cũng phải được mã hóa trước khi đưa ra ngoài hệ thống.

## 6. Sao lưu và khôi phục (Backup and Restore)

### 6.1 Sao lưu logic (pg_dump)

Sao lưu cơ sở dữ liệu CreditFlow (định dạng tùy chỉnh, nén, có thể khôi phục chọn lựa):

```bash
export DATABASE_URL="postgresql://creditflow:password@localhost:5432/creditflow"
pg_dump -d "$DATABASE_URL" -Fc -f "creditflow_$(date +%F_%H%M).dump"
```

Sao lưu toàn cluster (vai trò, quyền, cơ sở dữ liệu khác):

```bash
pg_dumpall -h "$PGHOST" -U "$PGUSER" > "cluster_$(date +%F).sql"
```

### 6.2 Khôi phục logic

Tạo cơ sở dữ liệu trống trước, rồi khôi phục:

```bash
createdb -h "$PGHOST" -U "$PGUSER" creditflow
pg_restore -d "$DATABASE_URL" -1 -v "creditflow_2026-11-03.dump"
```

- `-1` (--single-transaction): khôi phục trong một giao dịch, toàn bộ thành công hoặc toàn bộ rollback.

### 6.3 Khôi phục thời điểm liên tục (Point-in-Time Recovery, PITR)

Cấu hình `postgresql.conf`:

```
wal_level = replica
archive_mode = on
archive_command = 'cp %p /var/lib/postgresql/archive/%f'
```

Sao lưu cơ sở vật lý:

```bash
pg_basebackup -D /var/lib/postgresql/backup/base -Fp -Xs -P -R
```

Phục hồi đến một thời điểm trước sự cố (`recovery_target_time` trong
`postgresql.auto.conf` hoặc `recovery.signal`), sau đó PostgreSQL replay WAL
đến mục tiêu đó.

### 6.4 Khuyến nghị tần suất

- Sao lưu logic đầy đủ: hàng ngày (đêm).
- Sao lưu tăng dần/WAL archive: liên tục (mỗi 5–15 phút).
- Kiểm tra khôi phục: ít nhất một lần/Tháng trên môi trường staging.

## 7. Mục tiêu RPO / RTO

| Chỉ số | Mục tiêu | Cách đạt |
|--------|----------|----------|
| **RPO** (mất dữ liệu tối đa chấp nhận được) | ≤ 15 phút | WAL archive liên tục, sao lưu logic hàng ngày |
| **RTO** (thời gian khôi phục tối đa) | < 4 giờ | Base backup + replay WAL đến thời điểm sự cố; đã chạy drill khôi phục |

Trong phạm vi demo local (`kind-local-platform`, SQLite), không có yêu cầu
sao lưu/mã hóa sản xuất — xem `creditflow.storage_demo_mode` và
`constraint.no_azure_production_mutations` trong `project.md`.
