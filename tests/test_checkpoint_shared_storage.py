"""Shared (multi-process) checkpoint storage — selected by ``DATABASE_URL``.

``FileCheckpointSaver`` is single-process by design: one JSON snapshot on the
local disk (pipeline/agent/checkpointer.py).  A deployment that scales past
one replica — or restarts on a container without its volume — needs the
paused ``human_approval`` state somewhere shared.  These tests pin the
selection rule:

* no ``DATABASE_URL`` (or an unsupported scheme) → the *old* file saver,
  unchanged behaviour, existing tests keep passing;
* ``DATABASE_URL`` pointing at Postgres / SQLite → the matching langgraph
  saver (``PostgresSaver`` / ``SqliteSaver``), with ``setup()`` called once so
  the checkpoint tables exist;
* shared store requested but unreachable / driver package missing → falls
  back to the file saver with a loud warning, *unless*
  ``CREDITFLOW_CHECKPOINT_SHARED_REQUIRED=1`` (then it raises instead of
  silently downgrading a multi-replica deployment to local files).
* API wiring: ``backend.app._build_shared_graph`` uses the shared saver when
  the env says so.

The driver packages (``langgraph-checkpoint-postgres`` /
``langgraph-checkpoint-sqlite``) are *not* installed in this repo, so the
routing tests publish a fake ``langgraph.checkpoint.<backend>`` module in
``sys.modules``; the live test at the bottom exercises the real thing and is
skipped unless ``LIVE_TESTS=1``.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from langgraph.checkpoint.memory import InMemorySaver

from pipeline.agent import checkpointer as checkpointer_module
from pipeline.agent.checkpointer import (
    FileCheckpointSaver,
    SharedCheckpointUnavailable,
    create_checkpointer,
    shared_database_url,
)

POSTGRES_URL = "postgresql://user:pw@localhost:5432/creditflow?sslmode=require"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Keep these tests independent of a developer's local DATABASE_URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CREDITFLOW_CHECKPOINT_SHARED_REQUIRED", raising=False)


class FakeSharedSaver:
    """Stands in for PostgresSaver / SqliteSaver (their packages are absent)."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.setup_calls = 0

    @classmethod
    def from_conn_string(cls, url: str) -> "FakeSharedSaver":
        return cls(url)

    def setup(self) -> None:
        self.setup_calls += 1


def install_shared_saver(monkeypatch, module_name: str, class_name: str, factory=None):
    """Publish a fake ``langgraph.checkpoint.<backend>`` module.

    Returns the class whose ``from_conn_string`` produced the saver, so the
    test can assert on the instance that route selection handed back.
    """
    created: list = []

    def _from_conn_string(url):
        if factory is not None:
            saver = factory(url)
        else:
            saver = FakeSharedSaver(url)
        created.append(saver)
        return saver

    class _FakeModule:
        pass

    module = _FakeModule()
    setattr(
        module,
        class_name,
        type(
            class_name,
            (),
            {"from_conn_string": staticmethod(_from_conn_string), "setup": FakeSharedSaver.setup},
        ),
    )
    monkeypatch.setitem(sys.modules, module_name, module)
    return created


def block_module(monkeypatch, module_name: str) -> None:
    """Force ``import <module_name>`` to fail (None in sys.modules → ImportError)."""
    monkeypatch.setitem(sys.modules, module_name, None)


# ---------------------------------------------------------------------------
# Default: the file saver stays in charge
# ---------------------------------------------------------------------------
def test_without_database_url_the_file_saver_stays_in_charge(tmp_path, monkeypatch):
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", str(tmp_path / "cp.json"))
    saver, backend = create_checkpointer()
    assert backend == "file"
    assert isinstance(saver, FileCheckpointSaver)
    assert saver._path == tmp_path / "cp.json"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "mysql://u:p@host/db",
        "creditflow-db",
        "https://example.com/db",
        # in-memory SQLite is per-process: not shared, so ignore it
        "sqlite://",
        "sqlite:///:memory:",
    ],
)
def test_unsupported_or_empty_database_url_is_ignored(url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    assert shared_database_url() == ""


def test_bare_sqlite_path_is_treated_as_shared_storage(tmp_path, monkeypatch):
    path = tmp_path / "shared-checkpoints.db"
    monkeypatch.setenv("DATABASE_URL", str(path))
    assert shared_database_url() == f"sqlite:///{path}"


@pytest.mark.parametrize(
    "url,expected",
    [
        ("postgres://u:p@host/db", "postgres://u:p@host/db"),
        ("postgresql://u:p@host/db", "postgresql://u:p@host/db"),
        ("sqlite:///tmp/shared.db", "sqlite:///tmp/shared.db"),
    ],
)
def test_shared_database_url_accepts_shared_schemes(url, expected, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", url)
    assert shared_database_url() == expected


# ---------------------------------------------------------------------------
# Shared routing: Postgres / SQLite
# ---------------------------------------------------------------------------
def test_postgres_url_selects_the_shared_saver_and_creates_tables(monkeypatch):
    created = install_shared_saver(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver"
    )
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)
    saver, backend = create_checkpointer()
    assert backend == "postgres"
    assert saver is created[0]
    assert saver.url == POSTGRES_URL
    # setup() must have run once so the checkpoint tables exist.
    assert saver.setup_calls == 1


def test_sqlite_url_selects_the_shared_saver(monkeypatch):
    created = install_shared_saver(
        monkeypatch, "langgraph.checkpoint.sqlite", "SqliteSaver"
    )
    url = "sqlite:///tmp/creditflow-shared.db"
    monkeypatch.setenv("DATABASE_URL", url)
    saver, backend = create_checkpointer()
    assert backend == "sqlite"
    assert saver is created[0]
    assert saver.setup_calls == 1


def test_bare_sqlite_path_routes_to_the_sqlite_saver(tmp_path, monkeypatch):
    install_shared_saver(monkeypatch, "langgraph.checkpoint.sqlite", "SqliteSaver")
    path = tmp_path / "shared-checkpoints.db"
    monkeypatch.setenv("DATABASE_URL", str(path))
    saver, backend = create_checkpointer()
    assert backend == "sqlite"
    assert saver.url == f"sqlite:///{path}"


def test_from_conn_string_context_manager_is_unwrapped_and_stays_open(
    monkeypatch,
):
    """The real langgraph-checkpoint-postgres returns a *context manager*.

    ``PostgresSaver.from_conn_string()`` is decorated with
    ``@classmethod @contextmanager`` and yields the saver.  Using it naively
    hands the graph a ``_GeneratorContextManager`` (not a BaseCheckpointSaver)
    and the ``with`` body would close the connection on ``__exit__``.
    ``_open_shared_saver`` must return the *yielded* saver, run ``setup()``
    on it, and keep the context open for the process lifetime.
    """
    events: list[str] = []

    class YieldedSaver:
        def __init__(self, url: str) -> None:
            self.url = url
            self.setup_calls = 0

        def setup(self) -> None:
            self.setup_calls += 1
            events.append("setup")

    class FakeCM:
        """Stands in for contextlib._GeneratorContextManager."""

        def __init__(self, url: str) -> None:
            self.url = url

        def __enter__(self) -> YieldedSaver:
            events.append("enter")
            return YieldedSaver(self.url)

        def __exit__(self, *exc) -> bool:
            events.append("exit")
            return False

    class _FakeModule:
        pass

    module = _FakeModule()
    module.PostgresSaver = type(
        "PostgresSaver",
        (),
        {
            "from_conn_string": classmethod(lambda cls, url: FakeCM(url)),
        },
    )
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.postgres", module)

    saver = checkpointer_module._open_shared_saver(POSTGRES_URL)

    assert isinstance(saver, YieldedSaver)
    assert saver.url == POSTGRES_URL
    assert saver.setup_calls == 1
    # __exit__ must never run: it would close the connection the graph needs.
    assert events == ["enter", "setup"]
    # ...and the context object is kept alive alongside the saver.
    assert saver.__dict__["_from_conn_string_context"] is not None


# ---------------------------------------------------------------------------
# Degraded: shared requested but unavailable
# ---------------------------------------------------------------------------
def test_missing_postgres_driver_falls_back_to_file_with_a_warning(
    tmp_path, monkeypatch, caplog
):
    # langgraph-checkpoint-postgres is genuinely not installed here; pin that
    # state so the assertion holds even if someone installs it later.
    block_module(monkeypatch, "langgraph.checkpoint.postgres")
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", str(tmp_path / "cp.json"))
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)

    with caplog.at_level(logging.WARNING, logger="pipeline.agent.checkpointer"):
        saver, backend = create_checkpointer()

    assert backend == "file"
    assert isinstance(saver, FileCheckpointSaver)
    assert "DATABASE_URL" in caplog.text
    assert "langgraph-checkpoint-postgres" in caplog.text


def test_unreachable_postgres_falls_back_to_file(tmp_path, monkeypatch, caplog):
    def boom(url):
        raise OSError("connection refused")

    install_shared_saver(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver", factory=boom
    )
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", str(tmp_path / "cp.json"))
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)

    with caplog.at_level(logging.WARNING, logger="pipeline.agent.checkpointer"):
        saver, backend = create_checkpointer()

    assert backend == "file"
    assert isinstance(saver, FileCheckpointSaver)
    assert "connection refused" in caplog.text


def test_shared_required_raises_instead_of_downgrading(monkeypatch):
    block_module(monkeypatch, "langgraph.checkpoint.postgres")
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_SHARED_REQUIRED", "1")
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)

    with pytest.raises(SharedCheckpointUnavailable) as excinfo:
        create_checkpointer()

    assert "langgraph-checkpoint-postgres" in str(excinfo.value)


def test_shared_required_raises_when_the_store_is_unreachable(monkeypatch):
    def boom(url):
        raise OSError("connection refused")

    install_shared_saver(
        monkeypatch, "langgraph.checkpoint.postgres", "PostgresSaver", factory=boom
    )
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_SHARED_REQUIRED", "true")
    monkeypatch.setenv("DATABASE_URL", POSTGRES_URL)

    with pytest.raises(SharedCheckpointUnavailable):
        create_checkpointer()


# ---------------------------------------------------------------------------
# API wiring: backend.app builds the shared graph on the selected saver
# ---------------------------------------------------------------------------
def test_shared_graph_endpoint_uses_the_shared_checkpointer(monkeypatch):
    import backend.app as app_module

    # A real BaseCheckpointSaver so the graph can compile; identity is what
    # proves the wiring picked the object create_checkpointer() handed back.
    shared = InMemorySaver()
    monkeypatch.setattr(
        checkpointer_module,
        "create_checkpointer",
        lambda path=None: (shared, "postgres"),
    )
    monkeypatch.setattr(app_module, "_shared_graph", None)
    monkeypatch.setattr(app_module.app.state, "pipeline", None, raising=False)
    monkeypatch.setattr(app_module.app.state, "meta", {}, raising=False)
    # Earlier graph-endpoint tests may leak a compiled checkpointer onto
    # app.state; _build_shared_graph() reuses any non-None value, which would
    # bypass the patched create_checkpointer() below. Clear it so the wiring
    # proves it picks the object create_checkpointer() returns.
    app_module.app.state.checkpointer = None

    try:
        app_module._build_shared_graph()
        assert app_module.app.state.checkpointer is shared
        assert app_module.app.state.checkpoint_backend == "postgres"
    finally:
        app_module._reset_shared_graph()
        if hasattr(app_module.app.state, "checkpoint_backend"):
            del app_module.app.state.checkpoint_backend


def test_shared_graph_endpoint_falls_back_to_the_file_saver(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CREDITFLOW_CHECKPOINT_DB", str(tmp_path / "cp.json"))
    import backend.app as app_module

    monkeypatch.setattr(app_module, "_shared_graph", None)
    monkeypatch.setattr(app_module.app.state, "pipeline", None, raising=False)
    monkeypatch.setattr(app_module.app.state, "meta", {}, raising=False)

    try:
        app_module._build_shared_graph()
        assert isinstance(app_module.app.state.checkpointer, FileCheckpointSaver)
        assert app_module.app.state.checkpoint_backend == "file"
    finally:
        app_module._reset_shared_graph()
        if hasattr(app_module.app.state, "checkpoint_backend"):
            del app_module.app.state.checkpoint_backend


# ---------------------------------------------------------------------------
# Live: a real PostgresSaver against a real DATABASE_URL (opt-in only)
# ---------------------------------------------------------------------------
@pytest.mark.infra
def test_real_postgres_shared_checkpointer_when_configured():
    from tests.live_infra import require_db

    require_db("postgres")
    pytest.importorskip("langgraph.checkpoint.postgres")
    if not (os.environ.get("DATABASE_URL") or "").strip():
        pytest.skip("DATABASE_URL is not configured")

    saver, backend = create_checkpointer()
    from langgraph.checkpoint.base import BaseCheckpointSaver

    assert backend == "postgres"
    assert isinstance(saver, BaseCheckpointSaver)
