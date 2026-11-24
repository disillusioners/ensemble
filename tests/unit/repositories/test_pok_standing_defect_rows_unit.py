"""Standing-defect RESIDUAL rows in the write-gate ledger (Wave 1, T5.6).

Covers pok-write-gate Phase 5 T5.6 — "standing-defect residual rows in
ledger". The two standing defects of the POK write-gate subsystem and
their DISPOSITIONS (design-amendments-v2 §6, §9):

* ``record_verification`` → ``wired-via-gate-path`` — the metric IS
  wired: the gate path stamps it (the tool's no-delta MERGE verdict →
  ``_stamp_target_verification`` → ``POKRepository.record_verification``).
  addendum-3 item 7 REVERSED the retire-with-disclosure ruling. NOT
  retired.
* ``vector-leg`` → ``retired-with-disclosure`` — the embeddings
  companion is dead (0 embedding rows at the 2026-11-23 census, the
  mint hook has zero callers, the query seam never passes a
  ``query_embedding``). Documented, not wired.

Fence discipline: throwaway in-memory SQLite engine only. No daemon
boot, no InstanceManager, no live store, no network, no PostgreSQL.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.pok.models import (
    POK_LEDGER_PHASES,
    POK_LEDGER_VERDICTS,
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import (
    POK_STANDING_DEFECT_GATE_PREFIX,
    POK_STANDING_DEFECTS,
    POKRepository,
)

PROJECT_ID = "proj-standing-defects"

#: The two dispositions the plan fixes (T5.6) — the test pins the
#: TOKENS, so a silent re-wording of a disposition fails the pin.
EXPECTED_DISPOSITIONS: dict[str, str] = {
    "record_verification": "wired-via-gate-path",
    "vector-leg": "retired-with-disclosure",
}


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        eng,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
        ],
    )
    return eng


@pytest.fixture()
def repo(engine) -> POKRepository:
    return POKRepository(engine)


class TestTheStandingDefectVocabulary:
    def test_the_two_defects_and_their_dispositions_are_fixed(self) -> None:
        assert dict(
            (defect, disposition) for defect, disposition, _ in POK_STANDING_DEFECTS
        ) == EXPECTED_DISPOSITIONS, (
            "the standing-defect dispositions are PLAN-FIXED — "
            "record_verification is reserved for the gate path (NOT "
            "retired) and the vector leg is retired with disclosure"
        )

    def test_every_defect_carries_a_disclosure(self) -> None:
        for defect, _disposition, disclosure in POK_STANDING_DEFECTS:
            assert disclosure.strip(), (
                f"{defect} must carry a disclosure — a disposition "
                "without its disclosure is a silent drop"
            )

    def test_the_gate_id_prefix_marks_a_disposition_row(self) -> None:
        assert POK_STANDING_DEFECT_GATE_PREFIX == "standing-defect:"


class TestRecordingTheResidualRows:
    def test_both_dispositions_are_recorded(self, repo) -> None:
        appended = repo.record_standing_defects(PROJECT_ID)
        assert len(appended) == 2, (
            f"expected one residual row per standing defect, got {len(appended)}"
        )
        by_defect = {
            row.gate_id.removeprefix(POK_STANDING_DEFECT_GATE_PREFIX): row
            for row in appended
        }
        assert set(by_defect) == set(EXPECTED_DISPOSITIONS)
        for defect, disposition in EXPECTED_DISPOSITIONS.items():
            assert by_defect[defect].reason == disposition, (
                f"{defect} must carry its disposition token as the claim"
            )

    def test_the_rows_land_in_the_landed_ledger_shape(self, repo) -> None:
        """NO schema fork — the rows use the landed vocabulary:
        ``phase`` ∈ the closed phase set, ``verdict`` NULL (a
        disposition is not a gate outcome), ``basis`` NULL (nothing was
        archived), ``executed`` False (no knowledge row was written)."""
        appended = repo.record_standing_defects(PROJECT_ID)
        for row in appended:
            assert row.phase in POK_LEDGER_PHASES
            assert row.phase == "ADMISSION"
            assert row.verdict is None, (
                "a disposition is NOT a gate verdict — the four verdicts "
                f"are reserved for admission decisions (got {row.verdict!r})"
            )
            assert row.verdict is None or row.verdict in POK_LEDGER_VERDICTS
            assert row.basis is None, (
                "nothing was ARCHIVED — a disposition archives nothing"
            )
            assert row.executed is False
            assert row.gate_id.startswith(POK_STANDING_DEFECT_GATE_PREFIX)
            assert row.entry_id is None
            assert row.source is None, "a disposition is not a retrieval"

    def test_the_disclosure_is_carried_in_the_explanation(self, repo) -> None:
        appended = repo.record_standing_defects(PROJECT_ID)
        for row in appended:
            assert row.judge_explanation and row.judge_explanation.strip()

    def test_the_rows_are_readable_through_the_ledger_surface(
        self, repo
    ) -> None:
        repo.record_standing_defects(PROJECT_ID)
        # The LEDGER ALONE answers it — no cross-store join.
        rows = repo.query_ledger(PROJECT_ID, limit=None)
        assert len(rows) == 2
        residuals = repo.standing_defect_rows(PROJECT_ID)
        assert len(residuals) == 2
        assert {r.reason for r in residuals} == set(
            EXPECTED_DISPOSITIONS.values()
        )

    def test_recording_is_idempotent_per_defect(self, repo) -> None:
        """The ledger is APPEND-ONLY — a blind re-call would duplicate
        the audit trail, so a defect that already has a row is
        skipped."""
        first = repo.record_standing_defects(PROJECT_ID)
        second = repo.record_standing_defects(PROJECT_ID)
        assert len(first) == 2
        assert second == [], (
            "the second call must append nothing — the residual rows "
            "are already recorded"
        )
        assert len(repo.query_ledger(PROJECT_ID, limit=None)) == 2

    def test_other_projects_are_untouched(self, repo) -> None:
        repo.record_standing_defects(PROJECT_ID)
        assert repo.standing_defect_rows("proj-other") == []
        assert repo.query_ledger("proj-other", limit=None) == []


class TestTheResidualRowsAgainstTheLibrarianCounts:
    """T5.6 × the Phase-10 metric read — a disposition is NOT an
    admission decision, and the librarian must be able to TELL.

    The ``phase`` column carries a CLOSED vocabulary (the migration's
    CHECK constraint), so a residual disposition cannot invent its own
    chain-step token: it rides ``ADMISSION`` with a NULL verdict. That
    makes it invisible to a naive phase count — so the isolation
    surface is pinned here, or the librarian would read two
    dispositions as two admissions (the Phase-10 NOTE in
    ``count_ledger_phase`` says so in prose; this pins it in code).
    """

    def test_the_admission_phase_count_includes_the_dispositions(
        self, repo
    ) -> None:
        repo.record_standing_defects(PROJECT_ID)
        assert repo.count_ledger_phase(PROJECT_ID, "ADMISSION") == 2, (
            "the residual rows ARE ADMISSION-phase rows — the phase "
            "vocabulary is closed, so a disposition cannot carry its "
            "own token"
        )

    def test_a_verdict_bearing_admission_is_not_a_disposition(
        self, repo
    ) -> None:
        repo.append_ledger(
            POKWriteGateLedger(
                project_id=PROJECT_ID,
                gate_id="gate-abc",
                phase="ADMISSION",
                verdict="ACCEPT",
                reason="the operational claim",
                executed=False,
            )
        )
        repo.record_standing_defects(PROJECT_ID)
        residuals = repo.standing_defect_rows(PROJECT_ID)
        assert len(residuals) == 2, (
            "the verdict-bearing admission row is NOT a disposition — "
            "the isolation surface must not swallow it"
        )
        assert all(row.verdict is None for row in residuals)
        assert repo.count_ledger_phase(PROJECT_ID, "ADMISSION") == 3

    def test_the_librarian_can_subtract_the_dispositions(self, repo) -> None:
        """The Phase-10 read, expressed: ADMISSIONS = phase count −
        dispositions. This is the ONLY supported way to read the
        admission count, and the subtraction must be exact."""
        repo.append_ledger(
            POKWriteGateLedger(
                project_id=PROJECT_ID,
                gate_id="gate-abc",
                phase="ADMISSION",
                verdict="ACCEPT",
                reason="the operational claim",
                executed=False,
            )
        )
        repo.record_standing_defects(PROJECT_ID)
        admissions = (
            repo.count_ledger_phase(PROJECT_ID, "ADMISSION")
            - len(repo.standing_defect_rows(PROJECT_ID))
        )
        assert admissions == 1, (
            "after subtracting the dispositions the admission count "
            "must be the real admission count"
        )
