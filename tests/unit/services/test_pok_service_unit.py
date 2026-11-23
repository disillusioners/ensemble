"""POK service unit pack (POK Phase 1 — write/query/curate policy).

D1 legs: AC3 (universal read + pinned rank-1), AC4 (supersede txn;
dual-active structurally impossible), AC10 (default-project typed
refusals BOTH directions), AC14 (embedding lag surfaced, never
fatal), AC16 (disputed readable + laundering guard + archived
forward-only), AC19 (terminality cured: refuse → archive → succeed),
AC20 (escape-aware sizing: pok_body_oversize on the ESCAPED length;
≤239-escaped passes).

The engine is a throwaway in-memory SQLite (create_all over the two
POK tables) — no InstanceManager is ever constructed (the memoir
fence discipline). asyncio_mode=auto (pyproject).
"""

from __future__ import annotations

import pytest
from sqlalchemy.engine import create_engine
from sqlmodel import SQLModel

import daemon.repositories.pok.models  # noqa: F401 — register the tables
from daemon.repositories.pok.models import (
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.services.pok_service import (
    POKError,
    POKService,
    content_hash,
    is_curator,
    normalize_body,
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


# ── AC4: supersede txn + dual-active structurally impossible ─────────────


class TestAc4SupersedeTxn:
    def test_create_then_exact_dup_noop_echo(self, svc: POKService) -> None:
        first = svc.upsert(
            project_id="p1",
            slug="pipeline-01-merge-ci",
            body="Merging to master triggers CI.",
            agent_id="developer",
        )
        assert first.action == "created"
        dup = svc.upsert(
            project_id="p1",
            slug="pipeline-01-merge-ci",
            body="Merging to master triggers CI.",
            agent_id="developer",
        )
        assert dup.action == "no-op"
        assert dup.entry.id == first.entry.id

    def test_changed_content_supersedes_with_lineage_wire(
        self, svc: POKService
    ) -> None:
        v1 = svc.upsert(
            project_id="p1",
            slug="pipeline-01-merge-ci",
            body="Merging to master triggers CI.",
            agent_id="developer",
        )
        v2 = svc.upsert(
            project_id="p1",
            slug="pipeline-01-merge-ci",
            body="Merging to master triggers CI — verify the digest.",
            agent_id="developer",
        )
        assert v2.action == "superseded"
        assert v2.superseded_id == v1.entry.id
        prior = svc._repo.get(v1.entry.id)
        assert prior.status == "superseded"
        assert prior.superseded_by_id == v2.entry.id
        assert prior.pinned is False  # pin never carries over

    def test_dual_active_structurally_impossible(self, svc: POKService) -> None:
        """The uq_pok_active_slug guard — a RAW second active insert on
        the same (project, slug) IntegrityErrors even bypassing the
        service (the structural guard, not a service convention)."""
        import sqlalchemy

        svc.upsert(
            project_id="p1", slug="x-01-a", body="v1", agent_id="developer"
        )
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            svc._repo.insert(
                ProjectOperatingKnowledge(
                    project_id="p1",
                    slug="x-01-a",
                    body="v2",
                    content_sha256=content_hash("v2"),
                    created_by_agent="developer",
                )
            )

    def test_supersede_frees_the_slug_for_the_successor(
        self, svc: POKService
    ) -> None:
        svc.upsert(project_id="p1", slug="x-01-a", body="v1", agent_id="developer")
        v2 = svc.upsert(
            project_id="p1", slug="x-01-a", body="v2 differs", agent_id="developer"
        )
        assert v2.action == "superseded"
        active = svc._repo.get_by_slug("p1", "x-01-a", status="active")
        assert active.id == v2.entry.id

    def test_supersede_within_family_is_count_preserving(
        self, svc: POKService
    ) -> None:
        svc.upsert(project_id="p1", slug="f-01-a", body="one", agent_id="d")
        svc.upsert(project_id="p1", slug="f-02-b", body="two", agent_id="d")
        svc.upsert(project_id="p1", slug="f-03-c", body="three", agent_id="d")
        swapped = svc.upsert(
            project_id="p1", slug="f-01-a", body="one v2", agent_id="d"
        )
        assert swapped.action == "superseded"  # never pok_family_full

    def test_lineage_walk_oldest_first(self, svc: POKService) -> None:
        v1 = svc.upsert(project_id="p1", slug="c-01-a", body="v1", agent_id="d")
        v2 = svc.upsert(project_id="p1", slug="c-01-a", body="v2 x", agent_id="d")
        v3 = svc.upsert(project_id="p1", slug="c-01-a", body="v3 y", agent_id="d")
        chain = svc._repo.lineage(v3.entry.id)
        assert [r.id for r in chain] == [v1.entry.id, v2.entry.id, v3.entry.id]


# ── AC10: default-project typed refusals BOTH directions ────────────────


class TestAc10DefaultProjectRefusals:
    def test_write_refused_by_id(self, svc: POKService) -> None:
        import daemon.constants as C

        C.SYSTEM_DEFAULT_PROJECT_ID = "sys-default"
        try:
            with pytest.raises(POKError) as ei:
                svc.upsert(
                    project_id="sys-default",
                    slug="x",
                    body="fact",
                    agent_id="developer",
                )
            assert ei.value.code == "pok_default_project"
        finally:
            C.SYSTEM_DEFAULT_PROJECT_ID = None

    def test_write_refused_by_name_fallback(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="whatever",
                slug="x",
                body="fact",
                agent_id="developer",
                project_name="__system_default__",
            )
        assert ei.value.code == "pok_default_project"

    def test_read_refused(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.query(
                project_id="whatever",
                agent_id="developer",
                project_name="__system_default__",
            )
        assert ei.value.code == "pok_default_project"

    def test_curate_refused(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.curate(
                project_id="whatever",
                slug="x",
                agent_id="leader",
                pinned=True,
                project_name="__system_default__",
            )
        assert ei.value.code == "pok_default_project"

    def test_non_default_projects_are_unaffected(self, svc: POKService) -> None:
        import daemon.constants as C

        C.SYSTEM_DEFAULT_PROJECT_ID = "sys-default"
        try:
            out = svc.upsert(
                project_id="p1", slug="ok-01-a", body="fine", agent_id="developer"
            )
            assert out.action == "created"
        finally:
            C.SYSTEM_DEFAULT_PROJECT_ID = None


# ── AC16: disputed + laundering + archived forward-only ─────────────────


class TestAc16DisputeAndArchive:
    def test_disputed_row_stays_readable(self, svc: POKService) -> None:
        svc.upsert(
            project_id="p1", slug="plain-a", body="A plain fact.", agent_id="d"
        )
        svc.curate(
            project_id="p1", slug="plain-a", agent_id="leader", disputed=True
        )
        q = svc.query(project_id="p1", query="plain", agent_id="worker")
        assert any(e.slug == "plain-a" for e in q.entries)

    def test_dispute_laundering_guard_new_slug_refused(
        self, svc: POKService
    ) -> None:
        body = "Always run the daoduc backup before any cluster deploy."
        svc.upsert(project_id="p1", slug="plain-a", body=body, agent_id="d")
        svc.curate(
            project_id="p1", slug="plain-a", agent_id="leader", disputed=True
        )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1",
                slug="plain-renamed",
                body="always run the DAODUC   backup before any cluster deploy.",
                agent_id="d",
            )
        assert ei.value.code == "pok_dispute_laundering"
        assert ei.value.details.get("disputed_slug") == "plain-a"

    def test_laundering_guard_clears_when_undisputed(
        self, svc: POKService
    ) -> None:
        body = "Always run the daoduc backup before any cluster deploy."
        svc.upsert(project_id="p1", slug="plain-a", body=body, agent_id="d")
        svc.curate(
            project_id="p1", slug="plain-a", agent_id="leader", disputed=True
        )
        svc.curate(
            project_id="p1", slug="plain-a", agent_id="leader", disputed=False
        )
        out = svc.upsert(
            project_id="p1", slug="plain-renamed", body=body.upper(), agent_id="d"
        )
        assert out.action == "created"

    def test_archived_forward_only(self, svc: POKService) -> None:
        out = svc.upsert(project_id="p1", slug="arch-a", body="old fact", agent_id="d")
        svc.curate(
            project_id="p1", slug="arch-a", agent_id="leader", archived=True
        )
        # Re-read BY ROW ID (house shape: capture the id from the write
        # outcome, re-read by id). A by-slug ``status=None`` re-read is
        # the UNORDERED query the curator defect lived in — it returns
        # whichever row the database plan yields first.
        row = svc._repo.get(out.entry.id)
        assert row.status == "archived"
        assert row.pinned is False
        with pytest.raises(POKError) as ei:
            svc.curate(
                project_id="p1", slug="arch-a", agent_id="leader", archived=False
            )
        assert ei.value.code == "pok_unarchive_forward_only"

    def test_archived_row_excluded_from_query(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="arch-a", body="old fact unique", agent_id="d")
        svc.curate(
            project_id="p1", slug="arch-a", agent_id="leader", archived=True
        )
        q = svc.query(project_id="p1", query="old fact unique", agent_id="worker")
        assert all(e.slug != "arch-a" for e in q.entries)

    def test_already_archived_refusal(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="arch-a", body="old", agent_id="d")
        svc.curate(project_id="p1", slug="arch-a", agent_id="leader", archived=True)
        with pytest.raises(POKError) as ei:
            svc.curate(
                project_id="p1", slug="arch-a", agent_id="leader", archived=True
            )
        assert ei.value.code == "pok_already_archived"


# ── AC19: terminality cured (refuse → archive → succeed) ────────────────


class TestAc19TerminalityCured:
    def test_active_cap_constant_is_1024(self) -> None:
        """The shipped active-entry cap is PINNED.

        The refusal leg below seeds the active counter (it never
        inserts hundreds of real rows), so this leg is the named guard
        against a regression of the cap to an older value.
        """
        from daemon.services.pok_service import POK_ACTIVE_CAP_PER_PROJECT

        assert POK_ACTIVE_CAP_PER_PROJECT == 1024

    def test_active_cap_refuse_then_archive_cure_then_succeed(
        self, svc: POKService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refuse at the SHIPPED cap, name the cure, then succeed.

        FAST + honest: the admission gate's only input is the repo's
        active COUNTER, so the counter is seeded at the real shipped
        cap instead of inserting hundreds of real rows. Three real rows
        ARE inserted (small and honest) so the archive-cure TARGET
        exists and the refuse → archive → succeed flow runs end-to-end.
        """
        from daemon.services import pok_service as pok_mod

        cap = pok_mod.POK_ACTIVE_CAP_PER_PROJECT
        # The refusal mechanics below are cap-agnostic (the counter is
        # seeded), so THIS pin is what makes the refusal leg itself
        # red when the cap regresses to an older value.
        assert cap == 1024

        seeded = 3
        for i in range(seeded):
            svc.upsert(
                project_id="p2",
                slug=f"f{i:02d}-a-x",
                body=f"filler fact {i} unique content here",
                agent_id="developer",
            )
        # Seed the counter at the SHIPPED cap: the real rows plus the
        # gap that fills the cap. Archiving one row drops the reported
        # count by one, so the cured retry genuinely succeeds below.
        real_count = svc._repo.count_active
        monkeypatch.setattr(
            svc._repo,
            "count_active",
            lambda pid: real_count(pid) + (cap - seeded),
        )
        assert svc._repo.count_active("p2") == cap

        # refuse-never-evict — the refusal names the archive cure
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p2",
                slug="one-too-many",
                body="the next active fact",
                agent_id="developer",
            )
        assert ei.value.code == "pok_active_cap"
        # the limit is rendered FROM THE CONSTANT — this phrase only
        # exists if the constant was read at refusal time (a hardcoded
        # literal would fail it).
        assert f"{cap}-cap refuses rather than evicts" in ei.value.message
        # ...and the refusal still names the archive cure.
        assert "archive" in ei.value.message.lower()
        assert "pok_curate" in ei.value.message
        cure = ei.value.details.get("oldest_least_verified")
        assert cure, "the refusal must NAME the cure target"
        # The named cure must be the TRUE one: the named ACTIVE row,
        # archived BY SLUG. The old message advertised "archive a
        # superseded fact" — un-actionable, since a superseded row is
        # curated THROUGH its successor (and the resolver used to
        # refuse exactly this class of slug ⇒ the escape hatch could
        # fail with pok_not_found at the cap ⇒ a hard lock).
        assert "superseded fact" not in ei.value.message
        assert "ACTIVE entry" in ei.value.message
        assert cure in ei.value.message
        # archive the named cure → the write succeeds
        svc.curate(project_id="p2", slug=cure, agent_id="leader", archived=True)
        out = svc.upsert(
            project_id="p2",
            slug="one-too-many",
            body="the next active fact",
            agent_id="developer",
        )
        assert out.action == "created"

    def test_family_full_refusal_names_the_cure(self, svc: POKService) -> None:
        """PREMISE CORRECTION (fix iteration 1, F1): the refusal must
        advertise a WORKABLE cure.

        A same-slug supersede is COUNT-PRESERVING by construction (the
        prior active row leaves ``active`` in the same transaction the
        successor enters it — both carry the same slug, hence the same
        family) — it does NOT free a slot; only ARCHIVING one via
        pok_curate does. The old message offered the supersede as a
        slot-freeing cure, which is the unworkable-cure defect. This
        test now pins the WORKABLE cure and, if a supersede is
        mentioned at all, pins its non-slot-freeing qualifier."""
        from daemon.services.pok_service import POK_FAMILY_MAX

        for i in range(POK_FAMILY_MAX):
            svc.upsert(
                project_id="p3",
                slug=f"dep-{i + 1:02d}-m",
                body=f"deploy member {i}",
                agent_id="developer",
            )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p3",
                slug="dep-04-m",
                body="the 4th member",
                agent_id="developer",
            )
        assert ei.value.code == "pok_family_full"
        msg = ei.value.message
        # the family, its LIVE size, and the ACTIVE member slugs
        assert "'dep'" in msg
        assert f"holds {POK_FAMILY_MAX} active entries" in msg
        for i in range(POK_FAMILY_MAX):
            assert f"dep-{i + 1:02d}-m" in msg
        # the ONLY slot-freeing cure is ARCHIVE-via-pok_curate …
        assert "archive one via pok_curate to free a slot" in msg
        # … and the supersede, WHICH THE MESSAGE ALWAYS OFFERS, must be
        # qualified as NOT slot-freeing (a bare supersede claim = the F1
        # defect). UNCONDITIONAL (fix iteration 2, W6): the old
        # ``if "supersede" in msg:`` guard proved NOTHING — a message
        # that silently dropped the supersede stayed green.
        assert "supersede" in msg
        assert "does not free a slot" in msg.lower()
        # the members are relabelled as PREFIX-SHARING rows (W2 — the
        # count is a ``startswith`` superset, not family membership).
        assert "prefix-sharing active rows" in msg
        # the archive count is DERIVED from the live count (CRITICAL 1).
        assert ei.value.details["archive_needed"] == 1

    def test_over_cap_refusal_reports_live_count_and_archive_need(
        self, svc: POKService
    ) -> None:
        """CRITICAL 2 (fix iteration 2): the refusal must report the LIVE
        count and the ARCHIVE COUNT — not echo the bound.

        The at-cap case (live == bound) is satisfied by a service that
        reports the constant forever, so this test seeds the family
        OVER CAP directly through the REPOSITORY (the service gate is
        bypassed): 4 seeded members against a bound of 3. A
        bound-echoing message (3) therefore FAILS here, and so does the
        singular "archive one … to free a slot" cure — archiving one
        would still leave 3 (>= the bound), so the real cure is 2.
        """
        from daemon.services.pok_service import POK_FAMILY_MAX

        for i in range(POK_FAMILY_MAX + 1):
            svc._repo.insert(
                ProjectOperatingKnowledge(
                    project_id="p5",
                    slug=f"over-cap-{i + 1:02d}-m",
                    body=f"seeded member {i}",
                    content_sha256=content_hash(f"seeded member {i}"),
                    created_by_agent="developer",
                )
            )
        # the seeding bypassed the gate — the family is already OVER cap
        assert svc._repo.count_active_in_family("p5", "over-cap") == POK_FAMILY_MAX + 1
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p5",
                slug="over-cap-05-m",
                body="the 5th member",
                agent_id="developer",
            )
        assert ei.value.code == "pok_family_full"
        msg = ei.value.message
        # the LIVE count (4) — NOT the bound (3)
        assert f"holds {POK_FAMILY_MAX + 1} active entries" in msg
        assert f"holds {POK_FAMILY_MAX} active entries" not in msg
        # the OVER-BY amount and the bound are both named
        assert "over the bound by 1" in msg
        assert f"the bound of {POK_FAMILY_MAX}" in msg
        # the ARCHIVE COUNT (need = live - bound + 1 = 2) — message AND details
        need = POK_FAMILY_MAX + 1 - POK_FAMILY_MAX + 1
        assert need == 2
        assert "archive (at least) 2 members" in msg
        assert ei.value.details["archive_needed"] == need
        assert ei.value.details["active_count"] == POK_FAMILY_MAX + 1
        assert ei.value.details["family"] == "over-cap"
        # the seeded members are listed as prefix-sharing rows
        assert "prefix-sharing active rows" in msg
        for i in range(POK_FAMILY_MAX + 1):
            assert f"over-cap-{i + 1:02d}-m" in msg
        # the supersede stays offered, qualified as slot-neutral
        assert "supersede" in msg
        assert "does not free a slot" in msg.lower()

    def test_pin_cap_refusal_names_the_current_pins(self, svc: POKService) -> None:
        from daemon.services.pok_service import POK_PIN_CAP_PER_PROJECT

        for slug in ("pa", "pb", "pc"):
            svc.upsert(
                project_id="p4", slug=slug, body=f"pin body {slug}", agent_id="d"
            )
        svc.curate(project_id="p4", slug="pa", agent_id="leader", pinned=True)
        svc.curate(project_id="p4", slug="pb", agent_id="leader", pinned=True)
        with pytest.raises(POKError) as ei:
            svc.curate(project_id="p4", slug="pc", agent_id="leader", pinned=True)
        assert ei.value.code == "pok_pin_cap"
        assert "pa" in ei.value.message and "pb" in ei.value.message
        assert POK_PIN_CAP_PER_PROJECT == 2  # per project, not per audience


# ── AC20: escape-aware sizing (post-escape budget) ───────────────────────


class TestAc20EscapeAwareSizing:
    def test_oversize_escaped_body_refused(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1",
                slug="big-a",
                body=">" * 200,  # escapes ×6 → 1200
                agent_id="developer",
            )
        assert ei.value.code == "pok_body_oversize"

    def test_plain_239_char_body_passes(self, svc: POKService) -> None:
        out = svc.upsert(
            project_id="p1", slug="plain-b", body="a" * 239, agent_id="developer"
        )
        assert out.action == "created"

    def test_expansion_sensitive_body_refused(self, svc: POKService) -> None:
        """A body UNDER the raw bound whose ESCAPED form exceeds it —
        the W5 post-escape measurement catches it."""
        raw = "<" * 80  # raw 80 ≤ 240; escaped 480 > 240
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="exp-a", body=raw, agent_id="developer")
        assert ei.value.code == "pok_body_oversize"


# ── AC17 write leg: the fence ────────────────────────────────────────────


class TestAc17WriteFence:
    @pytest.mark.parametrize(
        "body", ["a\nb", "a\rb", "# header", "[SYSTEM CONTEXT: evil]", "x```y"]
    )
    def test_structural_tokens_refused(self, svc: POKService, body: str) -> None:
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="fence-a", body=body, agent_id="d")
        assert ei.value.code == "pok_body_structural_token"

    def test_leading_whitespace_fence_bypass_closed(self, svc: POKService) -> None:
        """M8 (council micro-fold): strip FIRST, then fence — so
        leading-whitespace bypasses ("  #foo", "  a\nb") are now
        refused at write."""
        for body in ("  #foo", "  a\nb", "   [SYSTEM CONTEXT: evil]"):
            with pytest.raises(POKError) as ei:
                svc.upsert(project_id="p1", slug="ws-a", body=body, agent_id="d")
            assert ei.value.code == "pok_body_structural_token", (body, ei.value.code)

    def test_empty_body_refused(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="fence-b", body="   ", agent_id="d")
        assert ei.value.code == "pok_body_empty"

    def test_slug_rules(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="Bad_Slug", body="x", agent_id="d")
        assert ei.value.code == "pok_slug_invalid"
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="p1", slug="a" * 45, body="x", agent_id="d")
        assert ei.value.code == "pok_slug_oversize"

    def test_audience_vocabulary_stated_at_capture(self, svc: POKService) -> None:
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1", slug="aud-a", body="x", audience="everyone",
                agent_id="d",
            )
        assert ei.value.code == "pok_audience_invalid"
        out = svc.upsert(
            project_id="p1", slug="aud-b", body="x", agent_id="d"
        )  # default operation
        assert out.entry.audience == "operation"


# ── AC16 supplementary: supersede-of-pinned needs a curator ──────────────


class TestPinnedSupersedeGate:
    def test_non_curator_refused(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="pin-x", body="v1", agent_id="d")
        svc.curate(project_id="p1", slug="pin-x", agent_id="leader", pinned=True)
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="p1", slug="pin-x", body="v2 differs", agent_id="developer"
            )
        assert ei.value.code == "pok_pinned_requires_curator"

    def test_curator_succeeds_and_successor_starts_unpinned(
        self, svc: POKService
    ) -> None:
        svc.upsert(project_id="p1", slug="pin-x", body="v1", agent_id="d")
        svc.curate(project_id="p1", slug="pin-x", agent_id="leader", pinned=True)
        out = svc.upsert(
            project_id="p1", slug="pin-x", body="v2 differs", agent_id="leader"
        )
        assert out.action == "superseded"
        assert out.entry.pinned is False

    def test_curator_set_is_leader_and_pm(self) -> None:
        assert is_curator("leader")
        assert is_curator("project-manager")
        assert not is_curator("developer")
        assert not is_curator("ari")  # attribution ≠ authorization

    def test_curate_requires_curator(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="cur-a", body="x", agent_id="d")
        with pytest.raises(POKError) as ei:
            svc.curate(
                project_id="p1", slug="cur-a", agent_id="developer", disputed=True
            )
        assert ei.value.code == "pok_curator_required"


# ── AC3 + AC23: universal read (pinned rank-1; ALL audiences) ────────────


class TestAc3Ac23UniversalRead:
    def test_pinned_bypass_ranks_first_for_its_query(
        self, svc: POKService
    ) -> None:
        svc.upsert(
            project_id="p1",
            slug="pipeline-03-deploy-order",
            body="Deploy order: daoduc backup first.",
            agent_id="devops",
        )
        svc.curate(
            project_id="p1", slug="pipeline-03-deploy-order",
            agent_id="leader", pinned=True,
        )
        svc.upsert(
            project_id="p1",
            slug="other-01-x",
            body="Deploy order matters too, differently.",
            agent_id="devops",
        )
        q = svc.query(
            project_id="p1", query="deploy order", agent_id="developer"
        )
        assert q.entries[0].slug == "pipeline-03-deploy-order"
        assert q.entries[0].pinned is True

    def test_universal_read_all_audiences_any_position(
        self, svc: POKService
    ) -> None:
        """AC23: a developer's pok_query returns the OPERATION-only
        deploy facts — the audience field never filters a read."""
        svc.upsert(
            project_id="p1", slug="op-only-fact",
            body="The daoduc backup runs first.", audience="operation",
            agent_id="devops",
        )
        svc.upsert(
            project_id="p1", slug="impl-only-fact",
            body="The test suite runs before merge.", audience="implementation",
            agent_id="developer",
        )
        svc.upsert(
            project_id="p1", slug="both-fact",
            body="The artifact is a container image.", audience="both",
            agent_id="developer",
        )
        for viewer in ("developer", "worker", "ari", "leader", "devops"):
            q = svc.query(project_id="p1", query="backup test artifact", agent_id=viewer)
            slugs = {e.slug for e in q.entries}
            assert {"op-only-fact", "impl-only-fact", "both-fact"} <= slugs, (
                f"universal read failed for {viewer}: {slugs}"
            )

    def test_embedding_lag_surfaced_not_fatal(self, svc: POKService) -> None:
        """AC14: no embeddings rows → bm25_only flag + every returned
        entry named in the lag list; the read still succeeds."""
        svc.upsert(project_id="p1", slug="lag-a", body="unique body words", agent_id="d")
        q = svc.query(project_id="p1", query="unique body words", agent_id="d")
        assert q.bm25_only is True
        assert "lag-a" in q.embedding_lag_entries
        assert q.entries  # the read is NOT fatal

    def test_domain_filter(self, svc: POKService) -> None:
        svc.upsert(
            project_id="p1", slug="dom-a", body="deploy fact", domain="deploy",
            agent_id="d",
        )
        svc.upsert(
            project_id="p1", slug="dom-b", body="artifact fact", domain="artifact",
            agent_id="d",
        )
        q = svc.query(project_id="p1", query="fact", domain="deploy", agent_id="d")
        assert {e.slug for e in q.entries} == {"dom-a"}
        with pytest.raises(POKError) as ei:
            svc.query(project_id="p1", query="x", domain="bogus", agent_id="d")
        assert ei.value.code == "pok_domain_invalid"

    def test_access_bookkeeping_bumps(self, svc: POKService) -> None:
        out = svc.upsert(project_id="p1", slug="acc-a", body="counter fact", agent_id="d")
        svc.query(project_id="p1", query="counter", agent_id="d")
        row = svc._repo.get(out.entry.id)
        assert row.access_count == 1
        assert row.last_accessed_at is not None

    def test_insert_stamps_last_verified_naturally(self, svc: POKService) -> None:
        """W3 (frozen A-8): a freshly inserted row carries a
        ``last_verified`` stamp — no DB-planted timestamp needed for
        the decay machinery to have a real clock to read."""
        out = svc.upsert(project_id="p1", slug="fresh-a", body="fresh fact", agent_id="d")
        row = svc._repo.get(out.entry.id)
        assert row.last_verified is not None
        assert row.last_verified == out.entry.last_verified

    def test_noop_echo_refreshes_last_verified(self, svc: POKService) -> None:
        """W3 (frozen A-8): the exact-dup no-op echo REFRESHES
        ``last_verified`` (restarts the staleness clock), so decay is
        reachable naturally — a row goes stale only if it is never
        re-echoed for POK_VERIFY_WINDOW_DAYS."""
        import time as _t

        out = svc.upsert(project_id="p1", slug="echo-a", body="echo body", agent_id="d")
        first = svc._repo.get(out.entry.id).last_verified
        _t.sleep(0.01)
        echo = svc.upsert(project_id="p1", slug="echo-a", body="echo body", agent_id="d")
        assert echo.action == "no-op"
        second = svc._repo.get(out.entry.id).last_verified
        assert second is not None and second > first

class TestCountActiveInFamilyBoundary:
    """Item 7 (council micro-fold): count_active_in_family regex verdict.

    VERDICT (CORRECTED for the multi-token family-key semantics — fix
    iteration 2 restored the DELETED half of the collision claim).
    Family keys now come from ``_slug_family`` = regex group 1 = a
    (possibly hyphenated) run of letter-led tokens
    (``[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*``), and
    ``count_active_in_family`` still matches
    ``slug.startswith(f"{family}-")``. A hyphenated family head (e.g.
    ``prompt-assent``) is now a real family key, not collapsed to its
    first token. Non-matching slugs (no NN ordinal) are excluded
    upstream (``_slug_family`` returns None).

    COLLISION VERDICT — two DIFFERENT things, both pinned below:

    * ``dep`` vs ``deploy`` CANNOT collide. The prefix is
      hyphen-TERMINATED (``"dep-"``), so a ``deploy-…`` slug does not
      match it. Two single-token families never share a prefix.
    * ``skill`` vs ``skill-e6`` DOES collide — in ONE direction only.
      The count is a ``startswith`` superset, not a family-membership
      query: the SHORT family ``skill`` prefix-counts the LONG family
      ``skill-e6``'s rows (a longer family inflates a shorter prefix),
      while the LONG family ``skill-e6`` NEVER counts the short
      family's ``skill-*`` rows. The leak is long → short, one way.
    """

    def test_hyphenated_tails_counted_in_same_family(self, svc: POKService) -> None:
        svc.upsert(project_id="p1", slug="dep-01-backup-first", body="a", agent_id="d")
        svc.upsert(project_id="p1", slug="dep-02-backup-extra", body="b", agent_id="d")
        assert svc._repo.count_active_in_family("p1", "dep") == 2

    def test_prefix_family_does_not_collide(self, svc: POKService) -> None:
        # "dep" and "deploy" are distinct families; startswith("dep-")
        # must NOT match "deploy-..." slugs.
        svc.upsert(project_id="p1", slug="dep-01-a", body="a", agent_id="d")
        svc.upsert(project_id="p1", slug="deploy-01-b", body="b", agent_id="d")
        assert svc._repo.count_active_in_family("p1", "dep") == 1
        assert svc._repo.count_active_in_family("p1", "deploy") == 1

    def test_prefix_sharing_leaks_one_direction_only(self, svc: POKService) -> None:
        """W2 (fix iteration 2): the prefix-superset leak is REAL, and
        it is ONE-DIRECTIONAL (long family → short family).

        The SHORT family ``skill`` prefix-counts the LONG family
        ``skill-e6``'s rows; the inverse never holds (the long prefix
        is hyphen-terminated).
        """
        svc.upsert(project_id="p1", slug="skill-01-a", body="a", agent_id="d")
        svc.upsert(project_id="p1", slug="skill-e6-01-x", body="b", agent_id="d")
        # long -> short: the SHORT family counts the long family's row
        assert svc._repo.count_active_in_family("p1", "skill") == 2
        # short -> long: NEVER
        assert svc._repo.count_active_in_family("p1", "skill-e6") == 1

    def test_non_matching_slug_excluded(self, svc: POKService) -> None:
        # no NN ordinal → _slug_family returns None → the service skips
        # the family-count check entirely (count_active_in_family is only
        # ever called with a regex-extracted family key, never a raw
        # prefix). Pin the upstream exclusion here.
        from daemon.services.pok_service import _slug_family

        assert _slug_family("model-catalogs-not-drift") is None
        svc.upsert(
            project_id="p1", slug="model-catalogs-not-drift", body="x", agent_id="d"
        )
        # even a direct call with a prefix cannot manufacture a family
        # hit, because the caller (the service) never invokes it for
        # an unordered slug
        assert _slug_family("model-catalogs-not-drift") is None

    def test_empty_family_returns_zero(self, svc: POKService) -> None:
        assert svc._repo.count_active_in_family("p1", "nope") == 0


    def test_normalization_semantics(self) -> None:
        assert normalize_body("  A  B  ") == "a b"
        assert normalize_body("A\tB") == "a b"
        assert normalize_body("A\nB") == "a b"
        assert content_hash("x") == content_hash("x")
        assert content_hash("x") != content_hash("y")


# ── Multi-token family key (corrected pattern) ────────────────────────────


class TestMultiTokenFamilyKey:
    """The CORRECTED (multi-token) family key — fix iteration 2.

    The family key is the maximal run of letter-led tokens before the
    leftmost ``-NN-`` ordinal: ``prompt-assent-01-x`` → family
    ``prompt-assent`` (NOT ``prompt``), ``snapshot-l3-02-y`` →
    ``snapshot-l3``. A slug with no ``-NN-`` ordinal has NO family and
    is exempt from the family gate.

    Pinned here: the family-key helper (direct), the LITERAL pattern
    constant (the single source in ``daemon/repositories/pok/models.py``),
    the at-cap refusal, the PREFIX-SUPERSET leak in BOTH directions
    (W2), and the supersede exemption.
    """

    def test_family_key_pattern_literal_pin(self) -> None:
        """LITERAL pin on the canonical pattern constant (fix iteration
        2, suggestion 1). The helper is DERIVED from it, so any change
        to the constant must show up as a DELIBERATE, visible diff
        here — not as a silent behaviour change."""
        from daemon.repositories.pok.models import POK_FAMILY_SLUG_PATTERN

        assert POK_FAMILY_SLUG_PATTERN == (
            r"^([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*)-(\d{2})-(.+)$"
        )

    def test_multi_token_gate_refuses_fourth_member(self, svc: POKService) -> None:
        from daemon.services.pok_service import POK_FAMILY_MAX

        for i in range(POK_FAMILY_MAX):
            svc.upsert(
                project_id="pm1",
                slug=f"prompt-assent-{i + 1:02d}-x",
                body=f"member {i}",
                agent_id="developer",
            )
        with pytest.raises(POKError) as ei:
            svc.upsert(
                project_id="pm1",
                slug="prompt-assent-04-y",
                body="the 4th member",
                agent_id="developer",
            )
        assert ei.value.code == "pok_family_full"
        msg = str(ei.value)
        assert "prompt-assent" in msg
        # COUNT PIN (fix iteration 1, F2): a bare `"3" in msg` is
        # satisfiable by the BOUND digit alone (3 appears in the bound
        # text too) — it proved nothing about the LIVE count. Pin the
        # phrase that carries the live count AND the structured value
        # the service passes (POKError.details).
        assert f"holds {POK_FAMILY_MAX} active entries" in msg
        assert ei.value.details["active_count"] == POK_FAMILY_MAX
        assert ei.value.details["family"] == "prompt-assent"
        for i in range(POK_FAMILY_MAX):
            assert f"prompt-assent-{i + 1:02d}-x" in msg

    def test_family_key_regex_pins_direct(self) -> None:
        """DIRECT pins on the corrected family key (fix iteration 1,
        F3).

        The corrected regex was otherwise pinned only TRANSITIVELY:
        under the OLD single-token pattern this class still reported
        3 passes — the other tests were pattern-insensitive. These
        pins are on the helper itself: a multi-token head IS a family
        key (group 1 = the whole letter-led token run), and a slug
        with no ``-NN-`` ordinal is NOT family-ordered."""
        from daemon.services.pok_service import _slug_family

        # positives — the hyphenated head is the family key
        assert _slug_family("prompt-assent-01-x") == "prompt-assent"
        assert _slug_family("snapshot-l3-02-y") == "snapshot-l3"
        assert _slug_family("skill-e6-04-a4-gauge") == "skill-e6"
        # negatives — no NN ordinal → no family (None → the service
        # skips the family-count check entirely)
        assert _slug_family("daoduc-helm-release-v28-kubectl-patch") is None
        assert _slug_family("artifact-is-container-image") is None

    def test_unnumbered_prefix_counting(self, svc: POKService) -> None:
        svc.upsert(project_id="pu", slug="verify-03-a", body="a", agent_id="d")
        svc.upsert(
            project_id="pu", slug="verify-something-unnumbered", body="b", agent_id="d"
        )
        assert svc._repo.count_active_in_family("pu", "verify") == 2

    def test_unnumbered_prefix_pushes_over_cap(self, svc: POKService) -> None:
        svc.upsert(project_id="pu2", slug="verify-01-a", body="a", agent_id="d")
        svc.upsert(project_id="pu2", slug="verify-02-b", body="b", agent_id="d")
        svc.upsert(
            project_id="pu2", slug="verify-something-unnumbered", body="c", agent_id="d"
        )
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="pu2", slug="verify-03-d", body="d", agent_id="d")
        assert ei.value.code == "pok_family_full"

    def test_supersede_exemption_multi_token(self, svc: POKService) -> None:
        for i in range(3):
            svc.upsert(
                project_id="ps",
                slug=f"prompt-assent-{i + 1:02d}-m",
                body=f"member {i}",
                agent_id="developer",
            )
        swapped = svc.upsert(
            project_id="ps",
            slug="prompt-assent-01-m",
            body="member 0 v2",
            agent_id="developer",
        )
        assert swapped.action == "superseded"

    def test_other_family_rows_do_not_refuse_a_different_family(
        self, svc: POKService
    ) -> None:
        """W2 (fix iteration 2, pin a): another family's rows must NOT
        refuse a DIFFERENT family's admission.

        ``skill-e6``'s own prefix count is 1 (its own rows only) — the
        ``skill`` family's rows do NOT leak INTO the long family (the
        leak runs long → short, one way), so the admission succeeds.
        """
        svc.upsert(project_id="pw", slug="skill-01-a", body="a", agent_id="d")
        svc.upsert(project_id="pw", slug="skill-02-b", body="b", agent_id="d")
        svc.upsert(project_id="pw", slug="skill-e6-01-x", body="c", agent_id="d")
        # the long family's own count is 1 — the short family's rows do
        # NOT leak into it
        assert svc._repo.count_active_in_family("pw", "skill-e6") == 1
        out = svc.upsert(
            project_id="pw", slug="skill-e6-02-y", body="d", agent_id="d"
        )
        assert out.action == "created"

    def test_prefix_leak_refuses_the_short_family_and_surfaces_it(
        self, svc: POKService
    ) -> None:
        """W2 (fix iteration 2, pin b): the inverse DOES refuse — and the
        refusal SURFACES the leak (the long family's row is listed as a
        PREFIX-SHARING row of the short family).
        """
        from daemon.services.pok_service import POK_FAMILY_MAX

        svc.upsert(project_id="px", slug="skill-01-a", body="a", agent_id="d")
        svc.upsert(project_id="px", slug="skill-02-b", body="b", agent_id="d")
        svc.upsert(project_id="px", slug="skill-e6-01-x", body="c", agent_id="d")
        with pytest.raises(POKError) as ei:
            svc.upsert(project_id="px", slug="skill-03-z", body="d", agent_id="d")
        assert ei.value.code == "pok_family_full"
        msg = ei.value.message
        assert "'skill'" in msg
        assert f"holds {POK_FAMILY_MAX} active entries" in msg
        assert "prefix-sharing active rows" in msg
        # the leaked row is SURFACED, labelled as prefix-sharing
        assert "skill-e6-01-x" in msg
        assert ei.value.details["family"] == "skill"
        assert ei.value.details["active_count"] == POK_FAMILY_MAX
        assert "skill-e6-01-x" in ei.value.details["members"]


# ── TASK-2: the slug→row resolver — curation must reach the OWNER ───────


class TestSlugOwnerResolution:
    """Curating a slug must flip THE ROW THAT OWNS IT.

    ONE slug is carried by a WHOLE LINEAGE: every supersede inserts a
    successor with the SAME slug (``uq_pok_active_slug`` frees the
    slug for the successor), so ``(project, slug)`` matches SEVERAL
    rows — the superseded ancestors plus the active head. The curator's
    lookup used to be an UNFILTERED single-row fetch, so the row it
    reached was whichever one the DATABASE PLAN returned first
    (PostgreSQL: the ROOT ancestor → every successor-generation row
    refused with ``pok_not_found``). The lookup is index-choice/
    plan-dependent and engine-agnostic — an unordered fetch MAY return
    either row depending on which index the planner picks — which is
    why this SQLite pack never showed the defect.

    The owner is resolved by STATUS PRECEDENCE (active > archived >
    superseded), applied over the whole match set — never by an
    ordering the database is free to choose.

    NON-VACUITY — how these legs fail under a ROOT-WALK resolution
    (the resolver walks the slug's lineage to the ROOT ancestor):
    * the root is the SUPERSEDED row, so ``curate(slug,
      archived=True)`` flips the ROOT (its status leaves 'superseded'
      for 'archived') and leaves the SUCCESSOR 'active' — the curator's
      intent is silently lost and the head keeps being injected;
    * and the root's ``superseded_by_id`` wire is rewritten, i.e. an
      ANCESTOR is mutated by a successor-generation curation.
    The legs below assert the opposite: the SUCCESSOR's row id is the
    one that changes, and every ancestor keeps its status, its
    ``superseded_by_id`` wire and its flags.
    """

    def test_curate_archives_the_successor_and_leaves_the_root_untouched(
        self, svc: POKService
    ) -> None:
        v1 = svc.upsert(project_id="p1", slug="chain-01-a", body="v1", agent_id="d")
        v2 = svc.upsert(
            project_id="p1", slug="chain-01-a", body="v2 differs", agent_id="d"
        )
        assert v2.action == "superseded"
        assert svc._repo.get(v1.entry.id).status == "superseded"

        out = svc.curate(
            project_id="p1", slug="chain-01-a", agent_id="leader", archived=True
        )
        # OBSERVABLE STATE — which row changed, by row id + status.
        assert out.id == v2.entry.id
        assert svc._repo.get(v2.entry.id).status == "archived"
        # SAFETY — the ROOT ancestor is untouched (status, wire, flags).
        root = svc._repo.get(v1.entry.id)
        assert root.status == "superseded"
        assert root.superseded_by_id == v2.entry.id
        assert root.pinned is False
        assert root.disputed is False

    def test_curate_never_mutates_any_ancestor_of_a_three_generation_chain(
        self, svc: POKService
    ) -> None:
        v1 = svc.upsert(project_id="p1", slug="chain-02-a", body="v1", agent_id="d")
        v2 = svc.upsert(
            project_id="p1", slug="chain-02-a", body="v2 differs", agent_id="d"
        )
        v3 = svc.upsert(
            project_id="p1", slug="chain-02-a", body="v3 differs more", agent_id="d"
        )
        assert v3.action == "superseded"

        out = svc.curate(
            project_id="p1", slug="chain-02-a", agent_id="leader", archived=True
        )
        assert out.id == v3.entry.id
        assert svc._repo.get(v3.entry.id).status == "archived"
        # BOTH ancestors keep their status AND their lineage wire.
        first = svc._repo.get(v1.entry.id)
        second = svc._repo.get(v2.entry.id)
        assert first.status == "superseded"
        assert first.superseded_by_id == v2.entry.id
        assert second.status == "superseded"
        assert second.superseded_by_id == v3.entry.id
        # The chain is still walkable end to end (query renders it).
        chain = svc._repo.lineage(v3.entry.id)
        assert [r.id for r in chain] == [v1.entry.id, v2.entry.id, v3.entry.id]

    def test_curate_on_a_root_only_slug_flips_only_that_row(
        self, svc: POKService
    ) -> None:
        """A slug with NO successor: the root IS the owner — it flips,
        and nothing else in the project moves."""
        v1 = svc.upsert(project_id="p1", slug="solo-01-a", body="solo fact", agent_id="d")
        before = {
            r.id
            for r in svc._repo.list_entries(
                "p1", statuses=("active", "superseded", "archived")
            )
        }
        out = svc.curate(
            project_id="p1", slug="solo-01-a", agent_id="leader", archived=True
        )
        assert out.id == v1.entry.id
        assert svc._repo.get(v1.entry.id).status == "archived"
        # No OTHER row changed: the id set is identical (a descendant
        # of a curated root must never be created or flipped).
        after = {
            r.id
            for r in svc._repo.list_entries(
                "p1", statuses=("active", "superseded", "archived")
            )
        }
        assert after == before
        assert len(
            [r for r in svc._repo.list_entries(
                "p1", statuses=("active", "superseded", "archived")
            ) if r.slug == "solo-01-a"]
        ) == 1

    def test_curate_does_not_resurrect_an_archived_ancestor(
        self, svc: POKService
    ) -> None:
        """E1 — THE COEXIST CASE: an ARCHIVED root beside an ACTIVE
        successor under one slug.

        Archive frees the slug; a later upsert inserts a FRESH active
        row beside the dead one. The OLD resolver could return the
        ARCHIVED root (status=None accepts it — only 'superseded' was
        rejected) ⇒ the flip landed on an INVISIBLE dead row while the
        tool reported ``"action": "curated"`` SUCCESS, and a pin on it
        was invisible to the active-only pin accounting.

        Required: the ACTIVE successor owns the slug — the flip lands
        on it, and the dead root is NEVER written.
        """
        v1 = svc.upsert(project_id="p1", slug="arch-01-a", body="first fact", agent_id="d")
        svc.curate(project_id="p1", slug="arch-01-a", agent_id="leader", archived=True)
        assert svc._repo.get(v1.entry.id).status == "archived"

        v2 = svc.upsert(
            project_id="p1", slug="arch-01-a", body="second fact differs", agent_id="d"
        )
        assert v2.action == "created"  # the archived row is NOT supersede-able
        # The COEXIST state: one ARCHIVED dead row + one ACTIVE owner
        # under the same slug.
        dead = svc._repo.get(v1.entry.id)
        owner = svc._repo.get(v2.entry.id)
        assert dead.status == "archived"
        assert owner.status == "active"
        dead_before = (dead.status, dead.pinned, dead.updated_at)

        out = svc.curate(
            project_id="p1", slug="arch-01-a", agent_id="leader", archived=True
        )
        # The flip landed on the ACTIVE owner — by row id.
        assert out.id == v2.entry.id
        assert svc._repo.get(v2.entry.id).status == "archived"
        # The dead root was NOT written at all (status, flags AND the
        # update stamp are untouched) — never un-archived, never
        # re-statused, never pinned.
        dead_after = svc._repo.get(v1.entry.id)
        assert (dead_after.status, dead_after.pinned, dead_after.updated_at) == (
            dead_before
        )
        assert dead_after.status == "archived"

    def test_curate_prefers_the_active_owner_over_an_archived_row(
        self, svc: POKService
    ) -> None:
        """E1 — the resolver's PREFERENCE is observable: with an
        ARCHIVED row and an ACTIVE row under one slug, the owner is
        the ACTIVE one (the archived row only owns the slug while no
        active row exists)."""
        v1 = svc.upsert(project_id="p1", slug="pref-01-a", body="first fact", agent_id="d")
        svc.curate(project_id="p1", slug="pref-01-a", agent_id="leader", archived=True)
        v2 = svc.upsert(
            project_id="p1", slug="pref-01-a", body="second fact differs", agent_id="d"
        )
        owner = svc._repo.resolve_slug_row("p1", "pref-01-a", scope="owner")
        assert owner.id == v2.entry.id
        assert owner.status == "active"
        assert owner.id != v1.entry.id
        # A pin lands on the ACTIVE owner too (the active-only pin
        # accounting can see it).
        svc.curate(project_id="p1", slug="pref-01-a", agent_id="leader", pinned=True)
        assert svc._repo.get(v2.entry.id).pinned is True
        assert svc._repo.get(v1.entry.id).pinned is False

    def test_unknown_slug_still_refuses_not_found(self, svc: POKService) -> None:
        """A genuinely-absent slug keeps the not-found refusal."""
        with pytest.raises(POKError) as ei:
            svc.curate(
                project_id="p1",
                slug="never-written-01-a",
                agent_id="leader",
                archived=True,
            )
        assert ei.value.code == "pok_not_found"

    def test_resolver_picks_the_active_owner_not_the_root(
        self, svc: POKService
    ) -> None:
        """The resolver CONTRACT (repo level): the owner of a slug is
        its active row — never the superseded root, whatever the
        database plan returns first."""
        v1 = svc.upsert(project_id="p1", slug="chain-03-a", body="v1", agent_id="d")
        v2 = svc.upsert(
            project_id="p1", slug="chain-03-a", body="v2 differs", agent_id="d"
        )
        owner = svc._repo.resolve_slug_row("p1", "chain-03-a", scope="owner")
        active = svc._repo.resolve_slug_row("p1", "chain-03-a", scope="active")
        assert owner.id == v2.entry.id
        assert owner.status == "active"
        assert active.id == v2.entry.id
        assert owner.id != v1.entry.id

    def test_resolver_active_scope_never_returns_an_archived_row(
        self, svc: POKService
    ) -> None:
        """The write path's scope: an ARCHIVED row is terminal — the
        'active' scope must not hand it back as a supersede target."""
        v1 = svc.upsert(project_id="p1", slug="term-01-a", body="first fact", agent_id="d")
        svc.curate(project_id="p1", slug="term-01-a", agent_id="leader", archived=True)
        assert svc._repo.resolve_slug_row("p1", "term-01-a", scope="active") is None
        owner = svc._repo.resolve_slug_row("p1", "term-01-a", scope="owner")
        assert owner.id == v1.entry.id
        assert owner.status == "archived"
