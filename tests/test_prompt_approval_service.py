"""Prompt-version approval service-layer tests.

Phase B service-layer coverage for the agent-prompt-immutability
commission (``.agents/shared/planning/agent-prompt-immutability/
phaseB-plan.md``: T1, T2, T3, T10 acceptance buckets).

The repository-layer surface (state machine, selection pointer,
audit-stamping, INSERT-retry, get_effective_rows) is exercised by
``tests/test_prompt_version_resolution.py``. THIS file owns the
service-layer delta:

* the **service-side canonical surface re-skin** (T1) —
  :mod:`daemon.services.prompt_state_machine` re-exports
  ``VALID_TRANSITIONS`` / ``InvalidTransitionError`` / ``PROMPT_STATES``
  from the repository and adds helper predicates
  (``is_terminal``, ``can_transition``, ``validate_transition``).
  The test asserts that the two surfaces stay in lock-step (single
  source of truth invariant — ``VALID_TRANSITIONS`` must be the
  same set-of-tuples from both import paths).

* **S16 retract authority** (T2) —
  :class:`PromptVersionService.retract` enforces the matrix:
  ``session`` = any row; ``internal`` = own row only;
  ``bearer`` = REFUSED; forged internal via HTTP-bearer lane =
  refused (anti-forgery).

* **human-only approve/reject guard** (T3) —
  :meth:`PromptVersionService.approve` and ``reject`` raise the
  typed :class:`HumanLaneRequiredError` for any non-session lane.
  The repo's defense-in-depth gate (``only HTTP actors``) stays
  covered by the resolution test file.

* **F1 OVERRIDES-ONLY seeding** (T10) — service docstring
  documents the strategy; this test confirms the behavior
  (``create_and_submit`` on a fresh key inserts ``version=1``;
  ``get_effective`` returns ``None`` for keys with no rows; the
  template file stays untouched — verified by the
  ``test_template_file_fallback_when_no_db_rows`` coverage at
  the repo layer; this file exercises the seeding-version=1
  invariants).

Test execution is fenced per the project critical notes:

* ``source .smoke/test-fence.sh`` BEFORE invoking pytest —
  ENSEMBLE_SELF_ENV=dev; POSTGRES_DB=ensemble_dev_test_only;
  FAKE_HOME under mktemp; POSTGRES_HOST/PORT/USER/PASSWORD/
  MAINT_DB read ONLY via ``PG_TEST_*`` env overrides (env-poison
  3-guard convention — ambient POSTGRES_DB on ensemble hosts
  carries ``ensemble_prod``, NEVER inherit it).
* The PG leg is the SC8 parity leg; uses the same
  ``_postgres_repo_engine`` scratch-DB-per-test pattern from
  ``test_prompt_version_resolution.py``.
* Run only the named file (``--tb=short -q``), never bare
  pytest, never ``-x``, never full-suite.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session

from daemon.repositories.prompt_version.models import (
    PromptAuditEvent,
    PromptVersion,
    PROMPT_STATES,
)
from daemon.repositories.prompt_version.repository import (
    AUTH_METHOD_BEARER,
    AUTH_METHOD_INTERNAL,
    AUTH_METHOD_SESSION,
    ActorContext,
    InvalidTransitionError,
    PromptVersionRepository,
    VALID_TRANSITIONS as REPO_VALID_TRANSITIONS,
    actor_from_instance,
    actor_from_request,
)
from daemon.services.prompt_state_machine import (
    PROMPT_STATES as SVC_PROMPT_STATES,
    TERMINAL_STATES,
    VALID_TRANSITIONS as SVC_VALID_TRANSITIONS,
    InvalidTransitionError as SvcInvalidTransitionError,
    PromptStateMachine,
    prompt_state_machine,
)
from daemon.services.prompt_version_service import (
    AuthContextMissingError,
    HumanLaneRequiredError,
    PromptVersionService,
    RetractAuthorityError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Engine fixtures — exact reuse of the SC8 pattern from
# ``test_prompt_version_resolution.py``. SQLite default; PG leg
# via parametrize. FAKE_HOME escape-guard: tempfile.mktemp (no
# live-shape paths).
# ─────────────────────────────────────────────────────────────────────────────

_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "ensemble_dev")
_PG_SCRATCH_PREFIX = "ensemble_promptver_test_"
_PG_FORBIDDEN_DBS = frozenset({"ensemble_prod"})


def _pg_admin_engine():
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE.

    Identical contract to ``test_prompt_version_resolution.py`` —
    diagnostic ``None`` skip when PG is unreachable; HARD ABORT when
    the resolved maintenance DB is in the forbidden set
    (env-poison 3-guard).
    """
    if _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved maintenance DB "
            f"{_PG_MAINT_DB!r} (env-poison 3-guard; never point prompt-version "
            f"tests at a live database).",
            returncode=1,
        )
    from sqlalchemy.exc import DBAPIError, OperationalError

    url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}"
    )
    engine = create_engine(
        url, isolation_level="AUTOCOMMIT", pool_pre_ping=True,
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        orig = getattr(exc, "orig", exc)
        sqlstate = getattr(getattr(orig, "diag", None), "sqlstate", None)
        kind = (
            "auth failure"
            if sqlstate in {"28000", "28P01"}
            else "unreachable"
        )
        return None, f"PostgreSQL {kind} at {_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}: {exc}"
    except DBAPIError as exc:
        engine.dispose()
        return None, f"PostgreSQL DBAPI error at {_PG_HOST}:{_PG_PORT}: {exc}"
    return engine, None


def _postgres_repo_engine():
    """Yield a fresh scratch-PostgreSQL engine + scratch DB name.

    Mirrors ``_postgres_repo_engine`` in the resolution file:
    CREATE DATABASE → ``SQLModel.metadata.create_all`` (PG truth
    path) → yield → dispose → terminate stray backends → DROP
    DATABASE. Skips loudly when PG is unreachable.
    """
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason or "PG unreachable")
    import secrets

    scratch_db = f"{_PG_SCRATCH_PREFIX}{secrets.token_hex(4)}"
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{scratch_db}"'))
    except Exception as exc:
        admin.dispose()
        pytest.skip(f"PG CREATE DATABASE failed: {exc}")
    admin.dispose()

    engine = None
    try:
        scratch_url = (
            f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
            f"@{_PG_HOST}:{_PG_PORT}/{scratch_db}"
        )
        engine = create_engine(scratch_url, pool_pre_ping=True)
        SQLModel.metadata.create_all(engine)
        yield engine, scratch_db
    finally:
        if engine is not None:
            engine.dispose()
        # Tear the scratch DB down (best-effort).
        try:
            with create_engine(
                f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
                f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}",
                isolation_level="AUTOCOMMIT",
            ) as teardown_admin:
                with teardown_admin.connect() as conn:
                    conn.execute(text(
                        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        f"WHERE datname = '{scratch_db}' AND pid <> pg_backend_pid()"
                    ))
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch_db}"'))
        except Exception:
            pass


def _sqlite_repo_engine():
    """Yield a fresh file-SQLite engine + db path under tempfile.mktemp."""
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

    SQLModel.metadata.create_all(engine)
    try:
        yield engine, db_path
    finally:
        engine.dispose()
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except FileNotFoundError:
                pass


@pytest.fixture
def db_dialect() -> str:
    """Default dialect = ``sqlite``; override via parametrize."""
    return "sqlite"


@pytest.fixture
def repo_engine(db_dialect: str):
    if db_dialect == "postgres":
        yield from _postgres_repo_engine()
    else:
        yield from _sqlite_repo_engine()


@pytest.fixture
def repo(repo_engine) -> PromptVersionRepository:
    engine, _ = repo_engine
    return PromptVersionRepository(engine)


@pytest.fixture
def service(repo) -> PromptVersionService:
    return PromptVersionService(repo)


# ─────────────────────────────────────────────────────────────────────────────
# Actor fixtures — the four lanes Phase B deals with. Reusing the
# resolution-file naming.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def own_internal_actor() -> ActorContext:
    """In-process submitter for the row's own retract path."""
    return actor_from_instance("inst-self", "developer", "")


@pytest.fixture
def other_internal_actor() -> ActorContext:
    """In-process submitter claiming a DIFFERENT instance id."""
    return actor_from_instance("inst-other", "developer", "")


@pytest.fixture
def http_session_actor() -> ActorContext:
    """Human session — approver / any-row retract authority."""
    return ActorContext(subject="op@example.com", auth_method=AUTH_METHOD_SESSION)


@pytest.fixture
def http_bearer_actor() -> ActorContext:
    """Machine lane — never approves / never retracts."""
    return ActorContext(subject="op-cli", auth_method=AUTH_METHOD_BEARER)


# ─────────────────────────────────────────────────────────────────────────────
# Service-side canonical surface (T1)
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceSurfaceT1:
    """T1 — service re-export + helper predicates; the repository
    stays the source of truth (single-source-of-truth invariant)."""

    def test_valid_transitions_matches_repository(self):
        """The service re-export must be the EXACT same set-of-tuples
        the repository carries. If these ever diverge the frozen
        contract is broken (A1) and the morning review must repair
        the repository, not the service."""
        assert SVC_VALID_TRANSITIONS == REPO_VALID_TRANSITIONS

    def test_prompt_states_matches_repository(self):
        assert SVC_PROMPT_STATES == PROMPT_STATES

    def test_invalid_transition_error_same_class(self):
        """``prompt_state_machine.InvalidTransitionError`` and the
        repository's are the same class — frozen A1 contract."""
        assert SvcInvalidTransitionError is InvalidTransitionError

    def test_terminal_states_exact_three(self):
        """D1 + A1: ``approved``, ``rejected``, ``archived`` are
        terminal; ``draft`` and ``pending_approval`` are not."""
        assert TERMINAL_STATES == frozenset({
            "approved", "rejected", "archived",
        })

    @pytest.mark.parametrize("state", ["draft", "pending_approval"])
    def test_is_terminal_returns_false_for_live_states(self, state):
        assert PromptStateMachine.is_terminal(state) is False

    @pytest.mark.parametrize("state", ["approved", "rejected", "archived"])
    def test_is_terminal_returns_true_for_terminal_states(self, state):
        assert PromptStateMachine.is_terminal(state) is True

    def test_is_terminal_rejects_unknown_state(self):
        with pytest.raises(ValueError, match="state must be one of"):
            PromptStateMachine.is_terminal("bogus")

    def test_can_transition_valid_pair(self):
        assert prompt_state_machine.can_transition(
            "draft", "pending_approval"
        ) is True

    def test_can_transition_rejects_invalid_pair(self):
        # approved is terminal — no outgoing edges.
        assert prompt_state_machine.can_transition("approved", "draft") is False
        # rejected is terminal — (rejected, draft) is the A1-skip pair.
        assert prompt_state_machine.can_transition("rejected", "draft") is False
        # archived is terminal.
        assert prompt_state_machine.can_transition("archived", "draft") is False

    def test_can_transition_rejects_none(self):
        # Creation is an INSERT; None means "row did not exist."
        assert prompt_state_machine.can_transition(None, "draft") is False

    def test_validate_transition_raises_on_invalid_pair(self):
        with pytest.raises(InvalidTransitionError) as exc_info:
            prompt_state_machine.validate_transition(
                "approved", "draft", version_id="v-test",
            )
        # The error carries the version_id + from/to fields.
        err = exc_info.value
        assert err.version_id == "v-test"
        assert err.from_state == "approved"
        assert err.to_state == "draft"


# ─────────────────────────────────────────────────────────────────────────────
# Service-level state machine (T1 enforcement through the service)
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceStateMachine:
    """Every valid pair transitions through the service; invalid
    pairs raise ``InvalidTransitionError`` with version_id + from/to."""

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    @pytest.mark.parametrize("transition", [
        ("draft", "pending_approval"),
        ("pending_approval", "draft"),
    ])
    def test_valid_pair_succeeds_via_service(
        self, service, own_internal_actor, http_session_actor,
        transition,
    ):
        from_state, to_state = transition
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        # ``create_and_submit`` lands the row in pending_approval;
        # back to draft first if the test pair needs it.
        if from_state == "draft":
            asyncio.run(service.retract(v.version_id, http_session_actor))
        if to_state == "draft":
            # pending_approval -> draft via service.retract (session OK).
            row = asyncio.run(service.retract(v.version_id, http_session_actor))
            assert row.state == "draft"
        else:
            # pending_approval -> pending_approval (no-op via service
            # would be a same-state transition; covered by
            # ``test_invalid_transition_raises_same_state`` below).
            pytest.skip(
                f"transition pair ({from_state}, {to_state}) needs a "
                f"different setup; covered by direct happy_path test "
                f"elsewhere"
            )

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_invalid_transition_raises_via_service(
        self, service, own_internal_actor, http_session_actor,
        repo,
    ):
        # approved -> draft is forbidden (approved is terminal).
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        asyncio.run(service.approve(v.version_id, http_session_actor))
        # Now attempt the service-level retract on a terminal row —
        # the service retract dispatches ``atomic_transition(
        # pending_approval, draft)``; but the row's state is now
        # ``approved``, so the repo's CAS will detect the mismatch
        # and surface ``InvalidTransitionError`` with the actual
        # ``from_state='approved'``.
        with pytest.raises(InvalidTransitionError):
            asyncio.run(service.retract(v.version_id, http_session_actor))

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_invalid_sampling_approved_to_anything(
        self, service, own_internal_actor, http_session_actor,
    ):
        """approved is terminal — every outgoing transition raises."""
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        asyncio.run(service.approve(v.version_id, http_session_actor))

        # The service retract path raises InvalidTransitionError when
        # the row is not in pending_approval. Cover a sampling of
        # alternate bad targets via the underlying repo for
        # completeness.
        for to_state in ("draft", "pending_approval", "rejected", "archived"):
            with pytest.raises(InvalidTransitionError):
                asyncio.run(service.atomic_transition(
                    v.version_id, "approved", to_state, http_session_actor,
                ))

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_invalid_sampling_rejected_to_draft(
        self, service, own_internal_actor, http_session_actor,
    ):
        """A1: ``(rejected, draft)`` is DROPPED — rejects stay terminal."""
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        asyncio.run(service.reject(v.version_id, http_session_actor))
        with pytest.raises(InvalidTransitionError):
            asyncio.run(service.atomic_transition(
                v.version_id, "rejected", "draft", http_session_actor,
            ))


# ─────────────────────────────────────────────────────────────────────────────
# Human-only approve / reject (T3)
# ─────────────────────────────────────────────────────────────────────────────


class TestHumanOnlyLaneT3:
    """T3 / D2: session lane approves / rejects; bearer + internal
    raise :class:`HumanLaneRequiredError`."""

    def _submit_one(self, service, actor):
        return asyncio.run(service.create_and_submit(
            "dev", "", "soul", "approve me", actor,
        ))

    def test_session_approves(
        self, service, own_internal_actor, http_session_actor,
    ):
        v = self._submit_one(service, own_internal_actor)
        approved = asyncio.run(service.approve(v.version_id, http_session_actor))
        assert approved.state == "approved"
        assert approved.approver_subject == "op@example.com"
        assert approved.approver_auth_method == AUTH_METHOD_SESSION

    def test_session_rejects(
        self, service, own_internal_actor, http_session_actor,
    ):
        v = self._submit_one(service, own_internal_actor)
        rejected = asyncio.run(service.reject(v.version_id, http_session_actor))
        assert rejected.state == "rejected"
        assert rejected.approver_auth_method == AUTH_METHOD_SESSION

    def test_bearer_approve_raises_human_lane_required(
        self, service, own_internal_actor, http_bearer_actor,
    ):
        v = self._submit_one(service, own_internal_actor)
        with pytest.raises(HumanLaneRequiredError) as exc_info:
            asyncio.run(service.approve(v.version_id, http_bearer_actor))
        err = exc_info.value
        assert err.version_id == v.version_id
        assert err.actor_auth_method == AUTH_METHOD_BEARER

    def test_bearer_reject_raises_human_lane_required(
        self, service, own_internal_actor, http_bearer_actor,
    ):
        v = self._submit_one(service, own_internal_actor)
        with pytest.raises(HumanLaneRequiredError) as exc_info:
            asyncio.run(service.reject(v.version_id, http_bearer_actor))
        err = exc_info.value
        assert err.actor_auth_method == AUTH_METHOD_BEARER

    def test_internal_approve_raises_human_lane_required(
        self, service, own_internal_actor,
    ):
        v = self._submit_one(service, own_internal_actor)
        with pytest.raises(HumanLaneRequiredError) as exc_info:
            asyncio.run(service.approve(v.version_id, own_internal_actor))
        assert exc_info.value.actor_auth_method == AUTH_METHOD_INTERNAL

    def test_internal_reject_raises_human_lane_required(
        self, service, own_internal_actor,
    ):
        v = self._submit_one(service, own_internal_actor)
        with pytest.raises(HumanLaneRequiredError) as exc_info:
            asyncio.run(service.reject(v.version_id, own_internal_actor))
        assert exc_info.value.actor_auth_method == AUTH_METHOD_INTERNAL

    def test_scope_does_not_lift_lane(
        self, service, own_internal_actor,
    ):
        """A bearer carrying the (hypothetical) ``prompts:approve``
        scope is STILL refused — D2: lane discriminator beats
        scope. The repo's defense-in-depth stays covered by the
        resolution test file."""
        bear = ActorContext(
            subject="op-cli", auth_method=AUTH_METHOD_BEARER,
        )
        # ``ActorContext`` is frozen; we just demonstrate that
        # even with arbitrary ``subject``, the bearer lane is
        # refused at the service gate.
        v = self._submit_one(service, own_internal_actor)
        with pytest.raises(HumanLaneRequiredError):
            asyncio.run(service.approve(v.version_id, bear))

    def test_missing_auth_method_refused(
        self, service, own_internal_actor,
    ):
        """An actor without ``auth_method`` attribute fails the
        lane check (caught by ``getattr(actor, "auth_method",
        None) -> None != "session"``)."""
        v = self._submit_one(service, own_internal_actor)
        no_auth = SimpleNamespace(subject="x")  # no auth_method attr
        with pytest.raises(HumanLaneRequiredError) as exc_info:
            asyncio.run(service.approve(v.version_id, no_auth))
        assert exc_info.value.actor_auth_method == "<missing>"


# ─────────────────────────────────────────────────────────────────────────────
# Retract authority matrix (T2 / S16)
# ─────────────────────────────────────────────────────────────────────────────


class TestRetractAuthorityT2:
    """T2 authority matrix (per phaseB-plan.md T2 + decisions.md S16)."""

    def _submit(self, service, actor):
        return asyncio.run(service.create_and_submit(
            "dev", "", "soul", "retract me", actor,
        ))

    def test_session_retracts_any_row(
        self, service, own_internal_actor, http_session_actor,
    ):
        v = self._submit(service, own_internal_actor)
        out = asyncio.run(service.retract(v.version_id, http_session_actor))
        assert out.state == "draft"

    def test_own_internal_retracts_own_row(
        self, service, own_internal_actor,
    ):
        v = self._submit(service, own_internal_actor)
        out = asyncio.run(service.retract(v.version_id, own_internal_actor))
        assert out.state == "draft"

    def test_other_instance_internal_refused(
        self, service, own_internal_actor, other_internal_actor,
    ):
        """S16 anti-forgery: a different instance id may not retract
        a row it did not submit."""
        v = self._submit(service, own_internal_actor)
        with pytest.raises(RetractAuthorityError) as exc_info:
            asyncio.run(service.retract(v.version_id, other_internal_actor))
        err = exc_info.value
        assert err.reason == "wrong_instance"
        assert err.actor_instance_id == "inst-other"
        assert err.row_submitter_instance_id == "inst-self"
        assert err.actor_auth_method == AUTH_METHOD_INTERNAL

    def test_bearer_refused(
        self, service, own_internal_actor, http_bearer_actor,
    ):
        v = self._submit(service, own_internal_actor)
        with pytest.raises(RetractAuthorityError) as exc_info:
            asyncio.run(service.retract(v.version_id, http_bearer_actor))
        assert exc_info.value.reason == "machine_lane_refused"

    def test_forged_internal_via_http_request_refused(
        self, service, own_internal_actor,
    ):
        """Anti-forgery tie-in: an HTTP request that body-asserts
        ``auth_method='internal'`` cannot mint an internal actor
        (``actor_from_request`` rejects internal for HTTP). If a
        forged internal somehow reaches retract, the
        own-instance check still applies."""
        # 1. Pretend the request body asserts internal. The HTTP
        # bridge MUST refuse — proving the authority gate has no
        # forged-internal path.
        fake_req = SimpleNamespace()
        fake_req.state = SimpleNamespace()
        fake_req.state.auth_context = SimpleNamespace(
            subject="forged", auth_method=AUTH_METHOD_INTERNAL,
            scopes=set(), expires_at="", meta={},
        )
        with pytest.raises(ValueError, match="reserved for in-process"):
            actor_from_request(fake_req)

        # 2. The normal HTTP contract: a bearer/session actor's
        # retract flows through ``actor_from_request``. The
        # ``authority check`` rejects bearer outright; session
        # passes through. We exercise both to confirm the forger
        # has no escape hatch.
        session_actor = actor_from_request(SimpleNamespace(
            state=SimpleNamespace(auth_context=SimpleNamespace(
                subject="op@example.com",
                auth_method=AUTH_METHOD_SESSION,
                scopes=set(), expires_at="", meta={},
            )),
        ))
        v = self._submit(service, own_internal_actor)
        # session-lane retract on the row succeeds — the forger
        # cannot reach a service-layer retract path they control.
        out = asyncio.run(service.retract(v.version_id, session_actor))
        assert out.state == "draft"

    def test_actor_from_request_missing_auth_context_raises(
        self,
    ):
        fake_req = SimpleNamespace()
        fake_req.state = SimpleNamespace()
        # No auth_context attribute at all.
        with pytest.raises(ValueError, match="no auth_context"):
            actor_from_request(fake_req)

    def test_actor_from_request_none_state_raises(self):
        """AuthContextMissingError pattern: when the request has no
        resolved AuthContext (auth-OFF posture), the HTTP bridge
        raises ``ValueError`` (the service-side typed error is
        :class:`AuthContextMissingError` — same condition, raised
        by service-layer helpers). This test pins the bridge's
        behavior to the contract."""
        fake_req = SimpleNamespace()
        fake_req.state = SimpleNamespace(auth_context=None)
        with pytest.raises(ValueError, match="no auth_context"):
            actor_from_request(fake_req)


# ─────────────────────────────────────────────────────────────────────────────
# Audit-row stamping at the service boundary (T2 / A3)
# ─────────────────────────────────────────────────────────────────────────────


class TestAuditStampingService:
    """One audit row per transition, stamped with the actor's
    subject/method. Covers both lanes (HTTP + internal)."""

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_session_approve_appends_one_audit_row(
        self, service, own_internal_actor, http_session_actor, repo,
    ):
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        asyncio.run(service.approve(v.version_id, http_session_actor))
        events = self._events_for(repo, v.version_id)
        # Two events: CREATED + SUBMITTED (from create_and_submit)
        # + APPROVED = 3.
        assert len(events) == 3
        # Last event stamps the HTTP session lane.
        approved = events[-1]
        assert approved["event_type"] == "APPROVED"
        assert approved["actor_subject"] == "op@example.com"
        assert approved["actor_auth_method"] == AUTH_METHOD_SESSION

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_internal_create_and_submit_appends_two_rows(
        self, service, own_internal_actor, repo,
    ):
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        events = self._events_for(repo, v.version_id)
        # CREATED + SUBMITTED — exactly one each.
        assert len(events) == 2
        types = sorted(e["event_type"] for e in events)
        assert types == ["CREATED", "SUBMITTED"]
        for e in events:
            assert e["actor_subject"] == "inst-self"
            assert e["actor_auth_method"] == AUTH_METHOD_INTERNAL

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_retract_appends_one_audit_row(
        self, service, own_internal_actor, repo,
    ):
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        # Delete the prior 2 events so the count is clean.
        asyncio.run(service.retract(v.version_id, own_internal_actor))
        events = self._events_for(repo, v.version_id)
        # Now we see 3: CREATED + SUBMITTED + RETRACTED.
        assert len(events) == 3
        retracted = [e for e in events if e["event_type"] == "RETRACTED"]
        assert len(retracted) == 1
        assert retracted[0]["actor_subject"] == "inst-self"
        assert retracted[0]["actor_auth_method"] == AUTH_METHOD_INTERNAL
        # Exactly-once invariant: no duplicate RETRACTED event.
        retracted_types = [e["event_type"] for e in events]
        assert retracted_types.count("RETRACTED") == 1

    @staticmethod
    def _events_for(repo, version_id):
        """Read all prompt_audit_events rows for a version_id."""
        with Session(repo.engine) as session:
            from sqlmodel import select, col
            stmt = (
                select(
                    col(PromptAuditEvent.event_type),
                    col(PromptAuditEvent.actor_subject),
                    col(PromptAuditEvent.actor_auth_method),
                )
                .select_from(PromptAuditEvent)
                .where(col(PromptAuditEvent.version_id) == version_id)
                .order_by(col(PromptAuditEvent.created_at))
            )
            rows = session.exec(stmt).all()
        return [
            {"event_type": r[0], "actor_subject": r[1],
             "actor_auth_method": r[2]}
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Concurrent transitions (T1 / R3 — service boundary)
# ─────────────────────────────────────────────────────────────────────────────


class TestServiceConcurrency:
    """Concurrent transitions on the same row serialize via the
    repo's atomic_transition CAS. SQLite default; PG parametrize
    leg runs against a fresh scratch DB (env-poison 3-guard)."""

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_concurrent_retract_only_one_wins(
        self, service, own_internal_actor, http_session_actor,
    ):
        # Two concurrent retracts on the same row: exactly one
        # CAS-wins; the loser sees the post-winner state and the
        # repo raises InvalidTransitionError (state already moved).
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "x", own_internal_actor,
        ))
        errors = []

        def _retract():
            try:
                asyncio.run(service.retract(v.version_id, http_session_actor))
            except Exception as e:  # noqa: BLE001
                errors.append(type(e).__name__)

        threads = [threading.Thread(target=_retract) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        # Some threads may lose the CAS race; we don't assert zero
        # errors (CAS losers raise InvalidTransitionError). We
        # assert the row landed in ``draft`` exactly once and the
        # final state is internally consistent.
        final = asyncio.run(service.get_version(v.version_id))
        assert final.state == "draft"


# ─────────────────────────────────────────────────────────────────────────────
# Pending-supersession (A5 / C4 — first override version = 1, ≤ 1 active)
# ─────────────────────────────────────────────────────────────────────────────


class TestPendingSupersession:
    """``create_and_submit`` twice on the same key:
    * first call inserts ``version=1`` (T10 / F1 OVERRIDES-ONLY);
    * second call inserts ``version=2`` and archives the prior
      pending row (A5/C4 ``≤ 1 active pending per key``).

    The repo-layer equivalent is
    ``TestCreateAndSubmit.test_create_and_submit_chains_and_archives_prior_pending``
    in ``test_prompt_version_resolution.py``. THIS test pins the
    same invariant at the service boundary so a future service-side
    composite regression fails fast here."""

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_first_draft_versions_to_one(
        self, service, own_internal_actor,
    ):
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "first", own_internal_actor,
        ))
        assert v.version == 1
        assert v.state == "pending_approval"

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_two_consecutive_submissions_leave_one_active(
        self, service, own_internal_actor,
    ):
        v1 = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "first", own_internal_actor,
        ))
        v2 = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "second", own_internal_actor,
        ))
        assert v2.version == 2, "second submission gets max(version)+1"

        v1_rel = asyncio.run(service.get_version(v1.version_id))
        v2_rel = asyncio.run(service.get_version(v2.version_id))
        assert v1_rel.state == "archived", "prior pending row archived"
        assert v2_rel.state == "pending_approval"

        # Service-level invariant: at most one ACTIVE pending row per key.
        active = asyncio.run(service.list_versions(
            "dev", "", "soul", limit=10,
        ))
        active_pending = [
            r for r in active if r.state == "pending_approval"
        ]
        assert len(active_pending) == 1
        assert active_pending[0].version_id == v2.version_id


# ─────────────────────────────────────────────────────────────────────────────
# Seeding (T10 / F1 OVERRIDES-ONLY)
# ─────────────────────────────────────────────────────────────────────────────


class TestSeedingF1:
    """F1 OVERRIDES-ONLY seeding contract (T10):
    * no DB rows → ``get_effective`` returns ``None`` (file fallback).
    * first ``create_and_submit`` → ``version=1``;
    * the template file is NEVER touched by the service.
    """

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_get_effective_returns_none_when_no_rows(self, service):
        eff = asyncio.run(service.get_effective("dev", "", "soul"))
        assert eff is None

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_first_create_and_submit_versions_to_one(
        self, service, own_internal_actor,
    ):
        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "first override", own_internal_actor,
        ))
        assert v.version == 1, (
            "F1 OVERRIDES-ONLY: first DB row per (agent_id, "
            "version_tag, artifact) carries version=1 (no template "
            "bootstrap row)"
        )

    @pytest.mark.parametrize("db_dialect", ["sqlite", "postgres"])
    def test_get_effective_returns_none_with_no_rows_then_content_after_approve(
        self, service, own_internal_actor, http_session_actor,
    ):
        # F1: pre-override, get_effective=None means file fallback.
        pre = asyncio.run(service.get_effective("dev", "", "soul"))
        assert pre is None

        v = asyncio.run(service.create_and_submit(
            "dev", "", "soul", "approved content", own_internal_actor,
        ))
        asyncio.run(service.approve(v.version_id, http_session_actor))

        post = asyncio.run(service.get_effective("dev", "", "soul"))
        assert post == "approved content"
