"""Backup / restore drill test for CreditFlow (Plan 04).

Verifies that a pg_dump → manifest round-trip produces a non-empty,
verifiable custom-format dump, and that pg_restore can parse the TOC.
Runs against a real local PostgreSQL (DATABASE_URL) — skipped when no
Postgres is available. A true restore-into-fresh-DB drill is run manually
against staging before any production cutover.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.storage.ledger import init_db


def _has_postgres() -> bool:
    return bool(os.environ.get("DATABASE_URL", "").startswith("postgresql://"))


@pytest.mark.skipif(not _has_postgres(), reason="requires DATABASE_URL → PostgreSQL")
def test_backup_restore_drill_round_trip(monkeypatch, tmp_path):
    """Boot schema → backup → verify dump + manifest → pg_restore --list."""
    init_db()  # boot on the real Postgres via DATABASE_URL

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CREDITFLOW_BACKUP_DIR", str(backup_dir))

    repo_root = Path(__file__).resolve().parents[1]

    # Backup
    result = subprocess.run(
        [sys.executable, "scripts/backup_restore.py", "backup"],
        capture_output=True, text=True, cwd=str(repo_root), check=False,
    )
    assert result.returncode == 0, result.stderr

    # Find the latest dump + manifest
    dumps = sorted(backup_dir.glob("creditflow-*.dump"))
    assert dumps, "no dump file produced"
    latest = dumps[-1]
    assert latest.stat().st_size > 0, "dump file is empty"

    manifest = latest.with_suffix(".manifest.json")
    assert manifest.exists(), "manifest not produced"
    meta = json.loads(manifest.read_text())
    assert meta["dump_file"] == latest.name
    assert len(meta["dump_sha256"]) == 64
    assert meta["format"] == "custom"
    assert meta["compressed"] is True
    assert meta["rpo_target_minutes"] == 15
    assert meta["rto_target_hours"] == 4

    # Restore drill (read-only): pg_restore --list parses the TOC without
    # writing, proving the dump is a valid, restorable archive.
    result = subprocess.run(
        ["pg_restore", "--list", str(latest)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    toc = result.stdout
    assert "loan_applications" in toc
    assert "disbursements" in toc
