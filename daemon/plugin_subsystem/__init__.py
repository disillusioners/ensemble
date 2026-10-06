"""Plugin subsystem (tier 2) — manifest vocabulary zone.

The ONLY package inside the daemon where manifest vocabulary
(``execution_mode`` / ``lifted_symbol`` / ``ipc_version`` /
``hosted_runtime_deps``) may appear (CON §7 vocabulary-confinement sentinel).
Tier-1 modules stay structurally blind to plugin internals.

Slice scope (REC §4.3):

- ① manifest vocab v1 — declaration dataclass, manifest reader, path-type
  registry, schema-CI manifests entry point.
- ② vendoring tool + plugin_registry + 388-file copy-freely set @ pinned
  tag (od_vendor.py) + carry-forwards (1)-(6).
- ③ sync-runner MVP — vendoring_classifier (comp 5), sync_runner (comp 6),
  full 3-class manifest for plugins/opendesign, §8.5 second-tag clean-pull
  dry-run, trigger-engine payload probe.

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
from daemon.plugin_subsystem.sync_runner import (
    DEFAULT_GIT_REMOTE_NAME,
    LOCALLY_OWNED_FILENAMES,
    DiffSummary,
    DriftAlarm,
    LocalGitCheckout,
    SyncRefusal,
    SyncResult,
    SyncRunner,
    UpstreamGit,
    build_drift_event_payload,
    emit_drift_event,
    sync,
)
from daemon.plugin_subsystem.vendoring_classifier import (
    ClassifiedFor,
    ClassificationRefusal,
    ROUTING_LADDER_STEPS,
    classify,
)

__all__ = [
    "ALARM_THRESHOLD",
    "DEFAULT_GIT_REMOTE_NAME",
    "DEFAULT_PLUGINS_ROOT",
    "ClassifiedFor",
    "ClassificationRefusal",
    "DiffSummary",
    "DriftAlarm",
    "EntrypointCheckResult",
    "LOCALLY_OWNED_FILENAMES",
    "LocalGitCheckout",
    "MANIFEST_FILENAME",
    "MANIFEST_SIZE_CAP_BYTES",
    "ManifestRefusal",
    "ManifestValidation",
    "PathTypeRegistry",
    "PathTypeRegistryError",
    "PluginDeclaration",
    "PluginRegistry",
    "REFUSE_THRESHOLD",
    "ROUTING_LADDER_STEPS",
    "SkeletonViolation",
    "SyncRefusal",
    "SyncResult",
    "SyncRunner",
    "UpstreamGit",
    "build_drift_event_payload",
    "check_entrypoint",
    "classify",
    "emit_drift_event",
    "load_default_registry",
    "load_registry",
    "read_manifest",
    "run_ci",
    "run_entrypoint_tripwire",
    "scan_plugins_root",
    "sync",
    "validate_manifest",
    "validate_plugin_dir",
]
