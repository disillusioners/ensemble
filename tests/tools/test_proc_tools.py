"""Tests for the Background Process Tools (``proc_tools``).

These tests exercise the 5 LangChain tools produced by
:func:`daemon.tools.proc_tools.create_proc_tools` against a real
:class:`BackgroundProcessManager` singleton (isolated per test via
unique ``instance_id`` values).

Test scenarios
---------------

1. **Lifecycle** (``TestLifecycle``) — start a long-running
   command, check status shows ``running``, read logs, stop it,
   verify status becomes ``killed``.
2. **Process cap** (``TestProcessCap``) — start 10 processes, try
   the 11th and assert an ``Error:`` string is returned.
3. **Log spillover** (``TestLogSpillover``) — start a process that
   emits >4 MB of output, verify the memory buffer is capped and a
   spill file was created, then confirm ``proc_logs`` merges memory
   + file correctly.
4. **Instance cleanup** (``TestInstanceCleanup``) — start several
   processes, call ``cleanup_instance``, verify all are killed and
   resources released.
5. **Process listing** (``TestProcList``) — start a few processes,
   verify ``proc_list`` returns a table with all of them.
6. **Cross-instance isolation** (``TestCrossInstanceIsolation``) —
   processes started under instance A are invisible to instance B.
7. **Split-line stitching** (``TestSplitLineStitching``) — when the
   memory/file boundary lands mid-line, ``_get_recent_lines`` stitches
   the partial line (C1 fix regression).
8. **File-tail reader** (``TestReadFileTailSync``) — unit tests for
   the backward chunked reader used by ``_read_file_tail_sync``.
9. **Spawn-window race** (``TestSpawnWindowRace``) — when
   ``cleanup_instance`` runs during subprocess creation, the C2 fix
   detects and kills the orphan.
10. **Timeout-killer** (``TestTimeoutKiller``) — ``timeout=...`` auto-kills
    the process and surfaces ``timed_out: true`` + ``status: killed``.
11. **Multi-chunk spillover** (``TestMultiChunkSpillover``) — emitting
    >8 MB triggers multiple spills; memory stays capped and the spill
    file grows proportionally.

Conventions
-----------

- Use ``pytest-asyncio`` (mode=auto via ``pyproject.toml``).
- Build tools once per test via ``create_proc_tools(...)`` using a
  **unique** ``current_instance_id`` per test to prevent cross-test
  state pollution. ``cleanup_instance`` is called in an async
  fixture's teardown block.
- ``proc_run`` and ``proc_stop`` are async; invoke via
  ``await tool.ainvoke({...})``. ``proc_logs``, ``proc_status``,
  and ``proc_list`` are sync but still use ``await tool.ainvoke``
  (LangChain's ``@tool`` exposes ``ainvoke`` as an async method).
- Tools never raise; they return either a success string or
  ``"Error: ..."``. Assertions target the return strings.
- All commands use Python to avoid platform-specific shell syntax.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# Module-level expected tool names — single source of truth for factory
# and registry assertions.
PROC_TOOL_NAMES: frozenset[str] = frozenset({
    "proc_run",
    "proc_logs",
    "proc_status",
    "proc_stop",
    "proc_list",
})

# Maximum concurrent processes per instance (matches
# ``MAX_PROCESSES_PER_INSTANCE`` in ``proc_tools``).
MAX_PROCESSES = 10


def _make_fake_info(pid: str):
    """Build a ``ProcessInfo``-like stub for ``cleanup_all`` unit tests.

    ``cleanup_instance`` only does duck-typed attribute access on
    ``info`` — the fields it actually touches are: ``reader_task``,
    ``exit_task``, ``timeout_task``, ``proc``, ``file_path``,
    ``file_handle``. All can be ``None`` (early-returned in the kill
    loop). We use ``SimpleNamespace`` rather than a dataclass because
    dataclass forbids mutable defaults like ``bytearray()``, and
    ``ProcessInfo`` requires constructor args we don't want to fake.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        process_id=pid,
        reader_task=None,
        exit_task=None,
        timeout_task=None,
        proc=None,
        file_path=None,
        file_handle=None,
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def unique_instance_id():
    """Return a per-test unique instance id for isolation."""
    return f"test-proc-{os.urandom(4).hex()}"


@pytest_asyncio.fixture
async def proc_tools(unique_instance_id):
    """Build the 5 proc tools scoped to a unique instance id.

    Async fixture so the teardown can ``await`` the manager's
    ``cleanup_instance`` coroutine. The unique ``current_instance_id``
    per test guarantees cross-instance isolation even if the
    singleton retains state between tests.
    """
    from daemon.tools.proc_tools import (
        create_proc_tools,
        get_background_process_manager,
    )

    tools_list = create_proc_tools(current_instance_id=unique_instance_id)
    tools = {getattr(t, "name", None): t for t in tools_list}
    yield tools, unique_instance_id

    # Teardown: kill all processes started under this instance_id so
    # they don't linger after the test. SIGKILL is fine — tests are
    # already done by this point.
    manager = get_background_process_manager()
    await manager.cleanup_instance(unique_instance_id)


# =============================================================================
# Group 1: Factory
# =============================================================================


class TestFactory:
    """``create_proc_tools`` must return 5 tools with the expected names."""

    def test_factory_returns_five_tools(self, unique_instance_id):
        """Factory returns exactly 5 tools."""
        from daemon.tools.proc_tools import create_proc_tools

        tools = create_proc_tools(current_instance_id=unique_instance_id)
        assert len(tools) == 5

    def test_factory_returns_expected_tool_names(self, proc_tools):
        """The 5 returned tool names match the expected set exactly."""
        tools, _ = proc_tools
        assert set(tools.keys()) == PROC_TOOL_NAMES

    def test_factory_empty_id_returns_empty_list(self):
        """Passing an empty string returns an empty list (no tools)."""
        from daemon.tools.proc_tools import create_proc_tools

        tools = create_proc_tools(current_instance_id="")
        assert tools == []

    def test_factory_registers_proc_category(
        self, proc_tools, unique_instance_id
    ):
        """After the factory runs, the ``"proc"`` category is registered.

        Wrapped in ``try/finally clear_registry()`` so the global
        registry state does not leak between tests.
        """
        from daemon.tools._tool_registry import (
            clear_registry,
            list_tools_by_category,
            scan_tools_for_full_docs,
        )

        tools, _ = proc_tools
        clear_registry()
        try:
            scan_tools_for_full_docs(list(tools.values()))
            categories = list_tools_by_category()
            assert "proc" in categories
            assert set(categories["proc"]) == PROC_TOOL_NAMES
        finally:
            clear_registry()


# =============================================================================
# Group 2: Lifecycle — start, status, logs, stop
# =============================================================================


class TestLifecycle:
    """Start a long-running process and verify its full lifecycle."""

    async def test_start_status_logs_stop(self, proc_tools, unique_instance_id):
        """Full lifecycle: start → status=running → read logs → stop → status=killed."""
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]
        proc_logs = tools["proc_logs"]
        proc_stop = tools["proc_stop"]

        # 1. Start a process that sleeps long enough to be observable.
        # Use Python with ``-u`` (unbuffered) so ``print`` flushes
        # through the pipe to the reader immediately — without it,
        # stdout is block-buffered when redirected and the agent
        # wouldn't see output until the buffer fills or the process
        # exits.
        result = await proc_run.ainvoke({
            "command": (
                f"{sys.executable} -u -c "
                "\"import time; print('start', flush=True); "
                "time.sleep(30); print('end', flush=True)\""
            ),
            "timeout": 0,
        })

        # Must succeed (no "Error:").
        assert "Error:" not in result, f"proc_run failed: {result}"
        # Extract process_id from the result.
        match = re.search(r"(proc-[0-9a-f]+)", result)
        assert match, f"Could not find process_id in result: {result}"
        process_id = match.group(1)

        # 2. Status should show "running".
        # Give the reader task time to flush the initial print through.
        await asyncio.sleep(1.0)
        status = await proc_status.ainvoke({"process_id": process_id})
        assert "status: running" in status, f"Expected running, got: {status}"
        assert f"process_id: {process_id}" in status

        # 3. Logs should include the "start" line.
        logs = await proc_logs.ainvoke({"process_id": process_id, "lines": 20})
        assert "start" in logs, f"Expected 'start' in logs, got: {logs}"
        assert "status=running" in logs

        # 4. Stop the process.
        stop_result = await proc_stop.ainvoke({"process_id": process_id})
        assert "Error:" not in stop_result, f"proc_stop failed: {stop_result}"
        assert "killed" in stop_result.lower(), (
            f"Expected 'killed' in stop result, got: {stop_result}"
        )

        # 5. Status should now show "killed".
        final_status = await proc_status.ainvoke({"process_id": process_id})
        assert "status: killed" in final_status, (
            f"Expected killed, got: {final_status}"
        )


# =============================================================================
# Group 3: Process cap enforcement
# =============================================================================


class TestProcessCap:
    """Verify that starting more than MAX_PROCESSES returns an error."""

    async def test_starting_max_processes_succeeds(
        self, proc_tools, unique_instance_id
    ):
        """Starting exactly MAX_PROCESSES (10) processes succeeds."""
        tools, _ = proc_tools
        proc_run = tools["proc_run"]

        for i in range(MAX_PROCESSES):
            result = await proc_run.ainvoke({
                "command": (
                    f"{sys.executable} -c "
                    "\"import time; time.sleep(300)\""
                ),
            })
            assert "Error:" not in result, (
                f"Start {i+1}/{MAX_PROCESSES} should succeed, got: {result}"
            )

    async def test_11th_process_returns_error(
        self, proc_tools, unique_instance_id
    ):
        """The 11th process start must be rejected with an ``Error:`` string."""
        tools, _ = proc_tools
        proc_run = tools["proc_run"]

        # Start MAX_PROCESSES first.
        for i in range(MAX_PROCESSES):
            result = await proc_run.ainvoke({
                "command": (
                    f"{sys.executable} -c "
                    "\"import time; time.sleep(300)\""
                ),
            })
            assert "Error:" not in result, f"Setup start {i+1} failed: {result}"

        # The 11th attempt must fail with the cap message.
        over_result = await proc_run.ainvoke({
            "command": (
                f"{sys.executable} -c "
                "\"import time; time.sleep(300)\""
            ),
        })
        assert "Error:" in over_result, (
            f"11th process should be rejected, got: {over_result}"
        )
        # The error should mention the cap.
        assert str(MAX_PROCESSES) in over_result, (
            f"Error should mention cap ({MAX_PROCESSES}), got: {over_result}"
        )


# =============================================================================
# Group 4: Log spillover (memory → spill file)
# =============================================================================


class TestLogSpillover:
    """Verify that >4 MB output triggers spillover to a temp file."""

    async def test_spillover_caps_memory_and_creates_file(
        self, proc_tools, unique_instance_id
    ):
        """After >4 MB of output, memory buffer is capped and a spill file exists."""
        from daemon.tools.proc_tools import (
            _MEMORY_BUFFER_LIMIT_BYTES,
            get_background_process_manager,
        )

        tools, inst_id = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]

        # Emit ~5 MB of data — enough to trigger spillover (limit is 4 MB).
        # 51 × 100 KB ≈ 5.1 MB. Write the script to a temp file rather
        # than embedding it on the command line: the embedded form has
        # shell-quoting hazards (``\'`` vs ``'``, ``\\n`` vs ``\n``)
        # that make it brittle across shells.
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
        ) as script:
            script.write(
                "import sys\n"
                "chunk = b'x' * 100000 + b'\\n'  # 100 KB per line\n"
                "for _ in range(51):  # 51 * 100 KB ~= 5.1 MB\n"
                "    sys.stdout.buffer.write(chunk)\n"
                "sys.stdout.buffer.flush()\n"
            )
            script_path = script.name

        try:
            result = await proc_run.ainvoke(
                {"command": f"{sys.executable} -u {script_path}"}
            )

            assert "Error:" not in result, f"proc_run failed: {result}"

            match = re.search(r"(proc-[0-9a-f]+)", result)
            assert match, f"Could not find process_id: {result}"
            process_id = match.group(1)

            # Wait for the subprocess to exit naturally (it writes 5 MB
            # and returns). Polling avoids the race where proc_run
            # returns immediately after spawn but the OS hasn't yet
            # opened the script file — unlinking too early causes
            # "can't open file" errors. Poll up to ~10s.
            deadline = asyncio.get_event_loop().time() + 10.0
            while asyncio.get_event_loop().time() < deadline:
                status = await proc_status.ainvoke(
                    {"process_id": process_id}
                )
                if "status: exited" in status or "status: killed" in status:
                    break
                await asyncio.sleep(0.2)

            # Brief pause to let the reader task drain the final pipe
            # data into memory + spill buffers.
            await asyncio.sleep(0.5)

            # Check ProcessInfo state directly from the manager.
            manager = get_background_process_manager()
            bucket = manager._processes.get(inst_id, {})
            info = bucket.get(process_id)
            assert info is not None, (
                f"Process {process_id} not found in manager"
            )

            # Memory buffer must be capped at or below the limit.
            assert len(info.memory_buffer) <= _MEMORY_BUFFER_LIMIT_BYTES, (
                f"Memory buffer ({len(info.memory_buffer)} bytes) exceeds "
                f"cap ({_MEMORY_BUFFER_LIMIT_BYTES})"
            )

            # A spill file must have been created.
            assert info.file_path is not None, (
                "Spill file path is None — spillover did not occur"
            )
            assert os.path.exists(info.file_path), (
                f"Spill file does not exist: {info.file_path}"
            )
            file_size = os.path.getsize(info.file_path)
            assert file_size > 0, "Spill file is empty"

            # Now verify proc_logs merges memory + file and returns lines.
            proc_logs = tools["proc_logs"]
            logs_result = await proc_logs.ainvoke(
                {"process_id": process_id, "lines": 100}
            )
            assert "Error:" not in logs_result, (
                f"proc_logs failed: {logs_result}"
            )
            # Should contain at least some 'x' characters from the output.
            assert "x" in logs_result, (
                f"Expected 'x' in merged logs, got: {logs_result[:200]}"
            )
            # Should mention the process status in the header.
            assert "status=" in logs_result
        finally:
            # Safe to unlink now — the subprocess has exited.
            try:
                os.unlink(script_path)
            except OSError:
                pass


# =============================================================================
# Group 5: Instance cleanup
# =============================================================================


class TestInstanceCleanup:
    """Verify that ``cleanup_instance`` kills all processes and cleans up."""

    async def test_cleanup_instance_kills_all_processes(
        self, unique_instance_id
    ):
        """``cleanup_instance`` kills every tracked process and drops the bucket."""
        from daemon.tools.proc_tools import (
            create_proc_tools,
            get_background_process_manager,
        )

        manager = get_background_process_manager()
        tools_list = create_proc_tools(current_instance_id=unique_instance_id)
        tools = {getattr(t, "name", None): t for t in tools_list}
        proc_run = tools["proc_run"]
        proc_list = tools["proc_list"]

        # Start 3 processes.
        pids = []
        for _ in range(3):
            result = await proc_run.ainvoke({
                "command": (
                    f"{sys.executable} -c "
                    "\"import time; time.sleep(300)\""
                ),
            })
            assert "Error:" not in result
            match = re.search(r"(proc-[0-9a-f]+)", result)
            assert match
            pids.append(match.group(1))

        # Confirm they are tracked.
        await asyncio.sleep(0.3)
        listing = await proc_list.ainvoke({})
        for pid in pids:
            assert pid in listing, f"{pid} should be in proc_list before cleanup"

        # Cleanup the entire instance.
        await manager.cleanup_instance(unique_instance_id)

        # Bucket should be gone.
        assert unique_instance_id not in manager._processes, (
            "Instance bucket should be removed after cleanup_instance"
        )

        # proc_list should report no processes.
        listing_after = await proc_list.ainvoke({})
        assert "No background processes" in listing_after, (
            f"Expected empty list after cleanup, got: {listing_after}"
        )


class TestCleanupAll:
    """Verify ``BackgroundProcessManager.cleanup_all`` (Phase 1 of the
    auto-kill background processes on root instance completion plan).

    ``cleanup_all`` is the daemon-shutdown sweep: it iterates every
    instance bucket and calls ``cleanup_instance`` per bucket, returning
    a count. It is idempotent and best-effort (per-iid try/except).

    The tests below directly poke ``manager._processes`` (the in-memory
    registry) rather than spawning real OS subprocesses. The manager's
    ``cleanup_instance`` itself is exercised end-to-end in
    ``TestInstanceCleanup``; here we only care that ``cleanup_all``
    walks every key and pops every bucket — and that failures are
    isolated.
    """

    async def test_cleanup_all_empties_all_buckets_across_instances(self):
        """Spawn procs across multiple instances, then ``cleanup_all`` empties every bucket."""
        from daemon.tools.proc_tools import (
            get_background_process_manager,
        )

        manager = get_background_process_manager()
        # Use unique ids so we don't collide with other tests.
        ids = [
            f"test-cleanup-all-a-{os.urandom(4).hex()}",
            f"test-cleanup-all-b-{os.urandom(4).hex()}",
            f"test-cleanup-all-c-{os.urandom(4).hex()}",
        ]
        # Inject fake buckets (skip subprocess spawn — we only exercise
        # the snapshot-and-pop logic, not the OS kill).

        try:
            for iid in ids:
                manager._processes[iid] = {  # type: ignore[assignment]
                    "proc-fake-1": _make_fake_info("proc-fake-1"),
                    "proc-fake-2": _make_fake_info("proc-fake-2"),
                }

            # Sanity: all buckets present before cleanup.
            for iid in ids:
                assert iid in manager._processes
                assert len(manager._processes[iid]) == 2

            cleaned = await manager.cleanup_all()

            assert cleaned == 3, f"Expected 3 cleaned buckets, got {cleaned}"
            assert manager._processes == {}, (
                f"Expected empty _processes dict, got: "
                f"{list(manager._processes.keys())}"
            )
        finally:
            # Belt-and-suspenders: ensure no leak if a sub-assert failed.
            for iid in ids:
                manager._processes.pop(iid, None)

    async def test_cleanup_all_is_idempotent(self):
        """Calling ``cleanup_all`` twice on the same manager is a no-op the second time."""
        from daemon.tools.proc_tools import (
            get_background_process_manager,
        )

        manager = get_background_process_manager()
        iid = f"test-cleanup-all-idem-{os.urandom(4).hex()}"

        # Empty state: cleanup_all is a no-op, returns 0.
        first = await manager.cleanup_all()
        assert first == 0, f"Empty state should yield 0, got {first}"

        try:
            manager._processes[iid] = {"proc-x": _make_fake_info("proc-x")}  # type: ignore[assignment]
            first_with_bucket = await manager.cleanup_all()
            assert first_with_bucket == 1, (
                f"Expected 1 cleaned bucket, got {first_with_bucket}"
            )
            second = await manager.cleanup_all()
            assert second == 0, (
                f"Second cleanup_all on empty registry should be 0, "
                f"got {second}"
            )
        finally:
            manager._processes.pop(iid, None)

    async def test_cleanup_all_isolates_per_instance_failures(self):
        """If ``cleanup_instance`` raises for one iid, others still get cleaned."""
        from daemon.tools.proc_tools import (
            get_background_process_manager,
        )

        manager = get_background_process_manager()
        iid_a = f"test-cleanup-all-fail-a-{os.urandom(4).hex()}"
        iid_b = f"test-cleanup-all-fail-b-{os.urandom(4).hex()}"

        # Stub ``cleanup_instance`` so it raises for iid_a only.
        original_cleanup = manager.cleanup_instance
        raised_for: list[str] = []

        async def stub_cleanup(iid: str) -> None:
            if iid == iid_a:
                raised_for.append(iid)
                # Mimic the real ``cleanup_instance``'s atomic pop BEFORE
                # raising so the bucket is gone from the registry — this
                # matches the production behavior (the pop happens before
                # the OS kill attempt, so even a kill failure leaves the
                # bucket empty). Otherwise we'd be testing an artifact
                # of the test stub, not the production code path.
                async with manager._lock:
                    manager._processes.pop(iid, {})
                raise RuntimeError("synthetic cleanup_instance failure")
            # Otherwise fall back to the real method (idempotent pop).
            await original_cleanup(iid)

        try:
            manager._processes[iid_a] = {"proc-y": _make_fake_info("proc-y")}  # type: ignore[assignment]
            manager._processes[iid_b] = {"proc-y": _make_fake_info("proc-y")}  # type: ignore[assignment]

            manager.cleanup_instance = stub_cleanup  # type: ignore[assignment]

            cleaned = await manager.cleanup_all()

            # iid_a failed (WARNING logged but counter not incremented);
            # iid_b succeeded.
            assert cleaned == 1, f"Expected 1 cleaned (iid_b), got {cleaned}"
            assert iid_a in raised_for, (
                "cleanup_instance should have been called for iid_a"
            )
            assert iid_a not in manager._processes, (
                f"iid_a bucket should still be popped (real pop happens "
                f"before the stub raises), got: "
                f"{list(manager._processes.keys())}"
            )
            assert iid_b not in manager._processes, (
                f"iid_b bucket should be cleaned, got: "
                f"{list(manager._processes.keys())}"
            )
        finally:
            manager.cleanup_instance = original_cleanup  # type: ignore[assignment]
            manager._processes.pop(iid_a, None)
            manager._processes.pop(iid_b, None)


# =============================================================================
# Group 6: Process list
# =============================================================================


class TestProcList:
    """Verify ``proc_list`` returns a table with running processes."""

    async def test_proc_list_shows_running_processes(
        self, proc_tools, unique_instance_id
    ):
        """``proc_list`` shows all started processes in a markdown table."""
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_list = tools["proc_list"]

        # Start 2 long-running processes.
        for _ in range(2):
            result = await proc_run.ainvoke({
                "command": (
                    f"{sys.executable} -c "
                    "\"import time; time.sleep(300)\""
                ),
            })
            assert "Error:" not in result

        await asyncio.sleep(0.3)

        listing = await proc_list.ainvoke({})
        assert "process_id | status | command | uptime" in listing, (
            f"Expected table header, got: {listing}"
        )
        # Markdown table separator row.
        assert "---|---|---|---" in listing
        # We expect exactly 2 data rows.
        data_rows = [
            line
            for line in listing.splitlines()
            if line.startswith("proc-")
        ]
        assert len(data_rows) == 2, (
            f"Expected 2 data rows, got {len(data_rows)}: {listing}"
        )

    async def test_proc_list_empty_instance(self):
        """``proc_list`` for an empty instance returns a friendly message."""
        from daemon.tools.proc_tools import create_proc_tools

        fresh_id = f"test-empty-{os.urandom(4).hex()}"
        tools_list = create_proc_tools(current_instance_id=fresh_id)
        tools = {getattr(t, "name", None): t for t in tools_list}
        proc_list = tools["proc_list"]

        listing = await proc_list.ainvoke({})
        assert "No background processes" in listing
        assert fresh_id in listing


# =============================================================================
# Group 7: Cross-instance isolation
# =============================================================================


class TestCrossInstanceIsolation:
    """Processes started under instance A must be invisible to instance B."""

    async def test_instance_a_process_not_visible_to_instance_b(self):
        """``proc_list`` for instance B does not show instance A's processes.

        Also verifies that ``proc_logs`` from instance B cannot read
        logs from instance A's process — they live in separate buckets.
        """
        from daemon.tools.proc_tools import (
            create_proc_tools,
            get_background_process_manager,
        )

        manager = get_background_process_manager()

        instance_a = f"test-iso-a-{os.urandom(4).hex()}"
        instance_b = f"test-iso-b-{os.urandom(4).hex()}"

        try:
            # Build tools for both instances.
            tools_a = {
                getattr(t, "name", None): t
                for t in create_proc_tools(current_instance_id=instance_a)
            }
            tools_b = {
                getattr(t, "name", None): t
                for t in create_proc_tools(current_instance_id=instance_b)
            }

            proc_run_a = tools_a["proc_run"]
            proc_list_b = tools_b["proc_list"]
            proc_logs_b = tools_b["proc_logs"]

            # Start a process under instance A.
            result_a = await proc_run_a.ainvoke({
                "command": (
                    f"{sys.executable} -c "
                    "\"import time; time.sleep(300)\""
                ),
            })
            assert "Error:" not in result_a
            match = re.search(r"(proc-[0-9a-f]+)", result_a)
            assert match
            process_id_a = match.group(1)

            await asyncio.sleep(0.3)

            # Instance B's proc_list must be empty.
            listing_b = await proc_list_b.ainvoke({})
            assert "No background processes" in listing_b, (
                f"Instance B should see no processes, got: {listing_b}"
            )

            # Instance B cannot read logs for instance A's process.
            logs_b = await proc_logs_b.ainvoke(
                {"process_id": process_id_a, "lines": 10}
            )
            assert "Error:" in logs_b, (
                f"Instance B should get an error for A's process_id, "
                f"got: {logs_b}"
            )
            assert process_id_a in logs_b
        finally:
            # Teardown both instances.
            await manager.cleanup_instance(instance_a)
            await manager.cleanup_instance(instance_b)


# =============================================================================
# Group 8: Split-line stitching at the memory/file boundary (C1 regression)
# =============================================================================


class TestSplitLineStitching:
    """C1 fix regression: ``_get_recent_lines`` must stitch the partial
    line that straddles the memory/spill-file boundary.

    When a spill splits the buffer mid-line, the last ``\n`` lives in
    the spill file and the head of the next line lives in memory (or
    vice versa, depending on the split direction). Without stitching,
    the reader returns two halves of the same logical line as separate
    garbled entries.

    The C1 fix tracks ``info._file_ends_with_newline``: when False,
    ``_get_recent_lines`` concatenates ``older[-1]`` with
    ``memory_lines[0]`` to recover the full line.
    """

    def _make_spill_file(self, content: bytes) -> str:
        """Write ``content`` to a temp spill file and return its path."""
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".log",
            delete=False,
        )
        try:
            handle.write(content)
        finally:
            handle.close()
        return handle.name

    async def test_split_line_is_stitched_when_file_does_not_end_with_newline(
        self,
    ):
        """When the spill file does not end with ``\\n``, the last
        line in the file and the first line in memory are stitched
        into a single logical line.
        """
        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        spill_path = self._make_spill_file(b"head of line ")  # NO trailing \n

        try:
            info = ProcessInfo(
                process_id="proc-test0001",
                instance_id="stitch-test",
                command="test",
            )
            info.file_path = spill_path
            info.memory_buffer.extend(b"tail of line\nnext line\n")
            # C1 fix flag: spill did NOT end on a newline boundary.
            info._file_ends_with_newline = False

            result = await manager._get_recent_lines(info, 50)

            # Stitched line must appear as a single contiguous string.
            assert "head of line tail of line" in result, (
                f"Stitched line missing from result: {result!r}"
            )
            # Garbled split (non-stitched) form must NOT appear.
            assert "head of line \ntail of line" not in result, (
                f"Result contains split-line artifact: {result!r}"
            )
            # The result must have exactly 2 lines (stitched + next).
            lines = result.splitlines()
            assert len(lines) == 2, (
                f"Expected 2 stitched lines, got {len(lines)}: {lines!r}"
            )
            assert lines[0] == "head of line tail of line"
            assert lines[1] == "next line"
        finally:
            try:
                os.unlink(spill_path)
            except OSError:
                pass

    async def test_no_stitching_when_file_ends_with_newline(self):
        """Control case: when the spill file ends with ``\\n``, the
        memory/file boundary is on a line boundary and no stitching
        should happen. The head stays in the file, the tail in memory.
        """
        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        # Same content as the stitched case, but the file DOES end
        # with a newline — no mid-line split to stitch.
        spill_path = self._make_spill_file(b"head of line\n")

        try:
            info = ProcessInfo(
                process_id="proc-test0002",
                instance_id="stitch-control",
                command="test",
            )
            info.file_path = spill_path
            info.memory_buffer.extend(b"tail of line\nnext line\n")
            info._file_ends_with_newline = True

            result = await manager._get_recent_lines(info, 50)

            # Stitched form must NOT appear — the file already ended
            # the line, so "head" and "tail" stay separate.
            assert "head of line tail of line" not in result, (
                f"Unexpected stitch on clean boundary: {result!r}"
            )
            # Both halves appear as independent lines.
            assert "head of line" in result
            assert "tail of line" in result
            # 3 separate lines (head, tail, next) — no stitching.
            lines = result.splitlines()
            assert len(lines) == 3, (
                f"Expected 3 unstitched lines, got {len(lines)}: {lines!r}"
            )
        finally:
            try:
                os.unlink(spill_path)
            except OSError:
                pass


# =============================================================================
# Group 9: _read_file_tail_sync unit tests
# =============================================================================


class TestReadFileTailSync:
    """Unit tests for the backward chunked reader used by
    :meth:`BackgroundProcessManager._read_file_tail_sync`.

    The reader walks the file in 64 KB chunks from EOF toward the start
    until it has enough newlines, then stitches and returns the last N
    lines (oldest → newest). These tests pin down corner cases that
    would break the chunk-boundary math: tiny files, multi-chunk files
    whose line length does not evenly divide 64 KB, files without
    trailing newlines, and empty files.
    """

    def _make_spill_file(self, content: bytes) -> str:
        """Write ``content`` to a temp spill file and return its path."""
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            suffix=".log",
            delete=False,
        )
        try:
            handle.write(content)
        finally:
            handle.close()
        return handle.name

    def _make_info(self, file_path: str):
        from daemon.tools.proc_tools import ProcessInfo

        return ProcessInfo(
            process_id="proc-tailtest",
            instance_id="tail-test",
            command="test",
            file_path=file_path,
        )

    def test_small_file_returns_last_n_lines(self):
        """200 lines (under 64 KB) — request 10 → last 10 in order."""
        from daemon.tools.proc_tools import BackgroundProcessManager

        content = "".join(f"line{i:04d}\n" for i in range(200)).encode()
        assert len(content) < 64 * 1024, "test pre-condition: fits in 1 chunk"

        path = self._make_spill_file(content)
        try:
            info = self._make_info(path)
            lines = BackgroundProcessManager._read_file_tail_sync(info, 10)
            assert lines == [f"line{i:04d}" for i in range(190, 200)], (
                f"Unexpected lines: {lines}"
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_multichunk_file_under_64kb_boundary(self):
        """>64 KB file with 7-byte lines (15000 × 7 = 105 KB) — request
        30 → exactly the last 30 lines, no partials, no duplicates.
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        content = "".join(f"L{i:05d}\n" for i in range(15000)).encode()
        assert len(content) > 64 * 1024, "test pre-condition: spans chunks"
        # 15000 lines × 7 bytes = 105000 bytes
        assert len(content) == 105000

        path = self._make_spill_file(content)
        try:
            info = self._make_info(path)
            lines = BackgroundProcessManager._read_file_tail_sync(info, 30)
            assert len(lines) == 30, f"Expected 30 lines, got {len(lines)}"
            expected = [f"L{i:05d}" for i in range(14970, 15000)]
            assert lines == expected, (
                f"Last 30 lines mismatch.\nGot: {lines[:5]}...{lines[-5:]}\n"
                f"Expected: {expected[:5]}...{expected[-5:]}"
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_file_without_trailing_newline(self):
        """File with 100 lines, no trailing newline — request 5 →
        ``splitlines`` correctly returns the last 5 (the final
        line-without-newline is preserved).
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        # 99 lines with \n + 1 final line WITHOUT \n. Encode to bytes
        # because ``_make_spill_file`` writes binary content.
        content = (
            "".join(f"L{i:05d}\n" for i in range(99)) + "L00099"
        ).encode()
        assert not content.endswith(b"\n"), (
            "test pre-condition: file must not end with a newline"
        )

        path = self._make_spill_file(content)
        try:
            info = self._make_info(path)
            lines = BackgroundProcessManager._read_file_tail_sync(info, 5)
            assert lines == [f"L{i:05d}" for i in range(95, 100)], (
                f"Unexpected lines: {lines}"
            )
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_empty_file_returns_empty_list(self):
        """Empty file → ``[]`` (defensive: avoids crashing on spill
        files that were created but never written to)."""
        from daemon.tools.proc_tools import BackgroundProcessManager

        path = self._make_spill_file(b"")
        try:
            info = self._make_info(path)
            lines = BackgroundProcessManager._read_file_tail_sync(info, 10)
            assert lines == [], f"Expected empty list, got {lines!r}"
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass


# =============================================================================
# Group 10: Spawn-window race (C2 regression)
# =============================================================================


class TestSpawnWindowRace:
    """C2 fix regression: if ``cleanup_instance`` runs while
    :meth:`start_process` is awaiting subprocess creation (i.e. between
    the lock-release that registers the stub and the post-spawn
    re-check), the manager must detect the cleanup and kill the
    orphaned subprocess + cancel its tasks.

    Without this guard, the spawned subprocess would leak: the bucket
    no longer tracks it, but the OS process is still alive with reader
    + drain tasks pointing at it.
    """

    async def test_cleanup_during_spawn_kills_orphan(
        self, unique_instance_id
    ):
        """Race ``cleanup_instance`` into the spawn window via a mocked
        ``asyncio.create_subprocess_shell`` that sleeps first. Assert
        that ``start_process`` returns the C2 error AND the orphan
        subprocess's ``kill()`` was called.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import get_background_process_manager

        manager = get_background_process_manager()

        # Track every call to the mock proc's kill() — the C2 fix
        # must invoke it after detecting the cleanup.
        killed_pids: list[int] = []

        async def slow_spawn(command, **kwargs):
            """Simulate the spawn window: sleep, then trigger cleanup
            during the window, then return a mock proc.
            """
            # Sleep gives the test loop time to interleave tasks.
            await asyncio.sleep(0.1)
            # Pop the stub from the bucket while start_process is
            # awaiting this coroutine.
            await manager.cleanup_instance(unique_instance_id)
            # Return a mock proc whose kill() we can detect.
            mock_proc = MagicMock()
            mock_proc.pid = 99999  # non-existent pid
            mock_proc.returncode = None
            mock_proc.stdout = None  # reader exits immediately
            mock_proc.kill = MagicMock(
                side_effect=lambda: killed_pids.append(mock_proc.pid)
            )
            mock_proc.wait = AsyncMock(return_value=-9)
            return mock_proc

        with patch(
            "daemon.tools.proc_tools.asyncio.create_subprocess_shell",
            new=slow_spawn,
        ):
            process_id, err = await manager.start_process(
                instance_id=unique_instance_id,
                command="sleep 100",
                workdir=None,
                timeout_seconds=0,
            )

        # C2 fix: must return the "cleaned up during start" error
        # AND a None process_id (caller cannot use the id).
        assert err is not None, (
            "start_process returned no error after cleanup-during-spawn"
        )
        assert "cleaned up during start" in err, (
            f"Unexpected error from start_process: {err!r}"
        )
        assert process_id is None, (
            f"Expected None process_id, got: {process_id!r}"
        )

        # The orphan subprocess must have been killed — otherwise the
        # whole point of the C2 fix is moot.
        assert killed_pids, (
            "Orphan proc.kill() was not called by the C2 fix"
        )
        assert killed_pids[0] == 99999, (
            f"kill() called on unexpected pid: {killed_pids}"
        )

        # Bucket must be empty after the test (C2 doesn't re-add the
        # process; cleanup_instance popped it).
        assert unique_instance_id not in manager._processes, (
            f"Bucket for {unique_instance_id} should be empty after C2"
        )


# =============================================================================
# Group 11: Timeout-killer auto-kill path
# =============================================================================


class TestTimeoutKiller:
    """Verify that ``timeout=`` schedules an auto-kill task that sets
    ``info.timed_out = True`` before killing the process — so
    :func:`_drain_exit_code` reports ``"killed"`` rather than
    ``"exited"`` and ``proc_status`` surfaces ``timed_out: true``.
    """

    async def test_timeout_kills_process_and_marks_timed_out(
        self, proc_tools
    ):
        """timeout=1 on a 10s sleep → after ~2s, status=killed and
        timed_out=true.
        """
        tools, _ = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]

        result = await proc_run.ainvoke({
            "command": (
                f"{sys.executable} -u -c "
                "\"import time; print('start', flush=True); "
                "time.sleep(10)\""
            ),
            "timeout": 1,
        })
        assert "Error:" not in result, f"proc_run failed: {result}"
        match = re.search(r"(proc-[0-9a-f]+)", result)
        assert match, f"Could not find process_id: {result}"
        process_id = match.group(1)

        # Timeout fires at t=1s; drain task settles ~immediately after
        # the OS kills the proc. Wait 2.5s for headroom.
        await asyncio.sleep(2.5)

        status = await proc_status.ainvoke({"process_id": process_id})
        assert "status: killed" in status, (
            f"Expected 'status: killed', got:\n{status}"
        )
        assert "timed_out: true" in status, (
            f"Expected 'timed_out: true' in status, got:\n{status}"
        )


# =============================================================================
# Group 12: Multi-chunk spillover (>8 MB output)
# =============================================================================


class TestMultiChunkSpillover:
    """Verify that emitting >>4 MB triggers multiple spills while
    keeping memory bounded and the spill file proportionally large.

    At >2× the memory cap the buffer is forced to spill multiple times;
    we assert that the spill file grows past the cap (proving that the
    oldest data was actually persisted to disk) and that ``proc_logs``
    still returns valid content from the merged view.
    """

    async def test_emitting_9mb_caps_memory_and_grows_spill_file(
        self, proc_tools, unique_instance_id
    ):
        """90 × 100 KB ≈ 9 MB → memory ≤ 4 MB, spill > 4 MB,
        proc_logs returns valid merged content.
        """
        from daemon.tools.proc_tools import (
            _MEMORY_BUFFER_LIMIT_BYTES,
            get_background_process_manager,
        )

        tools, inst_id = proc_tools
        proc_run = tools["proc_run"]
        proc_status = tools["proc_status"]
        proc_logs = tools["proc_logs"]

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
        ) as script:
            script.write(
                "import sys\n"
                "chunk = b'x' * 100000 + b'\\n'  # 100 KB per line\n"
                "for _ in range(90):  # 90 * 100 KB = 9 MB\n"
                "    sys.stdout.buffer.write(chunk)\n"
                "sys.stdout.buffer.flush()\n"
            )
            script_path = script.name

        try:
            result = await proc_run.ainvoke(
                {"command": f"{sys.executable} -u {script_path}"}
            )
            assert "Error:" not in result, f"proc_run failed: {result}"
            match = re.search(r"(proc-[0-9a-f]+)", result)
            assert match, f"Could not find process_id: {result}"
            process_id = match.group(1)

            # Wait for the subprocess to exit (it writes 9 MB and
            # returns). Poll up to ~20s — larger than the 5 MB test's
            # 10s budget because we're moving 9 MB through a pipe.
            deadline = asyncio.get_event_loop().time() + 20.0
            while asyncio.get_event_loop().time() < deadline:
                status = await proc_status.ainvoke(
                    {"process_id": process_id}
                )
                if "status: exited" in status or "status: killed" in status:
                    break
                await asyncio.sleep(0.2)

            # Let the reader + drain tasks settle.
            await asyncio.sleep(0.5)

            manager = get_background_process_manager()
            bucket = manager._processes.get(inst_id, {})
            info = bucket.get(process_id)
            assert info is not None, (
                f"Process {process_id} not found in manager"
            )

            # Memory buffer must be capped.
            assert len(info.memory_buffer) <= _MEMORY_BUFFER_LIMIT_BYTES, (
                f"Memory buffer ({len(info.memory_buffer)} bytes) exceeds "
                f"cap ({_MEMORY_BUFFER_LIMIT_BYTES})"
            )

            # Spill file must exist and grow past the cap.
            assert info.file_path is not None, (
                "Spill file path is None — spillover did not occur"
            )
            assert os.path.exists(info.file_path), (
                f"Spill file does not exist: {info.file_path}"
            )
            file_size = os.path.getsize(info.file_path)
            assert file_size > _MEMORY_BUFFER_LIMIT_BYTES, (
                f"Spill file ({file_size} bytes) should exceed memory "
                f"cap ({_MEMORY_BUFFER_LIMIT_BYTES}) for 9 MB output"
            )

            # proc_logs must return merged content from file + memory.
            logs_result = await proc_logs.ainvoke(
                {"process_id": process_id, "lines": 100}
            )
            assert "Error:" not in logs_result, (
                f"proc_logs failed: {logs_result}"
            )
            assert "x" in logs_result, (
                f"Expected 'x' in merged logs, got: {logs_result[:200]}"
            )
            assert "status=" in logs_result
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass


# =============================================================================
# Group 13: PID safety — defense-in-depth kill gates (Layer 1/2/3)
# =============================================================================
#
# ``stop_process`` routes every kill through ``_attempt_kill_signal``, which
# applies three defense layers BEFORE delivering any signal:
#
#   Layer 1 (status gate)   — skip if ``info.status`` is already terminal.
#   Layer 2 (liveness)      — skip if ``proc.returncode`` is set (OS reaped).
#   Layer 3 (PID ownership) — ABORT if the PID was recycled to an unrelated
#                             process (verified via the tracking env var).
#
# These tests exercise each layer in isolation by constructing mock
# ``ProcessInfo`` entries and patching OS-level functions. The mock ``proc``
# object exposes ``.returncode``, ``.pid``, ``.send_signal``, ``.kill``
# (``MagicMock`` spies) and ``.wait`` (``AsyncMock``). Tests assert on the
# spies to verify whether a kill signal was delivered.


def _make_pid_safety_info(
    process_id: str = "proc-deadbeef",
    instance_id: str = "pid-safety-test",
    status: str = "running",
    returncode: int | None = None,
    tracking_id: str = "",
    pid: int = 12345,
):
    """Build a ``ProcessInfo`` with a fully mocked ``proc`` for PID safety tests.

    The mock ``proc`` exposes ``.returncode``, ``.pid``, ``.send_signal``,
    ``.kill`` (``MagicMock`` spies), and ``.wait`` (``AsyncMock``). Tests
    assert on the spies to verify whether a kill signal was delivered.
    """
    from daemon.tools.proc_tools import ProcessInfo

    info = ProcessInfo(
        process_id=process_id,
        instance_id=instance_id,
        command="test-command",
    )
    info.status = status
    info.tracking_id = tracking_id

    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(
        return_value=returncode if returncode is not None else 0
    )
    info.proc = proc

    return info


class TestPIDSafetyStatusGate:
    """Layer 1 — status gate: ``stop_process`` must skip the kill entirely
    when ``info.status`` is already terminal.

    Terminal states: ``"exited"``, ``"killed"``, ``"error"``. In all these
    cases ``stop_process`` returns a success-style idempotency message and
    does NOT call ``_attempt_kill_signal`` — so no signal of any kind
    reaches the (possibly recycled) PID.
    """

    async def test_stop_when_already_exited(self):
        """``status='exited'`` → idempotent stop, no kill signal sent."""
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(status="exited", returncode=0)
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        # No Error: success-style idempotency message.
        assert "Error:" not in result, f"Expected success, got: {result}"
        assert "already stopped" in result.lower(), (
            f"Expected idempotent message, got: {result}"
        )
        # No kill signal sent (any path).
        assert not mock_killpg.called, (
            "os.killpg must not be called when status is already terminal"
        )
        assert not proc.send_signal.called, (
            "proc.send_signal must not be called when status is terminal"
        )
        assert not proc.kill.called, (
            "proc.kill must not be called when status is terminal"
        )

    async def test_stop_when_already_killed(self):
        """``status='killed'`` → idempotent stop, no kill signal sent."""
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="killed", returncode=-9, tracking_id="proc-deadbeef"
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        assert "Error:" not in result, f"Expected success, got: {result}"
        assert "already stopped" in result.lower(), (
            f"Expected idempotent message, got: {result}"
        )
        assert not mock_killpg.called
        assert not proc.send_signal.called
        assert not proc.kill.called

    async def test_stop_when_already_error(self):
        """``status='error'`` → idempotent stop, no kill signal sent."""
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="error", returncode=None, tracking_id="proc-deadbeef"
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        assert "Error:" not in result, f"Expected success, got: {result}"
        assert "already stopped" in result.lower(), (
            f"Expected idempotent message, got: {result}"
        )
        assert not mock_killpg.called
        assert not proc.send_signal.called
        assert not proc.kill.called


class TestPIDSafetyLiveness:
    """Layer 2 — liveness check: even when ``status='running'``, if
    ``proc.returncode`` is already set (the OS has reaped the process),
    ``_attempt_kill_signal`` must skip the kill, update status to a
    terminal state, and record the exit code.
    """

    async def test_stop_when_returncode_set_skips_kill(self):
        """``status='running'`` but ``proc.returncode=0`` → Layer 2
        intercepts, no kill signal, status/exit_code updated.
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="running",
            returncode=0,
            tracking_id="proc-deadbeef",
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        # Success (no "Error:").
        assert "Error:" not in result, f"Expected success, got: {result}"
        # No kill signal sent (Layer 2 intercepted before delivery).
        assert not mock_killpg.called, (
            "os.killpg must not be called when returncode is already set"
        )
        assert not proc.send_signal.called
        assert not proc.kill.called
        # Status updated to terminal.
        assert info.status in ("exited", "killed"), (
            f"Expected terminal status, got: {info.status}"
        )
        # Exit code recorded.
        assert info.exit_code == 0, (
            f"Expected exit_code=0, got: {info.exit_code}"
        )

    async def test_stop_when_returncode_negative(self):
        """``status='running'`` but ``proc.returncode=-15`` (SIGTERM) →
        Layer 2 intercepts, status updated to terminal, exit_code=-15.
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="running",
            returncode=-15,
            tracking_id="proc-deadbeef",
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        assert "Error:" not in result, f"Expected success, got: {result}"
        assert not mock_killpg.called
        assert not proc.send_signal.called
        assert not proc.kill.called
        assert info.status in ("exited", "killed"), (
            f"Expected terminal status, got: {info.status}"
        )
        assert info.exit_code == -15, (
            f"Expected exit_code=-15, got: {info.exit_code}"
        )


class TestPIDOwnershipVerification:
    """Layer 3 — PID ownership verification: before sending a kill signal,
    ``_attempt_kill_signal`` reads back the tracking env var from the live
    process. If the PID was recycled (env var absent or mismatched), the
    kill is ABORTED.

    Patch target: ``daemon.tools.proc_tools._verify_pid_ownership``
    (module-level function called directly by ``_attempt_kill_signal``).
    Return contract:
        True  → owned (safe to kill)
        False → recycled (ABORT kill)
        None  → undetermined (fail-open, proceed)
    """

    async def test_recycled_pid_aborts_kill(self):
        """``_verify_pid_ownership`` returns ``False`` → kill is ABORTED,
        no signal sent, result surfaces the recycled-PID warning.
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="running",
            returncode=None,
            tracking_id="proc-deadbeef",
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=False,
        ) as mock_verify, \
             patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        # Layer 3 verification was invoked.
        assert mock_verify.called, (
            "_verify_pid_ownership should be called when tracking_id is set"
        )
        # NO kill signal sent (any path).
        assert not mock_killpg.called, (
            "os.killpg must not be called when PID ownership verification "
            "reports the PID as recycled"
        )
        assert not proc.send_signal.called, (
            "proc.send_signal must not be called when PID is recycled"
        )
        assert not proc.kill.called, (
            "proc.kill must not be called when PID is recycled"
        )
        # Result surfaces the critical/recycled warning.
        assert "WARNING" in result or "ABORT" in result, (
            f"Expected PID-recycled warning in result, got: {result}"
        )
        # info.status should NOT become "killed" — the kill was aborted,
        # the process may still be alive. (NOTE: this assertion encodes
        # the safety contract; if it fails it reveals a bug in
        # stop_process's post-kill status promotion.)
        assert info.status != "killed", (
            f"status should NOT be 'killed' when kill was aborted "
            f"(PID recycled), got: {info.status}"
        )

    async def test_undetermined_proceeds_fail_open(self):
        """``_verify_pid_ownership`` returns ``None`` → fail-open, kill
        proceeds, status becomes "killed".
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="running",
            returncode=None,
            tracking_id="proc-deadbeef",
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=None,
        ) as mock_verify, \
             patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        # Layer 3 was consulted.
        assert mock_verify.called
        # Kill DOES proceed (fail-open on undetermined).
        assert mock_killpg.called or info.proc.send_signal.called, (
            "Expected kill signal on fail-open (None); neither killpg "
            "nor send_signal was called"
        )
        # No false-positive recycled warning.
        assert "PID ownership verification failed" not in result, (
            f"Recycled warning should NOT appear on fail-open, got: {result}"
        )
        # Status becomes "killed".
        assert info.status == "killed", (
            f"Expected status='killed' on fail-open, got: {info.status}"
        )

    async def test_owned_proceeds_with_kill(self):
        """``_verify_pid_ownership`` returns ``True`` → PID is ours, kill
        proceeds, status becomes "killed".
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="running",
            returncode=None,
            tracking_id="proc-deadbeef",
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=True,
        ) as mock_verify, \
             patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            result = await manager.stop_process(instance_id, process_id)

        # Layer 3 was consulted.
        assert mock_verify.called
        # Kill proceeds.
        assert mock_killpg.called or info.proc.send_signal.called, (
            "Expected kill signal on owned PID; neither killpg nor "
            "send_signal was called"
        )
        # No false-positive recycled warning.
        assert "PID ownership verification failed" not in result, (
            f"Recycled warning should NOT appear on owned PID, got: {result}"
        )
        # Status becomes "killed".
        assert info.status == "killed", (
            f"Expected status='killed' on owned PID, got: {info.status}"
        )


class TestIdempotentStop:
    """Double-stopping a process must be safe: the second call returns
    success without raising or sending another signal.
    """

    async def test_double_stop_safe(self):
        """First ``stop_process`` kills the process (status → killed);
        second ``stop_process`` returns idempotently WITHOUT sending
        another signal.
        """
        from daemon.tools.proc_tools import BackgroundProcessManager

        manager = BackgroundProcessManager()
        info = _make_pid_safety_info(
            status="running",
            returncode=None,
            tracking_id="proc-deadbeef",
        )
        instance_id = info.instance_id
        process_id = info.process_id
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=True,
        ), \
             patch("os.killpg") as mock_killpg, \
             patch("os.getpgid", return_value=12345):
            # First stop — kills the process.
            result1 = await manager.stop_process(instance_id, process_id)
            assert "Error:" not in result1, f"First stop failed: {result1}"
            assert info.status == "killed", (
                f"Expected status='killed' after first stop, got: "
                f"{info.status}"
            )
            calls_after_first = mock_killpg.call_count
            assert calls_after_first >= 1, (
                "First stop should have sent at least one kill signal"
            )

            # Second stop — must be idempotent.
            result2 = await manager.stop_process(instance_id, process_id)
            assert "Error:" not in result2, (
                f"Second stop should be idempotent, got: {result2}"
            )
            assert "already stopped" in result2.lower(), (
                f"Second stop should return idempotent message, got: "
                f"{result2}"
            )

            # No additional kill signal sent on the second call.
            assert mock_killpg.call_count == calls_after_first, (
                f"Second stop should not send another kill signal: "
                f"calls={calls_after_first} → {mock_killpg.call_count}"
            )


# =============================================================================
# Group 13: stop_process defense layers
# =============================================================================


class TestStopProcessDefenseLayers:
    """Unit tests for the three defense layers in :meth:`stop_process`.

    Each test instantiates a fresh :class:`BackgroundProcessManager`
    and a real :class:`ProcessInfo`, injects the info into
    ``manager._processes``, and patches ``sys.platform = "win32"`` so
    the kill path routes through ``proc.send_signal`` (which we can
    assert on) rather than ``os.killpg``.

    Layers exercised:

    * **L1 status gate** — already-terminal processes skip the kill.
    * **L2 liveness check** — ``proc.returncode`` set means already
      exited; no signal needed.
    * **L3 ownership check** — ``_verify_pid_ownership`` returns
      ``True`` / ``None`` (proceed) or ``False`` (abort).
    """

    async def test_stop_terminal_status_is_idempotent(self):
        """L1: ``info.status='exited'`` → stop returns idempotently, no signal."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        instance_id = f"test-stop-def-{os.urandom(4).hex()}"
        process_id = "proc-terminal01"

        proc = MagicMock()
        proc.pid = 9999
        proc.send_signal = MagicMock()
        proc.wait = AsyncMock(return_value=-9)
        proc.returncode = 0

        info = ProcessInfo(
            process_id=process_id,
            instance_id=instance_id,
            command="echo done",
            proc=proc,
            status="exited",
            exit_code=0,
        )
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools.BackgroundProcessManager._attempt_kill_signal"
        ) as helper:
            with patch("daemon.tools.proc_tools.sys.platform", "win32"):
                result = await manager.stop_process(
                    instance_id=instance_id,
                    process_id=process_id,
                    force=True,
                )

        assert "Error:" not in result, f"Unexpected error: {result}"
        assert "already stopped" in result.lower(), (
            f"Expected idempotent message, got: {result}"
        )
        assert info.status == "exited", (
            f"Status should remain 'exited', got: {info.status}"
        )
        proc.send_signal.assert_not_called()
        helper.assert_not_called()

    async def test_stop_running_with_returncode_set_reports_exit_code_42(self):
        """L2: ``proc.returncode=42`` → Layer 2 short-circuits, no signal, status terminal."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        instance_id = f"test-stop-def-{os.urandom(4).hex()}"
        process_id = "proc-rccode42"

        proc = MagicMock()
        proc.pid = 4242
        proc.send_signal = MagicMock()
        proc.wait = AsyncMock(return_value=42)
        proc.returncode = 42

        info = ProcessInfo(
            process_id=process_id,
            instance_id=instance_id,
            command="exit 42",
            proc=proc,
            status="running",
        )
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch("daemon.tools.proc_tools.sys.platform", "win32"):
            result = await manager.stop_process(
                instance_id=instance_id,
                process_id=process_id,
                force=True,
            )

        assert "Error:" not in result, f"Unexpected error: {result}"
        assert info.exit_code == 42, (
            f"Expected exit_code=42, got: {info.exit_code}"
        )
        assert info.status in ("exited", "killed"), (
            f"Expected terminal status, got: {info.status}"
        )
        proc.send_signal.assert_not_called()

    async def test_stop_aborts_on_pid_recycling(self, caplog):
        """L3=False: abort/recycling reported; status MUST NOT be 'killed'.

        The hard ``status != 'killed'`` assertion is intentional — it
        flags the source bug where ``stop_process`` falls through to
        ``info.status = "killed"`` even after
        ``_attempt_kill_signal`` returned ``"aborted_recycled"``. The
        correct behavior is to leave status untouched so the agent can
        see that no kill was actually delivered.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        instance_id = f"test-stop-def-{os.urandom(4).hex()}"
        process_id = "proc-deadbeef"
        tracking_id = "proc-deadbeef"

        proc = MagicMock()
        proc.pid = 12345
        proc.send_signal = MagicMock()
        proc.wait = AsyncMock(return_value=-9)
        proc.returncode = None

        info = ProcessInfo(
            process_id=process_id,
            instance_id=instance_id,
            command="sleeper",
            proc=proc,
            status="running",
            tracking_id=tracking_id,
        )
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=False,
        ):
            with patch("daemon.tools.proc_tools.sys.platform", "win32"):
                with caplog.at_level(
                    "WARNING", logger="daemon.tools.proc_tools"
                ):
                    result = await manager.stop_process(
                        instance_id=instance_id,
                        process_id=process_id,
                        force=True,
                    )

        proc.send_signal.assert_not_called()
        text = (result + "\n" + caplog.text).lower()
        assert "abort" in text or "recycl" in text, (
            f"Expected abort/recycling mention in response or log, "
            f"got response={result!r}, log={caplog.text!r}"
        )
        assert info.status != "killed", (
            f"Status should not be 'killed' after PID-recycle abort, "
            f"got: {info.status!r}"
        )

    async def test_stop_proceeds_when_verifier_returns_none(self):
        """L3=None (fail-open): signal proceeds, status='killed'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        instance_id = f"test-stop-def-{os.urandom(4).hex()}"
        process_id = "proc-failopen"
        tracking_id = "proc-failopen"

        proc = MagicMock()
        proc.pid = 23456
        proc.send_signal = MagicMock()
        proc.wait = AsyncMock(return_value=-9)
        proc.returncode = None

        info = ProcessInfo(
            process_id=process_id,
            instance_id=instance_id,
            command="sleeper",
            proc=proc,
            status="running",
            tracking_id=tracking_id,
        )
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=None,
        ):
            with patch("daemon.tools.proc_tools.sys.platform", "win32"):
                result = await manager.stop_process(
                    instance_id=instance_id,
                    process_id=process_id,
                    force=True,
                )

        assert "Error:" not in result, f"Unexpected error: {result}"
        proc.send_signal.assert_called_once()
        assert info.status == "killed", (
            f"Expected status='killed', got: {info.status}"
        )

    async def test_stop_proceeds_when_verifier_returns_true(self):
        """L3=True (PID owned): signal proceeds, status='killed'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        instance_id = f"test-stop-def-{os.urandom(4).hex()}"
        process_id = "proc-owned"
        tracking_id = "proc-owned"

        proc = MagicMock()
        proc.pid = 34567
        proc.send_signal = MagicMock()
        proc.wait = AsyncMock(return_value=-9)
        proc.returncode = None

        info = ProcessInfo(
            process_id=process_id,
            instance_id=instance_id,
            command="sleeper",
            proc=proc,
            status="running",
            tracking_id=tracking_id,
        )
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=True,
        ):
            with patch("daemon.tools.proc_tools.sys.platform", "win32"):
                result = await manager.stop_process(
                    instance_id=instance_id,
                    process_id=process_id,
                    force=True,
                )

        assert "Error:" not in result, f"Unexpected error: {result}"
        proc.send_signal.assert_called_once()
        assert info.status == "killed", (
            f"Expected status='killed', got: {info.status}"
        )

    async def test_double_stop_sends_one_signal(self):
        """First stop → 1 signal + status killed; second stop idempotent + total count stays 1."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.tools.proc_tools import (
            BackgroundProcessManager,
            ProcessInfo,
        )

        manager = BackgroundProcessManager()
        instance_id = f"test-stop-def-{os.urandom(4).hex()}"
        process_id = "proc-twostop"
        tracking_id = "proc-twostop"

        proc = MagicMock()
        proc.pid = 45678
        proc.send_signal = MagicMock()
        proc.wait = AsyncMock(return_value=-9)
        proc.returncode = None

        info = ProcessInfo(
            process_id=process_id,
            instance_id=instance_id,
            command="sleeper",
            proc=proc,
            status="running",
            tracking_id=tracking_id,
        )
        manager._processes[instance_id] = {process_id: info}
        proc = info.proc  # local: typed as MagicMock for assertion access

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership",
            return_value=True,
        ):
            with patch("daemon.tools.proc_tools.sys.platform", "win32"):
                first = await manager.stop_process(
                    instance_id=instance_id,
                    process_id=process_id,
                    force=True,
                )
                assert "Error:" not in first, f"First stop error: {first}"
                assert info.status == "killed", (
                    f"Expected status='killed' after first stop, "
                    f"got: {info.status}"
                )

                second = await manager.stop_process(
                    instance_id=instance_id,
                    process_id=process_id,
                    force=True,
                )

        assert "Error:" not in second, f"Second stop error: {second}"
        assert "already stopped" in second.lower(), (
            f"Expected idempotent message on second stop, got: {second}"
        )
        assert proc.send_signal.call_count == 1, (
            f"Expected exactly 1 send_signal call across both stops, "
            f"got: {proc.send_signal.call_count}"
        )


class TestVerifyPidOwnershipProcfs:
    """Layer 3 Linux backend — ``_verify_pid_ownership_procfs``.

    Reads ``/proc/{pid}/environ`` (NUL-separated ``KEY=VALUE`` entries)
    and decides owned / recycled / fail-open. These tests exercise the
    ACTUAL parser (no patching of the helper) and feed it synthetic
    ``/proc/{pid}/environ`` blobs via a mocked ``builtins.open``.

    Contract:
        True  → marker present with matching value (safe to kill)
        False → marker absent OR present with a different value (recycled)
        None  → file unreadable / process gone / permission denied
    """

    def _make_environ_blob(self, entries: list[tuple[str, str]]) -> bytes:
        """Build a ``/proc/{pid}/environ``-style blob: NUL-separated ``K=V``."""
        return "\x00".join(f"{k}={v}" for k, v in entries).encode("utf-8")

    def test_correct_tracking_id_returns_true(self):
        """Blob contains our marker with the matching value → owned."""
        from daemon.tools.proc_tools import _verify_pid_ownership_procfs

        tracking_id = "proc-deadbeef"
        blob = self._make_environ_blob(
            [
                ("PATH", "/usr/bin:/bin"),
                ("HOME", "/home/tester"),
                ("ENSEMBLE_PROC_TRACKING_ID", tracking_id),
                ("LANG", "en_US.UTF-8"),
            ]
        )

        fake_fh = MagicMock()
        fake_fh.read = MagicMock(return_value=blob)
        fake_fh.__enter__ = MagicMock(return_value=fake_fh)
        fake_fh.__exit__ = MagicMock(return_value=False)

        with patch("builtins.open", return_value=fake_fh, create=True):
            result = _verify_pid_ownership_procfs(12345, tracking_id)

        assert result is True, (
            f"Expected True for matching tracking id, got: {result!r}"
        )

    def test_wrong_tracking_id_returns_false(self):
        """Blob contains the marker key but with a DIFFERENT value → recycled."""
        from daemon.tools.proc_tools import _verify_pid_ownership_procfs

        blob = self._make_environ_blob(
            [
                ("PATH", "/usr/bin:/bin"),
                ("ENSEMBLE_PROC_TRACKING_ID", "proc-different"),
            ]
        )

        fake_fh = MagicMock()
        fake_fh.read = MagicMock(return_value=blob)
        fake_fh.__enter__ = MagicMock(return_value=fake_fh)
        fake_fh.__exit__ = MagicMock(return_value=False)

        with patch("builtins.open", return_value=fake_fh, create=True):
            result = _verify_pid_ownership_procfs(12345, "proc-deadbeef")

        assert result is False, (
            f"Expected False for mismatched tracking id, got: {result!r}"
        )

    def test_missing_env_var_returns_false(self):
        """Blob has env vars but NOT our marker → recycled (abort)."""
        from daemon.tools.proc_tools import _verify_pid_ownership_procfs

        blob = self._make_environ_blob(
            [
                ("PATH", "/usr/bin:/bin"),
                ("HOME", "/home/tester"),
                ("LANG", "en_US.UTF-8"),
            ]
        )

        fake_fh = MagicMock()
        fake_fh.read = MagicMock(return_value=blob)
        fake_fh.__enter__ = MagicMock(return_value=fake_fh)
        fake_fh.__exit__ = MagicMock(return_value=False)

        with patch("builtins.open", return_value=fake_fh, create=True):
            result = _verify_pid_ownership_procfs(12345, "proc-deadbeef")

        assert result is False, (
            f"Expected False for absent marker, got: {result!r}"
        )

    def test_marker_as_substring_of_other_value_returns_false(self):
        """Marker text appears as a substring inside another env var's
        VALUE — must NOT false-positive match. This is the bug the
        NUL-split fix prevents (the original ``if needle in data``
        substring search would incorrectly report owned here).
        """
        from daemon.tools.proc_tools import _verify_pid_ownership_procfs

        # An env var whose value CONTAINS the marker string as a substring
        # but is NOT an exact ``ENSEMBLE_PROC_TRACKING_ID=proc-deadbeef``
        # entry.
        blob = self._make_environ_blob(
            [
                ("PATH", "/usr/bin:/bin"),
                (
                    "SOME_BLOB",
                    "garbageENSEMBLE_PROC_TRACKING_ID=proc-deadbeefmore_garbage",
                ),
            ]
        )

        fake_fh = MagicMock()
        fake_fh.read = MagicMock(return_value=blob)
        fake_fh.__enter__ = MagicMock(return_value=fake_fh)
        fake_fh.__exit__ = MagicMock(return_value=False)

        with patch("builtins.open", return_value=fake_fh, create=True):
            result = _verify_pid_ownership_procfs(12345, "proc-deadbeef")

        assert result is False, (
            f"Expected False for marker-as-substring case, got: {result!r}. "
            f"This indicates the procfs parser regressed to substring match."
        )

    def test_file_not_found_returns_none(self):
        """``/proc/{pid}/environ`` missing → fail-open (None)."""
        from daemon.tools.proc_tools import _verify_pid_ownership_procfs

        # ``open`` raises ``FileNotFoundError`` directly (not via __enter__).
        with patch(
            "builtins.open",
            side_effect=FileNotFoundError(
                "[Errno 2] No such file or directory: '/proc/12345/environ'"
            ),
            create=True,
        ):
            result = _verify_pid_ownership_procfs(12345, "proc-deadbeef")

        assert result is None, (
            f"Expected None for missing file (fail-open), got: {result!r}"
        )


class TestVerifyPidOwnershipPs:
    """Layer 3 macOS / BSD backend — ``_verify_pid_ownership_ps``.

    Runs ``ps eww -p <pid>`` (env block) and a heuristic
    ``ps -p <pid> -o command=`` to detect the macOS "env not surfaced"
    case. These tests patch ``subprocess.run`` and feed synthetic
    outputs.

    Note: ``_verify_pid_ownership_ps`` may call ``subprocess.run`` twice —
    once for ``ps eww`` and once for the heuristic ``ps -o command=``.
    Tests use ``side_effect`` to return different values per call.

    Contract:
        True  → marker found in env block (owned)
        False → env block surfaced but marker absent / wrong value (recycled)
        None  → process gone (ps exit≠0), ps unavailable, timeout, or
                env block not surfaced (macOS limitation for non-bundled
                CLI children) — fail-open.
    """

    def test_correct_tracking_id_returns_true(self):
        """``ps eww`` output contains our marker → owned."""
        from daemon.tools.proc_tools import _verify_pid_ownership_ps

        tracking_id = "proc-deadbeef"
        # Realistic ``ps eww`` output: header line + command + env block.
        ps_eww = (
            "  PID   TT  STAT      TIME COMMAND\n"
            "12345   ??  S      0:00.01 /bin/sleep 30 "
            "PATH=/usr/bin:/bin HOME=/tmp "
            f"ENSEMBLE_PROC_TRACKING_ID={tracking_id} "
            "LANG=en_US.UTF-8\n"
        )
        # Heuristic ``ps -o command=`` strips env; we don't surface it.
        ps_cmd = "/bin/sleep 30\n"

        completed_eww = MagicMock()
        completed_eww.returncode = 0
        completed_eww.stdout = ps_eww
        completed_cmd = MagicMock()
        completed_cmd.returncode = 0
        completed_cmd.stdout = ps_cmd

        with patch(
            "daemon.tools.proc_tools.subprocess.run",
            side_effect=[completed_eww, completed_cmd],
        ):
            result = _verify_pid_ownership_ps(12345, tracking_id)

        assert result is True, (
            f"Expected True for matching marker in ps eww, got: {result!r}"
        )

    def test_wrong_tracking_id_returns_false(self):
        """Env block is surfaced but the marker has a different value."""
        from daemon.tools.proc_tools import _verify_pid_ownership_ps

        ps_eww = (
            "  PID   TT  STAT      TIME COMMAND\n"
            "12345   ??  S      0:00.01 /bin/sleep 30 "
            "PATH=/usr/bin:/bin "
            "ENSEMBLE_PROC_TRACKING_ID=proc-different\n"
        )
        ps_cmd = "/bin/sleep 30\n"

        completed_eww = MagicMock()
        completed_eww.returncode = 0
        completed_eww.stdout = ps_eww
        completed_cmd = MagicMock()
        completed_cmd.returncode = 0
        completed_cmd.stdout = ps_cmd

        with patch(
            "daemon.tools.proc_tools.subprocess.run",
            side_effect=[completed_eww, completed_cmd],
        ):
            result = _verify_pid_ownership_ps(12345, "proc-deadbeef")

        assert result is False, (
            f"Expected False for mismatched marker (recycled), got: {result!r}"
        )

    def test_process_not_found_returns_none(self):
        """``ps`` exits non-zero (process gone) → fail-open (None)."""
        from daemon.tools.proc_tools import _verify_pid_ownership_ps

        # The heuristic call may still happen AFTER eww; on the
        # "process gone" path we return early, but provide a fallback
        # for either ordering.
        completed_eww = MagicMock()
        completed_eww.returncode = 1  # ps: process not found
        completed_eww.stdout = ""
        completed_fallback = MagicMock()
        completed_fallback.returncode = 1
        completed_fallback.stdout = ""

        with patch(
            "daemon.tools.proc_tools.subprocess.run",
            side_effect=[completed_eww, completed_fallback],
        ):
            result = _verify_pid_ownership_ps(12345, "proc-deadbeef")

        assert result is None, (
            f"Expected None for ps exit≠0 (fail-open), got: {result!r}"
        )


class TestVerifyPidOwnershipDispatch:
    """``_verify_pid_ownership`` dispatches to the platform-specific
    backend based on ``sys.platform``. These tests verify the
    dispatch wiring without exercising the real backends.
    """

    def test_linux_dispatches_to_procfs(self):
        """``sys.platform == 'linux'`` → ``_verify_pid_ownership_procfs``."""
        from daemon.tools.proc_tools import (
            _verify_pid_ownership,
            _verify_pid_ownership_procfs,
        )

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership_procfs",
            return_value=True,
        ) as mock_procfs, patch(
            "daemon.tools.proc_tools.sys.platform", "linux"
        ):
            result = _verify_pid_ownership(12345, "proc-deadbeef")

        mock_procfs.assert_called_once_with(12345, "proc-deadbeef")
        assert result is True, (
            f"Expected True (procfs mock return), got: {result!r}"
        )
        # Sanity: confirm we patched the right symbol.
        assert _verify_pid_ownership_procfs is not None

    def test_macos_dispatches_to_ps(self):
        """``sys.platform == 'darwin'`` → ``_verify_pid_ownership_ps``."""
        from daemon.tools.proc_tools import (
            _verify_pid_ownership,
            _verify_pid_ownership_ps,
        )

        with patch(
            "daemon.tools.proc_tools._verify_pid_ownership_ps",
            return_value=True,
        ) as mock_ps, patch(
            "daemon.tools.proc_tools.sys.platform", "darwin"
        ):
            result = _verify_pid_ownership(12345, "proc-deadbeef")

        mock_ps.assert_called_once_with(12345, "proc-deadbeef")
        assert result is True, (
            f"Expected True (ps mock return), got: {result!r}"
        )
        # Sanity: confirm we patched the right symbol.
        assert _verify_pid_ownership_ps is not None
