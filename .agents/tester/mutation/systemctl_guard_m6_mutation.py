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
  M6j  drop the WHOLE systemctl-arity mechanism — the value options that
       were MISSING from the table (-P and the v255 long options) AND the
       non-terminal read-only parse => the systemctl arity DENY pins go
       RED. (The two halves are INDEPENDENT defences: a plant that removes
       only one of them leaves the pin green — that redundancy is the
       point, and it is why the leg removes both.)
  M6k  restore the "ssh with a remote command only" executor condition =>
       the ssh-without-a-remote-command DENY pin goes RED.
  M6l  drop the env split-string recursion (-S / --split-string) => the
       env DENY pins go RED.
  M6m  restore the "a shell reader only" heredoc condition => the
       scheduler-heredoc DENY pins go RED.
  M6n  drop the wrapper value options (timeout's -s / --signal / -k /
       --kill-after) => the timeout DENY pins go RED.
  M6o  drop the xargs long value options (--arg-file / --delimiter ...)
       => the xargs-option DENY pins go RED.
  M6p  drop the attached command-string form (su -c<cmd>) => the su
       attached-form DENY pins go RED.
  M6q  restore the "the xargs tail's FIRST word is the command" check =>
       the xargs-wrapper DENY pins go RED.
  M6r  drop the runner unknown-option fail-closed => the fail-closed DENY
       pins go RED (an option the tool does not have has an unknown arity).
  M6s  drop the xargs unknown-option fail-closed => the xargs fail-closed
       DENY pin goes RED.
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
M6H_OLD = """    if name.startswith(_ARGV_RUNNER_VALUE_PREFIXES):
        return name not in _ARGV_RUNNER_VALUE_FLAGS
"""
M6H_NEW = """    # MUTATION M6h: the runner option value-consumption removed.
"""

# M6i - the whole runner option value-consumption (the fix under proof).
M6I_OLD = """    if "=" in text:
        return False
    name = _argv_runner_option_name(text)
    if base == "watch":
        return name in _WATCH_VALUE_OPTIONS
    if name in _SYSTEMD_RUN_VALUE_OPTIONS:
        return True
    if name.startswith(_ARGV_RUNNER_VALUE_PREFIXES):
        return name not in _ARGV_RUNNER_VALUE_FLAGS
    return False
"""
M6I_NEW = """    # MUTATION M6i: the whole value-consumption handling removed.
    return False
"""

# M6j - the systemctl value options that were missing from the table.
M6J_OLD = """_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({
    "-H", "--host", "-M", "--machine", "-t", "--type", "--state",
    "-p", "--property", "-P", "-n", "--lines", "-o", "--output", "-s", "--signal",
    "--kill-whom", "--kill-value", "--root", "--image", "--job-mode",
    "--what", "--drop-in", "--message", "--preset-mode", "--when",
    "--timestamp", "--reboot-argument", "--check-inhibitors",
    # v255 value options that were missing from the table: with them absent
    # the parser lands on the option's VALUE and the real verb behind it is
    # never analysed (``systemctl -P cat stop <unit>``).
    "--image-policy", "--boot-loader-menu", "--boot-loader-entry",
})
"""
M6J_NEW = """_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({
    "-H", "--host", "-M", "--machine", "-t", "--type", "--state",
    "-p", "--property", "-n", "--lines", "-o", "--output", "-s", "--signal",
    "--kill-whom", "--kill-value", "--root", "--image", "--job-mode",
    "--what", "--drop-in", "--message", "--preset-mode", "--when",
    "--timestamp", "--reboot-argument", "--check-inhibitors",
})
"""

# M6j - the OTHER half of the same mechanism: the read-only early return.
# F9/F10 are covered by TWO independent defences (the value-option arity
# AND the non-terminal read-only parse); a plant that removes only one of
# them leaves the pin GREEN. The pin is load-bearing only when BOTH are
# neutralised — so this leg does exactly that.
M6J2_OLD = """    if verb in SYSTEMCTL_READONLY:
        # A read-only verb is NOT the end of the analysis. When an option's
        # arity is mis-parsed (a value option missing from the table), the
        # option's VALUE lands in the verb slot and the REAL command stays
        # in the operand tail — ``systemctl -P cat stop <unit>``. The parse
        # therefore CONTINUES: a MUTATING verb found later, FOLLOWED BY an
        # ensemble unit, is refused. ``systemctl status <unit>`` stays
        # allowed — the unit is an operand of the read-only verb with no
        # mutating verb in front of it (the property-name over-block stays
        # dropped: a property VALUE is consumed by its option, so it never
        # reaches this scan).
        for i, (tok, tok_ok) in enumerate(operands):
            if not tok_ok:
                continue
            if tok.lower() not in _SYSTEMCTL_TAIL_MUTATING:
                continue
            for later, later_ok in operands[i + 1:]:
                if later_ok and _unit_is_ensemble(later):
                    return _unit_base(later)
        return None
"""
M6J2_NEW = """    if verb in SYSTEMCTL_READONLY:
        return None
"""

# M6k - the ssh executor (a remote command was required).
M6K_OLD = """    if base == "ssh":
        # ``ssh host`` with NO remote command starts a remote login shell
        # and FORWARDS the client's stdin — the payload IS delivered, so the
        # segment is an executor either way.
        return "ssh"
"""
M6K_NEW = """    if base == "ssh" and _ssh_remote_words(words, k, env):
        return "ssh"
"""

# M6l - the env split-string recursion.
M6L_OLD = """    # ``env -S '<cmd string>'`` / ``env --split-string=<cmd string>`` RUN the
    # string: it is an EXECUTED command, not an opaque option value.
    env_payload = _env_split_string_payload(words, env)
    if env_payload is not None:
        nested = _command_text_violation(env_payload, depth + 1, marker, True)
        if nested is not None:
            return nested

"""
M6L_NEW = """    # MUTATION M6l: the env split-string recursion removed.

"""

# M6m - the scheduler heredoc.
M6M_OLD = """    base = text.rsplit("/", 1)[-1].lower()
    return base in _SHELLS or base in _SCHEDULERS
"""
M6M_NEW = """    base = text.rsplit("/", 1)[-1].lower()
    return base in _SHELLS
"""

# M6n - the wrapper value options.
M6N_OLD = """                    if arg in (
                        "-n", "--adjustment", "-c", "-i", "-o",
                        # ``timeout``'s own value options: the SIGNAL is a
                        # WORD, so the numeric-duration rule below does not
                        # skip it and the command behind it is never
                        # analysed (``timeout -s TERM 5 systemctl ...``).
                        "-s", "--signal", "-k", "--kill-after",
                    ):
"""
M6N_NEW = """                    if arg in (
                        "-n", "--adjustment", "-c", "-i", "-o",
                    ):
"""

# M6o - the xargs long value options.
M6O_OLD = """_XARGS_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a",
    "--arg-file", "--delimiter", "--max-lines", "--max-args", "--max-procs",
    "--max-chars", "--process-slot-var",
})
"""
M6O_NEW = """_XARGS_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a",
})
"""

# M6p - the attached command-string form.
M6P_OLD = """            if flag.startswith("--command="):
                value = flag[len("--command="):]
            elif flag.startswith("-c") and not flag.startswith("--") and len(flag) > 2:
                value = flag[2:]
            elif flag in ("-c", "--command"):
"""
M6P_NEW = """            if flag.startswith("--command="):
                value = flag[len("--command="):]
            elif flag in ("-c", "--command"):
"""

# M6q - the xargs tail's command word.
M6Q_OLD = """        tail = words[j:]
        ck = _command_word_index(tail, env)
        if ck is not None and ck < len(tail):
            sub_cmd, sub_ok = _resolve_word(tail[ck], env, 0)
            if sub_ok and _basename(sub_cmd) in _SHELLS:
                return _segment_violation(tail, env, depth + 1, marker, executed)
        return None
"""
M6Q_NEW = """        sub_cmd, sub_ok = _resolve_word(words[j], env, 0)
        if sub_ok and sub_cmd.rsplit("/", 1)[-1].lower() in _SHELLS:
            return _segment_violation(words[j:], env, depth + 1, marker, executed)
        return None
"""

# M6r - the runner unknown-option fail-closed.
M6R_OLD = """                if not _argv_runner_option_is_known(text, base):
                    # An option the runner does NOT have: its arity is
                    # unknown, so the tail analysis could anchor on the
                    # option's VALUE and never analyse the command behind
                    # it ⇒ FAIL CLOSED (the same convention as an
                    # unresolvable operand).
                    return UNRESOLVED_UNIT_TOKEN
"""
M6R_NEW = ""

# M6s - the xargs unknown-option fail-closed.
M6S_OLD = """                if text.split("=", 1)[0] not in _XARGS_FLAG_OPTIONS:
                    # An option xargs does NOT have: its arity is unknown,
                    # so the tail could anchor on the option's VALUE and
                    # never analyse the command behind it ⇒ FAIL CLOSED.
                    return UNRESOLVED_UNIT_TOKEN
"""
M6S_NEW = ""

LEGS = {
    "M6g": (M6G_OLD, M6G_NEW),
    "M6h": (M6H_OLD, M6H_NEW),
    "M6i": (M6I_OLD, M6I_NEW),
    "M6j": [(M6J_OLD, M6J_NEW), (M6J2_OLD, M6J2_NEW)],
    "M6k": (M6K_OLD, M6K_NEW),
    "M6l": (M6L_OLD, M6L_NEW),
    "M6m": (M6M_OLD, M6M_NEW),
    "M6n": (M6N_OLD, M6N_NEW),
    "M6o": (M6O_OLD, M6O_NEW),
    "M6p": (M6P_OLD, M6P_NEW),
    "M6q": (M6Q_OLD, M6Q_NEW),
    "M6r": (M6R_OLD, M6R_NEW),
    "M6s": (M6S_OLD, M6S_NEW),
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
    text = original.decode("utf-8")
    edits = LEGS[leg]
    if isinstance(edits, tuple):
        edits = [edits]
    for old, new in edits:
        if text.count(old) != 1:
            print("REFUSED %s: a plant anchor was not found exactly once" % leg)
            return None
        text = text.replace(old, new)
    try:
        open(target, "w", encoding="utf-8").write(text)
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
    ap.add_argument("--leg", default="all",
                    choices=sorted(LEGS) + ["all", "both"])
    args = ap.parse_args()
    worktree = os.path.abspath(args.worktree)
    if not os.path.isdir(worktree):
        print("ERROR: no such worktree: %s" % worktree)
        return 2
    if args.leg == "all":
        legs = list(LEGS)
    elif args.leg == "both":
        legs = ["M6g", "M6h"]
    else:
        legs = [args.leg]
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
