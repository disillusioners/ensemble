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
