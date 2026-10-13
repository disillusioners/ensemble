"""prompt-version DB-layer resolution tests.

T10 canonical file for the agent-prompt-immutability commission
(``..agents/shared/planning/agent-prompt-immutability/phaseA-plan.md``).

This module owns the DB-layer sections of the canonical test file
(per the brief: ``tests/test_prompt_version_resolution.py`` — the
T10 canonical file; the coder owning this commit owns its
DB-layer sections; later coders append the loader/hook/guard
sections). DB-layer coverage here:

* state transitions — ``test_state_transition_rejects_invalid_pair``
  (SC6 / A1 — all 5 valid pairs succeed; ``(rejected, draft)``
  forbidden; ``approved`` / ``rejected`` TERMINAL);
* one-selection-pointer invariant — ``test_one_selection_pointer_per_key_enforced``
  (SC5 / F5 / R3 — ``threading.Thread`` × N concurrent
  ``set_selection`` calls leave exactly one pointer);
* audit-event shape — ``test_audit_event_stamped_with_actor_and_method``
  (SC11 / A3 — actor + auth_method stamped inside the same
  ``engine.begin()`` txn as the mutation);
* precedence — ``test_selected_version_wins_over_latest_approved`` /
  ``test_latest_approved_wins_over_template_file`` /
  ``test_template_file_fallback_when_no_db_rows`` (F6 — the
  ``selected > latest_approved > None`` chain);
* ``create_and_submit`` composite — archive-on-resubmit +
  mutator composition + INSERT-retry (A5/C4 + A7/C5);
* get_effective_rows fingerprint×content concurrency (A8 — single
  SELECT returns selected + latest-approved id + content atomically).

The loader-hook / cache-invalidation / guard-bypass / no-override
no-regression tests are appended by the parallel Phase A workers
(filenames + ordering per phaseA-plan.md §T10 / §T12).

Test execution is fenced per the project critical notes:

* ``ENSEMBLE_SELF_ENV=dev`` (no live ensemble posture);
* ``POSTGRES_DB=ensemble_dev`` (sandbox DB only — abort if
  resolved to ``ensemble_prod``);
* use file-SQLite with WAL + QueuePool for the concurrency
  tests (in-memory + StaticPool has single-connection race bugs
  documented in the test fixture docstrings);
* run only the named file (``--tb=short -q``), never bare pytest,
  never ``-x``, never full-suite.
"""

from __future__ import annotations

import tempfile
import threading
import os
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_BEARER,
    AUTH_METHOD_INTERNAL,
    AUTH_METHOD_SESSION,
    ActorContext,
    InvalidTransitionError,
    PromptVersionRepository,
    VALID_TRANSITIONS,
    actor_from_instance,
    actor_from_request,
)


# ─────────────────────────────────────────────────────────────────────────────
# Engine fixture — file SQLite with WAL + QueuePool so the
# concurrency tests use real per-thread connections (in-memory +
# StaticPool serializes all threads on one connection and the
# Session.get after engine.begin() returns None under contention,
# even though the INSERT committed).
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def repo_engine() -> Iterator[tuple[Engine, str]]:
    """Yield a fresh file-SQLite engine + repo, with WAL configured.

    Each test gets its own DB file under ``tempfile.gettempdir()``
    (FAKE_HOME escape-guard: 2026-10-12 incident class). Cleanup
    via ``os.unlink`` in the finally branch.
    """
    db_path = tempfile.mktemp(suffix=".db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    # Schema: register the two new tables via create_all (the
    # SQLModel.metadata-driven path — the canonical parity test
    # covers the SQLite migration path separately).
    from sqlmodel import SQLModel
    from daemon.repositories.prompt_version.models import (
        PromptAuditEvent,
        PromptVersion,
    )
    SQLModel.metadata.create_all(engine)

    try:
        yield engine, db_path
    finally:
        engine.dispose()
        # WAL mode leaves -wal/-shm sidecars; ignore unlink errors.
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except FileNotFoundError:
                pass


@pytest.fixture
def repo(repo_engine) -> PromptVersionRepository:
    """Yield a PromptVersionRepository bound to the fresh engine."""
    engine, _ = repo_engine
    return PromptVersionRepository(engine)


@pytest.fixture
def http_actor() -> ActorContext:
    """HTTP session actor (approver lane)."""
    return ActorContext(subject="user@example.com", auth_method=AUTH_METHOD_SESSION)


@pytest.fixture
def http_bearer_actor() -> ActorContext:
    """HTTP bearer actor (machine lane)."""
    return ActorContext(subject="operator-cli", auth_method=AUTH_METHOD_BEARER)


@pytest.fixture
def internal_actor() -> ActorContext:
    """In-process submitter (inner_soul / agent_mother lane)."""
    return actor_from_instance("inst-1", "coder", "")


# ─────────────────────────────────────────────────────────────────────────────
# State machine (SC6 / A1)
# ─────────────────────────────────────────────────────────────────────────────


class TestStateMachine:
    """A1 ``VALID_TRANSITIONS`` + ``InvalidTransitionError(ValueError)``."""

    def test_valid_transitions_exactly_five_pairs(self):
        # A1 FROZEN: ``(rejected, draft)`` DROPPED; rejected +
        # approved are TERMINAL; recall edge ``(pending_approval,
        # draft)`` is the only path out of pending_approval other
        # than approval/rejection.
        assert VALID_TRANSITIONS == frozenset({
            ("draft", "pending_approval"),
            ("pending_approval", "approved"),
            ("pending_approval", "rejected"),
            ("pending_approval", "draft"),
            ("draft", "archived"),
        })

    def test_happy_path_draft_to_approved(
        self, repo, internal_actor, http_actor
    ):
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        assert v.state == "draft"
        v = repo.submit_for_approval(v.version_id, internal_actor)
        assert v.state == "pending_approval"
        v = repo.approve(v.version_id, http_actor)
        assert v.state == "approved"
        assert v.approver_subject == "user@example.com"
        assert v.approver_auth_method == AUTH_METHOD_SESSION

    def test_invalid_transition_raises(
        self, repo, internal_actor, http_actor
    ):
        # Cannot skip from draft to approved.
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        with pytest.raises(InvalidTransitionError):
            repo.atomic_transition(
                v.version_id, "draft", "approved", http_actor,
                approver_subject="x", approver_auth_method=AUTH_METHOD_SESSION,
            )

    def test_rejected_to_draft_is_dropped(
        self, repo, internal_actor, http_actor
    ):
        # A1: rejected is TERMINAL; re-submit = NEW row.
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.reject(v.version_id, http_actor)
        with pytest.raises(InvalidTransitionError):
            repo.atomic_transition(
                v.version_id, "rejected", "draft", http_actor,
            )

    def test_rejected_to_anything_is_terminal(
        self, repo, internal_actor, http_actor
    ):
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.reject(v.version_id, http_actor)
        # Every other transition off rejected also raises.
        for to_state in ("approved", "pending_approval", "archived"):
            with pytest.raises(InvalidTransitionError):
                repo.atomic_transition(
                    v.version_id, "rejected", to_state, http_actor,
                    approver_subject="x",
                    approver_auth_method=AUTH_METHOD_SESSION,
                )

    def test_re_submit_after_reject_is_new_row(
        self, repo, internal_actor, http_actor
    ):
        v1 = repo.create_draft("dev", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v1.version_id, internal_actor)
        repo.reject(v1.version_id, http_actor)
        v2 = repo.create_draft("dev", "", "soul", "b", internal_actor)
        assert v2.version == 2, (
            "rejection stays in lineage at v1; re-submit is a NEW row "
            "at max(version)+1 (A1 forensic-identity preservation)"
        )

    def test_internal_actor_cannot_approve_or_reject(
        self, repo, internal_actor
    ):
        # F4: agents cannot self-approve; HTTP-only approval rule.
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        with pytest.raises(ValueError, match="only HTTP actors"):
            repo.approve(v.version_id, internal_actor)
        with pytest.raises(ValueError, match="only HTTP actors"):
            repo.reject(v.version_id, internal_actor)


# ─────────────────────────────────────────────────────────────────────────────
# One-selection-pointer invariant (SC5 / F5 / R3)
# ─────────────────────────────────────────────────────────────────────────────


class TestSelectionPointer:
    """F5 / R3 — exactly one is_selected per key under concurrency."""

    def test_one_selection_pointer_per_key_enforced(
        self, repo, internal_actor, http_actor
    ):
        # Create + approve N versions for the same (agent, artifact).
        n = 8
        approved = []
        for i in range(n):
            v = repo.create_draft("dev", "", "soul", f"v{i}", internal_actor)
            repo.submit_for_approval(v.version_id, internal_actor)
            repo.approve(v.version_id, http_actor)
            approved.append(v.version_id)

        # N threads race to set_selection on the same key.
        errors = []
        def _select(vi):
            try:
                repo.set_selection("dev", "", "soul", vi, http_actor)
            except Exception as e:  # noqa: BLE001
                errors.append((vi[:8], type(e).__name__))

        threads = [threading.Thread(target=_select, args=(vi,)) for vi in approved]
        for t in threads: t.start()
        for t in threads: t.join()

        # Exactly one pointer is set, regardless of contention.
        sel = repo.get_selection("dev", "", "soul")
        assert sel is not None
        assert sel.version_id in approved

        # Raw count is also 1 (defends against ORM identity-map
        # caching masking a duplicate row).
        from sqlmodel import Session, select, col
        from daemon.repositories.prompt_version.models import PromptVersion
        with Session(repo.engine) as session:
            rows = session.exec(
                select(col(PromptVersion.version_id)).where(
                    col(PromptVersion.agent_id) == "dev",
                    col(PromptVersion.version_tag) == "",
                    col(PromptVersion.artifact) == "soul",
                    col(PromptVersion.is_selected) == 1,
                )
            ).all()
        assert len(rows) == 1, (
            f"expected exactly 1 pointer row in DB; saw {len(rows)}"
        )

    def test_set_selection_rejects_non_approved_target(
        self, repo, internal_actor, http_actor
    ):
        # F6: only approved content can be selected for the
        # effective prompt.
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        # v is in 'draft' state — cannot select it.
        with pytest.raises(ValueError, match="only approved"):
            repo.set_selection("dev", "", "soul", v.version_id, http_actor)

    def test_clear_selection_emits_cleared_audit(
        self, repo, internal_actor, http_actor, repo_engine
    ):
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v.version_id, http_actor)
        repo.clear_selection("dev", "", "soul", http_actor)
        sel = repo.get_selection("dev", "", "soul")
        assert sel is None

        # Audit trail carries CLEARED.
        engine, _ = repo_engine
        with Session(engine) if False else engine.connect() as conn:
            from sqlalchemy import text
            rows = list(conn.execute(text(
                "SELECT event_type FROM prompt_audit_events "
                "WHERE version_id = :vid ORDER BY created_at"
            ), {"vid": v.version_id}))
            event_types = [r[0] for r in rows]
        assert "CLEARED" in event_types


# ─────────────────────────────────────────────────────────────────────────────
# Audit-event stamping (SC11 / A3)
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditStamping:
    """A3 — audit row stamped inside the same engine.begin() txn."""

    def test_audit_event_stamped_with_actor_and_method(
        self, repo, internal_actor, http_actor
    ):
        v = repo.create_draft("dev", "", "soul", "a", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v.version_id, http_actor)

        engine, _ = repo.engine, None
        with engine.connect() as conn:
            from sqlalchemy import text
            rows = list(conn.execute(text(
                "SELECT event_type, actor_subject, actor_auth_method "
                "FROM prompt_audit_events WHERE version_id = :vid "
                "ORDER BY created_at"
            ), {"vid": v.version_id}))
        events = [(r[0], r[1], r[2]) for r in rows]
        # Every audit row carries a non-empty actor + auth_method.
        for event_type, subject, method in events:
            assert event_type, "event_type must be non-empty"
            assert subject, f"actor_subject missing for {event_type}"
            assert method, f"actor_auth_method missing for {event_type}"

        # The exact lifecycle sequence for this flow:
        expected_types = ["CREATED", "SUBMITTED", "APPROVED", "SELECTED"]
        assert [e[0] for e in events] == expected_types
        # Internal submitter for CREATED + SUBMITTED.
        assert events[0][2] == AUTH_METHOD_INTERNAL
        assert events[1][2] == AUTH_METHOD_INTERNAL
        # HTTP approver / selector for APPROVED + SELECTED.
        assert events[2][2] == AUTH_METHOD_SESSION
        assert events[3][2] == AUTH_METHOD_SESSION

    def test_actor_from_request_refuses_internal(self):
        from types import SimpleNamespace
        fake_req = SimpleNamespace()
        fake_req.state = SimpleNamespace()
        fake_req.state.auth_context = SimpleNamespace(
            subject="x", auth_method=AUTH_METHOD_INTERNAL,
            scopes=set(), expires_at="", meta={},
        )
        with pytest.raises(ValueError, match="reserved for in-process"):
            actor_from_request(fake_req)

    def test_actor_from_instance_requires_identity_triple(self):
        with pytest.raises(ValueError, match="requires instance_id"):
            ActorContext(subject="x", auth_method=AUTH_METHOD_INTERNAL)
        # agent_id missing -> still raises
        with pytest.raises(ValueError, match="requires instance_id"):
            ActorContext(
                subject="x", auth_method=AUTH_METHOD_INTERNAL,
                instance_id="i1", agent_id=None,
            )

    def test_actor_from_request_refuses_unknown_auth_method(self):
        from types import SimpleNamespace
        fake_req = SimpleNamespace()
        fake_req.state = SimpleNamespace()
        fake_req.state.auth_context = SimpleNamespace(
            subject="x", auth_method="bogus",
            scopes=set(), expires_at="", meta={},
        )
        with pytest.raises(ValueError, match="unknown auth_method"):
            actor_from_request(fake_req)


# ─────────────────────────────────────────────────────────────────────────────
# Precedence (F6 — selected > latest_approved > None)
# ─────────────────────────────────────────────────────────────────────────────


class TestPrecedence:
    """F6 — the three named resolution-path tests."""

    def test_template_file_fallback_when_no_db_rows(
        self, repo, internal_actor
    ):
        # F1+no-override no-regression: no DB rows → repo returns
        # None, caller falls back to file content.
        eff = repo.get_effective("dev", "", "soul")
        assert eff is None

    def test_latest_approved_wins_over_template_file(
        self, repo, internal_actor, http_actor
    ):
        v = repo.create_draft("dev", "", "soul", "DB APPROVED v1", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        # No selection set.
        eff = repo.get_effective("dev", "", "soul")
        assert eff == "DB APPROVED v1"

    def test_selected_version_wins_over_latest_approved(
        self, repo, internal_actor, http_actor
    ):
        v1 = repo.create_draft("dev", "", "soul", "v1 approved", internal_actor)
        repo.submit_for_approval(v1.version_id, internal_actor)
        repo.approve(v1.version_id, http_actor)
        v2 = repo.create_draft("dev", "", "soul", "v2 approved", internal_actor)
        repo.submit_for_approval(v2.version_id, internal_actor)
        repo.approve(v2.version_id, http_actor)
        # v2 is latest_approved; select v1 (the older approved).
        repo.set_selection("dev", "", "soul", v1.version_id, http_actor)
        eff = repo.get_effective("dev", "", "soul")
        assert eff == "v1 approved", (
            "explicit selection MUST override latest_approved (F6)"
        )

    def test_get_effective_rows_returns_selected_and_latest_for_each_artifact(
        self, repo, internal_actor, http_actor
    ):
        # Same (agent, version_tag) pair, two artifacts.
        v1_soul = repo.create_draft("dev", "", "soul", "soul v1", internal_actor)
        repo.submit_for_approval(v1_soul.version_id, internal_actor)
        repo.approve(v1_soul.version_id, http_actor)

        v2_soul = repo.create_draft("dev", "", "soul", "soul v2", internal_actor)
        repo.submit_for_approval(v2_soul.version_id, internal_actor)
        repo.approve(v2_soul.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v1_soul.version_id, http_actor)

        v1_rule = repo.create_draft("dev", "", "rule", "rule v1", internal_actor)
        repo.submit_for_approval(v1_rule.version_id, internal_actor)
        repo.approve(v1_rule.version_id, http_actor)

        rows = repo.get_effective_rows("dev", "")
        by_artifact = {r.artifact: r for r in rows}
        assert set(by_artifact) == {"soul", "rule"}

        # soul: selected = v1 (older), latest_approved = v2.
        assert by_artifact["soul"].selected_version_id == v1_soul.version_id
        assert by_artifact["soul"].selected_content == "soul v1"
        assert by_artifact["soul"].latest_approved_version_id == v2_soul.version_id
        assert by_artifact["soul"].latest_approved_content == "soul v2"

        # rule: no selection, only latest_approved.
        assert by_artifact["rule"].selected_version_id is None
        assert by_artifact["rule"].latest_approved_version_id == v1_rule.version_id
        assert by_artifact["rule"].latest_approved_content == "rule v1"


# ─────────────────────────────────────────────────────────────────────────────
# create_and_submit composite (A5 / C4)
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateAndSubmit:
    """A5/C4 — create_draft + submit_for_approval atomically."""

    def test_create_and_submit_full_content(
        self, repo, internal_actor, http_actor
    ):
        v = repo.create_and_submit("dev", "", "soul", "x", internal_actor)
        assert v.state == "pending_approval"
        assert v.version == 1

    def test_create_and_submit_mutator_applied_to_file_fallback(
        self, repo, internal_actor, http_actor
    ):
        def append(old):
            return (old or "") + " + LEARNED"
        v = repo.create_and_submit(
            "dev", "", "soul", append, internal_actor,
            file_fallback="base content",
        )
        assert v.state == "pending_approval"
        assert v.content == "base content + LEARNED"

    def test_create_and_submit_chains_and_archives_prior_pending(
        self, repo, internal_actor, http_actor
    ):
        # Two consecutive un-approved submissions on the same key:
        # the prior pending row gets archived (≤1 active pending).
        v1 = repo.create_and_submit("dev", "", "soul", "first", internal_actor)
        v2 = repo.create_and_submit("dev", "", "soul", "second", internal_actor)
        assert v1.version == 1
        assert v2.version == 2

        v1_rel = repo.get_version(v1.version_id)
        v2_rel = repo.get_version(v2.version_id)
        assert v1_rel.state == "archived", (
            "prior pending row must be archived by the new submission"
        )
        assert v2_rel.state == "pending_approval"


# ─────────────────────────────────────────────────────────────────────────────
# A7/C5 INSERT-retry under concurrency
# ─────────────────────────────────────────────────────────────────────────────


class TestInsertRetry:
    """A7/C5 — UNIQUE-constraint INSERT-retry on ``create_draft``."""

    def test_concurrent_create_draft_yields_distinct_versions(
        self, repo, internal_actor
    ):
        n = 10
        results = {}
        errors = {}

        def worker(name):
            try:
                v = repo.create_draft("dev", "", "soul", name, internal_actor)
                results[name] = v.version
            except Exception as e:  # noqa: BLE001
                errors[name] = (type(e).__name__, str(e)[:80])

        threads = [
            threading.Thread(target=worker, args=(f"t{i}",))
            for i in range(n)
        ]
        for t in threads: t.start()
        for t in threads: t.join()

        assert errors == {}, f"some workers failed: {errors}"
        assert len(results) == n
        assert sorted(results.values()) == list(range(1, n + 1)), (
            "all versions must be distinct 1..n (A7/C5 retry absorbs "
            "the SELECT MAX+INSERT race)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# A8 fingerprint×content concurrency
# ─────────────────────────────────────────────────────────────────────────────


class TestGetEffectiveRowsA8:
    """A8 — single SELECT returns selected + latest-approved atomically."""

    def test_get_effective_rows_one_select_no_toctou(
        self, repo, internal_actor, http_actor, repo_engine
    ):
        # Create + approve + select one version.
        v = repo.create_draft("dev", "", "soul", "TOCTOU test", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v.version_id, http_actor)

        # Verify a single SELECT — ``get_effective_rows`` uses one
        # SQL statement (the CTE-driven LEFT JOIN in the repo).
        engine, _ = repo_engine
        from sqlalchemy import event
        query_count = 0

        @event.listens_for(engine, "before_cursor_execute")
        def _count(conn, cursor, statement, parameters, context, executemany):
            nonlocal query_count
            if "prompt_versions" in statement.lower():
                query_count += 1

        rows = repo.get_effective_rows("dev", "")
        # remove listener
        event.remove(engine, "before_cursor_execute", _count)
        assert rows, "expected at least one row"
        assert query_count == 1, (
            f"get_effective_rows must be ONE SELECT (A8); saw "
            f"{query_count} statements touching prompt_versions"
        )

    def test_concurrent_get_effective_rows_is_consistent(
        self, repo, internal_actor, http_actor
    ):
        # 10 concurrent get_effective_rows on the same key all
        # return identical content (the A8 TOCTOU guarantee).
        v = repo.create_draft("dev", "", "soul", "concurrent-read", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v.version_id, http_actor)

        observed = []

        def reader():
            rows = repo.get_effective_rows("dev", "")
            by_artifact = {r.artifact: r for r in rows}
            observed.append(by_artifact.get("soul").selected_content if by_artifact.get("soul") else None)

        threads = [threading.Thread(target=reader) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        # All readers see the same selected content.
        assert observed == ["concurrent-read"] * 10


# Helper for the Session.get audit query inside the test.
from sqlmodel import Session