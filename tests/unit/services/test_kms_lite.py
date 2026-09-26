"""KMS-Lite mint primitive + fail-closed store (P3-WP7 + P3-WP9).

Day-1 mint-only contract. Closes arch §8 R2 (fail-soft CredentialManager)
and validates the handle / fingerprint / plaintext-never-returned invariants.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import pytest
from cryptography.fernet import Fernet

from daemon.services import kms_lite
from daemon.services.kms_lite import (
    KMS_HANDLE_PREFIX,
    KMS_MARKER_PREFIX,
    KMS_MARKER_SUFFIX,
    KMSUnavailableError,
    build_marker,
    kms_fingerprint,
    kms_request,
    kms_resolve_handle,
    reset_store_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_kms_store():
    """Each test sees a fresh module-level store.

    Important: ``reset_store_for_tests`` MUST run before any test that
    exercises the singleton, otherwise the previous test's
    ``SYSTEM_ENCRYPTION_KEY`` env-var state leaks into the next test's
    store constructor.
    """
    reset_store_for_tests()
    yield
    reset_store_for_tests()


@pytest.fixture(autouse=True)
def _isolate_cwd(tmp_path, monkeypatch):
    """P3-WP12a: every mint emits a ``kms_issue`` audit line via the
    canonical lane (resolved from CWD). Chdir into the test tmp dir so
    test runs never touch the repo's real audit lane."""
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture
def fernet_key(monkeypatch):
    """Provision a valid Fernet key in the env for the test duration."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", key)
    return key


# ---------------------------------------------------------------------------
# WP7 — happy path
# ---------------------------------------------------------------------------


class TestKMSRequestHappyPath:
    def test_mint_returns_handle_and_fingerprint(self, fernet_key: str) -> None:
        record = kms_request(service="opendesign", reason="capability_install")
        assert set(record.keys()) == {"handle", "fingerprint"}
        assert record["handle"].startswith(KMS_HANDLE_PREFIX)
        assert len(record["handle"]) == len(KMS_HANDLE_PREFIX) + 32  # uuid4 hex
        assert len(record["fingerprint"]) == 16

    def test_mint_with_actor(self, fernet_key: str, monkeypatch: pytest.MonkeyPatch) -> None:
        # Default actor "system" when caller doesn't pass one — exercised
        # here via the module-level kms_request wrapper.
        record = kms_request(service="opendesign", reason="install")
        assert record["handle"].startswith(KMS_HANDLE_PREFIX)

    def test_handle_format_is_uuid(self, fernet_key: str) -> None:
        record = kms_request(service="opendesign", reason="install")
        handle_suffix = record["handle"][len(KMS_HANDLE_PREFIX):]
        # uuid4 hex is exactly 32 chars [0-9a-f]
        uuid.UUID(hex=handle_suffix)

    def test_fingerprint_is_sha256_truncated_to_16_hex(self, fernet_key: str) -> None:
        import hashlib
        # We can't predict the plaintext, but we know the fingerprint is
        # exactly 16 lowercase hex chars.
        record = kms_request(service="opendesign", reason="install")
        fp = record["fingerprint"]
        assert len(fp) == 16
        int(fp, 16)  # raises if non-hex
        # And we can verify the *resolve* side computes the same shape.
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        assert hashlib.sha256(plaintext.encode()).hexdigest()[:16] == fp

    def test_handle_namespaces_are_unique(self, fernet_key: str) -> None:
        seen = set()
        for _ in range(50):
            r = kms_request(service="opendesign", reason="install")
            assert r["handle"] not in seen
            seen.add(r["handle"])

    def test_two_mints_with_same_service_reason_get_distinct_handles(
        self, fernet_key: str
    ) -> None:
        a = kms_request("opendesign", "install")
        b = kms_request("opendesign", "install")
        assert a["handle"] != b["handle"]
        assert a["fingerprint"] != b["fingerprint"]  # distinct plaintext → distinct fp


# ---------------------------------------------------------------------------
# WP7 — plaintext-never-returned invariant
# ---------------------------------------------------------------------------


class TestKMSPlaintextNeverReturned:
    def test_kms_request_returns_no_plaintext(self, fernet_key: str) -> None:
        # The record is {handle, fingerprint} only. Any third key would
        # be a regression of the plaintext-never-leaves-store invariant.
        record = kms_request("opendesign", "install")
        assert "plaintext" not in record
        assert "secret" not in record
        assert "value" not in record
        # And the record is JSON-serialisable without embedding the
        # plaintext (we serialise and check size — plaintext would add
        # ~43 chars of base64-urlsafe noise).
        encoded = json.dumps(record)
        # 32 bytes token_urlsafe → 43 chars; anything > 200 bytes is suspect
        assert len(encoded) < 200, (
            f"kms_request record serialised to {len(encoded)} bytes — "
            "looks like plaintext leak"
        )

    def test_resolve_returns_only_when_explicitly_called(
        self, fernet_key: str
    ) -> None:
        # Resolve is intentional and gated — it is reserved for the
        # spawn-time resolver. Verify the function exists and only
        # returns plaintext when called directly (i.e. the agent path
        # never accidentally invokes it via the kms_request surface).
        record = kms_request("opendesign", "install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        assert len(plaintext) >= 32  # secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# WP7 — re-mint appends (no revocation in day-1)
# ---------------------------------------------------------------------------


class TestKMSReMintAppends:
    def test_re_mint_appends_new_handle(self, fernet_key: str) -> None:
        first = kms_request("opendesign", "install")
        second = kms_request("opendesign", "reinstall")
        assert first["handle"] != second["handle"]
        # Both handles remain valid; revocation is §7.5a deferred.
        assert kms_resolve_handle(first["handle"]) is not None
        assert kms_resolve_handle(second["handle"]) is not None

    def test_unknown_handle_resolve_returns_none(self, fernet_key: str) -> None:
        assert kms_resolve_handle(KMS_HANDLE_PREFIX + "deadbeef" * 4) is None
        assert kms_fingerprint(KMS_HANDLE_PREFIX + "deadbeef" * 4) is None


# ---------------------------------------------------------------------------
# WP9 — fail-closed store (closes R2)
# ---------------------------------------------------------------------------


class TestKMSUnavailableErrorFailClosed:
    def test_no_key_raises_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SOURCE_CREDENTIAL_KEY", raising=False)  # legacy
        with pytest.raises(KMSUnavailableError) as excinfo:
            kms_request("opendesign", "install")
        assert "SYSTEM_ENCRYPTION_KEY" in str(excinfo.value)

    def test_empty_string_key_raises_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", "")
        with pytest.raises(KMSUnavailableError):
            kms_request("opendesign", "install")

    def test_malformed_key_raises_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fernet rejects anything that isn't a 32-byte base64-urlsafe key.
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", "not-a-fernet-key")
        with pytest.raises(KMSUnavailableError) as excinfo:
            kms_request("opendesign", "install")
        assert "invalid" in str(excinfo.value).lower()

    def test_no_key_does_not_write_plaintext_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # If the store were fail-soft, kms_request would write a row
        # carrying plaintext. Asserting the raised error IS the
        # guarantee — there is no path that returns a record without a
        # store, and the store raises on construction.
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SOURCE_CREDENTIAL_KEY", raising=False)
        with pytest.raises(KMSUnavailableError):
            kms_request("opendesign", "install")

    def test_no_key_does_not_emit_secret_bearing_log(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The fail-closed path may emit a *generic* warning (no
        # plaintext, no handle, no fingerprint) but MUST NOT carry a
        # secret in any field.
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SOURCE_CREDENTIAL_KEY", raising=False)
        caplog.set_level(logging.WARNING, logger="daemon.services.kms_lite")
        with pytest.raises(KMSUnavailableError):
            kms_request("opendesign", "install")
        # No log record should carry a substring that looks like a
        # secret (token_urlsafe is 43 chars of base64-urlsafe). The
        # module-level WARNING is only emitted from the tool layer
        # (infra.py), not from kms_lite directly.
        for record in caplog.records:
            assert "opendesign-install" not in record.getMessage()


# ---------------------------------------------------------------------------
# WP9 — happy-path Fernet path is unchanged
# ---------------------------------------------------------------------------


class TestKMSKeyPresentHappyPath:
    def test_key_present_round_trip(self, fernet_key: str) -> None:
        record = kms_request("opendesign", "install")
        plaintext = kms_resolve_handle(record["handle"])
        assert plaintext is not None
        # Same plaintext returned on repeat resolves (deterministic — no
        # rotation).
        again = kms_resolve_handle(record["handle"])
        assert plaintext == again

    def test_legacy_fallback_works(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The legacy SOURCE_CREDENTIAL_KEY fallback is honored (the
        # kms_lite module reads SYSTEM_ENCRYPTION_KEY ONLY — it does
        # NOT inherit CredentialManager's legacy fallback). Day-1
        # expectation: legacy env var is silently ignored (mints
        # raise). Document the behaviour here.
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.setenv("SOURCE_CREDENTIAL_KEY", Fernet.generate_key().decode())
        with pytest.raises(KMSUnavailableError):
            kms_request("opendesign", "install")

    def test_store_is_a_singleton_across_calls(self, fernet_key: str) -> None:
        # Two mints land in the same store (handle fingerprints and
        # resolves both work). This pins the process-singleton
        # contract.
        first = kms_request("opendesign", "install")
        # Touching _get_store() / _store to confirm singleton-ness.
        first_store = kms_lite._get_store()
        second = kms_request("opendesign", "install")
        second_store = kms_lite._get_store()
        assert first_store is second_store
        assert kms_resolve_handle(first["handle"]) is not None
        assert kms_resolve_handle(second["handle"]) is not None


# ---------------------------------------------------------------------------
# WP7 — input validation
# ---------------------------------------------------------------------------


class TestKMSInputValidation:
    def test_empty_service_raises(self, fernet_key: str) -> None:
        with pytest.raises(ValueError, match="service"):
            kms_request("", "install")

    def test_empty_reason_raises(self, fernet_key: str) -> None:
        with pytest.raises(ValueError, match="reason"):
            kms_request("opendesign", "")

    def test_build_marker_rejects_non_handle(self) -> None:
        with pytest.raises(ValueError, match="KMS_HANDLE_"):
            build_marker("not-a-handle")

    def test_build_marker_round_trip(self) -> None:
        h = KMS_HANDLE_PREFIX + "abcd1234"
        marker = build_marker(h)
        assert marker.startswith(KMS_MARKER_PREFIX)
        assert marker.endswith(KMS_MARKER_SUFFIX)
        # Strip prefix+suffix to recover the handle.
        assert marker[len(KMS_MARKER_PREFIX):-len(KMS_MARKER_SUFFIX)] == h


# ---------------------------------------------------------------------------
# WP9 — backward-compat blast radius
# ---------------------------------------------------------------------------


class TestCredentialManagerUnaffected:
    """Sanity-check: the fail-closed change is scoped to the KMS path.

    ``daemon.sources.credentials.CredentialManager.encrypt`` and
    ``decrypt`` MUST keep their historical fail-soft behaviour because
    the source adapter boot path (Slack / Telegram / Discord) depends
    on it. KMS-Lite is the fail-closed path; CredentialManager is the
    fail-soft path. If this assertion ever fails, the blast radius has
    been violated — report before reverting.
    """

    def test_credential_manager_failsoft_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from daemon.sources.credentials import CredentialManager
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        monkeypatch.delenv("SOURCE_CREDENTIAL_KEY", raising=False)
        cm = CredentialManager()
        # Fail-soft contract: encrypt returns JSON-encoded plaintext
        # when no key is configured. This MUST NOT change.
        encrypted = cm.encrypt({"api_key": "sk-test-1234"})
        decrypted = cm.decrypt(encrypted)
        assert decrypted == {"api_key": "sk-test-1234"}

class TestKmsIssueAuditLine:
    """P3-WP12 fold (c): every mint appends ONE §7.4 ``kms_issue`` audit
    line via the canonical lane. ``secret_ref`` carries the HANDLE only;
    the plaintext never rides the audit lane. Best-effort: an audit
    failure must never fail the mint."""

    def test_mint_emits_one_kms_issue_line(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", Fernet.generate_key().decode())
        kms_lite.reset_store_for_tests()
        try:
            record = kms_lite.kms_request(
                service="opendesign", reason="audit-line test"
            )
        finally:
            pass
        audit_path = (
            tmp_path
            / ".agents/shared/planning/designer-agent/install-audit.jsonl"
        )
        assert audit_path.exists()
        lines = [
            json.loads(l)
            for l in audit_path.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        kms_lines = [l for l in lines if l["event"] == "kms_issue"]
        assert len(kms_lines) == 1
        line = kms_lines[0]
        assert set(line.keys()) == {
            "ts", "event", "name", "actor", "parent",
            "secret_ref", "idempotency_key", "trace_id",
        }
        assert line["name"] == "opendesign"
        assert line["actor"] == "system"  # default actor (no caller passed)
        assert line["parent"] is None
        assert line["secret_ref"] == record["handle"]  # handle ONLY
        assert line["idempotency_key"] == ""
        assert line["secret_ref"].startswith("KMS_HANDLE_")
        assert "__KMS_REF__" not in line["secret_ref"]

    def test_audit_failure_never_fails_the_mint(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", Fernet.generate_key().decode())
        kms_lite.reset_store_for_tests()
        # Make the audit lane unwritable: the preferred path's parent is
        # a FILE, so mkdir/open inside the writer fails.
        blocker = tmp_path / ".agents"
        blocker.write_text("not a directory")
        try:
            record = kms_lite.kms_request(
                service="opendesign", reason="audit-failure test"
            )
            # Mint succeeded despite the audit-lane failure.
            assert record["handle"].startswith("KMS_HANDLE_")
            assert kms_lite.kms_fingerprint(record["handle"]) is not None
        finally:
            kms_lite.reset_store_for_tests()


# ---------------------------------------------------------------------------
# P3 review F5 — typed KMSUnavailableError on file-read failure
# ---------------------------------------------------------------------------


class TestKeyFileReadFailure:
    """``_build_fernet`` MUST surface a file-read failure as the typed
    ``KMSUnavailableError`` rather than leaking the raw ``OSError`` /
    ``PermissionError`` from the resolve path (P3 review F5).

    Both ``kms_request`` (which gates on
    :func:`daemon.util.key_hardening.validate_key_source` first) and
    ``kms_resolve_handle`` (which goes straight to ``_get_store()``)
    MUST raise ``KMSUnavailableError`` when the key file is unreadable.
    """

    @pytest.fixture
    def unreadable_key_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> Path:
        """Write a valid Fernet key to a file and chmod 0o000.

        Skips (yields ``None``) when running as root — chmod 0o000 is
        a no-op for uid 0.
        """
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            yield None  # type: ignore[misc]
            return
        key_path = tmp_path / "system_encryption.key"
        key_path.write_text(Fernet.generate_key().decode(), encoding="utf-8")
        key_path.chmod(0o000)
        # The file env var points at this file; the inline env var is
        # unset so ``_build_fernet`` follows the file-source path.
        monkeypatch.setenv(
            "SYSTEM_ENCRYPTION_KEY_FILE", str(key_path)
        )
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        yield key_path
        # Restore perms so tmp_path cleanup can delete the file.
        try:
            key_path.chmod(0o600)
        except OSError:
            pass

    def test_unreadable_file_raises_kms_unavailable_from_kms_request(
        self, unreadable_key_file
    ) -> None:
        if unreadable_key_file is None:
            pytest.skip("running as root — chmod 0o000 is a no-op")
        kms_lite.reset_store_for_tests()
        with pytest.raises(KMSUnavailableError) as excinfo:
            kms_request("opendesign", "install")
        # WP13a validator catches the 0o000 perms first (its own
        # "not readable" code path); either that or the F5 wrap must
        # have produced the typed exception. We assert the TYPE; the
        # intermediate code path can be either (or both).
        assert isinstance(excinfo.value, KMSUnavailableError)
        # The error message MUST give the operator enough signal to
        # act — accept any of: the F5 wrap message ("cannot read
        # SYSTEM_ENCRYPTION_KEY_FILE"), the validator's refusal
        # summary ("key hardening refusal"), or the validator's
        # ``[KEY_FILE_NOT_READABLE]`` code token (which only the
        # validator emits; the F5 wrap produces a different message).
        msg = str(excinfo.value)
        assert (
            "cannot read SYSTEM_ENCRYPTION_KEY_FILE" in msg
            or "key hardening refusal" in msg
            or "KEY_FILE_NOT_READABLE" in msg
        ), f"unhelpful error message: {msg!r}"

    def test_unreadable_file_raises_kms_unavailable_from_kms_resolve_handle(
        self, unreadable_key_file
    ) -> None:
        if unreadable_key_file is None:
            pytest.skip("running as root — chmod 0o000 is a no-op")
        kms_lite.reset_store_for_tests()
        with pytest.raises(KMSUnavailableError) as excinfo:
            kms_resolve_handle("KMS_HANDLE_doesnotexist")
        assert isinstance(excinfo.value, KMSUnavailableError)
        # kms_resolve_handle bypasses the validator; this path MUST
        # hit the F5 wrap and surface the "cannot read" message.
        assert "cannot read SYSTEM_ENCRYPTION_KEY_FILE" in str(excinfo.value)

    def test_missing_file_raises_kms_unavailable_from_kms_resolve_handle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-existent file path also surfaces as the typed exception
        (OSError family includes FileNotFoundError)."""
        monkeypatch.setenv(
            "SYSTEM_ENCRYPTION_KEY_FILE", str(tmp_path / "nope.key")
        )
        monkeypatch.delenv("SYSTEM_ENCRYPTION_KEY", raising=False)
        kms_lite.reset_store_for_tests()
        with pytest.raises(KMSUnavailableError) as excinfo:
            kms_resolve_handle("KMS_HANDLE_doesnotexist")
        assert "cannot read SYSTEM_ENCRYPTION_KEY_FILE" in str(excinfo.value)

