"""Unit tests for :mod:`daemon.services.secret_resolver`.

The resolver is the SHARED seam between the agent-secrets
service (the SELECT → unwrap → audit kernel) and the four
Phase-3 consumers (bash subprocess env overlay, proc
subprocess env, MCP cold/warm spawns, tool-param pre-invoke).
This module pins the resolver kernel's contract:

* **Byte-identical passthrough** for non-marker values —
  R1 invariant (caller's dict never mutated).
* **NEW-dict return** — same contract as the KMS resolver
  at ``daemon/services/kms_resolver.py:149``.
* **Typed envelope translation** at the wrapper boundary:
  the resolver catches ``SecretCipherUnavailable`` (raw,
  raised by ``SecretCipher`` at the service boundary) and
  maps it to ``SecretMarkerResolutionError`` carrying
  ``code=secret_cipher_unavailable``.
* **Marker malformed pre-DB** — values that LOOK like
  markers (start with the prefix) but fail the fullmatch
  raise ``secret_marker_malformed`` at THIS layer, BEFORE
  the service is called.
* **No ``secret_service`` defaults** — explicit injection
  only (zero import-time side effects on paths without
  markers; design §0).
* **Surface / context mapping** — the envelope carries the
  surface (``bash.env``); the audit row carries the context
  (``bash_env``). The resolver maps surface → context at
  the service call.
* **Partial-env abort** — any value failure propagates
  WITHOUT a partial dict returned (fail-closed; design §1a).

Test execution is fenced per the project critical notes:

* ``ENSEMBLE_SELF_ENV``-free: no daemon boot; pure
  in-process tests against a file-backed SQLite engine.
* Run ONLY this file (``--tb=short -q``), never bare
  pytest, never ``-x``, never full-suite (Phase 2 follows
  the same fence convention as the secrets service tests).
* mktemp FAKE_HOME + escape guard — no HOME-derived paths.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session

# Register the SQLModel tables before create_all runs.
from daemon.repositories.secrets.models import (  # noqa: F401
    SECRET_AUDIT_ACTIONS,
    Secret,
    SecretAuditEvent,
)
from daemon.services.secret_service import (
    ENS_SECRET_MARKER_PREFIX,
    ENS_SECRET_MARKER_RE_COMPILED,
    SECRET_AUDIT_RESOLVE_CONTEXTS,
    SECRET_CODE_CIPHER_UNAVAILABLE,
    SECRET_CODE_MARKER_MALFORMED,
    SECRET_CODE_NOT_FOUND,
    SECRET_CODE_STORE_UNAVAILABLE,
    SECRET_ENVELOPE_SURFACES,
    SecretCipher,
    SecretCipherUnavailable,
    SecretMarkerResolutionError,
    SecretService,
    mint_marker,
    parse_marker,
)
from daemon.services.secret_resolver import (
    is_secret_marker,
    resolve_marker,
    resolve_secret_markers,
)


# ─────────────────────────────────────────────────────────────────────
# Test fixtures (mirror ``test_secret_service.py``)
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    """Build a file-backed SQLite engine with the house
    PRAGMA set. Mirrors the convention in
    ``tests/unit/test_secret_service.py`` — inlined here so
    the resolver tests are self-contained (no cross-file
    fixture coupling).
    """
    db_path = tmp_path / "secret-resolver-test.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

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
    owns the dispose lifecycle."""
    eng = _make_sqlite_engine(tmp_path)
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def fernet_key() -> str:
    """Fresh Fernet key for each test."""
    return Fernet.generate_key().decode()


@pytest.fixture
def service(
    sqlite_engine: Engine, fernet_key: str
) -> Iterator[SecretService]:
    """Yield a fully-wired SecretService with the cipher
    explicitly constructed from a known key."""
    yield SecretService(
        engine=sqlite_engine,
        cipher=SecretCipher(encryption_key=fernet_key),
    )


def _session(engine: Engine) -> Session:
    """Yield a SQLModel session bound to ``engine``."""
    return Session(engine)


# ─────────────────────────────────────────────────────────────────────
# Marker parsing predicates
# ─────────────────────────────────────────────────────────────────────


class TestIsSecretMarker:
    """The :func:`is_secret_marker` predicate is the cheap
    pre-check for callers that want to distinguish marker
    values from plaintext WITHOUT performing the lookup.

    A value that STARTS WITH the prefix but fails fullmatch
    (malformed UUID4 inside) is NOT a secret marker — it is
    a malformed marker (caught by :func:`resolve_marker`
    with ``secret_marker_malformed``). Callers needing to
    surface malformed markers should branch on the prefix
    explicitly.
    """

    def test_full_marker_is_true(self) -> None:
        marker = mint_marker(str(uuid.uuid4()))
        assert is_secret_marker(marker) is True

    def test_plaintext_is_false(self) -> None:
        assert is_secret_marker("plaintext-value") is False

    def test_empty_string_is_false(self) -> None:
        assert is_secret_marker("") is False

    def test_prefix_only_is_false(self) -> None:
        """Prefix without UUID4 is malformed, not a marker."""
        assert is_secret_marker(ENS_SECRET_MARKER_PREFIX) is False

    def test_prefix_with_bad_uuid_is_false(self) -> None:
        """Prefix + non-UUID4 is malformed, not a marker."""
        assert is_secret_marker(
            "__ENS_SECRET__not-a-uuid__"
        ) is False

    def test_prefix_with_uuid_but_no_suffix_is_false(self) -> None:
        """Prefix + UUID4 without the closing fence is malformed."""
        bare_uuid = str(uuid.uuid4())
        assert is_secret_marker(
            ENS_SECRET_MARKER_PREFIX + bare_uuid
        ) is False

    def test_kms_lite_lanes_are_false(self) -> None:
        """The KMS-Lite lanes are NOT secret markers — the
        discriminator is the prefix (``__ENS_SECRET__`` vs
        ``__KMS_REF__`` / ``__KMS_ENV__``)."""
        assert is_secret_marker(
            "__KMS_REF__KMS_HANDLE_a1b2__"
        ) is False
        assert is_secret_marker(
            "__KMS_ENV__OPENAI_API_KEY__"
        ) is False

    def test_distinct_from_regex_fullmatch(self) -> None:
        """The resolver's predicate uses the SAME compiled
        regex as the validator (``ENS_SECRET_MARKER_RE_COMPILED``)
        — the two MUST agree on every well-formed marker."""
        marker = mint_marker(str(uuid.uuid4()))
        assert is_secret_marker(marker) == (
            ENS_SECRET_MARKER_RE_COMPILED.fullmatch(marker)
            is not None
        )


# ─────────────────────────────────────────────────────────────────────
# resolve_marker — single-value form
# ─────────────────────────────────────────────────────────────────────


class TestResolveMarkerSingleValue:
    """The single-value form is the tool-param pre-invoke
    seam (design §1d). It takes a single string and returns
    the resolved plaintext (or byte-identical passthrough
    for non-markers). The default surface is ``tool_param``.
    """

    def test_resolve_marker_returns_plaintext_on_marker(
        self, service: SecretService
    ) -> None:
        """Happy path: a marker value resolves to its plaintext."""
        minted = service.store(
            name="res-happy", kind="generic",
            value="api-token-secret", actor="worker",
        )
        marker = minted["marker"]
        plaintext = resolve_marker(
            marker, secret_service=service, actor="worker",
        )
        assert plaintext == "api-token-secret", (
            f"got {plaintext!r}"
        )

    def test_resolve_marker_byte_identical_for_non_marker(
        self, service: SecretService
    ) -> None:
        """Non-marker values pass through BYTE-IDENTICAL —
        the resolver is a NO-OP for values the seams didn't
        declare as ``secret_typed_args``."""
        cases = [
            "plaintext-value",
            "",
            "LOG_LEVEL=INFO",
            "1234567890",
            # Adjacent-but-not-matching: a string that
            # contains the prefix as a SUBSTRING (not at
            # the start) is NOT a marker and passes through.
            f"prefixed-but-not-marker: {ENS_SECRET_MARKER_PREFIX}foo",
        ]
        for value in cases:
            assert (
                resolve_marker(value, secret_service=service)
                == value
            ), f"non-marker {value!r} was mutated by resolver"

    def test_resolve_marker_does_not_mutate_callers_string(
        self, service: SecretService
    ) -> None:
        """R1 invariant — the resolver NEVER mutates its
        input (mirrors kms_resolver._resolve_value)."""
        minted = service.store(
            name="no-mutate", kind="generic",
            value="the-plaintext", actor="worker",
        )
        marker = minted["marker"]
        original = marker  # str is immutable, but we pin the intent
        resolve_marker(marker, secret_service=service)
        # String identity preserved.
        assert marker == original

    def test_resolve_marker_malformed_raises_typed_code(
        self, service: SecretService
    ) -> None:
        """A value that has the marker prefix but fails
        fullmatch → typed ``secret_marker_malformed``
        envelope at the resolver wrapper, pre-DB. The raw
        value is carried in ``secret_id`` (PB-F1: the raw
        value is NOT the secret value — it is the
        malformed shape the operator passed in)."""
        malformed = f"{ENS_SECRET_MARKER_PREFIX}not-a-uuid__"
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_marker(
                malformed, secret_service=service, actor="worker",
            )
        assert excinfo.value.code == SECRET_CODE_MARKER_MALFORMED
        assert excinfo.value.surface == "tool_param"
        assert excinfo.value.secret_id == malformed

    def test_resolve_marker_malformed_raw_payload_never_in_envelope(
        self, service: SecretService
    ) -> None:
        """P2 review advisory pin: even when the malformed
        marker SHAPE happens to embed an obvious plaintext
        payload (the kind a future operator might mistake
        for a stored secret ID), that payload MUST appear
        NOWHERE in ``str(exc)`` or ``repr(exc)`` — the
        resolver wrapper leaks at most the code prefix
        ``"secret resolution failed: secret_marker_malformed"``.
        The raw payload IS stored on ``self.secret_id`` for
        operator diagnostics; it is NOT crossed onto the
        envelope surface.

        Today this contract is enforced structurally (the
        ``SecretMarkerResolutionError.__init__`` passes a
        hardcoded message to ``super().__init__`` and never
        includes ``secret_id`` in any serialized form). The
        pin locks that invariant — a future refactor that
        interpolates ``secret_id`` into the message string
        trips it."""
        plaintext_payload = "my-real-secret-key"
        malformed_with_payload = (
            f"{ENS_SECRET_MARKER_PREFIX}{plaintext_payload}__"
        )
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_marker(
                malformed_with_payload,
                secret_service=service, actor="worker",
            )
        # The hardcoded envelope phrasing is the ONLY
        # serialized shape — secret_id is operator-diagnostics
        # only (see SecretMarkerResolutionError docstring).
        assert str(excinfo.value) == (
            "secret resolution failed: secret_marker_malformed"
        ), (
            "str(exc) drifted from the hardcoded envelope "
            "template — secret_id may have leaked into the "
            f"message: {str(excinfo.value)!r}"
        )
        # The plaintext payload is held on the attribute for
        # postmortem use; it MUST NOT cross the envelope
        # boundary via ``str`` or ``repr``.
        for rendered in (str(excinfo.value), repr(excinfo.value)):
            assert plaintext_payload not in rendered, (
                f"plaintext payload leaked into envelope "
                f"rendering ({rendered!r}) — secret_id may "
                "have been serialized"
            )
        # Defense-in-depth: the attribute-level payload is
        # preserved (operator diagnostics work), even though
        # the envelope rendering hides it.
        assert excinfo.value.secret_id == malformed_with_payload

    def test_resolve_marker_no_secret_service_raises_unconfigured(
        self, service: SecretService
    ) -> None:
        """No module-level default — explicit injection only
        (zero import-time side effects on paths without
        markers; design §0). An un-wired caller raises the
        store-unavailable envelope (a configuration error
        is the same trust class as a DB outage for the
        seams)."""
        minted = service.store(
            name="no-svc", kind="generic",
            value="x", actor="worker",
        )
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_marker(minted["marker"], secret_service=None)
        assert excinfo.value.code == SECRET_CODE_STORE_UNAVAILABLE
        assert excinfo.value.surface == "tool_param"
        assert excinfo.value.secret_id == "<unconfigured>"

    def test_resolve_marker_unconfigured_returns_byte_identical_for_plaintext(
        self,
    ) -> None:
        """Even WITHOUT a ``secret_service``, non-marker values
        pass through byte-identical — the resolver never
        touches plaintext unless a marker is present. (The
        unconfigured-error path fires only on marker
        values.)"""
        assert resolve_marker("plaintext", secret_service=None) == (
            "plaintext"
        )
        assert resolve_marker("", secret_service=None) == ""

    def test_resolve_marker_propagates_not_found_from_service(
        self, service: SecretService
    ) -> None:
        """Service-layer not-found propagates verbatim — the
        resolver does NOT translate it (the service already
        raised the typed envelope)."""
        unknown_marker = mint_marker(str(uuid.uuid4()))
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_marker(
                unknown_marker, secret_service=service,
                actor="worker",
            )
        assert excinfo.value.code == SECRET_CODE_NOT_FOUND
        assert excinfo.value.surface == "tool_param"
        # ``secret_id`` is the inner id (not the marker).
        assert excinfo.value.secret_id == parse_marker(unknown_marker)

    def test_resolve_marker_cipher_unavailable_translates_to_typed_code(
        self,
        sqlite_engine: Engine,
        fernet_key: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The resolver wrapper translates the RAW
        ``SecretCipherUnavailable`` (raised by the service
        at the cipher check) to the typed
        ``secret_cipher_unavailable`` envelope."""
        # Mint with the canonical key.
        minted = SecretService(
            engine=sqlite_engine,
            cipher=SecretCipher(encryption_key=fernet_key),
        ).store(
            name="cipher-down", kind="generic",
            value="x", actor="worker",
        )
        # Now resolve via a service with NO cipher.
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        svc_no_cipher = SecretService(
            engine=sqlite_engine, cipher=SecretCipher(),
        )
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_marker(
                minted["marker"], secret_service=svc_no_cipher,
                actor="worker",
            )
        assert excinfo.value.code == SECRET_CODE_CIPHER_UNAVAILABLE
        assert excinfo.value.surface == "tool_param"

    def test_resolve_marker_carries_configured_actor_to_audit(
        self, service: SecretService
    ) -> None:
        """The resolver's ``actor`` parameter flows through
        to the service-layer audit row — the resolver does
        NOT stamp its own identity; the calling agent
        attribution is preserved."""
        minted = service.store(
            name="actor-flow", kind="generic",
            value="x", actor="worker",
        )
        resolve_marker(
            minted["marker"], secret_service=service,
            actor="custom-actor-id",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(
                _select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "use_marker"
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].actor == "custom-actor-id", (
            f"resolver swallowed the actor; got {rows[0].actor!r}"
        )

    def test_resolve_marker_carries_configured_surface_to_envelope(
        self,
        sqlite_engine: Engine,
        fernet_key: str,
    ) -> None:
        """The resolver's ``surface`` parameter flows onto
        the raised envelope — the seam doesn't need to
        re-derive the surface from the context."""
        # Use a not-found marker so the envelope carries
        # the configured ``surface`` on the typed raise.
        unknown_marker = mint_marker(str(uuid.uuid4()))
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_marker(
                unknown_marker, secret_service=SecretService(
                    engine=sqlite_engine,
                    cipher=SecretCipher(encryption_key=fernet_key),
                ),
                actor="worker", surface="mcp_spawn",
            )
        assert excinfo.value.surface == "mcp_spawn"

    def test_resolve_marker_invalid_surface_raises_validation(
        self, service: SecretService
    ) -> None:
        """An unknown surface is a programmer error — the
        resolver rejects at the public boundary (defensive)."""
        minted = service.store(
            name="bad-surface", kind="generic",
            value="x", actor="worker",
        )
        from daemon.services.secret_service import SecretValidationError

        with pytest.raises(SecretValidationError):
            resolve_marker(
                minted["marker"], secret_service=service,
                actor="worker", surface="bogus_surface",
            )


# ─────────────────────────────────────────────────────────────────────
# resolve_secret_markers — dict form
# ─────────────────────────────────────────────────────────────────────


class TestResolveSecretMarkersDict:
    """The dict form is the spawn-time seam (design §1a +
    §1b + §1c). It returns a NEW dict; non-marker values
    pass through byte-identical; ``None`` → ``None``; any
    value failure aborts the dict (partial-env is
    impossible)."""

    def test_resolve_secret_markers_returns_new_dict(
        self, service: SecretService
    ) -> None:
        """R1 invariant: the resolver NEVER mutates its
        input dict. The returned dict is a fresh object."""
        minted = service.store(
            name="new-dict", kind="generic",
            value="secret-value", actor="worker",
        )
        env = {"API_KEY": minted["marker"], "LOG": "INFO"}
        resolved = resolve_secret_markers(
            env, secret_service=service, actor="worker",
        )
        assert resolved is not env, (
            "resolver returned the same dict object (R1 violation)"
        )
        assert resolved == {
            "API_KEY": "secret-value", "LOG": "INFO",
        }
        # Caller's dict is untouched.
        assert env == {
            "API_KEY": minted["marker"], "LOG": "INFO",
        }

    def test_resolve_secret_markers_none_input(
        self, service: SecretService
    ) -> None:
        """``None`` passes through as ``None`` (mirror of
        ``kms_resolver.resolve_env`` — the env-less case
        for callers that don't carry an overlay)."""
        assert (
            resolve_secret_markers(None, secret_service=service)
            is None
        )

    def test_resolve_secret_markers_empty_dict(
        self, service: SecretService
    ) -> None:
        """Empty dict returns empty dict (NEW object)."""
        resolved = resolve_secret_markers(
            {}, secret_service=service, actor="worker",
        )
        assert resolved == {}
        assert isinstance(resolved, dict)

    def test_resolve_secret_markers_byte_identical_for_non_markers(
        self, service: SecretService
    ) -> None:
        """Every non-marker value passes through byte-
        identical — the resolver is a NO-OP for values
        the seams didn't declare as secret-bearing."""
        env = {
            "PLAIN": "value",
            "EMPTY": "",
            "NUMBER": "12345",
            "WITH_PREFIX_SUBSTRING": (
                f"prefixed-but-not-marker: {ENS_SECRET_MARKER_PREFIX}foo"
            ),
        }
        resolved = resolve_secret_markers(
            env, secret_service=service, actor="worker",
        )
        assert resolved == env, (
            f"non-marker passthrough mutated values: {resolved}"
        )

    def test_resolve_secret_markers_mixed_dict(
        self, service: SecretService
    ) -> None:
        """Mixed dict (markers + plaintext) resolves markers
        and passes plaintext through byte-identical."""
        minted_a = service.store(
            name="mixed-a", kind="generic",
            value="alpha", actor="worker",
        )
        minted_b = service.store(
            name="mixed-b", kind="generic",
            value="beta", actor="worker",
        )
        env = {
            "A_KEY": minted_a["marker"],
            "LOG_LEVEL": "INFO",
            "B_KEY": minted_b["marker"],
            "EMPTY": "",
        }
        resolved = resolve_secret_markers(
            env, secret_service=service, actor="worker",
        )
        assert resolved == {
            "A_KEY": "alpha",
            "LOG_LEVEL": "INFO",
            "B_KEY": "beta",
            "EMPTY": "",
        }

    def test_resolve_secret_markers_partial_failure_aborts_at_first(
        self, service: SecretService
    ) -> None:
        """If ANY value fails, the typed envelope propagates
        and the caller never receives a partial dict (fail-
        closed; design §1a: never partial env). The order
        of iteration is dict-order (Python 3.7+)."""
        minted = service.store(
            name="partial-fail", kind="generic",
            value="good", actor="worker",
        )
        env = {
            "GOOD": minted["marker"],
            "BAD": f"{ENS_SECRET_MARKER_PREFIX}bad-uuid__",
            # Even a successful marker AFTER a failure
            # never resolves — short-circuit on first error.
            "ALSO_GOOD": minted["marker"],
        }
        # R1 failure-path pin (P2 review advisory): snapshot
        # the caller's env BEFORE invoking the resolver; the
        # seam is contracted to never mutate the input dict,
        # even on the failure path (mirror of the success-path
        # pin at :478-480).
        env_snapshot = dict(env)
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_secret_markers(
                env, secret_service=service, actor="worker",
            )
        assert excinfo.value.code == SECRET_CODE_MARKER_MALFORMED
        assert excinfo.value.surface == "bash.env"
        # R1 failure-path pin (P2 review advisory): the seam
        # does NOT mutate-or-partial-decode the caller's env
        # on the way to raising — mirror of the success-path
        # pin at :478-480. Snapshot is taken BEFORE the
        # resolver is invoked.
        assert env == env_snapshot, (
            f"resolver mutated caller's env on the failure "
            f"path: was {env_snapshot!r}, now {env!r}"
        )

    def test_resolve_secret_markers_default_surface_is_bash_env(
        self,
        sqlite_engine: Engine,
        fernet_key: str,
    ) -> None:
        """The dict form's default surface is ``bash.env``
        (design §1a: bash subprocess env overlay is the
        canonical dict-form consumer)."""
        # Force a not-found to inspect the surface
        # carried on the raised envelope.
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_secret_markers(
                {"KEY": mint_marker(str(uuid.uuid4()))},
                secret_service=SecretService(
                    engine=sqlite_engine,
                    cipher=SecretCipher(encryption_key=fernet_key),
                ),
                actor="worker",
            )
        assert excinfo.value.surface == "bash.env", (
            f"default surface drift: {excinfo.value.surface!r}"
        )

    def test_resolve_secret_markers_mcp_spawn_surface(
        self,
        sqlite_engine: Engine,
        fernet_key: str,
    ) -> None:
        """The MCP seams override ``surface`` to
        ``"mcp_spawn"`` (design §1c). The envelope MUST
        carry the override."""
        svc = SecretService(
            engine=sqlite_engine,
            cipher=SecretCipher(encryption_key=fernet_key),
        )
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_secret_markers(
                {"KEY": mint_marker(str(uuid.uuid4()))},
                secret_service=svc, actor="worker",
                surface="mcp_spawn",
            )
        assert excinfo.value.surface == "mcp_spawn"

    def test_resolve_secret_markers_audit_context_is_bash_env(
        self, service: SecretService
    ) -> None:
        """The resolver maps ``bash.env`` envelope surface
        to ``bash_env`` audit context (the dot vs underscore
        form; design §3.4). The audit row's ``context``
        column carries the underscore form."""
        minted = service.store(
            name="audit-mapping", kind="generic",
            value="x", actor="worker",
        )
        resolve_secret_markers(
            {"KEY": minted["marker"]}, secret_service=service,
            actor="worker", surface="bash.env",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(
                _select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "use_marker"
                )
            ).all()
        assert len(rows) == 1
        # Audit context is the underscore form — bash_env.
        assert rows[0].context == "bash_env", (
            f"audit context drift: {rows[0].context!r} "
            f"(expected bash_env)"
        )

    def test_resolve_secret_markers_mcp_audit_context(
        self, service: SecretService
    ) -> None:
        """The MCP audit context is ``mcp_spawn`` (byte-
        identical between envelope surface and audit
        context)."""
        minted = service.store(
            name="mcp-audit", kind="generic",
            value="x", actor="worker",
        )
        resolve_secret_markers(
            {"KEY": minted["marker"]}, secret_service=service,
            actor="worker", surface="mcp_spawn",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(
                _select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "use_marker"
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].context == "mcp_spawn"

    def test_resolve_secret_markers_no_secret_service_raises(
        self, service: SecretService
    ) -> None:
        """No service → store-unavailable envelope, raised
        BEFORE any iteration (the dict is never partially
        constructed)."""
        minted = service.store(
            name="no-svc-dict", kind="generic",
            value="x", actor="worker",
        )
        with pytest.raises(SecretMarkerResolutionError) as excinfo:
            resolve_secret_markers(
                {"KEY": minted["marker"]}, secret_service=None,
            )
        assert excinfo.value.code == SECRET_CODE_STORE_UNAVAILABLE
        assert excinfo.value.surface == "bash.env"

    def test_resolve_secret_markers_no_service_with_plaintext_passes_through(
        self,
    ) -> None:
        """Even WITHOUT a ``secret_service``, plaintext-only
        dicts pass through (the resolver does not gate
        plaintext values — only marker resolution requires
        the service)."""
        env = {"A": "plain", "B": "", "C": "12345"}
        resolved = resolve_secret_markers(env, secret_service=None)
        assert resolved == env
        assert resolved is not env  # still NEW dict

    def test_resolve_secret_markers_passes_through_non_string_values(
        self, service: SecretService
    ) -> None:
        """Defensive: stored values are always strings (the
        SecretService store() validates). A legacy / foreign
        row containing a non-str passes through untouched
        (mirrors ``kms_resolver.py:174-177``)."""
        env = {"STR": "plain", "NONE_VAL": None}  # type: ignore[dict-item]
        resolved = resolve_secret_markers(
            env, secret_service=service, actor="worker",
        )
        assert resolved["STR"] == "plain"
        assert resolved["NONE_VAL"] is None

    def test_resolve_secret_markers_invalid_surface_raises(
        self, service: SecretService
    ) -> None:
        """An unknown surface is a programmer error."""
        from daemon.services.secret_service import SecretValidationError

        with pytest.raises(SecretValidationError):
            resolve_secret_markers(
                {}, secret_service=service, actor="worker",
                surface="bogus_surface",
            )


# ─────────────────────────────────────────────────────────────────────
# Cross-cutting resolver contract
# ─────────────────────────────────────────────────────────────────────


class TestResolverContract:
    """Resolver invariants that cross the single-value and dict
    forms — the contract every consumer relies on."""

    def test_resolver_does_not_touch_secret_free_paths(
        self, service: SecretService
    ) -> None:
        """The resolver is a NO-OP for values that don't
        contain markers — secret-free agents / paths see
        zero behavior change (design §0 invariant)."""
        # Plain dict passthrough — no markers, no DB writes.
        env = {"LOG_LEVEL": "INFO", "EMPTY": ""}
        before_audit_count = self._audit_count(service._engine)
        resolved = resolve_secret_markers(
            env, secret_service=service, actor="worker",
        )
        after_audit_count = self._audit_count(service._engine)
        assert resolved == env
        # No audit row was stamped (no resolution happened).
        assert before_audit_count == after_audit_count

    def test_resolver_emits_audit_row_per_resolution(
        self, service: SecretService
    ) -> None:
        """Each marker resolution stamps exactly ONE audit
        row (per resolution, not per dict)."""
        minted_a = service.store(
            name="per-resolve-a", kind="generic",
            value="a", actor="worker",
        )
        minted_b = service.store(
            name="per-resolve-b", kind="generic",
            value="b", actor="worker",
        )
        # Two markers in one dict → two resolutions.
        resolve_secret_markers(
            {"A": minted_a["marker"], "B": minted_b["marker"]},
            secret_service=service, actor="worker",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(
                _select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "use_marker"
                )
            ).all()
        assert len(rows) == 2, (
            f"per-resolution audit row count drift: {len(rows)}"
        )

    @staticmethod
    def _audit_count(engine: Engine) -> int:
        """Count ``secrets_audit`` rows (used for the no-op
        passthrough pin)."""
        with _session(engine) as session:
            from sqlmodel import select as _select

            return len(
                session.exec(_select(SecretAuditEvent)).all()
            )

    def test_marker_regex_distinct_from_kms_lanes(
        self, service: SecretService
    ) -> None:
        """The KMS-Lite lane markers (``__KMS_REF__...__`` /
        ``__KMS_ENV__...__``) are NOT secret markers — the
        resolver passes them through byte-identical (the KMS
        resolver handles those lanes; this resolver handles
        only the agent-secrets lane)."""
        kms_lane_values = [
            "__KMS_REF__KMS_HANDLE_a1b2__",
            "__KMS_ENV__OPENAI_API_KEY__",
            # Even with the agent-secrets prefix as a SUBSTRING
            # (not at position 0): not a marker.
            f"some-prefix-{ENS_SECRET_MARKER_PREFIX}abc__",
        ]
        for value in kms_lane_values:
            assert (
                resolve_marker(value, secret_service=service)
                == value
            ), (
                f"KMS lane value {value!r} was incorrectly "
                f"treated as a secret marker"
            )


# ─────────────────────────────────────────────────────────────────────
# Phase-1 audit action integration pin
# ─────────────────────────────────────────────────────────────────────


class TestResolveMarkerVsP1Audit:
    """The Phase-1 audit actions (create / read_public /
    retire) carry ``context=NULL`` (design §3.4). The new
    ``use_marker`` action stamps the per-resolve context
    (bash_env / mcp_spawn / tool_param). The two families
    MUST stay disjoint — the resolver never stamps
    create/read_public/retire, and the P1 paths never
    stamp use_marker."""

    def test_resolve_marker_does_not_emit_p1_actions(
        self, service: SecretService
    ) -> None:
        minted = service.store(
            name="p1-audit-pin", kind="generic",
            value="x", actor="worker",
        )
        resolve_marker(
            minted["marker"], secret_service=service,
            actor="worker",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(_select(SecretAuditEvent)).all()
        actions = {r.action for r in rows}
        # ``store`` minted a create row; ``resolve_marker``
        # added a use_marker row. No read_public / retire.
        assert "use_marker" in actions
        assert "create" in actions
        assert "read_public" not in actions
        assert "retire" not in actions

    def test_resolve_marker_use_marker_action_in_enum(
        self, service: SecretService
    ) -> None:
        """``use_marker`` was reserved in P1 (``SECRET_AUDIT_ACTIONS``
        at ``models.py:370-381``); P2 becomes its first emitter
        (design §4). The ``reserve-and-stamp-later`` contract
        is verified here: the resolver stamps rows carrying
        ``action='use_marker'`` AND the per-resolve context."""
        minted = service.store(
            name="use-marker-emit", kind="generic",
            value="x", actor="worker",
        )
        resolve_marker(
            minted["marker"], secret_service=service,
            actor="worker",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(
                _select(SecretAuditEvent).where(
                    SecretAuditEvent.action == "use_marker"
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].context == "tool_param"
        # The enum membership pin — ``use_marker`` MUST
        # live in ``SECRET_AUDIT_ACTIONS`` so the helper's
        # validation accepts it.
        assert "use_marker" in SECRET_AUDIT_ACTIONS

    def test_resolve_marker_stamps_audit_with_non_null_context(
        self, service: SecretService
    ) -> None:
        """Every successful use_marker row carries a
        non-NULL ``context`` (bash_env / mcp_spawn /
        tool_param). The P1 row context stays NULL —
        never overwritten."""
        minted = service.store(
            name="ctx-non-null", kind="generic",
            value="x", actor="worker",
        )
        resolve_marker(
            minted["marker"], secret_service=service,
            actor="worker",
        )
        with _session(service._engine) as session:
            from sqlmodel import select as _select

            rows = session.exec(_select(SecretAuditEvent)).all()
        by_action = {}
        for r in rows:
            by_action.setdefault(r.action, []).append(r)
        # create row context is NULL (P1 contract).
        assert by_action["create"][0].context is None
        # use_marker row context is the supplied tag.
        assert by_action["use_marker"][0].context == "tool_param"