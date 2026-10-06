"""Plugin declaration — typed row per plugin (REC §1.2 component 1).

A plain frozen data carrier mirroring a validated ``MANIFEST.yaml``. No
logic beyond construction helpers: validation lives in
``daemon.plugin_subsystem.manifest_reader``; this module only gives the
validated manifest a typed shape for downstream consumers (registry,
sync-runner, classifier).

Every field maps 1:1 onto the manifest vocabulary
(``plugins-convention/manifest.schema.json``; CON §2). Fields that are
conditional in the vocabulary (B-path / A-path blocks) are ``None`` when
not declared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

__all__ = ["PluginDeclaration"]


@dataclass(frozen=True)
class PluginDeclaration:
    """Validated, typed view of one plugin's ``MANIFEST.yaml``.

    Built exclusively by :func:`daemon.plugin_subsystem.manifest_reader.read_manifest`
    after structural + semantic validation pass.
    """

    # plugin block
    name: str
    license: str
    upstream_repo: str
    tag_pin_per_class: Mapping[str, str]
    integration_path: str  # "B" | "C" | "A"
    execution_mode: str  # "resource-only" | "lifted-symbol" | "hosted-runtime"

    # B-path fields (declared iff execution_mode == "lifted-symbol")
    lifted_symbol: Optional[str] = None
    entrypoint: Optional[str] = None
    ipc_version: Optional[int] = None

    # A-path fields (declared iff execution_mode == "hosted-runtime"; FENCED)
    hosted_runtime_deps: Optional[Mapping[str, Any]] = None
    fence_grant: Optional[Mapping[str, str]] = None

    # provenance classes
    copy_freely: Mapping[str, Any] = field(default_factory=dict)
    snapshot_with_drift_alarm: Mapping[str, Any] = field(default_factory=dict)
    own_outright: Mapping[str, Any] = field(default_factory=dict)

    # parallel upstream subdir mapping (additive, CON §8 1.0.x);
    # when present, the sync-runner uses these instead of the
    # strip-class-prefix heuristic. The mapping is per-class;
    # absent upstream_paths for a class means the heuristic applies.
    upstream_paths_per_class: Mapping[str, Sequence[str]] = field(default_factory=dict)

    # parity boundary (required section; subsections may be empty)
    parity_intentionally_not_vendored: Sequence[Mapping[str, str]] = field(default_factory=tuple)
    parity_not_executed: Sequence[Mapping[str, str]] = field(default_factory=tuple)

    # plugin-skill section (CON §6; slice ④)
    # The manifest only NAMES the skill files; the registry reads each
    # file via plugin_skill.read_skill_file. Empty tuple = no skills
    # declared (or section absent entirely).
    manifest_skills_entries: Sequence[Mapping[str, str]] = field(default_factory=tuple)

    # context
    source_dir: Optional[Path] = None
    schema_version: str = "1.0.0"

    def pin_for_class(self, class_name: str) -> Optional[str]:
        """Tag pin for a provenance class; ``snapshot_with_drift_alarm``
        inherits ``copy_freely``'s pin when absent (CON §2 line 47)."""
        pin = self.tag_pin_per_class.get(class_name)
        if pin is None and class_name == "snapshot_with_drift_alarm":
            pin = self.tag_pin_per_class.get("copy_freely")
        return pin

    def upstream_paths_for_class(self, class_name: str) -> Optional[Sequence[str]]:
        """Parallel upstream-subdir list for ``class_name`` (additive).

        Returns ``None`` when no explicit mapping is declared (the
        sync-runner falls back to the strip-class-prefix heuristic).
        When the list is declared, ``upstream_paths_for_class[i]`` is
        the upstream subtree corresponding to ``paths[i]`` of the
        class section.
        """
        return self.upstream_paths_per_class.get(class_name)

    @property
    def divergence_register(self) -> Sequence[Mapping[str, Any]]:
        return self.snapshot_with_drift_alarm.get("divergence_register", ())  # type: ignore[return-value]

    @property
    def needs_adapter(self) -> bool:
        """True iff the execution mode requires an ``adapter/`` subtree
        (CON §1: required iff lifted-symbol | hosted-runtime)."""
        return self.execution_mode in ("lifted-symbol", "hosted-runtime")

    @property
    def declared_skill_ids(self) -> Sequence[str]:
        """Skill IDs declared in the manifest's ``skills.entries`` (CON §6).
        The actual skill YAML validation happens in the registry scan;
        the manifest's role is the DECLARATIVE enumeration."""
        return tuple(entry.get("skill_id", "") for entry in self.manifest_skills_entries)
