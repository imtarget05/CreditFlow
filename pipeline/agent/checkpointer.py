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

Demo scope: single-process, pickle file, atomic replace (tmp + os.replace).
"""
from __future__ import annotations

import base64
import json
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

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_PATH = ROOT / "data" / "creditflow_checkpoints.pkl"


def checkpoint_path() -> Path:
    """Resolve the checkpoint file path (env override lets tests isolate)."""
    env = os.environ.get("CREDITFLOW_CHECKPOINT_DB")
    return Path(env) if env else DEFAULT_CHECKPOINT_PATH


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
