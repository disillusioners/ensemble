#!/usr/bin/env python3
"""End-to-end tests for the jober agent's job-orchestration workflow.

These tests exercise the core jober use-cases against the REAL running
daemon (no mocks):

  1. **job_create + watch (single agent)** — the jober creates a leader
     job asking it to say hello, watches it, and receives the real
     ``completed`` event carrying the leader's actual Result.

  2. **job_create → job_continue (parent→child chain)** — two phases on
     the SAME jober instance:
       * P1: ``job_create`` a leader that says hello (establishes a
         leader instance + its ``work_id`` for continuation).
       * P2: a SECOND human message to the jober asking it to
         ``job_continue`` that leader so it spawns a developer child
         who says hello back. Verifies BOTH an ``in_progress ⟳`` event
         (fires when the leader finishes its turn while the developer
         is still running) AND a ``completed ✓`` event with the real
         leader Result.

WHY THIS FILE EXISTS (regression guards):

  Three regressions that silently broke job-orchestration notifications:

    * **Finalize crash** (Phase 5 migration miss): ``_finalize_job_db_sync``
      wrote the dropped ``completed_at``/``result_summary``/``error_message``
      columns onto ``JobItem`` → ``CompileError`` before ``notify_watchers``
      fired → the watched job fell back to ``failed`` and the jober hung /
      hallucinated a fake result.

    * **Missing Result on ``completed``**: the terminal ``notify_watchers``
      call dropped the result it had pre-fetched, and the resolver returns
      ``None`` for job-kind work, so the ``[JOB_EVENT] completed`` body
      omitted the ``Result:`` block (the leader's actual output never
      reached the jober).

    * **Missing ``in_progress``**: a parent that spawns a child and goes
      ``WAITING_CHILDREN`` produced no ``instance_lifecycle`` event the
      observer consumed, so the ``⟳`` progress event never fired — the
      jober never learned the job was mid-flight with children pending.

  The assertions below fail loudly if any of these regress.

Run with::

    # Start the daemon first
    ./dev.sh

    pytest tests/e2e/test_e2e_jober_orchestration.py -v -s -m integration
"""

from __future__ import annotations

import logging
import time
from typing import Any

import pytest
import requests

from tests.e2e.test_e2e_workflows import (
    API_BASE,
    COMPLETION_TIMEOUT,
    POLL_INTERVAL,
    _daemon_running,
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
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Timeouts — the chains involve multiple real LLM agents (jober → leader →
# developer), so allow generous wall-clock budget.
# --------------------------------------------------------------------------- #
JOB_EVENT_TIMEOUT = 180      # wait for a [JOB_EVENT] to land in the jober history
PHASE_GAP = 4                # pause between phases so the jober fully settles

# The exact markers the work_notifier emits (see work_notifier.py).
_EVENT_HEADER = "[JOB_EVENT]"
_RESULT_MARKER = "Result:"


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
# Helpers specific to jober orchestration
# --------------------------------------------------------------------------- #
def _wait_for_job_event(
    jober_id: str,
    status_word: str,
    timeout: int = JOB_EVENT_TIMEOUT,
) -> dict | None:
    """Poll the jober's message history until a ``[JOB_EVENT]`` arrives.

    A real notification is delivered as its own user-role message whose
    content starts with ``[JOB_EVENT] Job {id}... {status}``. This helper
    scans every message for one whose content contains both ``[JOB_EVENT]``
    and ``{status_word}`` (e.g. ``"in_progress"`` / ``"completed"``).

    Returns:
        The matching message dict, or ``None`` on timeout.
    """
    logger.info(
        f"[WAIT_EVENT] jober={jober_id[:8]}... status='{status_word}' "
        f"timeout={timeout}s"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            messages = _get_messages(jober_id)
            if isinstance(messages, list):
                for msg in messages:
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
        f"JOB_EVENT reached jober {jober_id[:8]}..."
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
def test_jober_job_create_and_watch():
    """jober creates a leader job, watches it, and receives the real event.

    Asserts the regressions are fixed:

      * a ``[JOB_EVENT] completed`` notification reaches the jober as a
        discrete user-role message (finalize crash regression — before the
        fix ``notify_watchers`` never fired and the jober hung), AND
      * that completed event carries a ``Result:`` block with the leader's
        actual output (result_summary regression — before the fix the body
        omitted ``Result:`` because the resolver returned ``None``).
    """
    jober_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 1: jober — job_create + watch (single agent)")
    logger.info("=" * 60)
    try:
        jober_id = _spawn_instance("jober")
        assert jober_id, "Failed to spawn jober instance"

        _send_message(
            jober_id,
            "Create a job for the leader agent asking it to say hello. "
            "Watch the job and report the result when it completes.",
        )

        completed = _wait_for_job_event(jober_id, "completed")
        assert completed is not None, (
            f"jober {jober_id[:8]}... never received a '[JOB_EVENT] "
            f"completed' notification within {JOB_EVENT_TIMEOUT}s — the "
            f"watch_job delivery path is broken (finalize crash / "
            f"notify_watchers not firing)."
        )
        assert completed.get("role") == "user", (
            f"Expected the [JOB_EVENT] to be a user-role message, got "
            f"role={completed.get('role')!r}."
        )

        # Regression guard for the missing Result: the completed event MUST
        # carry a ``Result:`` block (the leader's actual output), not just
        # ``Agent: leader``.
        completed_content = str(completed.get("content", ""))
        assert _RESULT_MARKER in completed_content, (
            f"[JOB_EVENT] completed body is missing the 'Result:' block — "
            f"the leader's actual output did not reach the jober. Got:\n"
            f"{completed_content}"
        )
        logger.info(
            f"[ASSERT] ✓ completed event carries Result "
            f"({len(completed_content)} chars)"
        )

        finished, final_status = _wait_for_completion(
            jober_id, timeout=COMPLETION_TIMEOUT
        )
        assert finished and final_status == "completed", (
            f"jober did not finish 'completed' (last={final_status})"
        )
        assert _assistant_turns(jober_id), "jober produced no assistant turns."
        logger.info("[ASSERT] ✓ TEST 1 passed")
    finally:
        _safe_terminate(jober_id)


# --------------------------------------------------------------------------- #
# Test 2 — job_create (P1) → job_continue with parent→child (P2)
# --------------------------------------------------------------------------- #
def test_jober_job_continue_spawns_child():
    """Two-phase jober flow exercising job_continue + parent→child.

    Phase 1 — ``job_create`` a leader that says hello. Establishes the
    leader instance and its ``work_id`` for continuation. Waits for the
    ``completed`` event before starting Phase 2 (so ``job_continue`` has a
    finished leader to continue).

    Phase 2 — a SECOND human message to the SAME jober asking it to
    ``job_continue`` the leader so it spawns a developer child who says
    hello back. Asserts BOTH:

      * an ``in_progress ⟳`` event fires (parent spawned child, now
        waiting) — the in_progress regression, AND
      * a ``completed ✓`` event fires with a ``Result:`` block (the
        leader's summary after receiving the developer's report).
    """
    jober_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 2: jober — job_create → job_continue (parent→child)")
    logger.info("=" * 60)
    try:
        jober_id = _spawn_instance("jober")
        assert jober_id, "Failed to spawn jober instance"

        # ── Phase 1: job_create (establish leader + work_id) ────────────
        logger.info("-" * 60)
        logger.info("PHASE 1: job_create leader (say hello)")
        logger.info("-" * 60)
        _send_message(
            jober_id,
            "Create a job for the leader agent asking it to say hello. "
            "Watch the job and report the result when it completes. "
            "Do NOT use job_continue — this is the first run.",
        )

        p1_completed = _wait_for_job_event(jober_id, "completed")
        assert p1_completed is not None, (
            f"P1: jober never received a '[JOB_EVENT] completed' for the "
            f"initial leader job within {JOB_EVENT_TIMEOUT}s."
        )
        assert _RESULT_MARKER in str(p1_completed.get("content", "")), (
            "P1: completed event missing the 'Result:' block."
        )
        logger.info("[P1] ✓ leader job completed with Result")

        # Wait for the jober to fully settle (its orchestration summary
        # turn) so the next message is a clean continuation, not a race
        # with the in-flight turn.
        finished, _ = _wait_for_completion(jober_id, timeout=COMPLETION_TIMEOUT)
        assert finished, "P1: jober did not reach a terminal status after job_create."
        time.sleep(PHASE_GAP)

        # ── Phase 2: job_continue → leader spawns developer child ───────
        logger.info("-" * 60)
        logger.info("PHASE 2: job_continue → leader spawns developer child")
        logger.info("-" * 60)
        # A fresh human message to the jober instructing it to continue the
        # leader instance it created in P1.
        _send_message(
            jober_id,
            "Now use job_continue on that same leader to ask it to spawn a "
            "developer agent and have the developer say hello back. Watch "
            "the job and report the result, including the in-progress and "
            "completed events.",
        )

        # Regression guard: an in_progress event MUST fire when the leader
        # finishes its turn while the developer is still running. Before the
        # fix this never fired (no instance_lifecycle event at the
        # WAITING_CHILDREN transition). The work_notifier renders the status
        # as ``in progress ⟳`` (see ``_STATUS_DISPLAY_MAP``), so we match on
        # the display string + the ``⟳`` glyph rather than the canonical
        # ``in_progress`` token.
        in_progress = _wait_for_job_event(jober_id, "in progress")
        assert in_progress is not None, (
            f"P2: jober never received a '[JOB_EVENT] in_progress' event "
            f"for the parent-child chain within {JOB_EVENT_TIMEOUT}s — the "
            f"in_progress notification did not fire when the leader spawned "
            f"a child and went WAITING_CHILDREN."
        )
        assert in_progress.get("role") == "user", (
            f"P2: expected in_progress to be a user-role message, got "
            f"role={in_progress.get('role')!r}."
        )
        logger.info("[P2] ✓ in_progress event received")

        p2_completed = _wait_for_job_event(jober_id, "completed")
        assert p2_completed is not None, (
            f"P2: jober never received a '[JOB_EVENT] completed' for the "
            f"parent-child chain within {JOB_EVENT_TIMEOUT}s."
        )
        p2_content = str(p2_completed.get("content", ""))
        assert _RESULT_MARKER in p2_content, (
            f"P2: completed event missing the 'Result:' block (the leader's "
            f"summary after the developer's report). Got:\n{p2_content}"
        )
        logger.info("[P2] ✓ completed event received with Result")

        finished, final_status = _wait_for_completion(
            jober_id, timeout=COMPLETION_TIMEOUT
        )
        assert finished and final_status == "completed", (
            f"P2: jober did not finish 'completed' (last={final_status})"
        )
        assert _assistant_turns(jober_id), "jober produced no assistant turns."
        logger.info("[ASSERT] ✓ TEST 2 passed")
    finally:
        _safe_terminate(jober_id)
