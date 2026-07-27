"""
Tests for the SYSTEM_ENCRYPTION_KEY / SOURCE_CREDENTIAL_KEY fallback helper
in ``daemon.sources.credentials``.

These tests verify the backward-compat behavior: when the canonical
``SYSTEM_ENCRYPTION_KEY`` is set, it is used (the legacy name is ignored);
when only the deprecated ``SOURCE_CREDENTIAL_KEY`` is set, the legacy value
is used and a deprecation warning is logged; when neither is set, the
helper returns ``None`` so callers fall back to unencrypted storage with
the existing warning.
"""

from __future__ import annotations

import logging
import os

import pytest
from cryptography.fernet import Fernet

from daemon.sources.credentials import (
    SYSTEM_ENCRYPTION_KEY_ENV,
    _LEGACY_SOURCE_CREDENTIAL_KEY_ENV,
    CredentialManager,
    get_encryption_key,
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure both env var names start unset for every test."""
    monkeypatch.delenv(SYSTEM_ENCRYPTION_KEY_ENV, raising=False)
    monkeypatch.delenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, raising=False)


@pytest.fixture
def fernet_key() -> str:
    """Return a freshly generated URL-safe base64 Fernet key."""
    return Fernet.generate_key().decode()


# ----------------------------------------------------------------------------
# get_encryption_key() — direct tests
# ----------------------------------------------------------------------------


def test_get_encryption_key_returns_none_when_neither_set():
    """When neither env var is set, the helper returns None (no warning)."""
    result = get_encryption_key()
    assert result is None


def test_get_encryption_key_prefers_canonical_name(monkeypatch, fernet_key):
    """When SYSTEM_ENCRYPTION_KEY is set, it is returned and the legacy
    name is ignored — even if the legacy name is also set."""
    monkeypatch.setenv(SYSTEM_ENCRYPTION_KEY_ENV, fernet_key)
    monkeypatch.setenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, "legacy-ignored")

    assert get_encryption_key() == fernet_key


def test_get_encryption_key_falls_back_to_legacy(monkeypatch, fernet_key, caplog):
    """When only the legacy name is set, it is returned and a deprecation
    WARNING is logged with the new env var name."""
    monkeypatch.setenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, fernet_key)

    with caplog.at_level(logging.WARNING, logger="daemon.sources.credentials"):
        result = get_encryption_key()

    assert result == fernet_key
    # Deprecation warning must mention both names so operators know what to do.
    assert any(
        SYSTEM_ENCRYPTION_KEY_ENV in record.message
        and _LEGACY_SOURCE_CREDENTIAL_KEY_ENV in record.message
        for record in caplog.records
    ), f"Expected deprecation warning mentioning both env var names, got: {[r.message for r in caplog.records]}"


def test_get_encryption_key_treats_empty_canonical_as_unset(monkeypatch, fernet_key):
    """An empty SYSTEM_ENCRYPTION_KEY is treated as unset → legacy fallback fires."""
    monkeypatch.setenv(SYSTEM_ENCRYPTION_KEY_ENV, "")
    monkeypatch.setenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, fernet_key)

    assert get_encryption_key() == fernet_key


# ----------------------------------------------------------------------------
# CredentialManager — integration with the fallback
# ----------------------------------------------------------------------------


def test_credential_manager_uses_canonical_key(monkeypatch, fernet_key):
    """CredentialManager encrypts when SYSTEM_ENCRYPTION_KEY is set."""
    monkeypatch.setenv(SYSTEM_ENCRYPTION_KEY_ENV, fernet_key)

    manager = CredentialManager()
    assert manager.is_encryption_available() is True

    ciphertext = manager.encrypt({"token": "secret"})
    assert ciphertext != '{"token": "secret"}'
    assert manager.decrypt(ciphertext) == {"token": "secret"}


def test_credential_manager_falls_back_to_legacy_key(monkeypatch, fernet_key):
    """CredentialManager still works when only the deprecated name is set."""
    monkeypatch.setenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, fernet_key)

    manager = CredentialManager()
    assert manager.is_encryption_available() is True

    ciphertext = manager.encrypt({"token": "secret"})
    assert manager.decrypt(ciphertext) == {"token": "secret"}


def test_credential_manager_logs_deprecation_warning_for_legacy(monkeypatch, fernet_key, caplog):
    """Building CredentialManager with only the legacy key emits a deprecation warning."""
    monkeypatch.setenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, fernet_key)

    with caplog.at_level(logging.WARNING, logger="daemon.sources.credentials"):
        CredentialManager()

    # At least one warning should mention SYSTEM_ENCRYPTION_KEY so operators
    # know to migrate.
    assert any(
        SYSTEM_ENCRYPTION_KEY_ENV in record.message
        for record in caplog.records
    ), f"Expected warning about SYSTEM_ENCRYPTION_KEY, got: {[r.message for r in caplog.records]}"


def test_credential_manager_unencrypted_when_neither_set(caplog):
    """When neither key is set, CredentialManager runs unencrypted with a warning."""
    with caplog.at_level(logging.WARNING, logger="daemon.sources.credentials"):
        manager = CredentialManager()

    assert manager.is_encryption_available() is False
    # Plain JSON round-trip still works.
    assert manager.decrypt(manager.encrypt({"a": 1})) == {"a": 1}
    # And the existing "no key provided" warning fires.
    assert any("SYSTEM_ENCRYPTION_KEY" in record.message for record in caplog.records)


def test_explicit_encryption_key_overrides_env(monkeypatch, fernet_key):
    """An explicit ``encryption_key`` argument takes precedence over any env var."""
    explicit = Fernet.generate_key().decode()
    monkeypatch.setenv(SYSTEM_ENCRYPTION_KEY_ENV, fernet_key)
    monkeypatch.setenv(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV, "ignored")

    manager = CredentialManager(encryption_key=explicit.encode())
    assert manager.is_encryption_available() is True

    # Round-trip with the explicit key (not the env var key).
    ciphertext = manager.encrypt({"k": "v"})
    assert manager.decrypt(ciphertext) == {"k": "v"}

    # And ciphertext is NOT decryptable by the env-var key, confirming
    # the explicit key actually won.
    other = CredentialManager(encryption_key=fernet_key.encode())
    with pytest.raises(Exception):
        # cryptography raises InvalidToken on mismatch.
        other.decrypt(ciphertext)
