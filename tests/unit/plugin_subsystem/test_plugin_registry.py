"""Plugin registry unit tests (REC §1.2 component 3; slice ②).

Exercises the boot-scan map: directory walking, manifest reader
delegation, lookup-by-name, refusal capture, and the CON §1 skeleton
invariants the slice-① reader does not own.

Carry-forward 6 (per slice ② dispatch): every fixture here uses a REAL
git-tag-shaped pin (``v0.23.0`` / ``v1.0.0`` / ``v2.5.0`` etc.). The
reserved-literal blocklist (``HEAD``/``main``/etc.) and range markers
live in the slice ① refusal tests, not here — we are testing the
registry, not re-testing the reader's range/sha discrimination.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daemon.plugin_subsystem import (
    DEFAULT_PLUGINS_ROOT,
    ManifestRefusal,
    PluginRegistry,
    SkeletonViolation,
    load_registry,
    scan_plugins_root,
)
from tests.unit.plugin_subsystem._manifest_fixtures import (
    VALID_B_PATH_MANIFEST,
    VALID_MINIMAL_MANIFEST,
    build_plugin,
)


# -- bare lookup / shape tests --------------------------------------------------


class TestPluginRegistryBasics:
    def test_empty_registry_constructs(self):
        reg = PluginRegistry({}, {})
        assert len(reg) == 0
        assert reg.names() == ()
        assert reg.refused_names() == ()
        assert reg.has_failures() is False

    def test_get_raises_for_unknown_name(self):
        reg = PluginRegistry({}, {})
        with pytest.raises(KeyError):
            reg.get("absent")

    def test_try_get_returns_none_for_unknown(self):
        reg = PluginRegistry({}, {})
        assert reg.try_get("absent") is None

    def test_contains(self):
        # Build a minimal registry by hand.
        from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

        decl = PluginDeclaration(
            name="a",
            license="MIT",
            upstream_repo="https://example.com/a.git",
            tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="C",
            execution_mode="resource-only",
            source_dir=None,
        )
        reg = PluginRegistry({"a": decl}, {})
        assert "a" in reg
        assert "b" not in reg

    def test_names_sorted(self):
        from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

        decl_a = PluginDeclaration(
            name="zeta", license="MIT", upstream_repo="x", tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="C", execution_mode="resource-only", source_dir=None,
        )
        decl_b = PluginDeclaration(
            name="alpha", license="MIT", upstream_repo="x", tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="C", execution_mode="resource-only", source_dir=None,
        )
        reg = PluginRegistry({"zeta": decl_a, "alpha": decl_b}, {})
        assert reg.names() == ("alpha", "zeta")

    def test_as_dict_round_trippable_via_json(self):
        from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

        decl = PluginDeclaration(
            name="rd", license="MIT", upstream_repo="x",
            tag_pin_per_class={"copy_freely": "v1.0.0"},
            integration_path="C", execution_mode="resource-only", source_dir=None,
        )
        reg = PluginRegistry({"rd": decl}, {})
        import json
        json.dumps(reg.as_dict())  # must not raise


# -- scan tests ---------------------------------------------------------------


class TestScanPluginsRoot:
    def test_scan_finds_valid_minimal_plugin(self, tmp_path: Path):
        # Real tag-shaped pin: v1.0.0 (carry-forward 6).
        plugin = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="valid-plugin")
        decls, refusals = scan_plugins_root(tmp_path)
        assert "valid-plugin" in decls
        assert decls["valid-plugin"].integration_path == "C"
        assert decls["valid-plugin"].execution_mode == "resource-only"
        assert "valid-plugin" not in refusals

    def test_scan_finds_valid_b_path_plugin(self, tmp_path: Path):
        # Real tag-shaped pin: v2.1.0 (carry-forward 6).
        plugin = build_plugin(tmp_path, VALID_B_PATH_MANIFEST, name="b-plugin")
        # B-path plugin needs an adapter/ subtree + divergence register (we
        # already have the latter in the fixture; we add a stub adapter to
        # make the tree-level check pass).
        (plugin / "adapter").mkdir()
        (plugin / "adapter" / "entry.ts").write_text("// stub\n", encoding="utf-8")
        decls, refusals = scan_plugins_root(tmp_path)
        assert "b-plugin" in decls
        assert "b-plugin" not in refusals

    def test_scan_records_refusal_for_missing_manifest(self, tmp_path: Path):
        (tmp_path / "no-manifest").mkdir()
        decls, refusals = scan_plugins_root(tmp_path)
        assert "no-manifest" not in decls
        assert "no-manifest" in refusals
        assert refusals["no-manifest"][0].code == "manifest_missing"

    def test_scan_records_refusal_for_invalid_manifest(self, tmp_path: Path):
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "MANIFEST.yaml").write_text("not: [valid", encoding="utf-8")
        decls, refusals = scan_plugins_root(tmp_path)
        assert "broken" not in decls
        assert "broken" in refusals
        assert refusals["broken"][0].code == "manifest_unparseable"

    def test_scan_skips_files_at_root(self, tmp_path: Path):
        # A loose file at the plugins root (e.g. a README) is not a plugin.
        (tmp_path / "README.md").write_text("hi", encoding="utf-8")
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="real")
        decls, refusals = scan_plugins_root(tmp_path)
        assert "README.md" not in decls
        assert "real" in decls

    def test_scan_missing_root_is_empty(self, tmp_path: Path):
        decls, refusals = scan_plugins_root(tmp_path / "does-not-exist")
        assert decls == {}
        assert refusals == {}

    def test_scan_mixed_valid_and_invalid(self, tmp_path: Path):
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="good")
        (tmp_path / "bad").mkdir()
        (tmp_path / "bad" / "MANIFEST.yaml").write_text(":", encoding="utf-8")
        decls, refusals = scan_plugins_root(tmp_path)
        assert "good" in decls
        assert "bad" in refusals


class TestLoadRegistry:
    def test_load_returns_typed_registry(self, tmp_path: Path):
        build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="r1")
        reg = load_registry(tmp_path)
        assert isinstance(reg, PluginRegistry)
        assert "r1" in reg
        assert reg.has_failures() is False

    def test_load_with_refusals(self, tmp_path: Path):
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "MANIFEST.yaml").write_text("not: [valid", encoding="utf-8")
        reg = load_registry(tmp_path)
        assert reg.has_failures() is True
        assert "broken" in reg.refused_names()

    def test_assert_skeleton_clean_raises_first_refusal(self, tmp_path: Path):
        (tmp_path / "broken").mkdir()
        (tmp_path / "broken" / "MANIFEST.yaml").write_text("not: [valid", encoding="utf-8")
        reg = load_registry(tmp_path)
        with pytest.raises(Exception) as ei:
            reg.assert_skeleton_clean()
        # The exception is the first refusal, which for a YAML parse error
        # is a ManifestRefusal with code manifest_unparseable.
        assert isinstance(ei.value, ManifestRefusal)

    def test_default_plugins_root_is_repo_plugins_dir(self):
        # The module-level default points at <repo-root>/plugins/. Slice ②
        # ships the first concrete plugin tree there; later slices can
        # override via an explicit ``plugins_root`` argument.
        assert DEFAULT_PLUGINS_ROOT.name == "plugins"
        assert DEFAULT_PLUGINS_ROOT.parent.name == "ensemble-src-wt-plugin-subsystem-03"


# -- skeleton convention tests ------------------------------------------------


class TestSkeletonConventions:
    def test_class_subdir_as_file_refused(self, tmp_path: Path):
        # copy_freely/ must be a directory if present, not a file.
        plugin = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="sk1")
        # Remove the auto-created copy_freely/ if any, then create a file
        # in its place.
        copy_freely = plugin / "copy_freely"
        if copy_freely.exists():
            import shutil
            shutil.rmtree(copy_freely)
        copy_freely.write_text("not a dir", encoding="utf-8")
        decls, refusals = scan_plugins_root(tmp_path)
        # Manifest is still valid; the class_subdir_not_a_directory
        # violation is recorded as a refusal.
        codes = [r.code for r in refusals.get("sk1", ())]
        assert "class_subdir_not_a_directory" in codes

    def test_root_symlink_refused(self, tmp_path: Path):
        plugin = build_plugin(tmp_path, VALID_MINIMAL_MANIFEST, name="sk2")
        # Create a symlink at the plugin root (e.g. a wrong "current" alias).
        try:
            (plugin / "current").symlink_to(plugin / "MANIFEST.yaml")
        except OSError:  # pragma: no cover - filesystem may forbid
            pytest.skip("symlink not supported on this filesystem")
        decls, refusals = scan_plugins_root(tmp_path)
        codes = [r.code for r in refusals.get("sk2", ())]
        assert "root_symlink" in codes

    def test_own_outright_directory_ok(self, tmp_path: Path):
        # A plugin with own_outright/ as a real directory is fine.
        plugin = build_plugin(
            tmp_path,
            VALID_MINIMAL_MANIFEST.replace(
                "copy_freely:\n  paths: [",
                "own_outright:\n  paths: ['compose_brief/']\ncopy_freely:\n  paths: [",
            ),
            name="sk3",
        )
        (plugin / "own_outright" / "compose_brief").mkdir(parents=True, exist_ok=True)
        (plugin / "own_outright" / "compose_brief" / "README.md").write_text("# local\n", encoding="utf-8")
        decls, refusals = scan_plugins_root(tmp_path)
        # No skeleton violation; manifest is valid (we mutated it carefully).
        codes = [r.code for r in refusals.get("sk3", ())]
        assert codes == []


# -- refusal-surface normalization --------------------------------------------


class TestRefusalNormalization:
    def test_manifest_refusal_serializes(self):
        from daemon.plugin_subsystem import load_registry
        (tmp_path := __import__("pathlib").Path("/tmp/_rfd"))  # noqa: F841
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "broken").mkdir()
            (tmp / "broken" / "MANIFEST.yaml").write_text("not: [valid", encoding="utf-8")
            reg = load_registry(tmp)
            import json
            payload = json.dumps(reg.as_dict())
        # If we reach here, the refusal surface is JSON-serializable.
        assert "manifest_unparseable" in payload
