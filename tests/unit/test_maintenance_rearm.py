"""Central Maintenance System (P1) — the adapter-side chain re-arm seam.

Covers plan ``phase1-plan.md`` Task 1.3 (BR-4): the ONE-SHOT CONSUMPTION
branch is the SOLE re-arm authority.  A consuming fire mints the next
week's instant, rewrites the row's ``run_at`` (a READ-MODIFY-WRITE of
the COMPLETE config), leaves the row ENABLED and never fires the
completion callback that disables a one-shot.  A FAILED dispatch does NOT
advance the chain (it is not a consuming outcome).

SAFETY FENCE: a throwaway SQLite database under ``tmp_path`` — no
PostgreSQL, no daemon, no live contact, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

from daemon.repositories.source.models import (
    ScheduleExecution,
    SourceConfig,
    SourceStatus,
)
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.sources.adapters.scheduler import SchedulerAdapter

SOURCE_ID = "chain-rearm-test-source"


@pytest.fixture
def rearm_engine(tmp_path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'chain_rearm.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(
        engine, tables=[SourceConfig.__table__, ScheduleExecution.__table__]
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _chain_config(run_at: datetime) -> dict:
    return {
        "agent": "maintenancer",
        "message": "Weekly central maintenance run.\n\ntrigger: scheduled\n",
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "once",
        "local_time": "03:00",
        "run_at": run_at.isoformat(),
        "instance_mode": "new_instance",
        "project_id": "system-default",
        "chain": {
            "mode": "weekly-random",
            "window_start": "01:00",
            "window_end": "05:00",
            "weekday": 0,
            "timezone": "Asia/Ho_Chi_Minh",
            "run_at_utc": run_at.isoformat(),
        },
    }


def _plain_config(run_at: datetime) -> dict:
    return {
        "agent": "maintenancer",
        "message": "one shot",
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "once",
        "local_time": "03:00",
        "run_at": run_at.isoformat(),
        "instance_mode": "new_instance",
        "project_id": "system-default",
    }


def _insert_row(engine: Engine, config: dict) -> SourceConfig:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        row = SourceConfig(
            source_id=SOURCE_ID,
            source_type="scheduler",
            name="central-maintenance-weekly",
            config=config,
            credentials=None,
            enabled=True,
            autostart=True,
            status=SourceStatus.RUNNING.value,
            error_message=None,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def _row(engine: Engine) -> SourceConfig | None:
    with Session(engine) as session:
        return session.get(SourceConfig, SOURCE_ID)


async def _noop_on_message(_msg) -> None:
    return None


def _build_adapter(engine: Engine, row: SourceConfig):
    source_repo = SQLModelSourceRepository(engine)
    completions: list[tuple[str, bool]] = []

    def _on_complete(source_id: str, completed: bool) -> None:
        completions.append((source_id, completed))

    adapter = SchedulerAdapter(
        row,
        on_message=_noop_on_message,
        on_complete_callback=_on_complete,
        source_repo=source_repo,
    )
    return adapter, completions


def _key(adapter) -> str:
    """The one-shot idempotency key (``scheduler:{source_id}:{run_at}``)."""
    return f"scheduler:{adapter.source_id}:{adapter._run_at.isoformat()}"


@pytest.fixture
def _fast_retry(monkeypatch):
    """The 5s same-episode cadence must not slow the test wall-clock."""
    import daemon.sources.adapters.scheduler as scheduler_module

    monkeypatch.setattr(scheduler_module, "SCHEDULER_ERROR_RETRY_S", 0.01)


# ══════════════════════════════════════════════════════════════════════════
# BR-4 — the consuming fire re-arms the chain
# ══════════════════════════════════════════════════════════════════════════


class TestConsumingFireRearms:
    @pytest.mark.asyncio
    async def test_a_consuming_fire_mints_the_next_week(self, rearm_engine):
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = _insert_row(rearm_engine, _chain_config(stale))
        adapter, completions = _build_adapter(rearm_engine, row)
        key_before = _key(adapter)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_DIRECT
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        # The row now carries a FRESH, FUTURE instant.
        refreshed = _row(rearm_engine)
        assert refreshed is not None
        new_run_at = datetime.fromisoformat(refreshed.config["run_at"])
        assert new_run_at > datetime.now(timezone.utc)
        assert refreshed.config["run_at"] != stale.isoformat()
        assert refreshed.config["chain"]["run_at_utc"] == refreshed.config["run_at"]

        # A new week ⇒ a NEW idempotency key.
        key_after = _key(adapter)
        assert key_after != key_before

        # The row stays ENABLED and startable — the completion callback
        # that disables a one-shot was SKIPPED for the chain row.
        assert refreshed.enabled is True
        assert refreshed.status == SourceStatus.RUNNING.value
        assert completions == [], (
            "the chain-aware skip did not skip the completion callback"
        )

    @pytest.mark.asyncio
    async def test_the_complete_config_survives_the_re_arm_write(self, rearm_engine):
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = _insert_row(rearm_engine, _chain_config(stale))
        adapter, _completions = _build_adapter(rearm_engine, row)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_DIRECT
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        refreshed = _row(rearm_engine)
        assert refreshed is not None
        config = refreshed.config
        # BR-4: the write is a read-modify-write of the COMPLETE config —
        # the config column is full-replaced, so a dropped key is a
        # dropped key.
        assert config["agent"] == "maintenancer"
        assert config["message"] == row.config["message"]
        assert config["timezone"] == "Asia/Ho_Chi_Minh"
        assert config["recurrence"] == "once"
        assert config["instance_mode"] == "new_instance"
        assert config["project_id"] == row.config["project_id"]
        assert config["chain"]["mode"] == "weekly-random"

    @pytest.mark.asyncio
    async def test_the_adapter_waits_for_the_new_instant(self, rearm_engine):
        """After the re-arm the loop stays alive with the new trigger —
        the chain is self-rescheduling inside ONE boot."""
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = _insert_row(rearm_engine, _chain_config(stale))
        adapter, _completions = _build_adapter(rearm_engine, row)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_DIRECT
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        # The one-shot is NOT consumed — the executed flag was reset so
        # the next trigger is the freshly minted instant.
        assert adapter._is_one_time_executed is False
        assert adapter._run_at > datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# A FAILED dispatch does NOT advance the chain
# ══════════════════════════════════════════════════════════════════════════


class TestFailedDispatchDoesNotAdvance:
    @pytest.mark.asyncio
    async def test_a_failed_outcome_leaves_the_chain_alone(
        self, rearm_engine, _fast_retry
    ):
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        original = stale.isoformat()
        row = _insert_row(rearm_engine, _chain_config(stale))
        adapter, completions = _build_adapter(rearm_engine, row)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_FAILED
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        refreshed = _row(rearm_engine)
        assert refreshed is not None
        # The chain did NOT advance — the same key is retried on the 5s
        # cadence (at-most-once within the key's TTL).
        assert refreshed.config["run_at"] == original
        assert adapter._run_at == stale
        assert completions == []

    def test_failed_and_rearmed_are_not_consuming_outcomes(self):
        """``failed`` / ``rearmed`` never consume a one-shot."""
        assert (
            SchedulerAdapter.FIRE_OUTCOME_FAILED
            not in SchedulerAdapter._CONSUMING_FIRE_OUTCOMES
        )
        assert (
            SchedulerAdapter.FIRE_OUTCOME_REARMED
            not in SchedulerAdapter._CONSUMING_FIRE_OUTCOMES
        )
        for outcome in (
            SchedulerAdapter.FIRE_OUTCOME_DIRECT,
            SchedulerAdapter.FIRE_OUTCOME_DEFERRED_DURABLE,
            SchedulerAdapter.FIRE_OUTCOME_REFUSED_PERMANENT,
        ):
            assert outcome in SchedulerAdapter._CONSUMING_FIRE_OUTCOMES


# ══════════════════════════════════════════════════════════════════════════
# The chain-aware skip — a NON-chain one-shot keeps its old behaviour
# ══════════════════════════════════════════════════════════════════════════


class TestNonChainOneShotUnchanged:
    @pytest.mark.asyncio
    async def test_a_plain_one_shot_still_fires_the_completion_callback(
        self, rearm_engine
    ):
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = _insert_row(rearm_engine, _plain_config(stale))
        adapter, completions = _build_adapter(rearm_engine, row)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_DIRECT
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        assert completions == [(SOURCE_ID, True)]
        assert adapter._is_one_time_executed is True

    def test_a_plain_config_is_not_a_chain_row(self, rearm_engine):
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = _insert_row(rearm_engine, _plain_config(stale))
        adapter, _completions = _build_adapter(rearm_engine, row)
        assert adapter._chain_config is None


# ══════════════════════════════════════════════════════════════════════════
# P5 T5.3 — additional legs (appended)
# ══════════════════════════════════════════════════════════════════════════


class TestPersistenceAcrossReconstruction:
    @pytest.mark.asyncio
    async def test_rearmed_run_at_persists_across_adapter_reconstruction(
        self, rearm_engine
    ):
        """The formal proof of P1 open-question #8: a rewritten run_at
        survives adapter reconstruction / boot."""
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        row = _insert_row(rearm_engine, _chain_config(stale))
        adapter, _completions = _build_adapter(rearm_engine, row)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_DIRECT
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        refreshed = _row(rearm_engine)
        assert refreshed is not None
        persisted = datetime.fromisoformat(refreshed.config["run_at"])

        # Reconstruct an adapter from the refreshed row (simulates a boot).
        new_adapter, _ = _build_adapter(rearm_engine, refreshed)
        assert new_adapter._run_at == persisted, (
            "the re-armed run_at did not survive adapter reconstruction"
        )


class TestConfigCompletenessRMW:
    @pytest.mark.asyncio
    async def test_all_identity_keys_survive_rearm_apart_from_run_at(
        self, rearm_engine
    ):
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        original = _chain_config(stale)
        row = _insert_row(rearm_engine, original)
        adapter, _completions = _build_adapter(rearm_engine, row)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_DIRECT
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        refreshed = _row(rearm_engine)
        assert refreshed is not None
        config = refreshed.config
        for key in ("agent", "message", "timezone", "recurrence",
                    "instance_mode", "project_id"):
            assert config[key] == original[key], (
                f"key {key!r} did not survive the re-arm write"
            )
        assert "chain" in config and isinstance(config["chain"], dict)
        assert config["chain"].get("mode") == "weekly-random"
        assert config["run_at"] != original["run_at"]
        assert config["chain"].get("run_at_utc") == config["run_at"]


class TestFailedRunRearm:
    @pytest.mark.asyncio
    async def test_a_failed_run_still_mints_the_next_run_at(
        self, rearm_engine, _fast_retry
    ):
        """STOP-THREAD CANDIDATE: production currently does NOT advance
        the chain on a failed outcome (failed is not a consuming fire).
        This leg asserts the requested behavior and is expected to be RED
        under the current production code."""
        stale = datetime.now(timezone.utc) - timedelta(minutes=5)
        original = stale.isoformat()
        row = _insert_row(rearm_engine, _chain_config(stale))
        adapter, completions = _build_adapter(rearm_engine, row)
        key_before = _key(adapter)

        async def _fake_emit() -> None:
            adapter._fire_outcome = adapter.FIRE_OUTCOME_FAILED
            adapter._stop_event.set()

        adapter._emit_scheduled_message = _fake_emit  # type: ignore[assignment]
        await adapter._run_schedule()

        refreshed = _row(rearm_engine)
        assert refreshed is not None
        new_run_at = datetime.fromisoformat(refreshed.config["run_at"])
        assert new_run_at > datetime.now(timezone.utc), (
            "a failed run did not mint the next run_at"
        )
        assert refreshed.config["run_at"] != original
        key_after = _key(adapter)
        assert key_after != key_before


class TestDistinguishWeeklyRearmFromSameEpisode:
    def test_weekly_rearm_and_same_episode_rearmed_are_distinct(self):
        assert (
            SchedulerAdapter.FIRE_OUTCOME_REARMED
            not in SchedulerAdapter._CONSUMING_FIRE_OUTCOMES
        )
        assert SchedulerAdapter.FIRE_OUTCOME_REARMED != SchedulerAdapter.FIRE_OUTCOME_DIRECT
        assert SchedulerAdapter.FIRE_OUTCOME_REARMED != SchedulerAdapter.FIRE_OUTCOME_FAILED
        import inspect

        src = inspect.getsource(SchedulerAdapter._run_schedule)
        assert "self._fire_outcome in self._CONSUMING_FIRE_OUTCOMES" in src


class TestG2ForceSetPin:
    def test_refresh_chain_keys_force_sets_chain_mode_for_legacy_config(self):
        from daemon.repositories.source.maintenance_seed import (
            CHAIN_MODE,
            refresh_chain_keys,
        )

        legacy_config = {
            "agent": "maintenancer",
            "message": "legacy",
            "timezone": "Asia/Ho_Chi_Minh",
            "recurrence": "once",
            "local_time": "03:00",
            "run_at": datetime.now(timezone.utc).isoformat(),
            "chain": {"mode": "legacy-mode", "run_at_utc": "x"},
        }

        class _Picked:
            run_at_iso = "2026-11-29T03:00:00+00:00"
            local_time = "10:00"

        class _Knobs:
            timezone = "Asia/Ho_Chi_Minh"
            window_start = "01:00"
            window_end = "05:00"
            weekday = 0

        out = refresh_chain_keys(legacy_config, picked=_Picked(), knobs=_Knobs())
        assert out["chain"]["mode"] == CHAIN_MODE == "weekly-random"
        assert out["chain"]["mode"] != "legacy-mode"

    def test_rearm_seam_delegates_to_refresh_chain_keys(self):
        import inspect

        src = inspect.getsource(SchedulerAdapter._rearm_chain_row)
        assert "refresh_chain_keys" in src


class TestBR2ExpiredIdempotencyKey:
    @pytest.mark.asyncio
    async def test_expired_key_creates_second_job_item(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock, patch

        from daemon.services.job_queue_service import JobQueueService

        repo = MagicMock()
        repo.find_by_idempotency_key = MagicMock(return_value=None)
        repo.create = MagicMock()

        def _create_or_get_side_effect(**kwargs):
            key = kwargs.get("idempotency_key")
            existing = repo.find_by_idempotency_key(key)
            if existing is not None:
                return existing, False
            new_job = repo.create(**kwargs)
            return new_job, True

        repo.create_or_get_by_idempotency_key = MagicMock(
            side_effect=_create_or_get_side_effect
        )

        lock_manager = MagicMock()
        queue_repo = MagicMock()

        mock_queue = MagicMock()
        mock_queue.queue_id = "system-fifo-queue-id"
        mock_queue.project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
        mock_queue.queue_name = "system_fifo_queue"

        def _get_by_name(project_id, queue_name):
            if queue_name == "system_fifo_queue":
                return mock_queue
            return None

        queue_repo.get_by_name = MagicMock(side_effect=_get_by_name)
        queue_repo.get = MagicMock(return_value=None)

        svc = JobQueueService(
            repository=repo,
            lock_manager=lock_manager,
            queue_repo=queue_repo,
        )
        mock_config = MagicMock()
        mock_config.idempotency_key_ttl_hours = 24
        svc.set_config(mock_config)

        # Pin the substrate TTL constant was NOT widened.
        assert svc._idempotency_key_ttl_hours == 24

        old_time = datetime.now(timezone.utc) - timedelta(hours=25)
        existing_job = MagicMock()
        existing_job.job_id = "old-job-expired"
        existing_job.agent_id = "developer"
        existing_job.project_id = "71931ae0-0f25-5fbf-853b-2a78cc978d7e"
        existing_job.admission_state = "queued"
        existing_job.message = "test message"
        existing_job.source = "api"
        existing_job.priority = 5
        existing_job.instance_id = None
        existing_job.queue_id = None
        existing_job.idempotency_key = "key-expired"
        existing_job.job_metadata = {}
        existing_job.created_at = old_time.isoformat()

        repo.find_by_idempotency_key.return_value = existing_job

        new_job = MagicMock()
        new_job.job_id = "new-job-expired"
        repo.create.return_value = new_job

        def _make_agent_meta():
            m = MagicMock()
            m.path = "/agents/developer"
            return m

        mock_registry = MagicMock()
        mock_registry.get_resolved.return_value = _make_agent_meta()
        mock_registry.get_version.return_value = None

        with patch("daemon.services.job_queue_service.get_registry") as mock_get_registry:
            mock_get_registry.return_value = mock_registry
            result = await svc.enqueue(
                agent_id="developer",
                message="test message",
                source="api",
                project_id="71931ae0-0f25-5fbf-853b-2a78cc978d7e",
                idempotency_key="key-expired",
            )

        assert result.job_id == "new-job-expired"
        repo.create.assert_called_once()

    def test_idempotency_ttl_constant_not_widened(self):
        from daemon.services.job_queue_service import JobQueueService

        # Read the source default (substrate TTL must stay 24h).
        import inspect

        src = inspect.getsource(JobQueueService.__init__)
        assert "self._idempotency_key_ttl_hours: int = 24" in src
