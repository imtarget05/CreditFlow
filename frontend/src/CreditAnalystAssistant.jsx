import { useState } from "react";

const PRIMARY_BASE = import.meta.env.VITE_API_BASE || "/api";

async function request(path, options = {}) {
  const response = await fetch(`${PRIMARY_BASE}${path}`, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

export default function CreditAnalystAssistant() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [user, setUser] = useState(null);
  const [workItems, setWorkItems] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [reason, setReason] = useState("");
  const [assignee, setAssignee] = useState("");
  const [brief, setBrief] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refreshQueue(activeToken = token) {
    const headers = { Authorization: `Bearer ${activeToken}` };
    const [queue, inbox] = await Promise.all([
      request("/work-items", { headers }),
      request("/notifications", { headers }),
    ]);
    setWorkItems(queue.work_items || []);
    setNotifications(inbox.notifications || []);
  }

  async function signIn(event) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const result = await request("/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const nextToken = result.access_token;
      await refreshQueue(nextToken);
      setToken(nextToken); setUser(result.user); setPassword("");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function openBrief(id) {
    setBusy(true); setError(""); setBrief(null);
    try {
      setBrief(await request(`/work-items/${encodeURIComponent(id)}/assistant`, {
        method: "POST", headers: { Authorization: `Bearer ${token}` },
      }));
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  async function act(id, action) {
    setBusy(true); setError("");
    try {
      await request(`/work-items/${encodeURIComponent(id)}/actions`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ action, request_id: crypto.randomUUID(), reason: reason.trim(), assignee: action === "assign" ? assignee.trim() : "" }),
      });
      await refreshQueue();
      setReason("");
      if (action === "assign") setAssignee("");
    } catch (err) { setError(err.message); }
    finally { setBusy(false); }
  }

  function signOut() { setToken(""); setUser(null); setWorkItems([]); setNotifications([]); setBrief(null); setPassword(""); }

  return (
    <section className="sheet" aria-labelledby="credit-analyst-title">
      <div className="sheet-head">
        <div><h2 id="credit-analyst-title">Credit Analyst Assistant</h2>
          <p>Trợ lý đọc phiếu thẩm định, kết quả đã lưu và chính sách; không thay đổi hồ sơ hay quyết định tín dụng.</p></div>
        {token && <button type="button" className="ghost" onClick={signOut}>Đăng xuất</button>}
      </div>
      {!token ? (
        <form className="pad" onSubmit={signIn}>
          <p className="detail-note">Đăng nhập bằng tài khoản analyst được cấu hình trên backend. Phiên chỉ được giữ trong bộ nhớ tab.</p>
          <div className="assistant-login-grid">
            <label>Tên đăng nhập<input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required /></label>
            <label>Mật khẩu<input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          </div>
          <button type="submit" disabled={busy}>{busy ? "Đang đăng nhập…" : "Đăng nhập và mở hàng đợi"}</button>
        </form>
      ) : (
        <div className="pad">
          <p className="detail-note">Đang đăng nhập: {user?.name || user?.username} · {user?.role}. Dữ liệu pilot dùng hồ sơ giả.</p>
          <button type="button" className="ghost" disabled={busy} onClick={() => refreshQueue().catch((err) => setError(err.message))}>Làm mới hàng đợi</button>
          <h3>Nhắc việc quá hạn ({notifications.length})</h3>
          {notifications.length ? <ul className="assistant-queue">{notifications.map((item) => (
            <li key={item.event_id}><span>Hồ sơ {item.application_id} · hạn {new Date(item.due_at).toLocaleString("vi-VN")}</span></li>
          ))}</ul> : <p className="detail-note">Không có việc quá hạn trong phạm vi của bạn.</p>}
          <div className="assistant-action-fields">
            <label>Lý do thao tác<input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={1000} placeholder="Bắt buộc khi nhận, giao, chuyển cấp hoặc hoàn tất" /></label>
            {user?.role === "RISK_COMMITTEE_L2" && <label>Giao cho nhân sự pilot<input value={assignee} onChange={(e) => setAssignee(e.target.value)} maxLength={100} placeholder="Tên đăng nhập đã cấu hình" /></label>}
          </div>
          <h3>Phiếu được phân quyền ({workItems.length})</h3>
          {workItems.length ? <ul className="assistant-queue">{workItems.map((item) => (
            <li key={item.id}>
              <span><strong>{item.kind}</strong> · {item.application_id} · {item.status}{item.assigned_to && ` · ${item.assigned_to}`}{item.due_at && ` · hạn ${new Date(item.due_at).toLocaleString("vi-VN")}`}</span>
              <div className="assistant-actions">
                {item.assigned_role === user?.role && <button type="button" className="ghost" disabled={busy} onClick={() => openBrief(item.id)}>Tạo brief</button>}
                {item.status === "OPEN" && !item.assigned_to && item.assigned_role === user?.role && <button type="button" disabled={busy || !reason.trim()} onClick={() => act(item.id, "claim")}>Nhận việc</button>}
                {item.status === "IN_PROGRESS" && item.assigned_to === user?.username && <button type="button" disabled={busy || !reason.trim()} onClick={() => act(item.id, "complete")}>Hoàn tất</button>}
                {item.status === "IN_PROGRESS" && item.assigned_to === user?.username && <button type="button" className="ghost" disabled={busy || !reason.trim()} onClick={() => act(item.id, "request_supplement")}>Cần bổ sung</button>}
                {item.status === "WAITING_SUPPLEMENT" && item.assigned_to === user?.username && <button type="button" className="ghost" disabled={busy || !reason.trim()} onClick={() => act(item.id, "resume")}>Tiếp tục</button>}
                {item.status === "IN_PROGRESS" && item.assigned_to === user?.username && item.assigned_role === "UNDERWRITER_L1" && <button type="button" className="ghost" disabled={busy || !reason.trim()} onClick={() => act(item.id, "escalate")}>Chuyển Risk</button>}
                {user?.role === "RISK_COMMITTEE_L2" && item.status !== "DONE" && <button type="button" className="ghost" disabled={busy || !reason.trim() || !assignee.trim()} onClick={() => act(item.id, "assign")}>Giao việc</button>}
              </div>
            </li>
          ))}</ul> : <p className="detail-note">Hàng đợi hiện chưa có phiếu thuộc vai trò của bạn.</p>}
          {brief && <article className="assistant-brief" aria-live="polite">
            <h3>Hồ sơ {brief.application_id}</h3>
            <p>{brief.summary}</p>
            <h4>Dữ kiện đã lưu</h4>
            <dl className="assistant-facts">{Object.entries(brief.case_facts).map(([key, value]) => (
              <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{Array.isArray(value) ? value.join(", ") || "—" : value ?? "—"}</dd></div>
            ))}</dl>
            <h4>Yếu tố cần lưu ý</h4>
            <ul>{(brief.evidence_factors || []).map((factor, index) => <li key={`${index}-${factor}`}>{factor}</li>)}</ul>
            <p className="detail-note">
              Nguồn yếu tố: {brief.evidence_origin === "recomputed_with_current_model" ? "tính lại bằng model hiện tại" : brief.evidence_origin === "persisted_workflow" ? "dữ liệu workflow đã lưu" : "chưa có"}.
              {brief.current_model_evidence && ` Model ${brief.current_model_evidence.model_name} ${brief.current_model_evidence.model_version}; xác suất hiện tại ${(brief.current_model_evidence.risk_probability * 100).toFixed(2)}%. ${brief.current_model_evidence.matches_recorded_probability ? "Khớp" : "Khác"} xác suất lưu ban đầu.`}
            </p>
            <h4>Checklist thẩm định</h4>
            <ul>{brief.review_checklist.map((item) => <li key={item}>{item}</li>)}</ul>
            {brief.policy_sources?.length > 0 && <><h4>Tài liệu chính sách truy xuất</h4><ul>{brief.policy_sources.map((source) => <li key={source.document}><strong>{source.document}</strong>: {source.excerpt}</li>)}</ul></>}
            <p className="detail-note">{brief.limitations}</p>
          </article>}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
