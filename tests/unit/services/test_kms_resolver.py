"""KMS marker resolver (P3-WP6 + v1.3.0-bridge LANE-2 env-ref).

Validates the marker substitution contract at the MCP spawn seam:

* marker values resolve to plaintext via the in-memory KMS store
* non-marker values pass through unchanged (back-compat for LOG_LEVEL etc.)
* ``resolve_env`` is a NEW dict — the input is never mutated
* unknown handles raise :class:`KMSMarkerResolutionError` (fail-closed;
  passing the literal marker to the subprocess env would be a bug)
* marker / non-marker discrimination via :func:`is_marker`

v1.3.0-bridge additions (LANE-2 env-ref):

* env-ref markers (``__KMS_ENV__<VAR>__``) resolve to
  ``os.environ[<VAR>]`` at spawn time — same fail-closed invariant
* missing env var raises :class:`KMSMarkerResolutionError` naming the
  var, never a silent empty-string fallback
* ``is_env_marker`` discriminates LANE-2 from LANE-1
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from daemon.services import kms_lite
from daemon.services.kms_lite import (
    KMS_ENV_MARKER_PREFIX,
    KMS_ENV_MARKER_SUFFIX,
    KMS_HANDLE_PREFIX,
    KMS_MARKER_PREFIX,
    KMS_MARKER_SUFFIX,
    build_env_marker,
    build_marker,
    kms_request,
    reset_store_for_tests,
)
from daemon.services.kms_resolver import (
    KMSMarkerResolutionError,
    is_env_marker,
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


# ---------------------------------------------------------------------------
# LANE-2 env-ref marker (v1.3.0-bridge) — happy path
# ---------------------------------------------------------------------------


class TestEnvMarkerHappyPath:
    def test_resolves_env_marker_to_plaintext(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``__KMS_ENV__<VAR>__`` marker resolves to ``os.environ[<VAR>]``
        — the plaintext exists ONLY in the resolved dict (in-RAM, never
        stored). The marker carries only the var NAME, not the value."""
        marker = build_env_marker("OPENAI_API_KEY")
        # Pin a value that's distinctive — easier to detect leaks.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-plaintext-DO-NOT-LEAK")
        env = {"BYOK_API_KEY": marker}
        resolved = resolve_env(env)
        assert resolved is not None
        assert resolved["BYOK_API_KEY"] == "sk-test-plaintext-DO-NOT-LEAK"
        # The marker is gone in the resolved dict.
        assert is_env_marker(resolved["BYOK_API_KEY"]) is False
        # But the input was not mutated.
        assert env["BYOK_API_KEY"] == marker
        assert is_env_marker(env["BYOK_API_KEY"]) is True

    def test_env_marker_resolves_at_call_time_not_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Spawn-time invariant — env values are read FRESH per resolve_env
        call. Pin by mutating the env var between two resolutions and
        confirming each resolution picks up the current value."""
        marker = build_env_marker("LIVE_VAR")
        env = {"X": marker}
        monkeypatch.setenv("LIVE_VAR", "first-value")
        assert resolve_env(env)["X"] == "first-value"
        monkeypatch.setenv("LIVE_VAR", "second-value")
        assert resolve_env(env)["X"] == "second-value"

    def test_non_marker_values_pass_through_with_env_marker_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Mixed env block: env-ref marker resolves, plaintext stays."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-plaintext-DO-NOT-LEAK")
        env = {
            "BYOK_API_KEY": build_env_marker("OPENAI_API_KEY"),
            "LOG_LEVEL": "info",
            "BYOK_BASE_URL": "https://llm-supervisor-proxy.example/v1",
        }
        resolved = resolve_env(env)
        assert resolved["BYOK_API_KEY"] == "sk-test-plaintext-DO-NOT-LEAK"
        assert resolved["LOG_LEVEL"] == "info"
        assert resolved["BYOK_BASE_URL"] == "https://llm-supervisor-proxy.example/v1"

    def test_empty_string_env_value_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty (but present) env value passes through verbatim — the
        resolver does NOT silent-fallback to a literal marker on ``""``.
        The MCP server decides what to do with an empty value (mirrors
        how a plain ``mcp_set_env`` write of ``""`` would behave)."""
        monkeypatch.setenv("EMPTY_VAR", "")
        marker = build_env_marker("EMPTY_VAR")
        env = {"X": marker}
        resolved = resolve_env(env)
        assert resolved["X"] == ""


# ---------------------------------------------------------------------------
# LANE-2 env-ref marker — failure surfaces (fail-closed)
# ---------------------------------------------------------------------------


class TestEnvMarkerFailures:
    def test_missing_env_var_raises_with_var_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``__KMS_ENV__<VAR>__`` marker naming an ABSENT env var
        fails closed: :class:`KMSMarkerResolutionError`, error names
        the VAR, NEVER a value (there is no value to leak)."""
        monkeypatch.delenv("NOT_SET_VAR", raising=False)
        marker = build_env_marker("NOT_SET_VAR")
        env = {"BYOK_API_KEY": marker}
        with pytest.raises(KMSMarkerResolutionError) as excinfo:
            resolve_env(env)
        assert "NOT_SET_VAR" in str(excinfo.value)
        # Defensive: no value should appear in the error — there is no
        # value to begin with, but ensure no future regression.
        assert "sk-" not in str(excinfo.value)
        assert "plaintext" not in str(excinfo.value).lower()

    def test_missing_env_var_does_not_silent_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bug class this closes: a missing var would land the LITERAL
        marker string in the subprocess env (``__KMS_ENV__NOT_SET_VAR__``)
        — an obvious bug. Pin: the resolution MUST raise, never return
        the literal."""
        monkeypatch.delenv("MISSING_LANE2", raising=False)
        marker = build_env_marker("MISSING_LANE2")
        with pytest.raises(KMSMarkerResolutionError):
            resolve_env({"X": marker})

    def test_env_marker_partial_match_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A value that *contains* an env-marker substring but is not a
        full marker is passed through unchanged. Same discipline as the
        LANE-1 partial-match test above."""
        monkeypatch.setenv("WEIRD_PARTIAL", "yes")
        marker = build_env_marker("WEIRD_PARTIAL")
        env = {"WEIRD_KEY": f"prefix_{marker}_suffix"}
        resolved = resolve_env(env)
        assert resolved is not None
        assert resolved["WEIRD_KEY"] == f"prefix_{marker}_suffix"

    def test_env_marker_malformed_var_name_passes_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An env-ref-shaped string whose var name is NOT a valid POSIX
        env-var identifier is NOT a marker — it is passed through. The regex
        enforces the identifier discipline; this test pins the regex."""
        # Space in the name → not a valid identifier → not a marker.
        weird = f"{KMS_ENV_MARKER_PREFIX}BAD NAME{KMS_ENV_MARKER_SUFFIX}"
        env = {"X": weird}
        resolved = resolve_env(env)
        assert resolved["X"] == weird
        # Leading digit → not a valid identifier → not a marker.
        weird2 = f"{KMS_ENV_MARKER_PREFIX}1FOO{KMS_ENV_MARKER_SUFFIX}"
        env2 = {"X": weird2}
        assert resolve_env(env2)["X"] == weird2


# ---------------------------------------------------------------------------
# LANE-2 env-ref marker — co-existence with LANE-1 handle marker
# ---------------------------------------------------------------------------


class TestBothLanesCoexist:
    def test_mixed_handle_and_env_marker_resolve_independently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single env block may carry BOTH a LANE-1 handle marker
        (resolves via the in-memory KMS store) AND a LANE-2 env-ref
        marker (resolves via ``os.environ``) — they are independent."""
        record = kms_request("opendesign", "install")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-plaintext-DO-NOT-LEAK")
        env = {
            "OD_API_TOKEN": build_marker(record["handle"]),
            "BYOK_API_KEY": build_env_marker("OPENAI_API_KEY"),
        }
        resolved = resolve_env(env)
        # LANE-1: minted plaintext from the KMS store.
        from daemon.services.kms_lite import kms_resolve_handle
        assert resolved["OD_API_TOKEN"] == kms_resolve_handle(record["handle"])
        # LANE-2: env-var plaintext.
        assert resolved["BYOK_API_KEY"] == "sk-test-plaintext-DO-NOT-LEAK"

    def test_handle_unknown_fails_first_over_env_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If a LANE-1 handle is unknown AND a LANE-2 env-ref is present,
        the resolver raises on the handle error first (LANE-1 is tried
        first per the order documented in ``_resolve_value``). The env
        ref is irrelevant on a failure — pin the LANE-1 fail-closed."""
        bogus_handle = KMS_HANDLE_PREFIX + "deadbeef" * 4
        monkeypatch.setenv("PRESENT_VAR", "present-value")
        env = {
            "X_BAD": build_marker(bogus_handle),
            "Y_OK": build_env_marker("PRESENT_VAR"),
        }
        with pytest.raises(KMSMarkerResolutionError) as excinfo:
            resolve_env(env)
        assert bogus_handle in str(excinfo.value)


# ---------------------------------------------------------------------------
# is_env_marker — LANE-2 discriminator
# ---------------------------------------------------------------------------


class TestIsEnvMarker:
    def test_recognises_full_env_marker(self) -> None:
        assert is_env_marker(build_env_marker("OPENAI_API_KEY")) is True

    def test_rejects_handle_marker(self) -> None:
        """A LANE-1 handle marker is NOT a LANE-2 env marker."""
        record = kms_request("opendesign", "install")
        marker = build_marker(record["handle"])
        assert is_env_marker(marker) is False
        # And ``is_marker`` recognises it as LANE-1 (broader discriminator).
        assert is_marker(marker) is True

    def test_rejects_partial_env_marker(self) -> None:
        assert is_env_marker(KMS_ENV_MARKER_PREFIX) is False
        assert is_env_marker(KMS_ENV_MARKER_PREFIX + "abc") is False
        assert is_env_marker(KMS_ENV_MARKER_SUFFIX) is False
        assert is_env_marker("plaintext") is False

    def test_rejects_malformed_var_name(self) -> None:
        # Hyphens / spaces / leading digits → not a marker (regex gate).
        assert (
            is_env_marker(f"{KMS_ENV_MARKER_PREFIX}BAD-NAME{KMS_ENV_MARKER_SUFFIX}")
            is False
        )
        assert (
            is_env_marker(f"{KMS_ENV_MARKER_PREFIX}BAD NAME{KMS_ENV_MARKER_SUFFIX}")
            is False
        )
        assert (
            is_env_marker(f"{KMS_ENV_MARKER_PREFIX}1FOO{KMS_ENV_MARKER_SUFFIX}")
            is False
        )
