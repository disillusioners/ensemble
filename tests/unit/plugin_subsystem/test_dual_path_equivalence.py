"""Dual-path structural-validation equivalence.

The vendored ``manifest.schema.json`` is the single structural authority;
``jsonschema`` (primary) and the hand-rolled mini-validator (fallback) both
validate against it. This suite pins the two paths to identical verdicts
over a refusal battery so they cannot drift — the known sibling-drift
hazard (cf. the reasoning-echo test-contract lesson).
"""

from __future__ import annotations

import pytest

from daemon.plugin_subsystem import manifest_reader
from daemon.plugin_subsystem.manifest_reader import (
    _HAS_JSONSCHEMA,
    _structural_refusal,
    _validate_structural,
    _validate_structural_mini,
    _load_schema,
)
from tests.unit.plugin_subsystem._manifest_fixtures import (
    VALID_B_PATH_MANIFEST,
    VALID_MINIMAL_MANIFEST,
)

import yaml

pytestmark = pytest.mark.skipif(
    not _HAS_JSONSCHEMA, reason="jsonschema not importable in this venv; fallback path is the primary"
)

_BATTERY = {
    "valid": VALID_MINIMAL_MANIFEST,
    "unknown_top": VALID_MINIMAL_MANIFEST + "sneaky: 1\n",
    "unknown_nested": VALID_MINIMAL_MANIFEST.replace(
        '  execution_mode: "resource-only"\n', '  execution_mode: "resource-only"\n  extra: 1\n'
    ),
    "missing_required": VALID_MINIMAL_MANIFEST.replace('  license: "Apache-2.0"\n', ""),
    "wrong_type": VALID_MINIMAL_MANIFEST.replace('license: "Apache-2.0"', "license: 42"),
    "empty_name": VALID_MINIMAL_MANIFEST.replace('name: "{name}"', 'name: ""'),  # minLength: 1
    "enum_violation": VALID_MINIMAL_MANIFEST.replace('integration_path: "C"', 'integration_path: "Q"'),
    "array_item_type": VALID_MINIMAL_MANIFEST.replace('paths: ["data/", "presets/"]', 'paths: [3, 4]'),
    "divergence_extra_field": VALID_B_PATH_MANIFEST.replace(
        '      pinning_test: "tests/unit/plugin_subsystem/test_manifest_reader.py"',
        '      pinning_test: "tests/unit/plugin_subsystem/test_manifest_reader.py"\n      extra: 1',
    ),
}
# NOTE: pattern policy (kebab name) is deliberately NOT structural — it carries
# a dedicated semantic code owned by manifest_reader._check_semantics and is
# covered there. minLength IS structural (schema carries minLength: 1 on
# plugin.name and path items) and is pinned by the `empty_name` battery case.


def _doc(key: str) -> dict:
    doc = yaml.safe_load(_BATTERY[key].format(name="test-plugin"))
    return doc


@pytest.mark.parametrize("key", sorted(_BATTERY))
def test_paths_agree_on_refusal_battery(key):
    schema = _load_schema()
    doc = _doc(key)
    primary = [_structural_refusal(e) for e in _validate_structural(doc)]
    fallback = [_structural_refusal(e) for e in _validate_structural_mini(doc, schema)]
    got_primary = [(r.code, r.location) for r in primary]
    got_fallback = [(r.code, r.location) for r in fallback]
    if key == "valid":
        assert got_primary == [] and got_fallback == []
    else:
        assert got_primary, "primary path must refuse"
        assert got_fallback, "fallback path must refuse"
    assert got_primary == got_fallback, f"paths disagree on {key!r}: {got_primary} != {got_fallback}"


def test_primary_path_is_jsonschema_here():
    """Documents which path is live in THIS venv (jsonschema 4.x in uv.lock)."""
    assert manifest_reader.jsonschema is not None
