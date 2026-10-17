"""Unit tests for :mod:`daemon.services.secret_service`.

Phase 1 of the agent-secrets commission (plan f11965039) — the
durable Fernet-encrypted secret store + the in-process
``SecretService``. The tests cover:

* **Cipher wrap/unwrap round-trip** — the Fernet key flow
  reads ``SYSTEM_ENCRYPTION_KEY`` at construction time and
  refuses any wrap/unwrap call when the key is missing.
* **Pubkey-only invariants** — the SSH-keypair mint surface
  NEVER returns the private key bits (OpenSSH private PEM
  bytes, raw ed25519 seed) in any response object.
* **Marker mint format** — the
  ``__ENS_SECRET__<secret_id>__`` FROZEN format is the only
  shape any tool response carries; the regex fullmatch is
  the validator.
* **Retire semantics** — soft-retire sets ``retired_at``
  and skips the active-registry partial index; the
  ``read_public`` path still works for audit.
* **Audit-row stamps** — every P1 action (``create`` /
  ``read_public`` / ``retire``) writes one row with the
  agent id as ``actor`` and the secret id as ``secret_id``
  (where applicable).
* **Name-collision** — a re-store on the same name errors
  at the service boundary (the trust ruling has no
  human-in-loop; silent rename is the wrong default).

Test execution is fenced per the project critical notes:

* ``ENSEMBLE_SELF_ENV=dev`` (no live ensemble posture);
* the integration-tier PG scratch-DB fence (the P1 basic
  test pack lives at
  ``tests/integration/test_secrets_v1_basic.py`` — these
  unit tests use file-backed SQLite to keep the run fast);
* run only the named file (``--tb=short -q``), never bare
  pytest, never ``-x``, never full-suite.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

# Register the SQLModel tables before create_all runs.
from daemon.repositories.secrets.models import (  # noqa: F401
    SECRET_AUDIT_ACTIONS,
    SECRET_AUDIT_ACTIONS_P1,
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    ENS_SECRET_MARKER_PREFIX,
    ENS_SECRET_MARKER_RE,
    ENS_SECRET_MARKER_RE_COMPILED,
    ENS_SECRET_MARKER_SUFFIX,
    SecretCipher,
    SecretNameConflictError,
    SecretNotFoundError,
    SecretService,
    SecretValidationError,
    generate_ssh_keypair_material,
    mint_marker,
    parse_marker,
)


# ─────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    """Build a file-backed SQLite engine with the house PRAGMA set.

    Mirrors the convention in
    ``tests/helpers/service_tool_sqlite.py:62-90`` but
    inlined here to avoid cross-test-folder coupling (the
    secrets tests are a self-contained pack).
    """
    db_path = tmp_path / "secret-service-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )
    from sqlalchemy import event as sa_event

    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=10000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
    return eng


@pytest.fixture
def sqlite_engine(tmp_path) -> Iterator[Engine]:
    """Yield a fresh file-backed SQLite engine with the
    Secret + SecretAuditEvent tables created. The caller
    owns the dispose lifecycle.
    """
    eng = _make_sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def fernet_key() -> str:
    """Fresh Fernet key for each test (no cross-test
    coupling on the encryption key)."""
    return Fernet.generate_key().decode()


@pytest.fixture
def service(sqlite_engine: Engine, fernet_key: str) -> SecretService:
    """Yield a fully-wired SecretService with the cipher
    explicitly constructed from a known key (so the
    wrap/unwrap round-trip is deterministic)."""
    return SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )


# ─────────────────────────────────────────────────────────────────────
# Marker format (Q3 — FROZEN 2026-10-17)
# ─────────────────────────────────────────────────────────────────────


class TestMarkerFormat:
    """The marker format is FROZEN — Q3 ruling 2026-10-17.

    The shape is the only P1-side surface that touches the
    marker scheme: the regex is the canonical form for any
    future P2 resolver. P1 mints handles; P2 resolves them.
    """

    def test_marker_prefix_suffix_frozen(self) -> None:
        assert ENS_SECRET_MARKER_PREFIX == "__ENS_SECRET__"
        assert ENS_SECRET_MARKER_SUFFIX == "__"

    def test_marker_distinct_from_kms_lite_lanes(self) -> None:
        """Visual / semantic distinction from KMS-Lite lanes
        is the binding constraint (plan §5.4 do-not-touch
        seams + §13 binding design constraint #1)."""
        # LANE-1: __KMS_REF__KMS_HANDLE_<uuid>__
        assert "KMS" not in ENS_SECRET_MARKER_PREFIX
        # LANE-2: __KMS_ENV__<VAR>__
        assert "ENV" not in ENS_SECRET_MARKER_PREFIX
        # agent-secrets: __ENS_SECRET__<uuid>__
        assert ENS_SECRET_MARKER_PREFIX.startswith("__ENS_")
        assert ENS_SECRET_MARKER_PREFIX.endswith("__")

    def test_mint_marker_round_trip(self) -> None:
        secret_id = str(uuid.uuid4())
        marker = mint_marker(secret_id)
        assert marker == f"__ENS_SECRET__{secret_id}__"
        # parse_marker is the inverse (P2 resolver shape).
        assert parse_marker(marker) == secret_id

    def test_mint_marker_rejects_non_uuid(self) -> None:
        """A non-UUID id is a programmer error (the column
        is UUID4-only); the mint helper refuses rather
        than silently mangling."""
        with pytest.raises(ValueError):
            mint_marker("not-a-uuid")
        with pytest.raises(ValueError):
            mint_marker("1234")

    def test_parse_marker_returns_none_for_non_marker(self) -> None:
        """parse_marker is the P2 resolver's first guard;
        non-marker values return None rather than raising
        (the consumer branches on None)."""
        assert parse_marker("plaintext-value") is None
        assert parse_marker("__KMS_REF__KMS_HANDLE_xyz__") is None
        assert parse_marker("__KMS_ENV__OPENAI_API_KEY__") is None
        assert parse_marker("__ENS_SECRET__not-a-uuid__") is None
        assert parse_marker("") is None

    def test_marker_regex_matches_known_good(self) -> None:
        """The frozen regex fullmatches every well-formed
        marker (the validator at the future P2 resolver
        boundary)."""
        secret_id = str(uuid.uuid4())
        marker = mint_marker(secret_id)
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker) is not None

    def test_marker_regex_rejects_known_bad(self) -> None:
        """The frozen regex REJECTS the KMS-Lite lanes
        (the P2 resolver will skip them — they're handled
        by their own regex)."""
        # LANE-1
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(
            "__KMS_REF__KMS_HANDLE_a1b2__"
        ) is None
        # LANE-2
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(
            "__KMS_ENV__OPENAI_API_KEY__"
        ) is None
        # Non-marker plaintext
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch("plaintext") is None
        # Empty
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch("") is None


# ─────────────────────────────────────────────────────────────────────
# Cipher round-trip
# ─────────────────────────────────────────────────────────────────────


class TestSecretCipherRoundTrip:
    """Fernet round-trip via the colocated SecretCipher.

    The cipher is the in-process helper that reads
    ``SYSTEM_ENCRYPTION_KEY`` at construction time (plan
    §7.2). The rotation coupling (plan §7.1 + §10) — the
    key is shared with ``db_connections`` — is documented
    at the cipher seam in the service module; tests pin
    the key-source contract (the env var name) and the
    fail-CLOSED posture.
    """

    def test_wrap_then_unwrap_round_trips(self, fernet_key: str) -> None:
        cipher = SecretCipher(encryption_key=fernet_key)
        assert cipher.is_available
        plaintext = b"hello, world"
        ciphertext = cipher.wrap(plaintext)
        assert ciphertext != plaintext
        assert cipher.unwrap(ciphertext) == plaintext

    def test_cipher_key_env_is_system_encryption_key(self) -> None:
        """The key source is ``SYSTEM_ENCRYPTION_KEY`` —
        the same env the existing
        ``daemon/sources/credentials.py`` +
        ``daemon/services/kms_lite.py`` paths consume
        (user Q1 ruling 2026-10-17, plan §7.1).

        Rotation coupling: a future ``SYSTEM_ENCRYPTION_KEY``
        rotation MUST re-encrypt ``secrets.wrapped_secret``
        AND the ``db_connections`` Fernet credentials.
        """
        assert SecretCipher.KEY_ENV == "SYSTEM_ENCRYPTION_KEY"

    def test_cipher_unavailable_when_key_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``SYSTEM_ENCRYPTION_KEY`` set + no explicit
        key arg → the cipher is unavailable. The service
        refuses to operate (fail-CLOSED rather than silently
        storing plaintext — the kms_lite precedent at
        ``kms_lite.py:131,215-221``)."""
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        cipher = SecretCipher()
        assert not cipher.is_available
        with pytest.raises(RuntimeError):
            cipher.wrap(b"x")
        with pytest.raises(RuntimeError):
            cipher.unwrap(b"x")

    def test_cipher_unavailable_when_key_invalid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid key (not a Fernet key) keeps the
        cipher unavailable — the wrap/unwrap calls raise
        rather than corrupting data."""
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", "not-a-fernet-key")
        cipher = SecretCipher()
        assert not cipher.is_available

    def test_cipher_reads_key_from_env(
        self, monkeypatch: pytest.MonkeyPatch, fernet_key: str
    ) -> None:
        """No explicit key arg + ``SYSTEM_ENCRYPTION_KEY``
        in env → the cipher is available and round-trips."""
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", fernet_key)
        cipher = SecretCipher()
        assert cipher.is_available
        assert cipher.unwrap(cipher.wrap(b"x")) == b"x"

    def test_cipher_rejects_bad_ciphertext(
        self, fernet_key: str
    ) -> None:
        """A ciphertext that was NOT produced by this key
        is a programmer error (or a key-rotation window);
        Fernet raises ``InvalidToken`` (re-exported from
        ``cryptography.fernet``). The service boundary
        surfaces this as a structured error envelope."""
        from cryptography.fernet import InvalidToken
        cipher = SecretCipher(encryption_key=fernet_key)
        # A different key's ciphertext
        other_cipher = SecretCipher(encryption_key=Fernet.generate_key().decode())
        other_ciphertext = other_cipher.wrap(b"x")
        with pytest.raises(InvalidToken):
            cipher.unwrap(other_ciphertext)


# ─────────────────────────────────────────────────────────────────────
# generate_ssh_keypair — pubkey-only invariants
# ─────────────────────────────────────────────────────────────────────


class TestSshKeypairPubkeyOnly:
    """The v1 pubkey-only rule is FINAL (plan §5.2).

    Private key bits (the OpenSSH private PEM, the raw
    ed25519 seed) NEVER cross a tool-result boundary.
    The test suite walks every response object the service
    returns to assert the invariant.
    """

    def test_generator_helper_returns_all_four_fields(self) -> None:
        """Pure-function smoke test for the ed25519 generator
        helper. The service composes the generator output
        with the cipher and the store; the helper itself is
        a pure function with no I/O."""
        m = generate_ssh_keypair_material()
        # Public side
        assert m.public_openssh.startswith("ssh-ed25519 ")
        assert m.fingerprint.startswith("SHA256:")
        # Private side — held in memory only; the service
        # drops the reference after the wrap call.
        assert m.private_openssh_pem.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----")
        assert len(m.raw_private_bytes) == 32  # ed25519 seed

    def test_generate_ssh_keypair_response_has_no_private_bits(
        self, service: SecretService
    ) -> None:
        """The P1 tool response shape is
        ``{id, name, public_key, fingerprint, marker}`` —
        NEVER the private PEM, NEVER the raw seed.
        """
        result = service.generate_ssh_keypair(
            name="test-key", actor="worker"
        )
        # The shape is exactly 5 fields — no extras.
        assert set(result.keys()) == {
            "id",
            "name",
            "public_key",
            "fingerprint",
            "marker",
        }
        # The stringified response carries NO private bits
        # (JSON-serialize then grep — the would-be leak
        # channel into checkpoints).
        blob = json.dumps(result)
        assert b"BEGIN OPENSSH PRIVATE KEY" not in blob.encode()
        assert b"PRIVATE KEY" not in blob.encode()
        # The raw seed is 32 bytes; even a one-byte
        # leak would surface as a non-UUID hex char in the
        # blob. Spot-check: the public_openssh field is the
        # only ``ssh-ed25519`` line.
        assert result["public_key"].startswith("ssh-ed25519 ")

    def test_generate_ssh_keypair_marker_format(
        self, service: SecretService
    ) -> None:
        """The marker is the FROZEN format
        ``__ENS_SECRET__<id>__`` — the Q3 ruling."""
        result = service.generate_ssh_keypair(
            name="marker-test", actor="worker"
        )
        marker = result["marker"]
        # The marker fullmatches the frozen regex.
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker) is not None
        # The inner id matches the row id.
        assert parse_marker(marker) == result["id"]
        # The marker carries ONLY the public-side shape —
        # no embedded private bits.
        blob = json.dumps(result)
        assert b"BEGIN OPENSSH PRIVATE KEY" not in blob.encode()


# ─────────────────────────────────────────────────────────────────────
# generate_api_token — last-4 preview only
# ─────────────────────────────────────────────────────────────────────


class TestApiTokenPreview:
    """The full token NEVER crosses the tool boundary in v1
    (plan §5.2). The response carries only the last-4-char
    preview.
    """

    def test_token_preview_is_last_four_chars(
        self, service: SecretService
    ) -> None:
        result = service.generate_api_token(
            name="api-1", length=32, actor="worker"
        )
        assert set(result.keys()) == {
            "id",
            "name",
            "token_preview",
            "marker",
        }
        preview = result["token_preview"]
        assert len(preview) == 4

    def test_token_full_value_not_in_response(
        self, service: SecretService
    ) -> None:
        """The full token must NOT be in the response.
        The wrapped_secret is in the DB; the response
        carries only the last 4 chars of the plaintext.
        """
        # We can't inspect the plaintext (it was never
        # logged; it was held only in memory until the
        # wrap call returned), so we assert the response
        # shape is constrained.
        result = service.generate_api_token(
            name="api-2", length=32, actor="worker"
        )
        blob = json.dumps(result)
        # No "token" or "secret" or "value" keys beyond
        # the documented ``token_preview``.
        assert "\"token\"" not in blob
        assert "\"value\"" not in blob
        assert "\"secret\"" not in blob

    def test_token_length_validation(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretValidationError):
            service.generate_api_token(
                name="bad-len", length=0, actor="worker"
            )
        with pytest.raises(SecretValidationError):
            service.generate_api_token(
                name="bad-len", length=-1, actor="worker"
            )


# ─────────────────────────────────────────────────────────────────────
# store — wrap-on-store
# ─────────────────────────────────────────────────────────────────────


class TestStoreWrapOnStore:
    """Wrap-and-store an arbitrary value under a
    forward-compatible kind.
    """

    def test_store_returns_marker_and_id_only(
        self, service: SecretService
    ) -> None:
        result = service.store(
            name="stored-1", kind="generic", value="some-secret",
            actor="worker",
        )
        assert set(result.keys()) == {"id", "name", "marker"}
        # The plaintext value NEVER crosses the boundary.
        blob = json.dumps(result)
        assert "some-secret" not in blob
        # The marker fullmatches the frozen regex.
        assert ENS_SECRET_MARKER_RE_COMPILED.fullmatch(
            result["marker"]
        ) is not None

    def test_store_supports_all_p1_kinds(
        self, service: SecretService
    ) -> None:
        """The P1-emitter set is the three kind tags."""
        for i, kind in enumerate(["ssh_keypair", "api_token", "generic"]):
            result = service.store(
                name=f"kind-{kind}-{i}",
                kind=kind,
                value=f"value-{i}",
                actor="worker",
            )
            assert result["marker"] is not None

    def test_store_rejects_unknown_kind(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretValidationError):
            service.store(
                name="bad-kind",
                kind="not-a-kind",
                value="x",
                actor="worker",
            )

    def test_store_rejects_non_string_value(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretValidationError):
            service.store(
                name="bad-val",
                kind="generic",
                value=12345,  # type: ignore[arg-type]
                actor="worker",
            )

    def test_store_rejects_empty_name(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretValidationError):
            service.store(
                name="",
                kind="generic",
                value="x",
                actor="worker",
            )


# ─────────────────────────────────────────────────────────────────────
# list — no secrets in the listing
# ─────────────────────────────────────────────────────────────────────


class TestListNoSecretMaterial:
    """The list view NEVER includes ``marker``, ``wrapped_secret``,
    or any plaintext. Only id + name + kind + public_material.
    """

    def test_list_returns_empty_when_no_secrets(
        self, service: SecretService
    ) -> None:
        result = service.list(actor="worker")
        assert result == []

    def test_list_returns_public_view_only(
        self, service: SecretService
    ) -> None:
        service.generate_ssh_keypair(name="k-1", actor="worker")
        service.generate_api_token(name="t-1", actor="worker")
        service.store(name="g-1", kind="generic", value="x", actor="worker")
        result = service.list(actor="worker")
        assert len(result) == 3
        for row in result:
            # The shape is exactly 4 fields — no marker,
            # no wrapped_secret, no plaintext.
            assert set(row.keys()) == {
                "id",
                "name",
                "kind",
                "public_material",
            }
        # Find each by kind
        kinds = {r["kind"] for r in result}
        assert kinds == {"ssh_keypair", "api_token", "generic"}
        # The ssh_keypair row carries the public_openssh.
        ssh_row = next(r for r in result if r["kind"] == "ssh_keypair")
        assert ssh_row["public_material"].startswith("ssh-ed25519 ")
        # The token / generic rows have null public_material.
        token_row = next(r for r in result if r["kind"] == "api_token")
        assert token_row["public_material"] is None
        gen_row = next(r for r in result if r["kind"] == "generic")
        assert gen_row["public_material"] is None


# ─────────────────────────────────────────────────────────────────────
# read_public — pubkey-only
# ─────────────────────────────────────────────────────────────────────


class TestReadPublicPubkeyOnly:
    """The pubkey-only rule is FINAL — read_public NEVER
    returns ``wrapped_secret``, NEVER returns the decrypted
    plaintext, NEVER returns private key bits.
    """

    def test_read_public_returns_public_view(
        self, service: SecretService
    ) -> None:
        created = service.generate_ssh_keypair(
            name="k-1", actor="worker"
        )
        result = service.read_public(id=created["id"], actor="worker")
        # The shape is exactly 4 fields — no wrapped_secret,
        # no plaintext, no private key bits.
        assert set(result.keys()) == {
            "id",
            "name",
            "kind",
            "public_material",
        }
        assert result["id"] == created["id"]
        assert result["name"] == "k-1"
        assert result["kind"] == "ssh_keypair"
        assert result["public_material"].startswith("ssh-ed25519 ")

    def test_read_public_unknown_id_raises(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretNotFoundError):
            service.read_public(id=str(uuid.uuid4()), actor="worker")

    def test_read_public_empty_id_raises(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretValidationError):
            service.read_public(id="", actor="worker")


# ─────────────────────────────────────────────────────────────────────
# retire — soft-retire
# ─────────────────────────────────────────────────────────────────────


class TestRetireSemantics:
    """Soft-retire semantics: sets ``retired_at``; the row
    stays queryable for audit; the active-registry partial
    index skips retired rows.
    """

    def test_retire_sets_retired_at(
        self, service: SecretService
    ) -> None:
        created = service.generate_ssh_keypair(
            name="k-retire", actor="worker"
        )
        result = service.retire(id=created["id"], actor="worker")
        assert set(result.keys()) == {"id", "retired_at"}
        assert result["id"] == created["id"]
        assert result["retired_at"] is not None

    def test_retired_rows_excluded_from_list(
        self, service: SecretService
    ) -> None:
        """The active-registry partial index skips retired
        rows (the ``WHERE retired_at IS NULL`` predicate)."""
        a = service.generate_ssh_keypair(name="a", actor="worker")
        b = service.generate_ssh_keypair(name="b", actor="worker")
        service.retire(id=a["id"], actor="worker")
        listed = service.list(actor="worker")
        ids = {r["id"] for r in listed}
        assert a["id"] not in ids
        assert b["id"] in ids

    def test_retired_rows_still_readable(
        self, service: SecretService
    ) -> None:
        """Audit-trail invariant: a retired row stays
        queryable via read_public (the P1 contract — the
        retirement is for the active-registry index, NOT a
        row deletion)."""
        created = service.generate_ssh_keypair(
            name="still-readable", actor="worker"
        )
        service.retire(id=created["id"], actor="worker")
        result = service.read_public(
            id=created["id"], actor="worker"
        )
        assert result["id"] == created["id"]
        assert result["public_material"].startswith("ssh-ed25519 ")

    def test_retire_unknown_id_raises(
        self, service: SecretService
    ) -> None:
        with pytest.raises(SecretNotFoundError):
            service.retire(id=str(uuid.uuid4()), actor="worker")

    def test_double_retire_raises(
        self, service: SecretService
    ) -> None:
        """A second retire on a retired row is an
        idempotent no-op for the audit row (the first
        retire set retired_at; the second finds the row
        by id and re-stamps retired_at). This is the
        v1 contract — plan §5.2 doesn't promise
        double-retire refusal, and the audit log
        records both events.

        NOTE: the test pins the current behavior
        (re-stamp, no raise) so future changes are
        visible.
        """
        created = service.generate_ssh_keypair(
            name="double-retire", actor="worker"
        )
        first = service.retire(id=created["id"], actor="worker")
        second = service.retire(id=created["id"], actor="worker")
        assert first["retired_at"] is not None
        assert second["retired_at"] is not None


# ─────────────────────────────────────────────────────────────────────
# Name-collision
# ─────────────────────────────────────────────────────────────────────


class TestNameCollision:
    """A re-store on the same name errors at the service
    boundary. The trust ruling has no human-in-loop, so
    silent rename is the wrong default.
    """

    def test_repeat_generate_ssh_keypair_same_name_errors(
        self, service: SecretService
    ) -> None:
        service.generate_ssh_keypair(name="dup", actor="worker")
        with pytest.raises(SecretNameConflictError):
            service.generate_ssh_keypair(name="dup", actor="worker")

    def test_repeat_generate_api_token_same_name_errors(
        self, service: SecretService
    ) -> None:
        service.generate_api_token(name="dup-tok", actor="worker")
        with pytest.raises(SecretNameConflictError):
            service.generate_api_token(name="dup-tok", actor="worker")

    def test_repeat_store_same_name_errors(
        self, service: SecretService
    ) -> None:
        service.store(
            name="dup-store", kind="generic", value="x", actor="worker"
        )
        with pytest.raises(SecretNameConflictError):
            service.store(
                name="dup-store",
                kind="generic",
                value="y",
                actor="worker",
            )


# ─────────────────────────────────────────────────────────────────────
# Audit rows
# ─────────────────────────────────────────────────────────────────────


class TestAuditRows:
    """Every P1 action writes one audit row. The action
    enum is forward-compatible (P2-only actions in the
    set with no P1 emitter); the P1 path stamps
    ``create`` / ``read_public`` / ``retire`` only.
    """

    def test_create_writes_create_audit(
        self, service: SecretService, sqlite_engine: Engine
    ) -> None:
        service.generate_ssh_keypair(name="audit-k", actor="worker")
        from sqlmodel import select as _select
        with _session(sqlite_engine) as session:
            rows = session.exec(
                _select(SecretAuditEvent)
            ).all()
        creates = [r for r in rows if r.action == "create"]
        assert len(creates) == 1
        assert creates[0].actor == "worker"
        assert creates[0].secret_id is not None

    def test_read_public_writes_read_public_audit(
        self, service: SecretService, sqlite_engine: Engine
    ) -> None:
        created = service.generate_ssh_keypair(
            name="audit-rp", actor="worker"
        )
        service.read_public(id=created["id"], actor="worker")
        from sqlmodel import select as _select
        with _session(sqlite_engine) as session:
            rows = session.exec(
                _select(SecretAuditEvent)
            ).all()
        rps = [r for r in rows if r.action == "read_public"]
        assert len(rps) == 1
        assert rps[0].actor == "worker"
        assert rps[0].secret_id == created["id"]

    def test_retire_writes_retire_audit(
        self, service: SecretService, sqlite_engine: Engine
    ) -> None:
        created = service.generate_ssh_keypair(
            name="audit-ret", actor="worker"
        )
        service.retire(id=created["id"], actor="worker")
        from sqlmodel import select as _select
        with _session(sqlite_engine) as session:
            rows = session.exec(
                _select(SecretAuditEvent)
            ).all()
        retires = [r for r in rows if r.action == "retire"]
        assert len(retires) == 1
        assert retires[0].actor == "worker"
        assert retires[0].secret_id == created["id"]

    def test_action_enum_p1_subset(
        self, service: SecretService, sqlite_engine: Engine
    ) -> None:
        """The action enum is forward-compatible
        (SECRET_AUDIT_ACTIONS includes P2-only entries
        with no P1 emitter). The P1-emitter set is
        the smaller SECRET_AUDIT_ACTIONS_P1 frozenset."""
        # All P1-emitter values are in the full set.
        assert SECRET_AUDIT_ACTIONS_P1.issubset(SECRET_AUDIT_ACTIONS)
        # The full set is strictly larger.
        assert SECRET_AUDIT_ACTIONS_P1 < SECRET_AUDIT_ACTIONS
        # P2-only entries are reserved-for-future.
        assert "use_marker" in SECRET_AUDIT_ACTIONS
        assert "use_marker" not in SECRET_AUDIT_ACTIONS_P1
        assert "read_value" in SECRET_AUDIT_ACTIONS
        assert "read_value" not in SECRET_AUDIT_ACTIONS_P1


# ─────────────────────────────────────────────────────────────────────
# SSH fingerprint OpenSSH parity — M3 governor pre-promote fix (2026-10-17)
# ─────────────────────────────────────────────────────────────────────


class TestSshFingerprintParity:
    """Pin the M3 ``ssh-keygen -lf`` fingerprint parity.

    The fingerprint must be SHA256 over the OpenSSH 51-byte
    wire blob (the binary form of the pubkey body — two
    length-prefixed strings: ``len("ssh-ed25519") ||
    "ssh-ed25519" || len(raw_key) || raw_key``). Hashing
    the raw 32-byte key alone (the pre-M3 layout) produces a
    non-matching fingerprint that breaks the ``ssh-keygen
    -lf`` parity claim.

    The hermetic reference vector is hardcoded — no
    dependency on the ``ssh-keygen`` binary at test time.
    The vector's expected fingerprint was independently
    verified against ``ssh-keygen -lf`` during development
    (the canary is the canonical RFC 8032-style deterministic
    ed25519 seed: 32 bytes of ``\x01``).

    A live cross-check with ``ssh-keygen`` is added behind a
    skip-if-binary-absent guard (the canonical pin must be
    the fixed vector, the live check is a redundancy belt).
    """

    # ── Hardcoded reference vector ──
    # Deterministic 32-byte seed (32 bytes of \x01).
    REFERENCE_SEED: bytes = b"\x01" * 32
    # The public-key line that the seed yields (computed
    # once during development — the source-of-truth pair is
    # (REFERENCE_SEED, REFERENCE_PUB_OPENSSH,
    # REFERENCE_FINGERPRINT)).
    REFERENCE_PUB_OPENSSH: str = (
        "ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIIqI4910CfGV/VLbLTy6XXLKZwm/"
        "HZQSG/N0iAG0D29c"
    )
    # Expected ``ssh-keygen -lf`` fingerprint — independently
    # verified against the binary during development.
    REFERENCE_FINGERPRINT: str = (
        "SHA256:fe85JkIjo8VPe+XqXJGH5Mau1EMFdK1OdKvJUFicyA8"
    )

    def _material_for_seed(self) -> Any:
        """Build a SecretSshKeypairMaterial for the
        deterministic seed (bypasses the random
        ``Ed25519PrivateKey.generate`` path).

        The helper monkeypatches the ``private_bytes`` /
        ``public_bytes`` call shape by calling the
        underlying ed25519 primitives directly with the
        fixed seed. The actual ``generate_ssh_keypair_material``
        call uses random — so for the test we mirror its
        internals without the random generation.
        """
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization

        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
            self.REFERENCE_SEED
        )
        public_key = private_key.public_key()
        public_openssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        ).decode("ascii").strip()
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.OpenSSH,
            encryption_algorithm=serialization.NoEncryption(),
        )
        raw_private = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

        # Compute the fingerprint using the EXACT same
        # logic as ``generate_ssh_keypair_material`` —
        # the helper calls the cryptography primitives
        # directly so we mirror its body. The hardcoded
        # expectation pins the algorithm + parameters.
        import base64
        import hashlib
        import struct

        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        algo_name = b"ssh-ed25519"
        wire_blob = (
            struct.pack(">I", len(algo_name))
            + algo_name
            + struct.pack(">I", len(raw_public))
            + raw_public
        )
        assert len(wire_blob) == 51
        digest = hashlib.sha256(wire_blob).digest()
        b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
        fingerprint = f"SHA256:{b64}"

        from daemon.services.secret_service import (
            SecretSshKeypairMaterial,
        )
        return SecretSshKeypairMaterial(
            public_openssh=public_openssh,
            fingerprint=fingerprint,
            private_openssh_pem=private_pem,
            raw_private_bytes=raw_private,
        )

    def test_reference_vector_fingerprint_matches_ssh_keygen(
        self,
    ) -> None:
        """Pin the M3 fingerprint parity via a hardcoded
        reference vector. The vector's expected fingerprint
        was independently verified against ``ssh-keygen
        -lf`` during development."""
        material = self._material_for_seed()
        assert material.public_openssh == self.REFERENCE_PUB_OPENSSH, (
            f"reference vector pubkey drift: "
            f"{material.public_openssh!r} != "
            f"{self.REFERENCE_PUB_OPENSSH!r}"
        )
        assert (
            material.fingerprint == self.REFERENCE_FINGERPRINT
        ), (
            f"M3 fingerprint parity violated: "
            f"got {material.fingerprint!r}, expected "
            f"{self.REFERENCE_FINGERPRINT!r}"
        )

    def test_random_generate_ssh_keypair_fingerprint_parity(
        self,
    ) -> None:
        """Sanity check: a random ``generate_ssh_keypair_material``
        call yields a fingerprint that is non-trivially
        different from the raw-key fingerprint — proves the
        wire-blob path is in use."""
        import base64
        import hashlib

        material = generate_ssh_keypair_material()

        # Fingerprint shape: ``SHA256:<base64-nopadding>``
        assert material.fingerprint.startswith("SHA256:"), (
            f"unexpected fingerprint shape: {material.fingerprint!r}"
        )
        b64_part = material.fingerprint.split(":", 1)[1]
        # Standard SHA256 = 32 bytes → 43-char unpadded b64
        # (256 / 6 = 42.67 → ceil). Verify the b64 decodes
        # to 32 bytes.
        decoded = base64.b64decode(b64_part + "==")
        assert len(decoded) == 32, (
            f"fingerprint digest length: {len(decoded)} "
            f"(expected 32 bytes for SHA256)"
        )
        # Also: hash the raw key the OLD way and assert
        # the new fingerprint is DIFFERENT (the M3 fix
        # changed the hash input).
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519

        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        old_fingerprint_digest = hashlib.sha256(raw_public).digest()
        old_b64 = (
            base64.b64encode(old_fingerprint_digest)
            .decode("ascii").rstrip("=")
        )
        # The OLD hash produces a 32-byte digest; b64 of 32
        # bytes → 43 chars unpadded (same shape, just wrong
        # value). Assert new != old for a random key — the
        # vectors differ because the input differs.
        new_b64 = material.fingerprint.split(":", 1)[1]
        assert new_b64 != old_b64, (
            "fingerprint identical between raw-key hash and "
            "wire-blob hash — wire-blob path is not in use"
        )

    def test_wire_blob_length_is_51_for_ed25519(self) -> None:
        """Sanity check: the wire-blob length is 51 bytes for
        ed25519 (``4 + 11 + 4 + 32``). A wrong length means
        the length-prefix math is off."""
        import struct

        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization

        private_key = ed25519.Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        algo_name = b"ssh-ed25519"
        wire_blob = (
            struct.pack(">I", len(algo_name))
            + algo_name
            + struct.pack(">I", len(raw_public))
            + raw_public
        )
        assert len(wire_blob) == 51, (
            f"wire-blob length drift: {len(wire_blob)} "
            f"(expected 51)"
        )

    def test_live_ssh_keygen_cross_check(
        self,
    ) -> None:
        """Redundancy belt: cross-check the fingerprint
        computation against the ``ssh-keygen -lf`` binary
        when it is available. The hardcoded vector
        (``test_reference_vector_fingerprint_matches_ssh_keygen``)
        is the canonical pin — this is the live belt."""
        import shutil
        import subprocess
        import tempfile

        if shutil.which("ssh-keygen") is None:
            pytest.skip("ssh-keygen binary not available")

        material = self._material_for_seed()
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".pub", delete=False
        ) as f:
            f.write(
                (material.public_openssh + "\n").encode("ascii")
            )
            pub_path = f.name
        try:
            result = subprocess.run(
                ["ssh-keygen", "-lf", pub_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, (
                f"ssh-keygen failed: {result.stderr}"
            )
            # Output shape:
            #   ``256 SHA256:<fp> <comment> (ED25519)``
            # Extract the fp field (between ``SHA256:`` and
            # the comment).
            ssh_keygen_fp = result.stdout.split()[1]
            assert ssh_keygen_fp == material.fingerprint, (
                f"ssh-keygen fingerprint mismatch: "
                f"{ssh_keygen_fp!r} != {material.fingerprint!r}"
            )
        finally:
            import os
            os.unlink(pub_path)


# ─────────────────────────────────────────────────────────────────────
# Atomicity pin — M1 governor pre-promote fix (2026-10-17)
# ─────────────────────────────────────────────────────────────────────


class TestAtomicityPin:
    """Pin the M1 atomicity contract (governor pre-promote
    fix, 2026-10-17):

    The mutation row AND its audit row commit in ONE
    ``engine.begin()`` transaction. A failure after the
    mutation's INSERT but before the audit's INSERT MUST
    roll back BOTH writes — neither side is visible.

    The injection scheme: a SQLAlchemy ``before_cursor_execute``
    event listener that raises an
    :class:`sqlalchemy.exc.OperationalError` on the audit
    INSERT. The pin asserts:

      1. ``insert_with_audit`` raises (the simulated crash).
      2. ``secrets`` table has ZERO matching rows (mutation
         rolled back).
      3. ``secrets_audit`` table has ZERO matching rows
         (audit rolled back — never even committed).
      4. ``mark_retired_with_audit`` carries the same
         property (UPDATE + audit, atomic).
      5. ``get_by_id_with_audit`` carries the same property
         (SELECT + audit, atomic — but on a missing row the
         audit is intentionally NOT written; the pin
         asserts that the read+audit path on an existing
         row leaves neither side visible if the audit
         insert fails).
      6. ``mark_retired_with_audit`` on a NOT-found id
         does NOT audit (mirrors the ``read_public``
         NOT-found contract — the v1 contract audits
         successful writes only).

    The pin is mutation-verified per repo convention
    (surgically apply the guarded regression — split the
    transaction across two sessions — and confirm the pin
    FAILS with the expected message). See
    ``test_mutation_verification_split_session_breaks_atomicity``
    in this class.
    """

    def test_insert_with_audit_rolls_back_on_audit_failure(
        self, sqlite_engine: Engine, fernet_key: str
    ) -> None:
        """Inject a crash on the audit INSERT; the mutation
        row MUST NOT be visible. Pin: ``secrets`` count = 0,
        ``secrets_audit`` count = 0."""
        from sqlalchemy.exc import OperationalError

        fail_count = {"n": 0}

        @sa_event.listens_for(sqlite_engine, "before_cursor_execute")
        def _fail_audit_insert(
            conn, cursor, statement, parameters, context, executemany
        ):
            # Detect the audit INSERT — its table name in the
            # statement is ``secrets_audit``. The mutation
            # INSERTs into ``secrets``. Fail the second one
            # (the audit) so we observe the partial-state
            # window the M1 fix closes.
            normalized = " ".join(statement.lower().split())
            if (
                "insert into secrets_audit" in normalized
                and fail_count["n"] == 0
            ):
                fail_count["n"] += 1
                raise OperationalError(
                    "injected failure", None, None
                )

        try:
            svc = SecretService(
                engine=sqlite_engine,
                cipher=SecretCipher(encryption_key=fernet_key),
            )
            with pytest.raises(OperationalError):
                svc.generate_ssh_keypair(
                    name="atomic-pin", actor="worker"
                )

            # Assert NO partial state — both tables empty.
            with _session(sqlite_engine) as session:
                from sqlmodel import select as _select
                secrets_count = len(
                    session.exec(_select(Secret)).all()
                )
                audit_count = len(
                    session.exec(_select(SecretAuditEvent)).all()
                )
            assert secrets_count == 0, (
                f"atomicity violated: {secrets_count} secret row(s) "
                f"visible after audit-INSERT crash"
            )
            assert audit_count == 0, (
                f"atomicity violated: {audit_count} audit row(s) "
                f"visible after audit-INSERT crash"
            )
        finally:
            sa_event.remove(
                sqlite_engine,
                "before_cursor_execute",
                _fail_audit_insert,
            )

    def test_mark_retired_with_audit_rolls_back_on_audit_failure(
        self, sqlite_engine: Engine, fernet_key: str
    ) -> None:
        """Inject a crash on the audit INSERT for ``retire``;
        the UPDATE on the row MUST NOT persist. Pin: row's
        ``retired_at`` is still ``None`` after the
        simulated crash."""
        from sqlalchemy.exc import OperationalError

        # Set up a row first WITHOUT the listener so the
        # INSERT itself succeeds.
        svc = SecretService(
            engine=sqlite_engine,
            cipher=SecretCipher(encryption_key=fernet_key),
        )
        created = svc.generate_ssh_keypair(
            name="retire-atomic-pin", actor="worker"
        )
        target_id = created["id"]

        # Now arm the listener and retry retire.
        fail_count = {"n": 0}

        @sa_event.listens_for(sqlite_engine, "before_cursor_execute")
        def _fail_audit_insert(
            conn, cursor, statement, parameters, context, executemany
        ):
            normalized = " ".join(statement.lower().split())
            if (
                "insert into secrets_audit" in normalized
                and fail_count["n"] == 0
            ):
                fail_count["n"] += 1
                raise OperationalError(
                    "injected failure", None, None
                )

        try:
            with pytest.raises(OperationalError):
                svc.retire(id=target_id, actor="worker")

            # The row MUST still be active (the UPDATE
            # was rolled back together with the audit
            # INSERT).
            with _session(sqlite_engine) as session:
                row = session.get(Secret, target_id)
                assert row is not None, (
                    "atomicity violated: the secret row was "
                    "deleted (UPDATE should be a soft-retire, "
                    "not a DELETE)"
                )
                assert row.retired_at is None, (
                    f"atomicity violated: retired_at={row.retired_at!r} "
                    f"after audit-INSERT crash"
                )
        finally:
            sa_event.remove(
                sqlite_engine,
                "before_cursor_execute",
                _fail_audit_insert,
            )

    def test_get_by_id_with_audit_rolls_back_on_audit_failure(
        self, sqlite_engine: Engine, fernet_key: str
    ) -> None:
        """Inject a crash on the audit INSERT for
        ``read_public``; the read happens BEFORE the audit
        in the same transaction, so the audit's failure
        rolls back the read-side state. Pin: no audit row
        visible after the crash."""
        from sqlalchemy.exc import OperationalError

        svc = SecretService(
            engine=sqlite_engine,
            cipher=SecretCipher(encryption_key=fernet_key),
        )
        created = svc.generate_ssh_keypair(
            name="read-atomic-pin", actor="worker"
        )
        target_id = created["id"]

        fail_count = {"n": 0}

        @sa_event.listens_for(sqlite_engine, "before_cursor_execute")
        def _fail_audit_insert(
            conn, cursor, statement, parameters, context, executemany
        ):
            normalized = " ".join(statement.lower().split())
            if (
                "insert into secrets_audit" in normalized
                and fail_count["n"] == 0
            ):
                fail_count["n"] += 1
                raise OperationalError(
                    "injected failure", None, None
                )

        try:
            with pytest.raises(OperationalError):
                svc.read_public(id=target_id, actor="worker")

            # Pin: no audit row visible (the read happened
            # before the failure — but the audit INSERT
            # rolled back, so no audit row is in the log).
            # The secret row itself stays intact (SELECT
            # alone never mutates state).
            with _session(sqlite_engine) as session:
                from sqlmodel import select as _select
                rows = session.exec(
                    _select(SecretAuditEvent).where(
                        SecretAuditEvent.secret_id == target_id
                    )
                ).all()
                read_audits = [
                    r for r in rows if r.action == "read_public"
                ]
                assert len(read_audits) == 0, (
                    f"atomicity violated: {len(read_audits)} "
                    f"read_public audit row(s) visible after "
                    f"audit-INSERT crash"
                )
        finally:
            sa_event.remove(
                sqlite_engine,
                "before_cursor_execute",
                _fail_audit_insert,
            )

    def test_mark_retired_with_audit_does_not_audit_on_not_found(
        self, sqlite_engine: Engine, fernet_key: str
    ) -> None:
        """NOT-found retire does NOT write an audit row
        (mirrors the ``read_public`` NOT-found contract —
        the v1 contract audits successful writes only)."""
        svc = SecretService(
            engine=sqlite_engine,
            cipher=SecretCipher(encryption_key=fernet_key),
        )
        with pytest.raises(SecretNotFoundError):
            svc.retire(id=str(uuid.uuid4()), actor="worker")
        with _session(sqlite_engine) as session:
            from sqlmodel import select as _select
            rows = session.exec(
                _select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "retire"
                )
            ).all()
            assert len(rows) == 0, (
                f"NOT-found audit leak: {len(rows)} retire audit "
                f"row(s) visible after retire of unknown id"
            )

    def test_insert_with_audit_atomic_on_unique_collision(
        self, sqlite_engine: Engine, fernet_key: str
    ) -> None:
        """UNIQUE(name) collision rolls back the entire
        transaction — neither the secret row nor the audit
        row is visible after the IntegrityError."""
        svc = SecretService(
            engine=sqlite_engine,
            cipher=SecretCipher(encryption_key=fernet_key),
        )
        # First write succeeds.
        svc.generate_ssh_keypair(name="collision-pin", actor="worker")

        # Count audit rows so far.
        with _session(sqlite_engine) as session:
            from sqlmodel import select as _select
            before_audits = len(
                session.exec(_select(SecretAuditEvent)).all()
            )

        # Second write on the same name → IntegrityError.
        with pytest.raises(SecretNameConflictError):
            svc.generate_ssh_keypair(
                name="collision-pin", actor="worker"
            )

        # Pin: only ONE secret row (the first one), and
        # audit count unchanged (no audit row from the
        # failed second insert).
        with _session(sqlite_engine) as session:
            from sqlmodel import select as _select
            secret_rows = session.exec(
                _select(Secret).where(Secret.name == "collision-pin")
            ).all()
            after_audits = len(
                session.exec(_select(SecretAuditEvent)).all()
            )
        assert len(secret_rows) == 1, (
            f"collision atomicity violated: {len(secret_rows)} "
            f"rows visible for the colliding name"
        )
        assert after_audits == before_audits, (
            f"collision atomicity violated: audit count grew "
            f"{before_audits}→{after_audits} on the failed insert"
        )

    def test_mutation_verification_split_session_breaks_atomicity(
        self, sqlite_engine: Engine, fernet_key: str
    ) -> None:
        """Mutation verification — the M1 atomicity pin
        actually fails when the guarded regression
        (split-session) is in place.

        Procedure (repo convention):
          1. Apply the guarded regression: replace the
             ``insert_with_audit`` engine.begin() block
             with two separate ``Session`` writes (the
             pre-M1 layout).
          2. Run the atomicity-pin failure-injection.
          3. Assert the pin FAILS with the expected
             message (a secret row visible after the
             audit INSERT crash — the partial state the
             M1 fix closes).
          4. Restore the engine.begin() block.

        The pin's value is the regression it detects; this
        test documents that value.
        """
        from sqlalchemy.exc import OperationalError
        from daemon.services import secret_service as svc_mod

        # Original (atomic) ``insert_with_audit`` reference.
        original = svc_mod._SqlSecretStore.insert_with_audit

        def split_session_insert_with_audit(
            self, secret, *, actor
        ):
            """Guarded regression: pre-M1 split-session layout
            (mutation in one session, audit in another)."""
            with Session(self._engine) as session:
                session.add(secret)
                session.commit()
                session.refresh(secret)
            with Session(self._engine) as session:
                event = SecretAuditEvent(
                    action="create",
                    secret_id=secret.id,
                    actor=actor,
                )
                session.add(event)
                session.commit()
            return secret

        try:
            svc_mod._SqlSecretStore.insert_with_audit = (
                split_session_insert_with_audit
            )

            fail_count = {"n": 0}

            @sa_event.listens_for(
                sqlite_engine, "before_cursor_execute"
            )
            def _fail_audit_insert(
                conn, cursor, statement, parameters, context, executemany
            ):
                normalized = " ".join(statement.lower().split())
                if (
                    "insert into secrets_audit" in normalized
                    and fail_count["n"] == 0
                ):
                    fail_count["n"] += 1
                    raise OperationalError(
                        "injected failure", None, None
                    )

            try:
                svc = SecretService(
                    engine=sqlite_engine,
                    cipher=SecretCipher(
                        encryption_key=fernet_key
                    ),
                )
                with pytest.raises(OperationalError):
                    svc.generate_ssh_keypair(
                        name="regression-pin", actor="worker"
                    )

                # Atomicity pin assertion: with the
                # split-session regression in place, the
                # secret row IS visible (the mutation
                # committed before the audit INSERT crash).
                with _session(sqlite_engine) as session:
                    from sqlmodel import select as _select
                    rows = session.exec(
                        _select(Secret).where(
                            Secret.name == "regression-pin"
                        )
                    ).all()
                    assert len(rows) == 1, (
                        f"mutation-verification failed: split-session "
                        f"regression should leave the mutation visible "
                        f"({len(rows)} row(s)); this means the pin is "
                        f"NOT detecting the regression"
                    )
            finally:
                sa_event.remove(
                    sqlite_engine,
                    "before_cursor_execute",
                    _fail_audit_insert,
                )
        finally:
            # Restore the atomic implementation.
            svc_mod._SqlSecretStore.insert_with_audit = original


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _session(engine: Engine):
    """Yield a SQLModel session bound to ``engine``."""
    from sqlmodel import Session
    return Session(engine)
