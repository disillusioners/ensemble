"""Schema-CI entry point — manifest + Port validation for test-pack /
promote wiring (REC §1.2 component 18 — manifests at ①, ports at ⑤).

Exposes manifest validation as a JSON-serializable report so test packs and
the promote gate can consume it without importing the reader directly.
Port-definition CI (JSON-serializability negative tests + three-role seam
gate) lands at slice ⑤ alongside the first real Ports.

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

from daemon.plugin_subsystem.capability_seam_gate import (
    is_three_role_complete,
    seam_gate_check,
)
from daemon.plugin_subsystem.manifest_reader import validate_manifest
from daemon.plugin_subsystem.sole_writer_gate import check_sole_writer
from daemon.plugin_subsystem.port_registry import (
    build_default_port_registry,
    validate_ports,
)

__all__ = [
    "validate_plugin_dir",
    "validate_ports_report",
    "run_ci",
    "main",
]


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


def validate_ports_report() -> Dict[str, Any]:
    """Validate the boot-time Port registry (CON §3 negative tests + three-role seam).

    Aggregates over the four opendesign Ports declared at
    :mod:`daemon.plugin_subsystem.opendesign.ports`; the same gate will
    apply to plugin #2+ when declared. Returns a JSON-serializable
    report so the test pack and the promote gate consume it uniformly
    with :func:`validate_plugin_dir`.
    """
    from daemon.plugin_subsystem.opendesign.ports import declared_opendesign_ports

    raw_ports = declared_opendesign_ports()
    ports, refusals = validate_ports(raw_ports, declared_in="plugins/opendesign/MANIFEST.yaml")
    gate_verdicts = [seam_gate_check(p) for p in ports] if ports else []
    # A refusal in validation refuses the entire batch (the adapter is
    # not built). The seam-gate verdict is per-Port and provides a
    # second layer of evidence.
    refused_refusals = [r.as_dict() for r in refusals]
    refused_gates = [
        v.as_dict()
        for v in gate_verdicts
        if not v.ok
    ]
    ok = not refused_refusals and not refused_gates
    return {
        "ok": ok,
        "checked": len(raw_ports),
        "passed": sum(1 for v in gate_verdicts if v.ok) - len(refusals),
        "refused_validation": refused_refusals,
        "refused_seam_gate": refused_gates,
        "ports": [
            {
                "port_id": p.port_id,
                "version": p.version,
                "adapter_id": p.adapter_id,
                "provider_path": p.provider_path,
                "consumers": list(p.consumers),
                "capability_tags": sorted(p.capability_tags),
            }
            for p in ports
        ],
    }


def run_ci(plugins_root: Path, *, sole_writer: bool = True) -> Dict[str, Any]:
    """Validate every plugin dir under ``plugins_root`` (one level deep).

    A directory counts as a plugin dir if it exists; missing manifests are
    reported as per-plugin refusals (``manifest_missing``), not skipped —
    fail-closed by construction. Returns an aggregate report that
    includes the Port-validation sibling step (slice ⑤) and, since
    slice ⑥, the sole-writer mechanization gate (additive
    ``sole_writer`` key; bars 1+4 consult the pinned upstream through
    the manifest's repo — a URL-shaped/unopenable upstream yields a
    VISIBLE ``skipped: upstream-unavailable`` per plugin, never a
    silent pass, and never flips the aggregate ``ok`` on its own:
    the gate's violations DO flip it, a skip does not).
    """
    plugins_root = Path(plugins_root)
    plugin_dirs = sorted(d for d in plugins_root.iterdir() if d.is_dir()) if plugins_root.is_dir() else []
    plugin_reports = [validate_plugin_dir(d) for d in plugin_dirs]
    failed = [r for r in plugin_reports if not r["ok"]]

    # Port sibling step (slice ⑤) — runs over the in-process declared
    # Ports (the first plugin with declared Ports is opendesign).
    # The build of the boot registry is exercised through this call;
    # a construction refusal surfaces here.
    ports_report: Dict[str, Any]
    try:
        ports_report = validate_ports_report()
    except Exception as exc:  # noqa: BLE001 - report-shaped surface
        ports_report = {
            "ok": False,
            "checked": 0,
            "passed": 0,
            "refused_validation": [
                {"code": "port_registry_construction_failed", "message": str(exc)}
            ],
            "refused_seam_gate": [],
            "ports": [],
        }
    failed.append({"ok": ports_report["ok"], "name": "<ports>"})

    # Sole-writer mechanization gate (slice ⑥ — the ⑤ reviewer ruling,
    # binding FROM ⑥): four bars over each manifest-bearing plugin dir.
    # Additive report key; violations flip the aggregate, a
    # skipped-upstream verdict does not (visible-skip discipline).
    sole_writer_reports: Dict[str, Any] = {}
    sole_writer_ok = True
    if sole_writer:
        for report in plugin_reports:
            if report.get("refusal") is not None:
                continue  # manifest already refused upstream of the gate
            pdir = plugins_root / report["plugin"]
            try:
                sw = check_sole_writer(pdir)
            except Exception as exc:  # noqa: BLE001 - report-shaped surface
                sw = {
                    "ok": False,
                    "plugin": report["plugin"],
                    "violations": [
                        {
                            "code": "sole-writer-gate-crashed",
                            "detail": str(exc),
                        }
                    ],
                    "skipped": None,
                }
            sole_writer_reports[report["plugin"]] = sw
            if not sw.get("ok", False) and not sw.get("skipped"):
                sole_writer_ok = False

    return {
        "plugins_root": str(plugins_root),
        "checked": len(plugin_reports),
        "passed": len(plugin_reports) - len([r for r in plugin_reports if not r["ok"]]),
        "failed": len([r for r in plugin_reports if not r["ok"]]),
        "ok": (
            not [r for r in plugin_reports if not r["ok"]]
            and ports_report["ok"]
            and sole_writer_ok
        ),
        "plugins": plugin_reports,
        "ports": ports_report,
        **({"sole_writer": sole_writer_reports} if sole_writer else {}),
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
