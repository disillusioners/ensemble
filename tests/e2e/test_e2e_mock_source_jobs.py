#!/usr/bin/env python3
"""End-to-end tests for the **ari** job-orchestrator via a mock source.

These tests exercise the job-orchestration pipeline through the ``ari``
front-door agent. They use a real running daemon (no mocks for the
agent runtime) and a real LLM. The "mock source" angle comes from
:mod:`tests.e2e.mock_source_server.DaemonSourceMock`, which talks to
the daemon's HTTP API directly — that bypass is necessary because the
daemon's source API only accepts the production adapter types
(telegram / slack / scheduler / webhook / whatsapp / discord), not a
generic ``mock`` type.

Test 1 — ``test_mock_source_job_create_and_watch``
    Spawn a fresh ari instance, send it one human message asking it to
    create a job for the leader agent, then wait for the ``completed``
    JOB_EVENT. Asserts the regression guards from
    ``test_e2e_jober_orchestration.py`` still hold when the orchestrator
    is the ari agent: the event lands as a user-role message, the body
    contains a ``Result:`` block, and ari reaches ``completed`` with
    non-empty assistant turns.

Test 2 — ``test_mock_source_job_continue``
    Two-phase flow on a single ari instance:
      * P1 — ari ``job_create``s a leader that says hello. Wait for
        completed event + Result block.
      * P2 — ari ``job_continue``s the leader, asking "what is 1+1?".
        Wait for the second completed event + Result block.
    This guards the ``job_continue`` path through ari, which is the
    natural pattern when the user wants a follow-up to a running
    background job.

Run with::

    # Start the daemon first
    ./dev.sh

    # Then run the tests (integration marker bypasses default exclusion)
    pytest tests/e2e/test_e2e_mock_source_jobs.py -v -s -m integration

Note:
    Messages explicitly say "this is a test/ping" so the agents keep
    their responses short. Without that hint the agents can produce
    long-winded results that push the test over its 180s wall-clock
    budget and bury the assertions in noise.
"""

from __future__ import annotations

import logging
import time

import pytest
import requests

from tests.e2e.mock_source_server import DaemonSourceMock
from tests.e2e.test_e2e_workflows import (
    API_BASE,
    COMPLETION_TIMEOUT,
    POLL_INTERVAL,
    _daemon_running,
    _get_instance,
    _get_messages,
    _send_message,
    _spawn_instance,
    _wait_for_completion,
)

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
# Timeouts — the chains involve multiple real LLM agents (ari → leader →
# sometimes a developer), so allow generous wall-clock budget.
# --------------------------------------------------------------------------- #
JOB_EVENT_TIMEOUT = 180      # wait for a [JOB_EVENT] to land in ari's history
PHASE_GAP = 4                # pause between phases so ari fully settles

# The exact markers the work_notifier emits (see work_notifier.py).
_EVENT_HEADER = "[JOB_EVENT]"
_RESULT_MARKER = "Result:"

# Messages are kept short and explicit so the orchestrator agent doesn't
# burn tokens on long context.
_PING_INTRO = "This is a test/ping workflow."


# --------------------------------------------------------------------------- #
# pytest collection gate — identical to the other e2e files
# --------------------------------------------------------------------------- #
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _daemon_running(),
        reason="Daemon not running at localhost:8079 — start with ./dev.sh",
    ),
]


# --------------------------------------------------------------------------- #
# Helpers specific to ari orchestration
# --------------------------------------------------------------------------- #
def _wait_for_job_event(
    ari_id: str,
    status_word: str,
    timeout: int = JOB_EVENT_TIMEOUT,
) -> dict | None:
    """Poll ari's message history until a ``[JOB_EVENT]`` arrives.

    A real notification is delivered as its own user-role message whose
    content starts with ``[JOB_EVENT] Job {id}... {status}``. This helper
    scans every message for one whose content contains both
    ``[JOB_EVENT]`` and ``{status_word}`` (e.g. ``"in_progress"`` /
    ``"completed"``).

    Returns:
        The matching message dict, or ``None`` on timeout.
    """
    logger.info(
        f"[WAIT_EVENT] ari={ari_id[:8]}... status='{status_word}' "
        f"timeout={timeout}s"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            messages = _get_messages(ari_id)
            if isinstance(messages, list):
                for msg in messages:
                    # Skip the synthetic system prompt — it contains
                    # ``[JOB_EVENT]`` + ``completed`` as template
                    # examples (ari's system prompt documents the event
                    # format) and would shadow the REAL notification.
                    if msg.get("is_synthetic") or msg.get("role") == "system":
                        continue
                    content = str(msg.get("content", ""))
                    if _EVENT_HEADER in content and status_word in content:
                        logger.info(
                            f"[WAIT_EVENT] ✓ found [{status_word}] event "
                            f"role={msg.get('role')}"
                        )
                        return msg
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WAIT_EVENT] GET failed (will retry): {exc}")
        time.sleep(POLL_INTERVAL)

    logger.warning(
        f"[WAIT_EVENT] timed out after {timeout}s — no [{status_word}] "
        f"JOB_EVENT reached ari {ari_id[:8]}..."
    )
    return None


def _assistant_turns(instance_id: str) -> list[dict]:
    """Return the non-empty assistant turns from an instance's history."""
    messages = _get_messages(instance_id)
    if not isinstance(messages, list):
        return []
    return [
        m for m in messages
        if isinstance(m, dict)
        and m.get("role") == "assistant"
        and (m.get("content") or "").strip()
    ]


def _safe_terminate(instance_id: str | None) -> None:
    """Best-effort terminate — never let cleanup crash the test."""
    if not instance_id:
        return
    try:
        requests.delete(f"{API_BASE}/instances/{instance_id}", timeout=30)
    except requests.exceptions.RequestException as exc:
        logger.warning(
            f"[CLEANUP] terminate {instance_id[:8]}... failed (non-fatal): {exc}"
        )


# --------------------------------------------------------------------------- #
# Test 1 — job_create + watch (single agent, expects a real Result)
# --------------------------------------------------------------------------- #
def test_mock_source_job_create_and_watch():
    """ari creates a leader job, watches it, and receives the real event.

    Asserts the regressions are still fixed when the orchestrator is
    ari (not jober):

      * a ``[JOB_EVENT] completed`` notification reaches ari as a
        discrete user-role message (finalize crash regression — before
        the fix ``notify_watchers`` never fired and the orchestrator
        hung), AND
      * that completed event carries a ``Result:`` block with the
        leader's actual output (result_summary regression — before the
        fix the body omitted ``Result:`` because the resolver returned
        ``None``).
    """
    ari_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 1: ari (mock source) — job_create + watch (single agent)")
    logger.info("=" * 60)
    try:
        # Bridge to the running daemon via DaemonSourceMock. We use the
        # mock only to spawn the instance and send the initial message;
        # subsequent polling uses the shared _get_messages helper so
        # the test logic stays identical to the jober e2e file.
        daemon = DaemonSourceMock()
        ari_id = _spawn_instance("ari")
        assert ari_id, "Failed to spawn ari instance"
        # Sanity check: the DaemonSourceMock can also see the same instance.
        instance_state = daemon.get_instance(ari_id)
        assert instance_state.get("instance_id") == ari_id, (
            f"DaemonSourceMock sees a different instance_id: "
            f"{instance_state.get('instance_id')!r} vs {ari_id!r}"
        )

        _send_message(
            ari_id,
            f"{_PING_INTRO} Create a job for the leader agent asking it "
            "to say hello. Watch the job and report the result when it "
            "completes. Keep your response brief.",
        )

        completed = _wait_for_job_event(ari_id, "completed")
        assert completed is not None, (
            f"ari {ari_id[:8]}... never received a '[JOB_EVENT] completed' "
            f"notification within {JOB_EVENT_TIMEOUT}s — the watch_job "
            f"delivery path is broken (finalize crash / notify_watchers "
            f"not firing)."
        )
        assert completed.get("role") == "user", (
            f"Expected the [JOB_EVENT] to be a user-role message, got "
            f"role={completed.get('role')!r}."
        )

        # Regression guard for the missing Result: the completed event MUST
        # carry a ``Result:`` block (the leader's actual output), not
        # just ``Agent: leader``.
        completed_content = str(completed.get("content", ""))
        assert _RESULT_MARKER in completed_content, (
            f"[JOB_EVENT] completed body is missing the 'Result:' block "
            f"— the leader's actual output did not reach ari. Got:\n"
            f"{completed_content}"
        )
        logger.info(
            f"[ASSERT] ✓ completed event carries Result "
            f"({len(completed_content)} chars)"
        )

        finished, final_status = _wait_for_completion(
            ari_id, timeout=COMPLETION_TIMEOUT
        )
        assert finished and final_status == "completed", (
            f"ari did not finish 'completed' (last={final_status})"
        )
        assert _assistant_turns(ari_id), "ari produced no assistant turns."
        logger.info("[ASSERT] ✓ TEST 1 passed")
    finally:
        _safe_terminate(ari_id)


# --------------------------------------------------------------------------- #
# Test 2 — job_create (P1) → job_continue (P2) through ari
# --------------------------------------------------------------------------- #
def test_mock_source_job_continue():
    """Two-phase ari flow exercising job_create + job_continue.

    Phase 1 — ``job_create`` a leader that says hello. Establishes the
    leader instance and its ``work_id`` for continuation. Waits for
    the ``completed`` event before starting Phase 2 (so ``job_continue``
    has a finished leader to continue).

    Phase 2 — a SECOND human message to the SAME ari asking it to
    ``job_continue`` the leader with a follow-up question ("what is
    1+1?"). Waits for the second ``completed`` event carrying the
    leader's fresh Result.

    Both messages explicitly say "this is a test/ping" so ari and the
    leader keep their responses short (regression-style assertion:
    ari must summarise the new result, not write an essay).
    """
    ari_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 2: ari (mock source) — job_create → job_continue")
    logger.info("=" * 60)
    try:
        # DaemonSourceMock is constructed so the e2e test demonstrates
        # the bridge class; the actual spawn + send use the shared
        # helpers so the test stays consistent with the rest of the
        # e2e suite.
        DaemonSourceMock()
        ari_id = _spawn_instance("ari")
        assert ari_id, "Failed to spawn ari instance"

        # ── Phase 1: job_create (establish leader + work_id) ────────────
        logger.info("-" * 60)
        logger.info("PHASE 1: ari job_create leader (say hello)")
        logger.info("-" * 60)
        _send_message(
            ari_id,
            f"{_PING_INTRO} Create a job for the leader agent asking it "
            "to simply say hello. Watch the job and report the result "
            "when it completes. Do NOT use job_continue — this is the "
            "first run. Keep your response brief.",
        )

        p1_completed = _wait_for_job_event(ari_id, "completed")
        assert p1_completed is not None, (
            f"P1: ari never received a '[JOB_EVENT] completed' for the "
            f"initial leader job within {JOB_EVENT_TIMEOUT}s."
        )
        assert p1_completed.get("role") == "user", (
            f"P1: expected completed event to be a user-role message, "
            f"got role={p1_completed.get('role')!r}."
        )
        p1_content = str(p1_completed.get("content", ""))
        assert _RESULT_MARKER in p1_content, (
            f"P1: completed event missing the 'Result:' block. Got:\n"
            f"{p1_content}"
        )
        logger.info("[P1] ✓ leader job completed with Result")

        # Wait for ari to fully settle (its orchestration summary turn)
        # so the next message is a clean continuation, not a race with
        # the in-flight turn.
        finished, _ = _wait_for_completion(ari_id, timeout=COMPLETION_TIMEOUT)
        assert finished, "P1: ari did not reach a terminal status after job_create."
        time.sleep(PHASE_GAP)

        # ── Phase 2: job_continue → leader answers a follow-up ──────────
        logger.info("-" * 60)
        logger.info("PHASE 2: ari job_continue leader (what is 1+1?)")
        logger.info("-" * 60)
        _send_message(
            ari_id,
            f"{_PING_INTRO} Now use job_continue on the same leader you "
            "created in P1 and ask it to answer: what is 1+1? Watch the "
            "job and report the result. Keep your response brief.",
        )

        p2_completed = _wait_for_job_event(ari_id, "completed")
        assert p2_completed is not None, (
            f"P2: ari never received a '[JOB_EVENT] completed' for the "
            f"job_continue within {JOB_EVENT_TIMEOUT}s."
        )
        assert p2_completed.get("role") == "user", (
            f"P2: expected completed event to be a user-role message, "
            f"got role={p2_completed.get('role')!r}."
        )
        p2_content = str(p2_completed.get("content", ""))
        assert _RESULT_MARKER in p2_content, (
            f"P2: completed event missing the 'Result:' block (the "
            f"leader's response to the follow-up). Got:\n{p2_content}"
        )
        # Stricter semantic check (Reviewer Council W6): the leader's
        # Result for "what is 1+1?" MUST contain the digit "2" — the
        # correct answer. This catches regressions where the follow-up
        # is silently dropped (leader replays the original "hello") or
        # where the answer is corrupted in the notification path.
        assert "2" in p2_content, (
            f"P2: expected the leader's Result to contain '2' (the "
            f"answer to 'what is 1+1?'), but no '2' digit found. The "
            f"follow-up may have been dropped. Got:\n{p2_content}"
        )
        logger.info("[P2] ✓ completed event received with Result containing '2'")

        finished, final_status = _wait_for_completion(
            ari_id, timeout=COMPLETION_TIMEOUT
        )
        assert finished and final_status == "completed", (
            f"P2: ari did not finish 'completed' (last={final_status})"
        )
        assert _assistant_turns(ari_id), "ari produced no assistant turns."
        # The instance must still exist and be in a known good state
        # after the second round-trip.
        final_state = _get_instance(ari_id)
        assert final_state.get("status") == "completed", (
            f"P2: final instance status was {final_state.get('status')!r}, "
            f"expected 'completed'."
        )
        logger.info("[ASSERT] ✓ TEST 2 passed")
    finally:
        _safe_terminate(ari_id)


# --------------------------------------------------------------------------- #
# Test 3 — Reviewer Council W5: lock in the source routing default
# --------------------------------------------------------------------------- #
def test_mock_source_routing_defaults_to_ari():
    """Verify the source routing default — no agent override → ``ari``.

    Reviewer Council recommendation W5 noted that the e2e tests do not
    explicitly assert that an IncomingMessage arriving through the
    mock source WITHOUT an explicit ``agent`` override in its
    metadata resolves to the ``ari`` front-door agent. This test
    locks that default in so a silent regression in
    ``MockSourceAdapter.emit()`` cannot pass the suite by accident.

    The mechanism: ``MockSourceAdapter.emit(..., agent="ari")`` defaults
    ``agent`` to ``"ari"`` and stamps that value onto the resulting
    ``IncomingMessage.metadata["agent"]``. The daemon's dispatcher
    reads ``metadata["agent"]`` (``daemon/sources/registry.py``:
    ``_handle_message``) to resolve the message to an agent instance
    — so when no override is provided, the routing must fall back to
    ``ari`` or every e2e test would silently spawn a different agent.

    The test wires the adapter's ``_on_message`` callback to a capture
    list, calls ``adapter.emit(...)`` WITHOUT specifying ``agent``,
    and asserts:

      * the callback fires exactly once,
      * the captured message's ``metadata["agent"]`` equals ``"ari"``,
      * a ``message_id`` was auto-generated (so dedup works),
      * the content was forwarded verbatim.

    This is intentionally a unit-style assertion (no daemon, no LLM,
    no real instance spawn) — it tests the adapter contract directly
    so it can run inside the same file as the integration tests
    without adding wall-clock cost.
    """
    import asyncio

    from daemon.sources.base import IncomingMessage, SourceConfig

    from tests.e2e.mock_source_server import MockSourceAdapter

    logger.info("=" * 60)
    logger.info("TEST 3: MockSourceAdapter routing default → 'ari'")
    logger.info("=" * 60)

    # 1. Build a SourceConfig matching the routing-default scenario.
    config = SourceConfig(
        source_id="test-mock-default",
        source_type="mock",
        name="default-test",
        config={},
        credentials={},
    )

    # 2. Capture the adapter's _on_message callback into a list so we
    #    can assert on what the adapter produced — exactly the same
    #    callback shape the daemon dispatcher would receive in
    #    production.
    captured: list[IncomingMessage] = []

    async def capture(msg: IncomingMessage) -> None:
        captured.append(msg)

    adapter = MockSourceAdapter(config=config, on_message=capture)

    # 3. Emit WITHOUT specifying ``agent`` — exercises the default
    #    branch (``agent="ari"``). The dispatcher never sees an
    #    explicit override; the routing decision is made entirely by the
    #    adapter's default.
    asyncio.run(adapter.emit("hello world", external_user_id="test_user"))

    # 4. Verify the routing default.
    assert len(captured) == 1, (
        f"Expected exactly one IncomingMessage to be captured by the "
        f"_on_message callback, got {len(captured)}. The mock adapter "
        f"should emit exactly once per emit() call."
    )
    captured_msg = captured[0]
    assert captured_msg.metadata.get("agent") == "ari", (
        f"MockSourceAdapter.emit() must stamp metadata['agent']='ari' "
        f"by default — the source routing default that ensures "
        f"messages WITHOUT an explicit agent override are routed to "
        f"the ari front-door agent (the same path the production "
        f"daemon dispatcher reads via "
        f"daemon/sources/registry.py:_handle_message). Got "
        f"metadata['agent']={captured_msg.metadata.get('agent')!r}. "
        f"If this regresses, every e2e test that relies on the "
        f"implicit 'ari' routing will silently fail to spawn an "
        f"instance."
    )
    assert captured_msg.metadata.get("message_id"), (
        f"MockSourceAdapter.emit() must auto-generate a message_id so "
        f"the daemon's dedup layer treats each emit as fresh; got "
        f"metadata={captured_msg.metadata!r}."
    )
    assert captured_msg.content == "hello world", (
        f"MockSourceAdapter.emit() must forward content verbatim; got "
        f"content={captured_msg.content!r}."
    )
    logger.info(
        f"[ASSERT] ✓ MockSourceAdapter routing default is "
        f"agent={captured_msg.metadata.get('agent')!r} "
        f"(message_id={captured_msg.metadata.get('message_id')!r})"
    )
    logger.info("[ASSERT] ✓ TEST 3 passed")
