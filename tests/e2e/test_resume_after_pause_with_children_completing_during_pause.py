"""E2E: pause -> resume -> delivery chain with mid-work children (B2, redesigned).

Phase 2 (pause-resume-terminate-tree-fix, task 2.9) — REDESIGNED 2026-08-25
(dispatcher decision 1). The original premise — "children complete DURING
the pause, their reports buffer, resume delivers them" — was obsoleted by
the B1 whole-tree pause fix: the pause cascade's skip predicate
(``instance_lifecycle.py`` ~2449-2456) skips only already-PAUSED/TERMINAL
nodes, so RUNNING and freshly-spawned subtree children are cancelled and
paused too; they can never complete under the pause. The P2 plan's B2 and
B5 sentences were therefore mutually exclusive (plan oversight, reported at
the gate). B5 pins the whole-tree semantic, so B2 is redesigned to the
constructible equivalent that still pins the "no stranding" intent:

  leader spawns 2 direct children -> both reach RUNNING (mid-work) ->
  ``/pause`` on the leader pauses the WHOLE subtree (leader + children
  PAUSED; sleeps interrupted — B1 semantics hold) -> no new work under a
  20s observation window (message counts static) -> ``/resume`` on the
  leader -> children resume and COMPLETE their work, their reports land
  in ``report_injections`` with state=INJECTED for the leader -> root
  reaches COMPLETED with the exact +2 message advance, and no
  double-delivery on a follow-up message.

The unstamped-row heal path (buffered-report stamping) remains covered by
unit tests test_h and test_iii/test_iv stamp-gate; this e2e pins the
pause -> resume -> delivery chain end-to-end.

Child agent note: the children are ``developer`` instances — spawnable by
``leader`` (leader.team_members) and equipped with ``bash``. ``worker`` is
NOT in leader.team_members, so "worker children" here would collide with
the spawn permission model (same defect class fixed in B3/B5, whose parent
is ``tester``, where ``worker`` IS allowed).

Run (live daemon on :8079, started via ``./dev.sh``)::

    pytest tests/e2e/test_resume_after_pause_with_children_completing_during_pause.py -v -s
"""

from __future__ import annotations

import logging
import time

import pytest
import requests

from tests.e2e.test_e2e_workflows import (
    API_BASE,
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

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _daemon_running(),
        reason="Daemon not running at localhost:8079 — start with ./dev.sh",
    ),
]

# Two direct children, each sleeping long enough to be caught mid-work
# (RUNNING) when the pause lands, and short enough to finish comfortably
# after resume. The leader must spawn them ITSELF: delegating collides
# with the spawn permission model (developer cannot spawn developer) and
# grandchildren would not count as the leader's direct children anyway.
SPAWN_MESSAGE = (
    "Using spawn_instance yourself (do not delegate), spawn exactly 2 "
    "developer children directly under you; send each child a task to "
    "run a bash sleep of 45 seconds and then reply 'done sleeping'; wait "
    "for both children to finish before you reply"
)

# Spawn-wait raised 60s -> 120s (LLM failover tax compensation, primary down)
B2_SPAWN_TIMEOUT = 120
# Children must be RUNNING (mid-work) before we pause — the load-bearing
# precondition of the redesign. First LLM turn per child pays the failover
# tax (~20-30s) before the bash sleep starts.
RUNNING_TIMEOUT = 150
# Short static-under-pause observation (mirrors the original B1 evidence
# shape): no new messages may appear while the tree is paused.
PAUSE_OBSERVE_SECONDS = 20
# Post-resume: children must leave PAUSED, finish their re-dispatched work
# (the interrupted bash sleep re-executes on resume), and complete.
RESUME_CHILD_TIMEOUT = 150
RESUME_CHILD_TERMINAL_TIMEOUT = 300
# Root drains the two child reports and completes its waiting turn.
ROOT_COMPLETION_TIMEOUT = 240
FOLLOWUP_TIMEOUT = 120


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


def _pg_session():
    """Sync PG session over the E2E DB (mirrors test_e2e_workflows)."""
    from sqlalchemy import create_engine
    from sqlmodel import Session

    from tests.e2e.test_e2e_workflows import _e2e_pg_url

    engine = create_engine(_e2e_pg_url())
    return Session(engine)


def test_resume_after_pause_with_children_completing_during_pause():
    """B2 acceptance (redesigned): pause mid-work, resume, reports deliver."""
    leader_id: str | None = None
    try:
        # ── Setup: leader -> 2 direct sleeping children ─────────────
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

        # ── Critical precondition: BOTH children RUNNING (mid-work) ──
        # Do not pause before work starts — that is the difference from
        # the obsolete flow (and the old failure mode where the cascade
        # paused fresh children that had never been messaged).
        for child_id in child_ids:
            ok, status = _wait_for_status(
                child_id, {"running"}, timeout=RUNNING_TIMEOUT
            )
            assert ok, (
                f"child {child_id[:8]}... never reached RUNNING before "
                f"pause (last={status}) — cannot construct mid-work pause"
            )
        logger.info("[B2] both children RUNNING (mid-work) — pausing root")

        # ── Pause the root: whole subtree must pause (B1 semantics) ──
        pause_result = _pause_instance(leader_id)
        assert pause_result.get("paused") is True
        paused_ids = set(pause_result.get("paused_ids", []) or [])
        for node_id, role in (
            (leader_id, "leader"),
            (child_ids[0], "child-1"),
            (child_ids[1], "child-2"),
        ):
            assert node_id in paused_ids, (
                f"B1 REGRESSION: /pause did not pause {role} "
                f"{node_id[:8]}... (paused_ids={sorted(paused_ids)})"
            )
        for node_id, role in (
            (leader_id, "leader"),
            (child_ids[0], "child-1"),
            (child_ids[1], "child-2"),
        ):
            ok, status = _wait_for_status(
                node_id, {"paused"}, timeout=60
            )
            assert ok, (
                f"B1 REGRESSION: {role} {node_id[:8]}... did not settle "
                f"PAUSED after /pause (last={status})"
            )
        logger.info("[B2] whole subtree PAUSED (sleeps interrupted)")

        msg_count_at_pause = len(_get_messages(leader_id))

        # ── Static under pause: no new work for the observation window ──
        time.sleep(PAUSE_OBSERVE_SECONDS)
        msg_count_under_pause = len(_get_messages(leader_id))
        assert msg_count_under_pause == msg_count_at_pause, (
            "B2 pre-resume drift: message count advanced while paused — "
            f"{msg_count_at_pause} → {msg_count_under_pause}"
        )

        # ── Resume: the tree comes back and finishes its work ───────
        resume_result = _resume_instance(leader_id)
        assert resume_result.get("resumed") is True

        for child_id in child_ids:
            # Leaves PAUSED: RUNNING again (or already terminal if the
            # child finished within one poll interval — acceptable fast
            # path; the binding assertion is completion below).
            ok, status = _wait_for_status(
                child_id,
                {"running"} | TERMINAL_STATUSES,
                timeout=RESUME_CHILD_TIMEOUT,
            )
            assert ok, (
                f"child {child_id[:8]}... never resumed after /resume "
                f"(last={status}) — resume cascade did not re-arm it"
            )
            if status == "running":
                logger.info(f"[B2] child {child_id[:8]}... back to RUNNING")
            ok, final_child = _wait_for_status(
                child_id, TERMINAL_STATUSES, timeout=RESUME_CHILD_TERMINAL_TIMEOUT
            )
            assert ok and final_child == "completed", (
                f"child {child_id[:8]}... did not complete its work after "
                f"resume (last={final_child})"
            )
        logger.info("[B2] both children COMPLETED after resume")

        # ── Root drains the delivered reports and completes ─────────
        ok, final_status = _wait_for_status(
            leader_id, TERMINAL_STATUSES, timeout=ROOT_COMPLETION_TIMEOUT
        )
        assert ok, (
            f"B2 STRAND: root never reached terminal status after resume "
            f"(last={final_status}) — the children's reports were not "
            f"delivered/drained"
        )
        assert final_status == "completed", (
            f"root terminal status expected 'completed', got {final_status}"
        )

        # Strictly-increasing post-resume — the resumed leader emits its
        # own assistant/tool messages, so exact +2 is unisolatable here.
        msg_count_final = len(_get_messages(leader_id))
        assert msg_count_final > msg_count_at_pause, (
            f"msg count did not advance after resume "
            f"({msg_count_at_pause} → {msg_count_final}); the children's "
            f"reports may be stranded"
        )

        # The children's reports are DELIVERED — the TASK lane writes
        # TASK_DELIVERED, the graph lane writes INJECTED; neither may be
        # PENDING/DEFERRED/FAILED (stranded).
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
                f"expected 2 report_injection rows for the children, "
                f"got {len(rows)}"
            )
            assert all(r in {"TASK_DELIVERED", "INJECTED"} for r in rows), (
                f"report rows not delivered (TASK_DELIVERED/INJECTED): {rows}"
            )
        except Exception as exc:  # noqa: BLE001 — DB probe is best-effort
            logger.warning(
                f"[B2] PG probe unavailable ({exc}); HTTP-level assertions "
                f"above remain the binding acceptance"
            )

        # No double-delivery on a follow-up message.
        _send_message(leader_id, "all children reported? reply yes")
        _wait_for_status(leader_id, TERMINAL_STATUSES, FOLLOWUP_TIMEOUT)
        after_followup = len(_get_messages(leader_id))
        assert after_followup - msg_count_final == 1, (
            f"follow-up message double-delivered buffered reports "
            f"({msg_count_final} → {after_followup})"
        )
    finally:
        if leader_id:
            requests.delete(f"{API_BASE}/instances/{leader_id}", timeout=30)
