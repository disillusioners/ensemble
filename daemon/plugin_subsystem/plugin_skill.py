"""Plugin-skill template + loader (REC §1.2 component 11; CON §6; slice ④).

The CON §6 consumption interface is the v1 contract for plugin skills.  A
plugin-skill is **DATA** that rides the existing tier-1 skill machinery —
the loader here produces typed, validated :class:`PluginSkill` objects
that the registration path (REC §1.2 comp 3; ``PluginRegistry``) hands to
consumers.  No runtime plugin code is loaded (CON §7 no-runtime-loading
sentinel); skills are YAML documents read from ``plugins/<name>/skills/``.

Frozen contract (CON §6)
------------------------

Each skill YAML at ``plugins/<name>/skills/<skill_id>.yaml`` carries:

- ``skill_id`` (string) — the kebab/dotted ID the consumer queries.
- ``schema_version`` (string) — the SKILL schema version.  v1 = ``"1.0.0"``
  (same family as the manifest; additive-only on ``1.0.x``).
- ``plugin_ref`` (object, required, non-empty) — the version-visibility
  surface.  At least these keys are required:
  ``name`` (kebab, must equal the parent plugin's ``plugin.name``);
  ``manifest_schema_version`` (literal ``"1.0.0"`` at v1);
  ``upstream_tag`` (per-class mapping; at least one of
  ``copy_freely`` / ``snapshot_with_drift_alarm`` /
  ``own_outright`` must be present — own_outright is unusual but legal
  for own-outright-only skill references);
  ``license`` (SPDX id; validated against the same list the manifest
  uses; empty = REFUSED).

- ``content`` (object, required, non-empty) — the skill's authored body.
  Carries ``description`` (string, non-empty);
  ``body_markdown`` (string, may be empty when vendored_references alone
  carry the payload);
  ``vendored_references`` (list, may be empty when the skill is
  pure-text).  Each vendored_reference MUST be
  ``{alias: str, path: str}`` and the path MUST resolve inside the
  parent plugin tree at load time — traversal outside the tree
  (e.g. ``..`` escapes, absolute paths) is REFUSED at load.

- ``consumption`` (object, required, non-empty) — declares the consumers.
  ``by`` is a list of NAMED consumer strings (e.g. ``"worker/designer"``).
  Empty list, ``["*"]``, or any other anonymous/globbed form is REFUSED
  (CON §6 "anonymous ['*'] refused (fail closed, tested)").

Failure modes (closed enum, all raised as :class:`PluginSkillRefusal`)
--------------------------------------------------------------------

- ``skill_missing`` — file not found at the expected path.
- ``skill_unparseable`` — invalid YAML or non-mapping root.
- ``skill_size_exceeded`` — file larger than the 64 KB hard-cap (matches
  the manifest cap; skill files are tiny by design).
- ``schema_version_missing`` / ``schema_version_unsupported`` —
  mirrors the manifest reader's gate (same ``1.0.x`` additive family).
- ``plugin_ref_missing`` / ``plugin_ref_malformed`` — the version-
  visibility block absent or missing required keys.
- ``plugin_name_mismatch`` — ``plugin_ref.name`` ≠ parent plugin's
  ``plugin.name`` (skills are anchored to one plugin tree).
- ``upstream_tag_missing`` — no class pin in ``plugin_ref.upstream_tag``
  (CON §6 invariant).
- ``license_invalid`` — license not in the vendored SPDX list.
- ``content_missing`` / ``content_field_malformed`` — content block
  absent or required field wrong shape.
- ``vendored_reference_malformed`` — entry missing ``alias``/``path``
  or empty fields.
- ``vendored_reference_outside_tree`` — path escapes the parent plugin
  directory (CON §6 "traversal OUTSIDE the plugin tree is REFUSED
  (fail closed, tested)").
- ``vendored_reference_unresolved`` — path does not exist at load time
  (CON §6 "MUST resolve at load").
- ``consumption_missing`` / ``consumption_by_anonymous`` /
  ``consumption_by_empty`` — consumers block absent, ``["*"]`` (or any
  globbed value), or empty list.
- ``skill_id_mismatch`` — the YAML's ``skill_id`` does not match the
  expected ``<id>.yaml`` filename.

Vocabulary confinement (CON §7)
-------------------------------

This module is the only zone (alongside ``manifest_reader.py`` and
``plugins-convention/``) where the consumption-interface vocabulary
(``plugin_ref`` / ``vendored_references`` / ``consumption``) may appear
as code.  Skill YAML files under ``plugins/<name>/skills/`` are DATA
instances, not vocabulary definition sites — the sentinel
``TestVocabularyConfinement`` carves them out via the
``PLUGIN_AUTHORIZED_DATA_FILENAMES`` family (extending the existing
``MANIFEST.yaml`` / ``CURATION.md`` carve-out by basename pattern
would be a separate evolution; the slice-④ test that asserts
vocabulary compliance for the slice-④ skill uses the manifest
``MANIFEST.yaml`` carve-out that already exists).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, FrozenSet, List, Mapping, Optional, Sequence, Union

import yaml

# Public re-export target — kept stable so callers (and tests) bind to
# a single symbol instead of the manifest reader's incidental surface.
SCHEMA_VERSION: str = "1.0.0"
SKILL_FILE_SUFFIX: str = ".yaml"
SKILL_SIZE_CAP_BYTES: int = 64 * 1024  # matches the manifest hard-cap (CON §1)

# Mirror the manifest reader's 1.0.x additive-family rule (CON §8).
# Skills and manifests share the same versioning rule (CON §6 carries
# the same ``schema_version: "1.0.0"`` family).
_SKILL_SCHEMA_VERSION_RE = re.compile(r"^1\.0\.(\d+)$")

# Per CON §2 / CON §6 / path_types.yaml C row: the three provenance
# classes whose pins may appear in ``plugin_ref.upstream_tag``.
# ``own_outright`` is never synced, so a skill pinned to an own_outright
# upstream is unusual but legal (e.g. an own-outright-only skill).
_TAG_CLASSES: frozenset = frozenset(
    {"copy_freely", "snapshot_with_drift_alarm", "own_outright"}
)

__all__ = [
    "PluginSkill",
    "PluginSkillRefusal",
    "VendoredReference",
    "SCHEMA_VERSION",
    "SKILL_FILE_SUFFIX",
    "SKILL_SIZE_CAP_BYTES",
    "read_skill_file",
    "validate_skill_doc",
    "list_skill_files",
]


# ─── typed refusal ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PluginSkillRefusal(Exception):
    """Typed refusal: ``{code, message, location}``.

    Codes form a closed enum (see module docstring for the full set).
    """

    code: str
    message: str
    location: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        loc = f" (at {self.location})" if self.location else ""
        return f"[{self.code}] {self.message}{loc}"

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "location": self.location}


# ─── typed value objects ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class VendoredReference:
    """One entry in ``content.vendored_references`` (CON §6).

    ``path`` is the vendored-relative path (resolved against the parent
    plugin tree at load time; traversal outside the tree is REFUSED).
    ``alias`` is the consumer-facing handle; uniqueness is checked at
    validate time (the consumer queries by alias, not by path).
    """

    alias: str
    path: str
    # Resolved absolute path — populated by ``read_skill_file`` after the
    # inside-tree + exists check passes.  Tests can assert on this.
    resolved: Optional[Path] = None


@dataclass(frozen=True)
class PluginSkill:
    """Validated, typed view of one plugin-skill YAML (CON §6).

    Constructed exclusively by :func:`read_skill_file` after structural
    + semantic validation pass.  The ``source_path`` records the
    concrete file the data came from so the registration layer can
    produce audit-friendly reports.
    """

    skill_id: str
    schema_version: str
    plugin_name: str
    plugin_manifest_schema_version: str
    upstream_tag: Mapping[str, str]
    license: str
    description: str
    body_markdown: str
    vendored_references: Sequence[VendoredReference]
    consumers: Sequence[str]
    source_path: Optional[Path] = None

    @property
    def alias_set(self) -> frozenset:
        """Consumer-facing alias handles (CON §6: each ``vendored_references``
        entry is referenced by alias; alias uniqueness is enforced at
        validate time and exposed here for quick lookups)."""
        return frozenset(ref.alias for ref in self.vendored_references)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _load_spdx_ids() -> FrozenSet[str]:
    """Re-read the vendored SPDX list (same file the manifest reader uses).

    The file is a JSON object with an ``ids`` key carrying the
    authoritative allowlist; the surrounding metadata keys
    (``list_name`` / ``list_version`` / ``match_rule`` / ``evolvability``)
    are operational notes and are NOT SPDX ids.
    """
    spdx_path = Path(__file__).resolve().parents[2] / "plugins-convention" / "spdx_ids.json"
    data = json.loads(spdx_path.read_text(encoding="utf-8"))
    return frozenset(data["ids"])


def _looks_like_range(value: str) -> bool:
    """Range-expression discrimination — mirrors the manifest reader's
    helper.  The skill's ``plugin_ref.upstream_tag`` is per-class tag
    pins (CON §2) so range markers are provably wrong; ``HEAD``/``main``
    etc. are reserved git literals (tombstone guard, slice ① carry)."""
    return any(marker in value for marker in ("^", "~", ">=", "<=", ">", "<", "*", "x", "X", ".."))


_RESERVED_PIN_LITERALS = frozenset({"HEAD", "main", "master", "develop", "latest"})
_HEX_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _check_upstream_tag(upstream_tag: Any, location: str) -> Optional[PluginSkillRefusal]:
    """Per-class tag-pin validation (CON §2 + CON §6 invariants)."""
    if not isinstance(upstream_tag, Mapping) or not upstream_tag:
        return PluginSkillRefusal(
            "upstream_tag_missing",
            "plugin_ref.upstream_tag is required and must be a non-empty mapping "
            "(at least one of copy_freely / snapshot_with_drift_alarm / own_outright)",
            location,
        )
    for class_name, pin in upstream_tag.items():
        if class_name not in _TAG_CLASSES:
            return PluginSkillRefusal(
                "upstream_tag_unknown_class",
                f"plugin_ref.upstream_tag carries unknown class {class_name!r}; "
                f"allowed: {', '.join(sorted(_TAG_CLASSES))}",
                f"{location}.{class_name}",
            )
        if not isinstance(pin, str) or not pin.strip():
            return PluginSkillRefusal(
                "upstream_tag_missing",
                f"plugin_ref.upstream_tag.{class_name} must be a non-empty string "
                "(CON §2: empty pins are refused offline)",
                f"{location}.{class_name}",
            )
        stripped = pin.strip()
        if _looks_like_range(stripped):
            return PluginSkillRefusal(
                "non_tag_pin",
                f"plugin_ref.upstream_tag.{class_name} {pin!r} looks like a range; "
                "tag pins only (CON §2)",
                f"{location}.{class_name}",
            )
        if stripped in _RESERVED_PIN_LITERALS:
            return PluginSkillRefusal(
                "non_tag_pin",
                f"plugin_ref.upstream_tag.{class_name} {pin!r} is a reserved git literal "
                "(HEAD/main/master/develop/latest are provably never tags)",
                f"{location}.{class_name}",
            )
        if _HEX_SHA_RE.match(stripped):
            return PluginSkillRefusal(
                "non_tag_pin",
                f"plugin_ref.upstream_tag.{class_name} must be a git tag, not a bare SHA",
                f"{location}.{class_name}",
            )
    return None


# ─── document validation ──────────────────────────────────────────────────────


def validate_skill_doc(
    doc: Mapping[str, Any],
    *,
    plugin_root: Path,
    plugin_name: str,
    plugin_license: str,
    source_path: Optional[Path] = None,
) -> PluginSkill:
    """Validate one parsed skill YAML document (CON §6).

    Args:
        doc: The parsed YAML mapping (the file's root document).
        plugin_root: The parent plugin tree root (used to resolve
            ``vendored_references`` and to enforce the inside-tree rule).
        plugin_name: The parent plugin's ``plugin.name`` (kebab; must
            equal the skill's ``plugin_ref.name``).
        plugin_license: The parent plugin's ``plugin.license`` (SPDX id).
        source_path: Optional path the doc was loaded from; recorded on
            the returned :class:`PluginSkill` for audit/registration.

    Returns:
        A validated :class:`PluginSkill` carrying the typed fields the
        registration layer hands to consumers.

    Raises:
        PluginSkillRefusal: any structural or semantic check fails.  The
            ``code`` is the stable refusal token (tests bind to codes,
            not to messages).
    """
    location_prefix = str(source_path) if source_path is not None else "skill-doc"

    # -- skill_id ---------------------------------------------------------------
    skill_id = doc.get("skill_id")
    if not isinstance(skill_id, str) or not skill_id.strip():
        raise PluginSkillRefusal(
            "skill_id_missing",
            "skill_id is required and must be a non-empty string",
            f"{location_prefix}.skill_id",
        )
    if source_path is not None:
        expected_stem = source_path.stem
        if expected_stem != skill_id:
            raise PluginSkillRefusal(
                "skill_id_mismatch",
                f"skill_id {skill_id!r} does not match the filename stem {expected_stem!r}",
                f"{location_prefix}.skill_id",
            )

    # -- schema_version ---------------------------------------------------------
    version = doc.get("schema_version")
    if not isinstance(version, str) or _SKILL_SCHEMA_VERSION_RE.match(version) is None:
        raise PluginSkillRefusal(
            "schema_version_unsupported",
            f"schema_version {version!r} is outside the accepted 1.0.x additive family "
            "(CON §8: 1.0.x additive only; ≥1.1.0 requires the runner to carry the newer schema)",
            f"{location_prefix}.schema_version",
        )

    # -- plugin_ref -------------------------------------------------------------
    plugin_ref = doc.get("plugin_ref")
    if not isinstance(plugin_ref, Mapping) or not plugin_ref:
        raise PluginSkillRefusal(
            "plugin_ref_missing",
            "plugin_ref is required and must be a non-empty mapping (CON §6 version-visibility surface)",
            f"{location_prefix}.plugin_ref",
        )
    ref_name = plugin_ref.get("name")
    if not isinstance(ref_name, str) or not ref_name.strip():
        raise PluginSkillRefusal(
            "plugin_ref_malformed",
            "plugin_ref.name is required and must be a non-empty string",
            f"{location_prefix}.plugin_ref.name",
        )
    if ref_name != plugin_name:
        raise PluginSkillRefusal(
            "plugin_name_mismatch",
            f"plugin_ref.name {ref_name!r} does not match the parent plugin's name {plugin_name!r} "
            "(skills are anchored to one plugin tree)",
            f"{location_prefix}.plugin_ref.name",
        )
    ref_manifest_schema = plugin_ref.get("manifest_schema_version")
    if ref_manifest_schema != "1.0.0":
        raise PluginSkillRefusal(
            "plugin_ref_malformed",
            f"plugin_ref.manifest_schema_version must be the literal \"1.0.0\" at v1; got {ref_manifest_schema!r}",
            f"{location_prefix}.plugin_ref.manifest_schema_version",
        )
    ref_license = plugin_ref.get("license")
    spdx = _load_spdx_ids()
    if not isinstance(ref_license, str) or ref_license not in spdx:
        raise PluginSkillRefusal(
            "license_invalid",
            f"plugin_ref.license {ref_license!r} is not in the vendored SPDX list",
            f"{location_prefix}.plugin_ref.license",
        )
    # Defense-in-depth: a skill must reference the SAME license the parent
    # plugin carries.  A divergence (skill says MIT, parent says Apache-2.0)
    # is a ref-data mistake and we refuse to load (CON §6 invariant:
    # license is part of the version-visibility surface).
    if ref_license != plugin_license:
        raise PluginSkillRefusal(
            "license_invalid",
            f"plugin_ref.license {ref_license!r} does not match the parent plugin's license {plugin_license!r}",
            f"{location_prefix}.plugin_ref.license",
        )
    upstream_refusal = _check_upstream_tag(
        plugin_ref.get("upstream_tag"),
        f"{location_prefix}.plugin_ref.upstream_tag",
    )
    if upstream_refusal is not None:
        raise upstream_refusal

    # -- content ----------------------------------------------------------------
    content = doc.get("content")
    if not isinstance(content, Mapping) or not content:
        raise PluginSkillRefusal(
            "content_missing",
            "content is required and must be a non-empty mapping (CON §6)",
            f"{location_prefix}.content",
        )
    description = content.get("description")
    if not isinstance(description, str) or not description.strip():
        raise PluginSkillRefusal(
            "content_field_malformed",
            "content.description is required and must be a non-empty string",
            f"{location_prefix}.content.description",
        )
    body_markdown = content.get("body_markdown", "")
    if not isinstance(body_markdown, str):
        raise PluginSkillRefusal(
            "content_field_malformed",
            "content.body_markdown must be a string (may be empty)",
            f"{location_prefix}.content.body_markdown",
        )

    # -- vendored_references ----------------------------------------------------
    raw_refs = content.get("vendored_references", [])
    if not isinstance(raw_refs, list):
        raise PluginSkillRefusal(
            "vendored_reference_malformed",
            "content.vendored_references must be a list (may be empty when the skill is pure text)",
            f"{location_prefix}.content.vendored_references",
        )
    resolved_refs: List[VendoredReference] = []
    seen_aliases: dict = {}
    plugin_root_abs = plugin_root.resolve()
    for index, entry in enumerate(raw_refs):
        if not isinstance(entry, Mapping):
            raise PluginSkillRefusal(
                "vendored_reference_malformed",
                f"vendored_references[{index}] must be a mapping with non-empty alias and path",
                f"{location_prefix}.content.vendored_references[{index}]",
            )
        alias = entry.get("alias")
        path = entry.get("path")
        if not isinstance(alias, str) or not alias.strip():
            raise PluginSkillRefusal(
                "vendored_reference_malformed",
                f"vendored_references[{index}].alias must be a non-empty string",
                f"{location_prefix}.content.vendored_references[{index}].alias",
            )
        if not isinstance(path, str) or not path.strip():
            raise PluginSkillRefusal(
                "vendored_reference_malformed",
                f"vendored_references[{index}].path must be a non-empty string",
                f"{location_prefix}.content.vendored_references[{index}].path",
            )
        if alias in seen_aliases:
            raise PluginSkillRefusal(
                "vendored_reference_malformed",
                f"vendored_references[{index}].alias {alias!r} duplicates index {seen_aliases[alias]}",
                f"{location_prefix}.content.vendored_references[{index}].alias",
            )
        seen_aliases[alias] = index
        # Inside-tree check: path must resolve UNDER plugin_root_abs.
        # Empty path or any ".." segment or an absolute path is refused.
        # We resolve to an absolute path via os-independent path joining
        # (Path.resolve() follows symlinks; we don't want symlink-flip
        # to flip a refused path into an accepted one, so we ALSO check
        # the literal path string).
        path_obj = Path(path)
        if path_obj.is_absolute():
            raise PluginSkillRefusal(
                "vendored_reference_outside_tree",
                f"vendored_references[{index}].path {path!r} is an absolute path; "
                "paths must be relative to the plugin tree root (CON §6)",
                f"{location_prefix}.content.vendored_references[{index}].path",
            )
        if ".." in path_obj.parts:
            raise PluginSkillRefusal(
                "vendored_reference_outside_tree",
                f"vendored_references[{index}].path {path!r} contains '..'; "
                "traversal outside the plugin tree is refused (CON §6)",
                f"{location_prefix}.content.vendored_references[{index}].path",
            )
        resolved = (plugin_root / path_obj).resolve()
        # Belt-and-braces: the resolved path must still live under
        # plugin_root_abs even if symlink resolution flipped it (defense
        # in depth — the explicit ".." + absolute checks above are the
        # primary guards; this catches the rarer "non-.. relative path
        # that ends up resolved outside via a symlink" case).
        try:
            resolved.relative_to(plugin_root_abs)
        except ValueError as exc:
            raise PluginSkillRefusal(
                "vendored_reference_outside_tree",
                f"vendored_references[{index}].path {path!r} resolves to {resolved}, "
                f"which is outside the plugin tree root {plugin_root_abs} "
                "(CON §6: traversal outside the tree is refused)",
                f"{location_prefix}.content.vendored_references[{index}].path",
            ) from exc
        if not resolved.exists():
            raise PluginSkillRefusal(
                "vendored_reference_unresolved",
                f"vendored_references[{index}].path {path!r} does not exist "
                f"(resolved to {resolved}); vendored_references MUST resolve at load (CON §6)",
                f"{location_prefix}.content.vendored_references[{index}].path",
            )
        resolved_refs.append(VendoredReference(alias=alias, path=path, resolved=resolved))

    # -- consumption ------------------------------------------------------------
    consumption = doc.get("consumption")
    if not isinstance(consumption, Mapping) or not consumption:
        raise PluginSkillRefusal(
            "consumption_missing",
            "consumption is required and must be a non-empty mapping (CON §6)",
            f"{location_prefix}.consumption",
        )
    consumers = consumption.get("by")
    if not isinstance(consumers, list) or not consumers:
        raise PluginSkillRefusal(
            "consumption_by_empty",
            "consumption.by must be a non-empty list of named consumer strings "
            "(CON §6: empty consumer list is refused)",
            f"{location_prefix}.consumption.by",
        )
    cleaned: List[str] = []
    for index, entry in enumerate(consumers):
        if not isinstance(entry, str) or not entry.strip():
            raise PluginSkillRefusal(
                "consumption_by_empty",
                f"consumption.by[{index}] must be a non-empty string (named consumer)",
                f"{location_prefix}.consumption.by[{index}]",
            )
        stripped = entry.strip()
        if stripped == "*" or "*" in stripped.split("/"):
            # "*" alone OR any segment that is a bare "*" is anonymous
            # and refused (CON §6: "anonymous ['*'] refused (fail closed,
            # tested)").  Path-shape strings like "worker/*" are equally
            # anonymous — a globbed consumer is not a NAMED consumer.
            raise PluginSkillRefusal(
                "consumption_by_anonymous",
                f"consumption.by[{index}] {entry!r} is anonymous/globbed; "
                "CON §6 requires NAMED consumers (anonymous ['*'] refused, fail closed)",
                f"{location_prefix}.consumption.by[{index}]",
            )
        cleaned.append(stripped)
    # Duplicate consumer entries are a no-op semantically; we keep them
    # in order (no dedup) so the audit surface mirrors the YAML.

    return PluginSkill(
        skill_id=skill_id,
        schema_version=version,
        plugin_name=ref_name,
        plugin_manifest_schema_version=ref_manifest_schema,
        upstream_tag=dict(plugin_ref["upstream_tag"]),
        license=ref_license,
        description=description,
        body_markdown=body_markdown,
        vendored_references=tuple(resolved_refs),
        consumers=tuple(cleaned),
        source_path=source_path,
    )


# ─── file API ─────────────────────────────────────────────────────────────────


def list_skill_files(skills_dir: Path) -> List[Path]:
    """List the YAML skill files in ``skills_dir`` (sorted, deterministic)."""
    if not skills_dir.is_dir():
        return []
    out: List[Path] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() not in (".yaml", ".yml"):
            continue
        out.append(child)
    return out


def read_skill_file(
    skill_file: Path,
    *,
    plugin_root: Path,
    plugin_name: str,
    plugin_license: str,
) -> PluginSkill:
    """Read + validate one skill YAML at ``skill_file``.

    Args:
        skill_file: Absolute path to the skill YAML.
        plugin_root: The parent plugin tree root (used for
            ``vendored_references`` resolution).
        plugin_name: The parent plugin's ``plugin.name`` (kebab; must
            equal the skill's ``plugin_ref.name``).
        plugin_license: The parent plugin's ``plugin.license`` (SPDX id).

    Returns:
        A validated :class:`PluginSkill` (see :func:`validate_skill_doc`).

    Raises:
        PluginSkillRefusal: any file-level gate (missing / size /
            unparseable) or document-level check fails.
    """
    skill_file = Path(skill_file)
    if not skill_file.is_file():
        raise PluginSkillRefusal(
            "skill_missing",
            f"skill file not found: {skill_file}",
            str(skill_file),
        )
    size = skill_file.stat().st_size
    if size > SKILL_SIZE_CAP_BYTES:
        raise PluginSkillRefusal(
            "skill_size_exceeded",
            f"skill file is {size} bytes; hard-cap is {SKILL_SIZE_CAP_BYTES}",
            str(skill_file),
        )
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - rare I/O
        raise PluginSkillRefusal(
            "skill_unparseable",
            f"could not read skill file: {exc}",
            str(skill_file),
        ) from exc
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PluginSkillRefusal(
            "skill_unparseable",
            f"invalid YAML in {skill_file.name}: {exc}",
            str(skill_file),
        ) from exc
    if not isinstance(doc, Mapping):
        raise PluginSkillRefusal(
            "skill_unparseable",
            f"skill file {skill_file.name} root must be a mapping",
            str(skill_file),
        )
    return validate_skill_doc(
        doc,
        plugin_root=plugin_root,
        plugin_name=plugin_name,
        plugin_license=plugin_license,
        source_path=skill_file,
    )
