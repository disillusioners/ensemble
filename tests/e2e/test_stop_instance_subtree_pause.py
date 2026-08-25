"""E2E test for ``/stop`` subtree-pause semantics.

Phase 3 (pause-resume-terminate-tree-fix) — Task 3.3 / B5 acceptance.
The plan's §B5 E2E spec is reproduced here:

  Build a tree: leader → tester → worker.
  Trigger worker ``sleep 60`` (long enough to observe).
  ``POST /instances/{tester}/stop`` → confirm only ``[tester, worker]`` paused
    (leader running).
  Wait 5s, assert leader still polling / has NOT entered paused state.
  ``POST /instances/{leader}/pause`` (not /stop) → confirm whole tree pauses
    (``paused_ids == [leader, tester, worker]``).
  This proves BOTH /stop subtree semantics AND /pause whole-tree semantics
  unchanged.

NOTE — DO NOT EXECUTE. This file is authored per the task contract
``authoring only; must be collection-clean (import-syntax valid)``. The
LLM at ``localhost:4123`` is flaky and the e2e suite is tester-gated;
the dispatcher routes e2e execution through the ``tester`` agent's
``RESULTS`` journal. The ``pytest.mark.skipif`` decorator at module
level skips when the daemon is not running on ``localhost:8079``, but
collection itself must succeed in CI / local quick-runs.

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
subtree, and a subsequent ``/pause`` on the ancestor pauses the
already-paused subtree uniformly (no double-cascade surprise).

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
    "ask tester to investigate something; tester must spawn 1 developer "
    "child that runs a bash sleep of 60 seconds before reporting; do not "
    "finish until tester and its child have fully reported back"
)


def _wait_for_grandchild(parent_id: str, timeout: int) -> str | None:
    """Wait for ``parent_id`` to have at least one direct child.

    Mirrors the B3 E2E pattern (``_direct_children`` +
    ``_wait_for_child_spawned``); tester spawns the worker mid-turn.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(
                f"{API_BASE}/instances",
                params={"parent_id": parent_id},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            items = data if isinstance(data, list) else data.get("instances", [])
            children = [
                i.get("instance_id") for i in items if i.get("instance_id")
            ]
            if children:
                return children[0]
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
    *   ``POST /instances/{leader}/pause`` →
        ``paused_ids == [leader, tester, worker]`` (whole tree pauses
        uniformly, including the already-paused subtree).

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

        worker_id = _wait_for_grandchild(tester_id, timeout=SPAWN_TIMEOUT)
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

        # ── /pause leader → whole tree pauses uniformly ──────────
        pause_result = _pause_instance(leader_id)
        assert pause_result.get("paused") is True
        pause_paused_ids = set(pause_result.get("paused_ids", []))
        # /pause cascades the whole tree: leader + tester + worker all
        # end up in paused_ids. The cascade classifies already-paused
        # subtree nodes into ``paused_ids`` (the helper writes
        # ``status=paused`` regardless of starting state, but the
        # classification loop's filter passes them through because
        # they're not in a TERMINAL status yet).
        assert pause_paused_ids == {leader_id, tester_id, worker_id}, (
            "/pause must pause the whole tree uniformly; got "
            f"paused_ids={pause_paused_ids}, expected "
            f"{{ {leader_id[:8]}, {tester_id[:8]}, {worker_id[:8]} }}"
        )

        # Wait briefly for the leader's transition to settle on disk.
        ok, final = _wait_for_status(
            leader_id, {"paused"}, timeout=COMPLETION_TIMEOUT
        )
        assert ok, (
            f"leader never reached paused status after /pause "
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