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
"""

import json
import logging
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
# WP3 additions (declared here so WP3 can import them; populated by
# the WP3 commit which re-exports them at module top-level).
_KIND_INPUT_NOT_FOUND = "input-not-found"

# Shared meta KV key for the monitoring triggers payload (P2-WP2 AC-5).
# Reads / writes go through ``manager.shared_meta_kv_repo`` — the same
# repo ``daemon/tools/shared_meta_kv_tools.py`` exposes to agents.
_MONITOR_KV_KEY = "design.comparator.monitor"

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
        # recent comparator child and refine it in place. Mirrors
        # charter's reuse logic — the comparator's pinned criteria
        # set lives in its soul, so a refinement turn on the same
        # child reuses the prior criteria context without a fresh
        # cold-start. Pure query-discovery; ``fresh`` is implicit
        # because we currently have no caller-side knob for it
        # (PD-13 noted and accepted: the comparator's reuse is the
        # default and only path).
        reusable = _find_reusable_comparator(manager, current_instance_id)
        if reusable is not None:
            comparator_id = reusable.instance_id
            prior_status = (reusable.status or "").lower()

            # Busy-reject when the comparator is mid-turn. Mirrors
            # charter's busy guard exactly — two waiters on one
            # ``instance_id`` share a single ``asyncio.Event`` so
            # the second would wake on the FIRST caller's completion.
            if comparator_id in _inflight_reuse:
                logger.warning(
                    "compare_images: caller=%s comparator=%s mode=%s",
                    current_instance_id[:8],
                    comparator_id[:8],
                    "busy-reject",
                )
                return _envelope(
                    _KIND_VISION_FAILURE,
                    message=_BUSY_MSG,
                )
            if prior_status == InstanceStatus.RUNNING.value:
                logger.warning(
                    "compare_images: caller=%s comparator=%s mode=%s",
                    current_instance_id[:8],
                    comparator_id[:8],
                    "busy-reject-running",
                )
                return _envelope(
                    _KIND_VISION_FAILURE,
                    message=_BUSY_MSG,
                )
            if prior_status == InstanceStatus.PAUSED.value:
                logger.warning(
                    "compare_images: caller=%s comparator=%s mode=%s",
                    current_instance_id[:8],
                    comparator_id[:8],
                    "busy-reject-paused",
                )
                return _envelope(
                    _KIND_VISION_FAILURE,
                    message=_PAUSED_MSG,
                )

            # Status pre-read for the error-classification path on
            # the reuse branch. The reuse flow itself does NOT
            # terminate / spawn — the comparator child is durable
            # and the next dispatch rides its existing checkpoint.
            try:
                _inflight_reuse.add(comparator_id)
                try:
                    raw, _reused = await invoke_agent_and_wait(
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
                    )
                except Exception as exc:
                    # Never-raise contract (mirrors chart_tools.py:515-516).
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
                        message=(
                            "Comparator timed out after 600s; "
                            "child may still be running."
                        ),
                    )
                if isinstance(raw, str) and raw.startswith("Error:"):
                    return _envelope(
                        _classify_error(raw),
                        message=raw,
                    )
                return raw
            finally:
                _inflight_reuse.discard(comparator_id)

        # Fresh-spawn path — no reusable comparator child on this
        # caller. Same never-raise + structured-error contract.
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
        return raw

    return [compare_images]
