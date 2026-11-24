"""POK LIBRARIAN pack (Wave 2 Phase 10) — the metrics reader, the
soft/smart max, the ruled-bases consolidator and the retrieval wiring.

Legs: the per-entry + per-store metric readers (T10.1); the
metrics-derived ceiling ``derived = max(floor, f(utilization))`` with
the COLD-START FLOOR (T10.2 — the highest-value leg: "no tightening
when ``active_count < floor``"); the I1 placement at
``project_metadata_records`` key ``pok.soft_max.v1`` + the fork pin
SOFT-MAX (T10.3 / T11.14); the H3 pass — registered UN_GATED, in
``JOB_MAINTENANCE_SECTIONS``, writing a ``maintenance_runs`` row, and
the fork pin PASS (T10.4 / T11.15 — the boot path executes NO pass);
the recompute + drift report (T10.5); the near-soft-max tightening
(T10.6); the ruled-bases consolidator (T10.7 — NEVER age, NEVER
``access_count`` alone); the J trusted-vs-ignored metric set + its 3
amendments (T10.8); the proactive consolidation trigger (T10.9); the
expired-ASK safety net that writes the ``ASK_EXPIRED`` audit row
(T10.10); the J partition wiring (T10.11 — a RAG twin is NEVER
redundancy); the R-RET detection leg (T10.12 — never a standalone
archive basis); and the retrieval-recorder wiring at BOTH live
``admit_view`` call sites (T10.13).

Every exercise runs on a scratch in-memory SQLite engine — no live
store, no network, no LLM.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.pok.models  # noqa: F401
from daemon.repositories.pok.models import (
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import POKRepository
from daemon.repositories.project.models import (
    Project,
    ProjectMetadataRecord,
    ProjectShortnameLink,
    ProjectTagLink,
)
from daemon.services import pok_librarian as librarian
from daemon.services.pok_librarian import (
    CONSOLIDATION_BASES,
    SOFT_MAX_METADATA_KEY,
    EntryMetrics,
    POKLibrarianJob,
    StoreMetrics,
    band_floor,
    classify_metrics,
    compute_drift,
    consolidation_basis_for,
    derive_soft_max,
    detect_r_ret_candidates,
    metric_triggers_consolidation,
    propose_consolidations,
    read_soft_max,
    read_store_metrics,
    run_librarian_pass,
    select_merge_target,
    surface_expired_asks,
    tightening_decision,
    write_soft_max,
)

PROJECT_ID = "proj-librarian"


@pytest.fixture()
def env():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(
        engine,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
            Project.__table__,
            ProjectMetadataRecord.__table__,
            ProjectTagLink.__table__,
            ProjectShortnameLink.__table__,
        ],
    )
    # The metadata writes need the project row to exist (the I1
    # placement lives under a project).
    _session(engine)
    return SimpleNamespace(engine=engine)


def _session(engine):
    from sqlmodel import Session

    with Session(engine) as session:
        session.add(
            Project(project_id=PROJECT_ID, name="librarian-project", status="active")
        )
        session.commit()


def _row(
    slug: str,
    *,
    body: str = "the body of the row",
    created_by: str = "worker",
    verify_count: int = 0,
    access_count: int = 0,
    pinned: bool = False,
    disputed: bool = False,
    source_tier: str = "agent",
    family_slug: str | None = None,
    content_sha256: str = "",
    last_verified: str | None = None,
) -> ProjectOperatingKnowledge:
    now = datetime.now(timezone.utc).isoformat()
    return ProjectOperatingKnowledge(
        id=f"id-{slug}",
        project_id=PROJECT_ID,
        slug=slug,
        domain="other",
        audience="both",
        body=body,
        provenance="observed",
        status="active",
        disputed=disputed,
        content_sha256=content_sha256 or f"sha-{slug}",
        created_by_agent=created_by,
        created_at=now,
        updated_at=now,
        last_verified=last_verified,
        verify_count=verify_count,
        access_count=access_count,
        pinned=pinned,
        source_tier=source_tier,
    )


def _seed(env, rows):
    from sqlmodel import Session

    with Session(env.engine) as session:
        for row in rows:
            session.add(row)
        session.commit()


def _ledger_row(
    *,
    phase: str,
    gate_id: str | None = None,
    entry_id: str | None = None,
    candidate_slug: str | None = None,
    verdict: str | None = None,
    source: str | None = None,
    target_slug: str | None = None,
    reason: str | None = None,
) -> POKWriteGateLedger:
    return POKWriteGateLedger(
        project_id=PROJECT_ID,
        gate_id=gate_id,
        phase=phase,
        entry_id=entry_id,
        candidate_slug=candidate_slug,
        verdict=verdict,
        source=source,
        target_slug=target_slug,
        reason=reason,
        executed=False,
        agent_id="tester",
    )


# ═══════════════════════════════════════════════════════════════════════════
# T10.1 — the metric readers
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricReader:
    def test_reads_the_per_entry_metrics(self, env):
        _seed(
            env,
            [
                _row(
                    "fam-01-one",
                    created_by="leader",
                    verify_count=2,
                    access_count=7,
                    pinned=True,
                    last_verified="2026-11-20T00:00:00+00:00",
                ),
                _row("fam-02-two", created_by="worker"),
            ],
        )
        store = read_store_metrics(POKRepository(env.engine), PROJECT_ID)
        assert store.active_count == 2
        first = store.entry("fam-01-one")
        assert first is not None
        assert first.verify_count == 2
        assert first.access_count == 7
        assert first.pinned is True
        assert first.family == "fam"
        assert first.created_by_agent == "leader"
        assert first.last_verified == "2026-11-20T00:00:00+00:00"
        assert first.never_retrieved is False  # it was accessed

    def test_reads_the_ledger_retrieval_counts_and_their_sources(self, env):
        _seed(env, [_row("fam-01-one")])
        repo = POKRepository(env.engine)
        repo.append_ledger(
            _ledger_row(phase="RETRIEVAL", entry_id="id-fam-01-one", source="injection")
        )
        repo.append_ledger(
            _ledger_row(phase="RETRIEVAL", entry_id="id-fam-01-one", source="injection")
        )
        repo.append_ledger(
            _ledger_row(phase="RETRIEVAL", entry_id="id-fam-01-one", source="query")
        )
        store = read_store_metrics(repo, PROJECT_ID)
        entry = store.entry("fam-01-one")
        assert entry.ledger_retrievals == 3
        assert entry.ledger_injections == 2
        assert entry.ledger_queries == 1
        assert store.ledger_retrievals == 3
        assert store.working_set == 1

    def test_reads_the_verdict_derived_counts(self, env):
        _seed(env, [_row("fam-01-one")])
        repo = POKRepository(env.engine)
        repo.append_ledger(
            _ledger_row(phase="ADMISSION", gate_id="g1", verdict="ACCEPT")
        )
        repo.append_ledger(
            _ledger_row(
                phase="ADMISSION",
                gate_id="g2",
                verdict="MERGE",
                target_slug="fam-01-one",
            )
        )
        store = read_store_metrics(repo, PROJECT_ID)
        assert store.verdicts["ACCEPT"] == 1
        assert store.verdicts["MERGE"] == 1
        assert store.entry("fam-01-one").merge_verdicts == 1

    def test_labels_the_never_retrieved_and_zero_verify_populations(self, env):
        _seed(
            env,
            [
                _row("fam-01-one"),
                _row("fam-02-two", verify_count=1, access_count=1),
            ],
        )
        store = read_store_metrics(POKRepository(env.engine), PROJECT_ID)
        assert store.never_retrieved == 1  # the row with no access and no ledger
        assert store.zero_verify == 1


# ═══════════════════════════════════════════════════════════════════════════
# T10.2 — the metrics-derived ceiling + the cold-start floor
# ═══════════════════════════════════════════════════════════════════════════


class TestSoftMaxDerivation:
    def test_the_value_is_derived_not_hand_set(self):
        """The ceiling MOVES with the measured working set."""
        cold, cold_inputs = derive_soft_max(active_count=499, working_set=0)
        warm, warm_inputs = derive_soft_max(active_count=499, working_set=431)
        assert cold != warm
        assert warm > cold
        assert cold_inputs["working_set"] == 0
        assert warm_inputs["working_set"] == 431
        assert cold_inputs["utilization_source"] == "cold-start"
        assert warm_inputs["utilization_source"] == "live-measurement"

    def test_the_floor_sits_at_or_above_the_current_active_band(self):
        assert band_floor(499) == 512
        assert band_floor(512) == 512
        assert band_floor(513) == 640
        value, inputs = derive_soft_max(active_count=499, working_set=0)
        assert value >= 499
        assert inputs["floor"] >= 499

    def test_no_tightening_when_the_active_count_is_below_the_floor(self):
        """THE highest-value leg (T10.2 / J cold-start policy)."""
        value, _ = derive_soft_max(active_count=499, working_set=0)
        decision = tightening_decision(active_count=499, soft_max=value)
        assert decision.tight is False
        assert decision.prefer_merge is False
        assert decision.extra_ask_rounds is False

    def test_tightening_engages_at_or_above_the_soft_max(self):
        decision = tightening_decision(active_count=512, soft_max=512)
        assert decision.tight is True
        assert decision.prefer_merge is True
        assert decision.extra_ask_rounds is True

    def test_the_derived_value_stays_below_the_hard_cap(self):
        value, _ = derive_soft_max(active_count=900, working_set=100000)
        assert value <= 1024

    def test_an_explicit_floor_never_lowers_the_derived_floor(self):
        _, inputs = derive_soft_max(active_count=499, working_set=0, floor=10)
        assert inputs["floor"] == 512


# ═══════════════════════════════════════════════════════════════════════════
# T10.3 / T11.14 — the I1 placement + the SOFT-MAX fork pin
# ═══════════════════════════════════════════════════════════════════════════


class TestSoftMaxPlacement:
    def test_the_value_lives_in_the_project_metadata(self, env):
        state = librarian.SoftMaxState(
            value=512,
            inputs={"active_count": 499},
            computed_at="2026-11-24T00:00:00+00:00",
            drift={"direction": "initial"},
        )
        assert write_soft_max(env.engine, PROJECT_ID, state) is True
        from daemon.repositories.project.repository import (
            SQLModelProjectRepository,
        )

        payload = SQLModelProjectRepository(env.engine).get_metadata(
            PROJECT_ID, SOFT_MAX_METADATA_KEY
        )
        assert payload["value"] == 512
        assert payload["inputs"] == {"active_count": 499}
        assert payload["computed_at"] == "2026-11-24T00:00:00+00:00"
        assert payload["drift"] == {"direction": "initial"}
        assert read_soft_max(env.engine, PROJECT_ID).value == 512

    def test_the_read_is_not_memoised(self, env):
        """A written value is visible to the NEXT read — no memo."""
        write_soft_max(
            env.engine,
            PROJECT_ID,
            librarian.SoftMaxState(value=512, inputs={}, computed_at="t", drift={}),
        )
        assert read_soft_max(env.engine, PROJECT_ID).value == 512
        write_soft_max(
            env.engine,
            PROJECT_ID,
            librarian.SoftMaxState(value=640, inputs={}, computed_at="t2", drift={}),
        )
        assert read_soft_max(env.engine, PROJECT_ID).value == 640

    def test_the_pok_store_never_carries_a_soft_max_row(self, env):
        """THE SOFT-MAX FORK PIN (T11.14) — the doctrine pin.

        A soft-max/meta row inside the POK store is DOCTRINE-WRONG: a
        count-that-changes is rolling state, which the store's own rule
        routes OUT of POK (it would steal an injection slot, burn a
        family slot and be self-gating).
        """
        _seed(env, [_row("fam-01-one")])
        repo = POKRepository(env.engine)
        # The PASS writes the soft max (the daemon-internal writer).
        run_librarian_pass(env.engine, PROJECT_ID)
        write_soft_max(
            env.engine,
            PROJECT_ID,
            librarian.SoftMaxState(value=512, inputs={}, computed_at="t", drift={}),
        )
        rows = repo.list_entries(PROJECT_ID, statuses=("active", "archived", "superseded"))
        for row in rows:
            identity = "%s %s" % (row.slug or "", row.body or "")
            identity = identity.lower().replace("_", "-").replace(".", "-")
            assert "soft-max" not in identity, row.slug
            assert SOFT_MAX_METADATA_KEY.lower() not in (row.slug or "").lower()
        # The metadata key lives ONLY in the project metadata table.
        from daemon.repositories.project.repository import (
            SQLModelProjectRepository,
        )

        project_repo = SQLModelProjectRepository(env.engine)
        keys = [
            record.meta_key
            for record in project_repo.list_metadata_records(
                _session_ctx(env), PROJECT_ID
            )
        ]
        assert keys == [SOFT_MAX_METADATA_KEY]


def _session_ctx(env):
    from sqlmodel import Session

    return Session(env.engine)


# ═══════════════════════════════════════════════════════════════════════════
# T10.4 / T11.15 — the H3 pass registration + the PASS fork pin
# ═══════════════════════════════════════════════════════════════════════════


class TestPassRegistration:
    def test_the_job_is_un_gated(self):
        from daemon.services.maintenance import (
            JOB_IDLE_POLICIES,
            IdlePolicy,
        )

        assert JOB_IDLE_POLICIES["pok_librarian"] == IdlePolicy.UN_GATED

    def test_the_job_is_in_the_maintenance_sections(self):
        from daemon.services.maintenance import JOB_MAINTENANCE_SECTIONS

        assert JOB_MAINTENANCE_SECTIONS["pok_librarian"] == "pok-librarian"

    def test_the_manager_registers_the_job_without_running_it(self):
        """THE PASS FORK PIN (T11.15) — the boot-SLO guard.

        The registration is a dict-append: the boot path must NOT
        execute a librarian pass (the Emperor-set 120 s boot budget).
        """
        import inspect

        import daemon.manager as manager_module

        source = inspect.getsource(manager_module)
        assert '"pok_librarian"' in source
        # The registration must NOT call the job's execute().
        registration = source.split('self._maintenance_service.register(\n            "pok_librarian"')
        assert len(registration) == 2
        block = registration[1][:400]
        assert "pok_librarian_job.execute" in block
        assert ".execute()" not in block.replace("pok_librarian_job.execute", "")
        assert "run_librarian_pass" not in source


# ═══════════════════════════════════════════════════════════════════════════
# T10.5 — recompute + drift report
# ═══════════════════════════════════════════════════════════════════════════


class TestDriftReport:
    def test_the_first_pass_reports_the_initial_drift(self):
        drift = compute_drift(None, 512, {"active_count": 499})
        assert drift["previous_value"] is None
        assert drift["direction"] == "initial"
        assert drift["changed"] is True

    def test_a_moved_value_reports_the_delta(self):
        previous = librarian.SoftMaxState(
            value=512, inputs={"active_count": 499}, computed_at="t0", drift={}
        )
        drift = compute_drift(previous, 862, {"active_count": 499, "working_set": 431})
        assert drift["previous_value"] == 512
        assert drift["delta"] == 350
        assert drift["direction"] == "up"
        assert drift["changed"] is True

    def test_a_flat_value_reports_no_movement(self):
        previous = librarian.SoftMaxState(
            value=512, inputs={}, computed_at="t0", drift={}
        )
        drift = compute_drift(previous, 512, {})
        assert drift["delta"] == 0
        assert drift["direction"] == "flat"
        assert drift["changed"] is False


# ═══════════════════════════════════════════════════════════════════════════
# T10.7 / T10.8 / T10.11 / T10.12 — the metric set + the consolidator
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricSet:
    def test_access_count_alone_never_triggers_a_consolidation(self):
        """THE MUTATION LEG (T10.8): access_count-alone ⇒ RED."""
        entry = EntryMetrics(
            id="id-1",
            slug="fam-01-one",
            access_count=42,
            content_sha256="sha-1",
        )
        signals = classify_metrics(entry)
        assert metric_triggers_consolidation(signals) is False
        assert consolidation_basis_for(entry, signals) is None

    def test_age_alone_never_triggers_a_consolidation(self):
        entry = EntryMetrics(
            id="id-1",
            slug="fam-01-one",
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        signals = classify_metrics(entry)
        assert metric_triggers_consolidation(signals) is False

    def test_a_gate_stamped_verification_is_trusted(self):
        entry = EntryMetrics(id="id-1", slug="fam-01-one", verify_count=1)
        signals = classify_metrics(entry)
        assert metric_triggers_consolidation(signals) is True
        assert any(s.kind == "verify_count" and s.trusted for s in signals)

    def test_the_curator_tier_is_trusted(self):
        entry = EntryMetrics(id="id-1", slug="fam-01-one", pinned=True)
        signals = classify_metrics(entry)
        assert any(s.kind == "curator_tier" and s.trusted for s in signals)

    def test_timestamps_corroborate_but_never_trigger(self):
        entry = EntryMetrics(
            id="id-1",
            slug="fam-01-one",
            last_verified="2026-01-01T00:00:00+00:00",
        )
        signals = classify_metrics(entry)
        timestamp = [s for s in signals if s.kind == "timestamp"]
        assert timestamp and all(s.corroborates_only for s in timestamp)
        assert metric_triggers_consolidation(signals) is False

    def test_the_ledger_retrieval_supersedes_the_access_count(self):
        """J amendment 1 — the ledger is the trusted surface."""
        entry = EntryMetrics(
            id="id-1", slug="fam-01-one", ledger_retrievals=1, access_count=0
        )
        signals = classify_metrics(entry)
        assert any(s.kind == "ledger_retrieval" and s.trusted for s in signals)
        assert metric_triggers_consolidation(signals) is True


class TestConsolidationBases:
    def test_only_the_ruled_bases_are_offered(self):
        assert set(CONSOLIDATION_BASES) == {
            "covered-by-surviving-row",
            "relocated-to-routed-home",
            "NOW-FALSE",
        }
        assert "gate-rejection" not in CONSOLIDATION_BASES

    def test_a_surviving_row_covers_the_fact(self):
        entry = EntryMetrics(
            id="id-1", slug="bad-01-slug", content_sha256="sha-x", verify_count=1
        )
        survivor = EntryMetrics(
            id="id-2", slug="fam-01-one", content_sha256="sha-x", verify_count=1
        )
        signals = classify_metrics(entry)
        basis = consolidation_basis_for(entry, signals, survivors=[survivor])
        assert basis == "covered-by-surviving-row"

    def test_a_curator_dispute_is_a_now_false_basis(self):
        entry = EntryMetrics(id="id-1", slug="fam-01-one", disputed=True, verify_count=1)
        signals = classify_metrics(entry)
        assert consolidation_basis_for(entry, signals) == "NOW-FALSE"

    def test_a_zero_access_boundary_row_is_surfaced_for_relocation(self):
        entry = EntryMetrics(id="id-1", slug="fam-01-one", verify_count=1)
        signals = classify_metrics(entry)
        basis = consolidation_basis_for(
            entry, signals, boundary_elsewhere=True
        )
        assert basis == "relocated-to-routed-home"

    def test_a_boundary_row_that_was_retrieved_is_not_surfaced(self):
        entry = EntryMetrics(
            id="id-1", slug="fam-01-one", verify_count=1, ledger_retrievals=2
        )
        signals = classify_metrics(entry)
        assert (
            consolidation_basis_for(entry, signals, boundary_elsewhere=True) is None
        )

    def test_a_row_with_no_trusted_signal_yields_no_basis(self):
        entry = EntryMetrics(id="id-1", slug="fam-01-one")
        signals = classify_metrics(entry)
        assert consolidation_basis_for(entry, signals) is None


class TestMergeTargetSelection:
    def _entry(self, slug, **kwargs):
        base = dict(id=slug, slug=slug, family="fam")
        base.update(kwargs)
        return EntryMetrics(**base)

    def test_the_verified_row_wins(self):
        target = select_merge_target(
            [
                self._entry("fam-01-a", verify_count=0),
                self._entry("fam-02-b", verify_count=3),
            ]
        )
        assert target.slug == "fam-02-b"

    def test_the_curated_row_wins_on_a_tie(self):
        target = select_merge_target(
            [
                self._entry("fam-01-a", verify_count=1),
                self._entry("fam-02-b", verify_count=1, source_tier="curated"),
            ]
        )
        assert target.slug == "fam-02-b"

    def test_the_family_match_wins_when_the_trusted_signals_tie(self):
        target = select_merge_target(
            [
                self._entry("other-01-a", verify_count=1, family="other"),
                self._entry("fam-02-b", verify_count=1, family="fam"),
            ],
            family="fam",
        )
        assert target.slug == "fam-02-b"

    def test_the_access_count_never_decides_alone(self):
        target = select_merge_target(
            [
                self._entry("fam-01-a", verify_count=0, access_count=999),
                self._entry("fam-02-b", verify_count=1, access_count=0),
            ]
        )
        assert target.slug == "fam-02-b"


class TestRRetDetection:
    def test_a_mis_slugged_duplicate_proposes_a_merge_under_a_ruled_basis(self):
        store = StoreMetrics(
            project_id=PROJECT_ID,
            active_count=2,
            entries=(
                EntryMetrics(
                    id="id-1",
                    slug="fam-01-one",
                    family="fam",
                    content_sha256="sha-x",
                    verify_count=1,
                ),
                EntryMetrics(
                    id="id-2",
                    slug="loose-slug",
                    family=None,
                    content_sha256="sha-x",
                    verify_count=1,
                ),
            ),
        )
        candidates = detect_r_ret_candidates(store)
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.kind == "merge"
        assert candidate.target_slug == "fam-01-one"
        assert candidate.basis == "covered-by-surviving-row"

    def test_a_bad_slug_on_unique_content_proposes_a_re_slug(self):
        store = StoreMetrics(
            project_id=PROJECT_ID,
            active_count=1,
            entries=(
                EntryMetrics(
                    id="id-1",
                    slug="loose-slug",
                    family=None,
                    content_sha256="sha-x",
                    verify_count=1,
                ),
            ),
        )
        candidates = detect_r_ret_candidates(store)
        assert len(candidates) == 1
        assert candidates[0].kind == "reslug"
        # NEVER a standalone archive basis.
        assert candidates[0].basis is None

    def test_r_ret_alone_never_archives(self):
        """THE MUTATION LEG (T10.12): R-RET alone ⇒ RED."""
        entry = EntryMetrics(
            id="id-1",
            slug="loose-slug",
            family=None,
            content_sha256="sha-x",
            verify_count=1,
            ledger_retrievals=0,
            access_count=0,
        )
        signals = classify_metrics(entry)
        # The never-retrieved signal alone never carries a basis.
        basis = consolidation_basis_for(entry, signals)
        assert basis is None


# ═══════════════════════════════════════════════════════════════════════════
# T10.10 — the expired-ASK safety net
# ═══════════════════════════════════════════════════════════════════════════


class TestExpiredAskSurfacing:
    def test_an_unanswered_gate_gets_an_ask_expired_row(self, env):
        repo = POKRepository(env.engine)
        repo.append_ledger(
            _ledger_row(phase="ASK_MINTED", gate_id="g-ask-1", candidate_slug="fam-01-x")
        )
        appended = surface_expired_asks(repo, PROJECT_ID)
        assert len(appended) == 1
        assert appended[0].phase == "ASK_EXPIRED"
        assert appended[0].gate_id == "g-ask-1"
        assert appended[0].executed is False

    def test_an_answered_gate_is_not_expired(self, env):
        repo = POKRepository(env.engine)
        repo.append_ledger(
            _ledger_row(phase="ASK_MINTED", gate_id="g-ask-2", candidate_slug="fam-01-x")
        )
        repo.append_ledger(
            _ledger_row(phase="ASK_ANSWERED", gate_id="g-ask-2", verdict="ASK")
        )
        assert surface_expired_asks(repo, PROJECT_ID) == []

    def test_the_expiry_is_idempotent(self, env):
        repo = POKRepository(env.engine)
        repo.append_ledger(
            _ledger_row(phase="ASK_MINTED", gate_id="g-ask-3", candidate_slug="fam-01-x")
        )
        assert len(surface_expired_asks(repo, PROJECT_ID)) == 1
        assert surface_expired_asks(repo, PROJECT_ID) == []


# ═══════════════════════════════════════════════════════════════════════════
# T10.4/T10.5/T10.9 — the pass itself
# ═══════════════════════════════════════════════════════════════════════════


class TestLibrarianPass:
    def test_the_pass_writes_the_soft_max_and_reports_the_drift(self, env):
        _seed(env, [_row("fam-01-one")])
        report = run_librarian_pass(env.engine, PROJECT_ID)
        assert report.error is None
        assert report.soft_max >= 128  # the band floor over one row
        assert report.previous_soft_max is None
        assert report.drift["direction"] == "initial"
        state = read_soft_max(env.engine, PROJECT_ID)
        assert state is not None
        assert state.value == report.soft_max

    def test_the_second_pass_reports_the_drift(self, env):
        _seed(env, [_row("fam-01-one")])
        run_librarian_pass(env.engine, PROJECT_ID)
        second = run_librarian_pass(env.engine, PROJECT_ID)
        assert second.previous_soft_max is not None
        assert second.drift["direction"] == "flat"

    def test_the_pass_never_raises(self, env):
        report = run_librarian_pass(env.engine, "a-project-without-rows")
        assert report.error is None
        assert report.active_count == 0

    def test_the_pass_stays_dormant_below_the_floor(self, env):
        """F3 — the PASS-LEVEL dormancy proof (the two-legged gap).

        The derive leg and the decision leg are pinned on their own
        (``test_no_tightening_when_the_active_count_is_below_the_floor``
        + ``test_tightening_engages_at_or_above_the_soft_max``). This
        is the END-TO-END leg: the store carries rows that WOULD
        qualify for a consolidation (a covered-by-surviving-row
        duplicate), yet the pass proposes NOTHING and writes NO
        consolidation — because the store is BELOW its floor
        (499 active < the 512 band floor).

        The CONTROL leg proves the rows really qualify, so a vacuous
        pass (one that never proposes anything) cannot pass this test.
        """
        rows = [
            _row(f"fam-{i:03d}-row") for i in range(497)
        ]
        # The duplicated pair: the same content under two slugs, both
        # gate-verified (a trusted signal) ⇒ a covered-by-surviving-row
        # consolidation CANDIDATE.
        rows.append(
            _row("fam-497-row", content_sha256="sha-duplicate", verify_count=1)
        )
        rows.append(
            _row("fam-498-row", content_sha256="sha-duplicate", verify_count=1)
        )
        _seed(env, rows)

        store = read_store_metrics(POKRepository(env.engine), PROJECT_ID)
        assert store.active_count == 499
        # THE CONTROL: the consolidator WOULD propose (the rows qualify).
        assert propose_consolidations(store), (
            "the control leg: the duplicated pair must qualify — else "
            "this test would pass vacuously"
        )

        report = run_librarian_pass(env.engine, PROJECT_ID)
        assert report.error is None
        assert report.active_count == 499
        assert report.soft_max == 512  # the cold-start band floor
        assert report.tightening["tight"] is False
        # THE PASS-LEVEL GATE: nothing is proposed below the floor.
        assert report.proposals == ()
        # And nothing was consolidated: no ledger row carries a basis.
        ledger = POKRepository(env.engine).query_ledger(PROJECT_ID, limit=None)
        assert [row for row in ledger if row.basis] == []

    def test_the_pass_does_not_write_pass_summaries_into_the_ledger(self, env):
        """I1's prohibition: the pass summary NEVER lives in the ledger."""
        _seed(env, [_row("fam-01-one")])
        run_librarian_pass(env.engine, PROJECT_ID)
        rows = POKRepository(env.engine).query_ledger(PROJECT_ID, limit=None)
        for row in rows:
            assert row.phase not in {"SOFT_MAX", "PASS", "SOFTMAX"}

    def test_the_job_walks_the_projects_and_writes_an_audit_row(self, env):
        from daemon.repositories.maintenance_runs.models import MaintenanceRun
        from daemon.repositories.maintenance_runs.repository import (
            MaintenanceRunsRepository,
        )

        SQLModel.metadata.create_all(
            env.engine, tables=[MaintenanceRun.__table__]
        )
        runs_repo = MaintenanceRunsRepository(env.engine)
        job = POKLibrarianJob(engine=env.engine, runs_repo=runs_repo)
        import asyncio

        reports = asyncio.run(job.execute())
        assert isinstance(reports, list)
        runs = runs_repo.list_all()
        assert len(runs) == 1
        assert runs[0].section == "pok-librarian"
        assert runs[0].status == "succeeded"


# ═══════════════════════════════════════════════════════════════════════════
# T10.13 — the retrieval recorder is wired at BOTH live call sites
# ═══════════════════════════════════════════════════════════════════════════


class TestRetrievalRecorderWiring:
    def test_the_context_seam_passes_the_recorder(self):
        import inspect

        from daemon.services import context_messages

        source = inspect.getsource(context_messages)
        assert "make_retrieval_recorder(" in source
        assert 'source="injection"' in source

    def test_the_ari_seam_passes_the_recorder(self):
        import inspect

        from daemon.services import pok_ari_seam

        source = inspect.getsource(pok_ari_seam)
        assert "make_retrieval_recorder(" in source
        assert 'source="injection"' in source

    def test_the_j_metric_read_returns_data_when_the_recorder_is_wired(self, env):
        """THE MUTATION LEG (T10.13): an unwired recorder ⇒ RED.

        The J metric chain (the ledger's retrieval counts) reads
        NOTHING when the recorder is not wired — the librarian would
        be blind, and the soft max would never lift.
        """
        from daemon.services.pok_injection import make_retrieval_recorder

        _seed(env, [_row("fam-01-one")])
        repo = POKRepository(env.engine)
        recorder = make_retrieval_recorder(
            repo, PROJECT_ID, agent_id="worker", source="injection"
        )

        class _Entry:
            def __init__(self, row):
                self.row = row

        recorder([_Entry(repo.list_entries(PROJECT_ID, statuses=("active",))[0])])
        store = read_store_metrics(repo, PROJECT_ID)
        assert store.ledger_retrievals == 1
        assert store.working_set == 1
        # And the metric chain is NOT blind: the derived value can lift.
        value, inputs = derive_soft_max(
            active_count=store.active_count, working_set=store.working_set
        )
        assert inputs["utilization_source"] == "live-measurement"
        assert value > 0


# ═══════════════════════════════════════════════════════════════════════════
# the store's own vocabulary (the census surface reads the same metrics)
# ═══════════════════════════════════════════════════════════════════════════


class TestCensusVocabulary:
    def test_the_census_payload_is_json_serialisable(self, env):
        _seed(env, [_row("fam-01-one")])
        store = read_store_metrics(POKRepository(env.engine), PROJECT_ID)
        payload = {
            "active_count": store.active_count,
            "by_family": dict(sorted(store.by_family.items())),
            "by_author": dict(sorted(store.by_author.items())),
            "verdicts": dict(sorted(store.verdicts.items())),
        }
        assert json.loads(json.dumps(payload))["active_count"] == 1
