"""E2E test for ``/stop`` subtree-pause semantics.

Phase 3 (pause-resume-terminate-tree-fix) — Task 3.3 / B5 acceptance.
The plan's §B5 E2E spec is reproduced here:

  Build a tree: leader → tester → worker.
  Trigger worker ``sleep 60`` (long enough to observe).
  ``POST /instances/{tester}/stop`` → confirm only ``[tester, worker]`` paused
    (leader running).
  Wait 5s, assert leader still polling / has NOT entered paused state.
  ``POST /instances/{leader}/pause`` (not /stop) → confirm the whole tree
    ENDS UP paused. NOTE (B1 correction): the plan's literal sentence
    expected ``paused_ids == [leader, tester, worker]``, but the pause
    cascade's skip predicate (``instance_lifecycle.py:2450-2456``)
    classifies ALREADY-PAUSED nodes into ``skipped_ids`` — not only
    terminal ones. The actual response contract is
    ``paused_ids == [leader]`` + ``skipped_ids == [tester, worker]``,
    with whole-tree-paused verified as the END state via
    ``_wait_for_status``. Pinned by unit case 4 in
    ``tests/unit/routers/test_stop_instance_subtree.py``.
  This proves BOTH /stop subtree semantics AND /pause whole-tree semantics
  unchanged.

This file is executed live as the B5 acceptance (merge gate for
feature/pause-resume-terminate-tree-fix — its sanctioned first
execution). The ``pytest.mark.skipif`` decorator at module level skips
when the daemon is not running on ``localhost:8079``; collection
itself must also succeed in CI / local quick-runs.

Why both `/stop` and `/pause` are exercised
--------------------------------------------

The plan's B5 acceptance sentence pins two distinct contracts:

  * ``POST /api/instances/{mid}/stop`` pauses the subtree rooted at
    ``mid`` (NOT the project root) — the new B5 fix.
  * ``POST /api/instances/{root}/pause`` keeps the long-standing
    whole-tree semantics — the contract 5 internal callers
    (``instance_messaging.py:1119, :3748``, ``watchover_service.py:1004,
    :1470``, manager facade ``manager.py:7948``) rely on.

This e2e proves both contracts in one tree-shaped scenario. The
composition is the key acceptance: ``/stop`` only affects the target
subtree, and a subsequent ``/pause`` on the ancestor leaves the whole
tree paused — reporting the already-paused subtree in ``skipped_ids``
(no double-cascade surprise).

Run (live daemon on :8079, started via ``./dev.sh``)::

    pytest tests/e2e/test_stop_instance_subtree_pause.py -v -s
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
    PROJECT_ID,
    SPAWN_TIMEOUT,
    _daemon_running,
    _pause_instance,
    _send_message,
    _spawn_instance,
    _wait_for_child_spawned,
)

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _daemon_running(),
        reason="Daemon not running at localhost:8079 — start with ./dev.sh",
    ),
]


# ---------------------------------------------------------------------------
# Local HTTP helpers — `/stop` is not exercised by sibling E2Es because the
# B5 fix is new. Mirror the `_pause_instance` style from test_e2e_workflows.
# ---------------------------------------------------------------------------


def _stop_instance(instance_id: str) -> dict:
    """POST ``/api/instances/{id}/stop`` and return the response body.

    Returns:
        Dict with at least ``paused`` (bool), ``paused_ids`` (list),
        and ``skipped_ids`` (list) — same shape as ``/pause`` per the
    ``stop_instance_deprecated` handler (B5 fix; phase3-plan §B5).

    Raises:
        requests.HTTPError: On non-2xx response.
    """
    logger.info(f"[STOP] {instance_id[:8]}...")
    response = requests.post(
        f"{API_BASE}/instances/{instance_id}/stop",
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _status(instance_id: str) -> str:
    """Return the current ``status`` field for ``instance_id``."""
    response = requests.get(f"{API_BASE}/instances/{instance_id}", timeout=30)
    response.raise_for_status()
    return response.json().get("status", "")


def _wait_for_status(
    instance_id: str, expected: set[str], timeout: int
) -> tuple[bool, str]:
    """Poll ``instance_id`` until its status is in ``expected``.

    Returns ``(reached, last_status)`` — ``reached`` is ``True`` if a
    status in ``expected`` was observed before ``timeout``.
    """
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        last = _status(instance_id)
        if last in expected:
            return True, last
        time.sleep(POLL_INTERVAL)
    return False, last


# ---------------------------------------------------------------------------
# Tree message — leader delegates to tester; tester spawns a worker that
# sleeps 60s. The sleep is long enough that we can observe the worker's
# status mid-flight when /stop lands.
# ---------------------------------------------------------------------------

TREE_MESSAGE = (
    "ask tester to investigate something; tester must spawn 1 worker "
    "child that runs a bash sleep of 60 seconds before reporting; do not "
    "finish until tester and its child have fully reported back"
)

# Grandchild spawn-wait raised 60s -> 120s (LLM failover tax compensation,
# primary down) — mirrors B2's sanctioned raise; same one-defect rationale.
B5_GRANDCHILD_TIMEOUT = 120


def _wait_for_grandchild(parent_id: str, timeout: int) -> str | None:
    """Wait for ``parent_id`` to have at least one direct child.

    ``GET /api/instances`` has no ``parent_id`` filter (the param is
    silently ignored and a flat root-based page is returned), so poll the
    parent's own ``children`` field — mirroring ``_wait_for_child_spawned``
    — and guard the resolved id against a ``parent_id`` mismatch before
    using it.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(
                f"{API_BASE}/instances/{parent_id}", timeout=30
            )
            response.raise_for_status()
            children = response.json().get("children", []) or []
            if children:
                child_id = children[0]
                detail = requests.get(
                    f"{API_BASE}/instances/{child_id}", timeout=30
                )
                detail.raise_for_status()
                if detail.json().get("parent_id") == parent_id:
                    return child_id
                logger.warning(
                    f"[WAIT_GRANDCHILD] {child_id[:8]}... parent_id mismatch "
                    f"(expected parent {parent_id[:8]}...); retrying"
                )
        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"[WAIT_GRANDCHILD] GET failed (will retry): {exc}"
            )
        time.sleep(POLL_INTERVAL)
    return None


# ---------------------------------------------------------------------------
# B5 acceptance — /stop subtree semantics + /pause whole-tree semantics
# ---------------------------------------------------------------------------


def test_stop_pauses_target_subtree_then_pause_pauses_whole_tree():
    """Phase 3 / B5 acceptance (live daemon).

    Sequence:

    *   Spawn leader, send TREE_MESSAGE (leader → tester → worker(sleep 60)).
    *   Wait for tester to spawn.
    *   Wait for tester to spawn its worker (the sleep-60 grandchild).
    *   ``POST /instances/{tester}/stop`` →
        ``paused_ids == [tester, worker]`` (NOT the leader).
    *   Wait 5s and assert leader has NOT paused
        (``status not in {paused, ...}``).
    *   ``POST /instances/{leader}/pause`` → response reports
        ``paused_ids == [leader]`` and ``skipped_ids ==
        [tester, worker]`` — the cascade's skip predicate classifies
        already-PAUSED nodes as skipped (not only terminal ones);
        the END state is still whole-tree paused, asserted via
        ``_wait_for_status`` on all three nodes.

    The composition is the load-bearing acceptance — the
    ``cascade_to_root`` kwarg (subtree vs whole tree) does not regress
    existing pause/resume state on the target subtree.
    """
    leader_id: str | None = None
    try:
        # ── Setup: leader → tester → worker(sleep 60) ──────────
        leader_id = _spawn_instance("leader", PROJECT_ID)
        assert leader_id
        _send_message(leader_id, TREE_MESSAGE)

        tester_id = _wait_for_child_spawned(leader_id, timeout=SPAWN_TIMEOUT)
        assert tester_id, "leader did not spawn a tester child in time"

        worker_id = _wait_for_grandchild(tester_id, timeout=B5_GRANDCHILD_TIMEOUT)
        assert worker_id, "tester did not spawn the sleeping worker in time"
        logger.info(
            f"[B5] tree ready: leader={leader_id[:8]} "
            f"tester={tester_id[:8]} worker={worker_id[:8]}"
        )

        # ── /stop tester → subtree only pauses (B5 fix) ──────────
        stop_result = _stop_instance(tester_id)
        assert stop_result.get("paused") is True
        paused_ids = set(stop_result.get("paused_ids", []))
        skipped_ids = set(stop_result.get("skipped_ids", []))

        # /stop pauses only [tester, worker] — NOT the leader.
        assert tester_id in paused_ids, (
            f"B5 /stop missed target: paused_ids={paused_ids} "
            f"should contain tester={tester_id[:8]}"
        )
        assert worker_id in paused_ids, (
            f"B5 /stop missed subtree: paused_ids={paused_ids} "
            f"should contain worker={worker_id[:8]}"
        )
        assert leader_id not in paused_ids, (
            f"B5 REGRESSION: /stop re-rooted to the project root "
            f"(paused_ids={paused_ids} unexpectedly contains "
            f"leader={leader_id[:8]}) — the B5 fix did not take effect"
        )
        # No skips on the target subtree (both were RUNNING).
        assert not (paused_ids & {tester_id, worker_id}) - paused_ids
        assert skipped_ids.isdisjoint({tester_id, worker_id}), (
            f"/stop should not skip fresh subtree nodes: skipped_ids={skipped_ids}"
        )

        # ── Wait 5s — leader must NOT have entered paused state ──
        time.sleep(5)
        leader_status = _status(leader_id)
        assert leader_status != "paused", (
            f"B5 REGRESSION: leader entered paused state after /stop on "
            f"tester (status={leader_status}); /stop must only affect the "
            f"target subtree"
        )
        assert leader_status in {"running", "waiting_children", "waiting"}, (
            f"Leader status unexpected after /stop: {leader_status}"
        )

        # ── /pause leader → response: leader paused, subtree skipped ──
        pause_result = _pause_instance(leader_id)
        assert pause_result.get("paused") is True
        pause_paused_ids = set(pause_result.get("paused_ids", []))
        pause_skipped_ids = set(pause_result.get("skipped_ids", []))
        # /pause cascades the whole tree, but the cascade's skip
        # predicate (instance_lifecycle.py:2450-2456) classifies
        # ALREADY-PAUSED nodes into ``skipped_ids`` — not only terminal
        # ones. tester+worker were paused by the preceding /stop, so
        # the response reports them as skipped and only the leader as
        # newly paused. Pinned by unit case 4
        # (tests/unit/routers/test_stop_instance_subtree.py::
        # test_case4_stop_already_paused_returns_all_skipped) and the
        # COMPOSITION case in the same file.
        assert pause_paused_ids == {leader_id}, (
            "B1: /pause after /stop must report only the leader as "
            "newly paused; got "
            f"paused_ids={pause_paused_ids}, expected "
            f"{{ {leader_id[:8]} }}"
        )
        assert pause_skipped_ids == {tester_id, worker_id}, (
            "B1: already-paused subtree nodes must land in "
            f"skipped_ids; got skipped_ids={pause_skipped_ids}, "
            f"expected {{ {tester_id[:8]}, {worker_id[:8]} }}"
        )

        # End-state IS whole-tree paused (the B5 contract): tester and
        # worker were paused by /stop and stay paused; the leader just
        # transitioned. Settle all three via the file's poll helper.
        for node_id, role in (
            (leader_id, "leader"),
            (tester_id, "tester"),
            (worker_id, "worker"),
        ):
            settled, final = _wait_for_status(
                node_id, {"paused"}, timeout=COMPLETION_TIMEOUT
            )
            assert settled, (
                f"{role} never reached paused status after /pause "
                f"(last={final})"
            )

    finally:
        # Best-effort cleanup — terminate the leader so the worker
        # cascade terminates too. Non-fatal on failure (CI may have
        # already torn down).
        if leader_id:
            try:
                requests.delete(
                    f"{API_BASE}/instances/{leader_id}",
                    timeout=15,
                )
            except requests.exceptions.RequestException as exc:
                logger.warning(
                    f"[CLEANUP] terminate leader failed (non-fatal): {exc}"
                )