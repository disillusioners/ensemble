"""Unit tests for :mod:`daemon.services.view_passcode_service`.

Phase 1 of the live-view-passcode commission (plan
``.agents/shared/planning/live-view-passcode/``). The tests
cover:

* **Cipher construction** — fail-CLOSED on missing/invalid key
  (the binding spec's "no lazy half-initialized failures"
  guidance; architecture-recommendation.md D4:59 implies
  fail-closed). Fernet + domain-separated HMAC key derivation.
* **Passcode primitives** — alphabet / length / casefold / constant-
  time compare (the FROZEN D4/D5 contract).
* **Mint idempotency** — first mint writes ``mint`` audit + new
  row; re-mint on existing row returns current code + locked
  status WITHOUT clearing the lock (ADR D5 sharpening).
* **Locked row idempotency** — mint on a locked row returns
  the current code + ``locked_status=True`` + ``created=False``
  (does NOT clear; ADR D5).
* **Verify outcomes** — ``OK`` / ``WRONG`` / ``LOCKED`` with
  constant-time compare; locked short-circuits (no compare, no
  increment).
* **Strike ceiling** — 9th wrong → not locked, 10th wrong → locked
  + ``lockout`` audit row IN THE SAME ``engine.begin()``
  transaction (ADR D5 atomic contract).
* **Refresh** — new passcode + generation+1 + counters reset +
  ``regenerate`` audit row; the generation bump silently revokes
  outstanding grant cookies (ADR D3).
* **Lock persists across service re-instantiation** — durable
  proof (architecture-recommendation.md D5 + project critical
  note about ``tmp_path`` FAKE_HOME discipline).
* **Rotation-fail primitive** — corrupted ciphertext decrypt →
  typed :class:`ViewPasscodeRotationRequired` error, NEVER a 500
  (architecture-recommendation.md D4:59; this is ALSO the
  rotation-fail primitive W1 pins at the P4 level).
* **Closed audit action set** — pinned as
  ``{mint, regenerate, grant, deny, lockout}`` (ADR D9:115); the
  service rejects unknown actions at the boundary.
* **Migration↔model parity** — verified in the companion file
  ``tests/migration/test_view_passcode_index_parity.py``.

Test execution is fenced per the project critical notes:

* ``ENSEMBLE_SELF_ENV=dev`` (no live ensemble posture);
* the unit-tier SQLite fence (file-backed ``tmp_path`` per the
  secret-service precedent at
  ``tests/unit/test_secret_service.py:93-117``);
* F1 pre-marked throwaway DB per the secrets-test precedent —
  though the unit suite here uses SQLAlchemy ``create_all``
  directly (the same in-memory SQLite path the parity test
  exercises) so the F1 trap does not bite. The pre-mark helper
  is referenced in the parity test for the boots-the-migration-
  runner path;
* run only the named file (``--tb=short -q``), never bare
  pytest, never ``-x``, never full-suite.
"""

from __future__ import annotations

import os
import uuid
from typing import Iterator

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, event as sa_event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool, StaticPool
from sqlmodel import SQLModel, Session, select

# Register the SQLModel tables before create_all runs.
from daemon.repositories.view_passcodes.models import (  # noqa: F401
    LIVE_VIEW_AUDIT_ACTIONS,
    LIVE_VIEW_AUDIT_IP_KDF_INFO,
    LIVE_VIEW_PASSCODE_ALPHABET,
    LIVE_VIEW_PASSCODE_LENGTH,
    LIVE_VIEW_PASSCODE_STRIKE_CEILING,
    LiveViewPasscode,
    LiveViewPasscodeAudit,
)
from daemon.services.view_passcode_service import (
    MintResult,
    VerifyOutcome,
    ViewPasscodeCipher,
    ViewPasscodeCipherUnavailable,
    ViewPasscodeNotFoundError,
    ViewPasscodeRotationRequired,
    ViewPasscodeService,
    ViewPasscodeValidationError,
    codes_match,
    compute_ip_hmac,
    generate_passcode,
    normalize_entry,
)


# ─────────────────────────────────────────────────────────────────────
# Test fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_sqlite_engine(tmp_path) -> Engine:
    """Build a file-backed SQLite engine with the house PRAGMA set.

    Mirrors the convention in
    ``tests/unit/test_secret_service.py:93-117``.
    """
    db_path = tmp_path / "view-passcode-test.sqlite"
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
    LiveViewPasscode + LiveViewPasscodeAudit tables created.
    The caller owns the dispose lifecycle.
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
def cipher(fernet_key: str) -> ViewPasscodeCipher:
    """Yield a fully-wired ViewPasscodeCipher with an
    explicit key."""
    return ViewPasscodeCipher(encryption_key=fernet_key)


@pytest.fixture
def service(
    sqlite_engine: Engine, cipher: ViewPasscodeCipher
) -> ViewPasscodeService:
    """Yield a fully-wired ViewPasscodeService."""
    return ViewPasscodeService(engine=sqlite_engine, cipher=cipher)


def _session(engine: Engine):
    """Yield a SQLModel session bound to ``engine``."""
    return Session(engine)


# ─────────────────────────────────────────────────────────────────────
# Cipher construction — fail-CLOSED on missing/invalid key
# (AC13 binding spec)
# ─────────────────────────────────────────────────────────────────────


class TestCipherConstruction:
    """The :class:`ViewPasscodeCipher` constructor RAISES on
    missing/invalid key (the binding spec's "no lazy half-
    initialized failures" guidance — architecture-recommendation.md
    D4:59 implies fail-closed; the agent-secrets P1
    ``SecretCipher`` is lazy because that service is wired at
    manager-init time and a missing key must not brick boot;
    this service is invoked from the P2 verify POST lane where
    a missing cipher would surface as a 500 on every request,
    so fail-fast at construction is the right posture).
    """

    def test_cipher_key_env_is_system_encryption_key(self) -> None:
        """The key source is ``SYSTEM_ENCRYPTION_KEY`` — the
        same env the existing ``daemon/sources/credentials.py``
        + ``daemon/services/kms_lite.py`` paths consume (user
        Q1 ruling 2026-10-17). Rotation coupling: a future
        ``SYSTEM_ENCRYPTION_KEY`` rotation MUST re-encrypt
        ``live_view_passcodes.wrapped_passcode`` AND
        ``secrets.wrapped_secret`` AND the ``db_connections``
        Fernet credentials.
        """
        assert ViewPasscodeCipher.KEY_ENV == "SYSTEM_ENCRYPTION_KEY"

    def test_cipher_raises_on_missing_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``SYSTEM_ENCRYPTION_KEY`` set + no explicit
        key arg → the cipher RAISES at construction (typed
        ``ViewPasscodeCipherUnavailable``). The binding spec's
        "no lazy half-initialized failures" posture.
        """
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        with pytest.raises(ViewPasscodeCipherUnavailable):
            ViewPasscodeCipher()

    def test_cipher_raises_on_invalid_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An invalid key (not a Fernet key) raises at
        construction — no half-initialized cipher."""
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", "not-a-fernet-key")
        with pytest.raises(ViewPasscodeCipherUnavailable):
            ViewPasscodeCipher()

    def test_cipher_reads_key_from_env(
        self, monkeypatch: pytest.MonkeyPatch, fernet_key: str
    ) -> None:
        """No explicit key arg + ``SYSTEM_ENCRYPTION_KEY`` in
        env → the cipher constructs without raising."""
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", fernet_key)
        cipher = ViewPasscodeCipher()
        assert cipher.wrap(b"hello").rstrip(b"=") != b"hello"
        assert cipher.unwrap(cipher.wrap(b"hello")) == b"hello"

    def test_cipher_ip_hmac_key_derived(self, cipher: ViewPasscodeCipher) -> None:
        """The :attr:`ip_hmac_key` is 32 bytes (SHA-256 output)
        — domain-separated from the Fernet key per ADR D9:116.
        NEVER reuse the Fernet key as the HMAC key."""
        assert len(cipher.ip_hmac_key) == 32
        assert isinstance(cipher.ip_hmac_key, bytes)


# ─────────────────────────────────────────────────────────────────────
# Passcode primitives — FROZEN D4 alphabet / shape (AC1)
# ─────────────────────────────────────────────────────────────────────


class TestPasscodePrimitives:
    """The passcode primitives are FROZEN (ADR D4)."""

    def test_alphabet_is_31_symbol_unambiguous_set(self) -> None:
        """The alphabet drops 0/O/1/i/l (the visually-ambiguous
        symbols) — the ADR D4 ruling."""
        assert len(LIVE_VIEW_PASSCODE_ALPHABET) == 31
        assert "0" not in LIVE_VIEW_PASSCODE_ALPHABET
        assert "O" not in LIVE_VIEW_PASSCODE_ALPHABET
        assert "1" not in LIVE_VIEW_PASSCODE_ALPHABET
        assert "i" not in LIVE_VIEW_PASSCODE_ALPHABET
        assert "l" not in LIVE_VIEW_PASSCODE_ALPHABET
        # And every char is unique (no duplicates).
        assert len(set(LIVE_VIEW_PASSCODE_ALPHABET)) == 31

    def test_alphabet_only_lowercase(self) -> None:
        """The alphabet is lowercase-only — the
        ``normalize_entry`` primitive casefolds customer
        input so upper or lower both work."""
        assert LIVE_VIEW_PASSCODE_ALPHABET == (
            LIVE_VIEW_PASSCODE_ALPHABET.lower()
        )

    def test_length_is_six(self) -> None:
        """Length is fixed at 6 (UX: 6 chars typed in ~10
        seconds on a phone keyboard). 31⁶ ≈ 29.7 bits of
        entropy — acceptable under the bounded-online-attack
        model (10 durable strikes + per-IP token bucket)."""
        assert LIVE_VIEW_PASSCODE_LENGTH == 6

    def test_generate_passcode_shape(self) -> None:
        """``generate_passcode`` returns a 6-char string from
        the alphabet. Property-style: run it 100 times, every
        result is in the alphabet and 6 chars long."""
        for _ in range(100):
            pc = generate_passcode()
            assert len(pc) == LIVE_VIEW_PASSCODE_LENGTH
            assert all(ch in LIVE_VIEW_PASSCODE_ALPHABET for ch in pc)

    def test_generate_passcode_uses_csprng(self) -> None:
        """Property-style: 200 generated codes are all distinct
        (no PNRG-replay risk — the alphabet is 31 chars over
        6 slots; collisions are still astronomically rare)."""
        codes = {generate_passcode() for _ in range(200)}
        assert len(codes) == 200

    def test_normalize_entry_strips_and_casefolds(self) -> None:
        """``normalize_entry`` strips whitespace + casefolds.
        Empty input returns empty."""
        assert normalize_entry("  AbC23J  ") == "abc23j"
        assert normalize_entry("ABC23J") == "abc23j"
        assert normalize_entry("abc23j") == "abc23j"
        assert normalize_entry("\tabc23j\n") == "abc23j"
        assert normalize_entry("") == ""

    def test_codes_match_case_insensitive(self) -> None:
        """``codes_match`` is case-insensitive (the customer
        can type upper or lower)."""
        assert codes_match("abc23j", "ABC23J")
        assert codes_match("ABC23J", "abc23j")
        assert codes_match("Abc23j", "aBc23J")
        assert not codes_match("abc23j", "abc23k")
        assert not codes_match("abc23j", "abc24j")

    def test_codes_match_constant_time_primitive(self) -> None:
        """``codes_match`` goes through
        :func:`hmac.compare_digest` — the constant-time
        primitive (AC1 binding spec)."""
        import hmac

        # Direct call to compare_digest on the SAME strings
        # should produce the same result as codes_match.
        a = "abc23j"
        b = "abc23j"
        assert codes_match(a, b) == hmac.compare_digest(
            normalize_entry(a), normalize_entry(b)
        )
        c = "abc23k"
        assert codes_match(a, c) == hmac.compare_digest(
            normalize_entry(a), normalize_entry(c)
        )

    def test_compute_ip_hmac_length_and_determinism(
        self, cipher: ViewPasscodeCipher
    ) -> None:
        """``compute_ip_hmac`` returns a 16-hex-char (64-bit)
        HMAC tag (ADR D9:116). Same IP + same cipher → same
        tag; different IP → different tag."""
        h1 = compute_ip_hmac(cipher, "192.168.1.1")
        h2 = compute_ip_hmac(cipher, "192.168.1.1")
        h3 = compute_ip_hmac(cipher, "192.168.1.2")
        assert h1 == h2
        assert h1 != h3
        assert len(h1) == 16
        # Hex-only.
        int(h1, 16)  # raises if not hex

    def test_compute_ip_hmac_is_domain_separated(
        self, fernet_key: str
    ) -> None:
        """Two ciphers with the same Fernet key produce the
        same ip_hmac_key (deterministic derivation). Two
        ciphers with different Fernet keys produce DIFFERENT
        ip_hmac_key (rotation unlinks historical rows)."""
        c1 = ViewPasscodeCipher(encryption_key=fernet_key)
        c2 = ViewPasscodeCipher(encryption_key=fernet_key)
        other = ViewPasscodeCipher(
            encryption_key=Fernet.generate_key().decode()
        )
        assert c1.ip_hmac_key == c2.ip_hmac_key
        assert c1.ip_hmac_key != other.ip_hmac_key
        # And the ip_hmac tag itself rotates with the key.
        h1 = compute_ip_hmac(c1, "192.168.1.1")
        h2 = compute_ip_hmac(other, "192.168.1.1")
        assert h1 != h2


# ─────────────────────────────────────────────────────────────────────
# Mint — idempotent for existing rows INCLUDING locked (AC10)
# ─────────────────────────────────────────────────────────────────────


class TestMintIdempotency:
    """``mint`` is idempotent for existing rows — does NOT
    clear an active lock (ADR D5 sharpening)."""

    def test_mint_first_time_writes_mint_audit(
        self, service: ViewPasscodeService, sqlite_engine: Engine
    ) -> None:
        res = service.mint("docs", "a/b.md", actor="worker")
        assert res.created is True
        assert res.generation == 1
        assert res.locked_status is False
        assert len(res.passcode) == 6
        # One mint audit row.
        with _session(sqlite_engine) as session:
            audits = session.exec(select(LiveViewPasscodeAudit)).all()
        mints = [a for a in audits if a.action == "mint"]
        assert len(mints) == 1
        assert mints[0].actor == "worker"
        assert mints[0].root_name == "docs"
        assert mints[0].rel_path == "a/b.md"
        assert mints[0].generation == 1

    def test_mint_existing_row_is_idempotent(
        self, service: ViewPasscodeService
    ) -> None:
        """A second mint on the same (root, rel) returns the
        SAME passcode, ``created=False``, and does NOT add a
        second ``mint`` audit row (idempotent re-display;
        re-display is not audited per plan)."""
        res1 = service.mint("docs", "a/b.md", actor="worker")
        res2 = service.mint("docs", "a/b.md", actor="worker")
        assert res2.created is False
        assert res2.passcode == res1.passcode
        assert res2.generation == res1.generation

    def test_mint_locked_row_does_not_clear_lock(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """A mint on a LOCKED row returns the current code +
        ``locked_status=True`` + ``created=False``; the lock
        is NOT cleared. ADR D5 sharpening (architecture-
        recommendation.md D5:67 — "idempotent ``view_link``
        does NOT clear an active lock")."""
        # First mint.
        res1 = service.mint("docs", "a/b.md", actor="worker")
        # Trip the lock via 10 failures.
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            service.record_failure("docs", "a/b.md", ip="10.0.0.1")
        # Fetch fresh row.
        row = service.get_row("docs", "a/b.md")
        assert row is not None and row.locked is True
        # Re-mint while locked.
        res_locked = service.mint("docs", "a/b.md", actor="worker")
        assert res_locked.created is False
        assert res_locked.locked_status is True
        assert res_locked.passcode == res1.passcode
        # And the row is STILL locked (idempotent did NOT clear).
        row_after = service.get_row("docs", "a/b.md")
        assert row_after is not None and row_after.locked is True
        assert row_after.generation == res1.generation  # no bump

    def test_mint_validates_root_name_and_rel_path(
        self, service: ViewPasscodeService
    ) -> None:
        """The service validates inputs at the boundary —
        TypeError for wrong type, ``ViewPasscodeValidationError``
        for empty string (the SecretService tidier pass #2
        convention)."""
        with pytest.raises(TypeError):
            service.mint(123, "x", actor="worker")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            service.mint("x", 123, actor="worker")  # type: ignore[arg-type]
        with pytest.raises(ViewPasscodeValidationError):
            service.mint("", "x", actor="worker")
        with pytest.raises(ViewPasscodeValidationError):
            service.mint("x", "", actor="worker")


# ─────────────────────────────────────────────────────────────────────
# Verify — constant-time compare + locked short-circuit
# ─────────────────────────────────────────────────────────────────────


class TestVerify:
    """``verify`` returns ``OK`` / ``WRONG`` / ``LOCKED``."""

    def test_verify_ok_on_match(
        self, service: ViewPasscodeService
    ) -> None:
        res = service.mint("docs", "a/b.md", actor="worker")
        row = service.get_row("docs", "a/b.md")
        assert row is not None
        assert service.verify(row, res.passcode) == VerifyOutcome.OK

    def test_verify_ok_on_case_insensitive_match(
        self, service: ViewPasscodeService
    ) -> None:
        res = service.mint("docs", "a/b.md", actor="worker")
        row = service.get_row("docs", "a/b.md")
        assert row is not None
        # Upper-case entry is normalized.
        assert service.verify(
            row, res.passcode.upper()
        ) == VerifyOutcome.OK

    def test_verify_wrong_on_mismatch(
        self, service: ViewPasscodeService
    ) -> None:
        service.mint("docs", "a/b.md", actor="worker")
        row = service.get_row("docs", "a/b.md")
        assert row is not None
        # Deterministic wrong string: 'X' is not in
        # LIVE_VIEW_PASSCODE_ALPHABET.
        assert service.verify(row, "XXXXXX") == VerifyOutcome.WRONG

    def test_verify_locked_short_circuits(
        self, service: ViewPasscodeService
    ) -> None:
        """A locked row returns ``LOCKED`` with NO compare +
        NO increment (ADR D5 sharpening). The ``verify`` path
        NEVER touches the database."""
        res = service.mint("docs", "a/b.md", actor="worker")
        # Trip the lock.
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            service.record_failure("docs", "a/b.md", ip="10.0.0.1")
        row = service.get_row("docs", "a/b.md")
        assert row is not None and row.locked is True
        # The CORRECT code on a locked row still returns
        # ``LOCKED`` (the lock gates entry, not established
        # grants — ADR D5:68; P2 grants survive lockout).
        assert service.verify(row, res.passcode) == VerifyOutcome.LOCKED
        # And the locked row's failed_attempts was NOT bumped
        # by verify (verify is a pure function on the row).
        row_after = service.get_row("docs", "a/b.md")
        assert (
            row_after is not None
            and row_after.failed_attempts
            == LIVE_VIEW_PASSCODE_STRIKE_CEILING
        )

    def test_verify_rotation_required_on_tampered_ciphertext(
        self,
        service: ViewPasscodeService,
        fernet_key: str,
    ) -> None:
        """A ciphertext that was NOT produced by this key
        raises the typed :class:`ViewPasscodeRotationRequired`
        error — the canonical rotation-fail primitive
        (architecture-recommendation.md D4:59; the Phase 4 W1
        pin exercises this at the integration level)."""
        res = service.mint("docs", "a/b.md", actor="worker")
        row = service.get_row("docs", "a/b.md")
        assert row is not None
        # Swap in a cipher with a different key — the
        # stored ciphertext was wrapped under the original
        # key, so decrypt fails.
        other_cipher = ViewPasscodeCipher(
            encryption_key=Fernet.generate_key().decode()
        )
        service._cipher = other_cipher
        with pytest.raises(ViewPasscodeRotationRequired):
            service.verify(row, res.passcode)


# ─────────────────────────────────────────────────────────────────────
# Strike ceiling — boundary 9→10 (AC10) + atomic lockout audit
# ─────────────────────────────────────────────────────────────────────


class TestStrikeCeiling:
    """The 10th wrong verify flips ``locked`` + emits the
    ``lockout`` audit row IN THE SAME ``engine.begin()``
    transaction (architecture-recommendation.md D5:65; AC10).
    """

    def test_9th_wrong_does_not_lock(
        self, service: ViewPasscodeService
    ) -> None:
        """Boundary: the 9th failure must NOT flip ``locked``."""
        service.mint("docs", "a/b.md", actor="worker")
        last_attempts = 0
        last_locked_now = False
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING - 1):
            attempts, locked_now = service.record_failure(
                "docs", "a/b.md", ip="10.0.0.1"
            )
            last_attempts = attempts
            last_locked_now = locked_now
        # 9th wrong attempt: NOT locked.
        assert last_attempts == LIVE_VIEW_PASSCODE_STRIKE_CEILING - 1
        assert last_locked_now is False
        # And no ``lockout`` audit row yet.
        with _session(service._engine) as session:
            lockouts = session.exec(
                select(LiveViewPasscodeAudit).where(
                    LiveViewPasscodeAudit.action == "lockout"
                )
            ).all()
        assert len(lockouts) == 0

    def test_10th_wrong_flips_lock_and_emits_lockout_audit(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """Boundary: the 10th failure flips ``locked`` AND
        emits the ``lockout`` audit row IN THE SAME
        ``engine.begin()`` transaction (ADR D5:65 atomic
        contract)."""
        service.mint("docs", "a/b.md", actor="worker")
        # Trip 9 prior failures.
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING - 1):
            service.record_failure(
                "docs", "a/b.md", ip="10.0.0.1"
            )
        # The 10th failure flips locked + emits lockout.
        attempts, locked_now = service.record_failure(
            "docs", "a/b.md", ip="10.0.0.1"
        )
        assert attempts == LIVE_VIEW_PASSCODE_STRIKE_CEILING
        assert locked_now is True
        # Atomic ``lockout`` audit row was written.
        with _session(sqlite_engine) as session:
            lockouts = session.exec(
                select(LiveViewPasscodeAudit).where(
                    LiveViewPasscodeAudit.action == "lockout"
                )
            ).all()
        assert len(lockouts) == 1
        lo = lockouts[0]
        assert lo.root_name == "docs"
        assert lo.rel_path == "a/b.md"
        assert lo.ip_hmac is not None
        assert len(lo.ip_hmac) == 16

    def test_11th_wrong_does_not_re_emit_lockout(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """After the lock is set, additional failure calls
        (e.g. concurrent verify before the lock check)
        must NOT re-emit the ``lockout`` audit row. The
        defense-in-depth guard inside
        :meth:`_SqlViewPasscodeStore.record_failure_with_audit`
        short-circuits (no increment, no audit)."""
        service.mint("docs", "a/b.md", actor="worker")
        # Trip 10 failures (lock flips at the 10th).
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            service.record_failure(
                "docs", "a/b.md", ip="10.0.0.1"
            )
        # Add more "failures" on the locked row — must
        # not increment the counter or re-emit lockout.
        attempts, locked_now = service.record_failure(
            "docs", "a/b.md", ip="10.0.0.1"
        )
        assert attempts == LIVE_VIEW_PASSCODE_STRIKE_CEILING
        assert locked_now is True
        with _session(sqlite_engine) as session:
            lockouts = session.exec(
                select(LiveViewPasscodeAudit).where(
                    LiveViewPasscodeAudit.action == "lockout"
                )
            ).all()
        assert len(lockouts) == 1


# ─────────────────────────────────────────────────────────────────────
# Refresh — regenerate + clear lock + bump generation (AC11)
# ─────────────────────────────────────────────────────────────────────


class TestRefresh:
    """``refresh`` regenerates the passcode + bumps generation
    + clears lock + audits ``regenerate`` (AC11)."""

    def test_refresh_bumps_generation_and_clears_lock(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        res1 = service.mint("docs", "a/b.md", actor="worker")
        # Trip the lock.
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            service.record_failure("docs", "a/b.md", ip="10.0.0.1")
        # Refresh.
        res2 = service.refresh("docs", "a/b.md", actor="worker")
        assert res2.generation == res1.generation + 1
        assert res2.locked_status is False
        assert res2.passcode != res1.passcode
        # The DB row reflects the post-refresh state.
        row = service.get_row("docs", "a/b.md")
        assert row is not None
        assert row.generation == res2.generation
        assert row.failed_attempts == 0
        assert row.locked is False
        # And a ``regenerate`` audit row was written.
        with _session(sqlite_engine) as session:
            regenerates = session.exec(
                select(LiveViewPasscodeAudit).where(
                    LiveViewPasscodeAudit.action == "regenerate"
                )
            ).all()
        assert len(regenerates) == 1
        rg = regenerates[0]
        assert rg.actor == "worker"
        assert rg.generation == res2.generation
        assert rg.ip_hmac is None  # privileged path, no IP

    def test_refresh_on_unknown_raises_not_found(
        self, service: ViewPasscodeService
    ) -> None:
        with pytest.raises(ViewPasscodeNotFoundError):
            service.refresh("unknown", "x", actor="worker")

    def test_refresh_idempotency_each_call_bumps(
        self, service: ViewPasscodeService
    ) -> None:
        """Two consecutive refreshes bump generation TWICE
        (no idempotency on refresh — the caller controls the
        cadence via ``view_link(refresh=True)``)."""
        service.mint("docs", "a/b.md", actor="worker")
        res2 = service.refresh("docs", "a/b.md", actor="worker")
        res3 = service.refresh("docs", "a/b.md", actor="worker")
        assert res3.generation == res2.generation + 1
        assert res3.passcode != res2.passcode


# ─────────────────────────────────────────────────────────────────────
# Durable lock — proves the lock persists across service
# re-instantiation on the SAME DB file (AC10 durable proof).
# ─────────────────────────────────────────────────────────────────────


class TestDurableLock:
    """The 10-strike lock is durable — survives daemon restart
    (architecture-recommendation.md D5:65 + project critical
    note: durable lockout is the binding contract). The
    hermetic proof re-instantiates the service on the SAME
    SQLite file.
    """

    def test_lock_persists_across_service_re_instantiation(
        self,
        sqlite_engine: Engine,
        fernet_key: str,
    ) -> None:
        # First service instance — mint + trip the lock.
        c1 = ViewPasscodeCipher(encryption_key=fernet_key)
        svc1 = ViewPasscodeService(engine=sqlite_engine, cipher=c1)
        svc1.mint("docs", "a/b.md", actor="worker")
        for _ in range(LIVE_VIEW_PASSCODE_STRIKE_CEILING):
            svc1.record_failure("docs", "a/b.md", ip="10.0.0.1")
        # Confirm locked.
        row1 = svc1.get_row("docs", "a/b.md")
        assert row1 is not None and row1.locked is True
        # Dispose the engine so the file is closed; a fresh
        # engine reopens the SAME file.
        sqlite_engine.dispose()

        # Fresh engine bound to the SAME db file.
        db_path = sqlite_engine.url.database
        fresh_eng = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )
        try:
            # Fresh service instance — the cipher is built
            # from the SAME key (durability across restart
            # assumes the operator has not rotated the key).
            c2 = ViewPasscodeCipher(encryption_key=fernet_key)
            svc2 = ViewPasscodeService(engine=fresh_eng, cipher=c2)
            row2 = svc2.get_row("docs", "a/b.md")
            assert row2 is not None
            assert row2.locked is True
            assert (
                row2.failed_attempts == LIVE_VIEW_PASSCODE_STRIKE_CEILING
            )
            # And the verify path short-circuits (LOCKED).
            res = svc2.mint("docs", "a/b.md", actor="worker")
            assert res.locked_status is True
        finally:
            fresh_eng.dispose()


# ─────────────────────────────────────────────────────────────────────
# Audit action set — closed set {mint, regenerate, grant, deny,
# lockout} (ADR D9:115)
# ─────────────────────────────────────────────────────────────────────


class TestAuditActionSet:
    """The audit action set is closed: ``{mint, regenerate,
    grant, deny, lockout}`` (ADR D9:115). The service boundary
    rejects unknown actions. The P1 path emits
    ``mint`` / ``regenerate`` / ``lockout`` only; P2 adds
    ``grant`` + ``deny``.
    """

    def test_closed_action_set_exact(self) -> None:
        assert LIVE_VIEW_AUDIT_ACTIONS == frozenset({
            "mint",
            "regenerate",
            "grant",
            "deny",
            "lockout",
        })

    def test_append_audit_rejects_unknown_action(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """``append_audit`` validates against the closed set
        at the service boundary — NOT at the model."""
        with pytest.raises(ViewPasscodeValidationError):
            service.append_audit(
                "docs",
                "a/b.md",
                generation=1,
                action="unknown-action",
                actor="worker",
            )

    def test_append_audit_accepts_each_closed_value(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """Every value in the closed set is accepted by the
        boundary (the P1 path doesn't, but the surface is
        forward-compatible with the P2 emitters)."""
        for action in sorted(LIVE_VIEW_AUDIT_ACTIONS):
            service.append_audit(
                "docs",
                "a/b.md",
                generation=1,
                action=action,
                actor="worker",
                ip="192.168.1.1",
            )
        with _session(sqlite_engine) as session:
            rows = session.exec(
                select(LiveViewPasscodeAudit).order_by(
                    LiveViewPasscodeAudit.id
                )
            ).all()
        actions = [r.action for r in rows]
        for action in sorted(LIVE_VIEW_AUDIT_ACTIONS):
            assert action in actions

    def test_append_audit_records_ip_hmac(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """``append_audit`` with ``ip`` stores the 16-hex-char
        HMAC; with ``ip=None`` stores NULL."""
        service.append_audit(
            "docs",
            "a/b.md",
            generation=1,
            action="grant",
            actor=None,
            ip="192.168.1.1",
        )
        service.append_audit(
            "docs",
            "a/b.md",
            generation=1,
            action="deny",
            actor=None,
            ip=None,
        )
        with _session(sqlite_engine) as session:
            rows = session.exec(
                select(LiveViewPasscodeAudit).order_by(
                    LiveViewPasscodeAudit.id
                )
            ).all()
        # The ``grant`` row carries ip_hmac; the ``deny`` row
        # has NULL.
        grant_row = next(r for r in rows if r.action == "grant")
        deny_row = next(r for r in rows if r.action == "deny")
        assert grant_row.ip_hmac is not None
        assert len(grant_row.ip_hmac) == 16
        assert deny_row.ip_hmac is None

    def test_append_audit_records_null_actor_for_public(
        self,
        service: ViewPasscodeService,
        sqlite_engine: Engine,
    ) -> None:
        """Public-lane audit rows (``grant`` / ``deny``) have
        ``actor=NULL`` — the unauthenticated verify POST has
        no agent provenance."""
        service.append_audit(
            "docs",
            "a/b.md",
            generation=1,
            action="deny",
            actor=None,
            ip="192.168.1.1",
        )
        with _session(sqlite_engine) as session:
            rows = session.exec(
                select(LiveViewPasscodeAudit).where(
                    LiveViewPasscodeAudit.action == "deny"
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].actor is None


# ─────────────────────────────────────────────────────────────────────
# append_audit boundary validation — reject wrong types / empty
# strings at the boundary (the SecretService tidier pass #2
# convention).
# ─────────────────────────────────────────────────────────────────────


class TestAppendAuditValidation:
    """``append_audit`` validates inputs at the boundary —
    TypeError for wrong types, ``ViewPasscodeValidationError``
    for empty strings / out-of-range generation / unknown
    action."""

    def test_append_audit_rejects_wrong_types(
        self, service: ViewPasscodeService
    ) -> None:
        with pytest.raises(TypeError):
            service.append_audit(
                123, "x", generation=1, action="grant"
            )
        with pytest.raises(TypeError):
            service.append_audit(
                "x", 123, generation=1, action="grant"
            )
        with pytest.raises(TypeError):
            service.append_audit(
                "x", "y", generation="bad", action="grant"
            )
        with pytest.raises(TypeError):
            service.append_audit(
                "x", "y", generation=True, action="grant"
            )
        with pytest.raises(TypeError):
            service.append_audit(
                "x",
                "y",
                generation=1,
                action="grant",
                ip=12345,
            )

    def test_append_audit_rejects_empty_root_or_rel(
        self, service: ViewPasscodeService
    ) -> None:
        with pytest.raises(ViewPasscodeValidationError):
            service.append_audit(
                "", "y", generation=1, action="grant"
            )
        with pytest.raises(ViewPasscodeValidationError):
            service.append_audit(
                "x", "", generation=1, action="grant"
            )

    def test_append_audit_rejects_generation_below_one(
        self, service: ViewPasscodeService
    ) -> None:
        with pytest.raises(ViewPasscodeValidationError):
            service.append_audit(
                "x", "y", generation=0, action="grant"
            )
        with pytest.raises(ViewPasscodeValidationError):
            service.append_audit(
                "x", "y", generation=-1, action="grant"
            )