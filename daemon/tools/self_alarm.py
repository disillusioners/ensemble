"""Agent-facing self-alarm tools — the ``self-alarm`` category (1 tool).

Thin closure layer over the EXISTING scheduling engine
(``daemon.services.scheduling_service``) plus the PURE helpers in
``daemon.services.self_alarm_service``. The engine is consumed
VERBATIM — this phase changes no engine file; a perceived engine defect
is a REPORTED deviation, never an edit.

House rules honored here (ADR-005):

* Tools NEVER raise — every failure returns an ``"ERROR: {msg}"`` string
  (precedent: ``daemon/tools/scheduling.py``).
* An UNEXPECTED exception is logged SERVER-SIDE and answered with a
  generic envelope (the PB-F1 leak class: ``str(exc)`` / ``traceback`` /
  ``repr(exc)`` never reach the payload). Engine-``ValueError`` messages
  are authored by the engine and ARE surfaced.
* The tool returns a DICT on success — never a bare string.

Access posture — THREE layers, ALL required
-------------------------------------------
1. The ``self-alarm`` category is PRIVILEGED (entry #10 of
   ``PRIVILEGED_TOOL_CATEGORIES``), so it is opt-in-only and never joins
   the default-grant universe (the empty-allow inherit universe — e.g.
   ``watcher`` — must NOT be able to arm alarms).
2. ``agents/ari/meta.json`` carries the explicit ``"self-alarm"`` allow
   entry — and ONLY ari (the leader ruling: ari-for-ari).
3. The tool body rejects any caller whose ``agent_id != "ari"`` — a
   LITERAL tool name in an allow list bypasses the category gate, so the
   category alone cannot spell "ari only".

The tool is the policy layer the raw ``task_schedule`` tool is not: it
enforces the >= 30-minute horizon, namespaces the label, resolves the
recurrence-family compatibility before any mutation, re-arms a fired
alarm safely, and composes a self-contained message for the fresh
instance that receives the alarm.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone as _stdlib_timezone
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..services.self_alarm_service import (
    FAMILY_CRON,
    FAMILY_ONCE,
    FAMILY_WEEKLY,
    LABEL_NAMESPACE,
    SelfAlarmInputError,
    candidate_labels,
    compose_alarm_message,
    effective_label_for,
    enforce_min_horizon,
    parse_when_or_every,
    resolve_fire_plan,
    summarize_message,
    validate_board_id,
    validate_label_input,
)
from ._tool_registry import register_tool_category

logger = logging.getLogger(__name__)

CATEGORY_NAME = "Self-Alarm"
CATEGORY_DOC = """\
Arm a reminder to yourself, at least 30 minutes out, delivered into a
fresh Ari instance at the scheduled instant.

Tool in this category:
- ``self_alarm`` — arm (or re-arm) one self-directed alarm

What the alarm IS: a durable schedule that fires once (or on a daily /
weekly / cron recurrence) and hands a self-contained message to a FRESH
Ari instance. That instance has NO memory of the request — everything
it needs is in the message, so the message must name the action
explicitly.

What the alarm is NOT: a notification. The scheduler source is one-way
and the receiving instance has no chat identity, so there is NO chat /
Discord / Slack ping and no interruption. The BOARD is the user-visible
surface: the fired instance acts (updates the board item, dispatches a
job) and the board shows the result.

Minimum horizon: 30 minutes. Anything shorter must be done inside the
current turn with ``bash`` + ``sleep``, not with an alarm.
"""

#: The only agent allowed to arm a self-alarm (the runtime gate — layer 3
#: of the three-layer posture).
SELF_ALARM_AUTHORIZED_AGENT = "ari"

#: The engine's invoked agent for EVERY create (D4 step 4a): the alarm
#: fires INTO ari — the receiving instance is a scheduler-owned ari
#: instance. The engine REQUIRES ``payload.agent`` and fail-fasts on an
#: unknown agent (``scheduling_service.py:132``, ``:673-676``).
SELF_ALARM_TARGET_AGENT = "ari"

#: The instance mode — a one-shot fires into a BRAND-NEW instance
#: (D6: the alarm is a fresh task, not a continuation).
SELF_ALARM_INSTANCE_MODE = "new_instance"


def _error(msg: str) -> str:
    """House error envelope — tools never raise."""
    return f"ERROR: {msg}"


@dataclass(frozen=True)
class _StoredRow:
    """The read-only view of a stored schedule row (D4 step 1)."""

    source_id: str
    name: str
    status: str | None
    enabled: bool | None
    source_type: str | None
    #: The stored TARGET agent (``config["agent"]``). The engine REQUIRES
    #: ``payload.agent`` at create and never rewrites it on update, so
    #: every legit row carries one — ``None`` (missing) means unknown
    #: provenance and is treated as FOREIGN by the gate (fail-closed).
    agent: str | None
    recurrence: str | None
    cron_expression: str | None
    timezone: str | None
    run_at: str | None


@dataclass(frozen=True)
class _Gate:
    """The D4 step-2 compatibility verdict."""

    mode: str  # "create" | "reuse" | "fresh_label"
    reason: str | None = None
    detail: str | None = None


def _read_stored_row(row: Any) -> _StoredRow | None:
    """Project a repository row onto the read-only view the gate needs."""
    if row is None:
        return None
    config = dict(getattr(row, "config", None) or {})
    return _StoredRow(
        source_id=str(getattr(row, "source_id", "") or ""),
        name=str(getattr(row, "name", "") or ""),
        status=getattr(row, "status", None),
        enabled=getattr(row, "enabled", None),
        source_type=getattr(row, "source_type", None),
        agent=config.get("agent"),
        recurrence=config.get("recurrence"),
        cron_expression=config.get("schedule"),
        timezone=config.get("timezone"),
        run_at=config.get("run_at"),
    )


def _stored_weekday(cron_expression: str | None) -> int | None:
    """Derive the stored day-of-week from the stored cron's 5th field.

    The engine stores NO ``weekday`` key — the stored cron's DOW field is
    authoritative (``scheduling_service.py:627``), so the wrapper derives
    it. ``None`` means "not derivable" — the gate then treats the row as
    incompatible rather than guessing.
    """
    if not cron_expression:
        return None
    parts = str(cron_expression).split()
    if len(parts) < 5:
        return None
    try:
        return int(parts[4])
    except (TypeError, ValueError):
        return None


def _compatibility_gate(parsed: Any, stored: _StoredRow | None) -> _Gate:
    """D4 step 2 — the compatibility gate, BEFORE any mutation.

    An update can move a schedule's TIME but never its SHAPE: the update
    payload has no ``weekday`` / ``recurrence`` / ``cron_expression``
    field (``scheduling_service.py:208-214``), the weekly branch rewrites
    only the minute/hour of the stored cron and PRESERVES the stored DOW
    (``scheduling_service.py:973-982``), and the cron branch RAISES on a
    ``when`` (``scheduling_service.py:983-986``). A shape mismatch
    therefore never reaches the engine: the original row is never
    mutated, so a duplicate-armed state is impossible by construction.

    The stored row's TARGET AGENT is part of the label's ownership: the
    wrapper arms alarms that fire INTO ari (I2-B1), so a row targeting
    another agent — or one whose agent is missing (unknown provenance) —
    is never updated from here (review advisory A1; fail-closed).
    """
    if stored is None:
        return _Gate(mode="create")

    # A non-scheduler row under our namespaced label is a NAME collision
    # (the lookup has no source_type filter) — the engine can neither
    # update nor cancel it, so it burns the name for us.
    if stored.source_type not in (None, "scheduler"):
        return _Gate(
            mode="fresh_label",
            reason="label_collision_foreign_source",
            detail=(
                f"the name is already used by a {stored.source_type!r} source "
                f"({stored.source_id})"
            ),
        )

    # A stored row whose TARGET AGENT is not exactly ari is another
    # agent's alarm squatting our namespaced label — updating it would
    # deliver Ari's message text into a foreign instance tree. MISSING
    # agent (``None``) is unknown provenance and FAILS CLOSED the same
    # way (review advisory A1 — I2-B1: the alarm fires INTO ari on
    # every path).
    if stored.agent != SELF_ALARM_TARGET_AGENT:
        return _Gate(
            mode="fresh_label",
            reason="label_owned_by_other_agent",
            detail=(
                f"the stored schedule targets agent {stored.agent!r}, not "
                f"{SELF_ALARM_TARGET_AGENT!r} — an alarm label fires INTO ari, "
                f"so a foreign (or agent-less) row is never updated from here"
            ),
        )

    if stored.status == "cancelled":
        return _Gate(
            mode="fresh_label",
            reason="label_burned_cancelled",
            detail=f"the existing label was cancelled and can never be reused",
        )

    if stored.recurrence != parsed.family:
        return _Gate(
            mode="fresh_label",
            reason="recurrence_family_mismatch",
            detail=(
                f"the stored schedule is {stored.recurrence!r}, the request is "
                f"{parsed.family!r} — an update can move the time but never the "
                f"recurrence family"
            ),
        )

    if parsed.family == FAMILY_WEEKLY:
        stored_dow = _stored_weekday(stored.cron_expression)
        if stored_dow is None or stored_dow != parsed.weekday:
            return _Gate(
                mode="fresh_label",
                reason="weekly_dow_mismatch",
                detail=(
                    f"the stored schedule fires on weekday {stored_dow}, the "
                    f"request asks for {parsed.weekday} — an update preserves "
                    f"the stored day-of-week"
                ),
            )

    if parsed.family == FAMILY_CRON:
        stored_expression = (stored.cron_expression or "").strip()
        if stored_expression != (parsed.cron_expression or "").strip():
            return _Gate(
                mode="fresh_label",
                reason="cron_expression_mismatch",
                detail=(
                    f"the stored schedule runs {stored_expression!r}, the "
                    f"request asks for {(parsed.cron_expression or '').strip()!r} "
                    f"— a cron schedule cannot be re-timed by an update"
                ),
            )

    return _Gate(mode="reuse")


def _resolve_project_id(instance_repository: Any, current_instance_id: str) -> str | None:
    """D11 — resolve the caller's project from the instance row.

    ``project_id`` is the ONLY selector for the job-queue fire route:
    without it the fire takes the legacy immediate path — delivered, but
    with no JobItem, no idempotency key, no retry and no visibility
    (``scheduler.py:995-998``). The blueprint auto-inject pattern is the
    precedent (``daemon/tools/blueprint.py:61-66``).
    """
    if instance_repository is None or not current_instance_id:
        return None
    try:
        instance = instance_repository.get(current_instance_id)
    except Exception as exc:  # noqa: BLE001 — a degraded alarm is REFUSED, never armed
        logger.warning(
            "self_alarm: cannot resolve project_id for instance %s: %s",
            current_instance_id,
            exc,
        )
        return None
    if instance is None:
        return None
    project_id = getattr(instance, "project_id", None)
    return str(project_id) if project_id else None


async def _resolve_wrapper_timezone(
    project_repository: Any, explicit: str | None = None
) -> tuple[ZoneInfo, str, str]:
    """Resolve the timezone through the SAME chain the engine uses.

    Chain (ordered): an explicit zone → the user timezone setting (when
    set + valid) → ``SchedulingConfig().default_timezone`` (env) →
    host-local → UTC with a loud warning. The wrapper hands the engine an
    explicit zone, so the engine's own chain is short-circuited — which is
    why the wrapper echoes ITS OWN warning (``tz_warning``), never the
    engine's.

    The parity test pins this resolution against
    ``scheduling_service._resolve_schedule_timezone`` so chain drift is
    caught, not guessed.
    """
    default_tz: str | None = None
    try:
        from daemon.config import SchedulingConfig

        default_tz = SchedulingConfig().default_timezone
    except Exception as exc:  # noqa: BLE001 — bootstrap-safety (mirrors the engine)
        logger.debug("self_alarm: SchedulingConfig unavailable (%s)", exc)
        default_tz = None

    user_tz: str | None = None
    if not explicit and project_repository is not None:
        # The user setting is read ONLY when no explicit zone is supplied —
        # the engine's chain spends the metadata read the same way.
        try:
            from daemon.services.user_timezone_utils import get_user_timezone_preference

            user_tz = await asyncio.to_thread(
                get_user_timezone_preference, project_repository
            )
        except Exception as exc:  # noqa: BLE001 — bootstrap-safety (mirrors the engine)
            logger.debug("self_alarm: user tz preference unavailable (%s)", exc)
            user_tz = None
    if user_tz:
        default_tz = user_tz

    from daemon.util.tz import resolve_timezone

    zone, warning = resolve_timezone(explicit, default=default_tz, for_tool=True)
    zone_name = str(zone) if isinstance(zone, ZoneInfo) else "UTC"
    try:
        effective_zone = ZoneInfo(zone_name)
    except Exception:  # noqa: BLE001 — defensive; ZoneInfo("UTC") always resolves
        effective_zone = ZoneInfo("UTC")
    return effective_zone, zone_name, warning or ""


async def _lookup_by_name(source_repo: Any, name: str) -> Any:
    """The read-only label probe (D4 step 1) — the ``task_schedule_cancel`` seam."""
    if source_repo is None:
        return None
    return await asyncio.to_thread(source_repo.get_source_config_by_name, name)


def _parse_iso(value: str | None) -> datetime | None:
    """Parse a stored ISO instant (the ``run_at``), or ``None``."""
    if not value:
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _same_instant(left: datetime, right: datetime, tolerance_seconds: float = 1.0) -> bool:
    """Are the two instants within the assertion tolerance?"""
    return abs((left - right).total_seconds()) <= tolerance_seconds


def create_self_alarm_tools(
    service: Any,
    *,
    source_repo: Any = None,
    instance_repository: Any = None,
    project_repository: Any = None,
    current_instance_id: str = "",
    agent_id: str = "",
    agent_tag: str | None = None,
) -> list:
    """Build the self-alarm tool for one instance/agent.

    Args:
        service: The ``daemon.services.scheduling_service`` module
            (exposes create_schedule / update_schedule / list_schedules).
            Injected per-instance from ``InstanceManager.scheduling_service``.
        source_repo: Source repository — used for the READ-ONLY label
            probe (D4 step 1) and the candidate collision checks.
        instance_repository: Instance repository — used to resolve the
            caller's ``project_id`` (D11). Injected per-instance from
            ``InstanceManager._instance_repository``.
        project_repository: Project repository — backs the user-timezone
            rung of the timezone chain (D3). Injected per-instance from
            ``InstanceManager._project_repository``.
        current_instance_id: Caller's instance id (audit + project lookup).
        agent_id: Caller's agent id — the runtime ari-only gate reads it.
        agent_tag: Caller's version tag. Currently unused by the tool
            itself; reserved so the factory signature matches the other
            per-instance factories (``create_job_tools`` et al.).

    Returns:
        A list holding the single ``@tool``-decorated callable.
    """
    caller_agent_id = agent_id or ""
    caller_instance_id = current_instance_id or ""

    class SelfAlarmInput(BaseModel):
        """Input schema for self_alarm."""

        label: Annotated[
            str,
            Field(
                description=(
                    "Short human name for the alarm (1-115 chars). Stored as "
                    "'self-alarm:<label>' — do NOT include the prefix."
                )
            ),
        ]
        when_or_every: Annotated[
            str,
            Field(
                description=(
                    "When the alarm fires: an absolute ISO-8601 instant "
                    "('2026-10-20T09:00:00+07:00'), a relative duration "
                    "('45m', 'in 2h', '1d30m'), 'daily 06:30', "
                    "'weekly 1 06:30' (0=Sunday..6=Saturday) or a raw cron "
                    "expression ('cron 0 9 * * *'). At least 30 minutes out."
                )
            ),
        ]
        message_to_self: Annotated[
            str,
            Field(
                description=(
                    "What the fresh instance should do when the alarm fires. "
                    "Appended verbatim as the message's Action line."
                )
            ),
        ]
        link_board_item: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional board item id ('wi-1a2b3c4d') to link — the fresh "
                    "instance is told to board_get it before acting."
                ),
            ),
        ]

    @register_tool_category("self-alarm")
    @tool(args_schema=SelfAlarmInput)
    async def self_alarm(
        label: Annotated[
            str,
            Field(
                description=(
                    "Short human name for the alarm (1-115 chars). Stored as "
                    "'self-alarm:<label>' — do NOT include the prefix."
                )
            ),
        ],
        when_or_every: Annotated[
            str,
            Field(
                description=(
                    "When the alarm fires: an absolute ISO-8601 instant "
                    "('2026-10-20T09:00:00+07:00'), a relative duration "
                    "('45m', 'in 2h', '1d30m'), 'daily 06:30', "
                    "'weekly 1 06:30' (0=Sunday..6=Saturday) or a raw cron "
                    "expression ('cron 0 9 * * *'). At least 30 minutes out."
                )
            ),
        ],
        message_to_self: Annotated[
            str,
            Field(
                description=(
                    "What the fresh instance should do when the alarm fires. "
                    "Appended verbatim as the message's Action line."
                )
            ),
        ],
        link_board_item: Annotated[
            str | None,
            Field(
                default=None,
                description=(
                    "Optional board item id ('wi-1a2b3c4d') to link — the fresh "
                    "instance is told to board_get it before acting."
                ),
            ),
        ] = None,
    ) -> dict | str:
        """Arm a self-directed alarm (>= 30 minutes out). Use tool_help("self_alarm") for details."""
        try:
            return await _arm_self_alarm(
                service=service,
                source_repo=source_repo,
                instance_repository=instance_repository,
                project_repository=project_repository,
                current_instance_id=caller_instance_id,
                agent_id=caller_agent_id,
                label=label,
                when_or_every=when_or_every,
                message_to_self=message_to_self,
                link_board_item=link_board_item,
            )
        except ValueError as exc:
            # Engine-authored message (e.g. "label already exists") — safe
            # to surface.
            return _error(str(exc))
        except Exception as exc:  # noqa: BLE001 — house pattern: error string, never raise
            logger.exception("self_alarm failed")
            return _error("self_alarm failed")

    self_alarm._full_doc_ = """Arm a reminder to yourself, at least 30 minutes out.

The alarm is a durable schedule that fires at the stated instant and hands
a SELF-CONTAINED message to a FRESH Ari instance — an instance with no
memory of this request, so the message must name the action explicitly.
`message_to_self` is appended VERBATIM as the message's Action line.

The board is the user-visible surface — there is NO ping
--------------------------------------------------------------------
The promise: at the scheduled instant a fresh Ari instance receives your
message, performs the action and leaves evidence ON THE BOARD (status /
notes) and in any job it dispatches; the live board view shows the change.
What is NOT promised: a chat / Discord / Slack message, a reminder in the
conversation, or an interruption. The scheduler source is one-way and the
receiving instance has no chat identity — so the board is the only
guaranteed surface. When you set or move a board item's due date, arm (or
re-arm) a self_alarm for it and pass `link_board_item` — the alarm is the
reminder mechanism, the board item's due date stays the source of truth
for the deadline.

Lateness — both cap states, and the skip signature
---------------------------------------------------
`one_shot_max_lateness_seconds` is DAEMON-GLOBAL config read at fire
time; the tool cannot set it, and every response echoes the current value
as `lateness_cap_seconds`:
  * `null` (the default) = UNLIMITED lateness. An alarm whose instant
    passed while the daemon was down fires LATE, at the next boot.
  * an integer N = a boot later than N writes a `schedule_executions` row
    with status `skipped`, dispatches NOTHING, and leaves the schedule
    ARMED — it re-checks the same past instant every 5 seconds with no
    self-heal and no user-visible signal. The only remedy is a manual
    `task_schedule_update` that moves the alarm to a new time.
The composed message always states the scheduled instant, so a late
delivery is detectable.

What this tool CANNOT observe
-----------------------------
There is no agent-facing post-fire hook. "It fired" is readable only from
the schedule row (status `stopped`, enabled False) — the tool never
reports a fire, and there is no auto-cleanup and no automatic "done" on
the linked board item. `stopped` means DISPATCHED, not acted upon: the
completion callback fires right after the enqueue, so the action itself
can still be lost or doubled.

Label namespace
---------------
Every label this tool mints is stored as `self-alarm:<label>` — the prefix
is added automatically (pass the bare label; a label that already starts
with `self-alarm:` is rejected). The namespace exists because the label
lookup filters only on the NAME, so an unprefixed label could collide
with an MCP-server or Slack source name — a collision that is invisible in
the schedule list. The label is also what the fired instance correlates
on: `task_schedule_list` shows it.

List semantics
--------------
`task_schedule_list` is NOT status-filtered by default and the page is
capped at 100 rows (newest first) — so armed alarms eventually hide
behind stopped ones. Pass `status='running'` to see the ARMED alarms.
`stopped` means dispatched, not acted upon.

Args:
    label: Short human name (1-115 chars). Stored as `self-alarm:<label>`.
    when_or_every: An absolute ISO-8601 instant ('2026-10-20T09:00:00+07:00'),
        a relative duration ('45m', 'in 2h', '1d30m'), 'daily 06:30',
        'weekly 1 06:30' (0=Sunday..6=Saturday), or a raw cron expression
        ('cron 0 9 * * *'). Interval recurrence ('every 1h') is NOT
        supported — use a raw cron.
    message_to_self: What the fresh instance should do. Appended verbatim.
    link_board_item: Optional board item id ('wi-1a2b3c4d') to link.

Returns:
    dict with ok, source_id, label (the effective `self-alarm:` label),
    status, recurrence, instance_mode, timezone, armed_at_utc,
    first_fire_at_local, first_fire_at_utc, board_item, reused,
    tz_warning, lateness_cap_seconds and message_preview. Failures come
    back as "ERROR: ..." strings, never as exceptions.

Examples:
    self_alarm(label="deploy-check", when_or_every="in 45m",
               message_to_self="Check the deploy status and update the board item.")
    self_alarm(label="morning-brief", when_or_every="daily 06:30",
               message_to_self="Write the morning briefing on the board.",
               link_board_item="wi-1a2b3c4d")
    self_alarm(label="weekly-rollup", when_or_every="weekly 5 16:30",
               message_to_self="Post the weekly rollup to the board.")

Error cases (returned as "ERROR: ..." strings, never raised):
    a fire closer than 30 minutes (the error always prints the computed
    next fire, and suggests `bash` + `sleep` for one-shots); a label
    that already carries the `self-alarm:` prefix; a label longer than
    115 characters; an unparseable board item id; an unparseable
    `when_or_every`; an unresolvable project id; a re-arm that is not
    strictly later than the stored one.
"""

    return [self_alarm]


async def _arm_self_alarm(
    *,
    service: Any,
    source_repo: Any,
    instance_repository: Any,
    project_repository: Any,
    current_instance_id: str,
    agent_id: str,
    label: str,
    when_or_every: str,
    message_to_self: str,
    link_board_item: str | None,
) -> dict | str:
    """The D4 policy — verify-compatibility → floor → update+assert →
    fallback+neutralization.

    The sequence is STRICTLY ORDERED: steps 1-3 never mutate the stored
    row, step 4 is the only mutation, and step 6 is what makes the
    fallback path safe.
    """
    # ── Layer 3 of the access posture: the runtime ari-only gate ────────
    if agent_id != SELF_ALARM_AUTHORIZED_AGENT:
        return _error(
            f"self_alarm is available to the {SELF_ALARM_AUTHORIZED_AGENT} agent "
            f"only (caller: {agent_id or 'unknown'})."
        )

    # ── D11: project_id resolve-FIRST, before any label lookup ──────────
    project_id = _resolve_project_id(instance_repository, current_instance_id)
    if not project_id:
        return _error(
            f"cannot resolve project_id for instance {current_instance_id} — the "
            "alarm was NOT armed. A project is required so the fire takes the "
            "job-queue route (with its JobItem, idempotency key and retry)."
        )

    # ── Input validation (pure — no repository access) ─────────────────
    try:
        bare_label = validate_label_input(label)
    except SelfAlarmInputError as exc:
        return _error(str(exc))
    effective_label = effective_label_for(bare_label)

    try:
        board_item = validate_board_id(link_board_item)
    except SelfAlarmInputError as exc:
        return _error(str(exc))

    action_text = (message_to_self or "").strip()
    if not action_text:
        return _error(
            "message_to_self must be a non-empty string — it is the action the "
            "fresh instance performs (it has no memory of this request)."
        )

    try:
        parsed = parse_when_or_every(when_or_every)
    except SelfAlarmInputError as exc:
        return _error(str(exc))

    # ── D4 step 1 — the READ-ONLY label probe ──────────────────────────
    row = await _lookup_by_name(source_repo, effective_label)
    stored = _read_stored_row(row)

    # ── D4 step 2 — the compatibility gate (BEFORE any mutation) ───────
    gate = _compatibility_gate(parsed, stored)

    # ── Timezone resolution (the wrapper's own chain) ──────────────────
    zone, zone_name, tz_warning = await _resolve_wrapper_timezone(project_repository)
    now = datetime.now(zone)
    armed_at_utc = now.astimezone(_stdlib_timezone.utc)

    stored_weekday: int | None = None
    stored_cron: str | None = None
    if gate.mode == "reuse" and stored is not None:
        stored_weekday = (
            _stored_weekday(stored.cron_expression) if parsed.family == FAMILY_WEEKLY else None
        )
        stored_cron = (
            stored.cron_expression if parsed.family == FAMILY_CRON else None
        )

    # ── The fire plan (the wrapper-computed first fire) ─────────────────
    try:
        plan = resolve_fire_plan(
            parsed,
            zone=zone,
            now=now,
            stored_weekday=stored_weekday,
            stored_cron_expression=stored_cron,
        )
    except SelfAlarmInputError as exc:
        return _error(str(exc))

    # ── A fired/paused ONE-SHOT must be re-armed with a STRICTLY later
    #    instant. A message-only re-arm keeps the stored `run_at`, so the
    #    rebuilt adapter re-enqueues under the SAME
    #    `scheduler:{source_id}:{run_at}` idempotency key and a still
    #    non-terminal prior job (within 24 h) collapses the submit — the
    #    alarm would silently never be delivered. (The recurring families
    #    never carry that key: the engine emits it for one-time schedules
    #    ONLY — `scheduler.py:1090-1094` — so a same-time re-arm of a
    #    paused recurring alarm is safe and is the normal resume flow.)
    if (
        gate.mode == "reuse"
        and stored is not None
        and stored.status == "stopped"
        and parsed.family == FAMILY_ONCE
    ):
        stored_run_at = _parse_iso(stored.run_at)
        if stored_run_at is not None:
            stored_run_at_utc = stored_run_at.astimezone(_stdlib_timezone.utc)
            if plan.first_fire_utc <= stored_run_at_utc:
                return _error(
                    f"the alarm {effective_label!r} already fired (or was paused) "
                    f"for {stored_run_at_utc.isoformat()} — a fired alarm must be "
                    "re-armed with a new, LATER time, not the same instant."
                )

    # ── D4 step 3 — the floor, on BOTH branches, BEFORE any engine call ─
    try:
        enforce_min_horizon(plan, now=now)
    except SelfAlarmInputError as exc:
        return _error(str(exc))

    lateness_cap_seconds = _lateness_cap_seconds()

    # ── D4 step 4 — mutate exactly one row ─────────────────────────────
    if gate.mode == "reuse" and stored is not None:
        return await _reuse_alarm(
            service=service,
            source_repo=source_repo,
            stored=stored,
            plan=plan,
            parsed=parsed,
            effective_label=effective_label,
            zone_name=zone_name,
            tz_warning=tz_warning,
            armed_at_utc=armed_at_utc,
            lateness_cap_seconds=lateness_cap_seconds,
            board_item=board_item,
            action_text=action_text,
            project_id=project_id,
            current_instance_id=current_instance_id,
            agent_id=agent_id,
        )

    return await _create_arm(
        service=service,
        source_repo=source_repo,
        parsed=parsed,
        plan=plan,
        effective_label=effective_label,
        gate=gate,
        stored=stored,
        zone_name=zone_name,
        tz_warning=tz_warning,
        armed_at_utc=armed_at_utc,
        lateness_cap_seconds=lateness_cap_seconds,
        board_item=board_item,
        action_text=action_text,
        project_id=project_id,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
    )


def _lateness_cap_seconds() -> int | None:
    """The daemon-global lateness cap (disclosure — the tool cannot set it)."""
    try:
        from daemon.config import SchedulingConfig

        value = SchedulingConfig().one_shot_max_lateness_seconds
    except Exception as exc:  # noqa: BLE001 — bootstrap-safety
        logger.debug("self_alarm: SchedulingConfig unavailable (%s)", exc)
        return None
    return int(value) if value is not None else None


def _create_payload(
    *,
    label: str,
    plan: Any,
    message: str,
    zone_name: str,
    project_id: str,
) -> dict:
    """The family-shaped create payload (D4 step 4a).

    ``agent`` is MANDATORY (the engine's create path fail-fasts on an
    unknown agent) and is always ``ari`` — the alarm fires INTO ari.
    ``instance_mode`` is ``new_instance`` (D6). ``timezone`` is the
    wrapper's resolved IANA name, so the stored zone is exactly the zone
    the wrapper floored the fire against.
    """
    return {
        "label": label,
        "agent": SELF_ALARM_TARGET_AGENT,
        "message": message,
        # The engine ignores `when` for a cron schedule (the REST mapping
        # passes "" there too) — the expression is authoritative.
        "when": plan.when if plan.recurrence != FAMILY_CRON else "",
        "timezone": zone_name,
        "recurrence": plan.recurrence,
        "weekday": plan.weekday,
        "cron_expression": plan.cron_expression,
        "project_id": project_id,
        "instance_mode": SELF_ALARM_INSTANCE_MODE,
    }


async def _create_arm(
    *,
    service: Any,
    source_repo: Any,
    parsed: Any,
    plan: Any,
    effective_label: str,
    gate: _Gate,
    stored: _StoredRow | None,
    zone_name: str,
    tz_warning: str,
    armed_at_utc: datetime,
    lateness_cap_seconds: int | None,
    board_item: str | None,
    action_text: str,
    project_id: str,
    current_instance_id: str,
    agent_id: str,
) -> dict | str:
    """D4 step 4a — CREATE the row (with the candidate collision check).

    The burned/mismatched case mints a FRESH label (``-2`` .. ``-5``);
    every candidate is collision-checked with the same name lookup before
    the create, because the create itself races (the table has NO unique
    constraint on ``name``). The duplicate loser is NEVER cancelled —
    cancel burns the name and hides the evidence; a duplicate is REPORTED.
    """
    candidates = candidate_labels(effective_label)
    if gate.mode == "fresh_label":
        # The base label is taken (cancelled / mismatched / foreign) —
        # start at the first suffix candidate.
        candidates = candidates[1:]

    chosen_label: str | None = None
    for candidate in candidates:
        existing = await _lookup_by_name(source_repo, candidate)
        if existing is None:
            chosen_label = candidate
            break
    if chosen_label is None:
        return _error(
            f"every free label candidate for {effective_label!r} is taken "
            f"({', '.join(candidates)}) — a new label is required."
        )

    # The message is composed BEFORE the create (the create carries it).
    # The source id does not exist yet on this path, so the "Armed at"
    # line carries no id segment — the effective label in the header is
    # the correlation handle the fired instance uses with
    # `task_schedule_list`.
    message = compose_alarm_message(
        effective_label=chosen_label,
        first_fire_local=plan.first_fire_local,
        first_fire_utc=plan.first_fire_utc,
        armed_at_utc=armed_at_utc,
        recurrence=plan.recurrence,
        timezone_name=zone_name,
        source_id="",
        board_item=board_item,
        message_to_self=action_text,
    )

    payload = _create_payload(
        label=chosen_label,
        plan=plan,
        message=message,
        zone_name=zone_name,
        project_id=project_id,
    )
    result = await service.create_schedule(
        payload,
        caller_instance_id=current_instance_id,
        caller_agent_id=agent_id,
    )

    created_source_id = str(getattr(result, "source_id", "") or "")
    created_status = str(getattr(result, "status", "") or "")

    # Post-create duplicate report — the name lookup returns only the
    # FIRST match, so a DIFFERENT id means another row shares the name.
    duplicate_source_ids: list[str] = []
    reread = await _lookup_by_name(source_repo, chosen_label)
    if reread is not None:
        other_id = str(getattr(reread, "source_id", "") or "")
        if other_id and other_id != created_source_id:
            duplicate_source_ids.append(other_id)

    response: dict[str, Any] = {
        "ok": True,
        "source_id": created_source_id,
        "label": chosen_label,
        "status": created_status,
        "recurrence": plan.recurrence,
        "instance_mode": SELF_ALARM_INSTANCE_MODE,
        "timezone": zone_name,
        "armed_at_utc": armed_at_utc.isoformat(),
        "first_fire_at_local": plan.first_fire_local.isoformat(),
        "first_fire_at_utc": plan.first_fire_utc.isoformat(),
        "board_item": board_item,
        "reused": False,
        "tz_warning": tz_warning,
        "lateness_cap_seconds": lateness_cap_seconds,
        "message_preview": summarize_message(message),
    }
    if gate.reason:
        response["fallback_reason"] = gate.reason
        if gate.detail:
            response["fallback_detail"] = gate.detail
    if gate.mode == "fresh_label" and stored is not None:
        # The pre-existing row is UNTOUCHED and still armed with its own
        # schedule — the honest consequence of not mutating it.
        response["untouched_existing"] = {
            "label": stored.name,
            "source_id": stored.source_id,
            "status": stored.status,
            "enabled": stored.enabled,
            "recurrence": stored.recurrence,
        }
    if duplicate_source_ids:
        response["duplicate_source_ids"] = duplicate_source_ids
        response["duplicate_note"] = (
            "another row shares this label — it was REPORTED, never cancelled "
            "(cancelling would burn the name and hide the evidence)."
        )
    return response


async def _reuse_alarm(
    *,
    service: Any,
    source_repo: Any,
    stored: _StoredRow,
    plan: Any,
    parsed: Any,
    effective_label: str,
    zone_name: str,
    tz_warning: str,
    armed_at_utc: datetime,
    lateness_cap_seconds: int | None,
    board_item: str | None,
    action_text: str,
    project_id: str,
    current_instance_id: str,
    agent_id: str,
) -> dict | str:
    """D4 step 4b — UPDATE the compatible row in place, then ASSERT.

    The payload is family-shaped: ``once`` / ``daily`` / ``weekly`` get a
    ``when``; a stored ``cron`` row gets NO ``when`` at all (the engine
    RAISES on one). ``timezone`` is MANDATORY (the engine refreshes the
    stored zone only when it is set — omitting it leaves a STALE zone and
    the alarm fires at the wrong instant). ``paused=False`` is MANDATORY
    (it is the resume branch; without it a fired row keeps
    ``enabled=False``, the rebuild is skipped and the alarm NEVER fires).
    """
    message = compose_alarm_message(
        effective_label=effective_label,
        first_fire_local=plan.first_fire_local,
        first_fire_utc=plan.first_fire_utc,
        armed_at_utc=armed_at_utc,
        recurrence=plan.recurrence,
        timezone_name=zone_name,
        source_id=stored.source_id,
        board_item=board_item,
        message_to_self=action_text,
    )

    update_payload: dict[str, Any] = {
        "message": message,
        "timezone": zone_name,
        "paused": False,
    }
    if plan.recurrence != FAMILY_CRON:
        update_payload["when"] = plan.when

    result = await service.update_schedule(stored.source_id, update_payload)

    # ── The response assertions (the resume detector) ──────────────────
    problems: list[str] = []
    if getattr(result, "paused", None) is not False:
        problems.append(
            f"the update response reports paused={getattr(result, 'paused', None)!r}, "
            "expected False — the resume did not take and the alarm would stay "
            "silently disarmed"
        )
    update_status = str(getattr(result, "status", "") or "")
    if update_status == "stopped":
        problems.append(
            "the update response reports status='stopped' — the alarm would "
            "stay silently disarmed"
        )
    echoed_next = _parse_iso(getattr(result, "next_run_at_utc", None))
    if echoed_next is None:
        problems.append(
            "the update response carries no next_run_at_utc — the armed instant "
            "could not be verified"
        )
    elif not _same_instant(echoed_next.astimezone(_stdlib_timezone.utc), plan.first_fire_utc):
        problems.append(
            f"the update response next_run_at_utc ({echoed_next.isoformat()}) does "
            f"not match the computed first fire ({plan.first_fire_utc.isoformat()})"
        )

    if not problems:
        return {
            "ok": True,
            "source_id": stored.source_id,
            "label": effective_label,
            "status": update_status,
            "recurrence": plan.recurrence,
            "instance_mode": SELF_ALARM_INSTANCE_MODE,
            "timezone": zone_name,
            "armed_at_utc": armed_at_utc.isoformat(),
            "first_fire_at_local": plan.first_fire_local.isoformat(),
            "first_fire_at_utc": plan.first_fire_utc.isoformat(),
            "board_item": board_item,
            "reused": True,
            "tz_warning": tz_warning,
            "lateness_cap_seconds": lateness_cap_seconds,
            "message_preview": summarize_message(message),
        }

    # ── D4 step 5 — FALLBACK: mint a fresh label and create it ─────────
    # The update has ALREADY mutated and re-armed the original row (the
    # engine mutates BEFORE it answers) ⇒ step 6 (neutralization) is
    # mandatory.
    logger.warning(
        "self_alarm: reuse assertion failed for %s (%s) — falling back to a "
        "fresh label",
        stored.source_id,
        "; ".join(problems),
    )
    fresh = await _create_arm(
        service=service,
        source_repo=source_repo,
        parsed=parsed,
        plan=plan,
        effective_label=effective_label,
        gate=_Gate(mode="fresh_label", reason="reuse_assertion_failed", detail="; ".join(problems)),
        stored=stored,
        zone_name=zone_name,
        tz_warning=tz_warning,
        armed_at_utc=armed_at_utc,
        lateness_cap_seconds=lateness_cap_seconds,
        board_item=board_item,
        action_text=action_text,
        project_id=project_id,
        current_instance_id=current_instance_id,
        agent_id=agent_id,
    )
    if isinstance(fresh, str):
        return fresh

    # ── D4 step 6 — neutralize the mutated original (MANDATORY) ─────────
    # The plan mandates a PAUSE: `update_schedule(source_id, {paused: True})`
    # — the engine's own disarm-without-destroying primitive. The row, its
    # history AND its label survive. The pause is issued unconditionally
    # and carries NO `when` (the row is disarmed, not re-timed).
    try:
        pause_result = await service.update_schedule(stored.source_id, {"paused": True})
    except Exception as exc:  # noqa: BLE001 — a still-armed original is a hard error
        logger.exception("self_alarm: neutralizing the mutated original failed")
        return _error(
            f"the alarm {effective_label!r} could not be neutralized "
            f"(source_id {stored.source_id}): {exc}. The fresh label "
            f"{fresh.get('label')!r} (source_id {fresh.get('source_id')}) was "
            "armed, but the original may still be armed — use a fresh label "
            "on retry."
        )

    if getattr(pause_result, "paused", None) is not True:
        return _error(
            f"the alarm {effective_label!r} could not be neutralized "
            f"(source_id {stored.source_id}): the pause response does not "
            f"report paused=True. The fresh label {fresh.get('label')!r} "
            f"(source_id {fresh.get('source_id')}) was armed, but the original "
            "may still be armed — use a fresh label on retry."
        )

    fresh["neutralized_original"] = {
        "source_id": stored.source_id,
        "label": stored.name,
        "paused": True,
    }
    fresh["reuse_assertion_problems"] = problems
    return fresh
