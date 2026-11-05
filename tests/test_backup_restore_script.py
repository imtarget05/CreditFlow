"""Offline contract tests for the backup/restore command wrapper."""

import hashlib
import json
import subprocess

import pytest

from scripts import backup_restore


def test_backup_writes_checksummed_manifest(tmp_path, monkeypatch):
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", backup_dir)
    monkeypatch.setenv("DATABASE_URL", "postgresql://local-test/db")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] is False
        assert command[:3] == ["pg_dump", "-d", "postgresql://local-test/db"]
        assert command[3] == "-Fc"
        (backup_dir / command[-1].split("/")[-1]).write_bytes(b"synthetic-custom-dump")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)
    backup_restore.backup()

    manifest_path = next(backup_dir.glob("*.manifest.json"))
    manifest = json.loads(manifest_path.read_text())
    dump = backup_dir / manifest["dump_file"]
    assert len(commands) == 1
    assert manifest["dump_sha256"] == hashlib.sha256(dump.read_bytes()).hexdigest()
    assert manifest["format"] == "custom"
    assert manifest["compressed"] is True


def test_restore_requires_existing_file_and_single_transaction(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local-test/db")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(backup_restore.subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as missing:
        backup_restore.restore(str(tmp_path / "missing.dump"))
    assert missing.value.code == 1
    assert commands == []

    dump = tmp_path / "synthetic.dump"
    dump.write_bytes(b"synthetic-custom-dump")
    backup_restore.restore(str(dump))
    assert commands == [["pg_restore", "-d", "postgresql://local-test/db", "-1", "-v", str(dump)]]


@pytest.mark.parametrize("url", ["", "sqlite:///local.db"])
def test_backup_rejects_missing_or_non_postgres_url(tmp_path, monkeypatch, url):
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(SystemExit) as rejected:
        backup_restore.backup()
    assert rejected.value.code == 1
    assert not (tmp_path / "backups").exists()


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_pg_tool_failure_is_not_reported_as_success(tmp_path, monkeypatch, operation):
    monkeypatch.setenv("DATABASE_URL", "postgresql://local-test/db")
    monkeypatch.setattr(backup_restore, "BACKUP_DIR", tmp_path / "backups")
    dump = tmp_path / "synthetic.dump"
    dump.write_bytes(b"synthetic-custom-dump")
    monkeypatch.setattr(
        backup_restore.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", "tool failed"),
    )
    with pytest.raises(SystemExit) as failed:
        if operation == "backup":
            backup_restore.backup()
        else:
            backup_restore.restore(str(dump))
    assert failed.value.code == 1
