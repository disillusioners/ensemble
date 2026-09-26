"""Image comparison tool facade — paths-in, structured findings out.

Mirrors the closure-injection pattern of ``daemon.tools.chart_tools``:
``create_compare_tools(manager, current_instance_id)`` is invoked from
``create_instance_tools`` to assemble the per-instance tool list. The
generated ``compare_images`` tool delegates to the ``image-comparator``
agent via ``invoke_agent_and_wait`` (600 s, never-raise) and returns
a structured findings artifact.

Category contract
-----------------
``@register_tool_category("design")`` MUST match the ``"design"`` row
in ``daemon/tools/_auth.py::TOOL_REQUIRED_AGENTS`` (PD-13 / G1 — the
same MUST-match rule every other tier-B agent-backed tool category
honors). Adding this tool without the matching registry row makes the
category invisible to the auth layer and silently strips the tool
from every agent's effective tool list.

P2-WP2 monitoring triggers (NOT day-1 work — recorded per arch §6 +
§10 OQ-2)
------------------------------------------------------------------------
T1: **Semaphore saturation >10 compares/day observed.** The
    ``invoke_agent_and_wait`` singleton semaphore (4 slots, shared
    with charter / explore / explain_image — ``daemon/utils.py:588-605``)
    queues compares behind concurrent blocking calls. When
    operator-visible queueing crosses the 10-compares/day threshold,
    open the **A→C consolidation commission** (fold the comparator
    into a lighter shape per five-axis record A=4.00/C=3.60/B=2.20;
    reduce the per-compare overhead by collapsing the
    spawn-invoke-wait round-trip into an in-process vision call).

T2: **image-reader gains ``comparison_mode``.** The generic-describe
    soul grows a judging mode (a primitive `image_get` plus a
    lightweight comparison prompt). When that lands, re-run the
    five-axis record; consolidate the comparator into image-reader
    if C closes the gap.

T3: **Native proxy multi-image support arrives.** Today the bridge
    inlines two base64 data URIs into a single ``images=[]`` dispatch
    (``daemon/services/instance_messaging.py:113-128``). When the
    chat proxy gains native multi-image support (no inlining,
    token-bounded streaming), re-evaluate the facade's thickness —
    the bridge may shrink to a thin shim or vanish.

These triggers are persisted to the shared meta KV under the key
``design.comparator.monitor`` so a future agent can read them
without parsing this docstring. The KV write happens lazily at first
``compare_images`` call OR at factory init if the manager is fully
wired — see ``_ensure_monitor_kv_recorded`` for the precedence rule.

Expected latency (recorded in the tool docstring per AC-6)
----------------------------------------------------------
One 600 s-capped blocking call per compare. The shared 4-slot
invoke semaphore serializes compares behind any concurrent
charter / explorer / image-reader call — no day-1 fan-out compare
loops. If a refinement turn needs a second compare, the
reuse-by-discovery path rides the caller's existing comparator
child instead of spawning a fresh one.

P2-WP3 path→data-URI bridge
---------------------------
The facade accepts BOTH substrate ids (32-hex, returned by
``image_save``) AND project-workdir paths. The bridge
(``daemon/services/tmp_image_bridge.py`` — Option B per
bridge-design.md §3) handles substrate ids; workdir paths are
resolved daemon-side inline via ``_load_image_from_path``
(``daemon/tools/image_tools.py:318-371``). Both paths produce
``data:`` URIs ready for the ``images=[]`` dispatch parameter.

Fail-loud model gate (P2-WP3 AC-5)
----------------------------------
At factory init, the facade reads ``manager.config.llm.allowed_models``.
If the ``"vision"`` model is absent AND the list is non-empty (an
empty list = "all models allowed" — the documented default), the
factory raises ``VisionModelNotAllowedError`` immediately. Silent
default resolution is FORBIDDEN per arch §8 🔴 (the model would
quietly resolve to the daemon default and the comparator would lose
its vision capability without any error).
"""

import base64
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool

from ._tool_registry import register_tool_category
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.utils import invoke_agent_and_wait

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# ── Comparator-reuse module state ────────────────────────────────────────────
#
# T5 mirror — in-flight reuse guard, keyed by comparator instance id. A
# second ``compare_images`` call targeting a comparator ALREADY being
# waited on is rejected with the busy error instead of registering a
# second waiter (see ``daemon/tools/chart_tools.py:35-49`` for the
# full rationale: two waiters on one ``instance_id`` share a single
# ``asyncio.Event``, so the second waiter wakes on the FIRST caller's
# completion and returns a stale result). The check fires BEFORE
# ``CompletionRegistry.register()`` with no ``await`` between the
# check and the set-add.
_inflight_reuse: set[str] = set()

# T6 — per-comparator ERROR/FAILED revive counter (chart precedent at
# ``daemon/tools/chart_tools.py:37-49``: ``_reuse_revive_attempts``).
# An ERROR/FAILED prior status consumes one revive; the NEXT
# discovery hit on the same comparator respawns fresh (bounded
# thrash). COMPLETED and the non-terminal statuses never touch this
# counter (free revives).
#
# SEPARATE MECHANISM from the agent-tool ReviveGuard: programmatic
# paths never call ``note_agent_tool_revive`` and never read/write
# ``manager._agent_tool_revive_counts``. This dict is
# compare-path-only, in-memory (lost on restart — accepted, same
# precedent), and invisible to the agent-tool revive budget.
#
# Comparator-vs-charter TERMINATED policy (P2-WP2 review MINOR-1):
# the charter reuses TERMINATED freely (per chart T8.13), but the
# comparator does NOT — TERMINATED children are skipped at the
# dispatch-site revive-rail (``compare_images`` below) and fall
# through to a fresh spawn. Rationale: the comparator has no
# durable cross-call state worth a TERMINATED→revive round-trip
# (no shared spec context the charter accumulates), and a fresh
# spawn is cheaper than the registry dance for a kill-then-retry
# pattern.
_reuse_revive_attempts: dict[str, int] = {}

# Busy-reject message — hoisted so the two return sites stay in lock-step.
# Tests pin this string verbatim; any copy-edit must update both the
# docstring below and the busy-reject return site.
_BUSY_MSG = "Error: Comparator busy; pass fresh=True for parallel compares."
_PAUSED_MSG = (
    "Error: Comparator is paused; resume it or pass fresh=True "
    "for a new comparator."
)

# Structured-error envelope kinds (P2-WP2 / P2-WP3 AC-3).
# Each ``kind`` corresponds to a distinct failure mode the facade
# classifies the raw ``invoke_agent_and_wait`` return string into.
# WP3 ADDS the fourth kind (``input-not-found``); the WP2 set is the
# three below. Keeping the kind values as module constants so the
# caller-facing error envelope and the test pins share a single source.
_KIND_TIMEOUT = "timeout"
_KIND_MISSING_AGENT = "missing-agent"
_KIND_VISION_FAILURE = "vision-failure"
# WP3 addition — facade input resolution failure (substrate id 404,
# workdir path missing / outside confinement, magic-byte reject).
_KIND_INPUT_NOT_FOUND = "input-not-found"
# WP3 addition — agent return did not parse as the findings schema.
# Surfaces as a structured envelope so the caller can branch without
# re-parsing prose.
_KIND_SCHEMA_INVALID = "schema-invalid"

# Shared meta KV key for the monitoring triggers payload (P2-WP2 AC-5).
# Reads / writes go through ``manager.shared_meta_kv_repo`` — the same
# repo ``daemon/tools/shared_meta_kv_tools.py`` exposes to agents.
_MONITOR_KV_KEY = "design.comparator.monitor"

# Substrate id pattern (P1-WP10 anchor — must byte-match
# ``daemon/tools/image_tools.py:640`` and the HTTP router regex).
# 32 lowercase hex characters. The facade accepts EITHER this shape
# OR a project-workdir path.
_SUBSTRATE_ID_REGEX = re.compile(r"^[a-f0-9]{32}$")

# Findings schema enum sets (P2-WP3 AC-1 / AC-7). Pinned here as
# module constants so the validator and the test pins share one source.
_FINDINGS_VERDICTS: frozenset[str] = frozenset(
    {"pass", "fail", "conditional_pass"}
)
_FINDINGS_RESULTS: frozenset[str] = frozenset({"pass", "fail"})
_FINDINGS_SEVERITIES: frozenset[str] = frozenset(
    {"critical", "major", "minor", "nit"}
)

# Vision model alias the comparator agent advertises in its meta.json
# (P1-WP1). Fail-loud init verifies this name is in
# ``config.llm.allowed_models`` (case-insensitive) — if it isn't AND
# the list is non-empty, the factory raises.
_COMPARATOR_MODEL_ALIAS = "vision"


class VisionModelNotAllowedError(RuntimeError):
    """Fail-loud: the ``vision`` model is missing from
    ``config.llm.allowed_models`` (P2-WP3 AC-5, arch §8 🔴).

    Mirrors the chart precedent for category-specific model checks;
    the silent default-resolution fallback is FORBIDDEN.
    """


CATEGORY_NAME = "Design"
CATEGORY_DOC = """\
Design tooling cluster — image comparison, future spec-checks, and
adjacent designer-agent work surfaces.

compare_images() delegates to the image-comparator agent, which
judges two images against a pinned criteria set and returns a
structured findings artifact (never an image).
"""


# ── Helpers ──────────────────────────────────────────────────────────────────


def _classify_error(raw: str) -> str:
    """Map an ``invoke_agent_and_wait`` error string to a facade kind.

    The classifier is best-effort — it relies on substring patterns
    stable across the ``invoke_agent_and_wait`` return contract
    (``daemon/utils.py:725-735``). When the classifier cannot
    confidently route a message, it returns
    ``_KIND_VISION_FAILURE`` — the safest catch-all because every
    non-success path through the comparator involves the vision
    model either directly (the agent's own vision call) or
    indirectly (a spawn refusal triggered by an underlying model
    resolution problem).

    Args:
        raw: The raw error string from ``invoke_agent_and_wait`` or
            from the agent's own return value.

    Returns:
        One of ``_KIND_TIMEOUT`` / ``_KIND_MISSING_AGENT`` /
        ``_KIND_VISION_FAILURE``. Never raises.
    """
    if not isinstance(raw, str):
        return _KIND_VISION_FAILURE
    lower = raw.lower()
    if "timed out after" in lower:
        return _KIND_TIMEOUT
    # Missing-agent detection — substring "not found" OR "does not
    # exist" with optional "agent" co-occurrence (the spawn layer
    # surfaces BOTH "Agent 'foo' not found" AND "Agent 'foo' does
    # not exist" — both must route to the same envelope kind, and
    # the comparator agent id ("image-comparator") is its own
    # marker that does NOT contain the word "agent").
    if (
        "not found" in lower
        or "does not exist" in lower
        or "agent not allowed" in lower
    ):
        return _KIND_MISSING_AGENT
    if (
        "vision" in lower
        or "400" in raw
        or "invalid model" in lower
    ):
        return _KIND_VISION_FAILURE
    return _KIND_VISION_FAILURE


def _envelope(kind: str, *, message: str, **extra: Any) -> str:
    """Build the structured-error envelope string the facade returns.

    The envelope is JSON so the calling agent can branch on ``kind``
    without re-parsing natural-language error messages. The shape
    mirrors the bridge's per-image structured errors
    (``bridge-design.md`` §3) — every failure surfaces a ``kind``
    the caller can dispatch on.

    Args:
        kind: One of ``_KIND_TIMEOUT`` / ``_KIND_MISSING_AGENT`` /
            ``_KIND_VISION_FAILURE`` / ``_KIND_INPUT_NOT_FOUND``.
        message: The human-readable detail string.
        **extra: Additional structured fields the caller may need
            (e.g. ``image_id``, ``path`` for the WP3
            input-not-found path).

    Returns:
        JSON-encoded envelope string.
    """
    payload: dict[str, Any] = {"kind": kind, "error": message}
    payload.update(extra)
    return json.dumps(payload, sort_keys=True)


def _find_reusable_comparator(
    manager: "InstanceManager", caller_id: str
) -> Instance | None:
    """Find the caller's most recent ``image-comparator`` child spawned as a tool.

    Mirrors ``daemon.tools.chart_tools._find_reusable_charter``
    (:67-145) — pure query-discovery via
    ``manager._instance_repository.get_children`` (parent_id is
    permanent across terminate-to-revive). Filters to
    ``image-comparator`` children flagged ``invoked_as_tool`` (flag
    stamped at ``daemon/services/instance_lifecycle.py:2166`` by
    ``invoke_agent_and_wait``'s spawn — note the chart_tools
    docstring's stale 1798-1799 cite; the live stamp site is at
    :2166 per plan §4 G3 — PD-21).

    Determinism mirrors the charter precedent: ``last_activity_at``
    desc with NULL treated as OLDEST (a NULL-activity comparator
    never silently wins), then ``created_at`` desc, then row id.

    Any repository error degrades to ``None`` → the caller falls back
    to a fresh spawn; discovery failure never raises to the LLM.

    Args:
        manager: The InstanceManager instance.
        caller_id: The calling instance id (scope key).

    Returns:
        The latest reusable comparator row, or ``None``.
    """
    try:
        rows = manager._instance_repository.get_children(caller_id)
        candidates = [
            row
            for row in rows
            if getattr(row, "agent_id", None) == "image-comparator"
            and (getattr(row, "instance_metadata", None) or {}).get(
                "invoked_as_tool"
            )
        ]
        if not candidates:
            return None

        def _sort_key(row):
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
                activity_key != "",
                activity_key,
                _iso_or_empty(row.created_at),
                row.instance_id or "",
            )

        return max(candidates, key=_sort_key)
    except Exception:
        logger.debug(
            "compare_images: comparator discovery failed for caller %s...",
            caller_id[:8],
            exc_info=True,
        )
        return None


async def _reuse_comparator(
    manager: "InstanceManager",
    comparator_id: str,
    message: str,
    caller_id: str,
    timeout: float = 600.0,
) -> str:
    """Register → enqueue → wait on the caller's EXISTING comparator instance.

    Chart-tools-local mirror of the ``invoke_agent_and_wait`` wait-block
    (``daemon/utils.py:672-740``) minus the spawn: the reuse path rides the
    service-side revive-on-send — ``enqueue_message`` flips a terminal
    comparator back to RUNNING and its checkpoint reloads
    (``instance_messaging.py``) — so no new instance is created.

    Mirrors ``_reuse_charter`` (``daemon/tools/chart_tools.py:148-372``)
    exactly except for two deliberate adaptations:

    * Comparator reuses TERMINATED *children are NOT eligible* (see the
      module-level comment on ``_reuse_revive_attempts``); the
      dispatch site filters TERMINATED out before reaching this helper,
      so we only have to handle the in-scope statuses here.
    * The image-comparator returns its findings as a JSON string
      (no ``"Error: "`` wrapping for normal results, but the agent's
      own error path returns ``"Error: ..."`` via ``invoke_agent_and_wait``
      ``None``/string contracts). We collapse the agent's prose-error
      string to the caller verbatim and let the caller-side
      ``_classify_error`` route it to the right envelope kind.

    Deviations from the fresh path, both deliberate:
    * NO ``_invoke_semaphore`` acquire — nothing is spawned, so there
      is no worker-pool contention with ``explore`` /
      ``explain_image``.
    * Timeout does NOT terminate the comparator: a refused-to-die
      comparator would leave the caller unable to retry the
      refinement later, and the busy guard rejects a still-running
      comparator so buffered completion absorbs a late finish.

    Args:
        manager: The InstanceManager instance.
        comparator_id: The discovered comparator instance to reuse.
        message: The refinement request for the image-comparator agent.
        caller_id: The calling instance id (drives the enqueue
            source tag).
        timeout: Maximum seconds to wait for the comparator's
            completion.

    Returns:
        The agent's response string on success; ``"Error: ..."`` on
        busy-reject, pause-reject, enqueue failure, timeout, or
        agent error. The caller is responsible for envelope-shaping
        the return (``_validate_findings`` for the success path,
        ``_classify_error`` for the error path).
    """
    # Lazy import — same shape as ``daemon/utils.py:641`` and
    # ``daemon/tools/chart_tools.py:184`` so the patched
    # ``daemon.services.completion_registry.get_completion_registry``
    # module attribute is re-read on every call.
    from daemon.services.completion_registry import get_completion_registry

    # 1. Status pre-check — authoritative re-read (discovery may be
    #    stale). TERMINATED was filtered upstream by the dispatch
    #    site's revive-rail, so it never reaches this helper.
    #    COMPLETED proceeds free; RUNNING is already busy; PAUSED is
    #    busy-rejected WITHOUT enqueue (a parked enqueue would sit
    #    PENDING until an operator resumes the comparator while the
    #    tool wait burns); ERROR/FAILED increment the local revive
    #    counter (the consume side of the T6 policy — the miss side
    #    is decided by the caller); any other status
    #    (IDLE / WAITING / WAITING_CHILDREN / QUEUED) enqueues cleanly.
    prior_status = None
    try:
        row = manager._instance_repository.get(comparator_id)
        if row is not None:
            prior_status = (row.status or "").lower()
    except Exception:
        prior_status = None

    if prior_status == InstanceStatus.RUNNING.value:
        logger.warning(
            "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
            caller_id[:8],
            comparator_id[:8],
            "busy-reject",
            prior_status,
        )
        return _BUSY_MSG
    if prior_status == InstanceStatus.PAUSED.value:
        logger.warning(
            "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
            caller_id[:8],
            comparator_id[:8],
            "busy-reject",
            prior_status,
        )
        return _PAUSED_MSG

    # 2. Busy guard (T5 mirror) — check BEFORE add, no await between
    #    the membership check and the set-add (chart_tools.py:227-249
    #    F10/S4 busy-guard atomicity invariant): a single asyncio
    #    event loop is assumed, and only sync code
    #    (``logger.warning`` + ``get_completion_registry``) sits
    #    between the check and the add inside the ``try`` below —
    #    a yield in between would let a second caller observe the
    #    same empty slot and slip a second waiter in.
    if comparator_id in _inflight_reuse:
        logger.warning(
            "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
            caller_id[:8],
            comparator_id[:8],
            "busy-reject",
            prior_status or "none",
        )
        return _BUSY_MSG

    registry = get_completion_registry()
    try:
        # S2 — set-add INSIDE the ``try`` so the ``finally`` cleanup
        # always discards the in-flight entry, even if the add itself
        # raises (defensive: a raise between add and try entry would
        # otherwise leak the entry until the next caller observed it).
        _inflight_reuse.add(comparator_id)

        logger.info(
            "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
            caller_id[:8],
            comparator_id[:8],
            "reuse",
            prior_status or "none",
        )

        # 3. Unregister-then-register — W1 stale-buffered completion
        #    fix (chart_tools.py:267-281): a buffered completion from
        #    a PREVIOUS turn sits in ``registry._buffered`` keyed by
        #    comparator id. The next ``register()`` would consume that
        #    stale entry and set the event immediately, so
        #    ``wait_for`` below would return the OLD content instead
        #    of the NEW turn's result. ``unregister`` clears
        #    ``_buffered``, giving the new turn a clean slate. Safe
        #    under the T5 busy-guard invariant — one waiter per
        #    comparator id at a time, so no other consumer is racing
        #    for the buffer slot.
        registry.unregister(comparator_id)
        registry.register(comparator_id)

        # 4. Enqueue on the EXISTING instance. ONLY existing kwargs
        #    — no facade change. The ``source`` carries the
        #    ``internal_comparator_reuse:`` prefix so the messaging
        #    layer can attribute the dispatch to the comparator-reuse
        #    rail (mirrors chart_tools.py:289 ``internal_chart_reuse:``).
        try:
            await manager.enqueue_message(
                instance_id=comparator_id,
                message=message,
                source=f"internal_comparator_reuse:{caller_id}",
                metadata={"comparator_reuse": True},
            )
        except Exception as enqueue_err:
            # S7 — mirror the fresh-path never-raise contract
            # (``daemon/utils.py:706-735``): wrap the enqueue in a
            # catch-all so the LLM never sees a raw ``enqueue_message``
            # stack trace on the reuse path. Brief exception class +
            # message, paired with the ``comparator_id`` so the caller
            # can still identify the instance.
            return (
                f"Error: {type(enqueue_err).__name__}: {enqueue_err}"
            )

        # S1 — counter increment AFTER successful ``enqueue_message``
        # (aligns with the vetted post-enqueue precedent at
        # ``daemon/tools/instance.py:3009-3012`` and
        # ``daemon/tools/chart_tools.py:304-322``). A transient
        # ``enqueue_message`` exception above leaves the child eligible
        # for a future revive attempt — the one-shot budget is only
        # consumed when the dispatch actually happened.
        if prior_status in (
            InstanceStatus.ERROR.value,
            InstanceStatus.FAILED.value,
        ):
            # ERROR/FAILED revive consumes the one-shot budget (T6).
            # This counter is SEPARATE from the agent-tool ReviveGuard
            # — see the module-level comment on ``_reuse_revive_attempts``.
            # Growth is daemon-restart-bounded: this dict is in-memory,
            # lost on restart, and accepted (mirrors the precedent at
            # ``daemon/manager.py:773`` ``_agent_tool_revive_counts``).
            _reuse_revive_attempts[comparator_id] = (
                _reuse_revive_attempts.get(comparator_id, 0) + 1
            )

        # 5. Re-register if consumed — S3 comment fix
        #    (chart_tools.py:324-334). The re-register guards the
        #    post-enqueue consumption race: the enqueue above can
        #    flip terminal→RUNNING and the comparator's existing
        #    completion (e.g. from a side channel) may have drained
        #    the event the step-3 register just set. Re-registering
        #    restores the wait surface for the ``wait_for`` below so
        #    a subsequent completion is captured.
        if not registry.is_registered(comparator_id):
            registry.register(comparator_id)

        # 6. Wait for completion (success or error).
        #
        # W2 — accepted interleaving (chart_tools.py:337-351).
        # ``CompletionRegistry`` keys one ``asyncio.Event`` per
        # instance id; two completions landing on the same id share
        # the event (one event set is binary). When an external
        # message completes the same comparator while we are parked
        # here, the resulting ``event.set()`` wakes THIS waiter with
        # THAT other message's completion result — bounded by the T5
        # busy guard to same-comparator turns (one reuse waiter per
        # comparator at a time, so cross-waiter mixing is
        # impossible).
        result = await registry.wait_for(comparator_id, timeout=timeout)

        if result is None:
            # Timeout — do NOT terminate.
            return (
                f"Error: Comparator timed out after {timeout}s. "
                f"Instance {comparator_id[:8]}... may still be running."
            )

        if result.is_error:
            # Agent errored out — it's already in ERROR status.
            return f"Error: Agent failed. {result.content}"

        # Success — return the agent's findings verbatim. Schema
        # validation happens at the caller (the existing
        # ``_validate_findings`` invariant).
        return result.content or ""
    finally:
        # 7. Always cleanup (mirrors chart_tools.py:368-371).
        registry.unregister(comparator_id)
        _inflight_reuse.discard(comparator_id)


def _ensure_monitor_kv_recorded(
    manager: "InstanceManager", current_instance_id: str
) -> None:
    """Record the monitoring triggers payload to the shared meta KV.

    Writes the ``design.comparator.monitor`` key with the T1–T3
    trigger catalog the future agent needs to re-evaluate facade
    thickness. The write is idempotent (existing value preserved
    — operators may add annotations). On any error (missing repo,
    partial-init manager, DB unavailable) the function logs and
    degrades to no-op; the docstring carries the canonical record,
    so a missing KV row never silences a real trigger.

    Args:
        manager: The InstanceManager instance. ``shared_meta_kv_repo``
            is consulted lazily inside the body.
    """
    payload = {
        "triggers": {
            "T1": {
                "name": "semaphore_saturation",
                "condition": (
                    "invoke_agent_and_wait semaphore saturation >10 "
                    "compares/day observed (shared 4-slot singleton at "
                    "daemon/utils.py:588-605)"
                ),
                "commission": (
                    "A->C consolidation commission — fold the "
                    "comparator into a lighter shape per five-axis "
                    "record (A=4.00/C=3.60/B=2.20); collapse the "
                    "spawn-invoke-wait round-trip into an in-process "
                    "vision call."
                ),
            },
            "T2": {
                "name": "image_reader_comparison_mode",
                "condition": (
                    "image-reader gains comparison_mode (generic "
                    "describe soul grows a judging mode)"
                ),
                "commission": (
                    "Re-run five-axis record; consolidate the "
                    "comparator into image-reader if C closes the gap."
                ),
            },
            "T3": {
                "name": "native_proxy_multi_image",
                "condition": (
                    "Native proxy multi-image support arrives "
                    "(removes the bridge raison d'etre in the facade)"
                ),
                "commission": (
                    "Re-evaluate facade thickness; bridge may shrink "
                    "to a thin shim or vanish."
                ),
            },
        },
        "recorded_at_wpid": "P2-WP2",
        "schema_doc": (
            "P2-WP2 monitoring triggers — see "
            "daemon/tools/compare_tools.py module docstring for "
            "canonical prose."
        ),
    }
    try:
        repo = manager.shared_meta_kv_repo
    except Exception as exc:
        logger.debug(
            "compare_images: monitor KV repo unavailable (%s); "
            "docstring remains canonical.",
            exc,
        )
        return
    try:
        # Idempotent upsert; no-op when the same payload is already
        # present so the write cost stays bounded. Mirrors the
        # shared_meta_kv_tools pattern (``daemon/tools/shared_meta_kv_tools.py``)
        # — use the caller's tree root as the context_key, fall back
        # to a module-level sentinel so the write never blocks on a
        # broken tree-root lookup.
        try:
            context_key = manager._instance_repository.get_tree_root_id(
                current_instance_id
            )
        except Exception:
            context_key = "compare_tools_module"
        if not context_key:
            context_key = "compare_tools_module"
        repo.set_many(context_key, {_MONITOR_KV_KEY: payload})
    except Exception as exc:
        logger.debug(
            "compare_images: monitor KV write failed (%s); "
            "docstring remains canonical.",
            exc,
        )


# ── Input resolution (P2-WP3 bridge integration) ────────────────────────────


def _looks_like_substrate_id(value: str) -> bool:
    """True iff ``value`` is a 32-hex substrate id (bridge input shape)."""
    if not isinstance(value, str):
        return False
    return bool(_SUBSTRATE_ID_REGEX.match(value))


def _resolve_substrate_input(
    image_ref: str,
    *,
    manager: "InstanceManager",
) -> dict[str, Any]:
    """Resolve a substrate id → ``data:`` URI dict via the bridge.

    On any failure the dict carries ``{"error": "input-not-found", ...}``
    so the facade can surface it as the ``_KIND_INPUT_NOT_FOUND``
    envelope.

    Args:
        image_ref: The 32-hex substrate id.
        manager: The InstanceManager instance — ``tmp_image_store`` is
            read from the manager (mirrors ``image_tools.py:652``).

    Returns:
        A dict with EITHER ``{"data_uri": str, "provenance": dict | None,
        "retention_class": str, ...}`` on success,
        OR ``{"error": "input-not-found", "image_id": str, "reason": str,
        "message": str}`` on any failure.
    """
    # Lazy import — keeps the import surface small when the facade
    # is wired but the call never fires (same pattern as the
    # ``image_tools`` substrate tools).
    from daemon.services.tmp_image_bridge import resolve_data_uris

    # Store wiring — mirrors ``daemon/tools/image_tools.py:651-654``.
    try:
        store = manager.tmp_image_store
    except Exception as exc:
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "store_not_initialized",
            "message": f"tmp-image store unavailable: {exc}",
        }
    if store is None:
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "store_not_initialized",
            "message": "tmp-image store not initialized",
        }

    try:
        results = resolve_data_uris([image_ref], store=store)
    except Exception as exc:
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "bridge_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
    if not results:
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "bridge_no_result",
            "message": "bridge returned no result",
        }
    row = results[0]
    if isinstance(row, dict) and row.get("error"):
        # Bridge surfaced a per-position error envelope — re-emit as
        # the facade's input-not-found kind with the bridge's
        # reason/message preserved.
        return {
            "error": "input-not-found",
            "image_id": row.get("image_id", image_ref),
            "reason": row.get("reason", row.get("error", "unknown")),
            "message": row.get("message", "bridge reported failure"),
        }
    return row


def _resolve_workdir_input(
    image_ref: str,
    *,
    manager: "InstanceManager",
    project_id: str | None,
) -> dict[str, Any]:
    """Resolve a workdir path → ``data:`` URI dict via the daemon-side read.

    Reuses ``_load_image_from_path`` from ``daemon/tools/image_tools.py``
    which already enforces the project workdir confinement. The
    facade holds the daemon-side read (P2-WP3 — workdir-confined
    ``explain_image`` is NOT used inside the facade per plan §5 P2-WP3
    risks).

    Args:
        image_ref: A filesystem path (absolute or project-relative).
        manager: The InstanceManager instance — used to look up the
            project's workdir via ``_project_repository``.
        project_id: The caller's project id (resolved upstream).

    Returns:
        A dict with EITHER ``{"data_uri": str}`` on success,
        OR ``{"error": "input-not-found", "image_id": str, "reason": str,
        "message": str}`` on any failure.
    """
    from daemon.tools.image_tools import _load_image_from_path

    # Resolve workdir from the project (mirrors the existing
    # image-tools workdir-confined read path).
    workdir: str | None = None
    if project_id is not None:
        try:
            project = manager._project_repository.get(project_id)
            if project is not None:
                workdir = getattr(project, "main_directory", None)
        except Exception:
            workdir = None

    try:
        data_uri = _load_image_from_path(image_ref, workdir=workdir)
    except ValueError as exc:
        # ``_load_image_from_path`` raises ``ValueError`` for path
        # confinement / magic-byte / size / non-regular-file
        # failures (P2-WP3 workdir validator semantics). These are
        # REJECTED paths, not read failures — the validator refused
        # the path before the open call. Distinct ``reason`` from
        # OSError below so the caller can branch between
        # "validator rejected the path" and "filesystem won't read".
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "workdir_invalid",
            "message": str(exc),
        }
    except OSError as exc:
        # ``_load_image_from_path`` raises ``OSError`` for actual
        # filesystem read failures (permissions, I/O, missing after
        # the validator passed — defensive since the strict
        # resolver can race a concurrent unlink). Distinct from
        # ``workdir_invalid`` so the caller knows the validator
        # passed and the read is the failure surface.
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "workdir_unreadable",
            "message": f"{type(exc).__name__}: {exc}",
        }
    except Exception as exc:
        # Anything else — defensive catch-all for unforeseen
        # failures (encoding errors, OOM, etc.). Distinct reason
        # so the caller / future operator can triage without
        # parsing prose.
        return {
            "error": "input-not-found",
            "image_id": image_ref,
            "reason": "workdir_unknown",
            "message": f"{type(exc).__name__}: {exc}",
        }
    return {
        "data_uri": data_uri,
        "image_id": image_ref,
        "source": "workdir",
    }


def _resolve_input(
    image_ref: str,
    *,
    manager: "InstanceManager",
    project_id: str | None,
) -> dict[str, Any]:
    """Resolve one facade input → ``data:`` URI or input-not-found envelope.

    Dispatch:
      * 32-hex substrate id → bridge (substrate store read).
      * Anything else → workdir path → daemon-side read with workdir
        confinement.

    The facade NEVER raises — every failure path returns the
    ``_KIND_INPUT_NOT_FOUND`` envelope shape the caller can branch on.

    Args:
        image_ref: The raw caller-supplied string.
        manager: The InstanceManager instance.
        project_id: The caller's project id (for workdir resolution).

    Returns:
        A dict with EITHER ``{"data_uri": str, ...}`` on success,
        OR ``{"error": "input-not-found", ...}`` on any failure.
    """
    if not isinstance(image_ref, str) or not image_ref:
        return {
            "error": "input-not-found",
            "image_id": str(image_ref),
            "reason": "invalid_input",
            "message": "image address must be a non-empty string",
        }
    if _looks_like_substrate_id(image_ref):
        return _resolve_substrate_input(image_ref, manager=manager)
    return _resolve_workdir_input(
        image_ref, manager=manager, project_id=project_id
    )


def _resolve_inputs_pair(
    image_a: str,
    image_b: str,
    *,
    manager: "InstanceManager",
    project_id: str | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """Resolve both inputs in one pass; return ``(uri_a, uri_b, error_json)``.

    On any input failure the ``error_json`` field carries the structured
    envelope string and the URI fields are ``None``. When BOTH inputs
    fail the FIRST error is reported (per bridge-design §5 example A1
    — the comparator surfaces the worst failure first).

    Args:
        image_a, image_b: The two caller-supplied image addresses.
        manager: The InstanceManager instance.
        project_id: The caller's project id (for workdir resolution).

    Returns:
        ``(uri_a, uri_b, error_json)`` — exactly one of
        ``uri_a``/``uri_b`` + ``error_json`` is set on success.
    """
    resolved_a = _resolve_input(
        image_a, manager=manager, project_id=project_id
    )
    if isinstance(resolved_a, dict) and resolved_a.get("error"):
        return (
            None,
            None,
            _envelope(
                _KIND_INPUT_NOT_FOUND,
                message=resolved_a.get(
                    "message", "input-not-found on image_a"
                ),
                image_id=resolved_a.get("image_id", image_a),
                reason=resolved_a.get("reason", "unknown"),
                input_slot="image_a",
            ),
        )
    resolved_b = _resolve_input(
        image_b, manager=manager, project_id=project_id
    )
    if isinstance(resolved_b, dict) and resolved_b.get("error"):
        return (
            None,
            None,
            _envelope(
                _KIND_INPUT_NOT_FOUND,
                message=resolved_b.get(
                    "message", "input-not-found on image_b"
                ),
                image_id=resolved_b.get("image_id", image_b),
                reason=resolved_b.get("reason", "unknown"),
                input_slot="image_b",
            ),
        )
    return resolved_a, resolved_b, None


# ── Findings schema validator (P2-WP3 AC-1 / AC-7) ─────────────────────────


def _validate_findings(raw: str) -> dict[str, Any] | None:
    """Validate the comparator agent's return value against the
    findings schema.

    Returns the parsed findings dict on success, or ``None`` on any
    schema violation. The caller is responsible for wrapping
    ``None`` in the ``_KIND_SCHEMA_INVALID`` envelope.

    Schema (P2-WP3 AC-1 + AC-7):

        {
          "verdict": "pass" | "fail" | "conditional_pass",
          "per_criterion": [
            {
              "criterion": str,
              "result": "pass" | "fail",
              "severity": "critical" | "major" | "minor" | "nit",
              "evidence": list[str]
            },
            ...
          ],
          "summary": str,
          "pinned_spec_sha": str | null
        }

    Validation is permissive on ``pinned_spec_sha`` shape (any string
    or null) — the SHA validation happens upstream when the caller
    compares it against the approved spec. The schema's
    ``pinned_spec_sha`` field is the D6 hard-rule wire.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        findings = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(findings, dict):
        return None

    # Required top-level fields
    verdict = findings.get("verdict")
    if verdict not in _FINDINGS_VERDICTS:
        return None
    if "per_criterion" not in findings:
        return None
    per_criterion = findings.get("per_criterion")
    if not isinstance(per_criterion, list) or len(per_criterion) < 1:
        return None
    for row in per_criterion:
        if not isinstance(row, dict):
            return None
        if not isinstance(row.get("criterion"), str):
            return None
        if row.get("result") not in _FINDINGS_RESULTS:
            return None
        if row.get("severity") not in _FINDINGS_SEVERITIES:
            return None
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            return None
        # Evidence list items are strings (the comparator's anti-drift
        # rule pins the shape).
        if not all(isinstance(item, str) for item in evidence):
            return None
    summary = findings.get("summary")
    if not isinstance(summary, str):
        return None
    pinned = findings.get("pinned_spec_sha", None)
    if pinned is not None and not isinstance(pinned, str):
        return None
    return findings


# ── Vision model fail-loud gate (P2-WP3 AC-5) ──────────────────────────────


def _verify_vision_allowed(manager: "InstanceManager") -> None:
    """Fail-loud at facade init when ``vision`` is not in allowed_models.

    Mirrors arch §8 🔴 — silent default resolution is FORBIDDEN.
    Raises :class:`VisionModelNotAllowedError` (a ``RuntimeError``
    subclass) when the comparator's ``vision`` alias is missing from
    ``config.llm.allowed_models`` AND the list is non-empty (the
    documented default for ``allowed_models`` is ``[]`` = "all
    models allowed").

    The check reads through ``manager.config.llm.allowed_models`` —
    the canonical config access path (G7). The check fails CLOSED on
    any error (defensive — a partial-init manager must not silently
    pass the gate).

    Args:
        manager: The InstanceManager instance.

    Raises:
        VisionModelNotAllowedError: ``vision`` is not in
            ``config.llm.allowed_models`` AND the list is non-empty.
    """
    try:
        config = getattr(manager, "config", None)
        if config is None:
            # No config wired — the gate is not enforceable.
            # The factory passes through; the actual spawn will
            # surface the failure downstream.
            return
        allowed_models = getattr(config.llm, "allowed_models", None)
    except Exception as exc:
        # Fail-closed: if we cannot verify the gate, refuse to
        # init the facade (a partial-init manager must not silently
        # pass).
        raise VisionModelNotAllowedError(
            "compare_images: cannot verify vision model in "
            f"allowed_models — config access failed ({exc}). "
            "Refusing to init the facade (silent default resolution "
            "is FORBIDDEN per arch §8)."
        ) from exc

    if not isinstance(allowed_models, list):
        # Garbage type — fail-closed.
        raise VisionModelNotAllowedError(
            "compare_images: config.llm.allowed_models is not a list "
            f"(got {type(allowed_models).__name__}). Refusing to init "
            "the facade."
        )
    if not allowed_models:
        # Empty list = "all models allowed" — the documented default.
        return
    # Case-insensitive exact match (mirrors the spawn-time check at
    # ``daemon/manager.py:7073``).
    lower_allowed = {m.lower() for m in allowed_models if isinstance(m, str)}
    if _COMPARATOR_MODEL_ALIAS.lower() not in lower_allowed:
        raise VisionModelNotAllowedError(
            f"compare_images: '{_COMPARATOR_MODEL_ALIAS}' model is "
            f"missing from config.llm.allowed_models ({allowed_models}). "
            "Silent default resolution is FORBIDDEN per arch §8 🔴. "
            f"Add '{_COMPARATOR_MODEL_ALIAS}' to OPENAI_SELECTABLE_MODELS "
            "and restart the daemon."
        )


# ── Factory ──────────────────────────────────────────────────────────────────


def create_compare_tools(
    manager: "InstanceManager", current_instance_id: str
) -> list:
    """Create image comparison tools with injected manager reference.

    Mirrors ``create_chart_tools`` end-to-end (manager +
    current_instance_id closure injection; one tool per category;
    agent-backed tier-B shape).

    Args:
        manager: The InstanceManager instance to use for operations.
        current_instance_id: The ID of the current instance (used as
            parent for the spawned ``image-comparator`` instance).

    Returns:
        List of tool functions: ``[compare_images]``.
    """
    # Fail-loud init gate (P2-WP3 AC-5). Mirrors arch §8 🔴 — silent
    # default resolution is FORBIDDEN. A missing ``vision`` alias in
    # ``allowed_models`` must surface at factory init, not at first
    # spawn when the comparator child silently falls back to the
    # daemon default model.
    _verify_vision_allowed(manager)

    def _get_project_id() -> str | None:
        """Auto-inject project_id from instance context."""
        try:
            instance_meta = manager._instance_repository.get(
                current_instance_id
            )
            if instance_meta and instance_meta.project_id:
                return instance_meta.project_id
        except Exception:
            pass
        return None

    @register_tool_category("design")
    @tool
    async def compare_images(
        image_a: str,
        image_b: str,
        criteria: list[str] | None = None,
        project_id: str | None = None,
        pinned_spec_sha: str | None = None,
    ) -> str:
        """Compare two images against pinned criteria; return structured findings.

        Delegates to the ``image-comparator`` specialist agent, which
        judges both images against the pinned five-axis criteria set
        (structural layout, content parity, token/color conformance,
        spacing/alignment, states & a11y-visible affordances) and
        returns a structured findings artifact — one overall verdict
        (``pass`` / ``fail`` / ``conditional_pass``), one row per
        criterion judged, severity + evidence per row, a short
        summary, and the ``pinned_spec_sha`` when the caller's
        criteria reference an approved spec (D6 hard rule).

        The tool blocks until the comparator produces its findings
        (default timeout = 600 s) and returns the agent's text —
        the JSON-encoded findings schema — directly to the caller.
        Paste it into your response without re-wrapping.

        The two images are passed to the agent as a single
        multimodal message (``images=[a, b]`` in one dispatch), so
        the comparator runs exactly ONE vision call per compare.

        Args:
            image_a: First image address. Accepts the substrate
                32-hex id (returned by ``image_save`` /
                ``image_list``) OR a project-workdir path (resolved
                by the WP3 bridge; the facade runs the path→data-URI
                conversion daemon-side so a workdir-confined caller
                does not need direct ``data_dir`` access).
            image_b: Second image address — same shape as
                ``image_a``.
            criteria: Optional list of caller-supplied criteria
                strings. Maps 1:1 to ``per_criterion`` rows in the
                findings; when ``None`` (default) the comparator's
                pinned five-axis criteria set applies. The criteria
                must be a subset of the pinned set — the comparator
                logs caller-supplied entries outside the pinned set
                as ``out_of_scope`` and continues with its own five.
            project_id: Optional project ID. Auto-detected from the
                current instance context if not provided.
            pinned_spec_sha: Optional SHA of an approved spec the
                caller's criteria reference. When ``None`` (default)
                the findings carry ``pinned_spec_sha: null`` — the
                comparator's verdict is advisory. Set this for
                spec-conformance contexts (D6 hard rule): a
                non-null SHA is mandatory for every conformance
                verdict that references an approved spec.

        Returns:
            JSON-encoded string on success — the findings schema::

                {
                  "verdict": "pass" | "fail" | "conditional_pass",
                  "per_criterion": [
                    {
                      "criterion": "<string>",
                      "result": "pass" | "fail",
                      "severity": "critical" | "major" | "minor" | "nit",
                      "evidence": ["<string>", ...]
                    },
                    ...
                  ],
                  "summary": "<string>",
                  "pinned_spec_sha": "<sha>" | null
                }

            Structured error envelope (JSON) on failure::

                {"kind": "timeout" | "missing-agent" | "vision-failure",
                 "error": "<human-readable detail>"}

            The envelope ``kind`` distinguishes the failure class;
            the LLM and the calling agent can branch on it without
            parsing prose.
        """
        pid = project_id or _get_project_id()

        # First-use monitoring trigger recording — idempotent upsert,
        # best-effort; never-raises. Docstring is the canonical
        # record, so a KV write failure never silences a real
        # trigger.
        _ensure_monitor_kv_recorded(manager, current_instance_id)

        # Construct a structured prompt for the comparator agent.
        # Mirrors ``create_chart_tools``' chart_message style — short
        # label-style header lines so the agent has explicit context
        # for the inputs and the criteria override.
        compare_message = (
            "compare_images tool facade invoked.\n"
            f"image_a: {image_a}\n"
            f"image_b: {image_b}\n"
        )
        if criteria:
            compare_message += (
                f"criteria_override: {json.dumps(list(criteria))}\n"
            )
        else:
            compare_message += "criteria_override: (none — use pinned five)\n"
        if pinned_spec_sha:
            compare_message += f"pinned_spec_sha: {pinned_spec_sha}\n"
        else:
            compare_message += "pinned_spec_sha: (none — advisory mode)\n"
        if pid:
            compare_message += f"project: {pid}\n"

        # Comparator reuse (default): discover the caller's most
        # recent comparator child and refine it in place via the
        # service-side revive-on-send (``enqueue_message`` flips
        # terminal→RUNNING and the existing checkpoint reloads).
        # ``fresh`` is implicit because we currently have no
        # caller-side knob for it — the comparator's reuse is the
        # default and only path. Mirrors charter's reuse rail at
        # ``daemon/tools/chart_tools.py:449-486``, with two
        # deliberate differences (see the ``_reuse_revive_attempts``
        # module-level comment):
        #
        # 1. TERMINATED children are NOT eligible for reuse — they
        #    fall through to a fresh spawn below. Charter reuses
        #    TERMINATED freely (chart T8.13); the comparator does
        #    NOT, because it has no durable cross-call state worth
        #    a TERMINATED→revive round-trip.
        # 2. ERROR/FAILED children get ONE revive attempt via
        #    ``_reuse_comparator``; the next discovery hit on the
        #    same comparator respawns fresh (T6 one-shot budget,
        #    same precedent as charter).
        reusable = _find_reusable_comparator(manager, current_instance_id)

        # Input resolution (P2-WP3). Run BEFORE the dispatch so a
        # missing image never wastes a spawn / enqueue. The bridge
        # handles substrate ids; workdir paths are read daemon-side
        # inline. On any failure the facade surfaces
        # ``kind: input-not-found`` immediately — never spawns the
        # comparator with a bad image.
        resolved_a, resolved_b, input_err = _resolve_inputs_pair(
            image_a,
            image_b,
            manager=manager,
            project_id=pid,
        )
        if input_err is not None:
            return input_err
        assert resolved_a is not None and resolved_b is not None
        images_param: list[str] = [
            resolved_a["data_uri"],
            resolved_b["data_uri"],
        ]

        # Reuse-rail dispatch (P2-WP2 review MAJOR-1). The
        # discovered comparator receives the dispatch on its
        # EXISTING instance id via ``enqueue_message``; the facade
        # does NOT spawn a fresh worker. Exactly ONE mode log line
        # per call (the fresh-spawn log below is skipped when the
        # reuse branch engages).
        mode_logged = False
        if reusable is not None:
            comparator_id = reusable.instance_id
            prior_status = (reusable.status or "").lower()

            # TERMINATED children are NOT reused — fall through to
            # fresh spawn (rationale: ``_reuse_revive_attempts``
            # module-level comment). The comparator has no durable
            # cross-call state worth a TERMINATED→revive round-trip.
            if prior_status == InstanceStatus.TERMINATED.value:
                logger.info(
                    "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
                    current_instance_id[:8],
                    comparator_id[:8],
                    "reuse-respawn-terminated",
                    prior_status,
                )
                mode_logged = True
            elif (
                prior_status
                in (
                    InstanceStatus.ERROR.value,
                    InstanceStatus.FAILED.value,
                )
                and _reuse_revive_attempts.get(comparator_id, 0) >= 1
            ):
                # One revive already consumed for this comparator
                # (T6 miss side, adjudicated P5) — respawn fresh.
                # The respawn line IS this call's single mode log,
                # so the generic fresh-spawn log below is skipped.
                logger.info(
                    "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
                    current_instance_id[:8],
                    comparator_id[:8],
                    "reuse-respawn-after-failure",
                    prior_status,
                )
                mode_logged = True
            else:
                # Reuse path: enqueue onto the existing comparator
                # id and wait via the completion registry. Busy /
                # paused / enqueue-failure / timeout / agent-error
                # are all collapsed to a single string by
                # ``_reuse_comparator``; envelope-shape it below.
                raw = await _reuse_comparator(
                    manager=manager,
                    comparator_id=comparator_id,
                    message=compare_message,
                    caller_id=current_instance_id,
                    timeout=600.0,
                )
                # Busy / paused / enqueue / timeout paths return an
                # ``Error: ...`` string. Schema success returns the
                # findings JSON. Both need envelope-shaping per the
                # facade's failure-kind contract.
                if isinstance(raw, str) and raw.startswith("Error:"):
                    return _envelope(
                        _classify_error(raw),
                        message=raw,
                    )
                # Schema validation (P2-WP3 AC-1) — agent returned
                # something that doesn't fit the findings schema.
                # The comparator child is the source of truth for
                # findings shape; the facade surfaces a structured
                # envelope so the caller can branch.
                if _validate_findings(raw) is None:
                    return _envelope(
                        _KIND_SCHEMA_INVALID,
                        message=(
                            "Comparator return did not match the "
                            "findings schema (verdict / per_criterion / "
                            "severity / evidence / summary)."
                        ),
                    )
                return raw

        # Fresh-spawn path — no reusable comparator child, OR
        # reuse-rail chose respawn (TERMINATED / one-shot-budget
        # exhausted). Same never-raise + structured-error contract.
        if not mode_logged:
            logger.info(
                "compare_images: caller=%s comparator=%s mode=%s prior_status=%s",
                current_instance_id[:8],
                "spawn",
                "fresh",
                "none",
            )
        try:
            raw, _child_id = await invoke_agent_and_wait(
                manager=manager,
                agent_id="image-comparator",
                message=compare_message,
                project_id=pid,
                parent_id=current_instance_id,
                instance_name=(
                    f"compare-{image_a[:6]}-vs-{image_b[:6]}"
                ),
                timeout=600.0,
                return_instance_id=True,
                images=images_param,
            )
        except Exception as exc:
            return _envelope(
                _KIND_VISION_FAILURE,
                message=(
                    f"Comparator invocation failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        if raw is None:
            return _envelope(
                _KIND_TIMEOUT,
                message="Comparator timed out after 600s.",
            )
        if isinstance(raw, str) and raw.startswith("Error:"):
            return _envelope(
                _classify_error(raw),
                message=raw,
            )
        # Schema validation (P2-WP3 AC-1) — fresh-path mirror of
        # the reuse-path check above.
        if _validate_findings(raw) is None:
            return _envelope(
                _KIND_SCHEMA_INVALID,
                message=(
                    "Comparator return did not match the findings "
                    "schema (verdict / per_criterion / severity / "
                    "evidence / summary)."
                ),
            )
        return raw

    return [compare_images]
