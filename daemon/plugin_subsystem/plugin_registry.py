"""Plugin registry — boot-scan map (REC §1.2 component 3; built in ①②).

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
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Sequence, Tuple

from daemon.plugin_subsystem.manifest_reader import (
    MANIFEST_FILENAME,
    ManifestRefusal,
    PluginDeclaration,
    read_manifest,
)
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration

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
    """Immutable lookup table over validated plugin declarations.

    Build via :func:`load_registry` or :func:`scan_plugins_root` — direct
    construction is supported (tests use it for fixture injection) but the
    scanning factories are the supported entry points for production use.

    The registry is split into two surfaces:

    - ``declarations`` — name → :class:`PluginDeclaration` for plugins whose
      ``MANIFEST.yaml`` validated cleanly. Lookup is here.
    - ``refusals`` — name → tuple of :class:`ManifestRefusal` /
      :class:`SkeletonViolation` for plugins that failed validation. The
      registry never silently drops a failure; tests and CI iterate
      ``all_refusals()`` to assert fail-closed behaviour.
    """

    def __init__(
        self,
        declarations: Mapping[str, PluginDeclaration],
        refusals: Mapping[str, Sequence[Exception]],
    ) -> None:
        # Frozen copies: the registry is immutable; callers that want a
        # fresh scan call ``discover()``.
        self._declarations: Dict[str, PluginDeclaration] = dict(declarations)
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

    # -- refusal surface -------------------------------------------------------

    def refusals(self, name: str) -> Tuple[Exception, ...]:
        """All refusals (manifest reader + skeleton) recorded for ``name``."""
        return self._refusals.get(name, ())

    def all_refusals(self) -> Mapping[str, Tuple[Exception, ...]]:
        """A read-only view of all per-plugin refusals (plugin name → tuple)."""
        return dict(self._refusals)

    def refused_names(self) -> Tuple[str, ...]:
        """Sorted tuple of plugin names whose manifest/skeleton did not pass."""
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
            "refusals": {
                name: [_refusal_to_dict(r) for r in refs]
                for name, refs in sorted(self._refusals.items())
            },
        }


def _refusal_to_dict(refusal: Exception) -> Dict[str, Any]:
    """Normalize a refusal (manifest reader + skeleton) to a JSON dict."""
    if isinstance(refusal, ManifestRefusal):
        return {"code": refusal.code, "message": refusal.message, "location": refusal.location}
    if isinstance(refusal, SkeletonViolation):
        return {
            "code": refusal.code,
            "message": refusal.message,
            "location": refusal.location,
        }
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
    - ``MANIFEST.yaml`` lives at the plugin root: name is enforced by
      the slice-① manifest reader (MANIFEST_FILENAME constant).  The
      PLUGIN_AUTHORIZED_DATA_FILENAMES carve-out's plugin-ROOT depth
      boundary is enforced by the W4 sentinel
      ``tests/unit/plugin_subsystem/test_sentinels.py::
      TestAuthorizedDataInstanceCarveoutIsPluginRootOnly``, NOT by
      a scan-time check here — the carve-out's depth-bound is a
      vocabulary-confinement property, not a layout invariant, so
      it lives with the vocabulary sentinels (slice ③ carry-forward 4).
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


# -- scan / load factories --------------------------------------------------------


def scan_plugins_root(
    plugins_root: Path,
    *,
    validate_tree: bool = True,
) -> Tuple[Dict[str, PluginDeclaration], Dict[str, Tuple[Exception, ...]]]:
    """Scan ``plugins_root`` (one level deep) and return (declarations, refusals).

    Each immediate child directory is treated as a plugin tree; the
    scan invokes the slice-① manifest reader for the manifest and the
    registry's own skeleton checks.  A plugin whose manifest fails
    validation is NOT silently dropped — it lands in ``refusals``.
    Missing manifests also land in ``refusals`` (consistent with the
    schema-CI runner's fail-closed behaviour).
    """
    plugins_root = Path(plugins_root)
    declarations: Dict[str, PluginDeclaration] = {}
    refusals: Dict[str, Tuple[Exception, ...]] = {}

    if not plugins_root.is_dir():
        # An absent plugins root is itself a registry condition; we return
        # an empty registry rather than raise so callers (CI / test packs)
        # can distinguish "no plugins/ yet" from "scan failed".
        return declarations, refusals

    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        collected: List[Exception] = []

        # Skeleton (layout) checks first — these fail independently of any
        # manifest content and are surfaced with dedicated codes.
        for violation in _check_skeleton(child):
            collected.append(violation)

        # Manifest read — wrap any refusal so the registry can record it.
        try:
            declarations[name] = read_manifest(child, validate_tree=validate_tree)
        except ManifestRefusal as exc:
            collected.append(exc)
        except Exception as exc:  # noqa: BLE001 - other I/O / parse errors land here
            collected.append(
                ManifestRefusal(
                    code="manifest_unreadable",
                    message=f"unexpected error reading manifest: {exc}",
                    location=str(child / MANIFEST_FILENAME),
                )
            )

        if collected:
            refusals[name] = tuple(collected)

    return declarations, refusals


def load_registry(plugins_root: Path, *, validate_tree: bool = True) -> PluginRegistry:
    """Build a :class:`PluginRegistry` from ``plugins_root``."""
    declarations, refusals = scan_plugins_root(plugins_root, validate_tree=validate_tree)
    return PluginRegistry(declarations, refusals)
