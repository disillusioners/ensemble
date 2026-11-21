"""Boot-shaped regression pins for the D-5 DB-tier opt-out (council C6).

The seeder runs inside ``InstanceManager.__init__`` (the tool-usage P3
block) BEFORE the api lifespan wires the managed-values provider — so
at the boot call site the ``live_managed_value`` cache is EMPTY and a
DB override ``limits.tool_usage_digest_seeding_enabled`` used to be
silently ignored (operator-intent reversal: disable → 14d GC reap →
reboot re-seeds). The fix reads the ``app_settings`` row DIRECTLY at
seed time through the seeder's own engine.

Pins (one per ruled scenario):

* C6-1 — EMPTY managed-values cache + DB row ``false`` ⇒ seeding
  SUPPRESSED (the defect scenario; resolved True unconditionally before).
* C6-2 — row absent ⇒ default True; seeding proceeds and is idempotent.
* C6-3 — env explicit ``false`` wins over a DB ``true`` row (the env
  tier keeps precedence).
* C6-4 — row present but malformed (undecodable JSON / decoded
  non-bool) ⇒ FAIL-CLOSED (suppressed).
* first-boot — ``app_settings`` table absent (bare engine) ⇒ the tier
  read fails open to the lower tiers (never a decision of its own).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel, Session

from daemon.models.app_settings import AppSetting
from daemon.repositories.source.models import SourceConfig, SourceStatus
from daemon.repositories.source.seed import (
    SEED_OPTOUT_KEY,
    SEED_SOURCE_ID,
    _seeding_enabled,
    seed_tool_usage_digest_schedule,
)
from daemon.services import metrics_gate

ENV_VAR = "LIMITS_TOOL_USAGE_DIGEST_SEEDING_ENABLED"


def _insert_override(engine, value_json: str) -> None:
    with Session(engine) as session:
        session.add(
            AppSetting(
                key=SEED_OPTOUT_KEY,
                value_json=value_json,
                value_type="bool",
                updated_by=None,
                updated_at="2026-11-21T00:00:00+00:00",
            )
        )
        session.commit()


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'seed_optout.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(autouse=True)
def _boot_shape(monkeypatch):
    """Pin the HONEST boot shape for every leg.

    At the real call site (``InstanceManager.__init__``) the
    managed-values provider is not wired yet — the cache MUST be empty
    here — and no env tier statement may leak in from the ambient
    environment (per-variable scrub; ``delenv`` on exactly this var).
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(metrics_gate, "_managed_values", {})


class TestC6DbTierOptOut:
    def test_c6_1_db_false_empty_cache_suppresses_seeding(self, engine):
        """THE defect scenario: DB override False is honored at boot."""
        _insert_override(engine, "false")
        assert _seeding_enabled(engine) is False
        assert seed_tool_usage_digest_schedule(engine) is None
        with Session(engine) as session:
            assert session.get(SourceConfig, SEED_SOURCE_ID) is None

    def test_c6_2_row_absent_defaults_true_and_seeds(self, engine):
        assert _seeding_enabled(engine) is True
        seeded = seed_tool_usage_digest_schedule(engine)
        assert seeded is not None
        assert seeded.source_id == SEED_SOURCE_ID
        with Session(engine) as session:
            row = session.get(SourceConfig, SEED_SOURCE_ID)
            assert row is not None
            # Status compensation: create leaves STOPPED; the seeder
            # promotes to RUNNING so start_all() picks the schedule up.
            assert row.status == SourceStatus.RUNNING.value
        # Idempotent get-or-create (D-4): a second boot is a no-op.
        again = seed_tool_usage_digest_schedule(engine)
        assert again is not None
        assert again.source_id == SEED_SOURCE_ID

    def test_c6_3_env_false_wins_over_db_true(self, engine, monkeypatch):
        _insert_override(engine, "true")
        monkeypatch.setenv(ENV_VAR, "false")
        assert _seeding_enabled(engine) is False
        assert seed_tool_usage_digest_schedule(engine) is None

    @pytest.mark.parametrize("bad_json", ["not-json{", '"yes"'])
    def test_c6_4_malformed_row_fails_closed(self, engine, bad_json):
        _insert_override(engine, bad_json)
        assert _seeding_enabled(engine) is False
        assert seed_tool_usage_digest_schedule(engine) is None


class TestFirstBootPosture:
    def test_missing_app_settings_table_reads_as_no_overrides(self, tmp_path):
        """A missing table is the NORMAL first-boot state — the tier read
        fails open to the lower tiers (default True). It never decides
        on its own and never aborts the boot (the ``app_settings``
        model's documented posture)."""
        bare = create_engine(
            f"sqlite:///{tmp_path / 'bare.db'}",
            connect_args={"check_same_thread": False},
        )
        # Deliberately NO create_all: the pre-create_all / pre-migration
        # first-boot state.
        try:
            assert _seeding_enabled(bare) is True
        finally:
            bare.dispose()
