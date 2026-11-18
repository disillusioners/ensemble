#!/usr/bin/env python3
"""MUTATION HARNESS - MUTATES SOURCE FILES. NEVER RUN FROM CI.

WHAT THIS IS
============
The counter-mutation harness for the systemctl-intent guard lane. It
PLANTS a mutation into the guard source, runs the scoped pin file, and
RESTORES the file byte-for-byte. A pin that does not go RED under its
own plant is not evidence.

THIS SCRIPT EDITS ``daemon/tools/_systemctl_guard.py`` IN PLACE.
   * NEVER run it from CI, from a pack, or from a release step.
   * ALWAYS give it a THROWAWAY worktree
     (``git worktree add --detach /tmp/wt-mut <sha>``) - never a live
     checkout.
   * It REFUSES to run unless the target file is CLEAN in git, and it
     restores the original bytes in a ``finally`` block and then verifies
     the sha256 is unchanged. If the process is SIGKILL'd mid-run the
     mutation STAYS PLANTED: check ``git status --porcelain
     --untracked-files=all`` and ``git diff`` before committing anything.

USAGE
=====
    python3 .agents/tester/mutation/systemctl_guard_m6_mutation.py \\
        --worktree /tmp/wt-mut --leg both --python /path/to/python

LEGS
====
  M6g  revert the whole-text pipe rule to adjacency-scoped payload
       analysis => the pipe-shape DENY pins go RED.
  M6h  drop the runner option value-consumption (the ``--on-*`` /
       ``--timer-*`` prefix rule) => the runner-shape DENY pins go RED.
  M6i  drop the WHOLE runner option value-consumption (the prefix rule
       AND the exact value-option sets) => every value-option DENY pin
       goes RED, the plain value options included.
"""

import argparse
import hashlib
import os
import subprocess
import sys

TARGET = "daemon/tools/_systemctl_guard.py"
DEFAULT_TEST = "tests/unit/tools/test_systemctl_intent_guard.py"

# M6g - the whole-text pipe rule (the fix under proof).
M6G_OLD = """    if saw_pipe and code and _text_has_mutating_ensemble_shape(code):
        for words in segments:
            if _executor_kind(words, env) is not None:
                return LAUNDERED_TOKEN
"""
M6G_NEW = """    # MUTATION M6g: the whole-text pipe rule removed - only the
    # adjacency-scoped payload analysis is left.
"""

# M6h - the runner option value-consumption (the fix under proof).
M6H_OLD = """    if text.startswith(_ARGV_RUNNER_VALUE_PREFIXES):
        return text not in _ARGV_RUNNER_VALUE_FLAGS
"""
M6H_NEW = """    # MUTATION M6h: the runner option value-consumption removed.
"""

# M6i - the whole runner option value-consumption (the fix under proof).
M6I_OLD = """    if "=" in text:
        return False
    if base == "watch":
        return text in _WATCH_VALUE_OPTIONS
    if text in _SYSTEMD_RUN_VALUE_OPTIONS:
        return True
    if text.startswith(_ARGV_RUNNER_VALUE_PREFIXES):
        return text not in _ARGV_RUNNER_VALUE_FLAGS
    return False
"""
M6I_NEW = """    # MUTATION M6i: the whole value-consumption handling removed.
    return False
"""

LEGS = {
    "M6g": (M6G_OLD, M6G_NEW),
    "M6h": (M6H_OLD, M6H_NEW),
    "M6i": (M6I_OLD, M6I_NEW),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_clean(worktree, path):
    out = subprocess.run(
        ["git", "-C", worktree, "status", "--porcelain", "--untracked-files=all", "--", path],
        capture_output=True, text=True,
    )
    return out.stdout.strip() == "", out.stdout


def run_pins(worktree, python, test_file):
    proc = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:randomly", "--timeout=120",
         "--tb=no", test_file],
        cwd=worktree, capture_output=True, text=True,
    )
    failed = sorted(
        line for line in proc.stdout.splitlines() if line.startswith("FAILED")
    )
    return proc.returncode, failed, proc.stdout


def plant_and_prove(worktree, python, test_file, leg):
    target = os.path.join(worktree, TARGET)
    original = open(target, "rb").read()
    before = sha256(original)
    clean, dirty_out = git_clean(worktree, TARGET)
    if not clean:
        print("REFUSED %s: %s is not clean in git:\n%s" % (leg, TARGET, dirty_out))
        return None
    old, new = LEGS[leg]
    text = original.decode("utf-8")
    if text.count(old) != 1:
        print("REFUSED %s: the plant anchor was not found exactly once" % leg)
        return None
    try:
        open(target, "w", encoding="utf-8").write(text.replace(old, new))
        rc, failed, out = run_pins(worktree, python, test_file)
    finally:
        open(target, "wb").write(original)
    after = sha256(open(target, "rb").read())
    clean_after, dirty_after = git_clean(worktree, TARGET)
    print("=== %s ===" % leg)
    print("  plant          : %s" % leg)
    print("  reds           : %d" % len(failed))
    for line in failed:
        print("      %s" % line)
    print("  pytest rc      : %d" % rc)
    print("  sha256 before  : %s" % before)
    print("  sha256 after   : %s" % after)
    print("  restore        : %s" % ("BYTE-IDENTICAL" if before == after else "MISMATCH"))
    print("  tree clean     : %s" % clean_after)
    ok = (len(failed) > 0) and (before == after) and clean_after
    print("  VERDICT        : %s" % ("PROVEN" if ok else "NOT PROVEN"))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worktree", required=True,
                    help="the worktree the mutation is planted in")
    ap.add_argument("--python", default=sys.executable,
                    help="the interpreter that runs pytest")
    ap.add_argument("--test", default=DEFAULT_TEST,
                    help="the scoped pin file")
    ap.add_argument("--leg", default="both",
                    choices=["M6g", "M6h", "M6i", "both"])
    args = ap.parse_args()
    worktree = os.path.abspath(args.worktree)
    if not os.path.isdir(worktree):
        print("ERROR: no such worktree: %s" % worktree)
        return 2
    legs = ["M6g", "M6h", "M6i"] if args.leg == "both" else [args.leg]
    results = []
    for leg in legs:
        res = plant_and_prove(worktree, args.python, args.test, leg)
        results.append((leg, res))
    print()
    for leg, res in results:
        print("SUMMARY %s: %s" % (leg, "PROVEN" if res else "NOT PROVEN"))
    return 0 if all(res for _, res in results) else 1


if __name__ == "__main__":
    sys.exit(main())
