#!/usr/bin/env python3
"""Production smoke — CreditFlow (stdlib only; safe for CI runners).

Read-only by default:
  1. GET /health/live, /health/ready (fallback /health)
  2. GET /model/info — model bundle identity
  3. OPTIONS preflight from the Pages origin — CORS allow-list
  4. GET /applications without key -> 401 (fail-closed auth gate)
  5. POST /predict with a valid SMOKE payload (unauthenticated intake by design)
Optional (export CREDITFLOW_SMOKE_API_KEY): protected reads with the shared key.

Never prints secrets. Exit 0 iff every executed check passed.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = (os.environ.get("CREDITFLOW_SMOKE_BASE") or "https://creditflow-api-ko2h.onrender.com").rstrip("/")
ORIGIN = os.environ.get("CREDITFLOW_SMOKE_ORIGIN") or "https://imtarget05.github.io"
API_KEY = os.environ.get("CREDITFLOW_SMOKE_API_KEY") or ""
FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL':4} | {name} {detail}")
    if not ok:
        FAILS.append(name)


def req(path, method="GET", headers=None, data=None):
    r = urllib.request.Request(BASE + path, method=method, headers=headers or {}, data=data)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers or {})
    except Exception as e:  # noqa: BLE001 — smoke must survive network errors
        return 0, str(e), {}


def main():
    print(f"API   : {BASE}\nOrigin: {ORIGIN}\n")

    st, _, _ = req("/health/live")
    check("health/live", st == 200, f"({st})")

    st, _, _ = req("/health/ready")
    if st == 404:
        st, _, _ = req("/health")
    check("health/ready (or /health)", st == 200, f"({st})")

    st, body, _ = req("/model/info")
    ok, detail = st == 200, f"({st})"
    if ok:
        try:
            info = json.loads(body)
            ident = info.get("model_name") or info.get("model_id") or info.get("model_version") or ""
            thr = info.get("threshold") or info.get("decision_threshold") or ""
            detail = f"(model={ident or 'see body'}, threshold={thr or 'see body'})" if (ident or thr) else f"(http=200, body~{body[:120]!r})"
        except json.JSONDecodeError:
            ok, detail = False, "(non-JSON body)"
    check("model/info identity", ok, detail)

    st, _, hdrs = req("/applications", method="OPTIONS", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "GET",
    })
    acao = next((v for k, v in hdrs.items() if k.lower() == "access-control-allow-origin"), "")
    ok = st in (200, 204) and ORIGIN in acao
    check("CORS preflight allows Pages origin", ok,
          f"(http={st}, ACAO={acao or 'missing - set CREDITFLOW_CORS_ORIGINS on Render'})")

    st, _, _ = req("/applications")
    check("auth gate (GET /applications -> 401 without key)", st == 401, f"({st})")

    # Intake scoring is unauthenticated by design (docs/CV_EVIDENCE.md).
    payload = json.dumps({
        "income": 8_000_000,
        "age": 35,
        "employment_years": 8,
        "loan_amount": 120_000_000,
        "loan_term": 36,
        "existing_debt": 15_000_000,
        "credit_history": 9,
        "previous_defaults": 0,
    }).encode()
    st, body, _ = req("/predict", method="POST",
                      headers={"Content-Type": "application/json"}, data=payload)
    ok, detail = st == 200, f"({st})"
    if ok:
        try:
            p = json.loads(body)
            detail = f"(decision={p.get('decision')}, risk={p.get('risk_probability')}, model={p.get('model_version')})"
        except json.JSONDecodeError:
            ok, detail = False, "(non-JSON body)"
    check("POST /predict business scoring", ok, detail)

    if API_KEY:
        st, _, _ = req("/applications", headers={"X-CreditFlow-API-Key": API_KEY})
        check("GET /applications with configured key", st == 200, f"({st})")
    else:
        print("SKIP | protected reads (export CREDITFLOW_SMOKE_API_KEY to enable)")

    print(f"\nResult: {len(FAILS)} failed check(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
