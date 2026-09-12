"""Chart generation tools for producing validated Mermaid diagrams.

Mirrors the closure-injection pattern of ``daemon.tools.knowledge_tools``:
``create_chart_tools(manager, current_instance_id)`` is invoked from
``create_instance_tools`` to assemble the per-instance tool list. The
generated ``generate_chart`` tool delegates to the ``charter`` agent via
``invoke_agent_and_wait`` and returns the validated Mermaid output.
"""

import logging
from typing import TYPE_CHECKING

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.utils import invoke_agent_and_wait

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

CATEGORY_NAME = "Chart"
CATEGORY_DOC = """\
Chart generation tools for producing validated Mermaid diagrams.

generate_chart() delegates to the Charter agent which produces
syntax-validated Mermaid diagrams across flowchart, sequence, class,
er, state, and gantt types.
"""


def _find_reusable_charter(manager, caller_id: str) -> Instance | None:
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
    manager,
    charter_id: str,
    message: str,
    caller_id: str,
    timeout: float = 600.0,
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
            "Error: Charter busy; pass fresh=True for parallel charts.",
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
            "Error: Charter busy; pass fresh=True for parallel charts.",
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

        Returns:
            The Charter agent's response containing a validated ```` ```mermaid ````
            fenced code block and a brief explanation.
        """
        pid = project_id or _get_project_id()

        # Construct a structured prompt for the charter agent. Mirrors the
        # ``explore()`` style at knowledge_tools.py — short label-style header
        # lines so the agent has explicit context for type and project scope.
        # Message construction is IDENTICAL on the reuse and fresh paths (M9).
        chart_message = (
            f"Create a {diagram_type} diagram.\n\n"
            f"Description: {description}\n"
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
                        timeout=600.0,
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
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="charter",
            message=chart_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"chart-{description[:30]}",
            timeout=600.0,
            return_instance_id=True,
        )

        # Handle error results — ``invoke_agent_and_wait`` returns
        # ``"Error: ..."`` on failure / timeout when ``return_instance_id`` is
        # True we still get the tuple; collapse to a single string for the
        # tool response. A ``None`` content means the agent never produced a
        # result (e.g. hard timeout during cleanup).
        if result is None:
            return "Error: Charter agent timed out or failed. Try a simpler description."
        return result

    generate_chart._full_doc_ = """\
Generate a validated Mermaid diagram by delegating to the Charter agent.

Sends a structured request to the Charter agent, which produces
syntax-validated Mermaid diagrams and returns them in a fenced code
block. The agent is responsible for:

1. Selecting the appropriate diagram type (if ``diagram_type`` is left
   implicit by the caller, charter will infer from the description).
2. Drafting the Mermaid syntax.
3. Validating via ``npx -y @mermaid-js/mermaid-cli``.
4. Returning the validated diagram with a brief explanation.

The tool blocks until the agent produces its final response (default
``timeout`` = 600s) and returns the agent's text — a ```` ```mermaid ````
fenced block plus explanation — directly to the caller. Paste it into
your response without re-wrapping or stripping the fence.

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

Returns:
    Charter agent's response containing a single ```` ```mermaid ```` fenced
    code block (the validated diagram) and a brief explanation. On
    timeout or failure the tool returns a short ``"Error: ..."`` string.

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
