"""Slice ② — real-plugin-tree manifest validation test.

Executes the slice-① manifest reader + plugin registry against the
SHIPPED ``plugins/opendesign/`` tree (not a tmp_path fixture) and
asserts the validation passes end-to-end.  This is the "manifest
validates via the slice-① reader" deliverable from the slice ②
dispatch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.plugin_subsystem import (
    ManifestRefusal,
    PluginRegistry,
    load_registry,
    read_manifest,
    scan_plugins_root,
    validate_manifest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "opendesign"


@pytest.mark.skipif(not PLUGIN_ROOT.is_dir(), reason="vendored plugin not present")
class TestRealPluginManifest:
    def test_manifest_file_present(self):
        assert (PLUGIN_ROOT / "MANIFEST.yaml").is_file()

    def test_validate_manifest_ok(self):
        result = validate_manifest(PLUGIN_ROOT, validate_tree=True)
        assert result.ok is True, f"manifest refused: {result.refusal}"
        assert result.declaration is not None
        assert result.declaration.name == "opendesign"
        assert result.declaration.license == "Apache-2.0"
        assert result.declaration.integration_path == "C"
        assert result.declaration.execution_mode == "resource-only"
        # Pin: the actual upstream tag name is `open-design-v0.23.0`.
        assert result.declaration.tag_pin_per_class == {"copy_freely": "open-design-v0.23.0"}

    def test_read_manifest_returns_declaration(self):
        declaration = read_manifest(PLUGIN_ROOT, validate_tree=True)
        assert declaration.name == "opendesign"
        # Parity boundary present.
        assert len(declaration.parity_intentionally_not_vendored) >= 1
        assert len(declaration.parity_not_executed) >= 1
        # copy_freely paths include the four vendored subtrees.
        cf_paths = declaration.copy_freely.get("paths", [])
        for required in (
            "copy_freely/design-systems/",
            "copy_freely/design-templates/",
            "copy_freely/craft/",
            "copy_freely/prompt-templates/",
        ):
            assert required in cf_paths, f"missing copy_freely path: {required}"

    def test_plugin_registry_loads(self):
        # Single-plugin tree: registry should contain "opendesign" with no refusals.
        declarations, _skills, refusals = scan_plugins_root(PLUGIN_ROOT.parent)
        assert "opendesign" in declarations
        assert "opendesign" not in refusals

    def test_plugin_registry_load_registry_method(self):
        reg = load_registry(PLUGIN_ROOT.parent)
        assert isinstance(reg, PluginRegistry)
        assert "opendesign" in reg
        assert not reg.has_failures()

    def test_manifest_under_size_cap(self):
        size = (PLUGIN_ROOT / "MANIFEST.yaml").stat().st_size
        # CON §1: 64 KB hard-cap.
        assert size < 64 * 1024, f"MANIFEST.yaml is {size} bytes (cap 64 KB)"

    def test_manifest_is_json_serializable(self):
        from daemon.plugin_subsystem import validate_plugin_dir
        report = validate_plugin_dir(PLUGIN_ROOT)
        json.dumps(report)  # must not raise
        assert report["ok"] is True
        assert report["plugin"] == "opendesign"
        assert report["declaration"]["license"] == "Apache-2.0"
        assert report["declaration"]["integration_path"] == "C"
