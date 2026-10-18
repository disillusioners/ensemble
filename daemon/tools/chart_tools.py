"""Chart generation tools for producing validated Mermaid diagrams.

Mirrors the closure-injection pattern of ``daemon.tools.knowledge_tools``:
``create_chart_tools(manager, current_instance_id)`` is invoked from
``create_instance_tools`` to assemble the per-instance tool list. The
generated ``generate_chart`` tool delegates to the ``charter`` agent via
``invoke_agent_and_wait`` and returns the validated Mermaid output.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.utils import invoke_agent_and_wait
# D5 — chart_render kind hint. The bash subprocesses the charter
# agent spawns (chromium / mmdc / shell helper) are bash children
# of the charter's instance graph. Pushing
# ``_PID_KIND_HINT.set(KIND_CHART_RENDER)`` around the
# ``invoke_agent_and_wait`` call propagates the kind into the
# charter's bash lane via the ContextVar copy — every bash child
# the charter spawns during this window registers with
# ``kind='chart_render'`` rather than the default
# ``kind='bash'``. Token is restored in finally so the hint does
# not leak into a sibling chart call or a follow-up bash tool
# call from the same parent task.
from daemon.tools.bash import KIND_CHART_RENDER, _PID_KIND_HINT

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# ── Charter-reuse module state ────────────────────────────────────────────────
#
# T5 — in-flight reuse guard, keyed by charter instance id. A second
# ``generate_chart`` call targeting a charter that is ALREADY being waited on
# is rejected with the busy error instead of registering a second waiter:
# two waiters on one ``instance_id`` share a single ``asyncio.Event``
# (``daemon/services/completion_registry.py``), so the second waiter would
# wake on the FIRST caller's completion and return a stale result for its
# own message (event coalescing). The check fires BEFORE
# ``CompletionRegistry.register()`` with no ``await`` between the check and
# the set-add — a yield in between would let a second caller slip through.
_inflight_reuse: set[str] = set()

# T6 — per-charter ERROR/FAILED revive counter (precedent:
# ``daemon/manager.py:773`` ``_agent_tool_revive_counts``). Scope mirrors the
# vetted v1-scope fix at ``daemon/manager.py:2876-2886``: an ERROR/FAILED
# prior status consumes one revive; the NEXT discovery hit on the same
# charter respawns fresh (bounded thrash). COMPLETED/TERMINATED never touch
# this counter (free revives).
#
# SEPARATE MECHANISM from the agent-tool ReviveGuard: programmatic paths
# never call ``note_agent_tool_revive`` (``daemon/manager.py:2816``) and
# never read/write ``manager._agent_tool_revive_counts``. This dict is
# chart-path-only, in-memory (lost on restart — accepted, same precedent),
# and invisible to the agent-tool revive budget.
_reuse_revive_attempts: dict[str, int] = {}

# d5_timeout — render_image=True extends the charter wait budget so a cold
# chromium/puppeteer bootstrap fits INSIDE the call. render_image=False
# (default) keeps the normal validate-only budget. Hoisted as module
# constants so the three call sites (fresh path, reuse path default,
# ``_full_doc_``) stay in lock-step. Source: user addendum via Discord
# 2026-10-05.
_RENDER_TIMEOUT_S = 1200.0   # 20 min — render_image=True (cold chromium fits)
_DEFAULT_TIMEOUT_S = 600.0   # 10 min — render_image=False (validate-only)

# Busy-reject message — hoisted so the two return sites stay in lock-step.
# Tests pin this string verbatim (``tests/test_chart_tools.py:_BUSY_STRING``);
# any copy-edit must update both. The ``_full_doc_`` docstring mention
# (~:558) is left literal as written.
_BUSY_MSG = "Error: Charter busy; pass fresh=True for parallel charts."

# ── O2 — chromium render concurrency gate (resource-cost-control) ────────
#
# CEREMONY-GATED posture: the default cap below ships IN THIS BRANCH and
# takes live effect only at the next promote ceremony (a daemon restart
# resolves the knob and builds the gate). This work performed ZERO
# live-host mutations — no live renders, no service restarts, no config
# writes against the live host.
#
# WHY: charter frontend renders spawn chromium children at 150-400 MB
# EACH with no global cap (verified at HEAD 4d572f561: zero semaphore
# coverage on the render path). Stacked renders are the prime suspect
# for the 2026-10-17 OOM night (/system.slice peak ≈9.6 GB ≈ 97 % of the
# box; kernel OOM-killed the daemon 23:19:14Z).
#
# SATURATION POLICY (chosen: BOUNDED WAIT QUEUE, documented per the O2
# brief): a render (``render_image=True``) must acquire a slot from a
# process-wide ``asyncio.Semaphore`` sized by
# ``limits.chart_render_concurrency`` (default 2; the consumption-side
# reader clamps any out-of-range config into [1, 4] with a WARN).
# Waiting is bounded TWICE over so the tool lane can never hang:
#   * at most ``_RENDER_QUEUE_MAX`` (4) waiters may queue — beyond that
#     the call FAILS FAST with a typed error envelope (the wait queue
#     itself is bounded; never unbounded growth), and
#   * each waiter may wait at most ``_RENDER_SLOT_WAIT_S`` (120 s) for
#     a slot — beyond that it also returns a typed error envelope.
# A render that cannot get a slot therefore always returns a clear
# error envelope instead of hanging its turn. The slot is released in
# ``finally`` on EVERY exit path, so no path can deadlock the gate.
# Non-render paths (``render_image=False``) never touch the gate and
# never consume slots.
#
# TIMEOUT-KILL AUDIT (found vs fixed): the pre-existing enforcement —
# ``invoke_agent_and_wait`` → ``registry.wait_for(timeout)`` →
# FIRE-AND-FORGET ``_try_terminate_orphan`` — ABANDONED the child: the
# tool returned its timeout envelope while the charter and its
# chromium/mmdc bash children could keep running unboundedly (the
# terminate was best-effort background work whose failure is silently
# swallowed — "stale task recovery or watchdog" was the only backstop).
# The charter-side bash lane itself DOES kill children on timeout and
# on cancellation (``bash.py`` ``_kill_process``: SIGTERM → 5 s grace →
# SIGKILL, and the shielded cancellation handler) — the gap was that
# nothing reliably DROVE that lane at the render-call boundary. The fix
# makes the kill bounded and AWAITED (see ``_kill_render_child``): on
# expiry we await ``manager.terminate_instance(...)`` under a hard
# grace cap, then belt-and-braces SIGKILL any bash process groups the
# cascade missed, and log the outcome. The 1200 s render budget
# semantics are UNCHANGED — only what happens AT expiry is hardened.
_DEFAULT_RENDER_CONCURRENCY = 2
_RENDER_CONCURRENCY_FLOOR = 1
_RENDER_CONCURRENCY_CEILING = 4
_RENDER_SLOT_WAIT_S = 120.0   # bounded slot wait — ≪ the 600/1200 s budgets
_RENDER_QUEUE_MAX = 4         # bounded waiter queue — fail-fast beyond this
_RENDER_KILL_GRACE_S = 30.0   # hard cap on the awaited terminate at expiry

# Process-lifetime gate state. Built lazily on the FIRST render call
# (inside the running loop — asyncio primitives must not be constructed
# at import time) and intentionally NOT rebuilt on config change: the
# knob is ``requires_restart=True`` (managed-settings ladder), so a
# mid-process config edit picks up at the next daemon restart.
_render_gate: asyncio.Semaphore | None = None
_render_gate_cap = 0
_render_waiters = 0

# Statuses at which a child is left alone at expiry: terminal statuses
# have already had their lifecycle cleanup run, and PAUSED reflects
# explicit operator intent (never fight a pause with a kill).
_NO_KILL_STATUSES = frozenset(
    {
        InstanceStatus.COMPLETED.value,
        InstanceStatus.TERMINATED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
        InstanceStatus.PAUSED.value,
    }
)


def _effective_render_concurrency(manager: "InstanceManager") -> int:
    """Effective chromium render concurrency cap (O2 knob reader).

    Mirrors the O3 ``_effective_instance_cache_ttl_hours`` consumption
    pattern: read ``manager.config.limits.chart_render_concurrency``
    (mutated in place at boot by ``apply_db_settings_overrides``), fall
    back to ``_DEFAULT_RENDER_CONCURRENCY`` when the attribute is
    missing/non-numeric, and CLAMP any out-of-range value into
    [``_RENDER_CONCURRENCY_FLOOR``, ``_RENDER_CONCURRENCY_CEILING``]
    with a WARN. Clamping at the consumer is deliberate: the managed
    settings ladder validates ``ge=1`` per the registry's v1 constraint
    model (no ``le``), while the gate itself must stay sane regardless
    of what a DB/env/yaml tier says.
    """
    raw = getattr(
        getattr(getattr(manager, "config", None), "limits", None),
        "chart_render_concurrency",
        None,
    )
    if raw is None:
        return _DEFAULT_RENDER_CONCURRENCY
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "chart render concurrency: non-numeric config %r — using "
            "default %d",
            raw,
            _DEFAULT_RENDER_CONCURRENCY,
        )
        return _DEFAULT_RENDER_CONCURRENCY
    if (
        value < _RENDER_CONCURRENCY_FLOOR
        or value > _RENDER_CONCURRENCY_CEILING
    ):
        clamped = min(
            max(value, _RENDER_CONCURRENCY_FLOOR),
            _RENDER_CONCURRENCY_CEILING,
        )
        logger.warning(
            "chart render concurrency: config %d outside [%d, %d] — "
            "clamped to %d",
            value,
            _RENDER_CONCURRENCY_FLOOR,
            _RENDER_CONCURRENCY_CEILING,
            clamped,
        )
        return clamped
    return value


def _get_render_gate(manager: "InstanceManager") -> asyncio.Semaphore:
    """Return the process-lifetime render gate, building it on first use.

    The cap is resolved from config ONCE per process (the knob is
    requires-restart); see the O2 block above for the posture.
    """
    global _render_gate, _render_gate_cap
    if _render_gate is None:
        cap = _effective_render_concurrency(manager)
        _render_gate = asyncio.Semaphore(cap)
        _render_gate_cap = cap
        logger.info(
            "chart render gate: created (cap=%d, queue_max=%d, "
            "slot_wait_s=%.0f, ceremony_gated=True)",
            cap,
            _RENDER_QUEUE_MAX,
            _RENDER_SLOT_WAIT_S,
        )
    return _render_gate


def _reset_render_gate_for_tests() -> None:
    """Reset the process-lifetime gate state (tests only).

    asyncio primitives are loop-bound, so each test's fresh event loop
    needs a fresh gate. Mirrors the module-global reset convention used
    by ``tests/test_chart_tools_reuse_integration.py``.
    """
    global _render_gate, _render_gate_cap, _render_waiters
    _render_gate = None
    _render_gate_cap = 0
    _render_waiters = 0


async def _acquire_render_slot(
    manager: "InstanceManager", caller_id: str
) -> str | None:
    """Bounded acquire of a render slot (O2 saturation policy).

    Returns ``None`` when a slot was acquired, or a typed error
    envelope string when the bounded queue is full or the bounded slot
    wait expired. Never raises; never waits unboundedly.
    """
    global _render_waiters
    if _render_waiters >= _RENDER_QUEUE_MAX:
        logger.warning(
            "chart render gate saturated (queue full): caller=%s "
            "waiters=%d queue_max=%d cap=%d",
            caller_id[:8],
            _render_waiters,
            _RENDER_QUEUE_MAX,
            _render_gate_cap,
        )
        return (
            "Error: Chart render lane saturated — "
            f"{_render_waiters} renders already queued (queue cap "
            f"{_RENDER_QUEUE_MAX}, concurrency cap {_render_gate_cap}). "
            "Retry shortly."
        )
    gate = _get_render_gate(manager)
    _render_waiters += 1
    try:
        try:
            await asyncio.wait_for(gate.acquire(), timeout=_RENDER_SLOT_WAIT_S)
        except asyncio.TimeoutError:
            logger.warning(
                "chart render gate slot wait expired: caller=%s waited="
                "%.0fs cap=%d — returning error envelope",
                caller_id[:8],
                _RENDER_SLOT_WAIT_S,
                _render_gate_cap,
            )
            return (
                "Error: Chart render lane busy — no slot became free "
                f"within {_RENDER_SLOT_WAIT_S:.0f}s (concurrency cap "
                f"{_render_gate_cap}). Retry shortly."
            )
    finally:
        _render_waiters -= 1
    logger.info(
        "chart render gate: slot acquired caller=%s cap=%d in_flight=%d",
        caller_id[:8],
        _render_gate_cap,
        _render_gate_cap - gate._value + 1,  # type: ignore[attr-defined]
    )
    return None


def _release_render_slot() -> None:
    """Release a previously acquired render slot (every-exit path)."""
    if _render_gate is not None:
        _render_gate.release()
        logger.debug("chart render gate: slot released")

CATEGORY_NAME = "Chart"
CATEGORY_DOC = """\
Chart generation tools for producing validated Mermaid diagrams.

generate_chart() delegates to the Charter agent which produces
syntax-validated Mermaid diagrams across flowchart, sequence, class,
er, state, and gantt types.
"""


def _find_reusable_charter(manager: "InstanceManager", caller_id: str) -> Instance | None:
    """Find the caller's most recent charter child spawned as a tool.

    Pure query-discovery (adjudicated P2 — the sole tracking store): the
    caller's ``instances`` rows are walked via ``get_children``
    (``parent_id`` is permanent across terminate-to-revive) and filtered to
    charter children flagged ``invoked_as_tool`` (flag stamped at
    ``instance_lifecycle.py:1798-1799`` by ``invoke_agent_and_wait``'s
    spawn).

    Determinism (W2 — implementation-mandatory): ``get_children`` ships NO
    ``ORDER BY`` and ``last_activity_at`` is NULLABLE, so ordering happens
    Python-side — ``last_activity_at`` desc with NULL treated as OLDEST
    (a NULL-activity charter never silently wins), then ``created_at`` desc,
    then row id. Total, deterministic order.

    Any repository error degrades to ``None`` → the caller falls back to a
    fresh spawn; discovery failure never raises to the LLM.

    Args:
        manager: The InstanceManager instance.
        caller_id: The calling instance id (scope key — adjudicated P3=(a):
            per-caller-instance; the "key" IS this ``get_children`` argument).

    Returns:
        The latest reusable charter row, or ``None``.
    """
    try:
        rows = manager._instance_repository.get_children(caller_id)
        candidates = [
            row
            for row in rows
            if getattr(row, "agent_id", None) == "charter"
            and (getattr(row, "instance_metadata", None) or {}).get(
                "invoked_as_tool"
            )
        ]
        if not candidates:
            return None

        def _sort_key(row):
            # Direction-uniform string key so ONE max() implements the spec:
            # ``last_activity_at`` desc (ISO strings compare chronologically;
            # NULL → "" sorts oldest), then ``created_at`` desc, then row id.
            # S8: ``created_at`` may arrive as either an ISO string (SQLite/JSON
            # origin) or a ``datetime`` (in-memory row origin) depending on the
            # repository path; normalize to a comparable ISO string with the
            # same NULL/empty → oldest treatment as ``last_activity_at`` so a
            # mixed-type compare never raises TypeError inside the discovery
            # ``try`` (silent fresh-spawn degradation hazard).
            def _iso_or_empty(value):
                if value is None or value == "":
                    return ""
                if isinstance(value, str):
                    return value
                return value.isoformat()

            activity = row.last_activity_at
            if activity is None:
                activity_key = ""
            elif isinstance(activity, str):
                activity_key = activity
            else:
                activity_key = activity.isoformat()
            return (
                activity_key != "",  # NULL-activity rows never win
                activity_key,
                _iso_or_empty(row.created_at),
                row.instance_id or "",
            )

        return max(candidates, key=_sort_key)
    except Exception:
        logger.debug(
            "generate_chart: charter discovery failed for caller %s...",
            caller_id[:8],
            exc_info=True,
        )
        return None


async def _reuse_charter(
    manager: "InstanceManager",
    charter_id: str,
    message: str,
    caller_id: str,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> tuple[str, str]:
    """Register → enqueue → wait on the caller's EXISTING charter instance.

    Chart-tools-local mirror of the ``invoke_agent_and_wait`` wait-block
    (``daemon/utils.py:672-740``) minus the spawn: the reuse path rides the
    service-side revive-on-send — ``enqueue_message`` flips a terminal
    charter back to RUNNING and its checkpoint reloads
    (``instance_messaging.py``) — so no new instance is created.

    Deviations from the fresh path, both deliberate:
    * NO ``_invoke_semaphore`` acquire — nothing is spawned, so there is no
      worker-pool contention with ``explore`` / ``explain_image``.
    * Timeout does NOT terminate the charter (M8): the charter is
      shared/durable; the busy guard rejects a still-running charter and
      buffered completion absorbs a late finish.

    Args:
        manager: The InstanceManager instance.
        charter_id: The discovered charter instance to reuse.
        message: The refinement request for the charter agent.
        caller_id: The calling instance id (drives the enqueue source tag).
        timeout: Maximum seconds to wait for the charter's completion.

    Returns:
        ``(content, charter_id)`` on success; ``("Error: ...", charter_id)``
        on busy-reject, pause-reject, timeout, or agent failure.
    """
    # Lazy import (same shape as ``daemon/utils.py:641``) so the patched
    # ``daemon.services.completion_registry.get_completion_registry`` module
    # attribute is re-read on every call.
    from daemon.services.completion_registry import get_completion_registry

    # 1. Status pre-check — authoritative re-read (discovery may be stale).
    #    COMPLETED/TERMINATED proceed free; RUNNING is already busy; PAUSED
    #    is busy-rejected WITHOUT enqueue (M14 — enqueue would sit PENDING
    #    until an operator resumes the charter while the tool wait burns);
    #    ERROR/FAILED increment the local revive counter (the consume side
    #    of the T6 policy — the miss side is decided by the caller);
    #    any other status (IDLE, WAITING_CHILDREN, ...) enqueues cleanly.
    prior_status = None
    try:
        row = manager._instance_repository.get(charter_id)
        if row is not None:
            prior_status = (row.status or "").lower()
    except Exception:
        prior_status = None

    if prior_status == InstanceStatus.RUNNING.value:
        logger.warning(
            "generate_chart: caller=%s charter=%s mode=%s prior_status=%s",
            caller_id[:8],
            charter_id[:8],
            "busy-reject",
            prior_status,
        )
        return (
            _BUSY_MSG,
            charter_id,
        )
    if prior_status == InstanceStatus.PAUSED.value:
        logger.warning(
            "generate_chart: caller=%s charter=%s mode=%s prior_status=%s",
            caller_id[:8],
            charter_id[:8],
            "busy-reject",
            prior_status,
        )
        return (
            "Error: Charter is paused; resume it or pass fresh=True for a "
            "new charter.",
            charter_id,
        )

    # 2. Busy guard (T5) — check BEFORE add, no await between the membership
    #    check and the set-add (F10 / S4 busy-guard atomicity invariant):
    #    a single asyncio event loop is assumed, and only sync code
    #    (``logger.warning`` + ``get_completion_registry``) sits between
    #    the check and the add inside the ``try`` below — a yield in
    #    between would let a second caller observe the same empty slot
    #    and slip a second waiter in. S6 — the guard is keyed by charter
    #    instance id only; caller identity is NOT part of the key (two
    #    callers targeting the same charter id share the busy bit, which
    #    matches the single-event coalescing contract documented in the
    #    module-level comment on ``_inflight_reuse``).
    if charter_id in _inflight_reuse:
        logger.warning(
            "generate_chart: caller=%s charter=%s mode=%s prior_status=%s",
            caller_id[:8],
            charter_id[:8],
            "busy-reject",
            prior_status or "none",
        )
        return (
            _BUSY_MSG,
            charter_id,
        )

    registry = get_completion_registry()
    try:
        # S2 — set-add INSIDE the ``try`` so the ``finally`` cleanup
        # always discards the in-flight entry, even if the add itself
        # raises (defensive: a raise between add and try entry would
        # otherwise leak the entry until the next caller observed it).
        _inflight_reuse.add(charter_id)

        logger.info(
            "generate_chart: caller=%s charter=%s mode=%s prior_status=%s",
            caller_id[:8],
            charter_id[:8],
            "reuse",
            prior_status or "none",
        )

        # 3. Unregister-then-register — W1 stale-buffered completion fix.
        #    A buffered completion from a PREVIOUS turn (e.g. fresh-path
        #    600s-timeout unregister, or an external message completing
        #    the charter while no waiter is parked) sits in
        #    ``registry._buffered`` keyed by charter id. The next
        #    ``register()`` would consume that stale entry and set the
        #    event immediately (``completion_registry.py:79-85``), so
        #    ``wait_for`` below would return the OLD content instead of
        #    the NEW turn's result. ``unregister`` clears ``_buffered``
        #    (``completion_registry.py:188-202``), giving the new turn
        #    a clean slate. Safe under the T5 busy-guard invariant — one
        #    waiter per charter id at a time, so no other consumer is
        #    racing for the buffer slot.
        registry.unregister(charter_id)
        registry.register(charter_id)

        # 4. Enqueue on the EXISTING instance (ONLY existing kwargs — no
        #    facade change, M11).
        try:
            await manager.enqueue_message(
                instance_id=charter_id,
                message=message,
                source=f"internal_chart_reuse:{caller_id}",
                metadata={"chart_reuse": True},
            )
        except Exception as enqueue_err:
            # S7 — mirror the fresh-path never-raise contract
            # (``daemon/utils.py:706-735``): wrap the enqueue in a
            # catch-all so the LLM never sees a raw ``enqueue_message``
            # stack trace on the reuse path. Brief exception class +
            # message, paired with the ``charter_id`` so the caller can
            # still identify the instance.
            return (
                f"Error: {type(enqueue_err).__name__}: {enqueue_err}",
                charter_id,
            )

        # S1 — counter increment AFTER successful ``enqueue_message``
        # (aligns with the vetted post-enqueue precedent at
        # ``daemon/tools/instance.py:3009-3012``). A transient
        # ``enqueue_message`` exception above leaves the child eligible
        # for a future revive attempt — the one-shot budget is only
        # consumed when the dispatch actually happened.
        if prior_status in (
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        ):
            # ERROR/FAILED revive consumes the one-shot budget (T6). This
            # counter is SEPARATE from the agent-tool ReviveGuard — see the
            # module-level comment on ``_reuse_revive_attempts``. S5 —
            # growth is daemon-restart-bounded: this dict is in-memory,
            # lost on restart, and accepted (mirrors the precedent at
            # ``daemon/manager.py:773`` ``_agent_tool_revive_counts``).
            _reuse_revive_attempts[charter_id] = (
                _reuse_revive_attempts.get(charter_id, 0) + 1
            )

        # 5. Re-register if consumed — S3 comment fix. The re-register
        #    guards the post-enqueue consumption race: the enqueue above
        #    can flip terminal→RUNNING and the charter's existing
        #    completion (e.g. from a side channel) may have drained the
        #    event the step-3 register just set. Re-registering restores
        #    the wait surface for the ``wait_for`` below so a subsequent
        #    completion is captured. (The step-3 buffered-drain
        #    misattribution hazard is closed separately; this block only
        #    covers the mid-flight drain after enqueue.)
        if not registry.is_registered(charter_id):
            registry.register(charter_id)

        # 6. Wait for completion (success or error).
        #
        # W2 — accepted interleaving. ``CompletionRegistry`` keys one
        # ``asyncio.Event`` per instance id; two completions landing on
        # the same id share the event (one event set is binary). When an
        # external message completes the same charter while we are
        # parked here, the resulting event.set() wakes THIS waiter with
        # THAT other message's completion result — bounded by the T5
        # busy guard to same-charter turns (one reuse waiter per
        # charter at a time, so cross-waiter mixing is impossible). The
        # full fix is a work-id-scoped wait: key the event by
        # (instance_id, work_id) so the wake can be matched to the
        # originating dispatch. Deferred — see W2 in
        # ``.agents/shared/planning/generate-chart-charter-reuse/
        # decisions.md`` (post-wake staleness check would need plumbing
        # through ``completion_registry.complete`` / chart-local stub).
        result = await registry.wait_for(charter_id, timeout=timeout)

        if result is None:
            # Timeout — do NOT terminate (M8).
            return (
                f"Error: Charter timed out after {timeout}s. "
                f"Instance {charter_id[:8]}... may still be running.",
                charter_id,
            )

        if result.is_error:
            # Agent errored out — it's already in ERROR status.
            return (f"Error: Agent failed. {result.content}", charter_id)

        # Success
        return (result.content or "", charter_id)
    finally:
        # 7. Always cleanup.
        registry.unregister(charter_id)
        _inflight_reuse.discard(charter_id)


# ── O2 — timeout-kill helpers ────────────────────────────────────────────────
#
# Found (audit): the fresh-path timeout enforcement was fire-and-forget —
# ``invoke_agent_and_wait`` scheduled ``manager.terminate_instance`` via
# ``asyncio.ensure_future`` and returned the timeout envelope immediately,
# so a failed/slow terminate left the charter's chromium/mmdc tree alive
# with no synchronous signal to the caller. The charter-side bash lane
# already kills children correctly on timeout and cancellation; the gap
# was that nothing reliably DROVE that lane at the render-call boundary.
# Fixed (below): bounded, awaited kill at expiry + belt-and-braces
# bash-group sweep, with the outcome logged.


def _fetch_child_status(
    manager: "InstanceManager", child_id: str
) -> tuple[bool, str | None]:
    """Read a child row's status. ``(exists, status_lower_or_None)``.

    Repository errors degrade to ``(False, None)`` — a kill decision
    must never be blocked by a read failure, but an unknown row is also
    never killed (nothing was spawned).
    """
    try:
        row = manager._instance_repository.get(child_id)
    except Exception:
        return (False, None)
    if row is None:
        return (False, None)
    return (True, (getattr(row, "status", "") or "").lower())


async def _bash_sweep_render_tree(child_id: str) -> int:
    """SIGKILL any bash process groups still tracked for ``child_id``.

    Belt-and-braces behind the terminate cascade: the lifecycle
    terminate already sweeps the bash registry, but a cascade that
    failed/timed out leaves groups behind — each is a 150-400 MB
    chromium/mmdc tree. Never raises; returns the killed-group count.
    """
    try:
        from daemon.tools.bash import get_bash_process_registry

        return await get_bash_process_registry().cleanup_instance(child_id)
    except Exception as exc:
        logger.error(
            "chart render kill: bash sweep failed for child %s: %s: %s",
            child_id[:8],
            type(exc).__name__,
            exc,
        )
        return 0


async def _kill_render_child(
    manager: "InstanceManager", child_id: str | None
) -> None:
    """Bounded, AWAITED kill of a timed-out render child (fresh lane).

    Closes the fire-and-forget gap: when this returns, either the
    terminate cascade has run (graph-task cancel → the bash lane's own
    SIGTERM→SIGKILL handlers) or its failure is loudly logged — and the
    bash-group sweep has SIGKILLed anything the cascade missed. Terminal
    and PAUSED children are left alone (PAUSED = operator intent).
    """
    if not child_id:
        return
    exists, status = _fetch_child_status(manager, child_id)
    if not exists or (status or "") in _NO_KILL_STATUSES:
        return
    try:
        await asyncio.wait_for(
            manager.terminate_instance(child_id), timeout=_RENDER_KILL_GRACE_S
        )
        logger.warning(
            "chart render kill: terminated timed-out child %s (status=%s)",
            child_id[:8],
            status,
        )
    except asyncio.TimeoutError:
        logger.error(
            "chart render kill: terminate grace (%.0fs) expired for child "
            "%s — falling through to bash sweep",
            _RENDER_KILL_GRACE_S,
            child_id[:8],
        )
    except Exception as exc:
        logger.error(
            "chart render kill: terminate failed for child %s: %s: %s",
            child_id[:8],
            type(exc).__name__,
            exc,
        )
    killed = await _bash_sweep_render_tree(child_id)
    if killed:
        logger.warning(
            "chart render kill: SIGKILLed %d leftover bash group(s) of "
            "child %s",
            killed,
            child_id[:8],
        )


async def _sweep_render_process_tree(
    manager: "InstanceManager", charter_id: str
) -> None:
    """Sweep ONLY the process tree of a timed-out REUSED charter.

    M8 (charter shared/durable) is preserved: the charter INSTANCE is
    never terminated or state-mutated here — other callers may be
    queued on it and the service-side revive-on-send owns its state
    machine. Only the charter's tracked bash process groups (the
    chromium/mmdc render trees) are SIGKILLed so a wedged render cannot
    outlive the tool call burning memory. Called ONLY on the reuse
    lane's timeout envelope — busy/pause rejects never reach this
    (another waiter may own the in-flight render).
    """
    exists, status = _fetch_child_status(manager, charter_id)
    if not exists or (status or "") in _NO_KILL_STATUSES:
        return
    killed = await _bash_sweep_render_tree(charter_id)
    if killed:
        logger.warning(
            "chart render sweep (reuse, M8-safe): SIGKILLed %d bash "
            "group(s) of timed-out charter %s (instance state untouched)",
            killed,
            charter_id[:8],
        )


def create_chart_tools(manager: "InstanceManager", current_instance_id: str) -> list:
    """Create chart generation tools with injected manager reference.

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as parent
            for the spawned charter instance).

    Returns:
        List of tool functions: [generate_chart]
    """

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        try:
            # Use _instance_repository directly - get_instance() returns
            # CompiledStateGraph, not metadata.
            instance_meta = manager._instance_repository.get(current_instance_id)
            if instance_meta and instance_meta.project_id:
                return instance_meta.project_id
        except Exception:
            pass
        return None

    @register_tool_category("chart")
    @tool
    async def generate_chart(
        description: str,
        diagram_type: str = "flowchart",
        project_id: str | None = None,
        fresh: bool = False,
        render_image: bool = False,
    ) -> str:
        """Generate a validated Mermaid diagram by delegating to the Charter agent.

        Sends a structured request to the Charter agent, which produces
        syntax-validated Mermaid diagrams and returns them in a fenced
        ```` ```mermaid ```` code block with a brief explanation. Use this for
        any structural artifact: architectures, process flows, state machines,
        data models, timelines.

        By default the request REUSES the caller's most recent charter
        instance, so refinements are grounded in the prior diagram's
        checkpoint history; pass ``fresh=True`` to always spawn a new one.

        Args:
            description: What the diagram should show — the subject, scope,
                and structure to visualize. Be specific: name the nodes /
                actors / entities and the relationships between them.
            diagram_type: Type of Mermaid diagram to produce — one of
                "flowchart", "sequence", "class", "er", "state", "gantt".
                Defaults to "flowchart".
            project_id: Optional project ID. Auto-detected from context
                if not provided.
            fresh: Skip discovery and ALWAYS spawn a new charter instance.
                Defaults to False — successive calls refine the caller's
                most recent charter child (reuse is the default). Pass
                True for a parallel/independent chart or to start over.
            render_image: When True, the charter agent ALSO renders the
                diagram to a PNG and emits the ``<!-- ens-img:chart-render:<id> -->``
                marker so chat-source dispatchers can attach the image to
                the same message. Default False — the validated Mermaid
                block is the entire deliverable; no render, no marker, no
                tmp_images write. Pass True ONLY when the caller (a chat
                source or an explicit user image request) needs the image
                — the render budget extends the wait timeout from 600s to
                1200s so a cold chromium/puppeteer bootstrap fits inside
                the call. Web/UI contexts never need it.

        Returns:
            The Charter agent's response containing a validated ```` ```mermaid ````
            fenced code block and a brief explanation. On
            ``render_image=True`` the response also carries a trailing
            ``<!-- ens-img:chart-render:<id> -->`` marker line so chat
            dispatchers can attach the PNG.
        """
        pid = project_id or _get_project_id()

        # d5_timeout — differential by render_image (user addendum
        # 2026-10-05): render_image=True → 1200s so a cold
        # chromium/puppeteer bootstrap fits; render_image=False (default)
        # → 600s, no slot held open for validate-only. The charter-side
        # directive is threaded into the dispatch message below.
        timeout_s = _RENDER_TIMEOUT_S if render_image else _DEFAULT_TIMEOUT_S

        # ── O2 — bounded chromium render gate (renders only) ────────
        # Validate-only paths never consume a slot. Saturation policy:
        # bounded wait queue (queue cap + slot-wait cap) → typed error
        # envelope; see the O2 block at the module constants.
        render_slot_held = False
        if render_image:
            gate_error = await _acquire_render_slot(
                manager, current_instance_id
            )
            if gate_error is not None:
                return gate_error
            render_slot_held = True
        try:
            # Construct a structured prompt for the charter agent. Mirrors the
            # ``explore()`` style at knowledge_tools.py — short label-style header
            # lines so the agent has explicit context for type and project scope.
            # Message construction is IDENTICAL on the reuse and fresh paths (M9).
            # The RENDER_IMAGE directive is a test-pinnable contract — the charter
            # workflow.md Step 5 conditional gate parses this exact line and
            # skips the render/persist/marker step when the value is false (or
            # when the line is absent, which is the validated-Mermaid-only
            # default).
            chart_message = (
                f"Create a {diagram_type} diagram.\n\n"
                f"Description: {description}\n"
                f"RENDER_IMAGE: {str(render_image).lower()}\n"
            )
            if pid:
                chart_message += f"Project: {pid}\n"

            # Charter reuse (default): discover the caller's most recent charter
            # child and refine it in place. ``fresh=True`` skips discovery and
            # always spawns a new charter. Exactly ONE mode log line per call.
            mode_logged = False
            if not fresh:
                reusable = _find_reusable_charter(manager, current_instance_id)
                if reusable is not None:
                    charter_id = reusable.instance_id
                    prior_status = (reusable.status or "").lower()
                    if (
                        prior_status
                        in (
                            InstanceStatus.ERROR.value,
                            InstanceStatus.FAILED.value,
                        )
                        and _reuse_revive_attempts.get(charter_id, 0) >= 1
                    ):
                        # One revive already consumed for this charter (T6
                        # miss side, adjudicated P5) — respawn fresh. The
                        # respawn line IS this call's single mode log, so the
                        # generic fresh-spawn log below is skipped.
                        logger.info(
                            "generate_chart: caller=%s charter=%s mode=%s prior_status=%s",
                            current_instance_id[:8],
                            charter_id[:8],
                            "reuse-respawn-after-failure",
                            prior_status,
                        )
                        mode_logged = True
                    else:
                        content, _reused_charter_id = await _reuse_charter(
                            manager=manager,
                            charter_id=charter_id,
                            message=chart_message,
                            caller_id=current_instance_id,
                            timeout=timeout_s,
                        )
                        # O2 timeout-kill (reuse lane) — M8-safe: the
                        # charter instance is NEVER terminated here; only
                        # a TIMED-OUT render sweeps the charter's tracked
                        # bash process groups (chromium/mmdc). Busy/pause
                        # rejects never reach this (exact-prefix gated
                        # below — another waiter may own the render).
                        if content.startswith(
                            "Error: Charter timed out after"
                        ):
                            await _sweep_render_process_tree(
                                manager, _reused_charter_id
                            )
                        return content
            if not mode_logged:
                logger.info(
                    "generate_chart: caller=%s charter=%s mode=%s prior_status=%s",
                    current_instance_id[:8],
                    "spawn",
                    "fresh",
                    "none",
                )

            # Invoke charter agent synchronously — the tool waits for the
            # validated Mermaid output. Always returns ``(content, instance_id)``
            # tuple when ``return_instance_id=True``.
            #
            # Never-raise contract: a raise from ``invoke_agent_and_wait``
            # (transport error, internal failure) must not escape the tool
            # coroutine into the LLM tool loop — collapse it to an ``Error:``
            # string, mirroring the ``None``-content branch below.
            #
            # D5 — chart_render stamp seam (pinned line: 918 —
            # ``invoke_agent_and_wait(...)``). The ContextVar
            # ``_PID_KIND_HINT`` rides the asyncio context through
            # the ``invoke_agent_and_wait`` call; any bash
            # subprocess the charter instance spawns during this
            # window (chromium / mmdc / shell helpers) inherits
            # the hint via the ContextVar copy mechanism and
            # registers with ``kind='chart_render'`` rather than
            # the default ``kind='bash'``. Only push the hint when
            # ``render_image=True`` — the validate-only path
            # (``render_image=False``) never spawns a render and
            # tagging those bash children with the wrong kind
            # would muddy the sampler attribution. Token is
            # reset in finally so the hint does not leak into a
            # sibling chart call.
            kind_hint_token = None
            if render_image:
                kind_hint_token = _PID_KIND_HINT.set(KIND_CHART_RENDER)
            try:
                result, child_instance_id = await invoke_agent_and_wait(
                    manager=manager,
                    agent_id="charter",
                    message=chart_message,
                    project_id=pid,
                    parent_id=current_instance_id,
                    instance_name=f"chart-{description[:30]}",
                    timeout=timeout_s,
                    return_instance_id=True,
                )
            except Exception as exc:
                return f"Error: Charter agent invocation failed: {exc}"
            finally:
                if kind_hint_token is not None:
                    _PID_KIND_HINT.reset(kind_hint_token)

            # Handle error results — ``invoke_agent_and_wait`` returns
            # ``"Error: ..."`` on failure / timeout when ``return_instance_id`` is
            # True we still get the tuple; collapse to a single string for the
            # tool response. A ``None`` content means the agent never produced a
            # result (e.g. hard timeout during cleanup).
            # O2 timeout-kill (fresh lane): bounded, AWAITED kill of a
            # timed-out render child — closes the fire-and-forget gap so
            # the chromium/mmdc tree cannot outlive this tool call.
            if result is None or (
                isinstance(result, str)
                and result.startswith("Error: Agent timed out after")
            ):
                await _kill_render_child(manager, child_instance_id)
            if result is None:
                return "Error: Charter agent timed out or failed. Try a simpler description."
            return result
        finally:
            # O2 — every-exit release: no path may hold a render slot
            # past this function returning.
            if render_slot_held:
                _release_render_slot()

    generate_chart._full_doc_ = """\
Generate a validated Mermaid diagram by delegating to the Charter agent.

Sends a structured request to the Charter agent, which produces
syntax-validated Mermaid diagrams and returns them in a fenced code
block. The agent is responsible for:

1. Selecting the appropriate diagram type (if ``diagram_type`` is left
   implicit by the caller, charter will infer from the description).
2. Drafting the Mermaid syntax.
3. Validating via ``npx -y @mermaid-js/mermaid-cli``.
4. (Only on ``render_image=True``) rendering the PNG and persisting it
   via ``image_save``, then emitting the byte-exact
   ``<!-- ens-img:chart-render:<id> -->`` marker. The default
   (``render_image=False``) returns the validated Mermaid block only —
   no render, no PNG, no marker, no tmp_images write.
5. Returning the validated diagram with a brief explanation.

The tool blocks until the agent produces its final response. The wait
timeout is differential by ``render_image``: 600s (validate-only, the
default) or 1200s (render=True — a cold chromium/puppeteer bootstrap
fits inside the call). Pasted into your response without re-wrapping
or stripping the fence.

Args:
    description: What the diagram should show — the subject, scope, and
        structure to visualize. Be specific: name the nodes / actors /
        entities and the relationships between them. Example:
        "Create a flowchart TD showing the authentication request flow.
        Include: User, Auth Service, Token Store, Protected Resource, and
        the decision branches for valid/invalid tokens."
    diagram_type: Type of Mermaid diagram — one of "flowchart",
        "sequence", "class", "er", "state", "gantt". Defaults to
        "flowchart".
    project_id: Optional project ID. Auto-detected from current instance
        context if not provided.
    fresh: Skip discovery and ALWAYS spawn a new charter instance.
        Defaults to False — successive calls refine the caller's most
        recent charter child (reuse is the default). Pass True for a
        parallel/independent chart or to start over.
    render_image: When True, the charter agent renders the diagram to a
        PNG and emits the ``<!-- ens-img:chart-render:<id> -->`` marker
        so chat-source dispatchers can attach the image. Default False
        (validated Mermaid only). Pass True ONLY when the caller is a
        chat source or the user explicitly asked to see the image; web
        and UI contexts never need it.

Returns:
    Charter agent's response containing a single ```` ```mermaid ```` fenced
    code block (the validated diagram) and a brief explanation. On
    ``render_image=True`` the response also carries a trailing
    ``<!-- ens-img:chart-render:<id> -->`` marker line so chat
    dispatchers can attach the PNG. On timeout or failure the tool
    returns a short ``"Error: ..."`` string.

Charter reuse: by default (``fresh=False``) the tool reuses the caller's
most recent charter child instead of spawning a new one — the request is
sent to the SAME instance, whose checkpoint history already carries the
prior diagram, reasoning rounds, and mermaid-lint context, so successive
calls refine in place. ``fresh=True`` skips discovery and always spawns a
new charter (use it for a parallel/independent chart; the next default
call discovers the newest charter by last activity). The DEFAULT CHANGED:
it used to be always-fresh. If the charter is already processing a
refinement, the tool returns
"Error: Charter busy; pass fresh=True for parallel charts."; if it is
paused: "Error: Charter is paused; resume it or pass fresh=True for a new
charter."
"""

    return [generate_chart]
