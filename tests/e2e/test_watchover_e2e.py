"""End-to-end tests for the Watchover feature.

These tests exercise the watchover API + watcher agent against a running
daemon (started via ``dev_with_mock.sh``) and the mock LLM server
(``tests/mock_llm_server.py``). Each test sets the mock server scenario,
then verifies the watchover lifecycle:

  1. ``allow``              — watcher returns "Allowed", instance continues.
  2. ``deny_then_correct``  — watcher denies once, then allows on retry.
  3. ``three_strikes``      — watcher denies 3 times, instance is terminated.
  4. ``builder_quality``    — context builder LLM call populates
                              ``watchover_context`` with the canonical
                              ``## Allowed`` / ``## Forbidden`` markdown.
  5. ``infra_error``        — watcher returns 500 on first call; fail-open
                              path keeps the instance active.

Run with::

    # Start daemon + mock server first
    ./dev_with_mock.sh

    # Run the tests
    RUN_E2E_TESTS=1 pytest tests/e2e/test_watchover_e2e.py -v -m integration

The tests are skipped unless ``RUN_E2E_TESTS=1`` is set, so they don't run
in CI without explicit opt-in.
"""

import asyncio
import logging
import os
import time

import httpx
import pytest
import pytest_asyncio


# --------------------------------------------------------------------------- #
# Logging configuration (mirrors the other e2e files)
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
DAEMON_URL = os.environ.get("DAEMON_URL", "http://localhost:8079")
MOCK_URL = os.environ.get("MOCK_LLM_URL", "http://localhost:4124")

# Generous timeouts (real LLM calls are involved through the mock LLM).
DEFAULT_TIMEOUT = 30.0
POLL_INTERVAL = 0.5
WATCHER_TIMEOUT = 15.0       # watcher / builder call budget
TERMINAL_TIMEOUT = 20.0      # three-strikes termination budget

# --------------------------------------------------------------------------- #
# Pytest collection gate — identical to the other e2e files
# --------------------------------------------------------------------------- #
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_E2E_TESTS") != "1",
        reason="E2E tests require RUN_E2E_TESTS=1; start via dev_with_mock.sh",
    ),
]

# Terminal InstanceStatus values (per daemon/repositories/instance/models.py).
# The watchover three-strikes outcome is ``terminated`` (watchover_terminated),
# but we accept any terminal state for resilience.
_TERMINAL_STATUSES = frozenset({"completed", "error", "terminated", "failed"})


# --------------------------------------------------------------------------- #
# Helpers — module-level async functions used by all tests
#
# Note: the spec sketched these without a client argument; we pass the
# ``httpx.AsyncClient`` explicitly as the first arg (idiomatic for pytest
# async tests) so the helpers stay testable without module-global state.
# --------------------------------------------------------------------------- #
async def set_scenario(client: httpx.AsyncClient, scenario: str) -> dict:
    """Switch the mock server to a given watchover scenario.

    Returns ``{"scenario": ..., "previous": ...}``.
    """
    resp = await client.post(f"{MOCK_URL}/scenario", json={"scenario": scenario})
    resp.raise_for_status()
    return resp.json()


async def get_stats(client: httpx.AsyncClient) -> dict:
    """Fetch the mock server's watchover stats."""
    resp = await client.get(f"{MOCK_URL}/stats")
    resp.raise_for_status()
    return resp.json()


async def create_instance(
    client: httpx.AsyncClient,
    agent_id: str = "devops",
    message: str | None = None,
) -> dict:
    """Spawn a new instance via the daemon API.

    Returns the ``InstanceInfo`` JSON payload (contains ``instance_id``).
    The ``message`` parameter is accepted for spec compatibility but the
    daemon's ``InstanceCreate`` schema does not consume it; we omit it from
    the body when ``None`` to avoid polluting the request.
    """
    body: dict = {"agent_id": agent_id}
    if message is not None:
        body["message"] = message
    resp = await client.post(f"{DAEMON_URL}/api/instances", json=body)
    resp.raise_for_status()
    return resp.json()


async def enable_watchover(
    client: httpx.AsyncClient,
    instance_id: str,
    requirement: str | None = None,
) -> dict:
    """Activate watchover on the target instance.

    Returns ``{"watchover_enabled": bool, "instance_id": str}``.
    """
    body: dict = {"enabled": True}
    if requirement is not None:
        body["requirement"] = requirement
    resp = await client.post(
        f"{DAEMON_URL}/api/instances/{instance_id}/watchover",
        json=body,
    )
    resp.raise_for_status()
    return resp.json()


async def get_instance(client: httpx.AsyncClient, instance_id: str) -> dict:
    """Fetch instance info via the daemon API."""
    resp = await client.get(f"{DAEMON_URL}/api/instances/{instance_id}")
    resp.raise_for_status()
    return resp.json()


async def send_message(
    client: httpx.AsyncClient,
    instance_id: str,
    content: str,
) -> dict:
    """Send a user message to the instance."""
    resp = await client.post(
        f"{DAEMON_URL}/api/instances/{instance_id}/messages",
        json={"content": content, "role": "user"},
    )
    resp.raise_for_status()
    return resp.json()


async def wait_for_condition(
    predicate,
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = POLL_INTERVAL,
) -> bool:
    """Poll an async predicate until it returns True or the timeout elapses.

    Args:
        predicate: An async callable taking no args and returning ``bool``.
        timeout: Maximum seconds to wait (default 30).
        interval: Seconds between polls (default 0.5).

    Returns:
        The final predicate value. ``True`` if the condition was met,
        ``False`` if the timeout elapsed.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            if await predicate():
                return True
        except Exception as exc:
            logger.debug("wait_for_condition: predicate raised %s", exc)
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)


async def _terminate_instance(client: httpx.AsyncClient, instance_id: str) -> None:
    """Best-effort DELETE for cleanup; never raises."""
    try:
        await client.delete(f"{DAEMON_URL}/api/instances/{instance_id}")
    except Exception as exc:
        logger.warning(
            "cleanup: terminate_instance(%s) failed: %s",
            instance_id[:8],
            exc,
        )


# --------------------------------------------------------------------------- #
# Health-check fixture — yield an httpx client only when both endpoints are up
# --------------------------------------------------------------------------- #
@pytest_asyncio.fixture
async def daemon_and_mock():
    """Provide an httpx client; skip test if daemon or mock server is unreachable."""
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        try:
            resp = await client.get(f"{DAEMON_URL}/api/health")
            resp.raise_for_status()
            logger.info("Daemon health OK at %s", DAEMON_URL)
        except Exception as e:
            pytest.skip(f"Daemon not reachable at {DAEMON_URL}: {e}")
        try:
            resp = await client.get(f"{MOCK_URL}/health")
            resp.raise_for_status()
            logger.info("Mock server health OK at %s", MOCK_URL)
        except Exception as e:
            pytest.skip(f"Mock LLM server not reachable at {MOCK_URL}: {e}")
        yield client


# --------------------------------------------------------------------------- #
# Test 1: allow_safe_command
# --------------------------------------------------------------------------- #
async def test_e2e_allow_safe_command(daemon_and_mock):
    """Watchover evaluates a safe command as Allowed; instance stays active."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info("[test_e2e_allow_safe_command] created instance=%s", instance_id[:8])

    try:
        activation = await enable_watchover(
            client,
            instance_id,
            requirement="read-only cluster inspection",
        )
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        await send_message(client, instance_id, "check cluster status")

        # Wait for the watcher to evaluate the agent's tool call.
        async def _watcher_called() -> bool:
            stats = await get_stats(client)
            return stats.get("watcher_call_count", 0) >= 1

        ok = await wait_for_condition(_watcher_called, timeout=WATCHER_TIMEOUT)
        stats = await get_stats(client)
        assert ok, (
            f"watcher never called within {WATCHER_TIMEOUT}s; "
            f"current stats={stats}"
        )
        assert stats["watcher_call_count"] >= 1, (
            f"expected watcher_call_count >= 1, got {stats['watcher_call_count']}"
        )

        # The instance should still be active (not terminated by watchover).
        info = await get_instance(client, instance_id)
        assert info.get("status") not in _TERMINAL_STATUSES, (
            f"instance reached terminal status {info.get('status')!r} under "
            f"the 'allow' scenario; expected to stay active. Full info: {info}"
        )
        assert info.get("watchover_enabled") is True, (
            f"watchover_enabled should be True after enable; "
            f"got {info.get('watchover_enabled')!r}"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 2: deny_then_correct
# --------------------------------------------------------------------------- #
async def test_e2e_deny_then_correct(daemon_and_mock):
    """Watcher denies once, then allows after the agent's correction."""
    client = daemon_and_mock
    await set_scenario(client, "deny_then_correct")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info("[test_e2e_deny_then_correct] created instance=%s", instance_id[:8])

    try:
        activation = await enable_watchover(client, instance_id)
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        await send_message(client, instance_id, "clean up old pods")

        # Wait for two watcher calls (first denies, second allows correction).
        async def _watcher_two_calls() -> bool:
            stats = await get_stats(client)
            return stats.get("watcher_call_count", 0) >= 2

        ok = await wait_for_condition(_watcher_two_calls, timeout=WATCHER_TIMEOUT)
        stats = await get_stats(client)
        assert ok, (
            f"watcher did not reach >= 2 calls within {WATCHER_TIMEOUT}s; "
            f"current stats={stats}"
        )
        assert stats["watcher_call_count"] >= 2, (
            f"expected watcher_call_count >= 2, got {stats['watcher_call_count']}"
        )

        # The instance should still be active (denial threshold not reached).
        info = await get_instance(client, instance_id)
        assert info.get("status") not in _TERMINAL_STATUSES, (
            f"instance reached terminal status {info.get('status')!r}; "
            f"expected to stay active after a single deny. Full info: {info}"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 3: three_strikes_termination
# --------------------------------------------------------------------------- #
async def test_e2e_three_strikes_termination(daemon_and_mock):
    """Three denials in a row terminate the instance via watchover."""
    client = daemon_and_mock
    await set_scenario(client, "three_strikes")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_three_strikes_termination] created instance=%s",
        instance_id[:8],
    )

    try:
        activation = await enable_watchover(client, instance_id)
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        await send_message(client, instance_id, "delete everything")

        # Wait for the instance to reach a terminal status (watchover_terminated).
        async def _instance_terminal() -> bool:
            info = await get_instance(client, instance_id)
            return info.get("status") in _TERMINAL_STATUSES

        ok = await wait_for_condition(_instance_terminal, timeout=TERMINAL_TIMEOUT)
        info = await get_instance(client, instance_id)
        assert ok, (
            f"instance did not reach terminal status within {TERMINAL_TIMEOUT}s; "
            f"current status={info.get('status')!r}, full info: {info}"
        )
        assert info.get("status") in _TERMINAL_STATUSES, (
            f"expected terminal status, got {info.get('status')!r}"
        )

        # Watcher should have been called at least 3 times (3 denials).
        stats = await get_stats(client)
        assert stats["watcher_call_count"] >= 3, (
            f"expected watcher_call_count >= 3, got {stats['watcher_call_count']}"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 4: context_builder_quality
# --------------------------------------------------------------------------- #
async def test_e2e_context_builder_quality(daemon_and_mock):
    """Watcher context builder returns markdown with ## Allowed and ## Forbidden."""
    client = daemon_and_mock
    await set_scenario(client, "builder_quality")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_context_builder_quality] created instance=%s",
        instance_id[:8],
    )

    try:
        # enable_watchover triggers the builder LLM call.
        activation = await enable_watchover(
            client,
            instance_id,
            requirement="read-only inspection only",
        )
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        # Wait for the builder to be called.
        async def _builder_called() -> bool:
            stats = await get_stats(client)
            return stats.get("builder_call_count", 0) >= 1

        ok = await wait_for_condition(_builder_called, timeout=WATCHER_TIMEOUT)
        stats = await get_stats(client)
        assert ok, (
            f"builder was not called within {WATCHER_TIMEOUT}s; "
            f"current stats={stats}"
        )
        assert stats["builder_call_count"] >= 1, (
            f"expected builder_call_count >= 1, got {stats['builder_call_count']}"
        )

        # The builder's output should be persisted as watchover_context.
        info = await get_instance(client, instance_id)
        context = info.get("watchover_context") or ""
        assert context, (
            f"watchover_context is empty after enable; expected builder output. "
            f"Full info: {info}"
        )
        assert "## Allowed" in context, (
            f"watchover_context missing '## Allowed' section. "
            f"Got: {context[:200]!r}"
        )
        assert "## Forbidden" in context, (
            f"watchover_context missing '## Forbidden' section. "
            f"Got: {context[:200]!r}"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 5: infra_error_fail_open
# --------------------------------------------------------------------------- #
async def test_e2e_infra_error_fail_open(daemon_and_mock):
    """Watcher serves 500 on first call; fail-open path keeps the instance active."""
    client = daemon_and_mock
    await set_scenario(client, "infra_error")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_infra_error_fail_open] created instance=%s",
        instance_id[:8],
    )

    try:
        activation = await enable_watchover(client, instance_id)
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        await send_message(client, instance_id, "check status")

        # Wait for the first watcher call (which fails with 500 from the mock).
        async def _first_watcher_call() -> bool:
            stats = await get_stats(client)
            return stats.get("watcher_call_count", 0) >= 1

        ok = await wait_for_condition(_first_watcher_call, timeout=WATCHER_TIMEOUT)
        stats = await get_stats(client)
        assert ok, (
            f"watcher was never called within {WATCHER_TIMEOUT}s; "
            f"current stats={stats}"
        )
        assert stats["watcher_call_count"] >= 1, (
            f"expected watcher_call_count >= 1, got {stats['watcher_call_count']}"
        )

        # The instance must stay active (fail-open prevents termination).
        info = await get_instance(client, instance_id)
        assert info.get("status") not in _TERMINAL_STATUSES, (
            f"instance reached terminal status {info.get('status')!r} under "
            f"the 'infra_error' scenario; fail-open should keep it active. "
            f"Full info: {info}"
        )
    finally:
        await _terminate_instance(client, instance_id)
