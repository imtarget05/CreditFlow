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
      { key: "income", label: "Thu nhập mỗi tháng", placeholder: "Ví dụ: 8.000.000", hint: "Đồng / tháng · tối thiểu 1.000.000", type: "text", money: true, min: 1000000, minLabel: "1.000.000đ (đơn vị đồng)" },
      { key: "age", label: "Tuổi", placeholder: "Ví dụ: 35", hint: "18–100 tuổi", type: "number", step: "1", min: 18, max: 100 },
      { key: "employment_years", label: "Số năm làm việc liên tục", placeholder: "Ví dụ: 8", hint: "năm · không vượt quá tuổi − 18", type: "number", step: "any", min: 0 },
    ],
  },
  {
    group: "Khoản vay muốn xin",
    note: "Số tiền muốn vay và tình trạng nợ hiện tại.",
    items: [
      { key: "loan_amount", label: "Số tiền muốn vay", placeholder: "Ví dụ: 120.000.000", hint: "Đồng · tối thiểu 1.000.000", type: "text", money: true, min: 1000000, minLabel: "1.000.000đ (đơn vị đồng)" },
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
  if (decision === "APPROVE") {
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
  if (decision === "REVIEW") {
    return {
      title: "Giữ lại — xem xét thêm",
      badge: "REVIEW",
      detail: level === "LOW"
        ? "Mức rủi ro mô hình thấp nhưng vượt ngưỡng an toàn tự động hoặc có cờ kiểm soát. Yêu cầu thẩm định viên rà soát."
        : "Hồ sơ có điểm cần lưu ý. Yêu cầu thêm giấy tờ hoặc trao đổi với bộ phận rủi ro trước khi quyết.",
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

const MODEL_SEEN_KEY = "creditflow-model-seen";

function loadModelSeen() {
  try {
    const raw = localStorage.getItem(MODEL_SEEN_KEY);
    const o = raw ? JSON.parse(raw) : null;
    return o && typeof o === "object" ? o : {};
  } catch {
    return {};
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
  const [updateNote, setUpdateNote] = useState(null);
  const [approving, setApproving] = useState(false);
  const [ledgerApps, setLedgerApps] = useState([]);
  const [ledgerDisbursements, setLedgerDisbursements] = useState([]);

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
        if (f.min !== undefined && v < f.min) errs[f.key] = f.minLabel ? `Phải ≥ ${f.minLabel}` : `Phải ≥ ${f.min}`;
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

  async function handleHumanDecision(action) {
    if (!result?.thread_id) return;
    setApproving(true);
    try {
      const r = await fetchJson(`/predict/graph/${result.thread_id}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: `Thẩm định viên phê duyệt: ${action}` }),
      });
      if (!r.ok) throw new Error(`Lỗi duyệt (${r.status})`);
      const data = await r.json();
      setResult((prev) => ({
        ...prev,
        ...data,
        risk_probability: data.risk_score ?? prev.risk_probability,
        decision: data.decision,
        approval_required: false,
        approval_status: data.approval_status,
        disbursement: data.disbursement,
      }));
      setHistory((h) => [{
        id: `${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
        at: Date.now(),
        inputs: { ...submitted?.inputs },
        prob: data.risk_score ?? result.risk_probability,
        level: data.risk_level ?? result.risk_level,
        decision: data.decision,
        model: `${result.model_name} · ${result.model_version} (${action.toUpperCase()})`,
      }, ...h].slice(0, HISTORY_MAX));
    } catch (err) {
      setError(`Không thể gửi quyết định: ${err.message}`);
    } finally {
      setApproving(false);
    }
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

      let body = null;
      // Try LangGraph workflow first (HITL + Ledger + Explain)
      try {
        const rGraph = await fetchJson("/predict/graph", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ customer_data: payload }),
        });
        if (rGraph.ok) {
          const gBody = await rGraph.json();
          if (gBody.error) {
            // Workflow rejected the profile: show the explicit validation message
            // (e.g. money-unit contract violation) instead of a silent REJECT.
            throw new Error(`Hồ sơ không hợp lệ: ${gBody.error}`);
          }
          body = {
            risk_probability: gBody.risk_score ?? 0.0,
            risk_level: gBody.risk_level ?? "UNKNOWN",
            decision: gBody.decision ?? "UNKNOWN",
            model_name: health?.model_name || "CreditFlow-Graph",
            model_version: health?.model_version || "v1",
            thread_id: gBody.thread_id,
            application_id: gBody.application_id,
            approval_required: gBody.approval_required,
            approval_status: gBody.approval_status,
            explanation: gBody.explanation,
            audit_trail: gBody.audit_trail,
            ledger_application_id: gBody.ledger_application_id,
            reasons: gBody.reasons && gBody.reasons.length
              ? gBody.reasons
              : (gBody.audit_trail || [])
                  .filter((a) => a.step === "risk_model" && a.detail)
                  .map((a) => a.detail),
            threshold: {
              tuned_threshold: gBody.tuned_threshold ?? null,
              approve_max: 0.5,
              review_max: 0.8,
            },
            basel_metrics: gBody.basel_metrics || {},
            pricing: gBody.pricing || {},
            cic_report: gBody.cic_report || {},
            bank_statement: gBody.bank_statement || {},
            authority_level: gBody.authority_level || "STP",
            amortization_schedule: gBody.amortization_schedule || [],
            vietqr_url: gBody.vietqr_url || "",
            loan_agreement_pdf: gBody.loan_agreement_pdf || "",
          };
        }
      } catch {
        /* fallback to /predict */
      }

      if (!body) {
        const r = await fetchJson("/predict", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
        const pBody = await r.json().catch(() => ({}));
        if (!r.ok) {
          const detail = typeof pBody.detail === "string" ? pBody.detail : JSON.stringify(pBody.detail || pBody);
          throw new Error(`Không thể kiểm tra rủi ro (${r.status}): ${detail}`);
        }
        body = pBody;
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
    try {
      const r = await fetchJson("/model/info");
      const info = r.ok ? await r.json() : null;
      setModelInfo(info);
      const m = info?.model;
      if (m?.trained_at) {
        const seen = loadModelSeen();
        if (seen.trained_at && seen.trained_at !== m.trained_at) {
          setUpdateNote({
            version: m.version,
            trained_at: m.trained_at,
            reason: m.update_reason || "—",
            previous_trained_at: m.previous_trained_at || "—",
          });
        } else if (!seen.trained_at) {
          try { localStorage.setItem(MODEL_SEEN_KEY, JSON.stringify({ version: m.version, trained_at: m.trained_at })); } catch { /* bỏ qua */ }
        }
      }
    } catch { setModelInfo(null); }
  }

  function dismissUpdateNote() {
    const m = modelInfo?.model;
    try {
      if (m?.trained_at) localStorage.setItem(MODEL_SEEN_KEY, JSON.stringify({ version: m.version, trained_at: m.trained_at }));
    } catch { /* bỏ qua */ }
    setUpdateNote(null);
  }

  async function fetchMetrics() {
    try {
      const r = await fetchJson("/metrics");
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setMetrics(await r.json()); setMonError(null);
    } catch { setMetrics(null); setMonError(`Không kết nối được backend (${apiLabel}). Khởi động backend trước: ${API_START_HINT}`); }
  }

  async function fetchLedger() {
    try {
      const [rApps, rDisb] = await Promise.all([
        fetchJson("/applications"),
        fetchJson("/disbursements"),
      ]);
      if (rApps.ok) {
        const data = await rApps.json();
        setLedgerApps(data.applications || []);
      }
      if (rDisb.ok) {
        const data = await rDisb.json();
        setLedgerDisbursements(data.disbursements || []);
      }
    } catch (err) {
      console.error("fetchLedger error:", err);
    }
  }

  const formInvalid = Object.keys(validate(form)).length > 0;

  useEffect(() => {
    checkHealth();
    setFieldErrors(validate(DEFAULT_PROFILE));
    const t = setInterval(checkHealth, 15000);
    return () => clearInterval(t);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, []);
  useEffect(() => {
    if (tab === "model") fetchModelInfo();
    if (tab === "monitor") fetchMetrics();
    if (tab === "ledger") fetchLedger();
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [tab]);

  const toneClass = !result ? "" : result.decision === "APPROVE" ? "good" : result.decision === "REJECT" ? "bad" : "warn";
  const rec = result && !loading ? recommendation(result.risk_level, result.decision) : null;
  const prob = result ? Number(result.risk_probability) : 0;

  // Số phiếu in: CF-YYYYMMDD-xxxxx (ngày địa phương + phần nghìn ms của lần chấm).
  let slipNo = null;
  if (submitted) {
    const d = new Date(submitted.at);
    const p2 = (n) => String(n).padStart(2, "0");
    slipNo = `CF-${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${String(submitted.at % 100000).padStart(5, "0")}`;
  }

  return (
    <>
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
          <div className={`health ${online ? "ok" : "down"}`} title={online ? `Backend đang hoạt động · ${health.model_version}` : "Backend không khả dụng"}>
            <span className="dot" aria-hidden="true" />
            {online ? health.model_version : "Backend offline"}
          </div>
        </div>
        <nav className="tabs" role="tablist" aria-label="Các mục CreditFlow">
          {[
            ["predict", "Chấm rủi ro"],
            ["history", "Lịch sử"],
            ["ledger", "Sổ cái tín dụng"],
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
                        <tr><th>Ngưỡng áp dụng</th><td>Duyệt khi P &lt; {result.threshold?.tuned_threshold ?? result.threshold?.approve_max} · Xem xét khi P &lt; {result.threshold?.review_max} (tuned threshold học từ validation)</td></tr>
                        <tr><th>Model</th><td>{result.model_name} · {result.model_version}</td></tr>
                        {result.reasons && result.reasons.length > 0 && (
                          <tr><th>Lý do chính</th><td>{result.reasons.join("; ")}</td></tr>
                        )}
                        {result.authority_level && (
                          <tr><th>Thẩm quyền phê duyệt</th><td>{typeof result.authority_level === "string" ? result.authority_level : result.authority_level?.role}</td></tr>
                        )}
                        {result.cic_report && Object.keys(result.cic_report).length > 0 && (
                          <tr><th>CIC</th><td>Điểm {result.cic_report.cic_score} · Nhóm {result.cic_report.bad_debt_group}{result.cic_report.legal_warning ? ` — ${result.cic_report.legal_warning}` : ""}</td></tr>
                        )}
                        {result.bank_statement && Object.keys(result.bank_statement).length > 0 && (
                          <tr><th>Sao kê</th><td>Thu nhập xác minh {fmtNumber(result.bank_statement.verified_average_salary)} · {result.bank_statement.analyst_summary}</td></tr>
                        )}
                        {result.basel_metrics && Object.keys(result.basel_metrics).length > 0 && (
                          <tr><th>Basel (PD/LGD/EAD)</th><td>{result.basel_metrics.pd} / {result.basel_metrics.lgd} / {fmtNumber(result.basel_metrics.ead)} · EL {fmtNumber(result.basel_metrics.expected_loss)} · RWA {fmtNumber(result.basel_metrics.rwa)} · Xếp hạng {result.basel_metrics.rating_grade}</td></tr>
                        )}
                        {result.pricing && Object.keys(result.pricing).length > 0 && (
                          <tr><th>Định giá</th><td>Lãi suất {((result.pricing.recommended_annual_rate || 0) * 100).toFixed(2)}%/năm · Hạn mức an toàn {fmtNumber(result.pricing.max_safe_credit_limit)}</td></tr>
                        )}
                        {result.vietqr_url && (
                          <tr><th>Giải ngân VietQR</th><td><a href={result.vietqr_url} target="_blank" rel="noreferrer">Mở mã VietQR giải ngân</a></td></tr>
                        )}
                      </tbody>
                    </table>

                    {result.explanation && (
                      <div style={{ marginTop: "14px", padding: "12px 14px", background: "var(--sheet-2)", borderRadius: "var(--radius)", border: "1px solid var(--rule)" }}>
                        <div className="pane-title" style={{ marginBottom: "6px" }}>Giải trình AI & Thẩm định quy định</div>
                        <p style={{ margin: 0, fontSize: "13.5px", lineHeight: "1.6", whiteSpace: "pre-wrap" }}>{result.explanation}</p>
                      </div>
                    )}

                    {result.disbursement && (
                      <div className="banner" style={{ marginTop: "14px", border: "2px solid var(--good)", background: "var(--good-wash)" }}>
                        <strong style={{ color: "var(--good)" }}>✓ Đã kích hoạt giải ngân tự động (Sổ cái)</strong>
                        <div style={{ fontSize: "13.5px", marginTop: "4px" }}>
                          Mã hợp đồng: <strong>{result.disbursement.contract_code || result.disbursement.disbursement_id}</strong><br />
                          Số tiền giải ngân: <strong>{fmtNumber(result.disbursement.loan_amount || result.disbursement.amount)} VND</strong> · Trạng thái: <strong>{result.disbursement.status}</strong>
                        </div>
                      </div>
                    )}

                    {result.approval_required && result.decision === "REVIEW" && (
                      <div className="banner" style={{ marginTop: "14px", border: "2px solid var(--warn)", background: "var(--warn-wash)" }}>
                        <strong>Yêu cầu phê duyệt cấp quản lý (HITL)</strong>
                        <p style={{ margin: "6px 0 10px", fontSize: "13.5px" }}>
                          Hồ sơ thuộc diện rà soát theo chính sách rủi ro. Cán bộ thẩm định đưa ra quyết định:
                        </p>
                        <div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
                          <button
                            type="button"
                            className="primary"
                            style={{ background: "var(--good)", borderColor: "var(--good)", padding: "8px 16px" }}
                            disabled={approving}
                            onClick={() => handleHumanDecision("approve")}
                          >
                            {approving ? "Đang xử lý…" : "✓ Phê duyệt cho vay"}
                          </button>
                          <button
                            type="button"
                            className="primary"
                            style={{ background: "var(--bad)", borderColor: "var(--bad)", padding: "8px 16px" }}
                            disabled={approving}
                            onClick={() => handleHumanDecision("reject")}
                          >
                            {approving ? "Đang xử lý…" : "✕ Từ chối cho vay"}
                          </button>
                        </div>
                      </div>
                    )}

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

        {tab === "ledger" && (
          <div className="sheet">
            <div className="sheet-head">
              <div>
                <h2>Sổ cái tín dụng &amp; Giải ngân (Durable Ledger)</h2>
                <p>Hồ sơ và lệnh giải ngân được lưu bền trong SQLite. Mỗi lệnh có hash SHA-256 tamper-evident: sửa dữ liệu đã hash sẽ bị phát hiện khi đọc lại — không phải bảo đảm bất biến.</p>
              </div>
              <button type="button" className="ghost" onClick={fetchLedger}>Làm mới sổ cái</button>
            </div>
            <div className="pad">
              <div className="next-title">Sổ giải ngân ({ledgerDisbursements.length} hợp đồng)</div>
              <p className="detail-note">Các khoản vay đã qua thẩm định phê duyệt và ký quỹ giải ngân thành công.</p>
              {ledgerDisbursements.length > 0 ? (
                <div className="table-wrap" style={{ marginBottom: "28px" }}>
                  <table className="grid">
                    <thead>
                      <tr>
                        <th>Mã hợp đồng</th>
                        <th>Hồ sơ gốc</th>
                        <th>Số tiền</th>
                        <th>Trạng thái</th>
                        <th>Mã băm kiểm toán (Ledger Hash)</th>
                        <th>Thời gian giải ngân</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ledgerDisbursements.map((d) => (
                        <tr key={d.id || d.contract_code}>
                          <td><strong>{d.contract_code}</strong></td>
                          <td>#{d.application_id}</td>
                          <td><strong>{fmtNumber(d.loan_amount)} VND</strong></td>
                          <td><span className={`pill ${d.status === "COMPLETED" || d.status === "DISBURSED" ? "good" : "warn"}`}>{d.status}</span></td>
                          <td><code>{d.ledger_hash ? `${d.ledger_hash.slice(0, 16)}…` : "—"}</code></td>
                          <td>{fmtTime(d.disbursed_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="detail-note" style={{ marginBottom: "28px" }}>Chưa có hợp đồng nào được giải ngân trong sổ cái.</p>
              )}

              <div className="next-title">Sổ hồ sơ vay ({ledgerApps.length} hồ sơ)</div>
              <p className="detail-note">Hồ sơ thẩm định được ghi nhận trong cơ sở dữ liệu bền vững.</p>
              {ledgerApps.length > 0 ? (
                <div className="table-wrap">
                  <table className="grid">
                    <thead>
                      <tr>
                        <th>Mã hồ sơ (ID)</th>
                        <th>Khoản vay</th>
                        <th>Thu nhập</th>
                        <th>Xác suất</th>
                        <th>Mức rủi ro</th>
                        <th>Quyết định</th>
                        <th>Trạng thái</th>
                        <th>Thời gian</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ledgerApps.map((a) => (
                        <tr key={a.id || a.thread_id}>
                          <td><code>{(a.application_id || a.thread_id || String(a.id)).slice(0, 14)}…</code></td>
                          <td>{fmtNumber(a.customer_data?.loan_amount ?? a.loan_amount)}</td>
                          <td>{fmtNumber(a.customer_data?.income ?? a.monthly_income)}</td>
                          <td>{a.risk_score != null ? `${(Number(a.risk_score) * 100).toFixed(1)}%` : "—"}</td>
                          <td>{a.risk_level || "—"}</td>
                          <td>
                            <span className={`pill ${a.decision === "APPROVE" ? "good" : a.decision === "REJECT" ? "bad" : "warn"}`}>
                              {a.decision || "PENDING"}
                            </span>
                          </td>
                          <td>{a.status}</td>
                          <td>{fmtTime(a.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="detail-note">Chưa có hồ sơ nào trong sổ cái cơ sở dữ liệu.</p>
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
                    <strong>Vì sao model này thắng:</strong> chi phí nghiệp vụ thấp nhất
                    ({modelInfo.model.business_cost}) — bỏ sót khách vỡ nợ (×{modelInfo.model.fn_cost}) đắt hơn
                    từ chối nhầm (×{modelInfo.model.fp_cost}). Không chọn theo accuracy.
                  </p>
                  {modelInfo.model.selection_reason && (
                    <details className="detail-note">
                      <summary>Lý do gốc (tiếng Anh)</summary>
                      {modelInfo.model.selection_reason}
                    </details>
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
              {updateNote && (
                <div className="banner" role="status">
                  <strong>Model vừa cập nhật (mô phỏng): {updateNote.version}</strong>{" "}
                  lúc {fmtTime(updateNote.trained_at)} — bản trước {fmtTime(updateNote.previous_trained_at)}.{" "}
                  <button type="button" className="ghost" onClick={dismissUpdateNote}>Đã rõ</button>
                </div>
              )}
              {modelInfo?.model?.update_reason && (
                <p className="detail-note"><strong>Ghi chú lần cập nhật:</strong> {modelInfo.model.update_reason}</p>
              )}
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
              <p className="detail-note">Chọn theo <strong>chi phí thấp nhất</strong> (bỏ sót ×5) — dòng ● là model đang chạy.</p>
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
    </div>

    {/* Phiếu in nằm NGOÀI .app: @media print ẩn .app bằng display:none, mà con của
        phần tử ẩn thì không thể hiển thị lại — trước đây khiến bản in trắng trang. */}
    {result && submitted && rec && (
      <div className="slip" aria-hidden="true">
        <div className="slip-head">
          <h1>Phiếu đánh giá rủi ro tín dụng</h1>
          <p>Số phiếu {slipNo} · CreditFlow · {fmtTime(submitted.at)} · Model {result.model_name} · {result.model_version} (mô phỏng)</p>
        </div>
        <table>
          <tbody>
            <tr><th>Số phiếu</th><td>{slipNo}</td></tr>
            {FIELD_ORDER.map((k) => (
              <tr key={k}>
                <th>{FIELD_LABEL[k]}</th>
                <td>{fmtNumber(submitted.inputs[k])}</td>
              </tr>
            ))}
            <tr><th>Xác suất vỡ nợ</th><td>{(prob * 100).toFixed(1)}%</td></tr>
            <tr><th>Mức rủi ro</th><td>{result.risk_level}</td></tr>
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
    </>
  );
}
