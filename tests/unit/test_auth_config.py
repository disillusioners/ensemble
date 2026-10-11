"""Unit tests for ``AuthConfig`` — the OAuth2 Phase 1 config surface.

phase1-plan §4 row 1 (T1.1). Asserts:

* Default ``enabled=False`` when ``auth:`` is absent ([REQ] §6.3 row 9).
* OFF + everything-set boots normally — the section parses every field
  but the combination validators are silently skipped (NFR-2 / G1.3).
* All 23 ``ENSEMBLE_AUTH_*`` env vars from [REQ] §6.1 parse to their
  fields (exact env names, table-listed defaults elsewhere).
* CSV AND JSON list forms both parse for ``scopes`` / ``allowed_origins``
  (D1.4 — shared ``_parse_csv_or_json_list``).
* ``live_view_external_base_url_fallback`` defaults ``None``
  (AMEND v1.1 / D2) and is NOT env-populatable (strip mechanism).
* ``kb_mcp_auth_enabled`` is NOT env-populatable either (AMEND v1.2/A3
  reserved seam).
* The shipped ``config.yaml`` ``auth.enabled: false`` key does NOT
  defeat an operator's ``ENSEMBLE_AUTH_ENABLED=true`` (env-over-yaml
  resolution in ``load_config`` — the init-kwarg-beats-env inversion
  guard).

Test env: fresh per-worktree venv (``uv sync``); convention is
``.venv/bin/python -m pytest`` from the worktree root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from daemon.config import AuthConfig, Config, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── helpers ──────────────────────────────────────────────────────────────────


def _parse(env: dict[str, str]) -> AuthConfig:
    """Construct ``AuthConfig`` with the given env vars monkeypatched in."""
    import os

    saved = {k: os.environ.pop(k, None) for k in env}
    try:
        os.environ.update(env)
        return AuthConfig()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── default OFF ──────────────────────────────────────────────────────────────


class TestDefaultOff:
    def test_default_enabled_false_when_auth_absent(self):
        """§6.3 row 9 baseline: no env, no yaml → enabled=False."""
        assert _parse({}).enabled is False

    def test_root_config_default_factory(self):
        """Root Config aggregates the section additively (D1.1)."""
        import os

        for key in list(os.environ):
            if key.startswith("ENSEMBLE_AUTH_"):
                del os.environ[key]
        config = Config()
        assert isinstance(config.auth, AuthConfig)
        assert config.auth.enabled is False

    def test_off_plus_everything_set_boots_normally(self):
        """OFF + everything-set (including OUT-OF-RANGE values that would
        refuse when enabled) → constructs fine, config silently ignored
        ([REQ] §6.3 row 9; NFR-2)."""
        section = _parse(
            {
                "ENSEMBLE_AUTH_ENABLED": "false",
                "ENSEMBLE_AUTH_CLIENT_ID": "my-app",
                "ENSEMBLE_AUTH_CLIENT_SECRET": "s3cret",
                "ENSEMBLE_AUTH_ISSUER": "http://insecure.example.com",  # would refuse ON
                "ENSEMBLE_AUTH_SCOPES": "",  # would refuse ON (row 13)
                "ENSEMBLE_AUTH_ALLOWED_ORIGINS": "",  # would refuse ON (row 5)
                "ENSEMBLE_AUTH_SESSION_TTL_SECONDS": "10",  # out of range
                "ENSEMBLE_AUTH_CLOCK_SKEW_SECONDS": "9999",  # out of range
                "ENSEMBLE_AUTH_COOKIE_SECURE": "false",  # would refuse ON (row 15)
                "ENSEMBLE_AUTH_COOKIE_SAMESITE": "Strict",  # would WARN ON (row 14)
            }
        )
        assert section.enabled is False
        assert section.client_id == "my-app"
        assert section.session_ttl_seconds == 10  # parsed, not validated

    def test_off_plus_everything_set_via_root_config(self):
        """Same contract through the root aggregation (env-only source)."""
        import os

        env = {
            "ENSEMBLE_AUTH_ENABLED": "false",
            "ENSEMBLE_AUTH_ISSUER": "http://insecure.example.com",
            "ENSEMBLE_AUTH_ALLOWED_ORIGINS": "",
        }
        saved = {k: os.environ.pop(k, None) for k in env}
        try:
            os.environ.update(env)
            config = Config()
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        assert config.auth.enabled is False
        assert str(config.auth.issuer) == "http://insecure.example.com/"

    def test_env_typo_guard_is_off_gated(self, monkeypatch):
        """A stale/typo'd ENSEMBLE_AUTH_* env var is only refused when
        auth is ON (NFR-2: an OFF deployment must boot byte-equivalent)."""
        monkeypatch.setenv("ENSEMBLE_AUTH_TYPO_X", "1")
        assert _parse({}).enabled is False  # no raise when OFF


# ── all 23 env vars parse ([REQ] §6.1 table) ─────────────────────────────────


@pytest.mark.parametrize(
    ("env_name", "env_value", "field", "expected"),
    [
        ("ENSEMBLE_AUTH_ENABLED", "true", "enabled", True),
        ("ENSEMBLE_AUTH_CLIENT_ID", "my-app", "client_id", "my-app"),
        ("ENSEMBLE_AUTH_CLIENT_SECRET", "s3cret", "client_secret", SecretStr("s3cret")),
        (
            "ENSEMBLE_AUTH_ISSUER",
            "https://idp.example.com",
            "issuer",
            "https://idp.example.com/",
        ),
        (
            "ENSEMBLE_AUTH_DISCOVERY_URL",
            "https://idp.example.com/realms/m/.well-known/openid-configuration",
            "discovery_url",
            "https://idp.example.com/realms/m/.well-known/openid-configuration",
        ),
        (
            "ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE",
            "https://ensemble.example.com",
            "external_redirect_base",
            "https://ensemble.example.com",
        ),
        (
            "ENSEMBLE_AUTH_REDIRECT_URI",
            "https://ensemble.example.com/api/auth/callback",
            "redirect_uri",
            "https://ensemble.example.com/api/auth/callback",
        ),
        ("ENSEMBLE_AUTH_SCOPES", "openid,email", "scopes", ["openid", "email"]),
        ("ENSEMBLE_AUTH_SESSION_TTL_SECONDS", "7200", "session_ttl_seconds", 7200),
        ("ENSEMBLE_AUTH_SLIDING_RENEWAL", "true", "sliding_renewal", True),
        ("ENSEMBLE_AUTH_CLOCK_SKEW_SECONDS", "30", "clock_skew_seconds", 30),
        (
            "ENSEMBLE_AUTH_DISCOVERY_CACHE_TTL_SECONDS",
            "120",
            "discovery_cache_ttl_seconds",
            120,
        ),
        ("ENSEMBLE_AUTH_JWKS_CACHE_TTL_SECONDS", "240", "jwks_cache_ttl_seconds", 240),
        ("ENSEMBLE_AUTH_COOKIE_SAMESITE", "None", "cookie_samesite", "None"),
        ("ENSEMBLE_AUTH_COOKIE_SECURE", "false", "cookie_secure", False),
        ("ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE", "true", "allow_insecure_cookie", True),
        (
            "ENSEMBLE_AUTH_FINGERPRINT_BY_IP_SUBNET",
            "true",
            "fingerprint_by_ip_subnet",
            True,
        ),
        (
            "ENSEMBLE_AUTH_BEARER_TOKEN_TTL_SECONDS",
            "3600",
            "bearer_token_ttl_seconds",
            3600,
        ),
        ("ENSEMBLE_AUTH_DOCS_ENABLED", "false", "docs_enabled", False),
        ("ENSEMBLE_AUTH_VSCODE_PROTECTED", "true", "vscode_protected", True),
        (
            "ENSEMBLE_AUTH_ALLOWED_ORIGINS",
            "https://a.example.com,https://b.example.com",
            "allowed_origins",
            ["https://a.example.com", "https://b.example.com"],
        ),
        ("ENSEMBLE_AUTH_BOOTSTRAP_TOKEN", "tok-123", "bootstrap_token", SecretStr("tok-123")),
        (
            "ENSEMBLE_AUTH_ANONYMOUS_VIEW_PATHS",
            "/views/x,/views/y",
            "anonymous_view_paths",
            ("/views/x", "/views/y"),
        ),
    ],
)
def test_all_23_env_vars_parse(monkeypatch, env_name, env_value, field, expected):
    """Every [REQ] §6.1 env var binds to its field with the parsed value.

    Baseline is a VALID enabled section so each single override still
    constructs (rows like ``COOKIE_SECURE=false`` would refuse without
    the ``ALLOW_INSECURE_COOKIE`` opt-in) — the combination-refusal
    matrix itself is covered in test_auth_config_validation.py; this
    test pins the parsing/binding of all 23 names.
    """
    monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
    monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_ID", "my-app")
    monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("ENSEMBLE_AUTH_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("ENSEMBLE_AUTH_ALLOWED_ORIGINS", "https://ensemble.example.com")
    monkeypatch.setenv("ENSEMBLE_AUTH_ALLOW_INSECURE_COOKIE", "true")
    monkeypatch.setenv(env_name, env_value)
    section = AuthConfig()
    actual = getattr(section, field)
    if isinstance(expected, SecretStr):
        assert isinstance(actual, SecretStr)
        assert actual.get_secret_value() == expected.get_secret_value()
    elif field == "issuer" or field == "discovery_url":
        # HttpUrl normalizes (trailing slash); compare stringly.
        assert str(actual) == expected
    else:
        assert actual == expected


def test_table_defaults_when_env_unset():
    """Unset env → the exact [REQ] §6.1 default column."""
    section = _parse({})
    assert section.enabled is False
    assert section.client_id is None
    assert section.client_secret is None
    assert section.issuer is None
    assert section.discovery_url is None
    assert section.external_redirect_base is None
    assert section.redirect_uri is None
    assert section.scopes == ["openid", "profile", "email"]
    assert section.session_ttl_seconds == 28800
    assert section.sliding_renewal is False
    assert section.clock_skew_seconds == 60
    assert section.discovery_cache_ttl_seconds == 3600
    assert section.jwks_cache_ttl_seconds == 3600
    assert section.cookie_samesite == "Lax"
    assert section.cookie_secure is True
    assert section.allow_insecure_cookie is False
    assert section.fingerprint_by_ip_subnet is False
    assert section.bearer_token_ttl_seconds == 2592000
    assert section.docs_enabled is True
    assert section.vscode_protected is False
    assert section.allowed_origins == []
    assert section.bootstrap_token is None
    assert section.anonymous_view_paths == ()


# ── CSV + JSON list forms (D1.4) ─────────────────────────────────────────────


class TestCsvAndJsonListForms:
    def test_scopes_csv_form(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_AUTH_SCOPES", "openid, profile ,email")
        assert AuthConfig().scopes == ["openid", "profile", "email"]

    def test_scopes_json_form(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_AUTH_SCOPES", '["openid","profile"]')
        assert AuthConfig().scopes == ["openid", "profile"]

    def test_allowed_origins_csv_form(self, monkeypatch):
        monkeypatch.setenv(
            "ENSEMBLE_AUTH_ALLOWED_ORIGINS", "https://a.example.com, https://b.example.com"
        )
        assert AuthConfig().allowed_origins == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_allowed_origins_json_form(self, monkeypatch):
        monkeypatch.setenv(
            "ENSEMBLE_AUTH_ALLOWED_ORIGINS", '["https://a.example.com","https://b.example.com"]'
        )
        assert AuthConfig().allowed_origins == [
            "https://a.example.com",
            "https://b.example.com",
        ]

    def test_empty_csv_form_parses_empty(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_AUTH_ALLOWED_ORIGINS", "")
        assert AuthConfig().allowed_origins == []

    def test_anonymous_view_paths_csv_to_tuple(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_AUTH_ANONYMOUS_VIEW_PATHS", "/views/a, /views/b")
        paths = AuthConfig().anonymous_view_paths
        assert paths == ("/views/a", "/views/b")
        assert isinstance(paths, tuple)


# ── non-env derived/reserved fields (strip mechanism) ───────────────────────


class TestNonEnvFields:
    def test_fallback_default_none_amended_v1_1_d2(self):
        """``live_view_external_base_url_fallback`` default ``None``
        (AMEND v1.1 / D2)."""
        assert _parse({}).live_view_external_base_url_fallback is None

    def test_fallback_not_env_populatable(self, monkeypatch):
        """The env-looking name is deliberately ineffective (strip)."""
        monkeypatch.setenv(
            "ENSEMBLE_AUTH_LIVE_VIEW_EXTERNAL_BASE_URL_FALLBACK",
            "https://should-not-win.example.com",
        )
        assert AuthConfig().live_view_external_base_url_fallback is None

    def test_fallback_not_init_populatable(self):
        """Yaml passthrough (init kwarg) cannot set it either — the only
        legitimate writer is the post-construction lifespan assignment."""
        section = AuthConfig(**{"live_view_external_base_url_fallback": "https://x"})
        assert section.live_view_external_base_url_fallback is None

    def test_kb_mcp_auth_enabled_default_false(self):
        assert _parse({}).kb_mcp_auth_enabled is False

    def test_kb_mcp_auth_enabled_not_env_populatable(self, monkeypatch):
        """Reserved seam (AMEND v1.2/A3) — env cannot flip it."""
        monkeypatch.setenv("ENSEMBLE_AUTH_KB_MCP_AUTH_ENABLED", "true")
        assert AuthConfig().kb_mcp_auth_enabled is False

    def test_kb_mcp_auth_enabled_not_init_populatable(self):
        assert AuthConfig(**{"kb_mcp_auth_enabled": True}).kb_mcp_auth_enabled is False


# ── shipped config.yaml: default OFF + env-over-yaml for `enabled` ──────────


class TestShippedYamlInteraction:
    def test_shipped_yaml_default_off(self):
        """The shipped ``auth.enabled: false`` boots OFF."""
        config = load_config(str(REPO_ROOT / "config.yaml"))
        assert config.auth.enabled is False

    def test_env_enabled_wins_over_shipped_yaml_false(self, monkeypatch):
        """``ENSEMBLE_AUTH_ENABLED=true`` must NOT be silently defeated by
        the shipped ``enabled: false`` yaml key (init-kwarg-beats-env
        inversion guard — explicit env-over-yaml resolution in
        ``load_config``). Full valid credentials so the boot succeeds."""
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
        monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_ID", "my-app")
        monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_SECRET", "s3cret")
        monkeypatch.setenv("ENSEMBLE_AUTH_ISSUER", "https://idp.example.com")
        monkeypatch.setenv("ENSEMBLE_AUTH_ALLOWED_ORIGINS", "https://ensemble.example.com")
        config = load_config(str(REPO_ROOT / "config.yaml"))
        assert config.auth.enabled is True

    def test_yaml_absent_env_only_enabled(self, monkeypatch, tmp_path):
        """An auth-less yaml + env-only enablement still enables (env-only
        deployments)."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("daemon:\n  host: 127.0.0.1\n  port: 8088\n")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(yaml_file))
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
        monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_ID", "my-app")
        monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_SECRET", "s3cret")
        monkeypatch.setenv("ENSEMBLE_AUTH_ISSUER", "https://idp.example.com")
        monkeypatch.setenv("ENSEMBLE_AUTH_ALLOWED_ORIGINS", "https://ensemble.example.com")
        config = load_config(str(yaml_file))
        assert config.auth.enabled is True
        assert config.auth.client_id == "my-app"

    def test_non_dict_auth_section_raises_loudly(self, tmp_path, monkeypatch):
        """``auth: 5`` → loud ValueError naming the section (critical_notes
        precedent — no silent fall-through to defaults)."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("auth: 5\n")
        with pytest.raises(ValueError, match="config.auth must be a mapping"):
            load_config(str(yaml_file))


# ── D1.3: unknown ENSEMBLE_AUTH_* env var fails loudly when ON ──────────────


class TestTypoGuardD13:
    def test_unknown_env_var_refused_when_enabled(self, monkeypatch):
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
        monkeypatch.setenv("ENSEMBLE_AUTH_TYPO_X", "1")
        with pytest.raises(Exception) as excinfo:  # ValidationError from AuthConfig()
            AuthConfig()
        assert "ENSEMBLE_AUTH_TYPO_X" in str(excinfo.value)

    def test_exact_var_names_still_accepted_when_enabled(self, monkeypatch):
        """The scan must not false-positive on the 23 real names (the
        enabled-gated missing-fields refusal is the EXPECTED failure
        here, not a typo refusal)."""
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
        with pytest.raises(Exception) as excinfo:
            AuthConfig()
        assert "unknown ENSEMBLE_AUTH_" not in str(excinfo.value)
        assert "missing" in str(excinfo.value)
