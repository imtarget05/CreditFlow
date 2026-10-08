"""File-backed checkpoint saver for the LangGraph decision workflow.

LangGraph pauses the credit workflow at ``human_approval`` (REVIEW) and
resumes it when a human approves.  With the default ``InMemorySaver`` the
paused state lives in RAM only — a server restart loses every in-flight
workflow, so ``POST /predict/graph/{thread_id}/approve`` 404s after restart.

``FileCheckpointSaver`` subclasses ``InMemorySaver`` and persists the three
checkpoint stores (``storage``, ``writes``, ``blobs``) to a single pickle
file after every write, restoring them on construction.  No extra dependency
is required: ``langgraph==1.2.11`` does not ship
``langgraph.checkpoint.sqlite`` (that lives in the separate
``langgraph-checkpoint-sqlite`` package), so this stdlib implementation
keeps the demo self-contained while closing the restart gap.

Shared storage: one local file is still *one process*.  When the API runs
more than one replica (or restarts on a container that lost its volume) the
paused workflow must live somewhere both processes reach — see
:func:`create_checkpointer`, which switches on ``DATABASE_URL`` and falls back
to this file saver when the shared driver package is absent.

Demo scope: single-process, pickle file, atomic replace (tmp + os.replace).
"""
from __future__ import annotations

import base64
import importlib
import json
import logging
import os
import pickle
import threading
import uuid
from collections import defaultdict
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import DeltaChannelHistory
from langgraph.checkpoint.memory import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    InMemorySaver,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_PATH = ROOT / "data" / "creditflow_checkpoints.pkl"


def checkpoint_path() -> Path:
    """Resolve the checkpoint file path (env override lets tests isolate)."""
    env = os.environ.get("CREDITFLOW_CHECKPOINT_DB")
    return Path(env) if env else DEFAULT_CHECKPOINT_PATH


# ---------------------------------------------------------------------------
# Shared (multi-process) checkpoint storage
# ---------------------------------------------------------------------------
# The file saver above is single-process by construction: one JSON snapshot on
# one local disk.  Scaling the API to >1 replica — or restarting on a
# container that lost its volume — needs the paused ``human_approval`` state
# in a store every process can reach.  langgraph ships those savers in
# separate packages (langgraph-checkpoint-postgres / -sqlite) that this repo
# deliberately does not install, so the switch is opt-in via ``DATABASE_URL``:
# configure it and the shared saver is used; leave it unset and nothing
# changes at all.  When the store cannot be opened the API still serves —
# it logs loudly and keeps the file saver — unless the operator demands
# shared storage (CREDITFLOW_CHECKPOINT_SHARED_REQUIRED=1), in which case a
# silent downgrade to local files would be worse than failing fast.
_SHARED_BACKENDS = {
    "postgres": ("langgraph.checkpoint.postgres", "PostgresSaver", "postgres"),
    "postgresql": ("langgraph.checkpoint.postgres", "PostgresSaver", "postgres"),
    "sqlite": ("langgraph.checkpoint.sqlite", "SqliteSaver", "sqlite"),
}

# Pip-name to install when the operator wants the real shared backend.
_SHARED_PACKAGES = {
    "langgraph.checkpoint.postgres": "langgraph-checkpoint-postgres",
    "langgraph.checkpoint.sqlite": "langgraph-checkpoint-sqlite",
}

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class SharedCheckpointUnavailable(RuntimeError):
    """``DATABASE_URL`` names shared storage that could not be opened."""


def shared_required() -> bool:
    """True when shared checkpoint storage is mandatory (fail closed)."""
    return (
        os.environ.get("CREDITFLOW_CHECKPOINT_SHARED_REQUIRED", "")
        .strip()
        .lower()
        in _TRUTHY
    )


def _url_scheme(url: str) -> str:
    return url.split("://", 1)[0].strip().lower() if "://" in url else ""


def shared_database_url() -> str:
    """Return ``DATABASE_URL`` only when it names *shared* checkpoint storage.

    Shared means another process can reach the same store: a Postgres
    connection string (``postgres://`` / ``postgresql://``) or SQLite, given
    either as a ``sqlite:///...`` URL or as a bare ``*.db`` path.  Anything
    else (empty, ``mysql://``, a plain word) returns ``""`` so the file saver
    stays in charge — an accidental value must never silently redirect
    startup to a different backend.
    """
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        return ""
    scheme = _url_scheme(url)
    if scheme in _SHARED_BACKENDS:
        # An in-memory SQLite URL is per-process, i.e. not shared at all.
        if scheme == "sqlite" and _is_memory_sqlite(url):
            return ""
        return url
    if not scheme and url.lower().endswith((".db", ".sqlite", ".sqlite3")):
        return f"sqlite:///{url}"
    return ""


def _is_memory_sqlite(url: str) -> bool:
    target = url.split("://", 1)[1].strip().strip("/")
    return target in ("", ":memory:")


def _open_shared_saver(url: str) -> Any:
    """Open the langgraph saver for a shared URL and create its schema.

    Imports lazily so a deployment without the driver package still imports
    this module (the ledger does the same with psycopg).  ``setup()`` is
    idempotent DDL (``CREATE TABLE IF NOT EXISTS``), so calling it on every
    process start is the documented langgraph pattern.  Any failure — missing
    package, bad credentials, unreachable host — propagates to the caller,
    which decides between fallback and fail-closed.
    """
    backend = _url_scheme(url)
    if backend not in _SHARED_BACKENDS:
        raise SharedCheckpointUnavailable(
            f"unsupported checkpoint URL scheme {backend!r}"
        )
    module_name, class_name, _label = _SHARED_BACKENDS[backend]
    module = importlib.import_module(module_name)
    saver = getattr(module, class_name).from_conn_string(url)
    setup = getattr(saver, "setup", None)
    if callable(setup):
        setup()
    return saver


def create_checkpointer(path: str | Path | None = None) -> tuple[Any, str]:
    """Build the workflow checkpointer: shared when configured, else the file.

    Returns ``(saver, backend)`` with ``backend`` in ``{"postgres",
    "sqlite", "file"}`` so startup can record (and operators can read) which
    store actually keeps the paused workflows.

    Selection:

    1. ``DATABASE_URL`` points at Postgres / SQLite → that shared saver.
    2. It points elsewhere, or is unset → :class:`FileCheckpointSaver`
       (today's behaviour, unchanged).
    3. It points at shared storage that cannot be opened → warn loudly and
       fall back to the file saver, *except* when
       ``CREDITFLOW_CHECKPOINT_SHARED_REQUIRED=1``, where a silent downgrade
       to a file that only one replica can see would hide a broken
       multi-replica deployment: then :class:`SharedCheckpointUnavailable`
       is raised instead.
    """
    url = shared_database_url()
    if not url:
        return FileCheckpointSaver(path), "file"

    scheme = _url_scheme(url)
    backend = _SHARED_BACKENDS[scheme][2]
    try:
        return _open_shared_saver(url), backend
    except Exception as exc:
        package = _SHARED_PACKAGES[_SHARED_BACKENDS[scheme][0]]
        reason = (
            "shared checkpoint storage requested (DATABASE_URL scheme "
            f"{scheme!r}) but could not be opened: {exc!r}"
        )
        if shared_required():
            raise SharedCheckpointUnavailable(
                f"{reason}. Fix DATABASE_URL or install {package} "
                f"(pip install {package}); CREDITFLOW_CHECKPOINT_SHARED_REQUIRED=1 "
                "forbids the single-process file fallback."
            ) from exc
        logger.warning(
            "%s. Falling back to FileCheckpointSaver (single-process scope). "
            "Install %s (pip install %s) or unset DATABASE_URL to silence this; "
            "set CREDITFLOW_CHECKPOINT_SHARED_REQUIRED=1 to fail fast instead.",
            reason,
            package,
            package,
        )
        return FileCheckpointSaver(path), "file"


# Single-process store lock — LangGraph dispatches `put` and `put_writes`
# onto its `BackgroundExecutor` thread pool *without blocking*
# (_loop.py: "save it, without blocking"), so several checkpointer calls
# genuinely run concurrently inside one `graph.invoke`, on top of
# FastAPI's threadpool (the same discipline ledger.py uses).  The three
# stores are plain mutable dicts shared by every thread, so this lock has
# to cover the whole read-modify-snapshot sequence: guarding only the file
# write left the store walk unsynchronised, and a concurrent resize of a
# live `writes` bucket surfaced as "dictionary changed size during
# iteration" (HTTP 502 GRAPH_RESUME_FAILED on resume-after-restart).
# Re-entrant because `_snapshot` re-acquires it from inside put/put_writes.
_snapshot_lock = threading.RLock()


def _encode_obj(obj: Any) -> Any:
    """Secure JSON encoder for primitives, bytes, and tuple keys/values."""
    if isinstance(obj, bytes):
        return {"__bytes__": base64.b64encode(obj).decode("ascii")}
    if isinstance(obj, tuple):
        return {"__tuple__": [_encode_obj(x) for x in obj]}
    if isinstance(obj, list):
        return [_encode_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {
            (str(k) if not isinstance(k, tuple) else "__tuple_key__" + json.dumps([_encode_obj(x) for x in k])): _encode_obj(v)
            for k, v in obj.items()
        }
    return obj


def _decode_obj(obj: Any) -> Any:
    """Secure JSON decoder reconstructing tuples and bytes without eval/pickle."""
    if isinstance(obj, dict):
        if "__bytes__" in obj:
            return base64.b64decode(obj["__bytes__"])
        if "__tuple__" in obj:
            return tuple(_decode_obj(x) for x in obj["__tuple__"])
        res = {}
        for k, v in obj.items():
            if k.startswith("__tuple_key__"):
                real_k = tuple(_decode_obj(x) for x in json.loads(k[len("__tuple_key__"):]))
            else:
                real_k = k
            res[real_k] = _decode_obj(v)
        return res
    if isinstance(obj, list):
        return [_decode_obj(x) for x in obj]
    return obj


class FileCheckpointSaver(InMemorySaver):
    """``InMemorySaver`` that persists checkpoint stores securely without pickle RCE.

    The parent class keeps full checkpoint semantics (interrupts, pending
    writes, thread isolation) in three in-memory stores; this subclass
    adds load-on-init and snapshot-after-write with secure JSON serialization
    so the paused workflow state survives a process restart.  Resume with:

        graph.invoke(Command(resume="approve"), config={"configurable": {"thread_id": ...}})
    """

    def __init__(self, path: str | Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._path = Path(path) if path is not None else checkpoint_path()
        self._load()

    # -- persistence ---------------------------------------------------------
    def _snapshot(self) -> None:
        """Atomically persist the three checkpoint stores using safe JSON."""
        data = {
            "storage": {
                thread_id: {
                    checkpoint_ns: dict(checkpoints)
                    for checkpoint_ns, checkpoints in ns_map.items()
                }
                for thread_id, ns_map in self.storage.items()
            },
            "writes": dict(self.writes),
            "blobs": dict(self.blobs),
        }
        with _snapshot_lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(
                f"{self._path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            )
            try:
                encoded = _encode_obj(data)
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(encoded, f)
                os.replace(tmp, self._path)
            finally:
                if tmp.exists():
                    tmp.unlink()

    def _load(self) -> None:
        """Restore stores from the checkpoint file (JSON only, fail-closed).

        A missing file starts fresh. An unreadable/corrupt file is
        quarantined with a timestamp suffix and the saver starts empty;
        ``checkpoint_corrupt`` is set so ``/health`` reports unready until
        the operator resolves the quarantined file.
        """
        self.checkpoint_corrupt: bool = False
        self.quarantined_path = None
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    data = _decode_obj(json.loads(content))
                else:
                    return
        except Exception:
            self._quarantine_corrupt_file()
            return
        if not data:
            return
        storage: defaultdict = defaultdict(lambda: defaultdict(dict))
        for thread_id, ns_map in data.get("storage", {}).items():
            inner: defaultdict = defaultdict(dict)
            for checkpoint_ns, checkpoints in ns_map.items():
                inner[checkpoint_ns] = defaultdict(dict, checkpoints)
            storage[thread_id] = inner
        self.storage = storage
        self.writes = defaultdict(dict, data.get("writes", {}))
        self.blobs = dict(data.get("blobs", {}))

    def _quarantine_corrupt_file(self) -> None:
        """Move a corrupt checkpoint aside; never unpickle it."""
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = self._path.with_name(f"{self._path.name}.corrupt-{stamp}")
        try:
            os.replace(self._path, target)
            self.quarantined_path = target
        except OSError:
            self.quarantined_path = self._path
        self.storage = defaultdict(lambda: defaultdict(dict))
        self.writes = defaultdict(dict)
        self.blobs = {}
        self.checkpoint_corrupt = True

    # -- write hooks ---------------------------------------------------------
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        # Mutate and persist atomically: the snapshot walk must never see a
        # store that another thread is halfway through resizing.
        with _snapshot_lock:
            result = super().put(config, checkpoint, metadata, new_versions)
            self._snapshot()
        return result

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        with _snapshot_lock:
            super().put_writes(config, writes, task_id, task_path)
            self._snapshot()

    def delete_thread(self, thread_id: str) -> None:
        with _snapshot_lock:
            super().delete_thread(thread_id)
            self._snapshot()

    # -- read hooks -----------------------------------------------------------
    # The parent serves reads by walking these same three stores, and its
    # defaultdicts auto-vivify an entry while doing so — so reads mutate the
    # stores too and need the lock as well.
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        with _snapshot_lock:
            return super().get_tuple(config)

    def get_delta_channel_history(
        self, *, config: RunnableConfig, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        with _snapshot_lock:
            return super().get_delta_channel_history(
                config=config, channels=channels
            )

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with _snapshot_lock:
            return iter(
                list(
                    super().list(
                        config, filter=filter, before=before, limit=limit
                    )
                )
            )

    # -- inspection helpers ---------------------------------------------------
    def threads(self) -> list[str]:
        """All thread IDs that have at least one checkpoint."""
        with _snapshot_lock:
            return list(self.storage.keys())
