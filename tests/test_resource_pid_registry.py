"""PID registry test pack (Phase-2 T6-T9 / AC4, AC5, AC6).

Covers:

  * AC4 — PID-recycle defense: a lookup against a pid whose
    ``/proc/<pid>/stat`` field 22 (starttime jiffies) no longer
    matches the registered value returns ``None``; the ``pgid``
    fallback (field 5) resolves when the starttime check cannot;
    ``parent_pid`` is recorded on the entry.
  * AC5 — bash success-path exit-mark (the REQUIRED leak fix):
    a successful ``bash`` call marks the pid_registry entry exited
    with ``exit_code`` + ``VmHWM``; N successful runs leave ZERO
    monotonic registry growth; the timeout path is unchanged.
  * AC6 — chart_render + mcp_child stamps: both kinds land in the
    live RAM dict AND are persisted to ``resource_pid_map`` (T3)
    via the flusher. The chart seam's exact pinned line is
    recorded in a comment (chart_tools.py:918 —
    ``invoke_agent_and_wait``).

FENCE DISCIPLINE (2026-10-12 critical note): every synthetic tree
is built under pytest ``tmp_path`` with the exact starttime/pgid
fields the parsers read; ``FAKE_HOME`` is mktemp; the real
``/proc`` is NEVER resolved (the escape-guard test asserts the
registry's ``proc_root`` is the fixture root, not ``/proc``).
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from daemon.services.resource_monitor.pid_registry import (
    EXIT_KIND_CANCELLED,
    EXIT_KIND_SUCCESS,
    EXIT_KIND_TIMEOUT,
    KIND_BASH,
    KIND_CHART_RENDER,
    KIND_MCP_CHILD,
    PidRegistry,
    PidRegistryEntry,
    read_proc_stat_field,
    reset_pid_registry_for_tests,
)


# ──────────────────────────────────────────────────────────────────────────
# Fence
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def _fence_session():
    os.environ["ENSEMBLE_SELF_ENV"] = "dev"
    allowed_dbs = {"ensemble_dev_test_only", "ensemble_test_only"}
    if os.environ.get("POSTGRES_DB") in (None, "", "ensemble_prod"):
        os.environ["POSTGRES_DB"] = "ensemble_dev_test_only"
    elif os.environ.get("POSTGRES_DB") not in allowed_dbs:
        pytest.exit(
            f"HARD ABORT: POSTGRES_DB={os.environ.get('POSTGRES_DB')!r}",
            returncode=1,
        )
    fake = os.environ.get("FAKE_HOME")
    if not fake or not os.path.isabs(fake):
        fake = tempfile.mkdtemp(prefix="ens-fake-home-")
        os.environ["FAKE_HOME"] = fake
        os.environ["HOME"] = fake
    yield


# ──────────────────────────────────────────────────────────────────────────
# Synthetic /proc fixture (exactly the fields the parsers read)
# ──────────────────────────────────────────────────────────────────────────

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent / "daemon" / "migrations" / "versions"
)
_PID_MAP_MIGRATION = "20261018_090003_create_resource_pid_map.sql"


def _write_proc_stat(
    proc_root: Path,
    pid: int,
    *,
    ppid: int = 1,
    pgid: int | None = None,
    starttime: int = 1000,
    comm: str = "python",
) -> None:
    """Write a ``/proc/<pid>/stat`` with the parser-read fields set.

    Field 4 (ppid) = ``ppid``; field 5 (pgrp/pgid) = ``pgid`` (or
    ``pid``); field 22 (starttime) = ``starttime``. Trailing
    zero-padding fills the rest so the parser's 1-indexed offset
    math (``parts[field-3]``) lands correctly.
    """
    d = proc_root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    pgid_val = pgid if pgid is not None else pid
    # parts[] after the ``)`` split are 1-indexed fields 3..N.
    # index 0 = field 3 (state), index 1 = field 4 (ppid),
    # index 2 = field 5 (pgrp), ..., index 19 = field 22 (starttime).
    fields = [
        "R",            # field 3  state
        str(ppid),      # field 4  ppid
        str(pgid_val),  # field 5  pgrp / pgid
    ]
    # Pad to index 19.
    while len(fields) < 19:
        fields.append("0")
    fields.append(str(starttime))  # index 19 = field 22
    # A few extra trailing fields (harmless).
    fields.extend(["0"] * 10)
    (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")


@pytest.fixture
def proc_root(tmp_path) -> Path:
    """A synthetic ``/proc`` root under tmp_path."""
    return tmp_path / "proc"


@pytest.fixture
def registry(proc_root):
    """A fresh PidRegistry bound to the synthetic proc root."""
    reg = PidRegistry(proc_root=proc_root)
    yield reg
    reg._reset()


@pytest.fixture
def patched_singleton(proc_root):
    """Replace the process singleton with one bound to the fixture.

    Recovers the original singleton's state afterward so other
    tests are not polluted.
    """
    reg = reset_pid_registry_for_tests(proc_root=proc_root)
    yield reg
    reg._reset()


# ──────────────────────────────────────────────────────────────────────────
# AC4 — PID-recycle defense
# ──────────────────────────────────────────────────────────────────────────


class TestAC4PidRecycleDefense:
    def test_lookup_matches_when_starttime_agrees(self, registry, proc_root):
        _write_proc_stat(proc_root, 4242, starttime=555001)
        registry.register(pid=4242, start_time=555001, kind=KIND_BASH)
        entry = registry.lookup(4242)
        assert entry is not None
        assert entry.pid == 4242
        assert entry.start_time == 555001

    def test_lookup_refuses_when_starttime_differs(self, registry, proc_root):
        """PID recycled: /proc starttime no longer matches."""
        registry.register(pid=4242, start_time=555001, kind=KIND_BASH)
        # The pid directory now carries a DIFFERENT starttime —
        # simulates the kernel reusing the pid for a new process.
        _write_proc_stat(proc_root, 4242, starttime=999999)
        assert registry.lookup(4242) is None, (
            "PID-recycle defense failed: lookup returned an entry "
            "whose starttime does not match /proc now"
        )

    def test_lookup_pgid_fallback_resolves(self, registry, proc_root):
        """When starttime is unreadable/gone, pgid fallback resolves."""
        registry.register(
            pid=5555, start_time=111, kind=KIND_BASH, pgid=5555
        )
        # Write a stat whose starttime does NOT match, but whose
        # pgid DOES — simulates a seam-escape where the entry's
        # starttime is stale but the process group still matches.
        _write_proc_stat(proc_root, 5555, pgid=5555, starttime=777777)
        entry = registry.lookup(5555)
        assert entry is not None, "pgid fallback did not resolve"
        assert entry.pgid == 5555

    def test_lookup_returns_none_when_no_proc_and_no_pgid(self, registry, proc_root):
        """No /proc entry + no pgid → None (cannot verify)."""
        registry.register(pid=6666, start_time=222, kind=KIND_BASH)
        # No /proc/<pid> written at all.
        assert registry.lookup(6666) is None

    def test_parent_pid_recorded(self, registry):
        entry = registry.register(
            pid=7001,
            start_time=333,
            kind=KIND_BASH,
            parent_pid=1234,
        )
        assert entry.parent_pid == 1234
        snap = registry.snapshot()
        assert snap[0].parent_pid == 1234

    def test_register_idempotent_same_starttime(self, registry):
        e1 = registry.register(pid=8001, start_time=444, kind=KIND_BASH)
        e2 = registry.register(pid=8001, start_time=444, kind=KIND_BASH)
        assert e1 is e2
        assert registry.live_count() == 1

    def test_register_recycled_pid_force_exits_old(self, registry):
        """A second register with a DIFFERENT starttime force-exits."""
        registry.register(pid=9001, start_time=100, kind=KIND_BASH)
        registry.register(pid=9001, start_time=200, kind=KIND_MCP_CHILD)
        assert registry.live_count() == 1
        snap = registry.snapshot()
        assert snap[0].start_time == 200
        # The old entry moved to history.
        hist = registry.drain_history()
        assert len(hist) == 1
        assert hist[0].start_time == 100

    def test_mark_exit_refuses_mismatched_starttime(self, registry):
        registry.register(pid=9101, start_time=100, kind=KIND_BASH)
        out = registry.mark_exit(
            pid=9101, start_time=999, exit_kind=EXIT_KIND_SUCCESS
        )
        assert out is None
        assert registry.live_count() == 1

    def test_mark_exit_idempotent(self, registry):
        registry.register(pid=9201, start_time=100, kind=KIND_BASH)
        first = registry.mark_exit(
            pid=9201, start_time=100, exit_kind=EXIT_KIND_SUCCESS
        )
        second = registry.mark_exit(
            pid=9201, start_time=100, exit_kind=EXIT_KIND_SUCCESS
        )
        assert first is not None
        assert second is None  # already gone
        assert registry.pending_history() == 1

    def test_invalid_kind_rejected(self, registry):
        with pytest.raises(ValueError):
            registry.register(pid=10, start_time=1, kind="bogus")

    def test_invalid_exit_kind_rejected(self, registry):
        registry.register(pid=11, start_time=1, kind=KIND_BASH)
        with pytest.raises(ValueError):
            registry.mark_exit(pid=11, start_time=1, exit_kind="bogus")


class TestAC4EscapeGuard:
    """2026-10-12 fence: the registry must NEVER read live /proc."""

    def test_read_proc_stat_field_fixture_only(self, proc_root):
        _write_proc_stat(proc_root, 123, starttime=4242)
        assert read_proc_stat_field(proc_root, 123, 22) == 4242
        # A pid NOT in the fixture returns None — never falls back
        # to the real /proc (the fixture root is authoritative).
        assert read_proc_stat_field(proc_root, 987654, 22) is None

    def test_registry_proc_root_is_fixture(self, registry, proc_root, tmp_path):
        assert registry._proc_root == proc_root
        assert str(registry._proc_root) != "/proc"
        assert str(tmp_path) in str(registry._proc_root)


# ──────────────────────────────────────────────────────────────────────────
# AC5 — bash success-path exit-mark
# ──────────────────────────────────────────────────────────────────────────


class TestAC5BashSuccessPath:
    def test_success_path_marks_exit(self, patched_singleton, proc_root):
        """spawn → wait success ⇒ entry exits with exit_code + VmHWM."""
        from daemon.tools.bash import bash

        async def _run():
            res = await bash.coroutine(
                command=["echo", "ok"],
                timeout=5,
                instance_id="ac5-instance",
            )
            assert "EXIT CODE: 0" in res

        asyncio.run(_run())
        # The leak fix: no live entry remains after a successful call.
        assert patched_singleton.live_count() == 0, (
            f"LEAK: success path left {patched_singleton.live_count()} "
            f"live entries"
        )
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        e = hist[0]
        assert e.exit_kind == EXIT_KIND_SUCCESS
        assert e.exit_code == 0
        assert e.kind == KIND_BASH
        assert e.instance_id == "ac5-instance"
        # VmHWM captured (real /proc read on the live child; the
        # bash lane reads it best-effort — a None is permitted if
        # the proc was reaped first, so we only assert when present).
        if e.peak_rss_kb is not None:
            assert e.peak_rss_kb >= 0

    def test_n_success_runs_zero_monotonic_growth(
        self, patched_singleton, proc_root
    ):
        """N successful bash calls ⇒ ZERO growth in BOTH registries
        (N-1: the pid_registry twin AND the old BashProcessRegistry
        killpg tracker)."""
        from daemon.tools.bash import bash, get_bash_process_registry

        # Hermetic start: the old registry is a process singleton
        # shared across the pack — clear any residue from earlier
        # tests before asserting zero growth.
        get_bash_process_registry()._entries.clear()

        async def _run_n(n: int):
            for _ in range(n):
                await bash.coroutine(
                    command=["true"], timeout=5, instance_id="ac5-n"
                )

        asyncio.run(_run_n(15))
        assert patched_singleton.live_count() == 0, (
            f"LEAK: {patched_singleton.live_count()} live entries "
            f"after 15 successful runs"
        )
        # N-1: the OLD registry must also return to zero — the
        # success arm unregisters its (instance_id, pid, pgid) row.
        assert get_bash_process_registry().tracked_count() == 0, (
            f"LEAK (old registry): "
            f"{get_bash_process_registry().tracked_count()} tracked rows "
            f"after 15 successful runs"
        )
        hist = patched_singleton.drain_history()
        assert len(hist) == 15, f"expected 15 history rows; got {len(hist)}"
        assert all(h.exit_kind == EXIT_KIND_SUCCESS for h in hist)

    def test_timeout_path_behavior_unchanged(self, patched_singleton, proc_root):
        """The timeout arm still exits the entry, exit_kind=timeout."""
        from daemon.tools.bash import bash

        async def _run():
            res = await bash.coroutine(
                command=["sleep", "5"],
                timeout=1,
                instance_id="ac5-timeout",
            )
            assert "timed out" in res.lower()

        asyncio.run(_run())
        assert patched_singleton.live_count() == 0
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        assert hist[0].exit_kind == EXIT_KIND_TIMEOUT

    def test_command_sanitized_no_env_values(self, patched_singleton, proc_root):
        """D14: the stamped command must NOT carry env values/markers."""
        from daemon.tools.bash import bash

        async def _run():
            await bash.coroutine(
                command=["echo", "hi"],
                timeout=5,
                instance_id="ac5-sanitize",
                env={"MY_SECRET_KEY": "super-secret-value"},
            )

        asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        e = hist[0]
        joined = f"{e.command or ''} {e.detail or ''}"
        assert "super-secret-value" not in joined, (
            f"env VALUE leaked into stamp: {joined!r}"
        )
        assert "__ENS_SECRET__" not in joined, (
            f"secret marker leaked into stamp: {joined!r}"
        )
        # The env KEYS are permitted in detail (forensic correlation).
        assert "MY_SECRET_KEY" in (e.detail or "")


# ──────────────────────────────────────────────────────────────────────────
# AC6 — chart_render + mcp_child stamps (+ T3 history)
# ──────────────────────────────────────────────────────────────────────────


class TestAC6ChartRenderStamp:
    def test_chart_kind_hint_stamps_bash_children(
        self, patched_singleton, proc_root
    ):
        """With the chart_render hint pushed, bash children stamp
        kind='chart_render' instead of the default 'bash'."""
        from daemon.tools.bash import _PID_KIND_HINT, bash

        async def _run():
            token = _PID_KIND_HINT.set(KIND_CHART_RENDER)
            try:
                await bash.coroutine(
                    command=["echo", "render"],
                    timeout=5,
                    instance_id="chart-instance",
                )
            finally:
                _PID_KIND_HINT.reset(token)

        asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        assert hist[0].kind == KIND_CHART_RENDER, (
            f"expected kind=chart_render; got {hist[0].kind}"
        )

    def test_chart_kind_hint_does_not_leak(self, patched_singleton, proc_root):
        """The hint is reset — a follow-up bash call is kind='bash'."""
        from daemon.tools.bash import _PID_KIND_HINT, _current_pid_kind, bash

        async def _run():
            token = _PID_KIND_HINT.set(KIND_CHART_RENDER)
            _PID_KIND_HINT.reset(token)
            # After reset, the default kind applies.
            assert _current_pid_kind() == KIND_BASH
            await bash.coroutine(
                command=["echo", "plain"], timeout=5, instance_id="plain-inst"
            )

        asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        assert hist[0].kind == KIND_BASH

    def test_chart_render_seam_line_recorded(self):
        """AC6: the chart seam's exact pinned line is recorded."""
        chart_src = (
            Path(__file__).resolve().parent.parent
            / "daemon" / "tools" / "chart_tools.py"
        ).read_text()
        # The pinned seam (verified at HEAD 2f4adb19a):
        #   daemon/tools/chart_tools.py:918
        #   result, child_instance_id = await invoke_agent_and_wait(
        assert "kind_hint_token = _PID_KIND_HINT.set(KIND_CHART_RENDER)" in chart_src
        assert "invoke_agent_and_wait(" in chart_src
        assert "_PID_KIND_HINT.reset(kind_hint_token)" in chart_src


class TestAC6McpChildStamp:
    def test_mcp_child_scan_registers_matching_proc(self, tmp_path, recipe=None):
        from daemon.mcp.stdio_wrapper import scan_mcp_child_pids

        proc = tmp_path / "proc"
        _write_proc_stat(proc, 81001, ppid=os.getpid(), starttime=70001,
                         comm="uvx")
        (proc / "81001" / "cmdline").write_bytes(
            b"\x00".join([b"uvx", b"mcp-server-test"]) + b"\x00"
        )
        # Non-matching: different command.
        _write_proc_stat(proc, 81002, ppid=os.getpid(), starttime=70002,
                         comm="npx")
        (proc / "81002" / "cmdline").write_bytes(
            b"\x00".join([b"npx", b"other"]) + b"\x00"
        )
        matches = scan_mcp_child_pids(
            command="uvx",
            args=["mcp-server-test"],
            proc_root=proc,
            parent_pid=os.getpid(),
        )
        assert matches == [(81001, 70001)], f"got {matches}"

    def test_mcp_child_stamp_via_wrapper(self, patched_singleton, tmp_path):
        """TaskScopedStdioClient stamps + exit-marks its children."""
        import types
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import daemon.mcp.stdio_wrapper as sw

        proc = tmp_path / "proc"
        _write_proc_stat(proc, 82001, ppid=os.getpid(), starttime=80001,
                         comm="uvx")
        (proc / "82001" / "cmdline").write_bytes(
            b"\x00".join([b"uvx", b"mcp-server-x"]) + b"\x00"
        )
        patched_singleton._proc_root = proc
        orig_proc_root = sw._PROC_ROOT
        sw._PROC_ROOT = proc

        @asynccontextmanager
        async def fake_client(server_params):
            yield ("r", "w")

        async def _run():
            # The suite's conftest globally mocks ``mcp`` — so
            # ``sw.StdioServerParameters`` is a MagicMock here. The
            # wrapper duck-types on ``.command`` / ``.args``, so a
            # lightweight namespace is the correct test shape.
            params = types.SimpleNamespace(
                command="uvx", args=["mcp-server-x"]
            )
            with patch.object(sw.mcp, "stdio_client", fake_client):
                cm = sw.TaskScopedStdioClient(
                    params, instance_id="mcp-inst", server_name="bx"
                )
                await cm.__aenter__()
                live = patched_singleton.snapshot()
                assert len(live) == 1
                assert live[0].kind == KIND_MCP_CHILD
                assert live[0].pid == 82001
                assert live[0].instance_id == "mcp-inst"
                await cm.__aexit__(None, None, None)

        try:
            asyncio.run(_run())
        finally:
            sw._PROC_ROOT = orig_proc_root
        assert patched_singleton.live_count() == 0
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        assert hist[0].kind == KIND_MCP_CHILD

    def test_mcp_child_nullable_instance_id(self, patched_singleton, tmp_path):
        """Pool-warmup shape: no instance_id ⇒ NULL row (D11)."""
        import types
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import daemon.mcp.stdio_wrapper as sw

        proc = tmp_path / "proc"
        _write_proc_stat(proc, 83001, ppid=os.getpid(), starttime=80011,
                         comm="uvx")
        (proc / "83001" / "cmdline").write_bytes(
            b"\x00".join([b"uvx", b"mcp-server-pool"]) + b"\x00"
        )
        patched_singleton._proc_root = proc
        orig_proc_root = sw._PROC_ROOT
        sw._PROC_ROOT = proc

        @asynccontextmanager
        async def fake_client(server_params):
            yield ("r", "w")

        async def _run():
            params = types.SimpleNamespace(
                command="uvx", args=["mcp-server-pool"]
            )
            with patch.object(sw.mcp, "stdio_client", fake_client):
                cm = sw.TaskScopedStdioClient(params)
                await cm.__aenter__()
                live = patched_singleton.snapshot()
                assert live[0].instance_id is None
                assert live[0].detail == "server=<pool>"
                await cm.__aexit__(None, None, None)

        try:
            asyncio.run(_run())
        finally:
            sw._PROC_ROOT = orig_proc_root
        assert patched_singleton.live_count() == 0


# ──────────────────────────────────────────────────────────────────────────
# AC6 — T3 history write (the flusher persists exited rows)
# ──────────────────────────────────────────────────────────────────────────


def _split_sql_up(sql_text: str) -> list[str]:
    if "-- DOWN" in sql_text:
        sql_text = sql_text.split("-- DOWN")[0]
    code = "\n".join(
        ln for ln in sql_text.splitlines() if not ln.lstrip().startswith("--")
    )
    return [s.strip() for s in code.split(";") if s.strip()]


@pytest.fixture
def sqlite_engine(tmp_path):
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        f"sqlite:///{tmp_path / 'pidmap.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    sql = (_MIGRATIONS_DIR / _PID_MAP_MIGRATION).read_text()
    with engine.begin() as conn:
        for stmt in _split_sql_up(sql):
            conn.execute(text(stmt))
    return engine


class TestAC6T3HistoryWrite:
    def test_flusher_writes_pid_map_history(self, sqlite_engine, tmp_path):
        from daemon.services.resource_monitor import Flusher, RetentionPolicy, TierRing

        proc = tmp_path / "proc"
        reg = PidRegistry(proc_root=proc)
        reg.register(
            pid=5001, start_time=100, kind=KIND_BASH,
            instance_id="inst-a", command="echo", pgid=5001,
            parent_pid=1,
        )
        reg.mark_exit(
            pid=5001, start_time=100, exit_code=0,
            exit_kind=EXIT_KIND_SUCCESS, peak_rss_kb=2048,
        )
        reg.register(pid=5002, start_time=200, kind=KIND_CHART_RENDER,
                     instance_id="inst-b", command="mmdc")
        reg.mark_exit(pid=5002, start_time=200, exit_code=0,
                      exit_kind=EXIT_KIND_SUCCESS)
        reg.register(pid=5003, start_time=300, kind=KIND_MCP_CHILD,
                     command="uvx", detail="server=ctx")
        reg.mark_exit(pid=5003, start_time=300, exit_kind=EXIT_KIND_TIMEOUT)

        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=8),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            pid_registry=reg,
        )
        result = flusher.run_once()
        assert result["rows_written"] >= 3
        with sqlite_engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT pid, kind, exit_kind, instance_id, command, "
                    "peak_rss_kb FROM resource_pid_map ORDER BY pid"
                )
            ).fetchall()
        assert len(rows) == 3, f"expected 3 pid_map rows; got {rows}"
        assert rows[0][0] == 5001 and rows[0][1] == KIND_BASH
        assert rows[0][2] == EXIT_KIND_SUCCESS
        assert rows[0][3] == "inst-a"
        assert rows[0][5] == 2048
        assert rows[1][1] == KIND_CHART_RENDER
        assert rows[2][1] == KIND_MCP_CHILD

    def test_flusher_sweep_does_not_touch_live_rows(self, sqlite_engine, tmp_path):
        """The pid_map sweep guards on exited_at IS NOT NULL.

        NOTE (honest coverage): this holds via BOTH the explicit
        ``extra_predicate`` AND SQL NULL semantics (``NULL < cutoff``
        is never true). Removing the predicate alone does NOT flip
        this test — the predicate is belt-and-braces, not the sole
        guard. The discriminating assertion for the timestamp
        column lives in ``test_flusher_sweep_deletes_old_exited_rows``.
        """
        from daemon.services.resource_monitor import Flusher, RetentionPolicy, TierRing

        # Seed a LIVE row directly (exited_at IS NULL) with an
        # ancient spawned_at — the sweep must NOT delete it.
        with sqlite_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_pid_map "
                    "(pid, kind, spawned_at, exited_at) VALUES "
                    "(6001, 'bash', '2000-01-01 00:00:00', NULL)"
                )
            )
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=8),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            pid_registry=PidRegistry(proc_root=tmp_path / "proc"),
        )
        result = flusher.run_once()
        with sqlite_engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM resource_pid_map WHERE pid=6001")
            ).scalar()
        assert n == 1, "sweep deleted a LIVE row (exited_at IS NULL)"
        assert result["swept"].get("pid_map", 0) == 0

    def test_flusher_sweep_deletes_old_exited_rows(self, sqlite_engine, tmp_path):
        """The pid_map sweep targets ``exited_at`` (NOT bucket_ts).

        Discriminating: seeds (a) a row exited LONG ago and (b) a row
        exited just now; the sweep must delete only (a). A wrong
        ``ts_column`` would raise ``no such column`` against the
        pid_map schema; a wrong cutoff would delete (b).
        """
        from daemon.services.resource_monitor import Flusher, RetentionPolicy, TierRing

        with sqlite_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO resource_pid_map "
                    "(pid, kind, spawned_at, exited_at) VALUES "
                    "(7101, 'bash', '2000-01-01 00:00:00', '2000-01-01 00:00:05'), "
                    "(7102, 'bash', '2000-01-01 00:00:00', '2099-01-01 00:00:00')"
                )
            )
        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=8),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            pid_registry=PidRegistry(proc_root=tmp_path / "proc"),
        )
        result = flusher.run_once()
        assert result["swept"].get("pid_map", 0) == 1
        with sqlite_engine.connect() as conn:
            remaining = {
                r[0]
                for r in conn.execute(
                    text("SELECT pid FROM resource_pid_map")
                ).fetchall()
            }
        assert remaining == {7102}, f"expected only 7102; got {remaining}"

    def test_flusher_empty_history_is_noop(self, sqlite_engine, tmp_path):
        from daemon.services.resource_monitor import Flusher, RetentionPolicy, TierRing

        flusher = Flusher(
            engine=sqlite_engine,
            raw_ring=TierRing(tier="raw", maxlen=8),
            flush_batch_seconds=60,
            retention=RetentionPolicy(2880, 336, 180),
            pid_registry=PidRegistry(proc_root=tmp_path / "proc"),
        )
        result = flusher.run_once()
        assert result["swept"].get("pid_map", 0) == 0
        with sqlite_engine.connect() as conn:
            n = conn.execute(
                text("SELECT COUNT(*) FROM resource_pid_map")
            ).scalar()
        assert n == 0


# ──────────────────────────────────────────────────────────────────────────
# Entry-shape contract (the integration coder consumes this)
# ──────────────────────────────────────────────────────────────────────────


class TestEntryShape:
    def test_entry_fields(self, registry, proc_root):
        _write_proc_stat(proc_root, 8, starttime=42)
        e = registry.register(
            pid=8, start_time=42, kind=KIND_BASH,
            instance_id="i", agent_id="a", command="ls",
            detail="d", parent_pid=1, pgid=8,
        )
        assert isinstance(e, PidRegistryEntry)
        for f in (
            "pid", "start_time", "kind", "spawned_at", "pgid",
            "instance_id", "agent_id", "command", "detail",
            "parent_pid", "exited_at", "exit_code", "exit_kind",
            "peak_rss_kb", "cpu_user_usec",
        ):
            assert hasattr(e, f), f"entry missing field {f}"
        # spawned_at is a naive-UTC ISO string (D9).
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", e.spawned_at
        )

    def test_snapshot_is_a_copy(self, registry):
        registry.register(pid=9, start_time=1, kind=KIND_BASH)
        snap = registry.snapshot()
        snap.clear()
        assert registry.live_count() == 1


# ──────────────────────────────────────────────────────────────────────────
# W-1 — instance-keyed chart kind (production topology path)
# ──────────────────────────────────────────────────────────────────────────


class TestW1InstanceKeyedChartKind:
    """W-1 (P2 review): the ContextVar hint CANNOT cross the
    production graph boundary — the charter executes on a worker
    THREAD (worker_pool → task_processor → main_loop_bridge via
    run_coroutine_threadsafe), whose fresh context drops the hint.
    The load-bearing path records ``{child_instance_id →
    'chart_render'}`` on the registry; the bash stamp site resolves
    its OWN instance_id through the map."""

    def test_instance_keyed_kind_stamps_bash_children(
        self, patched_singleton, proc_root
    ):
        """NO ContextVar set (production shape) — the registry map
        keyed by the bash caller's own instance_id must drive the
        kind to chart_render."""
        from daemon.tools.bash import bash

        patched_singleton.note_instance_kind(
            "charter-child-1", KIND_CHART_RENDER
        )

        async def _run():
            await bash.coroutine(
                command=["echo", "render"],
                timeout=5,
                instance_id="charter-child-1",
            )

        asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        assert hist[0].kind == KIND_CHART_RENDER, (
            f"instance-keyed hint lost: got {hist[0].kind!r}"
        )

    def test_instance_keyed_kind_scoped_to_owning_instance(
        self, patched_singleton, proc_root
    ):
        """A DIFFERENT instance's bash call stays kind='bash' — the
        map must not leak the chart kind across instances."""
        from daemon.tools.bash import bash

        patched_singleton.note_instance_kind(
            "charter-child-2", KIND_CHART_RENDER
        )

        async def _run():
            await bash.coroutine(
                command=["echo", "plain"],
                timeout=5,
                instance_id="some-other-instance",
            )

        asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        assert hist[0].kind == KIND_BASH

    def test_note_instance_kind_drop_oldest_cap(self, patched_singleton):
        """The hint map is bounded (drop-oldest) — a long-lived
        daemon cannot grow it without bound."""
        from daemon.services.resource_monitor.pid_registry import (
            INSTANCE_KIND_MAP_CAP,
        )

        for i in range(INSTANCE_KIND_MAP_CAP + 5):
            patched_singleton.note_instance_kind(f"inst-{i}", KIND_BASH)
        # Oldest entries dropped; size stays at the cap.
        assert patched_singleton.kind_for_instance("inst-0") is None
        assert patched_singleton.kind_for_instance("inst-4") is None
        assert patched_singleton.kind_for_instance(
            f"inst-{INSTANCE_KIND_MAP_CAP + 4}"
        ) == KIND_BASH

    def test_note_instance_kind_rejects_invalid_kind(
        self, patched_singleton
    ):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            patched_singleton.note_instance_kind("x", "bogus")

    def test_chart_tools_records_child_instance_kind(self):
        """Source pin: chart_tools records the spawned child
        instance's kind after invoke_agent_and_wait returns (the
        W-1 load-bearing line), alongside the retained ContextVar
        residual lane."""
        chart_src = (
            Path(__file__).resolve().parent.parent
            / "daemon" / "tools" / "chart_tools.py"
        ).read_text()
        assert "get_pid_registry().note_instance_kind(" in chart_src
        assert "child_instance_id, KIND_CHART_RENDER" in chart_src
        assert "if render_image and child_instance_id:" in chart_src
        # Residual ContextVar lane retained + reset discipline kept.
        assert "kind_hint_token = _PID_KIND_HINT.set(KIND_CHART_RENDER)" in chart_src
        assert "_PID_KIND_HINT.reset(kind_hint_token)" in chart_src


# ──────────────────────────────────────────────────────────────────────────
# W-2 — sanitizer redaction (list + string branches)
# ──────────────────────────────────────────────────────────────────────────


class TestW2SanitizerRedaction:
    """W-2 (P2 review): the sanitizer must neutralize BOTH
    secret-bearing shapes — env-assignment argv/string tokens and
    secret-marker tokens — because resource_pid_map.command /
    .detail persist OUTSIDE the secrets-P2 scrub seams."""

    CANARY_LIST = "sk-canary-list-1234567890"
    CANARY_STR = "sk-canary-string-0987654321"

    def test_list_env_assign_token_redacted(
        self, patched_singleton, proc_root, caplog
    ):
        """Canary as an argv LIST token (``cmd KEY=value …`` idiom):
        the KEY= prefix survives, the value never persists (row or
        logs)."""
        from daemon.tools.bash import bash

        async def _run():
            await bash.coroutine(
                command=["echo", f"OPENAI_API_KEY={self.CANARY_LIST}"],
                timeout=5,
                instance_id="w2-list",
            )

        with caplog.at_level("DEBUG"):
            asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        e = hist[0]
        joined = f"{e.command or ''} {e.detail or ''}"
        assert self.CANARY_LIST not in joined, f"argv leak: {joined!r}"
        assert "OPENAI_API_KEY=<redacted>" in joined, joined
        assert self.CANARY_LIST not in caplog.text, "argv leak into logs"

    def test_string_head_of_line_env_assign_stripped(
        self, patched_singleton, proc_root, caplog
    ):
        """Canary in a head-of-line KEY=value STRING token (the
        exact class the old ``split(None, 1)[0]`` persisted): argv0
        becomes the real command; neither row nor log carries the
        canary — INCLUDING the detail's argv0 echo."""
        from daemon.tools.bash import bash

        async def _run():
            res = await bash.coroutine(
                command=f"OPENAI_API_KEY={self.CANARY_STR} echo hi",
                timeout=5,
                instance_id="w2-string",
            )
            assert "EXIT CODE: 0" in res  # the command still ran fine

        with caplog.at_level("DEBUG"):
            asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        e = hist[0]
        joined = f"{e.command or ''} {e.detail or ''}"
        assert self.CANARY_STR not in joined, f"head-of-line leak: {joined!r}"
        assert e.command == "echo", f"argv0 should be 'echo': {e.command!r}"
        assert "argv0='echo'" in (e.detail or ""), e.detail
        assert self.CANARY_STR not in caplog.text, "head-of-line leak into logs"

    def test_string_all_assignments_fall_back_to_redacted_first(
        self, patched_singleton
    ):
        """An all-env-assignments string keeps a (redacted)
        fingerprint instead of an empty command."""
        from daemon.tools.bash import _sanitize_command_for_registry

        cmd, detail = _sanitize_command_for_registry(
            f"FOO={self.CANARY_STR}", env=None
        )
        assert cmd == "FOO=<redacted>"
        assert self.CANARY_STR not in detail

    def test_marker_token_redacted(self, patched_singleton, proc_root):
        """A token carrying a __ENS_SECRET__ marker persists as
        ``<redacted>`` — the marker id never reaches the row."""
        from daemon.tools.bash import bash

        marker_id = "3f2b8c1a-9d4e-4f6a-b7c8-d1e2f3a4b5c6"
        marker = f"__ENS_SECRET__{marker_id}__"

        async def _run():
            await bash.coroutine(
                command=["echo", marker, "plain-arg"],
                timeout=5,
                instance_id="w2-marker",
            )

        asyncio.run(_run())
        hist = patched_singleton.drain_history()
        assert len(hist) == 1
        e = hist[0]
        joined = f"{e.command or ''} {e.detail or ''}"
        assert marker_id not in joined, f"marker id leak: {joined!r}"
        assert "<redacted>" in joined

    def test_kms_marker_token_redacted(self, patched_singleton):
        """KMS-Lite markers (LANE-1 / LANE-2 shapes) never persist."""
        from daemon.tools.bash import _sanitize_command_for_registry

        for marker in (
            "__KMS_REF__KMS_HANDLE_123e4567-e89b-12d3-a456-426614174000__",
            "__KMS_ENV__OPENAI_API_KEY__",
        ):
            cmd, detail = _sanitize_command_for_registry(
                ["run", marker], env=None
            )
            joined = f"{cmd} {detail}"
            assert marker not in joined, joined
            assert "<redacted>" in joined

    def test_plain_argv_still_persists(self):
        """The redaction is surgical — a clean fingerprint is kept."""
        from daemon.tools.bash import _sanitize_command_for_registry

        cmd, _ = _sanitize_command_for_registry(
            ["mmdc", "-i", "in.mmd", "-o", "out.png"], env=None
        )
        assert cmd == "mmdc -i in.mmd -o out.png"


# ──────────────────────────────────────────────────────────────────────────
# W-3 — mcp child claim-once + args=[] guard
# ──────────────────────────────────────────────────────────────────────────


class TestW3McpClaimOnce:
    """W-3 (P2 review): concurrent same-command sessions must get
    DISJOINT registrations (first claim wins), and an args=[]
    server must match NOTHING (never wildcard by basename)."""

    @pytest.fixture(autouse=True)
    def _clean_claim_ledger(self):
        import daemon.mcp.stdio_wrapper as sw

        sw._claimed_children.clear()
        yield
        sw._claimed_children.clear()

    def test_concurrent_same_command_sessions_disjoint(
        self, patched_singleton, tmp_path
    ):
        import types
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import daemon.mcp.stdio_wrapper as sw

        proc = tmp_path / "proc"
        _write_proc_stat(proc, 85001, ppid=os.getpid(), starttime=90001,
                         comm="uvx")
        (proc / "85001" / "cmdline").write_bytes(
            b"\x00".join([b"uvx", b"mcp-server-shared"]) + b"\x00"
        )
        patched_singleton._proc_root = proc
        orig_proc_root = sw._PROC_ROOT
        sw._PROC_ROOT = proc

        @asynccontextmanager
        async def fake_client(server_params):
            yield ("r", "w")

        async def _run():
            params = types.SimpleNamespace(
                command="uvx", args=["mcp-server-shared"]
            )
            with patch.object(sw.mcp, "stdio_client", fake_client):
                a = sw.TaskScopedStdioClient(
                    params, instance_id="sess-a", server_name="sa"
                )
                b = sw.TaskScopedStdioClient(
                    params, instance_id="sess-b", server_name="sb"
                )
                await a.__aenter__()
                await b.__aenter__()
                # DISJOINT: the first claim owns the child; the
                # second session registered nothing.
                assert a.registered_child_pids == [(85001, 90001)]
                assert b.registered_child_pids == [], (
                    "double-claim: both sessions registered the same pid"
                )
                assert patched_singleton.live_count() == 1
                await a.__aexit__(None, None, None)
                # The still-live child was NOT falsely exited by the
                # sibling's close... it WAS legitimately exited by its
                # OWNER's close; the point is: exactly ONE exit-mark.
                assert patched_singleton.live_count() == 0
                await b.__aexit__(None, None, None)

        try:
            asyncio.run(_run())
        finally:
            sw._PROC_ROOT = orig_proc_root
        hist = patched_singleton.drain_history()
        # Exactly ONE history row for the shared child — the old
        # double-registration produced a false exit + a missing live
        # row on the first close.
        assert len(hist) == 1, f"expected 1 exit row, got {len(hist)}"
        assert hist[0].exit_kind == "exit"
        assert patched_singleton.live_count() == 0

    def test_args_empty_server_never_matches(self, patched_singleton, tmp_path):
        """args=[] configured ⇒ NO match even when a same-basename
        child of the daemon exists (skip, never wildcard)."""
        import types
        from contextlib import asynccontextmanager
        from unittest.mock import patch

        import daemon.mcp.stdio_wrapper as sw

        proc = tmp_path / "proc"
        _write_proc_stat(proc, 86001, ppid=os.getpid(), starttime=91001,
                         comm="uvx")
        (proc / "86001" / "cmdline").write_bytes(
            b"\x00".join([b"uvx", b"mcp-server-noargs"]) + b"\x00"
        )
        patched_singleton._proc_root = proc
        orig_proc_root = sw._PROC_ROOT
        sw._PROC_ROOT = proc

        # Scan level: empty args never match.
        assert sw.scan_mcp_child_pids(
            command="uvx", args=[], proc_root=proc,
            parent_pid=os.getpid(),
        ) == []
        assert sw.scan_mcp_child_pids(
            command="uvx", args=None, proc_root=proc,
            parent_pid=os.getpid(),
        ) == []

        # Wrapper level: an args-less server stamps nothing.
        @asynccontextmanager
        async def fake_client(server_params):
            yield ("r", "w")

        async def _run():
            params = types.SimpleNamespace(command="uvx", args=[])
            with patch.object(sw.mcp, "stdio_client", fake_client):
                cm = sw.TaskScopedStdioClient(params)
                await cm.__aenter__()
                assert patched_singleton.live_count() == 0, (
                    "args=[] server wildcard-matched a same-basename child"
                )
                await cm.__aexit__(None, None, None)

        try:
            asyncio.run(_run())
        finally:
            sw._PROC_ROOT = orig_proc_root
        assert patched_singleton.drain_history() == []
