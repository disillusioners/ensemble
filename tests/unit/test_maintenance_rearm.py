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
from sqlmodel import SQLModel, Session, select

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
