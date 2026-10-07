"""Thin stdlib-only CLI wrapper around the plugin manifest schema-CI check.

Delegates ALL logic to ``daemon.plugin_subsystem.schema_ci`` (the tier-2
vocabulary zone); this wrapper adds only argv plumbing + exit code so shell
test-packs / promote gates can invoke it without importing Python modules.

The tripwire (slice ② carry-forward 4) lives in
``daemon.plugin_subsystem.entrypoint_tripwire`` and runs by DEFAULT since
the slice-⑤ F1-round-3 fix: plugin #1 (opendesign) is a B-path plugin with
real ``adapter/`` files, so the original default-off rationale ("would
surface missing findings on every C-path plugin we ship at slice ②") no
longer holds — a default-off tripwire let the ≤200-line entrypoint guard
silently rot. Verified before the flip: ``run_tripwire`` returns
``not_applicable`` (not ``missing``) for non-B/non-lifted-symbol plugins,
so C-path plugins stay clean. Invocation modes:

  - default: run schema-CI AND the entrypoint tripwire; tripwire REFUSE
    findings fold into the exit code (alarms remain in the JSON report
    but do NOT fail the run — they are a "block promote after N days"
    signal, not a same-build gate, per CON §2).
  - ``--no-entrypoint-tripwire``: skip the tripwire (schema-CI only).

Usage:
    python plugins-convention/ci_runner.py <plugins_root>
    python plugins-convention/ci_runner.py <plugins_root> --no-entrypoint-tripwire
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# repo root = parent of plugins-convention/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from daemon.plugin_subsystem.entrypoint_tripwire import run_tripwire  # noqa: E402
from daemon.plugin_subsystem.manifest_reader import read_manifest  # noqa: E402
from daemon.plugin_subsystem.plugin_declaration import PluginDeclaration  # noqa: E402
from daemon.plugin_subsystem.schema_ci import run_ci as _schema_ci_run  # noqa: E402
from daemon.plugin_subsystem.schema_ci import validate_ports_report  # noqa: E402


def _discover_declarations(plugins_root: Path) -> dict[str, PluginDeclaration]:
    out: dict[str, PluginDeclaration] = {}
    if not plugins_root.is_dir():
        return out
    for child in sorted(plugins_root.iterdir()):
        if not child.is_dir():
            continue
        try:
            out[child.name] = read_manifest(child)
        except Exception:  # noqa: BLE001 - schema-CI surfaces its own refusals
            continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ci_runner",
        description="Plugin manifest vocabulary v1 — thin CI wrapper (delegates to "
        "daemon.plugin_subsystem.schema_ci)",
    )
    parser.add_argument("plugins_root", help="directory containing plugin trees (e.g. plugins/)")
    parser.add_argument(
        "--no-entrypoint-tripwire",
        action="store_true",
        help="skip the ≤200-line entrypoint tripwire (runs by DEFAULT since the "
        "slice-⑤ fix; pass this only for schema-CI-only invocations)",
    )
    args = parser.parse_args()

    plugins_root = Path(args.plugins_root)
    report = _schema_ci_run(plugins_root)

    if not args.no_entrypoint_tripwire:
        declarations = _discover_declarations(plugins_root)
        tripwire = run_tripwire(declarations)
        report["entrypoint_tripwire"] = tripwire
        # Fold the tripwire's REFUSE findings into the aggregate ok flag.
        # ALARM findings are surfaced but do NOT fail the run (per CON §2:
        # alarm is a "block promote after N days" signal, not a same-build
        # gate).
        report["ok"] = report["ok"] and tripwire["ok_overall"]

    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
