"""Unit tests for the AuthConfig invalid-combination matrix (§6.3).

phase1-plan §4 row 2 (T1.2 / T1.3). The matrix has **14 active rows**
(15 table rows; row 6 — SQLite refusal — RETRACTED per D17 inert-tables
posture: NO PG-only or SQLite-refusal boot behavior exists anywhere,
AMEND v1.3 / F11 canonical count).

Lane split (D1.5), asserted per row below:

* Section-internal rows raise ``ValidationError`` at config-load time
  via ``AuthConfig._validate_combinations`` — rows 1-3 (missing
  required), 4 (http issuer), 5 (wildcard-CORS replacement), 7
  (redirect_uri cross-origin, section-local half), 11 (discovery
  origin mismatch), 13 (scopes missing openid), 15 (cookie_secure).
* Cross-section row 10 (``external_redirect_base`` vs the daemon bind
  host) and row 8's positive case are enumerated by the exit-78 helper
  :func:`daemon.config.auth_config_boot_refusals` — the daemon bind
  identity lives outside the section.
* Row 12 (bootstrap token) is the CLI mint lane's helpful message —
  NOT a boot refusal; the constant pins the message.
* Row 14 (SameSite=Strict) warns and proceeds.
* Rows 8/9 boot normally (positive cases).

The exit-78 helper is asserted at unit level (boot-level coverage is
the P1 subprocess harness + P5 canary per phase1-plan §4).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from daemon.config import (
    AUTH_BOOTSTRAP_TOKEN_REQUIRED_MESSAGE,
    AuthConfig,
    Config,
    auth_config_boot_refusals,
    load_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── helpers ──────────────────────────────────────────────────────────────────


def _valid_kwargs() -> dict:
    """A section that passes every §6.3 row — mutate ONE thing per test."""
    return {
        "enabled": True,
        "client_id": "my-app",
        "client_secret": SecretStr("s3cret"),
        "issuer": "https://idp.example.com",
        "allowed_origins": ["https://ensemble.example.com"],
        # scopes default carries 'openid'; TTLs/cookies carry table defaults
    }


def _refusal_for(**overrides) -> str:
    """Construct with overrides; return the refusal text (must raise)."""
    with pytest.raises(ValidationError) as excinfo:
        AuthConfig(**{**_valid_kwargs(), **overrides})
    return str(excinfo.value)


def _tmp_yaml(tmp_path: Path, body: str) -> Path:
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(body)
    return yaml_file


# ── rows 1-3: missing required (exit-78 lane — enumerated list) ─────────────


class TestRows1to3MissingRequired:
    def test_row1_missing_client_id_raises_with_env_var_named(self):
        text = _refusal_for(client_id=None)
        assert "auth.enabled=true but missing" in text
        assert "client_id" in text
        assert "ENSEMBLE_AUTH_CLIENT_ID" in text

    def test_row2_missing_client_secret_raises_with_env_var_named(self):
        text = _refusal_for(client_secret=None)
        assert "auth.enabled=true but missing" in text
        assert "ENSEMBLE_AUTH_CLIENT_SECRET" in text

    def test_row2b_empty_client_secret_counts_as_missing(self):
        """§6.3 rows say 'CLIENT_SECRET= (empty)' — empty/whitespace is
        missing, NOT accepted (SecretStr('') is truthy; the unwrap in
        _secret_str_missing catches it)."""
        text = _refusal_for(client_secret=SecretStr(""))
        assert "client_secret" in text

    def test_row3_missing_issuer_raises_with_env_var_named(self):
        text = _refusal_for(issuer=None)
        assert "auth.enabled=true but missing" in text
        assert "ENSEMBLE_AUTH_ISSUER" in text

    def test_all_three_missing_enumerated_together(self):
        with pytest.raises(ValidationError) as excinfo:
            AuthConfig(enabled=True)
        text = str(excinfo.value)
        for env_var in (
            "ENSEMBLE_AUTH_CLIENT_ID",
            "ENSEMBLE_AUTH_CLIENT_SECRET",
            "ENSEMBLE_AUTH_ISSUER",
        ):
            assert env_var in text

    def test_exit78_helper_returns_enumerated_missing_list(self, tmp_path, monkeypatch):
        """T1.3: the exit-78 helper returns the operator-readable lines
        naming each missing env var exactly (unit level; the boot-level
        exit is the subprocess harness)."""
        yaml_file = _tmp_yaml(tmp_path, "daemon:\n  host: 127.0.0.1\n  port: 8088\n")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(yaml_file))
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
        lines = auth_config_boot_refusals()
        assert isinstance(lines, list) and lines
        joined = "\n".join(lines)
        for env_var in (
            "ENSEMBLE_AUTH_CLIENT_ID",
            "ENSEMBLE_AUTH_CLIENT_SECRET",
            "ENSEMBLE_AUTH_ISSUER",
        ):
            assert env_var in joined

    def test_exit78_helper_silent_when_off(self, tmp_path, monkeypatch):
        """OFF state → [] — silently ignored (§6.3 row 9 / G1.3)."""
        yaml_file = _tmp_yaml(tmp_path, "daemon:\n  host: 127.0.0.1\n  port: 8088\n")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(yaml_file))
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "false")
        monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_ID", "whatever")
        assert auth_config_boot_refusals() == []

    def test_exit78_helper_empty_when_enabled_and_valid(self):
        lines = auth_config_boot_refusals(config=Config(auth=_valid_kwargs()))
        assert lines == []


# ── row 4: http issuer without allow_insecure_cookie ─────────────────────────


class TestRow4IssuerScheme:
    def test_http_issuer_refused(self):
        text = _refusal_for(issuer="http://idp.example.com")
        assert "auth.issuer must be https://" in text

    def test_http_issuer_allowed_with_insecure_optin(self):
        kwargs = _valid_kwargs()
        kwargs.update(issuer="http://idp.lan", allow_insecure_cookie=True)
        section = AuthConfig(**kwargs)
        assert str(section.issuer).startswith("http://")


# ── row 5: wildcard CORS + credentials replacement ───────────────────────────


class TestRow5CorsReplacement:
    def test_empty_allowed_origins_refused(self):
        text = _refusal_for(allowed_origins=[])
        assert "auth.allowed_origins must replace wildcard + credentials" in text

    def test_nonempty_allowed_origins_passes(self):
        section = AuthConfig(**_valid_kwargs())
        assert section.allowed_origins == ["https://ensemble.example.com"]


# ── row 6: RETRACTED (D17) — asserted ABSENT ─────────────────────────────────


class TestRow6RetractedD17:
    def test_no_sqlite_refusal_exists(self):
        """D17 inert-tables posture: no field, no env var, no validator
        branch, no boot refusal keyed on the DB backend. The removed
        ``require_sqlite_rejection`` env var must not be bound (an
        ``ENSEMBLE_AUTH_REQUIRE_SQLITE_REJECTION`` var is UNKNOWN → the
        D1.3 typo guard names it when enabled)."""
        assert not hasattr(AuthConfig, "require_sqlite_rejection")
        assert "require_sqlite_rejection" not in AuthConfig.model_fields


# ── row 7: redirect_uri cross-origin (section-local half) ───────────────────


class TestRow7RedirectUriSameOrigin:
    def test_redirect_uri_different_origin_from_base_refused(self):
        text = _refusal_for(
            external_redirect_base="https://ensemble.example.com",
            redirect_uri="https://evil.example.com/api/auth/callback",
        )
        assert "auth.redirect_uri must be same-origin" in text

    def test_redirect_uri_same_origin_as_base_passes(self):
        section = AuthConfig(
            **_valid_kwargs(),
            external_redirect_base="https://ensemble.example.com",
            redirect_uri="https://ensemble.example.com/api/auth/callback",
        )
        assert section.redirect_uri.endswith("/api/auth/callback")

    def test_redirect_uri_with_unset_base_not_refused_at_config_time(self):
        """Base unset → the effective origin is Host-capture/bind (runtime
        quantity, D6 chain) — no static refusal; the login path handles
        the unresolvable case with 503 LOGIN_NOT_CONFIGURED (P3)."""
        section = AuthConfig(
            **_valid_kwargs(),
            redirect_uri="https://ensemble.example.com/api/auth/callback",
        )
        assert section.redirect_uri is not None


# ── rows 8 + 10: external_redirect_base vs daemon bind (helper lane) ────────


class TestRows8And10ExternalRedirectBaseBindOrigin:
    def _config(self, bind_host: str, base: str | None) -> Config:
        kwargs = _valid_kwargs()
        if base is not None:
            kwargs["external_redirect_base"] = base
        return Config(daemon={"host": bind_host, "port": 8088}, auth=kwargs)

    def test_row10_different_origin_from_bind_refused(self):
        """Row 10: the operator override cannot redirect to a foreign
        origin — 'must be same-origin as the daemon bind host'."""
        config = self._config("127.0.0.1", "https://evil.example.com")
        lines = auth_config_boot_refusals(config)
        assert len(lines) == 1
        assert (
            "auth.external_redirect_base must be same-origin as the daemon "
            "bind host" in lines[0]
        )

    def test_row8_same_origin_as_bind_boots_normally(self):
        config = self._config("127.0.0.1", "http://127.0.0.1:8088")
        assert auth_config_boot_refusals(config) == []

    def test_row8_wildcard_bind_carveout_accepts_public_hostname(self):
        """Wildcard bind (0.0.0.0) has NO static self-origin — the
        operator override IS the origin declaration (the documented
        reverse-proxy wire-up sets a public hostname over a wildcard
        bind). Runtime enforcement stays with the P3 resolver (D6)."""
        config = self._config("0.0.0.0", "https://ensemble.example.com")
        assert auth_config_boot_refusals(config) == []

    def test_row10_extra_shape_guards(self):
        """§6.1 validation column: scheme+host[:port] only — no path."""
        with pytest.raises(ValidationError, match="scheme\\+host"):
            AuthConfig(
                **_valid_kwargs(),
                external_redirect_base="https://ensemble.example.com/some/path",
            )


# ── row 11: discovery/issuer mismatch ────────────────────────────────────────


class TestRow11DiscoveryMismatch:
    def test_cross_origin_discovery_url_refused(self):
        text = _refusal_for(
            discovery_url="https://other-idp.example.com/.well-known/openid-configuration"
        )
        assert "discovery document issuer does not match auth.issuer" in text

    def test_same_origin_different_path_discovery_passes(self):
        """Same origin, different path (the Keycloak realms shape) is
        statically consistent — the post-fetch issuer check is the P2+
        lifespan lane."""
        section = AuthConfig(
            **_valid_kwargs(),
            discovery_url="https://idp.example.com/realms/m/.well-known/openid-configuration",
        )
        assert section.discovery_url is not None

    def test_derived_default_when_unset(self):
        section = AuthConfig(**_valid_kwargs())
        assert section.discovery_url is None


# ── row 12: bootstrap token (CLI lane — helpful message, NOT a refusal) ─────


class TestRow12BootstrapTokenCliLane:
    def test_empty_bootstrap_token_is_not_a_boot_refusal(self):
        """§6.3 row 12 behavior: 'CLI fails with helpful message' — the
        config itself is VALID without the token (§6.1: 'recommended')."""
        section = AuthConfig(**_valid_kwargs(), bootstrap_token=None)
        assert section.enabled is True

    def test_helpful_message_constant_names_env_var(self):
        assert "ENSEMBLE_AUTH_BOOTSTRAP_TOKEN" in AUTH_BOOTSTRAP_TOKEN_REQUIRED_MESSAGE
        assert AUTH_BOOTSTRAP_TOKEN_REQUIRED_MESSAGE == (
            "set ENSEMBLE_AUTH_BOOTSTRAP_TOKEN to mint first admin token"
        )


# ── row 14: SameSite=Strict — WARN, proceed ──────────────────────────────────


class TestRow14StrictSamesiteWarns:
    def test_strict_warns_but_constructs(self, caplog):
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            section = AuthConfig(**_valid_kwargs(), cookie_samesite="Strict")
        assert section.cookie_samesite == "Strict"
        assert any(
            "Strict" in r.message and "break" in r.message.lower()
            for r in caplog.records
        )

    def test_lax_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING, logger="daemon.config"):
            AuthConfig(**_valid_kwargs(), cookie_samesite="Lax")
        assert not any("Strict" in r.message for r in caplog.records)


# ── row 15: cookie_secure=false without opt-in ───────────────────────────────


class TestRow15CookieSecure:
    def test_cookie_secure_false_without_optin_refused(self):
        text = _refusal_for(cookie_secure=False)
        assert "cookie_secure=false requires allow_insecure_cookie=true" in text

    def test_cookie_secure_false_with_optin_passes(self):
        section = AuthConfig(
            **_valid_kwargs(), cookie_secure=False, allow_insecure_cookie=True
        )
        assert section.cookie_secure is False


# ── minor #2 (2026-10-11 review): SameSite=None without Secure ───────────────


class TestMinor2SameSiteNoneRequiresSecure:
    """REFUSAL (not warn) — deliberately row-15-shaped: an insecure
    None cookie is a security regression (browsers reject it outright;
    a client that honors one ships the session credential cross-site).
    Row 14 (Strict) stays a WARN because that one breaks function, not
    security."""

    def test_samesite_none_without_secure_refused(self):
        """The legal permissive combo (row 15 satisfied via the
        allow_insecure opt-in) still refuses the None-without-Secure
        pair — the minor-#2 check is its own row, not row-15 fallout."""
        text = _refusal_for(
            cookie_samesite="None",
            cookie_secure=False,
            allow_insecure_cookie=True,
        )
        assert "cookie_samesite=None requires cookie_secure=true" in text
        assert "ENSEMBLE_AUTH_COOKIE_SAMESITE" in text

    def test_samesite_none_with_secure_passes(self):
        section = AuthConfig(**_valid_kwargs(), cookie_samesite="None")
        assert section.cookie_samesite == "None"
        assert section.cookie_secure is True

    def test_samesite_none_insecure_without_optin_hits_row15_first(self):
        """Row 15 fires before the minor-#2 check — either refusal is
        correct; pin that at least one names the cookie posture."""
        text = _refusal_for(
            cookie_samesite="None",
            cookie_secure=False,
            allow_insecure_cookie=False,
        )
        assert "cookie_secure" in text or "cookie_samesite" in text


# ── row 9: OFF + everything-set boots normally (positive) ────────────────────


class TestRow9DisabledEverythingSet:
    def test_disabled_section_with_invalid_values_boots(self):
        """Every would-be refusal is skipped when enabled=false."""
        section = AuthConfig(
            enabled=False,
            client_id=None,
            client_secret=None,
            issuer="http://insecure.example.com",
            scopes=[],
            allowed_origins=[],
            cookie_secure=False,
            allow_insecure_cookie=False,
            session_ttl_seconds=10,
        )
        assert section.enabled is False
        assert section.session_ttl_seconds == 10


# ── §6.1 range validation (enabled-gated) ────────────────────────────────────


class TestRangeValidation:
    @pytest.mark.parametrize(
        ("field", "bad"),
        [
            ("session_ttl_seconds", 59),
            ("session_ttl_seconds", 86401),
            ("clock_skew_seconds", -1),
            ("clock_skew_seconds", 301),
            ("discovery_cache_ttl_seconds", 59),
            ("discovery_cache_ttl_seconds", 86401),
            ("jwks_cache_ttl_seconds", 59),
            ("jwks_cache_ttl_seconds", 86401),
            ("bearer_token_ttl_seconds", 59),
            ("bearer_token_ttl_seconds", 31536001),
        ],
    )
    def test_out_of_range_refused_when_enabled(self, field, bad):
        with pytest.raises(ValidationError, match="out of range"):
            AuthConfig(**_valid_kwargs(), **{field: bad})

    def test_range_bounds_inclusive_pass(self):
        section = AuthConfig(
            **_valid_kwargs(),
            session_ttl_seconds=86400,
            clock_skew_seconds=0,
            discovery_cache_ttl_seconds=60,
            jwks_cache_ttl_seconds=86400,
            bearer_token_ttl_seconds=31536000,
        )
        assert section.session_ttl_seconds == 86400


# ── the exit-78 helper never swallows non-auth config errors ────────────────


class TestHelperErrorLaneIsolation:
    def test_non_auth_validation_error_reraises(self, tmp_path, monkeypatch):
        """A broken NON-auth section must NOT be converted into an auth
        refusal — the normal load_config traceback is the surface."""
        yaml_file = _tmp_yaml(tmp_path, "limits:\n  llm_concurrency: not-an-int\n")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(yaml_file))
        monkeypatch.delenv("ENSEMBLE_AUTH_ENABLED", raising=False)
        with pytest.raises(ValidationError) as excinfo:
            auth_config_boot_refusals()
        assert excinfo.value.title != "AuthConfig"


# ── shipped config.yaml stays OFF (zero-refusal sanity at the real file) ────


class TestShippedYamlStillOff:
    def test_shipped_yaml_boots_with_empty_refusals(self):
        config = load_config(str(REPO_ROOT / "config.yaml"))
        assert config.auth.enabled is False
        assert auth_config_boot_refusals(config) == []


# ── R3 (2026-10-11 merge gate): vscode_protected ∧ enabled=false ────────────


class TestR3VscodeProtectedCrossField:
    """The protective-sounding flag must never open /vscode to remote
    peers: with auth disabled the OAuth2 middleware does not construct
    while api.py sizes the proxy's loopback peer gate as
    ``not vscode_protected`` — i.e. flag ∧ ¬auth disables the only
    gate. Refused by the exit-78 helper (the SINGLE refusal site);
    each variable alone never refuses."""

    def test_combo_refused_naming_both_vars(self):
        config = Config(
            daemon={"host": "127.0.0.1", "port": 8088},
            auth={**_valid_kwargs(), "enabled": False, "vscode_protected": True},
        )
        lines = auth_config_boot_refusals(config)
        assert len(lines) == 1
        assert "ENSEMBLE_AUTH_VSCODE_PROTECTED" in lines[0]
        assert "ENSEMBLE_AUTH_ENABLED" in lines[0]
        assert "vscode_protected=true requires auth.enabled=true" in lines[0]

    def test_flag_alone_with_auth_on_does_not_refuse(self):
        config = Config(
            daemon={"host": "127.0.0.1", "port": 8088},
            auth={**_valid_kwargs(), "vscode_protected": True},
        )
        assert auth_config_boot_refusals(config) == []

    def test_auth_off_alone_does_not_refuse(self):
        config = Config(
            daemon={"host": "127.0.0.1", "port": 8088},
            auth={**_valid_kwargs(), "enabled": False},
        )
        assert auth_config_boot_refusals(config) == []

    def test_combo_via_helper_load_lane(self, tmp_path, monkeypatch):
        """The config-load lane (helper called with no config) surfaces
        the same refusal — the exit-78 preflight consumes exactly this
        return shape."""
        yaml_file = _tmp_yaml(tmp_path, "daemon:\n  host: 127.0.0.1\n  port: 8088\n")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(yaml_file))
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "false")
        monkeypatch.setenv("ENSEMBLE_AUTH_VSCODE_PROTECTED", "true")
        lines = auth_config_boot_refusals()
        assert len(lines) == 1
        assert "ENSEMBLE_AUTH_VSCODE_PROTECTED" in lines[0]
