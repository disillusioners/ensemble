#!/usr/bin/env python3
"""Generate / verify the committed prompt-artifact baseline (Phase A T13 / S9).

The baseline file ``agents/.prompt-artifacts.sha256`` pins the
versioned-immutable prompt artifacts (``VERSIONED_PROMPT_ARTIFACTS`` from
``daemon/tools/prompt_artifacts.py`` — the single source; this script
NEVER duplicates the set) across every agent dir in the tree. It is the
committed anchor for the D-BASH-ESCAPE accepted-risk control: the bash
tool cannot be code-level guarded, so drift is DETECTED instead — at
stage time (scripts/upgrade/stage.sh, read-only + journaled, never
gating per the allow-stale policy) and at daemon boot (api.py lifespan
WARN).

REPO SOURCE OF TRUTH (scripts/protection/ convention): this file is
repo-native — unlike the host-armor mirrors in this directory it has NO
deployed host path; it runs wherever the repo (or release tree) lives.

Usage:
    # Regenerate the repo baseline (writes agents/.prompt-artifacts.sha256)
    python3 scripts/protection/generate_prompt_artifact_baseline.py

    # Read-only verify a tree (repo root or staged release root)
    python3 scripts/protection/generate_prompt_artifact_baseline.py --check --tree <dir>

Exit codes (--check): 0 = clean or baseline absent · 1 = drift found.
Deterministic output: sorted paths, "<sha256>  agents/<dir>/<artifact>"
lines (sha256sum -c compatible; run from the tree root), no comments.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

# Repo root = scripts/protection/ → up two.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Load the single-source module BY FILE PATH, never via the package:
# ``import daemon.tools.prompt_artifacts`` executes daemon/__init__.py +
# daemon/tools/__init__.py, which drag the langchain stack — unavailable
# to the bare host python3 that scripts/upgrade/stage.sh runs under.
# prompt_artifacts.py is stdlib-only by design (its own docstring pins
# "no upstream-import dependency at import time"), so a direct file load
# is safe AND keeps the versioned set single-sourced (this script never
# re-states the set).
_MODULE_PATH = _REPO_ROOT / "daemon" / "tools" / "prompt_artifacts.py"
_spec = importlib.util.spec_from_file_location(
    "_prompt_artifacts_single_source", _MODULE_PATH
)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise SystemExit(f"PROMPT-BASELINE: ERROR — cannot load {_MODULE_PATH}")
pa = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: dataclass processing resolves string annotations
# through sys.modules[cls.__module__].
sys.modules[_spec.name] = pa
_spec.loader.exec_module(pa)


def _default_tree() -> Path:
    return _REPO_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate or verify agents/.prompt-artifacts.sha256 over the "
            "versioned-immutable prompt artifacts (single-sourced from "
            "daemon.tools.prompt_artifacts)."
        ),
    )
    parser.add_argument(
        "--tree",
        type=Path,
        default=None,
        help=(
            "Tree root whose agents/ is hashed (default: this repo root "
            "for generation; required alongside --check for release-tree "
            "verification)."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Read-only verify mode: compare the tree against its baseline "
            "and report drift. NEVER writes. Exit 0 clean/absent, 1 drift."
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "Override the baseline file path (default: "
            "<tree>/agents/.prompt-artifacts.sha256)."
        ),
    )
    args = parser.parse_args(argv)

    tree = (args.tree if args.tree is not None else _default_tree()).resolve(
        strict=False
    )
    agents_root = tree / "agents"
    baseline = (
        args.baseline
        if args.baseline is not None
        else pa.baseline_path_for(agents_root)
    )

    if not agents_root.is_dir():
        print(
            f"PROMPT-BASELINE: ERROR — agents tree not found: {agents_root}",
            file=sys.stderr,
        )
        return 2

    if args.check:
        if not baseline.is_file():
            # Absent baseline is a normal pre-generation state — not drift.
            print(
                f"PROMPT-BASELINE: ABSENT — {baseline} (nothing to verify; "
                f"generate it via this script without --check)"
            )
            return 0
        expected, malformed = pa.parse_baseline(
            baseline.read_text(encoding="utf-8")
        )
        actual = pa.compute_versioned_artifact_hashes(agents_root)
        modified = sorted(
            p for p, d in expected.items() if p in actual and actual[p] != d
        )
        missing = sorted(p for p in expected if p not in actual)
        added = sorted(p for p in actual if p not in expected)
        result = pa.BaselineCheckResult(
            status=(
                "drift"
                if (modified or missing or added or malformed)
                else "ok"
            ),
            agents_root=agents_root,
            baseline_path=baseline,
            modified=tuple(modified),
            missing=tuple(missing),
            added=tuple(added),
            malformed=tuple(malformed),
            verified_count=len(actual),
        )
        print(result.summary())
        for line in result.detail_lines():
            print(line)
        return 1 if result.status == "drift" else 0

    # Generate mode — deterministic body, atomic-ish write (tmp + rename).
    text = pa.generate_baseline_text(agents_root)
    tmp = baseline.with_suffix(baseline.suffix + f".tmp.{id(text)}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(baseline)
    n = text.count("\n")
    print(f"PROMPT-BASELINE: GENERATED — {baseline} ({n} artifact(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
