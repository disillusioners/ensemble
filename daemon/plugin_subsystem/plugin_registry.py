"""Plugin registry — boot-scan map (REC §1.2 component 3; built in ①②④).

DATA discovery/registration for the plugin subsystem.  Scans a plugins root
directory, reads each immediate child dir's ``MANIFEST.yaml`` via the
slice-① manifest reader, and exposes lookup-by-name over the validated
declarations.  Refusals (per-plugin validation failures) are captured but
do not break the registry — the registry is fail-closed at the
declaration surface, not at the registration surface.

**Sentinels (mirrors ``test_sentinels``).**  No ``importlib``,
no entry-point scanning, no runtime plugin code loads here.  The registry
is plain directory scanning + the slice-① manifest reader.  Skeleton
conventions enforced here are the CON §1 layout invariants; per-class
content rules live in the manifest reader.

**Slice ④ addition (CON §6).**  The registry also reads each plugin's
declared skill files (``plugins/<name>/skills/<skill_id>.yaml``) and
produces validated :class:`PluginSkill` objects that consumers can
query by skill ID.  A skill-file validation failure is recorded as a
refusal on the parent plugin — the registry never silently drops a
failure.  The skill content is DATA: no plugin code is imported, no
plugin classes are instantiated, no entry points are scanned.  The
lookup API is :meth:`PluginRegistry.iter_skills` (all skills across
all plugins) and :meth:`PluginRegistry.get_skill` (by id).

**Scope discipline (slice ②).**  Boot-scan wiring at ``daemon/manager.py``
boot is a tier-1 touch that REC §1.2 does NOT explicitly assign to slice
② (only "Built in ①②" without specifying which slice within the pair) —
per the dispatch's default posture, the registry is implemented as a
standalone module with explicit calls from tests/CI; any manager.py
import is deferred to slice ③ and FLAGGED in the report.  Lookup-by-port
is a port-registry concern (slice ⑤); the manifest's ``integration_path``
is a path letter, not a Port ID, so no by-port stub is added here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from daemon.plugin_subsystem.manifest_reader import (
    MANIFEST_FILENAME,
    ManifestRefusal,
    read_manifest,
)
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration
from daemon.plugin_subsystem.plugin_skill import (
    PluginSkill,
    PluginSkillRefusal,
    read_skill_file,
)

__all__ = [
    "PluginRegistry",
    "SkeletonViolation",
    "DEFAULT_PLUGINS_ROOT",
    "scan_plugins_root",
    "load_registry",
]


# Default plugins root = <repo-root>/plugins/ (CON §1: "plugins/<name>/").
DEFAULT_PLUGINS_ROOT: Path = Path(__file__).resolve().parents[2] / "plugins"


# CON §1 vocabulary of optional class subtrees. The reader owns the
# required-when-X rules (adapter/, alarm_owner on classes); the registry
# owns the LAYOUT invariants (each subtree, if present, must be a dir).
_CLASS_SUBDIRS: Tuple[str, ...] = (
    "copy_freely",
    "snapshot_with_drift_alarm",
    "own_outright",
    "adapter",
    "skills",
)


# Skeleton violations — small, structured, fail-closed at the registry
# surface. The slice-① manifest reader owns the deeper semantic checks.


@dataclass(frozen=True)
class SkeletonViolation(Exception):
    """A CON §1 layout violation discovered at scan time.

    The registry records these per plugin (and raises them out of
    ``assert_skeleton_clean()`` for CI wiring) but does NOT include them
    in the lookup table. Each violation has a stable ``code`` so the
    promote-gate or CI can filter.
    """

    code: str
    message: str
    plugin: str
    location: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        loc = f" (at {self.location})" if self.location else ""
        return f"[{self.code}] {self.message} (plugin {self.plugin!r}){loc}"


class PluginRegistry:
    """Immutable lookup table over validated plugin declarations + skills.

    Build via :func:`load_registry` or :func:`scan_plugins_root` — direct
    construction is supported (tests use it for fixture injection) but the
    scanning factories are the supported entry points for production use.

    The registry is split into two surfaces:

    - ``declarations`` — name → :class:`PluginDeclaration` for plugins whose
      ``MANIFEST.yaml`` validated cleanly. Lookup is here.
    - ``skills`` — skill-id → :class:`PluginSkill` for every plugin-skill
      whose YAML validated cleanly across all loaded plugins (CON §6).
    - ``refusals`` — name → tuple of :class:`ManifestRefusal` /
      :class:`PluginSkillRefusal` / :class:`SkeletonViolation` for plugins
      or skills that failed validation. The registry never silently drops
      a failure; tests and CI iterate ``all_refusals()`` to assert
      fail-closed behaviour.
    """

    def __init__(
        self,
        declarations: Mapping[str, PluginDeclaration],
        skills: Mapping[str, PluginSkill],
        refusals: Mapping[str, Sequence[Exception]],
    ) -> None:
        # Frozen copies: the registry is immutable; callers that want a
        # fresh scan call ``discover()``.
        self._declarations: Dict[str, PluginDeclaration] = dict(declarations)
        self._skills: Dict[str, PluginSkill] = dict(skills)
        self._refusals: Dict[str, Tuple[Exception, ...]] = {
            name: tuple(refs) for name, refs in refusals.items()
        }

    # -- lookup ----------------------------------------------------------------

    def get(self, name: str) -> PluginDeclaration:
        """Lookup a validated plugin declaration by name (kebab)."""
        try:
            return self._declarations[name]
        except KeyError as exc:
            raise KeyError(f"plugin {name!r} not found in registry") from exc

    def try_get(self, name: str) -> PluginDeclaration | None:
        """Lookup-or-None helper for tests and CLI wiring."""
        return self._declarations.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._declarations

    def __len__(self) -> int:
        return len(self._declarations)

    def names(self) -> Tuple[str, ...]:
        """Sorted tuple of all valid plugin names."""
        return tuple(sorted(self._declarations))

    # -- skill lookup (CON §6; slice ④) -----------------------------------------

    def get_skill(self, skill_id: str) -> PluginSkill:
        """Lookup a validated plugin-skill by ID (CON §6)."""
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise KeyError(f"plugin-skill {skill_id!r} not found in registry") from exc

    def try_get_skill(self, skill_id: str) -> PluginSkill | None:
        """Lookup-or-None helper for skill queries."""
        return self._skills.get(skill_id)

    def iter_skills(self) -> Tuple[PluginSkill, ...]:
        """Tuple of all validated plugin-skills, sorted by ``skill_id``."""
        return tuple(self._skills[skill_id] for skill_id in sorted(self._skills))

    def skill_ids(self) -> Tuple[str, ...]:
        """Sorted tuple of every registered plugin-skill ID."""
        return tuple(sorted(self._skills))

    # -- refusal surface -------------------------------------------------------

    def refusals(self, name: str) -> Tuple[Exception, ...]:
        """All refusals (manifest reader + skeleton + skill) recorded for ``name``."""
        return self._refusals.get(name, ())

    def all_refusals(self) -> Mapping[str, Tuple[Exception, ...]]:
        """A read-only view of all per-plugin refusals (plugin name → tuple)."""
        return dict(self._refusals)

    def refused_names(self) -> Tuple[str, ...]:
        """Sorted tuple of plugin names whose manifest/skeleton/skills did not pass."""
        return tuple(sorted(self._refusals))

    def has_failures(self) -> bool:
        return bool(self._refusals)

    def assert_skeleton_clean(self) -> None:
        """Raise the first refusal across all plugins (CI gate)."""
        for name in sorted(self._refusals):
            refs = self._refusals[name]
            if refs:
                raise refs[0]

    # -- report ----------------------------------------------------------------

    def as_dict(self) -> Dict[str, Any]:
        return {
            "declarations": {
                name: {
                    "name": decl.name,
                    "license": decl.license,
                    "integration_path": decl.integration_path,
                    "execution_mode": decl.execution_mode,
                    "schema_version": decl.schema_version,
                    "source_dir": str(decl.source_dir) if decl.source_dir else None,
                }
                for name, decl in sorted(self._declarations.items())
            },
            "skills": {
                sid: {
                    "skill_id": sk.skill_id,
                    "plugin_name": sk.plugin_name,
                    "schema_version": sk.schema_version,
                    "upstream_tag": dict(sk.upstream_tag),
                    "license": sk.license,
                    "vendored_references": [
                        {"alias": r.alias, "path": r.path} for r in sk.vendored_references
                    ],
                    "consumers": list(sk.consumers),
                }
                for sid, sk in sorted(self._skills.items())
            },
            "refusals": {
                name: [_refusal_to_dict(r) for r in refs]
                for name, refs in sorted(self._refusals.items())
            },
        }


def _refusal_to_dict(refusal: Exception) -> Dict[str, Any]:
    """Normalize a refusal (manifest reader + skeleton + skill) to a JSON dict."""
    if isinstance(refusal, ManifestRefusal):
        return {"code": refusal.code, "message": refusal.message, "location": refusal.location}
    if isinstance(refusal, SkeletonViolation):
        return {
            "code": refusal.code,
            "message": refusal.message,
            "location": refusal.location,
        }
    if isinstance(refusal, PluginSkillRefusal):
        return {"code": refusal.code, "message": refusal.message, "location": refusal.location}
    return {"code": "unknown", "message": str(refusal), "location": ""}


# -- skeleton conventions (CON §1) --------------------------------------------------


def _check_skeleton(plugin_dir: Path) -> List[SkeletonViolation]:
    """Run the CON §1 skeleton checks that the slice-① reader does not own.

    The reader's tree-level checks cover ``adapter_missing`` and
    ``copy_freely_symlink`` (semantic checks against the manifest content).
    The registry's skeleton checks cover the layout invariants that
    apply independent of any specific manifest entry:

    - Each class subdir (copy_freely / snapshot_with_drift_alarm /
      own_outright / adapter / skills), if present, must be a DIRECTORY
      (not a file, not a symlink) — symlinks inside the class subtrees
      are a separate concern owned by the reader.
    - No symlinks at the plugin root (CON §1 "no symlinks inside
      copy_freely/" is the canonical example; we generalize to the root
      to keep the rule trivially expressible at scan time).
    - ``MANIFEST.yaml`` lives at the plugin root (the reader enforces the
      name via MANIFEST_FILENAME; the registry double-checks the location
      to surface "manifest nested under copy_freely/" style mistakes at
      scan time, before the reader is even invoked).
    """
    violations: List[SkeletonViolation] = []

    # Layout: each class subdir, if present, is a real directory.
    for subdir in _CLASS_SUBDIRS:
        target = plugin_dir / subdir
        if not target.exists():
            continue
        if target.is_symlink():
            violations.append(
                SkeletonViolation(
                    code="class_subdir_is_symlink",
                    message=f"{subdir!r} must be a directory, not a symlink (CON §1)",
                    plugin=plugin_dir.name,
                    location=subdir,
                )
            )
            continue
        if not target.is_dir():
            violations.append(
                SkeletonViolation(
                    code="class_subdir_not_a_directory",
                    message=f"{subdir!r} must be a directory (CON §1)",
                    plugin=plugin_dir.name,
                    location=subdir,
                )
            )

    # Root-level symlinks: banned by CON §1. We probe the immediate
    # children of the plugin root (the manifest file, class subdirs, and
    # any other top-level entry); symlinks are refused.
    if plugin_dir.is_dir():
        for child in plugin_dir.iterdir():
            if child.is_symlink():
                violations.append(
                    SkeletonViolation(
                        code="root_symlink",
                        message=f"symlink at plugin root {child.name!r} is forbidden (CON §1)",
                        plugin=plugin_dir.name,
                        location=child.name,
                    )
                )

    return violations


# -- per-plugin skill loading (CON §6; slice ④) ------------------------------------


def _load_plugin_skills(
    declaration: PluginDeclaration,
    plugin_dir: Path,
) -> Tuple[Dict[str, PluginSkill], Tuple[PluginSkillRefusal, ...]]:
    """Load and validate the plugin-skills declared by ``declaration``.

    Iterates ``declaration.manifest_skills_entries`` (the manifest's
    declarative list).  For each entry, calls
    :func:`read_skill_file` which enforces the CON §6 contract
    (plugin_ref, vendored_references, consumption.by).

    Returns ``(skills_by_id, refusals)``.  Refusals NEVER abort the load
    loop — the registry records them per plugin so CI / tests iterate
    ``all_refusals()`` and the user sees the full set in one report.

    Per-plugin skill-id duplicates (the manifest declaring the same
    skill_id twice in one plugin) are caught by ``read_skill_file``
    indirectly via the alias-uniqueness check; explicit duplicate-id
    checks live in the cross-plugin pass in :func:`scan_plugins_root`.
    """
    skills: Dict[str, PluginSkill] = {}
    refusals: List[PluginSkillRefusal] = []

    for index, entry in enumerate(declaration.manifest_skills_entries):
        skill_id = entry.get("skill_id", "")
        rel_path = entry.get("path", "")
        skill_file = plugin_dir / rel_path
        try:
            skill = read_skill_file(
                skill_file,
                plugin_root=plugin_dir,
                plugin_name=declaration.name,
                plugin_license=declaration.license,
                # W1: pass the parent manifest's schema_version + tag
                # pin set so the loader can refuse a skill that lies
                # about its data version (council-probed v9.9.9 under
                # v1.0.0 used to load clean; this closes that gap).
                parent_schema_version=declaration.schema_version,
                parent_tag_pins=dict(declaration.tag_pin_per_class),
            )
        except PluginSkillRefusal as exc:
            refusals.append(exc)
            continue
        skills[skill.skill_id] = skill

    return skills, tuple(refusals)


# -- scan / load factories --------------------------------------------------------


def _resolve_plugin(
    plugin_dir: Path,
    *,
    validate_tree: bool,
) -> Tuple[Optional[PluginDeclaration], List[Exception]]:
    """Run skeleton + manifest validation for one plugin; return the
    declaration (or None on refusal) and the full list of refusals
    (skeleton + manifest).

    Splitting this out from the scan loop lets the cross-plugin
    skill-id-collision pass build on a clean per-plugin record without
    re-running the manifest reader (a duplicate read would double-count
    schema-version-mismatch refusals, etc.).  The function is private —
    the public surface is :func:`scan_plugins_root`.
    """
    collected: List[Exception] = []

    # Skeleton (layout) checks first — these fail independently of any
    # manifest content and are surfaced with dedicated codes.
    for violation in _check_skeleton(plugin_dir):
        collected.append(violation)

    # Manifest read.
    try:
        declaration = read_manifest(plugin_dir, validate_tree=validate_tree)
    except ManifestRefusal as exc:
        collected.append(exc)
        return None, collected
    except Exception as exc:  # noqa: BLE001 - other I/O / parse errors land here
        collected.append(
            ManifestRefusal(
                code="manifest_unreadable",
                message=f"unexpected error reading manifest: {exc}",
                location=str(plugin_dir / MANIFEST_FILENAME),
            )
        )
        return None, collected

    return declaration, collected


def scan_plugins_root(
    plugins_root: Path,
    *,
    validate_tree: bool = True,
) -> Tuple[Dict[str, PluginDeclaration], Dict[str, PluginSkill], Dict[str, Tuple[Exception, ...]]]:
    """Scan ``plugins_root`` (one level deep) and return (declarations, skills, refusals).

    Each immediate child directory is treated as a plugin tree; the
    scan invokes the slice-① manifest reader for the manifest, the
    registry's own skeleton checks, AND the slice-④ skill loader for
    each declared plugin-skill.  A plugin whose manifest or skills fail
    validation is NOT silently dropped — the per-plugin refusal list
    captures every failure for the CI / operator report.

    Returns three dicts:

    - ``declarations`` — plugin-name → :class:`PluginDeclaration` for
      plugins whose manifest validated cleanly.  Skill files may still
      fail in a plugin whose manifest passed; the manifest pass is
      independent of the skill pass.
    - ``skills`` — skill-id → :class:`PluginSkill` for every validated
      plugin-skill across all plugins.  Skill IDs are global (a skill-id
      is a stable consumer-facing handle); duplicates across plugins
      are refused and recorded under BOTH plugins so the report is
      symmetric.
    - ``refusals`` — plugin-name → tuple of refusal exceptions
      (manifest / skeleton / skill).  A clean scan produces empty dicts.
    """
    plugins_root = Path(plugins_root)
    declarations: Dict[str, PluginDeclaration] = {}
    skills_by_id: Dict[str, PluginSkill] = {}
    refusals: Dict[str, Tuple[Exception, ...]] = {}

    if not plugins_root.is_dir():
        # An absent plugins root is itself a registry condition; we return
        # an empty registry rather than raise so callers (CI / test packs)
        # can distinguish "no plugins/ yet" from "scan failed".
        return declarations, skills_by_id, refusals

    # First pass: per-plugin skeleton + manifest + skill validation.
    # We collect the per-plugin records in a list so the second pass
    # (cross-plugin skill-id collision detection) can iterate without
    # re-running any I/O.
    per_plugin_records: List[Tuple[str, Path, Optional[PluginDeclaration], List[Exception], Dict[str, PluginSkill]]] = []
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        declaration, collected = _resolve_plugin(child, validate_tree=validate_tree)
        if declaration is not None:
            plugin_skills, skill_refusals = _load_plugin_skills(declaration, child)
            for sr in skill_refusals:
                collected.append(sr)
        else:
            plugin_skills = {}
        per_plugin_records.append((name, child, declaration, collected, plugin_skills))

    # Second pass: detect cross-plugin skill-id collisions.  When two
    # plugins declare the same skill_id, both plugins see the SAME
    # refusal code so the report is symmetric (no "winner / loser"
    # asymmetry).  We refuse BOTH the colliding entries (CON §6: skill
    # IDs are global consumer-facing handles; duplicates would silently
    # break the registry's lookup).
    #
    # The pop happens on BOTH plugins' skills dicts: when plugin-b is
    # the iter-current plugin, the prior plugin's record is reached
    # via the per_plugin_records list (looked up by name).  Without
    # this, the "winner" plugin's skill would still land in
    # skills_by_id at the third pass — the registry would surface a
    # half-state where the consumer finds the skill but BOTH plugins
    # carry the collision refusal.
    skill_id_to_plugin: Dict[str, str] = {}
    collision_pairs: Dict[str, List[str]] = {}  # plugin_name -> list of colliding skill_ids
    plugin_skills_by_name: Dict[str, Dict[str, PluginSkill]] = {}
    for name, _, declaration, _collected, plugin_skills in per_plugin_records:
        if declaration is not None:
            plugin_skills_by_name[name] = plugin_skills
    for name, _, declaration, _collected, plugin_skills in per_plugin_records:
        if declaration is None:
            continue
        for sid in list(plugin_skills.keys()):
            prior = skill_id_to_plugin.get(sid)
            if prior is None:
                skill_id_to_plugin[sid] = name
                continue
            # Collision: pop the SID from BOTH plugins' per-plugin
            # skills dicts (the prior one and the current one).  The
            # current one is `plugin_skills`; the prior is reached via
            # plugin_skills_by_name.
            plugin_skills.pop(sid, None)
            prior_skills = plugin_skills_by_name.get(prior, {})
            prior_skills.pop(sid, None)
            collision_pairs.setdefault(prior, []).append(sid)
            collision_pairs.setdefault(name, []).append(sid)

    # Third pass: build the final maps.  At this point every plugin's
    # ``plugin_skills`` is collision-free; the only refusal additions
    # are the per-plugin collision-pair records.
    for name, child, declaration, collected, plugin_skills in per_plugin_records:
        # Surface the cross-plugin collision refusals (one per
        # collision, one per plugin involved).
        if name in collision_pairs:
            for sid in collision_pairs[name]:
                # Find the OTHER plugin involved (the partner in the
                # collision) so the message is symmetric.
                partner = next(
                    (other for other, sids in collision_pairs.items() if sid in sids and other != name),
                    "?",
                )
                collected.append(
                    PluginSkillRefusal(
                        "skills_entry_duplicate",
                        f"plugin-skill {sid!r} is declared by both {name!r} and {partner!r}; "
                        "skill IDs are global consumer-facing handles (CON §6)",
                        "skills.entries",
                    )
                )
        if declaration is not None:
            declarations[name] = declaration
            for sid, skill in plugin_skills.items():
                skills_by_id[sid] = skill
        if collected:
            # Dedup identical refusal exceptions (same type+code+message+location)
            # so the report is stable across re-reads.
            seen = set()
            deduped: List[Exception] = []
            for r in collected:
                key = (type(r).__name__, getattr(r, "code", ""), str(r))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(r)
            refusals[name] = tuple(deduped)

    return declarations, skills_by_id, refusals


def load_registry(plugins_root: Path, *, validate_tree: bool = True) -> PluginRegistry:
    """Build a :class:`PluginRegistry` from ``plugins_root``."""
    declarations, skills, refusals = scan_plugins_root(plugins_root, validate_tree=validate_tree)
    return PluginRegistry(declarations, skills, refusals)
