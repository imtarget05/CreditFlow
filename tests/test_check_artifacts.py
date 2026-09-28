"""Tests for scripts/check_artifacts.py (stdlib + pytest only).

All cases use tmp files; the real models/production/ dir is never written to
(the informational real-verify run is a manual step, not part of this suite).
The script is loaded by file path so no heavy dependency is ever imported.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_ARTIFACTS = REPO_ROOT / "scripts" / "check_artifacts.py"


def _load_check_artifacts():
    spec = importlib.util.spec_from_file_location("check_artifacts", CHECK_ARTIFACTS)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ca = _load_check_artifacts()


def _artifacts(tmp_path: Path, files: dict[str, bytes]) -> Path:
    d = tmp_path / "art"
    d.mkdir()
    for name, data in files.items():
        (d / name).write_bytes(data)
    return d


def test_round_trip_pass(tmp_path, capsys):
    d = _artifacts(tmp_path, {"a.bin": b"model-bytes-1", "b.json": b'{"v": 1}'})
    manifest = str(d / "SHA256SUMS")
    assert _ca.main(["--build", "--dir", str(d), "--manifest", manifest]) == 0
    capsys.readouterr()
    assert _ca.main(["--dir", str(d), "--manifest", manifest]) == 0
    out = capsys.readouterr().out
    assert "OK a.bin" in out and "OK b.json" in out


def test_one_byte_tamper_detected(tmp_path, capsys):
    d = _artifacts(tmp_path, {"a.bin": b"0123456789abcdef"})
    manifest = str(d / "SHA256SUMS")
    assert _ca.main(["--build", "--dir", str(d), "--manifest", manifest]) == 0
    raw = bytearray((d / "a.bin").read_bytes())
    raw[0] ^= 0x01  # 1-byte tamper
    (d / "a.bin").write_bytes(bytes(raw))
    capsys.readouterr()
    assert _ca.main(["--dir", str(d), "--manifest", manifest]) == 1
    out = capsys.readouterr().out
    assert "FAIL a.bin" in out


def test_missing_file_detected(tmp_path, capsys):
    d = _artifacts(tmp_path, {"a.bin": b"aaa", "b.bin": b"bbb"})
    manifest = str(d / "SHA256SUMS")
    assert _ca.main(["--build", "--dir", str(d), "--manifest", manifest]) == 0
    (d / "b.bin").unlink()
    capsys.readouterr()
    assert _ca.main(["--dir", str(d), "--manifest", manifest]) == 1
    assert "FAIL b.bin" in capsys.readouterr().out


def test_missing_manifest_fails_verify(tmp_path, capsys):
    d = _artifacts(tmp_path, {"a.bin": b"aaa"})
    assert _ca.main(["--dir", str(d), "--manifest", str(d / "SHA256SUMS")]) == 1
    assert "MISSING" in capsys.readouterr().out


def test_manifest_itself_not_hashed(tmp_path):
    d = _artifacts(tmp_path, {"a.bin": b"aaa"})
    manifest = d / "SHA256SUMS"
    entries = _ca.build_manifest(d, manifest)
    assert set(entries) == {"a.bin"}
    ok, _ = _ca.verify_manifest(d, manifest)
    assert ok


def test_script_is_stdlib_only():
    src = CHECK_ARTIFACTS.read_text(encoding="utf-8")
    top = [ln for ln in src.splitlines()
           if ln.startswith("import ") or ln.startswith("from ")]
    stdlib = {"__future__", "argparse", "hashlib", "pathlib"}
    mods = {ln.split()[1].split(".")[0] for ln in top}
    assert mods <= stdlib, mods
