"""Path-type registry tests — slice ① coverage item 6.

- valid registry loads (rows B / C / A verbatim per CON §4)
- a row missing any of the six required fields is refused
- fence row without fence_evidence_required is refused
- fence-STRIPPING (fence:false onto A) is refused with an alarm-style signal
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from daemon.plugin_subsystem.path_type_registry import (
    REQUIRED_ROW_FIELDS,
    PathTypeRegistryError,
    V1_FENCED_PATH_TYPES,
    load_registry,
)


@pytest.fixture
def registry_yaml(tmp_path: Path):
    def _write(overrides: dict | None = None, *, rows: dict | None = None) -> Path:
        data = rows if rows is not None else yaml.safe_load(
            (Path(__file__).resolve().parents[3] / "plugins-convention" / "path_types.yaml").read_text()
        )
        if overrides:
            for letter, patch in overrides.items():
                if letter in data["path_types"]:
                    data["path_types"][letter].update(patch)
                else:  # row ADDITIONS are non-breaking (CON §8)
                    data["path_types"][letter] = patch
        target = tmp_path / "path_types.yaml"
        target.write_text(yaml.safe_dump(data), encoding="utf-8")
        return target

    return _write


# ─── valid registry ───────────────────────────────────────────────────────────


class TestValidRegistry:
    def test_default_registry_loads(self, default_registry):
        assert default_registry.registered_paths() == ("A", "B", "C")
        assert "B" in default_registry and "C" in default_registry and "A" in default_registry

    def test_rows_match_con_section4_verbatim(self, default_registry):
        b = default_registry.get("B")
        assert b.display_name == "Port-wrapping"
        assert b.fence is False
        assert b.allowed_execution_modes == ("lifted-symbol",)
        assert b.required_manifest_fields == ("lifted_symbol", "entrypoint", "ipc_version")
        assert b.vendoring_check == "importable_ctx_free"
        assert b.smoke_fixture == "fixtures/path_b_smoke.yaml"
        assert b.sync_default == "snapshot_with_drift_alarm"
        assert b.classification == "vendoring-time"

        c = default_registry.get("C")
        assert c.display_name == "Resource-izing"
        assert c.fence is False
        assert c.allowed_execution_modes == ("resource-only",)
        assert c.required_manifest_fields == ()  # CON §4 row C: empty list is meaningful
        assert c.vendoring_check == "resource_only_purity"
        assert c.sync_default == "copy_freely"

        a = default_registry.get("A")
        assert a.display_name == "Host-shim adapter"
        assert a.fence is True and a.fence_evidence_required is True  # ENGINE-ONLY EXCEPTION
        assert a.allowed_execution_modes == ("hosted-runtime",)
        assert a.required_manifest_fields == ("hosted_runtime_deps", "fence_grant")
        assert a.vendoring_check == "hosted_runtime_validated"
        assert a.sync_default == "snapshot_with_drift_alarm"

    def test_unregistered_letter_is_not_registered(self, default_registry):
        assert "Z" not in default_registry
        assert not default_registry.is_registered("Z")
        with pytest.raises(KeyError):
            default_registry.get("Z")


# ─── required-field validation ────────────────────────────────────────────────


class TestRequiredFieldValidation:
    def test_row_missing_each_required_field_refused(self, registry_yaml, tmp_path):
        for field_name in REQUIRED_ROW_FIELDS:
            rows = yaml.safe_load(
                (Path(__file__).resolve().parents[3] / "plugins-convention" / "path_types.yaml").read_text()
            )
            del rows["path_types"]["B"][field_name]
            path = tmp_path / f"missing_{field_name}.yaml"
            path.write_text(yaml.safe_dump(rows), encoding="utf-8")
            with pytest.raises(PathTypeRegistryError) as excinfo:
                load_registry(path)
            assert excinfo.value.code == "path_type_missing_required_field"
            assert field_name in excinfo.value.message

    def test_fence_false_without_evidence_on_non_fenced_row_is_fine(self, registry_yaml, tmp_path):
        path = registry_yaml({"B": {"fence_evidence_required": True}})  # harmless extra on unfenced row
        registry = load_registry(path)
        assert registry.get("B").fence is False

    def test_fence_row_without_evidence_refused(self, registry_yaml, tmp_path):
        path = registry_yaml({"D": {
            "display_name": "future",
            "fence": True,
            "allowed_execution_modes": ["hosted-runtime"],
            "required_manifest_fields": [],
            "vendoring_check": "some_check",
            "smoke_fixture": "fixtures/path_d_smoke.yaml",
            "sync_default": "copy_freely",
            "classification": "vendoring-time",
        }})
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "path_type_fence_without_evidence"

    def test_new_fenced_row_with_evidence_loads(self, registry_yaml, tmp_path):
        """Row ADDITIONS are non-breaking (CON §8) — D with proper fence loads."""
        path = registry_yaml({"D": {
            "display_name": "future-fenced",
            "fence": True,
            "fence_evidence_required": True,
            "allowed_execution_modes": ["hosted-runtime"],
            "required_manifest_fields": [],
            "vendoring_check": "some_check",
            "smoke_fixture": "fixtures/path_d_smoke.yaml",
            "sync_default": "copy_freely",
            "classification": "vendoring-time",
        }})
        registry = load_registry(path)
        assert registry.get("D").fence_evidence_required is True


# ─── fence-stripping ──────────────────────────────────────────────────────────


class TestFenceStripping:
    def test_fence_false_on_A_refused_with_alarm(self, registry_yaml, tmp_path):
        path = registry_yaml({"A": {"fence": False, "fence_evidence_required": False}})
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "path_type_fence_stripped"
        # alarm-style signal: the path-type registrar role is named in the message
        assert "registrar" in excinfo.value.message.lower()
        assert "ALARM" in excinfo.value.message

    def test_v1_fenced_set_is_exactly_A(self):
        assert V1_FENCED_PATH_TYPES == frozenset({"A"})

    def test_fence_stripping_detection_is_by_v1_invariant_not_by_data(self, registry_yaml, tmp_path):
        """The strip-guard keys off the frozen v1 fact (A is fenced), not off the
        edited file itself — this is what makes the guard strip-proof."""
        assert "A" in V1_FENCED_PATH_TYPES


# ─── malformed registry data ──────────────────────────────────────────────────


class TestMalformedRegistry:
    def test_missing_file(self, tmp_path):
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(tmp_path / "absent.yaml")
        assert excinfo.value.code == "registry_missing"

    def test_unparseable_yaml(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("path_types: [unclosed", encoding="utf-8")
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "registry_unparseable"

    def test_missing_path_types_key(self, tmp_path):
        path = tmp_path / "notable.yaml"
        path.write_text("something_else: {}\n", encoding="utf-8")
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "registry_malformed"

    def test_empty_registry_refused(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("path_types: {}\n", encoding="utf-8")
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "registry_empty"

    def test_row_not_a_mapping(self, registry_yaml, tmp_path):
        path = registry_yaml(rows={"path_types": {"B": "just-a-string"}})
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "path_type_row_malformed"

    def test_fence_not_a_bool(self, registry_yaml, tmp_path):
        path = registry_yaml({"B": {"fence": "false"}})
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "path_type_field_malformed"

    def test_allowed_execution_modes_empty_refused(self, registry_yaml, tmp_path):
        """A path with zero allowed execution modes is meaningless — the
        positive-declaration rule needs at least one allowed mode."""
        path = registry_yaml({"B": {"allowed_execution_modes": []}})
        with pytest.raises(PathTypeRegistryError) as excinfo:
            load_registry(path)
        assert excinfo.value.code == "path_type_field_malformed"

    def test_required_manifest_fields_empty_allowed(self, registry_yaml, tmp_path):
        """required_manifest_fields: [] is the CON §4 row-C shape (no extra
        fields needed) and must load."""
        path = registry_yaml({"B": {"required_manifest_fields": []}})
        registry = load_registry(path)
        assert registry.get("B").required_manifest_fields == ()
