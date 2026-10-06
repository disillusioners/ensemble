#!/usr/bin/env python3
"""Thin stdlib-only CLI wrapper around the plugin manifest schema-CI check.

Delegates ALL logic to ``daemon.plugin_subsystem.schema_ci`` (the tier-2
vocabulary zone); this wrapper adds only argv plumbing + exit code so shell
test-packs / promote gates can invoke it without importing Python modules.

Usage:
    python plugins-convention/ci_runner.py <plugins_root>

Exit code 0 = all manifests valid; 1 = at least one refusal; 2 = usage error.
Stdlib-only by convention: no third-party imports HERE — dependencies ride
the daemon package (yaml/jsonschema via the reader).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# repo root = parent of plugins-convention/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from daemon.plugin_subsystem.schema_ci import main as _schema_ci_main  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ci_runner",
        description="Plugin manifest vocabulary v1 — thin CI wrapper (delegates to "
        "daemon.plugin_subsystem.schema_ci)",
    )
    parser.add_argument("plugins_root", help="directory containing plugin trees (e.g. plugins/)")
    args = parser.parse_args()
    return _schema_ci_main([args.plugins_root])


if __name__ == "__main__":
    raise SystemExit(main())
