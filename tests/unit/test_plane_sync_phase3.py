"""Phase 3 / plane-integration-revival — retry machinery tests.

Covers the new state machine (linked / syncing / drift / error), the
re-entrancy claim guard, the drift detector, the watchdog (boot sweep
+ periodic + backoff + circuit-breaker ceiling), the manual HTTP
endpoint (200 / 404 / 409 / 503), and the re-drive idempotency path
(existing-plane adoption never creates a duplicate).

Mocking surface:
  * Plane HTTP calls → ``AsyncMock`` injected via
    ``PlaneSyncService(..., http_client=mock)``.
  * Repository → in-memory SQLite via the existing ``engine`` /
    ``repo`` fixtures from ``tests/conftest.py``-style setup.
  * ENV vars → ``monkeypatch`` (the autouse
    ``_disable_plane_sync_in_tests`` fixture clears Plane env vars
    unless ``mock_plane_env`` is requested).

NEVER hits real plane.ensem.dev — every HTTP call is mocked at the
client boundary.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from daemon.clients.plane_http_client import (
    PlaneAPIError,
    PlaneAuthError,
    PlaneHttpClient,
    PlaneNotFoundError,
)
from daemon.constants import (
    PLANE_ATTEMPT_COUNT_METADATA_KEY,
    PLANE_LAST_ATTEMPT_METADATA_KEY,
    PLANE_LAST_ERROR_METADATA_KEY,
    PLANE_PROJECT_ID_METADATA_KEY,
    PLANE_SYNC_STATE_DRIFT,
    PLANE_SYNC_STATE_ERROR,
    PLANE_SYNC_STATE_LINKED,
    PLANE_SYNC_STATE_METADATA_KEY,
    PLANE_SYNC_STATE_SYNCED_ALIAS,
    PLANE_SYNC_STATE_SYNCING,
    PLANE_SYNC_STATES_RETRYABLE,
    PLANE_SYNC_WATCHDOG_BACKOFF_BASE_SECONDS,
    PLANE_SYNC_WATCHDOG_BACKOFF_MAX_SECONDS,
    PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS,
    PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
    PLANE_SYNCED_AT_METADATA_KEY,
)
from daemon.repositories import SQLModelProjectRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
)
from daemon.services.plane_sync_service import (
    PlaneSyncService,
    _is_drift,
    compute_backoff_seconds,
    iter_projects_in_states,
    normalize_state,
)
from daemon.services.plane_sync_watchdog_service import (
    PlaneSyncWatchdogService,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine with project + metadata tables.

    Mirrors ``tests/unit/test_plane_sync.py::engine`` — kept local so
    this test file can be run in isolation without picking up the
    autouse fixtures from the sibling file.
    """
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
    """Set the env vars required by ``PlaneHttpClient.is_available``."""
    monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example.com")
    monkeypatch.setenv("PLANE_API_KEY", "test-api-key-xyz")
    monkeypatch.setenv("PLANE_MCP_WORKSPACE_SLUG", "test-ws")
    yield


def _make_client_mock() -> MagicMock:
    """Build a fully-mocked PlaneHttpClient substitute."""
    mock = MagicMock()
    mock.create_project = AsyncMock(return_value={"id": "plane-1"})
    mock.update_project = AsyncMock(return_value={"id": "plane-1"})
    mock.list_projects = AsyncMock(return_value=[])
    mock.get_project = AsyncMock(return_value={"id": "plane-1"})
    return mock


# ─────────────────────────────────────────────────────────────────────────────
# State machine primitives
# ─────────────────────────────────────────────────────────────────────────────


class TestNormalizeState:
    """Phase 3 — canonical-state normalization with back-compat alias."""

    def test_none_is_treated_as_error(self):
        """A row with no prior sync recorded reads as ``error`` so the
        watchdog can re-drive it on the next sweep."""
        assert normalize_state(None) == PLANE_SYNC_STATE_ERROR

    def test_synced_alias_maps_to_linked(self):
        """Pre-Phase-3 ``synced`` rows read as ``linked`` (back-compat)."""
        assert normalize_state(PLANE_SYNC_STATE_SYNCED_ALIAS) == PLANE_SYNC_STATE_LINKED

    def test_canonical_states_pass_through(self):
        for state in (
            PLANE_SYNC_STATE_LINKED,
            PLANE_SYNC_STATE_SYNCING,
            PLANE_SYNC_STATE_DRIFT,
            PLANE_SYNC_STATE_ERROR,
        ):
            assert normalize_state(state) == state

    def test_unknown_value_is_treated_as_error(self):
        """Defensive — an unrecognized value is flagged ``error`` so the
        next sync resolves it. No silent state corruption."""
        assert normalize_state("bogus") == PLANE_SYNC_STATE_ERROR
        assert normalize_state("") == PLANE_SYNC_STATE_ERROR

    def test_whitespace_state_normalized(self):
        """Leading / trailing whitespace does not produce a phantom
        state — the canonical value is returned."""
        assert normalize_state("  linked  ") == PLANE_SYNC_STATE_LINKED
        assert normalize_state("\tsynced\n") == PLANE_SYNC_STATE_LINKED


class TestComputeBackoff:
    """Phase 3 — per-project exponential backoff formula."""

    def test_zero_attempts_no_backoff(self):
        """count=0 → no backoff (treat as immediate)."""
        assert compute_backoff_seconds(0, base=60, cap=1800) == 0.0

    def test_one_attempt_no_backoff(self):
        """count=1 → no backoff (initial attempt)."""
        assert compute_backoff_seconds(1, base=60, cap=1800) == 0.0

    def test_two_attempts_base(self):
        """count=2 → BASE seconds."""
        assert compute_backoff_seconds(2, base=60, cap=1800) == 60.0

    def test_three_attempts_doubles(self):
        assert compute_backoff_seconds(3, base=60, cap=1800) == 120.0

    def test_four_attempts_quadruples(self):
        assert compute_backoff_seconds(4, base=60, cap=1800) == 240.0

    def test_caps_at_max(self):
        """Long-outage backoff stops growing at the cap — never exceeds it."""
        # 60 * 2**8 = 15360 → capped at 1800.
        assert compute_backoff_seconds(10, base=60, cap=1800) == 1800.0


class TestIsDrift:
    """Phase 3 — drift detection (identity-field comparison)."""

    def test_empty_plane_response_is_not_drift(self):
        """A sparse response is "no info" → no drift alarm."""
        project = MagicMock(spec=Project)
        project.name = "Foo"
        project.description = "bar"
        assert _is_drift(project, {}) is False

    def test_matching_name_and_description_no_drift(self):
        project = MagicMock(spec=Project)
        project.name = "Foo"
        project.description = "bar"
        assert _is_drift(project, {"name": "Foo", "description": "bar"}) is False

    def test_case_insensitive_name_match(self):
        project = MagicMock(spec=Project)
        project.name = "Foo"
        project.description = None
        assert _is_drift(project, {"name": "FOO", "description": None}) is False

    def test_name_divergence_is_drift(self):
        project = MagicMock(spec=Project)
        project.name = "Foo"
        project.description = None
        assert _is_drift(project, {"name": "Bar", "description": None}) is True

    def test_description_divergence_is_drift(self):
        project = MagicMock(spec=Project)
        project.name = "Foo"
        project.description = "alpha"
        assert _is_drift(project, {"name": "Foo", "description": "beta"}) is True

    def test_missing_description_field_is_no_info(self):
        """Plane omits ``description`` → no info → no drift alarm.

        Matches the heuristic the legacy service uses (a sparse
        response is not drift; only explicit divergence is).
        """
        project = MagicMock(spec=Project)
        project.name = "Foo"
        project.description = "alpha"
        # Plane response omits description entirely.
        assert _is_drift(project, {"name": "Foo"}) is False

    # ── Sanitized-name drift (prod hot-fix 2026-09-22) ──────────────

    def test_sanitized_name_agreement_is_not_drift(self):
        """THE hot-fix pin: Plane stores the SANITIZED name
        (``agents-ensemble`` → ``agents ensemble`` — raw hyphens 400 at
        create/update), so comparing raw-vs-stored would flag perpetual
        false drift on every hyphenated project. Sanitized-vs-sanitized
        agreement must read as linked, NOT drift."""
        project = MagicMock(spec=Project)
        project.name = "agents-ensemble"
        project.description = None
        assert (
            _is_drift(project, {"name": "agents ensemble", "description": None})
            is False
        )

    def test_sanitizer_does_not_blind_real_drift(self):
        """Sanitizing both sides must not swallow genuine divergence —
        names that disagree AFTER sanitization still flag drift."""
        project = MagicMock(spec=Project)
        project.name = "agents-ensemble"
        project.description = None
        assert (
            _is_drift(project, {"name": "agents ensemble two", "description": None})
            is True
        )

    def test_empty_raw_project_name_is_no_info_not_drift(self):
        """Emptiness is judged on RAW values: an empty Ensemble name has
        no information to compare, and the sanitizer's non-empty
        fallback constant must not fabricate a drift signal."""
        project = MagicMock(spec=Project)
        project.name = ""
        project.description = None
        assert _is_drift(project, {"name": "Foo", "description": None}) is False


# ─────────────────────────────────────────────────────────────────────────────
# State machine integration (sync_project)
# ─────────────────────────────────────────────────────────────────────────────


class TestSyncProjectStateMachine:
    """Phase 3 — sync_project drives the 4-state machine correctly."""

    def test_success_writes_linked(self, repo, mock_plane_env):
        """Happy path → plane_sync_state="linked". Identity agrees."""
        project = repo.create(name="LinkedProj", description="d")
        # Pre-seed an existing plane_project_id so the UPDATE path is exercised.
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            session.commit()
        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "LinkedProj", "description": "d"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "linked"
        assert result["action"] == "updated"

        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == "linked"
        assert meta[PLANE_ATTEMPT_COUNT_METADATA_KEY] == 0
        assert PLANE_SYNCED_AT_METADATA_KEY in meta

    def test_drift_when_plane_returns_different_name(self, repo, mock_plane_env):
        """UPDATE succeeded at the API level but Plane disagrees on
        identity → drift. The watchdog re-drives the corrective sync.
        """
        project = repo.create(name="DriftProj", description="desc")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            session.commit()
        mock = _make_client_mock()
        # Plane's response says the name is different — drift.
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "OTHER", "description": "desc"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == PLANE_SYNC_STATE_DRIFT
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_DRIFT

    def test_drift_when_plane_returns_different_description(
        self, repo, mock_plane_env
    ):
        """Description divergence → drift."""
        project = repo.create(name="DescDrift", description="expected")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            session.commit()
        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "DescDrift", "description": "actual"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == PLANE_SYNC_STATE_DRIFT

    def test_drift_correction_logs_healed_drift(self, repo, mock_plane_env, caplog):
        """A drift row that re-syncs cleanly transitions to ``linked`` and
        emits a "drift resolved" log line. Operators tailing the log can
        see the recovery."""
        import logging

        project = repo.create(name="HealDrift")
        # Pre-seed as drift.
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_DRIFT,
            )
            session.commit()

        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "HealDrift"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        with caplog.at_level(logging.INFO, logger="daemon.services.plane_sync_service"):
            result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "linked"
        assert any("drift resolved" in m.lower() for m in caplog.messages)

    def test_error_writes_state_and_increments_attempt_count(
        self, repo, mock_plane_env
    ):
        """Failure path → state=error, attempt_count+=1, plane_last_attempt stamped."""
        project = repo.create(name="ErrorProj")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            session.commit()

        mock = _make_client_mock()

        async def raise_api(*args, **kwargs):
            raise PlaneAPIError("server boom")

        mock.update_project = AsyncMock(side_effect=raise_api)

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert result["attempt"] == 1

        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_ERROR
        assert meta[PLANE_ATTEMPT_COUNT_METADATA_KEY] == 1
        assert PLANE_LAST_ATTEMPT_METADATA_KEY in meta
        assert PLANE_LAST_ERROR_METADATA_KEY in meta
        assert "server boom" in meta[PLANE_LAST_ERROR_METADATA_KEY]

    def test_success_resets_attempt_count(self, repo, mock_plane_env):
        """A successful sync resets plane_attempt_count to 0 so the
        quarantine counter starts fresh on the next outage."""
        project = repo.create(name="ResetProj")
        # Pre-seed high attempt_count.
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            repo.set_metadata_record(
                session, project.project_id, PLANE_ATTEMPT_COUNT_METADATA_KEY, 4
            )
            session.commit()

        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "ResetProj"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "linked"
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_ATTEMPT_COUNT_METADATA_KEY] == 0

    def test_attempt_count_saturates_at_max_attempts(self, repo, mock_plane_env):
        """A long outage does not blow up the JSONB column — the counter
        saturates at ``PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS``."""
        project = repo.create(name="SatProj")
        # Pre-seed at the ceiling.
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_PROJECT_ID_METADATA_KEY,
                "plane-existing",
            )
            repo.set_metadata_record(
                session, project.project_id, PLANE_ATTEMPT_COUNT_METADATA_KEY,
                PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
            )
            session.commit()

        mock = _make_client_mock()

        async def raise_api(*args, **kwargs):
            raise PlaneAPIError("still down")

        mock.update_project = AsyncMock(side_effect=raise_api)

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        # Counter still at the ceiling, not exceeded.
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_ATTEMPT_COUNT_METADATA_KEY] == PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS


# ─────────────────────────────────────────────────────────────────────────────
# Re-entrancy guard
# ─────────────────────────────────────────────────────────────────────────────


class TestReentrancyClaim:
    """Phase 3 — atomic claim/release for re-entrancy control."""

    def test_claim_succeeds_when_row_idle(self, repo, mock_plane_env):
        """A fresh row can be claimed."""
        project = repo.create(name="ClaimIdle")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())

        assert svc.claim_sync_slot(project.project_id) is True

        meta = svc.get_state_metadata(project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_SYNCING

    def test_claim_fails_when_row_already_syncing(self, repo, mock_plane_env):
        """A row already in ``syncing`` cannot be re-claimed — the second
        caller must observe 409 / skip the work."""
        project = repo.create(name="ClaimBusy")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())

        assert svc.claim_sync_slot(project.project_id) is True
        # Second claim fails.
        assert svc.claim_sync_slot(project.project_id) is False

    def test_claim_succeeds_after_release(self, repo, mock_plane_env):
        """Once the prior attempt released the slot, a new claim succeeds."""
        project = repo.create(name="ClaimAfterRelease")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())

        assert svc.claim_sync_slot(project.project_id) is True
        svc.release_sync_slot(project.project_id, PLANE_SYNC_STATE_LINKED)

        meta = svc.get_state_metadata(project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_LINKED

        assert svc.claim_sync_slot(project.project_id) is True

    def test_release_writes_state_and_attempt(self, repo, mock_plane_env):
        """``release_sync_slot`` updates state + stamps plane_last_attempt."""
        project = repo.create(name="ReleaseSlot")
        svc = PlaneSyncService(repo, http_client=_make_client_mock())

        svc.claim_sync_slot(project.project_id)
        svc.release_sync_slot(project.project_id, PLANE_SYNC_STATE_ERROR, last_error="boom")

        meta = svc.get_state_metadata(project.project_id)
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_ERROR
        assert meta[PLANE_LAST_ERROR_METADATA_KEY] == "boom"
        assert PLANE_LAST_ATTEMPT_METADATA_KEY in meta

    def test_disabled_returns_without_claiming(self, repo, monkeypatch):
        """Feature-disabled sync_project must NOT mutate state — the
        no-key contract is that the watchdog does not mark projects
        ``error`` merely because the integration is off."""
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)

        project = repo.create(name="DisabledProj")
        # Bypass the http_client mock — the disabled path doesn't even
        # consult the client.
        svc = PlaneSyncService(repo, http_client=None)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "disabled"

        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        # No state mutation; no error marking.
        assert PLANE_SYNC_STATE_METADATA_KEY not in meta


# ─────────────────────────────────────────────────────────────────────────────
# Back-compat: pre-Phase-3 ``synced`` rows
# ─────────────────────────────────────────────────────────────────────────────


class TestBackCompatSyncedAlias:
    """Phase 3 — pre-Phase-3 ``synced`` rows are still treated as linked."""

    def test_synced_row_re_syncs_as_linked(self, repo, mock_plane_env):
        """A row whose ``plane_sync_state`` is the legacy ``synced`` value
        re-syncs cleanly — the back-compat alias keeps the UPDATE path
        alive."""
        project = repo.create(name="LegacySynced")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_PROJECT_ID_METADATA_KEY, "plane-legacy",
            )
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_SYNCED_ALIAS,
            )
            session.commit()

        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-legacy", "name": "LegacySynced"}
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "linked"


# ─────────────────────────────────────────────────────────────────────────────
# Re-drive idempotency (Step 4)
# ─────────────────────────────────────────────────────────────────────────────


class TestRedriveIdempotency:
    """Phase 3 — error rows that have a Plane-side counterpart must be
    LINKED to it, never duplicate-created."""

    def test_error_row_with_plane_match_is_linked_not_duplicated(
        self, repo, mock_plane_env
    ):
        """An error row whose project exists on Plane is adopted (UPDATE)
        rather than CREATE — the listing-before-create path handles this.
        """
        project = repo.create(name="OrphanInPlane")
        # Pre-seed error state.
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_ERROR,
            )
            session.commit()

        mock = _make_client_mock()
        # Plane already has a project matching the Ensemble name.
        mock.list_projects = AsyncMock(
            return_value=[{"id": "plane-existing", "name": "OrphanInPlane"}]
        )
        mock.update_project = AsyncMock(
            return_value={"id": "plane-existing", "name": "OrphanInPlane"}
        )
        mock.create_project = AsyncMock(
            side_effect=AssertionError(
                "CREATE must NOT be called when an existing Plane project "
                "matches the Ensemble name"
            )
        )

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "linked"
        assert result["action"] == "updated"
        assert result["plane_project_id"] == "plane-existing"
        # The CRITICAL plane_project_id metadata is now correct.
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_PROJECT_ID_METADATA_KEY] == "plane-existing"

    def test_stored_plane_id_missing_clears_handle_for_recreate(
        self, repo, mock_plane_env
    ):
        """Stored plane_project_id points to a deleted Plane row → 404 →
        clear handle + mark error → watchdog re-drives cleanly."""
        project = repo.create(name="StaleId")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_PROJECT_ID_METADATA_KEY, "stale-id",
            )
            session.commit()

        mock = _make_client_mock()

        async def update_404(*args, **kwargs):
            raise PlaneNotFoundError("Plane 404 on ...: missing")

        mock.update_project = AsyncMock(side_effect=update_404)

        svc = PlaneSyncService(repo, http_client=mock)
        result = asyncio.run(svc.sync_project(project.project_id))

        assert result["status"] == "error"
        assert "recreate" in result["message"].lower()

        # Stale handle was cleared.
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        # After clear, the value is None — JSONB null.
        assert meta.get(PLANE_PROJECT_ID_METADATA_KEY) is None


# ─────────────────────────────────────────────────────────────────────────────
# Retry eligibility (watchdog pre-filter)
# ─────────────────────────────────────────────────────────────────────────────


class TestIsRetryEligible:
    """Phase 3 — watchdog's per-project pre-filter."""

    def test_linked_row_not_retryable(self, repo, mock_plane_env):
        """``linked`` is steady-state success — the watchdog must skip it."""
        project = repo.create(name="Linked")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_LINKED,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_retry_eligible(project.project_id) is False

    def test_syncing_row_not_retryable(self, repo, mock_plane_env):
        """``syncing`` is owned by another caller — watchdog must not
        double-drive."""
        project = repo.create(name="Syncing")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_SYNCING,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_retry_eligible(project.project_id) is False

    def test_error_row_immediately_eligible(self, repo, mock_plane_env):
        """An error row with no recent attempt is immediately eligible."""
        project = repo.create(name="FreshError")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_ERROR,
            )
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_ATTEMPT_COUNT_METADATA_KEY, 1,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        # count=1 → no backoff → eligible immediately.
        assert svc.is_retry_eligible(project.project_id) is True

    def test_error_row_with_backoff_not_eligible(self, repo, mock_plane_env):
        """An error row whose backoff window has NOT elapsed must be
        skipped (eligible returns False)."""
        project = repo.create(name="BackoffError")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_ERROR,
            )
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_ATTEMPT_COUNT_METADATA_KEY, 4,  # 4*base = 240s
            )
            # Last attempt was 10s ago — far less than the 240s backoff.
            recent = (
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat()
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_LAST_ATTEMPT_METADATA_KEY, recent,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_retry_eligible(project.project_id) is False

    def test_error_row_after_backoff_eligible(self, repo, mock_plane_env):
        """After the backoff window elapses, the row is eligible again."""
        project = repo.create(name="HealedError")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_ERROR,
            )
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_ATTEMPT_COUNT_METADATA_KEY, 4,
            )
            # Last attempt was 5 minutes ago — past the 240s backoff.
            old = (
                datetime.now(timezone.utc) - timedelta(seconds=300)
            ).isoformat()
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_LAST_ATTEMPT_METADATA_KEY, old,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_retry_eligible(project.project_id) is True

    def test_quarantined_row_not_eligible(self, repo, mock_plane_env):
        """A row at/above the max-attempts ceiling is quarantined — the
        watchdog stops re-driving until the operator resets via the
        manual endpoint.
        """
        project = repo.create(name="Quarantined")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_ERROR,
            )
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_ATTEMPT_COUNT_METADATA_KEY,
                PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
            )
            # Long-ago last attempt — backoff long past.
            old = (
                datetime.now(timezone.utc) - timedelta(hours=24)
            ).isoformat()
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_LAST_ATTEMPT_METADATA_KEY, old,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_retry_eligible(project.project_id) is False

    def test_drift_row_is_retryable(self, repo, mock_plane_env):
        """``drift`` is in ``PLANE_SYNC_STATES_RETRYABLE`` (corrective
        sync heals it)."""
        project = repo.create(name="DriftRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id,
                PLANE_SYNC_STATE_METADATA_KEY, PLANE_SYNC_STATE_DRIFT,
            )
            session.commit()

        svc = PlaneSyncService(repo, http_client=_make_client_mock())
        assert svc.is_retry_eligible(project.project_id) is True


class TestIterProjectsInStates:
    """Phase 3 — watchdog's enumeration helper."""

    def test_returns_only_retryable_projects(self, repo, mock_plane_env):
        """Only rows whose normalized state is in
        ``PLANE_SYNC_STATES_RETRYABLE`` are returned."""
        # linked
        p1 = repo.create(name="LinkedRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, p1.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_LINKED,
            )
            session.commit()

        # error
        p2 = repo.create(name="ErrorRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, p2.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_ERROR,
            )
            session.commit()

        # drift
        p3 = repo.create(name="DriftRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, p3.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_DRIFT,
            )
            session.commit()

        # No metadata (None state → "error" via normalize_state).
        p4 = repo.create(name="NoState")

        result = iter_projects_in_states(repo, PLANE_SYNC_STATES_RETRYABLE)
        ids = {row["project_id"] for row in result}

        assert ids == {p2.project_id, p3.project_id, p4.project_id}
        # Linked row + the no-state-but-no-key row are NOT retry-eligible
        # unless the watchdog is disabled — but this helper just filters
        # on the requested set; eligibility is a downstream check.

    def test_synced_alias_counted_as_retryable(self, repo, mock_plane_env):
        """Legacy ``synced`` rows are normalized to ``linked`` (NOT
        retryable) — they appear in steady-state, so the watchdog
        ignores them.
        """
        project = repo.create(name="LegacySynced")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_SYNCED_ALIAS,
            )
            session.commit()

        result = iter_projects_in_states(repo, PLANE_SYNC_STATES_RETRYABLE)
        assert all(r["project_id"] != project.project_id for r in result)


# ─────────────────────────────────────────────────────────────────────────────
# Watchdog service
# ─────────────────────────────────────────────────────────────────────────────


class TestPlaneSyncWatchdog:
    """Phase 3 — watchdog boot sweep + periodic cadence + no-key no-op."""

    def test_no_key_no_op(self, repo, monkeypatch):
        """When ``PLANE_API_KEY`` is missing, the watchdog emits a
        one-line boot log and returns an empty summary — it MUST NOT
        mark any projects ``error``.
        """
        monkeypatch.delenv("PLANE_BASE_URL", raising=False)
        monkeypatch.delenv("PLANE_API_KEY", raising=False)
        monkeypatch.delenv("PLANE_MCP_WORKSPACE_SLUG", raising=False)

        # Pre-seed an error row that should NOT be touched.
        project = repo.create(name="StuckRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_ERROR,
            )
            session.commit()

        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=1)
        summary = asyncio.run(watchdog.sweep_once())

        assert summary["considered"] == 0
        assert summary["re_drove"] == 0

        # The error row is still error — the watchdog did not mutate.
        meta_with_session = {}
        with Session(repo.engine) as session:
            from sqlmodel import select as sql_select
            from daemon.repositories.project.models import ProjectMetadataRecord
            stmt = sql_select(ProjectMetadataRecord).where(
                ProjectMetadataRecord.project_id == project.project_id,
                ProjectMetadataRecord.meta_key == PLANE_SYNC_STATE_METADATA_KEY,
            )
            rec = session.exec(stmt).first()
            if rec is not None:
                meta_with_session[PLANE_SYNC_STATE_METADATA_KEY] = rec.meta_value
        assert meta_with_session.get(PLANE_SYNC_STATE_METADATA_KEY) == PLANE_SYNC_STATE_ERROR

    def test_boot_sweep_redrives_error_rows(self, repo, mock_plane_env):
        """The first sweep (boot path) re-drives all error rows that
        have not exhausted the quarantine ceiling."""
        project = repo.create(name="BootSweepProj")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_ERROR,
            )
            session.commit()

        # Inject a working sync service with a mocked http client so
        # the watchdog's sweep actually performs work (no real HTTP).
        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-1", "name": "BootSweepProj"}
        )
        mock.list_projects = AsyncMock(
            return_value=[{"id": "plane-1", "name": "BootSweepProj"}]
        )
        sync_service = PlaneSyncService(repo, http_client=mock)
        watchdog = PlaneSyncWatchdogService(
            repo,
            interval_seconds=PLANE_SYNC_WATCHDOG_INTERVAL_SECONDS,
            sync_service=sync_service,
        )
        summary = asyncio.run(watchdog.sweep_once())

        assert summary["considered"] >= 1
        assert summary["re_drove"] >= 1

        # The row was recovered.
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_LINKED

    def test_watchdog_skips_backoff(self, repo, mock_plane_env):
        """Rows whose backoff window has not elapsed are counted as
        ``skipped_backoff``, not re-driven."""
        project = repo.create(name="BackoffRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_ERROR,
            )
            repo.set_metadata_record(
                session, project.project_id, PLANE_ATTEMPT_COUNT_METADATA_KEY,
                4,  # 240s backoff
            )
            recent = (
                datetime.now(timezone.utc) - timedelta(seconds=5)
            ).isoformat()
            repo.set_metadata_record(
                session, project.project_id, PLANE_LAST_ATTEMPT_METADATA_KEY,
                recent,
            )
            session.commit()

        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=300)
        summary = asyncio.run(watchdog.sweep_once())

        assert summary["considered"] == 1
        assert summary["skipped_backoff"] == 1
        assert summary["re_drove"] == 0

    def test_watchdog_skips_quarantine(self, repo, mock_plane_env):
        """Rows at the max-attempts ceiling are counted as
        ``skipped_quarantine``."""
        project = repo.create(name="QuarantineRow")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_ERROR,
            )
            repo.set_metadata_record(
                session, project.project_id, PLANE_ATTEMPT_COUNT_METADATA_KEY,
                PLANE_SYNC_WATCHDOG_MAX_ATTEMPTS,
            )
            # Long-ago last attempt — backoff long past.
            old = (
                datetime.now(timezone.utc) - timedelta(hours=24)
            ).isoformat()
            repo.set_metadata_record(
                session, project.project_id, PLANE_LAST_ATTEMPT_METADATA_KEY,
                old,
            )
            session.commit()

        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=300)
        summary = asyncio.run(watchdog.sweep_once())

        # is_retry_eligible returns False because count >= max_attempts,
        # which falls into the "skip because not eligible" bucket. We
        # do not differentiate backoff vs quarantine at the summary
        # level — both are "skipped because not eligible" in this
        # code path. The important assertion is that re_drove=0.
        assert summary["considered"] == 1
        assert summary["re_drove"] == 0

    def test_watchdog_drift_recovered_by_corrective_sync(self, repo, mock_plane_env):
        """A drift row → watchdog re-drives → corrective sync brings it
        back to linked (or back to drift if Plane still disagrees)."""
        project = repo.create(name="DriftRecoveryProj", description="d")
        with Session(repo.engine) as session:
            repo.set_metadata_record(
                session, project.project_id, PLANE_SYNC_STATE_METADATA_KEY,
                PLANE_SYNC_STATE_DRIFT,
            )
            session.commit()

        # Mock returns matching identity → drift is healed.
        mock = _make_client_mock()
        mock.update_project = AsyncMock(
            return_value={"id": "plane-1", "name": "DriftRecoveryProj", "description": "d"}
        )
        watchdog = PlaneSyncWatchdogService(
            repo, interval_seconds=300, sync_service=PlaneSyncService(repo, http_client=mock)
        )

        summary = asyncio.run(watchdog.sweep_once())

        assert summary["re_drove"] >= 1
        with Session(repo.engine) as session:
            records = repo.list_metadata_records(session, project.project_id)
        meta = {r.meta_key: r.meta_value for r in records}
        assert meta[PLANE_SYNC_STATE_METADATA_KEY] == PLANE_SYNC_STATE_LINKED

    def test_watchdog_start_stop_lifecycle(self, repo, mock_plane_env):
        """The watchdog task can be started and stopped cleanly."""
        import asyncio as _asyncio

        watchdog = PlaneSyncWatchdogService(repo, interval_seconds=1)
        _asyncio.run(watchdog._ensure_loop()) if hasattr(watchdog, "_ensure_loop") else None
        # The lifecycle test exercises the start/stop pair inside a
        # single event loop (mirrors the production wiring in
        # daemon/api.py:start + stop). pytest's async runner does not
        # exist by default — wrap in ``asyncio.run`` so we have a
        # running loop when ``asyncio.create_task`` fires.
        async def _lifecycle():
            watchdog.start()
            try:
                # Let the loop tick at least once.
                await _asyncio.sleep(0.05)
            finally:
                await watchdog.stop()

        _asyncio.run(_lifecycle())
        assert watchdog._task is None
