"""Service unit tests for ``daemon.services.scheduling_service`` (phase-2 §Task 1.9).

Explicitly DEFERRED from unit 2 to this commit (unit 4) per the commission
instructions. Mocks ``source_repo`` + ``source_registry``; NO DB, NO daemon.

Coverage map (phase-2 §Task 1.9 + architecture §8 items 10/12/13):
  * create flow per recurrence type (once / daily / weekly / cron)
  * duplicate-label + unknown-agent fail-fast (ValueError → caller maps 4xx)
  * cancel rejects already-cancelled + non-existent; echoes last_execution_id (§5.3)
  * list hides cancelled by default; include_cancelled un-hides
  * update pause→resume round-trip; update rejects cancelled (409)
  * tz warning surfaces when the chain falls back to UTC (OD-4)
  * per-source lock contention: two concurrent update_schedule serialize
  * rebuild actually builds a fresh adapter (builder → register → start)
  * the <1s rebuild-gap documentation lives in the module docstring

SAFETY FENCE: pure unit tests — mocks only, the daemon is NEVER booted and
no DB is ever touched, so no ambient ``POSTGRES_*`` can leak anywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.services import scheduling_service as svc


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _row(
    source_id: str,
    *,
    source_type: str = "scheduler",
    name: str | None = None,
    config: dict | None = None,
    status: str = "running",
    enabled: bool = True,
) -> SimpleNamespace:
    """A SourceConfig-shaped row double (only the attrs the service touches)."""
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        name=name if name is not None else source_id,
        config=config or {},
        credentials=None,
        enabled=enabled,
        autostart=True,
        status=status,
        error_message=None,
        created_at="2026-10-01T00:00:00+00:00",
        updated_at="2026-10-01T00:00:00+00:00",
    )


class _FakeRepo:
    """In-memory SourceRepository double matching the call shapes the
    service actually makes (verified against ``daemon/repositories/source/repository.py``):
    ``create_source_config(source_type, name, config, credentials, enabled,
    autostart)``, ``update_source_config(source_id, source_type, name,
    config, credentials, enabled, autostart)``, ``update_source_status``,
    ``cancel_source_config``, ``get_source_config``, ``get_source_config_by_name``,
    ``list_source_configs``, ``get_latest_execution``.
    """

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}
        self.executions: dict[str, SimpleNamespace] = {}
        self.update_status_calls: list[tuple] = []
        self.cancel_calls: list[str] = []
        self.update_hook: Any = None  # optional slow-path hook for the lock test

    # -- writes -------------------------------------------------------------
    def create_source_config(self, source_type, name, config, credentials=None,
                             enabled=True, autostart=True, source_id=None):
        source_id = source_id or name
        row = _row(source_id, source_type=source_type, name=name, config=config,
                   enabled=enabled, status="stopped")
        self.rows[source_id] = row
        return row

    def update_source_config(self, source_id, source_type=None, name=None, config=None,
                             credentials=None, enabled=None, autostart=None):
        row = self.rows.get(source_id)
        if row is None:
            return None
        if self.update_hook is not None:
            self.update_hook()  # test hook (lock-contention observation)
        if name is not None:
            row.name = name
        if config is not None:
            row.config = config
        if enabled is not None:
            row.enabled = enabled
        return row

    def update_source_status(self, source_id, status, error_message=None):
        self.update_status_calls.append((source_id, status))
        row = self.rows.get(source_id)
        if row is not None:
            row.status = status
        return row

    def cancel_source_config(self, source_id):
        self.cancel_calls.append(source_id)
        row = self.rows.get(source_id)
        if row is None:
            return None
        row.status = "cancelled"
        row.enabled = False
        return row

    # -- reads --------------------------------------------------------------
    def get_source_config(self, source_id):
        return self.rows.get(source_id)

    def get_source_config_by_name(self, name):
        for row in self.rows.values():
            if row.name == name:
                return row
        return None

    def list_source_configs(self, *_args, **_kwargs):
        return list(self.rows.values())

    def get_latest_execution(self, schedule_id):
        return self.executions.get(schedule_id)


def _configure_service(repo: _FakeRepo, registry: MagicMock, monkeypatch) -> None:
    """Wire the module-level singleton deps + isolate the global agent registry."""
    svc.configure(source_repo=repo, source_registry=registry)
    monkeypatch.setattr(svc, "_source_locks", {})
    # create_schedule resolves the agent via the GLOBAL registry at call time
    # (function-local import) — patch the module attribute so the test does
    # not depend on the real ./agents directory.
    import daemon.registry as registry_module

    monkeypatch.setattr(registry_module, "get_registry", lambda: registry)


@pytest.fixture
def service_env(monkeypatch):
    """Configured service deps, restored + lock-cleared after each test."""
    repo = _FakeRepo()
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)  # no live adapters by default
    registry.unregister = MagicMock(return_value=True)
    registry._create_adapter_from_config = AsyncMock(return_value=MagicMock(name="fresh-adapter"))
    registry.register = MagicMock()
    registry.start_adapter = AsyncMock(return_value=True)

    _configure_service(repo, registry, monkeypatch)
    yield repo, registry
    svc._deps.source_repo = None
    svc._deps.source_registry = None
    svc._deps.project_repository = None
    svc._source_locks.clear()


# ---------------------------------------------------------------------------
# Create flow — one per recurrence type
# ---------------------------------------------------------------------------


class TestCreateScheduleRecurrences:
    """Phase-2 §Task 1.9: create flow with each recurrence type."""

    @pytest.mark.asyncio
    async def test_create_once_anchors_run_at_and_starts_adapter(self, service_env):
        repo, registry = service_env
        result = await svc.create_schedule(
            {
                "label": "once-1", "agent": "ari", "message": "fire",
                "when": "2030-01-15T09:00:00", "timezone": "UTC",
                "recurrence": "once",
            },
            caller_instance_id="test", caller_agent_id="tester",
        )
        assert result.source_id == "once-1"
        assert result.label == "once-1"
        row = repo.rows["once-1"]
        # once → run_at (anchored ISO) + human-intent echoes; NO cron key.
        assert row.config["run_at"].startswith("2030-01-15T09:00:00")
        assert row.config["recurrence"] == "once"
        assert row.config["local_time"] == "2030-01-15T09:00:00"
        assert "schedule" not in row.config
        assert row.source_type == "scheduler"
        assert row.enabled is True and row.autostart is True
        # Adapter rebuild → register → start ran (best-effort start).
        registry._create_adapter_from_config.assert_awaited_once()
        registry.register.assert_called_once()
        registry.start_adapter.assert_awaited_once_with("once-1")
        # Both tz echoes present.
        assert result.next_run_at_local is not None
        assert result.next_run_at_utc is not None

    @pytest.mark.asyncio
    async def test_create_daily_builds_cron_from_hhmm(self, service_env):
        repo, _registry = service_env
        result = await svc.create_schedule(
            {
                "label": "daily-1", "agent": "ari", "message": "m",
                "when": "06:30", "timezone": "Asia/Tokyo", "recurrence": "daily",
            },
            caller_instance_id="test", caller_agent_id="tester",
        )
        row = repo.rows["daily-1"]
        assert row.config["schedule"] == "30 6 * * *"
        assert "run_at" not in row.config
        assert row.config["timezone"] == "Asia/Tokyo"
        assert result.status in {"running", "stopped"}

    @pytest.mark.asyncio
    async def test_create_weekly_requires_weekday_and_builds_dow_cron(self, service_env):
        repo, _registry = service_env
        with pytest.raises(ValueError, match="weekday is required"):
            await svc.create_schedule(
                {"label": "wk-bad", "agent": "ari", "message": "m",
                 "when": "16:30", "recurrence": "weekly"},
                caller_instance_id="test", caller_agent_id="tester",
            )
        await svc.create_schedule(
            {"label": "wk-1", "agent": "ari", "message": "m",
             "when": "16:30", "recurrence": "weekly", "weekday": 5},
            caller_instance_id="test", caller_agent_id="tester",
        )
        # weekday=5 (Sat in cron DOW where 0=Sun) — Friday in the canonical doc
        # is 5 under "0=Sun..6=Sat"; the cron field must carry the canonical int.
        assert repo.rows["wk-1"].config["schedule"] == "30 16 * * 5"

    @pytest.mark.asyncio
    async def test_create_cron_stores_raw_expression(self, service_env):
        repo, _registry = service_env
        await svc.create_schedule(
            {"label": "cron-1", "agent": "ari", "message": "m",
             "when": "", "timezone": "UTC", "recurrence": "cron",
             "cron_expression": "*/10 * * * *"},
            caller_instance_id="test", caller_agent_id="tester",
        )
        row = repo.rows["cron-1"]
        assert row.config["schedule"] == "*/10 * * * *"
        assert "run_at" not in row.config

    @pytest.mark.asyncio
    async def test_create_rejects_duplicate_label(self, service_env):
        _repo, _registry = service_env
        payload = {"label": "dupe", "agent": "ari", "message": "m",
                   "when": "06:00", "recurrence": "daily"}
        await svc.create_schedule(dict(payload), caller_instance_id="t", caller_agent_id="t")
        with pytest.raises(ValueError, match="label already exists"):
            await svc.create_schedule(dict(payload), caller_instance_id="t", caller_agent_id="t")

    @pytest.mark.asyncio
    async def test_create_fail_fast_unknown_agent(self, service_env, monkeypatch):
        repo, _registry = service_env
        import daemon.registry as registry_module

        ghost = MagicMock()
        ghost.exists = MagicMock(return_value=False)
        monkeypatch.setattr(registry_module, "get_registry", lambda: ghost)
        with pytest.raises(ValueError, match="does not exist"):
            await svc.create_schedule(
                {"label": "ghost-1", "agent": "ghost", "message": "m",
                 "when": "06:00", "recurrence": "daily"},
                caller_instance_id="t", caller_agent_id="t",
            )
        # Fail-fast: NO DB write happened.
        assert repo.rows == {}


# ---------------------------------------------------------------------------
# Cancel — terminal, rejects, §5.3 echo
# ---------------------------------------------------------------------------


class TestCancelSchedule:
    @pytest.mark.asyncio
    async def test_cancel_rejects_nonexistent(self, service_env):
        _repo, _registry = service_env
        with pytest.raises(ValueError, match="Schedule not found"):
            await svc.cancel_schedule("ghost")

    @pytest.mark.asyncio
    async def test_cancel_rejects_already_cancelled(self, service_env):
        repo, _registry = service_env
        repo.rows["c1"] = _row("c1", status="cancelled")
        with pytest.raises(ValueError, match="already cancelled"):
            await svc.cancel_schedule("c1")

    @pytest.mark.asyncio
    async def test_cancel_uses_atomic_cancel_source_config(self, service_env):
        """The SOLE status writer: one atomic ``cancel_source_config`` call —
        never the two-call sequence (architecture §3.2/§5.4)."""
        repo, registry = service_env
        registry.get = MagicMock(return_value=None)  # no live adapter to evict
        repo.rows["c1"] = _row("c1", config={"recurrence": "daily", "local_time": "06:00"})
        result = await svc.cancel_schedule("c1")
        assert repo.cancel_calls == ["c1"]
        assert result.status == "cancelled"
        assert result.cancelled_at
        # No non-atomic status write happened.
        assert repo.update_status_calls == []

    @pytest.mark.asyncio
    async def test_cancel_echoes_last_execution_id(self, service_env):
        """Architecture §5.3: the in-flight JobItem is NOT cancellable — the
        response echoes the last execution id so the operator can kill it."""
        repo, registry = service_env
        registry.get = MagicMock(return_value=None)
        repo.rows["c1"] = _row("c1")
        repo.executions["c1"] = SimpleNamespace(execution_id="exec-7")
        result = await svc.cancel_schedule("c1")
        assert result.last_execution_id == "exec-7"

    @pytest.mark.asyncio
    async def test_cancel_preserves_history_and_never_deletes(self, service_env):
        repo, registry = service_env
        registry.get = MagicMock(return_value=None)
        repo.rows["c1"] = _row("c1")
        repo.executions["c1"] = SimpleNamespace(execution_id="exec-1")
        await svc.cancel_schedule("c1")
        # Row still present (terminal, never purged) and history queryable.
        assert repo.rows["c1"].status == "cancelled"
        assert repo.get_latest_execution("c1").execution_id == "exec-1"

    @pytest.mark.asyncio
    async def test_cancel_resolves_by_label(self, service_env):
        repo, registry = service_env
        registry.get = MagicMock(return_value=None)
        repo.rows["id-1"] = _row("id-1", name="my-label")
        result = await svc.cancel_schedule("my-label")
        assert result.source_id == "id-1"


# ---------------------------------------------------------------------------
# List — cancelled hidden by default
# ---------------------------------------------------------------------------


class TestListSchedules:
    @pytest.mark.asyncio
    async def test_list_hides_cancelled_by_default(self, service_env):
        repo, _registry = service_env
        repo.rows = {
            "a": _row("a", config={"recurrence": "daily"}),
            "b": _row("b", status="cancelled", config={"recurrence": "daily"}),
            "t": _row("t", source_type="telegram"),
        }
        items = await svc.list_schedules()
        ids = [i.source_id for i in items]
        assert ids == ["a"]  # cancelled hidden AND non-scheduler rows excluded

    @pytest.mark.asyncio
    async def test_list_include_cancelled(self, service_env):
        repo, _registry = service_env
        repo.rows = {
            "a": _row("a", config={"recurrence": "daily"}),
            "b": _row("b", status="cancelled", config={"recurrence": "daily"}),
        }
        items = await svc.list_schedules(include_cancelled=True)
        assert sorted(i.source_id for i in items) == ["a", "b"]

    @pytest.mark.asyncio
    async def test_get_schedule_returns_cancelled_rows(self, service_env):
        """architecture §3.4: GET-by-id DOES return cancelled rows."""
        repo, _registry = service_env
        repo.rows["b"] = _row("b", status="cancelled", config={"recurrence": "daily"})
        detail = await svc.get_schedule("b")
        assert detail is not None
        assert detail.status == "cancelled"
        assert detail.cancelled_at is not None


# ---------------------------------------------------------------------------
# Update — pause/resume round-trip, cancelled rejection, lock
# ---------------------------------------------------------------------------


class TestUpdateSchedule:
    @pytest.mark.asyncio
    async def test_pause_resume_round_trip(self, service_env):
        repo, registry = service_env
        repo.rows["u1"] = _row("u1", config={"recurrence": "daily", "local_time": "06:00",
                                             "schedule": "0 6 * * *"})

        paused = await svc.update_schedule("u1", {"paused": True})
        assert paused.paused is True
        assert repo.rows["u1"].enabled is False
        assert ("u1", "stopped") in repo.update_status_calls
        # Pausing must NOT rebuild/start the adapter.
        registry.start_adapter.assert_not_awaited()

        resumed = await svc.update_schedule("u1", {"paused": False})
        assert resumed.paused is False
        assert repo.rows["u1"].enabled is True
        # Resume rebuilds: fresh adapter → register → start.
        registry._create_adapter_from_config.assert_awaited()
        registry.start_adapter.assert_awaited_with("u1")

    @pytest.mark.asyncio
    async def test_update_rejects_cancelled(self, service_env):
        repo, _registry = service_env
        repo.rows["u1"] = _row("u1", status="cancelled")
        with pytest.raises(ValueError, match="cancelled and cannot be updated"):
            await svc.update_schedule("u1", {"message": "new"})

    @pytest.mark.asyncio
    async def test_update_rejects_nonexistent(self, service_env):
        _repo, _registry = service_env
        with pytest.raises(ValueError, match="Schedule not found"):
            await svc.update_schedule("ghost", {"message": "new"})

    @pytest.mark.asyncio
    async def test_concurrent_updates_serialize_on_per_source_lock(self, service_env):
        """Architecture §5.4 concurrency model: two concurrent
        ``update_schedule(same_id)`` calls serialize on the shared per-source
        lock — the config mutations never overlap.
        """
        import asyncio
        import threading

        repo, _registry = service_env
        repo.rows["u1"] = _row("u1", config={"recurrence": "daily", "local_time": "06:00",
                                             "schedule": "0 6 * * *"})

        state = {"in_flight": 0, "max_in_flight": 0}
        mutex = threading.Lock()

        def _slow_update_hook():
            """Runs inside the repo write (a thread per to_thread call) —
            records the max observed concurrency."""
            with mutex:
                state["in_flight"] += 1
                state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            import time

            time.sleep(0.05)
            with mutex:
                state["in_flight"] -= 1

        repo.update_hook = _slow_update_hook

        await asyncio.gather(
            svc.update_schedule("u1", {"message": "first"}),
            svc.update_schedule("u1", {"message": "second"}),
        )
        assert state["max_in_flight"] == 1, (
            f"expected serialized writes, observed {state['max_in_flight']} concurrent"
        )
        assert repo.rows["u1"].config["message"] in {"first", "second"}

    @pytest.mark.asyncio
    async def test_rebuild_creates_a_fresh_adapter(self, service_env):
        """Architecture §5.1: the service IS the adapter-rebuild seam —
        update-with-resume must call the registry builder (fresh adapter),
        then register + start. It must NOT reuse a live adapter object."""
        repo, registry = service_env
        registry.get = MagicMock(return_value=MagicMock(name="stale-live-adapter"))
        repo.rows["u1"] = _row("u1", config={"recurrence": "daily", "local_time": "06:00",
                                             "schedule": "0 6 * * *"})
        await svc.update_schedule("u1", {"message": "edited"})
        registry._create_adapter_from_config.assert_awaited_once()
        built = registry._create_adapter_from_config.await_args.args[0]
        assert built.source_id == "u1"
        registry.register.assert_called_once()
        registry.start_adapter.assert_awaited_once_with("u1")

    def test_rebuild_gap_documented_in_module_docstring(self):
        """The <1s stop→start missed-fire window is a DOCUMENTED residual
        (architecture §5.1) — it must stay surfaced in the module docstring."""
        doc = svc.__doc__ or ""
        assert "rebuild gap" in doc
        assert "<1s" in doc


# ---------------------------------------------------------------------------
# Tz warning surface (OD-4)
# ---------------------------------------------------------------------------


class TestTzWarningSurface:
    @pytest.mark.asyncio
    async def test_tz_warning_surfaces_on_utc_fallback(self, service_env, monkeypatch):
        """Chain falls through to UTC → ``tz_warning`` is NEVER suppressed."""
        from daemon.util import tz as tz_module

        monkeypatch.setenv("ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS", "0")
        monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", raising=False)
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(tz_module, "_read_etc_localtime_target", lambda: None)
        tz_module._cache_clear_for_tests()
        try:
            result = await svc.create_schedule(
                {"label": "warn-1", "agent": "ari", "message": "m",
                 "when": "06:00", "recurrence": "daily", "timezone": None},
                caller_instance_id="t", caller_agent_id="t",
            )
        finally:
            tz_module._cache_clear_for_tests()
        assert result.tz_warning != ""
        assert "UTC" in result.tz_warning

    @pytest.mark.asyncio
    async def test_clean_tz_has_empty_warning(self, service_env):
        result = await svc.create_schedule(
            {"label": "clean-1", "agent": "ari", "message": "m",
             "when": "06:00", "recurrence": "daily", "timezone": "Asia/Tokyo"},
            caller_instance_id="t", caller_agent_id="t",
        )
        assert result.tz_warning == ""


# ---------------------------------------------------------------------------
# User timezone setting rung (tz chain priority)
# ---------------------------------------------------------------------------


def _wire_user_tz(monkeypatch, stored_value) -> MagicMock:
    """Stub the user-timezone preference read at the repo seam.

    Patches ``Session`` on the *user_timezone_utils* module (the same
    technique as ``tests/test_settings_api.py``'s helper tests) and wires
    a project-repo double through ``svc.configure`` so the service
    wrapper's metadata read resolves to ``stored_value``.
    """
    from daemon.services import user_timezone_utils as utz

    class _DummySession:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(utz, "Session", lambda *_a, **_kw: _DummySession())
    project_repo = MagicMock()
    record = None if stored_value is None else SimpleNamespace(meta_value=stored_value)
    project_repo.get_metadata_record.return_value = record
    svc.configure(project_repository=project_repo)
    return project_repo


async def _create_daily(repo, monkeypatch_env_done=True, **payload_overrides):
    """Create a 06:00 daily schedule with the given payload, return the result."""
    payload = {
        "label": "tz-rung", "agent": "ari", "message": "m",
        "when": "06:00", "recurrence": "daily", "timezone": None,
    }
    payload.update(payload_overrides)
    return await svc.create_schedule(payload, caller_instance_id="t", caller_agent_id="t")


class TestUserTimezoneRung:
    """Chain priority: explicit → user-setting → env default → host-local → UTC.

    The user-setting rung is implemented at THIS service wrapper (repo
    access lives here); ``daemon.util.tz.resolve_timezone`` stays pure and
    receives the preference as the preferred ``default``. Pins:

    * a SET + VALID user tz beats the env default and resolves CLEAN
      (no warning — deliberate user choice, never a fallback);
    * an explicit param ALWAYS wins (and skips the metadata read);
    * an unset / invalid stored value falls through SILENTLY to the env
      default (no warning when the env rung resolves);
    * the UTC terminal warning is unchanged (nothing set anywhere).
    """

    @pytest.fixture(autouse=True)
    def _isolate_tz_chain(self, monkeypatch):
        """Kill host-local detection + env default so every rung is observable.

        Mirrors ``tests/unit/test_tz_resolver.py::_isolate_host_detection``:
        cache-seconds=0 keeps ``detect_host_local_timezone`` fresh; with
        /etc/localtime + TZ + env default all neutralized, the terminal
        rung (UTC + warning) is the only thing left when the earlier
        rungs are absent.
        """
        from daemon.util import tz as tz_module

        monkeypatch.setenv("ENSEMBLE_SCHEDULING_HOST_LOCAL_TZ_CACHE_SECONDS", "0")
        monkeypatch.delenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", raising=False)
        monkeypatch.delenv("TZ", raising=False)
        monkeypatch.setattr(tz_module, "_read_etc_localtime_target", lambda: None)
        tz_module._cache_clear_for_tests()
        yield
        tz_module._cache_clear_for_tests()

    @pytest.mark.asyncio
    async def test_set_user_tz_beats_env_default_with_no_warning(self, service_env, monkeypatch):
        """SET + VALID user tz wins over the env default; resolves CLEAN."""
        repo, _registry = service_env
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", "America/New_York")
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(repo)
        assert repo.rows["tz-rung"].config["timezone"] == "Asia/Bangkok"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_set_user_tz_resolves_local_echo_in_user_zone(self, service_env, monkeypatch):
        """The LOCAL echo is rendered in the user tz when it wins the chain."""
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(repo)
        assert result.next_run_at_local is not None
        assert result.next_run_at_local.endswith("+07:00")
        assert result.next_run_at_utc is not None

    @pytest.mark.asyncio
    async def test_explicit_wins_over_user_setting(self, service_env, monkeypatch):
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(repo, timezone="America/New_York")
        assert repo.rows["tz-rung"].config["timezone"] == "America/New_York"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_explicit_skips_the_metadata_read(self, service_env, monkeypatch):
        """Cost pin: zero metadata reads when the caller pinned a tz."""
        repo, _registry = service_env
        project_repo = _wire_user_tz(monkeypatch, "Asia/Bangkok")

        await _create_daily(repo, timezone="America/New_York")
        project_repo.get_metadata_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_unset_stored_value_falls_to_env_silently(self, service_env, monkeypatch):
        """No stored row → env default applies, still no warning."""
        repo, _registry = service_env
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", "America/New_York")
        project_repo = _wire_user_tz(monkeypatch, None)

        result = await _create_daily(repo)
        assert repo.rows["tz-rung"].config["timezone"] == "America/New_York"
        assert result.tz_warning == ""
        project_repo.get_metadata_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_stored_value_falls_to_env_silently(self, service_env, monkeypatch):
        """Invalid-at-read stored value → treated as unset, SILENT fall-through."""
        repo, _registry = service_env
        monkeypatch.setenv("ENSEMBLE_SCHEDULING_DEFAULT_TIMEZONE", "America/New_York")
        _wire_user_tz(monkeypatch, "Not/ARealZone")

        result = await _create_daily(repo)
        assert repo.rows["tz-rung"].config["timezone"] == "America/New_York"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_set_user_tz_used_when_env_absent(self, service_env, monkeypatch):
        """User tz is the preferred default even with no env default configured."""
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(repo)
        assert repo.rows["tz-rung"].config["timezone"] == "Asia/Bangkok"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_nothing_set_terminal_utc_warning_unchanged(self, service_env, monkeypatch):
        """Full matrix floor: no explicit, no user tz, no env → UTC + loud warning."""
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, None)

        result = await _create_daily(repo)
        assert repo.rows["tz-rung"].config["timezone"] == "UTC"
        assert result.tz_warning != ""
        assert "UTC" in result.tz_warning

    @pytest.mark.asyncio
    async def test_explicit_invalid_still_warns_utc_despite_user_setting(
        self, service_env, monkeypatch
    ):
        """Explicit beats user-setting even when explicit is INVALID (D2 step-1
        contract): UTC + 'Invalid tz' warning — the user-setting rung is
        never consulted after an explicit param."""
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(repo, timezone="Not/ARealZone")
        assert repo.rows["tz-rung"].config["timezone"] == "UTC"
        assert "Invalid tz" in result.tz_warning

    @pytest.mark.asyncio
    async def test_user_setting_rung_covers_cron_probe_site(self, service_env, monkeypatch):
        """The cron-validation call site (:687) rides the same chain."""
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(
            repo, recurrence="cron", when="ignored", cron_expression="0 6 * * *"
        )
        assert repo.rows["tz-rung"].config["timezone"] == "Asia/Bangkok"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_user_setting_rung_covers_once_anchor_site(self, service_env, monkeypatch):
        """The once-anchor call site (:706) rides the same chain — naive ISO
        anchors in the USER tz when it wins."""
        repo, _registry = service_env
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await _create_daily(
            repo, recurrence="once", when="2026-10-05T06:00:00"
        )
        assert repo.rows["tz-rung"].config["timezone"] == "Asia/Bangkok"
        # 06:00 Bangkok = 23:00Z the previous day.
        assert result.next_run_at_utc == "2026-10-04T23:00:00+00:00"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_update_explicit_tz_change_flows_through_wrapper(
        self, service_env, monkeypatch
    ):
        """Update-path call site (:931): an explicit tz CHANGE re-resolves via
        the (now async) wrapper — guards the awaited signature end-to-end."""
        repo, registry = service_env
        repo.rows["u1"] = _row("u1", config={"recurrence": "daily", "local_time": "06:00",
                                             "timezone": "UTC", "schedule": "0 6 * * *"})
        _wire_user_tz(monkeypatch, "Asia/Bangkok")

        result = await svc.update_schedule("u1", {"timezone": "America/New_York"})
        assert repo.rows["u1"].config["timezone"] == "America/New_York"
        assert result.tz_warning == ""

    @pytest.mark.asyncio
    async def test_update_without_explicit_tz_does_not_consult_user_setting(
        self, service_env, monkeypatch
    ):
        """Update only re-resolves on an explicit tz change (pre-existing
        semantics) — the stored row's tz is NOT silently re-zoned by the
        user preference."""
        repo, _registry = service_env
        repo.rows["u1"] = _row("u1", config={"recurrence": "daily", "local_time": "06:00",
                                             "timezone": "UTC", "schedule": "0 6 * * *"})
        project_repo = _wire_user_tz(monkeypatch, "Asia/Bangkok")

        await svc.update_schedule("u1", {"message": "new text"})
        assert repo.rows["u1"].config["timezone"] == "UTC"
        project_repo.get_metadata_record.assert_not_called()
