"""PG/SQLite checkpoint round-trip for empty-response-guard marker kwargs.

Reviewer caveat (cadbce48 review pass): "PG runtime round-trip of new kwarg
unexercised". This file closes that caveat with a REAL
``AsyncPostgresSaver`` round-trip on a disposable PG14 cluster AND a
matching ``AsyncSqliteSaver`` round-trip on a file-backed SQLite
checkpointer.

What is being proved:

1. The ``additional_kwargs`` markers the §8.1 once-per-window nudge
   allowance keys on — ``empty_response_nudge``, ``injected_message``,
   ``attestation_nudge`` (the retroactive-heal stamp on the
   attestation-gate deny nudge) — survive a checkpoint save+restore
   cycle BYTE-EQUAL on BOTH backends. A langgraph msgpack
   serialization regression that dropped or mangled those kwargs would
   silently flip a legitimate §8.1 second-empty into an unguarded
   raise (false positive — the guard raises when the window says
   "human" + no prior spoke) or into a missing raise (false negative
   — the guard passes when the window says "human" but the LLM spoke).

2. ``_scan_turn_window`` on the RESTORED message list returns the
   SAME ``(nearest_marker, prior_spoke)`` as on the pre-save list —
   the guard's window classification is restore-invariant.

3. ``validate_llm_response(empty_ai, input_messages=restored)`` raises
   :class:`EmptyLLMResponseError` exactly as the pre-save call. The
   new ``input_messages`` kwarg (S1 turn-aware raise, introduced in
   ``abc226c7`` and refined in ``cadbce48``) is exercised on messages
   that came BACK from a real checkpointer, not just an in-memory
   list — proving the kwarg is not an in-memory-only path.

If a checkpoint save/restore cycle silently dropped those kwargs, a
resumed turn would re-scan a window that no longer recognizes its
nudges → false raises / false passes that the validator's unit suite
cannot detect (the unit suite passes in-memory lists, never a
checkpoint round-trip).

PG leg MUST execute (not skip). If the environment cannot start a
local PG14 cluster the test fails LOUD (does not silently skip) and
the dispatcher gets a blocker.

SQLite leg: file-backed ``AsyncSqliteSaver`` with WAL +
``busy_timeout=10000`` in a ``tmp_path`` (aiosqlite thread pool,
NOT StaticPool+WriteGuardSession per repo conventions).

HOUSE-STYLE NOTES (read before changing):

* Conftest mocks langgraph (``tests/conftest.py``); the
  ``_real_langgraph`` fixture evicts those mocks so REAL packages
  load — mirrors ``checkpoint_prune_real_saver.py`` /
  ``test_compaction_e2e.py`` pattern.
* ``daemon.response_validation`` reads ``additional_kwargs`` from the
  Python attribute on the restored BaseMessage object — langgraph's
  ``JsonPlusSerializer`` is the seam that can silently lose kwargs
  if the schema is widened incorrectly. The PG serializer uses the
  same JSON serializer under the hood, so any kwarg loss shows up on
  BOTH backends (which is why we test both — for backends,
  byte-equal-on-both IS the regression assertion).
* The disposable PG cluster is provisioned by the SESSION-scoped
  ``disposable_pg`` fixture; per-test disposable DBs are created
  inline (xdist-safe; never touches ``ensemble_prod``).
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile

import pytest


# ============================================================================
# Disposable PG14 cluster (session-scoped; mirrors the L1 house recipe)
# ============================================================================
# Per .agents/tester/LESSONS/2026-09-09-w1-gate-pg-fixture-wiring-and-
# provisional-new.md (L1): the interactive shell sets
# POSTGRES_HOST/POSTGRES_PORT/POSTGRES_USER/POSTGRES_DB/POSTGRES_PASSWORD
# pointing at ensemble_prod on port 5432. NEVER let those leak into
# pg_ctl/psql/initdb — scrub before EVERY subprocess call.
# Per L1 (recipe line): local initdb/pg_ctl on port 154xx works when
# docker is down; ~30s incl. teardown.

PG_BIN_DIR = "/opt/homebrew/opt/postgresql@14/bin"
PG_PORT_RANGE = (15441, 15460)  # inclusive; first FREE wins
PG_USER = "ensemble"  # trust auth — password is unused
PG_ADMIN_DB = "ensemble_test_erg_rt"


def _is_port_free(port: int) -> bool:
    """Probe a TCP port without actually claiming it (no SO_REUSEADDR dance)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _pick_free_port() -> int:
    for port in range(PG_PORT_RANGE[0], PG_PORT_RANGE[1] + 1):
        if _is_port_free(port):
            return port
    raise RuntimeError(
        f"No free port in {PG_PORT_RANGE[0]}-{PG_PORT_RANGE[1]}"
    )


def _scrubbed_env() -> dict:
    """Return a clean env dict with POSTGRES_* scrubbed.

    The interactive shell sets POSTGRES_* pointing at ensemble_prod on
    port 5432. NEVER let those leak into pg_ctl/psql/initdb. Only
    PATH (with the @14 bin prepended) and PGUSER survive.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("POSTGRES_")}
    env["PATH"] = PG_BIN_DIR + os.pathsep + env.get("PATH", "")
    env["PGUSER"] = PG_USER
    return env


def _run(args, *, env, timeout):
    cp = subprocess.run(
        args,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if cp.returncode != 0:
        raise RuntimeError(
            f"command failed: {args!r}\n"
            f"stdout={cp.stdout!r}\nstderr={cp.stderr!r}"
        )
    return cp


@pytest.fixture(scope="session")
def disposable_pg():
    """Start a disposable PG14 cluster; tear down on session exit.

    Yields ``(host, port, admin_db)``. Trust auth; never touches port
    5432 or ensemble_prod. Cluster lives in /tmp; entire datadir is
    rm -rf'd on teardown so /tmp does not accumulate.

    Failure modes (loud, not silent):
      * No free port → ``RuntimeError`` → test FAILS.
      * initdb fails → ``RuntimeError`` → test FAILS.
      * pg_ctl start fails → ``RuntimeError`` → test FAILS.
      * pg_ctl stop fails on teardown → swallowed (best-effort);
        datadir still removed.
    """
    port = _pick_free_port()
    datadir = pathlib.Path(tempfile.mkdtemp(prefix="erg_rt_pgdata_"))
    env = _scrubbed_env()
    initdb = pathlib.Path(PG_BIN_DIR) / "initdb"
    pg_ctl = pathlib.Path(PG_BIN_DIR) / "pg_ctl"
    psql = pathlib.Path(PG_BIN_DIR) / "psql"

    # initdb: -A trust (no password); -U matches what we'll use later;
    # --no-locale keeps initdb output deterministic.
    _run(
        [
            str(initdb),
            "-A", "trust",
            "-U", PG_USER,
            "--no-locale",
            "-D", str(datadir),
        ],
        env=env,
        timeout=120,
    )
    # Start cluster on the chosen port; capture log under datadir.
    _run(
        [
            str(pg_ctl),
            "-D", str(datadir),
            "-l", str(datadir / "pg.log"),
            "-o", f"-p {port}",
            "start",
        ],
        env=env,
        timeout=60,
    )

    try:
        # Create the admin DB (initdb creates only `postgres`).
        _run(
            [
                str(psql),
                "-h", "127.0.0.1",
                "-p", str(port),
                "-U", PG_USER,
                "-d", "postgres",
                "-c", f'CREATE DATABASE "{PG_ADMIN_DB}"',
            ],
            env=env,
            timeout=30,
        )

        # Hand off — host/port/admin_db so the per-test code can build
        # its own disposable DB inside this cluster.
        yield "127.0.0.1", port, PG_ADMIN_DB
    finally:
        # Best-effort teardown: stop cluster, then rm -rf datadir.
        # Both run with the SCRUBBED env so prod credentials cannot
        # re-enter even if the subprocess is somehow re-pointed.
        try:
            _run(
                [str(pg_ctl), "-D", str(datadir), "-m", "fast", "stop"],
                env=_scrubbed_env(),
                timeout=60,
            )
        except Exception:
            pass
        shutil.rmtree(datadir, ignore_errors=True)


# ============================================================================
# Langgraph mock eviction (root conftest mocks langgraph globally)
# ============================================================================
# The root tests/conftest.py injects MagicMock langgraph modules at
# collection time so unit tests don't have to load the real package.
# Integration tests that touch real AsyncPostgresSaver /
# AsyncSqliteSaver must evict those mocks first; mirroring the
# pattern in tests/integration/checkpoint_prune_real_saver.py and
# tests/integration/test_compaction_e2e.py.
_LANGGRAPH_MOCK_KEYS = [
    "langgraph",
    "langgraph.graph",
    "langgraph.graph.state",
    "langgraph.prebuilt",
    "langgraph.constants",
    "langgraph.checkpoint",
    "langgraph.checkpoint.memory",
    "langgraph.checkpoint.sqlite",
    "langgraph.checkpoint.sqlite.aio",
    # PG-specific keys the root conftest does not mock today, but
    # evict anyway in case a future conftest revision adds them.
    "langgraph.checkpoint.postgres",
    "langgraph.checkpoint.postgres.aio",
]


@pytest.fixture(autouse=True)
def _real_langgraph():
    """Evict the root conftest's langgraph mocks so REAL packages load."""
    saved = {}
    for key in _LANGGRAPH_MOCK_KEYS:
        if key in sys.modules:
            saved[key] = sys.modules[key]
            del sys.modules[key]
    try:
        yield
    finally:
        # Restore mocks for other tests. Modules imported by THIS test
        # during the real-package window are dropped so the next test's
        # conftest mock takes effect cleanly.
        for key in _LANGGRAPH_MOCK_KEYS:
            if key in sys.modules and key not in saved:
                del sys.modules[key]
        for key, mod in saved.items():
            sys.modules[key] = mod


# ============================================================================
# Empty-guard config reset (validate_llm_response reads installed state)
# ============================================================================
# daemon/response_validation.py reads module-level
# _EMPTY_RESPONSE_GUARD_ENABLED (installed by load_config). The unit
# suite uses _reset_empty_guard_config_for_tests to mirror the
# documented defaults. This integration test mirrors the same
# defaults so the S1 raise actually fires when expected.
@pytest.fixture(autouse=True)
def _restore_empty_guard_defaults():
    from daemon.response_validation import _reset_empty_guard_config_for_tests
    _reset_empty_guard_config_for_tests()
    yield
    _reset_empty_guard_config_for_tests()


# ============================================================================
# Helpers — the message list the test validates
# ============================================================================
NUDGE_TEXT = (
    "Continue with your task, or provide your final response "
    "if you are finished."
)


def _build_roundtrip_messages():
    """[real human, assistant spoke, nudge] — the in-scope input list.

    The empty-content AIMessage that the validator REJECTS is NOT in
    this list — it is the ``response`` argument to
    ``validate_llm_response``. The list IS the ``input_messages``
    argument; the §8.1 second-empty raise requires a nudge-nearest
    window. Walking backwards from the end:

        3. nudge (HumanMessage w/ empty_response_nudge=True)  → nearest
        2. AI w/ non-empty content                              → spoke
        1. real human                                           → boundary

    Pre-save result of ``_scan_turn_window``: ``("nudge", True)``.
    With the nudge marker surviving → ``validate_llm_response`` raises
    ``EmptyLLMResponseError`` (the §8.1 nudge allowance wins over
    ``prior_spoke=True``).
    """
    from langchain_core.messages import AIMessage, HumanMessage
    return [
        HumanMessage(content="What is the status of the migration?",
                     id="human-1"),
        AIMessage(content="Migration is 80% complete, ETA 2 hours.",
                  id="ai-1"),
        HumanMessage(
            content=NUDGE_TEXT,
            id="nudge-1",
            additional_kwargs={
                "injected_message": True,
                "empty_response_nudge": True,
            },
        ),
    ]


def _empty_response():
    """The current-LLM-output AIMessage that the validator must reject."""
    from langchain_core.messages import AIMessage
    return AIMessage(content="", id="empty-1")


# ============================================================================
# Common round-trip contract (parameterized; runs for SQLite + PG)
# ============================================================================
# Build the StateGraph + ainvoke + aget_state seam once; the two legs
# differ only in their checkpointer factory. Returns the restored
# message list (deserialized BaseMessage objects).
async def _round_trip_via(saver_factory, messages):
    """Persist messages through ``saver_factory()`` and read them back.

    ``saver_factory`` is a zero-arg async callable that returns a
    LangGraph checkpointer (AsyncSqliteSaver / AsyncPostgresSaver) with
    ``setup()`` already called. Caller owns the saver lifecycle.
    """
    from langgraph.graph import END, START, MessagesState, StateGraph

    class _S(MessagesState):
        pass

    def node_fn(state):  # no-op — we only test message persistence
        return {"messages": []}

    g = StateGraph(_S)
    g.add_node("n", node_fn)
    g.add_edge(START, "n")
    g.add_edge("n", END)

    saver = await saver_factory()
    try:
        app = g.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "t-empty-guard-rt"}}
        await app.ainvoke({"messages": messages}, config)
        state = await app.aget_state(config)
        return list(state.values.get("messages", []))
    finally:
        # Saver-connection lifecycle is the factory's responsibility —
        # SQLite factory closes its own aiosqlite conn; PG factory
        # closes its own psycopg.AsyncConnection. The saver object
        # itself does not own a single close() method, so we do not
        # call one here.
        pass


async def _sqlite_round_trip(messages):
    """File-backed AsyncSqliteSaver round-trip in a tmp_path.

    Mirrors the recipe in the project conventions ("File-backed
    SQLite: tmp_path + NullPool + WAL + busy_timeout=10000"); the
    aiosqlite library is the project's de-facto NullPool-equivalent
    (it runs SQLite ops on a background thread pool, NOT a
    StaticPool+WriteGuardSession).
    """
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    with tempfile.TemporaryDirectory() as td:
        db_path = pathlib.Path(td) / "ckpt.db"

        async def factory():
            conn = await aiosqlite.connect(str(db_path))
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=10000")
            saver = AsyncSqliteSaver(conn)
            await saver.setup()
            return saver

        try:
            return await _round_trip_via(factory, messages)
        finally:
            # aiosqlite connections close cleanly when the factory's
            # conn goes out of scope; explicit close is best-effort.
            pass


async def _pg_round_trip(messages, host, port, admin_db):
    """AsyncPostgresSaver round-trip on the disposable cluster.

    Creates a uniquely-named disposable DB inside the session cluster
    (xdist-safe; never touches ``ensemble_prod``), runs the
    production-shaped saver setup (psycopg autocommit +
    ``prepare_threshold=0`` + ``dict_row``, then
    ``AsyncPostgresSaver.setup()``), executes the round-trip, and
    drops the disposable DB on exit. Mirrors
    ``daemon/persistence.py::create_postgres_checkpointer``.
    """
    import asyncpg
    import psycopg
    from psycopg.rows import dict_row

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    # Per-test disposable DB inside the session cluster.
    db_name = f"ensemble_erg_rt_{os.getpid()}_{id(messages) & 0xffff:x}"
    admin_dsn = f"postgresql://{PG_USER}@127.0.0.1:{port}/{admin_db}"
    target_dsn = f"postgresql://{PG_USER}@127.0.0.1:{port}/{db_name}"

    conn = await asyncpg.connect(admin_dsn)
    try:
        await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()

    saver_conn = None
    try:
        # Production-shaped setup. autocommit=True because the saver
        # manages its own transactions. prepare_threshold=0 matches
        # the upstream AsyncPostgresSaver recommendation.
        saver_conn = await psycopg.AsyncConnection.connect(
            target_dsn,
            autocommit=True,
            prepare_threshold=0,
            row_factory=dict_row,
        )
        saver = AsyncPostgresSaver(conn=saver_conn)
        await saver.setup()

        async def factory():
            return saver

        return await _round_trip_via(factory, messages)
    finally:
        if saver_conn is not None:
            try:
                await saver_conn.close()
            except Exception:
                pass
        # Drop the disposable DB — best-effort; older PG14 may lack
        # WITH (FORCE), fall back to terminate + drop.
        cleanup = await asyncpg.connect(admin_dsn)
        try:
            try:
                await cleanup.execute(
                    f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'
                )
            except Exception:
                await cleanup.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = $1 AND pid <> pg_backend_pid()",
                    db_name,
                )
                await cleanup.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await cleanup.close()


# ============================================================================
# SQLite leg
# ============================================================================
async def test_sqlite_round_trip_preserves_nudge_markers_and_guard_semantics():
    """SQLite leg: nudge markers byte-survive save+restore.

    Asserts (a) ``additional_kwargs`` byte-equal pre/post on the nudge
    message, (b) ``_scan_turn_window`` returns the same
    ``(nearest_marker, prior_spoke)`` on the restored list, (c)
    ``validate_llm_response(empty_ai, input_messages=restored)``
    raises ``EmptyLLMResponseError`` exactly as the pre-save call.
    """
    from daemon.response_validation import (
        EmptyLLMResponseError,
        _scan_turn_window,
        validate_llm_response,
    )

    pre_save = _build_roundtrip_messages()
    pre_marker, pre_spoke = _scan_turn_window(pre_save)

    # Pre-save sanity: validate MUST raise on the in-memory list. If it
    # does not, the test's whole premise is wrong — fail LOUD, do not
    # silently produce a green test against a misconfigured guard.
    with pytest.raises(EmptyLLMResponseError):
        validate_llm_response(_empty_response(), input_messages=list(pre_save))

    restored = await _sqlite_round_trip(list(pre_save))

    # (a) Markers byte-survive on the nudge message — explicit failure
    # beats implicit. If langgraph's serializer ever drops kwargs, this
    # is the line that catches it.
    nudge_pre = pre_save[2]
    nudge_post = restored[2]
    assert nudge_post.type == "human"
    assert nudge_post.content == nudge_pre.content
    assert nudge_post.additional_kwargs == nudge_pre.additional_kwargs, (
        f"nudge kwargs mangled by SQLite round-trip: "
        f"pre={nudge_pre.additional_kwargs!r} "
        f"post={nudge_post.additional_kwargs!r}"
    )
    # Specific marker pins (defensive — explicit failure beats implicit).
    assert nudge_post.additional_kwargs.get("empty_response_nudge") is True
    assert nudge_post.additional_kwargs.get("injected_message") is True

    # (b) _scan_turn_window on the restored list returns the SAME
    # (marker, prior_spoke). A divergence here means the guard's
    # window classification changed across the round-trip — the very
    # class of regression this test was written to catch.
    post_marker, post_spoke = _scan_turn_window(restored)
    assert (post_marker, post_spoke) == (pre_marker, pre_spoke), (
        f"window scan diverged across SQLite round-trip: "
        f"pre=({pre_marker!r}, {pre_spoke!r}) "
        f"post=({post_marker!r}, {post_spoke!r})"
    )

    # (c) validate_llm_response raises on the restored list exactly as
    # on the pre-save list. This is the §8.1 once-per-window nudge
    # allowance — it MUST remain consumed post-restore, otherwise a
    # resumed turn would silently re-classify the second empty as a
    # first-empty-after-tool and lose the raise.
    with pytest.raises(EmptyLLMResponseError):
        validate_llm_response(_empty_response(), input_messages=restored)


# ============================================================================
# PG leg
# ============================================================================
async def test_pg_round_trip_preserves_nudge_markers_and_guard_semantics(
    disposable_pg,
):
    """PG leg: nudge markers byte-survive save+restore on a REAL
    ``AsyncPostgresSaver`` against a disposable PG14 cluster.

    Same matrix as the SQLite leg. This leg MUST RUN — a skip means
    the reviewer caveat "PG runtime round-trip of new kwarg
    unexercised" is unaddressed. The ``disposable_pg`` session fixture
    raises (the test fails, NOT skips) if PG cannot start.
    """
    from daemon.response_validation import (
        EmptyLLMResponseError,
        _scan_turn_window,
        validate_llm_response,
    )

    host, port, admin_db = disposable_pg

    pre_save = _build_roundtrip_messages()
    pre_marker, pre_spoke = _scan_turn_window(pre_save)
    with pytest.raises(EmptyLLMResponseError):
        validate_llm_response(_empty_response(), input_messages=list(pre_save))

    restored = await _pg_round_trip(list(pre_save), host, port, admin_db)

    # (a) Markers byte-survive on the nudge message.
    nudge_pre = pre_save[2]
    nudge_post = restored[2]
    assert nudge_post.type == "human"
    assert nudge_post.content == nudge_pre.content
    assert nudge_post.additional_kwargs == nudge_pre.additional_kwargs, (
        f"nudge kwargs mangled by PG round-trip: "
        f"pre={nudge_pre.additional_kwargs!r} "
        f"post={nudge_post.additional_kwargs!r}"
    )
    assert nudge_post.additional_kwargs.get("empty_response_nudge") is True
    assert nudge_post.additional_kwargs.get("injected_message") is True

    # (b) _scan_turn_window same result.
    post_marker, post_spoke = _scan_turn_window(restored)
    assert (post_marker, post_spoke) == (pre_marker, pre_spoke), (
        f"PG window scan diverged: "
        f"pre=({pre_marker!r}, {pre_spoke!r}) "
        f"post=({post_marker!r}, {post_spoke!r})"
    )

    # (c) validate_llm_response raises on restored list.
    with pytest.raises(EmptyLLMResponseError):
        validate_llm_response(_empty_response(), input_messages=restored)