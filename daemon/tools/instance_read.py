"""Instance READ tools — the ``instance_read`` privileged category (5 tools).

The five tools answer the questions the maintenancer could not answer
before this category existed: *what is in the fleet*, *what is this one
instance doing*, *what looks wedged*, and *how much capacity is left*.

Five tools
----------
``instance_list``            — fleet census (cheap rows, SQL-side filters)
``instance_get``             — deep detail for ONE instance (hydration)
``instance_health``          — wedge report (seven reporting predicates)
``fleet_capacity_summary``   — "how's the nest?" (counts, queue, pool, DLQ)
``instance_fleet_summary``   — ALIAS of ``fleet_capacity_summary`` (F1)

CENSUS AND DETAIL ARE COMPLEMENTARY, NOT NESTED
------------------------------------------------
A census over 200 rows must NOT hydrate the task-table join
(``current_task``) or the checkpoint read (``last_messages``) per row —
those are paid for ONCE, in ``instance_get``. The split is structural,
not a convenience.

THE FIELD MODEL IS ORTHOGONAL — NO NEW VOCABULARY (D-19)
----------------------------------------------------------
Four things are reported, and they are deliberately NOT collapsed into
one derived enum:

1. ``status``  — the instance's OWN 10-value domain, and the ONLY
   server-side filter (applied in SQL, never as a post-page Python
   filter — a Python-side status filter past one page silently returns
   an arbitrary pinned-ordered subset).
2. ``liveness`` — the canonical vocabulary, derived from
   ``_STATUS_CANONICAL_MAP`` (``daemon/services/work_status.py`` — "the
   single source of truth — no parallel vocabulary set is maintained").
   It is a RETURNED FIELD, never a filter.
3. ``stale`` / ``idle_seconds`` — the TIME axis, reported SEPARATELY.
   Never folded into the liveness enum.
4. ``pause_marker`` — reported alongside, so a ``paused`` row reads as
   "deliberately parked, 4h idle", not "stalled".

A caller that wants the old ``alive``/``stalled`` can compute it in one
line from ``status`` + ``stale``.

THE PARK/WEDGE DISTINCTION IS THE POINT
----------------------------------------
A deliberately parked instance (paused + the deliberate-pause
tombstone) has a FROZEN ``last_activity_at``, so past the staleness
threshold it is byte-identical to a genuinely wedged one under any
liveness derivation. The park marker exists precisely to keep the two
apart — the liveness value does NOT change with staleness.

Access posture — TWO layers, BOTH required
------------------------------------------
1. The ``instance_read`` category is PRIVILEGED, so it never joins the
   empty-allow default-grant universe.
2. A privileged category alone is NOT sufficient — a **literal tool
   name** in an allow list bypasses the category gate entirely. So
   every tool body ALSO calls the one shared module-level helper
   :func:`_is_instance_read_authorized`.

Both gates are required; neither substitutes for the other.

Tools NEVER raise — every failure returns a typed refusal string. The
audit ledger belongs to the ACT category only: a read has no side
effect and writes NO audit row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Annotated, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..repositories.instance.models import InstanceStatus
from ..services.work_status import _STATUS_CANONICAL_MAP
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Instance Read"
CATEGORY_DOC = """\
Read-only views over the instance fleet — the fleet census, one
instance's deep detail, the wedge report, and the capacity summary.

Tools in this category:
- ``instance_list``           — fleet census (one cheap row per instance)
- ``instance_get``            — deep detail for ONE instance
- ``instance_health``         — wedge report (seven detector classes)
- ``fleet_capacity_summary``  — counts, queue depth, worker pool, DLQ
- ``instance_fleet_summary``  — alias of fleet_capacity_summary

These tools READ. They never change anything, never write an audit
row, and never trigger a repair. They stay available even when the
matching act surface is switched off — you diagnose with the reads and
act only when the act surface is enabled.

The census is CHEAP by design: one row per instance, no task-table
join, no checkpoint read. ``instance_get`` is the expensive one — it
pays for the hydration of exactly ONE instance.

Filter the census by ``status`` (the instance's own status value).
Everything else you can filter on — staleness, agent, parent — is a
field on the returned rows.

``instance_health`` NEVER repairs anything. It reports what looks
sick so you can decide; the acts are separate tools.

MEASURES — read them for what they count (F5): `dlq_depth` is the row
count of the legacy `dead_letter_items` SNAPSHOT table (DLQ-service
writes only, retention-pruned); `dead_jobs_total` is the canonical
count of `job_queue_items` in the terminal `dead` admission state. A
dead job can exist with NO snapshot row, so `dlq_depth <=
dead_jobs_total` is expected — `dlq_depth = 0` with
`dead_jobs_total > 0` is NOT an error.
"""

#: The agents allowed to READ the instance fleet. The privileged
#: category gates VISIBILITY; this set gates AUTHORITY. Both are
#: required (a literal-tool-name allow entry bypasses the category
#: gate). MAINTENANCER-ONLY (D-13 — one owner for both categories).
INSTANCE_READ_AUTHORIZED_AGENTS: frozenset[str] = frozenset({"maintenancer"})

#: The maximum number of census rows one call may return.
MAX_LIST_LIMIT = 200
#: The default census page size.
DEFAULT_LIST_LIMIT = 50
#: The default ``instance_health`` threshold, in minutes.
DEFAULT_STALE_MINUTES = 30
#: The default staleness threshold, in seconds — DERIVED from the one
#: minute constant so the two tools can never disagree (L3 fix).
DEFAULT_STALE_SECONDS = DEFAULT_STALE_MINUTES * 60

#: The minimum age (seconds) of a NEVER-ACTIVATED instance before the
#: husk detector reports it (nest-ops shakedown F3, D-1). 24h — four
#: times the longest legitimate spawn→dispatch horizon in the tree and
#: twelve times the age of the youngest known production husk, so
#: fixture age jitter cannot flip a verdict.
#:
#: It is deliberately NOT derived from ``stale_minutes``: that argument
#: answers "a RUNNING instance with no activity for N minutes" (a
#: turn-in-flight judgement); this constant answers "this instance
#: never had a turn AT ALL" (an evidence judgement). It is deliberately
#: NOT a tool argument either — the threshold moves by editing this
#: constant, never by widening the tool schema.
DEFAULT_HUSK_MIN_AGE_SECONDS: int = 86_400
#: The maximum number of checkpoint messages ``instance_get`` returns.
MAX_LAST_MESSAGES = 20

#: The fleet scan ceiling for ``instance_health`` and
#: ``fleet_capacity_summary`` (M8): past this many instances the
#: OLDEST rows are invisible to the scan — both payloads surface
#: ``scan_truncated`` so the blind spot is VISIBLE, never silent.
FLEET_SCAN_LIMIT = 1000

#: The number of offset windows ``instance_health`` may walk before it
#: gives up (F4b). ``FLEET_SCAN_LIMIT`` is the WINDOW SIZE; this is the
#: window COUNT, so one report covers at most
#: ``FLEET_SCAN_LIMIT * FLEET_SCAN_MAX_WINDOWS`` rows (20 000 at the
#: shipped constants). The cap exists so a huge fleet cannot turn one
#: tool call into an unbounded walk — and when it fires, the payload
#: says so (``scan_truncated`` + ``scan_stop_reason``).
FLEET_SCAN_MAX_WINDOWS = 20

#: The wall-clock budget (seconds) for the ``instance_health`` fleet
#: scan (D-20). Checked BEFORE each window; on exhaustion the walk
#: breaks with ``scan_truncated=True`` and
#: ``scan_stop_reason="time_budget"``. A 20-second tool call would eat
#: a sixth of the LLM read window — the budget converts an unbounded
#: cost into a loud bounded one.
FLEET_SCAN_TIME_BUDGET_SECONDS = 10

#: The metadata keys that ``instance_get`` is allowed to surface. The
#: raw metadata blob is NEVER returned — the existing
#: ``get_instance_info`` tool returns it verbatim, and that is a
#: genuine improvement this category makes.
_INSTANCE_GET_METADATA_KEYS: tuple[str, ...] = (
    "title",
    "model",
    "model_override",
    "llm_model",
    "source_type",
    "watchover_enabled",
    "question_paused",
    # The act tools write DOTTED marker keys (instance_ops.py:151-152);
    # the old dead top-level "instance_ops" entry never matched them
    # (M2 fix) — the maintenancer could not read back its own marker.
    "instance_ops.protect",
    "instance_ops.suspect",
)


def _is_instance_read_authorized(agent_id: str) -> bool:
    """Is this agent allowed to read the instance fleet?

    ONE shared helper, called by ALL FOUR tool bodies — the set lives
    in exactly one place and the four bodies share it (the
    ``daemon/tools/views_auth.py`` / ``daemon/tools/board_tools.py``
    precedent).

    Fail-closed: a blank or non-``str`` ``agent_id`` is NEVER
    authorized.
    """
    if not isinstance(agent_id, str) or not agent_id:
        return False
    return agent_id in INSTANCE_READ_AUTHORIZED_AGENTS


def _refusal(label: str, token: str, message: str) -> str:
    """The house refusal envelope.

    ``Error: {label} REFUSED — reason={token}: {message}`` — the same
    shape the act category uses, so one parser reads both.
    """
    return f"Error: {label} REFUSED — reason={token}: {message}"


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


def _liveness_for(status: str) -> str:
    """Canonical liveness for an instance status.

    Derived from ``_STATUS_CANONICAL_MAP`` — the single source of
    truth. NO parallel vocabulary set is maintained here; a status the
    canonical map does not know renders as ``"unknown"`` rather than
    inventing a value.
    """
    if not isinstance(status, str):
        return "unknown"
    return _STATUS_CANONICAL_MAP.get(status, "unknown")


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp defensively — ``None`` on garbage."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds_since(value: Any, *, now: datetime) -> int | None:
    """Seconds between ``now`` and an ISO timestamp (``None`` if absent)."""
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _redact_metadata(metadata: Any) -> dict[str, Any]:
    """The REDACTED safe subset of ``instances.metadata``.

    The raw blob is NEVER returned — it can carry prompt payloads,
    credentials-adjacent values and internal plumbing. Only the keys
    on the allow list survive, and only scalar/JSON-safe values.
    """
    if not isinstance(metadata, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _INSTANCE_GET_METADATA_KEYS:
        if key not in metadata:
            continue
        value = metadata.get(key)
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue
        out[key] = value
    return out


def _pause_marker_of(metadata: Any) -> dict[str, Any] | None:
    """The pause-cause marker of an instance row, or ``None``.

    The marker lives in ``instances.metadata["question_paused"]``:
    ``{"pack_id":…, "paused_at":…}`` for a question pause and
    ``{"deliberate": True, "cleared_at": …}`` for a deliberate park.
    """
    if not isinstance(metadata, dict):
        return None
    marker = metadata.get("question_paused")
    return marker if isinstance(marker, dict) else None


def _question_pack_of(manager: Any, instance_id: str) -> Any:
    """The instance's pending question pack, or ``None``.

    A cheap RAM-dict lookup — question packs live in memory
    (``daemon/services/question_manager.py``), so this NEVER touches
    the database.
    """
    qm = getattr(manager, "_question_manager", None)
    if qm is None:
        return None
    try:
        return qm.get_question_pack(instance_id)
    except Exception:  # noqa: BLE001 — read-only probe
        return None


def _pending_counts_by_instance(manager: Any) -> dict[str, int]:
    """ONE grouped query: pending message counts per instance.

    A per-row queue-stat call would be two queries per census row; the
    census is bounded at 200 rows, so the grouped read is the shape
    that keeps the census cheap. Defensive: a failure returns ``{}``
    (the rows then report ``pending_count`` as 0).
    """
    engine = getattr(manager, "engine", None)
    if engine is None:
        return {}
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT instance_id, COUNT(*) FROM message_queue "
                    "WHERE status IN ('pending', 'ready', 'processing', "
                    "'retrying') GROUP BY instance_id"
                )
            ).all()
        return {str(r[0]): int(r[1]) for r in rows if r[0]}
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: pending-count read failed: %s", exc)
        return {}


def _lineage_max_activity(manager: Any, instance_id: str) -> str | None:
    """MAX(last_activity_at) across the instance's PERMANENT lineage.

    R4 (hard): ``last_activity_at`` FREEZES on a ``waiting_children``
    parent while a descendant streams mid-LLM, so a per-row staleness
    check false-positives on every live parent. The aggregate uses the
    existing primitive
    (``repo.get_max_last_activity_in_instances``) over the permanent
    lineage (``repo.get_tree_ids_permanent``) — the tree's semantics are
    already settled there and are NOT re-derived here.

    Returns the ISO timestamp of the aggregate, or ``None``.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return None
    try:
        tree_ids = repo.get_tree_ids_permanent(instance_id)
        if not tree_ids:
            tree_ids = [instance_id]
        stamp = repo.get_max_last_activity_in_instances(tree_ids)
        if stamp is None:
            return None
        if isinstance(stamp, datetime):
            return stamp.isoformat()
        return str(stamp)
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug(
            "instance_read: lineage-max read failed for %s: %s", instance_id, exc
        )
        return None


# ─────────────────────────────────────────────────────────────────────
# Internal plumbing shared by the tools
# ─────────────────────────────────────────────────────────────────────
async def _resolve_target(manager: Any, instance_id: str) -> str:
    """Resolve an instance id with the existing fuzzy-match helper.

    Deferred import: ``daemon/tools/instance.py`` imports THIS module's
    factory (the third registration leg), so a module-level import here
    would be circular. The deferred import resolves at call time, when
    both modules are fully loaded.

    .. deprecated:: 2026-10-24 (nest-ops shakedown F8)
        The WRITE-path resolver hydrates the instance: it goes through
        ``manager.get_instance``, whose cold path runs MCP schema
        discovery AND ``_restore_instance`` — which WRITES the row.
        A read must not do either. ``instance_get`` now uses
        :func:`_resolve_target_readonly`; this helper remains for any
        caller that genuinely wants the hydration side effect.

    Raises:
        ValueError: when the id is empty, unknown, or ambiguous — the
            caller maps it to the ``instance-not-found`` refusal.
    """
    from .instance import _resolve_instance_id

    return await _resolve_instance_id(manager, instance_id)


async def _resolve_target_readonly(
    manager: Any, instance_id: str, *, repo: Any = None
) -> tuple[str, Any]:
    """Resolve an instance id WITHOUT hydrating the instance (F8).

    Repo-only exact match, then the existing fuzzy matcher — and it
    RETURNS THE FETCHED ROW so the caller does not pay a second
    ``repo.get``. It NEVER calls ``manager.get_instance``: that path's
    cold branch runs ``ensure_mcp_preloaded`` (per-server
    ``get_schemas_for_server`` — external discovery, uncached on empty)
    and ``_restore_instance`` (whose re-elevation WRITES
    ``agent_tag``/``agent_dir`` on the row).

    Behaviour parity with the write-path resolver it replaces — all
    four refusal texts are reproduced BYTE-FOR-BYTE from
    ``daemon/tools/instance.py::_resolve_instance_id``, and the loud
    WARNING on an exact-miss is kept (that failure is otherwise silent:
    the tool surface renders it as a ``ToolMessage``, never a log line).
    The shared helper itself is NOT touched — it is pinned by
    ``tests/unit/test_find_near_instance.py`` and remains the write
    path's resolver.

    Args:
        manager: The ``InstanceManager`` — only its repository and
            ``find_near_instance`` are touched.
        instance_id: The id to resolve (short form accepted).
        repo: The instance repository, when the caller already has it
            (avoids a second ``getattr``); resolved from the manager
            otherwise.

    Returns:
        ``(resolved_id, row)`` — the row is the fetched ``Instance``
        (never ``None`` on success, so the caller need not re-read).

    Raises:
        ValueError: when the id is empty, unknown, or ambiguous — the
            caller maps it to the ``instance-not-found`` refusal.
    """
    if repo is None:
        repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        raise RuntimeError("instance repository unavailable")

    if not instance_id:
        raise ValueError("ERROR: instance_id cannot be empty")

    # Exact match — repository read only. NO hydration, NO MCP, NO write.
    try:
        row = repo.get(instance_id)
    except Exception:
        logger.exception("instance_get exact read failed")
        raise
    if row is not None:
        return instance_id, row

    # WARNING parity with the write-path resolver: this failure is
    # otherwise silent (the tool wrapper renders it as a ToolMessage,
    # not a log line), and a just-spawned-but-unresolvable instance is
    # exactly the ghost-creation signature operators look for.
    logger.warning(
        "instance resolution failed (exact repo lookup miss): "
        "instance_id=%s — not in the instances table; "
        "check for a failed spawn / cold-load None-read "
        "(ghost-child risk for spawning parents)",
        instance_id,
        extra={"unresolved_instance_id": instance_id},
    )

    # Fuzzy match — ``manager.find_near_instance`` is repo-only (it
    # lists ids and computes edit distance in Python), so the fuzzy leg
    # hydrates nothing either.
    from ..utils import DEFAULT_FUZZY_MATCH_DISTANCE

    near_matches = manager.find_near_instance(
        instance_id, max_distance=DEFAULT_FUZZY_MATCH_DISTANCE
    )
    if near_matches:
        if len(near_matches) == 1:
            raise ValueError(
                f"ERROR: instance '{instance_id}' not found. "
                f"Did you mean '{near_matches[0]}'? Please retry with the corrected instance_id."
            )
        candidates = "', '".join(near_matches)
        raise ValueError(
            f"ERROR: instance '{instance_id}' not found. Multiple similar instances found: "
            f"'{candidates}'. Please retry with the correct instance_id."
        )
    raise ValueError(
        f"ERROR: instance '{instance_id}' not found and no similar instance found. "
        f"Please check the instance ID or spawn a new instance for your task."
    )


def _census_row(
    info: dict[str, Any],
    *,
    now: datetime,
    stale_seconds: int,
    pending_count: int,
    pack_present: bool,
    activity_iso: str | None,
) -> dict[str, Any]:
    """Shape ONE census row from a ``manager.list_instances`` dict.

    The census shape is deliberately CHEAP — no ``current_task``, no
    ``last_messages``. Those belong to :func:`instance_get`.
    """
    status = info.get("status")
    metadata = info.get("metadata")
    marker = _pause_marker_of(metadata)
    children = info.get("children") or []
    age_seconds = _seconds_since(info.get("created_at"), now=now)
    idle_seconds = _seconds_since(
        activity_iso if activity_iso is not None else info.get("last_activity_at"),
        now=now,
    )
    stale = idle_seconds is not None and idle_seconds > stale_seconds
    return {
        "instance_id": info.get("instance_id"),
        "agent_id": info.get("agent_id"),
        "agent_name": info.get("agent_name"),
        "status": status,
        "liveness": _liveness_for(status),
        "parent_id": info.get("parent_id") or None,
        "children_count": len(children),
        "age_seconds": age_seconds,
        "idle_seconds": idle_seconds,
        "stale": stale,
        "pause_marker": marker,
        "pending_question_pack": pack_present,
        "pending_count": pending_count,
        "project_id": info.get("project_id"),
        "title": info.get("title"),
        "waiting_on": (
            {"kind": "children"} if status == "waiting_children" else None
        ),
    }


def create_instance_read_tools(
    manager: Any,
    *,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the five ``instance_read`` tools for one instance/agent.

    Args:
        manager: The ``InstanceManager`` — the tools read through its
            repositories and services; they never write.
        current_instance_id: The caller's instance id (unused by the
            reads themselves; kept so the factory signature matches the
            other per-instance factories).
        agent_id: The caller's agent id. Captured at factory time and
            checked by :func:`_is_instance_read_authorized` in every
            tool body. An agent rename invalidates the closure at the
            next daemon restart (documented, acceptable).
        agent_tag: The caller's version tag (unused; signature parity).

    Returns:
        A list of the five ``@tool``-decorated callables (four reads +
        the ``instance_fleet_summary`` alias, F1).
    """
    caller_agent_id = agent_id or ""

    # ── instance_list ────────────────────────────────────────────────
    class InstanceListInput(BaseModel):
        """Input schema for instance_list."""

        status: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Exact instance status to filter on, applied in SQL: "
                    "idle | running | waiting | paused | completed | error | "
                    "terminated | queued | waiting_children | failed. Omit for "
                    "all statuses. Any other value is refused with "
                    "reason=unknown-status-value."
                ),
            ),
        ]
        limit: Annotated[
            int,
            Field(
                default=DEFAULT_LIST_LIMIT,
                description=(
                    f"Max rows to return (1-{MAX_LIST_LIMIT}, default "
                    f"{DEFAULT_LIST_LIMIT}). Out-of-range values are refused "
                    "with reason=invalid-limit."
                ),
            ),
        ]
        offset: Annotated[
            int,
            Field(
                default=0,
                description=(
                    "Rows to skip (SQL offset). A negative value is clamped to "
                    "0 and the clamp is reported in filters_applied."
                ),
            ),
        ]

    @register_tool_category("instance_read")
    @tool(args_schema=InstanceListInput)
    async def instance_list(
        status: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> str:
        """Fleet census — one cheap row per instance. Use tool_help("instance_list") for details."""
        if not _is_instance_read_authorized(caller_agent_id):
            return _error("instance_read operation not permitted for this agent")

        if status is not None and not InstanceStatus.is_valid(status):
            return _refusal(
                "INSTANCE_LIST",
                "unknown-status-value",
                (
                    f"{status!r} is not an instance status. Valid values: "
                    + ", ".join(s.value for s in InstanceStatus)
                ),
            )
        if not isinstance(limit, int) or isinstance(limit, bool):
            return _refusal(
                "INSTANCE_LIST",
                "invalid-limit",
                f"limit must be an integer between 1 and {MAX_LIST_LIMIT}",
            )
        if limit < 1 or limit > MAX_LIST_LIMIT:
            return _refusal(
                "INSTANCE_LIST",
                "invalid-limit",
                f"limit must be between 1 and {MAX_LIST_LIMIT} (got {limit})",
            )

        clamped_offset = False
        applied_offset = offset
        if not isinstance(applied_offset, int) or isinstance(applied_offset, bool):
            applied_offset = 0
            clamped_offset = True
        elif applied_offset < 0:
            applied_offset = 0
            clamped_offset = True

        try:
            rows, total, _repo_truncated = await asyncio.to_thread(
                manager.list_instances,
                limit=limit,
                offset=applied_offset,
                status=status,
            )
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_list failed")
            return _error(f"instance_list failed: {type(exc).__name__}")

        pending_counts = await asyncio.to_thread(_pending_counts_by_instance, manager)
        now = datetime.now(timezone.utc)

        census: list[dict[str, Any]] = []
        for info in rows:
            instance_id = info.get("instance_id") or ""
            activity_iso = None
            if info.get("status") == "waiting_children":
                # R4: the parent's own last_activity_at FREEZES while a
                # descendant streams — aggregate the lineage MAX instead.
                activity_iso = await asyncio.to_thread(
                    _lineage_max_activity, manager, instance_id
                )
            census.append(
                _census_row(
                    info,
                    now=now,
                    stale_seconds=DEFAULT_STALE_SECONDS,
                    pending_count=pending_counts.get(instance_id, 0),
                    pack_present=_question_pack_of(manager, instance_id) is not None,
                    activity_iso=activity_iso,
                )
            )

        # F4a: an HONEST truncation flag.
        #
        # The repository's flat path returns a CONSTANT ``False``
        # (``daemon/repositories/instance/repository.py`` — the flat path
        # never runs the descendant BFS), so copying that flag verbatim
        # made ``truncated`` dead: a census that stopped at the page
        # boundary read as "complete". The tool computes the flag from the
        # numbers it already has:
        #
        #   total_matching — the COUNT(*) over the SAME WHERE (all matching
        #                   rows, NOT the remaining-after-offset count);
        #   applied_offset — the clamped offset the page was taken at;
        #   len(census)   — the rows actually returned in THIS page.
        #
        # ``offset + len(rows)`` is therefore the number of matching rows
        # consumed so far, and the comparison is EXACT. The empty page
        # case reads ``True`` ONLY when ``total > offset`` — the
        # exhausted case (``len(rows) == 0 and total <= offset``) reads
        # ``False``, because nothing exists past the caller's offset.
        #
        # ``_repo_truncated`` is the repository's descendant-cap flag; it
        # is False on the flat path this tool always uses
        # (``include_descendants`` is never set here), so it carries no
        # signal the formula above does not already express.
        truncated = bool(total > applied_offset + len(census))

        payload = {
            "rows": census,
            "count": len(census),
            "total_matching": total,
            "truncated": truncated,
            "filters_applied": {
                "status": status,
                "limit": limit,
                "offset": applied_offset,
                "offset_clamped": clamped_offset,
                "stale_threshold_seconds": DEFAULT_STALE_SECONDS,
            },
        }
        return json.dumps(payload, default=str)

    instance_list._full_doc_ = f"""Fleet census — one cheap row per instance.

This is the CENSUS, not the detail view. It deliberately does NOT
include `current_task` or `last_messages` — those are paid for once, in
`instance_get`, because hydrating them per row would turn a 200-row
census into 400 extra queries.

Args:
    status: Exact instance status to filter on. The filter is applied in
        SQL, so a fleet larger than one page still returns a CORRECT
        filtered result (never an arbitrary pinned-ordered subset of the
        first page). Valid values: idle, running, waiting, paused,
        completed, error, terminated, queued, waiting_children, failed.
        Omit for all statuses. Any other value is refused with
        `reason=unknown-status-value`.
        F12 (2026-10-24): the filter used to be dropped on the way to
        the repository, so this paragraph was FALSE — the rows came back
        unfiltered while `total_matching` counted only the matching
        population. The filter now reaches SQL, and `total_matching`
        counts exactly the population the returned rows are drawn from.
    limit: Max rows to return (1-{MAX_LIST_LIMIT}, default
        {DEFAULT_LIST_LIMIT}). Out of range is refused with
        `reason=invalid-limit`.
    offset: Rows to skip. SQL offset — this is real pagination, so
        `offset=limit` continues where the previous page ended. A
        negative value is clamped to 0 and the clamp is VISIBLE in
        `filters_applied.offset_clamped`.

Returns:
    One JSON string: `rows` (the census), `count` (rows in THIS page),
    `total_matching` (all rows matching the status filter), `truncated`,
    and `filters_applied`.

    `truncated` is HONEST and re-derivable from this payload alone:
    `truncated == (total_matching > filters_applied.offset + count)`.
    It is `true` when more matching rows exist past this page, and
    `false` on an exhausted page (nothing matches past your offset).
    It was a constant `false` before 2026-10-24 (F4a) — a census that
    stopped at the page boundary used to read as "complete".

    Each row: `instance_id`, `agent_id`, `agent_name`, `status` (the
    raw 10-value instance status), `liveness` (the canonical vocabulary,
    a RETURNED FIELD — never a filter), `parent_id`, `children_count`,
    `age_seconds`, `idle_seconds`, `stale`, `pause_marker`,
    `pending_question_pack`, `pending_count`, `project_id`, `title`,
    `waiting_on`.

    `pause_marker` distinguishes a DELIBERATELY PARKED instance from a
    wedged one: a parked row reads as "deliberately parked, N seconds
    idle", never as "stalled". `stale` is a separate field for exactly
    that reason — do not fold it into `liveness`.

Side effects:
    NONE. Read-only; no audit row. The census issues SELECTs only (the
    per-row pending-count probe is one grouped query, never one per
    row). The `truncated` flag is computed from the page numbers the
    payload already carries.

Examples:
    instance_list(status="running", limit=100)          # live fleet
    instance_list(status="paused")                       # parked / paused
    instance_list(limit=50, offset=50)                    # page 2
"""

    # ── instance_get ─────────────────────────────────────────────────
    class InstanceGetInput(BaseModel):
        """Input schema for instance_get."""

        id: Annotated[
            str,
            Field(
                description=(
                    "Instance id (short form accepted — typos are resolved by "
                    "fuzzy match). Required."
                )
            ),
        ]

    @register_tool_category("instance_read")
    @tool(args_schema=InstanceGetInput)
    async def instance_get(id: str) -> str:
        """Deep detail for ONE instance (hydration). Use tool_help("instance_get") for details."""
        if not _is_instance_read_authorized(caller_agent_id):
            return _error("instance_read operation not permitted for this agent")

        repo = getattr(manager, "_instance_repository", None)
        if repo is None:
            return _error("instance_get: instance repository unavailable")

        try:
            # F8: the READ-scoped resolver — repo exact match + the
            # repo-only fuzzy matcher. It NEVER calls
            # ``manager.get_instance``, so a read triggers no MCP schema
            # discovery and never takes the ``_restore_instance`` path
            # that writes the row. The fetched row comes back with the
            # id, so the caller does not re-read it.
            instance_id, row = await _resolve_target_readonly(
                manager, id, repo=repo
            )
        except ValueError as exc:
            return _refusal("INSTANCE_GET", "instance-not-found", str(exc))
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_get resolve failed")
            return _error(f"instance_get failed: {type(exc).__name__}")
        if row is None:
            return _refusal(
                "INSTANCE_GET",
                "instance-not-found",
                f"no instance row for {instance_id!r}",
            )

        info = row.to_dict()
        now = datetime.now(timezone.utc)

        # Children + descendants from the PERMANENT parent_id record (the
        # instance_hierarchy working set deletes rows on completion).
        try:
            children = await asyncio.to_thread(
                repo.list_child_ids_permanent, instance_id
            )
        except Exception:  # noqa: BLE001 — read-only probe
            children = []
        try:
            tree_ids = await asyncio.to_thread(
                repo.get_tree_ids_permanent, instance_id
            )
        except Exception:  # noqa: BLE001 — read-only probe
            tree_ids = []
        descendants = [i for i in (tree_ids or []) if i != instance_id]

        # Ancestors: walk the permanent parent chain (bounded).
        ancestors: list[str] = []
        direct_parent_agent_id: str | None = None
        cursor = info.get("parent_id")
        seen: set[str] = set()
        while cursor and cursor not in seen and len(ancestors) < 64:
            seen.add(cursor)
            ancestors.append(cursor)
            try:
                parent_row = await asyncio.to_thread(repo.get, cursor)
            except Exception:  # noqa: BLE001 — read-only probe
                break
            if parent_row is None:
                break
            if direct_parent_agent_id is None:
                # H1: the DIRECT parent's agent — the SPAWNER. NEVER
                # fall back to the target's own agent id here.
                direct_parent_agent_id = getattr(parent_row, "agent_id", None)
            cursor = parent_row.parent_id
        ancestors.reverse()  # root -> parent
        if direct_parent_agent_id is None and info.get("parent_id"):
            # The walk broke before the direct parent resolved — ONE
            # bounded fallback fetch (still never the target's own agent).
            try:
                _prow = await asyncio.to_thread(repo.get, info["parent_id"])
            except Exception:  # noqa: BLE001 — read-only probe
                _prow = None
            if _prow is not None:
                direct_parent_agent_id = getattr(_prow, "agent_id", None)
        if not info.get("parent_id"):
            spawned_by_note = "no-parent"  # a root instance — no spawner
        elif direct_parent_agent_id is None:
            spawned_by_note = "parent-agent-unresolved"
        else:
            spawned_by_note = None

        # Hydration 1: the task-table row (the census never pays for this).
        current_task = None
        task_repo = getattr(manager, "_task_repo", None)
        if task_repo is not None:
            try:
                tasks = await asyncio.to_thread(
                    task_repo.get_by_instance, instance_id
                )
            except Exception:  # noqa: BLE001 — read-only probe
                tasks = []
            terminal_task_statuses = {"completed", "failed", "cancelled"}
            live = [t for t in tasks if getattr(t, "status", None) not in terminal_task_statuses]
            chosen = live[-1] if live else (tasks[-1] if tasks else None)
            if chosen is not None:
                current_task = {
                    "id": getattr(chosen, "id", None),
                    "work_id": getattr(chosen, "work_id", None),
                    "task_type": getattr(chosen, "task_type", None),
                    "status": getattr(chosen, "status", None),
                    "created_at": getattr(chosen, "created_at", None),
                }

        # Hydration 2: the last N checkpoint messages (bounded).
        last_messages: list[dict[str, Any]] = []
        try:
            state = await manager.checkpointer.raw_saver.aget(
                {"configurable": {"thread_id": instance_id}}
            )
            messages = (
                (state or {}).get("channel_values", {}).get("messages", []) or []
            )
            for msg in messages[-MAX_LAST_MESSAGES:]:
                content = getattr(msg, "content", "")
                if not isinstance(content, str):
                    content = str(content)
                last_messages.append(
                    {
                        "type": getattr(msg, "type", type(msg).__name__),
                        "id": getattr(msg, "id", None),
                        "content": content[:2000],
                    }
                )
        except Exception as exc:  # noqa: BLE001 — checkpoint read is optional
            logger.debug(
                "instance_get: checkpoint read failed for %s: %s", instance_id, exc
            )

        pack = _question_pack_of(manager, instance_id)
        pack_payload = None
        if pack is not None:
            try:
                from ..services.question_manager import pack_to_dict

                pack_payload = pack_to_dict(pack)
            except Exception:  # noqa: BLE001 — read-only probe
                pack_payload = {"status": getattr(pack, "status", None)}

        metadata = info.get("metadata")
        marker = _pause_marker_of(metadata)
        activity_iso = None
        if info.get("status") == "waiting_children":
            activity_iso = await asyncio.to_thread(
                _lineage_max_activity, manager, instance_id
            )
        idle_seconds = _seconds_since(
            activity_iso if activity_iso is not None else info.get("last_activity_at"),
            now=now,
        )

        # M1: the old mission_ref probe getattr'd two resolver methods
        # that exist nowhere in the tree — a permanently-null field.
        # Deleted rather than wired (the smaller fix, review 2026-10-21).

        payload = {
            "instance_id": info.get("instance_id"),
            "agent_id": info.get("agent_id"),
            "agent_name": info.get("agent_name"),
            "agent_tag": info.get("agent_tag"),
            "status": info.get("status"),
            "liveness": _liveness_for(info.get("status")),
            # REDACTED safe subset — never the raw blob.
            "instance_metadata": _redact_metadata(metadata),
            "project_id": info.get("project_id"),
            "parent_id": info.get("parent_id") or None,
            "children": children,
            "children_count": len(children),
            "ancestors": ancestors,
            "descendants": descendants,
            "created_at": info.get("created_at"),
            "updated_at": info.get("updated_at"),
            "last_activity_at": info.get("last_activity_at"),
            "age_seconds": _seconds_since(info.get("created_at"), now=now),
            "idle_seconds": idle_seconds,
            "stale": idle_seconds is not None
            and idle_seconds > DEFAULT_STALE_SECONDS,
            "paused_at": info.get("paused_at"),
            "pause_marker": marker,
            "pending_question_pack": pack_payload,
            "current_task": current_task,
            "last_messages": last_messages,
            "spawned_by": {
                "parent_id": info.get("parent_id") or None,
                # H1: the SPAWNER's (direct parent's) agent id — NEVER
                # the target's own. `note` states why it may be null.
                "agent_id": direct_parent_agent_id,
                "note": spawned_by_note,
            },
            # M1: no canonical per-instance mission projection exists —
            # the permanently-null probe was deleted; key kept for shape.
            "mission_ref": None,
        }
        return json.dumps(payload, default=str)

    instance_get._full_doc_ = """Deep detail for ONE instance — the HYDRATION tool.

It earns its slot by paying for exactly what the census must not:
`current_task` (a task-table read) and `last_messages` (a checkpoint
read) are hydrated here, for ONE instance.

Args:
    id: Instance id. Required. The short form is accepted — a typo is
        resolved by fuzzy match. An unknown or ambiguous id is refused
        with `reason=instance-not-found`.

Returns:
    One JSON string: `instance_id`, `agent_id`, `agent_name`,
    `agent_tag`, `status` (raw), `liveness` (canonical),
    `instance_metadata` (a REDACTED safe subset — never the raw blob),
    `project_id`, `parent_id`, `children` (ids), `children_count`,
    `ancestors` (root -> parent), `descendants` (ids), `created_at`,
    `updated_at`, `last_activity_at`, `age_seconds`, `idle_seconds`,
    `stale`, `paused_at`, `pause_marker`, `pending_question_pack` (the
    pack OBJECT, not just a bool), `current_task`, `last_messages`,
    `spawned_by` (the PARENT's agent id — never the target's own;
    `note` states why the agent id may be null).

    `pending_question_pack` is `null` after a daemon restart — question
    packs live in memory only, and the honest rendering of that is a
    null, never a fabricated pack.

Side effects:
    NONE. Read-only; no audit row. A read never triggers MCP schema discovery and never restores the instance into memory — a cold instance stays cold.

    RESIDUALS of that purity (2026-10-24, F8): reads no longer warm the instance cache, so the next real operation on a cold instance pays the restore cost then; cold reads no longer PROMOTE `agent_tag` / `agent_dir`, so a row whose versioned agent dir reappeared after a fallback now reports its STORED fallback tag (payload-visible, and the correct read-pure behaviour); and a restore that used to ERROR now becomes a successful plain read.

Examples:
    instance_get(id="a1b2c3d4")     # short form accepted
"""

    # ── instance_health ──────────────────────────────────────────────
    class InstanceHealthInput(BaseModel):
        """Input schema for instance_health."""

        stale_minutes: Annotated[
            int,
            Field(
                default=DEFAULT_STALE_MINUTES,
                description=(
                    "Staleness threshold in minutes (>= 1, default 30). A "
                    "non-positive value is refused with "
                    "reason=invalid-stale-minutes."
                ),
            ),
        ]

    @register_tool_category("instance_read")
    @tool(args_schema=InstanceHealthInput)
    async def instance_health(stale_minutes: int = DEFAULT_STALE_MINUTES) -> str:
        """Wedge report — seven read-only detectors. Use tool_help("instance_health") for details."""
        if not _is_instance_read_authorized(caller_agent_id):
            return _error("instance_read operation not permitted for this agent")

        if (
            not isinstance(stale_minutes, int)
            or isinstance(stale_minutes, bool)
            or stale_minutes < 1
        ):
            return _refusal(
                "INSTANCE_HEALTH",
                "invalid-stale-minutes",
                f"stale_minutes must be an integer >= 1 (got {stale_minutes!r})",
            )

        threshold_seconds = stale_minutes * 60
        try:
            report, scan_truncated, scan_stop_reason = await asyncio.to_thread(
                _build_health_report, manager, threshold_seconds
            )
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_health failed")
            return _error(f"instance_health failed: {type(exc).__name__}")

        payload = {
            "stale_minutes": stale_minutes,
            "detectors": report,
            "total_flagged": sum(d["count"] for d in report),
            # M8: a VISIBLE blind spot — the scan walks the whole fleet
            # in offset windows, so the blind spot is now the WINDOW CAP
            # (or the wall-clock budget), not "the newest 1000 rows".
            "scan_truncated": bool(scan_truncated),
            # F4b: WHY the walk stopped — `end_of_set` (the whole fleet
            # was covered), `window_cap` (too many windows) or
            # `time_budget` (the walk ran out of wall-clock).
            "scan_stop_reason": scan_stop_reason,
        }
        return json.dumps(payload, default=str)

    instance_health._full_doc_ = """Wedge report — seven read-only detector classes.

This NEVER repairs anything. It reports what looks sick so you can
decide what to do; the acts are separate tools.

Args:
    stale_minutes: Staleness threshold in minutes (>= 1, default 30).
        A non-positive value is refused with
        `reason=invalid-stale-minutes`.

Returns:
    One JSON string: `stale_minutes`, `detectors` (a list), and
    `total_flagged`.

    Each detector row: `detector` (the class name), `instances` (the
    flagged rows with `instance_id`, `agent_id`, `status`,
    `idle_seconds`, `pause_marker`, `detail`), `count`, and
    `disagreement_pin` — the observable sweep counterpart the detector
    is pinned against, or `null` where no observable counterpart
    exists (an honest gap, not a pretend one).

    The seven classes: `stale-running`, `waiting-on-a-dead-parent`,
    `question-paused-without-task-row`, `zombie-pending`, `orphans`,
    `failed-with-unreported-result`, `never-dispatched-husk`.

    `never-dispatched-husk` is the SEVENTH detector (added 2026-10-24,
    F3): a row that was SPAWNED but never received any work at all —
    no task, no job item, no message, no child, no pending question,
    no pause marker, and no activity EVER (`last_activity_at` is null)
    — aged past 24h (`DEFAULT_HUSK_MIN_AGE_SECONDS`). For a husk,
    `idle_seconds` is the AGE since `created_at`, because "how long
    since it last did anything" has no answer: it never did anything.
    The `detail` string names the mechanism, so a husk row is never
    mistaken for an activity-derived idle.

    `scan_truncated` says whether the fleet was covered COMPLETELY;
    `scan_stop_reason` says WHY the walk stopped —
    `end_of_set` (the whole fleet was read), `window_cap` (the walk hit
    its window ceiling) or `time_budget` (the walk hit its wall-clock
    budget).

    THE SCAN IS AN OFFSET WINDOW LOOP over a LIVE fleet: rows created
    or pinned mid-scan may be counted twice or missed, and
    `scan_truncated=true` means the OLDEST rows may not be covered.
    Read the flag as "this report may not see everything", never as
    "the fleet is healthy".

    IMPORTANT — a deliberately parked instance is NOT wedged. The
    `pause_marker` on a flagged row tells you which one you are looking
    at. The `stale-running` detector aggregates activity across the
    instance's LINEAGE, so a live parent waiting on a streaming child is
    never flagged.

Side effects:
    NONE. A health report never triggers a repair. The scan issues
    SELECTs only — the fleet walk, the per-candidate evidence probes
    (two batched round trips) and the job-active census are all reads.

Examples:
    instance_health()                 # 30-minute threshold
    instance_health(stale_minutes=90)  # only long-idle wedges
"""

    # ── fleet_capacity_summary ───────────────────────────────────────
    class FleetCapacitySummaryInput(BaseModel):
        """Input schema for fleet_capacity_summary (no arguments)."""

    @register_tool_category("instance_read")
    @tool(args_schema=FleetCapacitySummaryInput)
    async def fleet_capacity_summary() -> str:
        """How's the nest — counts, queue depth, pool, DLQ. Use tool_help("fleet_capacity_summary") for details."""
        if not _is_instance_read_authorized(caller_agent_id):
            return _error("instance_read operation not permitted for this agent")

        try:
            payload = await asyncio.to_thread(_build_capacity_summary, manager)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("fleet_capacity_summary failed")
            return _error(f"fleet_capacity_summary failed: {type(exc).__name__}")
        return json.dumps(payload, default=str)

    fleet_capacity_summary._full_doc_ = """How's the nest — the fleet and system capacity in one view.

Two thirds of this payload is SYSTEM capacity (queue depth, worker
pool, dead-letter depth), not instances — that is why the tool is named
for the fleet's capacity rather than for an instance.

`instance_fleet_summary` is an alias of this tool; both return the same
payload.

Args:
    (none)

Returns:
    One JSON string: `counts_by_agent`, `counts_by_status`,
    `counts_by_liveness`, `queue_depth` (one row per queue with
    `queue_name`, `pending`, `active`, `dead`), `worker_pool` (`size`,
    `busy`, `idle`, `saturated`), `dlq_depth`, `dead_jobs_total`,
    `dlq_note`, `totals` (`total_rows`, `counted_rows`,
    `scan_truncated`, `reconciled`, `note`), `audit_pending`
    (`count` + `oldest_age_seconds`), `generated_at`.

    `dead_jobs_total` — the canonical count of jobs in the terminal
    `dead` admission state (summed from the per-queue `dead` column).
    `dlq_depth` is a **different** measure: the row count of the legacy
    `dead_letter_items` snapshot table, written only when a job is moved
    to the dead-letter queue through the DLQ service and pruned by the
    retention sweep. A dead job can exist with **no** snapshot row (the
    drift-recovery path finalizes to `dead` directly), so
    `dlq_depth <= dead_jobs_total` is expected and `dlq_depth = 0` with
    `dead_jobs_total > 0` is **not** an error. The same text rides the
    payload as `dlq_note`.

    `totals` is the reconciliation surface: `total_rows` is the
    independent COUNT(*) over the census WHERE, `counted_rows` is the
    sum of `counts_by_status`, and `reconciled` is their agreement flag.
    The census covers the WHOLE fleet (one GROUP BY, no scan window), so
    for an UNFILTERED `instance_list` call `totals.total_rows` equals
    that tool's `total_matching`. A FILTERED page is a different
    population — see the note in the payload.

    `audit_pending` is the reconciliation surface: a row stuck in
    `pending` means an ACT's intent was recorded but its outcome was
    never settled (a crash mid-act). When that happens, read the TRUE
    outcome from the INSTANCE STATE, not from the ledger — the row
    records the intent, the instance records the fact.

Side effects:
    NONE. Read-only. The census is one grouped COUNT/GROUP BY over the
    instances table plus SELECTs on the queue, worker-pool (in-memory
    stats) and audit surfaces.

Examples:
    fleet_capacity_summary()
"""

    # ── instance_fleet_summary (ALIAS of fleet_capacity_summary) ─────
    # F1 (nest-ops shakedown): the ratified rename specified
    # ``instance_fleet_summary``; the shipped surface is
    # ``fleet_capacity_summary``. BOTH names resolve. This is a SECOND
    # LITERAL ``@tool`` closure — a distinct named def, NOT
    # code-generated: the registry's AST discovery scans ``@tool`` at
    # any nesting depth (_tool_registry.py), so a loop/partial/helper
    # generated alias would be SILENTLY INVISIBLE to registration and
    # to the bidirectional name-drift pin.
    #
    # The LIVE name is untouched — same function, same decorator, same
    # payload (ONE builder), same doc (bar the one-sentence alias
    # mention). This is an ADDITIVE alias, never a rename. Its own
    # ``_full_doc_`` is mandatory: full docs are keyed by the tool
    # NAME, so without one the alias would show up in ``tool_help``
    # with an empty body.
    class InstanceFleetSummaryInput(BaseModel):
        """Input schema for instance_fleet_summary (no arguments)."""

    @register_tool_category("instance_read")
    @tool(args_schema=InstanceFleetSummaryInput)
    async def instance_fleet_summary() -> str:
        """Alias of fleet_capacity_summary — same payload. Use tool_help("fleet_capacity_summary") for details."""
        if not _is_instance_read_authorized(caller_agent_id):
            return _error("instance_read operation not permitted for this agent")

        try:
            payload = await asyncio.to_thread(_build_capacity_summary, manager)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_fleet_summary failed")
            return _error(f"instance_fleet_summary failed: {type(exc).__name__}")
        return json.dumps(payload, default=str)

    instance_fleet_summary._full_doc_ = """Alias of `fleet_capacity_summary` — the SAME payload, ONE builder.

The ratified rename (the nest-ops planning set specified
`instance_fleet_summary`) shipped as `fleet_capacity_summary`; both
names resolve to the same view and cannot drift, because both delegate
to the same builder. Use whichever name you already know.

Args:
    (none)

Returns:
    Identical to `fleet_capacity_summary` — see
    tool_help("fleet_capacity_summary") for the full field reference.

Side effects:
    NONE. Read-only; no audit row.

Examples:
    instance_fleet_summary()
    fleet_capacity_summary()   # the same payload, the live name
"""

    return [
        instance_list,
        instance_get,
        instance_health,
        fleet_capacity_summary,
        instance_fleet_summary,
    ]


# ─────────────────────────────────────────────────────────────────────
# The report builders — REPORTING PREDICATES, not composition (D-19)
#
# GT-4 established that the six wedge sweeps expose NO reusable
# read-only predicates: their classify steps are private, and the one
# public method on the job-recovery service is a RECONCILER (mutating).
# So the detectors below are written HERE as cheap, read-only,
# explicitly labelled PROJECTIONS. Where an observable sweep
# counterpart exists, the detector names it in `disagreement_pin` and a
# test pins the two code paths against each other on a labelled corpus.
# Where no observable counterpart exists, the row says `null` — an
# honest gap, not a pretend one.
# ─────────────────────────────────────────────────────────────────────
def _flagged_row(
    info: dict[str, Any],
    *,
    idle_seconds: int | None,
    detail: str,
) -> dict[str, Any]:
    """Shape one flagged row in a detector's `instances` list."""
    return {
        "instance_id": info.get("instance_id"),
        "agent_id": info.get("agent_id"),
        "status": info.get("status"),
        "idle_seconds": idle_seconds,
        "pause_marker": _pause_marker_of(info.get("metadata")),
        "detail": detail,
    }


def _parent_status_of(
    repo: Any,
    status_by_id: dict[str, str],
    parent_id: str | None,
    cache: dict[str, str | None] | None = None,
) -> str | None:
    """The parent's status, or ``None`` when the parent row is gone.

    ``status_by_id`` is the COMPACT ``instance_id -> status`` map the
    streaming scan keeps (the fleet's rows are never materialized, so
    the map is the only cross-row state). A parent that is not in the
    map is fetched from the repository — the cost is paid ONCE per
    distinct parent because the resolved value is memoized in ``cache``.
    """
    if not parent_id:
        return None
    if cache is not None and parent_id in cache:
        return cache[parent_id]
    status = status_by_id.get(parent_id)
    if status is None:
        try:
            prow = repo.get(parent_id)
        except Exception:  # noqa: BLE001 — read-only probe
            prow = None
        status = getattr(prow, "status", None) if prow is not None else None
    if cache is not None:
        cache[parent_id] = status
    return status


def _has_live_task(task_repo: Any, instance_id: str) -> bool:
    """True when the instance owns a non-terminal task row."""
    if task_repo is None:
        return False
    try:
        tasks = task_repo.get_by_instance(instance_id)
    except Exception:  # noqa: BLE001 — read-only probe
        return False
    terminal = {"completed", "failed", "cancelled"}
    return any(getattr(t, "status", None) not in terminal for t in tasks)


def _chunked_ids(instance_ids: list[str], size: int) -> list[list[str]]:
    """Split an id list into consecutive chunks (the bind-limit guard)."""
    if size <= 0:
        return [list(instance_ids)]
    return [
        instance_ids[i : i + size]
        for i in range(0, len(instance_ids), size)
    ]


#: The bind-parameter chunk size for the husk detector's batched
#: JobItem probe. SQLite's ``SQLITE_MAX_VARIABLE_NUMBER`` is 999; 500
#: leaves headroom and matches the repository's evidence-probe chunk.
_HUSK_PROBE_CHUNK = 500


def _job_item_counts_by_instance(
    manager: Any, instance_ids: list[str]
) -> dict[str, int] | None:
    """ONE batched grouped query: JobItem rows per instance, ANY state.

    E3 of the husk predicate (nest-ops shakedown F3). A ``job_queue_items``
    row is real evidence that a dispatch was attempted and mirrored —
    in ANY admission state, soft-deleted included (a soft-deleted job
    still proves a dispatch happened), so there is deliberately NO
    ``deleted_at`` filter. The repository's own helpers cannot answer
    "any row, any state" (``JobRepository.get_by_instance`` filters
    ``deleted_at IS NULL`` and returns only the newest;
    ``find_jobs_by_instance`` filters to the ACTIVE admission states).

    Chunked so the rendered ``IN (...)`` stays inside SQLite's bind
    limit.

    Returns the ``{instance_id: count}`` mapping, or ``None`` when the
    probe could not run — the caller must then NOT flag (a husk verdict
    must not rest on an unavailable leg).
    """
    engine = getattr(manager, "engine", None)
    if engine is None or not instance_ids:
        return None
    counts: dict[str, int] = {}
    try:
        from sqlalchemy import bindparam, text

        stmt = text(
            "SELECT instance_id, COUNT(*) FROM job_queue_items "
            "WHERE instance_id IN :ids GROUP BY instance_id"
        ).bindparams(bindparam("ids", expanding=True))
        with engine.connect() as conn:
            for chunk in _chunked_ids(list(instance_ids), _HUSK_PROBE_CHUNK):
                if not chunk:
                    continue
                rows = conn.execute(stmt, {"ids": list(chunk)}).all()
                for instance_id, count in rows:
                    if instance_id:
                        counts[str(instance_id)] = int(count or 0)
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: job-item evidence read failed: %s", exc)
        return None
    return counts


def _husk_row_local_candidate(
    manager: Any,
    info: dict[str, Any],
    *,
    now: datetime,
    terminal_statuses: Any,
    threshold: int,
) -> int | None:
    """The row-local legs of the husk predicate — the candidate's AGE.

    Non-terminal ∧ ``last_activity_at is None`` ∧ no children (E4, the
    per-row attach) ∧ no question pack (E5, RAM) ∧ no pause marker (E6)
    ∧ age > threshold. ``None`` means "not a candidate". The BATCHED
    evidence legs (E1/E2/E3) are resolved later, for the whole candidate
    set at once — see :func:`_husk_flagged_rows`.
    """
    if info.get("status") in terminal_statuses:
        return None
    if info.get("last_activity_at") is not None:
        return None
    age = _seconds_since(info.get("created_at"), now=now)
    if age is None or age <= threshold:
        return None
    if info.get("children"):
        return None
    if _question_pack_of(manager, info.get("instance_id") or "") is not None:
        return None
    if _pause_marker_of(info.get("metadata")) is not None:
        return None
    return age


def _husk_flagged_rows(
    manager: Any,
    candidates: list[tuple[dict[str, Any], int]],
    *,
    threshold: int,
) -> list[dict[str, Any]]:
    """Resolve the BATCHED evidence legs over the husk candidates.

    The candidate list is the output of the row-local filter, run
    per-window while the fleet streamed past. Two batched round trips
    settle it, independent of the candidate count:

    1. ``repo.filter_without_any_evidence_ids`` — the SINGLE-HOMED
       evidence expression (E1 no task rows, E2 no message rows). The
       detector NEVER calls ``filter_never_dispatched_ids``: that
       method's ``idle ∧ version == 1`` conjuncts are a STATE
       signature, not an evidence signature, and would silently
       false-negative a ``running``/``version>1`` evidence-free row
       (D-1 amendment, arch §1.1-§1.2).
    2. the grouped JobItem probe (E3) over the surviving ids.

    Either leg being UNAVAILABLE (a probe failure) suppresses the
    verdict for that run: a husk report must never rest on a leg the
    scanner could not actually evaluate.
    """
    if not candidates:
        return []
    repo = getattr(manager, "_instance_repository", None)
    if repo is None:
        return []

    candidate_ids = [
        (info.get("instance_id") or "") for info, _age in candidates
    ]
    try:
        evidence_free = repo.filter_without_any_evidence_ids(candidate_ids)
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: husk evidence probe failed: %s", exc)
        return []

    surviving = [iid for iid in candidate_ids if iid in evidence_free]
    job_counts = _job_item_counts_by_instance(manager, surviving)
    if job_counts is None:
        return []

    flagged: list[dict[str, Any]] = []
    for info, age in candidates:
        instance_id = info.get("instance_id") or ""
        if instance_id not in evidence_free:
            continue
        if job_counts.get(instance_id, 0):
            continue
        flagged.append(
            _flagged_row(
                info,
                idle_seconds=age,
                detail=(
                    "never dispatched: no activity ever (last_activity_at is "
                    "null), no task, no job item, no messages, no children, "
                    "no question pack, no pause marker; "
                    f"age {age}s (threshold {threshold}s)"
                ),
            )
        )
    return flagged


def _build_health_report(
    manager: Any, threshold_seconds: int
) -> tuple[list[dict[str, Any]], bool, str]:
    """Build the detector rows, the scan-truncated flag and the stop reason.

    NEVER repairs anything.

    THE SCAN IS AN OFFSET WINDOW LOOP (F4b)
    ---------------------------------------
    The old shape read ONE window (``limit=FLEET_SCAN_LIMIT``) of the
    NEWEST rows and threw the rest away: the OLDEST rows — the day-scale
    never-dispatched husks — were invisible, and the blind spot was
    reported as a constant ``False``. The walk now covers the whole
    fleet, window by window, and says WHY it stopped.

    Invariants (each one is pinned):

    * ``total`` is captured from the FIRST window ONLY and is never
      reassigned. A concurrent INSERT at the front of the
      ``created_at DESC`` order shifts every later row down, so a
      recomputed total would make the walk stop early and silently skip
      the tail — the exact rows the husks live in.
    * The walk breaks ONLY on an EMPTY window (end of set) or a cap
      (window count / wall-clock). It NEVER breaks on
      ``len(read) >= total``.
    * Rows are consumed PER WINDOW: only the FLAGGED rows, a compact
      ``instance_id -> status`` map and the job-active rows survive the
      loop. The whole fleet is never materialized.
    * ``scan_stop_reason`` says why the walk stopped —
      ``end_of_set`` / ``window_cap`` / ``time_budget``.

    Returns:
        ``(detectors, scan_truncated, scan_stop_reason)``.
    """
    from ..constants import ALIVE_INSTANCE_STATUSES, TERMINAL_INSTANCE_STATUSES

    now = datetime.now(timezone.utc)
    deadline = time.monotonic() + FLEET_SCAN_TIME_BUDGET_SECONDS
    repo = getattr(manager, "_instance_repository", None)
    task_repo = getattr(manager, "_task_repo", None)

    # ── the job-active instance ids (detector 4's global query) ──────
    # Taken ONCE, before the walk: the detector needs the set of
    # instances owning an ACTIVE job item — including ids that are NOT
    # in the fleet at all (a deleted instance with a live job row is
    # still a zombie). The rows that ARE in the fleet are picked up
    # during the walk (the detail map below); the ids that are NOT are
    # reported as "missing" — the pre-existing semantics.
    job_active_ids: set[str] = set()
    job_active_rows: dict[str, dict[str, Any]] = {}
    try:
        from sqlalchemy import text

        engine = getattr(manager, "engine", None)
        if engine is not None:
            with engine.connect() as conn:
                active_rows = conn.execute(
                    text(
                        "SELECT DISTINCT instance_id FROM job_queue_items "
                        "WHERE admission_state = 'active' "
                        "AND instance_id IS NOT NULL"
                    )
                ).all()
            job_active_ids = {str(r[0]) for r in active_rows if r[0]}
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: zombie-pending scan failed: %s", exc)

    # ── the per-detector accumulators ───────────────────────────────
    # Only FLAGGED rows are retained — the fleet itself is never kept.
    stale_running: list[dict[str, Any]] = []
    dead_parent: list[dict[str, Any]] = []
    paused_no_task: list[dict[str, Any]] = []
    zombie_pending: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    failed_unreported: list[dict[str, Any]] = []
    # The husk detector's row-local candidates: (row, age-since-created).
    # The id list is the ONLY thing that survives the walk — the batched
    # evidence legs are resolved once, after the loop.
    husk_candidates: list[tuple[dict[str, Any], int]] = []

    # Compact cross-row state: the ONLY per-row field a detector needs
    # from another row is the parent's STATUS.
    status_by_id: dict[str, str] = {}
    # A parent's status is resolved at most ONCE per report.
    parent_status_cache: dict[str, str | None] = {}

    total_first: int | None = None
    reached_end_of_set = False
    stop_reason = "end_of_set"
    window_index = 0
    while window_index < FLEET_SCAN_MAX_WINDOWS:
        if time.monotonic() >= deadline:
            stop_reason = "time_budget"
            break
        rows, total, _repo_truncated = manager.list_instances(
            limit=FLEET_SCAN_LIMIT, offset=window_index * FLEET_SCAN_LIMIT
        )
        if total_first is None:
            # FIRST window only — never reassigned (see the docstring).
            total_first = total
        if not rows:
            reached_end_of_set = True
            stop_reason = "end_of_set"
            break

        # Pass 1 — the window's statuses, so a child can resolve a
        # parent that sits in the SAME window (the common case).
        for info in rows:
            instance_id = info.get("instance_id")
            if instance_id:
                status_by_id[instance_id] = info.get("status")

        # Pass 2 — the detectors, per row, streaming.
        for info in rows:
            instance_id = info.get("instance_id") or ""
            status = info.get("status")

            # ── 1. stale-running ────────────────────────────────────
            # The LINEAGE-MAX aggregate (R4, hard): `last_activity_at`
            # FREEZES on a waiting_children parent while a descendant
            # streams mid-LLM, so a per-row check false-positives on
            # every live parent.
            if status == "running":
                activity = _lineage_max_activity(manager, instance_id)
                idle = _seconds_since(
                    activity if activity else info.get("last_activity_at"), now=now
                )
                if idle is not None and idle > threshold_seconds:
                    stale_running.append(
                        _flagged_row(
                            info,
                            idle_seconds=idle,
                            detail=(
                                f"no activity in the lineage for {idle}s "
                                f"(threshold {threshold_seconds}s)"
                            ),
                        )
                    )

            # ── 2. waiting-on-a-dead-parent ──────────────────────────
            if status == "waiting_children":
                parent_id = info.get("parent_id") or None
                if parent_id:
                    parent_status = _parent_status_of(
                        repo, status_by_id, parent_id, parent_status_cache
                    )
                    if parent_status not in ALIVE_INSTANCE_STATUSES:
                        idle = _seconds_since(info.get("last_activity_at"), now=now)
                        if idle is None or idle > threshold_seconds:
                            dead_parent.append(
                                _flagged_row(
                                    info,
                                    idle_seconds=idle,
                                    detail=(
                                        f"waiting on children; parent {parent_id} is "
                                        f"{parent_status or 'missing'}"
                                    ),
                                )
                            )

            # ── 3. question-paused-without-task-row ──────────────────
            if status == "paused":
                idle = _seconds_since(info.get("last_activity_at"), now=now)
                if idle is not None and idle > threshold_seconds:
                    if _question_pack_of(manager, instance_id) is None:
                        if not _has_live_task(task_repo, instance_id):
                            marker = _pause_marker_of(info.get("metadata"))
                            deliberate = bool(marker) and marker.get("deliberate") is True
                            paused_no_task.append(
                                _flagged_row(
                                    info,
                                    idle_seconds=idle,
                                    detail=(
                                        "paused, aged, no live turn and no pending question"
                                        + (
                                            " (DELIBERATE PARK — not a wedge)"
                                            if deliberate
                                            else ""
                                        )
                                    ),
                                )
                            )

            # ── 4. zombie-pending (the row half) ─────────────────────
            # The OTHER half — instances with an active job row that
            # are NOT in the fleet at all — is settled after the walk.
            if instance_id and instance_id in job_active_ids:
                job_active_rows[instance_id] = info

            # ── 5. orphans ───────────────────────────────────────────
            if status != "waiting_children" and status not in TERMINAL_INSTANCE_STATUSES:
                parent_id = info.get("parent_id") or None
                if parent_id:
                    parent_status = _parent_status_of(
                        repo, status_by_id, parent_id, parent_status_cache
                    )
                    if parent_status not in ALIVE_INSTANCE_STATUSES:
                        orphans.append(
                            _flagged_row(
                                info,
                                idle_seconds=_seconds_since(
                                    info.get("last_activity_at"), now=now
                                ),
                                detail=(
                                    f"parent {parent_id} is {parent_status or 'missing'}; "
                                    "this instance is still live"
                                ),
                            )
                        )

            # ── 6. failed-with-unreported-result ─────────────────────
            if status in ("failed", "error"):
                children = info.get("children") or []
                if children:
                    failed_unreported.append(
                        _flagged_row(
                            info,
                            idle_seconds=_seconds_since(
                                info.get("last_activity_at"), now=now
                            ),
                            detail=(
                                f"failed with {len(children)} descendant(s); the child's "
                                "report may never have been delivered"
                            ),
                        )
                    )

            # ── 7. never-dispatched-husk (the ROW-LOCAL legs) ────────
            # Only the cheap legs run here (F3, D-1): non-terminal,
            # never active, aged, no children, no pending question, no
            # pause marker. The BATCHED evidence legs (E1/E2/E3) are
            # settled once, after the walk, over the whole candidate
            # set — two round trips for the whole report, independent
            # of the fleet size.
            candidate_age = _husk_row_local_candidate(
                manager,
                info,
                now=now,
                terminal_statuses=TERMINAL_INSTANCE_STATUSES,
                threshold=DEFAULT_HUSK_MIN_AGE_SECONDS,
            )
            if candidate_age is not None:
                husk_candidates.append((info, candidate_age))

        window_index += 1
    else:
        # The window cap fired without ever seeing an empty window.
        stop_reason = "window_cap"

    # ── 4. zombie-pending (the missing-instance half) ───────────────
    for instance_id in sorted(job_active_ids):
        info = job_active_rows.get(instance_id)
        status = info.get("status") if info else None
        if status in ALIVE_INSTANCE_STATUSES:
            continue
        if _has_live_task(task_repo, instance_id):
            continue
        zombie_pending.append(
            _flagged_row(
                info
                or {
                    "instance_id": instance_id,
                    "agent_id": None,
                    "status": status,
                    "metadata": {},
                },
                idle_seconds=None,
                detail=(
                    "job item is ACTIVE but the instance is "
                    f"{status or 'missing'} and owns no task row"
                ),
            )
        )

    detectors: list[dict[str, Any]] = [
        {
            "detector": "stale-running",
            "instances": stale_running,
            "count": len(stale_running),
            # NO observable classify-only counterpart on the stale-task
            # recovery sweep — an honest gap, declared.
            "disagreement_pin": None,
        },
        {
            "detector": "waiting-on-a-dead-parent",
            "instances": dead_parent,
            "count": len(dead_parent),
            # OBSERVABLE counterpart — the disagreement test runs two
            # independent code paths over one labelled corpus.
            "disagreement_pin": "WaitingChildrenWatchdog.wedge_episodes()",
        },
        {
            "detector": "question-paused-without-task-row",
            "instances": paused_no_task,
            "count": len(paused_no_task),
            # The sweep's predicate is DOCUMENTED in its docstring but its
            # selectors are private — the pin is fixture-labelled, which
            # is materially weaker than an executable counterpart.
            "disagreement_pin": "stale_pause_sweep (documented classification)",
        },
        {
            "detector": "zombie-pending",
            "instances": zombie_pending,
            "count": len(zombie_pending),
            # The one public method is a RECONCILER (mutating); the
            # predicate is documented — the pin is fixture-labelled.
            "disagreement_pin": (
                "job_recovery_service.reconcile_drift_states "
                "(documented classification)"
            ),
        },
        {
            "detector": "orphans",
            "instances": orphans,
            "count": len(orphans),
            # OBSERVABLE counterpart.
            "disagreement_pin": "OrphanWatcherSweepService.counters()",
        },
        {
            "detector": "failed-with-unreported-result",
            "instances": failed_unreported,
            "count": len(failed_unreported),
            # NO observable classify-only counterpart — an honest gap.
            "disagreement_pin": None,
        },
    ]

    # ── 7. never-dispatched-husk (the BATCHED evidence legs) ────────
    # Appended LAST: a pure addition, so the six existing detector
    # indices stay stable for any consumer that reads them positionally.
    husk_flagged = _husk_flagged_rows(
        manager, husk_candidates, threshold=DEFAULT_HUSK_MIN_AGE_SECONDS
    )
    detectors.append(
        {
            "detector": "never-dispatched-husk",
            "instances": husk_flagged,
            "count": len(husk_flagged),
            # D-1 amendment (arch §1.2 item 6): the containment holds on
            # the EVIDENCE conjuncts only. The canonical twins carry a
            # STATE signature (idle ∧ version==1) on top of the same
            # evidence, so "husk ⊆ canonical ghost set" would be FALSE —
            # a paused evidence-free row is a husk and not a canonical
            # ghost.
            "disagreement_pin": (
                "ChildReportsService._ghost_child_filter / "
                "InstanceRepository.filter_never_dispatched_ids "
                "(the never-dispatched EVIDENCE signature — zero "
                "message_queue rows, zero task rows, via the shared "
                "expression never_dispatched_evidence_expr). "
                "EVIDENCE-CONJUNCT CONTAINMENT ONLY: every husk-flagged "
                "row satisfies no-message-rows AND no-task-rows (via "
                "that shared expression) AND non-terminal AND "
                "last_activity_at IS NULL AND age > "
                "DEFAULT_HUSK_MIN_AGE_SECONDS. The husk set is NOT a "
                "subset of the canonical ghost set — that set adds the "
                "idle ∧ version==1 state conjuncts, which are not "
                "evidence."
            ),
        }
    )

    # F4b: the flag is TRUE when the walk did NOT cover the fleet —
    # either fewer distinct rows than the count promised, or the walk
    # stopped on a cap without ever seeing the end of the set.
    distinct_read = len(status_by_id)
    scan_truncated = bool(
        (total_first is not None and distinct_read < total_first)
        or (not reached_end_of_set)
    )

    return detectors, scan_truncated, stop_reason


def _build_capacity_summary(manager: Any) -> dict[str, Any]:
    """Build the capacity payload — counts, queue depth, pool, DLQ.

    The census half is ONE grouped query over the WHOLE fleet (F2): the
    old shape counted a bounded window in Python, so its
    ``counts_by_status`` summed to the window size while
    ``instance_list.total_matching`` reported the real fleet — two true
    numbers that could not reconcile. See
    ``SQLModelInstanceRepository.count_fleet_by_status_and_agent`` for
    the query shape and the KB-exclusion contract.
    """
    repo = getattr(manager, "_instance_repository", None)
    if repo is None or not hasattr(repo, "count_fleet_by_status_and_agent"):
        raise RuntimeError(
            "fleet census unavailable: the instance repository does not "
            "expose count_fleet_by_status_and_agent"
        )
    census = repo.count_fleet_by_status_and_agent(exclude_kb=True)

    counts_by_agent: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    for status, agent_id, count in census.groups:
        agent_key = agent_id or "unknown"
        status_key = status or "unknown"
        counts_by_agent[agent_key] = counts_by_agent.get(agent_key, 0) + count
        counts_by_status[status_key] = counts_by_status.get(status_key, 0) + count

    # Liveness is a pure function of status — the canonical map, no
    # fork, no extra query. NOTE (F2): `idle` is a STATUS bucket only.
    # `_STATUS_CANONICAL_MAP` maps idle → "processing", so
    # counts_by_liveness deliberately has NO `idle` key.
    counts_by_liveness: dict[str, int] = {}
    for status_key, count in counts_by_status.items():
        liveness = _liveness_for(status_key)
        counts_by_liveness[liveness] = counts_by_liveness.get(liveness, 0) + count

    total_rows = int(census.total)
    counted_rows = sum(counts_by_status.values())
    reconciled = counted_rows == total_rows

    # Queue depth — one grouped query across queues and admission states.
    queue_depth: list[dict[str, Any]] = []
    try:
        from sqlalchemy import text

        engine = getattr(manager, "engine", None)
        if engine is not None:
            with engine.connect() as conn:
                qrows = conn.execute(
                    text(
                        "SELECT COALESCE(q.queue_name, 'default') AS qname, "
                        "SUM(CASE WHEN i.admission_state = 'queued' THEN 1 "
                        "ELSE 0 END) AS pending, "
                        "SUM(CASE WHEN i.admission_state = 'active' THEN 1 "
                        "ELSE 0 END) AS active, "
                        "SUM(CASE WHEN i.admission_state = 'dead' THEN 1 "
                        "ELSE 0 END) AS dead "
                        "FROM job_queue_items i "
                        "LEFT JOIN job_queues q ON q.queue_id = i.queue_id "
                        "GROUP BY COALESCE(q.queue_name, 'default') "
                        "ORDER BY COALESCE(q.queue_name, 'default')"
                    )
                ).all()
            queue_depth = [
                {
                    "queue_name": str(r[0]),
                    "pending": int(r[1] or 0),
                    "active": int(r[2] or 0),
                    "dead": int(r[3] or 0),
                }
                for r in qrows
            ]
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: queue-depth read failed: %s", exc)

    # Worker pool — size / busy / idle / saturated.
    worker_pool: dict[str, Any] = {
        "size": 0,
        "busy": 0,
        "idle": 0,
        "saturated": False,
    }
    pool = getattr(manager, "_worker_pool", None)
    if pool is not None:
        try:
            stats = pool.get_stats()
            size = int(stats.get("num_workers") or 0)
            busy = 0
            for worker in getattr(pool, "_workers", []) or []:
                if getattr(worker, "_current_task_id", None) is not None:
                    busy += 1
            worker_pool = {
                "size": size,
                "busy": busy,
                "idle": max(0, size - busy),
                "saturated": bool(size) and busy >= size,
            }
        except Exception as exc:  # noqa: BLE001 — read-only probe
            logger.debug("instance_read: worker-pool read failed: %s", exc)

    # DLQ depth.
    dlq_depth = 0
    try:
        from ..services.dead_letter_service import get_dead_letter_service

        dlq_depth = int(get_dead_letter_service().count_dlq())
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: dlq depth read failed: %s", exc)

    # F5: the CANONICAL dead-job measure, derived from data already in
    # hand — zero extra queries. `dlq_depth` and the per-queue `dead`
    # column are TWO TRUE FACTS ABOUT TWO DIFFERENT TABLES, not a
    # contradiction: `dlq_depth` counts the legacy `dead_letter_items`
    # SNAPSHOT rows (DLQ-service writes only, retention-pruned) while
    # `dead` counts `job_queue_items` in the terminal `dead` admission
    # state (canonical — a dead job can exist with no snapshot row, the
    # drift-recovery path finalizes to `dead` directly). The payload
    # carries both plus a one-line note so an operator reading raw JSON
    # does not have to consult the docstring.
    dead_jobs_total = sum(int(row.get("dead") or 0) for row in queue_depth)

    # The Tier-2 reconciliation surface (W4).
    audit_pending = {"count": 0, "oldest_age_seconds": None}
    try:
        from ..repositories.instance_ops.repository import InstanceOpsAuditRepository

        engine = getattr(manager, "engine", None)
        if engine is not None:
            audit_repo = InstanceOpsAuditRepository(engine)
            audit_pending = {
                "count": audit_repo.count_pending(),
                "oldest_age_seconds": audit_repo.oldest_pending_age_seconds(),
            }
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: audit_pending read failed: %s", exc)

    return {
        "counts_by_agent": counts_by_agent,
        "counts_by_status": counts_by_status,
        "counts_by_liveness": counts_by_liveness,
        "queue_depth": queue_depth,
        "worker_pool": worker_pool,
        "dlq_depth": dlq_depth,
        "dead_jobs_total": dead_jobs_total,
        "dlq_note": (
            "dlq_depth counts dead_letter_items snapshot rows "
            "(DLQ-service writes only, retention-pruned); "
            "dead_jobs_total counts job_queue_items in the 'dead' "
            "admission state (canonical). They measure different tables."
        ),
        "audit_pending": audit_pending,
        # F2: the reconciliation block. `counted_rows` and `total_rows`
        # are two INDEPENDENT computations of the same number over the
        # same WHERE; `reconciled` is their explicit agreement flag.
        "totals": {
            "total_rows": total_rows,
            "counted_rows": counted_rows,
            # The census no longer reads a bounded window, so this key
            # is False BY CONSTRUCTION. It stays for shape stability —
            # consumers (and the _full_doc_ contract) read it.
            "scan_truncated": False,
            "reconciled": reconciled,
            "note": (
                "counts_by_status is computed over the FULL fleet with one "
                "GROUP BY query (no scan window). total_rows is the COUNT(*) "
                "over the same WHERE (KB agents excluded) — the same number "
                "instance_list reports as total_matching for an UNFILTERED "
                "call. counted_rows is the sum of counts_by_status; "
                "reconciled = (counted_rows == total_rows). A FILTERED "
                "instance_list page is a different population: with a "
                "status filter its total_matching counts only the matching "
                "rows, so do not compare it to these totals. reconciled="
                "false can transiently reflect a write landing between the "
                "two statements of this scan."
            ),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # F2: the census no longer scans a bounded window — the flag is
        # False by construction, never a copied constant.
        "scan_truncated": False,
    }
