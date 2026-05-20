"""Unit tests for MCP warmup pool."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from daemon.mcp.warmup_pool import (
    McpWarmupPool,
    PooledConnection,
    get_mcp_warmup_pool,
)
from daemon.mcp.config import McpStdioConfig


def _make_config(command: str = "npx", args: list[str] = None) -> McpStdioConfig:
    """Create a mock STDIO config."""
    return McpStdioConfig(
        transport="stdio",
        command=command,
        args=args or ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
    )


def _make_pooled_connection(
    server_name: str = "context7",
    session: AsyncMock = None,
    stream_cm: MagicMock = None,
) -> PooledConnection:
    """Create a mock PooledConnection."""
    return PooledConnection(
        session=session or AsyncMock(),
        stream_cm=stream_cm or MagicMock(),
        tools=[MagicMock(spec=AsyncMock)],
        server_name=server_name,
        created_at=time.monotonic(),
    )


@pytest.fixture
def pool():
    """Create a fresh McpWarmupPool instance."""
    return McpWarmupPool()


@pytest.fixture
def registered_pool(pool):
    """Pool with a registered server."""
    pool.register_server("context7", _make_config(), pool_size=2)
    return pool


@pytest.fixture
def mock_stdio_client():
    """Mock mcp.stdio_client to return simulated streams."""
    with patch("daemon.mcp.warmup_pool.mcp.stdio_client") as mock_stdio, \
         patch("daemon.mcp.warmup_pool.ManagedClientSession") as mock_client_session_cls:
        # Create mock session that properly handles async methods
        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.send_ping = AsyncMock()
        mock_client_session_cls.return_value = mock_session

        # Create mock context manager for stdio_client
        mock_cm = MagicMock()
        read_stream = AsyncMock()
        write_stream = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(read_stream, write_stream))
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_stdio.return_value = mock_cm
        yield mock_stdio


@pytest.fixture
def mock_load_mcp_tools():
    """Mock load_mcp_tools to return a dummy tool."""
    with patch("daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock) as mock:
        mock.return_value = [MagicMock()]
        yield mock


class TestRegisterServer:
    """Tests for register_server method."""

    def test_register_server_creates_queue_and_lock(self, pool):
        """Register creates queue and lock for the server."""
        pool.register_server("context7", _make_config(), pool_size=3)

        assert "context7" in pool._pools
        assert isinstance(pool._pools["context7"], asyncio.Queue)
        assert "context7" in pool._locks
        assert isinstance(pool._locks["context7"], asyncio.Lock)
        assert pool._pool_sizes["context7"] == 3

    def test_register_server_idempotent(self, pool):
        """Registering same server twice logs warning and skips."""
        pool.register_server("context7", _make_config())

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            pool.register_server("context7", _make_config())
            mock_logger.warning.assert_called_once()

        # Only one queue should exist
        assert len(pool._pools) == 1


class TestSingletonFactory:
    """Tests for get_mcp_warmup_pool singleton."""

    def test_get_mcp_warmup_pool_returns_same_instance(self):
        """get_mcp_warmup_pool should return singleton."""
        import daemon.mcp.warmup_pool as wp

        wp._mcp_warmup_pool = None

        pool1 = get_mcp_warmup_pool()
        pool2 = get_mcp_warmup_pool()

        assert pool1 is pool2

        # Cleanup
        wp._mcp_warmup_pool = None


class TestCreatePooledConnection:
    """Tests for _create_pooled_connection method."""

    @pytest.mark.asyncio
    async def test_create_pooled_connection_cleanup_on_session_failure(self, pool):
        """Verify subprocess cleanup when ManagedClientSession() constructor fails."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", side_effect=RuntimeError("constructor failed")):
            with pytest.raises(RuntimeError, match="constructor failed"):
                await pool._create_pooled_connection("context7")

        # Verify cleanup: __aexit__ was called to terminate subprocess
        mock_cm.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self, pool):
        """Session.initialize() retry succeeds on 2nd attempt."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=[TimeoutError("First timeout"), None])
        mock_session.send_ping = AsyncMock()

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session), \
             patch("daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock) as mock_tools:
            mock_tools.return_value = [MagicMock()]

            conn = await pool._create_pooled_connection("context7")

        assert conn is not None
        assert mock_session.initialize.call_count == 2, "Should have retried once"

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_third_attempt(self, pool):
        """Session.initialize() retry succeeds on 3rd (final) attempt."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        # Fail twice, succeed on third
        mock_session.initialize = AsyncMock(side_effect=[TimeoutError("1"), TimeoutError("2"), None])
        mock_session.send_ping = AsyncMock()

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session), \
             patch("daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock) as mock_tools:
            mock_tools.return_value = [MagicMock()]

            conn = await pool._create_pooled_connection("context7")

        assert conn is not None
        assert mock_session.initialize.call_count == 3, "Should have retried twice"

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self, pool):
        """All 3 retries exhausted raises the final error."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=RuntimeError("Persistent failure"))

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session):
            with pytest.raises(RuntimeError, match="Persistent failure"):
                await pool._create_pooled_connection("context7")

        assert mock_session.initialize.call_count == 3, "Should have attempted 3 times"
        mock_cm.__aexit__.assert_called_once(), "Cleanup should be called"

    @pytest.mark.asyncio
    async def test_retry_exponential_backoff_timing(self, pool):
        """Verify exponential backoff: 2s then 4s delays between retries."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=[TimeoutError("1"), TimeoutError("2"), None])
        mock_session.send_ping = AsyncMock()

        sleep_durations: list[float] = []
        original_sleep = asyncio.sleep

        async def track_sleep(duration: float):
            sleep_durations.append(duration)
            await original_sleep(0)

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session), \
             patch("daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock) as mock_tools, \
             patch("asyncio.sleep", side_effect=track_sleep):
            mock_tools.return_value = [MagicMock()]
            conn = await pool._create_pooled_connection("context7")

        assert conn is not None
        # sleep_durations = [2.0 (startup), 2.0 (retry1 backoff), 4.0 (retry2 backoff)]
        # We only care about retry backoffs: positions 1 and 2
        assert sleep_durations[1] == 2.0, f"Expected 2.0s backoff first, got {sleep_durations[1]}"
        assert sleep_durations[2] == 4.0, f"Expected 4.0s backoff second, got {sleep_durations[2]}"

    @pytest.mark.asyncio
    async def test_per_attempt_timeout_triggers_retry(self, pool):
        """10s per-attempt timeout on initialize() triggers retry."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_session.send_ping = AsyncMock()

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session), \
             patch("daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock) as mock_tools:
            mock_tools.return_value = [MagicMock()]

            with pytest.raises(asyncio.TimeoutError):
                await pool._create_pooled_connection("context7")

        assert mock_session.initialize.call_count == 3, "Should have retried 3 times on timeout"

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_immediately(self, pool):
        """CancelledError propagates immediately without retrying."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=asyncio.CancelledError("Task cancelled"))
        mock_session.send_ping = AsyncMock()

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session):
            with pytest.raises(asyncio.CancelledError):
                await pool._create_pooled_connection("context7")

        # Should only attempt once (no retries for CancelledError)
        assert mock_session.initialize.call_count == 1, "CancelledError should not trigger retries"
        mock_cm.__aexit__.assert_called_once(), "Cleanup should still be called"

    @pytest.mark.asyncio
    async def test_first_attempt_succeeds_no_backoff(self, pool):
        """First attempt success means no retry delays (only startup delay)."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(return_value=None)
        mock_session.send_ping = AsyncMock()

        sleep_durations: list[float] = []
        original_sleep = asyncio.sleep

        async def track_sleep(duration: float):
            sleep_durations.append(duration)
            await original_sleep(0)

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session), \
             patch("daemon.mcp.warmup_pool.load_mcp_tools", new_callable=AsyncMock) as mock_tools, \
             patch("asyncio.sleep", side_effect=track_sleep):
            mock_tools.return_value = [MagicMock()]
            conn = await pool._create_pooled_connection("context7")

        assert conn is not None
        assert mock_session.initialize.call_count == 1
        # Only startup delay (2.0s), no retry backoff
        assert sleep_durations == [2.0], f"Expected only startup delay [2.0], got {sleep_durations}"

    @pytest.mark.asyncio
    async def test_retry_log_levels(self, pool):
        """Verify WARNING on retry attempts, ERROR on final failure."""
        pool.register_server("context7", _make_config(), pool_size=1)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_cm.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.start = AsyncMock()
        mock_session.initialize = AsyncMock(side_effect=RuntimeError("Persistent"))
        mock_session.send_ping = AsyncMock()

        with patch("daemon.mcp.warmup_pool.mcp.stdio_client", return_value=mock_cm), \
             patch("daemon.mcp.warmup_pool.ManagedClientSession", return_value=mock_session), \
             patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            with pytest.raises(RuntimeError):
                await pool._create_pooled_connection("context7")

        # 2 WARNING calls (retries), 1 ERROR call (final failure)
        assert mock_logger.warning.call_count == 2, f"Expected 2 warnings, got {mock_logger.warning.call_count}"
        assert mock_logger.error.call_count == 1, f"Expected 1 error, got {mock_logger.error.call_count}"


class TestWarmup:
    """Tests for warmup method."""

    @pytest.mark.asyncio
    async def test_warmup_creates_correct_connections(self, registered_pool, mock_stdio_client, mock_load_mcp_tools):
        """warmup() should create N connections per registered server."""
        await registered_pool.warmup({"context7": 2})

        assert registered_pool._running is True
        assert registered_pool._pools["context7"].qsize() == 2

    @pytest.mark.asyncio
    async def test_warmup_parallel_multiple_servers(self, pool, mock_stdio_client, mock_load_mcp_tools):
        """Parallel warmup of multiple servers should work."""
        pool.register_server("server1", _make_config(), pool_size=1)
        pool.register_server("server2", _make_config(), pool_size=1)

        await pool.warmup()

        assert pool._pools["server1"].qsize() == 1
        assert pool._pools["server2"].qsize() == 1

    @pytest.mark.asyncio
    async def test_warmup_failure_doesnt_block_others(self, pool, mock_stdio_client, mock_load_mcp_tools):
        """One server failure shouldn't prevent others from warming up."""
        pool.register_server("good-server", _make_config(), pool_size=1)
        pool.register_server("bad-server", _make_config(), pool_size=1)

        # Make one stdio_client fail
        async def fail_on_second(*args, **kwargs):
            if mock_stdio_client.return_value.__aenter__.call_count > 1:
                raise RuntimeError("Connection failed")
            return await mock_stdio_client.return_value.__aenter__(*args, **kwargs)

        mock_stdio_client.return_value.__aenter__ = AsyncMock(side_effect=fail_on_second)

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            await pool.warmup()

            # Should have logged error for bad-server but not crashed
            assert any("bad-server" in str(c) for c in mock_logger.error.call_args_list)

    @pytest.mark.asyncio
    async def test_warmup_double_call_guard(self, registered_pool, mock_stdio_client, mock_load_mcp_tools):
        """Calling warmup() twice should not duplicate connections."""
        await registered_pool.warmup()

        first_qsize = registered_pool._pools["context7"].qsize()

        # Second warmup should be blocked
        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            await registered_pool.warmup()
            mock_logger.warning.assert_called()

        # Queue should not have more connections
        assert registered_pool._pools["context7"].qsize() == first_qsize


class TestAcquire:
    """Tests for acquire method."""

    @pytest.mark.asyncio
    async def test_acquire_returns_connection_when_available(self, registered_pool):
        """acquire should return PooledConnection when available."""
        registered_pool._running = True  # Pool must be running
        conn = _make_pooled_connection("context7")
        await registered_pool._pools["context7"].put(conn)

        result = await registered_pool.acquire("context7")

        assert result is conn

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_empty(self, registered_pool):
        """acquire should return None when pool is empty."""
        result = await registered_pool.acquire("context7")

        assert result is None

    @pytest.mark.asyncio
    async def test_acquire_returns_none_when_not_running(self, pool):
        """acquire should return None when pool is not running (drained)."""
        pool.register_server("context7", _make_config())
        pool._running = False  # Simulate drained state

        result = await pool.acquire("context7")

        assert result is None

    @pytest.mark.asyncio
    async def test_acquire_triggers_replenish(self, registered_pool, mock_stdio_client, mock_load_mcp_tools):
        """Successful acquire should trigger background replenishment."""
        registered_pool._running = True  # Pool must be running for replenish

        # Create a patched version that tracks when tasks are added
        original_start = registered_pool._start_tracked_replenish

        added_task_server: dict = {}

        def patched_start(server_name: str) -> None:
            added_task_server["server"] = server_name
            return original_start(server_name)

        registered_pool._start_tracked_replenish = patched_start

        try:
            conn = _make_pooled_connection("context7")
            await registered_pool._pools["context7"].put(conn)

            await registered_pool.acquire("context7")

            # Verify replenish was started for context7
            assert added_task_server.get("server") == "context7", "Replenish should have been triggered for context7"
        finally:
            registered_pool._start_tracked_replenish = original_start


class TestReplenish:
    """Tests for _replenish method."""

    @pytest.mark.asyncio
    async def test_replenish_creates_new_connection(self, registered_pool, mock_stdio_client, mock_load_mcp_tools):
        """_replenish should create a new connection after acquire."""
        registered_pool._running = True  # Pool must be running
        conn = _make_pooled_connection("context7")
        await registered_pool._pools["context7"].put(conn)

        # Manually call replenish
        await registered_pool._replenish("context7")

        # Pool should have 2 connections now
        assert registered_pool._pools["context7"].qsize() == 2

    @pytest.mark.asyncio
    async def test_replenish_semaphore_caps_concurrent(self, pool, mock_stdio_client, mock_load_mcp_tools):
        """Semaphore should limit concurrent replenishment."""
        pool.register_server("server1", _make_config(), pool_size=2)  # Size 2 so replenish doesn't skip
        pool.register_server("server2", _make_config(), pool_size=2)
        pool._running = True  # Must be running for replenish to execute

        async def slow_create(*args, **kwargs):
            await asyncio.sleep(0.5)
            return _make_pooled_connection()

        # Start two replenish tasks simultaneously
        with patch.object(pool, "_create_pooled_connection", side_effect=slow_create):
            # Put initial connections (pool_size=2, so replenish is needed)
            await pool._pools["server1"].put(_make_pooled_connection("server1"))
            await pool._pools["server2"].put(_make_pooled_connection("server2"))

            # Start replenish tasks
            t1 = asyncio.create_task(pool._replenish("server1"))
            t2 = asyncio.create_task(pool._replenish("server2"))

            await asyncio.sleep(0.1)

            # Semaphore value should have decreased from initial value of 2
            # (acquire decrements it; if one task is holding it, value should be 1)
            assert pool._replenish_semaphore._value < 2, "Semaphore should have been acquired (value < 2)"

            # Clean up
            t1.cancel()
            t2.cancel()
            try:
                await asyncio.gather(t1, t2, return_exceptions=True)
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_replenish_failure_logged_not_raised(self, pool, mock_stdio_client, mock_load_mcp_tools):
        """Replenish failure should be logged but not raised."""
        pool.register_server("context7", _make_config(), pool_size=2)
        pool._running = True

        async def fail_create(*args, **kwargs):
            raise RuntimeError("Connection failed")

        with patch.object(pool, "_create_pooled_connection", side_effect=fail_create):
            with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
                # Should not raise
                await pool._replenish("context7")

                # Should log warning
                mock_logger.warning.assert_called()


class TestDrain:
    """Tests for drain method."""

    @pytest.mark.asyncio
    async def test_drain_closes_all_sessions(self, registered_pool, mock_stdio_client, mock_load_mcp_tools):
        """drain should close all sessions and stream context managers."""
        conn1 = _make_pooled_connection("context7")
        conn2 = _make_pooled_connection("context7")
        await registered_pool._pools["context7"].put(conn1)
        await registered_pool._pools["context7"].put(conn2)

        await registered_pool.drain()

        conn1.session.stop.assert_awaited_once()
        conn2.session.stop.assert_awaited_once()
        conn1.stream_cm.__aexit__.assert_awaited_once()
        conn2.stream_cm.__aexit__.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_drain_cancels_health_task(self, pool):
        """drain should cancel the health check task."""
        pool._running = True
        pool._health_task = asyncio.create_task(asyncio.sleep(100))

        await pool.drain()

        assert pool._health_task.done()

    @pytest.mark.asyncio
    async def test_drain_cancels_replenish_tasks(self, pool):
        """drain should cancel ALL tracked replenish tasks."""
        pool._running = True

        async def slow_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise

        # Create multiple replenish tasks
        t1 = asyncio.create_task(slow_task())
        t2 = asyncio.create_task(slow_task())
        pool._replenish_tasks.add(t1)
        pool._replenish_tasks.add(t2)

        await pool.drain()

        # Tasks should be cancelled
        assert t1.done()
        assert t2.done()
        assert len(pool._replenish_tasks) == 0

    @pytest.mark.asyncio
    async def test_drain_sets_running_false(self, registered_pool):
        """drain should set _running to False."""
        registered_pool._running = True

        await registered_pool.drain()

        assert registered_pool._running is False


class TestHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_removes_dead_connections(self, registered_pool):
        """Dead connections should be removed, healthy ones restored."""
        healthy_conn = _make_pooled_connection("context7")
        healthy_conn.session.send_ping = AsyncMock()  # Successful ping

        dead_conn = _make_pooled_connection("context7")
        dead_conn.session.send_ping = AsyncMock(side_effect=RuntimeError("Dead"))

        await registered_pool._pools["context7"].put(healthy_conn)
        await registered_pool._pools["context7"].put(dead_conn)

        await registered_pool.health_check()

        # Only healthy connection should remain
        assert registered_pool._pools["context7"].qsize() == 1
        # Dead connection should have been closed
        dead_conn.session.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_health_check_acquires_lock(self, registered_pool):
        """health_check should hold per-server lock during check, blocking other acquires."""
        lock = registered_pool._locks["context7"]

        # Verify that acquiring the lock prevents health_check from running concurrently
        # by checking that health_check waits for the lock

        # Create a wrapper lock that tracks acquisition
        lock_acquired = asyncio.Event()
        proceed = asyncio.Event()

        class LockTracker:
            def __init__(self, inner_lock):
                self._inner = inner_lock
                self._task = None

            async def __aenter__(self):
                await self._inner.acquire()
                lock_acquired.set()
                self._task = asyncio.current_task()
                await proceed.wait()
                return self

            async def __aexit__(self, *args):
                self._inner.release()

            def release(self):
                self._inner.release()

            @property
            def locked(self):
                return self._inner.locked()

            async def acquire(self):
                return await self._inner.acquire()

        # Replace lock with tracker
        original_lock = registered_pool._locks["context7"]
        tracker = LockTracker(original_lock)
        registered_pool._locks["context7"] = tracker

        try:
            # Start health_check in background - it should acquire the lock
            health_task = asyncio.create_task(registered_pool.health_check())

            # Wait for lock to be acquired
            await asyncio.wait_for(lock_acquired.wait(), timeout=1.0)

            # Now try to acquire the same lock from another task - should block
            direct_acquire_task = asyncio.create_task(tracker.acquire())

            # Give it time to attempt acquisition
            await asyncio.sleep(0.05)

            # The direct acquire should not have completed yet (lock is held)
            assert not direct_acquire_task.done(), "Lock acquire should block while health_check holds it"

            # Clean up
            proceed.set()
            direct_acquire_task.cancel()
            health_task.cancel()
            try:
                await direct_acquire_task
            except asyncio.CancelledError:
                pass
            try:
                await health_task
            except asyncio.CancelledError:
                pass
        finally:
            registered_pool._locks["context7"] = original_lock

    @pytest.mark.asyncio
    async def test_health_check_triggers_replenish_for_evicted(self, registered_pool):
        """Replenishment should be triggered for evicted connections."""
        # Add one healthy connection, one that will die
        healthy_conn = _make_pooled_connection("context7")
        healthy_conn.session.send_ping = AsyncMock()

        dead_conn = _make_pooled_connection("context7")
        dead_conn.session.send_ping = AsyncMock(side_effect=RuntimeError("Dead"))

        await registered_pool._pools["context7"].put(healthy_conn)
        await registered_pool._pools["context7"].put(dead_conn)

        with patch.object(registered_pool, "_start_tracked_replenish") as mock_replenish:
            await registered_pool.health_check()

            # Replenish should be triggered (1 healthy < target pool_size of 2)
            mock_replenish.assert_called_with("context7")


class TestWarmupServerExceptionLogging:
    """Tests for _warmup_server exception logging (Fix 1)."""

    @pytest.mark.asyncio
    async def test_exc_info_is_proper_3tuple(self, pool):
        """When exception occurs in _warmup_server, exc_info should be proper 3-tuple."""
        pool.register_server("context7", _make_config(), pool_size=1)

        # Mock _create_pooled_connection to raise an exception
        async def raise_error(*args, **kwargs):
            raise RuntimeError("Connection failed")

        pool._create_pooled_connection = raise_error

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            await pool._warmup_server("context7", 1)

            # Find the error call
            assert mock_logger.error.called, "Expected error to be logged"
            call_kwargs = mock_logger.error.call_args.kwargs
            exc_info = call_kwargs.get("exc_info")

            # Verify exc_info is a proper 3-tuple (type, value, traceback)
            assert isinstance(exc_info, tuple), f"exc_info should be tuple, got {type(exc_info)}"
            assert len(exc_info) == 3, f"exc_info should have 3 elements, got {len(exc_info)}"

            exc_type, exc_value, exc_tb = exc_info
            assert exc_type is RuntimeError, f"First element should be type, got {exc_type}"
            assert exc_value is not None, "Second element (value) should not be None"
            assert str(exc_value) == "Connection failed", f"Value message mismatch: {exc_value}"
            assert exc_tb is not None, "Third element (traceback) should not be None"
            assert hasattr(exc_tb, "tb_frame"), "Third element should be a traceback object"


class TestWarmupServerExceptionHandling:
    """Tests for _warmup_server exception type handling (Fix 2)."""

    @pytest.mark.asyncio
    async def test_cancelled_error_is_caught_and_logged(self, pool):
        """CancelledError should be caught and logged, not propagated."""
        pool.register_server("context7", _make_config(), pool_size=1)

        async def raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError("Task cancelled")

        pool._create_pooled_connection = raise_cancelled

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            # Should not raise
            await pool._warmup_server("context7", 1)

            # Should log error for the CancelledError
            mock_logger.error.assert_called()

            # Verify it was caught as CancelledError
            call_kwargs = mock_logger.error.call_args.kwargs
            exc_info = call_kwargs.get("exc_info")
            assert exc_info is not None
            assert exc_info[0] is asyncio.CancelledError

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_propagates(self, pool):
        """KeyboardInterrupt should propagate (not caught by isinstance)."""
        pool.register_server("context7", _make_config(), pool_size=1)

        # Create a mock exception that's like KeyboardInterrupt (inherits from BaseException, not Exception)
        class MockKeyboardInterrupt(BaseException):
            """Mock that simulates KeyboardInterrupt behavior without triggering pytest's Ctrl+C."""
            pass

        async def raise_mock_interrupt(*args, **kwargs):
            raise MockKeyboardInterrupt("Simulated interrupt")

        pool._create_pooled_connection = raise_mock_interrupt

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            # Should raise, not caught (because MockKeyboardInterrupt inherits from BaseException, not Exception)
            with pytest.raises(MockKeyboardInterrupt, match="Simulated interrupt"):
                await pool._warmup_server("context7", 1)

            # Error should NOT be logged since it's not caught by isinstance check

    @pytest.mark.asyncio
    async def test_system_exit_propagates(self, pool):
        """SystemExit should propagate (not caught by isinstance)."""
        pool.register_server("context7", _make_config(), pool_size=1)

        # Create a mock exception that's like SystemExit (inherits from BaseException, not Exception)
        class MockSystemExit(BaseException):
            """Mock that simulates SystemExit behavior."""
            pass

        async def raise_mock_exit(*args, **kwargs):
            raise MockSystemExit("Exiting")

        pool._create_pooled_connection = raise_mock_exit

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            # Should raise, not caught (because MockSystemExit inherits from BaseException, not Exception)
            with pytest.raises(MockSystemExit, match="Exiting"):
                await pool._warmup_server("context7", 1)

    @pytest.mark.asyncio
    async def test_regular_exception_is_caught(self, pool):
        """Regular Exception subclasses should be caught and logged."""
        pool.register_server("context7", _make_config(), pool_size=1)

        async def raise_value_error(*args, **kwargs):
            raise ValueError("Invalid config")

        pool._create_pooled_connection = raise_value_error

        with patch("daemon.mcp.warmup_pool.logger") as mock_logger:
            # Should not raise
            await pool._warmup_server("context7", 1)

            # Should log error
            mock_logger.error.assert_called()

            # Verify it was caught as ValueError
            call_kwargs = mock_logger.error.call_args.kwargs
            exc_info = call_kwargs.get("exc_info")
            assert exc_info is not None
            assert exc_info[0] is ValueError


class TestGetStatusHealthy:
    """Tests for get_status healthy field (Fix 3)."""

    @pytest.mark.asyncio
    async def test_healthy_false_when_pool_empty(self, pool):
        """healthy should be False when pool is empty (qsize == 0)."""
        pool.register_server("context7", _make_config(), pool_size=2)

        status = pool.get_status()

        assert "context7" in status
        assert status["context7"]["available"] == 0
        assert status["context7"]["healthy"] is False, "healthy should be False when pool is empty"

    @pytest.mark.asyncio
    async def test_healthy_true_when_pool_has_connections(self, pool):
        """healthy should be True when pool has at least one connection (qsize > 0)."""
        pool.register_server("context7", _make_config(), pool_size=2)
        conn = _make_pooled_connection("context7")
        await pool._pools["context7"].put(conn)

        status = pool.get_status()

        assert "context7" in status
        assert status["context7"]["available"] == 1
        assert status["context7"]["healthy"] is True, "healthy should be True when pool has connections"

    @pytest.mark.asyncio
    async def test_healthy_true_when_pool_full(self, pool):
        """healthy should be True when pool is at capacity."""
        pool.register_server("context7", _make_config(), pool_size=2)
        await pool._pools["context7"].put(_make_pooled_connection("context7"))
        await pool._pools["context7"].put(_make_pooled_connection("context7"))

        status = pool.get_status()

        assert status["context7"]["available"] == 2
        assert status["context7"]["healthy"] is True


class TestGetStatus:
    """Tests for get_status method."""

    @pytest.mark.asyncio
    async def test_get_status(self, registered_pool):
        """get_status should return correct status dict."""
        conn = _make_pooled_connection("context7")
        await registered_pool._pools["context7"].put(conn)

        status = registered_pool.get_status()

        assert "context7" in status
        assert status["context7"]["available"] == 1
        assert status["context7"]["pool_size"] == 2
        assert status["context7"]["healthy"] is True  # Has connection, so healthy

    @pytest.mark.asyncio
    async def test_get_status_multiple_servers(self, pool):
        """get_status should return status for all registered servers."""
        pool.register_server("server1", _make_config(), pool_size=1)
        pool.register_server("server2", _make_config(), pool_size=2)

        # server1 has 1 connection
        await pool._pools["server1"].put(_make_pooled_connection("server1"))
        # server2 is empty

        status = pool.get_status()

        assert "server1" in status
        assert "server2" in status
        assert status["server1"]["available"] == 1
        assert status["server1"]["healthy"] is True
        assert status["server2"]["available"] == 0
        assert status["server2"]["healthy"] is False
