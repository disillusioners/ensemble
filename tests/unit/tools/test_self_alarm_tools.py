"""Unit tests for the agent-facing ``self_alarm`` tool (ari-tools Phase 2).

Drives ``create_self_alarm_tools`` (``daemon/tools/self_alarm.py``) with
the REAL ``daemon.services.scheduling_service`` module behind a thin
SPY — the tool is exercised against the actual engine, with an in-memory
repository double and a registry double (no DB, no daemon boot, no
ambient ``POSTGRES_*``).

MOCK DISCIPLINE (the F1 lesson) — the mocked shapes ARE the real ones:

* the service double is a SPY over the REAL engine, so every response
  the tool sees is the engine's own object: the real
  ``ScheduleCreateResponse`` / ``ScheduleUpdateResponse``, the engine's
  ``status`` semantics (the PRE-update status unless a successful adapter
  start triggered the engine's re-read) and the engine's own
  ``next_run_at_utc`` echo;
* the only value the spy corrupts is the ``next_run_at_utc`` echo, and
  ONLY where the test needs a forced assertion failure (the fallback
  path) — the corruption is applied to the REAL response object, not to
  a hand-written shape;
* the pause semantics are the ENGINE's: a pause response reports
  ``paused is True`` while its ``status`` stays the STALE pre-pause
  value, and the row's ``stopped`` state is a REPO-side fact (proven by
  re-reading the row) — the wrapper asserts ``paused is True`` ONLY.

Pinned contract (phase2-plan D4 / D9 / D10 / D11):

* the ari-only runtime gate, the ``project_id`` resolve-FIRST ordering,
  the >= 30-minute floor on BOTH branches, the family-aware rejection
  text, the compatibility gate BEFORE any mutation, the update payload
  shape (``timezone`` + ``paused=False`` mandatory, NO ``when`` for a
  stored cron row), the fallback + neutralization, and the "never cancel
  the duplicate loser" rule.

SAFETY FENCE: pure unit tests — an in-memory repository double and a
registry double only; the daemon is NEVER booted and no DB is ever
touched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone as _stdlib_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from daemon.tools.self_alarm import create_self_alarm_tools

CALLER_INSTANCE = "inst-caller-1"
CALLER_AGENT = "ari"
OTHER_AGENT = "watcher"
TZ_NAME = "Asia/Ho_Chi_Minh"
TZ = ZoneInfo(TZ_NAME)
PROJECT_ID = "proj-1"
EFFECTIVE = "self-alarm:deploy-check"


# ---------------------------------------------------------------------------
# The in-memory doubles
# ---------------------------------------------------------------------------


class FakeRow:
    """A minimal stand-in for a ``SourceConfig`` row."""

    def __init__(self, **overrides):
        base = dict(
            source_id="src-1",
            name=EFFECTIVE,
            source_type="scheduler",
            status="running",
            enabled=True,
            autostart=True,
            config={
                "agent": "ari",
                "message": "old message",
                "timezone": TZ_NAME,
                "recurrence": "once",
                "local_time": "2026-10-20T09:00:00+07:00",
                "instance_mode": "new_instance",
                "run_at": "2026-10-20T02:00:00+00:00",
            },
        )
        base.update(overrides)
        for key, value in base.items():
            setattr(self, key, value)


class FakeRepo:
    """An in-memory repository double.

    It backs BOTH the tool's read-only label probes AND the real
    scheduling service, so the two sides see one coherent state.
    """

    def __init__(self, rows=None) -> None:
        self.rows: list[FakeRow] = list(rows or [])
        self.status_writes: list[tuple[str, str]] = []
        #: The status a newly created row carries.
        self.created_row_status = "running"
        #: The status the registry double writes when the adapter starts
        #: (the real registry writes 'running'; 'starting' is the
        #: transient status the plan counts as armed).
        self.started_status = "running"
        #: A hook run right after a row is created (used to simulate a
        #: duplicate-label race).
        self.on_create = None
        #: When True, the FIRST name lookup answers "not found" (used to
        #: stage a duplicate that only appears after the create).
        self.hide_first_name_probe = False
        self._probes = 0

    # ── the repository surface the service + the tool use ───────────────
    def get_source_config(self, source_id):
        for row in self.rows:
            if row.source_id == source_id:
                return row
        return None

    def get_source_config_by_name(self, name):
        self._probes += 1
        if self.hide_first_name_probe and self._probes == 1:
            return None
        for row in self.rows:
            if row.name == name:
                return row
        return None

    def create_source_config(
        self,
        source_type,
        name,
        config,
        credentials=None,
        enabled=True,
        autostart=True,
        source_id=None,
    ):
        if self.on_create is not None:
            self.on_create(self)
        row = FakeRow(
            source_id=source_id or f"src-created-{len(self.rows) + 1}",
            name=name,
            status=self.created_row_status,
            enabled=enabled,
            config=dict(config),
        )
        self.rows.append(row)
        return row

    def update_source_config(
        self,
        source_id,
        source_type=None,
        name=None,
        config=None,
        credentials=None,
        enabled=None,
        autostart=None,
    ):
        row = self.get_source_config(source_id)
        if row is None:
            return None
        if config is not None:
            row.config = dict(config)
        if enabled is not None:
            row.enabled = enabled
        if name is not None:
            row.name = name
        # The REAL repository returns a FRESHLY LOADED object (its own
        # session), so the later ``update_source_status`` write — which
        # runs in ANOTHER session — does NOT alias the returned object.
        # Returning the live row here would make the engine's
        # ``updated.status`` read the POST-status-write value ('stopped')
        # instead of the pre-update status — a mock-fidelity artefact
        # that does not exist in the real repository.
        snapshot = FakeRow(
            source_id=row.source_id,
            name=row.name,
            source_type=row.source_type,
            status=row.status,
            enabled=row.enabled,
            autostart=row.autostart,
            config=dict(row.config),
        )
        return snapshot

    def update_source_status(self, source_id, status, error_message=None):
        row = self.get_source_config(source_id)
        if row is not None:
            row.status = status
        self.status_writes.append((source_id, status))
        return row


class FakeAdapter:
    """A stand-in for the scheduler adapter."""

    source_id = "src-1"

    async def stop(self) -> None:
        return None


class FakeRegistry:
    """A registry double.

    The REAL registry writes ``running`` onto the row when the adapter
    starts (``daemon/sources/registry.py``) — the double does the same,
    because the ENGINE's update path re-reads the row ONLY when the
    adapter start succeeded (``scheduling_service.py:1050-1052``) and the
    wrapper's response assertions depend on that re-read.

    ``fail_rebuild=True`` reproduces the engine's "the adapter could not
    be rebuilt" branch: ``started`` stays False ⇒ no re-read ⇒ the
    response carries the PRE-update status.
    """

    def __init__(self, repo: FakeRepo | None = None, *, fail_rebuild: bool = False) -> None:
        self.repo = repo
        self.fail_rebuild = fail_rebuild
        self.registered: list = []

    def get(self, source_id):
        return None

    def unregister(self, source_id) -> None:
        return None

    async def _create_adapter_from_config(self, row):
        if self.fail_rebuild:
            raise RuntimeError("no adapter in the test double")
        return FakeAdapter()

    def register(self, adapter) -> None:
        self.registered.append(adapter)

    async def start_adapter(self, source_id):
        if self.fail_rebuild:
            return False
        if self.repo is not None:
            row = self.repo.get_source_config(source_id)
            if row is not None:
                row.status = self.repo.started_status
        return True


class SpyService:
    """A thin spy over the REAL scheduling service.

    Every response the tool sees is the engine's own object — the
    response SHAPES are therefore never hand-written. The only
    corruption is ``bad_echo``, applied to the REAL response object and
    only to the update calls that are NOT a neutralization pause.
    """

    def __init__(self, inner, repo: FakeRepo) -> None:
        self.inner = inner
        self.repo = repo
        self.create_calls: list = []
        self.update_calls: list = []
        self.bad_echo = False
        self.raise_on_create: Exception | None = None
        self.raise_on_update: Exception | None = None

    async def create_schedule(self, payload, **kwargs):
        self.create_calls.append((payload, kwargs))
        if self.raise_on_create is not None:
            raise self.raise_on_create
        return await self.inner.create_schedule(payload, **kwargs)

    async def update_schedule(self, source_id, payload):
        self.update_calls.append((source_id, payload))
        if self.raise_on_update is not None:
            raise self.raise_on_update
        response = await self.inner.update_schedule(source_id, payload)
        if self.bad_echo and not (isinstance(payload, dict) and payload.get("paused") is True):
            response = response.model_copy(
                update={
                    "next_run_at_utc": "2020-01-01T00:00:00+00:00",
                    "next_run_at_local": "2020-01-01T00:00:00+00:00",
                }
            )
        return response


def _future_iso(hours: float = 2.0) -> str:
    """An absolute instant ``hours`` from now (offset-aware)."""
    return (datetime.now(TZ) + timedelta(hours=hours)).isoformat()


# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def real_service():
    """The REAL scheduling service, with its deps saved and restored."""
    from daemon.services import scheduling_service

    previous = (
        scheduling_service._deps.source_repo,
        scheduling_service._deps.source_registry,
        scheduling_service._deps.project_repository,
    )
    yield scheduling_service
    (
        scheduling_service._deps.source_repo,
        scheduling_service._deps.source_registry,
        scheduling_service._deps.project_repository,
    ) = previous


@pytest.fixture
def tool_env(monkeypatch: pytest.MonkeyPatch, real_service):
    """The tool bound to the real engine (spied) + the in-memory repo."""
    monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", TZ_NAME)
    repo = FakeRepo()
    real_service.configure(source_repo=repo, source_registry=FakeRegistry(repo))
    service = SpyService(real_service, repo)
    instance_repo = MagicMock()
    instance_repo.get = MagicMock(return_value=SimpleNamespace(project_id=PROJECT_ID))
    tools = create_self_alarm_tools(
        service,
        source_repo=repo,
        instance_repository=instance_repo,
        project_repository=None,
        current_instance_id=CALLER_INSTANCE,
        agent_id=CALLER_AGENT,
    )
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"self_alarm"}
    return by_name, service, repo, instance_repo


async def _call(tools, **kwargs):
    """Invoke the tool through the house convention."""
    return await tools["self_alarm"].coroutine(**kwargs)


# ---------------------------------------------------------------------------
# The floor (D9) — BOTH branches
# ---------------------------------------------------------------------------


class TestFloorBoundary:
    @pytest.mark.asyncio
    async def test_29_minutes_is_rejected_with_no_engine_call(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 29m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR: ")
        assert "minimum horizon of 30 minutes" in result
        assert service.create_calls == []
        assert service.update_calls == []
        assert repo.rows == []

    @pytest.mark.asyncio
    async def test_30_minutes_is_accepted(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 30m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert len(service.create_calls) == 1

    @pytest.mark.asyncio
    async def test_30_minutes_plus_one_second_is_accepted(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 30m1s",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["ok"] is True
        assert len(service.create_calls) == 1

    @pytest.mark.asyncio
    async def test_reuse_29_minutes_is_rejected_with_ZERO_update_calls(
        self, tool_env
    ) -> None:
        """The floor is BRANCH-INDEPENDENT — a below-floor reuse never
        mutates the stored row."""
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 29m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "minimum horizon of 30 minutes" in result
        assert service.update_calls == []
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_reuse_30_minutes_is_accepted(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 30m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["reused"] is True
        assert len(service.update_calls) == 1
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_reuse_30_minutes_plus_one_second_is_accepted(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 30m1s",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["reused"] is True
        assert len(service.update_calls) == 1
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_the_one_shot_text_carries_the_bash_sleep_hint(self, tool_env) -> None:
        tools, _service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 12m",
            message_to_self="Do the thing.",
        )
        assert "use bash with `sleep <seconds>`" in result

    @pytest.mark.asyncio
    async def test_the_recurring_text_names_the_next_fire_and_has_no_bash_hint(
        self, tool_env
    ) -> None:
        tools, _service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="cron */5 * * * *",
            message_to_self="Do the thing.",
        )
        assert "use bash with `sleep <seconds>`" not in result
        assert "minimum horizon of 30 minutes" in result
        assert "`cron */5 * * * *`" in result

    @pytest.mark.asyncio
    async def test_a_past_due_absolute_is_rejected(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        past = (datetime.now(TZ) - timedelta(hours=1)).isoformat()
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=past,
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "minimum horizon of 30 minutes" in result
        assert service.create_calls == []


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_a_116_char_label_is_rejected(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="x" * 116,
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "115" in result
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_a_prefixed_label_is_rejected(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="self-alarm:deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "added automatically" in result
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_an_invalid_board_id_is_rejected(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
            link_board_item="not-a-board-id",
        )
        assert isinstance(result, str)
        assert "wi-" in result
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_every_1h_is_rejected_with_the_raw_cron_hint(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="every 1h",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "cron 0 */2 * * *" in result
        assert "*/30" not in result
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_an_empty_message_is_rejected(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="   ",
        )
        assert isinstance(result, str)
        assert "message_to_self" in result
        assert service.create_calls == []


# ---------------------------------------------------------------------------
# The three access layers + the D11 ordering
# ---------------------------------------------------------------------------


class TestAccessAndOrdering:
    @pytest.mark.asyncio
    async def test_a_non_ari_caller_is_refused_with_no_engine_call(
        self, real_service
    ) -> None:
        repo = FakeRepo()
        real_service.configure(source_repo=repo, source_registry=FakeRegistry(repo))
        service = SpyService(real_service, repo)
        instance_repo = MagicMock()
        tools = create_self_alarm_tools(
            service,
            source_repo=repo,
            instance_repository=instance_repo,
            current_instance_id=CALLER_INSTANCE,
            agent_id=OTHER_AGENT,
        )
        result = await _call(
            {t.name: t for t in tools},
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR: ")
        assert "ari" in result
        assert service.create_calls == []
        assert repo.rows == []

    @pytest.mark.asyncio
    async def test_project_id_resolution_failure_is_refused_before_any_label_lookup(
        self, tool_env
    ) -> None:
        """D11 — the project is resolved FIRST: a missing project must
        leave NO row and burn NO label."""
        tools, service, repo, instance_repo = tool_env
        instance_repo.get.return_value = SimpleNamespace(project_id=None)
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "cannot resolve project_id" in result
        # NO label lookup, NO engine write.
        assert repo._probes == 0
        assert service.create_calls == []
        assert service.update_calls == []

    @pytest.mark.asyncio
    async def test_a_raising_instance_repository_is_refused_not_swallowed(
        self, tool_env
    ) -> None:
        tools, service, repo, instance_repo = tool_env
        instance_repo.get.side_effect = RuntimeError("db down")
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "cannot resolve project_id" in result
        assert repo._probes == 0
        assert service.create_calls == []


# ---------------------------------------------------------------------------
# The create branch (D4 step 4a)
# ---------------------------------------------------------------------------


class TestCreateBranch:
    @pytest.mark.asyncio
    async def test_the_create_payload_is_family_shaped_and_mandatory_complete(
        self, tool_env
    ) -> None:
        tools, service, repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        payload = service.create_calls[0][0]
        assert payload["label"] == EFFECTIVE
        # ``agent`` is MANDATORY — the engine fail-fasts on an unknown one.
        assert payload["agent"] == "ari"
        assert payload["timezone"] == TZ_NAME
        assert payload["instance_mode"] == "new_instance"
        assert payload["project_id"] == PROJECT_ID
        assert payload["recurrence"] == "once"
        assert payload["weekday"] is None
        assert payload["cron_expression"] is None
        assert payload["when"].endswith("+07:00")
        # The row the ENGINE stored carries the same zone + agent.
        stored = repo.rows[0]
        assert stored.config["timezone"] == TZ_NAME
        assert stored.config["agent"] == "ari"
        assert stored.config["instance_mode"] == "new_instance"

    @pytest.mark.asyncio
    async def test_the_create_response_echoes_the_branch_scoped_status(
        self, tool_env
    ) -> None:
        tools, _service, repo, _instance_repo = tool_env
        # The adapter start leaves the row in the transient 'starting'
        # status — the plan counts it as ARMED.
        repo.started_status = "starting"
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        # 'starting' COUNTS AS ARMED on the create branch.
        assert result["status"] == "starting"
        assert result["reused"] is False
        assert result["label"] == EFFECTIVE

    @pytest.mark.asyncio
    async def test_a_daily_request_sends_the_hhmm_when(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        await _call(
            tools,
            label="daily-check",
            when_or_every="daily 23:50",
            message_to_self="Do the thing.",
        )
        payload = service.create_calls[0][0]
        assert payload["recurrence"] == "daily"
        assert payload["when"] == "23:50"
        assert payload["weekday"] is None
        # The ENGINE built the same cron the wrapper computed.
        assert repo.rows[0].config["schedule"] == "50 23 * * *"

    @pytest.mark.asyncio
    async def test_a_weekly_request_sends_the_weekday(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        await _call(
            tools,
            label="weekly-check",
            when_or_every="weekly 3 23:50",
            message_to_self="Do the thing.",
        )
        payload = service.create_calls[0][0]
        assert payload["recurrence"] == "weekly"
        assert payload["when"] == "23:50"
        assert payload["weekday"] == 3
        assert repo.rows[0].config["schedule"] == "50 23 * * 3"

    @pytest.mark.asyncio
    async def test_a_cron_request_sends_the_expression(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        await _call(
            tools,
            label="cron-check",
            when_or_every="cron 0 9 * * *",
            message_to_self="Do the thing.",
        )
        payload = service.create_calls[0][0]
        assert payload["recurrence"] == "cron"
        assert payload["cron_expression"] == "0 9 * * *"
        assert payload["when"] == ""
        assert repo.rows[0].config["schedule"] == "0 9 * * *"

    @pytest.mark.asyncio
    async def test_the_message_carries_every_required_element(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Check the deploy status.",
            link_board_item="wi-1a2b3c4d",
        )
        message = service.create_calls[0][0]["message"]
        assert message.startswith("[self-alarm] self-alarm:deploy-check")
        assert "Scheduled fire:" in message
        assert "Board item: wi-1a2b3c4d" in message
        assert "board_get" in message
        assert "fresh instance with no memory" in message
        assert message.endswith("Action: Check the deploy status.")

    @pytest.mark.asyncio
    async def test_lateness_cap_is_echoed_as_none_and_as_an_int(
        self, tool_env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tools, _service, _repo, _instance_repo = tool_env
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert result["lateness_cap_seconds"] is None

        monkeypatch.setenv("ENSEMBLE_SCHEDULING_ONE_SHOT_MAX_LATENESS_SECONDS", "900")
        result = await _call(
            tools,
            label="deploy-check-2",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert result["lateness_cap_seconds"] == 900

    @pytest.mark.asyncio
    async def test_a_duplicate_row_is_reported_never_cancelled(self, tool_env) -> None:
        """The name lookup returns only the FIRST match — a DIFFERENT id
        means another row shares the name. It is REPORTED, never
        cancelled (cancel burns the name and hides the evidence)."""
        tools, service, repo, _instance_repo = tool_env
        repo.hide_first_name_probe = True

        def _stage_racer(the_repo):
            # A racing create inserted the same name FIRST.
            the_repo.rows.append(
                FakeRow(source_id="src-other", name=EFFECTIVE, status="running")
            )

        repo.on_create = _stage_racer
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert result["duplicate_source_ids"] == ["src-other"]
        # NEVER cancelled — the service double exposes no cancel seam and
        # the update seam was never touched.
        assert service.update_calls == []


# ---------------------------------------------------------------------------
# The reuse branch (D4 step 4b)
# ---------------------------------------------------------------------------


class TestReuseBranch:
    @pytest.mark.asyncio
    async def test_the_update_payload_is_family_shaped(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        target = _future_iso(3.0)
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=target,
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["reused"] is True
        source_id, payload = service.update_calls[0]
        assert source_id == "src-1"
        # ``paused=False`` is MANDATORY — it is the resume branch.
        assert payload["paused"] is False
        # ``timezone`` is MANDATORY — the stored zone is refreshed only
        # when it is set.
        assert payload["timezone"] == TZ_NAME
        assert payload["when"] is not None
        assert "message" in payload
        # The ENGINE stored the resolved zone and the re-armed row.
        stored = repo.get_source_config("src-1")
        assert stored.enabled is True
        assert stored.config["timezone"] == TZ_NAME

    @pytest.mark.asyncio
    async def test_a_stored_cron_row_gets_NO_when_key(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(
            FakeRow(
                config={
                    "agent": "ari",
                    "message": "old",
                    "timezone": TZ_NAME,
                    "recurrence": "cron",
                    "schedule": "0 9 * * *",
                    "instance_mode": "new_instance",
                }
            )
        )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="cron 0 9 * * *",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        _source_id, payload = service.update_calls[0]
        # The engine RAISES on a ``when`` for a cron schedule ⇒ the
        # payload must NOT carry one.
        assert "when" not in payload
        assert payload["paused"] is False
        assert payload["timezone"] == TZ_NAME
        # The stored expression stayed authoritative.
        assert repo.get_source_config("src-1").config["schedule"] == "0 9 * * *"

    @pytest.mark.asyncio
    async def test_the_update_status_is_the_engine_returned_one(self, tool_env) -> None:
        """On the update branch the echoed status is the ENGINE's value
        (the re-read row status when the adapter start succeeded, the
        pre-update status otherwise) — the wrapper never re-derives one."""
        tools, _service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow(status="starting"))
        target = _future_iso(3.0)
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=target,
            message_to_self="Do the thing.",
        )
        assert result["status"] == repo.get_source_config("src-1").status
        assert result["status"] == "running"


# ---------------------------------------------------------------------------
# The compatibility gate (D4 step 2) — BEFORE any mutation
# ---------------------------------------------------------------------------


class TestCompatibilityGate:
    @pytest.mark.asyncio
    async def test_a_family_mismatch_creates_a_fresh_label_without_touching_the_row(
        self, tool_env
    ) -> None:
        tools, service, repo, _instance_repo = tool_env
        stored = FakeRow(
            config={"recurrence": "once", "timezone": TZ_NAME, "run_at": "2026-10-20T02:00:00+00:00"}
        )
        repo.rows.append(stored)
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="daily 23:50",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["fallback_reason"] == "recurrence_family_mismatch"
        assert result["label"] == "self-alarm:deploy-check-2"
        # The stored row is NEVER mutated.
        assert service.update_calls == []
        assert stored.config == {"recurrence": "once", "timezone": TZ_NAME, "run_at": "2026-10-20T02:00:00+00:00"}
        # …and it is DISCLOSED (it is still armed with its own schedule).
        assert result["untouched_existing"]["source_id"] == "src-1"

    @pytest.mark.asyncio
    async def test_a_weekly_dow_mismatch_creates_a_fresh_label(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(
            FakeRow(
                config={
                    "recurrence": "weekly",
                    "schedule": "30 6 * * 1",  # stored DOW = 1 (Monday)
                    "timezone": TZ_NAME,
                }
            )
        )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="weekly 3 23:50",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["fallback_reason"] == "weekly_dow_mismatch"
        assert service.update_calls == []

    @pytest.mark.asyncio
    async def test_a_cron_expression_mismatch_creates_a_fresh_label(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(
            FakeRow(
                config={
                    "recurrence": "cron",
                    "schedule": "0 9 * * *",
                    "timezone": TZ_NAME,
                }
            )
        )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="cron 30 7 * * *",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["fallback_reason"] == "cron_expression_mismatch"
        assert service.update_calls == []

    @pytest.mark.asyncio
    async def test_a_matching_weekly_row_is_reused(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(
            FakeRow(
                config={
                    "recurrence": "weekly",
                    "schedule": "30 6 * * 3",  # stored DOW = 3 (Wednesday)
                    "timezone": TZ_NAME,
                }
            )
        )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="weekly 3 23:50",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["reused"] is True
        assert len(service.update_calls) == 1
        # The update rewrote only the minute/hour — the stored DOW
        # survived.
        assert repo.get_source_config("src-1").config["schedule"] == "50 23 * * 3"


# ---------------------------------------------------------------------------
# The burned label (D4) + the fresh-candidate chain
# ---------------------------------------------------------------------------


class TestBurnedLabel:
    @pytest.mark.asyncio
    async def test_a_cancelled_label_is_burned_and_a_fresh_candidate_is_used(
        self, tool_env
    ) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow(status="cancelled", enabled=False))
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["label"] == "self-alarm:deploy-check-2"
        assert result["fallback_reason"] == "label_burned_cancelled"
        assert service.create_calls[0][0]["label"] == "self-alarm:deploy-check-2"

    @pytest.mark.asyncio
    async def test_the_fresh_candidate_is_collision_checked(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow(status="cancelled", enabled=False))
        repo.rows.append(
            FakeRow(source_id="src-2", name="self-alarm:deploy-check-2", status="cancelled", enabled=False)
        )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert result["label"] == "self-alarm:deploy-check-3"
        assert service.create_calls[0][0]["label"] == "self-alarm:deploy-check-3"

    @pytest.mark.asyncio
    async def test_the_candidate_chain_is_capped(self, tool_env) -> None:
        """The chain is base + ``-2`` .. ``-5`` — beyond that a NEW label
        is required."""
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow(status="cancelled", enabled=False))
        for suffix in (2, 3, 4, 5):
            repo.rows.append(
                FakeRow(
                    source_id=f"src-{suffix}",
                    name=f"self-alarm:deploy-check-{suffix}",
                    status="cancelled",
                    enabled=False,
                )
            )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "new label is required" in result
        assert service.create_calls == []


# ---------------------------------------------------------------------------
# The re-arm rules (D4 step 4b) + the fallback/neutralization (steps 5-6)
# ---------------------------------------------------------------------------


class TestRearmAndFallback:
    @pytest.mark.asyncio
    async def test_a_message_only_rearm_of_a_fired_alarm_is_refused(self, tool_env) -> None:
        """A fired one-shot must be re-armed with a STRICTLY LATER
        instant — a message-only re-arm keeps the stored ``run_at``, the
        rebuilt adapter re-enqueues under the SAME idempotency key and the
        submit collapses: the alarm would silently never be delivered."""
        tools, service, repo, _instance_repo = tool_env
        stored_fire = _future_iso(2.0)
        repo.rows.append(
            FakeRow(
                status="stopped",
                enabled=False,
                config={
                    "recurrence": "once",
                    "timezone": TZ_NAME,
                    "run_at": stored_fire,
                },
            )
        )
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=stored_fire,
            message_to_self="A new message, same time.",
        )
        assert isinstance(result, str)
        assert "new, LATER time" in result
        assert service.update_calls == []
        assert service.create_calls == []

    @pytest.mark.asyncio
    async def test_a_stopped_row_is_rearmed_with_a_later_time(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        stored_fire = _future_iso(2.0)
        repo.rows.append(
            FakeRow(
                status="stopped",
                enabled=False,
                config={
                    "recurrence": "once",
                    "timezone": TZ_NAME,
                    "run_at": stored_fire,
                },
            )
        )
        target = _future_iso(5.0)
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=target,
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        assert result["reused"] is True
        _source_id, payload = service.update_calls[0]
        assert payload["paused"] is False
        # The re-armed row is ARMED again (enabled True).
        assert repo.get_source_config("src-1").enabled is True

    @pytest.mark.asyncio
    async def test_a_failed_assertion_falls_back_and_pauses_the_original(
        self, tool_env
    ) -> None:
        """The forced assertion failure ⇒ a fresh-label create AND the
        mutated original is PAUSED ⇒ exactly ONE armed row survives."""
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        service.bad_echo = True
        target = _future_iso(3.0)
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=target,
            message_to_self="Do the thing.",
        )
        assert isinstance(result, dict)
        # The fresh label was created.
        assert result["label"] == "self-alarm:deploy-check-2"
        assert result["fallback_reason"] == "reuse_assertion_failed"
        # The mutated original was NEUTRALIZED with a pause.
        assert len(service.update_calls) == 2
        pause_source_id, pause_payload = service.update_calls[1]
        assert pause_source_id == "src-1"
        assert pause_payload == {"paused": True}
        assert result["neutralized_original"]["source_id"] == "src-1"
        assert result["neutralized_original"]["paused"] is True
        # The row-state is a REPO-side fact (never the pause response).
        original = repo.get_source_config("src-1")
        assert original.status == "stopped"
        assert original.enabled is False

    @pytest.mark.asyncio
    async def test_a_failed_pause_is_a_typed_error_naming_both_rows(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        service.bad_echo = True
        target = _future_iso(3.0)
        # The pause call explodes.
        original_update = service.update_schedule

        async def _update(source_id, payload):
            if isinstance(payload, dict) and payload.get("paused") is True:
                raise RuntimeError("pause blew up")
            return await original_update(source_id, payload)

        service.update_schedule = _update
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=target,
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR: ")
        assert "src-1" in result
        assert "self-alarm:deploy-check-2" in result
        assert "original may still be armed — use a fresh label on retry" in result

    @pytest.mark.asyncio
    async def test_a_pause_that_does_not_report_paused_is_an_error(self, tool_env) -> None:
        tools, service, repo, _instance_repo = tool_env
        repo.rows.append(FakeRow())
        service.bad_echo = True
        target = _future_iso(3.0)
        original_update = service.update_schedule

        async def _update(source_id, payload):
            if isinstance(payload, dict) and payload.get("paused") is True:
                # A pause response that does NOT report paused=True.
                from daemon.services.scheduling_service import ScheduleUpdateResponse

                return ScheduleUpdateResponse(
                    source_id=source_id,
                    label=EFFECTIVE,
                    status="stopped",
                    paused=False,
                    next_run_at_local=None,
                    next_run_at_utc=None,
                )
            return await original_update(source_id, payload)

        service.update_schedule = _update
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every=target,
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert "paused=True" in result
        assert "use a fresh label on retry" in result


# ---------------------------------------------------------------------------
# Engine error paths — the tool NEVER raises
# ---------------------------------------------------------------------------


class TestEngineErrors:
    @pytest.mark.asyncio
    async def test_a_raising_create_becomes_an_error_string(self, tool_env) -> None:
        tools, service, _repo, _instance_repo = tool_env
        service.raise_on_create = ValueError("label already exists: x")
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR: ")
        assert "label already exists" in result

    @pytest.mark.asyncio
    async def test_an_unexpected_exception_becomes_a_generic_error_string(
        self, tool_env
    ) -> None:
        tools, service, _repo, _instance_repo = tool_env
        service.raise_on_create = RuntimeError("secret internal detail")
        result = await _call(
            tools,
            label="deploy-check",
            when_or_every="in 45m",
            message_to_self="Do the thing.",
        )
        assert isinstance(result, str)
        assert result.startswith("ERROR: ")
        # The PB-F1 leak class: an unexpected exception's message never
        # reaches the payload.
        assert "secret internal detail" not in result


# ---------------------------------------------------------------------------
# The REAL engine semantics the wrapper depends on
# ---------------------------------------------------------------------------


class TestRealServiceContract:
    """The engine facts the wrapper's assertions rest on, proven against
    the REAL service (fake repo + registry doubles, no DB)."""

    @pytest.mark.asyncio
    async def test_the_pause_response_carries_paused_true_and_a_stale_status(
        self, real_service
    ) -> None:
        row = FakeRow(status="running", enabled=True)
        repo = FakeRepo([row])
        real_service.configure(source_repo=repo, source_registry=FakeRegistry(repo))
        response = await real_service.update_schedule("src-1", {"paused": True})
        assert response.paused is True
        # The engine writes 'stopped' to the ROW, but the RESPONSE echoes
        # the stale pre-pause status — the wrapper asserts `paused` only.
        assert response.status == "running"
        assert ("src-1", "stopped") in repo.status_writes
        assert repo.get_source_config("src-1").status == "stopped"
        assert repo.get_source_config("src-1").enabled is False

    @pytest.mark.asyncio
    async def test_the_update_response_status_is_the_pre_update_status(
        self, real_service
    ) -> None:
        """NO re-read ⇒ the response carries the PRE-update status.

        The engine re-reads the row ONLY when the adapter start
        succeeded (``scheduling_service.py:1050-1052``) — with a failed
        rebuild the response echoes the stale pre-update value.
        """
        row = FakeRow(status="stopped", enabled=False)
        repo = FakeRepo([row])
        real_service.configure(
            source_repo=repo, source_registry=FakeRegistry(repo, fail_rebuild=True)
        )
        response = await real_service.update_schedule(
            "src-1",
            {
                "when": "2026-10-20T09:00:00+07:00",
                "message": "new message",
                "timezone": TZ_NAME,
                "paused": False,
            },
        )
        assert response.paused is False
        assert response.status == "stopped"

    @pytest.mark.asyncio
    async def test_a_successful_start_triggers_the_engines_re_read(
        self, real_service
    ) -> None:
        """The other half of the contract: a successful adapter start
        re-reads the row, so the response carries the POST-update
        status."""
        row = FakeRow(status="stopped", enabled=False)
        repo = FakeRepo([row])
        real_service.configure(source_repo=repo, source_registry=FakeRegistry(repo))
        response = await real_service.update_schedule(
            "src-1",
            {
                "when": _future_iso(3.0),
                "message": "new message",
                "timezone": TZ_NAME,
                "paused": False,
            },
        )
        assert response.paused is False
        assert response.status == "running"
        assert response.next_run_at_utc is not None

    @pytest.mark.asyncio
    async def test_the_next_run_echo_equals_the_engines_own_computation(
        self, real_service
    ) -> None:
        row = FakeRow(
            status="running",
            enabled=True,
            config={
                "recurrence": "daily",
                "schedule": "30 6 * * *",
                "timezone": TZ_NAME,
            },
        )
        repo = FakeRepo([row])
        real_service.configure(source_repo=repo, source_registry=FakeRegistry(repo))
        response = await real_service.update_schedule(
            "src-1",
            {"when": "23:50", "message": "m", "timezone": TZ_NAME, "paused": False},
        )
        expected_local, expected_utc, _warning = real_service._compute_next_run(
            repo.get_source_config("src-1").config
        )
        assert response.next_run_at_local == expected_local
        assert response.next_run_at_utc == expected_utc
