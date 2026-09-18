# ML correctness and business acceptance — 2026-09-17

## Status

ML/unit-contract checks, benchmark reproduction and the local test suites pass.
The explanation layer now has an enforced guard (key whitelist + numeric grounding).
**Still not revalidated here**: live-provider factual quality with real credentials and
browser/container deployment. No new model and no real bank dataset was added.

## Reproduce

From `/Users/mainguyenbinhtan/Downloads/CreditFlow`:

```bash
.venv/bin/python -m pytest tests/test_models.py tests/test_drift.py -q
.venv/bin/python -m pytest tests/ -q -m 'not slow and not integration'
.venv/bin/python scripts/audit_benchmark.py
```

Latest local results: 19 passed / **127 passed, 16 deselected, exit 0**.
The 16 deselected = 13 `slow` + 3 `integration`; they were re-run separately in an
isolated state directory and passed (**16 passed, exit 0**). A single unfiltered run of
the whole suite in that isolated environment gives **143 passed, exit 0** (48.7s), i.e.
no test is skipped, xfailed or silently dropped.

Re-run just the deselected groups in an isolated state directory:

```bash
TMP_STATE=$(mktemp -d)
CREDITFLOW_LEDGER_DB="$TMP_STATE/ledger.db" \
CREDITFLOW_CHECKPOINT_DB="$TMP_STATE/checkpoints.pkl" \
CREDITFLOW_LLM_PROVIDER='' \
.venv/bin/python -m pytest tests/ -q -m 'slow or integration' -rs
```

The `integration` group includes live `https://creditflow-api-ko2h.onrender.com`
`/health` and `/predict` calls, so that pass depends on the deployed Render service
being awake, not only on this worktree. No test hits a live LLM provider: provider
paths are exercised through monkeypatched transport.

The audit script trains the existing four models offline, locks model/threshold selection
on validation before evaluating test, and checks the committed benchmark rounded to four
decimal places. It writes only the QA report, not production artifacts or MLflow records.
Exact library versions, estimator parameters, all metrics and confusion matrices are in
`benchmark-audit.json`. No claim of bit-identical reproduction across library versions.

Dataset: **synthetic 5,000 rows**, seed 42, stratified 3,500/750/750 train/validation/test.
SHA-256: `b65728f853cad72e3ce51a639ac96c7f04e935170e79acda637b4e5eec2f5d80`.
No duplicate eight-feature profiles or cross-split identical profiles found. These checks
do not prove absence of every possible semantic leakage mechanism.
The persisted scaler has 3,500 fitted samples and mean/variance equal to train only;
predicting validation/test does not refit it. Drift reference also uses train only.

## Logistic Regression only, threshold 0.20

| Split | Precision | Recall | F1 | ROC-AUC | TN | FP | FN | TP | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Validation | 0.4420 | 0.8000 | 0.5694 | 0.8817 | 549 | 101 | 20 | 80 | 201 |
| Test | 0.4321 | 0.7000 | 0.5344 | 0.8672 | 558 | 92 | 30 | 70 | 242 |

Cost = `5*FN + FP`, hypothetical units, **not VND losses**. Binary default classification
cost is not the cost of the separate three-way APPROVE/REVIEW/REJECT workflow.
Model selection: minimum validation cost, tie-break validation F1. Test is reporting only.
The above numbers must not be attributed to every model.

XGBoost now reports validation cost **293**, F1 **0.4451**, recall **0.71**, versus older
demo documentation's 273/0.4721/0.72. Repeated runs in the recorded current environment
match the new benchmark; Logistic Regression remains the winner. The old environment
was not fully captured, so the exact cause of historical XGBoost drift is unproven.
Do not describe this as a confirmed library bug or tune to recover the old numbers.

## Serving and business evidence

- API/UI money contract: VND. Artifact training scale: thousand VND, divisor 1,000.
  The serving helper reads scale and feature order from metadata; no magnitude-based
  unit guessing or conditional rescaling. The one-million VND income/loan floor is a
  **demo input constraint**, not a way to infer an unknown currency unit. Caller must
  send VND; sufficiently large mislabelled values cannot be reliably detected.
- Four profiles match actual `/predict` responses against independent direct-pipeline
  conversion. A global `Pipeline.predict_proba` hook on one actual TestClient request
  observed one model call: `8,000,000 / 120,000,000 / 15,000,000` VND became
  `8,000 / 120,000 / 15,000`. Response: probability 0.0186, APPROVE.
  Other raw features remain unchanged. No double conversion found in this path.
- Missing/invalid API profiles are rejected by validation, and a wrong-unit profile is
  refused with a message naming the field, the contract unit and the floor:
  `POST /predict` with training-scale money returns HTTP 422
  (`income: 8000 is below the VND contract floor 1,000,000 ... training-scale values
  (nghìn VND) are not accepted`). `POST /predict/graph` answers HTTP 200 with
  `decision=REJECT` and the same explanation in `error`. Threshold 0.20 comes from
  artifact metadata; REVIEW extends to 0.80. Risk display buckets are separate.
- PSI checks cover insufficient samples, missing/all-NaN comparable data and shifted
  distributions. PSI reports distribution changes, not measured model degradation.
- Temporary SQLite test round-trips customer data, audit trail, score and disbursement;
  changing amount by 1 without updating digest is detected on read.
- Hash scope: application ID, contract code, amount formatted to two decimals, timestamp.
  No signature, external anchor, append-only enforcement or hash chain. A DB writer can
  change data and recompute the hash; other fields and sub-cent changes are not covered.
- Cloudflare timeout/exception tests use mocked transport, no live credentials. Template
  response leaves the decision state unchanged. The explanation path enforces three
  mechanically checkable properties: (0) **Policy Guard** — state classified
  `CONFIDENTIAL` (the default) never leaves the process through a public-cloud provider
  (`cloudflare`, `groq`, `openai`, `anthropic`, `google`); the explicit override is
  `CREDITFLOW_ALLOW_EXTERNAL_LLM=1` in the environment (a conscious owner opt-in, used
  for the synthetic-data demo); (1) only the four contract fields are accepted, so an
  LLM-supplied `decision` / `risk_score` / `risk_level` is dropped and can never reach
  graph state; (2) every number token in the prose must be traceable to the case context
  or to the deterministic narrative built from it, otherwise the response is refused, the
  template is served, and `fallback_reason` records why (`ungrounded_numbers`,
  `malformed_output`, `provider_error`, `provider_unavailable`).
  Scope limits, stated plainly: this is a numeric grounding gate, **not a faithfulness
  proof**. It does not verify that sentences are semantically true, a grounded number can
  still be used misleadingly, and an ambiguous token passes if any separator reading is
  grounded. The eval harness reports `guard_blocked_rate` so that can be observed when a
  key is available.

### Groq provider (added 2026-09-17, live-verified)

- `CREDITFLOW_LLM_PROVIDER=groq` + `GROQ_API_KEY` (+ optional `GROQ_MODEL`, default
  `openai/gpt-oss-20b`). Same HTTP+JSON transport as Cloudflare, no new dependency.
- Live-verified with a real key on 2026-09-17, high-risk profile (P=0.90, REJECT):
  the provider returned a Vietnamese explanation, the guard accepted it (no fabricated
  numbers, `fallback_reason` absent), and the `decision` stayed `REJECT` and out of the
  output. Provenance: `_llm=true`, `llm_model=openai/gpt-oss-20b`,
  `prompt_version=credit-explain-v1`.
- Two provider quirks found live and handled in code (with regression tests):
  1. gpt-oss rejects `response_format={"type":"json_object"}` (HTTP 400
     `json_validate_failed`) → the request uses plain mode and `_balanced_json`
     recovers the JSON object from the text;
  2. the request must carry `model` explicitly (HTTP 400 otherwise).
  This key's account cannot see `llama-3.1-8b-instant` (404), so the default is gpt-oss-20b.
- What was measured and what was not: connectivity, JSON recovery, guard acceptance and
  decision isolation were measured live **on one profile**. Factual quality across many
  profiles, latency and cost remain unmeasured; the offline tests never hit the network.


## Demo, synthetic data only

Use an isolated state directory and the existing environment/artifacts:

```bash
cd /Users/mainguyenbinhtan/Downloads/CreditFlow
DEMO_STATE=$(mktemp -d)
CREDITFLOW_LLM_PROVIDER='' \
CREDITFLOW_LEDGER_DB="$DEMO_STATE/ledger.db" \
CREDITFLOW_CHECKPOINT_DB="$DEMO_STATE/checkpoints.pkl" \
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8080
```

In a second terminal:

```bash
cd /Users/mainguyenbinhtan/Downloads/CreditFlow/frontend
npm run dev
```

Use the synthetic preset profiles in the UI. For a direct API demo:

```bash
curl -sS http://127.0.0.1:8080/predict -H 'Content-Type: application/json' \
  -d '{"income":8000000,"age":35,"employment_years":8,"loan_amount":120000000,"loan_term":36,"existing_debt":15000000,"credit_history":9,"previous_defaults":0}'
```

Frontend production build passed locally (`npm run build`, 31 modules). TestClient evidence
is not a browser E2E or live-deployment check; the browser walk-through stays a manual step.
The marked `slow`/`integration` groups were re-run in this session against an isolated
temp state directory (`mktemp -d`) with the LLM provider disabled, and passed 16/16 — never
against the shared demo ledger, checkpoint file or a live provider.
