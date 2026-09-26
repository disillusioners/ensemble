"""KMS marker resolver (P3-WP6).

Validates the marker substitution contract at the MCP spawn seam:

* marker values resolve to plaintext via the in-memory KMS store
* non-marker values pass through unchanged (back-compat for LOG_LEVEL etc.)
* ``resolve_env`` is a NEW dict — the input is never mutated
* unknown handles raise :class:`KMSMarkerResolutionError` (fail-closed;
  passing the literal marker to the subprocess env would be a bug)
* marker / non-marker discrimination via :func:`is_marker`
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from daemon.services import kms_lite
from daemon.services.kms_lite import (
    KMS_HANDLE_PREFIX,
    KMS_MARKER_PREFIX,
    KMS_MARKER_SUFFIX,
    build_marker,
    kms_request,
    reset_store_for_tests,
)
from daemon.services.kms_resolver import (
    KMSMarkerResolutionError,
    is_marker,
    resolve_env,
    resolve_headers,
)


@pytest.fixture(autouse=True)
def _reset_store(monkeypatch: pytest.MonkeyPatch):
    """Provision a Fernet key and reset the singleton between tests."""
    monkeypatch.setenv("SYSTEM_ENCRYPTION_KEY", Fernet.generate_key().decode())
    reset_store_for_tests()
    yield
    reset_store_for_tests()


# ---------------------------------------------------------------------------
# resolve_env — happy path
# ---------------------------------------------------------------------------


class TestResolveEnvHappyPath:
    def test_resolves_marker_to_plaintext(self) -> None:
        record = kms_request("opendesign", "install")
        env = {"OPENDESIGN_API_KEY": build_marker(record["handle"])}
        resolved = resolve_env(env)
        assert resolved is not None
        assert resolved["OPENDESIGN_API_KEY"] != env["OPENDESIGN_API_KEY"]
        # The plaintext is the actual token (43 chars of token_urlsafe).
        assert len(resolved["OPENDESIGN_API_KEY"]) >= 32
        # And the marker is gone.
        assert is_marker(resolved["OPENDESIGN_API_KEY"]) is False

    def test_non_marker_values_pass_through(self) -> None:
        env = {"LOG_LEVEL": "info", "MY_MCP_TRANSPORT": "stdio"}
        resolved = resolve_env(env)
        assert resolved == env  # byte-equal

    def test_mixed_marker_and_plaintext(self) -> None:
        record = kms_request("opendesign", "install")
        env = {
            "OPENDESIGN_API_KEY": build_marker(record["handle"]),
            "LOG_LEVEL": "info",
        }
        resolved = resolve_env(env)
        assert resolved is not None
        assert resolved["OPENDESIGN_API_KEY"] != env["OPENDESIGN_API_KEY"]
        assert resolved["LOG_LEVEL"] == "info"

    def test_returns_new_dict_does_not_mutate(self) -> None:
        record = kms_request("opendesign", "install")
        env = {"OPENDESIGN_API_KEY": build_marker(record["handle"])}
        original_marker_value = env["OPENDESIGN_API_KEY"]
        resolved = resolve_env(env)
        assert resolved is not env
        # The original dict was not mutated.
        assert env["OPENDESIGN_API_KEY"] == original_marker_value
        assert is_marker(env["OPENDESIGN_API_KEY"])

    def test_none_env_returns_none(self) -> None:
        assert resolve_env(None) is None

    def test_empty_env_returns_empty(self) -> None:
        assert resolve_env({}) == {}


# ---------------------------------------------------------------------------
# resolve_env — failure surfaces
# ---------------------------------------------------------------------------


class TestResolveEnvFailures:
    def test_unknown_handle_raises(self) -> None:
        # Construct a marker for a handle that was never minted.
        bogus_handle = KMS_HANDLE_PREFIX + "deadbeef" * 4
        env = {"OPENDESIGN_API_KEY": build_marker(bogus_handle)}
        with pytest.raises(KMSMarkerResolutionError) as excinfo:
            resolve_env(env)
        assert bogus_handle in str(excinfo.value)

    def test_marker_partial_match_does_not_substitute(self) -> None:
        # A value that *contains* a marker substring but is not a full
        # marker is passed through unchanged. We pin this so a future
        # refactor that loosens the regex doesn't silently mangle
        # plaintext values like ``"prefix__KMS_REF__foo__suffix"``.
        record = kms_request("opendesign", "install")
        marker = build_marker(record["handle"])
        env = {"WEIRD_KEY": f"prefix_{marker}_suffix"}
        resolved = resolve_env(env)
        assert resolved is not None
        assert resolved["WEIRD_KEY"] == f"prefix_{marker}_suffix"

    def test_value_just_marker_prefix_does_not_substitute(self) -> None:
        # ``__KMS_REF__`` alone (no handle, no closing ``__``) is not a
        # marker. The regex requires the full shape.
        env = {"WEIRD_KEY": KMS_MARKER_PREFIX}
        resolved = resolve_env(env)
        assert resolved is not None
        assert resolved["WEIRD_KEY"] == KMS_MARKER_PREFIX


# ---------------------------------------------------------------------------
# resolve_headers — parity with resolve_env
# ---------------------------------------------------------------------------


class TestResolveHeaders:
    def test_resolves_header_marker(self) -> None:
        record = kms_request("opendesign", "install")
        headers = {"Authorization": f"Bearer {build_marker(record['handle'])}"}
        # The full header value is the marker (Bearer <marker>) —
        # this is not a *full* marker per the regex, so it passes
        # through. Pin the expected day-1 behaviour: marker values are
        # not embedded in composite header values.
        resolved = resolve_headers(headers)
        assert resolved == headers

    def test_resolves_pure_marker_header(self) -> None:
        record = kms_request("opendesign", "install")
        headers = {"X-API-KEY": build_marker(record["handle"])}
        resolved = resolve_headers(headers)
        assert resolved is not None
        assert resolved["X-API-KEY"] != headers["X-API-KEY"]
        assert is_marker(resolved["X-API-KEY"]) is False

    def test_none_headers_returns_none(self) -> None:
        assert resolve_headers(None) is None


# ---------------------------------------------------------------------------
# is_marker — discriminator
# ---------------------------------------------------------------------------


class TestIsMarker:
    def test_recognises_full_marker(self) -> None:
        record = kms_request("opendesign", "install")
        assert is_marker(build_marker(record["handle"])) is True

    def test_rejects_partial_marker(self) -> None:
        assert is_marker(KMS_MARKER_PREFIX) is False
        assert is_marker(KMS_MARKER_PREFIX + "abc") is False
        assert is_marker(KMS_MARKER_SUFFIX) is False
        assert is_marker("plaintext") is False

    def test_marker_must_include_handle_prefix(self) -> None:
        # ``__KMS_REF__something__`` (no ``KMS_HANDLE_`` namespace) is
        # not a KMS marker — it's just a plaintext value that happens
        # to share the prefix/suffix.
        assert is_marker(KMS_MARKER_PREFIX + "something" + KMS_MARKER_SUFFIX) is False