"""Env-first knobs + uniform-random minute picker for the Central
Maintenance System's weekly trigger chain (Phase 1).

The chain is ONE self-rescheduling one-shot ``source_configs`` row whose
``run_at`` is rewritten at each fire.  The FIRE INSTANT is not a fixed
time: the Emperor ruled that the window is a KNOB that is re-rolled
every week, so the picker below chooses a minute uniformly at random
inside ``[window_start, window_end)`` on the next occurrence of the
configured weekday.

Knobs (env-first, read at EVERY arm/re-arm — never cached)
-----------------------------------------------------------
* ``ENSEMBLE_MAINT_WINDOW_START`` — ``HH:MM`` local, default ``01:00``.
* ``ENSEMBLE_MAINT_WINDOW_END``   — ``HH:MM`` local, default ``05:00``.
* ``ENSEMBLE_MAINT_WEEKDAY``      — ``0``=Sunday .. ``6``=Saturday
  (canonical, matches cron DOW and ``scheduling_service.py:160-165``),
  default ``0`` (Sunday).
* ``ENSEMBLE_MAINT_TIMEZONE``     — IANA name, default ``Asia/Ho_Chi_Minh``.

Window semantics (EXPLICIT, because off-by-one here is a silent
missed-week bug)
-----------------
* The minute is drawn with ``randrange(start_total_min, end_total_min)``
  ⇒ **start INCLUSIVE, end EXCLUSIVE**.  The default window
  ``01:00``–``05:00`` therefore has exactly **240** candidate minutes
  (60, 61, … 299).
* ``start < end`` on the SAME day is required.  A window that crosses
  midnight is OUT OF SCOPE for v1: there is no safe fire instant, so the
  picker **fails CLOSED** (raises :class:`MaintenanceKnobError`) and the
  caller arms nothing.
* The weekday knob is Sunday-based; Python's ``datetime.weekday()`` is
  Monday-based, hence the ``(D + 6) % 7`` conversion.

Weekday boundary
----------------
The next occurrence of weekday D is picked.  ``allow_today=True``
(the ARM path) keeps today eligible — but ONLY when the randomly
chosen minute is still in the future; a today-past minute rolls to the
next week.  ``allow_today=False`` (the RE-ARM path) never selects
today: the next slot is the next weekday-D counted from the re-arm,
which is what makes the effective interval 5–13 days rather than 7
(BR-1 sub-semantics).

No croniter
-----------
The instant is computed DIRECTLY (no cron expression, no croniter):
``daemon.util.tz.anchor_local_to_utc`` is the canonical tz anchor and
returns an ``(aware, warning)`` TUPLE — the tuple is CONSUMED here,
never discarded (BR-5).

BR-5 — tz honesty
-----------------
The tz knob advertises arbitrary zones, so DST gap/fold are real cases
(ICT itself is a fixed UTC+7 offset with no DST):

* a GAP (spring-forward) shifts the instant forward and emits a loud
  warning;
* a FOLD (fall-back) uses ``fold=0`` (the first occurrence);
* the anchored instant is then RE-CHECKED against ``[start, end)`` IN
  the resolved local zone.  If it lands outside the window the picker
  **fails CLOSED** — no arm, loud warning surfaced, never swallowed.

No-idle attestation
-------------------
This module carries NO idle/busyness precondition of any kind: the
weekly trigger fires regardless of system busyness (R2 / E-2).
"""

from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as _stdlib_timezone
from zoneinfo import ZoneInfo

from daemon.util.tz import anchor_local_to_utc

logger = logging.getLogger(__name__)

#: The four env knobs (env-first; read at EVERY arm/re-arm).
ENV_WINDOW_START = "ENSEMBLE_MAINT_WINDOW_START"
ENV_WINDOW_END = "ENSEMBLE_MAINT_WINDOW_END"
ENV_WEEKDAY = "ENSEMBLE_MAINT_WEEKDAY"
ENV_TIMEZONE = "ENSEMBLE_MAINT_TIMEZONE"

DEFAULT_WINDOW_START = "01:00"
DEFAULT_WINDOW_END = "05:00"
DEFAULT_WEEKDAY = 0  # 0 = Sunday (canonical 0=Sun..6=Sat)
DEFAULT_TIMEZONE = "Asia/Ho_Chi_Minh"

#: Canonical weekday names for the knob value (index == canonical int).
WEEKDAY_NAMES: tuple[str, ...] = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)

#: The stdlib UTC tzinfo — ``ZoneInfo("UTC")`` can raise on a stripped
#: container without tzdata, so the conversion target is the C-level
#: constant (house doctrine, ``daemon/util/tz.py`` module docstring).
_UTC = _stdlib_timezone.utc


class MaintenanceKnobError(ValueError):
    """The knobs cannot produce a safe fire instant (fail-closed).

    Raised for an invalid window, an out-of-range weekday, an
    unresolvable timezone, or an anchored instant that lands outside
    ``[window_start, window_end)`` (BR-5).  Callers MUST NOT swallow
    this silently — no arm is the intended outcome.
    """


@dataclass(frozen=True)
class MaintenanceKnobs:
    """The RESOLVED knobs for one arm/re-arm.

    Attributes:
        window_start: The raw ``HH:MM`` window start (audit echo).
        window_end: The raw ``HH:MM`` window end (audit echo).
        weekday: Canonical weekday, ``0``=Sunday .. ``6``=Saturday.
        timezone: The resolved IANA timezone name.
        start_minute: Window start in minutes since local midnight.
        end_minute: Window end in minutes since local midnight (EXCLUSIVE).
    """

    window_start: str
    window_end: str
    weekday: int
    timezone: str
    start_minute: int
    end_minute: int

    @property
    def candidate_minutes(self) -> int:
        """The number of candidate minutes (``end - start``)."""
        return self.end_minute - self.start_minute


@dataclass(frozen=True)
class PickedFire:
    """One minted fire instant plus the resolved knobs that produced it."""

    instant_utc: datetime
    local_time: str
    weekday: int
    window_start: str
    window_end: str
    timezone: str
    warnings: tuple[str, ...] = ()

    @property
    def run_at_iso(self) -> str:
        """The ISO-8601 UTC string stored in the row's ``run_at``."""
        return self.instant_utc.isoformat()


def _parse_hhmm(raw: str, *, env_name: str) -> int:
    """Parse a ``HH:MM`` string into minutes since local midnight."""
    text = (raw or "").strip()
    if not text:
        raise MaintenanceKnobError(f"{env_name} is empty — expected HH:MM")
    parts = text.split(":")
    if len(parts) != 2:
        raise MaintenanceKnobError(
            f"{env_name}={text!r} is malformed — expected HH:MM"
        )
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError as exc:
        raise MaintenanceKnobError(
            f"{env_name}={text!r} is malformed — expected HH:MM"
        ) from exc
    if not (0 <= hours <= 23) or not (0 <= minutes <= 59):
        raise MaintenanceKnobError(
            f"{env_name}={text!r} is out of range — expected 00:00..23:59"
        )
    return hours * 60 + minutes


def _parse_weekday(raw: str) -> int:
    """Parse the weekday knob into the canonical ``0``=Sun..``6``=Sat int."""
    text = (raw or "").strip()
    if not text:
        raise MaintenanceKnobError(
            f"{ENV_WEEKDAY} is empty — expected an integer 0..6 "
            f"(0={WEEKDAY_NAMES[0]}..6={WEEKDAY_NAMES[6]})"
        )
    try:
        weekday = int(text)
    except ValueError as exc:
        raise MaintenanceKnobError(
            f"{ENV_WEEKDAY}={text!r} is malformed — expected an integer 0..6 "
            f"(0={WEEKDAY_NAMES[0]}..6={WEEKDAY_NAMES[6]})"
        ) from exc
    if not (0 <= weekday <= 6):
        raise MaintenanceKnobError(
            f"{ENV_WEEKDAY}={weekday} is out of range — expected 0..6 "
            f"(0={WEEKDAY_NAMES[0]}..6={WEEKDAY_NAMES[6]})"
        )
    return weekday


def read_maintenance_knobs() -> MaintenanceKnobs:
    """Resolve the env-first knobs (read at EVERY arm/re-arm).

    Env-first, no caching: the value in force at the moment of the
    arm/re-arm is the value that is echoed into the row config, so an
    operator edit takes effect on the next arm without a daemon
    restart.

    Raises:
        MaintenanceKnobError: any knob is invalid (fail-closed — the
            caller arms nothing).
    """
    raw_start = (os.environ.get(ENV_WINDOW_START) or DEFAULT_WINDOW_START).strip()
    raw_end = (os.environ.get(ENV_WINDOW_END) or DEFAULT_WINDOW_END).strip()
    raw_weekday = (os.environ.get(ENV_WEEKDAY) or "").strip()
    raw_timezone = (os.environ.get(ENV_TIMEZONE) or DEFAULT_TIMEZONE).strip()

    start_minute = _parse_hhmm(raw_start, env_name=ENV_WINDOW_START)
    end_minute = _parse_hhmm(raw_end, env_name=ENV_WINDOW_END)
    if start_minute >= end_minute:
        # v1 forbids a midnight-crossing window: there is no safe instant.
        raise MaintenanceKnobError(
            f"{ENV_WINDOW_START}={raw_start!r} is not before "
            f"{ENV_WINDOW_END}={raw_end!r} — a midnight-crossing window is "
            "out of scope for v1; refusing to arm (fail-closed)"
        )
    weekday = _parse_weekday(raw_weekday or str(DEFAULT_WEEKDAY))
    if not raw_timezone:
        raise MaintenanceKnobError(
            f"{ENV_TIMEZONE} is empty — expected an IANA timezone name"
        )
    try:
        ZoneInfo(raw_timezone)
    except Exception as exc:  # noqa: BLE001 — any tzdb failure is fatal here
        raise MaintenanceKnobError(
            f"{ENV_TIMEZONE}={raw_timezone!r} does not resolve to a "
            f"timezone — refusing to arm (fail-closed): {exc}"
        ) from exc

    return MaintenanceKnobs(
        window_start=raw_start,
        window_end=raw_end,
        weekday=weekday,
        timezone=raw_timezone,
        start_minute=start_minute,
        end_minute=end_minute,
    )


def pick_next_fire_instant(
    knobs: MaintenanceKnobs,
    *,
    now: datetime | None = None,
    allow_today: bool = True,
    rng: random.Random | None = None,
) -> PickedFire:
    """Pick the next fire instant inside the window on the target weekday.

    Args:
        knobs: The resolved knobs.
        now: The reference instant.  ``None`` ⇒ "now" in the knob's
            timezone.  A naive value is read as local wall-clock; an
            aware value is converted into the knob's timezone.
        allow_today: ``True`` (the ARM path) keeps today eligible when
            the randomly chosen minute is still in the future; ``False``
            (the RE-ARM path) always selects the next weekday-D
            occurrence counted from ``now`` — the re-arm must not land on
            the same day it just fired.
        rng: Optional ``random.Random`` (tests inject a seeded instance).

    Returns:
        The :class:`PickedFire` — the UTC instant plus the audit echo.

    Raises:
        MaintenanceKnobError: the anchored instant landed outside
            ``[window_start, window_end)`` (BR-5 fail-closed).
    """
    tz = ZoneInfo(knobs.timezone)
    if now is None:
        local_now = datetime.now(tz)
    elif now.tzinfo is None:
        local_now = now.replace(tzinfo=tz)
    else:
        local_now = now.astimezone(tz)

    # ``random`` (module) and ``random.Random`` (instance) both expose
    # ``randrange`` — the injected instance is what makes the draw
    # deterministic for the unit tests.
    picker = rng if rng is not None else random
    # [start, end) — start INCLUSIVE, end EXCLUSIVE.
    minute_of_day = int(picker.randrange(knobs.start_minute, knobs.end_minute))

    # Sunday-based knob (0=Sun..6=Sat) → Python weekday() (Mon=0..Sun=6).
    target_py_weekday = (knobs.weekday + 6) % 7
    # The weekday is anchored on the LOCAL window date (``today`` is in
    # the knob's timezone), NEVER on the UTC date of the stored instant
    # (P5B F2): a "Sunday 01:00–05:00 ICT" chain fires Sunday LOCAL, so
    # the stored ``run_at_utc`` legitimately lands on the PRIOR UTC date
    # (Sun 04:15 ICT = Sat 21:15Z). Pinned behaviorally by the
    # UTC-date-boundary tests in tests/unit/test_maintenance_schedule_knobs.py.
    today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_ahead = (target_py_weekday - today.weekday()) % 7
    if days_ahead == 0 and not allow_today:
        days_ahead = 7
    candidate_day = today + timedelta(days=days_ahead)
    local_naive = datetime(
        candidate_day.year,
        candidate_day.month,
        candidate_day.day,
        minute_of_day // 60,
        minute_of_day % 60,
    )
    if allow_today and local_naive <= local_now.replace(tzinfo=None):
        # Today's randomly chosen minute is already past ⇒ next week.
        # The comparison is wall-clock-to-wall-clock: the candidate is a
        # NAIVE local datetime (the canonical anchor takes a naive local
        # value), so it is compared against the naive local "now".
        local_naive += timedelta(days=7)

    # The canonical tz anchor.  The (aware, warning) TUPLE is consumed —
    # the warning is surfaced, never discarded (BR-5).
    anchored, warning = anchor_local_to_utc(local_naive, tz)
    warnings: list[str] = []
    if warning:
        warnings.append(warning)
        logger.warning(
            "[CentralMaintenance] tz anchor warning for %s: %s",
            local_naive.isoformat(),
            warning,
        )

    instant_utc = anchored.astimezone(_UTC)
    back_local = instant_utc.astimezone(tz)
    back_minute = back_local.hour * 60 + back_local.minute
    if not (knobs.start_minute <= back_minute < knobs.end_minute):
        # BR-5 fail-closed: the anchored instant (possibly shifted by a
        # DST gap) left the window — refuse to arm instead of silently
        # firing outside the operator's window.
        raise MaintenanceKnobError(
            f"anchored instant {instant_utc.isoformat()} resolves to "
            f"{back_local.strftime('%H:%M')} local in {knobs.timezone}, "
            f"outside [{knobs.window_start}, {knobs.window_end}) — "
            "refusing to arm (fail-closed)"
        )

    return PickedFire(
        instant_utc=instant_utc,
        local_time=back_local.strftime("%H:%M"),
        weekday=knobs.weekday,
        window_start=knobs.window_start,
        window_end=knobs.window_end,
        timezone=knobs.timezone,
        warnings=tuple(warnings),
    )


__all__ = [
    "DEFAULT_TIMEZONE",
    "DEFAULT_WEEKDAY",
    "DEFAULT_WINDOW_END",
    "DEFAULT_WINDOW_START",
    "ENV_TIMEZONE",
    "ENV_WEEKDAY",
    "ENV_WINDOW_END",
    "ENV_WINDOW_START",
    "MaintenanceKnobError",
    "MaintenanceKnobs",
    "PickedFire",
    "WEEKDAY_NAMES",
    "pick_next_fire_instant",
    "read_maintenance_knobs",
]
