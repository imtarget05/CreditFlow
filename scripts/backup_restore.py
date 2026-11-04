"""Backup / restore for CreditFlow PostgreSQL storage (Plan 04).

Usage:
  # Backup (logical, custom format, compressed)
  DATABASE_URL=postgresql://... python scripts/backup_restore.py backup

  # Restore (single transaction, all-or-nothing)
  DATABASE_URL=postgresql://... python scripts/backup_restore.py restore creditflow_2026-11-04.dump

Exit codes:
  0 — success
  1 — failure (missing env, missing file, pg_dump/pg_restore error)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKUP_DIR = Path(os.environ.get("CREDITFLOW_BACKUP_DIR", "backups"))


def _require_env() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.stderr.write("DATABASE_URL is required\n")
        sys.exit(1)
    if not url.startswith(("postgresql://", "postgres://")):
        sys.stderr.write("DATABASE_URL must be a postgresql:// URL\n")
        sys.exit(1)
    return url


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def backup() -> None:
    url = _require_env()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dump_path = BACKUP_DIR / f"creditflow-{stamp}.dump"
    manifest_path = BACKUP_DIR / f"creditflow-{stamp}.manifest.json"

    result = subprocess.run(
        ["pg_dump", "-d", url, "-Fc", "-f", str(dump_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)

    manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "dump_file": dump_path.name,
        "dump_sha256": _sha256(dump_path),
        "format": "custom",
        "compressed": True,
        "rpo_target_minutes": 15,
        "rto_target_hours": 4,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"backup: {dump_path} ({manifest['dump_sha256'][:16]})")


def restore(dump_file: str) -> None:
    url = _require_env()
    dump_path = Path(dump_file)
    if not dump_path.exists():
        sys.stderr.write(f"dump file not found: {dump_file}\n")
        sys.exit(1)

    result = subprocess.run(
        ["pg_restore", "-d", url, "-1", "-v", str(dump_path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        sys.exit(1)

    print(f"restore: {dump_path} -> {url.split('@')[-1] if '@' in url else url}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("usage: backup_restore.py <backup|restore> [dump_file]\n")
        sys.exit(1)
    command = sys.argv[1]
    if command == "backup":
        backup()
    elif command == "restore":
        if len(sys.argv) < 3:
            sys.stderr.write("restore requires a dump file path\n")
            sys.exit(1)
        restore(sys.argv[2])
    else:
        sys.stderr.write(f"unknown command: {command}\n")
        sys.exit(1)
