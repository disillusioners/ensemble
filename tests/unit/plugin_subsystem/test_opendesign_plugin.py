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
        # Additive epoch (review council adjudication council-od-slice4-
        # 20261006): the shipped manifest MUST declare 1.0.1 — slice ③
        # added snapshot_with_drift_alarm.upstream_paths and CON §8 says
        # the first additive change is 1.0.1, never v2.  A silent
        # regression to "1.0.0" fails here.
        assert result.declaration.schema_version == "1.0.1"
        # Pins: slice ③ added the snapshot_with_drift_alarm layer
        # (REC §4.1 row 2); the per-class pin for snapshot inherits
        # copy_freely's pin by default (CON §2 line 47) and is
        # declared explicitly here so the slice ③ tag-diff has a
        # concrete anchor.
        assert result.declaration.tag_pin_per_class == {
            "copy_freely": "open-design-v0.23.0",
            "snapshot_with_drift_alarm": "open-design-v0.23.0",
        }
        # 3-class provenance layout (slice ③; REC §4.1):
        #   copy_freely — data layer (slice ②, unchanged)
        #   snapshot_with_drift_alarm — prompt code (slice ③ NEW)
        #   own_outright — Turn-3 + lint/parse5 ports (slice ③
        #     DECLARED-NOT-AUTHORED; ⑤ authors content)
        snap = result.declaration.snapshot_with_drift_alarm
        assert "paths" in snap and len(snap["paths"]) >= 1, (
            f"snapshot_with_drift_alarm.paths must be non-empty (CON §2); got {snap}"
        )
        assert snap.get("alarm_owner"), "snapshot_with_drift_alarm.alarm_owner is required (CON §2)"
        # Divergence register is REQUIRED from day one (CON §2: non-empty
        # register on non-empty paths); 4 SEEDED entries per slice ③
        # minimal-faithful reading.
        register = result.declaration.divergence_register
        assert len(register) >= 1, "divergence_register must be non-empty on non-empty snapshot paths"
        for entry in register:
            for f in ("id", "files", "delta", "rationale", "pinning_test"):
                assert f in entry, f"divergence entry missing {f!r}: {entry}"
        own = result.declaration.own_outright
        assert "paths" in own, "own_outright.paths declared at slice ③"

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
        # Cross-lane coupling: use arity-safe positional access (result[0]
        # is always declarations; result[-1] is always refusals on both
        # ②'s 2-tuple and ④'s 3-tuple; result[1] would mean different
        # things on each side).  Per the slice ③ cross-lane steer.
        scan_result = scan_plugins_root(PLUGIN_ROOT.parent)
        declarations = scan_result[0]
        refusals = scan_result[-1]
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
