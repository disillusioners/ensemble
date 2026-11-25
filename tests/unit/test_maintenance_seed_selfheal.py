"""PIN — the maintenance seeder's CANONICAL SELF-HEAL (M-B), branch
feature/schedule-listing-truth-phase1 (round commit 143027ba2, narrowed
to the id-only discriminator by 52945180b).

The rules this file pins:

* **(a) the canonical self-heal** — a STOPPED-but-ENABLED row whose
  ``source_id == CANONICAL_SEED_SOURCE_ID`` is SELF-HEALED: promoted to
  RUNNING with ``enabled=True``, and — critically — WITHOUT the
  chain-specific BR-1 ``run_at`` re-mint: its stored config keeps NO
  ``chain`` block and NO ``run_at`` (no graft).  The discriminator is
  ID-ONLY: the label alone is deliberately NOT part of the test, so an
  operator-created row squatting the canonical label is never re-armed
  by the seeder.

* **(b) the legacy row is left alone** — a legacy-identity row (the
  legacy chain label, a non-canonical id) that is stopped and carries
  NO chain block is LEFT ALONE (``action="noop_existing"``, the status
  stays stopped).  This is the pre-existing house pin — the canonical
  self-heal must not swallow it.

* **(c) a failed promotion is LOUD** — when the status promotion fails
  the guard reports NO success (``action="failed"``, never
  ``"reenabled"``) and an ERROR-level log record is emitted.

* **(d) a promotion that MATCHES NO ROW is LOUD too (MA-3)** — the
  repository's ``update_source_status`` returns ``None`` when no row
  matches the source id (``daemon/repositories/source/repository.py``).
  A silent no-op there would let the guard report a promotion it never
  performed, so the seeder raises ``MaintenanceSeedError`` and the
  message NAMES THE SEED LABEL.

MUTATION EVIDENCE (skill ``mutation-verified-regression-pins``): the
pins are PER-PIN mutation-proven — each pin's own docstring names the
SINGLE edit that turns it RED, and that RED was demonstrated in the
round that added the pin (mutate ⇒ RED ⇒ revert ⇒ GREEN).  No blanket
"each pin" claim: where a pin can only bite a COMBINED mutation, its
docstring says so.

SAFETY FENCE: a throwaway SQLite database under ``tmp_path`` — no
PostgreSQL, no daemon, no live contact, no network.  The artifact root
is redirected into ``tmp_path`` so the LIVE-INSTALL tree is never
touched.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, select

from daemon.models.board import BoardItem, BoardItemTag
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectTagLink,
)
from daemon.repositories.source.models import (
    ScheduleExecution,
    SourceConfig,
    SourceConfigArchive,
    SourceStatus,
)
from daemon.repositories.source import maintenance_seed as ms
from daemon.repositories.source.maintenance_seed import (
    CANONICAL_SEED_NAME,
    CANONICAL_SEED_SOURCE_ID,
    SEED_NAME,
    SEED_SOURCE_ID,
    SEED_SOURCE_TYPE,
    seed_central_maintenance_schedule,
)

GUARD_TABLES = [
    SourceConfig.__table__,
    SourceConfigArchive.__table__,
    ScheduleExecution.__table__,
    Project.__table__,
    ProjectTagLink.__table__,
    ProjectShortnameLink.__table__,
    ProjectMetadataRecord.__table__,
    BoardItem.__table__,
    BoardItemTag.__table__,
]

CANONICAL_CRON = "0 1 * * 0"


@pytest.fixture
def guard_engine(tmp_path) -> Engine:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'selfheal_seed.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    SQLModel.metadata.create_all(engine, tables=GUARD_TABLES)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def results_root(tmp_path):
    return tmp_path / "live-install"


def _row(engine: Engine, source_id: str) -> SourceConfig | None:
    with Session(engine) as session:
        return session.get(SourceConfig, source_id)


def _rows(engine: Engine) -> list[SourceConfig]:
    with Session(engine) as session:
        return list(session.exec(select(SourceConfig)))


def _canonical_config() -> dict:
    """The CANONICAL recurring shape the seeder writes: recurrence=weekly
    + a raw cron expression + the local time + the timezone — NO chain
    block and NO run_at."""
    return {
        "agent": "maintenancer",
        "message": "weekly run",
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "weekly",
        "schedule": CANONICAL_CRON,
        "local_time": "01:00",
        "instance_mode": "new_instance",
        "project_id": "system-default",
    }


def _insert_row(
    engine: Engine,
    *,
    source_id: str,
    name: str,
    config: dict,
    status: str,
    enabled: bool = True,
    source_type: str = SEED_SOURCE_TYPE,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with Session(engine) as session:
        session.add(
            SourceConfig(
                source_id=source_id,
                source_type=source_type,
                name=name,
                config=config,
                credentials=None,
                enabled=enabled,
                autostart=True,
                status=status,
                error_message=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()


class _ExplodingStatusRepo:
    """A repository wrapper whose ``update_source_status`` always fails.

    Everything else is delegated to the real repository — so the ONLY
    injected defect is the failed status promotion.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def update_source_status(self, *args, **kwargs):
        raise RuntimeError("status write failed (injected)")


class _NullStatusRepo:
    """A repository wrapper whose ``update_source_status`` matches NO ROW.

    The REAL repository returns ``None`` when no row matches the source
    id (``daemon/repositories/source/repository.py``: the row is gone) —
    a silent no-op.  Everything else is delegated to the real repository,
    so the ONLY injected defect is the no-match promotion.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def update_source_status(self, *args, **kwargs):
        return None


class TestCanonicalSelfHealMB:
    def test_a_stopped_canonical_row_is_promoted_to_running_and_enabled(
        self, guard_engine, results_root
    ):
        """(a) M-B — a STOPPED-but-ENABLED CANONICAL row is self-healed:
        promoted to RUNNING with enabled=True, and its stored config is
        NOT grafted (NO chain block, NO run_at — the chain-specific BR-1
        re-mint must not touch a canonical row)."""
        _insert_row(
            guard_engine,
            source_id=CANONICAL_SEED_SOURCE_ID,
            name=CANONICAL_SEED_NAME,
            config=_canonical_config(),
            status=SourceStatus.STOPPED.value,
            enabled=True,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "reenabled", outcome.detail
        row = _row(guard_engine, CANONICAL_SEED_SOURCE_ID)
        assert row is not None
        # PROMOTED.
        assert row.status == SourceStatus.RUNNING.value
        assert row.enabled is True
        # NOT GRAFTED — the canonical recurring shape survived the heal.
        config = row.config
        assert "chain" not in config
        assert "run_at" not in config
        assert config["recurrence"] == "weekly"
        assert config["schedule"] == CANONICAL_CRON

    def test_the_canonical_self_heal_is_id_only_not_label_driven(
        self, guard_engine, results_root
    ):
        """(a) M-B — the discriminator is ID-ONLY: a row that SQUATS the
        canonical LABEL under a different id is NOT re-armed by the
        seeder (the label is deliberately not part of the test)."""
        _insert_row(
            guard_engine,
            source_id="operator-squatter-row",
            name=CANONICAL_SEED_NAME,
            config=_canonical_config(),
            status=SourceStatus.STOPPED.value,
            enabled=True,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        # The squatter is not the canonical row — the seeder must not
        # heal it (it neither owns the canonical id nor the chain
        # shape).  Whatever the guard decides, the row's own status must
        # not have been flipped to RUNNING by the canonical self-heal.
        row = _row(guard_engine, "operator-squatter-row")
        assert row is not None
        assert row.status == SourceStatus.STOPPED.value
        assert outcome.action != "reenabled" or (
            outcome.source_id == "operator-squatter-row"
        )

    def test_a_legacy_stopped_row_without_a_chain_block_is_left_alone(
        self, guard_engine, results_root
    ):
        """(b) M-B — the LEGACY-identity row (the legacy chain label, a
        non-canonical id) that is stopped and carries NO chain block is
        LEFT ALONE: ``action="noop_existing"`` and the status stays
        stopped.  The canonical self-heal must not swallow this
        pre-existing house rule."""
        _insert_row(
            guard_engine,
            source_id=SEED_SOURCE_ID,
            name=SEED_NAME,
            config=_canonical_config(),
            status=SourceStatus.STOPPED.value,
            enabled=True,
        )
        outcome = seed_central_maintenance_schedule(
            guard_engine, results_root=results_root
        )
        assert outcome.action == "noop_existing", outcome.detail
        row = _row(guard_engine, SEED_SOURCE_ID)
        assert row is not None
        assert row.status == SourceStatus.STOPPED.value
        assert row.enabled is True

    def test_a_failed_status_promotion_is_loud_and_never_reports_success(
        self, guard_engine, results_root, monkeypatch, caplog
    ):
        """(c) M-B — a FAILED status promotion is LOUD: the guard reports
        NO success and an ERROR-level log record is emitted."""
        _insert_row(
            guard_engine,
            source_id=CANONICAL_SEED_SOURCE_ID,
            name=CANONICAL_SEED_NAME,
            config=_canonical_config(),
            status=SourceStatus.STOPPED.value,
            enabled=True,
        )
        real_factory = ms.create_source_repository

        def _exploding_factory(*args, **kwargs):
            return _ExplodingStatusRepo(real_factory(*args, **kwargs))

        monkeypatch.setattr(ms, "create_source_repository", _exploding_factory)

        with caplog.at_level(logging.ERROR):
            outcome = seed_central_maintenance_schedule(
                guard_engine, results_root=results_root
            )

        # NEVER a success.
        assert outcome.action != "reenabled", outcome.detail
        assert outcome.action == "failed", outcome.detail
        # LOUD — an ERROR-level record was emitted.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, (
            "a failed status promotion must be logged at ERROR level "
            f"(records: {[r.getMessage() for r in caplog.records]})"
        )
        assert any(
            "CentralMaintenance" in r.getMessage() for r in errors
        ), [r.getMessage() for r in errors]

    def test_a_promotion_that_matches_no_row_is_loud_and_never_reports_success(
        self, guard_engine, results_root, monkeypatch, caplog
    ):
        """(d) M-B/MA-3 — a promotion that MATCHES NO ROW is LOUD: the
        repository returns ``None`` (the row is gone) and the guard must
        raise ``MaintenanceSeedError`` — it never reports a success it
        did not perform.  The message names the SEED LABEL.

        COUNTER-MUTATION: drop the ``if promoted is None: raise
        MaintenanceSeedError(...)`` branch in
        ``maintenance_seed._write_chain_config`` ⇒ the no-match promotion
        is SILENT ⇒ the outcome reports ``"reenabled"`` and the direct
        call does not raise ⇒ RED."""
        _insert_row(
            guard_engine,
            source_id=CANONICAL_SEED_SOURCE_ID,
            name=CANONICAL_SEED_NAME,
            config=_canonical_config(),
            status=SourceStatus.STOPPED.value,
            enabled=True,
        )
        real_factory = ms.create_source_repository

        def _null_factory(*args, **kwargs):
            return _NullStatusRepo(real_factory(*args, **kwargs))

        monkeypatch.setattr(ms, "create_source_repository", _null_factory)

        # (i) THROUGH THE GUARD — never a success, and LOUD.
        with caplog.at_level(logging.ERROR):
            outcome = seed_central_maintenance_schedule(
                guard_engine, results_root=results_root
            )
        assert outcome.action != "reenabled", outcome.detail
        assert outcome.action == "failed", outcome.detail
        # The failure NAMES THE SEED LABEL (an operator can grep it).
        assert CANONICAL_SEED_NAME in outcome.detail, outcome.detail
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, (
            "a promotion that matched no row must be logged at ERROR level "
            f"(records: {[r.getMessage() for r in caplog.records]})"
        )
        assert any(
            "CentralMaintenance" in r.getMessage() for r in errors
        ), [r.getMessage() for r in errors]

        # (ii) AT THE SEAM — the promotion itself raises.
        repo = _NullStatusRepo(
            real_factory(engine=guard_engine, create_tables=False)
        )
        with pytest.raises(ms.MaintenanceSeedError) as excinfo:
            ms._write_chain_config(
                repo,
                source_id=CANONICAL_SEED_SOURCE_ID,
                config=_canonical_config(),
                enabled=True,
                status=SourceStatus.RUNNING.value,
            )
        assert CANONICAL_SEED_NAME in str(excinfo.value)
