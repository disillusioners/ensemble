"""Unit tests for AuthConfig secret hygiene (NFR-12 groundwork).

phase1-plan §4 row 3 (T1.1 / D1.2). ``client_secret`` and
``bootstrap_token`` are ``SecretStr`` — the material must never leak
through ``repr()`` / ``str()`` of the section, the root config, the
parsed-field stringification, OR the exit-78 refusal lines (the
operator-readable ERROR path names env VARS, never values).
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from daemon.config import (
    AuthConfig,
    Config,
    auth_config_boot_refusals,
)

SECRET = "s3cret-value-never-leak-9f2c"
BOOTSTRAP = "bootstrap-value-never-leak-1a7d"


def _section_kwargs() -> dict:
    return {
        "enabled": True,
        "client_id": "my-app",
        "client_secret": SecretStr(SECRET),
        "issuer": "https://idp.example.com",
        "allowed_origins": ["https://ensemble.example.com"],
        "bootstrap_token": SecretStr(BOOTSTRAP),
    }


class TestSecretStrMasking:
    def test_section_repr_masks_client_secret(self):
        section = AuthConfig(**_section_kwargs())
        assert SECRET not in repr(section)
        assert "**********" in repr(section)

    def test_section_repr_masks_bootstrap_token(self):
        section = AuthConfig(**_section_kwargs())
        assert BOOTSTRAP not in repr(section)

    def test_section_str_masks_secrets(self):
        section = AuthConfig(**_section_kwargs())
        assert SECRET not in str(section)
        assert BOOTSTRAP not in str(section)

    def test_field_str_masks(self):
        section = AuthConfig(**_section_kwargs())
        assert SECRET not in str(section.client_secret)
        assert str(section.client_secret) == "**********"

    def test_root_config_repr_masks_secrets(self):
        config = Config(auth=_section_kwargs())
        assert SECRET not in repr(config)
        assert BOOTSTRAP not in repr(config)
        assert SECRET not in str(config)

    def test_model_dump_masks_secret_values(self):
        section = AuthConfig(**_section_kwargs())
        dumped = section.model_dump()
        assert SECRET not in repr(dumped["client_secret"])
        assert BOOTSTRAP not in repr(dumped["bootstrap_token"])
        assert dumped["client_secret"].get_secret_value() == SECRET


class TestRefusalLinesNeverCarrySecrets:
    def test_missing_secret_refusal_names_var_not_value(self, tmp_path, monkeypatch):
        """The exit-78 lane prints the ENV VAR NAME for a missing secret —
        and even when other fields carry values, no value materializes in
        the refusal lines."""
        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("daemon:\n  host: 127.0.0.1\n  port: 8088\n")
        monkeypatch.setenv("ENSEMBLE_CONFIG", str(yaml_file))
        monkeypatch.setenv("ENSEMBLE_AUTH_ENABLED", "true")
        monkeypatch.setenv("ENSEMBLE_AUTH_CLIENT_ID", "my-app")
        # client_secret intentionally unset
        lines = auth_config_boot_refusals()
        joined = "\n".join(lines)
        assert "ENSEMBLE_AUTH_CLIENT_SECRET" in joined
        assert SECRET not in joined  # unset anyway — but pin the invariant

    def test_section_error_text_does_not_leak_set_secret(self):
        """A matrix refusal raised on a section that HAS a secret set must
        not embed the secret in the error text (the error names fields,
        never values)."""
        kwargs = _section_kwargs()
        kwargs["cookie_secure"] = False  # trigger row 15 refusal
        with pytest.raises(Exception) as excinfo:
            AuthConfig(**kwargs)
        assert SECRET not in str(excinfo.value)
        assert BOOTSTRAP not in str(excinfo.value)
