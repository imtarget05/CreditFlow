"""Build/verify a SHA-256 manifest over model artifacts (stdlib only).

* ``--build``  — hash every regular file in the artifacts dir (except the
  manifest itself) and write ``SHA256SUMS`` (``"<sha256>  <name>"`` lines,
  sorted by name, like ``sha256sum``).
* default ``--verify`` — recompute hashes, compare against the manifest,
  print one ``OK``/``FAIL`` line per entry, exit 0 iff everything matches.
  A 1-byte tamper or a missing file exits nonzero and names the offender.
  A missing manifest file itself is also a failure (exit 1).

Only ever writes the manifest when explicitly asked via ``--build``; do NOT
run ``--build`` against the real ``models/production/`` dir without asking —
verification there is informational (read-only).

Usage::

    python scripts/check_artifacts.py --build --dir models/production
    python scripts/check_artifacts.py            # verify, exit 0/1
    python scripts/check_artifacts.py --dir /tmp/art --manifest /tmp/art/SHA256SUMS
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO_ROOT / "models" / "production"
MANIFEST_NAME = "SHA256SUMS"
CHUNK_SIZE = 1024 * 1024


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def artifact_files(artifacts_dir: Path, manifest: Path) -> list[Path]:
    manifest_resolved = manifest.resolve()
    return sorted(
        p
        for p in artifacts_dir.iterdir()
        if p.is_file() and p.resolve() != manifest_resolved
    )


def build_manifest(artifacts_dir: Path, manifest: Path) -> dict[str, str]:
    entries = {p.name: sha256_of(p) for p in artifact_files(artifacts_dir, manifest)}
    with open(manifest, "w", encoding="utf-8") as fh:
        for name in sorted(entries):
            fh.write(f"{entries[name]}  {name}\n")
    return entries


def verify_manifest(artifacts_dir: Path, manifest: Path) -> tuple[bool, list[str]]:
    """Return (ok, problems). Prints one OK/FAIL/MISSING line per entry."""
    _ = artifacts_dir  # manifest stores bare names resolved against its parent
    if not manifest.is_file():
        print(f"MISSING manifest: {manifest}")
        return False, [f"manifest missing: {manifest}"]
    problems: list[str] = []
    base = manifest.parent
    with open(manifest, encoding="utf-8") as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    for ln in lines:
        try:
            digest, name = ln.split(None, 1)
            name = name.strip()
        except ValueError:
            print(f"FAIL malformed manifest line: {ln!r}")
            problems.append(f"malformed line: {ln!r}")
            continue
        target = base / name
        if not target.is_file():
            print(f"FAIL {name}: file missing")
            problems.append(f"missing: {name}")
            continue
        actual = sha256_of(target)
        if actual == digest:
            print(f"OK {name}")
        else:
            print(f"FAIL {name}: digest mismatch")
            problems.append(f"tampered: {name}")
    return (not problems), problems


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Build or verify a SHA-256 manifest over model artifacts."
    )
    ap.add_argument("--dir", default=str(DEFAULT_DIR),
                    help="Artifacts directory (default: models/production).")
    ap.add_argument("--manifest", default="",
                    help="Manifest path (default: <dir>/SHA256SUMS).")
    ap.add_argument("--build", action="store_true",
                    help="Write the manifest (default action is --verify).")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifacts_dir = Path(args.dir)
    manifest = Path(args.manifest) if args.manifest else artifacts_dir / MANIFEST_NAME
    if args.build:
        if not artifacts_dir.is_dir():
            print(f"error: artifacts dir not found: {artifacts_dir}")
            return 1
        entries = build_manifest(artifacts_dir, manifest)
        for name in sorted(entries):
            print(f"WROTE {entries[name]}  {name}")
        return 0
    ok, _ = verify_manifest(artifacts_dir, manifest)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
