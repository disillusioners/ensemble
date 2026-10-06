"""Manifest fixture builders for plugin_subsystem tests.

Repo convention: shared helpers live in a sibling module imported via the
package path (``tests.unit.plugin_subsystem._manifest_fixtures``), not in
conftest (see tests/unit/tools/_fakes.py, tests/helpers/).

Builders write plugin trees under ``tmp_path``; the manifest reader takes the
plugin DIR (name/dir-mismatch checks bind to the directory name).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

VALID_MINIMAL_MANIFEST = """\
schema_version: "1.0.0"
plugin:
  name: "{name}"
  license: "Apache-2.0"
  upstream:
    repo: "https://example.com/upstream.git"
    tag_pin_per_class:
      copy_freely: "v1.0.0"
  integration_path: "C"
  execution_mode: "resource-only"
copy_freely:
  paths: ["data/", "presets/"]
  alarm_owner: "worker"
  escalation: "block-promote-after-days"
parity_boundary:
  intentionally_not_vendored:
    - path: "ui/"
      reason: "UI-like surface excluded from backend-mostly plugin population"
  not_executed:
    - path: "index.ts"
      reason: "inert bytes under resource-only"
"""

VALID_B_PATH_MANIFEST = """\
schema_version: "1.0.0"
plugin:
  name: "{name}"
  license: "MIT"
  upstream:
    repo: "https://example.com/upstream.git"
    tag_pin_per_class:
      copy_freely: "v2.1.0"
      snapshot_with_drift_alarm: "v2.1.0"
  integration_path: "B"
  execution_mode: "lifted-symbol"
  lifted_symbol: "generateDesign"
  entrypoint: "adapter/entry.ts"
  ipc_version: 1
snapshot_with_drift_alarm:
  paths: ["prompts/"]
  alarm_owner: "designer-owners"
  divergence_register:
    - id: 1
      files: ["prompts/compose.ts"]
      delta: "local gate nudge added to system prompt"
      rationale: "ensemble-side completeness gate needs the nudge"
      upstream_ref: "PR #42"
      pinning_test: "tests/unit/plugin_subsystem/test_manifest_reader.py"
parity_boundary:
  intentionally_not_vendored: []
  not_executed: []
"""

VALID_A_PATH_MANIFEST = """\
schema_version: "1.0.0"
plugin:
  name: "{name}"
  license: "Apache-2.0"
  upstream:
    repo: "https://example.com/upstream.git"
    tag_pin_per_class:
      copy_freely: "v0.16.1"
  integration_path: "A"
  execution_mode: "hosted-runtime"
  hosted_runtime_deps:
    runtime_pin: "20.11.1"
    profile_id: "sdk-minimal"
    ipc_schema_pin: "1.0.0"
    capability_allowlist: ["od.generate"]
  fence_grant:
    rationale: "engine-only exception ratified for the dsh-style giant"
    granted_by: "user-confirmation-2026-10-06"
    granted_at: "2026-10-06T00:00:00Z"
own_outright:
  paths: ["compose_brief/"]
parity_boundary:
  intentionally_not_vendored: []
  not_executed: []
"""


def build_plugin(
    tmp_path: Path,
    manifest_text: str,
    *,
    name: str = "test-plugin",
    files: Optional[Dict[str, str]] = None,
    symlink_inside_copy_freely: Optional[str] = None,
) -> Path:
    """Materialize a plugin tree: dir + MANIFEST.yaml (+ optional extra files)."""
    plugin_dir = tmp_path / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    if manifest_text is not None:
        (plugin_dir / "MANIFEST.yaml").write_text(manifest_text.format(name=name), encoding="utf-8")
    for rel, content in (files or {}).items():
        target = plugin_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    if symlink_inside_copy_freely is not None:
        link = plugin_dir / symlink_inside_copy_freely
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(plugin_dir / "MANIFEST.yaml")
    return plugin_dir
