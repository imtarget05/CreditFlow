-- ============================================================================
-- 002_outcome_tracking.sql — Theo dõi kết quả cho vay (Loan Outcome Tracking)
-- Điều kiện tiên quyết: bảng loan_applications phải đã tồn tại (xem 001).
-- ============================================================================

-- Bảng kết quả cho vay: ghi nhận kết quả thực tế của mỗi đơn vay sau khi
-- thu hồi (mạnh hay nợ xấu), phục vụ backtest mô hình rủi ro và báo cáo NPL.
CREATE TABLE IF NOT EXISTS loan_outcomes (
    id                    BIGSERIAL PRIMARY KEY,
    application_id        INTEGER NOT NULL UNIQUE REFERENCES loan_applications(id) ON DELETE RESTRICT,
    actual_default        BOOLEAN NOT NULL DEFAULT FALSE,
    default_date          DATE,
    actual_repayment_amount NUMERIC(18, 2),
    total_repaid          NUMERIC(18, 2),
    num_missed_payments   INTEGER NOT NULL DEFAULT 0,
    last_payment_date     DATE,
    outcome_status        TEXT NOT NULL CHECK (
        outcome_status IN ('PERFORMING', 'NON_PERFORMING', 'DEFAULTED', 'CHARGE_OFF')
    ) DEFAULT 'PERFORMING',
    cohort_month          TEXT NOT NULL,      -- định dạng YYYY-MM, nhóm theo tháng phát tán
    source                TEXT NOT NULL CHECK (
        source IN ('BANK_STATEMENT', 'CORE_BANKING', 'EXTERNAL')
    ),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Nếu đánh dấu default thì phải có ngày default
    CONSTRAINT ck_lo_default_consistency
        CHECK (NOT actual_default OR default_date IS NOT NULL)
);

COMMENT ON TABLE loan_outcomes IS 'Kết quả thực tế của mỗi khoản vay (default hay không), dùng để đánh giá mô hình rủi ro và báo cáo nợ xấu (NPL).';
COMMENT ON COLUMN loan_outcomes.application_id IS 'FK tới loan_applications(id), UNIQUE vì mỗi khoản vay chỉ có một kết quả.';
COMMENT ON COLUMN loan_outcomes.actual_default IS 'Có phải khách nợ xấu thực tế hay không.';
COMMENT ON COLUMN loan_outcomes.default_date IS 'Ngày xác định nợ xấu (NULL nếu chưa default).';
COMMENT ON COLUMN loan_outcomes.actual_repayment_amount IS 'Số tiền khách hàng đã thực tế trả lại.';
COMMENT ON COLUMN loan_outcomes.total_repaid IS 'Tổng số tiền đã trả (gồm gốc và lãi).';
COMMENT ON COLUMN loan_outcomes.num_missed_payments IS 'Số kỳ vay khách hàng đã bỏ nợ/trả trễ.';
COMMENT ON COLUMN loan_outcomes.last_payment_date IS 'Ngày thanh toán gần nhất.';
COMMENT ON COLUMN loan_outcomes.outcome_status IS 'PERFORMING (đang tốt) / NON_PERFORMING / DEFAULTED / CHARGE_OFF.';
COMMENT ON COLUMN loan_outcomes.cohort_month IS 'Tháng cohort theo định dạng YYYY-MM (tháng phát tán khoản vay).';
COMMENT ON COLUMN loan_outcomes.source IS 'Nguồn dữ liệu kết quả: BANK_STATEMENT / CORE_BANKING / EXTERNAL.';

CREATE INDEX IF NOT EXISTS idx_lo_cohort_month ON loan_outcomes(cohort_month);
CREATE INDEX IF NOT EXISTS idx_lo_outcome_status ON loan_outcomes(outcome_status);
CREATE INDEX IF NOT EXISTS idx_lo_application_id ON loan_outcomes(application_id);
