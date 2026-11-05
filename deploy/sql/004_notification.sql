-- ============================================================================
-- 004_notification.sql — Gửi thông báo (Notification Delivery)
-- ============================================================================

-- Bảng thông báo: ghi chép từng thông báo gửi tới khách hàng/nhân viên qua
-- email, SMS và thông báo trong ứng dụng, cùng trạng thái gửi và số lần thử lại.
CREATE TABLE IF NOT EXISTS notifications (
    id              BIGSERIAL PRIMARY KEY,
    notification_id TEXT NOT NULL UNIQUE,     -- mã thông báo duy nhất, dùng để tránh gửi trùng khi retry
    recipient_id    TEXT NOT NULL,            -- id người nhận (user_id hoặc mã nhóm/vai trò)
    recipient_type  TEXT NOT NULL CHECK (
        recipient_type IN ('USER', 'ROLE', 'GROUP')
    ),
    channel         TEXT NOT NULL CHECK (
        channel IN ('EMAIL', 'SMS', 'IN_APP')
    ),
    subject         TEXT,
    content         TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (
        status IN ('PENDING', 'SENT', 'DELIVERED', 'FAILED')
    ) DEFAULT 'PENDING',
    sent_at         TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    error_message   TEXT,                     -- lý do gửi thất bại (dùng để tra cứu và xử lý lại)
    error_message_encrypted TEXT, -- mã hóa lưu trữ (pgcrypto), PKI/FDE key từ env
    retry_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE notifications IS 'Ghi chép từng thông báo gửi (email/SMS/in-app) cùng trạng thái gửi và số lần thử lại.';
COMMENT ON COLUMN notifications.notification_id IS 'Mã thông báo duy nhất, dùng để tránh gửi trùng khi retry.';
COMMENT ON COLUMN notifications.recipient_id IS 'ID người nhận (user_id hoặc mã nhóm/vai trò).';
COMMENT ON COLUMN notifications.recipient_type IS 'USER (cá nhân) / ROLE (theo vai trò) / GROUP (theo nhóm).';
COMMENT ON COLUMN notifications.channel IS 'Kênh gửi: EMAIL / SMS / IN_APP.';
COMMENT ON COLUMN notifications.subject IS 'Tiêu đề thông báo (có thể NULL đối với SMS).';
COMMENT ON COLUMN notifications.content IS 'Nội dung thông báo.';
COMMENT ON COLUMN notifications.status IS 'PENDING (đang chờ gửi) / SENT (đã gửi) / DELIVERED (đã đến) / FAILED (gửi thất bại).';
COMMENT ON COLUMN notifications.sent_at IS 'Thời điểm gửi thông báo.';
COMMENT ON COLUMN notifications.delivered_at IS 'Thời điểm thông báo xác nhận đã đến người nhận.';
COMMENT ON COLUMN notifications.error_message IS 'Lý do gửi thất bại, dùng để tra cứu và xử lý lại.';
COMMENT ON COLUMN notifications.retry_count IS 'Số lần đã thử lại sau khi gửi thất bại.';

CREATE INDEX IF NOT EXISTS idx_notif_recipient_id ON notifications(recipient_id);
CREATE INDEX IF NOT EXISTS idx_notif_status ON notifications(status);
CREATE INDEX IF NOT EXISTS idx_notif_created_at ON notifications(created_at);

-- Bảng mẫu thông báo: nội dung chuẩn hóa theo kênh, hỗ trợ biến số tuỳ chỉnh.
CREATE TABLE IF NOT EXISTS notification_templates (
    id                BIGSERIAL PRIMARY KEY,
    template_code     TEXT NOT NULL UNIQUE,   -- mã mẫu (WELCOME_EMAIL, OTP_SMS, LOAN_APPROVED, ...)
    channel           TEXT NOT NULL CHECK (
        channel IN ('EMAIL', 'SMS', 'IN_APP')
    ),
    subject_template  TEXT,                   -- tiêu đề mẫu (có thể NULL cho SMS)
    content_template  TEXT NOT NULL,          -- nội dung mẫu chứa biến {{variable}}
    variables         JSONB NOT NULL DEFAULT '[]'::jsonb,  -- danh sách biến bắt buộc trong mẫu
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE notification_templates IS 'Mẫu nội dung thông báo chuẩn hóa theo kênh, dùng biến {{variable}} để cá nhân hóa mỗi thông báo.';
COMMENT ON COLUMN notification_templates.template_code IS 'Mã mẫu duy nhất (WELCOME_EMAIL, OTP_SMS, LOAN_APPROVED, ...).';
COMMENT ON COLUMN notification_templates.channel IS 'Kênh gửi mẫu: EMAIL / SMS / IN_APP.';
COMMENT ON COLUMN notification_templates.subject_template IS 'Tiêu đề mẫu (có thể NULL đối với kênh SMS).';
COMMENT ON COLUMN notification_templates.content_template IS 'Nội dung mẫu chứa biến {{variable}} để thay thế khi gửi.';
COMMENT ON COLUMN notification_templates.variables IS 'Danh sách tên biến bắt buộc trong mẫu (JSON mảng chuỗi).';

CREATE INDEX IF NOT EXISTS idx_notif_templates_channel ON notification_templates(channel);
