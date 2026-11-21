#!/usr/bin/env python3
"""Generate ``PRE_EXISTING_DAEMON_OFFENDERS`` for the tool-usage family pin.

The adjudication pack's ``TestNoProducerInstrumentation`` diffs the tree
against ``LANE_BASE`` and requires every changed ``daemon/`` path to be
either in ``ALLOWED_DAEMON_DIFF`` or in the recorded pre-existing set.
Hand-typed lists drift (the lane that regenerated this one inherited 108
rows accumulated across five lanes and could not tell "inherited" from
"ours"), so the row set is DERIVED FROM GIT here.

Definition (mechanical, no judgement):

    offenders = { changed daemon paths in `git diff --name-only <base>..HEAD -- daemon/` }
                - ALLOWED_DAEMON_DIFF          (parsed from the test file)
                - { paths under --wiring-root } (the pin's own exclusion)

Each emitted row carries the commit that last touched it inside the span,
so the offender stays attributable.

Usage (recorded command for the tool-usage-metrics-p1 gate):

    python3 scripts/gen_tool_usage_daemon_allowlist.py \
        --base 0b5624dc9d57fe1c8ec49415382cacb2cbd55f25 \
        --test-file tests/unit/metrics/test_metrics_family_adjudication.py \
        --wiring-root daemon/services/metrics_query/wiring

Prints the frozenset block. Add ``--write`` to splice it into the test
file in place (everything between the ``PRE_EXISTING_DAEMON_OFFENDERS =
frozenset(`` declaration and its closing ``)`` is replaced; the prose
header above the declaration is left alone).
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def allowed_daemon_diff(test_file: Path) -> set[str]:
    """Parse ``ALLOWED_DAEMON_DIFF`` out of the adjudication pack."""
    src = test_file.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and getattr(node.targets[0], "id", "") == "ALLOWED_DAEMON_DIFF"
        ):
            return set(ast.literal_eval(node.value))
    raise SystemExit(f"ALLOWED_DAEMON_DIFF not found in {test_file}")


def build_block(
    repo: Path, base: str, allowed: set[str], wiring_root: str
) -> tuple[str, dict[str, object]]:
    changed = [
        path
        for path in _git(repo, "diff", "--name-only", f"{base}..HEAD", "--", "daemon/").split()
        if path
    ]
    pin_visible = [p for p in changed if not p.startswith(wiring_root)]
    offenders = sorted(p for p in pin_visible if p not in allowed)

    lines = ["PRE_EXISTING_DAEMON_OFFENDERS = frozenset(", "    {"]
    for path in offenders:
        sha = _git(repo, "log", "-1", "--format=%h", f"{base}..HEAD", "--", path).strip()
        lines.append(f'        "{path}",  # {sha or "unknown"}')
    lines += ["    }", ")"]
    stats = {
        "changed": len(changed),
        "wiring_root_excluded": len(changed) - len(pin_visible),
        "allowlisted": len(pin_visible) - len(offenders),
        "offenders": len(offenders),
    }
    return "\n".join(lines) + "\n", stats


def splice(test_file: Path, block: str) -> int:
    """Replace the existing frozenset body in place. Returns chars replaced."""
    src = test_file.read_text(encoding="utf-8")
    decl = "PRE_EXISTING_DAEMON_OFFENDERS = frozenset("
    start = src.index(decl)
    end = src.index("\n)\n", start) + len("\n)\n")
    test_file.write_text(src[:start] + block + src[end:], encoding="utf-8")
    return end - start


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", required=True, help="the lane's true base commit")
    ap.add_argument(
        "--test-file",
        default="tests/unit/metrics/test_metrics_family_adjudication.py",
        help="the adjudication pack (source of ALLOWED_DAEMON_DIFF)",
    )
    ap.add_argument(
        "--wiring-root",
        default="daemon/services/metrics_query/wiring",
        help="the pin's own WIRING_ROOT exclusion",
    )
    ap.add_argument(
        "--repo", default=None, help="repo root (default: derived from --test-file)"
    )
    ap.add_argument(
        "--write", action="store_true", help="splice into the test file in place"
    )
    args = ap.parse_args(argv)

    test_file = Path(args.test_file).resolve()
    repo = Path(args.repo).resolve() if args.repo else test_file.parents[3]
    allowed = allowed_daemon_diff(test_file)
    block, stats = build_block(repo, args.base, allowed, args.wiring_root)

    print(
        "changed daemon paths: {changed}  |  wiring-root excluded: "
        "{wiring_root_excluded}  |  allowlisted: {allowlisted}  |  "
        "OFFENDERS: {offenders}".format(**stats)
    )
    if args.write:
        replaced = splice(test_file, block)
        print(f"spliced {replaced} chars -> {len(block)} chars in {test_file}")
        return 0
    sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
