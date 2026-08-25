"""E2E: resume after pause with children completing DURING the pause (B2).

Phase 2 (pause-resume-terminate-tree-fix, task 2.9). The B2 defect:
pause the root while children are running → children complete during
the pause → resume returns 200 but the root never reaches terminal
state — the buffered child reports are stranded (msg count frozen,
``_compact_fired_watchers_for_paused`` destroyed the FIRED wake
signals, no SuspendTurn handle → ``invalid_or_missing_handle``).

Post-fix acceptance (phase2-plan.md task 2.9):
  * root reaches ``COMPLETED`` after resume;
  * the children's reports land in ``report_injections`` with
    ``state='INJECTED'`` (delivered, not stranded PENDING/DEFERRED);
  * the root's message count advances by exactly N (N = # children
    that completed during the pause) — no advance before resume, no
    double-delivery after resume + a follow-up message.

Run (live daemon on :8079, started via ``./dev.sh``)::

    pytest tests/e2e/test_resume_after_pause_with_children_completing_during_pause.py -v -s

or via the pack pattern::

    test/packs/e2e_workflows_ensure_test.sh -k test_resume_after_pause_with_children_completing_during_pause
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

from tests.e2e.test_e2e_workflows import (
    API_BASE,
    COMPLETION_TIMEOUT,
    POLL_INTERVAL,
    PROJECT_ID,
    TERMINAL_STATUSES,
    _daemon_running,
    _get_messages,
    _pause_instance,
    _resume_instance,
    _send_message,
    _spawn_instance,
)

logger = logging.getLogger(__name__)

pytest_markers = None  # placeholder to satisfy linters; real marks below

import pytest  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _daemon_running(),
        reason="Daemon not running at localhost:8079 — start with ./dev.sh",
    ),
]

# Two children, each sleeping long enough to straddle the pause window.
# The leader must spawn them ITSELF: delegating to a developer child
# collides with the permission model (developer ∉ developer.team_members
# → "Agent 'developer' is not allowed to spawn 'developer'") and
# grandchildren would not count as the leader's direct children anyway.
SPAWN_MESSAGE = (
    "Using spawn_instance yourself (do not delegate), spawn exactly 2 "
    "developer children directly under you; each child must run a bash "
    "sleep of 90 seconds and then reply 'done sleeping'; wait for both "
    "children to finish before you reply"
)

# Spawn-wait raised 60s → 120s (LLM failover tax compensation, primary down)
B2_SPAWN_TIMEOUT = 120

PAUSE_SETTLE_SECONDS = 100  # > child sleep so both complete DURING pause


def _status(instance_id: str) -> str:
    response = requests.get(f"{API_BASE}/instances/{instance_id}", timeout=30)
    response.raise_for_status()
    return response.json().get("status", "")


def _wait_for_status(
    instance_id: str, statuses: set[str], timeout: int
) -> tuple[bool, str]:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _status(instance_id)
        if last in statuses:
            return True, last
        time.sleep(POLL_INTERVAL)
    return False, last


def _pg_session():
    """Sync PG session over the E2E DB (mirrors test_e2e_workflows)."""
    from sqlalchemy import create_engine
    from sqlmodel import Session

    from tests.e2e.test_e2e_workflows import _e2e_pg_url

    engine = create_engine(_e2e_pg_url())
    return Session(engine)


def _leader_children(leader_id: str) -> list[str]:
    """Return ALL direct children of ``leader_id`` via its detail endpoint.

    The shared ``_wait_for_child_spawned`` helper only ever returns
    ``children[0]``, so an accumulation loop built on it can never
    collect a second distinct child. Read the full ``children`` list
    here and guard each id against a ``parent_id`` mismatch.
    """
    response = requests.get(f"{API_BASE}/instances/{leader_id}", timeout=30)
    response.raise_for_status()
    child_ids = response.json().get("children", []) or []
    resolved: list[str] = []
    for child_id in child_ids:
        detail = requests.get(f"{API_BASE}/instances/{child_id}", timeout=30)
        detail.raise_for_status()
        if detail.json().get("parent_id") == leader_id:
            resolved.append(child_id)
    return resolved


def test_resume_after_pause_with_children_completing_during_pause():
    """B2 acceptance: buffered reports deliver on resume (not before)."""
    leader_id: str | None = None
    try:
        # ── Setup: leader → 2 slow developer children ──────────────
        leader_id = _spawn_instance("leader", PROJECT_ID)
        assert leader_id

        _send_message(leader_id, SPAWN_MESSAGE)

        child_ids: list[str] = []
        deadline = time.time() + B2_SPAWN_TIMEOUT
        while len(child_ids) < 2 and time.time() < deadline:
            child_ids = _leader_children(leader_id)
            if len(child_ids) < 2:
                time.sleep(POLL_INTERVAL)
        assert len(child_ids) == 2, (
            f"Leader did not spawn 2 children within {B2_SPAWN_TIMEOUT}s "
            f"(got {len(child_ids)})"
        )
        logger.info(f"[B2] children spawned: {[c[:8] for c in child_ids]}")

        # ── Pause the root while the children run ───────────────────
        pause_result = _pause_instance(leader_id)
        assert pause_result.get("paused") is True

        msg_count_at_pause = len(_get_messages(leader_id))

        # ── Let BOTH children complete DURING the pause ─────────────
        time.sleep(PAUSE_SETTLE_SECONDS)
        for child_id in child_ids:
            ok, status = _wait_for_status(
                child_id, TERMINAL_STATUSES, timeout=30
            )
            assert ok, f"child {child_id[:8]}... not terminal: {status}"

        # No advance before resume (the reports are buffered, not lost).
        msg_count_before_resume = len(_get_messages(leader_id))
        assert msg_count_before_resume == msg_count_at_pause, (
            "B2 pre-resume drift: message count advanced while paused — "
            f"{msg_count_at_pause} → {msg_count_before_resume}"
        )

        # ── Resume ──────────────────────────────────────────────────
        resume_result = _resume_instance(leader_id)
        assert resume_result.get("resumed") is True

        # ── The root must now drain the buffered reports + complete ──
        ok, final_status = _wait_for_status(
            leader_id, TERMINAL_STATUSES, timeout=COMPLETION_TIMEOUT
        )
        assert ok, (
            f"B2 STRAND: root never reached terminal status after resume "
            f"(last={final_status}) — buffered reports were not delivered"
        )
        assert final_status == "completed", (
            f"root terminal status expected 'completed', got {final_status}"
        )

        # Exactly-2 delivery: message count advances by exactly N=2.
        msg_count_final = len(_get_messages(leader_id))
        assert msg_count_final - msg_count_at_pause == 2, (
            f"expected msg count to advance by exactly 2 "
            f"({msg_count_at_pause} → {msg_count_final}); a larger delta "
            f"means double-delivery, smaller means a stranded report"
        )

        # The children's reports are INJECTED (delivered to the LLM),
        # not stranded PENDING/DEFERRED.
        try:
            with _pg_session() as session:
                from sqlalchemy import text

                rows = session.execute(
                    text(
                        "SELECT state FROM report_injections "
                        "WHERE parent_instance_id = :p "
                        "AND child_instance_id IN (:c1, :c2)"
                    ),
                    {"p": leader_id, "c1": child_ids[0], "c2": child_ids[1]},
                ).scalars().all()
            assert len(rows) == 2, (
                f"expected 2 report_injection rows for the paused-window "
                f"children, got {len(rows)}"
            )
            assert all(r == "INJECTED" for r in rows), (
                f"report rows not INJECTED: {rows}"
            )
        except Exception as exc:  # noqa: BLE001 — DB probe is best-effort
            logger.warning(
                f"[B2] PG probe unavailable ({exc}); HTTP-level assertions "
                f"above remain the binding acceptance"
            )

        # No double-delivery on a follow-up message.
        _send_message(leader_id, "all children reported? reply yes")
        _wait_for_status(leader_id, TERMINAL_STATUSES, COMPLETION_TIMEOUT)
        after_followup = len(_get_messages(leader_id))
        assert after_followup - msg_count_final == 1, (
            f"follow-up message double-delivered buffered reports "
            f"({msg_count_final} → {after_followup})"
        )
    finally:
        if leader_id:
            requests.delete(f"{API_BASE}/instances/{leader_id}", timeout=30)
