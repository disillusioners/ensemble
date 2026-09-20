"""Phase 4 / plane-integration-revival — hygiene + advisories tests.

Covers the Phase-4 dispatch items:

* ``PLANE_SYNC_ENABLED`` kill-switch (explicitly user-requested,
  sanctioned 2026-09-20): off → every sync-side surface disabled with
  NO state writes; default (unset) → everything available.
* Crash-recovery wedge fix (advisory a): boot sweep recovers rows
  stuck in ``syncing`` beyond N x interval; fresh syncing rows are
  left alone.
* Tightened claim CAS (advisory a(1)): the claim is a rowcount-guarded
  conditional upsert — a stale read can no longer win the slot.
* Adopt-path drift threading (advisory i): the adoption UPDATE
  response feeds the drift check.
* Attempt-counter preservation on read failure (advisory j): a failed
  counter read returns ``None`` and PRESERVES the stored value.

Mocking surface mirrors ``tests/unit/test_plane_sync_phase3.py``:
AsyncMock at the client boundary, in-memory SQLite, monkeypatch for
env vars. NEVER hits real plane.ensem.dev.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.constants import (
    PLANE_ATTEMPT_COUNT_METADATA_KEY,
    PLANE_LAST_ATTEMPT_METADATA_KEY,
    PLANE_PROJECT_ID_METADATA_KEY,
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_DRIFT,
    PLANE_SYNC_STATE_LINKED,
    PLANE_SYNC_STATE_METADATA_KEY,
    PLANE_SYNC_STATE_SYNCING,
)
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
)
from daemon.services.plane_sync_service import (
    PlaneSyncService,
    plane_sync_enabled,
)
from daemon.services.plane_sync_watchdog_service import (
    PlaneSyncWatchdogService,
)
from daemon.tools.plane_sync import (
    _last_sync,
    create_plane_sync_tools,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (mirrors test_plane_sync_phase3.py — file runnable in isolation)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _ = (Project, ProjectMetadataRecord, ProjectShortnameLink)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelProjectRepository:
    return SQLModelProjectRepository(engine)


@pytest.fixture
def mock_plane_env(monkeypatch):
    """Enable the integration AND clear both kill-switches (opt-in
    fixture shadows the conftest autouse clearing, so the switches
    must be cleared explicitly here)."""
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example.com")
    monkeypatch.setenv("PLANE_API_KEY", "test-api-key-xyz")
    monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("PLANE_SYNC_ENABLED", raising=False)
    monkeypatch.delenv("PLANE_MCP_ENABLED", raising=False)
    yield


@pytest.fixture(autouse=True)
def clear_cooldown():
    """Reset the tool-level cooldown dict between tests."""
    _last_sync.clear()
    yield
    _last_sync.clear()


def _make_client_mock() -> MagicMock:
    mock = MagicMock()
    mock.create_project = AsyncMock(return_value={"id": "plane-1"})
    mock.update_project = AsyncMock(return_value={"id": "plane-1"})
    mock.list_projects = AsyncMock(return_value=[])
    mock.get_project = AsyncMock(return_value={"id": "plane-1"})
    return mock


def _seed_state(repo, project_id, key, value):
    with Session(repo.engine) as session:
        repo.set_metadata_record(session, project_id, key, value)
        session.commit()


def _read_meta(repo, project_id) -> dict:
    with Session(repo.engine) as session:
        records = repo.list_metadata_records(session, project_id)
    return {r.meta_key: r.meta_value for r in records}


# ─────────────────────────────────────────────────────────────────────────────
# PLANE_SYNC_ENABLED kill-switch
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncEnabledKillSwitch:
    """PLANE_SYNC_ENABLED — explicitly user-requested integration
    kill-switch (sanctioned 2026-09-20). Default ON; OFF must produce
    the no-key surfaces with NO error-state writes."""

    def test_default_unset_is_enabled(self, monkeypatch):
        monkeypatch.delenv("PLANE_SYNC_ENABLED", raising=False)
        assert plane_sync_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "False", "no", "OFF", " off "])
    def test_falsy_values_disable(self, monkeypatch, value):
        monkeypatch.setenv("PLANE_SYNC_ENABLED", value)
        assert plane_sync_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", ""])
    def test_other_values_keep_enabled(self, monkeypatch, value):
        monkeypatch.setenv("PLANE_SYNC_ENABLED", value)
        assert plane_sync_enabled() is True

    def test_is_available_false_when_switch_off(self, repo, mock_plane_env, monkeypatch):
        """Switch off beats full env config — the gate is independent."""
        monkeypatch.setenv("PLANE_SYNC_ENABLED", "false")
        assert PlaneSyncService.is_available() is False
        assert "PLANE_SYNC_ENABLED" in PlaneSyncService.unavailable_reason()

    def test_sync_project_disabled_without_state_writes(
        self, repo, mock_plane_env, monkeypatch
    ):
        """Switch off + env configured + client mock injected →
        ``disabled``, and the pre-seeded error row is untouched (same
        discipline as the no-key path)."""
        monkeypatch.setenv("PLANE_SYNC_ENABLED", "false")
        project = repo.create(name="SwitchOffProj")
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_ERROR)

        mock = _make_client_mock()
        mock.create_project = AsyncMock(
            side_effect=AssertionError("must not be called when disabled")
        )
        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "disabled"
        assert "PLANE_SYNC_ENABLED" in result["message"]
        mock.list_projects.assert_not_called()

        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_ERROR

    def test_watchdog_noop_with_boot_log(self, repo, mock_plane_env, monkeypatch, caplog):
        """Switch off → sweep_once emits ONE boot log line naming the
        kill-switch and returns an all-zero summary; an error row is
        not mutated."""
        monkeypatch.setenv("PLANE_SYNC_ENABLED", "false")
        project = repo.create(name="SwitchOffWatchdog")
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_ERROR)

        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=1)
        with caplog.at_level(logging.INFO, logger="daemon.services.plane_sync_watchdog_service"):
            summary = asyncio.run(watchdog.sweep_once())

        assert summary["considered"] == 0
        assert summary["re_drove"] == 0
        assert summary["recovered_stale_syncing"] == 0
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_ERROR

        switch_lines = [
            r for r in caplog.records if "PLANE_SYNC_ENABLED" in r.getMessage()
        ]
        assert len(switch_lines) == 1

    def test_watchdog_noop_boot_log_emitted_once(self, repo, mock_plane_env, monkeypatch, caplog):
        """The boot log is a ONE-line-per-watchdog notice, not per-tick."""
        monkeypatch.setenv("PLANE_SYNC_ENABLED", "false")
        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=1)
        with caplog.at_level(logging.INFO, logger="daemon.services.plane_sync_watchdog_service"):
            asyncio.run(watchdog.sweep_once())
            asyncio.run(watchdog.sweep_once())
        switch_lines = [
            r for r in caplog.records if "PLANE_SYNC_ENABLED" in r.getMessage()
        ]
        assert len(switch_lines) == 1

    def test_tool_disabled(self, repo, mock_plane_env, monkeypatch):
        monkeypatch.setenv("PLANE_SYNC_ENABLED", "false")
        tools = create_plane_sync_tools(repo)
        result = tools[0].func(project_id="any-id")
        assert result["status"] == "disabled"
        assert "PLANE_SYNC_ENABLED" in result["message"]

    def test_explicit_true_keeps_available(self, repo, mock_plane_env, monkeypatch):
        monkeypatch.setenv("PLANE_SYNC_ENABLED", "true")
        assert PlaneSyncService.is_available() is True


# ─────────────────────────────────────────────────────────────────────────────
# Crash-recovery: stale ``syncing`` rows (advisory a-2)
# ─────────────────────────────────────────────────────────────────────────────


class TestStaleSyncingRecovery:
    """Boot sweep must recover rows wedged in ``syncing`` (claimer died
    between claim and release) — the wedge class that strands slots."""

    def _seed_syncing(self, repo, project_id, *, attempt_age_seconds):
        _seed_state(repo, project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_SYNCING)
        stamp = datetime.now(timezone.utc) - timedelta(seconds=attempt_age_seconds)
        _seed_state(repo, project_id, PLANE_LAST_ATTEMPT_METADATA_KEY,
                    stamp.isoformat())

    def test_stale_syncing_recovered_and_re_driven(self, repo, mock_plane_env):
        project = repo.create(name="WedgedRow")
        self._seed_syncing(repo, project.project_id, attempt_age_seconds=600)

        mock = _make_client_mock()
        mock.list_projects = AsyncMock(
            return_value=[{"id": "plane-1", "name": "WedgedRow"}]
        )
        mock.update_project = AsyncMock(
            return_value={"id": "plane-1", "name": "WedgedRow"}
        )
        sync_service = PlaneSyncService(repo, http_client=mock)
        # interval=1 → stale threshold 2s; attempt is 600s old → stale.
        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=1,
                                            sync_service=sync_service)
        summary = asyncio.run(watchdog.sweep_once())

        assert summary["recovered_stale_syncing"] == 1
        assert summary["re_drove"] == 1
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_LINKED

    def test_fresh_syncing_row_not_stolen(self, repo, mock_plane_env):
        """A healthy in-flight sync (attempt seconds old) is untouched —
        no false steal, no double-drive."""
        project = repo.create(name="LiveSyncRow")
        self._seed_syncing(repo, project.project_id, attempt_age_seconds=0)

        mock = _make_client_mock()
        sync_service = PlaneSyncService(repo, http_client=mock)
        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=300,
                                            sync_service=sync_service)
        summary = asyncio.run(watchdog.sweep_once())

        assert summary["recovered_stale_syncing"] == 0
        assert summary["re_drove"] == 0
        mock.list_projects.assert_not_called()
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_SYNCING

    def test_missing_attempt_timestamp_counts_as_stale(self, repo, mock_plane_env):
        """Claim stamps the attempt immediately after taking the slot —
        a syncing row with NO attempt stamp died between the two writes."""
        project = repo.create(name="NoStampRow")
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_SYNCING)

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_stale_syncing(
            project.project_id, stale_after_seconds=2
        ) is True

    def test_fail_stale_syncing_is_cas_guarded(self, repo, mock_plane_env):
        """The steal only fires while the row is STILL ``syncing`` — a
        sync that released between check and steal is not clobbered."""
        project = repo.create(name="RaceRow")
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_LINKED)

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.fail_stale_syncing(project.project_id) is False
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_LINKED


# ─────────────────────────────────────────────────────────────────────────────
# Tightened claim CAS (advisory a-1)
# ─────────────────────────────────────────────────────────────────────────────


class TestClaimCas:
    """claim_sync_slot is a rowcount-guarded conditional upsert."""

    def test_claim_inserts_when_row_missing(self, repo, mock_plane_env):
        project = repo.create(name="FreshClaim")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.claim_sync_slot(project.project_id) is True
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_SYNCING

    def test_claim_from_linked_state(self, repo, mock_plane_env):
        project = repo.create(name="LinkedClaim")
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_LINKED)
        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.claim_sync_slot(project.project_id) is True

    def test_second_claim_refused_while_syncing(self, repo, mock_plane_env):
        """Rowcount-guarded: once the slot is held, ANY further claim is
        refused — including from a caller holding a STALE read."""
        project = repo.create(name="HeldSlot")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.claim_sync_slot(project.project_id) is True
        assert svc.claim_sync_slot(project.project_id) is False

    def test_stale_read_cannot_win_the_slot(self, repo, mock_plane_env, monkeypatch):
        """THE race pin: a claimer whose metadata READ predates another
        caller's claim must still be refused. Under the old
        read-check-write implementation this succeeded (TOCTOU); under
        the conditional upsert the DB row is the authority."""
        project = repo.create(name="StaleReader")
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_SYNCING)

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        # Simulate a stale cached read that says "linked".
        monkeypatch.setattr(
            svc,
            "get_state_metadata",
            lambda pid: {PLANE_SYNC_STATE_METADATA_KEY: PLANE_SYNC_STATE_LINKED},
        )
        assert svc.claim_sync_slot(project.project_id) is False

    def test_claim_fails_closed_on_db_error(self, repo, mock_plane_env, monkeypatch):
        """Guard-write failure → no claim (fail-closed): the sync must
        not run without a durable re-entrancy claim."""
        project = repo.create(name="DbFailClaim")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())

        def boom(session):
            raise RuntimeError("db down")

        monkeypatch.setattr(repo, "_get_dialect_insert", boom)
        assert svc.claim_sync_slot(project.project_id) is False

    def test_endpoint_claim_release_cycle_still_works(self, repo, mock_plane_env):
        """The endpoint contract (claim → sync with claim_slot=False →
        release) is preserved by the CAS rewrite."""
        project = repo.create(name="CycleRow")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.claim_sync_slot(project.project_id) is True
        result = asyncio.run(
            svc.sync_project(project.project_id, claim_slot=False)
        )
        assert result["status"] == "linked"
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_LINKED


# ─────────────────────────────────────────────────────────────────────────────
# Adopt-path drift threading (advisory i)
# ─────────────────────────────────────────────────────────────────────────────


class TestAdoptPathDrift:
    """Adoption sync previously hardcoded ``plane_response = {}`` so a
    Plane-side divergence at adoption time was invisible (state
    mis-marked ``linked``)."""

    def test_adopt_with_divergent_response_flags_drift(self, repo, mock_plane_env):
        project = repo.create(name="AdoptDrift", description="d")
        # No plane_project_id → CREATE/adopt path. Plane has a match.
        mock = _make_client_mock()
        mock.list_projects = AsyncMock(
            return_value=[{"id": "plane-existing", "name": "AdoptDrift"}]
        )
        # The adoption UPDATE response disagrees on identity → drift.
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "OTHER", "description": "d"}
        )
        mock.create_project = AsyncMock(
            side_effect=AssertionError("adopt path must not create")
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == PLANE_SYNC_STATE_DRIFT
        assert result["action"] == "updated"
        meta = _read_meta(repo, project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_DRIFT
        assert meta[PLANE_PROJECT_ID_METADATA_KEY] == "plane-existing"

    def test_adopt_with_agreeing_response_is_linked(self, repo, mock_plane_env):
        project = repo.create(name="AdoptOk", description="d")
        mock = _make_client_mock()
        mock.list_projects = AsyncMock(
            return_value=[{"id": "plane-existing", "name": "AdoptOk"}]
        )
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "AdoptOk", "description": "d"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == PLANE_SYNC_STATE_LINKED

    def test_fresh_create_still_trivially_linked(self, repo, mock_plane_env):
        """No Plane match → CREATE path → ``plane_response`` is {} →
        no drift (fresh row trivially agrees)."""
        project = repo.create(name="FreshCreate")
        mock = _make_client_mock()
        mock.list_projects = AsyncMock(return_value=[])
        mock.create_project = AsyncMock(return_value={"id": "plane-new"})

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == PLANE_SYNC_STATE_LINKED
        assert result["action"] == "created"


# ─────────────────────────────────────────────────────────────────────────────
# Attempt-counter preservation on read failure (advisory j)
# ─────────────────────────────────────────────────────────────────────────────


class TestAttemptCounterPreservation:
    """A failed counter READ must not reset the stored counter."""

    def test_read_failure_returns_none_and_preserves_value(
        self, repo, mock_plane_env, monkeypatch
    ):
        project = repo.create(name="CounterPreserve")
        _seed_state(repo, project.project_id,
                    PLANE_ATTEMPT_COUNT_METADATA_KEY, 3)
        _seed_state(repo, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                    PLANE_SYNC_STATE_ERROR)

        svc = PlaneSyncService(repo, http_client=_make_client_mock())

        def broken_list(session, pid):
            raise RuntimeError("read failed")

        monkeypatch.setattr(repo, "list_metadata_records", broken_list)
        assert svc._bump_attempt_count(project.project_id) is None

        # The stored value is PRESERVED (old code reset it to 1) —
        # verify via a direct select (the repo method stays patched).
        with Session(repo.engine) as session:
            rec = session.exec(
                select(ProjectMetadataRecord).where(
                    ProjectMetadataRecord.project_id == project.project_id,
                    ProjectMetadataRecord.meta_key
                    == PLANE_ATTEMPT_COUNT_METADATA_KEY,
                )
            ).first()
        assert rec is not None
        assert rec.meta_value == 3

    def test_error_path_response_carries_attempt_none_on_read_failure(
        self, repo, mock_plane_env, monkeypatch
    ):
        """The sync_project error handlers surface attempt=None (not a
        fabricated 1) when the counter read failed.

        The read failure is injected ONLY at the counter call (3rd
        ``list_metadata_records`` call: 1 = project load enrichment,
        2 = sync's pre-claim metadata read, 3 = counter read) — earlier
        callers must still succeed for the flow to reach the error
        handler.
        """
        project = repo.create(name="AttemptNone")
        _seed_state(repo, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                    "plane-existing")

        mock = _make_client_mock()
        mock.update_project = AsyncMock(side_effect=Exception("plane down"))
        svc = PlaneSyncService(repo, http_client=mock)

        real_list = repo.list_metadata_records
        calls = {"n": 0}

        def flaky_list(session, pid):
            calls["n"] += 1
            if calls["n"] >= 3:
                raise RuntimeError("read failed")
            return real_list(session, pid)

        monkeypatch.setattr(repo, "list_metadata_records", flaky_list)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert result["attempt"] is None
        assert calls["n"] == 3
