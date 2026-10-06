"""Manifest reader tests — slice ① coverage items 1–5.

Item 1: valid minimal manifest passes and yields a correct PluginDeclaration.
Item 2: EVERY refusal case in the validation matrix, asserted on its own
        refusal code path (one test per refusal).
Item 3: extension rule — additive 1.0.1 accepted; breaking versions refused.
Item 4: 64 KB manifest size cap.
Item 5: license validation (valid SPDX passes; invalid refused).
"""

from __future__ import annotations

import pytest

from daemon.plugin_subsystem.manifest_reader import (
    MANIFEST_SIZE_CAP_BYTES,
    read_manifest,
    validate_manifest,
)
from tests.unit.plugin_subsystem._manifest_fixtures import (
    VALID_A_PATH_MANIFEST,
    VALID_B_PATH_MANIFEST,
    VALID_MINIMAL_MANIFEST,
    build_plugin,
)

# ─── Item 1: valid manifests pass ─────────────────────────────────────────────


class TestValidManifests:
    def test_valid_minimal_manifest_passes(self, valid_minimal_plugin):
        result = validate_manifest(valid_minimal_plugin)
        assert result.ok, result.refusal
        assert result.refusal is None

    def test_valid_minimal_yields_correct_declaration(self, valid_minimal_plugin):
        declaration = read_manifest(valid_minimal_plugin)
        assert declaration.name == "test-plugin"
        assert declaration.license == "Apache-2.0"
        assert declaration.upstream_repo == "https://example.com/upstream.git"
        assert declaration.integration_path == "C"
        assert declaration.execution_mode == "resource-only"
        assert declaration.schema_version == "1.0.0"
        assert declaration.source_dir == valid_minimal_plugin

    def test_declaration_pin_inheritance(self, valid_minimal_plugin):
        declaration = read_manifest(valid_minimal_plugin)
        assert declaration.pin_for_class("copy_freely") == "v1.0.0"
        # snapshot_with_drift_alarm pin absent ⇒ inherits copy_freely pin (CON §2)
        assert declaration.pin_for_class("snapshot_with_drift_alarm") == "v1.0.0"
        assert declaration.pin_for_class("own_outright") is None

    def test_valid_b_path_with_adapter_tree_passes(self, tmp_path):
        plugin_dir = build_plugin(
            tmp_path,
            VALID_B_PATH_MANIFEST,
            name="b-plugin",
            files={"adapter/entry.ts": "export function generateDesign() {}"},
        )
        result = validate_manifest(plugin_dir, validate_tree=True)
        assert result.ok, result.refusal
        declaration = result.declaration
        assert declaration.lifted_symbol == "generateDesign"
        assert declaration.entrypoint == "adapter/entry.ts"
        assert declaration.ipc_version == 1
        assert declaration.needs_adapter is True

    def test_valid_a_path_with_fence_passes(self, tmp_path):
        plugin_dir = build_plugin(
            tmp_path, VALID_A_PATH_MANIFEST, name="a-plugin", files={"adapter/main.ts": "export {}"}
        )
        result = validate_manifest(plugin_dir, validate_tree=True)
        assert result.ok, result.refusal
        declaration = result.declaration
        assert declaration.fence_grant["granted_by"] == "user-confirmation-2026-10-06"
        assert declaration.hosted_runtime_deps["runtime_pin"] == "20.11.1"
        assert declaration.own_outright["paths"] == ["compose_brief/"]

    def test_manifest_only_validation_does_not_require_adapter(self, tmp_path):
        """Tree-level adapter/ check fires ONLY when validate_tree=True."""
        plugin_dir = build_plugin(tmp_path, VALID_B_PATH_MANIFEST, name="b-plugin")  # no adapter/
        result = validate_manifest(plugin_dir, validate_tree=False)
        assert result.ok, result.refusal

    def test_raising_read_manifest_returns_declaration(self, valid_minimal_plugin):
        declaration = read_manifest(valid_minimal_plugin)
        assert isinstance(declaration.name, str)


# ─── Item 2: refusal matrix — one test per refusal code path ─────────────────


class TestFileLevelRefusals:
    def test_manifest_missing(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, None, name="no-manifest")
        result = validate_manifest(plugin_dir)
        assert not result.ok
        assert result.refusal.code == "manifest_missing"

    def test_manifest_unparseable_yaml(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, None, name="bad-yaml")
        (plugin_dir / "MANIFEST.yaml").write_text("schema_version: [unclosed\n  - broken", encoding="utf-8")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "manifest_unparseable"

    def test_manifest_unparseable_non_mapping_root(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, None, name="list-root")
        (plugin_dir / "MANIFEST.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "manifest_unparseable"

    def test_manifest_too_large(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="huge-plugin")
        pad = "# " + "x" * (MANIFEST_SIZE_CAP_BYTES) + "\n"  # > 64 KB of comment
        with open(plugin_dir / "MANIFEST.yaml", "a", encoding="utf-8") as fh:
            fh.write(pad)
        assert (plugin_dir / "MANIFEST.yaml").stat().st_size > MANIFEST_SIZE_CAP_BYTES
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "manifest_too_large"

    def test_manifest_at_cap_passes(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="cap-plugin")
        current = (plugin_dir / "MANIFEST.yaml").stat().st_size
        with open(plugin_dir / "MANIFEST.yaml", "a", encoding="utf-8") as fh:
            fh.write("# " + "x" * (MANIFEST_SIZE_CAP_BYTES - current - 3) + "\n")
        assert (plugin_dir / "MANIFEST.yaml").stat().st_size <= MANIFEST_SIZE_CAP_BYTES
        result = validate_manifest(plugin_dir)
        assert result.ok, result.refusal


class TestSchemaVersionRefusals:
    def test_schema_version_missing(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('schema_version: "1.0.0"\n', "")
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "schema_version_missing"

    def test_schema_version_breaking_major_refused(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"2.0.0"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "schema_version_unsupported"

    def test_schema_version_uncarried_minor_refused(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"1.1.0"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "schema_version_unsupported"

    def test_schema_version_non_semver_refused(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"one-zero-zero"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "schema_version_unsupported"


class TestStructuralRefusals:
    def test_unknown_field_top_level(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST + "sneaky_extra: true\n"
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "unknown_field"
        assert "sneaky_extra" in result.refusal.message or result.refusal.location.endswith("sneaky_extra")

    def test_unknown_field_nested_in_plugin(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace(
            '  execution_mode: "resource-only"\n',
            '  execution_mode: "resource-only"\n  sneaky_nested: 1\n',
        )
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "unknown_field"
        assert "sneaky_nested" in result.refusal.message or "sneaky_nested" in result.refusal.location

    def test_unknown_field_in_class_section(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace(
            '  alarm_owner: "worker"\n',
            '  alarm_owner: "worker"\n  alarm_owner_extra: "nope"\n',
        )
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "unknown_field"

    def test_required_field_missing(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('  license: "Apache-2.0"\n', "")
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "required_field_missing"
        assert "license" in result.refusal.message or "license" in result.refusal.location

    def test_type_mismatch_wrong_type(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace("license: \"Apache-2.0\"", "license: [\"Apache-2.0\"]")
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "type_mismatch"

    def test_type_mismatch_enum_violation_on_integration_path(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('integration_path: "C"', 'integration_path: "Ω"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "type_mismatch"

    def test_name_invalid_not_kebab(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('name: "{name}"', 'name: "Not_Kebab!"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "name_invalid"

    def test_name_dir_mismatch(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.format(name="test-plugin")  # manifest says test-plugin…
        plugin_dir = build_plugin(tmp_path, text, name="other-dir-name")  # …dir says otherwise
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "name_dir_mismatch"


class TestPluginBlockRefusals:
    def test_license_invalid(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('license: "Apache-2.0"', 'license: "Not-A-Real-License-1.0"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "license_invalid"
        assert result.refusal.location == "plugin.license"

    def test_license_spdx_expression_refused(self, tmp_path):
        """Validator v1 is exact-match; OR-expressions are out of scope."""
        text = VALID_MINIMAL_MANIFEST.replace(
            'license: "Apache-2.0"', 'license: "(MIT OR Apache-2.0)"'
        )
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "license_invalid"

    def test_unregistered_integration_path(self, tmp_path, default_registry):
        """The schema enum (B|C|A) gates letter SHAPE structurally; the registry
        is the AUTHORITY for registration — proven here with a registry that
        dropped a row (the CON §4 cross-check: manifest's integration_path
        against registry keys). Also the path a future enum extension (D/E/F)
        rides before its registry row lands."""
        from daemon.plugin_subsystem.path_type_registry import PathTypeRegistry

        rows = {letter: default_registry.get(letter) for letter in ("A", "B")}  # C dropped
        trimmed = PathTypeRegistry(rows)
        plugin_dir = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="test-plugin")
        result = validate_manifest(plugin_dir, registry=trimmed)
        assert result.refusal.code == "unregistered_integration_path"
        assert "C" in result.refusal.message

    def test_absent_execution_mode(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('  execution_mode: "resource-only"\n', "")
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "absent_execution_mode"

    def test_execution_mode_not_allowed(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace(
            '  execution_mode: "resource-only"', '  execution_mode: "lifted-symbol"'
        )
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "execution_mode_not_allowed"

    def test_missing_required_manifest_fields_b_path(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace('  lifted_symbol: "generateDesign"\n', "")
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "missing_required_manifest_fields"
        assert "lifted_symbol" in result.refusal.message

    def test_missing_required_manifest_fields_a_path(self, tmp_path):
        block = '''  hosted_runtime_deps:
    runtime_pin: "20.11.1"
    profile_id: "sdk-minimal"
    ipc_schema_pin: "1.0.0"
    capability_allowlist: ["od.generate"]
'''
        text = VALID_A_PATH_MANIFEST.replace(block, "")  # field ABSENT, not renamed
        plugin_dir = build_plugin(tmp_path, text, name="a-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "missing_required_manifest_fields"
        assert "hosted_runtime_deps" in result.refusal.message

    def test_fence_missing_grant_absent(self, tmp_path):
        block = '''  fence_grant:
    rationale: "engine-only exception ratified for the dsh-style giant"
    granted_by: "user-confirmation-2026-10-06"
    granted_at: "2026-10-06T00:00:00Z"
'''
        text = VALID_A_PATH_MANIFEST.replace(block, "")  # field ABSENT, not renamed
        plugin_dir = build_plugin(tmp_path, text, name="a-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "missing_required_manifest_fields"  # row-required-field absence
        assert "fence_grant" in result.refusal.message

    def test_fence_missing_grant_incomplete(self, tmp_path):
        text = VALID_A_PATH_MANIFEST.replace(
            '    granted_at: "2026-10-06T00:00:00Z"\n', ""
        )
        plugin_dir = build_plugin(tmp_path, text, name="a-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "fence_missing"
        assert "granted_at" in result.refusal.message

    def test_runtime_pin_not_exact(self, tmp_path):
        text = VALID_A_PATH_MANIFEST.replace('runtime_pin: "20.11.1"', 'runtime_pin: "^20.11.1"')
        plugin_dir = build_plugin(tmp_path, text, name="a-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "runtime_pin_not_exact"

    def test_non_tag_pin_sha_refused(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('copy_freely: "v1.0.0"', 'copy_freely: "a1b2c3d4e5f6a7b8"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "non_tag_pin"

    def test_non_tag_pin_empty_refused(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('copy_freely: "v1.0.0"', 'copy_freely: ""')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "non_tag_pin"

    def test_tag_pin_with_v_prefix_accepted(self, valid_minimal_plugin):
        result = validate_manifest(valid_minimal_plugin)
        assert result.ok, result.refusal  # v1.0.0 is a tag-shaped pin


class TestProvenanceClassRefusals:
    def test_missing_class_section(self, tmp_path):
        lines = VALID_MINIMAL_MANIFEST.splitlines(keepends=True)
        kept = [ln for ln in lines if not ln.startswith("copy_freely:") and not ln.startswith("  paths: [")
                and not ln.startswith('  alarm_owner: "worker"') and not ln.startswith("  escalation:")]
        text = "".join(kept)
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "missing_class_section"

    def test_alarm_owner_missing_empty_string(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('alarm_owner: "worker"', 'alarm_owner: ""')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "alarm_owner_missing"

    def test_alarm_owner_missing_absent(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('  alarm_owner: "worker"\n', "")
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "alarm_owner_missing"

    def test_alarm_owner_missing_on_snapshot(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace('  alarm_owner: "designer-owners"\n', "")
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "alarm_owner_missing"

    def test_own_outright_requires_no_alarm_owner(self, tmp_path):
        """own_outright carries NO alarm_owner — its absence must NOT refuse."""
        result = validate_manifest(build_plugin(tmp_path, VALID_A_PATH_MANIFEST, name="a-plugin"))
        assert result.ok, result.refusal

    def test_own_outright_with_alarm_owner_refused(self, tmp_path):
        """own_outright + alarm_owner is outside the frozen vocabulary (schema refuses)."""
        text = VALID_A_PATH_MANIFEST.replace(
            '  paths: ["compose_brief/"]',
            '  paths: ["compose_brief/"]\n  alarm_owner: "someone"',
        )
        plugin_dir = build_plugin(tmp_path, text, name="a-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "unknown_field"

    def test_empty_divergence_register_on_non_empty_paths(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace(
            "  divergence_register:\n    - id: 1", "  divergence_register: []\n    - id: 1"
        )
        # cleaner: replace the whole register block
        text = VALID_B_PATH_MANIFEST.split("  divergence_register:")[0]
        text += "  divergence_register: []\nparity_boundary:\n  intentionally_not_vendored: []\n  not_executed: []\n"
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "empty_divergence_register"

    def test_divergence_entry_malformed_missing_pinning_test(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace(
            '      pinning_test: "tests/unit/plugin_subsystem/test_manifest_reader.py"\n', ""
        )
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "divergence_entry_malformed"

    def test_empty_paths_snapshot_with_valid_register_passes(self, tmp_path):
        """Empty snapshot paths tolerate a register (register required only on
        non-empty paths); also verifies divergence entries carry optional upstream_ref."""
        text = VALID_B_PATH_MANIFEST.replace('paths: ["prompts/"]', "paths: []")
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.ok, result.refusal


class TestParityRefusals:
    def test_missing_parity_boundary(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.split("parity_boundary:")[0]
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "missing_parity_boundary"

    def test_parity_row_malformed_missing_reason(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace(
            '      reason: "UI-like surface excluded from backend-mostly plugin population"', ""
        )
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "parity_row_malformed"

    def test_parity_subsections_may_be_empty(self, tmp_path):
        """Section required; subsections may be empty lists (VALID_B has both empty)."""
        plugin_dir = build_plugin(tmp_path, VALID_B_PATH_MANIFEST, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.ok, result.refusal


class TestEntrypointRefusals:
    def test_entrypoint_invalid_absolute(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace(
            'entrypoint: "adapter/entry.ts"', 'entrypoint: "/abs/adapter/entry.ts"'
        )
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "entrypoint_invalid"

    def test_entrypoint_invalid_escape(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace(
            'entrypoint: "adapter/entry.ts"', 'entrypoint: "src/entry.ts"'
        )
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "entrypoint_invalid"

    def test_entrypoint_invalid_traversal(self, tmp_path):
        text = VALID_B_PATH_MANIFEST.replace(
            'entrypoint: "adapter/entry.ts"', 'entrypoint: "adapter/../secrets/entry.ts"'
        )
        plugin_dir = build_plugin(tmp_path, text, name="b-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "entrypoint_invalid"


class TestTreeLevelChecks:
    def test_adapter_missing_at_vendoring(self, tmp_path):
        plugin_dir = build_plugin(tmp_path, VALID_B_PATH_MANIFEST, name="b-plugin")  # no adapter/
        result = validate_manifest(plugin_dir, validate_tree=True)
        assert result.refusal.code == "adapter_missing"

    def test_resource_only_tree_needs_no_adapter(self, tmp_path):
        plugin_dir = build_plugin(
            tmp_path, VALID_MINIMAL_MANIFEST, name="test-plugin", files={"data/systems.json": "{}"}
        )
        result = validate_manifest(plugin_dir, validate_tree=True)
        assert result.ok, result.refusal

    def test_copy_freely_symlink_refused(self, tmp_path):
        plugin_dir = build_plugin(
            tmp_path,
            VALID_MINIMAL_MANIFEST,
            name="test-plugin",
            files={"data/keep.txt": "x"},
            symlink_inside_copy_freely="data/link.yaml",
        )
        result = validate_manifest(plugin_dir, validate_tree=True)
        assert result.refusal.code == "copy_freely_symlink"

    def test_tree_checks_skipped_without_validate_tree(self, tmp_path):
        plugin_dir = build_plugin(
            tmp_path,
            VALID_MINIMAL_MANIFEST,
            name="test-plugin",
            symlink_inside_copy_freely="data/link.yaml",
        )
        result = validate_manifest(plugin_dir, validate_tree=False)
        assert result.ok, result.refusal


# ─── Item 3: extension rule ───────────────────────────────────────────────────


class TestExtensionRule:
    def test_additive_1_0_1_accepted(self, tmp_path):
        """The additive 1.0.x family is accepted: a 1.0.1-declared manifest whose
        content conforms to the frozen 1.0.0 vocabulary passes (additive-only
        means no new REQUIRED fields, so 1.0.1 documents stay 1.0.0-conformant)."""
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"1.0.1"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.ok, result.refusal
        assert result.declaration.schema_version == "1.0.1"

    def test_additive_1_0_x_patch_accepted(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"1.0.7"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        assert validate_manifest(plugin_dir).ok

    def test_breaking_major_refused(self, tmp_path):
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"2.0.0"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        assert validate_manifest(plugin_dir).refusal.code == "schema_version_unsupported"

    def test_unknown_field_refused_regardless_of_version(self, tmp_path):
        """Vocabulary growth is NOT something a manifest can declare into —
        the schema is frozen; an unknown field refuses even at 1.0.1."""
        text = VALID_MINIMAL_MANIFEST.replace('"1.0.0"', '"1.0.1"') + "brand_new_field: 1\n"
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        assert validate_manifest(plugin_dir).refusal.code == "unknown_field"


# ─── Item 5: license validation ───────────────────────────────────────────────


class TestLicenseValidation:
    @pytest.mark.parametrize("spdx_id", ["MIT", "Apache-2.0", "BSD-3-Clause", "ISC", "MPL-2.0", "CC0-1.0"])
    def test_valid_spdx_ids_pass(self, tmp_path, spdx_id):
        text = VALID_MINIMAL_MANIFEST.replace('license: "Apache-2.0"', f'license: "{spdx_id}"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.ok, result.refusal

    @pytest.mark.parametrize("bad", ["Apache 2.0", "apache-2.0", "Mit", "GPL-2.0", "Custom-Proprietary"])
    def test_invalid_license_refused(self, tmp_path, bad):
        text = VALID_MINIMAL_MANIFEST.replace('license: "Apache-2.0"', f'license: "{bad}"')
        plugin_dir = build_plugin(tmp_path, text, name="test-plugin")
        result = validate_manifest(plugin_dir)
        assert result.refusal.code == "license_invalid"
