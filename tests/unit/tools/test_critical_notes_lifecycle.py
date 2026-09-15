"""Critical-notes lifecycle tools — Phase 1 (critical-notes-retrieval).

Covers the Phase-1 tool-layer contracts against a REAL SQLite repository
(file-backed, repo conventions — never StaticPool):

- ``project_cn_pin``: core cap-8 REJECT naming demotion candidates
  (reject-don't-evict, D2); pin/unpin stamps audit + review clock;
  superseded rows refuse to join the core tier.
- ``project_cn_supersede``: guards — old==new rejected; a SUPERSEDED row
  cannot supersede; both ids same-project; success bumps
  ``last_reviewed_at``.
- ``project_cn_remove`` cascade semantics (N6): default REFUSE when
  another row's ``superseded_by_id`` targets the entry; ``cascade=True``
  removes the target AND nullifies the pointers, returning the pointing
  rows to ACTIVE, with every re-activated id named in the response.
- R20 collision scope: superseded rows are invisible to the
  near-duplicate scan; a verbatim re-add after supersede inserts fresh.
- Reference bound dual enforcement + precedence: write-side REJECT is
  authoritative (>500 rejected); detail_ref accepted unbounded.
- ``project_cn_list`` maintenance marks: strike-through display for
  superseded rows, stale marks + proposal-only archive candidates;
  order stays created_at DESC.
- Pin suggestion (D2): a priority=critical write suggests pinning,
  never auto-pins.
- Config install hooks: install/reset of core_cap / reference_max /
  stale_days module state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.project.models  # noqa: F401  (table registration)
from daemon.repositories.project.repository import SQLModelProjectRepository
from daemon.tools.critical_notes import (
    create_critical_notes_tools,
    install_critical_notes_config,
    reset_critical_notes_config,
    _find_near_duplicate_entry,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite engine (NullPool + WAL + busy_timeout)."""
    db_path = tmp_path / "cn-lifecycle.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture
def repo(engine):
    return SQLModelProjectRepository(engine)


@pytest.fixture
def project_id(repo):
    project = repo.create(name="cn-lifecycle-project")
    return project.project_id


@pytest.fixture
def tools(repo):
    return {
        t.name: t
        for t in create_critical_notes_tools(
            repo, current_instance_id="leader-instance", agent_id="leader"
        )
    }


@pytest.fixture(autouse=True)
def _restore_config_defaults():
    """Keep module-level config state isolated across tests."""
    reset_critical_notes_config()
    yield
    reset_critical_notes_config()


@pytest.fixture
def seeded(repo, project_id):
    """A small deterministic store: old note superseded by new note."""
    old = repo.add_critical_note(
        project_id,
        source_agent="leader",
        category="decision",
        priority="high",
        summary="Deploy via launcher script X",
    )
    new = repo.add_critical_note(
        project_id,
        source_agent="leader",
        category="decision",
        priority="high",
        summary="Deploy via launcher script Y",
    )
    repo.supersede_critical_note(project_id, old.id, new.id)
    return {"old": old, "new": new}


# ─── Pin: cap-8 REJECT + demotion candidates (D2) ────────────────────────────


class TestProjectCnPin:
    def test_pin_sets_state_audit_and_review_clock(self, tools, repo, project_id, seeded):
        note = seeded["new"]
        result = tools["project_cn_pin"].invoke(
            {"project_id": project_id, "entry_id": note.id, "pinned": True}
        )
        assert result["pinned"] is True
        assert result["pinned_at"] is not None
        assert result["pinned_by"] == "leader"
        # Any leader write refreshes the review clock.
        assert result["last_reviewed_at"] is not None

    def test_unpin_refreshes_review_clock_keeps_audit(self, tools, repo, project_id, seeded):
        note = seeded["new"]
        tools["project_cn_pin"].invoke(
            {"project_id": project_id, "entry_id": note.id, "pinned": True}
        )
        result = tools["project_cn_pin"].invoke(
            {"project_id": project_id, "entry_id": note.id, "pinned": False}
        )
        assert result["pinned"] is False
        # Audit columns keep the LAST pin as history.
        assert result["pinned_at"] is not None
        assert result["pinned_by"] == "leader"

    def test_cap_8_reject_names_demotion_candidates_and_changes_nothing(
        self, tools, repo, project_id
    ):
        # Seed exactly core_cap (8) pinned notes.
        notes = []
        for i in range(8):
            n = repo.add_critical_note(
                project_id,
                source_agent="leader",
                category="pattern",
                priority="high",
                summary=f"Pin discipline contract number {i} unique",
            )
            repo.pin_critical_note(project_id, n.id, pinned=True, pinned_by="leader")
            notes.append(n)
        ninth = repo.add_critical_note(
            project_id,
            source_agent="leader",
            category="pattern",
            priority="high",
            summary="The ninth pinned candidate note unique",
        )
        result = tools["project_cn_pin"].invoke(
            {"project_id": project_id, "entry_id": ninth.id, "pinned": True}
        )
        assert "error" in result
        assert "cap of 8" in result["error"]
        # Demotion candidates are NAMED with actionable ids.
        assert "project_cn_pin(entry_id=" in result["error"]
        for n in notes[:3]:
            assert n.id in result["error"]
        # Reject-don't-evict: nothing changed.
        assert repo.count_pinned_critical_notes(project_id) == 8
        assert repo.get_critical_note(project_id, ninth.id).pinned is False

    def test_repinnning_an_already_pinned_row_does_not_hit_cap(
        self, tools, repo, project_id
    ):
        n = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="medium",
            summary="Already pinned row unique",
        )
        repo.pin_critical_note(project_id, n.id, pinned=True, pinned_by="leader")
        result = tools["project_cn_pin"].invoke(
            {"project_id": project_id, "entry_id": n.id, "pinned": True}
        )
        assert "error" not in result
        assert result["pinned"] is True

    def test_superseded_row_cannot_join_core_tier(self, tools, project_id, seeded):
        result = tools["project_cn_pin"].invoke(
            {"project_id": project_id, "entry_id": seeded["old"].id, "pinned": True}
        )
        assert "error" in result
        assert "SUPERSEDED" in result["error"]

    def test_cap_counts_only_active_pins_not_superseded_ones(
        self, tools, repo, project_id, seeded
    ):
        # The superseded "old" row is pinned in storage (legacy state) —
        # the derived core tier must not count it. "new" is the only
        # PINNED + ACTIVE row.
        repo.pin_critical_note(project_id, seeded["old"].id, pinned=True, pinned_by="leader")
        repo.pin_critical_note(project_id, seeded["new"].id, pinned=True, pinned_by="leader")
        assert repo.count_pinned_critical_notes(project_id) == 1  # only "new"


# ─── Supersede guards (§4.4 #24) ─────────────────────────────────────────────


class TestProjectCnSupersede:
    def test_success_sets_pointer_and_bumps_review_clock(self, tools, repo, project_id):
        from sqlmodel import Session
        a = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="high",
            summary="First version of the contract",
        )
        b = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="high",
            summary="Second version of the contract",
        )
        # Force an old review clock on `a` to observe the bump.
        stale_clock = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        with Session(repo.engine) as session:
            row = session.get(type(a), a.id)
            row.last_reviewed_at = stale_clock
            session.commit()
        result = tools["project_cn_supersede"].invoke(
            {"project_id": project_id, "old_id": a.id, "new_id": b.id}
        )
        assert "error" not in result
        assert result["superseded_by_id"] == b.id
        assert result["last_reviewed_at"] > stale_clock

    def test_rejects_old_equals_new(self, tools, repo, project_id, seeded):
        result = tools["project_cn_supersede"].invoke(
            {"project_id": project_id, "old_id": seeded["new"].id, "new_id": seeded["new"].id}
        )
        assert "error" in result
        assert "cannot supersede itself" in result["error"]

    def test_superseded_row_cannot_act_as_superseder(self, tools, repo, project_id, seeded):
        c = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="high",
            summary="Third version of the contract",
        )
        # old (already superseded by new) cannot supersede c.
        result = tools["project_cn_supersede"].invoke(
            {"project_id": project_id, "old_id": seeded["old"].id, "new_id": c.id}
        )
        assert "error" in result
        assert "cannot supersede another" in result["error"]

    def test_2_cycle_supersede_refused_new_is_already_superseded(
        self, tools, repo, project_id
    ):
        # ``supersede(A, B)`` then ``supersede(B, A)`` would succeed
        # without Guard 4 (R21): both rows point at each other and the
        # lineage graph loses partial-order. Guard 4 closes the cycle
        # by rejecting when ``new.superseded_by_id is not None``.
        a = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="high",
            summary="Cycle probe note A",
        )
        b = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="high",
            summary="Cycle probe note B",
        )
        # Step 1: A → B (legal: both ACTIVE).
        first = tools["project_cn_supersede"].invoke(
            {"project_id": project_id, "old_id": a.id, "new_id": b.id}
        )
        assert "error" not in first
        # Step 2: B → A MUST be refused (B is now superseded → cannot be
        # the superseder for the cycle-closing reverse direction).
        reverse = tools["project_cn_supersede"].invoke(
            {"project_id": project_id, "old_id": b.id, "new_id": a.id}
        )
        assert "error" in reverse
        assert "2-cycle" in reverse["error"]
        # Neither row gained the cycle pointer — A still points at B
        # (from step 1), B's pointer stays None (it was the superseder,
        # never the supersedee).
        assert repo.get_critical_note(project_id, a.id).superseded_by_id == b.id
        assert repo.get_critical_note(project_id, b.id).superseded_by_id is None

    def test_rejects_cross_project_ids(self, tools, repo, project_id):
        other = repo.create(name="cn-other-project")
        a = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="high",
            summary="Note in the first project",
        )
        repo.add_critical_note(
            other.project_id, source_agent="l", category="risk", priority="high",
            summary="Note in the second project",
        )
        result = tools["project_cn_supersede"].invoke(
            {"project_id": project_id, "old_id": a.id, "new_id": "nonexistent-id"}
        )
        assert "error" in result
        # The pointer must NOT be written.
        assert repo.get_critical_note(project_id, a.id).superseded_by_id is None


# ─── Remove cascade (N6 exact semantics) ─────────────────────────────────────


class TestProjectCnRemoveCascade:
    def test_default_refuses_when_pointed_at(self, tools, repo, project_id, seeded):
        result = tools["project_cn_remove"].invoke(
            {"project_id": project_id, "entry_id": seeded["new"].id}
        )
        assert "error" in result
        assert "cascade=true" in result["error"]
        assert seeded["old"].id in result["error"]  # pointing row named
        # Nothing was removed.
        assert repo.get_critical_note(project_id, seeded["new"].id) is not None

    def test_cascade_removes_target_and_reactivates_pointing_rows(
        self, tools, repo, project_id, seeded
    ):
        result = tools["project_cn_remove"].invoke(
            {"project_id": project_id, "entry_id": seeded["new"].id, "cascade": True}
        )
        assert result["removed"] is True
        assert result["cascade"] is True
        assert result["reactivated"] == [seeded["old"].id]
        # Target gone; pointing row back to ACTIVE.
        assert repo.get_critical_note(project_id, seeded["new"].id) is None
        old_row = repo.get_critical_note(project_id, seeded["old"].id)
        assert old_row.superseded_by_id is None

    def test_cascade_names_multiple_reactivated_rows(self, tools, repo, project_id, seeded):
        third = repo.add_critical_note(
            project_id, source_agent="l", category="decision", priority="high",
            summary="Also superseded by the new note",
        )
        repo.supersede_critical_note(project_id, third.id, seeded["new"].id)
        result = tools["project_cn_remove"].invoke(
            {"project_id": project_id, "entry_id": seeded["new"].id, "cascade": True}
        )
        assert sorted(result["reactivated"]) == sorted([seeded["old"].id, third.id])

    def test_plain_remove_unrelated_entry_unaffected(self, tools, repo, project_id, seeded):
        loner = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="medium",
            summary="Loner note with no lineage",
        )
        result = tools["project_cn_remove"].invoke(
            {"project_id": project_id, "entry_id": loner.id}
        )
        assert result["removed"] is True
        assert result["cascade"] is False
        assert result["reactivated"] == []


# ─── R20 collision scope ─────────────────────────────────────────────────────


class TestCollisionScopeExcludesSuperseded:
    def test_superseded_rows_invisible_to_scan(self, repo, project_id, seeded):
        # Re-adding the SUPERSEDED row's summary verbatim must NOT
        # collide — superseded rows are invisible to the scan (R20).
        old = repo.get_critical_note(project_id, seeded["old"].id)
        assert old.superseded_by_id is not None
        assert _find_near_duplicate_entry([old], old.summary) is None

    def test_active_rows_still_collide_byte_identical_semantics(self, seeded):
        assert (
            _find_near_duplicate_entry([seeded["new"]], "deploy via   launcher script Y ")
            is not None
        )

    def test_verbatim_readd_after_supersede_inserts_fresh(
        self, tools, repo, project_id, seeded
    ):
        add = tools["project_cn_add"]
        result = add.invoke({
            "project_id": project_id,
            "category": "decision",
            "priority": "high",
            "summary": "Deploy via launcher script X",  # == superseded row
        })
        assert "error" not in result
        assert result["id"] != seeded["old"].id
        assert result["superseded_by_id"] is None


# ─── Reference bound: dual enforcement + precedence ──────────────────────────


class TestReferenceBound:
    def test_write_reject_over_500_is_authoritative(self, tools, project_id):
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "convention",
            "priority": "high",
            "summary": "Reference bound probe",
            "reference": "x" * 501,
        })
        assert "error" in result
        assert "500" in result["error"]
        assert "detail_ref" in result["error"]  # points at the right fix

    def test_write_at_500_accepted(self, tools, repo, project_id):
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "convention",
            "priority": "high",
            "summary": "Reference bound edge probe",
            "reference": "x" * 500,
        })
        assert "error" not in result
        assert len(result["reference"]) == 500

    def test_detail_ref_accepted_unbounded_and_read_via_list(
        self, tools, repo, project_id
    ):
        big_detail = "d" * 5000
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "convention",
            "priority": "high",
            "summary": "Detail overflow probe",
            "detail_ref": big_detail,
        })
        assert "error" not in result
        assert result["detail_ref"] == big_detail
        listed = tools["project_cn_list"].invoke({"project_id": project_id})
        match = [e for e in listed["entries"] if e["id"] == result["id"]]
        assert match and match[0]["detail_ref"] == big_detail

    def test_reference_max_knob_is_installable(self, tools, project_id):
        install_critical_notes_config(reference_max=10)
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "convention",
            "priority": "high",
            "summary": "Tunable bound probe",
            "reference": "x" * 11,
        })
        assert "error" in result
        assert "10" in result["error"]


# ─── List surface: R19 maintenance marks + housekeeping ──────────────────────


class TestProjectCnListMarks:
    def test_superseded_strike_through_and_stale_mark_in_list_only(
        self, tools, repo, project_id, seeded
    ):
        # Make the active row ancient so it goes stale.
        ancient = repo.get_critical_note(project_id, seeded["new"].id)
        ancient_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
        ancient.last_reviewed_at = ancient_ts
        repo.engine  # engine alive; commit via a fresh session
        from sqlmodel import Session
        with Session(repo.engine) as session:
            row = session.get(type(ancient), ancient.id)
            row.last_reviewed_at = ancient_ts
            session.commit()

        listed = tools["project_cn_list"].invoke({"project_id": project_id})
        by_id = {e["id"]: e for e in listed["entries"]}

        old_entry = by_id[seeded["old"].id]
        assert old_entry["superseded"] is True
        assert old_entry["display_summary"].startswith("~~")
        assert f"✅ superseded by {seeded['new'].id}" in old_entry["display_summary"]
        # Superseded rows are never marked stale (staleness is for
        # ACTIVE rows — the row is already retired).
        assert old_entry["stale"] is False

        new_entry = by_id[seeded["new"].id]
        assert new_entry["stale"] is True
        assert "⚠️ last reviewed" in new_entry["display_summary"]
        assert new_entry["days_since_review"] >= 120

    def test_housekeeping_archive_candidates_proposal_only(
        self, tools, repo, project_id, seeded
    ):
        ancient_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        from sqlmodel import Session
        with Session(repo.engine) as session:
            row = session.get(type(seeded["new"]), seeded["new"].id)
            row.last_reviewed_at = ancient_ts
            session.commit()

        listed = tools["project_cn_list"].invoke({"project_id": project_id})
        hk = listed["housekeeping"]
        assert hk["proposal_only"] is True
        assert hk["stale_days"] == 90
        ids = [c["id"] for c in hk["archive_candidates"]]
        assert seeded["new"].id in ids
        assert seeded["old"].id not in ids  # superseded rows are not candidates
        # Never auto-applied: the candidate row still exists untouched.
        assert repo.get_critical_note(project_id, seeded["new"].id) is not None

    def test_list_order_stays_created_at_desc(self, tools, repo, project_id):
        first = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="medium",
            summary="Earliest note in the store",
        )
        second = repo.add_critical_note(
            project_id, source_agent="l", category="risk", priority="critical",
            summary="Later note in the store",
        )
        listed = tools["project_cn_list"].invoke({"project_id": project_id})
        ids = [e["id"] for e in listed["entries"]]
        # created_at DESC — the injected-block R19 ordering does NOT
        # leak into the tool surface.
        assert ids.index(second.id) < ids.index(first.id)


# ─── Pin suggestion (D2) ─────────────────────────────────────────────────────


class TestPinSuggestion:
    def test_critical_write_suggests_never_auto_pins(self, tools, repo, project_id):
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "risk",
            "priority": "critical",
            "summary": "Suggestion probe for critical writes",
        })
        assert "error" not in result
        assert result["pinned"] is False  # never auto-pinned
        assert "pin_suggestion" in result
        assert result["id"] in result["pin_suggestion"]

    def test_non_critical_write_carries_no_suggestion(self, tools, repo, project_id):
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "risk",
            "priority": "high",
            "summary": "Suggestion probe for high writes",
        })
        assert "error" not in result
        assert "pin_suggestion" not in result


# ─── Review-conditions: tz-naive guard, update-path suggestion, None-preserve ──


class TestReviewConditions:
    def test_days_since_accepts_naive_iso_string(self, tools, repo, project_id):
        """Regression: a parseable ISO string WITHOUT tz offset must NOT
        crash ``_days_since`` (the bug class crashes ``project_cn_list``
        entirely). The fix coerces naive→UTC inside ``_parse_iso_ts``;
        this test pins that path with a real row + a tz-less override
        of ``last_reviewed_at`` via a raw SQL UPDATE.
        """
        from sqlmodel import Session

        added = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "risk",
            "priority": "medium",
            "summary": "Naive-ts list-render probe",
        })
        assert "error" not in added

        # Overwrite last_reviewed_at with a tz-less ISO string. The
        # earlier defect was that ``datetime.fromisoformat("...")``
        # returns a naive datetime, and ``datetime.now(timezone.utc) -
        # naive_dt`` raises ``TypeError``. The guard coerces naive→UTC.
        from daemon.repositories.project.models import CriticalNoteModel
        naive_ts = (datetime.now(timezone.utc) - timedelta(days=30)).replace(
            tzinfo=None
        ).isoformat()
        with Session(repo.engine) as session:
            row = session.get(CriticalNoteModel, added["id"])
            row.last_reviewed_at = naive_ts
            session.commit()

        # Must NOT raise; the row renders cleanly without STALE (30d < 90d).
        listed = tools["project_cn_list"].invoke({"project_id": project_id})
        match = [e for e in listed["entries"] if e["id"] == added["id"]]
        assert match, "the added row should appear in the list"
        entry = match[0]
        assert entry["stale"] is False  # 30d < stale_days=90
        assert entry["days_since_review"] is not None
        assert 25 <= entry["days_since_review"] <= 35

    def test_detail_ref_none_on_update_preserves_existing(self, tools, repo, project_id):
        """One-line asymmetry pin: passing ``detail_ref=None`` on the
        update path means "not touched", not "cleared". The repo's
        ``_ALLOWED_UPDATES`` skip-when-None guard (``value is not None``
        at ``repository.py:1586``) implements this — pin behavior here.
        """
        original_detail = "long context block, never clear me"
        added = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "risk",
            "priority": "medium",
            "summary": "Detail-preserve probe",
            "detail_ref": original_detail,
        })
        assert "error" not in added
        assert added["detail_ref"] == original_detail

        # Update WITHOUT passing detail_ref (defaults to None → repo skip).
        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "entry_id": added["id"],
            "category": "risk",
            "priority": "medium",
            "summary": "Detail-preserve probe (updated)",
        })
        assert "error" not in result
        assert result["detail_ref"] == original_detail  # NOT cleared

    def test_pin_suggestion_fires_on_update_to_critical(self, tools, repo, project_id):
        """One-line pin-suggestion pin: a priority escalation to
        ``critical`` on the UPDATE path (not just the add path) must
        surface the suggestion field — curation is still the leader's
        explicit call; the suggestion is in-band nudging, never auto-pin.
        """
        added = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "category": "risk",
            "priority": "medium",
            "summary": "Escalate-to-critical probe",
        })
        assert "error" not in added
        assert "pin_suggestion" not in added  # medium → no suggestion on add

        result = tools["project_cn_add"].invoke({
            "project_id": project_id,
            "entry_id": added["id"],
            "category": "risk",
            "priority": "critical",  # escalate
            "summary": "Escalate-to-critical probe",
        })
        assert "error" not in result
        assert result["priority"] == "critical"
        assert "pin_suggestion" in result
        assert result["id"] in result["pin_suggestion"]
