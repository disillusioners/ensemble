"""POK strict family-membership corpus (commission pok-family-count-strict).

Mutation-proven strict-count pin + distilled census-structure pin + strict
refusal behaviour. In-memory SQLite engine only (no InstanceManager, no
live store, no network/DB).

asyncio_mode=auto (pyproject).
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import create_engine
from sqlmodel import SQLModel

import daemon.repositories.pok.models  # noqa: F401 — register the tables
import daemon.repositories.pok.models as _pok_models
import daemon.services.pok_service as _pok_service
from daemon.repositories.pok.models import (
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.services.pok_service import (
    POKError,
    POKService,
    POK_FAMILY_MAX,
    content_hash,
)


@pytest.fixture()
def svc():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
        ],
    )
    return POKService(engine)


# ── Class A — mutation-proven strict-count legs ──────────────────────────


class TestStrictFamilyCount:
    def test_longer_family_never_inflates_a_shorter_family(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="skill-01-a", body="a", agent_id="d")
        svc.upsert(project_id="p1", slug="skill-02-b", body="b", agent_id="d")
        svc.upsert(project_id="p1", slug="skill-e6-01-x", body="c", agent_id="d")
        # strict: skill counts only its own true members (2), not the
        # longer family's row. The old prefix counter returned 3.
        assert svc._repo.count_active_in_family("p1", "skill") == 2
        assert svc._repo.count_active_in_family("p1", "skill-e6") == 1

    def test_unnumbered_slug_has_no_family_and_never_counts(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="verify-01-a", body="a", agent_id="d")
        svc.upsert(project_id="p1", slug="verify-something-unnumbered", body="b", agent_id="d")
        assert svc._repo.count_active_in_family("p1", "verify") == 1

    def test_hyphenated_head_is_its_own_family(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="prompt-assent-01-x", body="a", agent_id="d")
        svc.upsert(project_id="p1", slug="prompt-01-y", body="b", agent_id="d")
        assert svc._repo.count_active_in_family("p1", "prompt-assent") == 1
        assert svc._repo.count_active_in_family("p1", "prompt") == 1

    def test_family_key_helper_agrees_with_the_service_helper(self, svc: POKService) -> None:
        battery = [
            "skill-01-a",
            "skill-e6-01-x",
            "prompt-assist-01-x",
            "snapshot-l3-02-y",
            "verify-something-unnumbered",
            "artifact-is-container-image",
            "over-cap-01-m",
            "gc-p4-03-x",
            "guard-inv-04-y",
            "packs-01-z",
            "skillab-02-b",
            "model-catalogs-not-drift",
            "prompt-assent-01-x",
            "group-chat-03-y",
        ]
        for s in battery:
            assert _pok_models.pok_family_key(s) == _pok_service._slug_family(s), s

    def test_family_key_helper_returns_none_without_ordinal(self) -> None:
        assert _pok_models.pok_family_key("verify-something-unnumbered") is None
        assert _pok_models.pok_family_key("artifact-is-container-image") is None
        assert _pok_models.pok_family_key("model-catalogs-not-drift") is None


# ── Class B — distilled census-structure pin ─────────────────────────────

CENSUS_STRICT = {
    "skill": 3,
    "stage": 2,
    "verify": 2,
    "promote": 3,
    "worktree": 3,
    "guard": 1,
    "mutation": 3,
    "pack": 1,
    "prompt-assent": 4,
    "release": 3,
    "gc-p4": 3,
    "packs": 1,
    "skillab": 3,
    "gc-gate": 3,
    "git": 2,
    "group-chat": 3,
    "guard-inv": 4,
    "regate": 2,
    "review": 3,
    "rogue": 1,
    "sonic": 2,
}


def _seed_family(svc: POKService, family: str, n: int, longer: bool = True) -> list[str]:
    slugs = [f"{family}-{i + 1:02d}-m" for i in range(n)]
    for i, slug in enumerate(slugs):
        svc._repo.insert(
            ProjectOperatingKnowledge(
                project_id=f"proj-{family}",
                slug=slug,
                body=f"member {i}",
                content_sha256=content_hash(f"member {i}"),
                created_by_agent="d",
            )
        )
    longer_slugs = []
    if longer:
        svc._repo.insert(
            ProjectOperatingKnowledge(
                project_id=f"proj-{family}",
                slug=f"{family}-longer-01-x",
                body="longer",
                content_sha256=content_hash("longer"),
                created_by_agent="d",
            )
        )
        longer_slugs.append(f"{family}-longer-01-x")
    return slugs + longer_slugs


class TestCensusStructureStrictMembership:
    @pytest.mark.parametrize("family", sorted(CENSUS_STRICT))
    def test_census_strict_count_exact(self, svc: POKService, family: str) -> None:
        n = CENSUS_STRICT[family]
        fixture = _seed_family(svc, family, n, longer=True)
        pid = f"proj-{family}"
        strict_count = svc._repo.count_active_in_family(pid, family)
        assert strict_count == n, (family, strict_count, n)
        # discriminating: old prefix-superset count is strictly greater
        prefix_count = sum(1 for s in fixture if s.startswith(family + "-"))
        assert prefix_count > strict_count, (family, prefix_count, strict_count)

    def test_census_shape_two_over_three_nineteen_at_or_under(self) -> None:
        over = [f for f, n in CENSUS_STRICT.items() if n > 3]
        at_or_under = [f for f, n in CENSUS_STRICT.items() if n <= 3]
        assert len(over) == 2, over
        assert len(at_or_under) == 19, at_or_under
        # 21 total
        assert len(CENSUS_STRICT) == 21

    @pytest.mark.parametrize("family", [f for f, n in CENSUS_STRICT.items() if n < 3])
    def test_phantom_dissolution_admitted(self, svc: POKService, family: str) -> None:
        n = CENSUS_STRICT[family]
        _seed_family(svc, family, n, longer=True)
        pid = f"proj-{family}"
        # family holds n (<3) true members -> a new write is admitted
        out = svc.upsert(project_id=pid, slug=f"{family}-{n + 1:02d}-z", body="new", agent_id="d")
        assert out.action == "created"

    @pytest.mark.parametrize("family", [f for f, n in CENSUS_STRICT.items() if n == 3])
    def test_at_three_refused_with_live_count_three(self, svc: POKService, family: str) -> None:
        n = CENSUS_STRICT[family]
        _seed_family(svc, family, n, longer=True)
        pid = f"proj-{family}"
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id=pid, slug=f"{family}-{n + 1:02d}-z", body="new", agent_id="d")
        assert ei.value.code == "pok_family_full"
        assert ei.value.details["active_count"] == 3
        msg = ei.value.message
        assert "holds 3 active entries" in msg
        assert "active family members" in msg


# ── Class C — strict refusal behaviour ───────────────────────────────────


class TestStrictRefusalBehaviour:
    def test_fourth_member_into_a_family_holding_exactly_three_refuses(self, svc: POKService) -> None:
        for i in range(POK_FAMILY_MAX):
            svc.upsert(project_id="p1", slug=f"rf-{i + 1:02d}-m", body=f"m{i}", agent_id="d")
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="rf-04-z", body="z", agent_id="d")
        assert ei.value.code == "pok_family_full"
        assert ei.value.details["family"] == "rf"
        assert ei.value.details["active_count"] == 3
        assert ei.value.details["archive_needed"] == 1
        msg = ei.value.message
        assert "active family members" in msg
        for i in range(POK_FAMILY_MAX):
            assert f"rf-{i + 1:02d}-m" in msg

    def test_refusal_names_true_members_only(self, svc: POKService) -> None:
        for i in range(POK_FAMILY_MAX):
            svc.upsert(project_id="p1", slug=f"strictdemo-{i + 1:02d}-a", body=f"m{i}", agent_id="d")
        svc.upsert(project_id="p1", slug="strictdemo-x-01-y", body="longer", agent_id="d")
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="strictdemo-04-z", body="z", agent_id="d")
        assert ei.value.code == "pok_family_full"
        msg = ei.value.message
        members = ei.value.details["members"]
        for i in range(POK_FAMILY_MAX):
            assert f"strictdemo-{i + 1:02d}-a" in msg
            assert f"strictdemo-{i + 1:02d}-a" in members
        assert "strictdemo-x-01-y" not in msg
        assert "strictdemo-x-01-y" not in members
        assert ei.value.details["active_count"] == 3

    def test_genuinely_over_families_still_refuse(self, svc: POKService) -> None:
        from daemon.services.pok_service import POK_FAMILY_MAX

        for fam in ("guard-inv", "prompt-assent"):
            pid = f"proj-{fam}"
            for i in range(POK_FAMILY_MAX + 1):
                svc._repo.insert(
                    ProjectOperatingKnowledge(
                        project_id=pid,
                        slug=f"{fam}-{i + 1:02d}-m",
                        body=f"seeded {i}",
                        content_sha256=content_hash(f"seeded {i}"),
                        created_by_agent="developer",
                    )
                )
            assert svc._repo.count_active_in_family(pid, fam) == POK_FAMILY_MAX + 1
            with pytest.raises(POKError) as ei:
                svc.upsert(project_id=pid, slug=f"{fam}-05-m", body="new", agent_id="d")
            assert ei.value.code == "pok_family_full"
            assert ei.value.details["active_count"] == POK_FAMILY_MAX + 1
            assert ei.value.details["archive_needed"] == 2
            assert "over the bound by 1" in ei.value.message

    def test_at_three_families_are_one_write_away(self, svc: POKService) -> None:
        # holding exactly 2 admits the 3rd
        svc.upsert(project_id="p2", slug="at3-01-a", body="a", agent_id="d")
        svc.upsert(project_id="p2", slug="at3-02-b", body="b", agent_id="d")
        out = svc.upsert(project_id="p2", slug="at3-03-c", body="c", agent_id="d")
        assert out.action == "created"
        # holding exactly 3 refuses the 4th
        svc2 = svc
        svc2.upsert(project_id="p3", slug="at3b-01-a", body="a", agent_id="d")
        svc2.upsert(project_id="p3", slug="at3b-02-b", body="b", agent_id="d")
        svc2.upsert(project_id="p3", slug="at3b-03-c", body="c", agent_id="d")
        with pytest.raises(POKError) as ei:
            svc2.upsert(project_id="p3", slug="at3b-04-d", body="d", agent_id="d")
        assert ei.value.code == "pok_family_full"

    def test_supersede_remains_count_preserving(self, svc: POKService) -> None:
        for i in range(POK_FAMILY_MAX):
            svc.upsert(project_id="p1", slug=f"sp-{i + 1:02d}-m", body=f"m{i}", agent_id="d")
        swapped = svc.upsert(project_id="p1", slug="sp-01-m", body="m0 v2", agent_id="d")
        assert swapped.action == "superseded"
        assert svc._repo.count_active_in_family("p1", "sp") == POK_FAMILY_MAX
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="sp-04-z", body="z", agent_id="d")
        assert ei.value.code == "pok_family_full"
