"""fix-vscode-image-preview Step 2: ``VSCODE_BINARY_PATH`` env-override resolver.

Covers the documented precedence chain (daemon/config.py):

  1. ``_resolve_vscode_binary_path`` — pure string resolver (Shape A):
     env ``VSCODE_BINARY_PATH`` > yaml ``vscode.binary_path`` > ``None``.
  2. ``load_config`` end-to-end — precedence resolved EXPLICITLY at the
     resolution site (NOT by pydantic layering: the plain yaml passthrough
     handed ``binary_path`` (including the live-proven ``binary_path:
     null``) to ``Config(**config_dict)`` as an init kwarg, and
     pydantic-settings gives init kwargs priority over env vars —
     silently dead the operator knob; the manager then fell back to
     ``shutil.which("code-server")``, spawning the deprecated brew
     binary even with the env pointing at a standalone code-server).

Mirrors the fixture mechanics of ``tests/unit/test_compaction_model_config.py``
(string-resolver + load_config end-to-end precedence classes) and resets the
module caches ``load_config`` installs (kv-ambient, vscode webview-CSP)
via their ``_*_for_tests`` seams so no restart-to-flip state leaks.
"""

from __future__ import annotations

import pytest

from daemon.config import (
    VSCodeConfig,
    _resolve_vscode_binary_path,
    _reset_kv_ambient_for_tests,
    _reset_vscode_webview_csp_fix_for_tests,
    load_config,
)


# =============================================================================
# Config-state reset seam (load_config installs restart-to-flip caches)
# =============================================================================

@pytest.fixture(autouse=True)
def _reset_config_module_caches():
    """Leave the boot-time module caches COLD after every test (mirror of
    the reset discipline used by the kv-ambient / webview-CSP tests)."""
    yield
    _reset_kv_ambient_for_tests()
    _reset_vscode_webview_csp_fix_for_tests()


# =============================================================================
# Helpers
# =============================================================================

def _write_yaml(tmp_path, vscode_block: str | None) -> str:
    """Minimal loadable config.yaml with an optional vscode section."""
    text = """
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "test-key"
  model: "gpt-4"

persistence:
  db_path: "./data/instances.db"
"""
    if vscode_block is not None:
        text += f"\nvscode:\n{vscode_block}\n"
    config_file = tmp_path / "config.yaml"
    config_file.write_text(text)
    return str(config_file)


# =============================================================================
# Pure resolver: _resolve_vscode_binary_path (daemon/config.py)
# =============================================================================

class TestResolveVscodeBinaryPathPure:
    """env VSCODE_BINARY_PATH > yaml vscode.binary_path > None."""

    def test_env_wins_over_non_null_yaml(self) -> None:
        assert _resolve_vscode_binary_path(
            "/yaml/code-server", env_value="/env/code-server"
        ) == "/env/code-server"

    def test_env_wins_over_null_yaml(self) -> None:
        assert _resolve_vscode_binary_path(None, env_value="/env/code-server") == (
            "/env/code-server"
        )

    def test_yaml_honored_when_env_unset(self) -> None:
        assert _resolve_vscode_binary_path(
            "/yaml/code-server", env_value=None
        ) == "/yaml/code-server"

    def test_blank_env_treated_as_unset_yaml_wins(self) -> None:
        """Empty/whitespace env values are UNSET (launcher.sh exports bare
        ``KEY=`` lines verbatim) — the yaml value must not be shadowed."""
        assert _resolve_vscode_binary_path(
            "/yaml/code-server", env_value=""
        ) == "/yaml/code-server"
        assert _resolve_vscode_binary_path(
            "/yaml/code-server", env_value="   "
        ) == "/yaml/code-server"

    def test_yaml_none_normalizes_to_none(self) -> None:
        assert _resolve_vscode_binary_path(None, env_value=None) is None

    def test_yaml_blank_normalizes_to_none(self) -> None:
        assert _resolve_vscode_binary_path("", env_value=None) is None
        assert _resolve_vscode_binary_path("   ", env_value=None) is None

    def test_neither_set_yields_none(self) -> None:
        assert _resolve_vscode_binary_path(None, env_value=None) is None


# =============================================================================
# load_config end-to-end precedence
# =============================================================================

class TestLoadConfigVscodeBinaryPathPrecedence:
    """Precedence resolved EXPLICITLY in load_config, not pydantic layering."""

    def test_env_beats_null_yaml(self, tmp_path, monkeypatch) -> None:
        """The exact Run-1 defect shape: yaml ``binary_path: null`` used to
        arrive as an explicit ``None`` init kwarg and dead the env knob."""
        monkeypatch.setenv("VSCODE_BINARY_PATH", "/opt/code-server/bin/code-server")
        path = _write_yaml(tmp_path, "  binary_path: null")
        config = load_config(config_path=path)
        assert config.vscode.binary_path == "/opt/code-server/bin/code-server"

    def test_env_beats_non_null_yaml(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("VSCODE_BINARY_PATH", "/opt/code-server/bin/code-server")
        path = _write_yaml(tmp_path, '  binary_path: "/usr/local/bin/code-server"')
        config = load_config(config_path=path)
        assert config.vscode.binary_path == "/opt/code-server/bin/code-server"

    def test_yaml_honored_when_env_unset(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("VSCODE_BINARY_PATH", raising=False)
        path = _write_yaml(tmp_path, '  binary_path: "/usr/local/bin/code-server"')
        config = load_config(config_path=path)
        assert config.vscode.binary_path == "/usr/local/bin/code-server"

    def test_neither_set_defaults_to_none(self, tmp_path, monkeypatch) -> None:
        """None → manager PATH-lookup fallback (``shutil.which``), unchanged."""
        monkeypatch.delenv("VSCODE_BINARY_PATH", raising=False)
        path = _write_yaml(tmp_path, "  binary_path: null")
        config = load_config(config_path=path)
        assert config.vscode.binary_path is None

    def test_blank_env_falls_to_yaml(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("VSCODE_BINARY_PATH", "   ")
        path = _write_yaml(tmp_path, '  binary_path: "/usr/local/bin/code-server"')
        config = load_config(config_path=path)
        assert config.vscode.binary_path == "/usr/local/bin/code-server"

    def test_blank_env_and_null_yaml_yields_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("VSCODE_BINARY_PATH", "")
        path = _write_yaml(tmp_path, "  binary_path: null")
        config = load_config(config_path=path)
        assert config.vscode.binary_path is None

    def test_env_only_yaml_section_absent(self, tmp_path, monkeypatch) -> None:
        """Boundary pin (pre-existing pydantic-native path): no ``vscode:``
        key in yaml → no init kwarg exists and BaseSettings reads the env
        directly. Must KEEP working so the resolver never needs to fire
        for absent sections."""
        monkeypatch.setenv("VSCODE_BINARY_PATH", "/opt/code-server/bin/code-server")
        path = _write_yaml(tmp_path, None)
        config = load_config(config_path=path)
        assert config.vscode.binary_path == "/opt/code-server/bin/code-server"

    def test_other_vscode_yaml_keys_untouched(self, tmp_path, monkeypatch) -> None:
        """Resolution is scoped to ``binary_path``; sibling keys keep the
        exact pre-existing passthrough semantics (and the Step-1
        webview_csp_fix resolution still applies)."""
        monkeypatch.delenv("VSCODE_BINARY_PATH", raising=False)
        block = (
            "  binary_path: /usr/local/bin/code-server\n"
            "  allow_remote: true\n"
            "  user_data_dir: /tmp/vscode-data\n"
            "  extensions:\n"
            "    - eamodio.gitlens\n"
        )
        config = load_config(config_path=_write_yaml(tmp_path, block))
        assert config.vscode.binary_path == "/usr/local/bin/code-server"
        assert config.vscode.allow_remote is True
        assert config.vscode.user_data_dir == "/tmp/vscode-data"
        assert config.vscode.extensions == ["eamodio.gitlens"]


# =============================================================================
# Env-name drift pin (literal vs VSCodeConfig env_prefix + field)
# =============================================================================

class TestEnvNameContract:
    """The literal ``VSCODE_BINARY_PATH`` at the load_config call site MUST
    stay in sync with ``VSCodeConfig`` (``env_prefix="VSCODE_"`` + field
    ``binary_path``). Renaming either side without the other would dead
    the knob again — this class fails loudly on that drift."""

    def test_vscode_config_env_prefix_and_field(self) -> None:
        assert VSCodeConfig.model_config.get("env_prefix") == "VSCODE_"
        assert "binary_path" in VSCodeConfig.model_fields

    def test_model_reads_env_without_init_kwarg(self, monkeypatch) -> None:
        monkeypatch.setenv("VSCODE_BINARY_PATH", "/opt/code-server/bin/code-server")
        assert VSCodeConfig().binary_path == "/opt/code-server/bin/code-server"
