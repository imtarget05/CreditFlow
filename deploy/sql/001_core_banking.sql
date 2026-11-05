-- ============================================================================
-- 001_core_banking.sql — Bảng core banking (Core Banking)
-- Điều kiện tiên quyết: bảng loan_applications phải đã tồn tại
--   (tạo bởi pipeline/storage/ledger.py init_db() khi DATABASE_URL là Postgres).
-- ============================================================================

-- Bảng giao dịch core banking: ghi chép mọi việc phát tán vay, trả nợ và
-- hoàn lại theo quy trình làm và kiểm duyệt (maker-checker) của ngân hàng.
CREATE TABLE IF NOT EXISTS core_banking_transactions (
    id                BIGSERIAL PRIMARY KEY,
    transaction_id    TEXT NOT NULL UNIQUE,
    application_id    TEXT NOT NULL,
    transaction_type  TEXT NOT NULL CHECK (
        transaction_type IN ('DISBURSEMENT', 'REPAYMENT', 'ROLLBACK')
    ),
    amount            NUMERIC(18, 2) NOT NULL,
    currency          TEXT NOT NULL DEFAULT 'VND',
    account_number    TEXT NOT NULL DEFAULT '',
    account_name      TEXT NOT NULL DEFAULT '',
    error_message     TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (
        status IN ('PENDING', 'APPROVED', 'COMPLETED', 'FAILED', 'REVERSED')
    ),
    maker_id          TEXT NOT NULL,          -- người tạo giao dịch (tái tra cứu bằng username/user_id)
    checker_id        TEXT,                   -- người duyệt (NULL trước khi duyệt)
    approved_at       TIMESTAMPTZ,
    executed_at       TIMESTAMPTZ,
    reversed_at       TIMESTAMPTZ,
    reversal_reason   TEXT,
    reference_code    TEXT,                   -- mã tham chiếu nội bộ (HDTD-YYYYMMDD-XXXX)
    external_reference TEXT,                  -- mã tham chiếu từ core banking / ngân hàng đối tác
    idempotency_key   TEXT NOT NULL,          -- khóa idempotency để tránh ghi giao dịch trùng lặp
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Một idempotency_key chỉ được ghi một lần cho mỗi loại giao dịch
    CONSTRAINT uq_cbt_idem_per_type UNIQUE (idempotency_key, transaction_type)
);

COMMENT ON TABLE core_banking_transactions IS 'Ghi chép giao dịch tiền core banking (phát tán/trả nợ/hoàn lại) với quy trình làm và kiểm duyệt (maker-checker).';
COMMENT ON COLUMN core_banking_transactions.transaction_id IS 'Mã giao dịch duy nhất sinh bởi hệ thống core banking.';
COMMENT ON COLUMN core_banking_transactions.application_id IS 'Mã hồ sơ vay (application_id) mà giao dịch này thuộc về.';
COMMENT ON COLUMN core_banking_transactions.transaction_type IS 'Loại giao dịch: DISBURSEMENT (phát tán), REPAYMENT (trả nợ), ROLLBACK (hoàn lại).';
COMMENT ON COLUMN core_banking_transactions.amount IS 'Số tiền giao dịch, lưu NUMERIC(18,2) để tránh sai số dạng float.';
COMMENT ON COLUMN core_banking_transactions.account_number IS 'Số tài khoản thụ hưởng của khách hàng.';
COMMENT ON COLUMN core_banking_transactions.account_name IS 'Tên chủ tài khoản thụ hưởng.';
COMMENT ON COLUMN core_banking_transactions.error_message IS 'Thông điệp lỗi nếu giao dịch thất bại.';
COMMENT ON COLUMN core_banking_transactions.reversed_at IS 'Thời điểm hoàn lại giao dịch.';
COMMENT ON COLUMN core_banking_transactions.reversal_reason IS 'Lý do hoàn lại giao dịch.';
COMMENT ON COLUMN core_banking_transactions.idempotency_key IS 'Khóa idempotency: cùng key cùng loại giao dịch chỉ được ghi một lần (chống gửi tiền trùng).';
COMMENT ON COLUMN core_banking_transactions.maker_id IS 'Người tạo giao dịch.';
COMMENT ON COLUMN core_banking_transactions.checker_id IS 'Người duyệt giao dịch (NULL nếu chưa duyệt).';
COMMENT ON COLUMN core_banking_transactions.approved_at IS 'Thời điểm duyệt giao dịch.';
COMMENT ON COLUMN core_banking_transactions.executed_at IS 'Thực hiện giao dịch (chuyển tiền thực tế).';
COMMENT ON COLUMN core_banking_transactions.reference_code IS 'Mã tham chiếu nội bộ (theo định dạng HDTD-...).';
COMMENT ON COLUMN core_banking_transactions.external_reference IS 'Mã tham chiếu từ core banking thực hoặc ngân hàng đối tác.';

-- Chỉ mục truy vấn phổ biến: theo giao dịch, khoản vay, trạng thái và thời gian
CREATE INDEX IF NOT EXISTS idx_cbt_transaction_id ON core_banking_transactions(transaction_id);
CREATE INDEX IF NOT EXISTS idx_cbt_application_id ON core_banking_transactions(application_id);
CREATE INDEX IF NOT EXISTS idx_cbt_status ON core_banking_transactions(status);
CREATE INDEX IF NOT EXISTS idx_cbt_created_at ON core_banking_transactions(created_at);

-- Bảng nhật ký kiểm tra core banking: viết lại lịch sử thay đổi trạng thái
-- của mỗi giao dịch để phục vụ kiểm toán và thanh tra ngân hàng.
CREATE TABLE IF NOT EXISTS core_banking_audit (
    id           BIGSERIAL PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES core_banking_transactions(transaction_id) ON DELETE RESTRICT,
    action       TEXT NOT NULL,                -- CREATE / APPROVE / EXECUTE / FAIL / REVERSE
    actor_id     TEXT NOT NULL,                -- người thực hiện hành động
    before_state TEXT,                         -- trạng thái giao dịch trước khi thay đổi (JSON)
    after_state  TEXT,                         -- trạng thái giao dịch sau khi thay đổi (JSON)
    timestamp    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip_address   TEXT                          -- địa chỉ IP thực hiện (hỗ trợ tra cứu vi phạm bảo mật)
);

COMMENT ON TABLE core_banking_audit IS 'Nhật ký kiểm tra toàn vòng đời giao dịch core banking (ai, khi nào, từ trạng thái gì sang trạng thái gì).';
COMMENT ON COLUMN core_banking_audit.transaction_id IS 'FK tới core_banking_transactions(transaction_id).';
COMMENT ON COLUMN core_banking_audit.action IS 'Hành động thực hiện trên giao dịch (CREATE/APPROVE/EXECUTE/FAIL/REVERSE).';
COMMENT ON COLUMN core_banking_audit.actor_id IS 'Người thực hiện hành động.';
COMMENT ON COLUMN core_banking_audit.before_state IS 'JSON trạng thái giao dịch trước khi thay đổi.';
COMMENT ON COLUMN core_banking_audit.after_state IS 'JSON trạng thái giao dịch sau khi thay đổi.';
COMMENT ON COLUMN core_banking_audit.timestamp IS 'Thời điểm ghi nhật ký (múi giờ UTC).';
COMMENT ON COLUMN core_banking_audit.ip_address IS 'IP của tác giả hành động, dùng để điều tra sự cố bảo mật.';

CREATE INDEX IF NOT EXISTS idx_cba_transaction_id ON core_banking_audit(transaction_id);
CREATE INDEX IF NOT EXISTS idx_cba_timestamp ON core_banking_audit(timestamp);
