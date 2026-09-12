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

import os
import pickle
import threading
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import (
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    InMemorySaver,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CHECKPOINT_PATH = ROOT / "data" / "creditflow_checkpoints.pkl"


def checkpoint_path() -> Path:
    """Resolve the checkpoint file path (env override lets tests isolate)."""
    env = os.environ.get("CREDITFLOW_CHECKPOINT_DB")
    return Path(env) if env else DEFAULT_CHECKPOINT_PATH


# Single-process write lock — LangGraph may call put/put_writes from
# worker threads concurrently (the same discipline ledger.py uses for
# FastAPI's threadpool).
_snapshot_lock = threading.Lock()


class FileCheckpointSaver(InMemorySaver):
    """``InMemorySaver`` that persists checkpoint stores to a pickle file.

    The parent class keeps full checkpoint semantics (interrupts, pending
    writes, thread isolation) in three in-memory stores; this subclass only
    adds load-on-init and snapshot-after-write so the paused workflow state
    survives a process restart.  Resume with:

        graph.invoke(Command(resume="approve"), config={"configurable": {"thread_id": ...}})
    """

    def __init__(self, path: str | Path | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._path = Path(path) if path is not None else checkpoint_path()
        self._load()

    # -- persistence ---------------------------------------------------------
    def _snapshot(self) -> None:
        """Atomically persist the three checkpoint stores.

        The outer ``storage`` defaultdict carries a lambda default factory
        (unpicklable), so it is flattened to plain dicts; the inner
        ``defaultdict(dict)`` levels are rebuilt on load.

        The tmp file name is unique per call: LangGraph invokes ``put`` /
        ``put_writes`` from worker threads concurrently, and a shared
        ``.tmp`` name would make one thread's ``os.replace`` unlink another
        thread's tmp file mid-write (FileNotFoundError race).
        """
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
                with open(tmp, "wb") as f:
                    pickle.dump(data, f)
                os.replace(tmp, self._path)
            finally:
                if tmp.exists():
                    tmp.unlink()

    def _load(self) -> None:
        """Restore stores from the checkpoint file (no-op if absent).

        A corrupted file is ignored — the API starts fresh rather than
        crashing at import time.  The SQLite ledger keeps the authoritative
        application/disbursement records either way.
        """
        if not self._path.exists():
            return
        try:
            with open(self._path, "rb") as f:
                data = pickle.load(f)
        except (pickle.UnpicklingError, EOFError, OSError, AttributeError):
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

    # -- write hooks ---------------------------------------------------------
    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
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
        super().put_writes(config, writes, task_id, task_path)
        self._snapshot()

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._snapshot()

    # -- inspection helpers ---------------------------------------------------
    def threads(self) -> list[str]:
        """All thread IDs that have at least one checkpoint."""
        return list(self.storage.keys())
