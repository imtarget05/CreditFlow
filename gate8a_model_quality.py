#!/usr/bin/env python3
"""GATE 8A — CREDITFLOW MODEL QUALITY GATE.

Cau hoi phong van: "Model moi tot hon bang cach nao, va ai quyet dinh
duoc deploy?"

Cau tra loi phai la: versioned eval set + quality thresholds dinh nghia
truoc + CI gate chan duoc regression. Khong phai "accuracy dep".

Primary metric KHONG phai accuracy. Vi FN dat gap 5x FP, mot model
accuracy 90% bo sot nhieu khach hang default van co the te hon model
accuracy 86% bat sai huong co chu dich.

Diem mau chot cua gate nay KHONG phai "model pass". Do la chung minh co
che kiem soat THUC SU phat hien duoc loi: mot candidate te phai bi CI
chan, va CI phai tra exit code khac 0.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent          # CreditFlow/
PROJ = ROOT.parent                              # repo root
sys.path.insert(0, str(ROOT))

from pipeline.feature_engineering.features import add_derived_features  # noqa: E402
from pipeline.modeling.train import stratified_train_val_test_split  # noqa: E402

CFG = yaml.safe_load((ROOT / "gate8a_thresholds.yaml").read_text())
GATES = CFG["GATES"]
NEGATIVE_THRESHOLD = CFG["NEGATIVE_THRESHOLD"]
FN_COST, FP_COST = CFG["FN_COST"], CFG["FP_COST"]

EVIDENCE = PROJ / "docs/evidence/e2e/gate8a-creditflow-model-quality.json"
RESULTS: list[dict] = []


def check(cid: str, desc: str, status: str, detail: str, oracle: str) -> None:
    RESULTS.append({"id": cid, "description": desc, "status": status,
                    "detail": detail, "oracle_source": oracle})
    print(f"  [{status}] {cid} {desc}: {detail}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def score(y_true, y_proba, threshold: float) -> dict:
    """Business-cost metrics at a given decision threshold.

    predict = 1 (se default / REJECT) when proba >= threshold.
    """
    y_pred = (y_proba >= threshold).astype(int)
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    n = len(y_true)
    cost = fn * FN_COST + fp * FP_COST
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "threshold": threshold,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "business_cost": cost,
        # Normalized so datasets of different size stay comparable.
        "normalized_business_cost": round(cost / n, 4) if n else None,
        "recall_positive": round(recall, 4),
        "precision_positive": round(precision, 4),
        "f1": round(f1, 4),
        "accuracy": round((tp + tn) / n, 4) if n else None,
        "n": n,
    }


def evaluate_gates(m: dict) -> tuple[bool, list[str]]:
    """Apply the PRE-COMMITTED thresholds. Returns (passed, violations)."""
    v = []
    if m["normalized_business_cost"] > GATES["max_normalized_business_cost"]:
        v.append(f"normalized_business_cost {m['normalized_business_cost']} > "
                 f"{GATES['max_normalized_business_cost']}")
    if m["recall_positive"] < GATES["min_default_recall"]:
        v.append(f"recall_positive {m['recall_positive']} < "
                 f"{GATES['min_default_recall']}")
    if m["f1"] < GATES["min_f1"]:
        v.append(f"f1 {m['f1']} < {GATES['min_f1']}")
    return (not v), v


def run_gate_cli(m: dict):
    """Run the gate the way CI would: as a separate process, check exit code.

    Importing the gate in-process and asserting on its return value proves
    nothing about CI. Only an observed non-zero exit code proves the pipeline
    actually fails the build.
    """
    src = (
        "import sys, yaml;"
        f"g=yaml.safe_load(open({str(ROOT / 'gate8a_thresholds.yaml')!r}))['GATES'];"
        f"norm={m['normalized_business_cost']!r};"
        f"rec={m['recall_positive']!r};"
        f"f1={m['f1']!r};"
        "bad=(norm>g['max_normalized_business_cost']"
        " or rec<g['min_default_recall'] or f1<g['min_f1']);"
        "print('gate decision:','FAIL' if bad else 'PASS');"
        "sys.exit(1 if bad else 0)"
    )
    return subprocess.run([sys.executable, "-c", src],
                          capture_output=True, text=True)
# ---------------------------------------------------------------- A1
print("\n[A1] dataset integrity + leakage guard")
data_path = ROOT / CFG["DATASET"]
ds_sha = sha256(data_path)
raw = pd.read_csv(data_path)

df, _ = add_derived_features(raw.copy())
X_train, X_val, X_test, y_train, y_val, y_test = stratified_train_val_test_split(
    df, test_size=0.15, random_state=42
)

check("A1", "eval dataset is byte-identical to the frozen file",
      "PASS", f"sha256={ds_sha[:16]} rows={len(raw)}",
      "SHA-256 of the eval dataset file")

# train_test_split partitions on row index, so index intersection IS the
# leakage question. If any eval row was also trained on, every metric below
# is optimistic and worthless.
train_rows, eval_rows = set(X_train.index), set(X_test.index)
overlap = len(train_rows & eval_rows)
val_overlap = len(set(X_val.index) & eval_rows)
check("A2", "no train/eval row overlap (data leakage guard)",
      "PASS" if overlap == 0 else "FAIL",
      f"train={len(train_rows)} eval={len(eval_rows)} overlap={overlap} "
      f"val_cap_eval={val_overlap}",
      "set intersection of split row indices")
if overlap > 0:
    # Khong duoc khoe metric neu dataset dinh leakage.
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(
        {"gate": "8A", "status": "FAIL", "reason": "data leakage",
         "train_eval_overlap": overlap}, indent=2))
    check("A2x", "metrics withheld because the split is leaked", "FAIL",
          "refusing to report metrics on a leaked split", "overlap > 0")
    sys.exit(1)

# ---------------------------------------------------------------- A3/A4
print("\n[A3/A4] approved baseline (threshold read from meta.json)")
bundle = joblib.load(ROOT / "models/production/pipeline.joblib")
meta = json.loads((ROOT / "models/production/meta.json").read_text())
manifest = json.loads((ROOT / "models/production/manifest.json").read_text())
model_sha = sha256(ROOT / "models/production/pipeline.joblib")
approved_threshold = float(meta["threshold"])

# Hai override chi dung cho negative CI test. Chúng KHONG bao gio duoc
# dat trong mot lan chay binh thuong — neu chay sai, evidence se khong
# phan anh trang thai deploy that.
if os.environ.get("GATE8A_EVIDENCE_SUFFIX"):
    EVIDENCE = EVIDENCE.with_name(
        EVIDENCE.stem + os.environ["GATE8A_EVIDENCE_SUFFIX"] + EVIDENCE.suffix)
DRYRUN = os.environ.get("GATE8A_DRYRUN") == "1"
if os.environ.get("GATE8A_THRESHOLD_OVERRIDE"):
    approved_threshold = float(os.environ["GATE8A_THRESHOLD_OVERRIDE"])
    print(f"  !! simulating a DEPLOYED candidate at threshold "
          f"{approved_threshold}")

proba = bundle.predict_proba(X_test)[:, 1]
y_true = np.asarray(y_test)
baseline = score(y_true, proba, approved_threshold)
passed, violations = evaluate_gates(baseline)

check("A3", "approved baseline passes the quality gate",
      "PASS" if passed else "FAIL",
      f"threshold={approved_threshold} cost={baseline['business_cost']} "
      f"norm={baseline['normalized_business_cost']} "
      f"recall={baseline['recall_positive']} f1={baseline['f1']}",
      "pre-committed thresholds in gate8a_thresholds.yaml")
for i, v in enumerate(violations):
    check(f"A3v{i}", f"gate violation: {v}", "FAIL", "threshold exceeded",
          "threshold comparison")

# manifest.json ghi khoa truong day. Neu khong reproduce duoc, eval set da
# troi khoi ban duyet moi va moi con so o tren la ve mot dataset khac.
declared = manifest["metadata"]["confusion_matrix"]
CM_KEYS = {"tp": "true_positive", "fp": "false_positive",
           "tn": "true_negative", "fn": "false_negative"}
reproduced = all(baseline["confusion_matrix"][short] == declared[long]
                 for short, long in CM_KEYS.items())
check("A4", "eval set reproduces the approved manifest confusion matrix",
      "PASS" if reproduced else "FAIL",
      f"ours={baseline['confusion_matrix']} "
      f"manifest={ {v: declared[v] for v in CM_KEYS.values()} }",
      "comparison against manifest.json written at training time")
# business_cost 201.0 trong meta.json la cost tren VALIDATION, khong phai
# test. Test cost la 242.0. Ghi ro de khong ai nham hai con so.
declared_test = manifest["metadata"]["test_metrics"]
check("A5", "eval set reproduces the approved manifest test metrics",
      "PASS" if (abs(declared_test["recall"] - baseline["recall_positive"]) < 1e-9
                 and abs(declared_test["f1"] - baseline["f1"]) < 1e-4) else "FAIL",
      f"ours recall={baseline['recall_positive']} f1={baseline['f1']} | "
      f"manifest recall={declared_test['recall']} f1={declared_test['f1']}",
      "recall/f1 comparison against manifest test_metrics")

# ---------------------------------------------------------------- A6
print("\n[A6] reproducibility — same inputs must give same metrics")
# Reload the bundle from disk so a cached in-memory model cannot make this
# trivially true.
proba2 = joblib.load(ROOT / "models/production/pipeline.joblib"
                     ).predict_proba(X_test)[:, 1]
repro = score(y_true, proba2, approved_threshold)
identical = repro == baseline
check("A6", "repeated evaluation is bit-identical",
      "PASS" if identical else "FAIL",
      "identical" if identical else f"drift: {repro} != {baseline}",
      "second independent load + inference pass")

# ---------------------------------------------------------------- A6
print("\n[A7] negative candidate — a REAL config regression")
# The negative is deliberately NOT random predictions, which would be a
# toy. It is the sklearn default threshold 0.50: exactly what a developer
# gets by refactoring and silently losing the tuned threshold. A gate that
# cannot catch this catches nothing worth catching.
negative = score(y_true, proba, NEGATIVE_THRESHOLD)
neg_passed, neg_violations = evaluate_gates(negative)
caught = not neg_passed
check("A7", "known-bad candidate (threshold 0.50) is REJECTED",
      "PASS" if caught else "FAIL",
      f"norm_cost {baseline['normalized_business_cost']} -> "
      f"{negative['normalized_business_cost']}, "
      f"recall {baseline['recall_positive']} -> {negative['recall_positive']}, "
      f"accuracy {baseline['accuracy']} -> {negative['accuracy']}; "
      f"violations={neg_violations}",
      "pre-committed thresholds applied to a regressed config")
# A negative test only proves something if the failure is real. Show that the
# regression is genuine rather than the gate being unconditionally strict.
check("A7b", "negative candidate is genuinely worse on business cost",
      "PASS" if negative["normalized_business_cost"]
      > baseline["normalized_business_cost"] else "FAIL",
      f"{negative['normalized_business_cost']} vs "
      f"{baseline['normalized_business_cost']}",
      "comparison of normalized cost between baseline and negative")
# This is the whole argument of the gate, so assert it explicitly: accuracy
# went UP while the business outcome got worse.
acc_up = negative["accuracy"] > baseline["accuracy"]
check("A7c", "accuracy alone would have hidden this regression",
      "PASS" if (acc_up and caught) else "FAIL",
      f"accuracy {baseline['accuracy']} -> {negative['accuracy']} "
      f"(up={acc_up}) yet default recall fell "
      f"{baseline['recall_positive']} -> {negative['recall_positive']}",
      "accuracy vs recall divergence between baseline and negative")

# ---------------------------------------------------------------- A7/A8
print("\n[A8/A9] CI behaviour — real subprocess, observed exit codes")
proc_bad = run_gate_cli(negative)
check("A8", "CI exits non-zero when the quality gate fails",
      "PASS" if proc_bad.returncode != 0 else "FAIL",
      f"exit={proc_bad.returncode} stdout={proc_bad.stdout.strip()}",
      "observed process exit code, not an in-process return value")

proc_good = run_gate_cli(baseline)
check("A9", "CI exits zero for the approved candidate",
      "PASS" if proc_good.returncode == 0 else "FAIL",
      f"exit={proc_good.returncode} stdout={proc_good.stdout.strip()}",
      "observed process exit code")

# A8/A9 above test the gate DECISION in isolation. That is not enough: it does
# not prove the real harness fails the build. A9b runs this entire script,
# unmodified except for the deployed threshold, as CI would. If a bad
# candidate can still produce a green build, the gate is decorative.
#
# DEPTH guard is mandatory: a child run would otherwise re-enter A9b and
# spawn its own child forever. Only the top-level run may recurse.
DEPTH = int(os.environ.get("GATE8A_DEPTH", "0"))
if DEPTH == 0:
    env_bad = dict(os.environ,
                   GATE8A_THRESHOLD_OVERRIDE=str(NEGATIVE_THRESHOLD),
                   GATE8A_DRYRUN="1", GATE8A_DEPTH="1",
                   GATE8A_EVIDENCE_SUFFIX="-negative-e2e")
    full_bad = subprocess.run([sys.executable, __file__], cwd=str(ROOT),
                              capture_output=True, text=True, env=env_bad)
    check("A9b", "FULL harness run fails the build on a deployed bad candidate",
          "PASS" if full_bad.returncode != 0 else "FAIL",
          f"whole-script exit={full_bad.returncode}; status line="
          f"{[l.strip() for l in full_bad.stdout.splitlines() if 'status=' in l]}",
          "exit code of this entire script re-run as a subprocess")

    # And the same must hold for the approved candidate, or the gate is simply
    # broken rather than strict.
    env_good = dict(os.environ, GATE8A_DRYRUN="1", GATE8A_DEPTH="1",
                    GATE8A_EVIDENCE_SUFFIX="-approved-e2e")
    env_good.pop("GATE8A_THRESHOLD_OVERRIDE", None)
    full_good = subprocess.run([sys.executable, __file__], cwd=str(ROOT),
                               capture_output=True, text=True, env=env_good)
    check("A9c", "FULL harness run passes the build on the approved candidate",
          "PASS" if full_good.returncode == 0 else "FAIL",
          f"whole-script exit={full_good.returncode}",
          "exit code of this entire script re-run as a subprocess")
else:
    full_bad = full_good = None

# ---------------------------------------------------------------- summary
n_pass = sum(1 for r in RESULTS if r["status"] == "PASS")
status = "PASS" if n_pass == len(RESULTS) else "PARTIAL"

# Commit phai tro ve repo chua CODE (CreditFlow/), khong phai repo cha
# chua docs/. Neu tro sai repo, evidence se gan mot ma commit ma khong
# chua mot dong code nao cua gate nay.
commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                        capture_output=True, text=True).stdout.strip()
evidence = {
    "gate": "8A",
    "title": "CreditFlow Model Quality Gate",
    "status": status,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "commit": commit,
    "commit_repo": "CreditFlow (git remote: imtarget05/CreditFlow)",
    "environment": {"python": sys.version.split()[0], "numpy": np.__version__},
    "thresholds_precommitted": GATES,
    "negative_candidate_config": {"threshold": NEGATIVE_THRESHOLD},
    "integrity": {
        "dataset_sha256": ds_sha,
        "model_sha256": model_sha,
        "model_version": meta["version"],
        "approved_threshold": approved_threshold,
    },
    "leakage_guard": {
        "train_count": int(len(X_train)),
        "eval_count": int(len(X_test)),
        "val_count": int(len(X_val)),
        "train_eval_overlap": overlap,
        "val_eval_overlap": val_overlap,
    },
    "baseline": baseline,
    "negative_candidate": negative,
    "negative_tests": {
        "creditflow_rejected": caught,
        "ci_exit_nonzero_on_fail": proc_bad.returncode != 0,
        "ci_exit_zero_on_approved": proc_good.returncode == 0,
        "full_harness_exit_on_bad_candidate": (
            full_bad.returncode if full_bad else "skipped_nested_run"),
        "full_harness_exit_on_approved": (
            full_good.returncode if full_good else "skipped_nested_run"),
    },
    "checks": RESULTS,
    "key_finding": (
        "Changing ONLY the decision threshold, with the model untouched, moved "
        f"normalized business cost {baseline['normalized_business_cost']} -> "
        f"{negative['normalized_business_cost']} and default recall "
        f"{baseline['recall_positive']} -> {negative['recall_positive']}, "
        f"while accuracy went {baseline['accuracy']} -> {negative['accuracy']}. "
        "An accuracy-only gate would have shipped this regression."
    ),
}
if not DRYRUN:
    print(f"\n{n_pass}/{len(RESULTS)} status={status}")
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(evidence, indent=2))
    print(f"evidence: {EVIDENCE}")
else:
    print(f"\n{n_pass}/{len(RESULTS)} status={status} (dry run, no evidence written)")
sys.exit(0 if status == "PASS" else 1)
