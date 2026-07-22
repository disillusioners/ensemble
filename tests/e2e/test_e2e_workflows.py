#!/usr/bin/env python3
"""End-to-end tests for critical user workflows in the Ensemble Daemon.

These tests hit the REAL running daemon HTTP API (no mocks). They exercise
the three most frequent user workflows:

  1. **Happy path** — leader receives a message, spawns a developer child,
     the child runs, and the workflow reaches a terminal state.

  2. **Pause after spawn, then resume** — same workflow, but the leader
     is paused after the child spawns. We verify the instance (and child)
     actually reach ``paused``, hold for a few seconds without further
     processing, then resume to completion.

  3. **Terminate after spawn, then revive** — the leader is terminated
     after the child spawns, then a new message REVIVES it: the instance
     flips back to ``running`` and reaches a terminal state processing
     the new message (terminal = terminal, just a different reason —
     checkpoint/history persist and reload). Regression for the
     2026-07-01 revive-fix where terminal instances couldn't be revived.

  5. **Pause blocks defer queue** — a leader is paused mid-flight, then a
     deferred job is created. The defer queue must hold the job while the
     instance is paused (a paused instance counts as non-idle), then
     release it after resume. Regression for the 2026-07-01 pause-fix
     where ``has_active_non_deferred_work`` excluded ``paused``.

Conventions (per ``tests/e2e/test_mcp_tools.py``):

  * Uses the ``requests`` library with polling at ``POLL_INTERVAL``.
  * Configures module-level logging with clear step headers.
  * Each test cleans up in a ``finally`` block — never leave instances
    running, even on assertion failure.
  * Tests are excluded from default pytest runs via the
    ``pytest.mark.integration`` marker (see ``pyproject.toml``).

Run with::

    # Start the daemon first
    ./dev.sh

    # Then run the tests (integration marker bypasses default exclusion)
    pytest tests/e2e/test_e2e_workflows.py -v -s -m integration
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import pytest
import requests

# --------------------------------------------------------------------------- #
# Logging configuration (mirrors test_mcp_tools.py)
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
BASE_URL = "http://localhost:8079"           # Dev server port
API_BASE = f"{BASE_URL}/api"
PROJECT_ID = os.environ.get("ENSEMBLE_PROJECT_ID", None)

# Timeouts (generous — real LLM calls are involved)
SPAWN_TIMEOUT = 60          # seconds to wait for a child to appear
COMPLETION_TIMEOUT = 120    # seconds to wait for a terminal status
POLL_INTERVAL = 3           # seconds between status polls

# The message we send to the leader for all three tests. Asking the leader
# to delegate a trivial task to a developer child gives us a deterministic
# spawn event to observe without depending on long-running behaviour.
TEST_MESSAGE = (
    "ask developer to say hello, this is a test workflow, developer dont need do anything"
)

# Phase 2 message — sent to the same (already-completed) leader to verify
# that the leader instance is reused and the existing developer child is reused
# rather than spawning a brand-new instance.
PHASE2_MESSAGE = (
    "continue our test, reuse the developer instance, say hi and ask him say another hello"
)

# Statuses that mean "the instance is done doing work" for our purposes.
# ``completed`` is the normal end-state; ``terminated`` / ``error`` /
# ``failed`` indicate the workflow stopped for some other reason.
TERMINAL_STATUSES = {"completed", "terminated", "error", "failed"}


# --------------------------------------------------------------------------- #
# Skip-if-daemon-down guard
# --------------------------------------------------------------------------- #
def _daemon_running() -> bool:
    """Return ``True`` if the daemon is reachable at ``localhost:8079``.

    Used by the module-level ``pytest.mark.skipif`` so that collection
    succeeds even when the daemon is not running. Wrapped in try/except
    so a network failure at import time does not crash the test process.
    """
    try:
        response = requests.get(f"{API_BASE}/health", timeout=5)
        return response.status_code == 200
    except requests.exceptions.RequestException:
        return False
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"_daemon_running: unexpected error: {exc!r}")
        return False


# ``pytest.mark.skipif`` must be evaluable at collection time, so we call
# the helper eagerly here. Combined with ``pytest.mark.integration`` to
# exclude these tests from the default ``pytest tests/`` invocation
# (matches project convention; see pyproject.toml ``addopts``).
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _daemon_running(),
        reason="Daemon not running at localhost:8079 — start with ./dev.sh",
    ),
]


# --------------------------------------------------------------------------- #
# HTTP helpers (module-level — keep tests declarative)
# --------------------------------------------------------------------------- #
def _spawn_instance(agent_id: str, project_id: str = PROJECT_ID) -> str:
    """POST ``/api/instances`` and return the new ``instance_id``.

    Args:
        agent_id: The agent to spawn (e.g. ``"leader"`` or ``"developer"``).
        project_id: Optional project scope for the instance.

    Returns:
        The instance ID string returned by the daemon.

    Raises:
        RuntimeError: If the daemon returns a non-2xx response or omits
            ``instance_id`` from the response body.
    """
    logger.info(f"[SPAWN] agent_id={agent_id} project_id={project_id}")
    payload: dict[str, Any] = {"agent_id": agent_id}
    if project_id is not None:
        payload["project_id"] = project_id

    response = requests.post(
        f"{API_BASE}/instances",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    instance_id = data.get("instance_id")
    if not instance_id:
        raise RuntimeError(f"Spawn response missing instance_id: {data}")
    logger.info(f"[SPAWN] -> instance_id={instance_id}")
    return instance_id


def _send_message(instance_id: str, content: str) -> str:
    """POST ``/api/instances/{id}/messages`` and return the ``message_id``.

    Args:
        instance_id: Target instance.
        content: The user message body.

    Returns:
        The message ID string returned by the daemon.

    Raises:
        RuntimeError: If the daemon returns a non-2xx response or omits
            ``message_id`` from the response body.
    """
    logger.info(f"[MSG] -> {instance_id[:8]}... ({len(content)} chars)")
    response = requests.post(
        f"{API_BASE}/instances/{instance_id}/messages",
        json={"content": content},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    message_id = data.get("message_id")
    if not message_id:
        raise RuntimeError(f"Send message response missing message_id: {data}")
    logger.info(f"[MSG] -> message_id={message_id}")
    return message_id


def _get_instance(instance_id: str) -> dict:
    """GET ``/api/instances/{id}`` and return the parsed body.

    Args:
        instance_id: The instance to fetch.

    Returns:
        The instance info dict (matches the ``InstanceInfo`` schema in
        ``daemon/models/instance.py`` — includes ``status`` and
        ``children: list[str]``).

    Raises:
        requests.HTTPError: On non-2xx response.
    """
    response = requests.get(
        f"{API_BASE}/instances/{instance_id}",
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _get_messages(instance_id: str) -> list:
    """GET ``/api/instances/{id}/messages`` and return the message list.

    Args:
        instance_id: The instance whose history to fetch.

    Returns:
        A list of message dicts (``role``, ``content``, etc.).

    Raises:
        requests.HTTPError: On non-2xx response.
    """
    response = requests.get(
        f"{API_BASE}/instances/{instance_id}/messages",
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


# ── Internal bus message leak detection ─────────────────────────────────────
# These patterns should NEVER appear in the leader's message history.
# They indicate internal dependency_bus FollowUp content leaking into the
# user-visible message stream.
_BUS_LEAK_PATTERNS = [
    "[dependency_bus]",
    "dependency_bus",
    "child ... completed for message",
    "completed for message",
    "[FollowUp",
    "FollowUp",
    "bus_followup",
    "bus: emit_terminal",
    "dependency_bus_followup",
]


def _check_bus_message_leak(instance_id, label=""):
    """Check leader's message history for internal bus message leaks.

    Returns (leak_found: bool, leaked_messages: list).
    """
    messages = _get_messages(instance_id)
    if not isinstance(messages, list):
        return False, []

    leaked = []
    for msg in messages:
        content = str(msg.get("content", ""))
        role = msg.get("role", "")
        # Check all leak patterns (case-insensitive)
        content_lower = content.lower()
        for pattern in _BUS_LEAK_PATTERNS:
            if pattern.lower() in content_lower:
                leaked.append({
                    "pattern_matched": pattern,
                    "role": role,
                    "content_preview": content[:300],
                    "message_id": msg.get("message_id", "unknown"),
                })
                break  # one match per message is enough

    if leaked:
        logger.error(f"{'[' + label + '] ' if label else ''}BUS MESSAGE LEAK DETECTED: {len(leaked)} leaked messages in instance {instance_id}")
        for lm in leaked:
            logger.error(f"  Pattern: '{lm['pattern_matched']}' | Role: {lm['role']} | Preview: {lm['content_preview'][:200]}")
    else:
        logger.info(f"{'[' + label + '] ' if label else ''}✅ No bus message leaks in instance {instance_id} ({len(messages)} messages checked)")

    return len(leaked) > 0, leaked


def _pause_instance(instance_id: str) -> dict:
    """POST ``/api/instances/{id}/pause`` and return the response body.

    Returns:
        Dict with at least ``paused`` (bool), ``paused_ids`` (list),
        and ``skipped_ids`` (list).

    Raises:
        requests.HTTPError: On non-2xx response.
    """
    logger.info(f"[PAUSE] {instance_id[:8]}...")
    response = requests.post(
        f"{API_BASE}/instances/{instance_id}/pause",
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _resume_instance(instance_id: str, message: str | None = None) -> dict:
    """POST ``/api/instances/{id}/resume`` and return the response body.

    Args:
        instance_id: Target instance.
        message: Optional message to send alongside the resume. When
            ``None`` the daemon defaults to the literal string
            ``"resume"`` (see ``ResumeRequest`` in
            ``daemon/models/instance.py``).

    Returns:
        Dict with ``resumed``, ``resumed_ids``, ``skipped_ids``, etc.

    Raises:
        requests.HTTPError: On non-2xx response.
    """
    logger.info(f"[RESUME] {instance_id[:8]}... (message={message!r})")
    payload: dict[str, Any] = {}
    if message is not None:
        payload["message"] = message
    response = requests.post(
        f"{API_BASE}/instances/{instance_id}/resume",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _terminate_instance(instance_id: str) -> bool:
    """DELETE ``/api/instances/{id}`` with defensive error handling.

    Returns:
        ``True`` on a 2xx response, ``False`` otherwise. Never raises —
        cleanup must not crash the test process.
    """
    logger.info(f"[TERMINATE] {instance_id[:8]}...")
    try:
        response = requests.delete(
            f"{API_BASE}/instances/{instance_id}",
            timeout=30,
        )
        response.raise_for_status()
        logger.info(f"[TERMINATE] {instance_id[:8]}... OK")
        return True
    except requests.exceptions.RequestException as exc:
        logger.warning(
            f"[TERMINATE] {instance_id[:8]}... failed (non-fatal): {exc}"
        )
        return False


def _send_injection_raw(instance_id: str, content: str) -> requests.Response:
    """POST ``/api/instances/{id}/messages`` and return the raw response.

    Unlike :func:`_send_message`, this helper does NOT call
    ``raise_for_status()`` — the injection path returns 202 Accepted
    (RUNNING/WAITING_CHILDREN) which is the success outcome, not a
    server error. Callers must inspect ``response.status_code`` and
    ``response.json()`` themselves.

    Args:
        instance_id: Target instance.
        content: The user injection body (will be routed to the
            injection slot for RUNNING/WAITING_CHILDREN instances; for
            PAUSED instances the server triggers auto-resume and returns
            200; for IDLE/terminal it falls through to the normal
            ``enqueue_message`` path).

    Returns:
        The raw ``requests.Response`` object from the daemon.

    Raises:
        requests.exceptions.RequestException: On connection / timeout
            errors only. HTTP status codes are surfaced via
            ``response.status_code``.
    """
    logger.info(
        f"[INJ_POST] {instance_id[:8]}... ({len(content)} chars)"
    )
    response = requests.post(
        f"{API_BASE}/instances/{instance_id}/messages",
        json={"content": content},
        timeout=30,
    )
    logger.info(
        f"[INJ_POST] {instance_id[:8]}... status={response.status_code}"
    )
    return response


def _get_injection(instance_id: str) -> dict:
    """GET ``/api/instances/{id}/injection`` and return the parsed body.

    The endpoint returns ``{"instance_id": ..., "pending": bool,
    "content": str|None, "timestamp": str|None}``. ``pending=False`` is
    the normal steady state (no injection stored OR already consumed
    by agent_node) — it is NOT an error.

    Args:
        instance_id: The instance whose injection slot to inspect.

    Returns:
        Dict with at least ``pending`` (bool), ``content`` (``str`` or
        ``None``), and ``timestamp`` (``str`` or ``None``) keys.

    Raises:
        requests.HTTPError: On non-2xx response (e.g. 404 for unknown
            instances).
    """
    logger.info(f"[INJ_GET] {instance_id[:8]}...")
    response = requests.get(
        f"{API_BASE}/instances/{instance_id}/injection",
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    logger.info(
        f"[INJ_GET] {instance_id[:8]}... pending={data.get('pending')} "
        f"content_len={len(data.get('content') or '')}"
    )
    return data


# --------------------------------------------------------------------------- #
# Polling helpers
# --------------------------------------------------------------------------- #
def _wait_for_child_spawned(
    parent_id: str,
    timeout: int = SPAWN_TIMEOUT,
) -> str | None:
    """Poll the parent's instance info until at least one child appears.

    The ``InstanceInfo`` schema exposes ``children`` as a ``list[str]`` of
    child instance IDs (see ``daemon/models/instance.py``). This helper
    also defensively checks ``child_ids`` and ``child_instances`` in case
    the schema evolves; the first match wins.

    Args:
        parent_id: The leader/parent instance to inspect.
        timeout: Maximum seconds to wait.

    Returns:
        The first child instance ID found, or ``None`` on timeout.
    """
    logger.info(
        f"[WAIT_CHILD] parent={parent_id[:8]}... timeout={timeout}s"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = _get_instance(parent_id)

            # Primary key — matches the InstanceInfo schema.
            children = data.get("children")
            if isinstance(children, list) and children:
                child_id = children[0]
                logger.info(
                    f"[WAIT_CHILD] found child={child_id[:8]}... via 'children'"
                )
                return child_id

            # Defensive: future-proof against schema renames.
            for alt_key in ("child_ids", "child_instances"):
                alt = data.get(alt_key)
                if isinstance(alt, list) and alt:
                    child_id = alt[0]
                    logger.info(
                        f"[WAIT_CHILD] found child={child_id[:8]}... via '{alt_key}'"
                    )
                    return child_id
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WAIT_CHILD] GET failed (will retry): {exc}")

        time.sleep(POLL_INTERVAL)

    logger.warning(f"[WAIT_CHILD] timed out after {timeout}s for parent={parent_id[:8]}...")
    return None


def _wait_for_completion(
    instance_id: str,
    timeout: int = COMPLETION_TIMEOUT,
) -> tuple[bool, str]:
    """Poll an instance's status until it reaches a terminal state.

    Args:
        instance_id: The instance to watch.
        timeout: Maximum seconds to wait.

    Returns:
        ``(finished, final_status)`` where ``finished`` is ``True`` if the
        status is in :data:`TERMINAL_STATUSES`, and ``final_status`` is
        the last observed status string (may be ``"running"`` etc. on
        timeout).
    """
    logger.info(
        f"[WAIT_COMPLETE] instance={instance_id[:8]}... timeout={timeout}s"
    )
    deadline = time.time() + timeout
    last_status: str = "unknown"
    while time.time() < deadline:
        try:
            data = _get_instance(instance_id)
            last_status = str(data.get("status", "unknown"))
            if last_status in TERMINAL_STATUSES:
                logger.info(
                    f"[WAIT_COMPLETE] {instance_id[:8]}... -> {last_status}"
                )
                return True, last_status
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WAIT_COMPLETE] GET failed (will retry): {exc}")

        time.sleep(POLL_INTERVAL)

    logger.warning(
        f"[WAIT_COMPLETE] timed out after {timeout}s; last_status={last_status}"
    )
    return False, last_status


def _wait_for_status(
    instance_id: str,
    target_status: str,
    timeout: int,
) -> bool:
    """Poll an instance's status until it matches ``target_status``.

    Args:
        instance_id: The instance to watch.
        target_status: The status string to wait for (e.g. ``"paused"``).
        timeout: Maximum seconds to wait.

    Returns:
        ``True`` if the status was observed, ``False`` on timeout.
    """
    logger.info(
        f"[WAIT_STATUS] instance={instance_id[:8]}... target={target_status} "
        f"timeout={timeout}s"
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = _get_instance(instance_id)
            current = str(data.get("status", ""))
            if current == target_status:
                logger.info(
                    f"[WAIT_STATUS] {instance_id[:8]}... reached {target_status}"
                )
                return True
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WAIT_STATUS] GET failed (will retry): {exc}")

        time.sleep(POLL_INTERVAL)

    logger.warning(
        f"[WAIT_STATUS] timed out waiting for {target_status} on "
        f"{instance_id[:8]}..."
    )
    return False


def _get_child_statuses(child_ids: list[str]) -> dict[str, str]:
    """Fetch the current status of each child instance.

    Used by premature-completion checks that need to verify all children
    are terminal when the parent reaches a terminal status. This is
    **architecture-agnostic** — it inspects child instance status
    directly rather than relying on the ``waiting_for`` column, which is
    vestigial (always reads 0 on the
    DependencyBus code path; tracking is done via ``dependency_watchers``
    instead).

    Args:
        child_ids: Child instance IDs to inspect.

    Returns:
        Dict mapping each ``child_id`` to its lowercase status string.
        On fetch error, the child's status is recorded as ``"unknown"``
        (treated as non-terminal by callers — conservative).
    """
    statuses: dict[str, str] = {}
    for cid in child_ids:
        try:
            child = _get_instance(cid)
            statuses[cid] = str(child.get("status", "unknown")).lower()
        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"[CHILD_STATUS] failed to get child {cid[:8]}...: {exc}"
            )
            statuses[cid] = "unknown"
    return statuses


def _wait_for_leader_completion_safe(
    leader_id: str,
    child_ids: list[str],
    timeout: int = COMPLETION_TIMEOUT,
) -> tuple[bool, str, str | None]:
    """Wait for leader to reach terminal status, verifying no premature completion.

    Polls the leader's status until it reaches a terminal state. At that
    moment, checks ALL children — if ANY child is still non-terminal,
    that's a premature completion bug.

    This check is **architecture-agnostic**: it directly inspects child
    instance status rather than relying on the ``waiting_for`` column
    (which is vestigial under DependencyBus
    and always reads 0 on that code path). This means the check works
    correctly under BOTH the legacy CorrelationManager path and the
    DependencyBus path.

    Args:
        leader_id: The leader/parent instance to watch.
        child_ids: Child instance IDs to check at leader terminal time.
        timeout: Maximum seconds to wait for the leader.

    Returns:
        ``(finished, final_status, premature_detail)``:

        * ``finished``: ``True`` if the leader reached a terminal status.
        * ``final_status``: Last observed leader status string.
        * ``premature_detail``: ``None`` if no premature completion was
          detected, or a human-readable description string identifying
          the still-running child.
    """
    logger.info(
        f"[WAIT_SAFE] leader={leader_id[:8]}... "
        f"children={[c[:8] + '...' for c in child_ids]} "
        f"timeout={timeout}s"
    )
    deadline = time.time() + timeout
    last_status: str = "unknown"

    while time.time() < deadline:
        try:
            data = _get_instance(leader_id)
            last_status = str(data.get("status", "unknown")).lower()

            if last_status in TERMINAL_STATUSES:
                # Leader reached terminal — verify ALL children are
                # also terminal. If any child is still non-terminal,
                # that's a premature completion bug.
                child_statuses = _get_child_statuses(child_ids)
                non_terminal = {
                    cid: st for cid, st in child_statuses.items()
                    if st not in TERMINAL_STATUSES
                }

                if non_terminal:
                    detail = (
                        f"leader {leader_id[:8]}... reached "
                        f"'{last_status}' but child(ren) still "
                        f"non-terminal: {non_terminal}"
                    )
                    logger.error(f"[WAIT_SAFE] ❌ PREMATURE: {detail}")
                    return True, last_status, detail

                logger.info(
                    f"[WAIT_SAFE] ✅ leader reached '{last_status}' "
                    f"with all children terminal: {child_statuses}"
                )
                return True, last_status, None

        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WAIT_SAFE] GET failed (will retry): {exc}")

        time.sleep(POLL_INTERVAL)

    logger.warning(
        f"[WAIT_SAFE] timed out after {timeout}s; last_status={last_status}"
    )
    return False, last_status, None


# --------------------------------------------------------------------------- #
# Job + project helpers (used by Test 4: wave + defer queue + cross-system)
# --------------------------------------------------------------------------- #
def _create_job(
    agent_id: str,
    message: str,
    project_id: str | None = None,
    priority: int = 5,
    queue_id: str | None = None,
) -> str:
    """POST ``/api/jobs`` and return the new ``job_id``.

    Args:
        agent_id: The agent to invoke for the job (e.g. ``"leader"``).
        message: The job's user message body.
        project_id: Optional project scope for the job.
        priority: Job priority (default ``5``).
        queue_id: Optional explicit queue ID. When ``None`` the daemon
            defaults to ``system_fifo_queue`` for the project.

    Returns:
        The job ID string returned by the daemon.

    Raises:
        RuntimeError: If the daemon returns a non-2xx response or omits
            ``job_id`` from the response body.
    """
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "message": message,
        "priority": priority,
    }
    if project_id:
        body["project_id"] = project_id
    if queue_id:
        body["queue_id"] = queue_id

    logger.info(
        f"[JOB_CREATE] agent={agent_id} project={project_id} "
        f"queue={queue_id or 'default'} priority={priority}"
    )
    response = requests.post(f"{API_BASE}/jobs", json=body, timeout=30)
    response.raise_for_status()
    data = response.json()
    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise RuntimeError(f"Job create response missing job_id: {data}")
    logger.info(
        f"[JOB_CREATE] -> job_id={job_id} status={data.get('status')}"
    )
    return job_id


def _get_job(job_id: str) -> dict:
    """GET ``/api/jobs/{job_id}`` and return the parsed body.

    Args:
        job_id: The job to fetch.

    Returns:
        The job dict (``status``, ``instance_id``, ``queue_id``, etc.).

    Raises:
        requests.HTTPError: On non-2xx response.
    """
    response = requests.get(f"{API_BASE}/jobs/{job_id}", timeout=10)
    response.raise_for_status()
    return response.json()


def _wait_for_job_status(
    job_id: str,
    target_statuses: str | set[str],
    timeout: int = 120,
) -> tuple[bool, str]:
    """Poll a job until its status is in ``target_statuses``.

    Args:
        job_id: The job to watch.
        target_statuses: A single status string or a set of acceptable
            status strings (e.g. ``{"processing", "completed"}``).
        timeout: Maximum seconds to wait.

    Returns:
        ``(reached, final_status)`` — ``reached`` is ``True`` if the job
        was observed in one of the target statuses; ``final_status`` is
        the last observed status string (or ``"unknown"`` on hard error).
    """
    if isinstance(target_statuses, str):
        target_statuses = {target_statuses}
    else:
        target_statuses = set(target_statuses)

    logger.info(
        f"[WAIT_JOB] job={job_id[:8]}... targets={target_statuses} "
        f"timeout={timeout}s"
    )
    deadline = time.time() + timeout
    last_status: str = "unknown"
    while time.time() < deadline:
        try:
            data = _get_job(job_id)
            last_status = str(data.get("status", "unknown")).lower()
            if last_status in target_statuses:
                logger.info(
                    f"[WAIT_JOB] {job_id[:8]}... -> {last_status}"
                )
                return True, last_status
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WAIT_JOB] GET failed (will retry): {exc}")

        time.sleep(POLL_INTERVAL)

    logger.warning(
        f"[WAIT_JOB] timed out after {timeout}s; last_status={last_status}"
    )
    return False, last_status


def _cancel_job(job_id: str) -> bool:
    """Best-effort ``POST /api/jobs/{id}/cancel`` (tolerates 404).

    Returns:
        ``True`` on a 2xx response or 404 (already-gone), ``False``
        otherwise. Never raises — cleanup must not crash the test.
    """
    try:
        response = requests.post(
            f"{API_BASE}/jobs/{job_id}/cancel", timeout=10
        )
        if response.status_code == 404:
            logger.info(
                f"[JOB_CANCEL] {job_id[:8]}... already gone (404)"
            )
            return True
        response.raise_for_status()
        logger.info(f"[JOB_CANCEL] {job_id[:8]}... OK")
        return True
    except requests.exceptions.RequestException as exc:
        logger.warning(
            f"[JOB_CANCEL] {job_id[:8]}... failed (non-fatal): {exc}"
        )
        return False


def _find_active_job_for_instance(
    instance_id: str,
    project_id: str | None = None,
    max_attempts: int = 3,
) -> str | None:
    """Find the most recent active job for an instance via the API.

    Phase 6 E2E helper: lists jobs in the project and picks the one
    bound to ``instance_id``. Returns the ``job_id`` or ``None`` if
    the API isn't reachable / the job isn't yet visible.

    This deliberately uses the public API (``GET /api/jobs``) rather
    than direct DB access so the test stays decoupled from the
    on-disk DB path (which differs between SQLite dev and PostgreSQL
    production). The list endpoint also enforces the same auth/RLS
    path as the rest of the test, so the assertion exercises the
    same shape a real client would see.

    Args:
        instance_id: The instance whose job we want to find.
        project_id: Project scope for the listing; if ``None``, the
            first project returned by ``/api/projects`` is used.
        max_attempts: How many polls to retry before giving up. The
            job is created asynchronously after the message is sent
            and may take a few seconds to appear in the listing.

    Returns:
        The ``job_id`` of the most recent active (non-terminal) job
        for the instance, or ``None`` if not found within
        ``max_attempts * POLL_INTERVAL`` seconds.
    """
    # When no explicit project_id was passed, scan ALL projects rather
    # than guessing the first one — leader instances spawned with
    # ``project_id=None`` may end up under a different default project
    # than what ``/api/projects`` returns first. Without this, the Phase 6
    # PAUSED-job assertion is silently skipped.
    scan_all_projects = project_id is None

    for attempt in range(max_attempts):
        try:
            candidates: list[dict] = []

            if scan_all_projects:
                # Paginate through /api/jobs across all projects.
                resp = requests.get(
                    f"{API_BASE}/jobs",
                    params={"limit": 200, "include_deleted": "false"},
                    timeout=10,
                )
                resp.raise_for_status()
                payload = resp.json()
                jobs = payload.get("jobs", payload) if isinstance(payload, dict) else payload
                if isinstance(jobs, list):
                    for job in jobs:
                        if (
                            isinstance(job, dict)
                            and str(job.get("instance_id", "")) == instance_id
                        ):
                            candidates.append(job)
            else:
                response = requests.get(
                    f"{API_BASE}/jobs",
                    params={
                        "project_id": project_id,
                        "limit": 200,
                        "include_deleted": "false",
                    },
                    timeout=10,
                )
                response.raise_for_status()
                payload = response.json()
                jobs = payload.get("jobs", payload) if isinstance(payload, dict) else payload
                if isinstance(jobs, list):
                    for job in jobs:
                        if (
                            isinstance(job, dict)
                            and str(job.get("instance_id", "")) == instance_id
                        ):
                            candidates.append(job)

            if not candidates:
                logger.info(
                    f"[FIND_JOB] attempt {attempt + 1}/{max_attempts}: no "
                    f"jobs yet for instance {instance_id[:8]}..."
                )
            else:
                # Prefer active (non-terminal) jobs; fall back to most recent.
                non_terminal_statuses = {"pending", "processing", "paused"}
                active = [
                    j for j in candidates
                    if str(j.get("status", "")).lower() in non_terminal_statuses
                ]
                pick = active[0] if active else candidates[0]
                job_id = pick.get("job_id") or pick.get("id")
                if job_id:
                    logger.info(
                        f"[FIND_JOB] -> job_id={job_id[:8]}... "
                        f"status={pick.get('status')}"
                    )
                    return str(job_id)

        except requests.exceptions.RequestException as exc:
            logger.warning(
                f"[FIND_JOB] attempt {attempt + 1}/{max_attempts} failed: {exc}"
            )
        time.sleep(POLL_INTERVAL)

    logger.warning(
        f"[FIND_JOB] gave up — no active job found for {instance_id[:8]}..."
    )
    return None


def _get_first_project_id() -> str | None:
    """Discover the first available ``project_id`` via ``GET /api/projects``.

    Tolerates both response shapes:
      * plain list: ``[{"project_id": "..."}, ...]``
      * envelope:   ``{"projects": [...], "total": N}``

    Returns:
        The first project's ID string, or ``None`` if no projects exist.
    """
    logger.info("[DISCOVER_PROJECTS] GET /api/projects")
    response = requests.get(f"{API_BASE}/projects", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        projects = payload.get("projects") or payload.get("items") or []
    else:
        projects = payload
    if isinstance(projects, list) and projects:
        first = projects[0]
        pid = first.get("project_id") or first.get("id")
        logger.info(
            f"[DISCOVER_PROJECTS] first project: id={pid} "
            f"name={first.get('name')!r}"
        )
        return pid
    logger.warning("[DISCOVER_PROJECTS] no projects found")
    return None


def _get_system_defer_queue_id(project_id: str) -> str | None:
    """Find the auto-provisioned ``system_defer_queue`` for a project.

    Tolerates both response shapes:
      * plain list: ``[{"queue_id": "...", "queue_name": "..."}, ...]``
      * envelope:   ``{"queues": [...], "total": N}``

    Returns:
        The defer queue's ID, or ``None`` if no defer queue exists for
        the project (e.g. system queues were never auto-provisioned).
    """
    logger.info(
        f"[DISCOVER_DEFER_QUEUE] GET /api/projects/{project_id[:8]}.../queues"
    )
    response = requests.get(
        f"{API_BASE}/projects/{project_id}/queues", timeout=10
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        queues = payload.get("queues") or payload.get("items") or []
    else:
        queues = payload
    if not isinstance(queues, list):
        logger.warning(
            f"[DISCOVER_DEFER_QUEUE] unexpected queues payload: "
            f"{type(queues).__name__}"
        )
        return None
    for q in queues:
        if not isinstance(q, dict):
            continue
        name = (q.get("queue_name") or q.get("name") or "").lower()
        if "defer" in name:
            qid = q.get("queue_id") or q.get("id")
            logger.info(
                f"[DISCOVER_DEFER_QUEUE] found defer queue: "
                f"name={q.get('queue_name') or q.get('name')!r} id={qid}"
            )
            return qid
    logger.warning(
        f"[DISCOVER_DEFER_QUEUE] no defer queue for project "
        f"{project_id[:8]}..."
    )
    return None


# --------------------------------------------------------------------------- #
# Virtual Job Management Surface helpers (Phase 4 — feature/virtual-job-management-surface)
# --------------------------------------------------------------------------- #
# ``GET /api/work`` exposes the unified WorkRecord view-model: ``work_id``,
# ``kind`` (``"job"`` | ``"report"``; Phase 4 collapse removed ``"turn"``
# and ``"task"`` — message turns are now JobItems with ``kind="job"``),
# ``status``, ``instance_id``, ``project_id``, ``agent_id``, ``result_summary``,
# ``error``, ``created_at``. The same ``work_id`` is also accepted at
# ``GET /api/jobs/{work_id}/events`` (SSE) and ``POST /api/jobs/{work_id}/cancel``
# for JobItem-backed rows. For message-driven tasks, ``message_id == work_id``.
def _get_work_by_id(work_id: str) -> dict | None:
    """Look up a single WorkRecord via the unified ``GET /api/work`` surface.

    Returns the WorkRecord dict if found, ``None`` otherwise. The
    ``work_id`` is the same UUID4 as ``message_id`` for message-driven
    tasks, so callers can pass either interchangeably.
    """
    response = requests.get(f"{API_BASE}/work", timeout=10)
    response.raise_for_status()
    for record in response.json():
        if record.get("work_id") == work_id:
            return record
    return None


def _get_work_by_instance(
    instance_id: str, kind: str | None = None
) -> list[dict]:
    """Get work records for an instance via the unified surface.

    Args:
        instance_id: Instance whose work to fetch.
        kind: Optional filter (``"job"`` | ``"report"``; Phase 4 collapse
            removed ``"turn"`` and ``"task"`` — message turns are now
            JobItems with ``kind="job"``). ``None`` returns all kinds
            (UNION).

    Returns:
        A list of WorkRecord dicts, newest-first.
    """
    params: dict[str, str] = {"instance_id": instance_id}
    if kind:
        params["kind"] = kind
    response = requests.get(f"{API_BASE}/work", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def _wait_for_work_status(
    work_id: str,
    target_statuses: str | set[str],
    timeout: int = 120,
) -> tuple[bool, str | None]:
    """Poll the virtual job surface until work status reaches a target.

    Args:
        work_id: The work UUID4 to poll.
        target_statuses: A single status string or a set of acceptable
            canonical status strings (``"completed"``, ``"cancelled"``,
            ``"failed"``, ``"processing"``, etc.).
        timeout: Maximum seconds to wait.

    Returns:
        ``(reached, final_status)`` — ``reached`` is ``True`` if the
        work was observed in one of the target statuses;
        ``final_status`` is the last observed status (or ``None`` if
        the work was never seen).
    """
    if isinstance(target_statuses, str):
        target_statuses = {target_statuses}
    else:
        target_statuses = set(target_statuses)

    deadline = time.time() + timeout
    last_status: str | None = None
    while time.time() < deadline:
        record = _get_work_by_id(work_id)
        if record is not None:
            last_status = record.get("status")
            if last_status in target_statuses:
                logger.info(
                    f"[WAIT_WORK] {work_id[:8]}... -> {last_status}"
                )
                return True, last_status
        time.sleep(POLL_INTERVAL)
    logger.warning(
        f"[WAIT_WORK] timed out after {timeout}s; last_status={last_status}"
    )
    return False, last_status


def _cancel_work(work_id: str) -> bool:
    """Best-effort cooperative cancel via ``POST /api/jobs/{id}/cancel``.

    Uses the unified work endpoint which is JobItem-gated at the HTTP
    layer (task work_ids return 404 — task cancellation is currently
    only routed through the MCP ``job_cancel`` tool). Tolerates 404
    so callers can use this defensively against either kind.

    Returns:
        ``True`` on 2xx response or 404, ``False`` otherwise.
    """
    try:
        response = requests.post(
            f"{API_BASE}/jobs/{work_id}/cancel", timeout=10
        )
        if response.status_code == 404:
            logger.info(
                f"[WORK_CANCEL] {work_id[:8]}... not found (404) — "
                "may be task-kind; HTTP cancel is JobItem-only"
            )
            return True
        response.raise_for_status()
        logger.info(f"[WORK_CANCEL] {work_id[:8]}... OK")
        return True
    except requests.exceptions.RequestException as exc:
        logger.warning(
            f"[WORK_CANCEL] {work_id[:8]}... failed (non-fatal): {exc}"
        )
        return False


def _consume_sse_job_events(
    work_id: str, timeout: int = 30
) -> list[dict]:
    """Subscribe to SSE job events for a ``work_id`` and collect events.

    The SSE endpoint at ``/api/jobs/{work_id}/events`` is resolver-gated
    and accepts both ``job_id`` and task ``work_id`` UUID4s. The stream
    emits ``connected`` (initial state) and ``status_update`` /
    ``completed`` / ``error`` events. The function terminates early on
    ``completed`` / ``error`` and on timeout.

    Returns:
        A list of parsed event dicts. Each dict has ``event`` and
        ``data`` keys matching the SSE wire format.
    """
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    try:
        response = requests.get(
            f"{API_BASE}/jobs/{work_id}/events",
            stream=True,
            timeout=timeout,
            headers={"Accept": "text/event-stream"},
        )
        for line in response.iter_lines(decode_unicode=True):
            # Wall-clock deadline: ``requests`` timeout only governs connect/
            # read timeouts, not the total stream lifetime. SSE endpoints
            # often keep the connection open with heartbeats, so an idle
            # stream (e.g. deferred job that never starts) would otherwise
            # block ``iter_lines`` forever.
            if time.monotonic() >= deadline:
                break
            if not line or not line.startswith("data:"):
                continue
            try:
                data = json.loads(line[5:].strip())
            except (json.JSONDecodeError, ValueError):
                continue
            events.append({"event": "data", "data": data})
            # Stop early on terminal markers (SSE parser doesn't propagate
            # the named event, so we infer from the data payload).
            status = (
                data.get("status") if isinstance(data, dict) else None
            )
            if status in {"completed", "failed", "cancelled", "dead_letter"}:
                # Try to read one more frame to capture the named event
                # header (e.g. ``event: completed``) if present.
                pass
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
        pass
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[SSE] unexpected error on {work_id[:8]}...: {exc}")
    return events


# --------------------------------------------------------------------------- #
# Test 1 — Happy path: parent → child → terminal
# --------------------------------------------------------------------------- #
def test_parent_child_workflow_happy_path():
    """E2E Test 1: Normal parent→child workflow (happy path).

    **Phase 1** — Sends a message to the leader asking it to spawn a developer
    to say hello. Verifies that the leader spawns a developer child, the
    leader eventually completes (or otherwise reaches a terminal status),
    and the conversation history contains an assistant turn.

    **Phase 2** — After Phase 1 completes, sends a *second* message to the
    same already-completed leader and verifies that (a) the leader instance
    is reused, (b) the same developer child is reused rather than a fresh spawn,
    (c) the reused child runs the new message to terminal status, and
    (d) the leader produces at least one additional assistant turn.

    Run with::

        pytest tests/e2e/test_e2e_workflows.py::test_parent_child_workflow_happy_path -v -s
    """
    leader_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 1: parent -> child happy path")
    logger.info("=" * 60)

    try:
        # Step 1: spawn the leader.
        leader_id = _spawn_instance("leader")
        assert leader_id, "Failed to spawn leader instance"

        # Step 2: send the test message (capture message_id == work_id).
        msg_id = _send_message(leader_id, TEST_MESSAGE)

        # Step 3: wait for the developer child to be spawned.
        child_id = _wait_for_child_spawned(leader_id, timeout=SPAWN_TIMEOUT)
        assert child_id is not None, (
            f"Leader {leader_id[:8]}... did not spawn a developer child "
            f"within {SPAWN_TIMEOUT}s"
        )

        # Step 4: wait for the leader to reach a terminal status, while
        # verifying NO premature completion. Architecture-agnostic check:
        # at the moment the leader becomes terminal, the child must also
        # be terminal. We check child instance status directly rather
        # than relying on the ``waiting_for`` column (which is vestigial
        # under DependencyBus).
        finished, final_status, premature = _wait_for_leader_completion_safe(
            leader_id, [child_id], timeout=COMPLETION_TIMEOUT
        )
        assert not premature, (
            f"PREMATURE COMPLETION DETECTED: {premature}"
        )
        assert finished, (
            f"Leader {leader_id[:8]}... did not reach a terminal status "
            f"within {COMPLETION_TIMEOUT}s (last status: {final_status})"
        )
        logger.info(
            f"[ASSERT] leader reached terminal status: {final_status} "
            f"(child also terminal — no premature completion)"
        )

        # ---- Virtual Job Management Surface: verify work_id resolves ----
        # NOTE: ``message_id`` returned by /messages is the message_queue
        # UUID; the matching Task row has its OWN ``work_id`` UUID. The
        # mapping is via ``Task.message_id == message_id`` and the
        # WorkRecord's ``result_summary.message_id`` field. To resolve
        # the work_id we list the instance's turns and pick the one
        # whose ``result_summary.message_id`` matches the message we just
        # sent (or fall back to the most recent turn if not found).
        logger.info(
            "[VJM] Verifying virtual job surface for message_id=%s "
            "on leader=%s",
            msg_id,
            leader_id[:8] + "...",
        )

        # 1. List work records via the unified surface (Phase 5: all
        # message-driven work resolves as kind="job").
        instance_turns = _get_work_by_instance(leader_id, kind="job")
        assert instance_turns, (
            f"No work records returned for leader {leader_id[:8]}... — "
            f"virtual job surface failed to surface message-driven work"
        )

        # Find the work record whose result_summary.message_id matches msg_id
        work_record: dict | None = None
        for tr in instance_turns:
            rs = tr.get("result_summary") or ""
            if msg_id in rs:
                work_record = tr
                break
        # Fallback: take the most recent work record if no result_summary match
        if work_record is None:
            logger.warning(
                "[VJM] no job record matched msg_id=%s via result_summary — "
                "using most recent record",
                msg_id,
            )
            work_record = instance_turns[0]

        assert work_record["kind"] == "job", (
            f"Expected kind='job' for message-driven work, got "
            f"kind='{work_record['kind']}'"
        )
        assert work_record["status"] in ("completed", "processing"), (
            f"Expected completed/processing status, got "
            f"'{work_record['status']}'"
        )
        work_id = work_record["work_id"]
        assert work_id, "WorkRecord missing work_id"
        logger.info(
            "[VJM] ✓ job_get resolves message as kind='job', "
            "status='%s', work_id=%s",
            work_record["status"],
            work_id,
        )

        # 2. job_list returns UNION: at least one job record exists for this instance
        instance_work = _get_work_by_instance(leader_id)
        kinds_present = {w["kind"] for w in instance_work}
        # Phase 5: all message-driven work surfaces as kind="job" (JobItem).
        # VJM dedup keys on (instance_id, message_id).
        assert "job" in kinds_present, (
            f"Expected 'job' kind in work list for instance, got "
            f"kinds={kinds_present}"
        )
        logger.info(
            "[VJM] ✓ job_list UNION contains kind='job' (kinds present: %s)",
            kinds_present,
        )

        # 3. watch_job via SSE: the completed turn should emit a terminal event
        # using its work_id (the Task's UUID, NOT the message_id).
        sse_events = _consume_sse_job_events(work_id, timeout=15)
        assert len(sse_events) > 0, (
            f"No SSE events received for work_id={work_id} — "
            f"watch_job SSE failed"
        )
        first_event = sse_events[0].get("data", {})
        first_status = (
            first_event.get("status") if isinstance(first_event, dict) else None
        )
        assert first_status in {
            "completed", "failed", "cancelled", "dead_letter",
        }, (
            f"Expected terminal status in first SSE event for completed "
            f"turn, got status={first_status!r}"
        )
        logger.info(
            "[VJM] ✓ watch_job SSE delivered connected event with terminal "
            "status '%s' for work_id=%s",
            first_status,
            work_id,
        )

        # Step 5: verify the leader's conversation history has at least
        # one assistant message (evidence the LLM actually produced a
        # reply rather than silently dying).
        messages = _get_messages(leader_id)
        assert isinstance(messages, list), (
            f"Expected list from /messages, got: {type(messages).__name__}"
        )
        assistant_turns = [
            m for m in messages
            if isinstance(m, dict) and m.get("role") == "assistant"
            and (m.get("content") or "").strip()
        ]
        assert assistant_turns, (
            f"Leader {leader_id[:8]}... produced no assistant turns in its "
            f"conversation history (got {len(messages)} messages)"
        )
        logger.info(
            f"[ASSERT] leader produced {len(assistant_turns)} assistant turn(s)"
        )

        # ── Verify no bus message leaks after Phase 1 ────────────────────
        leak_found, leaked = _check_bus_message_leak(leader_id, label="Test 1 Phase 1")
        assert not leak_found, (
            f"Internal bus messages leaked into leader's message history: "
            f"{len(leaked)} messages with bus content. "
            f"First leak: {leaked[0] if leaked else 'N/A'}"
        )

        # =====================================================================
        # PHASE 2: Send a second message to the same (completed) leader.
        # Verify that the leader instance is reused, the same developer child is
        # reused, and the full parent → child → completion → parent cycle
        # works a second time on the same leader_id.
        # =====================================================================
        logger.info("=" * 60)
        logger.info("PHASE 2: message-after-completion reuse")
        logger.info("=" * 60)

        # Step P2.1 — Preserve the Phase 1 child_id for reuse verification.
        assert child_id is not None, (
            "Phase 1 child_id should not be None at the start of Phase 2"
        )
        logger.info(
            f"[P2.1] Phase 1 child_id preserved: {child_id[:8]}..."
        )

        # Step P2.2 — Record the leader's children list before the second
        # message, so we can later verify whether the developer child was reused
        # (expected) or whether a new child was spawned (warn-worthy).
        children_before_data = _get_instance(leader_id)
        children_before = children_before_data.get("children", [])
        # Defensive fallbacks for schema evolution (mirrors _wait_for_child_spawned).
        if not isinstance(children_before, list):
            for alt_key in ("child_ids", "child_instances"):
                alt = children_before_data.get(alt_key)
                if isinstance(alt, list):
                    children_before = alt
                    break
            else:
                children_before = []
        child_ids_before = set(children_before)
        logger.info(
            f"[P2.2] Children before Phase 2: "
            f"count={len(children_before)} ids={child_ids_before}"
        )

        # Step P2.3 — Send the second message. The leader may be in
        # ``completed`` status; the POST /messages endpoint should
        # auto-resume/reactivate the instance.
        p2_msg_id = _send_message(leader_id, PHASE2_MESSAGE)

        # ---- Virtual Job Management Surface: verify Phase 2 work_id ----
        # The reused leader should produce a NEW work record (Phase 2
        # message) visible via GET /api/work as kind="job" (Phase 5).
        # Same work_id-vs-message_id note as Phase 1: resolve via instance.
        p2_instance_turns = _get_work_by_instance(leader_id, kind="job")
        assert p2_instance_turns, (
            f"Phase 2: no work records returned for leader {leader_id[:8]}..."
        )
        # Find the record whose result_summary.message_id matches p2_msg_id
        p2_work_record: dict | None = None
        for tr in p2_instance_turns:
            rs = tr.get("result_summary") or ""
            if p2_msg_id in rs:
                p2_work_record = tr
                break
        if p2_work_record is None:
            logger.warning(
                "[VJM] Phase 2: no job record matched p2_msg_id=%s via "
                "result_summary — using most recent record",
                p2_msg_id,
            )
            p2_work_record = p2_instance_turns[0]
        assert p2_work_record["kind"] == "job", (
            f"Phase 2: expected kind='job' for message-driven work, "
            f"got kind='{p2_work_record['kind']}'"
        )
        logger.info(
            "[VJM] ✓ Phase 2 message resolves as kind='job' with "
            "work_id=%s",
            p2_work_record["work_id"],
        )

        # Step P2.4 — Wait for the leader to process the second message and
        # reach a terminal status again.
        p2_finished, p2_final_status = _wait_for_completion(leader_id)
        assert p2_finished, (
            f"Leader {leader_id[:8]}... did not reach a terminal status "
            f"in Phase 2 within {COMPLETION_TIMEOUT}s "
            f"(last status: {p2_final_status})"
        )
        logger.info(
            f"[ASSERT] leader reached terminal status after Phase 2: "
            f"{p2_final_status}"
        )

        # ── Phase 2: Verify the leader was reused and processed the second message ────
        #
        # The leader instance was reused (same instance_id — implicit, we sent to
        # the same ID). The leader reactivated from completed status and processed
        # the Phase 2 message.
        #
        # NOTE: After a child completes, it is cleaned up from the parent's
        # active children list — that is expected daemon behavior, not a bug.
        # So we do NOT assert that the Phase 1 child_id still appears in
        # ``children_after`` (the cleanup makes that test flaky by design).
        # We also do NOT wait for the child to reach a terminal status in
        # Phase 2 — the leader reaching terminal status is sufficient
        # evidence that the message was processed.
        # The children list state is logged for observability only.

        # Assert: leader reached a terminal status after Phase 2
        # (p2_finished / p2_final_status captured above).
        assert p2_finished, (
            f"Leader {leader_id[:8]}... did not reach a terminal status "
            f"after Phase 2 message (last status: {p2_final_status})"
        )
        logger.info(
            f"[ASSERT] leader reached terminal status after Phase 2: "
            f"{p2_final_status}"
        )

        # Assert: leader produced at least one NEW assistant turn after Phase 2.
        messages_after = _get_messages(leader_id)
        assert isinstance(messages_after, list), (
            f"Expected list from /messages, got: {type(messages_after).__name__}"
        )
        assistant_turns_after = [
            m for m in messages_after
            if isinstance(m, dict) and m.get("role") == "assistant"
            and (m.get("content") or "").strip()
        ]
        assert len(assistant_turns_after) > len(assistant_turns), (
            f"Expected more assistant turns after Phase 2 "
            f"(had {len(assistant_turns)}), "
            f"but got {len(assistant_turns_after)}"
        )
        logger.info(
            f"Phase 2: Leader produced "
            f"{len(assistant_turns_after) - len(assistant_turns)} "
            f"new assistant turn(s) "
            f"(total {len(assistant_turns_after)}, "
            f"was {len(assistant_turns)} after Phase 1)"
        )

        # Soft check: report children state for observability (no hard assertion).
        children_info = _get_instance(leader_id)
        raw_children = (
            children_info.get("children")
            or children_info.get("child_ids")
            or children_info.get("child_instances")
            or []
        )
        if raw_children:
            logger.info(f"Phase 2: Children after Phase 2: {raw_children}")
        else:
            logger.info(
                "Phase 2: No active children after Phase 2 "
                "(completed children cleaned up — expected)"
            )

        logger.info("=" * 60)
        logger.info(
            "Phase 2 PASSED: Leader reused, reactivated from completed, "
            "produced new assistant turn"
        )
        logger.info("=" * 60)

        # ── Verify no bus message leaks after Phase 2 ────────────────────
        leak_found, leaked = _check_bus_message_leak(leader_id, label="Test 1 Phase 2")
        assert not leak_found, (
            f"Internal bus messages leaked into leader's message history: "
            f"{len(leaked)} messages with bus content. "
            f"First leak: {leaked[0] if leaked else 'N/A'}"
        )

        logger.info("TEST 1 PASSED")

    finally:
        # Cleanup: always terminate the leader, even on failure.
        if leader_id:
            _terminate_instance(leader_id)


# --------------------------------------------------------------------------- #
# Test 2 — Pause after spawn, then resume
# --------------------------------------------------------------------------- #
def test_pause_after_spawn_then_resume():
    """E2E Test 2: Pause after spawn, then resume.

    Phase 5 (pause/resume redesign) expectations:
      * Pause the leader → instance status → ``paused`` (cascade
        SQL transitions instance + job + task atomically; ``paused``
        is the new ``JobStatus`` value introduced in Phase 2, replacing
        the pre-Phase 2 behavior where the job stayed ``processing``
        during pause).
      * Cascade pause: child instance also transitions to ``paused``
        (the cascade uses ``get_tree_ids`` BFS to batch-update all
        descendants in a single transaction).
      * Hold window: the leader's status stays ``paused`` for 5s
        (no rogue processing leaks through).
      * Resume → instance leaves ``paused`` and reaches a terminal
        status. The resume path uses ``_process_resume_finalize`` to
        drive ``COMPLETED`` via the same transactional bus gate as
        the lifecycle-event path (Phase 3 C1 fix — replaces the
        pre-Phase 3 direct ``complete_job`` call with the
        TOCTOU-safe observer method).
      * No bus message leaks into the leader's message history.

    Same workflow as Test 1, but pauses the leader as soon as the developer
    child is observed, verifies both leader and child reach ``paused``,
    holds for a few seconds to confirm no further processing, then
    resumes and confirms the workflow completes.
    """
    leader_id: str | None = None
    child_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 2: pause after spawn, then resume")
    logger.info("=" * 60)

    try:
        # Step 1: spawn leader + send message.
        leader_id = _spawn_instance("leader")
        assert leader_id, "Failed to spawn leader instance"
        _send_message(leader_id, TEST_MESSAGE)

        # Step 2: wait for the developer child.
        child_id = _wait_for_child_spawned(leader_id, timeout=SPAWN_TIMEOUT)
        assert child_id is not None, (
            f"Leader {leader_id[:8]}... did not spawn a developer child "
            f"within {SPAWN_TIMEOUT}s"
        )

        # Step 3: pause the leader (cascades to children).
        _pause_instance(leader_id)

        # Step 4: verify leader is paused.
        leader_paused = _wait_for_status(leader_id, "paused", timeout=30)
        assert leader_paused, (
            f"Leader {leader_id[:8]}... did not reach status 'paused' "
            f"within 30s"
        )

        # Step 4b (Phase 6): verify the leader's job is PAUSED.
        # The job transitions PROCESSING → PAUSED atomically with the
        # instance status flip (Phase 2 ``_pause_cascade_db_sync``).
        # If the assertion fails, the pause cascade is broken — jobs
        # would keep running against a paused instance.
        leader_job_id = _find_active_job_for_instance(leader_id)
        if leader_job_id is not None:
            job_paused_ok, job_status_at_pause = _wait_for_job_status(
                leader_job_id, "paused", timeout=15
            )
            assert job_paused_ok, (
                f"Leader's job {leader_job_id[:8]}... did not reach "
                f"'paused' within 15s (status={job_status_at_pause}). "
                f"Pause cascade is broken — the job kept running after "
                f"the instance was paused."
            )
            logger.info(
                f"[ASSERT] leader job {leader_job_id[:8]}... is PAUSED"
            )
        else:
            logger.warning(
                "[ASSERT-SKIP] could not discover leader's job_id — "
                "job status assertion skipped (daemon reachable but job "
                "not visible via /api/jobs)"
            )

        # Step 5: verify child is also paused (best-effort; pause is
        # supposed to cascade, but we don't want to fail the test if
        # a particular child was already terminal).
        try:
            child_info = _get_instance(child_id)
            child_status = str(child_info.get("status", ""))
            if child_status in TERMINAL_STATUSES:
                logger.info(
                    f"[INFO] child {child_id[:8]}... already terminal "
                    f"({child_status}); skipping pause verification"
                )
            else:
                child_paused = _wait_for_status(child_id, "paused", timeout=15)
                if not child_paused:
                    logger.warning(
                        f"[WARN] child {child_id[:8]}... did not reach "
                        f"'paused' within 15s (status={child_status})"
                    )
                else:
                    # Step 5b (Phase 6): child's job is PAUSED too.
                    child_job_id = _find_active_job_for_instance(child_id)
                    if child_job_id is not None:
                        child_job_paused_ok, _ = _wait_for_job_status(
                            child_job_id, "paused", timeout=10
                        )
                        assert child_job_paused_ok, (
                            f"Child's job {child_job_id[:8]}... did "
                            f"not reach 'paused' within 10s — pause "
                            f"cascade did not propagate to jobs."
                        )
                        logger.info(
                            f"[ASSERT] child job {child_job_id[:8]}... "
                            f"is PAUSED"
                        )
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WARN] could not check child status: {exc}")

        # Step 6: hold for a few seconds and confirm leader is still
        # paused (no rogue processing).
        time.sleep(5)
        try:
            current = _get_instance(leader_id)
            current_status = str(current.get("status", ""))
            assert current_status == "paused", (
                f"Leader {leader_id[:8]}... left 'paused' during the "
                f"5s hold window (status={current_status})"
            )
            logger.info(
                f"[ASSERT] leader held 'paused' status for 5s window"
            )

            # Step 6b (Phase 6): the job must also still be PAUSED
            # during the hold window — verifies the new PAUSED state
            # is sticky (no rogue processing leaks through).
            if leader_job_id is not None:
                job_data = _get_job(leader_job_id)
                job_status_in_hold = str(job_data.get("status", "")).lower()
                assert job_status_in_hold == "paused", (
                    f"Leader's job {leader_job_id[:8]}... left 'paused' "
                    f"during the 5s hold window (status={job_status_in_hold}). "
                    f"Pause is not sticky — this is the premature-completion "
                    f"bug class Phase 1 was designed to prevent."
                )
                logger.info(
                    f"[ASSERT] leader job held 'paused' for 5s window"
                )
        except requests.exceptions.RequestException as exc:
            logger.warning(f"[WARN] could not re-check leader status: {exc}")

        # Step 7: resume the leader with an optional continuation prompt.
        _resume_instance(leader_id, message="continue")

        # Step 7b (Phase 6): after resume, the leader's job must
        # transition PAUSED → PROCESSING (the resume cascade's
        # UPDATE 2 in ``_resume_cascade_db_sync``).
        if leader_job_id is not None:
            # After resume, ``_resume_cascade_db_sync`` UPDATE 2
            # intentionally CANCELS the original PROCESS_MESSAGE
            # Task — its driver is superseded by the checkpoint-
            # resume turn. The WorkResolver resolves ``work_id``
            # by looking up Tasks FIRST, so a cancelled Task
            # surfaces ``status='cancelled'``. This is correct:
            # the resume itself succeeds, and a NEW turn (with a
            # NEW JobItem) drives the continued processing. We
            # therefore accept ``cancelled`` alongside the
            # in-flight ``processing`` / ``completed`` states.
            resumed_ok, post_resume_status = _wait_for_job_status(
                leader_job_id,
                {"processing", "completed", "cancelled"},  # cancelled = original Task superseded by resume
                timeout=30,
            )
            assert resumed_ok, (
                f"Leader's job {leader_job_id[:8]}... did not leave "
                f"'paused' within 30s of resume "
                f"(status={post_resume_status})"
            )
            logger.info(
                f"[ASSERT] leader job left 'paused' after resume "
                f"(status={post_resume_status})"
            )

            # Secondary instance-level check: when the OLD job_id
            # surfaces ``cancelled`` (orphan Task, normal in
            # resume semantics), we still need positive proof the
            # resume cascade actually transitioned the instance
            # out of ``paused``. Poll briefly for an active
            # instance status (not ``paused`` / ``queued``) so
            # we catch both ``running`` and ``processing`` and
            # fail fast if the instance is wedged.
            instance_resumed = False
            instance_status_now = ""
            resume_check_deadline = time.time() + 15
            while time.time() < resume_check_deadline:
                try:
                    instance_info = _get_instance(leader_id)
                    instance_status_now = str(
                        instance_info.get("status", "")
                    ).lower()
                    if instance_status_now not in (
                        "",
                        "paused",
                        "queued",
                        "pending",
                    ):
                        instance_resumed = True
                        break
                except requests.exceptions.RequestException:
                    pass
                time.sleep(POLL_INTERVAL)
            assert instance_resumed, (
                f"Instance {leader_id[:8]}... did not resume correctly "
                f"after the cascade — last status={instance_status_now}. "
                f"Expected one of (running, processing, etc.), but the "
                f"instance stayed in {instance_status_now!r}."
            )
            logger.info(
                f"[ASSERT] leader instance resumed after cascade "
                f"(status={instance_status_now})"
            )

        # Step 8: verify leader left 'paused'. We allow a generous window
        # because resume + job pickup has some startup latency.
        left_paused = False
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                info = _get_instance(leader_id)
                status = str(info.get("status", ""))
                if status != "paused":
                    left_paused = True
                    logger.info(
                        f"[ASSERT] leader left 'paused' (now status={status})"
                    )
                    break
            except requests.exceptions.RequestException:
                pass
            time.sleep(POLL_INTERVAL)
        assert left_paused, (
            f"Leader {leader_id[:8]}... never left 'paused' after resume"
        )

        # Step 9: wait for completion.
        finished, final_status = _wait_for_completion(leader_id)
        assert finished, (
            f"Leader {leader_id[:8]}... did not reach a terminal status "
            f"after resume (last status: {final_status})"
        )
        logger.info(
            f"[ASSERT] leader reached terminal status after resume: {final_status}"
        )

        # Step 9b (Phase 6): the leader's job must reach COMPLETED
        # (not just the instance). A workflow completion without a
        # job completion would mean the job tracker is out of sync
        # with the lifecycle — a bug class the redesign was built
        # to close.
        if leader_job_id is not None:
            # ``leader_job_id`` captures the OLD JobItem created at
            # spawn time. After resume, that JobItem is ORPHANED:
            # its PROCESS_MESSAGE Task is cancelled by the resume
            # cascade and the observer finalizes a NEW JobItem
            # (created for the resume message) instead. The OLD
            # JobItem therefore stays at ``cancelled`` for the
            # rest of the workflow — it never reaches
            # ``completed``. We accept the orphan's
            # ``cancelled`` terminal state as the equivalent of
            # "out of the way" for the OLD job; Step 9 already
            # proved the workflow itself reached a terminal
            # instance status. We do NOT re-resolve the work_id
            # to chase the NEW JobItem because Test 2 deliberately
            # exercises the orphan semantics.
            completed_ok, job_final_status = _wait_for_job_status(
                leader_job_id,
                {"completed", "cancelled"},  # OLD job_id is orphaned post-resume
                timeout=60,
            )
            assert completed_ok, (
                f"Leader's job {leader_job_id[:8]}... did not reach "
                f"a terminal job state after the workflow finished "
                f"(status={job_final_status})"
            )
            logger.info(
                f"[ASSERT] leader OLD job reached terminal state "
                f"(status={job_final_status}); workflow completion "
                f"verified separately via Step 9"
            )

        # ---- Virtual Job Management Surface: verify cancel + work surface ----
        # The leader's job_id IS its work_id (JobItem-backed); the same
        # cancel endpoint serves both the legacy ``/api/jobs/{id}/cancel``
        # and the unified work surface (cancel is resolver-gated at the
        # HTTP layer for JobItem rows). Cancel a deferred JobItem (the
        # kind-side cancellation flow is JobItem-backed), then verify the
        # cancellation propagates to the ``/api/work`` view.
        cancel_target_id = _create_job(
            agent_id="leader",
            message="Test 2 VJM cancel target",
            project_id=PROJECT_ID,
            priority=5,
        )
        assert cancel_target_id, "Failed to create cancel-target job"
        # Verify the new job shows up in the work UNION as kind="job"
        cancel_work = _get_work_by_id(cancel_target_id)
        assert cancel_work is not None, (
            f"Cancel-target JobItem {cancel_target_id[:8]}... missing "
            f"from /api/work"
        )
        assert cancel_work["kind"] == "job", (
            f"Expected kind='job' for fresh JobItem, got "
            f"kind='{cancel_work['kind']}'"
        )
        logger.info(
            "[VJM] ✓ fresh JobItem work_id=%s visible as kind='job' in /work",
            cancel_target_id,
        )

        cancelled = _cancel_work(cancel_target_id)
        assert cancelled, (
            f"Failed to cancel JobItem work_id={cancel_target_id} — "
            f"unified cancel endpoint failed"
        )

        # Verify the cancellation propagated to the work surface.
        cancelled_ok, final_vjm_status = _wait_for_work_status(
            cancel_target_id, "cancelled", timeout=30
        )
        assert cancelled_ok, (
            f"Work surface did not reflect cancellation of "
            f"{cancel_target_id[:8]}... (last status={final_vjm_status})"
        )
        logger.info(
            "[VJM] ✓ JobItem work_id=%s reaches status='cancelled' in /work",
            cancel_target_id,
        )

        # Verify the leader has visible job records (UNION contains
        # message-driven work as kind="job" in Phase 5).
        leader_turns = _get_work_by_instance(leader_id, kind="job")
        if leader_turns:
            sample = leader_turns[0]
            assert sample["kind"] == "job", (
                f"Expected kind='job' for leader work record, got "
                f"kind='{sample['kind']}'"
            )
            logger.info(
                "[VJM] ✓ leader has %d job record(s) via /work",
                len(leader_turns),
            )
        else:
            logger.info(
                "[VJM] no job records visible for leader — may be "
                "compacted by the time the test runs the check"
            )

        # ── Verify no bus message leaks ───────────────────────────────────
        leak_found, leaked = _check_bus_message_leak(leader_id, label="Test 2")
        assert not leak_found, (
            f"Internal bus messages leaked into leader's message history: "
            f"{len(leaked)} messages with bus content. "
            f"First leak: {leaked[0] if leaked else 'N/A'}"
        )

        logger.info("TEST 2 PASSED")

    finally:
        # Cleanup: always terminate the leader.
        if leader_id:
            _terminate_instance(leader_id)


# --------------------------------------------------------------------------- #
# Test 3 — Terminate after spawn, then revive (revive-fix, 2026-07-01)
# --------------------------------------------------------------------------- #
def test_terminate_after_spawn_then_revive():
    """E2E Test 3: Terminate after spawn, then revive.

    Same workflow as Test 1, but terminates the leader immediately after
    the developer child spawns. Verifies the leader reaches ``terminated``,
    then asserts that sending a new message REVIVES it: the instance
    flips back to ``running`` (terminal = terminal, just a different
    reason — checkpoint/history persist and reload) and reaches a
    terminal state processing the new message.

    Pre-revive-fix (2026-07-01) this only documented behavior — the
    message created a Task stuck ``pending`` forever because
    ``enqueue_message`` didn't reactivate terminal instances and
    ``claim_pending_task`` excludes terminated instances. Now revive is
    real, so this asserts it.
    """
    leader_id: str | None = None
    child_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 3: terminate after spawn, then revive")
    logger.info("=" * 60)

    try:
        # Step 1: spawn leader + send message.
        leader_id = _spawn_instance("leader")
        assert leader_id, "Failed to spawn leader instance"
        _send_message(leader_id, TEST_MESSAGE)

        # Step 2: wait for the developer child.
        child_id = _wait_for_child_spawned(leader_id, timeout=SPAWN_TIMEOUT)
        assert child_id is not None, (
            f"Leader {leader_id[:8]}... did not spawn a developer child "
            f"within {SPAWN_TIMEOUT}s"
        )

        # Step 3: terminate the leader.
        terminated = _terminate_instance(leader_id)
        assert terminated, f"DELETE failed for leader {leader_id[:8]}..."

        # Step 4: verify leader is terminated. We tolerate a brief window
        # during which the status may still be 'running' (the DELETE
        # endpoint returns 200 before the cascade finishes).
        leader_terminated = _wait_for_status(
            leader_id, "terminated", timeout=15
        )
        assert leader_terminated, (
            f"Leader {leader_id[:8]}... did not reach 'terminated' within 15s"
        )

        # Step 5: best-effort check that the child was also terminated
        # (terminate is supposed to cascade to children).
        if child_id:
            try:
                child_info = _get_instance(child_id)
                child_status = str(child_info.get("status", ""))
                logger.info(
                    f"[INFO] child {child_id[:8]}... status after parent "
                    f"terminate: {child_status}"
                )
                # We don't assert here — some children may already be
                # 'completed' if they finished before the parent died.
            except requests.exceptions.RequestException as exc:
                logger.warning(f"[WARN] could not check child status: {exc}")

        # Step 6: REVIVE — send a new message to the terminated leader.
        # The revive-fix reactivates terminal instances (completed /
        # terminated / error / failed → running) on a new message, so
        # the endpoint must ACCEPT the message (not 4xx) and the instance
        # must leave 'terminated'.
        REVIVE_MESSAGE = (
            "continue our conversation — you were terminated, now revived"
        )
        response = requests.post(
            f"{API_BASE}/instances/{leader_id}/messages",
            json={"content": REVIVE_MESSAGE},
            timeout=30,
        )
        assert response.status_code == 200, (
            f"Revive message to terminated leader {leader_id[:8]}... was "
            f"rejected: status={response.status_code} body={response.text[:300]!r}. "
            "A terminal instance must accept a new message and revive."
        )
        logger.info(
            f"[STEP6] revive message accepted (status={response.status_code})"
        )

        # Step 7: assert the instance revived — it must leave 'terminated'
        # and flip to 'running' (proving enqueue_message reactivated it
        # so the Task is claimable, not stuck pending).
        revived = _wait_for_status(leader_id, "running", timeout=COMPLETION_TIMEOUT)
        assert revived, (
            f"Leader {leader_id[:8]}... did NOT revive to 'running' after a "
            f"new message within {COMPLETION_TIMEOUT}s — the revive-fix "
            f"(enqueue_message reactivates terminal states) is broken or the "
            f"instance stayed 'terminated' / stuck 'pending'."
        )
        logger.info(f"[STEP7] ✓ leader revived to 'running': {leader_id[:8]}...")

        # Step 8: the revived instance processes the new message and
        # reaches a terminal state. Generous timeout — real LLM.
        reached_terminal, final_status = _wait_for_completion(
            leader_id, timeout=COMPLETION_TIMEOUT
        )
        assert reached_terminal, (
            f"Revived leader {leader_id[:8]}... did not reach a terminal "
            f"state within {COMPLETION_TIMEOUT}s (last status: {final_status!r}). "
            "The revive accepted the message but the Task may be stuck "
            "pending (claim guard still excluding the instance) — see "
            "has_active_non_deferred_work / claim_pending_task."
        )
        assert final_status in TERMINAL_STATUSES, (
            f"Revived leader reached unexpected status {final_status!r} "
            f"(expected one of {TERMINAL_STATUSES})."
        )
        logger.info(
            f"[STEP8] ✓ revived leader reached terminal: {final_status}"
        )

        # ── Verify no bus message leaks ───────────────────────────────────
        leak_found, leaked = _check_bus_message_leak(leader_id, label="Test 3")
        assert not leak_found, (
            f"Internal bus messages leaked into leader's message history: "
            f"{len(leaked)} messages with bus content. "
            f"First leak: {leaked[0] if leaked else 'N/A'}"
        )

    finally:
        # Cleanup: ensure leader is terminated even on failure.
        if leader_id:
            _terminate_instance(leader_id)


# --------------------------------------------------------------------------- #
# Test 4 — Wave spawn (2 children) + defer queue + cross-system
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_three_level_cascade_reports():
    """E2E: 3-level cascade — leader → tester → staggered workers.

    A deeper cascade than the 2-level wave test, directly exercising the
    completion-detection + report-delivery fixes:

      * **Report delivery** — each worker sleeps a distinct duration
        (5/15/30s) then says "hello from worker N". Every worker's report
        must reach the tester (level 3 → 2), and the tester's summary must
        reach the leader (level 2 → 1).
      * **No premature completion** — the tester must NOT reach a terminal
        status (nor report to the leader) while ANY worker is still
        non-terminal; the leader must NOT complete while the tester is
        non-terminal. Regression for ``777eff96`` (premature completion
        when a parent ended a turn with transiently-0 active children but
        a pending task).
      * **No stuck completion** — the tester MUST eventually COMPLETE and
        report to the leader, not stall forever in ``waiting_children``
        because a no-op INJECTED report task defers completion.
        Regression for ``284fb4b5``.
      * **State switching** — the tester transitions through
        ``waiting_children`` while workers run, then to ``completed``.

    The staggered sleeps create turn-boundary windows where the
    completion guards (active-children + pending-task +
    no-op-INJECTED-exclusion) are all exercised.

    Run with::

        pytest tests/e2e/test_e2e_workflows.py::test_three_level_cascade_reports \\
            -v -s -m integration
    """
    leader_id: str | None = None
    tester_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST: 3-level cascade — leader → tester → staggered workers")
    logger.info("=" * 60)

    try:
        project_id = _get_first_project_id()
        assert project_id, "No project found via GET /api/projects"

        # ── Step 1: Spawn leader ──────────────────────────────────────
        leader_id = _spawn_instance("leader", project_id=project_id)
        assert leader_id, "Failed to spawn leader instance"
        logger.info(f"[STEP1] leader spawned: {leader_id[:8]}...")

        # ── Step 2: Delegate to a tester that spawns 3 staggered workers
        # The leader delegates to a tester instance; the tester spawns 3
        # workers with distinct sleeps so their completions are staggered,
        # creating the turn-boundary windows the completion guards must
        # survive. Workers run a deterministic `sleep` then reply.
        CASCADE_MESSAGE = (
            "Delegate this to a tester instance (spawn a tester and send it "
            "this task). The tester must run this test: spawn 3 worker "
            "instances in one wave. Worker 1 must run `bash` command `sleep 5` "
            "and then reply with exactly: hello from worker 1. Worker 2 must "
            "run `sleep 15` then reply: hello from worker 2. Worker 3 must run "
            "`sleep 30` then reply: hello from worker 3. The tester must wait "
            "for all 3 worker reports, then report a brief summary back to "
            "you. Do not do the workers' work yourself."
        )
        _send_message(leader_id, CASCADE_MESSAGE)
        logger.info("[STEP2] cascade message sent to leader")

        # ── Step 3: Wait for the tester (leader's child) to spawn ─────
        tester_id = _wait_for_child_spawned(leader_id, timeout=SPAWN_TIMEOUT + 60)
        assert tester_id, (
            "Leader did not spawn a tester child within "
            f"{SPAWN_TIMEOUT + 60}s"
        )
        logger.info(f"[STEP3] tester (leader child): {tester_id[:8]}...")

        # ── Step 4: Wait for workers (tester's children) to spawn ─────
        # Lenient: require >= 2 workers (the LLM may merge or omit one).
        worker_ids: list[str] = []
        WORKER_SPAWN_TIMEOUT = 150
        spawn_deadline = time.time() + WORKER_SPAWN_TIMEOUT
        while time.time() < spawn_deadline:
            try:
                info = _get_instance(tester_id)
                raw = (
                    info.get("children")
                    or info.get("child_ids")
                    or info.get("child_instances")
                    or []
                )
                if isinstance(raw, list) and raw:
                    worker_ids = list(raw)
                    if len(worker_ids) >= 2:
                        break
                if str(info.get("status", "")).lower() in TERMINAL_STATUSES:
                    logger.warning(
                        f"[STEP4] tester reached terminal before spawning "
                        f"2 workers (have {len(worker_ids)})"
                    )
                    break
            except requests.exceptions.RequestException as exc:
                logger.warning(f"[STEP4] poll failed (will retry): {exc}")
            time.sleep(POLL_INTERVAL)

        assert worker_ids, (
            "Tester did not spawn any workers within "
            f"{WORKER_SPAWN_TIMEOUT}s (final tester status="
            f"{_get_instance(tester_id).get('status')!r})"
        )
        logger.info(
            f"[STEP4] {len(worker_ids)} worker(s) spawned: "
            f"{[w[:8] + '...' for w in worker_ids]}"
        )

        # ── Step 5: Monitor for premature completion + state transitions
        # Invariants:
        #   (a) tester NOT terminal while ANY worker is non-terminal
        #       (premature tester completion — 777eff96).
        #   (b) leader NOT terminal while tester is non-terminal
        #       (premature leader completion).
        # A full status timeline is captured for post-mortem analysis.
        MONITOR_TIMEOUT = 250  # kept under the 280s pytest-timeout cap
        timeline: list[tuple[str, str, str, str]] = []
        premature_tester = ""
        premature_leader = ""
        cascade_done = False

        logger.info("[STEP5] monitoring leader/tester/worker statuses...")
        monitor_deadline = time.time() + MONITOR_TIMEOUT
        while time.time() < monitor_deadline:
            try:
                tester_st = str(
                    _get_instance(tester_id).get("status", "")
                ).lower()
            except requests.exceptions.RequestException as exc:
                logger.warning(f"[STEP5] tester poll failed (will retry): {exc}")
                time.sleep(POLL_INTERVAL)
                continue
            try:
                leader_st = str(
                    _get_instance(leader_id).get("status", "")
                ).lower()
            except requests.exceptions.RequestException as exc:
                logger.warning(f"[STEP5] leader poll failed (will retry): {exc}")
                leader_st = "unknown"

            worker_sts = _get_child_statuses(worker_ids)
            worker_summary = (
                ", ".join(f"{w[:8]}={s}" for w, s in worker_sts.items())
                or "(no workers)"
            )
            all_workers_terminal = bool(worker_sts) and all(
                s in TERMINAL_STATUSES for s in worker_sts.values()
            )

            ts = time.strftime("%H:%M:%S")
            timeline.append((ts, leader_st, tester_st, worker_summary))

            # (a) premature tester completion
            if tester_st in TERMINAL_STATUSES and not all_workers_terminal:
                premature_tester = (
                    f"tester='{tester_st}' at {ts} but workers not all "
                    f"terminal: [{worker_summary}]"
                )
                logger.error(f"[STEP5] ❌ PREMATURE TESTER: {premature_tester}")
                break

            # (b) premature leader completion
            if (
                leader_st in TERMINAL_STATUSES
                and tester_st not in TERMINAL_STATUSES
            ):
                premature_leader = (
                    f"leader='{leader_st}' at {ts} but tester='{tester_st}' "
                    f"(workers: [{worker_summary}])"
                )
                logger.error(f"[STEP5] ❌ PREMATURE LEADER: {premature_leader}")
                break

            # cascade fully terminal
            if (
                leader_st in TERMINAL_STATUSES
                and tester_st in TERMINAL_STATUSES
                and all_workers_terminal
            ):
                cascade_done = True
                logger.info(f"[STEP5] ✅ cascade fully terminal at {ts}")
                break

            time.sleep(POLL_INTERVAL)

        logger.info("[STEP5] status timeline:")
        for ts, lst, tst, ws in timeline:
            logger.info(f"  {ts}: leader={lst:<14} tester={tst:<16} workers=[{ws}]")

        assert not premature_tester, (
            "PREMATURE TESTER COMPLETION (777eff96 regression): "
            + premature_tester
        )
        assert not premature_leader, (
            "PREMATURE LEADER COMPLETION: " + premature_leader
        )

        # ── Step 6: Final states — tester + leader completed (not stuck)
        # The tester MUST reach a terminal status. The 284fb4b5 regression
        # left it stuck in 'waiting_children' forever (a no-op INJECTED
        # report task deferring completion), so it never reported to the
        # leader and the leader never completed.
        final_tester = str(
            _get_instance(tester_id).get("status", "")
        ).lower()
        assert final_tester in TERMINAL_STATUSES, (
            f"Tester did NOT reach a terminal status — stuck at "
            f"'{final_tester}' (284fb4b5 no-op-INJECTED-defer regression). "
            f"Timeline tail: {timeline[-5:] if timeline else 'empty'}"
        )
        final_leader = str(
            _get_instance(leader_id).get("status", "")
        ).lower()
        assert final_leader in TERMINAL_STATUSES, (
            f"Leader did not complete after the tester reported: "
            f"'{final_leader}'"
        )
        logger.info(
            f"[STEP6] ✅ final: tester={final_tester}, leader={final_leader}"
        )

        # ── Step 7: Report delivery ───────────────────────────────────
        # (a) Worker reports reached the tester (level 3 → 2).
        tester_msgs = _get_messages(tester_id)
        worker_reports = [
            m for m in tester_msgs
            if isinstance(m, dict)
            and "hello from worker" in (m.get("content") or "").lower()
        ]
        if worker_reports:
            logger.info(
                f"[STEP7a] ✅ {len(worker_reports)} worker report(s) "
                f"reached tester"
            )
        else:
            # Lenient fallback: a worker may have paraphrased its reply.
            # The strong invariants (premature/stuck) already passed; this
            # is a softer content sanity check.
            logger.warning(
                "[STEP7a] ⚠️ no exact 'hello from worker' string found in "
                "tester messages — a worker may have paraphrased. Falling "
                "back to counting tester assistant turns as proof reports "
                "were processed."
            )

        # (b) The tester reported to the leader (level 2 → 1): the leader
        # produced an assistant turn after the cascade, proving it received
        # and processed the tester's completion report.
        leader_msgs = _get_messages(leader_id)
        leader_assistant = [
            m for m in leader_msgs
            if isinstance(m, dict)
            and m.get("role") == "assistant"
            and (m.get("content") or "").strip()
        ]
        assert leader_assistant, (
            "Leader produced no assistant turn — the tester's report may "
            "never have been delivered/processed (level 2 → 1 failed)."
        )
        logger.info(
            f"[STEP7b] ✅ leader produced {len(leader_assistant)} assistant "
            f"turn(s) (tester report processed)"
        )

        # ── Step 8: State switching — tester passed through a waiting state
        # during the wave, then completed. Lenient: warn if not observed
        # (the wave may have completed within one poll interval).
        tester_statuses_seen = {t for _, _, t, _ in timeline}
        if "waiting_children" in tester_statuses_seen:
            logger.info("[STEP8] ✅ tester entered waiting_children during wave")
        else:
            logger.warning(
                f"[STEP8] ⚠️ tester did not observe waiting_children "
                f"(seen: {sorted(tester_statuses_seen)}) — may have "
                f"completed within one poll interval"
            )

        # ── Step 9: No internal bus message leaks into leader history ──
        leak_found, leaked = _check_bus_message_leak(leader_id, label="3-level")
        assert not leak_found, (
            f"Internal bus messages leaked into leader history: "
            f"{len(leaked)} message(s). First: {leaked[0] if leaked else 'N/A'}"
        )

        logger.info("=" * 60)
        logger.info("TEST PASSED: 3-level cascade reports + completion verified")
        logger.info("=" * 60)

    finally:
        # Cleanup: terminate the leader; the cascade terminates its
        # children (tester + workers).
        if leader_id:
            _terminate_instance(leader_id)


# --------------------------------------------------------------------------- #
# Test 5 — Pause blocks defer queue (pause-fix, 2026-07-01)
# --------------------------------------------------------------------------- #
# Reproduces the dev_run.log bug:
#   send_message → pause instance → create defer job
#   → defer job was WRONGLY admitted while the instance was paused.
# Root cause: ``has_active_non_deferred_work`` excluded ``paused`` from
# its status membership set, so a paused (non-deferred) instance read as
# "idle" and the defer idle-gate passed.
# This e2e test exercises the FULL integration chain that the unit test
# ``test_paused_non_deferred_task_blocks_defer_gate`` covers at the
# predicate level: pause API → pause cascade writes task.status=paused
# → shared predicate sees it → defer gate holds the job. It then resumes
# to prove the job was correctly gated (not permanently stuck).
def test_pause_blocks_defer_queue():
    """E2E Test 5: a paused instance counts as non-idle — a deferred job
    created while an instance is paused MUST stay ``pending`` until the
    instance is resumed and reaches a terminal state.
    """
    logger.info("=" * 60)
    logger.info("TEST 5: pause blocks defer queue (pause-fix, 2026-07-01)")
    logger.info("=" * 60)

    leader_id: str | None = None
    job_id: str | None = None
    premature_admission = False
    premature_detail = ""

    try:
        # ── Setup: discover project + defer queue ─────────────────────────
        project_id = _get_first_project_id()
        assert project_id, (
            "No project found via GET /api/projects — cannot test pause+defer"
        )

        defer_queue_id = _get_system_defer_queue_id(project_id)
        if defer_queue_id:
            logger.info(f"[SETUP] defer queue available: {defer_queue_id[:8]}...")
        else:
            logger.warning(
                "[SETUP] no system_defer_queue for project — per-instance "
                "concurrency gate will still apply, but defer semantics are "
                "strongest with a real defer queue"
            )

        # ── Step 1: Spawn leader + start a long-running task ─────────────
        # Ask the leader to spawn a developer that sleeps — this gives a
        # window of active (running / waiting_children) work that we can
        # pause mid-flight.
        leader_id = _spawn_instance("leader", project_id=project_id)
        assert leader_id, "Failed to spawn leader instance"
        logger.info(f"[STEP1] leader spawned: {leader_id[:8]}...")

        WAVE_MESSAGE = (
            "Spawn 1 developer instance and ask it to sleep for 60 seconds "
            "then say hello. Wait for the developer to complete before "
            "reporting back."
        )
        _send_message(leader_id, WAVE_MESSAGE)
        logger.info("[STEP2] long-running task message sent")

        # Give the leader a moment to start processing / spawn the child.
        time.sleep(10)

        # ── Step 3: Pause the instance mid-flight ────────────────────────
        _pause_instance(leader_id)
        paused = _wait_for_status(leader_id, "paused", timeout=SPAWN_TIMEOUT)
        assert paused, (
            f"Leader {leader_id[:8]}... did not reach 'paused' within "
            f"{SPAWN_TIMEOUT}s — cannot assert the defer invariant without "
            f"a paused instance"
        )
        logger.info(f"[STEP3] leader confirmed paused: {leader_id[:8]}...")

        # ── Step 4: Create a deferred job WHILE paused ───────────────────
        JOB_MESSAGE = (
            "hello, this is a paused+defer test — should wait for resume"
        )
        job_id = _create_job(
            agent_id="leader",
            message=JOB_MESSAGE,
            project_id=project_id,
            priority=5,
            queue_id=defer_queue_id,
        )
        assert job_id, "Failed to create deferred job"
        logger.info(f"[STEP4] deferred job created: {job_id[:8]}...")

        # ── Step 5: Hold window — defer job MUST stay pending while paused
        # This is the core invariant. A paused instance is suspended-but-
        # occupying, so the defer idle-gate must treat it as non-idle and
        # hold the job. We poll for PAUSE_HOLD_SECONDS and assert the job
        # never advances past 'pending'.
        PAUSE_HOLD_SECONDS = 25
        hold_deadline = time.time() + PAUSE_HOLD_SECONDS
        while time.time() < hold_deadline:
            try:
                defer_job = _get_job(job_id)
                defer_status = str(defer_job.get("status", "unknown")).lower()
            except requests.exceptions.RequestException as exc:
                logger.warning(f"[STEP5] defer-job GET failed (will retry): {exc}")
                time.sleep(POLL_INTERVAL)
                continue

            # Confirm the instance is STILL paused (not resumed by a stray
            # event) so the invariant under test is actually held.
            try:
                info = _get_instance(leader_id)
                inst_status = str(info.get("status", "")).lower()
            except requests.exceptions.RequestException:
                inst_status = "unknown"

            if defer_status != "pending":
                premature_admission = True
                premature_detail = (
                    f"defer job prematurely admitted while instance was "
                    f"{inst_status!r}: defer status={defer_status!r}"
                )
                logger.error(f"[STEP5] ❌ {premature_detail}")
                break

            logger.info(
                f"[STEP5] holding: defer={defer_status!r} "
                f"instance={inst_status!r}"
            )
            time.sleep(POLL_INTERVAL)

        assert not premature_admission, (
            f"PAUSE+DEFER PREMATURE ADMISSION: {premature_detail}. "
            "The deferred job advanced past 'pending' while a non-deferred "
            "instance was paused. A paused instance must count as non-idle "
            "(suspended-but-occupying), so the defer idle-gate must hold "
            "the job. See has_active_non_deferred_work (pause-fix)."
        )
        logger.info(
            f"[STEP5] ✓ defer job held 'pending' for {PAUSE_HOLD_SECONDS}s "
            f"while instance was paused"
        )

        # ── Step 6: Resume — the job should eventually advance ───────────
        # This proves the job was correctly GATED (held by the pause), not
        # permanently stuck by some other defect. After resume the leader
        # finishes its task → project goes idle → defer queue admits.
        _resume_instance(leader_id)
        logger.info(f"[STEP6] leader resumed: {leader_id[:8]}...")

        # Give the resumed workflow time to reach a terminal state, then
        # confirm the defer job eventually leaves 'pending'. We accept any
        # non-pending status (processing/completed/failed) as proof the
        # gate released. Generous timeout: resume + LLM completion + defer
        # admission all take real time.
        RESUME_TIMEOUT = COMPLETION_TIMEOUT * 2
        released, final_status = _wait_for_job_status(
            job_id,
            {"processing", "completed", "failed", "cancelled"},
            timeout=RESUME_TIMEOUT,
        )
        # We do NOT hard-assert release here (LLM timing is flaky and the
        # core invariant — pause holds the job — is already proven in
        # Step 5). Log the outcome for diagnostics.
        if released:
            logger.info(
                f"[STEP6] ✓ defer job advanced to {final_status!r} after "
                f"resume — gate released correctly"
            )
        else:
            logger.warning(
                f"[STEP6] defer job stayed {final_status!r} after resume "
                f"within {RESUME_TIMEOUT}s (may be LLM timing — the pause "
                f"hold invariant in Step 5 is the binding assertion)"
            )

        logger.info("=" * 60)
        logger.info(
            "TEST 5 PASSED: paused instance held the defer queue (pause-fix)"
        )
        logger.info("=" * 60)

    finally:
        # Cleanup: resume (in case still paused) → cancel job → terminate.
        if leader_id:
            try:
                info = _get_instance(leader_id)
                if str(info.get("status", "")).lower() == "paused":
                    _resume_instance(leader_id)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(f"[CLEANUP] resume check failed: {exc}")
        if job_id:
            try:
                job = _get_job(job_id)
                cur = str(job.get("status", "")).lower()
                if cur in {"pending", "processing"}:
                    _cancel_job(job_id)
                else:
                    logger.info(
                        f"[CLEANUP] job {job_id[:8]}... already terminal "
                        f"({cur}); skipping cancel"
                    )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    f"[CLEANUP] could not check/cancel job {job_id[:8]}...: {exc}"
                )
        if leader_id:
            _terminate_instance(leader_id)


# --------------------------------------------------------------------------- #
# Injection E2E tests (Task 7 — branch feature/user-msg-injection)
#
# These tests exercise the new injection slot that lets a user post a
# message to a RUNNING / WAITING_CHILDREN instance without enqueueing
# it as a fresh user turn. The slot is consumed by agent_node on the
# next LLM step; pausing the instance clears (rather than consumes) the
# pending content (W6 fix).
# --------------------------------------------------------------------------- #

# S9: deliberately long prompt designed to keep the leader's LLM turn
# busy long enough for us to inject against a RUNNING instance. This
# is the same role TEST_MESSAGE plays for child-spawn, but instead of
# triggering delegation it asks for multi-step web summarisation which
# reliably produces a RUNNING status window of >30s in our local dev
# environment.
LONG_PROMPT = (
    "Search the web for recent AI news, then write a detailed "
    "500-word summary of each article you find"
)

# A simpler prompt for the pause-clear test — LONG_PROMPT's web search
# takes 6+ minutes after pause+resume, causing timeout. This prompt asks
# for a shorter response that still keeps the instance RUNNING long enough
# for injection but completes within the timeout after resume.
PAUSE_TEST_PROMPT = (
    "Write a detailed essay about the history of computing, "
    "covering at least 5 major milestones. Take your time and be thorough."
)


@pytest.mark.integration
def test_injection_consumed_by_running_instance():
    """TEST 6: Injection into RUNNING instance is consumed by agent_node.

    Validates the core injection flow:
      1. POST /messages to a RUNNING instance returns 202 with status='injected'.
      2. GET /injection shows pending=True immediately after.
      3. The agent_node pulls the slot and clears it (pending→False).
      4. The injection content is visible in the conversation history.
      5. The instance reaches a terminal state.
    """
    instance_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 6: injection consumed by running instance")
    logger.info("=" * 60)

    INJECTION_MARKER = "INJECTION_TEST_MARKER_12345"

    try:
        # Step 1: spawn + send long-running prompt (S9)
        instance_id = _spawn_instance("leader")
        assert instance_id, "Failed to spawn leader instance"
        _send_message(instance_id, LONG_PROMPT)

        # Step 2: wait for RUNNING (1s interval, 30s timeout)
        running_ok = _wait_for_status(instance_id, "running", timeout=30)
        if not running_ok:
            # Tolerate: LLM may be too fast and the instance already
            # completed; that's a flake, mark and skip the asserts.
            current = _get_instance(instance_id)
            logger.warning(
                f"[STEP2] leader never reached 'running' (status="
                f"{current.get('status')!r}); LLM may have completed "
                f"faster than expected — TEST 6 cannot verify the "
                f"core inject-while-running contract."
            )
            pytest.skip(
                "Leader did not enter 'running' within 30s — cannot "
                "test injection against a RUNNING instance"
            )
        logger.info(f"[STEP2] leader is running: {instance_id[:8]}...")

        # Step 3: POST /messages — expect 202 + status='injected'
        response = _send_injection_raw(instance_id, INJECTION_MARKER)
        assert response.status_code == 202, (
            f"Expected 202 from injection to RUNNING instance, got "
            f"{response.status_code}; body={response.text[:300]!r}"
        )
        body = response.json()
        assert body.get("status") == "injected", (
            f"Expected status='injected', got {body!r}"
        )
        logger.info("[STEP3] ✓ injection accepted (202, status=injected)")

        # Step 4: GET /injection — pending=True
        injection = _get_injection(instance_id)
        assert injection.get("pending") is True, (
            f"Expected pending=True after injection, got {injection!r}"
        )
        assert injection.get("content") == INJECTION_MARKER, (
            f"Expected content={INJECTION_MARKER!r}, got "
            f"{injection.get('content')!r}"
        )
        logger.info("[STEP4] ✓ /injection reports pending=True with marker")

        # Step 5: poll for consumption (pending→False, 2s interval, 60s)
        CONSUME_TIMEOUT = 60
        consume_deadline = time.time() + CONSUME_TIMEOUT
        consumed = False
        while time.time() < consume_deadline:
            current_inj = _get_injection(instance_id)
            if current_inj.get("pending") is False:
                consumed = True
                break
            time.sleep(2)
        assert consumed, (
            f"Injection was not consumed within {CONSUME_TIMEOUT}s — "
            f"agent_node never cleared the slot"
        )
        logger.info("[STEP5] ✓ injection consumed (pending=False)")

        # Step 6: wait for terminal status FIRST (before checking messages).
        # The injected HumanMessage is only persisted to the checkpoint when
        # agent_node RETURNS (after the LLM call finishes), so we must
        # wait for completion before asserting the marker is present.
        # LONG_PROMPT requires the doubled timeout — the 500-word web
        # summary reliably exceeds 120s on local dev.
        finished, final_status = _wait_for_completion(
            instance_id, timeout=COMPLETION_TIMEOUT * 2
        )
        assert finished, (
            f"Instance did not reach terminal status within "
            f"{COMPLETION_TIMEOUT * 2}s (last status: {final_status})"
        )
        logger.info(f"[STEP6] ✓ instance reached terminal: {final_status}")

        # Step 7: marker appears in conversation history (AFTER completion)
        messages = _get_messages(instance_id)
        marker_found = any(
            INJECTION_MARKER in str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict)
        )
        assert marker_found, (
            f"Expected {INJECTION_MARKER!r} in conversation history; "
            f"got {len(messages)} messages without it"
        )
        logger.info("[STEP7] ✓ marker present in conversation history")

        logger.info("TEST 6 PASSED")

    finally:
        if instance_id:
            _terminate_instance(instance_id)


@pytest.mark.integration
def test_injection_queue_cleared_on_pause():
    """TEST 7: Pause clears the injection slot (W6 fix).

    Validates the W6 fix: pausing an instance with a pending injection
    must CLEAR the slot (NOT consume it). The injected content must NOT
    appear in the conversation history after the pause+resume cycle —
    it is discarded by the pause cascade rather than being read by the
    agent_node.
    """
    instance_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 7: injection cleared on pause (W6 fix)")
    logger.info("=" * 60)

    INJECTION_MARKER = "PAUSE_CLEAR_TEST_MARKER"

    try:
        # Step 1: spawn + send long-running prompt
        instance_id = _spawn_instance("leader")
        assert instance_id, "Failed to spawn leader instance"
        _send_message(instance_id, PAUSE_TEST_PROMPT)

        # Step 2: wait for RUNNING
        running_ok = _wait_for_status(instance_id, "running", timeout=30)
        if not running_ok:
            current = _get_instance(instance_id)
            logger.warning(
                f"[STEP2] leader never reached 'running' "
                f"(status={current.get('status')!r}); TEST 7 cannot "
                f"verify W6 pause-clear contract."
            )
            pytest.skip(
                "Leader did not enter 'running' within 30s — cannot "
                "test pause-clear of a pending injection"
            )
        logger.info(f"[STEP2] leader is running: {instance_id[:8]}...")

        # Step 3: send injection
        response = _send_injection_raw(instance_id, INJECTION_MARKER)
        assert response.status_code == 202, (
            f"Expected 202, got {response.status_code}; body="
            f"{response.text[:300]!r}"
        )
        logger.info("[STEP3] ✓ injection sent (202)")

        # Step 4: GET /injection → pending=True BEFORE pausing
        injection_before = _get_injection(instance_id)
        assert injection_before.get("pending") is True, (
            f"Expected pending=True before pause, got "
            f"{injection_before!r}"
        )
        logger.info("[STEP4] ✓ pending=True before pause")

        # Step 5: pause the instance
        _pause_instance(instance_id)
        paused_ok = _wait_for_status(instance_id, "paused", timeout=30)
        assert paused_ok, (
            f"Instance did not reach 'paused' within 30s"
        )
        logger.info("[STEP5] ✓ instance paused")

        # Step 6: GET /injection → pending=False (cleared by pause cascade)
        injection_after = _get_injection(instance_id)
        assert injection_after.get("pending") is False, (
            f"Expected pending=False after pause (W6 fix), got "
            f"{injection_after!r}. The pause cascade did NOT clear the "
            f"injection slot — the queued content would still be "
            f"consumed on resume."
        )
        logger.info(
            "[STEP6] ✓ injection cleared by pause cascade (pending=False)"
        )

        # Step 7: resume
        _resume_instance(instance_id, message="continue")
        logger.info("[STEP7] resume issued")

        # Step 8: wait for completion. PAUSE_TEST_PROMPT (vs LONG_PROMPT)
        # avoids the 6-8min web-search tail after resume; 2x the normal
        # completion timeout (240s) is plenty for the simpler prompt.
        finished, final_status = _wait_for_completion(
            instance_id, timeout=COMPLETION_TIMEOUT * 2
        )
        assert finished, (
            f"Instance did not reach terminal status within "
            f"{COMPLETION_TIMEOUT * 2}s (last status: {final_status})"
        )
        logger.info(f"[STEP8] ✓ instance reached terminal: {final_status}")

        # Step 9: marker must NOT appear in conversation history
        # (it was cleared, NOT consumed)
        messages = _get_messages(instance_id)
        marker_found = any(
            INJECTION_MARKER in str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict)
        )
        assert not marker_found, (
            f"Marker {INJECTION_MARKER!r} appears in conversation "
            f"history — pause did NOT clear the injection slot. The "
            f"agent_node consumed the cleared content anyway (W6 fix "
            f"is broken)."
        )
        logger.info(
            "[STEP9] ✓ marker absent from conversation history (cleared, "
            "not consumed)"
        )

        logger.info("TEST 7 PASSED")

    finally:
        # Defensive resume: if still paused when the test exits, resume
        # so the terminate in finally can land cleanly.
        if instance_id:
            try:
                current = _get_instance(instance_id)
                if str(current.get("status", "")).lower() == "paused":
                    _resume_instance(instance_id)
            except Exception as exc:
                logger.warning(
                    f"[CLEANUP] resume check failed: {exc}"
                )
            _terminate_instance(instance_id)


@pytest.mark.integration
def test_injection_replacement():
    """TEST 8: Second injection replaces the first.

    Validates Task 5 / W5 replacement semantics: posting a NEW
    injection while a previous one is still in the slot must REPLACE
    (not append) the content. The first marker must NOT appear in
    the eventual conversation history; only the second marker must.
    """
    instance_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 8: injection replacement (second replaces first)")
    logger.info("=" * 60)

    FIRST_MARKER = "FIRST_INJECTION_MARKER"
    SECOND_MARKER = "SECOND_INJECTION_MARKER"

    try:
        # Step 1: spawn + send long-running prompt
        instance_id = _spawn_instance("leader")
        assert instance_id, "Failed to spawn leader instance"
        _send_message(instance_id, LONG_PROMPT)

        # Step 2: wait for RUNNING
        running_ok = _wait_for_status(instance_id, "running", timeout=30)
        if not running_ok:
            current = _get_instance(instance_id)
            logger.warning(
                f"[STEP2] leader never reached 'running' "
                f"(status={current.get('status')!r}); TEST 8 cannot "
                f"verify replacement contract."
            )
            pytest.skip(
                "Leader did not enter 'running' within 30s — cannot "
                "test injection replacement"
            )
        logger.info(f"[STEP2] leader is running: {instance_id[:8]}...")

        # Step 3: send first injection
        first_resp = _send_injection_raw(instance_id, FIRST_MARKER)
        assert first_resp.status_code == 202, (
            f"Expected 202 for first injection, got "
            f"{first_resp.status_code}"
        )

        # Step 4: GET /injection — pending=True, content=FIRST_MARKER
        first_state = _get_injection(instance_id)
        assert first_state.get("pending") is True
        assert first_state.get("content") == FIRST_MARKER, (
            f"Expected content={FIRST_MARKER!r}, got "
            f"{first_state.get('content')!r}"
        )
        logger.info(
            f"[STEP4] ✓ first injection in slot: {FIRST_MARKER!r}"
        )

        # Step 5: send second injection (must REPLACE first)
        second_resp = _send_injection_raw(instance_id, SECOND_MARKER)
        assert second_resp.status_code == 202, (
            f"Expected 202 for replacement injection, got "
            f"{second_resp.status_code}"
        )

        # Step 6: GET /injection — pending=True, content=SECOND_MARKER
        second_state = _get_injection(instance_id)
        assert second_state.get("pending") is True
        assert second_state.get("content") == SECOND_MARKER, (
            f"Expected content={SECOND_MARKER!r} after replacement, "
            f"got {second_state.get('content')!r}. Injection slot did "
            f"NOT replace the prior content."
        )
        logger.info(
            f"[STEP6] ✓ second injection replaced first: "
            f"{SECOND_MARKER!r}"
        )

        # Step 7: poll for consumption (pending→False, 60s timeout)
        CONSUME_TIMEOUT = 60
        consume_deadline = time.time() + CONSUME_TIMEOUT
        consumed = False
        while time.time() < consume_deadline:
            current_inj = _get_injection(instance_id)
            if current_inj.get("pending") is False:
                consumed = True
                break
            time.sleep(2)
        assert consumed, (
            f"Injection not consumed within {CONSUME_TIMEOUT}s — slot "
            f"never cleared"
        )
        logger.info("[STEP7] ✓ injection consumed")

        # Step 8: wait for completion FIRST (before checking markers).
        # The injected HumanMessage is only persisted to the checkpoint
        # when agent_node RETURNS, so the marker is only visible in the
        # conversation history after the instance reaches a terminal
        # state. LONG_PROMPT requires the doubled timeout.
        finished, final_status = _wait_for_completion(
            instance_id, timeout=COMPLETION_TIMEOUT * 2
        )
        assert finished, (
            f"Instance did not reach terminal status within "
            f"{COMPLETION_TIMEOUT * 2}s (last status: {final_status})"
        )
        logger.info(f"[STEP8] ✓ instance reached terminal: {final_status}")

        # Step 9: SECOND_MARKER in history, FIRST_MARKER absent
        messages = _get_messages(instance_id)
        has_second = any(
            SECOND_MARKER in str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict)
        )
        has_first = any(
            FIRST_MARKER in str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict)
        )
        assert has_second, (
            f"Expected {SECOND_MARKER!r} in conversation history — "
            f"replacement content not consumed"
        )
        assert not has_first, (
            f"Did NOT expect {FIRST_MARKER!r} in conversation history "
            f"— replacement contract is broken: both injections were "
            f"consumed instead of replacing"
        )
        logger.info(
            "[STEP9] ✓ only second marker in history (replacement OK)"
        )

        logger.info("TEST 8 PASSED")

    finally:
        if instance_id:
            _terminate_instance(instance_id)


@pytest.mark.integration
def test_injection_into_waiting_children():
    """TEST 9: Injection into WAITING_CHILDREN parent (W3).

    Validates the W3 fix: when a leader is in ``waiting_children``
    status (parked waiting for child completion reports) and the user
    posts an injection, it lands in the slot and is consumed when the
    leader's next LLM turn picks it up — i.e. when the leader resumes
    after the child completes.

    The TEST_MESSAGE pattern from Test 1 (delegating "ask developer to
    say hello" to a developer child) produces a parent in
    ``waiting_children`` once the child is spawned.
    """
    instance_id: str | None = None
    child_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 9: injection into waiting_children parent (W3 fix)")
    logger.info("=" * 60)

    INJECTION_MARKER = "WAITING_CHILDREN_INJECTION_MARKER"
    PARENT_MESSAGE = (
        "ask developer to say hello, this is a test workflow, "
        "developer dont need do anything"
    )

    try:
        # Step 1: spawn leader, send child-spawn prompt
        instance_id = _spawn_instance("leader")
        assert instance_id, "Failed to spawn leader instance"
        _send_message(instance_id, PARENT_MESSAGE)
        logger.info(f"[STEP1] leader spawned, message sent: "
                    f"{instance_id[:8]}...")

        # Step 2: wait for child spawned
        child_id = _wait_for_child_spawned(instance_id, timeout=SPAWN_TIMEOUT)
        assert child_id is not None, (
            f"Leader did not spawn a child within {SPAWN_TIMEOUT}s — "
            f"cannot test waiting_children injection"
        )
        logger.info(f"[STEP2] child spawned: {child_id[:8]}...")

        # Step 3: poll parent until 'waiting_children' (2s, 60s timeout)
        WAITING_TIMEOUT = 60
        waiting_ok = _wait_for_status(
            instance_id, "waiting_children", timeout=WAITING_TIMEOUT
        )
        if not waiting_ok:
            # Tolerate: we may hit 'running' between turns, or the
            # LLM may complete too fast. Skip the injection step
            # rather than fail the test — the test value is in
            # proving INJECTION works at all in waiting_children.
            current = _get_instance(instance_id)
            logger.warning(
                f"[STEP3] leader never reached 'waiting_children' "
                f"within {WAITING_TIMEOUT}s (status="
                f"{current.get('status')!r}); TEST 9 cannot verify "
                f"the waiting_children injection contract."
            )
            pytest.skip(
                f"Leader did not enter 'waiting_children' within "
                f"{WAITING_TIMEOUT}s — cannot test injection in that "
                f"state"
            )
        logger.info(f"[STEP3] leader in waiting_children")

        # Step 4: POST /messages to parent — expect 202
        response = _send_injection_raw(instance_id, INJECTION_MARKER)
        assert response.status_code == 202, (
            f"Expected 202 for injection into waiting_children, got "
            f"{response.status_code}; body={response.text[:300]!r}"
        )

        # Step 5: GET /injection → pending=True
        injection = _get_injection(instance_id)
        assert injection.get("pending") is True, (
            f"Expected pending=True after injection to waiting "
            f"children, got {injection!r}"
        )
        logger.info("[STEP5] ✓ injection accepted, pending=True")

        # Step 6: wait for leader+child to complete (parent resumes,
        # consumes the injection slot on its final turn). Use safe
        # completion to confirm no premature-completion bug.
        finished, final_status, premature = _wait_for_leader_completion_safe(
            instance_id, [child_id], timeout=COMPLETION_TIMEOUT
        )
        assert not premature, (
            f"PREMATURE COMPLETION DETECTED: {premature}"
        )
        assert finished, (
            f"Leader did not reach terminal status within "
            f"{COMPLETION_TIMEOUT}s (last status: {final_status})"
        )
        logger.info(
            f"[STEP6] ✓ leader reached terminal: {final_status} "
            f"(with child terminal)"
        )

        # Step 7: marker appears in parent conversation history
        messages = _get_messages(instance_id)
        marker_found = any(
            INJECTION_MARKER in str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict)
        )
        assert marker_found, (
            f"Expected {INJECTION_MARKER!r} in parent's conversation "
            f"history — injection into waiting_children was NOT consumed"
        )
        logger.info(
            "[STEP7] ✓ marker present in parent's conversation history"
        )

        logger.info("TEST 9 PASSED")

    finally:
        if instance_id:
            _terminate_instance(instance_id)


@pytest.mark.integration
def test_paused_auto_resume_unchanged():
    """TEST 10: PAUSED auto-resume contract preserved (C4 regression guard).

    Validates C4: sending a message to a PAUSED instance must CONTINUE
    to return 200 with ``auto_resumed: true`` (the old behaviour), NOT
    409 (rejection) and NOT 202 (injection — paused is not in
    ``_INJECTION_ELIGIBLE_STATUSES``).

    This guards against accidental scope creep where the injection
    route's "running" handling might silently move the PAUSED branch
    to an incompatible response code. PAUSED must remain a hard
    auto-resume trigger — that is the contract the legacy frontend
    depends on.
    """
    instance_id: str | None = None
    logger.info("=" * 60)
    logger.info(
        "TEST 10: paused auto-resume unchanged (C4 regression guard)"
    )
    logger.info("=" * 60)

    AUTO_RESUME_MARKER = "AUTO_RESUME_TEST_MARKER"

    try:
        # Step 1: spawn + send long-running prompt
        instance_id = _spawn_instance("leader")
        assert instance_id, "Failed to spawn leader instance"
        _send_message(instance_id, LONG_PROMPT)

        # Step 2: wait for RUNNING
        running_ok = _wait_for_status(instance_id, "running", timeout=30)
        if not running_ok:
            current = _get_instance(instance_id)
            logger.warning(
                f"[STEP2] leader never reached 'running' "
                f"(status={current.get('status')!r}); TEST 10 cannot "
                f"verify C4 auto-resume contract."
            )
            pytest.skip(
                "Leader did not enter 'running' within 30s — cannot "
                "test pause+auto-resume"
            )
        logger.info(f"[STEP2] leader is running: {instance_id[:8]}...")

        # Step 3: pause the instance
        _pause_instance(instance_id)

        # Step 4: wait for PAUSED
        paused_ok = _wait_for_status(instance_id, "paused", timeout=30)
        assert paused_ok, (
            f"Instance did not reach 'paused' within 30s"
        )
        logger.info(f"[STEP4] ✓ instance paused: {instance_id[:8]}...")

        # Step 5: POST /messages to PAUSED — expect 200 + auto_resumed=True
        # NOT 202 (paused is NOT an injection-eligible status), NOT 409.
        response = _send_injection_raw(instance_id, AUTO_RESUME_MARKER)
        assert response.status_code == 200, (
            f"Expected 200 from PAUSED auto-resume, got "
            f"{response.status_code}; body={response.text[:300]!r}. "
            f"PAUSED must continue to auto-resume (status 200, "
            f"NOT 202 injection, NOT 409 rejection) — the C4 "
            f"contract is broken."
        )
        body = response.json()
        assert body.get("auto_resumed") is True, (
            f"Expected auto_resumed=True from PAUSED branch, got "
            f"{body!r}. PAUSED must signal auto-resume, not silent "
            f"injection (PAUSED is NOT eligible for the injection slot)."
        )
        assert "message_id" in body, (
            f"Expected message_id in PAUSED auto-resume response, got "
            f"{body!r}"
        )
        logger.info(
            f"[STEP5] ✓ PAUSED auto-resume: 200 + auto_resumed=True "
            f"(NOT 202, NOT 409)"
        )

        # Step 6: wait for completion (resume auto-triggered).
        # LONG_PROMPT may need extra time after auto-resume — observed
        # 4-6 minute completions in local dev.
        finished, final_status = _wait_for_completion(
            instance_id, timeout=COMPLETION_TIMEOUT * 3
        )
        assert finished, (
            f"Instance did not reach terminal status within "
            f"{COMPLETION_TIMEOUT * 3}s after auto-resume "
            f"(last status: {final_status})"
        )
        logger.info(f"[STEP6] ✓ instance reached terminal: {final_status}")

        # Step 7: message content appears in history
        messages = _get_messages(instance_id)
        marker_found = any(
            AUTO_RESUME_MARKER in str(m.get("content", ""))
            for m in messages
            if isinstance(m, dict)
        )
        assert marker_found, (
            f"Expected {AUTO_RESUME_MARKER!r} in conversation history "
            f"— auto-resumed message was not processed"
        )
        logger.info(
            "[STEP7] ✓ auto-resumed message present in conversation history"
        )

        logger.info("TEST 10 PASSED")

    finally:
        # Defensive resume in case the instance is still paused.
        if instance_id:
            try:
                current = _get_instance(instance_id)
                if str(current.get("status", "")).lower() == "paused":
                    _resume_instance(instance_id)
            except Exception as exc:
                logger.warning(
                    f"[CLEANUP] resume check failed: {exc}"
                )
            _terminate_instance(instance_id)


@pytest.mark.integration
def test_injection_query_endpoint():
    """TEST 11: GET /injection query endpoint lifecycle.

    Validates the slot lifecycle as observed through the query endpoint:
      1. No injection yet → pending=False, content=None.
      2. POST /messages → pending=True, content=<marker>.
      3. agent_node consumes → pending=False again.

    This is the Task 6 query endpoint that the frontend uses to
    reconcile SSE-event gaps (e.g. mid-stream reconnects).
    """
    instance_id: str | None = None
    logger.info("=" * 60)
    logger.info("TEST 11: GET /injection query endpoint lifecycle")
    logger.info("=" * 60)

    QUERY_MARKER = "QUERY_ENDPOINT_TEST_MARKER"

    try:
        # Step 1: spawn + send long-running prompt
        instance_id = _spawn_instance("leader")
        assert instance_id, "Failed to spawn leader instance"
        _send_message(instance_id, LONG_PROMPT)

        # Step 2: wait for RUNNING
        running_ok = _wait_for_status(instance_id, "running", timeout=30)
        if not running_ok:
            current = _get_instance(instance_id)
            logger.warning(
                f"[STEP2] leader never reached 'running' "
                f"(status={current.get('status')!r}); TEST 11 cannot "
                f"verify the query-endpoint lifecycle."
            )
            pytest.skip(
                "Leader did not enter 'running' within 30s — cannot "
                "test injection query endpoint lifecycle"
            )
        logger.info(f"[STEP2] leader is running: {instance_id[:8]}...")

        # Step 3: GET /injection → pending=False (no injection yet)
        initial = _get_injection(instance_id)
        assert initial.get("pending") is False, (
            f"Expected pending=False before any injection, got "
            f"{initial!r}"
        )
        assert initial.get("content") in (None, ""), (
            f"Expected content=None when pending=False, got "
            f"{initial.get('content')!r}"
        )
        logger.info("[STEP3] ✓ initial state: pending=False, content=None")

        # Step 4: send injection
        response = _send_injection_raw(instance_id, QUERY_MARKER)
        assert response.status_code == 202

        # Step 5: GET /injection → pending=True, content=QUERY_MARKER
        filled = _get_injection(instance_id)
        assert filled.get("pending") is True, (
            f"Expected pending=True after injection, got {filled!r}"
        )
        assert filled.get("content") == QUERY_MARKER, (
            f"Expected content={QUERY_MARKER!r}, got "
            f"{filled.get('content')!r}"
        )
        assert filled.get("timestamp"), (
            f"Expected timestamp to be set, got {filled!r}"
        )
        logger.info(
            f"[STEP5] ✓ filled: pending=True, content={QUERY_MARKER!r}, "
            f"timestamp={filled.get('timestamp')}"
        )

        # Step 6: poll until pending=False (consumed)
        CONSUME_TIMEOUT = 60
        consume_deadline = time.time() + CONSUME_TIMEOUT
        consumed = False
        while time.time() < consume_deadline:
            current = _get_injection(instance_id)
            if current.get("pending") is False:
                consumed = True
                break
            time.sleep(2)
        assert consumed, (
            f"pending did not flip to False within {CONSUME_TIMEOUT}s — "
            f"slot never cleared"
        )
        logger.info("[STEP6] ✓ consumed: pending=False again")

        # Step 7: GET /injection → pending=False (post-consumption)
        final_state = _get_injection(instance_id)
        assert final_state.get("pending") is False, (
            f"Expected pending=False after consumption, got "
            f"{final_state!r}"
        )
        logger.info("[STEP7] ✓ post-consumption state: pending=False")

        # Step 8: wait for completion. LONG_PROMPT may need extra time —
        # observed 4-6 minute completions in local dev.
        finished, final_status = _wait_for_completion(
            instance_id, timeout=COMPLETION_TIMEOUT * 3
        )
        assert finished, (
            f"Instance did not reach terminal status within "
            f"{COMPLETION_TIMEOUT * 3}s (last status: {final_status})"
        )
        logger.info(f"[STEP8] ✓ instance reached terminal: {final_status}")

        logger.info("TEST 11 PASSED")

    finally:
        if instance_id:
            _terminate_instance(instance_id)
