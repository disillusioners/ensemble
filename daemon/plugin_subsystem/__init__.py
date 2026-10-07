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
- ④ plugin-skill consumption — plugin_skill.py module + opendesign.list_systems
  + registration mechanism (CON §6).
- ⑤ port + native generate tool + designer rewire — port_registry (comp 8),
  capability_seam_gate (comp 14), plugin_tool_factory (comp 12), the
  per-capability opendesign B-element (generate / compose_brief / save / lint)
  + port-schema CI wiring + designer rewire.

NO runtime loading anywhere: no importlib, no entry-point scanning
(CON §7 no-runtime-loading sentinel).
"""

from __future__ import annotations

from daemon.plugin_subsystem.capability_seam_gate import (
    SeamGateVerdict,
    is_three_role_complete,
    seam_gate_check,
    seam_gate_check_batch,
)
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
from daemon.plugin_subsystem.opendesign.ports import declared_opendesign_ports
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
from daemon.plugin_subsystem.plugin_skill import (
    SCHEMA_VERSION as PLUGIN_SKILL_SCHEMA_VERSION,
    SKILL_FILE_SUFFIX,
    SKILL_SIZE_CAP_BYTES as PLUGIN_SKILL_SIZE_CAP_BYTES,
    PluginSkill,
    PluginSkillRefusal,
    VendoredReference,
    list_skill_files,
    read_skill_file,
    validate_skill_doc,
)
from daemon.plugin_subsystem.plugin_tool_factory import (
    ADAPTER_REGISTRY,
    build_plugin_tools,
    build_tools_for_port,
    register_adapter,
)
from daemon.plugin_subsystem.port_registry import (
    ANONYMOUS_CONSUMER,
    PORT_ID_PATTERN,
    Port,
    PortRefusal,
    PortRegistry,
    build_default_port_registry,
    validate_port,
    validate_port_serializability,
    validate_ports,
)
from daemon.plugin_subsystem.schema_ci import (
    run_ci,
    validate_plugin_dir,
    validate_ports_report,
)
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
    "ANONYMOUS_CONSUMER",
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
    "PLUGIN_SKILL_SCHEMA_VERSION",
    "PLUGIN_SKILL_SIZE_CAP_BYTES",
    "PORT_ID_PATTERN",
    "ManifestRefusal",
    "ManifestValidation",
    "PathTypeRegistry",
    "PathTypeRegistryError",
    "PluginDeclaration",
    "PluginRegistry",
    "PluginSkill",
    "PluginSkillRefusal",
    "Port",
    "PortRefusal",
    "PortRegistry",
    "REFUSE_THRESHOLD",
    "ROUTING_LADDER_STEPS",
    "SKILL_FILE_SUFFIX",
    "SeamGateVerdict",
    "SkeletonViolation",
    "SyncRefusal",
    "SyncResult",
    "SyncRunner",
    "UpstreamGit",
    "VendoredReference",
    "ADAPTER_REGISTRY",
    "build_default_port_registry",
    "build_drift_event_payload",
    "build_plugin_tools",
    "build_tools_for_port",
    "check_entrypoint",
    "classify",
    "declared_opendesign_ports",
    "emit_drift_event",
    "is_three_role_complete",
    "list_skill_files",
    "load_default_registry",
    "load_registry",
    "read_manifest",
    "read_skill_file",
    "register_adapter",
    "run_ci",
    "run_entrypoint_tripwire",
    "scan_plugins_root",
    "seam_gate_check",
    "seam_gate_check_batch",
    "sync",
    "validate_manifest",
    "validate_plugin_dir",
    "validate_port",
    "validate_port_serializability",
    "validate_ports",
    "validate_ports_report",
    "validate_skill_doc",
]