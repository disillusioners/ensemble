"""Manifest reader — parse + validate ``MANIFEST.yaml`` (REC §1.2 component 2).

Bounded validation of one plugin manifest against the frozen v1 vocabulary
(``plugins-convention/manifest.schema.json``; CON §2) plus the semantic
checks JSON-Schema cannot express (validation matrix keyed to CON). Produces
a :class:`~daemon.plugin_subsystem.plugin_declaration.PluginDeclaration` or a
typed refusal.

**Structural validation — dual path, one authority.** If the ``jsonschema``
package imports (it is in ``uv.lock`` transitively; NOT a declared
dependency of this slice), validation runs through ``Draft7Validator`` over
the vendored schema file. If it does not import, a hand-rolled
mini-validator validates the SAME schema file over the exact keyword subset
the v1 vocabulary uses (``type`` / ``required`` / ``properties`` /
``additionalProperties: false`` / ``items`` / ``enum`` / ``pattern`` /
``minLength`` / ``minItems``). The schema file — not a parallel in-code spec
table — is the single structural authority in both paths, so the two paths
cannot drift.

**Shape vs policy.** The schema expresses vocabulary SHAPE (allowed keys,
types, enums; ``additionalProperties: false`` everywhere). Policy checks
whose refusal codes are semantically demanded (kebab name, execution_mode
positivity, alarm_owner presence, parity section presence, divergence-entry
well-formedness, fence completeness, tag-only pins) carry NO structural
constraint in the schema — otherwise the generic structural code would fire
before the dedicated semantic code. See ``plugins-convention/manifest.schema.json``
top-level description for the same statement from the schema side.

**Refusal codes** (``ManifestRefusal.code``; snake_case; CON §5 codes reused
where they map):

Manifest/file level:
- ``manifest_missing`` — MANIFEST.yaml not found in the plugin dir
- ``manifest_unparseable`` — invalid YAML or non-mapping root document
- ``manifest_too_large`` — file exceeds the 64 KB hard-cap (CON §1)
- ``schema_version_missing`` — no ``schema_version`` key
- ``schema_version_unsupported`` — version outside the accepted 1.0.x family
  (breaking or not-yet-carried minor; see "schema_version rule" below)
- ``unknown_field`` — field outside the frozen vocabulary (any level)
- ``required_field_missing`` — schema-required field absent
- ``type_mismatch`` — present field of the wrong type/shape

plugin block:
- ``name_invalid`` — ``plugin.name`` not kebab-case
- ``name_dir_mismatch`` — ``plugin.name`` ≠ plugin directory name
- ``license_invalid`` — license not an exact match in the vendored SPDX list
  (missing/empty license is ``required_field_missing`` / ``type_mismatch``
  at the structural layer)
- ``unregistered_integration_path`` — path letter not a registry row
- ``absent_execution_mode`` — ``execution_mode`` absent (positive
  declaration: silence is not permission)
- ``execution_mode_not_allowed`` — mode not in the row's allowlist
- ``missing_required_manifest_fields`` — a field of the row's
  ``required_manifest_fields`` set absent (B ⇒ lifted_symbol/entrypoint/
  ipc_version; A ⇒ hosted_runtime_deps/fence_grant)
- ``fence_missing`` — ``integration_path: "A"`` without a complete
  ``fence_grant`` block
- ``runtime_pin_not_exact`` — ``hosted_runtime_deps.runtime_pin`` looks like
  a range (``^``/``~``/``>=``/``<=``/``*``/``x``) rather than an exact pin
- ``non_tag_pin`` — tag pin empty or bare-hex-SHA shaped (7–40 hex chars).
  v1 discriminates only SHA-shape + emptiness offline; full branch-vs-tag
  discrimination rides the vendoring-time sync checks (slice ③) where the
  upstream git is consulted.

provenance classes:
- ``missing_class_section`` — none of copy_freely / snapshot_with_drift_alarm
  / own_outright present
- ``alarm_owner_missing`` — empty/missing ``alarm_owner`` on copy_freely or
  snapshot_with_drift_alarm (own_outright deliberately carries none)
- ``empty_divergence_register`` — non-empty snapshot paths with an empty
  register
- ``divergence_entry_malformed`` — register entry missing a required field
  (belt-and-braces re-check of the schema shape)
- ``missing_parity_boundary`` — parity_boundary section absent
- ``parity_row_malformed`` — parity row without ``path``/``reason``

tree level (only when ``validate_tree=True``):
- ``adapter_missing`` — ``adapter/`` required iff execution_mode ∈
  {lifted-symbol, hosted-runtime}; refused at VENDORING, never runtime (CON §1)
- ``copy_freely_symlink`` — symlink found inside a copy_freely path (CON §1)

B-path only:
- ``entrypoint_invalid`` — ``plugin.entrypoint`` not a relative path rooted
  under ``adapter/`` (no ``..`` escape, no absolute path). The ≤200-line
  tripwire (CI alarm 220 / refuse 300) operates on real adapter files which
  do not exist until slice ② ships plugin trees — DEFERRED until then.

**Fence-routing (mirrors CONVENTION.md §Fence-routing contract).** The
``fence_grant`` field-set is checked in two stages and the refusal codes
split accordingly so the dedicated code wins over the generic missing-field
code: an A-path plugin with ``fence_grant`` ABSENT ⇒
``missing_required_manifest_fields`` (the registry row's
``required_manifest_fields`` set lists ``fence_grant``, so its absence fires
the row-required-fields refusal first); ``fence_grant`` PRESENT but
INCOMPLETE (any of ``rationale`` / ``granted_by`` / ``granted_at`` missing
or empty-string) ⇒ ``fence_missing``. Callers MUST handle both.

**Interpretations documented (minimal faithful readings):**
- *schema_version rule:* accepts the literal ``1.0.0`` and the additive
  ``1.0.x`` family (``^1\\.0\\.\\d+$``). Anything ≥1.1.0 (not-yet-carried
  minor) or any other major is ``schema_version_unsupported`` until the
  runner carries the newer schema (CON §8; ADR-PLUG-001 §2).
- *alarm_owner on own_outright:* NOT required and NOT allowed — CON §2's
  ``own_outright`` block declares only ``paths``; authored-locally means
  nothing upstream to alarm on. The schema refuses the field by construction.
- *parity subsections:* the SECTION is required; its two subsections are
  lists that may be empty; any present row must carry exactly
  ``{path, reason}``.
- *divergence entry shape:* required fields per CON §2 are
  ``{id, files, delta, rationale, pinning_test}``; ``upstream_ref`` is the
  one optional field.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Mapping, Optional, Sequence, Union

import yaml

from daemon.plugin_subsystem.path_type_registry import PathTypeRegistry, load_default_registry
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

__all__ = [
    "ManifestRefusal",
    "ManifestValidation",
    "read_manifest",
    "validate_manifest",
    "MANIFEST_FILENAME",
    "MANIFEST_SIZE_CAP_BYTES",
]

MANIFEST_FILENAME = "MANIFEST.yaml"
MANIFEST_SIZE_CAP_BYTES = 64 * 1024  # 64 KB hard-cap (CON §1)

_CONVENTION_DIR = Path(__file__).resolve().parents[2] / "plugins-convention"
_SCHEMA_PATH = _CONVENTION_DIR / "manifest.schema.json"
_SPDX_PATH = _CONVENTION_DIR / "spdx_ids.json"

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SCHEMA_VERSION_RE = re.compile(r"^1\.0\.(\d+)$")  # 1.0.x additive family only (see docstring)
_HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
# `..` is added per slice ② carry-forward (1): a parent-directory refname is
# provably never a valid git refname (git ref-format rules reject `..` as a
# path-traversal marker). The existing parametrized range-marker test
# auto-covers the addition.
_RANGE_PIN_CHARS = ("^", "~", ">=", "<=", ">", "<", "*", "x", "X", "..")
# Conservative blocklist of reserved git literals that are PROVABLY never tags.
# Full branch-vs-tag discrimination (e.g. arbitrary branch names, hex-named
# tags, reflog inspection) lands at slice ③ via git consultation — until
# then, the only way a real tag genuinely named "main" could slip through
# is if a maintainer publishes a tag whose exact literal collides here, which
# we treat as a near-impossible convention violation worth the safety margin.
_RESERVED_PIN_LITERALS = frozenset({"HEAD", "main", "master", "develop", "latest"})

_PROVENANCE_CLASSES = ("copy_freely", "snapshot_with_drift_alarm", "own_outright")
_DIVERGENCE_REQUIRED_FIELDS = ("id", "files", "delta", "rationale", "pinning_test")


def _looks_like_range(value: str) -> bool:
    """True iff ``value`` carries any range-expression marker from
    ``_RANGE_PIN_CHARS`` (extracted per slice ② carry-forward (2) to dedupe
    the two offline-provable call sites: the A-path ``runtime_pin`` check
    and the per-class ``tag_pin_per_class`` check)."""
    return any(marker in value for marker in _RANGE_PIN_CHARS)


# ─── refusal type ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ManifestRefusal(Exception):
    """Typed refusal: ``{code, message, location}``."""

    code: str
    message: str
    location: str = MANIFEST_FILENAME

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message} (at {self.location})"

    def as_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message, "location": self.location}


@dataclass(frozen=True)
class ManifestValidation:
    """Non-raising result shape for CI wiring."""

    ok: bool
    declaration: Optional[PluginDeclaration]
    refusal: Optional[ManifestRefusal]


# ─── structural validation ────────────────────────────────────────────────────

try:  # primary path: jsonschema (transitive in uv.lock; NOT added to pyproject here)
    import jsonschema  # type: ignore

    _HAS_JSONSCHEMA = True
except ImportError:  # fallback path: bounded mini-validator over the same schema file
    jsonschema = None  # type: ignore[assignment]
    _HAS_JSONSCHEMA = False


def _load_schema() -> Dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class _StructuralError:
    location: str
    code: str  # unknown_field | required_field_missing | type_mismatch
    message: str


def _dotted(path: Sequence[Union[str, int]]) -> str:
    parts = [str(p) for p in path]
    return ".".join(parts) if parts else MANIFEST_FILENAME


def _validate_structural_mini(doc: Any, schema: Mapping[str, Any]) -> List[_StructuralError]:
    """Hand-rolled fallback: bounded validator over the schema's keyword subset.

    Supports exactly the keywords the v1 vocabulary schema uses (see module
    docstring). Anything else in the schema file is ignored — additions to
    the vocabulary must extend this subset deliberately.
    """
    errors: List[_StructuralError] = []

    def check(node: Any, sch: Mapping[str, Any], path: List[Union[str, int]]) -> None:
        expected = sch.get("type")
        if expected == "object":
            if not isinstance(node, Mapping):
                errors.append(_StructuralError(_dotted(path), "type_mismatch", "expected an object"))
                return
            props = sch.get("properties", {})
            declared = set(props)
            # Reporting convention matches jsonschema: violations anchor at the
            # CONTAINING object, with the offending key named in the message.
            for key in node:
                if key not in declared and sch.get("additionalProperties") is False:
                    errors.append(
                        _StructuralError(
                            _dotted(path),
                            "unknown_field",
                            f"field {key!r} is not part of the frozen v1 vocabulary",
                        )
                    )
            for req in sch.get("required", []):
                if req not in node:
                    errors.append(
                        _StructuralError(
                            _dotted(path), "required_field_missing", f"required field {req!r} is absent"
                        )
                    )
            for key, value in node.items():
                if key in props:
                    check(value, props[key], path + [key])
        elif expected == "array":
            if not isinstance(node, list):
                errors.append(_StructuralError(_dotted(path), "type_mismatch", "expected a list"))
                return
            min_items = sch.get("minItems")
            if min_items is not None and len(node) < min_items:
                errors.append(
                    _StructuralError(_dotted(path), "type_mismatch", f"expected at least {min_items} item(s)")
                )
                return
            items_schema = sch.get("items")
            if isinstance(items_schema, Mapping):
                for index, item in enumerate(node):
                    check(item, items_schema, path + [index])
        elif expected == "string":
            if not isinstance(node, str):
                errors.append(_StructuralError(_dotted(path), "type_mismatch", "expected a string"))
                return
            if "enum" in sch and node not in sch["enum"]:
                errors.append(
                    _StructuralError(_dotted(path), "type_mismatch", f"value {node!r} not in {sch['enum']}")
                )
            min_length = sch.get("minLength")
            if min_length is not None and len(node) < min_length:
                errors.append(
                    _StructuralError(_dotted(path), "type_mismatch", f"shorter than {min_length} character(s)")
                )
            pattern = sch.get("pattern")
            if pattern is not None and re.search(pattern, node) is None:
                errors.append(_StructuralError(_dotted(path), "type_mismatch", f"does not match pattern {pattern!r}"))
        elif expected == "integer":
            if isinstance(node, bool) or not isinstance(node, int):
                errors.append(_StructuralError(_dotted(path), "type_mismatch", "expected an integer"))
                return
            if "enum" in sch and node not in sch["enum"]:
                errors.append(_StructuralError(_dotted(path), "type_mismatch", f"value {node!r} not in {sch['enum']}"))
        elif expected == "boolean":
            if not isinstance(node, bool):
                errors.append(_StructuralError(_dotted(path), "type_mismatch", "expected a boolean"))

    check(doc, schema, [])
    return errors


def _validate_structural(doc: Any) -> List[_StructuralError]:
    schema = _load_schema()
    if _HAS_JSONSCHEMA:
        errors: List[_StructuralError] = []
        validator = jsonschema.Draft7Validator(schema)  # type: ignore[union-attr]
        for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
            location = _dotted(err.absolute_path)
            if err.validator == "additionalProperties":
                code = "unknown_field"
            elif err.validator == "required":
                code = "required_field_missing"
            else:
                code = "type_mismatch"
            errors.append(_StructuralError(location, code, err.message))
        return errors
    warnings.warn(
        "manifest_reader: jsonschema unavailable — engaging bounded mini-validator fallback "
        "(degraded mode; dual-path equivalence is pinned by test_dual_path_equivalence.py)",
        RuntimeWarning,
        stacklevel=2,
    )
    return _validate_structural_mini(doc, schema)


def _structural_refusal(err: _StructuralError) -> ManifestRefusal:
    # name kebab violations surface with their dedicated semantic code from
    # _check_semantics — the schema carries no `pattern` on plugin.name, so
    # no structural error can ever be a name violation (name_invalid is
    # semantic-only).
    return ManifestRefusal(err.code, err.message, err.location)


# ─── semantic checks ──────────────────────────────────────────────────────────


def _load_spdx_ids() -> FrozenSet[str]:  # type: ignore[name-defined]
    data = json.loads(_SPDX_PATH.read_text(encoding="utf-8"))
    return frozenset(data["ids"])  # type: ignore[no-any-return]


def _check_semantics(
    doc: Mapping[str, Any],
    *,
    plugin_dir: Path,
    registry: PathTypeRegistry,
    spdx_ids: FrozenSet[str],  # type: ignore[name-defined]
) -> Optional[ManifestRefusal]:
    """Ordered semantic checks over an already structurally-valid document.

    Returns the first refusal or None.
    """
    plugin = doc["plugin"]

    # -- schema_version (pre-checked by caller; assert-family guard here) ----
    # (caller: _check_schema_version before structural validation)

    # -- name / dir -----------------------------------------------------------
    name = plugin["name"]
    if _KEBAB_RE.match(name) is None:
        return ManifestRefusal("name_invalid", "plugin.name must be kebab-case", "plugin.name")
    if name != plugin_dir.name:
        return ManifestRefusal(
            "name_dir_mismatch",
            f"plugin.name {name!r} does not match plugin directory {plugin_dir.name!r}",
            "plugin.name",
        )

    # -- license (exact-match against the vendored ids-only list) -------------
    if plugin["license"] not in spdx_ids:
        return ManifestRefusal(
            "license_invalid",
            f"license {plugin['license']!r} is not in the vendored SPDX validator list "
            f"({len(spdx_ids)} ids; validator list is EVOLVABLE)",
            "plugin.license",
        )

    # -- integration_path registered ------------------------------------------
    path_letter = plugin["integration_path"]
    if not registry.is_registered(path_letter):
        return ManifestRefusal(
            "unregistered_integration_path",
            f"integration_path {path_letter!r} is not a registered path-type row "
            f"(registered: {', '.join(registry.registered_paths())})",
            "plugin.integration_path",
        )
    row = registry.get(path_letter)

    # -- execution_mode: positive declaration (silence is not permission) -----
    if "execution_mode" not in plugin:
        return ManifestRefusal(
            "absent_execution_mode",
            "execution_mode must be declared positively (silence is not permission)",
            "plugin.execution_mode",
        )
    execution_mode = plugin["execution_mode"]
    if execution_mode not in row.allowed_execution_modes:
        return ManifestRefusal(
            "execution_mode_not_allowed",
            f"execution_mode {execution_mode!r} is not allowed for path {path_letter!r} "
            f"(allowed: {', '.join(row.allowed_execution_modes)})",
            "plugin.execution_mode",
        )

    # -- row-required manifest fields ------------------------------------------
    missing = [field_name for field_name in row.required_manifest_fields if field_name not in plugin]
    if missing:
        return ManifestRefusal(
            "missing_required_manifest_fields",
            f"path {path_letter!r} requires manifest field(s): {', '.join(missing)}",
            "plugin",
        )

    # -- A-path fence -----------------------------------------------------------
    if path_letter == "A":
        grant = plugin.get("fence_grant") or {}
        incomplete = [
            field_name
            for field_name in ("rationale", "granted_by", "granted_at")
            if not isinstance(grant.get(field_name), str) or not grant.get(field_name, "").strip()
        ]
        if incomplete:
            return ManifestRefusal(
                "fence_missing",
                f"integration_path 'A' requires a complete fence_grant; incomplete/empty: {', '.join(incomplete)}",
                "plugin.fence_grant",
            )
        deps = plugin.get("hosted_runtime_deps") or {}
        runtime_pin = deps.get("runtime_pin", "")
        if _looks_like_range(runtime_pin):
            return ManifestRefusal(
                "runtime_pin_not_exact",
                f"hosted_runtime_deps.runtime_pin {runtime_pin!r} looks like a range; exact pins only",
                "plugin.hosted_runtime_deps.runtime_pin",
            )

    # -- B-path entrypoint shape ------------------------------------------------
    entrypoint = plugin.get("entrypoint")
    if entrypoint is not None:
        if (
            not isinstance(entrypoint, str)
            or entrypoint.startswith("/")
            or ".." in Path(entrypoint).parts
            or Path(entrypoint).parts[:1] != ("adapter",)
        ):
            return ManifestRefusal(
                "entrypoint_invalid",
                f"entrypoint {entrypoint!r} must be a relative path rooted under adapter/",
                "plugin.entrypoint",
            )

    # -- tag pins: OFFLINE-PROVABLE refusals enforced in v1; full branch-vs-tag
    #    discrimination (hex-named tags, arbitrary branch names) defers to
    #    vendoring-time sync checks at slice ③ via git consultation. The
    #    conservative blocklist below is intentional — a real tag literally
    #    named "main" upstream is indistinguishable offline and we refuse
    #    until ③ rather than risk it.
    pins = plugin["upstream"]["tag_pin_per_class"]
    for class_name, pin in pins.items():
        stripped = pin.strip()
        if not stripped:
            return ManifestRefusal(
                "non_tag_pin",
                f"tag pin for {class_name!r} must be a git tag, not an empty value "
                "(empty pins are refused offline in v1)",
                f"plugin.upstream.tag_pin_per_class.{class_name}",
            )
        if _looks_like_range(stripped):
            return ManifestRefusal(
                "non_tag_pin",
                f"tag pin for {class_name!r} {pin!r} looks like a range expression; "
                "range markers are provably never tags (refused offline in v1)",
                f"plugin.upstream.tag_pin_per_class.{class_name}",
            )
        if stripped in _RESERVED_PIN_LITERALS:
            return ManifestRefusal(
                "non_tag_pin",
                f"tag pin for {class_name!r} {pin!r} is a reserved git literal "
                "(HEAD/main/master/develop/latest are provably never tags; refused offline in v1; "
                "full branch-vs-tag discrimination lands at slice ③)",
                f"plugin.upstream.tag_pin_per_class.{class_name}",
            )
        if _HEX_SHA_RE.match(stripped):
            return ManifestRefusal(
                "non_tag_pin",
                f"tag pin for {class_name!r} must be a git tag, not a bare SHA "
                "(bare hex SHAs are refused offline in v1)",
                f"plugin.upstream.tag_pin_per_class.{class_name}",
            )

    # -- ≥1 provenance class section ---------------------------------------------
    if not any(class_name in doc for class_name in _PROVENANCE_CLASSES):
        return ManifestRefusal(
            "missing_class_section",
            "at least one provenance-class section is required "
            "(copy_freely / snapshot_with_drift_alarm / own_outright)",
            MANIFEST_FILENAME,
        )

    # -- alarm_owner required on the synced classes -------------------------------
    for class_name in ("copy_freely", "snapshot_with_drift_alarm"):
        section = doc.get(class_name)
        if section is None:
            continue
        owner = section.get("alarm_owner")
        if not isinstance(owner, str) or not owner.strip():
            return ManifestRefusal(
                "alarm_owner_missing",
                f"{class_name}.alarm_owner is REQUIRED and must be a non-empty string "
                "(own_outright carries no alarm_owner by design)",
                f"{class_name}.alarm_owner",
            )

    # -- divergence register on non-empty snapshot paths ---------------------------
    snapshot = doc.get("snapshot_with_drift_alarm")
    if snapshot is not None and snapshot.get("paths"):
        register = snapshot.get("divergence_register")
        if not register:
            return ManifestRefusal(
                "empty_divergence_register",
                "non-empty snapshot_with_drift_alarm.paths require a non-empty divergence_register "
                "(empty register on non-empty path = refused, CON §2)",
                "snapshot_with_drift_alarm.divergence_register",
            )
        for index, entry in enumerate(register):
            malformed = [
                field_name
                for field_name in _DIVERGENCE_REQUIRED_FIELDS
                if field_name not in entry or entry[field_name] in (None, "", [])
            ]
            if malformed:
                return ManifestRefusal(
                    "divergence_entry_malformed",
                    f"divergence_register[{index}] missing/empty required field(s): {', '.join(malformed)} "
                    "(upstream_ref is the one optional field)",
                    f"snapshot_with_drift_alarm.divergence_register[{index}]",
                )

    # -- parity boundary section required ------------------------------------------
    parity = doc.get("parity_boundary")
    if parity is None:
        return ManifestRefusal(
            "missing_parity_boundary",
            "parity_boundary is a required manifest section "
            "(intentionally_not_vendored / not_executed; subsections may be empty)",
            "parity_boundary",
        )
    for subsection in ("intentionally_not_vendored", "not_executed"):
        for index, row_entry in enumerate(parity.get(subsection, [])):
            if not isinstance(row_entry, Mapping) or not row_entry.get("path") or not row_entry.get("reason"):
                return ManifestRefusal(
                    "parity_row_malformed",
                    f"parity_boundary.{subsection}[{index}] rows must carry non-empty path and reason",
                    f"parity_boundary.{subsection}[{index}]",
                )

    # -- skills section (CON §6; slice ④) ---------------------------------------
    skills_section = doc.get("skills")
    if skills_section is not None:
        entries = skills_section.get("entries")
        if not isinstance(entries, list) or not entries:
            return ManifestRefusal(
                "skills_entries_empty",
                "skills.entries must be a non-empty list when the skills section is present "
                "(CON §6: each declared skill must carry a concrete file path; "
                "declaring an empty skills section is refused)",
                "skills.entries",
            )
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                return ManifestRefusal(
                    "skills_entry_malformed",
                    f"skills.entries[{index}] must be a mapping with non-empty skill_id and path",
                    f"skills.entries[{index}]",
                )
            skill_id = entry.get("skill_id")
            entry_path = entry.get("path")
            if not isinstance(skill_id, str) or not skill_id.strip():
                return ManifestRefusal(
                    "skills_entry_malformed",
                    f"skills.entries[{index}].skill_id must be a non-empty string",
                    f"skills.entries[{index}].skill_id",
                )
            if not isinstance(entry_path, str) or not entry_path.strip():
                return ManifestRefusal(
                    "skills_entry_malformed",
                    f"skills.entries[{index}].path must be a non-empty string",
                    f"skills.entries[{index}].path",
                )
            path_obj = Path(entry_path)
            if path_obj.is_absolute():
                return ManifestRefusal(
                    "skills_entry_path_outside_tree",
                    f"skills.entries[{index}].path {entry_path!r} is an absolute path; "
                    "paths must be relative to the plugin tree root (CON §6)",
                    f"skills.entries[{index}].path",
                )
            if ".." in path_obj.parts:
                return ManifestRefusal(
                    "skills_entry_path_outside_tree",
                    f"skills.entries[{index}].path {entry_path!r} contains '..'; "
                    "traversal outside the plugin tree is refused (CON §6)",
                    f"skills.entries[{index}].path",
                )

    return None


def _check_schema_version(doc: Mapping[str, Any]) -> Optional[ManifestRefusal]:
    if "schema_version" not in doc:
        return ManifestRefusal("schema_version_missing", "schema_version is required", "schema_version")
    version = doc["schema_version"]
    if not isinstance(version, str) or _SCHEMA_VERSION_RE.match(version) is None:
        return ManifestRefusal(
            "schema_version_unsupported",
            f"schema_version {version!r} is outside the accepted 1.0.x additive family "
            "(v1 validator knows only 1.0.x; ≥1.1.0 requires the runner to carry the newer schema; "
            "a different major is breaking, CON §8)",
            "schema_version",
        )
    return None


# ─── tree-level checks (vendoring-time; only when a tree is validated) ────────


def _check_tree(doc: Mapping[str, Any], plugin_dir: Path) -> Optional[ManifestRefusal]:
    execution_mode = doc["plugin"]["execution_mode"]
    needs_adapter = execution_mode in ("lifted-symbol", "hosted-runtime")
    if needs_adapter and not (plugin_dir / "adapter").is_dir():
        return ManifestRefusal(
            "adapter_missing",
            f"adapter/ is required for execution_mode {execution_mode!r} (refused at VENDORING, never runtime)",
            "adapter/",
        )

    # cheap one-liner guard: no symlinks inside copy_freely/ (CON §1)
    copy_section = doc.get("copy_freely") or {}
    for rel in copy_section.get("paths", []):
        target = plugin_dir / rel
        if target.is_symlink():
            return ManifestRefusal(
                "copy_freely_symlink", f"symlink found inside copy_freely path {rel!r} (CON §1)", f"copy_freely/{rel}"
            )
        if target.is_dir():
            for child in target.rglob("*"):
                if child.is_symlink():
                    return ManifestRefusal(
                        "copy_freely_symlink",
                        f"symlink found inside copy_freely path {rel!r}: {child.relative_to(plugin_dir)} (CON §1)",
                        f"copy_freely/{rel}",
                    )
    return None


# ─── public API ───────────────────────────────────────────────────────────────


def _build_declaration(doc: Mapping[str, Any], plugin_dir: Path) -> PluginDeclaration:
    plugin = doc["plugin"]
    return PluginDeclaration(
        name=plugin["name"],
        license=plugin["license"],
        upstream_repo=plugin["upstream"]["repo"],
        tag_pin_per_class=dict(plugin["upstream"]["tag_pin_per_class"]),
        integration_path=plugin["integration_path"],
        execution_mode=plugin["execution_mode"],
        lifted_symbol=plugin.get("lifted_symbol"),
        entrypoint=plugin.get("entrypoint"),
        ipc_version=plugin.get("ipc_version"),
        hosted_runtime_deps=plugin.get("hosted_runtime_deps"),
        fence_grant=plugin.get("fence_grant"),
        copy_freely=dict(doc.get("copy_freely", {})),
        snapshot_with_drift_alarm=dict(doc.get("snapshot_with_drift_alarm", {})),
        own_outright=dict(doc.get("own_outright", {})),
        parity_intentionally_not_vendored=tuple(
            doc.get("parity_boundary", {}).get("intentionally_not_vendored", [])
        ),
        parity_not_executed=tuple(doc.get("parity_boundary", {}).get("not_executed", [])),
        # CON §6: the manifest NAMES plugin-skills here; the registry
        # reads each file.  An absent ``skills`` section is normal
        # (every plugin does not have to ship skills) — empty tuple
        # captures the "no skills declared" state.
        manifest_skills_entries=tuple(
            doc.get("skills", {}).get("entries", [])
        ),
        source_dir=plugin_dir,
        schema_version=doc["schema_version"],
    )


def validate_manifest(
    plugin_dir: Path,
    *,
    validate_tree: bool = False,
    registry: Optional[PathTypeRegistry] = None,
    spdx_ids: Optional[FrozenSet[str]] = None,  # type: ignore[name-defined]
) -> ManifestValidation:
    """Validate one plugin dir's manifest; non-raising result shape for CI."""
    plugin_dir = Path(plugin_dir)
    registry = registry if registry is not None else load_default_registry()
    spdx = spdx_ids if spdx_ids is not None else _load_spdx_ids()

    manifest_path = plugin_dir / MANIFEST_FILENAME

    # file-level gates
    if not manifest_path.is_file():
        refusal = ManifestRefusal(
            "manifest_missing", f"{MANIFEST_FILENAME} not found in {plugin_dir.name}/", str(manifest_path)
        )
        return ManifestValidation(False, None, refusal)
    size_bytes = manifest_path.stat().st_size
    if size_bytes > MANIFEST_SIZE_CAP_BYTES:
        refusal = ManifestRefusal(
            "manifest_too_large",
            f"manifest is {size_bytes} bytes; hard-cap is {MANIFEST_SIZE_CAP_BYTES} (CON §1)",
            str(manifest_path),
        )
        return ManifestValidation(False, None, refusal)
    try:
        doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        refusal = ManifestRefusal("manifest_unparseable", f"invalid YAML: {exc}", str(manifest_path))
        return ManifestValidation(False, None, refusal)
    if not isinstance(doc, Mapping):
        refusal = ManifestRefusal(
            "manifest_unparseable", "manifest root must be a mapping", str(manifest_path)
        )
        return ManifestValidation(False, None, refusal)

    # schema_version gate (before structural: dedicated code, family rule)
    version_refusal = _check_schema_version(doc)
    if version_refusal is not None:
        return ManifestValidation(False, None, version_refusal)

    # structural gate (jsonschema primary / mini-validator fallback, same schema file)
    structural_errors = _validate_structural(doc)
    if structural_errors:
        first = structural_errors[0]
        return ManifestValidation(False, None, _structural_refusal(first))

    # semantic gate
    semantic_refusal = _check_semantics(doc, plugin_dir=plugin_dir, registry=registry, spdx_ids=spdx)
    if semantic_refusal is not None:
        return ManifestValidation(False, None, semantic_refusal)

    # tree gate (vendoring-time only)
    if validate_tree:
        tree_refusal = _check_tree(doc, plugin_dir)
        if tree_refusal is not None:
            return ManifestValidation(False, None, tree_refusal)

    return ManifestValidation(True, _build_declaration(doc, plugin_dir), None)


def read_manifest(
    plugin_dir: Path,
    *,
    validate_tree: bool = False,
    registry: Optional[PathTypeRegistry] = None,
    spdx_ids: Optional[FrozenSet[str]] = None,  # type: ignore[name-defined]
) -> PluginDeclaration:
    """Parse + validate ``MANIFEST.yaml``; returns the typed declaration.

    Raises :class:`ManifestRefusal` on any validation failure (typed
    ``{code, message, location}`` refusal, catchable by callers).
    """
    result = validate_manifest(
        plugin_dir, validate_tree=validate_tree, registry=registry, spdx_ids=spdx_ids
    )
    if result.refusal is not None:
        raise result.refusal
    assert result.declaration is not None  # noqa: S101 - invariant of ok=True
    return result.declaration
