"""Integration tests for agent-secrets Phase 1 (plan f11965039).

PG-backed integration test pack (per plan §8 P1 exit
criterion): the worker agent can
``secret_generate_ssh_keypair`` → ``secret_list`` →
``secret_read_public`` and get the pubkey back. Private key
bits appear in NO response (the v1 pubkey-only rule is FINAL
— plan §5.2).

Coverage:

* **End-to-end v1 flow** — generate_ssh_keypair →
  list → read_public round-trip on PG (the plan §8 P1 exit
  criterion). Asserts the pubkey is the OpenSSH authorized-
  keys form.
* **Pubkey-only invariant** — every response object across
  the three P1 calls is grep-checked for any private-key
  byte pattern (``BEGIN OPENSSH PRIVATE KEY``, raw seed
  bytes, etc.). ZERO private bits cross the boundary.
* **Marker format** — every P1 call's response carries the
  FROZEN ``__ENS_SECRET__<id>__`` marker; the regex
  fullmatch is the validator.
* **Audit row stamps** — every P1 action writes one
  ``secrets_audit`` row with the agent id as ``actor`` and
  the secret id as ``secret_id``.

Test execution is fenced per the project critical notes:

* ``ENSEMBLE_SELF_ENV=dev`` (no live ensemble posture);
* PG scratch-DB fence: a DEDICATED SCRATCH DATABASE per
  test (created + dropped per test — never
  ``ensemble_dev`` itself, never ``ensemble_prod``);
* run only the named file (``--tb=short -q``), never bare
  pytest, never ``-x``, never full-suite.

KNOWN PRE-EXISTING DEFECT (NOT in scope): the agent-secrets
P1 scope explicitly excludes the existing
``20260714_000001`` migration that breaks fresh-SQLite
boots. The PG path is the integration target.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, select

# Register every SQLModel table BEFORE create_all runs.
from daemon.repositories.secrets.models import (  # noqa: F401
    SECRET_AUDIT_ACTIONS,
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    ENS_SECRET_MARKER_RE_COMPILED,
    SecretCipher,
    SecretNameConflictError,
    SecretService,
)


# ─────────────────────────────────────────────────────────────────────
# PG scratch-DB convention (mirrors
# tests/test_prompt_version_resolution.py:100-280)
# ─────────────────────────────────────────────────────────────────────


_PG_HOST = os.environ.get("PG_TEST_HOST", "127.0.0.1")
_PG_PORT = int(os.environ.get("PG_TEST_PORT", "5432"))
_PG_USER = os.environ.get("PG_TEST_USER", "ensemble")
_PG_PASSWORD = os.environ.get("PG_TEST_PASSWORD", "ensemble_dev")
_PG_MAINT_DB = os.environ.get("PG_TEST_MAINT_DB", "ensemble_dev")
_PG_SCRATCH_PREFIX = "ensemble_secrets_v1_test_"

# Hard-abort set — a resolved target in this set means the
# fixture is pointed at a live database; fail the whole
# session (fail-closed, env-poison 3-guard convention)
# instead of writing test rows into it.
_PG_FORBIDDEN_DBS = frozenset({"ensemble_prod"})


def _pg_admin_engine():
    """Maintenance engine (AUTOCOMMIT) for CREATE/DROP DATABASE.

    Probes connectivity first and distinguishes auth failures
    from "PG is down" so the skip reason is diagnostic instead
    of misleading. Returns ``None`` when PG is unreachable —
    the caller skips loudly.
    """
    if _PG_MAINT_DB in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved maintenance "
            f"DB {_PG_MAINT_DB!r} — refuse to point secrets tests "
            f"at a live database (env-poison 3-guard convention).",
            returncode=1,
        )
    from sqlalchemy.exc import DBAPIError, OperationalError

    url = (
        f"postgresql+psycopg://{_PG_USER}:{_PG_PASSWORD}"
        f"@{_PG_HOST}:{_PG_PORT}/{_PG_MAINT_DB}"
    )
    engine = create_engine(
        url, isolation_level="AUTOCOMMIT", pool_pre_ping=True
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        orig = getattr(exc, "orig", exc)
        sqlstate = getattr(
            getattr(orig, "diag", None), "sqlstate", None
        )
        kind = (
            "auth failure (check PG_TEST_USER / PG_TEST_PASSWORD)"
            if sqlstate in {"28000", "28P01"}
            else "unreachable"
        )
        return None, (
            f"PostgreSQL {kind} at {_PG_HOST}:{_PG_PORT}/"
            f"{_PG_MAINT_DB}: {exc}"
        )
    except DBAPIError as exc:
        engine.dispose()
        return None, (
            f"PostgreSQL DBAPI error at {_PG_HOST}:{_PG_PORT}: {exc}"
        )
    return engine, None


def _postgres_scratch_engine() -> Iterator[Engine]:
    """Yield a fresh scratch-PostgreSQL engine for a dedicated
    scratch DB.

    Lifecycle: probe maintenance connection → CREATE DATABASE
    ``ensemble_secrets_v1_test_<8hex>`` → ``SQLModel.metadata
    .create_all`` (PG truth path) → yield → dispose →
    terminate stray backends → DROP DATABASE. Skips loudly
    (CI portability) when PG is unreachable.
    """
    admin, skip_reason = _pg_admin_engine()
    if admin is None:
        pytest.skip(skip_reason)

    scratch_db = f"{_PG_SCRATCH_PREFIX}{uuid.uuid4().hex[:8]}"
    if scratch_db in _PG_FORBIDDEN_DBS:
        pytest.exit(
            f"HARD ABORT: PG scratch fixture resolved scratch DB "
            f"{scratch_db!r} — refuse to point secrets tests at a "
            f"live database (env-poison 3-guard convention).",
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
        # Schema: the PG truth path — full-metadata create_all
        # on a DEDICATED scratch DB (mirrors api-lifespan
        # behavior). The .sql migration runner is SQLite-only
        # by design; PG receives brand-new tables via
        # create_all (dual-driver truth; stored_watches /
        # snapshot / prompt_version precedent).
        SQLModel.metadata.create_all(engine)
        yield engine
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
                conn.execute(
                    text(f'DROP DATABASE IF EXISTS "{scratch_db}"')
                )
        finally:
            admin.dispose()


def _sqlite_scratch_engine() -> Iterator[Engine]:
    """Yield a fresh file-SQLite engine for the SQLite leg
    of the integration test.

    The v1 basic flow ALSO works on SQLite (the
    ``SQLModel.metadata.create_all`` + the SQLAlchemy
    ``LargeBinary`` + ``UniqueConstraint`` + dual-dialect
    partial index all work on both drivers). The PG leg
    is the integration target; the SQLite leg is the
    portability sanity check.
    """
    db_path = tempfile.mktemp(suffix=".db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()
        # WAL mode leaves -wal/-shm sidecars; ignore unlink errors.
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(db_path + ext)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    """Yield a scratch-PG engine; the caller owns the
    lifecycle (dispose + DROP DATABASE is the fixture's
    job)."""
    yield from _postgres_scratch_engine()


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    """Yield a scratch-SQLite engine for the portability
    sanity leg."""
    yield from _sqlite_scratch_engine()


@pytest.fixture
def service_pg(pg_engine: Engine) -> SecretService:
    """Yield a SecretService backed by the scratch PG
    engine."""
    return SecretService(
        engine=pg_engine,
        cipher=SecretCipher(encryption_key=Fernet.generate_key().decode()),
    )


@pytest.fixture
def service_sqlite(sqlite_engine: Engine) -> SecretService:
    """Yield a SecretService backed by the scratch SQLite
    engine."""
    return SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=Fernet.generate_key().decode()),
    )


# ─────────────────────────────────────────────────────────────────────
# Phase 1 v1 basic flow — PG-backed (plan §8 P1 exit criterion)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestV1BasicFlowPG:
    """The P1 exit criterion (plan §8): a worker agent can
    ``secret_generate_ssh_keypair`` →
    ``secret_list`` → ``secret_read_public`` and get the
    pubkey back. The pubkey-only invariant is FINAL
    (plan §5.2): private key bits appear in NO
    response.
    """

    def test_generate_ssh_keypair_returns_pubkey_shape(
        self, service_pg: SecretService
    ) -> None:
        """generate_ssh_keypair returns
        ``{id, name, public_key, fingerprint, marker}`` —
        the v1 tool response shape.
        """
        result = service_pg.generate_ssh_keypair(
            name="pg-key-1", actor="worker"
        )
        assert set(result.keys()) == {
            "id",
            "name",
            "public_key",
            "fingerprint",
            "marker",
        }
        # The public_key is the OpenSSH authorized-keys
        # form (``ssh-ed25519 AAAA... <comment>``).
        assert result["public_key"].startswith("ssh-ed25519 ")
        # The fingerprint is the SHA256 form (``SHA256:xxxx``).
        assert result["fingerprint"].startswith("SHA256:")
        # The marker is the FROZEN format.
        assert (
            ENS_SECRET_MARKER_RE_COMPILED.fullmatch(result["marker"])
            is not None
        )

    def test_generate_ssh_keypair_no_private_bits_in_response(
        self, service_pg: SecretService
    ) -> None:
        """Pubkey-only invariant (criterion #2 — plan §5.2
        + §11 success criteria)."""
        result = service_pg.generate_ssh_keypair(
            name="pg-key-2", actor="worker"
        )
        blob = json.dumps(result).encode()
        # The OpenSSH private-key PEM header must NEVER
        # appear in the response.
        assert b"BEGIN OPENSSH PRIVATE KEY" not in blob
        assert b"PRIVATE KEY" not in blob
        # The raw ed25519 seed is 32 bytes; the only way
        # it would leak is via the wrapped_secret value
        # leaking — the response carries no wrapped_secret
        # field, so this is a tautology, but the explicit
        # check catches future regressions.
        assert b"wrapped_secret" not in blob

    def test_list_includes_generated_keypair(
        self, service_pg: SecretService
    ) -> None:
        """list returns the active row with public_material
        populated (the ssh-ed25519 form) and no other
        secret material."""
        generated = service_pg.generate_ssh_keypair(
            name="pg-key-list", actor="worker"
        )
        result = service_pg.list(actor="worker")
        assert len(result) == 1
        row = result[0]
        assert row["id"] == generated["id"]
        assert row["name"] == "pg-key-list"
        assert row["kind"] == "ssh_keypair"
        assert row["public_material"].startswith("ssh-ed25519 ")
        # The list view NEVER carries marker / wrapped_secret.
        assert "marker" not in row
        assert "wrapped_secret" not in row

    def test_read_public_returns_pubkey_only(
        self, service_pg: SecretService
    ) -> None:
        """read_public returns
        ``{id, name, kind, public_material}`` — NEVER the
        wrapped_secret, NEVER the decrypted plaintext,
        NEVER the private key bits (the v1 pubkey-only
        rule is FINAL).
        """
        generated = service_pg.generate_ssh_keypair(
            name="pg-key-rp", actor="worker"
        )
        result = service_pg.read_public(
            id=generated["id"], actor="worker"
        )
        assert set(result.keys()) == {
            "id",
            "name",
            "kind",
            "public_material",
        }
        assert result["id"] == generated["id"]
        assert result["name"] == "pg-key-rp"
        assert result["kind"] == "ssh_keypair"
        assert result["public_material"].startswith("ssh-ed25519 ")
        # The blob is grep-checked.
        blob = json.dumps(result).encode()
        assert b"BEGIN OPENSSH PRIVATE KEY" not in blob
        assert b"PRIVATE KEY" not in blob
        assert b"wrapped_secret" not in blob

    def test_full_round_trip_generate_list_read_public(
        self, service_pg: SecretService
    ) -> None:
        """The plan §8 P1 exit criterion end-to-end:
        generate_ssh_keypair → list → read_public returns
        the pubkey. Private key bits appear in NO
        response (criterion #2 for the tool lane).
        """
        # Step 1: generate
        generated = service_pg.generate_ssh_keypair(
            name="pg-e2e", actor="worker"
        )
        gen_blob = json.dumps(generated).encode()
        # Step 2: list
        listed = service_pg.list(actor="worker")
        list_blob = json.dumps(listed).encode()
        # Step 3: read_public
        result = service_pg.read_public(
            id=generated["id"], actor="worker"
        )
        rp_blob = json.dumps(result).encode()

        # The pubkey is byte-equal across the three
        # responses (the list view's public_material
        # is the same value the generate_ssh_keypair
        # response's public_key, which is the same
        # value the read_public response's
        # public_material).
        assert (
            generated["public_key"]
            == listed[0]["public_material"]
            == result["public_material"]
        )

        # Private bits appear in NONE of the three blobs.
        for label, blob in (
            ("generate", gen_blob),
            ("list", list_blob),
            ("read_public", rp_blob),
        ):
            assert b"BEGIN OPENSSH PRIVATE KEY" not in blob, (
                f"{label} response leaked OpenSSH private key"
            )
            assert b"PRIVATE KEY" not in blob, (
                f"{label} response leaked 'PRIVATE KEY' header"
            )
            assert b"wrapped_secret" not in blob, (
                f"{label} response leaked wrapped_secret"
            )

    def test_audit_rows_written_for_p1_actions(
        self, service_pg: SecretService, pg_engine: Engine
    ) -> None:
        """Every P1 action stamps an audit row. The
        action enum is forward-compatible (P2-only
        entries in the set with no P1 emitter); the P1
        path stamps ``create`` / ``read_public`` /
        ``retire`` only.
        """
        created = service_pg.generate_ssh_keypair(
            name="pg-audit", actor="worker"
        )
        service_pg.read_public(
            id=created["id"], actor="worker"
        )
        service_pg.retire(id=created["id"], actor="worker")
        with _session(pg_engine) as session:
            rows = session.exec(
                select(SecretAuditEvent)
            ).all()
        actions = sorted({r.action for r in rows})
        # The three P1 actions are present (and only
        # those — no P2-only actions have a v1 emitter).
        assert actions == ["create", "read_public", "retire"]
        # Each row carries the agent id as actor.
        for row in rows:
            assert row.actor == "worker"
            assert row.secret_id == created["id"]
            assert row.at is not None

    def test_marker_format_frozen_across_p1(
        self, service_pg: SecretService
    ) -> None:
        """The marker format is FROZEN (Q3 ruling
        2026-10-17). Every mint (generate_ssh_keypair /
        generate_api_token / store) returns the same
        shape.
        """
        ssh = service_pg.generate_ssh_keypair(
            name="marker-ssh", actor="worker"
        )
        tok = service_pg.generate_api_token(
            name="marker-tok", actor="worker"
        )
        sto = service_pg.store(
            name="marker-sto",
            kind="generic",
            value="x",
            actor="worker",
        )
        for label, marker in (
            ("ssh_keypair", ssh["marker"]),
            ("api_token", tok["marker"]),
            ("store", sto["marker"]),
        ):
            assert (
                ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker)
                is not None
            ), f"{label} marker {marker!r} did not fullmatch"

    def test_idx_secrets_kind_active_exists_on_pg(
        self, pg_engine: Engine
    ) -> None:
        """Reviewer pin (gap 4a — PG index parity): the
        partial index ``idx_secrets_kind_active`` declared
        via the SQLModel ``Index(postgresql_where=...)``
        is materialized on the PG scratch engine after
        ``create_all``. The SQLite leg covers the same
        predicate via ``sqlite_where``; this test pins
        the PG parity explicitly via pg_indexes.
        """
        with _session(pg_engine) as session:
            rows = session.exec(
                text(
                    "SELECT indexname, indexdef "
                    "FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND tablename = 'secrets'"
                )
            ).all()
        index_defs = {name: defn for name, defn in rows}
        assert "idx_secrets_kind_active" in index_defs, (
            f"partial index missing on PG; found: "
            f"{sorted(index_defs)!r}"
        )
        # The PG partial index carries the same
        # ``retired_at IS NULL`` predicate the SQLite
        # side emits via ``sqlite_where`` — parity-
        # record contract.
        assert (
            "retired_at IS NULL"
            in index_defs["idx_secrets_kind_active"]
        ), (
            f"PG partial index predicate missing; "
            f"indexdef={index_defs['idx_secrets_kind_active']!r}"
        )

    def test_store_name_conflict_no_audit_row_on_pg(
        self, service_pg: SecretService, pg_engine: Engine
    ) -> None:
        """Reviewer pin (gap 4b — PG atomicity): the M1
        ``engine.begin()`` atomicity contract holds on PG.
        A duplicate-name ``store()`` raises
        ``SecretNameConflictError`` AND rolls back the
        mutation+audit insert together — no orphan
        ``secrets_audit`` row is left after the failed
        attempt (the SQLite leg is pinned by the dev's
        M1 atomicity tests; this pins the PG path
        symmetrically).
        """
        created = service_pg.store(
            name="dup-pg",
            kind="generic",
            value="first",
            actor="worker",
        )
        with pytest.raises(SecretNameConflictError):
            service_pg.store(
                name="dup-pg",
                kind="generic",
                value="second",
                actor="worker",
            )
        with _session(pg_engine) as session:
            audit_rows = session.exec(
                select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "create"
                )
            ).all()
            secrets_rows = session.exec(
                select(Secret).where(Secret.name == "dup-pg")
            ).all()
        # Exactly one (the first successful call's)
        # audit row remains — the failed call rolled
        # back its audit insert along with the mutation.
        assert len(audit_rows) == 1, (
            f"failed store() leaked audit row(s): "
            f"{[(r.id, r.secret_id) for r in audit_rows]!r}"
        )
        assert {r.secret_id for r in audit_rows} == {
            created["id"]
        }
        assert len(secrets_rows) == 1, (
            f"duplicate-name insert leaked secrets row(s): "
            f"{len(secrets_rows)}"
        )


# ─────────────────────────────────────────────────────────────────────
# Phase 1 v1 basic flow — SQLite leg (portability sanity check)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.integration
class TestV1BasicFlowSQLitePortability:
    """The same end-to-end flow must work on SQLite too
    (the dual-driver invariant: the SQLModel + the
    ``LargeBinary`` + the dual-dialect partial index all
    work on both drivers).
    """

    def test_full_round_trip_sqlite(
        self, service_sqlite: SecretService
    ) -> None:
        generated = service_sqlite.generate_ssh_keypair(
            name="sqlite-e2e", actor="worker"
        )
        listed = service_sqlite.list(actor="worker")
        result = service_sqlite.read_public(
            id=generated["id"], actor="worker"
        )
        # The pubkey shape is identical to the PG leg.
        assert (
            generated["public_key"]
            == listed[0]["public_material"]
            == result["public_material"]
        )
        # The blob grep checks.
        for blob in (
            json.dumps(generated).encode(),
            json.dumps(listed).encode(),
            json.dumps(result).encode(),
        ):
            assert b"BEGIN OPENSSH PRIVATE KEY" not in blob
            assert b"PRIVATE KEY" not in blob
            assert b"wrapped_secret" not in blob


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _session(engine: Engine):
    """Yield a SQLModel session bound to ``engine``."""
    return _Session(engine)


from sqlmodel import Session as _Session  # noqa: E402
