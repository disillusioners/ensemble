"""POK write-gate Phase 2 — counter carry-over, family-full refusal,
census re-verify.

Covers pok-write-gate Phase 2:

* T2.7 — COUNTER CARRY-OVER at ``supersede_and_insert``: the successor
  INHERITS the prior row's counters instead of defaulting to 0
  (design-amendments-v2 §13.4 defect #2 — a supersede that silently
  resets the counters breaks the J trusted-metric chain, making the
  metric self-defending against exactly the rows the librarian most
  needs to judge).
* T2.5 — the family-full refusal consumes the CORRECTED count (the
  strict per-key family count landed by the PARALLEL lane on
  origin/latest — this lane consumes it and re-verifies it, it does
  not own the fix).
* T2.6 — the corrected count is re-derived against the 2026-11-23
  census, READ-ONLY, from the census document itself.

Fence discipline: throwaway in-memory SQLite engines only. No daemon
boot, no InstanceManager, no live store, no network, no PostgreSQL.
The census is read as a DOCUMENT in the repo — the live store is never
contacted.
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.pok.models import (
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
    pok_family_key,
)
from daemon.repositories.pok.repository import POKRepository
from daemon.services.pok_service import (
    POKError,
    POKService,
    POK_FAMILY_MAX,
    content_hash,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CENSUS_DOC = (
    REPO_ROOT
    / ".agents"
    / "shared"
    / "planning"
    / "pok-write-gate"
    / "store-census-2026-11-23.md"
)


@pytest.fixture()
def repo() -> POKRepository:
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        eng,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
        ],
    )
    return POKRepository(eng)


@pytest.fixture()
def svc() -> POKService:
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        eng,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
        ],
    )
    return POKService(eng)


def _make(slug: str, **kwargs) -> ProjectOperatingKnowledge:
    """Build a row WITHOUT inserting it — the successor of a supersede
    must not be active while the prior still is (``uq_pok_active_slug``
    makes dual-active structurally impossible)."""
    body = kwargs.pop("body", "body")
    return ProjectOperatingKnowledge(
        project_id=kwargs.pop("project_id", "p1"),
        slug=slug,
        body=body,
        content_sha256=content_hash(body),
        created_by_agent="d",
        **kwargs,
    )


def _insert(repo: POKRepository, slug: str, **kwargs) -> ProjectOperatingKnowledge:
    return repo.insert(_make(slug, **kwargs))


# ── T2.7 — counter carry-over across a supersede ────────────────────────


class TestCounterCarryOver:
    def test_verify_count_lineage_is_preserved(self, repo: POKRepository) -> None:
        """T2.7 acceptance — verify_count lineage preserved across
        supersede. The trusted metric must NOT restart at every merge.
        """
        prior = _insert(repo, "demo-01-a")
        repo.record_verification(prior.id)
        repo.record_verification(prior.id)
        repo.record_verification(prior.id)
        assert repo.get(prior.id).verify_count == 3

        successor = _make("demo-01-a")
        repo.supersede_and_insert(prior, successor)

        landed = repo.get(successor.id)
        assert landed.verify_count == 3, (
            "the successor must inherit the prior lineage's verify_count — "
            "a reset makes the J trusted-metric chain self-defending"
        )

    def test_access_count_rides_along(self, repo: POKRepository) -> None:
        """access_count is LEGACY corroboration — it rides along, it is
        not the trusted metric, but it must not be lost either."""
        prior = _insert(repo, "demo-01-a")
        repo.bump_access([prior.id])
        repo.bump_access([prior.id])
        assert repo.get(prior.id).access_count == 2

        successor = _make("demo-01-a")
        repo.supersede_and_insert(prior, successor)
        assert repo.get(successor.id).access_count == 2

    def test_the_verify_stamp_travels_with_the_count(self, repo: POKRepository) -> None:
        """The verify stamp IS the provenance of the count."""
        prior = _insert(repo, "demo-01-a")
        repo.record_verification(prior.id)
        stamp = repo.get(prior.id).last_verified
        assert stamp is not None

        successor = _make("demo-01-a")
        repo.supersede_and_insert(prior, successor)
        assert repo.get(successor.id).last_verified == stamp

    def test_carry_over_accumulates_rather_than_overwrites(
        self, repo: POKRepository
    ) -> None:
        """``prior + successor`` — a caller that pre-set a counter keeps
        it rather than having it silently replaced."""
        prior = _insert(repo, "demo-01-a")
        repo.record_verification(prior.id)
        repo.record_verification(prior.id)

        successor = _make("demo-01-a")
        successor.verify_count = 5
        repo.supersede_and_insert(prior, successor)
        assert repo.get(successor.id).verify_count == 7

    def test_a_fresh_lineage_starts_at_zero(self, repo: POKRepository) -> None:
        """No prior counters → no inherited counters."""
        prior = _insert(repo, "demo-01-a")
        successor = _make("demo-01-a")
        repo.supersede_and_insert(prior, successor)
        assert repo.get(successor.id).verify_count == 0

    def test_the_supersede_is_still_count_preserving(self, repo: POKRepository) -> None:
        """The carry-over must not change the row COUNT — a merge is
        count-preserving (one active row before, one after)."""
        prior = _insert(repo, "demo-01-a")
        successor = _make("demo-01-a")
        repo.supersede_and_insert(prior, successor)
        assert repo.count_active("p1") == 1


# ── T2.5 — the family-full refusal consumes the CORRECTED count ────────


class TestFamilyFullRefusalCorrectedCount:
    def test_a_longer_family_never_refuses_the_shorter_one(
        self, svc: POKService
    ) -> None:
        """The strict per-key count: ``skill-e6-*`` must NOT count
        toward ``skill``. Under the old prefix count, ``skill`` looked
        over-cap and the write was refused for the wrong reason.
        """
        for slug in ("skill-01-a", "skill-02-b"):
            svc.upsert(project_id="p1", slug=slug, body="x", agent_id="d")
        for i in range(POK_FAMILY_MAX):
            svc.upsert(
                project_id="p1", slug=f"skill-e6-{i + 1:02d}-x", body="y", agent_id="d"
            )
        # ``skill`` holds 2 own members — well under the cap — so a
        # third own member is ADMITTED.
        svc.upsert(project_id="p1", slug="skill-03-c", body="z", agent_id="d")
        assert svc._repo.count_active_in_family("p1", "skill") == 3

    def test_the_fourth_own_member_is_refused_with_the_true_members(
        self, svc: POKService
    ) -> None:
        for i in range(POK_FAMILY_MAX):
            svc.upsert(
                project_id="p1", slug=f"guard-inv-{i + 1:02d}-x", body="x", agent_id="d"
            )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1", slug="guard-inv-04-y", body="x", agent_id="d"
            )
        err = ei.value
        assert err.code == "pok_family_full"
        # The refusal names the family's OWN members — a longer family's
        # rows are NOT listed.
        assert "guard-inv-01-x" in err.message
        assert "guard-inv-03-x" in err.message
        assert err.details.get("active_count") == POK_FAMILY_MAX

    def test_an_unnumbered_slug_never_counts(self, svc: POKService) -> None:
        """An unnumbered slug has NO family and never counts."""
        svc.upsert(project_id="p1", slug="verify-01-a", body="x", agent_id="d")
        svc.upsert(
            project_id="p1", slug="verify-something-unnumbered", body="y", agent_id="d"
        )
        assert svc._repo.count_active_in_family("p1", "verify") == 1

    def test_the_refusal_consumes_the_repository_count(
        self, svc: POKService
    ) -> None:
        """T2.5 — the refusal path reads the CORRECTED count from the
        repository (one source of truth), not a private re-count."""
        for i in range(POK_FAMILY_MAX):
            svc.upsert(
                project_id="p1", slug=f"prompt-assent-{i + 1:02d}-x", body="x", agent_id="d"
            )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1", slug="prompt-assent-04-y", body="x", agent_id="d"
            )
        assert ei.value.code == "pok_family_full"
        assert (
            ei.value.details.get("active_count")
            == svc._repo.count_active_in_family("p1", "prompt-assent")
        )


# ── T2.6 — read-only re-verify of the corrected count against the census ─


def _census_slugs() -> list[str]:
    """The 499 ACTIVE slugs of the 2026-11-23 census, read from the
    census DOCUMENT (never from a live store)."""
    lines = CENSUS_DOC.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 4."))
    end = next(i for i, ln in enumerate(lines) if ln.startswith("## 5."))
    slugs = []
    for raw in lines[start:end]:
        m = re.match(r"^\|\s*([a-z0-9][a-z0-9-]*)\s*\|\s*([A-Z/]+)\s*\|", raw)
        if m:
            slugs.append(m.group(1))
    return slugs


class TestCensusReVerify:
    def test_the_census_document_parses_to_499_active_rows(self) -> None:
        assert len(_census_slugs()) == 499

    def test_strict_membership_yields_exactly_two_over_cap_families(self) -> None:
        """The plan's figure: POST-FIX strict membership yields 2 true
        over-cap families — guard-inv and prompt-assent (both 4)."""
        counts = collections.Counter()
        for slug in _census_slugs():
            key = pok_family_key(slug)
            if key is not None:
                counts[key] += 1
        over = {k: v for k, v in counts.items() if v > POK_FAMILY_MAX}
        assert over == {"guard-inv": 4, "prompt-assent": 4}

    def test_the_at_cap_family_set_matches_the_census(self) -> None:
        """The census names 34 at-cap families — the strict derivation
        reproduces the SAME set (set-equal, not just same size)."""
        counts = collections.Counter()
        for slug in _census_slugs():
            key = pok_family_key(slug)
            if key is not None:
                counts[key] += 1
        derived = sorted(k for k, v in counts.items() if v == POK_FAMILY_MAX)
        census_at_cap = sorted(
            "baseleg, bindfix, ceremony-clean-checkout, discord, disk, "
            "engine-pilot, forensics, gateleg, gc-gate, gc-p4, group-chat, "
            "hbk-mut, job-lookup, mutation, p7-item5, packconv, promote, "
            "prompt-author, pyinst, quarantine, release, review, seal, "
            "settings-registry, skill, skillab, snapshot-l3, testgate, "
            "tier2-closure, upgfix, wake-path, wiring, worktree, "
            "wtgate".split(", ")
        )
        assert derived == census_at_cap

    def test_the_pre_fix_prefix_figure_is_21(self) -> None:
        """The plan's provenance figure: 21 is the PRE-FIX
        prefix-counting figure (the sprawl-engine evidence). It is
        reproduced here so the 19-dissolve / 2-real reconciliation is
        re-derivable, not asserted."""
        slugs = _census_slugs()
        fams = {k for k in (pok_family_key(s) for s in slugs) if k}
        prefix_over = [
            f for f in fams if sum(1 for s in slugs if s.startswith(f + "-")) > POK_FAMILY_MAX
        ]
        assert len(prefix_over) == 21

    def test_nineteen_dissolve_and_two_remain(self) -> None:
        """19 of the 21 pre-fix breaches dissolve under strict
        membership; 2 remain (guard-inv, prompt-assent)."""
        slugs = _census_slugs()
        fams = {k for k in (pok_family_key(s) for s in slugs) if k}
        strict = collections.Counter()
        for slug in slugs:
            key = pok_family_key(slug)
            if key is not None:
                strict[key] += 1
        prefix_over = {
            f
            for f in fams
            if sum(1 for s in slugs if s.startswith(f + "-")) > POK_FAMILY_MAX
        }
        still_over = {f for f in prefix_over if strict[f] > POK_FAMILY_MAX}
        assert len(prefix_over) - len(still_over) == 19
        assert still_over == {"guard-inv", "prompt-assent"}

    def test_the_corrected_count_agrees_with_the_strict_derivation(
        self, repo: POKRepository
    ) -> None:
        """The repository's count agrees with the census derivation on a
        scratch fixture — the method the refusal consumes IS the
        corrected one."""
        for i in range(POK_FAMILY_MAX + 1):
            _insert(repo, f"guard-inv-{i + 1:02d}-x", project_id="p1")
        # A longer family and an unnumbered slug that the OLD prefix
        # count would have swept in.
        for i in range(5):
            _insert(repo, f"guard-inv-long-{i + 1:02d}-x", project_id="p1")
        _insert(repo, "guard-inv-unnumbered", project_id="p1")
        assert repo.count_active_in_family("p1", "guard-inv") == POK_FAMILY_MAX + 1
        assert repo.count_active_in_family("p1", "guard-inv-long") == 5
