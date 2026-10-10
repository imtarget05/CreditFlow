"""Regression tests for the ``_encode_obj`` checkpoint-hang bug.

``FileCheckpointSaver._snapshot`` re-encodes the *entire* in-memory store
(``storage`` + ``writes`` + ``blobs``) on every ``put`` / ``put_writes``.  The
old encoder walked shared sub-objects once per reference and had no
cycle/memo guard, so a structure with heavy sharing (the accumulated
real-world checkpoint store) cost exponential time and a self-referential
object recursed until ``RecursionError``.  The multi-thread faulthandler dump
showed one executor thread stuck CPU-bound in ``_encode_obj`` while every other
executor thread blocked behind the ``_snapshot_lock`` RLock.

These tests pin three things:

1. A self-referential dict must terminate with a *clear* error, never an
   interminable recursion / ``RecursionError``.
2. A cyclic nested tuple (via a list back-reference) must terminate likewise.
3. Memoization must keep a shared, acyclic store cheap and round-trip safe
   through ``_decode_obj``.
"""
from __future__ import annotations

import json
import threading
import timeit
from collections import defaultdict

import pytest

from pipeline.agent.checkpointer import (
    FileCheckpointSaver,
    _decode_obj,
    _encode_obj,
)


def _run_with_watchdog(fn, timeout: float = 10.0) -> dict:
    """Run ``fn`` on a daemon thread; fail if it does not finish in ``timeout``.

    A plain ``pytest.raises`` cannot distinguish "raised" from "still
    recursing", so the caller also gets the wall-clock guarantee.  The thread
    is a daemon purely as a safety net: if the encoder ever spins forever the
    test process can still exit.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - report any failure mode
            box["exc"] = exc
        finally:
            box["done"] = True

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(timeout)
    if not box.get("done"):
        pytest.fail(f"encoder did not terminate within {timeout}s (hang)")
    return box


def _assert_cycle_rejected(box: dict) -> None:
    exc = box.get("exc")
    assert exc is not None, (
        "a self-referential structure must be rejected with a clear error, "
        "not silently encoded"
    )
    # RecursionError subclasses RuntimeError, so check it *before* the allowed
    # set to keep the RED signal (infinite recursion) unambiguous.
    assert not isinstance(exc, RecursionError), (
        f"_encode_obj recursed without bound (RecursionError): {exc!r}"
    )
    assert isinstance(exc, (ValueError, RuntimeError)), (
        f"expected a clear ValueError/RuntimeError, got {type(exc).__name__}: {exc!r}"
    )


# ---------------------------------------------------------------------------
# 1 & 2. Cycle / self-reference containment
# ---------------------------------------------------------------------------
def test_encode_obj_self_referential_dict_terminates():
    """A dict that contains itself must not recurse forever."""
    d: dict = {}
    d["self"] = d

    box = _run_with_watchdog(lambda: _encode_obj(d), timeout=10.0)
    _assert_cycle_rejected(box)


def test_encode_obj_cyclic_nested_tuple_terminates():
    """A tuple reachable from itself through a list must be rejected too."""
    chain: list = []
    top = (chain,)
    chain.append(top)

    box = _run_with_watchdog(lambda: _encode_obj(top), timeout=10.0)
    _assert_cycle_rejected(box)


def test_encode_obj_cyclic_list_terminates():
    """A list that appends itself is the list analogue of the dict case."""
    values: list = []
    values.append(values)

    box = _run_with_watchdog(lambda: _encode_obj(values), timeout=10.0)
    _assert_cycle_rejected(box)


# ---------------------------------------------------------------------------
# 3. Big-but-acyclic round trip + memoized performance
# ---------------------------------------------------------------------------
def _big_acyclic_structure() -> dict:
    """A realistic acyclic payload: bytes, tuple keys, nested tuples/lists.

    ``shared`` is deliberately referenced from several branches so an
    unmemoized encoder pays for it repeatedly; the memoized encoder must not.
    """
    blob = bytes(range(256)) * 16
    shared = {"channel": "risk_assessment", "score": 0.42, "flags": ["a", "b"]}
    shared_nested = (1, "two", (3.0, b"three"), [4, 5, {"six": 6}])
    return {
        ("thread-1", "", "cp-1"): [shared, dict(shared), blob],
        ("thread-2", "", "cp-2"): (
            blob,
            {"nested": shared_nested},
            [b"x", b"y", b"z"],
        ),
        "top_level": shared,
        "shared_nested": shared_nested,
    }


def test_big_acyclic_structure_round_trips_through_decode():
    original = _big_acyclic_structure()

    encoded = _encode_obj(original)

    # The encoder output must stay JSON-serializable (file format invariant).
    json.dumps(encoded)

    decoded = _decode_obj(json.loads(json.dumps(encoded)))
    assert decoded == original


def test_shared_acyclic_structure_encodes_under_time_budget():
    """Heavy sharing must be memoized, not re-walked once per reference.

    Without a memo guard the number of visits multiplies along each shared
    branch; this shape makes that blow-up unmistakable while staying small
    enough that the test is fast once memoized.
    """
    # A diamond/DAG: each level's dict is referenced by several paths below.
    shared = {"leaf": [b"data"] * 4}
    node = shared
    for _ in range(18):
        node = {"left": node, "right": node, "extra": list(range(4))}
    root = {"root": node, "again": node, "blob": b"payload" * 10}

    elapsed = timeit.timeit(lambda: _encode_obj(root), number=1)
    assert elapsed < 5.0, f"shared acyclic encode too slow: {elapsed:.2f}s"


# ---------------------------------------------------------------------------
# 4. Regression: snapshot of a simplified accumulated store is bounded
# ---------------------------------------------------------------------------
def _populate_simplified_store(saver: FileCheckpointSaver) -> None:
    """Fill the three stores the way LangGraph does, but small and realistic."""
    blob = bytes(range(64)) * 4
    shared_state = {
        "loan_amount": 120_000_000.0,
        "decision": "REVIEW",
        "approval_required": True,
        "messages": [("human", {"text": "review me"}), ("ai", {"ok": True})],
    }

    for thread_index in range(3):
        thread_id = f"run-{thread_index}"
        checkpoint_id = f"cp-{thread_index}"
        saver.storage[thread_id][""][checkpoint_id] = {
            "v": 1,
            "id": checkpoint_id,
            "channel_versions": {"__start__": "0001", "risk": "0002"},
            "versions_seen": {"__start__": {}},
            "pending_sends": [],
        }
        saver.writes[(thread_id, "", checkpoint_id)] = {
            "risk": [("task-1", "risk", shared_state)],
            "audit": [("task-2", "audit", [shared_state, shared_state])],
        }
        saver.blobs[(thread_id, "", "risk", "0002")] = blob
        saver.blobs[(thread_id, "", "audit", "0001")] = blob

    # One extra shared reference across threads to exercise the memo cache.
    saver.writes[("run-0", "", "cp-0")]["shared"] = [
        ("task-3", "shared", shared_state)
    ]


def test_snapshot_simplified_accumulated_store_completes_quickly(tmp_path):
    """A simplified accumulated store must snapshot in bounded wall clock."""
    path = tmp_path / "checkpoints.pkl"
    saver = FileCheckpointSaver(path)
    _populate_simplified_store(saver)

    start = timeit.default_timer()
    saver._snapshot()
    elapsed = timeit.default_timer() - start
    assert elapsed < 5.0, f"_snapshot too slow: {elapsed:.2f}s"

    # The snapshot must be valid JSON and reload losslessly as a checkpoint.
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw) == {"storage", "writes", "blobs"}

    reloaded = FileCheckpointSaver(path)
    assert set(reloaded.threads()) == {"run-0", "run-1", "run-2"}
    assert reloaded.storage["run-0"][""]["cp-0"]["id"] == "cp-0"
    assert reloaded.blobs[("run-0", "", "risk", "0002")] == bytes(range(64)) * 4
