"""Pure helpers for the ``self_alarm`` wrapper (ari-tools Phase 2).

This module is the **pure** half of the self-alarm wrapper (D2 of
``phase2-plan.md``). It contains NO tool imports, NO engine calls and NO
DB access — every function here is a deterministic transform over its
arguments, so the whole grammar / fire-plan / floor / message surface is
100% unit-testable without a daemon, a database or a live scheduler.

The LLM-facing half lives in ``daemon/tools/self_alarm.py``; the
scheduling engine is consumed VERBATIM from
``daemon/services/scheduling_service.py`` and is never modified by this
phase.

What lives here (the plan's Task 2 list):

* :func:`parse_when_or_every` — the small, prefix-disambiguated
  ``when_or_every`` grammar (D3).
* :func:`resolve_fire_plan` — the tz-aware fire plan: the absolute
  first-fire instant plus the family-shaped engine payload (D3).
* :func:`enforce_min_horizon` — the >= 30-minute floor with its
  FAMILY-AWARE rejection text (D9).
* :func:`compose_alarm_message` — the fresh-instance message contract
  (D7).
* :func:`validate_board_id` — the phase-1 board id shape (D12).

The public primitives reused here are the SAME ones the engine uses
(``daemon.util.tz``): :func:`daemon.util.tz.resolve_timezone`,
:func:`daemon.util.tz.anchor_local_to_utc` and
:func:`daemon.util.tz.compute_next_cron_fire`. Reusing them (instead of
re-deriving DST semantics) is what makes the wrapper's first fire
identical to the engine's, by construction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as _stdlib_timezone
from zoneinfo import ZoneInfo

__all__ = [
    "BOARD_ITEM_RE",
    "LABEL_NAMESPACE",
    "MAX_LABEL_INPUT",
    "MIN_HORIZON_SECONDS",
    "FirePlan",
    "ParsedWhen",
    "SelfAlarmInputError",
    "compose_alarm_message",
    "enforce_min_horizon",
    "parse_when_or_every",
    "resolve_fire_plan",
    "validate_board_id",
    "validate_label_input",
]


# ---------------------------------------------------------------------------
# Constants (the policy surface — every number here is a plan-pinned value)
# ---------------------------------------------------------------------------

#: The label namespace every wrapper-minted label carries (D4). The
#: name lookup (``get_source_config_by_name``) filters ONLY on ``name``
#: and has NO ``source_type`` filter, so an unprefixed label could
#: collide with an MCP-server or Slack source name — a collision that is
#: invisible in the schedule list.
LABEL_NAMESPACE = "self-alarm:"

#: The engine caps ``label`` at 128 chars (``ScheduleCreatePayload.label``).
#: The stored name is the 11-char namespace prefix + the user input + an
#: optional ``-N`` retry suffix (``-5`` is the cap) => 115 reserves the
#: worst case.
MAX_LABEL_INPUT = 115

#: The minimum horizon (D9): ``first_fire - now >= 1800 s``. Stated in
#: minutes to the user, compared in seconds against the absolute instant.
MIN_HORIZON_SECONDS = 1800

#: The phase-1 board item id shape (D12) — ``wi-<8 hex>``.
BOARD_ITEM_RE = re.compile(r"^wi-[0-9a-f]{8}$")

#: The recurrence families the engine knows (``scheduling_service._RECURRENCES``).
FAMILY_ONCE = "once"
FAMILY_DAILY = "daily"
FAMILY_WEEKLY = "weekly"
FAMILY_CRON = "cron"

#: The unit → seconds table of the relative-duration grammar.
_UNIT_SECONDS: dict[str, float] = {
    "s": 1.0,
    "sec": 1.0,
    "m": 60.0,
    "min": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
}

_DAILY_RE = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
_WEEKLY_RE = re.compile(r"^weekly\s+(\d)\s+(\d{1,2}):(\d{2})$", re.IGNORECASE)
_CRON_RE = re.compile(r"^cron\s+(.+)$", re.IGNORECASE)
_EVERY_RE = re.compile(r"^every\b", re.IGNORECASE)
_DURATION_TOKEN_RE = re.compile(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+)", re.IGNORECASE)


class SelfAlarmInputError(ValueError):
    """A typed, agent-readable rejection from the pure layer.

    Every message is AUTHORED (no ``str(exc)`` of an unexpected
    exception ever reaches the tool payload — the PB-F1 leak class).
    The tool layer maps this onto the house ``"ERROR: {msg}"`` envelope.
    """


# ---------------------------------------------------------------------------
# The parsed grammar
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedWhen:
    """The parsed ``when_or_every`` request.

    Attributes:
        family: The engine recurrence family — ``once`` | ``daily`` |
            ``weekly`` | ``cron``. This is the axis the D4 step-2
            compatibility gate compares against the STORED row.
        kind: How the request was expressed — ``absolute`` | ``relative``
            | ``daily`` | ``weekly`` | ``cron``. For the ``once`` family
            this distinguishes an absolute instant from a relative
            duration; for the recurring families it equals ``family``.
        when: The engine payload ``when`` — an ISO 8601 string for
            ``once``, ``"HH:MM"`` for ``daily``/``weekly``, ``None`` for
            ``cron`` (the engine ignores it there; the REST mapping
            passes ``""``).
        weekday: The canonical day-of-week for ``weekly`` (0=Sunday ..
            6=Saturday — the engine's own convention).
        cron_expression: The raw cron expression for ``cron``.
        delta_seconds: The relative offset in seconds (``relative`` only).
        schedule_text: The canonical human rendering of the request, used
            verbatim in the family-aware floor message.
    """

    family: str
    kind: str
    when: str | None
    weekday: int | None
    cron_expression: str | None
    delta_seconds: float | None
    schedule_text: str

    @property
    def is_recurring(self) -> bool:
        """True for the recurring families (daily / weekly / cron)."""
        return self.family != FAMILY_ONCE


@dataclass(frozen=True)
class FirePlan:
    """The tz-aware fire plan for one request.

    Attributes:
        recurrence: The engine recurrence family.
        when: The engine payload ``when`` for THIS row's semantics.
        weekday: The day-of-week the row will carry.
        cron_expression: The raw cron the row will carry (``cron`` family).
        cron_for_next_fire: The cron expression the NEXT occurrence is
            computed from (``daily`` / ``weekly`` / ``cron`` only).
        first_fire_utc: The absolute first-fire instant (UTC).
        first_fire_local: The same instant rendered in the resolved zone.
        zone: The resolved zone (``ZoneInfo``) the plan was computed in.
        schedule_text: The canonical human rendering of the request.
    """

    recurrence: str
    when: str | None
    weekday: int | None
    cron_expression: str | None
    cron_for_next_fire: str | None
    first_fire_utc: datetime
    first_fire_local: datetime
    zone: ZoneInfo
    schedule_text: str

    @property
    def is_recurring(self) -> bool:
        """True for the recurring families."""
        return self.recurrence != FAMILY_ONCE


# ---------------------------------------------------------------------------
# Grammar (D3)
# ---------------------------------------------------------------------------


def _parse_relative_duration(text: str) -> float | None:
    """Parse a relative duration (``45m``, ``in 2h``, ``1d30m``) → seconds.

    Compound durations are supported (the grammar table lists ``1d30m``),
    so the segments are summed. Returns ``None`` when the text is not a
    relative duration at all (the caller then tries the absolute branch).
    """
    body = text.strip()
    lowered = body.lower()
    if lowered == "in":
        return None
    if lowered.startswith("in "):
        body = body[3:].strip()
    if not body:
        return None
    total = 0.0
    pos = 0
    matched = False
    while pos < len(body):
        match = _DURATION_TOKEN_RE.match(body, pos)
        if match is None:
            return None
        unit = match.group(2).lower()
        if unit not in _UNIT_SECONDS:
            return None
        total += float(match.group(1)) * _UNIT_SECONDS[unit]
        pos = match.end()
        matched = True
    if not matched or total <= 0:
        return None
    return total


def _parse_absolute(text: str) -> datetime | None:
    """Parse an ISO 8601 instant (naive or offset-aware) → ``datetime``."""
    candidate = text.strip()
    if not candidate:
        return None
    if candidate.endswith(("Z", "z")):
        candidate = candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        return None


def _normalize_hhmm(hour_text: str, minute_text: str) -> str:
    """``"6"``, ``"30"`` → ``"06:30"`` (the engine's canonical local time)."""
    return f"{int(hour_text):02d}:{int(minute_text):02d}"


def parse_when_or_every(text: str) -> ParsedWhen:
    """Parse the small ``when_or_every`` grammar into a :class:`ParsedWhen`.

    Accepted forms (D3):

    * absolute one-shot — ``2026-10-20T09:00:00+07:00`` / ``…Z`` /
      ``2026-10-20T09:00:00`` (naive);
    * relative one-shot — ``45m``, ``in 45m``, ``2h``, ``1d30m``, ``90s``;
    * ``daily 06:30``;
    * ``weekly 1 06:30`` (0=Sunday .. 6=Saturday);
    * ``cron 0 9 * * *`` (raw 5-field expression).

    Rejected: ``every 1h`` (the engine has no interval recurrence) and
    everything else (a typed error lists the accepted forms).

    Raises:
        SelfAlarmInputError: The request is not in the grammar.
    """
    raw = (text or "").strip()
    if not raw:
        raise SelfAlarmInputError(
            "when_or_every must not be empty. Accepted forms: an absolute "
            "ISO-8601 instant (2026-10-20T09:00:00+07:00), a relative "
            "duration (45m, in 2h, 1d30m), 'daily 06:30', 'weekly 1 06:30' "
            "or 'cron 0 9 * * *'."
        )

    if _EVERY_RE.match(raw):
        raise SelfAlarmInputError(
            "interval recurrence ('every ...') is not supported — the "
            "scheduling engine has no interval variant in create_schedule. "
            "Use a raw cron expression instead — e.g. `cron 0 */2 * * *` "
            "(every two hours). A cron with a period of 30 minutes or less "
            "can never satisfy the 30-minute horizon, so pick a period "
            "comfortably above it."
        )

    weekly_match = _WEEKLY_RE.match(raw)
    if weekly_match is not None:
        weekday = int(weekly_match.group(1))
        if weekday < 0 or weekday > 6:
            raise SelfAlarmInputError(
                f"weekly day-of-week must be 0-6 (0=Sunday..6=Saturday), got {weekday}."
            )
        hour_text, minute_text = weekly_match.group(2), weekly_match.group(3)
        if int(hour_text) > 23 or int(minute_text) > 59:
            raise SelfAlarmInputError(
                f"weekly time must be a valid wall-clock time, got {hour_text}:{minute_text}."
            )
        hhmm = _normalize_hhmm(hour_text, minute_text)
        return ParsedWhen(
            family=FAMILY_WEEKLY,
            kind=FAMILY_WEEKLY,
            when=hhmm,
            weekday=weekday,
            cron_expression=None,
            delta_seconds=None,
            schedule_text=f"weekly {weekday} {hhmm}",
        )

    daily_match = _DAILY_RE.match(raw)
    if daily_match is not None:
        hour_text, minute_text = daily_match.group(1), daily_match.group(2)
        if int(hour_text) > 23 or int(minute_text) > 59:
            raise SelfAlarmInputError(
                f"daily time must be a valid wall-clock time, got {hour_text}:{minute_text}."
            )
        hhmm = _normalize_hhmm(hour_text, minute_text)
        return ParsedWhen(
            family=FAMILY_DAILY,
            kind=FAMILY_DAILY,
            when=hhmm,
            weekday=None,
            cron_expression=None,
            delta_seconds=None,
            schedule_text=f"daily {hhmm}",
        )

    cron_match = _CRON_RE.match(raw)
    if cron_match is not None:
        expression = cron_match.group(1).strip()
        if not expression:
            raise SelfAlarmInputError(
                "cron requires a raw 5-field expression — e.g. `cron 0 9 * * *`."
            )
        _validate_cron_expression(expression)
        return ParsedWhen(
            family=FAMILY_CRON,
            kind=FAMILY_CRON,
            when=None,
            weekday=None,
            cron_expression=expression,
            delta_seconds=None,
            schedule_text=f"cron {expression}",
        )

    delta_seconds = _parse_relative_duration(raw)
    if delta_seconds is not None:
        return ParsedWhen(
            family=FAMILY_ONCE,
            kind="relative",
            when=None,
            weekday=None,
            cron_expression=None,
            delta_seconds=delta_seconds,
            schedule_text=raw,
        )

    absolute = _parse_absolute(raw)
    if absolute is not None:
        return ParsedWhen(
            family=FAMILY_ONCE,
            kind="absolute",
            when=absolute.isoformat(),
            weekday=None,
            cron_expression=None,
            delta_seconds=None,
            schedule_text=raw,
        )

    raise SelfAlarmInputError(
        f"cannot parse when_or_every {raw!r}. Accepted forms: an absolute "
        "ISO-8601 instant (2026-10-20T09:00:00+07:00), a relative duration "
        "(45m, in 2h, 1d30m), 'daily 06:30', 'weekly 1 06:30' or "
        "'cron 0 9 * * *'."
    )


def _validate_cron_expression(expression: str) -> None:
    """Validate a raw cron expression with croniter (the engine's own check).

    The engine validates the expression on the create path
    (``scheduling_service.py:684-694``); the wrapper validates it EARLIER
    so a bad expression never reaches a label lookup.

    Raises:
        SelfAlarmInputError: The expression is not a valid cron.
    """
    try:
        from croniter import croniter
    except ImportError:  # pragma: no cover — croniter is a hard dependency
        return
    try:
        croniter(expression, datetime.now(_stdlib_timezone.utc))
    except Exception as exc:  # noqa: BLE001 — any croniter rejection is a user error
        raise SelfAlarmInputError(
            f"invalid cron expression {expression!r}: {exc}. A cron example "
            "that satisfies the 30-minute horizon is `cron 0 9 * * *`."
        ) from exc


# ---------------------------------------------------------------------------
# The fire plan (D3) — the absolute first-fire instant
# ---------------------------------------------------------------------------


def _cron_for(hour: int, minute: int, weekday: int | None) -> str:
    """Build the cron the row will carry — the ENGINE's own construction.

    ``scheduling_service.create_schedule`` builds ``f"{minute} {hour} * * *"``
    for daily and ``f"{minute} {hour} * * {weekday}"`` for weekly
    (``scheduling_service.py:722-729``). The wrapper builds the SAME
    expression so the first fire it computes is the first fire the row
    will produce.
    """
    if weekday is None:
        return f"{minute} {hour} * * *"
    return f"{minute} {hour} * * {weekday}"


def resolve_fire_plan(
    parsed: ParsedWhen,
    *,
    zone: ZoneInfo,
    now: datetime,
    stored_weekday: int | None = None,
    stored_cron_expression: str | None = None,
) -> FirePlan:
    """Compute the tz-aware fire plan for one request.

    The first-fire instant is computed per the semantics of the row the
    request will END UP on:

    * ``once`` → the requested absolute instant (a naive one is anchored
      in the resolved zone, exactly like the engine's create path);
    * ``daily`` / ``weekly`` / ``cron`` → the NEXT OCCURRENCE of the cron
      the row will carry, computed with the same helper the engine's
      ``_compute_next_run`` uses (``daemon.util.tz.compute_next_cron_fire``)
      ⇒ DST fold/gap semantics are identical by construction.

    Args:
        parsed: The parsed request.
        zone: The resolved timezone (``ZoneInfo``).
        now: The current instant (used as the cron cursor).
        stored_weekday: On the REUSE path, the day-of-week the STORED row
            carries — the update branch rewrites only the minute/hour of
            the stored cron and PRESERVES the stored DOW, so the first
            fire must be computed with the stored DOW.
        stored_cron_expression: On the REUSE path, the cron expression the
            STORED row carries (the ``cron`` family: the update branch
            raises on a ``when``, so the stored expression stays
            authoritative).

    Returns:
        The computed :class:`FirePlan`.

    Raises:
        SelfAlarmInputError: The plan cannot be computed (e.g. a cron with
            no next occurrence).
    """
    local_now = now.astimezone(zone)

    if parsed.family == FAMILY_ONCE:
        if parsed.kind == "relative":
            first_fire = local_now + timedelta(seconds=float(parsed.delta_seconds or 0.0))
            when_value = first_fire.isoformat()
        else:
            naive_or_aware = _parse_absolute(parsed.when or "")
            if naive_or_aware is None:
                raise SelfAlarmInputError(
                    f"cannot parse the absolute instant {parsed.when!r} — expected ISO 8601."
                )
            if naive_or_aware.tzinfo is None:
                from daemon.util.tz import anchor_local_to_utc

                anchored, _warning = anchor_local_to_utc(naive_or_aware, zone)
                first_fire = anchored
            else:
                first_fire = naive_or_aware.astimezone(zone)
            when_value = first_fire.isoformat()
        return FirePlan(
            recurrence=FAMILY_ONCE,
            when=when_value,
            weekday=None,
            cron_expression=None,
            cron_for_next_fire=None,
            first_fire_utc=first_fire.astimezone(_stdlib_timezone.utc),
            first_fire_local=first_fire.astimezone(zone),
            zone=zone,
            schedule_text=parsed.schedule_text,
        )

    # Recurring families — the cron the row will carry.
    if parsed.family == FAMILY_DAILY:
        hour, minute = _split_hhmm(parsed.when or "")
        cron_expression = _cron_for(hour, minute, None)
    elif parsed.family == FAMILY_WEEKLY:
        hour, minute = _split_hhmm(parsed.when or "")
        weekday = stored_weekday if stored_weekday is not None else parsed.weekday
        cron_expression = _cron_for(hour, minute, weekday)
    else:  # cron
        hour = minute = None
        weekday = None
        cron_expression = (
            stored_cron_expression
            if stored_cron_expression is not None
            else (parsed.cron_expression or "")
        ).strip()
        if not cron_expression:
            raise SelfAlarmInputError(
                "the stored cron expression is empty — the row cannot be re-armed."
            )

    from daemon.util.tz import compute_next_cron_fire

    next_fire = compute_next_cron_fire(cron_expression, local_now, zone)
    if next_fire is None:
        raise SelfAlarmInputError(
            f"cannot compute a next fire for {parsed.schedule_text!r} — the "
            "expression has no upcoming occurrence."
        )

    return FirePlan(
        recurrence=parsed.family,
        when=parsed.when,
        weekday=weekday if parsed.family == FAMILY_WEEKLY else None,
        cron_expression=cron_expression if parsed.family == FAMILY_CRON else None,
        cron_for_next_fire=cron_expression,
        first_fire_utc=next_fire.astimezone(_stdlib_timezone.utc),
        first_fire_local=next_fire.astimezone(zone),
        zone=zone,
        schedule_text=parsed.schedule_text,
    )


def _split_hhmm(value: str) -> tuple[int, int]:
    """``"06:30"`` → ``(6, 30)``."""
    text = (value or "").strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise SelfAlarmInputError(
            f"invalid wall-clock time {value!r} — expected HH:MM."
        ) from exc


# ---------------------------------------------------------------------------
# The floor (D9)
# ---------------------------------------------------------------------------


def enforce_min_horizon(plan: FirePlan, *, now: datetime) -> None:
    """Enforce the >= 30-minute horizon on the wrapper-computed first fire.

    The check runs on the ABSOLUTE instant (``first_fire - now >= 1800 s``)
    and BEFORE any engine call ⇒ a rejected alarm creates no row, burns no
    label and mutates no stored row.

    The rejection text is FAMILY-AWARE and always prints the computed
    next fire (A-22): the bash-sleep hint is WRONG advice for a recurring
    schedule, so it appears for the one-shot family only.

    Raises:
        SelfAlarmInputError: The first fire is closer than the floor.
    """
    now_utc = now.astimezone(_stdlib_timezone.utc)
    delta_seconds = (plan.first_fire_utc - now_utc).total_seconds()
    if delta_seconds >= MIN_HORIZON_SECONDS:
        return

    minutes_away = int(delta_seconds // 60)
    if not plan.is_recurring:
        raise SelfAlarmInputError(
            "self_alarm requires a minimum horizon of 30 minutes — the "
            f"requested fire time is {minutes_away}m away "
            f"({plan.first_fire_local.isoformat()}). For anything shorter, "
            "use bash with `sleep <seconds>` inside the current turn "
            "instead of an alarm."
        )

    from daemon.util.tz import compute_next_cron_fire

    following = compute_next_cron_fire(
        plan.cron_for_next_fire or "",
        plan.first_fire_local,
        plan.zone,
    )
    following_text = (
        following.astimezone(plan.zone).isoformat() if following is not None else "the next occurrence"
    )
    raise SelfAlarmInputError(
        "self_alarm requires a minimum horizon of 30 minutes — the next "
        f"`{plan.schedule_text}` fire is {minutes_away}m away "
        f"({plan.first_fire_local.isoformat()}). To start recurring later, "
        f"arm this schedule after the first fire ({following_text}) or "
        "choose a later time."
    )


# ---------------------------------------------------------------------------
# The message contract (D7) and the board id (D12)
# ---------------------------------------------------------------------------


def compose_alarm_message(
    *,
    effective_label: str,
    first_fire_local: datetime,
    first_fire_utc: datetime,
    armed_at_utc: datetime,
    recurrence: str,
    timezone_name: str,
    source_id: str,
    board_item: str | None,
    message_to_self: str,
) -> str:
    """Compose the fresh-instance reminder message (D7 — VERBATIM shape).

    The wrapper stamps the prefix, injects the context and appends the
    agent's action text VERBATIM. The "fresh instance, no shared state"
    line is mandatory (Ari instances are separate trees), and the
    scheduled instant is stated so a late delivery is detectable.
    """
    armed_line = (
        f"Armed at: {armed_at_utc.isoformat()}   Schedule: {recurrence} | "
        f"tz {timezone_name}"
    )
    if source_id:
        # The source id does not exist yet on the create path — the
        # effective label in the header is the correlation handle the
        # fired instance uses with `task_schedule_list`.
        armed_line += f" | id {source_id}"
    lines = [
        f"[self-alarm] {effective_label}",
        f"Scheduled fire: {first_fire_local.isoformat()}  ({first_fire_utc.isoformat()})",
        armed_line,
    ]
    if board_item:
        lines.append(
            f"Board item: {board_item} — call board_get with that id to load "
            "its title, body, status and notes before acting."
        )
    lines.append(
        "You are a fresh instance with no memory of this request; everything "
        "you need is in this message."
    )
    lines.append(f"Action: {message_to_self}")
    return "\n".join(lines)


def validate_board_id(board_item: str | None) -> str | None:
    """Validate the optional board link (D12) — shape only, never parsed.

    The id is embedded VERBATIM (no title/status lookup) ⇒ zero runtime
    dependency on the board service. ``None`` means "no board link"; any
    other string must match the pinned phase-1 shape.

    Raises:
        SelfAlarmInputError: The id does not match ``^wi-[0-9a-f]{8}$``.
    """
    if board_item is None:
        return None
    text = board_item.strip()
    if not BOARD_ITEM_RE.match(text):
        raise SelfAlarmInputError(
            f"link_board_item {board_item!r} is not a valid board item id — "
            "expected the form 'wi-1a2b3c4d' (wi- plus 8 lowercase hex "
            "characters)."
        )
    return text


def validate_label_input(label: str) -> str:
    """Validate the human-supplied label (D4 namespace rule).

    The stored name is ALWAYS ``self-alarm:<label>`` — the wrapper never
    accepts a user string that already carries the prefix (it would be
    stored double-prefixed), and the input is capped at
    :data:`MAX_LABEL_INPUT` chars so the engine's 128-char cap always has
    room for the ``-N`` retry suffix.

    Raises:
        SelfAlarmInputError: The label is empty, too long, or already
            carries the namespace prefix.
    """
    text = (label or "").strip()
    if not text:
        raise SelfAlarmInputError("label must be a non-empty string.")
    if text.lower().startswith(LABEL_NAMESPACE):
        raise SelfAlarmInputError(
            f"the '{LABEL_NAMESPACE}' prefix is added automatically — pass the "
            f"bare label (e.g. 'deploy-check'), not '{text}'."
        )
    if len(text) > MAX_LABEL_INPUT:
        raise SelfAlarmInputError(
            f"label must be at most {MAX_LABEL_INPUT} characters (got {len(text)})."
        )
    return text


def effective_label_for(label_input: str) -> str:
    """The stored label — always namespaced (D4)."""
    return f"{LABEL_NAMESPACE}{label_input}"


def candidate_labels(effective_label: str) -> list[str]:
    """The fresh-label candidate chain (D4 step 4a / step 5).

    The base label first, then ``-2`` .. ``-5`` (the plan's cap). Every
    candidate is collision-checked before use — the name lookup alone
    cannot be trusted (the create itself races).
    """
    candidates = [effective_label]
    for suffix in range(2, 6):
        candidates.append(f"{effective_label}-{suffix}")
    return candidates


def summarize_message(message: str, *, max_chars: int = 280) -> str:
    """A bounded preview of the composed message for the tool response."""
    text = message or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"
