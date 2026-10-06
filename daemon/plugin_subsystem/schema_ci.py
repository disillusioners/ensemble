"""Schema-CI entry point — manifest validation for test-pack / promote wiring
(REC §1.2 component 18, manifests portion).

Exposes manifest validation as a JSON-serializable report so test packs and
the promote gate can consume it without importing the reader directly.
Port-definition CI (JSON-serializability negative tests) is slice ⑤ —
deferred here by design.

Usage:

    from daemon.plugin_subsystem.schema_ci import run_ci, validate_plugin_dir

    report = run_ci(Path("plugins"))            # aggregate over a plugins root
    report = validate_plugin_dir(Path("plugins/opendesign"))

The thin ``plugins-convention/ci_runner.py`` CLI wraps ``run_ci`` for shell
wiring (stdlib-only wrapper; all logic lives here).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from daemon.plugin_subsystem.manifest_reader import validate_manifest

__all__ = ["validate_plugin_dir", "run_ci", "main"]


def validate_plugin_dir(plugin_dir: Path) -> Dict[str, Any]:
    """Validate one plugin dir; returns a JSON-serializable report."""
    plugin_dir = Path(plugin_dir)
    result = validate_manifest(plugin_dir)
    report: Dict[str, Any] = {
        "plugin": plugin_dir.name,
        "manifest": str(plugin_dir / "MANIFEST.yaml"),
        "ok": result.ok,
    }
    if result.ok and result.declaration is not None:
        declaration = result.declaration
        report["declaration"] = {
            "name": declaration.name,
            "license": declaration.license,
            "integration_path": declaration.integration_path,
            "execution_mode": declaration.execution_mode,
            "upstream_repo": declaration.upstream_repo,
            "schema_version": declaration.schema_version,
        }
    if result.refusal is not None:
        report["refusal"] = result.refusal.as_dict()
    return report


def run_ci(plugins_root: Path) -> Dict[str, Any]:
    """Validate every plugin dir under ``plugins_root`` (one level deep).

    A directory counts as a plugin dir if it exists; missing manifests are
    reported as per-plugin refusals (``manifest_missing``), not skipped —
    fail-closed by construction. Returns an aggregate report.
    """
    plugins_root = Path(plugins_root)
    plugin_dirs = sorted(d for d in plugins_root.iterdir() if d.is_dir()) if plugins_root.is_dir() else []
    plugin_reports = [validate_plugin_dir(d) for d in plugin_dirs]
    failed = [r for r in plugin_reports if not r["ok"]]
    return {
        "plugins_root": str(plugins_root),
        "checked": len(plugin_reports),
        "passed": len(plugin_reports) - len(failed),
        "failed": len(failed),
        "ok": not failed,
        "plugins": plugin_reports,
    }


def main(argv: List[str] | None = None) -> int:
    """CLI entry: ``python -m daemon.plugin_subsystem.schema_ci <plugins_root>``."""
    parser = argparse.ArgumentParser(
        prog="schema_ci", description="Plugin manifest vocabulary v1 — schema CI check"
    )
    parser.add_argument("plugins_root", help="directory containing plugin trees (e.g. plugins/)")
    args = parser.parse_args(argv)
    report = run_ci(Path(args.plugins_root))
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    raise SystemExit(main())
