"""Plugin subsystem (tier 2) — manifest vocabulary zone.

The ONLY package inside the daemon where manifest vocabulary
(``execution_mode`` / ``lifted_symbol`` / ``ipc_version`` /
``hosted_runtime_deps``) may appear (CON §7 vocabulary-confinement sentinel).
Tier-1 modules stay structurally blind to plugin internals.

Slice ① scope (REC §4.3 row ①): manifest vocab v1 — declaration dataclass,
manifest reader, path-type registry, schema-CI manifests entry point.
NO runtime loading anywhere: no importlib, no entry-point scanning
(CON §7 no-runtime-loading sentinel).
"""

from __future__ import annotations

from daemon.plugin_subsystem.entrypoint_tripwire import (
    ALARM_THRESHOLD,
    REFUSE_THRESHOLD,
    EntrypointCheckResult,
    check_entrypoint,
    run_tripwire as run_entrypoint_tripwire,
)
from daemon.plugin_subsystem.manifest_reader import (
    MANIFEST_FILENAME,
    MANIFEST_SIZE_CAP_BYTES,
    ManifestRefusal,
    ManifestValidation,
    read_manifest,
    validate_manifest,
)
from daemon.plugin_subsystem.path_type_registry import (
    PathTypeRegistry,
    PathTypeRegistryError,
    load_default_registry,
)
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration
from daemon.plugin_subsystem.plugin_registry import (
    DEFAULT_PLUGINS_ROOT,
    PluginRegistry,
    SkeletonViolation,
    load_registry,
    scan_plugins_root,
)
from daemon.plugin_subsystem.schema_ci import run_ci, validate_plugin_dir

__all__ = [
    "ALARM_THRESHOLD",
    "DEFAULT_PLUGINS_ROOT",
    "EntrypointCheckResult",
    "MANIFEST_FILENAME",
    "MANIFEST_SIZE_CAP_BYTES",
    "ManifestRefusal",
    "ManifestValidation",
    "PathTypeRegistry",
    "PathTypeRegistryError",
    "PluginDeclaration",
    "PluginRegistry",
    "REFUSE_THRESHOLD",
    "SkeletonViolation",
    "check_entrypoint",
    "load_default_registry",
    "load_registry",
    "read_manifest",
    "run_ci",
    "run_entrypoint_tripwire",
    "scan_plugins_root",
    "validate_manifest",
    "validate_plugin_dir",
]
