import { useEffect, useState } from "react";

const PRIMARY_BASE = (import.meta.env.VITE_API_BASE) || "/api";
const FALLBACK_BASE = "http://localhost:8080";

const APP_TITLE = "CreditFlow";
const APP_SUBTITLE = "Sổ chấm rủi ro khoản vay";
const API_START_HINT = "uvicorn backend.app:app --host 0.0.0.0 --port 8080";
const HISTORY_KEY = "creditflow-history";
const HISTORY_MAX = 50;

async function fetchJson(path, options) {
  let lastErr = null;
  for (const base of [PRIMARY_BASE, FALLBACK_BASE]) {
    try {
      const r = await fetch(`${base}${path}`, options);
      if (r.status === 404 && base === PRIMARY_BASE) {
        lastErr = new Error("HTTP 404 via proxy, trying direct backend");
        continue;
      }
      return r;
    } catch (err) {
      lastErr = err;
      continue;
    }
  }
  throw lastErr || new Error("Backend unreachable");
}

// --- Helpers cho nhân viên văn phòng --------------------------------------
// VND: hiển thị "8.000.000", nhập liệu chấp nhận dấu chấm/phẩy/triệu.
function parseVND(raw) {
  if (raw === "" || raw === null || raw === undefined) return NaN;
  if (typeof raw === "number") return raw;
  let s = String(raw).trim().toLowerCase();
  // "8 triệu", "8tr", "8.5 triệu" -> 8_000_000
  const trieu = s.match(/([\d.,\s]+)\s*(tr|triệu|trieu)/);
  if (trieu) {
    const n = Number(trieu[1].replace(/[\s.]/g, "").replace(",", "."));
    return Number.isFinite(n) ? Math.round(n * 1_000_000) : NaN;
  }
  s = s.replace(/vnd|đ/gi, "").trim();
  // "8,5" nghĩa là 8.5; "8.000.000" nghĩa là 8 triệu
  if (/^\d{1,3}(\.\d{3})+(,\d+)?$/.test(s)) {
    s = s.replace(/\./g, "").replace(",", ".");
  } else if (s.includes(",") && !s.includes(".")) {
    s = s.replace(",", ".");
  } else {
    s = s.replace(/[\s,]/g, "");
  }
  const n = Number(s);
  return n;
}

function fmtNumber(raw) {
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n)) return String(raw ?? "—");
  return Math.round(n).toLocaleString("vi-VN");
}

function fmtTime(ts) {
  try {
    return new Date(ts).toLocaleString("vi-VN", { hour12: false });
  } catch {
    return String(ts);
  }
}

const FIELDS = [
  {
    group: "Thu nhập & công việc",
    note: "Khách hàng kiếm bao nhiêu và ổn định ra sao.",
    items: [
      { key: "income", label: "Thu nhập mỗi tháng", placeholder: "Ví dụ: 8.000.000", hint: "Đồng / tháng", type: "text", money: true, min: 0 },
      { key: "age", label: "Tuổi", placeholder: "Ví dụ: 35", hint: "18–100 tuổi", type: "number", step: "1", min: 18, max: 100 },
      { key: "employment_years", label: "Số năm làm việc liên tục", placeholder: "Ví dụ: 8", hint: "năm · không vượt quá tuổi − 18", type: "number", step: "any", min: 0 },
    ],
  },
  {
    group: "Khoản vay muốn xin",
    note: "Số tiền muốn vay và tình trạng nợ hiện tại.",
    items: [
      { key: "loan_amount", label: "Số tiền muốn vay", placeholder: "Ví dụ: 120.000.000", hint: "Đồng", type: "text", money: true, min: 0 },
      { key: "loan_term", label: "Vay trong bao lâu", placeholder: "Ví dụ: 36", hint: "tháng", type: "number", step: "1", min: 1 },
      { key: "existing_debt", label: "Nợ đang có", placeholder: "Ví dụ: 15.000.000", hint: "Tổng nợ hiện tại, tính bằng đồng", type: "text", money: true, min: 0 },
    ],
  },
  {
    group: "Lịch sử trả nợ",
    note: "Khách hàng đã từng vay và trả nợ như thế nào.",
    items: [
      { key: "credit_history", label: "Đã có lịch sử tín dụng bao lâu", placeholder: "Ví dụ: 9", hint: "năm · không vượt quá tuổi − 18", type: "number", step: "any", min: 0 },
      { key: "previous_defaults", label: "Đã từng không trả được nợ mấy lần", placeholder: "Ví dụ: 0", hint: "lần · 0 nghĩa là chưa từng", type: "number", step: "1", min: 0 },
    ],
  },
];

const FIELD_ORDER = FIELDS.flatMap((g) => g.items.map((f) => f.key));
const FIELD_LABEL = Object.fromEntries(FIELDS.flatMap((g) => g.items.map((f) => [f.key, f.label])));

const DEFAULT_PROFILE_VALUES = {
  income: 8000000,
  age: 35,
  employment_years: 8,
  loan_amount: 120000000,
  loan_term: 36,
  existing_debt: 15000000,
  credit_history: 9,
  previous_defaults: 0,
};

const DEFAULT_PROFILE = { ...DEFAULT_PROFILE_VALUES };

const PRESETS = {
  ideal: {
    label: "Khách hàng lý tưởng",
    subtitle: "Thu nhập ổn định, lịch sử tín dụng tốt, khoản vay nhỏ.",
    values: { income: 8000000, age: 35, employment_years: 8, loan_amount: 120000000, loan_term: 36, existing_debt: 15000000, credit_history: 9, previous_defaults: 0 },
  },
  typical: {
    label: "Khách hàng trung bình",
    subtitle: "Khách hàng phổ biến, nợ khá, lịch sử tín dụng khá.",
    values: { income: 2500000, age: 32, employment_years: 4, loan_amount: 120000000, loan_term: 36, existing_debt: 35000000, credit_history: 5, previous_defaults: 0 },
  },
  risky: {
    label: "Khách hàng rủi ro cao",
    subtitle: "Thu nhập thấp, nợ cao, lịch sử ngắn, từng không trả nợ.",
    values: { income: 1500000, age: 48, employment_years: 1, loan_amount: 600000000, loan_term: 60, existing_debt: 180000000, credit_history: 1, previous_defaults: 4 },
  },
};

function recommendation(level, decision) {
  if (level === "LOW" && decision === "APPROVE") {
    return {
      title: "Duyệt khoản vay",
      badge: "APPROVE",
      detail: "Khả năng trả nợ tốt, nguy cơ vỡ nợ thấp. Tiếp tục phê duyệt theo quy định nội bộ.",
      checklist: [
        "Đối chiếu lần cuối với bộ phận rủi ro",
        "Phê duyệt theo quy định nội bộ",
      ],
    };
  }
  if (level === "MEDIUM" && decision === "REVIEW") {
    return {
      title: "Giữ lại — xem xét thêm",
      badge: "REVIEW",
      detail: "Hồ sơ có điểm cần lưu ý. Yêu cầu thêm giấy tờ hoặc trao đổi với bộ phận rủi ro trước khi quyết.",
      checklist: [
        "Yêu cầu bổ sung giấy tờ hoặc tài sản đảm bảo",
        "Xem lại số tiền vay so với thu nhập",
        "Trao đổi yếu tố rủi ro với bộ phận rủi ro",
      ],
    };
  }
  return {
    title: "Đề xuất từ chối",
    badge: "REJECT",
    detail: "Hồ sơ có tín hiệu rủi ro cao. Đề xuất không duyệt, hoặc chỉ duyệt kèm điều kiện đặc biệt sau khi rà soát kỹ.",
    checklist: [
      "Ghi lại quyết định và điểm rủi ro vào hồ sơ",
      "Nếu duyệt: yêu cầu tài sản đảm bảo hoặc người bảo lãnh",
    ],
  };
}

function loadHistory() {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

export default function App() {
  const [tab, setTab] = useState("predict");
  const [form, setForm] = useState({ ...DEFAULT_PROFILE });
  const [result, setResult] = useState(null);
  const [submitted, setSubmitted] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null);
  const [modelInfo, setModelInfo] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [monError, setMonError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});
  const [history, setHistory] = useState(loadHistory);

  const apiLabel = PRIMARY_BASE === "/api" ? "proxy dev /api → :8080" : PRIMARY_BASE;
  const online = health?.status === "ok";

  useEffect(() => {
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, HISTORY_MAX)));
    } catch {
      /* bộ nhớ đầy hoặc bị chặn — lịch sử phiên vẫn dùng được */
    }
  }, [history]);

  function validate(values) {
    const errs = {};
    for (const g of FIELDS) {
      for (const f of g.items) {
        const raw = values[f.key];
        if (raw === "" || raw == null || raw === undefined) { errs[f.key] = "Trường này là bắt buộc"; continue; }
        const v = f.money ? parseVND(raw) : Number(raw);
        if (!Number.isFinite(v)) { errs[f.key] = "Phải là một con số"; continue; }
        if (f.min !== undefined && v < f.min) errs[f.key] = `Phải ≥ ${f.min}`;
        if (f.max !== undefined && v > f.max) errs[f.key] = `Phải ≤ ${f.max}`;
      }
    }
    const age = Number(values.age);
    if (Number.isFinite(age)) {
      const emp = Number(values.employment_years);
      if (Number.isFinite(emp) && emp > age - 18) errs.employment_years = "Số năm làm việc không được lớn hơn tuổi − 18";
      const hist = Number(values.credit_history);
      if (Number.isFinite(hist) && hist > age - 18) errs.credit_history = "Lịch sử tín dụng không được lớn hơn tuổi − 18";
    }
    return errs;
  }

  function setField(key, value) {
    setForm((f) => { const next = { ...f, [key]: value }; setFieldErrors(validate(next)); return next; });
  }

  function marchNext(key) {
    const i = FIELD_ORDER.indexOf(key);
    const next = FIELD_ORDER[i + 1];
    if (next) {
      const el = document.querySelector(`[data-fkey="${next}"]`);
      if (el) { el.focus(); el.select?.(); return; }
    }
    document.querySelector("[data-submit]")?.focus();
  }

  function applyPreset(name) {
    const values = { ...PRESETS[name].values };
    setForm(values); setFieldErrors({}); setResult(null); setSubmitted(null); setError(null);
  }

  function reloadEntry(entry) {
    setForm({ ...entry.inputs });
    setFieldErrors({});
    setResult(entry.result);
    setSubmitted({ inputs: entry.inputs, at: entry.at });
    setError(null);
    setTab("predict");
  }

  async function checkHealth() {
    try { const r = await fetchJson("/health"); setHealth(r.ok ? await r.json() : { status: `error ${r.status}` }); }
    catch { setHealth({ status: "unreachable" }); }
  }

  async function submit(e) {
    e.preventDefault();
    const errs = validate(form);
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) { setError("Vui lòng sửa các trường đánh dấu trước khi kiểm tra rủi ro."); return; }
    setLoading(true); setError(null); setResult(null);
    try {
      const payload = {};
      for (const g of FIELDS) for (const f of g.items) payload[f.key] = f.money ? parseVND(form[f.key]) : Number(form[f.key]);
      const r = await fetchJson("/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
        throw new Error(`Không thể kiểm tra rủi ro (${r.status}): ${detail}`);
      }
      setResult(body);
      setSubmitted({ inputs: { ...payload }, at: Date.now() });
      setHistory((h) => [{
        id: `${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
        at: Date.now(),
        inputs: { ...payload },
        prob: body.risk_probability,
        level: body.risk_level,
        decision: body.decision,
        model: `${body.model_name} · ${body.model_version}`,
      }, ...h].slice(0, HISTORY_MAX));
    } catch (err) {
      if (err instanceof TypeError || err.message.includes("Failed to fetch") || err.message.includes("unreachable")) setError(`Không kết nối được backend (${apiLabel}). Khởi động backend trước: ${API_START_HINT}`);
      else setError(err.message);
    } finally { setLoading(false); checkHealth(); }
  }

  async function fetchModelInfo() {
    try { const r = await fetchJson("/model/info"); setModelInfo(r.ok ? await r.json() : null); }
    catch { setModelInfo(null); }
  }

  async function fetchMetrics() {
    try {
      const r = await fetchJson("/metrics");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setMetrics(await r.json()); setMonError(null);
    } catch { setMetrics(null); setMonError(`Không kết nối được backend (${apiLabel}). Khởi động backend trước: ${API_START_HINT}`); }
  }

  const formInvalid = Object.keys(validate(form)).length > 0;

  useEffect(() => { checkHealth(); setFieldErrors(validate(DEFAULT_PROFILE)); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  useEffect(() => { if (tab === "model") fetchModelInfo(); if (tab === "monitor") fetchMetrics(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [tab]);

  const toneClass = !result ? "" : result.decision === "APPROVE" ? "good" : result.decision === "REJECT" ? "bad" : "warn";
  const rec = result && !loading ? recommendation(result.risk_level, result.decision) : null;
  const prob = result ? Number(result.risk_probability) : 0;

  return (
    <div className="app">
      <header className="cmdbar">
        <div className="cmdbar-row">
          <div className="brand">
            <div className="stamp" aria-hidden="true">CF</div>
            <div>
              <h1 className="brand-title">{APP_TITLE}</h1>
              <div className="subtitle">{APP_SUBTITLE}</div>
            </div>
          </div>
          <div className={`health ${online ? "ok" : "down"}`} title={JSON.stringify(health)}>
            <span className="dot" aria-hidden="true" />
            {online ? `Backend đang hoạt động · ${health.model_version}` : "Backend không khả dụng"}
          </div>
        </div>
        <nav className="tabs" role="tablist" aria-label="Các mục CreditFlow">
          {[
            ["predict", "Chấm rủi ro"],
            ["history", "Lịch sử"],
            ["model", "Model đang dùng"],
            ["monitor", "Theo dõi"],
          ].map(([name, label]) => (
            <button
              key={name}
              type="button"
              role="tab"
              aria-selected={tab === name}
              className={tab === name ? "active" : ""}
              onClick={() => setTab(name)}
            >
              {label}
              {name === "history" && history.length > 0 && <span className="count">{history.length}</span>}
            </button>
          ))}
        </nav>
      </header>

      {!online && health && (
        <div className="banner" role="alert">
          <strong>Backend không khả dụng.</strong>{" "}
          Khởi động backend trước, sau đó làm mới trang: <code>{API_START_HINT}</code>
        </div>
      )}

      <main>
        {tab === "predict" && (
          <div className="sheet">
            <div className="sheet-head">
              <div>
                <h2>Hồ sơ khách hàng</h2>
                <p>Chọn mẫu hồ sơ hoặc điền tay, nhấn Enter để sang ô tiếp theo.</p>
              </div>
              <div className="presets">
                {Object.entries(PRESETS).map(([name, p]) => (
                  <button key={name} type="button" className="chip" onClick={() => applyPreset(name)} title={p.subtitle}>
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="predict-layout">
              <div className="input-pane">
                <form onSubmit={submit} noValidate>
                  {FIELDS.map((g) => (
                    <section key={g.group} className="frozen-pane" aria-label={g.group}>
                      <h3 className="pane-title">{g.group}</h3>
                      <p className="pane-note">{g.note}</p>
                      <div className="field-row">
                        {g.items.map((f) => (
                          <label key={f.key} className={`field ${fieldErrors[f.key] ? "field-invalid" : ""}`}>
                            <span className="field-label">
                              {f.label}
                              <span className="field-hint">{f.hint}</span>
                            </span>
                            <input
                              data-fkey={f.key}
                              type={f.type}
                              step={f.step}
                              min={f.min}
                              max={f.max}
                              value={form[f.key]}
                              placeholder={f.placeholder}
                              onChange={(e) => setField(f.key, e.target.value)}
                              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); marchNext(f.key); } }}
                              aria-invalid={Boolean(fieldErrors[f.key])}
                              aria-describedby={fieldErrors[f.key] ? `${f.key}-error` : undefined}
                              className={f.money ? "field-nums" : undefined}
                            />
                            {fieldErrors[f.key] && (
                              <span id={`${f.key}-error`} className="field-error">
                                {fieldErrors[f.key]}
                              </span>
                            )}
                          </label>
                        ))}
                      </div>
                    </section>
                  ))}
                  <div className="actions">
                    <button type="submit" data-submit className="primary" disabled={loading || !online || formInvalid}>
                      {loading ? "Đang chấm…" : "Chấm rủi ro"}
                    </button>
                    {!online && !formInvalid && (
                      <span className="offline-note">Backend đang offline — khởi động backend trước.</span>
                    )}
                  </div>
                </form>
                {error && <div className="error" role="alert">{error}</div>}
              </div>

              <aside className="readout" aria-labelledby="result-heading" aria-live="polite">
                <h2 id="result-heading" className="pane-title">Kết quả chấm</h2>
                {loading && (
                  <div className="skel" role="status" aria-label="Đang tính toán rủi ro">
                    <span className="wide" /><span className="mid" /><span /><span className="mid" />
                  </div>
                )}
                {!result && !loading && !error && (
                  <div className="empty">
                    <div className="empty-title">Sổ đang trống</div>
                    <p>Điền hồ sơ bên trái rồi nhấn <strong>Chấm rủi ro</strong> (hoặc Enter ở ô cuối). Quyết định, xác suất và lý do sẽ đóng dấu ở đây.</p>
                  </div>
                )}
                {error && <div className="error" role="alert">{error}</div>}
                {result && !loading && rec && (
                  <div className="stamp-in">
                    <div className={`sign ${toneClass}`}>
                      <div className="sign-band">
                        <div className="sign-decision">{rec.title}</div>
                        <div className="sign-level">{rec.badge} · mức {result.risk_level}</div>
                      </div>
                      <div className="sign-body">
                        <div className="prob-big">{(prob * 100).toFixed(1)}%</div>
                        <div className="prob-cap">xác suất vỡ nợ ước tính</div>
                        <div className="meter" style={{ color: "var(--ink)" }} aria-hidden="true">
                          <div style={{ width: `${Math.max(0, Math.min(100, prob * 100))}%` }} />
                        </div>
                        <div className="meter-scale"><span>0% — an toàn</span><span>50% — xem xét</span><span>80% — từ chối</span></div>
                        <div className="model-line">Model {result.model_name} · {result.model_version}</div>
                      </div>
                    </div>

                    <p className="detail-note">{rec.detail}</p>

                    <div className="next-title">Việc cần làm tiếp theo</div>
                    <ul className="checklist">
                      {rec.checklist.map((c) => (<li key={c}>{c}</li>))}
                    </ul>

                    <table className="fact-table">
                      <tbody>
                        <tr><th>Ngưỡng áp dụng</th><td>Duyệt &lt; {result.threshold?.approve_max} · Xem xét &lt; {result.threshold?.review_max}</td></tr>
                        <tr><th>Model</th><td>{result.model_name} · {result.model_version}</td></tr>
                        {result.reasons && result.reasons.length > 0 && (
                          <tr><th>Lý do chính</th><td>{result.reasons.join("; ")}</td></tr>
                        )}
                      </tbody>
                    </table>

                    <div className="readout-actions">
                      <button type="button" className="primary" onClick={() => window.print()}>In phiếu</button>
                      <button
                        type="button"
                        className="ghost"
                        onClick={() => { setResult(null); setSubmitted(null); setError(null); }}
                      >
                        Chấm hồ sơ khác
                      </button>
                    </div>
                  </div>
                )}
              </aside>
            </div>
          </div>
        )}

        {tab === "history" && (
          <div className="sheet">
            <div className="sheet-head">
              <div>
                <h2>Lịch sử chấm</h2>
                <p>{history.length > 0 ? `${history.length} hồ sơ trong bộ nhớ trình duyệt — bấm một dòng để mở lại.` : "Sổ lưu trên trình duyệt này, tắt máy vẫn còn."}</p>
              </div>
              {history.length > 0 && (
                <button type="button" className="ghost danger-ghost" onClick={() => setHistory([])}>Xóa hết</button>
              )}
            </div>
            <div className="pad">
              {history.length > 0 ? (
                <div className="table-wrap">
                  <table className="grid">
                    <thead>
                      <tr>
                        <th>Giờ chấm</th>
                        <th>Thu nhập</th>
                        <th>Khoản vay</th>
                        <th>Xác suất</th>
                        <th>Quyết định</th>
                        <th>Model</th>
                        <th><span className="sr-only">Thao tác</span></th>
                      </tr>
                    </thead>
                    <tbody>
                      {history.map((h) => (
                        <tr key={h.id} className="rowlink" onClick={() => reloadEntry(h)} title="Bấm để mở lại hồ sơ này">
                          <td>{fmtTime(h.at)}</td>
                          <td>{fmtNumber(h.inputs.income)}</td>
                          <td>{fmtNumber(h.inputs.loan_amount)}</td>
                          <td>{(Number(h.prob) * 100).toFixed(1)}%</td>
                          <td><span className={`pill ${h.decision === "APPROVE" ? "good" : h.decision === "REJECT" ? "bad" : "warn"}`}>{h.decision}</span></td>
                          <td>{h.model}</td>
                          <td>
                            <button
                              type="button"
                              className="ghost"
                              onClick={(e) => { e.stopPropagation(); setHistory((x) => x.filter((y) => y.id !== h.id)); }}
                              aria-label={`Xóa hồ sơ lúc ${fmtTime(h.at)}`}
                            >
                              Xóa
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="empty">
                  <div className="empty-title">Chưa chấm hồ sơ nào</div>
                  <p>Sang tab <strong>Chấm rủi ro</strong>, điền một hồ sơ và nhấn Chấm — mỗi kết quả tự ghi một dòng vào sổ này.</p>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "model" && (
          <div className="sheet">
            <div className="sheet-head">
              <div>
                <h2>Model đang dùng</h2>
                <p>Chọn theo chi phí nghiệp vụ thấp nhất, không phải độ chính xác cao nhất.</p>
              </div>
              <button type="button" className="ghost" onClick={fetchModelInfo}>Làm mới</button>
            </div>
            <div className="pad">
              {modelInfo?.model ? (
                <div>
                  <div className="kv">
                    <div><span>Tên model</span><strong>{modelInfo.model.model_name}</strong></div>
                    <div><span>Phiên bản</span><strong>{modelInfo.model.version} · production</strong></div>
                    <div><span>Ngưỡng phân loại</span><strong>{modelInfo.model.threshold}</strong></div>
                    <div><span>Chi phí nghiệp vụ</span><strong>{modelInfo.model.business_cost}</strong></div>
                    <div><span>Val F1 / Recall</span><strong>{modelInfo.model.val_metrics?.f1} / {modelInfo.model.val_metrics?.recall}</strong></div>
                    <div><span>Test F1 / Accuracy</span><strong>{modelInfo.model.test_metrics?.f1} / {modelInfo.model.test_metrics?.accuracy}</strong></div>
                    <div><span>Dữ liệu huấn luyện</span><strong>{modelInfo.model.dataset} · {fmtNumber(modelInfo.model.rows)} hồ sơ</strong></div>
                    <div><span>Ngày huấn luyện</span><strong>{modelInfo.model.trained_at || "—"}</strong></div>
                    <div><span>Tỉ lệ chi phí FN/FP</span><strong>{modelInfo.model.fn_cost}/{modelInfo.model.fp_cost}</strong></div>
                  </div>
                  <p className="detail-note">
                    Bỏ sót khách vỡ nợ tốn gấp {modelInfo.model.fn_cost}/{modelInfo.model.fp_cost} lần
                    so với từ chối nhầm — vì vậy model thắng bằng recall/F1, không bằng accuracy.
                  </p>
                  {modelInfo.model.selection_reason && (
                    <p className="detail-note">{modelInfo.model.selection_reason}</p>
                  )}
                </div>
              ) : (
                <div className="error">Chưa lấy được thông tin model — backend có đang chạy không?</div>
              )}
            </div>
          </div>
        )}

        {tab === "monitor" && (
          <div className="sheet">
            <div className="sheet-head">
              <div>
                <h2>Theo dõi hệ thống</h2>
                <p>Số liệu vận hành và so sánh model từ lần huấn luyện thật.</p>
              </div>
              <button type="button" className="ghost" onClick={fetchMetrics}>Làm mới</button>
            </div>
            <div className="pad">
              {monError && <div className="error" role="alert">{monError}</div>}
              {metrics ? (
                <div className="stat-grid">
                  <div className="stat"><span>Tổng yêu cầu</span><strong>{metrics.runtime?.requests?.total ?? "—"}</strong></div>
                  <div className="stat"><span>Lượt chấm</span><strong>{metrics.runtime?.requests?.predict ?? "—"}</strong></div>
                  <div className="stat"><span>Số lỗi</span><strong>{metrics.runtime?.errors?.total ?? "—"}</strong></div>
                  <div className="stat"><span>TB mỗi lượt</span><strong>{metrics.runtime?.avg_predict_latency_ms ?? "—"} ms</strong></div>
                  <div className="stat"><span>Đã chạy</span><strong>{metrics.runtime ? Math.round(metrics.runtime.uptime_seconds) : "—"} giây</strong></div>
                  <div className="stat"><span>Model phục vụ</span><strong>{metrics.model?.model_name ?? "—"}</strong></div>
                </div>
              ) : (
                !monError && <p className="detail-note">Đang tải số liệu…</p>
              )}
              <div className="next-title">So sánh các model</div>
              {metrics?.benchmark && metrics.benchmark.length > 0 ? (
                <div className="table-wrap">
                  <table className="grid">
                    <thead>
                      <tr>
                        <th>Model</th>
                        <th>Ngưỡng</th>
                        <th>Chi phí</th>
                        <th>Val F1</th>
                        <th>Val Recall</th>
                        <th>Test F1</th>
                        <th>Test Acc</th>
                      </tr>
                    </thead>
                    <tbody>
                      {metrics.benchmark.map((m) => (
                        <tr key={m.model} className={metrics.model?.model_name === m.model ? "current" : ""}>
                          <td>{m.model}{metrics.model?.model_name === m.model ? " ●" : ""}</td>
                          <td>{m.best_threshold}</td>
                          <td>{m.business_cost}</td>
                          <td>{m.val_f1}</td>
                          <td>{m.val_recall}</td>
                          <td>{m.test_f1}</td>
                          <td>{m.test_accuracy}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="error">Chưa có dữ liệu benchmark — hãy mở tab này sau khi backend phục vụ /metrics.</div>
              )}
            </div>
          </div>
        )}

        <footer className="foot">
          CreditFlow · hệ thống hỗ trợ quyết định — kết quả mang tính tham khảo (dữ liệu mô phỏng),
          quyết định cuối cùng thuộc về cán bộ tín dụng.
        </footer>
      </main>

      <div className="statusbar" aria-hidden="true">
        <span>Sổ: {history.length} hồ sơ</span>
        <span>Model {health?.model_version ?? "—"}</span>
        <span className={online ? "ok" : "bad"}>{online ? "Backend hoạt động" : "Backend offline"}</span>
      </div>

      {result && submitted && rec && (
        <div className="slip" aria-hidden="true">
          <div className="slip-head">
            <h1>Phiếu đánh giá rủi ro tín dụng</h1>
            <p>CreditFlow · {fmtTime(submitted.at)} · Model {result.model_name} · {result.model_version} (mô phỏng)</p>
          </div>
          <table>
            <tbody>
              {FIELD_ORDER.map((k) => (
                <tr key={k}>
                  <th>{FIELD_LABEL[k]}</th>
                  <td>{fmtNumber(submitted.inputs[k])}</td>
                </tr>
              ))}
              <tr><th>Xác suất vỡ nợ</th><td>{(prob * 100).toFixed(1)}%</td></tr>
              <tr><th>Lý do chính</th><td>{(result.reasons || []).join("; ")}</td></tr>
              <tr><th>Ngưỡng áp dụng</th><td>Duyệt &lt; {result.threshold?.approve_max} · Xem xét &lt; {result.threshold?.review_max} · tuned {result.threshold?.tuned_threshold}</td></tr>
            </tbody>
          </table>
          <div className="slip-decision">{rec.badge} — {rec.title}</div>
          <p>{rec.detail}</p>
          <div className="slip-sign">
            <div>Cán bộ tín dụng<br /><br />..............................</div>
            <div>Trưởng phòng<br /><br />..............................</div>
          </div>
          <p className="slip-foot">Kết quả mang tính tham khảo từ dữ liệu mô phỏng; quyết định cuối cùng thuộc về cán bộ tín dụng.</p>
        </div>
      )}
    </div>
  );
}
