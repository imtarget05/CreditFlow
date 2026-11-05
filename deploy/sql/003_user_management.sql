-- ============================================================================
-- 003_user_management.sql — Quản trị vòng đời người dùng (User Lifecycle)
-- ============================================================================

-- Bảng người dùng: tài khoản nhân viên ngân hàng tham gia quy trình cho vay
-- (maker/checker/duyệt) với trạng thái và lịch sử phân quyền.
CREATE TABLE IF NOT EXISTS users (
    id                BIGSERIAL PRIMARY KEY,
    username          TEXT NOT NULL UNIQUE,
    email             TEXT NOT NULL UNIQUE,
    password_hash     TEXT NOT NULL,          -- băm bcrypt/argon2, không bao giờ lưu mật khẩu rõ ràng
    role              TEXT NOT NULL,          -- ADMIN / MAKER / CHECKER / VIEWER / AUDITOR
    status            TEXT NOT NULL CHECK (
        status IN ('ACTIVE', 'SUSPENDED', 'REVOKED')
    ) DEFAULT 'ACTIVE',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at     TIMESTAMPTZ,
    revoked_at        TIMESTAMPTZ,
    revoked_by        BIGINT REFERENCES users(id) ON DELETE SET NULL,  -- người thực hiện thu hồi
    revocation_reason TEXT                    -- lý do thu hồi (bảo mật / vi phạm / nghỉ việc)
);

COMMENT ON TABLE users IS 'Tài khoản người dùng trong hệ thống cho vay, kèm trạng thái và lịch sử phân quyền.';
COMMENT ON COLUMN users.username IS 'Tên đăng nhập duy nhất, dùng để xác thực.';
COMMENT ON COLUMN users.email IS 'Email duy nhất, dùng khôi phục tài khoản và gửi thông báo.';
COMMENT ON COLUMN users.password_hash IS 'Băm một chiều của mật khẩu (bcrypt/argon2), chưa bao giờ lưu rõ ràng.';
COMMENT ON COLUMN users.role IS 'Vai trò: ADMIN / MAKER / CHECKER / VIEWER / AUDITOR.';
COMMENT ON COLUMN users.status IS 'ACTIVE (hoạt động) / SUSPENDED (tạm khóa) / REVOKED (thu hồi vĩnh viễn).';
COMMENT ON COLUMN users.last_login_at IS 'Thời điểm đăng nhập thành công gần nhất.';
COMMENT ON COLUMN users.revoked_at IS 'Thời điểm tài khoản bị thu hồi (NULL nếu còn hoạt động).';
COMMENT ON COLUMN users.revoked_by IS 'Người thực hiện thu hồi (FK tự tham chiếu users(id)).';
COMMENT ON COLUMN users.revocation_reason IS 'Lý do thu hồi tài khoản, dùng cho báo cáo kiểm toán bảo mật.';

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

-- Bảng phiên làm việc: lưu hash token phiên và thời gian hết hạn để thu hồi phiên khi cần.
CREATE TABLE IF NOT EXISTS user_sessions (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,         -- băm một chiều của session token (JWT / refresh token)
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ                   -- NULL khi phiên còn hiệu lực
);

COMMENT ON TABLE user_sessions IS 'Phiên làm việc của người dùng; dùng token_hash để thu hồi phiên khi phát hiện vi phạm bảo mật.';
COMMENT ON COLUMN user_sessions.user_id IS 'FK tới users(id); xoá người dùng sẽ xoá toàn bộ phiên của họ.';
COMMENT ON COLUMN user_sessions.token_hash IS 'Băm một chiều của session token, không lưu token gốc.';
COMMENT ON COLUMN user_sessions.expires_at IS 'Thời điểm phiên hết hiệu lực.';
COMMENT ON COLUMN user_sessions.revoked_at IS 'Thời điểm thu hồi phiên (NULL nếu phiên còn hiệu lực).';

CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id);

-- Bảng lịch sử phân quyền: ghi lại mọi thay đổi vai trò người dùng để kiểm toán nội bộ.
CREATE TABLE IF NOT EXISTS user_role_history (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    old_role    TEXT,                          -- vai trò cũ (NULL nếu lần đầu gán vai trò)
    new_role    TEXT NOT NULL,                 -- vai trò mới sau khi thay đổi
    changed_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,  -- người thực hiện thay đổi
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason      TEXT                           -- lý do thay đổi vai trò (báo cáo kiểm toán)
);

COMMENT ON TABLE user_role_history IS 'Lịch sử thay đổi vai trò người dùng (ai thay đổi, từ vai trò gì sang vai trò gì, khi nào, vì lý do gì).';
COMMENT ON COLUMN user_role_history.user_id IS 'FK tới users(id) — người dùng được thay đổi vai trò.';
COMMENT ON COLUMN user_role_history.old_role IS 'Vai trò trước khi thay đổi (NULL nếu lần đầu gán).';
COMMENT ON COLUMN user_role_history.new_role IS 'Vai trò mới sau khi thay đổi.';
COMMENT ON COLUMN user_role_history.changed_by IS 'Người thực hiện thay đổi (FK users(id)).';
COMMENT ON COLUMN user_role_history.changed_at IS 'Thời điểm thay đổi vai trò.';
COMMENT ON COLUMN user_role_history.reason IS 'Lý do thay đổi vai trò, dùng cho báo cáo kiểm toán nội bộ.';

CREATE INDEX IF NOT EXISTS idx_user_role_history_user_id ON user_role_history(user_id);
