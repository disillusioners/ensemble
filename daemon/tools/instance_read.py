"""Instance READ tools — the ``instance_read`` privileged category (4 tools).

The four tools answer the questions the maintenancer could not answer
before this category existed: *what is in the fleet*, *what is this one
instance doing*, *what looks wedged*, and *how much capacity is left*.

Four tools
----------
``instance_list``            — fleet census (cheap rows, SQL-side filters)
``instance_get``             — deep detail for ONE instance (hydration)
``instance_health``          — wedge report (six reporting predicates)
``fleet_capacity_summary``   — "how's the nest?" (counts, queue, pool, DLQ)

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
- ``instance_health``         — wedge report (six detector classes)
- ``fleet_capacity_summary``  — counts, queue depth, worker pool, DLQ

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
#: The default staleness threshold, in seconds (30 minutes — the same
#: default as ``instance_health``).
DEFAULT_STALE_SECONDS = 1800
#: The default ``instance_health`` threshold, in minutes.
DEFAULT_STALE_MINUTES = 30
#: The maximum number of checkpoint messages ``instance_get`` returns.
MAX_LAST_MESSAGES = 20

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
    "instance_ops",
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

    Raises:
        ValueError: when the id is empty, unknown, or ambiguous — the
            caller maps it to the ``instance-not-found`` refusal.
    """
    from .instance import _resolve_instance_id

    return await _resolve_instance_id(manager, instance_id)


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
    """Build the four ``instance_read`` tools for one instance/agent.

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
        A list of the four ``@tool``-decorated callables.
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
            rows, total, truncated = await asyncio.to_thread(
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

        payload = {
            "rows": census,
            "count": len(census),
            "total_matching": total,
            "truncated": bool(truncated),
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
    NONE. Read-only; no audit row.

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

        try:
            instance_id = await _resolve_target(manager, id)
        except ValueError as exc:
            return _refusal("INSTANCE_GET", "instance-not-found", str(exc))
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_get resolve failed")
            return _error(f"instance_get failed: {type(exc).__name__}")

        repo = getattr(manager, "_instance_repository", None)
        if repo is None:
            return _error("instance_get: instance repository unavailable")

        try:
            row = await asyncio.to_thread(repo.get, instance_id)
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_get row read failed")
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
            cursor = parent_row.parent_id
        ancestors.reverse()  # root -> parent

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

        # Mission reference — a projection when the resolver exposes one.
        mission_ref = None
        resolver = getattr(manager, "_mission_resolver", None)
        if resolver is not None:
            for attr in ("get_mission_for_instance", "mission_for_instance"):
                fn = getattr(resolver, attr, None)
                if fn is None:
                    continue
                try:
                    mission = fn(instance_id)
                    if mission is not None:
                        mission_ref = {
                            "mission_id": getattr(mission, "mission_id", None)
                            or getattr(mission, "id", None),
                            "liveness": getattr(mission, "liveness", None),
                        }
                    break
                except Exception:  # noqa: BLE001 — read-only probe
                    break

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
                "agent_id": info.get("agent_id"),
            },
            "mission_ref": mission_ref,
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
    `spawned_by`, `mission_ref`.

    `pending_question_pack` is `null` after a daemon restart — question
    packs live in memory only, and the honest rendering of that is a
    null, never a fabricated pack.

Side effects:
    NONE. Read-only; no audit row.

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
        """Wedge report — six read-only detectors. Use tool_help("instance_health") for details."""
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
            report = await asyncio.to_thread(
                _build_health_report, manager, threshold_seconds
            )
        except Exception as exc:  # noqa: BLE001 — tool boundary swallows all
            logger.exception("instance_health failed")
            return _error(f"instance_health failed: {type(exc).__name__}")

        payload = {
            "stale_minutes": stale_minutes,
            "detectors": report,
            "total_flagged": sum(d["count"] for d in report),
        }
        return json.dumps(payload, default=str)

    instance_health._full_doc_ = """Wedge report — six read-only detector classes.

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

    The six classes: `stale-running`, `waiting-on-a-dead-parent`,
    `question-paused-without-task-row`, `zombie-pending`, `orphans`,
    `failed-with-unreported-result`.

    IMPORTANT — a deliberately parked instance is NOT wedged. The
    `pause_marker` on a flagged row tells you which one you are looking
    at. The `stale-running` detector aggregates activity across the
    instance's LINEAGE, so a live parent waiting on a streaming child is
    never flagged.

Side effects:
    NONE. A health report never triggers a repair.

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

Args:
    (none)

Returns:
    One JSON string: `counts_by_agent`, `counts_by_status`,
    `counts_by_liveness`, `queue_depth` (one row per queue with
    `queue_name`, `pending`, `active`, `dead`), `worker_pool` (`size`,
    `busy`, `idle`, `saturated`), `dlq_depth`, `audit_pending`
    (`count` + `oldest_age_seconds`), `generated_at`.

    `audit_pending` is the reconciliation surface: a row stuck in
    `pending` means an ACT's intent was recorded but its outcome was
    never settled (a crash mid-act). When that happens, read the TRUE
    outcome from the INSTANCE STATE, not from the ledger — the row
    records the intent, the instance records the fact.

Side effects:
    NONE. Read-only.

Examples:
    fleet_capacity_summary()
"""

    return [
        instance_list,
        instance_get,
        instance_health,
        fleet_capacity_summary,
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
    repo: Any, by_id: dict[str, dict[str, Any]], parent_id: str | None
) -> str | None:
    """The parent's status, or ``None`` when the parent row is gone."""
    if not parent_id:
        return None
    parent = by_id.get(parent_id)
    if parent is not None:
        return parent.get("status")
    try:
        prow = repo.get(parent_id)
    except Exception:  # noqa: BLE001 — read-only probe
        return None
    return getattr(prow, "status", None) if prow is not None else None


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


def _build_health_report(manager: Any, threshold_seconds: int) -> list[dict[str, Any]]:
    """Build the six detector rows. NEVER repairs anything."""
    from ..constants import ALIVE_INSTANCE_STATUSES, TERMINAL_INSTANCE_STATUSES

    now = datetime.now(timezone.utc)
    rows, _total, _truncated = manager.list_instances(limit=1000, offset=0)
    by_id = {r.get("instance_id"): r for r in rows if r.get("instance_id")}
    repo = getattr(manager, "_instance_repository", None)
    task_repo = getattr(manager, "_task_repo", None)
    detectors: list[dict[str, Any]] = []

    # ── 1. stale-running ────────────────────────────────────────────
    # The LINEAGE-MAX aggregate (R4, hard): `last_activity_at` FREEZES
    # on a waiting_children parent while a descendant streams mid-LLM,
    # so a per-row check false-positives on every live parent.
    flagged: list[dict[str, Any]] = []
    for info in rows:
        if info.get("status") != "running":
            continue
        instance_id = info.get("instance_id") or ""
        activity = _lineage_max_activity(manager, instance_id)
        idle = _seconds_since(
            activity if activity else info.get("last_activity_at"), now=now
        )
        if idle is not None and idle > threshold_seconds:
            flagged.append(
                _flagged_row(
                    info,
                    idle_seconds=idle,
                    detail=(
                        f"no activity in the lineage for {idle}s "
                        f"(threshold {threshold_seconds}s)"
                    ),
                )
            )
    detectors.append(
        {
            "detector": "stale-running",
            "instances": flagged,
            "count": len(flagged),
            # NO observable classify-only counterpart on the stale-task
            # recovery sweep — an honest gap, declared.
            "disagreement_pin": None,
        }
    )

    # ── 2. waiting-on-a-dead-parent ────────────────────────────────
    flagged = []
    for info in rows:
        if info.get("status") != "waiting_children":
            continue
        parent_id = info.get("parent_id") or None
        if not parent_id:
            continue
        parent_status = _parent_status_of(repo, by_id, parent_id)
        if parent_status in ALIVE_INSTANCE_STATUSES:
            continue
        idle = _seconds_since(info.get("last_activity_at"), now=now)
        if idle is not None and idle <= threshold_seconds:
            continue
        flagged.append(
            _flagged_row(
                info,
                idle_seconds=idle,
                detail=(
                    f"waiting on children; parent {parent_id} is "
                    f"{parent_status or 'missing'}"
                ),
            )
        )
    detectors.append(
        {
            "detector": "waiting-on-a-dead-parent",
            "instances": flagged,
            "count": len(flagged),
            # OBSERVABLE counterpart — the disagreement test runs two
            # independent code paths over one labelled corpus.
            "disagreement_pin": "WaitingChildrenWatchdog.wedge_episodes()",
        }
    )

    # ── 3. question-paused-without-task-row ────────────────────────
    flagged = []
    for info in rows:
        if info.get("status") != "paused":
            continue
        instance_id = info.get("instance_id") or ""
        idle = _seconds_since(info.get("last_activity_at"), now=now)
        if idle is None or idle <= threshold_seconds:
            continue
        if _question_pack_of(manager, instance_id) is not None:
            continue
        if _has_live_task(task_repo, instance_id):
            continue
        marker = _pause_marker_of(info.get("metadata"))
        deliberate = bool(marker) and marker.get("deliberate") is True
        flagged.append(
            _flagged_row(
                info,
                idle_seconds=idle,
                detail=(
                    "paused, aged, no live turn and no pending question"
                    + (" (DELIBERATE PARK — not a wedge)" if deliberate else "")
                ),
            )
        )
    detectors.append(
        {
            "detector": "question-paused-without-task-row",
            "instances": flagged,
            "count": len(flagged),
            # The sweep's predicate is DOCUMENTED in its docstring but its
            # selectors are private — the pin is fixture-labelled, which
            # is materially weaker than an executable counterpart.
            "disagreement_pin": "stale_pause_sweep (documented classification)",
        }
    )

    # ── 4. zombie-pending ──────────────────────────────────────────
    # admission_state='active', no Task row, instance not alive.
    flagged = []
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
            for (instance_id,) in active_rows:
                info = by_id.get(instance_id)
                status = info.get("status") if info else None
                if status in ALIVE_INSTANCE_STATUSES:
                    continue
                if _has_live_task(task_repo, instance_id):
                    continue
                flagged.append(
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
    except Exception as exc:  # noqa: BLE001 — read-only probe
        logger.debug("instance_read: zombie-pending scan failed: %s", exc)
    detectors.append(
        {
            "detector": "zombie-pending",
            "instances": flagged,
            "count": len(flagged),
            # The one public method is a RECONCILER (mutating); the
            # predicate is documented — the pin is fixture-labelled.
            "disagreement_pin": (
                "job_recovery_service.reconcile_drift_states "
                "(documented classification)"
            ),
        }
    )

    # ── 5. orphans ─────────────────────────────────────────────────
    # A non-terminal instance whose parent is gone or terminal — the
    # shape the orphan sweep's selectors act on.
    flagged = []
    for info in rows:
        if info.get("status") == "waiting_children":
            continue  # detector 2 owns this class
        if info.get("status") in TERMINAL_INSTANCE_STATUSES:
            continue
        parent_id = info.get("parent_id") or None
        if not parent_id:
            continue
        parent_status = _parent_status_of(repo, by_id, parent_id)
        if parent_status in ALIVE_INSTANCE_STATUSES:
            continue
        flagged.append(
            _flagged_row(
                info,
                idle_seconds=_seconds_since(info.get("last_activity_at"), now=now),
                detail=(
                    f"parent {parent_id} is {parent_status or 'missing'}; "
                    "this instance is still live"
                ),
            )
        )
    detectors.append(
        {
            "detector": "orphans",
            "instances": flagged,
            "count": len(flagged),
            # OBSERVABLE counterpart.
            "disagreement_pin": "OrphanWatcherSweepService.counters()",
        }
    )

    # ── 6. failed-with-unreported-result ───────────────────────────
    flagged = []
    for info in rows:
        if info.get("status") not in ("failed", "error"):
            continue
        children = info.get("children") or []
        if not children:
            continue
        idle = _seconds_since(info.get("last_activity_at"), now=now)
        flagged.append(
            _flagged_row(
                info,
                idle_seconds=idle,
                detail=(
                    f"failed with {len(children)} descendant(s); the child's "
                    "report may never have been delivered"
                ),
            )
        )
    detectors.append(
        {
            "detector": "failed-with-unreported-result",
            "instances": flagged,
            "count": len(flagged),
            # NO observable classify-only counterpart — an honest gap.
            "disagreement_pin": None,
        }
    )

    return detectors


def _build_capacity_summary(manager: Any) -> dict[str, Any]:
    """Build the capacity payload — counts, queue depth, pool, DLQ."""
    rows, _total, _truncated = manager.list_instances(limit=1000, offset=0)

    counts_by_agent: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    counts_by_liveness: dict[str, int] = {}
    for info in rows:
        agent = info.get("agent_id") or "unknown"
        status = info.get("status") or "unknown"
        liveness = _liveness_for(status)
        counts_by_agent[agent] = counts_by_agent.get(agent, 0) + 1
        counts_by_status[status] = counts_by_status.get(status, 0) + 1
        counts_by_liveness[liveness] = counts_by_liveness.get(liveness, 0) + 1

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
        "audit_pending": audit_pending,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
