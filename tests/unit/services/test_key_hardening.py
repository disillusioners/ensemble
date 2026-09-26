"""Root-key hardening — P3-WP13a acceptance suite.

Covers the five acceptance cases from
``.agents/shared/planning/designer-agent/implementation-plan/phase3-bootstrap-kms-lite.md``
lines 271-275:

(a) key in ``0o644`` file → refusal + clear error line.
(b) key in ``0o600`` file with old mtime (beyond threshold) → refusal.
(c) ``0o600`` + fresh mtime → mint proceeds (the gate returns ok).
(d) inline key → proceeds with ok-with-note (no perm/age checks).
(e) ``SYSTEM_ENCRYPTION_KEY_FILE`` points at missing file → refusal.

Day-1 contract: NO PG / live DB. All fixtures are ``tmp_path`` + ``monkeypatch``
env. No daemon boot.

Note on the integration with the mint path: the mint-time gate hook in
``daemon.services.kms_lite`` LANDED in P3-WP12a (fold of the WP13a
dispatch). The unit tests above exercise ``validate_key_source()``
directly; the ``TestMintGateThroughKmsRequest`` class below proves the
gate fires through ``kms_request`` itself — a violating key file yields
``KMSUnavailableError`` from the mint entry point, not merely a
``ok=False`` from the standalone validator.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from daemon.util.key_hardening import (
    DEFAULT_MAX_AGE_DAYS,
    KEY_FILE_MISSING,
    KEY_FILE_NOT_READABLE,
    KEY_FILE_PERMS,
    KEY_FILE_STALE,
    KEY_NOT_SET,
    SYSTEM_ENCRYPTION_KEY_ENV,
    SYSTEM_ENCRYPTION_KEY_FILE_ENV,
    SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV,
    KeyHardeningResult,
    KeyHardeningViolation,
    validate_key_source,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_key_file(
    path: Path,
    *,
    mode: int,
    mtime: float | None = None,
    body: str = "fake-fernet-key-not-real",
) -> None:
    """Write a key file with a specific mode + (optionally) mtime.

    ``mode`` is applied via ``os.chmod`` AFTER the write so we control
    the file's perm bits regardless of the test runner's umask. The
    ``mtime`` is set with ``os.utime`` so tests are deterministic
    without sleep().
    """
    path.write_text(body)
    os.chmod(path, mode)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def fixed_now() -> float:
    """Fixed "now" for deterministic age math (2026-09-26 12:00:00 UTC)."""
    return 1_700_000_000.0


# ---------------------------------------------------------------------------
# (a) 0o644 file → refusal
# ---------------------------------------------------------------------------


class TestKeyFilePermsViolation:
    """Plan §Acceptance (a): 0o644 (world-readable) is a violation."""

    def test_0644_file_is_refused(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        key_file = tmp_path / "key"
        _write_key_file(key_file, mode=0o644, mtime=fixed_now)

        env = {
            SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file),
        }
        result = validate_key_source(env=env, now=fixed_now)

        assert result.ok is False
        assert result.source == "file"
        codes = [v.code for v in result.violations]
        assert KEY_FILE_PERMS in codes
        # Refusal message must NOT carry key material.
        assert "fake-fernet-key" not in result.message
        for v in result.violations:
            assert "fake-fernet-key" not in v.detail
            # Confirm the mode is surfaced for the operator.
            assert "0o644" in v.detail

    def test_0660_file_is_refused_group_writable(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        key_file = tmp_path / "key"
        _write_key_file(key_file, mode=0o660, mtime=fixed_now)
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file)}
        result = validate_key_source(env=env, now=fixed_now)
        assert result.ok is False
        assert KEY_FILE_PERMS in [v.code for v in result.violations]

    def test_0600_file_is_accepted_on_perms(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        key_file = tmp_path / "key"
        _write_key_file(key_file, mode=0o600, mtime=fixed_now)
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file)}
        result = validate_key_source(env=env, now=fixed_now)
        assert result.ok is True
        assert KEY_FILE_PERMS not in [v.code for v in result.violations]


# ---------------------------------------------------------------------------
# (b) 0o600 file with old mtime → refusal
# ---------------------------------------------------------------------------


class TestKeyFileStaleViolation:
    """Plan §Acceptance (b): stale mtime is a violation."""

    def test_stale_file_is_refused(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        key_file = tmp_path / "key"
        # 365 days old (well past default 90).
        old_mtime = fixed_now - (365 * 86400.0)
        _write_key_file(key_file, mode=0o600, mtime=old_mtime)
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file)}
        result = validate_key_source(env=env, now=fixed_now)

        assert result.ok is False
        codes = [v.code for v in result.violations]
        assert KEY_FILE_STALE in codes
        # Message must include the configured threshold so the operator
        # knows how to fix it (rotate the file).
        assert "90 days" in next(
            v.detail for v in result.violations if v.code == KEY_FILE_STALE
        )

    def test_custom_threshold_relaxes_staleness(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        key_file = tmp_path / "key"
        # 100 days old, threshold raised to 365 → should pass.
        old_mtime = fixed_now - (100 * 86400.0)
        _write_key_file(key_file, mode=0o600, mtime=old_mtime)
        env = {
            SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file),
            SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV: "365",
        }
        result = validate_key_source(env=env, now=fixed_now)
        assert result.ok is True
        assert KEY_FILE_STALE not in [v.code for v in result.violations]

    def test_stale_and_perms_combined_violations(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        # Both checks fail simultaneously — the gate must report both
        # violations, not just one (operator wants the full picture).
        key_file = tmp_path / "key"
        old_mtime = fixed_now - (200 * 86400.0)
        _write_key_file(key_file, mode=0o644, mtime=old_mtime)
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file)}
        result = validate_key_source(env=env, now=fixed_now)
        assert result.ok is False
        codes = [v.code for v in result.violations]
        assert KEY_FILE_PERMS in codes
        assert KEY_FILE_STALE in codes


# ---------------------------------------------------------------------------
# (c) 0o600 + fresh mtime → mint proceeds (gate returns ok)
# ---------------------------------------------------------------------------


class TestKeyFileFreshAccepted:
    """Plan §Acceptance (c): fresh + 0o600 → gate ok."""

    def test_fresh_0600_file_is_accepted(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        key_file = tmp_path / "key"
        # mtime 1 hour old — well within default 90-day window.
        fresh = fixed_now - 3600.0
        _write_key_file(key_file, mode=0o600, mtime=fresh)
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file)}
        result = validate_key_source(env=env, now=fixed_now)

        assert result.ok is True
        assert result.source == "file"
        assert result.violations == []
        # No shadowing note because the inline key is NOT set.
        assert all("shadowed" not in n.lower() for n in result.notes)
        # Message must NOT contain key material.
        assert "fake-fernet-key" not in result.message

    def test_fresh_0600_file_with_inline_also_set(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        # If both env vars are set, the file path wins (per the
        # implementation order). Inline key is recorded as a note
        # rather than an error.
        key_file = tmp_path / "key"
        fresh = fixed_now - 3600.0
        _write_key_file(key_file, mode=0o600, mtime=fresh)
        env = {
            SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file),
            SYSTEM_ENCRYPTION_KEY_ENV: "some-inline-key",
        }
        result = validate_key_source(env=env, now=fixed_now)

        assert result.ok is True
        assert result.source == "file"
        # The note surfaces the inline shadowing for operator visibility.
        assert any("inline" in n.lower() for n in result.notes)


# ---------------------------------------------------------------------------
# (d) inline key → ok-with-note
# ---------------------------------------------------------------------------


class TestInlineKeyAcceptedWithNote:
    """Plan §Acceptance (d): inline key — no perm/age checks; ok-with-note."""

    def test_inline_only_returns_ok_with_residual_risk_note(self) -> None:
        env = {SYSTEM_ENCRYPTION_KEY_ENV: "some-inline-key-value"}
        result = validate_key_source(env=env)

        assert result.ok is True
        assert result.source == "inline"
        assert result.violations == []
        assert len(result.notes) >= 1
        # The note MUST reference the residual risk and the ops doc so
        # operators know where to read more.
        joined = " ".join(result.notes).lower()
        assert "residual risk" in joined
        assert "kms-lite-hardening" in joined
        # And the note MUST NOT echo the key value.
        assert "some-inline-key-value" not in joined

    def test_inline_message_does_not_carry_key(self) -> None:
        env = {SYSTEM_ENCRYPTION_KEY_ENV: "supersecretkey"}
        result = validate_key_source(env=env)
        assert "supersecretkey" not in result.message
        for n in result.notes:
            assert "supersecretkey" not in n


# ---------------------------------------------------------------------------
# (e) missing file → refusal
# ---------------------------------------------------------------------------


class TestKeyFileMissingViolation:
    """Plan §Acceptance (e): file does not exist → refusal."""

    def test_missing_file_is_refused(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        missing = tmp_path / "does-not-exist"
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(missing)}
        result = validate_key_source(env=env, now=fixed_now)

        assert result.ok is False
        assert result.source == "file"
        codes = [v.code for v in result.violations]
        assert KEY_FILE_MISSING in codes
        # The detail surfaces the path the operator configured (so they
        # can spot typos in the env var).
        assert str(missing) in next(
            v.detail for v in result.violations if v.code == KEY_FILE_MISSING
        )

    def test_unreadable_file_is_refused(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        # Point at a path containing a NUL byte — os.stat rejects it
        # with ValueError (which we catch as KEY_FILE_NOT_READABLE).
        # More portable than relying on chmod 0o000 semantics, which
        # differ across root / non-root runs.
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: "/dev/null/with\x00nul"}
        result = validate_key_source(env=env, now=fixed_now)

        # os.path.exists returns False for a path with a NUL byte on
        # CPython (FileNotFoundError); either KEY_FILE_MISSING or
        # KEY_FILE_NOT_READABLE is acceptable — both convey "refused".
        assert result.ok is False
        assert result.source == "file"
        codes = [v.code for v in result.violations]
        assert any(
            c in {KEY_FILE_NOT_READABLE, KEY_FILE_MISSING}
            for c in codes
        )


# ---------------------------------------------------------------------------
# Neither env var set → refusal (KEY_NOT_SET)
# ---------------------------------------------------------------------------


class TestNoKeySetRefused:
    def test_neither_set_returns_key_not_set(self) -> None:
        # Empty env — neither file nor inline is set.
        result = validate_key_source(env={})
        assert result.ok is False
        assert result.source == "none"
        assert [v.code for v in result.violations] == [KEY_NOT_SET]


# ---------------------------------------------------------------------------
# Max-age-days env var parsing
# ---------------------------------------------------------------------------


class TestMaxAgeDaysParsing:
    def test_default_when_unset(self) -> None:
        env = {SYSTEM_ENCRYPTION_KEY_ENV: "k"}
        result = validate_key_source(env=env)
        # Source=inline skips the file check; but the parser default is
        # exercised regardless. Smoke-test the public contract: result
        # is ok=True and no perms/stale violation fires.
        assert result.ok is True

    def test_zero_max_age_falls_back_to_default(
        self, tmp_path: Path, fixed_now: float, caplog: pytest.LogCaptureFixture
    ) -> None:
        key_file = tmp_path / "key"
        _write_key_file(key_file, mode=0o600, mtime=fixed_now - 3600.0)
        env = {
            SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file),
            SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV: "0",
        }
        # Should not raise; should fall back to DEFAULT_MAX_AGE_DAYS and
        # therefore accept the 1-hour-old file.
        result = validate_key_source(env=env, now=fixed_now)
        assert result.ok is True
        assert any(
            "must be > 0" in r.message or "falling back" in r.message
            for r in caplog.records
            if r.levelname == "WARNING"
        )

    def test_malformed_max_age_falls_back_to_default(
        self, tmp_path: Path, fixed_now: float, caplog: pytest.LogCaptureFixture
    ) -> None:
        key_file = tmp_path / "key"
        _write_key_file(key_file, mode=0o600, mtime=fixed_now - 3600.0)
        env = {
            SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file),
            SYSTEM_ENCRYPTION_KEY_MAX_AGE_DAYS_ENV: "not-an-int",
        }
        result = validate_key_source(env=env, now=fixed_now)
        assert result.ok is True
        # The fallback WARNING surfaces the bad value (so operators can
        # grep for it) but never the key.
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "not-an-int" in joined
        assert "fake-fernet-key" not in joined


# ---------------------------------------------------------------------------
# Result invariants — sanity-check the dataclass never lies
# ---------------------------------------------------------------------------


class TestResultInvariants:
    def test_ok_false_with_empty_violations_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            KeyHardeningResult(ok=False, source="file")

    def test_ok_true_with_violations_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            KeyHardeningResult(
                ok=True,
                source="file",
                violations=[KeyHardeningViolation(code="X", detail="x")],
            )

    def test_unknown_source_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            KeyHardeningResult(ok=True, source="weird")

    def test_key_material_never_appears_in_result_messages(
        self, tmp_path: Path, fixed_now: float
    ) -> None:
        # Property-style sweep: for every violation + the top-level
        # message + every note, no occurrence of the key body substring.
        key_body = "ultra-secret-key-value-XYZ"
        key_file = tmp_path / "key"
        _write_key_file(key_file, mode=0o600, mtime=fixed_now - 3600.0, body=key_body)
        env = {SYSTEM_ENCRYPTION_KEY_FILE_ENV: str(key_file)}
        result = validate_key_source(env=env, now=fixed_now)

        joined = result.message + " " + " ".join(result.notes)
        for v in result.violations:
            joined += " " + v.detail
        assert key_body not in joined


# ---------------------------------------------------------------------------
# P3-WP12a — the gate fires through kms_request (live enforcement)
# ---------------------------------------------------------------------------


class TestMintGateThroughKmsRequest:
    """The WP13a hook is IN the mint path: ``kms_request`` refuses on a
    hardening violation. Proves the gate fires at the mint entry point —
    not merely that the standalone validator returns ``ok=False``."""

    def test_0644_file_refuses_through_kms_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cryptography.fernet import Fernet

        from daemon.services.kms_lite import (
            KMSUnavailableError,
            kms_request,
            reset_store_for_tests,
        )

        key_file = tmp_path / "world-readable.key"
        _write_key_file(key_file, mode=0o644, body=Fernet.generate_key().decode())
        monkeypatch.setenv(SYSTEM_ENCRYPTION_KEY_FILE_ENV, str(key_file))
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        reset_store_for_tests()
        try:
            with pytest.raises(KMSUnavailableError):
                kms_request(service="opendesign", reason="wp13a gate proof")
        finally:
            reset_store_for_tests()

    def test_fresh_0600_file_mints_through_kms_request(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The compliant path mints end-to-end: file source read by the
        store, gate passes, handle issued. Also proves the gate does not
        over-refuse (0o600 + fresh mtime is accepted). The key file is
        written NOW (no mtime override) so the real-clock age check in
        the mint path sees a fresh file."""
        from cryptography.fernet import Fernet

        from daemon.services.kms_lite import (
            kms_request,
            reset_store_for_tests,
        )

        key_file = tmp_path / "hardened.key"
        _write_key_file(key_file, mode=0o600, body=Fernet.generate_key().decode())
        monkeypatch.setenv(SYSTEM_ENCRYPTION_KEY_FILE_ENV, str(key_file))
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        reset_store_for_tests()
        try:
            record = kms_request(service="opendesign", reason="wp13a happy path")
            assert record["handle"].startswith("KMS_HANDLE_")
            assert len(record["fingerprint"]) == 16
        finally:
            reset_store_for_tests()
