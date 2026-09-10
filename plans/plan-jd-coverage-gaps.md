# JD Coverage Gaps — Interview Package Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close two JD coverage gaps for Bosch-style ML Engineer / Data Scientist roles: hands-on deployment evidence and full-stack demo-ready UI, without changing project scope or breaking the existing local-first interview-ready state.

**Architecture:** 
- Keep the existing backend/FastAPI/Docker/MLflow core intact.
- Add a local deploy verification path that mirrors Render's expected runtime, producing evidence artifacts.
- Polish the existing React UI into an interview demo surface with guided flows and visible backend integration evidence.
- Document tradeoffs in `tasks/notes/` so future agents understand what was deliberately emphasized or downplayed.

**Tech Stack:** Existing repo stack plus Docker compose verification, Vite build artifacts, and Markdown evidence docs.

**Spec:** `docs/spec.md` §14 (cloud deployment acceptance), §19 (constraints: no steady-state compatibility; cloud permanently out of scope after acceptance), and current status `tasks/current.md`.

## Global Constraints

- Cloud permanently OUT OF SCOPE for actual deployment; only verification artifacts are added.
- Frontend remains optional/core-secondary per spec §1; polish only, no architecture change.
- `.env` stays gitignored; no secrets in evidence docs.
- Proxy dataset disclaimer must remain visible in any demo artifact.
- All changes must preserve the current test count and not break `python3 -m pytest tests/ -q` or `npm run build`.

---

## File Structure

- NEW `scripts/verify_deploy.py` — local deploy verification: build image, run container, hit `/health`, `/predict`, `/model/info`, collect JSON evidence.
- NEW `docs/evidence/deploy-check.json` — machine-readable deploy verification output.
- MODIFY `frontend/src/App.jsx` — add interview demo preset chips and guided flow state (no architecture change).
- MODIFY `frontend/src/styles.css` — add demo polish classes for guided presets and result emphasis.
- NEW `docs/interview/jd-coverage.md` — narrative mapping JD requirements to repo evidence.
- NEW `tasks/notes/jd-coverage.md` — tradeoff decisions for emphasis/downplay choices.

---

### Task 1: Local deploy verification script

**Files:**
- Create: `scripts/verify_deploy.py`
- Test: manual run + `docker --version`

**Interfaces:**
- Consumes: `Dockerfile`, `docker-compose.yml`, `.env.example`
- Produces: `docs/evidence/deploy-check.json`, console evidence suitable for screenshots/recordings

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Local deploy verification for CreditFlow.

Builds the Docker image, runs the container, probes the required endpoints,
and writes evidence to docs/evidence/deploy-check.json.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = ROOT / "docs" / "evidence"
EVIDENCE_FILE = EVIDENCE_DIR / "deploy-check.json"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def wait_for_health(base: str, timeout: int = 60) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/health", timeout=2)
            if r.status_code == 200:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def main() -> dict:
    evidence: dict = {
        "docker_build": {},
        "docker_run": {},
        "endpoints": {},
        "overall": "FAIL",
    }

    # Build
    build = run(["docker", "build", "-t", "creditflow-api", "."])
    evidence["docker_build"] = {
        "returncode": build.returncode,
        "stdout_tail": build.stdout[-400:],
        "stderr_tail": build.stderr[-400:],
    }
    if build.returncode != 0:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return evidence

    # Run
    run(["docker", "rm", "-f", "creditflow-api"], check=False)
    run_cmd = [
        "docker", "run", "-d",
        "-p", "8080:8080",
        "--env-file", ".env.example",
        "--name", "creditflow-api",
        "creditflow-api",
    ]
    run_proc = run(run_cmd, check=False)
    evidence["docker_run"] = {
        "returncode": run_proc.returncode,
        "stdout_tail": run_proc.stdout[-400:],
        "stderr_tail": run_proc.stderr[-400:],
    }
    if run_proc.returncode != 0:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return evidence

    base = "http://localhost:8080"
    healthy = wait_for_health(base)
    evidence["endpoints"]["health"] = {"ok": healthy}
    if not healthy:
        evidence["overall"] = "FAIL"
        print(json.dumps(evidence, indent=2, ensure_ascii=False))
        return evidence

    # /predict
    payload = {
        "income": 5000, "age": 35, "employment_years": 10,
        "loan_amount": 20000, "loan_term": 36, "existing_debt": 3000,
        "credit_history": 8, "previous_defaults": 0,
    }
    r = requests.post(f"{base}/predict", json=payload, timeout=10)
    evidence["endpoints"]["predict"] = {
        "status": r.status_code,
        "body_keys": list(r.json().keys()) if r.status_code == 200 else r.text[:200],
    }

    # /model/info
    r = requests.get(f"{base}/model/info", timeout=10)
    evidence["endpoints"]["model_info"] = {
        "status": r.status_code,
        "body_keys": list(r.json().keys()) if r.status_code == 200 else r.text[:200],
    }

    evidence["overall"] = "PASS"
    print(json.dumps(evidence, indent=2, ensure_ascii=False))

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.write_text(json.dumps(evidence, indent=2, ensure_ascii=False))
    print(f"wrote {EVIDENCE_FILE}")
    return evidence


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

Run: `python3 scripts/verify_deploy.py`
Expected: build + run + `/health` + `/predict` + `/model/info` all return 200, `docs/evidence/deploy-check.json` written, console shows PASS.

- [ ] **Step 3: Commit**

```bash
git add scripts/verify_deploy.py docs/evidence/deploy-check.json
git commit -m "chore(deploy): add local deploy verification + evidence"
```

---

### Task 2: Frontend interview demo polish

**Files:**
- Modify: `frontend/src/App.jsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: existing `/api` proxy, `/health`, `/predict`, `/model/info`
- Produces: polished Predict tab with preset chips, guided validation, visible backend status

- [ ] **Step 1: Add preset chips to Predict tab**

In `frontend/src/App.jsx`, add a preset chips section above the form with 3 profiles:
- "Khách hàng lý tưởng" (low risk)
- "Trung bình" (medium risk)
- "Rủi ro cao" (high risk)

Each chip fills realistic VND values into the form and triggers validation.

Add these constants at the top of the file:

```javascript
const PRESETS = {
  IDEAL: {income:8000000,age:35,employment_years:10,loan_amount:20000000,loan_term:36,existing_debt:2000000,credit_history:8,previous_defaults:0},
  MID: {income:5000000,age:32,employment_years:4,loan_amount:12000000,loan_term:36,existing_debt:8000000,credit_history:4,previous_defaults:2},
  HIGH: {income:3000000,age:30,employment_years:2,loan_amount:50000000,loan_term:60,existing_debt:40000000,credit_history:1,previous_defaults:3},
};
```

Add a chip row component that calls `setFormData(PRESETS.IDEAL)` etc.

- [ ] **Step 2: Add backend status indicator**

In the Predict tab header, show:
- 🟢 Backend connected when `/api/health` returns 200
- 🔴 Backend unreachable otherwise

Poll `/api/health` on mount and show the dot + status text.

- [ ] **Step 3: Style the guided flow**

In `frontend/src/styles.css`, add:

```css
.preset-chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.preset-chip { 
  padding: 6px 12px; border-radius: 999px; border: 1px solid #d0d5dd;
  background: #f8f9fb; cursor: pointer; font-size: 14px;
}
.preset-chip:hover { background: #eef0f4; }
.backend-status { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; margin-bottom: 8px; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.status-dot.ok { background: #16a34a; }
.status-dot.err { background: #dc2626; }
```

- [ ] **Step 4: Verify**

Run: `npm run build --prefix frontend`
Expected: build succeeds, chunks unchanged in count, CSS/JS sizes stay within ~10% of baseline.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.jsx frontend/src/styles.css
git commit -m "feat(frontend): add interview demo presets + backend status"
```

---

### Task 3: Evidence artifacts for JD package

**Files:**
- Create: `docs/interview/jd-coverage.md`
- Create: `tasks/notes/jd-coverage.md`

**Interfaces:**
- Consumes: repo README, benchmark results, manual acceptance doc, Docker/render setup
- Produces: narrative mapping + tradeoff record

- [ ] **Step 1: Write JD coverage narrative**

Create `docs/interview/jd-coverage.md`:

```markdown
# JD Coverage — CreditFlow

> **Status**: Interview package evidence
> **Disclaimer**: CreditFlow uses a public proxy dataset and is a simulated ML pipeline, not a production banking system.

## Emphasis

- **MLOps pipeline**: end-to-end reproducible pipeline (`pipeline/`) with validation, feature engineering, sklearn Pipeline, 4-model benchmark, cost-aware threshold, MLflow tracking + registry.
- **Production API**: FastAPI service with 7 endpoints, Pydantic validation, runtime metrics, drift monitoring.
- **Containerization**: Dockerfile + compose + render.yaml ready for cloud-style deployment.
- **Monitoring**: `/metrics` for system + `/drift` for PSI-based data/prediction drift.
- **Explainability**: rule-based reasons + LangGraph audit trail + optional LLM explanation with grounded RAG over policy corpus.

## Downplay / caveats

- **Cloud deployment**: `render.yaml` committed and container verified locally; actual Render deploy is not run in this repo (owner action required).
- **Frontend**: React UI is present and demoable, but the project's primary signal is backend + ML engineering, not frontend craftsmanship.
- **Domain**: tabular credit risk on proxy data; not automotive/vision/IoT domain unless explicitly framed as transferable ML engineering skills.

## Evidence commands

```bash
# Backend
python scripts/train_models.py
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8080
curl http://localhost:8080/health

# Tests
python3 -m pytest tests/ -q

# Frontend
npm run build --prefix frontend

# Docker
docker build -t creditflow-api .
docker run -p 8080:8080 --env-file .env.example creditflow-api
```

## Talking points

1. Threshold chosen by business cost (FN=5, FP=1), not accuracy.
2. Model registry + experiment tracking with MLflow.
3. Drift monitoring with PSI, documented limitation: no performance degradation without labels.
4. LLM never decides; explain node only, with template fallback.
```

- [ ] **Step 2: Write tradeoff note**

Create `tasks/notes/jd-coverage.md`:

```markdown
# JD Coverage — Tradeoffs

- Cloud deploy evidence limited to local Docker verification; actual Render deploy left to owner because cloud is out of scope per spec §19 and `tasks/current.md`.
- Frontend polished for demo presets only; no new routing, state management, or auth added because frontend is explicitly optional in spec §1.
- Emphasize MLOps + evaluation + monitoring in interviews; avoid implying real banking deployment.
```

- [ ] **Step 3: Commit**

```bash
git add docs/interview/jd-coverage.md tasks/notes/jd-coverage.md
git commit -m "docs: add JD coverage evidence + tradeoffs"
```

---

## Execution Handoff

Plan complete and saved to `plans/plan-jd-coverage-gaps.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — I execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
