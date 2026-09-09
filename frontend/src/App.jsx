import React, { useEffect, useState } from "react";

const PRIMARY_BASE = (import.meta.env.VITE_API_BASE) || "/api";
const FALLBACK_BASE = "http://localhost:8080";

const APP_TITLE = "CreditFlow";
const APP_SUBTITLE = "Hỗ trợ đánh giá rủi ro khoản vay";
const API_START_HINT = "uvicorn backend.app:app --host 0.0.0.0 --port 8080";

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

function formatVND(raw) {
  const n = typeof raw === "number" ? raw : parseVND(raw);
  if (!Number.isFinite(n)) return "";
  return Math.round(n).toLocaleString("vi-VN");
}

function fmtNumber(raw) {
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n)) return String(raw ?? "—");
  return Math.round(n).toLocaleString("vi-VN");
}

// Map reason kỹ thuật -> tiếng Việt dễ hiểu cho cán bộ tín dụng.
const REASON_VI = {
  "high debt-to-income ratio": "Nợ hiện tại cao so với thu nhập",
  "high loan-to-income ratio": "Khoản vay xin mới quá lớn so với thu nhập",
  "short credit history": "Lịch sử tín dụng còn ngắn",
  "history of missed payments": "Đã từng không trả nợ trong quá khứ",
  "low overall risk profile": "Không có dấu hiệu rủi ro nổi bật",
};

function reasonVI(r) {
  return REASON_VI[r] || r;
}

function outcomeCopy(result) {
  if (!result) return null;
  if (result.decision === "APPROVE")
    return {
      title: "Nên duyệt",
      plain: "Hồ sơ này trông ổn. Khả năng khách hàng không trả được nợ là thấp.",
      next: "Tiếp tục quy trình duyệt như bình thường.",
    };
  if (result.decision === "REVIEW")
    return {
      title: "Cần xem xét thêm",
      plain: "Hồ sơ có một số điểm cần lưu ý. Chưa nên quyết ngay.",
      next: "Đề nghị bổ sung giấy tờ, kiểm tra thêm hoặc giảm số tiền vay.",
    };
  return {
    title: "Nên từ chối",
    plain: "Khả năng khách hàng không trả được nợ là cao.",
    next: "Từ chối hoặc yêu cầu tài sản đảm bảo / đồng vay.",
  };
}

const FIELDS = [
  {
    group: "1 · Thu nhập & công việc",
    note: "Cho hệ thống biết khách hàng kiếm bao nhiêu và ổn định ra sao.",
    items: [
      { key: "income", label: "Thu nhập mỗi tháng", placeholder: "Ví dụ: 8.000.000", hint: "Đồng / tháng", type: "text", money: true, min: 0 },
      { key: "age", label: "Tuổi", placeholder: "Ví dụ: 35", hint: "18–100 tuổi", type: "number", step: "1", min: 18, max: 100 },
      { key: "employment_years", label: "Số năm làm việc liên tục", placeholder: "Ví dụ: 8", hint: "năm · không vượt quá tuổi − 18", type: "number", step: "any", min: 0 },
    ],
  },
  {
    group: "2 · Khoản vay muốn xin",
    note: "Số tiền khách hàng muốn vay và tình trạng nợ hiện tại.",
    items: [
      { key: "loan_amount", label: "Số tiền muốn vay", placeholder: "Ví dụ: 120.000.000", hint: "Đồng", type: "text", money: true, min: 0 },
      { key: "loan_term", label: "Vay trong bao lâu", placeholder: "Ví dụ: 36", hint: "tháng", type: "number", step: "1", min: 1 },
      { key: "existing_debt", label: "Nợ đang có", placeholder: "Ví dụ: 15.000.000", hint: "Tổng nợ hiện tại, tính bằng đồng", type: "text", money: true, min: 0 },
    ],
  },
  {
    group: "3 · Lịch sử trả nợ",
    note: "Khách hàng đã từng vay và trả nợ như thế nào.",
    items: [
      { key: "credit_history", label: "Đã có lịch sử tín dụng bao lâu", placeholder: "Ví dụ: 9", hint: "năm · không vượt quá tuổi − 18", type: "number", step: "any", min: 0 },
      { key: "previous_defaults", label: "Đã từng không trả được nợ mấy lần", placeholder: "Ví dụ: 0", hint: "lần · 0 nghĩa là chưa từng", type: "number", step: "1", min: 0 },
    ],
  },
];

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
      kicker: "Recommendation",
      title: "Approve the loan",
      badge: "APPROVE",
      detail: "The customer repayment capacity looks good and risk of default is low. Continue the approval per internal policy.",
      checklist: [
        "Run a final credit check with the risk department",
        "Proceed with loan approval per internal rules",
      ],
    };
  }
  if (level === "MEDIUM" && decision === "REVIEW") {
    return {
      kicker: "Recommendation",
      title: "Hold — review further",
      badge: "REVIEW",
      detail: "The profile has some points to consider. Ask for more documents or discuss with the risk team before deciding.",
      checklist: [
        "Request additional documents or collateral",
        "Review the loan amount versus income again",
        "Discuss risk factors with the risk team",
      ],
    };
  }
  return {
    kicker: "Recommendation",
    title: "Consider rejecting",
    badge: "REJECT",
    detail: "The profile shows high-risk signals. Recommend not approving, or approve only with special conditions after a thorough review.",
    checklist: [
      "Document the decision and risk findings",
      "If approved, add collateral or a co-signer",
    ],
  };
}

function gutterClass(level) {
  return level === "HIGH" ? "bad" : level === "MEDIUM" ? "warn" : "good";
}

export default function App() {
  const [tab, setTab] = useState("predict");
  const [form, setForm] = useState({ ...DEFAULT_PROFILE });
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null);
  const [modelInfo, setModelInfo] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [monError, setMonError] = useState(null);
  const [fieldErrors, setFieldErrors] = useState({});
  const [modelRawExpanded, setModelRawExpanded] = useState(false);

  const apiLabel = PRIMARY_BASE === "/api" ? "proxy dev /api → :8080" : PRIMARY_BASE;
  const online = health?.status === "ok";

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

  function applyPreset(name) {
    const values = { ...PRESETS[name].values };
    setForm(values); setFieldErrors({}); setResult(null); setError(null);
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">CF</div>
          <div>
            <h1 className="brand-title">{APP_TITLE}</h1>
            <div className="subtitle">{APP_SUBTITLE}</div>
          </div>
        </div>
        <div className={`health ${online ? "ok" : "down"}`} title={JSON.stringify(health)}>
          <span className={`dot ${online ? "ok" : "down"}`} />
          {online ? `Backend đang hoạt động · ${health.model_version}` : "Backend không khả dụng"}
        </div>
      </header>

      {!online && health && (
        <div className="banner" role="alert">
          <strong>Backend không khả dụng.</strong>{" "}
          Khởi động backend trước, sau đó làm mới trang: <code>{API_START_HINT}</code>
        </div>
      )}

      <nav className="tabs" role="tablist" aria-label="Các mục CreditFlow">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "predict"}
          className={tab === "predict" ? "active" : ""}
          onClick={() => setTab("predict")}
        >
          Kiểm tra rủi ro
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "model"}
          className={tab === "model" ? "active" : ""}
          onClick={() => setTab("model")}
        >
          Model đang dùng
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "monitor"}
          className={tab === "monitor" ? "active" : ""}
          onClick={() => setTab("monitor")}
        >
          Theo dõi hệ thống
        </button>
      </nav>

      <main>
        {tab === "predict" && (
          <div className="layout predict-layout">
            <section className="card form-card" aria-labelledby="form-heading">
              <div className="card-head">
                <h2 id="form-heading">Hồ sơ khách hàng</h2>
                <div className="presets">
                  {Object.entries(PRESETS).map(([name, p]) => (
                    <button
                      key={name}
                      type="button"
                      className="chip"
                      onClick={() => applyPreset(name)}
                      title={p.subtitle}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              <p className="form-help">
                Chọn mẫu hồ sơ ở trên, hoặc điền thủ công các ô bên dưới. Sau đó nhấn{" "}
                <strong>Kiểm tra rủi ro</strong> để nhận khuyến nghị.
              </p>

              <form onSubmit={submit} noValidate className="field-groups">
                {FIELDS.map((g) => (
                  <fieldset key={g.group} className="field-group">
                    <legend className="field-group-title">{g.group}</legend>
                    <div className="grid">
                      {g.items.map((f) => (
                        <label
                          key={f.key}
                          className={`field ${fieldErrors[f.key] ? "field-invalid" : ""}`}
                        >
                          <span className="field-label">
                            {f.label}
                            <span className="field-hint">{f.hint}</span>
                          </span>
                          <input
                            type={f.type}
                            step={f.step}
                            min={f.min}
                            max={f.max}
                            value={form[f.key]}
                            placeholder={f.placeholder}
                            onChange={(e) => setField(f.key, e.target.value)}
                            aria-invalid={Boolean(fieldErrors[f.key])}
                            aria-describedby={fieldErrors[f.key] ? `${f.key}-error` : undefined}
                          />
                          {fieldErrors[f.key] && (
                            <span id={`${f.key}-error`} className="field-error">
                              {fieldErrors[f.key]}
                            </span>
                          )}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ))}

                <div className="actions">
                  {!online && !formInvalid && (
                    <span className="offline-note">Backend đang offline — vui lòng khởi động backend trước.</span>
                  )}
                  <button
                    type="submit"
                    disabled={loading || !online || formInvalid}
                  >
                    {loading ? "Đang kiểm tra…" : "Kiểm tra rủi ro"}
                  </button>
                </div>
              </form>

              {loading && (
                <div className="loading" role="status">
                  <span className="spinner" /> Đang tính toán rủi ro…
                </div>
              )}

              {error && <div className="error" role="alert">{error}</div>}
            </section>

            <aside className="card result-card" aria-labelledby="result-heading" aria-live="polite">
              <h2 id="result-heading">Kết quả đánh giá</h2>
              {!result && !loading && !error && (
                <div className="empty">
                  <div className="empty-title">Chưa có kết quả</div>
                  <p>Điền hồ sơ bên trái, sau đó nhấn <strong>Kiểm tra rủi ro</strong>. Khuyến nghị, xác suất và lý do sẽ hiện ở đây.</p>
                </div>
              )}
              {loading && (
                <div className="loading" role="status">
                  <span className="spinner" /> Đang tính toán rủi ro…
                </div>
              )}
              {error && <div className="error" role="alert">{error}</div>}
              {result && !loading && (() => {
                const prob = Number(result.risk_probability);
                const rec = recommendation(result.risk_level, result.decision);
                const toneClass =
                  result.decision === "APPROVE" ? "good"
                  : result.decision === "REJECT" ? "bad"
                  : "warn";
                return (
                  <div>
                    <div className={`result ${toneClass}`}>
                      <div className="result-kicker">{rec.kicker}</div>
                      <div className="result-title">{rec.title}</div>
                      <div className="prob-row">
                        <div className="prob">{(prob * 100).toFixed(1)}%</div>
                        <div>
                          <div className="level">{rec.badge} · {result.risk_level}</div>
                          <div className="model-line">Model {result.model_name} · {result.model_version}</div>
                        </div>
                      </div>
                      <div className="bar" aria-hidden="true">
                        <div className="fill" style={{ width: `${Math.max(0, Math.min(100, prob * 100))}%` }} />
                      </div>
                      <div className="scale">
                        <span>0% — an toàn</span>
                        <span>50% — xem xét</span>
                        <span>80% — từ chối</span>
                      </div>
                    </div>

                    <p className="note">{rec.detail}</p>

                    <div className="checklist-title">Việc cần làm tiếp theo</div>
                    <ul className="checklist">
                      {rec.checklist.map((c) => (
                        <li key={c}>{c}</li>
                      ))}
                    </ul>

                    <dl>
                      <div>
                        <dt>Xác suất rủi ro</dt>
                        <dd>{(prob * 100).toFixed(1)}%</dd>
                      </div>
                      <div>
                        <dt>Khuyến nghị</dt>
                        <dd>{result.decision} ({result.risk_level})</dd>
                      </div>
                      <div>
                        <dt>Ngưỡng áp dụng</dt>
                        <dd>Duyệt &lt; {result.threshold?.approve_max} · Xem xét &lt; {result.threshold?.review_max}</dd>
                      </div>
                      <div>
                        <dt>Model</dt>
                        <dd>{result.model_name} · {result.model_version}</dd>
                      </div>
                    </dl>

                    {result.reasons && result.reasons.length > 0 && (
                      <div className="reasons">
                        <strong>Lý do chính: </strong>
                        {result.reasons.join("; ")}
                      </div>
                    )}

                    <button
                      type="button"
                      className="ghost"
                      onClick={() => {
                        setResult(null);
                        setError(null);
                        setTab("predict");
                      }}
                    >
                      Đánh giá hồ sơ khác
                    </button>
                  </div>
                );
              })()}
            </aside>
          </div>
        )}

        {tab === "model" && (
          <section className="card model-card" aria-labelledby="model-heading">
            <div className="card-head">
              <h2 id="model-heading">Model đang dùng</h2>
              <button type="button" className="ghost" onClick={fetchModelInfo}>Làm mới</button>
            </div>
            {modelInfo?.model ? (
              <div>
                <div className="model-hero">
                  <div>
                    <div className="model-name">{modelInfo.model.model_name}</div>
                    <div className="model-version">Phiên bản {modelInfo.model.version} · production</div>
                  </div>
                  <span className="badge">production</span>
                </div>
                <p className="note">
                  Model được chọn theo <strong>chi phí nghiệp vụ thấp nhất</strong> trên tập validation
                  (bỏ sót khách vỡ nợ tốn gấp {modelInfo.model.fn_cost}/{modelInfo.model.fp_cost} lần
                  so với từ chối nhầm), không phải theo độ chính xác cao nhất.
                </p>
                <div className="kv">
                  <div><span>Ngưỡng phân loại</span><strong>{modelInfo.model.threshold}</strong></div>
                  <div><span>Chi phí nghiệp vụ</span><strong>{modelInfo.model.business_cost}</strong></div>
                  <div><span>Val F1 / Recall</span><strong>{modelInfo.model.val_metrics?.f1} / {modelInfo.model.val_metrics?.recall}</strong></div>
                  <div><span>Test F1 / Accuracy</span><strong>{modelInfo.model.test_metrics?.f1} / {modelInfo.model.test_metrics?.accuracy}</strong></div>
                  <div><span>Dữ liệu huấn luyện</span><strong>{modelInfo.model.dataset} · {fmtNumber(modelInfo.model.rows)} hồ sơ</strong></div>
                  <div><span>Ngày huấn luyện</span><strong>{modelInfo.model.trained_at || "—"}</strong></div>
                </div>
                {modelInfo.model.selection_reason && (
                  <p className="note">{modelInfo.model.selection_reason}</p>
                )}
              </div>
            ) : (
              <div className="error">Chưa lấy được thông tin model — backend có đang chạy không?</div>
            )}
          </section>
        )}

        {tab === "monitor" && (
          <section className="monitor" aria-labelledby="monitor-heading">
            <div className="card">
              <div className="card-head">
                <h2 id="monitor-heading">Tình trạng hệ thống</h2>
                <button type="button" className="ghost" onClick={fetchMetrics}>Làm mới</button>
              </div>
              {monError && <div className="error" role="alert">{monError}</div>}
              {metrics ? (
                <div className="stat-grid">
                  <div className="stat"><span>Tổng số yêu cầu</span><strong>{metrics.runtime?.requests?.total ?? "—"}</strong></div>
                  <div className="stat"><span>Lượt đánh giá</span><strong>{metrics.runtime?.requests?.predict ?? "—"}</strong></div>
                  <div className="stat"><span>Số lỗi</span><strong>{metrics.runtime?.errors?.total ?? "—"}</strong></div>
                  <div className="stat"><span>Thời gian TB</span><strong>{metrics.runtime?.avg_predict_latency_ms ?? "—"} ms</strong></div>
                  <div className="stat"><span>Đã chạy</span><strong>{metrics.runtime ? Math.round(metrics.runtime.uptime_seconds) : "—"} giây</strong></div>
                  <div className="stat"><span>Model phục vụ</span><strong>{metrics.model?.model_name ?? "—"}</strong></div>
                </div>
              ) : (
                !monError && <p className="note">Đang tải số liệu…</p>
              )}
            </div>

            <div className="card">
              <h2>So sánh các model</h2>
              <p className="note">
                Kết quả thật từ quá trình huấn luyện. Tiêu chí chọn: <strong>chi phí nghiệp vụ thấp nhất</strong> trên
                tập validation (bỏ sót khách vỡ nợ tốn gấp 5 lần so với từ chối nhầm) — không phải độ chính xác cao nhất.
              </p>
              {metrics?.benchmark && metrics.benchmark.length > 0 ? (
                <div className="table-wrap">
                  <table>
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
            <button type="button" className="primary" onClick={() => setTab("predict")}>Đánh giá hồ sơ khác</button>
          </section>
        )}

        <footer className="foot">
          CreditFlow · hệ thống hỗ trợ quyết định — kết quả mang tính tham khảo, quyết định cuối cùng thuộc về cán bộ tín dụng.
        </footer>
      </main>
    </div>
  );
}

