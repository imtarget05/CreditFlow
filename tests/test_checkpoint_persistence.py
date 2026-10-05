"""FileCheckpointSaver — the paused workflow survives a process restart.

With InMemorySaver, POST /predict/graph/{thread_id}/approve 404s after a
server restart because the paused state (interrupt at human_approval) lived
only in RAM.  These tests prove the paused state is restored from the
checkpoint file:

1. Graph layer: two FileCheckpointSaver instances on the same file —
   "process 1" pauses a REVIEW workflow, "process 2" (fresh saver + graph)
   resumes and completes it.
2. API layer: approve succeeds after resetting the app's shared graph
   (simulated restart) because the checkpointer reloads from disk.

Each test isolates via CREDITFLOW_CHECKPOINT_DB (resolved at saver
construction). Requires the trained production model at models/production/.
"""
from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from langgraph.types import Command

import backend.app as app_module
from backend.app import app
from backend.predict_service import load_production_model
from pipeline.agent.checkpointer import FileCheckpointSaver
from pipeline.agent.graph import build_credit_graph
from tests.auth_support import AUTH_HEADERS, configure_auth_env

# NOTE: graph-layer resume tests below are local-only fast (file saver +
# model fixtures, no HTTP). Only the API-level restart tests at the bottom
# carry the `slow` marker individually.

REVIEW_PROFILE = {
    "income": 8000000.0,
    "age": 35,
    "employment_years": 0.5,
    "loan_amount": 120000000.0,
    "loan_term": 36,
    "existing_debt": 2000000.0,
    "credit_history": 9.0,
    "previous_defaults": 2,
}

REVIEW_CANDIDATES = [
    dict(REVIEW_PROFILE, previous_defaults=d, employment_years=e)
    for d in (1, 2)
    for e in (0.5, 2.0)
]


@pytest.fixture()
def checkpoint_env(tmp_path, monkeypatch):
    """Point the checkpointer at a fresh per-test file."""
    db = tmp_path / "checkpoints.pkl"
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", str(db))
    return db


def _invoke_until_paused(graph, profile: dict, thread_id: str) -> dict:
    """Invoke a workflow; return the config of the paused REVIEW workflow."""
    config = {"configurable": {"thread_id": thread_id}}
    try:
        graph.invoke({"customer_data": profile, "request_meta": {}}, config=config)
    except Exception:
        # Interrupt raised when the graph pauses at human_approval.
        pass
    state = graph.get_state(config=config)
    assert state.values.get("approval_required") is True, (
        f"expected a REVIEW (paused) workflow, got: {state.values.get('decision')}"
    )
    assert state.values.get("workflow_complete") is not True
    return config


def _first_paused_thread(graph, prefix: str) -> dict:
    """Start workflows over the candidates until one pauses in REVIEW."""
    for i, profile in enumerate(REVIEW_CANDIDATES):
        thread_id = f"{prefix}-{i}"
        config = {"configurable": {"thread_id": thread_id}}
        try:
            graph.invoke(
                {"customer_data": profile, "request_meta": {}}, config=config
            )
        except Exception:
            # Interrupt raised when the graph pauses at human_approval.
            pass
        state = graph.get_state(config=config)
        if (
            state.values.get("approval_required") is True
            and state.values.get("workflow_complete") is not True
        ):
            return config
    pytest.fail("no candidate produced a REVIEW (paused) workflow")



# ---------------------------------------------------------------------------
# Graph layer: restart restores the paused workflow
# ---------------------------------------------------------------------------
def test_paused_workflow_resumes_after_restart(checkpoint_env):
    pipeline, meta = load_production_model()

    # --- "process 1": start a REVIEW workflow, pause at human_approval ---
    saver1 = FileCheckpointSaver()
    graph1 = build_credit_graph(pipeline, meta, checkpointer=saver1)
    config = _first_paused_thread(graph1, "run-restart-1")

    # --- "process 2": brand-new saver + graph from the same checkpoint file ---
    saver2 = FileCheckpointSaver()
    assert saver2 is not saver1
    graph2 = build_credit_graph(pipeline, meta, checkpointer=saver2)

    restored = graph2.get_state(config=config)
    assert restored.values.get("approval_required") is True
    assert restored.values.get("application_id")  # state survived intact

    # Resume the restored workflow — it completes end-to-end.
    final = graph2.invoke(Command(resume="approve"), config=config)
    assert final.get("workflow_complete") is True
    assert final.get("approval_status") == "APPROVED"
    assert final.get("decision") == "APPROVE"


def test_paused_workflow_reject_resumes_after_restart(checkpoint_env):
    """The reject path resumes equally well from a fresh 'process'."""
    pipeline, meta = load_production_model()
    saver1 = FileCheckpointSaver()
    graph1 = build_credit_graph(pipeline, meta, checkpointer=saver1)
    config = _first_paused_thread(graph1, "run-restart-2")

    saver2 = FileCheckpointSaver()
    graph2 = build_credit_graph(pipeline, meta, checkpointer=saver2)
    final = graph2.invoke(Command(resume="reject"), config=config)
    assert final.get("workflow_complete") is True
    assert final.get("approval_status") == "REJECTED"
    assert final.get("decision") == "REJECT"


def test_threads_are_isolated_inside_the_checkpoint_file(checkpoint_env):
    """Two threads on one shared saver must not see each other's state."""
    pipeline, meta = load_production_model()
    saver = FileCheckpointSaver()
    graph = build_credit_graph(pipeline, meta, checkpointer=saver)

    config_a = _first_paused_thread(graph, "run-iso-a")
    config_b = _first_paused_thread(graph, "run-iso-b")

    state_a = graph.get_state(config=config_a)
    state_b = graph.get_state(config=config_b)
    assert state_a.values.get("application_id") != state_b.values.get(
        "application_id"
    )
    assert set(saver.threads()) >= set(
        [config_a["configurable"]["thread_id"], config_b["configurable"]["thread_id"]]
    )


def test_corrupted_checkpoint_file_starts_fresh(checkpoint_env):
    """A corrupted file must not crash the API at saver construction."""
    checkpoint_env.write_bytes(b"not-a-pickle")
    saver = FileCheckpointSaver()  # must not raise
    assert saver.threads() == []



# ---------------------------------------------------------------------------
# API layer: approve works after a simulated server restart — SLOW (spins the
# full 12-node LangGraph twice via TestClient lifespan).
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_api_approve_works_after_simulated_restart(
    checkpoint_env, tmp_path, monkeypatch
):
    from pipeline.storage.ledger import init_db

    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(tmp_path / "ledger.db"))
    # The approve endpoint requires the shared API key.
    configure_auth_env(monkeypatch)
    init_db()
    app_module._reset_shared_graph()

    with TestClient(app) as client:
        started = None
        for profile in REVIEW_CANDIDATES:
            r = client.post("/predict/graph", json={"customer_data": profile})
            assert r.status_code == 200, r.text
            body = r.json()
            if body.get("approval_required"):
                started = body
                break
        assert started is not None, "no candidate produced a REVIEW workflow"
        thread_id = started["thread_id"]

    # --- simulate a server restart: drop the shared graph + checkpointer ---
    app_module._reset_shared_graph()

    with TestClient(app) as client:
        # A thread that truly never started still 404s.
        r = client.post(
            "/predict/graph/run-never-started/approve",
            json={"action": "approve"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 404

        # The real thread: state restored from disk, resume succeeds.
        r = client.post(
            f"/predict/graph/{thread_id}/approve",
            json={"action": "approve", "note": "approved after restart"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workflow_complete"] is True
        assert body["approval_status"] == "APPROVED"
        assert body["disbursement"] is not None
        assert body["disbursement"]["status"] == "COMPLETED"

        # The audit trail survives the restart as well.
        application_id = body["application_id"]
        r = client.get(f"/audit/{application_id}", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["thread_id"] == thread_id

    app_module._reset_shared_graph()


# ---------------------------------------------------------------------------
# Regression: the resume-after-restart path with a mutating state dict.
# ---------------------------------------------------------------------------
class _GatedWritesBucket(dict):
    """A persisted-writes bucket that yields control in the middle of a walk.

    ``FileCheckpointSaver._snapshot()`` shallow-copies only the top level of
    ``self.writes``, so the encoder walks the *live* inner
    ``writes[(thread_id, checkpoint_ns, checkpoint_id)]`` dicts with
    ``for k, v in obj.items()``.  Yielding after the first item reproduces the
    GIL hand-off that, in production, lets a second thread resize that dict
    mid-iteration ("dictionary changed size during iteration").  Only the first
    walk pauses, so the concurrent writer's own snapshot never blocks.
    """

    def __init__(self, initial, walking, resume_walk):
        super().__init__(initial)
        self._walking = walking
        self._resume_walk = resume_walk
        self._armed = True

    def items(self):
        entries = super().items()
        if not self._armed:
            return entries
        self._armed = False

        def _paused():
            for index, entry in enumerate(entries):
                if index == 1:
                    self._walking.set()
                    self._resume_walk.wait(30)
                yield entry

        return _paused()


@pytest.mark.slow
def test_api_approve_works_after_simated_restart_with_mutating_state(
    checkpoint_env, tmp_path, monkeypatch
):
    """Approve must still resume after a restart while the state dict mutates.

    Regression for ``GRAPH_RESUME_FAILED: dictionary changed size during
    iteration`` (GitHub Actions run 36206195878, HTTP 502).  LangGraph
    dispatches ``put``/``put_writes`` onto its ``BackgroundExecutor`` thread
    pool without blocking, so the saver's shared ``storage``/``writes``/``blobs``
    dicts were mutated by one thread while another walked them to persist a
    snapshot: the lock guarded only the file write, not the walk.  Only the
    resume crashed, because only the resume appends into a ``writes`` bucket
    that was restored from the checkpoint file.  The saver must serialize the
    whole read-modify-snapshot sequence.
    """
    from pipeline.agent import checkpointer as checkpointer_module
    from pipeline.storage.ledger import init_db

    monkeypatch.setenv("CREDITFLOW_LEDGER_DB", str(tmp_path / "ledger.db"))
    # The approve endpoint requires the shared API key.
    configure_auth_env(monkeypatch)
    init_db()
    app_module._reset_shared_graph()

    with TestClient(app) as client:
        started = None
        for profile in REVIEW_CANDIDATES:
            r = client.post("/predict/graph", json={"customer_data": profile})
            assert r.status_code == 200, r.text
            body = r.json()
            if body.get("approval_required"):
                started = body
                break
        assert started is not None, "no candidate produced a REVIEW workflow"
        thread_id = started["thread_id"]

    # --- simulate a server restart: drop the shared graph + checkpointer ---
    app_module._reset_shared_graph()

    walking = threading.Event()
    resume_walk = threading.Event()
    mutate_now = threading.Event()
    armed = threading.Event()
    armed.set()
    resume_bucket: list = []

    real_put_writes = checkpointer_module.FileCheckpointSaver.put_writes

    def gate_resume_writes(self, config, writes, task_id, task_path=""):
        """Gate the bucket the resume itself appends into (the loaded one)."""
        conf = config["configurable"]
        outer_key = (
            conf.get("thread_id"),
            conf.get("checkpoint_ns", ""),
            conf.get("checkpoint_id"),
        )
        if armed.is_set() and any(channel == "__resume__" for channel, _ in writes):
            armed.clear()
            resume_bucket.append(outer_key)
            self.writes[outer_key] = _GatedWritesBucket(
                self.writes[outer_key], walking, resume_walk
            )
        return real_put_writes(self, config, writes, task_id, task_path)

    monkeypatch.setattr(
        checkpointer_module.FileCheckpointSaver, "put_writes", gate_resume_writes
    )

    with TestClient(app) as client:
        # A thread that truly never started still 404s.
        r = client.post(
            "/predict/graph/run-never-started/approve",
            json={"action": "approve"},
            headers=AUTH_HEADERS,
        )
        assert r.status_code == 404

        saver = app.state.checkpointer
        stop = threading.Event()

        def concurrent_writer():
            """A second in-flight workflow writing to the shared checkpointer."""
            if not mutate_now.wait(30):
                return
            index = 0
            while not stop.is_set():
                index += 1
                thread, namespace, checkpoint = resume_bucket[0]
                try:
                    saver.put_writes(
                        {"configurable": {
                            "thread_id": thread,
                            "checkpoint_ns": namespace,
                            "checkpoint_id": checkpoint,
                        }},
                        [("__start__", {"seq": index})],
                        f"concurrent-task-{index}",
                    )
                except Exception:
                    return

        def watchdog():
            # Let the resume's snapshot walk reach the gate, give the second
            # writer time to resize the live bucket, then let the walk finish.
            if not walking.wait(30):
                return
            time.sleep(0.3)
            mutate_now.set()
            time.sleep(0.3)
            resume_walk.set()

        helpers = [
            threading.Thread(target=concurrent_writer, daemon=True),
            threading.Thread(target=watchdog, daemon=True),
        ]
        for helper in helpers:
            helper.start()

        r = client.post(
            f"/predict/graph/{thread_id}/approve",
            json={"action": "approve", "note": "approved after restart"},
            headers=AUTH_HEADERS,
        )
        resume_walk.set()
        stop.set()
        for helper in helpers:
            helper.join(timeout=15)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["workflow_complete"] is True
        assert body["approval_status"] == "APPROVED"
        assert body["disbursement"] is not None
        assert body["disbursement"]["status"] == "COMPLETED"

        # The interleaved writer did not corrupt persistence: the audit trail
        # and the approved thread both survive.
        r = client.get(f"/audit/{body['application_id']}", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["thread_id"] == thread_id
        assert thread_id in saver.threads()

    app_module._reset_shared_graph()