#!/usr/bin/env python3
"""Micro-benchmark: CreditFlow ledger posting throughput.

Prefers pipeline.storage.ledger if import-clean, else sqlite3 baseline
(explicitly labeled). Never fails: SKIP + exit 0 only on unexpected error.

Run: python3 scripts/bench_creditflow.py   (from CreditFlow/)
"""
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmp.close()
os.environ["CREDITFLOW_LEDGER_DB"] = tmp.name

try:
    from pipeline.storage import ledger as ledger_mod

    MODE = "pipeline.storage.ledger"
    IMPORT_OK = True
except Exception as exc:
    ledger_mod = None
    MODE = f"sqlite3 baseline (pipeline.storage.ledger unavailable: {exc})"
    IMPORT_OK = False


def bench_ledger(n=200):
    import uuid

    ledger_mod.init_db()
    samples = []
    for i in range(n):
        tid = f"bench-{uuid.uuid4().hex[:8]}-{i}"
        t0 = time.perf_counter()
        app_id = ledger_mod.record_application(
            tid, {"name": "bench", "amount": 1000000}, 0.1, "LOW", "APPROVE", "APPROVED"
        )
        ledger_mod.record_disbursement(app_id, 1000000)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return samples


def bench_sqlite(n=500):
    import sqlite3

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    samples = []
    for i in range(n):
        t0 = time.perf_counter()
        con.execute("INSERT INTO t(v) VALUES (?)", (f"row-{i}",))
        con.commit()
        samples.append((time.perf_counter() - t0) * 1000.0)
    con.close()
    return samples


def main():
    try:
        if IMPORT_OK:
            samples = bench_ledger(200)
            op = "ledger_posting"
        else:
            samples = bench_sqlite(500)
            op = "sqlite_baseline"
    except Exception as exc:
        print(f"SKIP bench_creditflow: {exc}")
        sys.exit(0)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    samples.sort()
    n = len(samples)
    mean = statistics.fmean(samples)
    p95 = samples[min(n - 1, int(n * 0.95))]
    print(f"mode: {MODE}")
    print(f"{'op':>16}{'n':>8}{'mean_ms':>12}{'p95_ms':>12}")
    print(f"{op:>16}{n:>8}{mean:>12.4f}{p95:>12.4f}")


if __name__ == "__main__":
    main()
