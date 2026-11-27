"""F5 pins — the stall criterion is TRAFFIC, not ``comparisons``.

THE GAP THIS CLOSES

``find_stalled_ab_tests`` and ``resolve_ab_test_sanctioned(basis="stalled")``
used ``SkillABTest.comparisons == 0`` as the zero-traffic signal. That
counter is bumped by EXACTLY ONE site — ``increment_comparison`` — whose
only caller is the INJECTION/serving selector
(``skill_injection_service._select_ab_variant``). It is NOT bumped by
``record_feedback`` / ``record_task_completion`` / any feedback path, so
a pair can carry REAL traffic on its arms with ``comparisons == 0`` —
the live 98c3e028 shape. No test pinned that distinction in either
direction.

THE NEW SIGNAL (F5, 2026-11-26)

A pair is 'stalled' ONLY when BOTH ARMS are at zero traffic —
``total_selections == 0`` AND ``total_applied == 0`` on BOTH skill rows
(the counters the serving/feedback paths bump through
``SkillRepository.increment_counter``). Traffic on EITHER arm makes the
pair NOT stalled, at BOTH enforcement sites:

* **Site A** — ``find_stalled_ab_tests`` EXCLUDES the pair.
* **Site B** — ``resolve_ab_test_sanctioned`` REFUSES it.

The traffic read is the NEW dedicated, read-only repository surface
``SkillRepository.get_arm_traffic(skill_id)`` (daemon/repositories/
skill/repository.py) — the sole reader the predicate uses, so the two
numbers can never drift into a second source of truth.

LIVE-EXAMPLE FIXTURE (the front door's trap, modelled as VALUES — never
a live row): pair ``11080c82`` / group ``98c3e028``; arms
``3b7a21df`` (gen0, sel=2, app=2) and ``4d04b153`` (gen1, sel=2,
app=2); ``comparisons == 0``. Under the OLD counter the pair looked
stall-eligible; under the new signal it is traffic-bearing and must be
neither listed nor resolved.

E-6 IS UNTOUCHED: the stall rule changes only WHICH PAIRS are eligible,
never WHO WINS (``_e6_winner`` / ``_ab_pair_age_days`` are not modified
by this lane).

NO live-DB writes, no pair resolutions outside the in-memory scratch
engine, fixtures/mocks only.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from daemon.repositories.skill.models import (  # noqa: F401 — tables
    Skill,
    SkillABTest,
    SkillEmbedding,
    SkillLineage,
    SkillTrigger,
    SkillUsageRecord,
)
from daemon.repositories.skill.repository import (
    SkillABTestRepository,
    SkillRepository,
)
from daemon.services.skill_evolution_service import SkillEvolutionService

PROJECT = "p-f5-stall"

#: The front door's LIVE example — VALUES ONLY (never a live row).
LIVE_PAIR_ID = "11080c82"  # the pair row id (informational)
LIVE_GROUP = "98c3e028"    # the group token
LIVE_ARM_OLD = "3b7a21df"  # gen0, sel=2, app=2
LIVE_ARM_NEW = "4d04b153"  # gen1, sel=2, app=2


# ── Harness ───────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'f5-stall.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def skill_repo(engine):
    return SkillRepository(engine)


@pytest.fixture
def ab_test_repo(engine):
    return SkillABTestRepository(engine)


@pytest.fixture
def svc(skill_repo, ab_test_repo):
    return SkillEvolutionService(
        skill_repo=skill_repo,
        lineage_repo=MagicMock(),
        usage_repo=MagicMock(),
        embedding_service=MagicMock(),
        metrics_service=MagicMock(),
        ab_test_repo=ab_test_repo,
        config=SimpleNamespace(ab_stall_timeout_days=7.0),
        llm_config={"base_url": "http://test", "api_key": "test"},
    )


def _make_arm(skill_repo, *, name, generation):
    return skill_repo.create(
        name=name,
        description="d",
        content="c",
        project_id=PROJECT,
        generation=generation,
    )


def _make_pair(
    skill_repo,
    ab_test_repo,
    *,
    group,
    old_gen=0,
    new_gen=1,
    age_days=30.0,
    comparisons=0,
    old_traffic=(0, 0),
    new_traffic=(0, 0),
):
    """Two arms + a pair row; traffic written THROUGH the production
    counter path (``increment_counter``) — never by direct assignment."""
    old = _make_arm(skill_repo, name=f"arm-old-{group}", generation=old_gen)
    new = _make_arm(skill_repo, name=f"arm-new-{group}", generation=new_gen)
    for row in (old, new):
        skill_repo.update(
            row.id, ab_test_group=group, status="ab_testing"
        )
    ab_test_repo.create_ab_test(group, old.id, new.id)
    with Session(ab_test_repo.engine) as session:
        row = session.exec(
            select(SkillABTest).where(SkillABTest.ab_test_group == group)
        ).first()
        row.created_at = (
            datetime.now(timezone.utc) - timedelta(days=age_days)
        ).isoformat()
        row.comparisons = comparisons
        session.add(row)
        session.commit()
    for row, (sel, app) in ((old, old_traffic), (new, new_traffic)):
        if sel:
            skill_repo.increment_counter(row.id, "total_selections", sel)
        if app:
            skill_repo.increment_counter(row.id, "total_applied", app)
    return old, new


# ── The reader (the NEW dedicated counter surface) ────────────────────────


class TestGetArmTrafficReader:
    def test_reads_the_two_counters_from_the_skill_row(
        self, skill_repo
    ):
        row = _make_arm(skill_repo, name="reader-arm", generation=0)
        skill_repo.increment_counter(row.id, "total_selections", 2)
        skill_repo.increment_counter(row.id, "total_applied", 2)

        out = skill_repo.get_arm_traffic(row.id)

        assert out == {
            "skill_id": row.id,
            "total_selections": 2,
            "total_applied": 2,
        }

    def test_zero_counters_are_zero_not_missing(self, skill_repo):
        row = _make_arm(skill_repo, name="reader-zero", generation=0)

        out = skill_repo.get_arm_traffic(row.id)

        assert out["total_selections"] == 0
        assert out["total_applied"] == 0

    def test_missing_row_is_None_not_zero(self, skill_repo):
        """A MISSING arm row is 'traffic UNKNOWN' — deliberately distinct
        from 'zero traffic' so the caller can decide EXPLICITLY."""
        assert skill_repo.get_arm_traffic("no-such-arm") is None

    def test_reader_is_read_only(self, skill_repo):
        row = _make_arm(skill_repo, name="reader-ro", generation=0)
        before = skill_repo.get_arm_traffic(row.id)
        skill_repo.get_arm_traffic(row.id)
        assert skill_repo.get_arm_traffic(row.id) == before


# ── Site A — find_stalled_ab_tests EXCLUDES a traffic-bearing pair ────────


class TestSiteAFindStalledExcludesTraffic:
    @pytest.mark.asyncio
    async def test_live_example_pair_is_not_listed(
        self, svc, skill_repo, ab_test_repo
    ):
        """The LIVE 98c3e028 shape: comparisons=0, BOTH arms sel=2/app=2,
        old pair — under the OLD counter stall-eligible; under the new
        signal NOT listed."""
        _make_pair(
            skill_repo,
            ab_test_repo,
            group=LIVE_GROUP,
            old_gen=0,
            new_gen=1,
            age_days=30.0,
            comparisons=0,           # the broken counter: looks idle
            old_traffic=(2, 2),      # 3b7a21df: sel=2, app=2
            new_traffic=(2, 2),      # 4d04b153: sel=2, app=2
        )

        stalled = await svc.find_stalled_ab_tests()

        assert all(
            entry["ab_test_group"] != LIVE_GROUP for entry in stalled
        ), (
            "a pair carrying traffic on its arms must never be listed as "
            f"stalled — got {stalled!r}"
        )

    @pytest.mark.asyncio
    async def test_traffic_on_ONE_arm_alone_is_not_listed(
        self, svc, skill_repo, ab_test_repo
    ):
        """The predicate is BOTH-ARMS-ZERO — traffic on EITHER arm
        alone disqualifies (not 'either arm at zero ⇒ stalled')."""
        _make_pair(
            skill_repo,
            ab_test_repo,
            group="g-one-arm",
            comparisons=0,
            old_traffic=(0, 0),
            new_traffic=(5, 0),  # challenger served but never applied
        )

        stalled = await svc.find_stalled_ab_tests()

        assert all(
            entry["ab_test_group"] != "g-one-arm" for entry in stalled
        )

    @pytest.mark.asyncio
    async def test_zero_traffic_both_arms_IS_listed(
        self, svc, skill_repo, ab_test_repo
    ):
        """Control: the honest zero-traffic pair is still stall-eligible."""
        _make_pair(
            skill_repo,
            ab_test_repo,
            group="g-truly-idle",
            comparisons=0,
            old_traffic=(0, 0),
            new_traffic=(0, 0),
        )

        stalled = await svc.find_stalled_ab_tests()

        listed = [e["ab_test_group"] for e in stalled]
        assert "g-truly-idle" in listed


# ── Site B — resolve_ab_test_sanctioned REFUSES a traffic-bearing pair ────


class TestSiteBSanctionedRefusesTraffic:
    @pytest.mark.asyncio
    async def test_live_example_pair_is_not_resolved(
        self, svc, skill_repo, ab_test_repo
    ):
        """The same live shape is REFUSED at the sanctioned resolver —
        NO mutation, no winner, no resolution."""
        old, new = _make_pair(
            skill_repo,
            ab_test_repo,
            group=LIVE_GROUP,
            old_gen=0,
            new_gen=1,
            age_days=30.0,
            comparisons=0,
            old_traffic=(2, 2),
            new_traffic=(2, 2),
        )

        with pytest.raises(ValueError, match="traffic-bearing"):
            await svc.resolve_ab_test_sanctioned(
                LIVE_GROUP, basis="stalled", reason="sweep",
            )

        # ZERO mutation: both arms stay live pair members, the pair row
        # stays unresolved.
        for row_id in (old.id, new.id):
            row = skill_repo.get(row_id)
            assert row.status == "ab_testing"
            assert row.is_active is True
            assert row.ab_test_group == LIVE_GROUP
        assert ab_test_repo.get_by_group(LIVE_GROUP).resolved_at is None

    @pytest.mark.asyncio
    async def test_one_arm_traffic_alone_is_refused(
        self, svc, skill_repo, ab_test_repo
    ):
        _make_pair(
            skill_repo,
            ab_test_repo,
            group="g-b-one-arm",
            comparisons=0,
            old_traffic=(0, 0),
            new_traffic=(0, 3),  # applied-only traffic on the challenger
        )
        with pytest.raises(ValueError, match="traffic-bearing"):
            await svc.resolve_ab_test_sanctioned(
                "g-b-one-arm", basis="stalled", reason="sweep",
            )

    @pytest.mark.asyncio
    async def test_zero_traffic_pair_still_resolves_by_e6(
        self, svc, skill_repo, ab_test_repo
    ):
        """Control: an honestly idle pair resolves — E-6 crowns the
        NEWER GENERATION (untouched rule)."""
        old, new = _make_pair(
            skill_repo,
            ab_test_repo,
            group="g-b-idle",
            old_gen=0,
            new_gen=1,
            age_days=30.0,
            comparisons=0,
            old_traffic=(0, 0),
            new_traffic=(0, 0),
        )
        result = await svc.resolve_ab_test_sanctioned(
            "g-b-idle", basis="stalled", reason="sweep",
        )
        assert result["resolved"] is True
        assert result["winner_id"] == new.id  # newer generation wins
        assert result["loser_id"] == old.id
        assert skill_repo.get(new.id).status == "active"
        assert skill_repo.get(old.id).status == "inactive"


# ── The deliberate MISSING-ARM decision ───────────────────────────────────


class TestMissingArmDecision:
    @pytest.mark.asyncio
    async def test_missing_arm_row_parks_never_resolves(
        self, svc, skill_repo, ab_test_repo
    ):
        """DECIDED BEHAVIOUR: an absent arm row has NO traffic record ⇒
        the pair is NOT traffic-bearing at the predicate (so it is NOT
        silently skipped — the anomaly stays surfaced), and the E-6
        winner rule then PARKS it with ``stalled_variant_row_missing``
        BEFORE any mutation. Net result: never resolved without a
        verifiable winner."""
        old, new = _make_pair(
            skill_repo,
            ab_test_repo,
            group="g-missing-arm",
            old_gen=0,
            new_gen=1,
            age_days=30.0,
            comparisons=0,
        )
        # Delete the challenger ROW while leaving the pair row intact.
        with skill_repo.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            conn.exec_driver_sql(
                "DELETE FROM skills WHERE id = :sid", {"sid": new.id}
            )
            conn.commit()
            conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert skill_repo.get(new.id) is None
        assert ab_test_repo.get_by_group("g-missing-arm") is not None

        # SITE A: the pair IS listed (surfaced, not silently skipped) —
        # a missing arm has no traffic RECORD, so it is not
        # traffic-bearing; the E-6 rule supplies the park reason so the
        # operator SEES the desync.
        stalled = await svc.find_stalled_ab_tests()
        entry = next(
            (e for e in stalled if e["ab_test_group"] == "g-missing-arm"),
            None,
        )
        assert entry is not None
        assert entry["park_reason"] == "stalled_variant_row_missing"

        # SITE B: the sanctioned resolver CANNOT resolve it — it parks.
        result = await svc.resolve_ab_test_sanctioned(
            "g-missing-arm", basis="stalled", reason="sweep",
        )

        assert result["resolved"] is False
        assert result["parked"] is True
        assert result["reason"] == "stalled_variant_row_missing"
        # The survivor is untouched.
        survivor = skill_repo.get(old.id)
        assert survivor.status == "ab_testing"
        assert survivor.is_active is True
        assert ab_test_repo.get_by_group("g-missing-arm").resolved_at is None
