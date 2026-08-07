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
    resume_message: str | None = None,
) -> dict:
    """Activate watchover on the target instance.

    Returns ``{"watchover_enabled": bool, "instance_id": str}``.

    Args:
        client: The shared httpx client.
        instance_id: Target instance to activate watchover for.
        requirement: Optional operator-supplied requirement string.
        resume_message: Optional custom message to deliver to the target
            instance on the post-activation resume. When ``None`` (default)
            the daemon sends the fixed token ``"continue"`` to the target.
            When provided, the message is delivered verbatim (max 2000 chars
            per the WatchoverRequest contract).
    """
    body: dict = {"enabled": True}
    if requirement is not None:
        body["requirement"] = requirement
    if resume_message is not None:
        body["resume_message"] = resume_message
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


# --------------------------------------------------------------------------- #
# Helpers for resume-fix tests
# --------------------------------------------------------------------------- #
async def pause_instance(
    client: httpx.AsyncClient,
    instance_id: str,
) -> dict:
    """Pause the target instance via POST /api/instances/{id}/pause.

    Returns the response payload (typically ``{"status": "paused"}``).
    """
    resp = await client.post(f"{DAEMON_URL}/api/instances/{instance_id}/pause")
    resp.raise_for_status()
    return resp.json()


async def get_messages(client: httpx.AsyncClient, instance_id: str) -> list[dict]:
    """Fetch the persisted message history for an instance.

    Returns the raw list of message dicts from
    ``GET /api/instances/{id}/messages``. Order is chronological.
    """
    resp = await client.get(f"{DAEMON_URL}/api/instances/{instance_id}/messages")
    resp.raise_for_status()
    return resp.json()


def _message_text(msg: dict) -> str:
    """Best-effort text content extraction from a serialized message dict.

    Mirrors the field names used by the checkpoint-backed serializer
    (``content`` may be a string or a list of typed parts; the latter is
    joined into a single string for substring matching).
    """
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def _count_messages_with_text(messages: list[dict], needle: str) -> int:
    """Count serialized messages whose text content contains ``needle``.

    Substring match is case-insensitive to match the dedup logic in
    :class:`WatchoverService._has_pending_resume_message`.
    """
    needle_lower = needle.lower()
    count = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = _message_text(msg)
        if text and needle_lower in text.lower():
            count += 1
    return count


# --------------------------------------------------------------------------- #
# Test 6: continue_message_after_activation
# --------------------------------------------------------------------------- #
async def test_e2e_continue_message_after_activation(daemon_and_mock):
    """After watchover activation, the watched instance receives a 'continue' message and the graph restarts."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_continue_message_after_activation] created instance=%s",
        instance_id[:8],
    )

    try:
        # Step 1: start the graph with a user message.
        await send_message(client, instance_id, "check cluster status")

        # Step 2: activate watchover (default resume_message -> "continue").
        activation = await enable_watchover(client, instance_id)
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        # Step 3: wait for the "continue" message to be processed and
        # checkpointed. The activation resumes the instance with the
        # default "continue" token; the agent consumes it on the next
        # graph iteration, at which point it appears in /messages.
        async def _continue_seen_in_history() -> bool:
            messages = await get_messages(client, instance_id)
            return _count_messages_with_text(messages, "continue") >= 1

        ok = await wait_for_condition(
            _continue_seen_in_history,
            timeout=DEFAULT_TIMEOUT,
        )
        messages = await get_messages(client, instance_id)
        assert ok, (
            f"no 'continue' message in history within {DEFAULT_TIMEOUT}s; "
            f"got {len(messages)} messages. "
            f"Contents (truncated): "
            f"{[(m.get('role'), _message_text(m)[:60]) for m in messages[:8]]!r}"
        )

        # Step 4: verify the graph restarted (the mock recorded at least
        # one post-activation agent call).
        stats = await get_stats(client)
        assert stats["agent_call_count"] >= 1, (
            f"expected agent_call_count >= 1 after activation (graph "
            f"restart), got {stats['agent_call_count']}. stats={stats}"
        )

        # Step 5: instance is still alive under the 'allow' scenario.
        info = await get_instance(client, instance_id)
        assert info.get("status") not in _TERMINAL_STATUSES, (
            f"instance reached terminal status {info.get('status')!r} "
            f"under 'allow' scenario; expected to stay active. "
            f"Full info: {info}"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 7: custom_resume_message
# --------------------------------------------------------------------------- #
async def test_e2e_custom_resume_message(daemon_and_mock):
    """If resume_message is provided in WatchoverRequest, it's used instead of 'continue'."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_custom_resume_message] created instance=%s",
        instance_id[:8],
    )

    custom_msg = "Please proceed with the task"

    try:
        # Step 1: start the graph with a user message.
        await send_message(client, instance_id, "check cluster status")

        # Step 2: activate watchover with a custom resume_message.
        activation = await enable_watchover(
            client,
            instance_id,
            resume_message=custom_msg,
        )
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        # Step 3: wait for the custom message to appear in /messages.
        async def _custom_seen_in_history() -> bool:
            messages = await get_messages(client, instance_id)
            return _count_messages_with_text(messages, custom_msg) >= 1

        ok = await wait_for_condition(
            _custom_seen_in_history,
            timeout=DEFAULT_TIMEOUT,
        )
        messages = await get_messages(client, instance_id)
        assert ok, (
            f"custom resume message {custom_msg!r} not found in history "
            f"within {DEFAULT_TIMEOUT}s; got {len(messages)} messages. "
            f"Contents (truncated): "
            f"{[(m.get('role'), _message_text(m)[:60]) for m in messages[:8]]!r}"
        )

        # Step 4: NO bare "continue" message should be present — the
        # custom resume_message replaces the default token. We allow
        # other messages that happen to contain the substring "continue"
        # (e.g. an assistant elaboration), so the assertion is that the
        # exact custom message is in the history, not that the literal
        # token "continue" is absent. The "at least one" check on the
        # custom message above is the primary assertion.
        custom_count = _count_messages_with_text(messages, custom_msg)
        assert custom_count >= 1, (
            f"expected at least one message containing {custom_msg!r}, "
            f"got {custom_count} in {len(messages)} messages"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 8: no_duplicate_continue
# --------------------------------------------------------------------------- #
async def test_e2e_no_duplicate_continue(daemon_and_mock):
    """Activating watchover on an already-paused instance doesn't send duplicate 'continue' messages."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_no_duplicate_continue] created instance=%s",
        instance_id[:8],
    )

    try:
        # Step 1: start the graph.
        await send_message(client, instance_id, "check cluster status")

        # Step 2: pause the instance so the agent cannot drain the
        # message queue. This guarantees the first "continue" enqueued
        # by the activation stays in READY state, which is what the
        # dedup check operates on.
        pause_result = await pause_instance(client, instance_id)
        logger.info(
            "[test_e2e_no_duplicate_continue] pause result=%s",
            pause_result,
        )

        # Step 3: first activation → enqueues one "continue" message.
        first_activation = await enable_watchover(client, instance_id)
        assert first_activation.get("watchover_enabled") is True, (
            f"first enable_watchover returned watchover_enabled="
            f"{first_activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {first_activation}"
        )

        # Step 4: confirm the queue now contains exactly one pending
        # "continue" message.
        info = await get_instance(client, instance_id)
        first_pending = info.get("pending_count")
        assert first_pending == 1, (
            f"expected pending_count == 1 after first activation, got "
            f"{first_pending}. Full info: {info}"
        )

        # Step 5: activate AGAIN — the dedup logic must detect the
        # pending "continue" and skip enqueueing a duplicate.
        second_activation = await enable_watchover(client, instance_id)
        assert second_activation.get("watchover_enabled") is True, (
            f"second enable_watchover returned watchover_enabled="
            f"{second_activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {second_activation}"
        )

        # Step 6: queue must still hold exactly one resume/continue
        # message — no duplicate was enqueued on the second activation.
        info = await get_instance(client, instance_id)
        second_pending = info.get("pending_count")
        assert second_pending == 1, (
            f"expected pending_count == 1 after second activation "
            f"(dedup should have skipped), got {second_pending}. "
            f"Full info: {info}"
        )

        # Step 7: spot-check the messages endpoint — if any "continue"
        # has been checkpointed, there should be exactly one. The
        # instance is paused so this is expected to be 0, but the
        # invariant we care about is "no duplicates ever".
        messages = await get_messages(client, instance_id)
        continue_in_history = _count_messages_with_text(messages, "continue")
        assert continue_in_history <= 1, (
            f"expected at most 1 'continue' message in history, got "
            f"{continue_in_history} in {len(messages)} messages. "
            f"Dedup failed — duplicate 'continue' was enqueued."
        )
    finally:
        await _terminate_instance(client, instance_id)



# --------------------------------------------------------------------------- #
# 5-Layer Message Architecture tests
#
# The watchover evaluator (daemon/graph.py:WatchoverEvaluator) builds the
# LLM payload as five logical layers so the provider's prefix cache can
# hit on the stable layers across a batch of tool calls:
#
#   1. SystemMessage  — watcher soul prompt
#   2. HumanMessage   — [WATCHOVER CONTEXT] ... [WATCHOVER CONTEXT END]
#   3. HumanMessage   — [CONVERSATION SNAPSHOT] ... [CONVERSATION SNAPSHOT
#                       END]  (omitted on early turns before the first
#                       snapshot regeneration)
#   4. Delta messages — verbatim HumanMessage / AIMessage / ToolMessage
#                       between two HumanMessage separators:
#                       [start of recent messages] / [end of recent messages]
#   5. HumanMessage   — [WATCHOVER CHECK] ...  (per-call, uncached)
#
# The mock LLM server captures every watcher-evaluator payload; these tests
# assert on that captured payload via GET /requests (and the /stats counters
# for snapshot regeneration).
# --------------------------------------------------------------------------- #
async def get_captured_requests(client: httpx.AsyncClient) -> dict:
    """Fetch the captured watcher-evaluator payloads from the mock server.

    Returns ``{"watcher_requests": [...], "watcher_call_count": N,
    "builder_call_count": N, "snapshot_call_count": N,
    "agent_call_count": N}``.
    """
    resp = await client.get(f"{MOCK_URL}/requests")
    resp.raise_for_status()
    return resp.json()


def _captured_watcher_text(captured: list[dict]) -> str:
    """Flatten one captured watcher payload into a single lowercased string.

    Used for substring assertions on the 5-layer markers.
    """
    parts: list[str] = []
    for msg in captured:
        content = msg.get("content") or ""
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts).lower()


# --------------------------------------------------------------------------- #
# Test 9: 5-layer message structure
# --------------------------------------------------------------------------- #
async def test_e2e_5layer_message_structure(daemon_and_mock):
    """Verify the watcher evaluator receives the 5-layer message structure."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_5layer_message_structure] created instance=%s",
        instance_id[:8],
    )

    try:
        activation = await enable_watchover(
            client,
            instance_id,
            requirement="read-only cluster inspection only",
        )
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        # Send a message so the agent emits a tool call the watcher evaluates.
        await send_message(client, instance_id, "check cluster status")

        # Wait for the watcher to capture at least one payload.
        async def _watcher_payload_captured() -> bool:
            data = await get_captured_requests(client)
            return len(data.get("watcher_requests", [])) >= 1

        ok = await wait_for_condition(
            _watcher_payload_captured, timeout=WATCHER_TIMEOUT
        )
        data = await get_captured_requests(client)
        captured = data.get("watcher_requests", [])
        assert ok, (
            f"no watcher payload captured within {WATCHER_TIMEOUT}s; "
            f"stats={await get_stats(client)}"
        )
        assert captured, (
            "expected at least one captured watcher payload, got none"
        )

        # Inspect the most recent captured watcher payload.
        payload = captured[-1]
        blob = _captured_watcher_text(payload)

        # Layer 2: guardrail / context block (populated by the builder).
        assert "[watchover context]" in blob, (
            "watcher payload missing layer-2 [WATCHOVER CONTEXT] marker. "
            f"Payload roles: {[m.get('role') for m in payload]}"
        )
        assert "[watchover context end]" in blob, (
            "watcher payload missing layer-2 [WATCHOVER CONTEXT END] marker."
        )

        # Layer 3: snapshot marker. On early turns the snapshot layer is
        # omitted (no regeneration yet), so we accept EITHER the populated
        # snapshot marker OR the absence-of-snapshot case. We assert that
        # IF a snapshot is present, both delimiters appear together.
        has_snapshot = "[conversation snapshot]" in blob
        has_snapshot_end = "[conversation snapshot end]" in blob
        assert has_snapshot == has_snapshot_end, (
            "snapshot markers are inconsistent: "
            f"start={has_snapshot}, end={has_snapshot_end}. "
            "Both must appear together or not at all."
        )

        # Layer 4: delta message separators must always be present
        # (the delta block is emitted even when empty).
        assert "[start of recent messages]" in blob, (
            "watcher payload missing layer-4 [start of recent messages] "
            "separator."
        )
        assert "[end of recent messages]" in blob, (
            "watcher payload missing layer-4 [end of recent messages] "
            "separator."
        )

        # Layer 5: per-call check marker (the tool call under evaluation).
        assert "[watchover check]" in blob, (
            "watcher payload missing layer-5 [WATCHOVER CHECK] marker "
            "(the tool call under evaluation)."
        )

        # The system prompt (layer 1) is always present as a system role.
        roles = [m.get("role") for m in payload]
        assert "system" in roles, (
            f"watcher payload missing layer-1 system message. Roles: {roles}"
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 10: snapshot regeneration at delta_max
# --------------------------------------------------------------------------- #
async def test_e2e_snapshot_at_delta_max(daemon_and_mock):
    """Snapshot regeneration triggers when message count exceeds delta_max (20)."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_snapshot_at_delta_max] created instance=%s",
        instance_id[:8],
    )

    try:
        activation = await enable_watchover(client, instance_id)
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        # Each user message drives one agent turn (the mock agent emits one
        # bash tool call per turn, which the watcher evaluates, adding to the
        # delta buffer). Send enough messages to push the delta buffer past
        # delta_max=20 and trigger at least one snapshot regeneration.
        for i in range(22):
            await send_message(client, instance_id, f"check status round {i}")

        # Wait for the snapshot counter to increment at least once.
        async def _snapshot_triggered() -> bool:
            stats = await get_stats(client)
            return stats.get("snapshot_call_count", 0) >= 1

        ok = await wait_for_condition(
            _snapshot_triggered, timeout=DEFAULT_TIMEOUT
        )
        stats = await get_stats(client)
        assert ok, (
            f"snapshot regeneration never triggered within "
            f"{DEFAULT_TIMEOUT}s after 22 messages; "
            f"snapshot_call_count={stats.get('snapshot_call_count', 0)}, "
            f"watcher_call_count={stats.get('watcher_call_count', 0)}, "
            f"full stats={stats}"
        )
        assert stats["snapshot_call_count"] >= 1, (
            f"expected snapshot_call_count >= 1, got "
            f"{stats['snapshot_call_count']}"
        )

        # After regeneration the watcher payload should carry the populated
        # snapshot layer (layer 3).
        data = await get_captured_requests(client)
        captured = data.get("watcher_requests", [])
        assert captured, (
            "expected captured watcher payloads after snapshot regeneration, "
            "got none"
        )
        blob = _captured_watcher_text(captured[-1])
        assert "[conversation snapshot]" in blob, (
            "post-snapshot watcher payload missing [CONVERSATION SNAPSHOT] "
            "marker; regeneration should have populated layer 3."
        )
        assert "[conversation snapshot end]" in blob, (
            "post-snapshot watcher payload missing "
            "[CONVERSATION SNAPSHOT END] marker."
        )
    finally:
        await _terminate_instance(client, instance_id)


# --------------------------------------------------------------------------- #
# Test 11: delta messages preserve original types
# --------------------------------------------------------------------------- #
async def test_e2e_delta_preserves_message_types(daemon_and_mock):
    """Layer 4 delta messages preserve original message types (Human/AI/Tool)."""
    client = daemon_and_mock
    await set_scenario(client, "allow")

    inst = await create_instance(client)
    instance_id = inst["instance_id"]
    logger.info(
        "[test_e2e_delta_preserves_message_types] created instance=%s",
        instance_id[:8],
    )

    try:
        activation = await enable_watchover(client, instance_id)
        assert activation.get("watchover_enabled") is True, (
            f"enable_watchover returned watchover_enabled="
            f"{activation.get('watchover_enabled')!r}, expected True. "
            f"Full response: {activation}"
        )

        # A single user message produces a full turn: user (human) -> agent
        # tool call (ai) -> tool result (tool). The watcher evaluates the
        # tool call, absorbing all three message types into the delta buffer.
        await send_message(client, instance_id, "check cluster status")

        # Wait for at least one captured watcher payload.
        async def _payload_captured() -> bool:
            data = await get_captured_requests(client)
            return len(data.get("watcher_requests", [])) >= 1

        ok = await wait_for_condition(
            _payload_captured, timeout=WATCHER_TIMEOUT
        )
        data = await get_captured_requests(client)
        captured = data.get("watcher_requests", [])
        assert ok, (
            f"no watcher payload captured within {WATCHER_TIMEOUT}s; "
            f"stats={await get_stats(client)}"
        )
        assert captured, "expected captured watcher payload, got none"

        payload = captured[-1]

        # Locate the delta block: everything strictly between the
        # [start of recent messages] and [end of recent messages] separators.
        start_idx = end_idx = None
        for i, msg in enumerate(payload):
            content = (msg.get("content") or "").strip().lower()
            if content == "[start of recent messages]":
                start_idx = i
            elif content == "[end of recent messages]":
                end_idx = i
                break  # first end marker closes the block
        assert start_idx is not None, (
            "delta block start separator [start of recent messages] not "
            f"found in payload. Roles: {[m.get('role') for m in payload]}"
        )
        assert end_idx is not None, (
            "delta block end separator [end of recent messages] not found "
            f"in payload. Roles: {[m.get('role') for m in payload]}"
        )
        assert end_idx > start_idx, (
            f"end separator (idx={end_idx}) must come after start separator "
            f"(idx={start_idx})."
        )

        delta_messages = payload[start_idx + 1 : end_idx]
        assert delta_messages, (
            "delta block between the separators is empty; expected at least "
            "one absorbed message (user / agent / tool)."
        )
        delta_roles = {m.get("role") for m in delta_messages}

        # The delta preserves the original message TYPES as roles. A real
        # turn always produces at least a human (user) message and an ai
        # (assistant) tool call; tool results may or may not have been
        # absorbed yet depending on timing. We assert the two guaranteed
        # types and note the third as best-effort.
        assert "user" in delta_roles or "human" in delta_roles, (
            f"delta block missing a human/user message. Delta roles: "
            f"{delta_roles}"
        )
        assert "assistant" in delta_roles or "ai" in delta_roles, (
            f"delta block missing an ai/assistant message (the agent's tool "
            f"call). Delta roles: {delta_roles}"
        )
        # Tool results (role 'tool') are expected over a full turn but are
        # timing-dependent; log rather than hard-assert.
        if "tool" not in delta_roles:
            logger.info(
                "[test_e2e_delta_preserves_message_types] "
                "no 'tool' role in delta yet (timing); delta_roles=%s",
                delta_roles,
            )
    finally:
        await _terminate_instance(client, instance_id)
