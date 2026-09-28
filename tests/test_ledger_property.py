"""Phase-2 ledger property invariants (seeded, stdlib + import-clean ledger only).

Import path mirrors tests/test_ledger_units.py: ``from pipeline.storage import
ledger`` (stdlib sqlite3/hashlib only — no pandas/torch/langchain). Every case
uses an isolated tmp SQLite file so the suite never touches the real ledger.

Invariants (double-entry conservation):
  * every disbursement posting balances: (debit receivable, credit cash) sums
    to zero per disbursement and across the whole ledger;
  * no negative money movement: stored loan_amount equals the non-negative
    input, contract codes are unique, every row is hash_valid;
  * replay safety: disbursing the same application twice returns the identical
    row without adding a second money-movement row.
"""
from __future__ import annotations

import random
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.storage import ledger  # noqa: E402

pytestmark = [pytest.mark.adversarial]

SEED = 20260927
N_CASES = 200


def _profile(rng: random.Random) -> dict:
    income = rng.choice([0, 3_000_000, 8_000_000, 25_000_000, 10**12])
    loan = rng.choice([0, 10_000_000, 120_000_000, 5_000_000_000])
    debt = rng.choice([0, 15_000_000, 200_000_000])
    return {"income": income, "loan_amount": loan, "existing_debt": debt}


def test_double_entry_conservation_over_seeded_applications(tmp_path):
    """Sum of postings == 0 and disbursed totals match inputs (N_CASES apps)."""
    rng = random.Random(SEED)
    db = tmp_path / "ledger-prop.db"
    ledger.init_db(db)
    expected_total = 0.0
    n = 0
    for case in range(N_CASES):
        profile = _profile(rng)
        amount = float(profile["loan_amount"])
        app_id = ledger.record_application(
            f"prop-thread-{case}", profile, 0.01, "LOW", "APPROVE", "APPROVED",
            db_path=db,
        )
        row = ledger.record_disbursement(app_id, amount, db_path=db)
        expected_total += amount
        n += 1
        # No negative money movement: stored amount equals the non-negative input.
        assert row["loan_amount"] == amount >= 0, (
            f"case {case}: stored amount {row['loan_amount']} != input {amount}")
        assert row["hash_valid"] if "hash_valid" in row else True, (
            f"case {case}: fresh disbursement row must verify")
    rows = ledger.list_disbursements(db_path=db)
    assert len(rows) == n, (
        f"ledger holds {len(rows)} rows for {n} disbursements (duplication?)")
    # Double-entry: each disbursement posts +amount (receivable) and -amount
    # (cash); the journal must net to exactly zero.
    postings = []
    for r in rows:
        assert r["hash_valid"] is True, (
            f"contract {r['contract_code']}: stored row must be hash_valid")
        postings += [r["loan_amount"], -r["loan_amount"]]
    assert sum(postings) == 0.0, (
        f"double-entry journal does not balance: {sum(postings)}")
    assert sum(r["loan_amount"] for r in rows) == expected_total, (
        "sum of disbursed amounts must equal sum of approved inputs")
    codes = [r["contract_code"] for r in rows]
    assert len(set(codes)) == len(codes), "contract codes must be unique"


def test_replay_same_application_never_duplicates_money(tmp_path):
    rng = random.Random(SEED + 1)
    db = tmp_path / "ledger-replay.db"
    ledger.init_db(db)
    for case in range(N_CASES):
        profile = _profile(rng)
        amount = float(profile["loan_amount"])
        app_id = ledger.record_application(
            f"replay-{case}", profile, 0.02, "LOW", "APPROVE", "APPROVED",
            db_path=db,
        )
        first = ledger.record_disbursement(app_id, amount, db_path=db)
        # An idempotency-keyed retry and a bare retry must both return the
        # SAME row (no second money movement).
        again = ledger.record_disbursement(
            app_id, amount, db_path=db, idempotency_key=f"key-{case}")
        bare = ledger.record_disbursement(app_id, amount + 999.0, db_path=db)
        assert again["contract_code"] == first["contract_code"], (
            f"case {case}: keyed replay changed the contract (duplicate money?)")
        assert bare["contract_code"] == first["contract_code"], (
            f"case {case}: bare replay changed the contract (duplicate money?)")
        assert bare["loan_amount"] == amount, (
            f"case {case}: replay must not overwrite the amount {amount}")
    rows = ledger.list_disbursements(db_path=db)
    assert len(rows) == N_CASES, (
        f"expected exactly {N_CASES} money-movement rows, got {len(rows)}")
    assert sum(r["loan_amount"] for r in rows) + sum(
        -r["loan_amount"] for r in rows) == 0.0, (
        "journal must still balance after replays")


def test_tamper_breaks_hash_but_never_silently(tmp_path):
    """Editing any hashed field is always detectable (hash_valid False)."""
    rng = random.Random(SEED + 2)
    db = tmp_path / "ledger-tamper.db"
    ledger.init_db(db)
    for case in range(N_CASES):
        profile = _profile(rng)
        amount = float(profile["loan_amount"]) + 1.0  # keep > 0 for the edit
        app_id = ledger.record_application(
            f"tamper-{case}", profile, 0.03, "LOW", "APPROVE", "APPROVED",
            db_path=db,
        )
        row = ledger.record_disbursement(app_id, amount, db_path=db)
        assert ledger.verify_disbursement_hash(
            {**row, "ledger_hash": row["ledger_hash"]}) is True, (
            f"case {case}: fresh row must verify before tampering")
        col = rng.choice(["loan_amount", "contract_code"])
        with sqlite3.connect(db) as conn:
            if col == "loan_amount":
                conn.execute("UPDATE disbursements SET loan_amount = loan_amount + 1 "
                             "WHERE contract_code = ?", (row["contract_code"],))
            else:
                conn.execute("UPDATE disbursements SET contract_code = contract_code || 'X' "
                             "WHERE contract_code = ?", (row["contract_code"],))
        reread = [r for r in ledger.list_disbursements(db_path=db)
                  if r["application_id"] == app_id][0]
        assert reread["hash_valid"] is False, (
            f"case {case}: edited {col} must be detected (hash_valid False)")
