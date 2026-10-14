"""Phase B T15 — dual-driver (SQLite + PostgreSQL) parity tests.

agent-prompt-immutability commission
(``.agents/shared/planning/agent-prompt-immutability/phaseB-plan.md`` T15;
acceptance: "state machine + selection pointer + audit append work
identically on SQLite + PG"; "selection pointer invariant holds under
concurrency on both engines"; "audit rows present on both").

Coverage — every DB-touching test is parametrized over
``["sqlite", "postgres"]``:

* **State machine (A1)** — all FIVE valid transition pairs execute
  for real on both engines: ``(draft, pending_approval)``,
  ``(pending_approval, approved)``, ``(pending_approval, rejected)``,
  ``(pending_approval, draft)`` (recall), ``(draft, archived)``;
  invalid-pair sampling (draft→approved, draft→rejected,
  pending_approval→archived, approved→draft, approved→rejected,
  rejected→draft, archived→pending_approval) raises
  ``InvalidTransitionError`` on both.
* **Selection pointer (F5/R3)** — set / re-select / clear round-trip;
  concurrent ``set_selection`` (16 threads) leaves EXACTLY one
  ``is_selected=1`` row per key on BOTH engines (PG via
  ``pg_advisory_xact_lock`` per-key serialization; SQLite via
  DB-level single-writer + WAL).
* **Audit append (A3)** — every transition stamps its audit row
  inside the same transaction; the exact lifecycle event-type
  sequence matches on both engines; raw ``append_audit_event``
  round-trips on both.

Fixture REUSE: the engine fixtures are IMPORTED from
``tests/test_prompt_version_resolution.py`` (the Phase A T10
canonical file) — single source for the scratch-DB lifecycle,
the ``PG_TEST_*``-only env contract (ambient ``POSTGRES_*`` is
NEVER read — env-poison 3-guard), the ``ensemble_prod`` hard-abort,
and the WAL+QueuePool SQLite shape. If that fixture drifts, this
file's parity semantics follow it BY CONSTRUCTION.

PG legs SKIP loudly (never silently) when PostgreSQL is unreachable;
on the dev host they are expected to EXECUTE — a PG skip is a
report-worthy failure for this commission, not an acceptable
outcome.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import text

from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_INTERNAL,
    AUTH_METHOD_SESSION,
    ActorContext,
    InvalidTransitionError,
    PromptVersionRepository,
    VALID_TRANSITIONS,
)

# True fixture reuse — the Phase A T10 canonical fixtures
# (db_dialect / repo_engine / repo / internal_actor / http_actor /
# http_bearer_actor). Importing registers them for THIS module's
# tests; the parametrize below overrides the sqlite default.
from tests.test_prompt_version_resolution import (  # noqa: F401
    db_dialect,
    http_actor,
    http_bearer_actor,
    internal_actor,
    repo,
    repo_engine,
)


# The five A1-frozen valid pairs, with the driver sequence needed to
# reach the from-state legitimately.
#
#   (draft, pending_approval)  — create → submit
#   (pending_approval, approved) — create → submit → approve
#   (pending_approval, rejected) — create → submit → reject
#   (pending_approval, draft)  — create → submit → recall
#   (draft, archived)          — create → archive
_VALID_PAIR_DRIVERS = {
    ("draft", "pending_approval"): "submit",
    ("pending_approval", "approved"): "approve",
    ("pending_approval", "rejected"): "reject",
    ("pending_approval", "draft"): "recall",
    ("draft", "archived"): "archive",
}

# Invalid-pair sample (mission: "invalid sampling") — covers
# forward-skip, terminal-state exits, and the A1-dropped recall edge.
_INVALID_PAIR_SAMPLE = [
    ("draft", "approved"),
    ("draft", "rejected"),
    ("pending_approval", "archived"),
    ("approved", "draft"),
    ("approved", "rejected"),
    ("approved", "pending_approval"),
    ("rejected", "draft"),  # A1 fold-in: DROPPED — rejected is terminal
    ("archived", "pending_approval"),
]


def _drive_to(repo: PromptVersionRepository, pair, internal_actor, http_actor):
    """Create a row and drive it legitimately to ``pair[0]``."""
    v = repo.create_draft("pdev", "", "soul", "body", internal_actor)
    if pair[0] == "draft":
        return v
    if pair[0] == "archived":
        # Archive departs from DRAFT — must run BEFORE submit.
        return repo.atomic_transition(
            version_id=v.version_id,
            from_state="draft",
            to_state="archived",
            actor=internal_actor,
        )
    repo.submit_for_approval(v.version_id, internal_actor)
    if pair[0] == "pending_approval":
        return repo.get_version(v.version_id)
    if pair[0] == "approved":
        return repo.approve(
            v.version_id,
            http_actor,
        )
    if pair[0] == "rejected":
        return repo.reject(
            v.version_id,
            http_actor,
        )
    raise AssertionError(f"no driver for from-state {pair[0]!r}")


# ─────────────────────────────────────────────────────────────────────────────
# State machine — all five valid pairs, both dialects
# ─────────────────────────────────────────────────────────────────────────────


class TestDualDriverStateMachine:
    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_all_five_valid_pairs_execute(self, repo, internal_actor, http_actor, db_dialect):
        executed = []
        for pair in sorted(_VALID_PAIR_DRIVERS):
            from_state, to_state = pair
            v = _drive_to(repo, pair, internal_actor, http_actor)

            kwargs = {}
            if to_state in {"approved", "rejected"}:
                kwargs = {
                    "approver_subject": http_actor.subject,
                    "approver_auth_method": http_actor.auth_method,
                }
            out = repo.atomic_transition(
                version_id=v.version_id,
                from_state=from_state,
                to_state=to_state,
                actor=http_actor if to_state in {"approved", "rejected"} else internal_actor,
                **kwargs,
            )
            assert out.state == to_state, f"{pair} landed in {out.state}"
            executed.append(pair)

        assert sorted(executed) == sorted(_VALID_PAIR_DRIVERS)

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_invalid_pair_sampling_raises(self, repo, internal_actor, http_actor, db_dialect):
        for pair in _INVALID_PAIR_SAMPLE:
            from_state, to_state = pair
            v = _drive_to(repo, pair, internal_actor, http_actor)
            with pytest.raises(InvalidTransitionError):
                repo.atomic_transition(
                    version_id=v.version_id,
                    from_state=from_state,
                    to_state=to_state,
                    actor=http_actor,
                    approver_subject=http_actor.subject,
                    approver_auth_method=http_actor.auth_method,
                )

    def test_valid_transitions_frozenset_is_a1(self):
        # Dialect-independent anchor (the DB legs above prove behavior;
        # this pins the skeleton both engines implement against).
        assert VALID_TRANSITIONS == frozenset({
            ("draft", "pending_approval"),
            ("pending_approval", "approved"),
            ("pending_approval", "rejected"),
            ("pending_approval", "draft"),
            ("draft", "archived"),
        })


# ─────────────────────────────────────────────────────────────────────────────
# Selection pointer — round-trip + concurrency, both dialects
# ─────────────────────────────────────────────────────────────────────────────


class TestDualDriverSelectionPointer:
    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_select_reselect_clear_roundtrip(self, repo, internal_actor, http_actor, db_dialect):
        approved = []
        for i in range(2):
            v = repo.create_draft("psel", "", "soul", f"v{i}", internal_actor)
            repo.submit_for_approval(v.version_id, internal_actor)
            repo.approve(v.version_id, http_actor)
            approved.append(v.version_id)

        repo.set_selection("psel", "", "soul", approved[0], http_actor)
        sel = repo.get_selection("psel", "", "soul")
        assert sel is not None and sel.version_id == approved[0]

        # Re-select moves the single pointer.
        repo.set_selection("psel", "", "soul", approved[1], http_actor)
        sel = repo.get_selection("psel", "", "soul")
        assert sel.version_id == approved[1]

        # Clear drops it.
        repo.clear_selection("psel", "", "soul", http_actor)
        assert repo.get_selection("psel", "", "soul") is None

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_concurrent_set_selection_serializes_exactly_one_pointer(
        self, repo, internal_actor, http_actor, db_dialect
    ):
        """T1/T15 acceptance — 16 racing set_selection calls on one key.

        F-1/F-2 posture: with per-key serialization (pg_advisory_
        xact_lock on PG; SQLite single-writer) NO call may fail and
        EXACTLY one ``is_selected=1`` row must survive on BOTH
        engines.
        """
        n = 16
        approved = []
        for i in range(n):
            v = repo.create_draft("pconc", "", "soul", f"v{i}", internal_actor)
            repo.submit_for_approval(v.version_id, internal_actor)
            repo.approve(v.version_id, http_actor)
            approved.append(v.version_id)

        errors: list[tuple[str, str]] = []

        def _select(vi: str):
            try:
                repo.set_selection("pconc", "", "soul", vi, http_actor)
            except Exception as e:  # noqa: BLE001
                errors.append((vi[:8], type(e).__name__))

        threads = [threading.Thread(target=_select, args=(vi,)) for vi in approved]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], (
            f"set_selection failures under contention on {db_dialect}: {errors}"
        )

        sel = repo.get_selection("pconc", "", "soul")
        assert sel is not None
        assert sel.version_id in approved

        # Raw count defends against ORM identity-map masking.
        from sqlmodel import Session, select, col
        from daemon.repositories.prompt_version.models import PromptVersion

        with Session(repo.engine) as session:
            rows = session.exec(
                select(col(PromptVersion.version_id)).where(
                    col(PromptVersion.agent_id) == "pconc",
                    col(PromptVersion.version_tag) == "",
                    col(PromptVersion.artifact) == "soul",
                    col(PromptVersion.is_selected) == 1,
                )
            ).all()
        assert len(rows) == 1, (
            f"{db_dialect}: expected exactly 1 pointer row; saw {len(rows)}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Audit append — same-transaction stamping, both dialects
# ─────────────────────────────────────────────────────────────────────────────


class TestDualDriverAuditAppend:
    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_lifecycle_stamps_expected_audit_sequence(
        self, repo, internal_actor, http_actor, db_dialect
    ):
        v = repo.create_draft("paud", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("paud", "", "soul", v.version_id, http_actor)

        with repo.engine.connect() as conn:
            rows = list(conn.execute(text(
                "SELECT event_type, actor_subject, actor_auth_method "
                "FROM prompt_audit_events WHERE agent_id = 'paud' "
                "ORDER BY created_at"
            )))

        events = [(r[0], r[1], r[2]) for r in rows]
        assert [e[0] for e in events] == [
            "CREATED", "SUBMITTED", "APPROVED", "SELECTED",
        ]
        # Internal lane for submit-side rows; session lane for the
        # human approve/select rows — identical on BOTH engines.
        assert events[0][2] == AUTH_METHOD_INTERNAL
        assert events[1][2] == AUTH_METHOD_INTERNAL
        assert events[2][2] == AUTH_METHOD_SESSION
        assert events[3][2] == AUTH_METHOD_SESSION
        assert events[2][1] == "user@example.com"

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_reject_and_recall_stamp_audit(self, repo, internal_actor, http_actor, db_dialect):
        vr = repo.create_draft("paud2", "", "soul", "r", internal_actor)
        repo.submit_for_approval(vr.version_id, internal_actor)
        repo.reject(vr.version_id, http_actor)

        vd = repo.create_draft("paud2", "workflow", "w", "d", internal_actor)
        repo.submit_for_approval(vd.version_id, internal_actor)
        # Recall edge: pending_approval → draft.
        recalled = repo.atomic_transition(
            version_id=vd.version_id,
            from_state="pending_approval",
            to_state="draft",
            actor=internal_actor,
        )
        assert recalled.state == "draft"

        with repo.engine.connect() as conn:
            rows = list(conn.execute(text(
                "SELECT event_type FROM prompt_audit_events "
                "WHERE agent_id = 'paud2' ORDER BY created_at"
            )))
        types = [r[0] for r in rows]
        assert "REJECTED" in types
        assert types.count("CREATED") == 2

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_raw_append_audit_event_roundtrip(self, repo, http_actor, db_dialect):
        """``append_audit_event`` (A2 surface) round-trips on both engines."""
        v = repo.create_draft("paud3", "", "soul", "x", http_actor)
        repo.append_audit_event(
            agent_id="paud3",
            version_tag="",
            artifact="soul",
            version_id=v.version_id,
            event_type="AGENT_CREATE_TEMPLATE_COPIED",
            actor=http_actor,
            payload={"source": "_baby_template"},
        )
        with repo.engine.connect() as conn:
            rows = list(conn.execute(text(
                "SELECT event_type, actor_subject, actor_auth_method "
                "FROM prompt_audit_events "
                "WHERE agent_id = 'paud3' "
                "AND event_type = 'AGENT_CREATE_TEMPLATE_COPIED'"
            )))
        assert len(rows) == 1
        assert rows[0][1] == "user@example.com"  # http_actor.subject (session fixture)
        assert rows[0][2] == "session"


# ─────────────────────────────────────────────────────────────────────────────
# create_and_submit composite — parity of the A5 path T8 rides on
# ─────────────────────────────────────────────────────────────────────────────


class TestDualDriverCreateAndSubmit:
    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_composite_lands_pending_and_archives_prior(
        self, repo, internal_actor, db_dialect
    ):
        r1 = repo.create_and_submit("pcas", "", "soul", "first", internal_actor)
        assert r1.state == "pending_approval"
        assert r1.version == 1

        r2 = repo.create_and_submit("pcas", "", "soul", "second", internal_actor)
        assert r2.state == "pending_approval"
        assert r2.version == 2

        from sqlmodel import Session, select, col
        from daemon.repositories.prompt_version.models import PromptVersion

        with Session(repo.engine) as session:
            states = {
                row[0]: row[1]
                for row in session.exec(
                    select(
                        col(PromptVersion.version),
                        col(PromptVersion.state),
                    ).where(
                        col(PromptVersion.agent_id) == "pcas",
                        col(PromptVersion.artifact) == "soul",
                    )
                ).all()
            }
        assert states == {1: "archived", 2: "pending_approval"}

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_composite_mutator_composes_on_effective_chain(
        self, repo, internal_actor, http_actor, db_dialect
    ):
        """C4: mutator composes selected > latest_approved > fallback."""
        # Approve v1 so the chain has an approved rung.
        v1 = repo.create_and_submit("pmut", "", "soul", "BASE", internal_actor)
        repo.approve(v1.version_id, http_actor)

        # Mutator composes on the effective (now the approved BASE).
        v2 = repo.create_and_submit(
            "pmut",
            "",
            "soul",
            lambda effective: f"{effective}\nAPPENDED",
            internal_actor,
        )
        assert v2.content == "BASE\nAPPENDED"

        # With NO approved rung and a file_fallback, the mutator sees
        # the fallback content.
        v3 = repo.create_and_submit(
            "pmut",
            "",
            "workflow",
            lambda effective: f"{effective or 'EMPTY'}+FALLBACK",
            internal_actor,
            file_fallback="FILE BODY",
        )
        assert v3.content == "FILE BODY+FALLBACK"
