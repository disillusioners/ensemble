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
* ``POSTGRES_DB=ensemble_dev_test_only`` (daemon-identity fence —
  abort if resolved to ``ensemble_prod``);
* use file-SQLite with WAL + QueuePool for the concurrency
  tests (in-memory + StaticPool has single-connection race bugs
  documented in the test fixture docstrings);
* run only the named file (``--tb=short -q``), never bare pytest,
  never ``-x``, never full-suite.

SC8/SC9 PostgreSQL parity (2026-10-13, T10 close-out): the
DB-layer test classes are parametrized over ``[sqlite, postgres]``.
The PG leg runs against a DEDICATED SCRATCH DATABASE per test
(``ensemble_promptver_test_<8hex>``, created + dropped per test —
never ``ensemble_dev`` itself, never ``ensemble_prod``). The
fixture deliberately reads ONLY ``PG_TEST_*`` env vars (the
``tests/postgres/conftest.py`` convention) and NEVER ambient
``POSTGRES_*`` — the ambient environment on ensemble hosts carries
``POSTGRES_DB=ensemble_prod`` (env-poison 3-guard class), so
inheriting it would point tests at prod. Two PG-specific
production findings surfaced while closing this gap (F-1
one-pointer invariant, F-2 INSERT-retry budget) were FIXED in the
repository on 2026-10-13 (dialect-gated ``pg_advisory_xact_lock``
per-key writer serialization — R3); the temporary non-strict
xfail markers that mapped these defects have been REMOVED and
both concurrency tests are now real assertions that must pass
deterministically on BOTH dialects.
"""

from __future__ import annotations

import tempfile
import threading
import time
import os
import uuid
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
# Engine fixtures — SC8 SQLite/PG parity.
#
# ``repo_engine(db_dialect)`` dispatches on the parametrized dialect:
#   * ``sqlite``   — file SQLite with WAL + QueuePool so the
#     concurrency tests use real per-thread connections (in-memory +
#     StaticPool serializes all threads on one connection and the
#     Session.get after engine.begin() returns None under contention,
#     even though the INSERT committed). Byte-identical to the
#     original pre-parametrization fixture.
#   * ``postgres`` — a DEDICATED SCRATCH DATABASE per test on the
#     local PG server (created via ``CREATE DATABASE`` on a
#     maintenance connection to ``ensemble_dev``, dropped at
#     teardown). Never writes into ``ensemble_dev`` itself; NEVER
#     ``ensemble_prod`` (hard session-abort). Schema via
#     ``SQLModel.metadata.create_all`` — the PG truth path (the
#     migration runner is SQLite-only by design; PG receives new
#     tables via create_all, mirroring api-lifespan behavior).
#
# Connection settings reuse the ``tests/postgres/conftest.py``
# convention (``PG_TEST_HOST/PORT/USER/PASSWORD`` env overrides) so
# CI can retarget without editing the file. The maintenance DB is
# ``PG_TEST_MAINT_DB`` (default ``ensemble_dev`` — scratch DBs are
# created from it; it is NEVER written to). Ambient ``POSTGRES_*``
# vars are deliberately NOT read (env-poison 3-guard convention:
# hosts carry ``POSTGRES_DB=ensemble_prod`` in the ambient env).
# ─────────────────────────────────────────────────────────────────────────────

_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "ensemble_dev")
_PG_SCRATCH_PREFIX = "ensemble_promptver_test_"

# Hard-abort set — a resolved target in this set means the fixture is
# pointed at a live database; fail the whole session (fail-closed,
# env-poison 3-guard convention) instead of writing test rows into it.
_PG_FORBIDDEN_DBS = frozenset({"ensemble_prod"})


def _pg_admin_engine():
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE.

    Probes connectivity first and distinguishes auth failures from
    "PG is down" (pattern from ``tests/postgres/conftest.py``) so the
    skip reason is diagnostic instead of misleading. Returns
    ``None`` when PG is unreachable — the caller skips loudly.
    """
    if _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        # Hard session-abort, not a skip: the fixture resolved to a
        # live database. Never write test rows into it.
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved maintenance DB "
            f"{_PG_MAINT_DB!r} — refuse to point prompt-version tests "
            f"at a live database (env-poison 3-guard convention).",
            returncode=1,
        )
    from sqlalchemy.exc import DBAPIError, OperationalError

    url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}"
    )
    engine = create_engine(url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        orig = getattr(exc, "orig", exc)
        sqlstate = getattr(getattr(orig, "diag", None), "sqlstate", None)
        kind = (
            "auth failure (check PG_TEST_USER / PG_TEST_PASSWORD)"
            if sqlstate in {"28000", "28P01"}
            else "unreachable"
        )
        return None, f"PostgreSQL {kind} at {_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}: {exc}"
    except DBAPIError as exc:
        engine.dispose()
        return None, f"PostgreSQL DBAPI error at {_PG_HOST}:{_PG_PORT}: {exc}"
    return engine, None


def _postgres_repo_engine() -> Iterator[tuple[Engine, str]]:
    """Yield a fresh scratch-PostgreSQL engine + scratch DB name.

    Lifecycle: probe maintenance connection → CREATE DATABASE
    ``ensemble_promptver_test_<8hex>`` → ``SQLModel.metadata
    .create_all`` (PG truth path) → yield → dispose → terminate
    stray backends → DROP DATABASE. Skips loudly (CI portability)
    when PG is unreachable.
    """
    from sqlmodel import SQLModel

    from daemon.repositories.prompt_version.models import (
        PromptAuditEvent,
        PromptVersion,
    )
    # Importing the model modules registers the tables on
    # SQLModel.metadata BEFORE create_all (tests/postgres/conftest.py
    # convention) — without this the scratch schema can come up
    # empty when only part of the metadata was populated.
    assert PromptVersion is not None and PromptAuditEvent is not None  # noqa: F821

    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    if scratch_db in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved scratch DB "
            f"{scratch_db!r} — refuse to point prompt-version tests "
            f"at a live database (env-poison 3-guard convention).",
            returncode=1,
        )

    engine: Engine | None = None
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
        scratch_url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True)
        # Schema: the PG truth path — full-metadata create_all on a
        # DEDICATED scratch DB (mirrors api-lifespan behavior). The
        # .sql migration runner is SQLite-only by design; PG
        # receives brand-new tables via create_all (dual-driver
        # truth; stored_watches / snapshot precedent).
        SQLModel.metadata.create_all(engine)
        yield engine, scratch_db
    finally:
        if engine is not None:
            engine.dispose()
        try:
            with admin.connect() as conn:
                conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM "
                        "pg_stat_activity WHERE datname = :d "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"d": scratch_db},
                )
                conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        finally:
            admin.dispose()


def _sqlite_repo_engine() -> Iterator[tuple[Engine, str]]:
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
def db_dialect() -> str:
    """Default dialect for DB-layer tests.

    The SC8 parity tests are parametrized over this name via
    ``@pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])``,
    which OVERRIDES this default for those tests. The default keeps
    anything not explicitly parametrized on SQLite — the historical
    engine — so a missed parametrize can never silently introduce a
    PG dependency (and loader/hook tests stay SQLite-only by design:
    they test loader behavior, not dialect).
    """
    return "sqlite"


@pytest.fixture
def repo_engine(db_dialect: str) -> Iterator[tuple[Engine, str]]:
    """Yield ``(engine, db_identifier)`` for the parametrized dialect."""
    if db_dialect == "postgres":
        yield from _postgres_repo_engine()
    else:
        yield from _sqlite_repo_engine()


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

    # SC8: the DB-touching tests are parametrized over
    # [sqlite, postgres] (``test_valid_transitions_exactly_five_pairs``
    # below is a pure frozenset check — dialect-independent, not
    # parametrized).

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
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

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
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

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
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

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
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

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
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

    # SC8: parametrized over [sqlite, postgres]. The PG leg was a
    # non-strict xfail for F-1 until the 2026-10-13 repo fix
    # (dialect-gated pg_advisory_xact_lock per-key serialization);
    # the marker is REMOVED — this is now a real assertion on both
    # dialects, with concurrency raised 8→16 to keep the fix
    # provable under contention.
    pytestmark = pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])

    def test_one_selection_pointer_per_key_enforced(
        self, repo, internal_actor, http_actor
    ):
        # F-1 regression guard: concurrent set_selection must leave
        # EXACTLY one is_selected pointer on BOTH dialects (the
        # repo serializes per-key writers via pg_advisory_xact_lock
        # on PG; SQLite orders them via DB-level single-writer).
        n = 16
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

        # With per-key serialization no set_selection may fail at
        # all (a swallowed IntegrityError/DBAPIError here would
        # silently weaken the pointer assertion below).
        assert errors == [], f"set_selection failures under contention: {errors}"

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

    # SC8: the DB-touching audit test is parametrized over
    # [sqlite, postgres]; the three actor-validation tests below are
    # pure Python (no engine) and stay unparametrized.
    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
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

    # SC8: all four tests hit the repo — parametrized over
    # [sqlite, postgres].
    pytestmark = pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])

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

    # SC8: all three tests hit the repo — parametrized over
    # [sqlite, postgres].
    pytestmark = pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])

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

    # F5 amendment (leader-gate 2026-10-14, dispatcher-ratified) —
    # composition base for the mutator path is the prior active
    # (draft|pending) row's content, NOT get_effective (which
    # excludes pending rows). Without the fix, two consecutive
    # un-approved mutator submissions would land v2 = f(template) +
    # delta₂ — silently losing delta₁ on approval (the exact
    # regression the C4 "two calls = two appends" intent exists to
    # prevent).

    def test_create_and_submit_mutator_chains_on_prior_active_content(
        self, repo, internal_actor, http_actor
    ):
        """F5: the second mutator submission composes on the prior
        active row's content (not the file template). v2 carries
        BOTH deltas; v1 is archived (≤ 1 active pending per key).
        """
        def append_delta1(old):
            return (old or "") + " + DELTA1"
        v1 = repo.create_and_submit(
            "dev", "", "soul", append_delta1, internal_actor,
            file_fallback="base content",
        )
        assert v1.state == "pending_approval"
        assert v1.content == "base content + DELTA1"

        def append_delta2(old):
            return (old or "") + " + DELTA2"
        v2 = repo.create_and_submit(
            "dev", "", "soul", append_delta2, internal_actor,
            file_fallback="base content",
        )
        assert v2.state == "pending_approval"
        # The critical F5 assertion: v2 composes on v1's content
        # (the prior active row's content), NOT on get_effective
        # (which would return file_fallback "base content" — losing
        # DELTA1).
        assert v2.content == "base content + DELTA1 + DELTA2", (
            f"F5 amendment: expected both deltas in v2.content, "
            f"got {v2.content!r} (composition must chain on the "
            f"prior active row's content, not the F6 effective chain)"
        )

        # Pending-supersession invariant preserved: v1 archived, v2
        # pending (the ≤ 1 active pending per key fence).
        v1_rel = repo.get_version(v1.version_id)
        v2_rel = repo.get_version(v2.version_id)
        assert v1_rel.state == "archived"
        assert v2_rel.state == "pending_approval"

    def test_create_and_submit_mutator_after_approval_composes_on_approved(
        self, repo, internal_actor, http_actor
    ):
        """F5 regression guard: after approval (no prior active
        rows), the next mutator submission composes on the
        approved content via the F6 effective chain. This is the
        pre-existing "first-submission-after-approve" behavior —
        the F5 fix MUST NOT regress it.
        """
        v1 = repo.create_and_submit(
            "dev", "", "soul", "approved content", internal_actor,
        )
        repo.approve(v1.version_id, http_actor)

        def append_new(old):
            return (old or "") + " + NEW"
        v2 = repo.create_and_submit(
            "dev", "", "soul", append_new, internal_actor,
            file_fallback="template content",
        )
        # No prior active (v1 is approved, not draft/pending).
        # Base = get_effective() = "approved content" (file_fallback
        # "template content" is NOT used because get_effective
        # returns the approved content first).
        assert v2.content == "approved content + NEW", (
            f"post-approval compose-on-approved: expected "
            f"'approved content + NEW', got {v2.content!r}"
        )

    def test_create_and_submit_approval_retains_prior_active_delta(
        self, repo, internal_actor, http_actor
    ):
        """F5 end-to-end: two un-approved mutator submissions chain
        via the prior-active base, and the approved row serves
        content with BOTH deltas (the C4 "two calls = two
        appends" intent — the regression the defect would
        introduce).
        """
        def append_delta1(old):
            return (old or "") + " + DELTA1"
        v1 = repo.create_and_submit(
            "dev", "", "soul", append_delta1, internal_actor,
            file_fallback="base content",
        )

        def append_delta2(old):
            return (old or "") + " + DELTA2"
        v2 = repo.create_and_submit(
            "dev", "", "soul", append_delta2, internal_actor,
            file_fallback="base content",
        )

        # Approve v2 — this is the critical step. The pre-F5-fix
        # defect would have v2.content = "base content + DELTA2"
        # (composed on get_effective = file_fallback, not on
        # prior_active = v1.content) — losing DELTA1. After the
        # fix, v2.content carries BOTH deltas.
        repo.approve(v2.version_id, http_actor)

        # get_effective serves the approved row's content, which
        # now carries BOTH deltas (the F5 fix preserved the
        # C4 append intent through approval).
        effective = repo.get_effective("dev", "", "soul")
        assert effective == "base content + DELTA1 + DELTA2", (
            f"F5 end-to-end: expected both deltas after approval, "
            f"got {effective!r} (approval must retain the prior "
            f"active delta — the regression the F5 fix closes)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# A7/C5 INSERT-retry under concurrency
# ─────────────────────────────────────────────────────────────────────────────


class TestInsertRetry:
    """A7/C5 — UNIQUE-constraint INSERT-retry on ``create_draft``."""

    # SC8: parametrized over [sqlite, postgres]. The PG leg was a
    # non-strict xfail for F-2 until the 2026-10-13 repo fix (the
    # per-key advisory lock closes the SELECT MAX+INSERT race; the
    # bounded retry budget stays as backstop, NOT raised); the
    # marker is REMOVED — real assertion on both dialects, with
    # concurrency raised 10→16 to keep the fix provable.
    pytestmark = pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])

    def test_concurrent_create_draft_yields_distinct_versions(
        self, repo, internal_actor
    ):
        # F-2 regression guard: N concurrent create_draft calls on
        # the same key must ALL succeed with distinct versions 1..n
        # on BOTH dialects (no IntegrityError escapes; A7 retry
        # budget untouched as the backstop).
        n = 16
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

    # SC8: both tests hit the repo — parametrized over
    # [sqlite, postgres].
    pytestmark = pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])

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


# ─────────────────────────────────────────────────────────────────────────────
# S12 tag normalization (A7) — DB-layer pins, both dialects
# ─────────────────────────────────────────────────────────────────────────────


class TestTagNormalizationS12:
    """S12 / A7 — tag normalization at the DB write boundary.

    Two halves of the S12 contract:

    * ``NULL``/``""`` ``version_tag`` unify to the ``""`` convention
      at write time (documented in
      ``daemon/repositories/prompt_version/models.py`` — NULLs are
      distinct in UNIQUE on BOTH drivers, so the column must never
      hold NULL or the fence becomes ambiguous);
    * ``[v2]``-shaped agent identities are stored as the PARSED pair
      ``(agent_id, version_tag)`` (``registry._parse_agent_dir_name``
      at every DB-write site) so a tagged sibling's rows never leak
      into the base agent and vice versa (F9 keying).
    """

    # SC8: the two DB-touching tests are parametrized over
    # [sqlite, postgres]; the parser-contract test is pure Python.

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_null_and_empty_tag_unify_to_empty(
        self, repo, internal_actor, repo_engine
    ):
        v1 = repo.create_draft("dev", None, "soul", "a", internal_actor)
        v2 = repo.create_draft("dev", "", "soul", "b", internal_actor)
        assert v1.version_tag == "" and v2.version_tag == "", (
            "NULL/'' must normalize to '' at write time (S12)"
        )
        # Same key namespace: the version counter continues across
        # the NULL/'' forms (they are ONE identity, not two).
        assert v1.version == 1
        assert v2.version == 2

        # Raw DB check: NO NULL version_tag rows exist (NULLs are
        # distinct in UNIQUE on both drivers — a NULL row would be
        # invisible to the UNIQUE fence).
        from sqlmodel import Session, select, col
        from daemon.repositories.prompt_version.models import PromptVersion
        engine, _ = repo_engine
        with Session(engine) as session:
            null_rows = session.exec(
                select(col(PromptVersion.version_id)).where(
                    col(PromptVersion.agent_id) == "dev",
                    col(PromptVersion.version_tag).is_(None),
                )
            ).all()
        assert null_rows == [], (
            "version_tag must never persist as NULL (S12 normalization)"
        )

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_tagged_v2_identity_is_disjoint_from_base(
        self, repo, internal_actor, http_actor
    ):
        # ``developer[v2]`` parses to (developer, v2) — the DB-write
        # contract; the tagged key's version sequence and effective
        # reads are fully disjoint from the base key (F9).
        base = repo.create_draft(
            "developer", "", "soul", "base v1", internal_actor
        )
        tagged = repo.create_draft(
            "developer", "v2", "soul", "tagged v1", internal_actor
        )
        assert base.version == 1 and tagged.version == 1, (
            "tagged and base keys hold INDEPENDENT version sequences"
        )

        # Approve + select ONLY the tagged row; the base key must
        # not see it.
        repo.submit_for_approval(tagged.version_id, internal_actor)
        repo.approve(tagged.version_id, http_actor)
        assert repo.get_effective("developer", "", "soul") is None, (
            "tagged override must never leak into the base key (F9)"
        )
        assert repo.get_effective("developer", "v2", "soul") == "tagged v1"

    def test_registry_tag_parser_contract(self):
        # The parser DB-write sites rely on (A7 S12 clause) —
        # pinned here so a parser regression cannot silently
        # misfile tagged rows. Pure Python; not dialect-parametrized.
        from daemon.registry import _parse_agent_dir_name

        assert _parse_agent_dir_name("developer") == ("developer", None)
        assert _parse_agent_dir_name("developer[v2]") == ("developer", "v2")
        assert _parse_agent_dir_name("developer[test_version]") == (
            "developer",
            "test_version",
        )
        # Inner brackets / path chars do NOT match — treated as a
        # plain agent id (never a tag).
        assert _parse_agent_dir_name("dev[[v2]]") == ("dev[[v2]]", None)
        assert _parse_agent_dir_name("dev[../etc]") == ("dev[../etc]", None)


# ─────────────────────────────────────────────────────────────────────────────
# SC9 PG leg — fresh-PG create_all schema truth + repo round-trip
# ─────────────────────────────────────────────────────────────────────────────


class TestPGSchemaSC9:
    """SC9 PostgreSQL leg — the fresh-PG proof.

    Asserts, against the dedicated scratch PG database the fixture
    creates (fresh ``create_all``, never ``ensemble_dev`` itself):

    * both prompt-version tables exist (information_schema);
    * the A7/C5/G-4e UNIQUE fence exists with its exact name and
      ordered column list (pg_constraint — ``uq_prompt_versions_
      agent_tag_artifact_version`` on ``(agent_id, version_tag,
      artifact, version)``);
    * every named index from the parity contract exists
      (pg_indexes — the same set
      ``tests/migration/test_prompt_versions_index_parity.py``
      pins for the models + migration sides);
    * a fresh ``create_all`` → repository round-trip works
      (create_draft → submit → approve → set_selection →
      get_effective).

    NOTE (dual-driver truth, documented): the ``.sql`` migration
    runner is SQLite-ONLY by design (``daemon/migrations`` runner;
    PG truth = ``SQLModel.metadata.create_all`` — the
    stored_watches / snapshot precedent). There is intentionally NO
    ``.sql`` execution leg here; the fresh-PG proof IS this
    create_all-based schema inspection.
    """

    @pytest.mark.parametrize("db_dialect", ["postgres"])
    def test_fresh_pg_create_all_schema_and_round_trip(
        self, repo, repo_engine, internal_actor, http_actor
    ):
        from sqlalchemy import inspect

        engine, scratch_db = repo_engine
        assert scratch_db.startswith("ensemble_promptver_test_"), (
            f"expected the dedicated scratch DB, got {scratch_db!r}"
        )
        assert scratch_db != "ensemble_prod"

        insp = inspect(engine)
        tables = set(insp.get_table_names())
        assert {"prompt_versions", "prompt_audit_events"} <= tables, (
            f"fresh-PG create_all must create both tables; saw {tables}"
        )

        # — UNIQUE fence (A7/C5/G-4e), exact name + ordered columns —
        with engine.connect() as conn:
            uq_rows = list(conn.execute(text(
                "SELECT a.attname "
                "FROM pg_constraint c "
                "CROSS JOIN LATERAL unnest(c.conkey) "
                "  WITH ORDINALITY AS k(attnum, ord) "
                "JOIN pg_attribute a "
                "  ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
                "WHERE c.conname = :name AND c.contype = 'u' "
                "ORDER BY k.ord"
            ), {"name": "uq_prompt_versions_agent_tag_artifact_version"}))
        assert uq_rows, (
            "UNIQUE constraint uq_prompt_versions_agent_tag_artifact_version "
            "must exist on the fresh PG schema (A7/C5/G-4e)"
        )
        assert tuple(r[0] for r in uq_rows) == (
            "agent_id", "version_tag", "artifact", "version",
        ), f"UNIQUE fence columns drifted: {[r[0] for r in uq_rows]}"

        # — named indexes via pg_indexes (parity-contract sets,
        #    mirrored from
        #    tests/migration/test_prompt_versions_index_parity.py) —
        with engine.connect() as conn:
            pv_indexes = {
                r[0] for r in conn.execute(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'prompt_versions'"
                ))
            }
            pa_indexes = {
                r[0] for r in conn.execute(text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'prompt_audit_events'"
                ))
            }
        assert {
            "idx_prompt_versions_key",
            "idx_prompt_versions_key_state",
            "idx_prompt_versions_state_updated",
            # The UNIQUE fence's backing index carries the
            # constraint name on PG.
            "uq_prompt_versions_agent_tag_artifact_version",
        } <= pv_indexes, f"prompt_versions indexes drifted: {sorted(pv_indexes)}"
        assert {
            "idx_prompt_audit_events_key_created",
            "idx_prompt_audit_events_version",
            "idx_prompt_audit_events_type",
        } <= pa_indexes, (
            f"prompt_audit_events indexes drifted: {sorted(pa_indexes)}"
        )

        # — fresh create_all → repo round-trip —
        v = repo.create_draft("dev", "", "soul", "SC9 PG round-trip", internal_actor)
        assert v.state == "draft" and v.version == 1
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v.version_id, http_actor)
        assert repo.get_effective("dev", "", "soul") == "SC9 PG round-trip", (
            "fresh-PG create_all → repo round-trip must resolve the "
            "selected content (SC9)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Phase A T6 / T7 / T8 / T10 — loader-level resolution hook + cache invalidation
# ─────────────────────────────────────────────────────────────────────────────
#
# The DB-layer tests above exercise ``repo.get_effective(...)`` and
# ``repo.get_effective_rows(...)`` directly. The tests below exercise
# the LOADER hook (``resolve_prompts_from_db`` + ``load_and_cache_prompt``
# integration) end-to-end with the real loader functions, real file IO,
# and the synthetic ``db_versions_fingerprint`` cache-bust mechanism.
#
# Fencing: every test creates its own agent directory under ``tmp_path``
# (NEVER live-shaped — 2026-10-12 critical note) and a fresh SQLite
# repo. The hook obtains the repo via ``daemon.loader.set_prompt_version_repo``
# and resets it on teardown so cross-test bleed is impossible.


import logging
from pathlib import Path

from daemon.loader import (
    PromptCache,
    _compute_db_versions_fingerprint,
    _get_prompt_version_repo,
    get_prompt_version_hook_failures,
    load_and_cache_prompt,
    resolve_prompts_from_db,
    set_prompt_version_repo,
)


class _LoaderHookFence:
    """Per-test loader-hook setup/teardown.

    * Registers the repo with the loader's module-level singleton.
    * Resets the hook failure counter so a prior test's failures don't
      leak into the current test's assertions.
    * On exit: clears the singleton + counter.

    Designed as a context manager; tests use ``_LoaderHookFence(repo)``
    in a ``with`` block or as a fixture finalizer.
    """

    def __init__(self, repo) -> None:
        self._repo = repo
        self._prev_repo = _get_prompt_version_repo()
        # Reset the failure counter so prior tests don't pollute.
        # Note: the counter is also reset on __exit__ — tests that
        # assert against the counter must read it BEFORE __exit__
        # fires. The most natural way is ``assert ... in-context``
        # then ``with`` re-enters with the post-bump value.
        from daemon import loader as _loader
        _loader._PROMPT_VERSION_HOOK_FAILURES = 0

    def __enter__(self):
        set_prompt_version_repo(self._repo)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        set_prompt_version_repo(self._prev_repo)
        # Intentionally NOT resetting the counter here — tests
        # typically want to read the post-bump value after the
        # with-block. The next test's __init__ will reset for
        # isolation.


class TestLoaderHookFailOpenA6C7:
    """A6/C7 — named test ``test_hook_db_unreachable_falls_back_to_template``.

    The hook MUST swallow ALL DB exceptions → WARN log → failure counter
    bump → serve the file-loaded prompt unchanged. No retries. This is
    the only new hot-path DB dependency in Phase A (loader.py:822 hook
    insertion).
    """

    def test_hook_db_unreachable_falls_back_to_template(self, tmp_path, caplog):
        # Agent dir with the conservative artifact set written to disk.
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("FILE-SOUL")
        (agent_dir / "rule.md").write_text("FILE-RULE")

        # A repo whose get_effective_rows ALWAYS raises — simulating
        # DB unreachable / pool checkout timeout / etc.
        class _BoomRepo:
            def get_effective_rows(self, *a, **kw):
                raise RuntimeError("simulated DB unreachable")
            def get_effective(self, *a, **kw):
                raise RuntimeError("simulated DB unreachable")

        before_failures = get_prompt_version_hook_failures()
        with _LoaderHookFence(_BoomRepo()):
            with caplog.at_level(logging.WARNING, logger="daemon.loader"):
                prompt, _tokens = load_and_cache_prompt(
                    "agent", agent_dir, PromptCache(),
                )
        # Failure counter bumped at least once — N3 folded the cache-miss
        # path's two ``get_effective_rows`` calls into one, so a single
        # exception path bumps once per ``load_and_cache_prompt`` call.
        bumped = get_prompt_version_hook_failures() - before_failures
        assert bumped >= 1, (
            "FAIL-OPEN hook MUST bump the failure counter on every "
            f"exception (A6/C7) — never swallow silently. bumped={bumped}"
        )
        # WARN logged with the exact required message substring.
        warn_msgs = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.WARNING
        ]
        assert any(
            "prompt-version resolution unavailable" in m for m in warn_msgs
        ), f"expected the named WARN log; got {warn_msgs!r}"
        # File content is served unchanged.
        assert "FILE-SOUL" in prompt, "FAIL-OPEN: soul.md content must be served as-is"
        assert "FILE-RULE" in prompt, "FAIL-OPEN: rule.md content must be served as-is"

    def test_hook_no_db_rows_is_noop(self, tmp_path, repo):
        # A repo with NO rows → hook is a no-op; file content preserved.
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("FILE-SOUL")

        with _LoaderHookFence(repo):
            prompt, _ = load_and_cache_prompt("agent", agent_dir, PromptCache())
        assert "FILE-SOUL" in prompt


class TestLoaderPrecedenceF6:
    """F6 — selected > latest_approved > file (via the loader hook)."""

    def test_resolve_prompts_from_db_selected_wins_over_latest_approved(self, repo, internal_actor, http_actor):
        # Two approved versions; select the older one.
        v1 = repo.create_draft("dev", "", "soul", "selected-older", internal_actor)
        repo.submit_for_approval(v1.version_id, internal_actor)
        repo.approve(v1.version_id, http_actor)
        v2 = repo.create_draft("dev", "", "soul", "latest-approved-newer", internal_actor)
        repo.submit_for_approval(v2.version_id, internal_actor)
        repo.approve(v2.version_id, http_actor)
        repo.set_selection("dev", "", "soul", v1.version_id, http_actor)

        prompts = {"soul": "FILE-SOUL", "rule": "FILE-RULE"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        # Selection wins over latest_approved.
        assert out["soul"] == "selected-older"
        # Untouched file content preserved (rule has no DB row).
        assert out["rule"] == "FILE-RULE"

    def test_resolve_prompts_from_db_latest_approved_wins_over_template_file(self, repo, internal_actor, http_actor):
        # One approved version, no selection set.
        v = repo.create_draft("dev", "", "soul", "DB-APPROVED", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        prompts = {"soul": "FILE-SOUL"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        assert out["soul"] == "DB-APPROVED"

    def test_resolve_prompts_from_db_template_file_fallback_when_no_db_rows(self, repo):
        # No DB rows at all → file content preserved verbatim.
        prompts = {"soul": "FILE-SOUL", "rule": "FILE-RULE"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        assert out == prompts

    def test_resolve_prompts_from_db_carves_out_memory(self, repo, internal_actor, http_actor):
        # memory.md was CARVED OUT in pre-Ud#3 (D-ARTIFACT-DISPOSITION
        # — R7/R11). User Directive #3 (2026-10-14) REMOVED the carve-out
        # for memory.md + the memories/ family: MEMORY-class rows reach
        # the loader with class-distinct precedence (selected > latest
        # > template-file-fallback). The carve-out still applies to
        # ``user`` / ``growth`` — they are NEITHER loader-prompt-visible
        # NOR prompt-key in the loader's prompts dict, so the loader
        # naturally no-ops them via the missing-dict-key lookup. This
        # test now exercises the new MEMORY behavior end-to-end.
        v = repo.create_draft("dev", "", "memory", "DB-MEM-DRAFT", internal_actor)
        # MEMORY rows are created via create_memory_version (transition-free);
        # create_draft lands the row in 'draft' but doesn't transition.
        # Use create_memory_version to land the row in 'approved' (born-effective).
        # The test: with a MEMORY row present, the loader resolves the
        # artifact via the DB selection/latest chain; without, the file
        # fallback fires.
        v_mem = repo.create_memory_version(
            "dev", "", "memory", "DB-MEM", internal_actor,
        )
        # NOTE: v (the draft created above) stays in 'draft' — for the
        # MEMORY path the test only needs v_mem to be effective.

        prompts = {"soul": "FILE-SOUL", "memory": "FILE-MEM"}
        out = resolve_prompts_from_db(prompts, "dev", "", repo)
        # MEMORY: latest row content beats the file fallback (the
        # memory.md file is "FILE-MEM" but DB MEMORY row carries
        # "DB-MEM" — the loader takes DB content as effective).
        assert out["memory"] == "DB-MEM"
        # Other artifacts unaffected.
        assert out["soul"] == "FILE-SOUL"


class TestLoaderNoDBSnapshotGoldenSC7:
    """SC7 golden — no-DB-rows yields identical prompt to pre-Phase-A.

    The loader's hook MUST be a strict no-op when no DB rows exist for
    the (agent_id, version_tag) pair. Verified by snapshot-comparing a
    generated system prompt against the pre-Phase-A file-only loader
    path. Byte-equal (or whitespace-trimmed equal).
    """

    def test_no_db_rows_yields_identical_prompt_to_pre_phase_a(
        self, tmp_path, repo,
    ):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("# Soul\nFile soul content.")
        (agent_dir / "rule.md").write_text("# Rule\nFile rule content.")
        (agent_dir / "workflow.md").write_text("# Workflow\nFile workflow content.")
        (agent_dir / "memory.md").write_text("# Memory\nFile memory content.")

        cache = PromptCache()

        # Path A — Phase A with the hook ACTIVE and a real repo (no rows).
        with _LoaderHookFence(repo):
            prompt_a, tokens_a = load_and_cache_prompt(
                "agent", agent_dir, cache, version_tag="",
            )

        # Path B — pre-Phase-A path: same agent, same files, hook
        # effectively disabled (no repo registered). The loader falls
        # through to the file-only behavior.
        set_prompt_version_repo(None)
        cache_b = PromptCache()
        prompt_b, tokens_b = load_and_cache_prompt(
            "agent", agent_dir, cache_b, version_tag="",
        )

        # Whitespace-trimmed equality (no spurious newlines from the
        # hook path).
        assert prompt_a.strip() == prompt_b.strip(), (
            f"SC7 violated — hook changed the no-DB-rows prompt:\n"
            f"  A: {prompt_a!r}\n  B: {prompt_b!r}"
        )
        assert tokens_a == tokens_b


class TestLoaderCacheInvalidateSC4:
    """SC4 — PromptCache.invalidate clears every key variant.

    Parametrized over (base + tagged) × (single + multiple mcp variants).
    """

    @pytest.mark.parametrize(
        "agent_id,version_tag,mcp_variants",
        [
            ("developer", None, []),
            ("developer", None, ["mcp_a"]),
            ("developer", None, ["mcp_a", "mcp_b", "mcp_c"]),
            ("developer", "v2", []),
            ("developer", "v2", ["mcp_a"]),
            ("developer", "v2", ["mcp_a", "mcp_b"]),
            ("coder", "", ["mcp_only"]),
        ],
        ids=[
            "base-no-mcp",
            "base-single-mcp",
            "base-multi-mcp",
            "tagged-no-mcp",
            "tagged-single-mcp",
            "tagged-multi-mcp",
            "different-agent",
        ],
    )
    def test_invalidate_respects_version_tag(
        self, agent_id, version_tag, mcp_variants,
    ):
        cache = PromptCache()
        # Seed cache entries under the relevant keys.
        for mcp in [None, *([] if not mcp_variants else [mcp_variants])]:
            # mcp=None seeds the no-mcp variant.
            # mcp=mcp_variants (a list) seeds the multi-mcp variant.
            cache.set(
                agent_id, f"prompt-{mcp}", 42, {"x.md": 1.0},
                mcp_tool_names=mcp, version_tag=version_tag,
            )

        # All seeded entries should be present.
        if not mcp_variants:
            assert cache.get(agent_id, None, version_tag) is not None
        else:
            assert cache.get(agent_id, mcp_variants, version_tag) is not None

        # Invalidate the (agent_id, version_tag) pair.
        cache.invalidate(agent_id, mcp_tool_names=mcp_variants, version_tag=version_tag)

        # All seeded entries MUST be gone.
        assert cache.get(agent_id, mcp_variants, version_tag) is None, (
            "invalidate MUST bust every MCP variant for the same "
            "(agent_id, version_tag) pair (SC4)"
        )

    def test_invalidate_base_does_not_bust_tagged(self):
        # F9 / D15 — the keystone invariant: invalidating the base key
        # MUST NOT touch the tagged key (and vice versa).
        cache = PromptCache()
        cache.set("dev", "base-prompt", 1, {}, mcp_tool_names=None, version_tag=None)
        cache.set("dev", "tagged-prompt", 2, {}, mcp_tool_names=None, version_tag="v2")

        cache.invalidate("dev", mcp_tool_names=None, version_tag=None)
        # Base gone, tagged still present.
        assert cache.get("dev", None, None) is None
        assert cache.get("dev", None, "v2") is not None, (
            "F9 keystone violated — base invalidate touched the tagged key"
        )


class TestLoaderDriftSC1T8:
    """SC1 / T8 — template file drift does not affect the resolved prompt.

    (a) no DB rows → byte-identical today-behavior;
    (b) override approved mid-session → restored instance sees NEW
        approved content (human-gated);
    (c) overridden artifacts → no on-disk drift (DB wins over file mtime).
    """

    def test_template_file_mtime_drift_after_spawn_does_not_affect_prompt(
        self, tmp_path, repo, internal_actor, http_actor,
    ):
        # Approve one DB version, then call spawn → cache stores the
        # DB-resolved content. Mutate the template file's mtime.
        # Subsequent cache-hit MUST still return the DB content
        # (cache self-heals on mtime change but the DB fingerprint
        # keeps the resolved version stable).
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        soul_file = agent_dir / "soul.md"
        soul_file.write_text("FILE-SOUL-ORIGINAL")
        v = repo.create_draft("dev", "", "soul", "DB-APPROVED", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        with _LoaderHookFence(repo):
            cache = PromptCache()
            prompt1, _ = load_and_cache_prompt("dev", agent_dir, cache)
            assert "DB-APPROVED" in prompt1

            # Mutate the template file mtime + content.
            time.sleep(0.05)
            soul_file.write_text("FILE-SOUL-MUTATED")

            # Call again — mtime changed, cache miss → reload from file
            # but the hook replaces soul with DB content.
            prompt2, _ = load_and_cache_prompt("dev", agent_dir, cache)
            assert "DB-APPROVED" in prompt2, (
                "DB-resolved content MUST win over the mutated file"
            )
            assert "FILE-SOUL-MUTATED" not in prompt2, (
                "DB version wins over file mtime drift (SC1 case c)"
            )

    def test_restore_does_not_drift_after_template_mtime_change(
        self, tmp_path, repo, internal_actor, http_actor,
    ):
        # T8 / SC3 / S19 case (c): restore path returns DB-resolved
        # content, not the on-disk file. Mutate agents/<x>/soul.md
        # between a "spawn" call and a "restore" call → restored
        # prompt unchanged.
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("FILE-SOUL-ORIGINAL")
        v = repo.create_draft("dev", "", "soul", "DB-APPROVED", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        with _LoaderHookFence(repo):
            cache = PromptCache()

            # First "spawn" — DB content gets compiled.
            spawn_prompt, _ = load_and_cache_prompt("dev", agent_dir, cache)

            # File drift between spawn and restore (different
            # intermediate state — file mtime + content change).
            time.sleep(0.05)
            (agent_dir / "soul.md").write_text("FILE-SOUL-DRIFTED")

            # "Restore" call — uses the same loader entry point
            # (instance_lifecycle._restore_instance → load_and_cache_prompt).
            restore_prompt, _ = load_and_cache_prompt("dev", agent_dir, cache)

            assert spawn_prompt == restore_prompt, (
                "T8 / S19 case (c) violated — restore saw file-drifted content"
            )
            assert "DB-APPROVED" in restore_prompt
            assert "FILE-SOUL-DRIFTED" not in restore_prompt

    def test_template_file_deletion_after_db_version_present_still_spawns_with_db_content(
        self, tmp_path, repo, internal_actor, http_actor,
    ):
        # SC1 — the keystone drift test: delete the template file
        # AFTER a DB version is approved. Spawn must still see the DB
        # content (no template fallback for overridden keys).
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        soul_file = agent_dir / "soul.md"
        soul_file.write_text("FILE-SOUL")
        v = repo.create_draft("dev", "", "soul", "DB-APPROVED", internal_actor)
        repo.submit_for_approval(v.version_id, internal_actor)
        repo.approve(v.version_id, http_actor)

        with _LoaderHookFence(repo):
            cache = PromptCache()
            # Delete the template file.
            soul_file.unlink()

            prompt, _ = load_and_cache_prompt("dev", agent_dir, cache)
            assert "DB-APPROVED" in prompt, (
                "SC1 violated — file deletion caused prompt to fall back "
                "to empty / fail-loud; DB content MUST win"
            )


class TestLoaderFingerprintC1:
    """C1 — synthetic ``db_versions_fingerprint`` busts the cache on
    every DB change.

    Two calls of ``load_and_cache_prompt`` with the SAME files but
    DIFFERENT DB state must NOT hit the cache — the fingerprint changes.
    """

    def test_fingerprint_busts_cache_on_db_state_change(
        self, tmp_path, repo, internal_actor, http_actor,
    ):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("FILE-SOUL")

        with _LoaderHookFence(repo):
            cache = PromptCache()

            # First call: no DB rows → fingerprint = "" (N4 unified
            # sentinel — empty rows == missing repo == ""; no
            # sha256("") boot-moment cache bust).
            prompt1, _ = load_and_cache_prompt("dev", agent_dir, cache)
            assert "FILE-SOUL" in prompt1

            # Approve a DB version — fingerprint MUST change.
            v = repo.create_draft("dev", "", "soul", "DB-APPROVED", internal_actor)
            repo.submit_for_approval(v.version_id, internal_actor)
            repo.approve(v.version_id, http_actor)

            # Second call: file mtime unchanged BUT DB fingerprint
            # changed → cache MUST miss → resolve from DB.
            prompt2, _ = load_and_cache_prompt("dev", agent_dir, cache)
            assert "DB-APPROVED" in prompt2, (
                "C1 violated — cache served stale file content despite DB change"
            )

    def test_fingerprint_is_pure_function_of_rows(self):
        # Unit test on the pure-function fingerprint helper: same
        # rows → same fingerprint, regardless of call order.
        from daemon.repositories.prompt_version.repository import EffectiveRow

        rows_a = [
            EffectiveRow(artifact="soul", selected_version_id="s1", latest_approved_version_id="a1"),
            EffectiveRow(artifact="rule", selected_version_id=None, latest_approved_version_id="a2"),
        ]
        rows_b = [
            EffectiveRow(artifact="rule", selected_version_id=None, latest_approved_version_id="a2"),
            EffectiveRow(artifact="soul", selected_version_id="s1", latest_approved_version_id="a1"),
        ]
        assert _compute_db_versions_fingerprint(rows_a) == _compute_db_versions_fingerprint(rows_b)


class TestLoaderPromptCacheInvalidateRespectsTagsT7D3:
    """T7 — the 7 invalidate-thread sites pass ``version_tag`` correctly.

    Lightweight grep-level + behavioral verification: each of the 7
    invalidate / cache.get sites calls into ``PromptCache.{invalidate,get}``
    with ``version_tag`` set (so per-tag keys are busted / read).
    """

    def test_invalidate_call_sites_thread_version_tag(self):
        # Inspect the source of each writer. The agents/inner_soul.py
        # sites are post-shim (line numbers drifted from the plan per
        # the brief); we grep for ``invalidate(agent_id, version_tag=``
        # at every writer. The exact count + locations are recorded
        # in the coder report.
        import re
        from pathlib import Path

        # (a) agent_mother — RETIRED direct-write path (User Directive
        # #3, 2026-10-14, RESOLUTION-CORE). The previously-direct
        # writers (memory.md, growth.md) now route through the
        # shared ``_submit_prompt_version`` /
        # ``_submit_memory_version`` helpers in inner_soul.py, which
        # own the prompt_cache.invalidate(agent_id, version_tag=)
        # call. agent_mother's contract is now "submit a DB row +
        # return the row identity"; the cache-bust is the helper's
        # responsibility. The grep below now expects 0 agent_mother
        # sites (the direct-write path is dead code; agent_mother.py
        # returns a defensive refusal if ever reached there).
        am = Path("daemon/tools/agent_mother.py").read_text()
        am_calls = re.findall(
            r"prompt_cache\.invalidate\(",  # noqa: E501
            am,
        )
        assert len(am_calls) == 0, (
            f"agent_mother must NOT call prompt_cache.invalidate "
            f"directly anymore (User Directive #3, 2026-10-14 — all "
            f"writers route through inner_soul's shared helpers); "
            f"saw {am_calls!r}"
        )

        # (b) inner_soul — version_tag threading at every invalidate
        # site. Phase B T7 consolidated the soul/user/workflow
        # invalidation into the shared ``_submit_prompt_version``
        # helper. User Directive #3 added the MEMORY helper
        # ``_submit_memory_version`` (also threads version_tag).
        # The PHYSICAL count is 2 (shared CORE helper + shared
        # MEMORY helper) covering the 5 LOGICAL writer sites; each
        # still threads ``version_tag=version_tag``. The functional
        # per-tag bust is behaviorally pinned by
        # ``tests/test_inner_soul_db.py::TestInvalidateThreadsVersionTag``.
        is_mod = Path("daemon/tools/inner_soul.py").read_text()
        is_calls = re.findall(
            r"prompt_cache\.invalidate\(\s*agent_id,\s*version_tag=version_tag\s*\)",  # noqa: E501
            is_mod,
        )
        assert len(is_calls) == 2, (
            f"inner_soul invalidate sites must ALL thread "
            f"version_tag=version_tag (Ud#3: 2 physical sites — "
            f"shared _submit_prompt_version + shared "
            f"_submit_memory_version — covering the 5 logical "
            f"writers); saw {len(is_calls)}"
        )
        # No invalidate call may omit version_tag.
        bare_calls = re.findall(
            r"prompt_cache\.invalidate\(\s*agent_id\s*\)", is_mod,
        )
        assert not bare_calls, (
            f"inner_soul must have NO base-key-only invalidate calls; "
            f"saw {bare_calls!r}"
        )

        # (c) instance_messaging._get_system_prompt_tokens — drops
        # version_tag today; the fix threads it via meta.agent_tag.
        im = Path("daemon/services/instance_messaging.py").read_text()
        # Find the get(...) call inside _get_system_prompt_tokens.
        # Should now include ``version_tag=version_tag``.
        assert "_prompt_cache.get(meta.agent_id, mcp_tool_names, version_tag=version_tag)" in im, (
            "instance_messaging._get_system_prompt_tokens must thread "
            "version_tag into the prompt_cache.get() call"
        )


class TestPersistenceReconstructionPath:
    """O-14 two-layer check — persistence.py reconstruction path uses
    the resolution hook.

    The reconstruction caller at ``persistence.py:1141`` already calls
    ``load_and_cache_prompt`` with the resolved ``version_tag``. T6's
    hook makes that call return the DB-resolved prompt for overridden
    artifacts. Verified at the SOURCE layer (grep) — the call passes
    ``version_tag=version_tag`` (NOT drops it), so the hook fires.
    """

    def test_persistence_reconstruction_passes_version_tag(self):
        from pathlib import Path
        src = Path("daemon/persistence.py").read_text()
        # The reconstruction call site at line 1141 (current line
        # after this commit) must include version_tag=version_tag.
        # Grep for the call pattern. Phase C (User Directive #5A,
        # 2026-10-14) added the ``is_custom_agent`` kwarg; the test
        # was last updated for the pre-Phase-C source shape and now
        # allows the additional kwarg for forward compatibility.
        assert (
            "load_and_cache_prompt(\n"
            "        agent_id=agent_id,\n"
            "        agent_dir=agent_dir,\n"
            "        cache=prompt_cache,\n"
            "        mcp_tool_names=mcp_tool_names,\n"
            "        version_tag=version_tag,\n"
            "        is_custom_agent=_is_custom_persist,\n"
            "    )"
        ) in src, (
            "persistence.py:1141 reconstruction MUST pass "
            "version_tag=version_tag into load_and_cache_prompt "
            "so the T6 hook fires for restored instances"
        )


class TestLoaderLatencySC10:
    """SC10 — no regression in spawn-time / restore-time / message-
    reconstruction-time prompt latency on the cache-hit path.

    Light sanity check (±10% tolerance). Reports the numbers in the
    final coder report; does NOT hard-gate on absolute values.
    """

    def test_cache_hit_path_latency_under_threshold(self, tmp_path):
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "soul.md").write_text("X")
        (agent_dir / "rule.md").write_text("Y")

        cache = PromptCache()
        # First call — cache miss (relatively slow path).
        import time as _t
        first_ms = _measure_ms(lambda: load_and_cache_prompt(
            "agent", agent_dir, cache,
        ))

        # Subsequent calls — cache hits. Average over a few.
        hits = [
            _measure_ms(lambda: load_and_cache_prompt(
                "agent", agent_dir, cache,
            ))
            for _ in range(20)
        ]
        hit_avg_ms = sum(hits) / len(hits)
        # The cache-hit path MUST be at least as fast as the first call
        # (cache wins). Tolerance: allow the hit path to be up to 10%
        # SLOWER only if the absolute number is tiny (sub-millisecond
        # measurements are noisy). Real expectation: hit < first.
        assert hit_avg_ms <= first_ms * 1.10, (
            f"SC10 violated — cache-hit path slower than cache-miss: "
            f"first={first_ms:.2f}ms hit_avg={hit_avg_ms:.2f}ms"
        )


def _measure_ms(fn) -> float:
    """Measure wall-clock for ``fn`` in milliseconds."""
    import time
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


# Helper for the Session.get audit query inside the test.
from sqlmodel import Session

# ─────────────────────────────────────────────────────────────────────────────
# User Directive 2026-10-14 — clean-start narrative (1× sqlite, NOT parametrized)
#
# Verifies the F1 OVERRIDES-ONLY default end-to-end against an EMPTY DB:
# the fresh prompt-version store must be served from the template file
# until the user submits + approves a draft. Combined narrative of the
# existing TestPrecedence + TestSoulSubmissionCreatesPendingRow +
# TestApprovedContentBecomesCompositionBase patterns, asserting at each
# step:
#   * DB row count is zero before any submission (empty-slate);
#   * pre-submission effective content == template file content (no
#     stale state leaks from any prior path);
#   * post-approval effective content != file (DB row wins on the chain).
#
# Parametrization deliberately omitted (the chain holds on either driver;
# the existing parametrized precedence tests already cover both).
# ─────────────────────────────────────────────────────────────────────────────


from unittest.mock import MagicMock, patch

from daemon.registry import AgentMetadata
from daemon.tools.inner_soul import create_inner_soul_tool


class TestUserDirectiveCleanStart:
    """F1 + D-SEED-EDGE-RESOLVED-DEAD narrative (directive 2026-10-14).

    Walks the resolution chain against a fresh empty prompt-version
    DB and proves NO stale state leaks (no rows → file fallback;
    submitted draft → row version=1, file untouched; approve →
    effective content comes from DB, not the file).
    """

    def test_empty_db_template_then_submit_then_approve_serves_db(
        self, repo_engine, repo
    ):
        engine, _ = repo_engine  # sqlite-only by fixture filter

        # Step 0 — assert empty-slate provenance (DB row count == 0).
        initial_rows = repo.list_versions("sweptagent", "", "soul")
        assert initial_rows == [], (
            f"Empty-slate invariant: DB must start with zero rows. "
            f"Found {len(initial_rows)} pre-existing rows."
        )
        assert repo.get_effective("sweptagent", "", "soul") is None, (
            "Empty-slate: no DB rows → effective MUST return None "
            "(file fallback path fires, no DB content leaks)."
        )

        # Step 1 — build the runtime surface (tmp agent dir + manager stub).
        from pathlib import Path

        import tempfile
        from daemon.loader import PromptCache
        from daemon.services.prompt_version_service import PromptVersionService

        tmp = Path(tempfile.mkdtemp())
        (tmp / "soul.md").write_text("# Who I Am\n\nI am a swept agent.\n")
        template_content = (tmp / "soul.md").read_text()

        # Pre-submission effective == file content (no DB rows).
        pre_effective = repo.get_effective("sweptagent", "", "soul")
        # The resolver returns None for the zero-row case; assert that
        # composition then serves the file unchanged.
        assert pre_effective is None
        # File IS the pre-submission source (no surprises).
        assert (tmp / "soul.md").read_text() == template_content

        # Step 2 — submit a draft via the real inner_soul tool path.
        service = PromptVersionService(repo=repo)
        manager_stub = MagicMock()
        # A real PromptCache (not None/Mock) so the post-submission
        # prompt_cache.invalidate busts deterministically (the tool
        # path expects a dict-like object with an ``invalidate`` method).
        manager_stub.prompt_cache = PromptCache()
        manager_stub._prompt_version_service = service

        registry_mock = MagicMock()
        metadata = AgentMetadata(
            id="sweptagent",
            name="Swept Agent",
            description="Test agent for clean-start narrative",
            path=tmp,
            system=False,
        )
        registry_mock.get_version.return_value = metadata
        registry_mock.get_resolved.return_value = metadata

        tool = create_inner_soul_tool(
            manager_stub, "sweptagent", "inst-directive-1"
        )

        soul_file = tmp / "soul.md"
        file_before_mtime = soul_file.stat().st_mtime_ns

        # Phrasing matches the soul name-change mutator pattern
        # ("My name is …") so the content assertion is on the
        # established test_inner_soul_db.py convention (Phase B T7).
        result = tool.invoke({
            "request": "My name is CleanStart",
            "target": "soul",
        })

        # The tool response grammar is preserved (📋 / pending).
        assert "📋 soul:" in result
        assert "pending approval" in result

        # The DB row materialized as version=1, state=pending_approval,
        # with the new name bolded inside the composed content (the
        # soul name-change mutator mutates "My name is" into the
        # bolded form on the effective chain).
        rows = repo.list_versions("sweptagent", "", "soul")
        assert len(rows) == 1
        assert rows[0].version == 1
        assert rows[0].state == "pending_approval"
        assert "**My name is CleanStart**" in rows[0].content, (
            f"Soul name-change mutator must bold the new name. "
            f"Content: {rows[0].content!r}"
        )
        assert rows[0].submitter_instance_id == "inst-directive-1"

        # The file is UNTOUCHED (no write side-effect — DB-only draft).
        assert soul_file.read_text() == template_content
        assert soul_file.stat().st_mtime_ns == file_before_mtime

        # Pre-approval effective is STILL file-content (pending row is NOT
        # effective — F6 precedence: only approved/selected rows resolve).
        pre_approval_effective = repo.get_effective("sweptagent", "", "soul")
        assert pre_approval_effective is None, (
            "Pre-approval effective MUST stay None (pending rows are "
            "not effective — only approved/selected resolve). "
            "Empty-slate provenance is preserved through submission."
        )

        # Step 3 — human approve via session lane (the ONLY lane allowed).
        import asyncio

        human = ActorContext(
            subject="user@example.com", auth_method="session"
        )
        approved = asyncio.run(
            service.approve(rows[0].version_id, human)
        )
        assert approved.state == "approved"

        # Step 4 — post-approval effective now serves from DB (NOT file).
        post_approval_effective = repo.get_effective(
            "sweptagent", "", "soul"
        )
        assert post_approval_effective == rows[0].content, (
            f"Post-approval effective MUST equal approved DB row "
            f"content (F6 precedence: latest_approved > file). "
            f"Expected: {rows[0].content!r}, got: "
            f"{post_approval_effective!r}"
        )
        assert post_approval_effective != template_content, (
            "Empty-slate invariant: post-approval effective MUST differ "
            "from the template file. If they match, the resolver is "
            "still serving file and ignoring the DB row — REGRESSION."
        )

        # Step 5 — empty-slate still proven: the non-soul artifact has
        # ZERO rows AND effective=None (no fall-through from the soul
        # draft to unrelated artifacts).
        assert repo.list_versions("sweptagent", "", "workflow") == []
        assert repo.get_effective("sweptagent", "", "workflow") is None
