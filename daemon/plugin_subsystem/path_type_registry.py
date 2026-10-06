"""Path-type registry — data rows over ``plugins-convention/path_types.yaml``
(REC §1.2 component 4; CON §4 — THE extension point, frozen in v1).

A registry TABLE, not a loader: plain YAML data read + explicit validation +
lookup. No importlib, no entry-point scanning, no runtime loading of any
code (CON §7 no-runtime-loading sentinel). ``vendoring_check`` is a
registered check NAME (declarative); binding it to a function is a
later-slice concern and happens outside this module.

Refusal codes raised by this module (see also manifest_reader docstring):

- ``path_type_row_malformed`` — row is not a mapping / not a string-keyed row
- ``path_type_missing_required_field`` — one of the six MUST-provide fields
  (CON §4 a–f) absent
- ``path_type_field_malformed`` — present field of the wrong shape/type
- ``path_type_fence_without_evidence`` — ``fence: true`` row without
  ``fence_evidence_required: true`` (registry CI refusal, CON §4)
- ``path_type_fence_stripped`` — ``fence: false`` onto a v1-fenced path
  letter; carries the ALARM-style signal to the path-type registrar role
  (CON §4 refusal rules)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, FrozenSet, Mapping, Sequence, Tuple

import yaml

__all__ = [
    "PathTypeRegistry",
    "PathTypeRegistryError",
    "V1_FENCED_PATH_TYPES",
    "load_default_registry",
]

# Path-type convention root (repo-root sibling of the daemon package's repo).
_CONVENTION_DIR = Path(__file__).resolve().parents[2] / "plugins-convention"
_DEFAULT_REGISTRY_PATH = _CONVENTION_DIR / "path_types.yaml"

# The six MUST-provide fields per CON §4 list (a)–(f).
REQUIRED_ROW_FIELDS: Tuple[str, ...] = (
    "vendoring_check",
    "smoke_fixture",
    "allowed_execution_modes",
    "required_manifest_fields",
    "fence",
    "sync_default",
)

# v1-frozen fact: path letter A is the ENGINE-ONLY FENCED exception
# (CON §4 row A). Fence-removal from a letter in this set is a breaking,
# user-ratification-class change (CON §8) and is refused here with an
# alarm-style signal rather than silently accepted.
V1_FENCED_PATH_TYPES: FrozenSet[str] = frozenset({"A"})


class PathTypeRegistryError(Exception):
    """Typed refusal raised for malformed / policy-violating registry data."""

    def __init__(self, code: str, message: str, location: str = "path_types.yaml") -> None:
        self.code = code
        self.message = message
        self.location = location
        super().__init__(f"[{code}] {message} (at {location})")


@dataclass(frozen=True)
class PathTypeRow:
    """One validated path-type row (immutable data)."""

    path_letter: str
    display_name: str
    fence: bool
    allowed_execution_modes: Tuple[str, ...]
    required_manifest_fields: Tuple[str, ...]
    vendoring_check: str
    smoke_fixture: str
    sync_default: str
    classification: str
    fence_evidence_required: bool = False


class PathTypeRegistry:
    """Immutable lookup table over validated path-type rows."""

    def __init__(self, rows: Mapping[str, PathTypeRow]) -> None:
        self._rows: Dict[str, PathTypeRow] = dict(rows)

    # -- lookup ----------------------------------------------------------
    def get(self, path_letter: str) -> PathTypeRow:
        row = self._rows.get(path_letter)
        if row is None:
            raise KeyError(path_letter)
        return row

    def is_registered(self, path_letter: str) -> bool:
        return path_letter in self._rows

    def registered_paths(self) -> Tuple[str, ...]:
        return tuple(sorted(self._rows))

    def __contains__(self, path_letter: object) -> bool:
        return path_letter in self._rows

    def __len__(self) -> int:
        return len(self._rows)


def _validate_row(letter: str, raw: object, source: str) -> PathTypeRow:
    if not isinstance(raw, Mapping):
        raise PathTypeRegistryError(
            "path_type_row_malformed", f"path type {letter!r} must be a mapping", source
        )
    for field_name in REQUIRED_ROW_FIELDS:
        if field_name not in raw:
            raise PathTypeRegistryError(
                "path_type_missing_required_field",
                f"path type {letter!r} is missing required field {field_name!r}",
                source,
            )

    fence = raw["fence"]
    if not isinstance(fence, bool):
        raise PathTypeRegistryError(
            "path_type_field_malformed",
            f"path type {letter!r} field 'fence' must be a bool",
            source,
        )

    def _nonempty_str(field_name: str) -> str:
        value = raw.get(field_name)
        if not isinstance(value, str) or not value.strip():
            raise PathTypeRegistryError(
                "path_type_field_malformed",
                f"path type {letter!r} field {field_name!r} must be a non-empty string",
                source,
            )
        return value

    def _str_tuple(field_name: str, *, allow_empty: bool = False) -> Tuple[str, ...]:
        value = raw.get(field_name)
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise PathTypeRegistryError(
                "path_type_field_malformed",
                f"path type {letter!r} field {field_name!r} must be a list",
                source,
            )
        if (not allow_empty and not value) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise PathTypeRegistryError(
                "path_type_field_malformed",
                f"path type {letter!r} field {field_name!r} must be a non-empty list of non-empty strings",
                source,
            )
        return tuple(value)

    display_name = _nonempty_str("display_name")
    classification = _nonempty_str("classification")
    vendoring_check = _nonempty_str("vendoring_check")
    smoke_fixture = _nonempty_str("smoke_fixture")
    sync_default = _nonempty_str("sync_default")
    allowed_modes = _str_tuple("allowed_execution_modes")
    required_fields = _str_tuple("required_manifest_fields", allow_empty=True)

    fence_evidence_required = raw.get("fence_evidence_required", False)
    if not isinstance(fence_evidence_required, bool):
        raise PathTypeRegistryError(
            "path_type_field_malformed",
            f"path type {letter!r} field 'fence_evidence_required' must be a bool",
            source,
        )

    # Fence policy (CON §4 refusal rules).
    if fence and not fence_evidence_required:
        raise PathTypeRegistryError(
            "path_type_fence_without_evidence",
            f"path type {letter!r} declares fence:true without fence_evidence_required:true "
            "(registry CI refusal)",
            source,
        )
    if not fence and letter in V1_FENCED_PATH_TYPES:
        raise PathTypeRegistryError(
            "path_type_fence_stripped",
            "[ALARM: notify path-type registrar role] path type "
            f"{letter!r} is FENCED in v1 (engine-only exception); fence:false onto it is "
            "fence-stripping — a breaking, user-ratification-class change (registry CI refusal)",
            source,
        )

    return PathTypeRow(
        path_letter=letter,
        display_name=display_name,
        fence=fence,
        allowed_execution_modes=allowed_modes,
        required_manifest_fields=required_fields,
        vendoring_check=vendoring_check,
        smoke_fixture=smoke_fixture,
        sync_default=sync_default,
        classification=classification,
        fence_evidence_required=fence_evidence_required,
    )


def load_registry(path: Path = _DEFAULT_REGISTRY_PATH) -> PathTypeRegistry:
    """Read + validate a ``path_types.yaml`` into an immutable registry.

    Plain YAML data read — no caching, no code execution, no discovery.
    """
    path = Path(path)
    if not path.is_file():
        raise PathTypeRegistryError("registry_missing", f"registry file not found: {path}", str(path))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PathTypeRegistryError("registry_unparseable", f"invalid YAML: {exc}", str(path)) from exc
    if not isinstance(raw, Mapping) or not isinstance(raw.get("path_types"), Mapping):
        raise PathTypeRegistryError(
            "registry_malformed", "top-level 'path_types' mapping is required", str(path)
        )
    rows = {
        str(letter): _validate_row(str(letter), row, str(path))
        for letter, row in raw["path_types"].items()
    }
    if not rows:
        raise PathTypeRegistryError("registry_empty", "path_types must declare at least one row", str(path))
    return PathTypeRegistry(rows)


def load_default_registry() -> PathTypeRegistry:
    """Load the vendored ``plugins-convention/path_types.yaml``."""
    return load_registry(_DEFAULT_REGISTRY_PATH)
