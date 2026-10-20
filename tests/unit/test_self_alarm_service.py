"""Unit tests for the PURE self-alarm helpers (ari-tools Phase 2).

``daemon/services/self_alarm_service.py`` is the pure half of the
self-alarm wrapper: grammar, fire plan, the >= 30-minute floor, the
message contract and the board-id shape. No tool imports, no engine
calls, no DB ⇒ every test here is hermetic and instant.

Pinned contract (phase2-plan D3 / D7 / D9 / D12):

* the grammar is SMALL and prefix-disambiguated — absolute ISO, relative
  duration, ``daily HH:MM``, ``weekly D HH:MM`` (0=Sunday..6=Saturday),
  raw cron; ``every 1h`` is REJECTED with a raw-cron hint;
* the fire plan is computed with the SAME public primitives the engine
  uses (``anchor_local_to_utc`` / ``compute_next_cron_fire``), so the
  wrapper's first fire IS the engine's first fire;
* the floor compares SECONDS against the absolute instant and is
  FAMILY-AWARE — the bash-sleep hint is for one-shots ONLY and BOTH
  texts print the computed next fire;
* the timezone resolution is PINNED to
  ``scheduling_service._resolve_schedule_timezone`` (parity — chain
  drift is caught, not guessed);
* the message is the D7 template VERBATIM.

SAFETY FENCE: pure unit tests — no daemon boot, no DB, no ambient
``POSTGRES_*``. The one engine touch is the REAL
``scheduling_service._compute_next_run`` (a pure function) plus the
tz-resolution parity check, both hermetic.
"""

from __future__ import annotations


from datetime import datetime, timedelta, timezone as _stdlib_timezone
from zoneinfo import ZoneInfo

import pytest

from daemon.services.self_alarm_service import (
    BOARD_ITEM_RE,
    LABEL_NAMESPACE,
    MAX_LABEL_INPUT,
    MIN_HORIZON_SECONDS,
    SelfAlarmInputError,
    compose_alarm_message,
    effective_label_for,
    enforce_min_horizon,
    parse_when_or_every,
    resolve_fire_plan,
    validate_board_id,
    validate_label_input,
)

UTC = _stdlib_timezone.utc
HCM = ZoneInfo("Asia/Ho_Chi_Minh")


# ---------------------------------------------------------------------------
# Grammar (D3)
# ---------------------------------------------------------------------------


class TestGrammar:
    @pytest.mark.parametrize(
        "text,delta",
        [
            ("45m", 2700),
            ("in 45m", 2700),
            ("2h", 7200),
            ("in 2h", 7200),
            ("90s", 90),
            ("1d", 86400),
            # Compound durations are accepted (the grammar table lists
            # ``1d30m``) — the segments are summed.
            ("1d30m", 86400 + 1800),
            ("1 day 30 m", 86400 + 1800),
        ],
    )
    def test_relative_forms(self, text: str, delta: int) -> None:
        parsed = parse_when_or_every(text)
        assert parsed.family == "once"
        assert parsed.kind == "relative"
        assert parsed.delta_seconds == pytest.approx(delta)

    def test_absolute_aware(self) -> None:
        parsed = parse_when_or_every("2026-10-20T09:00:00+07:00")
        assert parsed.family == "once"
        assert parsed.kind == "absolute"
        assert parsed.when is not None
        assert parsed.when.startswith("2026-10-20T09:00:00")

    def test_absolute_naive(self) -> None:
        parsed = parse_when_or_every("2026-10-20T09:00:00")
        assert parsed.family == "once"
        assert parsed.kind == "absolute"

    def test_absolute_zulu(self) -> None:
        parsed = parse_when_or_every("2026-10-20T02:00:00Z")
        assert parsed.family == "once"
        assert parsed.kind == "absolute"

    def test_daily(self) -> None:
        parsed = parse_when_or_every("daily 06:30")
        assert parsed.family == "daily"
        assert parsed.when == "06:30"
        assert parsed.weekday is None
        assert parsed.is_recurring

    def test_weekly(self) -> None:
        parsed = parse_when_or_every("weekly 1 06:30")
        assert parsed.family == "weekly"
        assert parsed.when == "06:30"
        assert parsed.weekday == 1
        assert parsed.is_recurring

    def test_cron(self) -> None:
        parsed = parse_when_or_every("cron 0 9 * * *")
        assert parsed.family == "cron"
        assert parsed.cron_expression == "0 9 * * *"
        assert parsed.when is None
        assert parsed.is_recurring

    def test_every_is_rejected_with_the_raw_cron_hint(self) -> None:
        """``every 1h`` has NO engine interval variant — the hint points
        at a raw cron whose period clears the floor."""
        with pytest.raises(SelfAlarmInputError) as excinfo:
            parse_when_or_every("every 1h")
        message = str(excinfo.value)
        assert "cron 0 */2 * * *" in message
        # The hint must NEVER point at a <= 30-minute period.
        assert "*/30" not in message
        assert "*/60" not in message

    @pytest.mark.parametrize("text", ["", "tomorrow-ish", "next tuesday", "6:30", "90"])
    def test_garbage_is_rejected_with_the_accepted_forms(self, text: str) -> None:
        with pytest.raises(SelfAlarmInputError) as excinfo:
            parse_when_or_every(text)
        message = str(excinfo.value)
        assert "daily 06:30" in message
        assert "weekly 1 06:30" in message
        assert "cron 0 9 * * *" in message

    def test_invalid_cron_is_rejected(self) -> None:
        with pytest.raises(SelfAlarmInputError) as excinfo:
            parse_when_or_every("cron not a cron")
        assert "invalid cron expression" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Label + board id (D4 / D12)
# ---------------------------------------------------------------------------


class TestLabelAndBoardId:
    def test_label_max_length_is_115(self) -> None:
        assert MAX_LABEL_INPUT == 115
        assert validate_label_input("x" * 115) == "x" * 115

    def test_116_char_label_is_rejected(self) -> None:
        with pytest.raises(SelfAlarmInputError) as excinfo:
            validate_label_input("x" * 116)
        assert "115" in str(excinfo.value)

    def test_prefixed_label_is_rejected(self) -> None:
        with pytest.raises(SelfAlarmInputError) as excinfo:
            validate_label_input("self-alarm:deploy-check")
        assert "added automatically" in str(excinfo.value)

    def test_empty_label_is_rejected(self) -> None:
        with pytest.raises(SelfAlarmInputError):
            validate_label_input("   ")

    def test_effective_label_is_namespaced(self) -> None:
        assert LABEL_NAMESPACE == "self-alarm:"
        assert effective_label_for("deploy-check") == "self-alarm:deploy-check"
        # The worst case fits the engine's 128-char cap: prefix + input
        # + the "-5" retry suffix.
        assert len(f"{LABEL_NAMESPACE}{'x' * MAX_LABEL_INPUT}-5") == 128

    def test_board_id_accepts_the_phase1_shape(self) -> None:
        assert validate_board_id("wi-1a2b3c4d") == "wi-1a2b3c4d"
        assert BOARD_ITEM_RE.match("wi-1a2b3c4d")

    @pytest.mark.parametrize(
        "value", ["wi-1A2B3C4D", "wi-123", "1a2b3c4d", "wi-1a2b3c4dz", "board-1"]
    )
    def test_board_id_rejects_everything_else(self, value: str) -> None:
        with pytest.raises(SelfAlarmInputError):
            validate_board_id(value)

    def test_board_id_none_means_no_link(self) -> None:
        assert validate_board_id(None) is None


# ---------------------------------------------------------------------------
# The fire plan (D3)
# ---------------------------------------------------------------------------


class TestFirePlan:
    def test_absolute_naive_is_anchored_in_the_resolved_zone(self) -> None:
        parsed = parse_when_or_every("2026-10-20T09:00:00")
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = resolve_fire_plan(parsed, zone=HCM, now=now)
        assert plan.recurrence == "once"
        assert plan.first_fire_utc == datetime(2026, 10, 20, 2, 0, tzinfo=UTC)
        assert plan.first_fire_local.isoformat() == "2026-10-20T09:00:00+07:00"
        # The engine receives an OFFSET-AWARE instant.
        assert plan.when is not None
        assert plan.when.endswith("+07:00")

    def test_relative_resolves_from_now(self) -> None:
        parsed = parse_when_or_every("45m")
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = resolve_fire_plan(parsed, zone=HCM, now=now)
        assert plan.first_fire_utc == now + timedelta(seconds=2700)

    def test_daily_builds_the_engine_cron(self) -> None:
        parsed = parse_when_or_every("daily 06:30")
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = resolve_fire_plan(parsed, zone=HCM, now=now)
        # The engine builds ``f"{minute} {hour} * * *"`` for daily.
        assert plan.cron_for_next_fire == "30 6 * * *"
        assert plan.first_fire_local.isoformat() == "2026-10-20T06:30:00+07:00"

    def test_weekly_builds_the_engine_cron(self) -> None:
        parsed = parse_when_or_every("weekly 1 06:30")
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = resolve_fire_plan(parsed, zone=HCM, now=now)
        assert plan.cron_for_next_fire == "30 6 * * 1"
        assert plan.weekday == 1

    def test_weekly_reuse_computes_with_the_STORED_dow(self) -> None:
        """The update branch rewrites only the minute/hour of the stored
        cron and PRESERVES the stored DOW ⇒ the first fire must be
        computed with the STORED DOW."""
        parsed = parse_when_or_every("weekly 3 06:30")
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = resolve_fire_plan(
            parsed, zone=HCM, now=now, stored_weekday=1
        )
        assert plan.cron_for_next_fire == "30 6 * * 1"
        assert plan.weekday == 1

    def test_cron_reuse_uses_the_STORED_expression(self) -> None:
        parsed = parse_when_or_every("cron 0 9 * * *")
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = resolve_fire_plan(
            parsed, zone=HCM, now=now, stored_cron_expression="0 9 * * *"
        )
        assert plan.cron_expression == "0 9 * * *"
        assert plan.when is None
        # now = 2026-10-19T07:00+07:00 ⇒ the next 09:00 fire is TODAY.
        assert plan.first_fire_local.isoformat() == "2026-10-19T09:00:00+07:00"


# ---------------------------------------------------------------------------
# The floor (D9)
# ---------------------------------------------------------------------------


class TestMinHorizon:
    def _plan(self, text: str, *, now: datetime):
        parsed = parse_when_or_every(text)
        return resolve_fire_plan(parsed, zone=HCM, now=now)

    def test_boundary_29_minutes_is_rejected(self) -> None:
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = self._plan("in 29m", now=now)
        with pytest.raises(SelfAlarmInputError) as excinfo:
            enforce_min_horizon(plan, now=now)
        assert "minimum horizon of 30 minutes" in str(excinfo.value)

    def test_boundary_30_minutes_is_accepted(self) -> None:
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = self._plan("in 30m", now=now)
        assert enforce_min_horizon(plan, now=now) is None

    def test_boundary_30_minutes_plus_one_second_is_accepted(self) -> None:
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = self._plan("in 30m", now=now)
        plan = type(plan)(
            recurrence=plan.recurrence,
            when=plan.when,
            weekday=plan.weekday,
            cron_expression=plan.cron_expression,
            cron_for_next_fire=plan.cron_for_next_fire,
            first_fire_utc=plan.first_fire_utc + timedelta(seconds=1),
            first_fire_local=plan.first_fire_local + timedelta(seconds=1),
            zone=plan.zone,
            schedule_text=plan.schedule_text,
        )
        assert enforce_min_horizon(plan, now=now) is None

    def test_past_due_absolute_is_rejected(self) -> None:
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = self._plan("2026-10-18T09:00:00+00:00", now=now)
        with pytest.raises(SelfAlarmInputError):
            enforce_min_horizon(plan, now=now)

    def test_one_shot_text_carries_the_bash_sleep_hint(self) -> None:
        now = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)
        plan = self._plan("in 12m", now=now)
        with pytest.raises(SelfAlarmInputError) as excinfo:
            enforce_min_horizon(plan, now=now)
        message = str(excinfo.value)
        assert "use bash with `sleep <seconds>`" in message
        # The computed next fire is ALWAYS printed.
        assert plan.first_fire_local.isoformat() in message

    def test_recurring_text_has_no_bash_sleep_hint_and_names_the_fire(self) -> None:
        now = datetime(2026, 10, 20, 6, 15, tzinfo=HCM)
        plan = self._plan("daily 06:30", now=now)
        with pytest.raises(SelfAlarmInputError) as excinfo:
            enforce_min_horizon(plan, now=now)
        message = str(excinfo.value)
        assert "use bash with `sleep <seconds>`" not in message
        assert "`daily 06:30`" in message
        assert plan.first_fire_local.isoformat() in message
        # The FOLLOWING occurrence is offered as the escape hatch.
        assert "2026-10-21T06:30:00+07:00" in message

    def test_short_period_cron_is_unarmable(self) -> None:
        """A period of 30 minutes or less can never satisfy the floor —
        its first fire is at most ONE period away.

        NOTE the knife-edge: the floor is ``>= 1800 s`` (the plan's own
        threshold), so a 30-minute period armed EXACTLY on its boundary
        yields exactly 1800 s and is accepted. At every other instant it
        is rejected — so a 30-minute period is unusable in practice, and
        a STRICTLY shorter period is rejected unconditionally.
        """
        now = datetime(2026, 10, 20, 6, 1, 0, tzinfo=HCM)
        plan = self._plan("cron */30 * * * *", now=now)
        with pytest.raises(SelfAlarmInputError) as excinfo:
            enforce_min_horizon(plan, now=now)
        assert "minimum horizon of 30 minutes" in str(excinfo.value)

    def test_a_strictly_shorter_period_is_always_rejected(self) -> None:
        for minute in (0, 1, 7, 29):
            now = datetime(2026, 10, 20, 6, minute, 0, tzinfo=HCM)
            plan = self._plan("cron */15 * * * *", now=now)
            with pytest.raises(SelfAlarmInputError):
                enforce_min_horizon(plan, now=now)

    def test_documented_cron_example_is_armable(self) -> None:
        now = datetime(2026, 10, 20, 0, 0, 0, tzinfo=HCM)
        plan = self._plan("cron 0 9 * * *", now=now)
        assert enforce_min_horizon(plan, now=now) is None

    def test_floor_threshold_constant(self) -> None:
        assert MIN_HORIZON_SECONDS == 1800


# ---------------------------------------------------------------------------
# The message contract (D7)
# ---------------------------------------------------------------------------


class TestMessageComposition:
    def _message(self, board_item: str | None = None) -> str:
        return compose_alarm_message(
            effective_label="self-alarm:deploy-check",
            first_fire_local=datetime(2026, 10, 20, 9, 0, tzinfo=HCM),
            first_fire_utc=datetime(2026, 10, 20, 2, 0, tzinfo=UTC),
            armed_at_utc=datetime(2026, 10, 19, 19, 0, tzinfo=UTC),
            recurrence="once",
            timezone_name="Asia/Ho_Chi_Minh",
            source_id="src-1",
            board_item=board_item,
            message_to_self="Check the deploy status.",
        )

    def test_the_template_is_verbatim(self) -> None:
        message = self._message()
        assert message.splitlines() == [
            "[self-alarm] self-alarm:deploy-check",
            "Scheduled fire: 2026-10-20T09:00:00+07:00  (2026-10-20T02:00:00+00:00)",
            "Armed at: 2026-10-19T19:00:00+00:00   Schedule: once | tz Asia/Ho_Chi_Minh | id src-1",
            "You are a fresh instance with no memory of this request; everything you need is in this message.",
            "Action: Check the deploy status.",
        ]

    def test_the_board_line_carries_the_id_and_the_instruction(self) -> None:
        message = self._message(board_item="wi-1a2b3c4d")
        assert "Board item: wi-1a2b3c4d" in message
        assert "board_get" in message

    def test_the_action_text_is_verbatim(self) -> None:
        message = compose_alarm_message(
            effective_label="self-alarm:x",
            first_fire_local=datetime(2026, 10, 20, 9, 0, tzinfo=HCM),
            first_fire_utc=datetime(2026, 10, 20, 2, 0, tzinfo=UTC),
            armed_at_utc=datetime(2026, 10, 19, 19, 0, tzinfo=UTC),
            recurrence="once",
            timezone_name="Asia/Ho_Chi_Minh",
            source_id="src-1",
            board_item=None,
            message_to_self="Do EXACTLY this — with the odd punctuation.",
        )
        assert message.endswith("Action: Do EXACTLY this — with the odd punctuation.")

    def test_no_source_id_means_no_id_segment(self) -> None:
        """The source id does not exist yet on the create path — the
        effective label in the header is the correlation handle."""
        message = compose_alarm_message(
            effective_label="self-alarm:deploy-check",
            first_fire_local=datetime(2026, 10, 20, 9, 0, tzinfo=HCM),
            first_fire_utc=datetime(2026, 10, 20, 2, 0, tzinfo=UTC),
            armed_at_utc=datetime(2026, 10, 19, 19, 0, tzinfo=UTC),
            recurrence="once",
            timezone_name="Asia/Ho_Chi_Minh",
            source_id="",
            board_item=None,
            message_to_self="Act.",
        )
        assert " | id " not in message
        assert "Schedule: once | tz Asia/Ho_Chi_Minh" in message


# ---------------------------------------------------------------------------
# The tz chain — PARITY with the engine (D3)
# ---------------------------------------------------------------------------


class TestTimezoneParity:
    """The wrapper's resolution must equal the ENGINE's resolution.

    The engine's ``_resolve_schedule_timezone`` is the reference
    (``daemon/services/scheduling_service.py:362-395``). The wrapper
    resolves the zone itself (it hands the engine an explicit zone), so
    a drift between the two would silently arm the alarm at the wrong
    instant — this parity test is what catches it.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "explicit,user_tz,env_default",
        [
            (None, None, None),
            (None, "Europe/Berlin", None),
            (None, None, "America/New_York"),
            (None, "Europe/Berlin", "America/New_York"),
            ("Asia/Tokyo", None, None),
            ("Asia/Tokyo", "Europe/Berlin", "America/New_York"),
        ],
    )
    async def test_wrapper_matches_the_engine(
        self,
        monkeypatch: pytest.MonkeyPatch,
        explicit,
        user_tz,
        env_default,
    ) -> None:
        from daemon.services import scheduling_service
        from daemon.tools.self_alarm import _resolve_wrapper_timezone

        if env_default is None:
            monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", raising=False)
        else:
            monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", env_default)

        def _fake_user_tz(_repo):
            # SYNCHRONOUS on purpose — the real helper is sync and is
            # called through ``asyncio.to_thread`` on BOTH sides.
            return user_tz

        monkeypatch.setattr(
            "daemon.services.user_timezone_utils.get_user_timezone_preference",
            _fake_user_tz,
        )
        # The engine reads the user setting through the same module-level
        # function (imported inside the function body) ⇒ one patch serves
        # both sides.
        previous_repo = scheduling_service._deps.project_repository
        scheduling_service.configure(project_repository=object())
        try:
            engine_zone, engine_name, engine_warning = (
                await scheduling_service._resolve_schedule_timezone(explicit)
            )
            wrapper_zone, wrapper_name, wrapper_warning = await _resolve_wrapper_timezone(
                object(), explicit
            )
        finally:
            scheduling_service._deps.project_repository = previous_repo

        assert wrapper_name == engine_name
        assert wrapper_warning == engine_warning
        assert wrapper_zone.utcoffset(
            datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        ) == engine_zone.utcoffset(datetime(2026, 1, 15, 12, 0, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_the_wrapper_reads_the_env_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The env rung is live: the wrapper honours
        ``ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE``."""
        from daemon.tools.self_alarm import _resolve_wrapper_timezone

        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", "America/New_York")
        _zone, name, _warning = await _resolve_wrapper_timezone(None)
        assert name == "America/New_York"


# ---------------------------------------------------------------------------
# Engine parity — the wrapper's first fire IS the engine's first fire
# ---------------------------------------------------------------------------


class TestEngineFireParity:
    """The wrapper computes the first fire with the SAME helper the
    engine's ``_compute_next_run`` uses — this test pins the two
    together against the REAL engine function (a pure function — no DB).
    """

    @pytest.mark.parametrize(
        "text,config_cron",
        [
            ("daily 06:30", "30 6 * * *"),
            ("weekly 1 06:30", "30 6 * * 1"),
            ("cron 0 9 * * *", "0 9 * * *"),
        ],
    )
    def test_wrapper_first_fire_equals_the_engine_echo(self, text: str, config_cron: str) -> None:
        from daemon.services.scheduling_service import _compute_next_run

        # The engine computes its next run from ``datetime.now(zone)`` —
        # the parity check therefore uses the REAL now on the wrapper side
        # too, so both sides start from the same instant.
        now = datetime.now(HCM)
        parsed = parse_when_or_every(text)
        plan = resolve_fire_plan(parsed, zone=HCM, now=now)
        config = {
            "timezone": "Asia/Ho_Chi_Minh",
            "schedule": config_cron,
        }
        next_local, next_utc, _warning = _compute_next_run(config)
        assert next_local == plan.first_fire_local.isoformat()
        assert next_utc == plan.first_fire_utc.isoformat()

    def test_the_pure_module_imports_no_tool_engine_or_db(self) -> None:
        """Structural hermeticity proof (D2): the pure module imports NO
        tool module, NO engine module and NO DB layer — so every test in
        this file is hermetic by construction (no daemon boot, no DB, no
        ambient ``POSTGRES_*``)."""
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        source = (repo_root / "daemon" / "services" / "self_alarm_service.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(name.startswith("daemon.tools") for name in imported), imported
        assert "daemon.services.scheduling_service" not in imported
        assert "daemon.services.board_service" not in imported
        assert not any(
            name.startswith("sqlmodel") or name.startswith("sqlalchemy") for name in imported
        ), imported
