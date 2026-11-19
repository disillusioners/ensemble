#!/usr/bin/env python3
"""MUTATION HARNESS - MUTATES SOURCE FILES. NEVER RUN FROM CI.

WHAT THIS IS
============
The counter-mutation harness for the systemctl-intent guard lane. It PLANTS
a mutation into the guard source, runs the scoped pin file, and RESTORES
the file byte-for-byte. A pin that does not go RED under its own plant is
not evidence.

NUMBERING NOTE: this file starts at M6g. M6a-M6f were the PRIOR lane's
legs; they lived only in /tmp and were never checked in.

THIS SCRIPT EDITS ``daemon/tools/_systemctl_guard.py`` IN PLACE.
   * NEVER run it from CI, from a pack, or from a release step.
   * ALWAYS give it a THROWAWAY worktree
     (``git worktree add --detach /tmp/wt-mut <sha>``) - never a live
     checkout; it REFUSES anything that is not a linked worktree (a
     ``.git`` FILE).
   * It REFUSES to run unless the target file is CLEAN in git (and it
     checks git's own return code), it restores the original bytes in a
     ``finally`` block, it verifies the sha256 afterwards, and it re-runs
     the pins POST-RESTORE and requires them GREEN before a leg counts.
     If the process is SIGKILL'd mid-run the mutation STAYS PLANTED: check
     ``git status --porcelain --untracked-files=all`` and ``git diff``
     before committing anything.

USAGE
=====
    python3 .agents/tester/mutation/systemctl_guard_m6_mutation.py \\
        --worktree /tmp/wt-mut --leg all --python /path/to/python
    ... --discover   # print the red count of every leg, check nothing

INTEGRITY
=========
  * ``EXPECTED`` (below) is the documented red count of every leg; a run
    that does not match it EXACTLY is a failure, so a leg silently losing
    pins is loud rather than green.
  * the child pytest runs with ``POSTGRES_*`` scrubbed from its
    environment (per-variable, never the ``env -u POSTGRES_*`` glob).
"""

import argparse
import hashlib
import os
import subprocess
import sys

TARGET = "daemon/tools/_systemctl_guard.py"
DEFAULT_TEST = "tests/unit/tools/test_systemctl_intent_guard.py"

#: Environment variables scrubbed from the child pytest (the pack's
#: per-variable fence - never the ``env -u POSTGRES_*`` glob, which is a
#: silent no-op).
_SCRUB_ENV = (
    "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST",
    "POSTGRES_PORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGHOST", "PGPORT",
)

# M6g - the whole-text pipe rule (the fix under proof).
M6G_OLD = """    if saw_pipe:
        for words in segments:
            if _executor_kind(words, env) is None:
"""
M6G_NEW = """    if False and saw_pipe:
        for words in segments:
            if _executor_kind(words, env) is None:
"""

# M6h - the timer prefix options take a value. The plant makes every
# --on-* / --timer-* option a KNOWN FLAG (skip 1), so the tail analysis
# anchors on the option's VALUE - the bypass the rule prevents.
M6H_OLD = """        _SYSTEMD_RUN_VALUE_OPTIONS, _SYSTEMD_RUN_FLAG_OPTIONS,
        _ARGV_RUNNER_VALUE_PREFIXES, _SYSTEMD_RUN_TIMER_NO_VALUE,
    )
"""
M6H_NEW = """        _SYSTEMD_RUN_VALUE_OPTIONS, _SYSTEMD_RUN_FLAG_OPTIONS,
        _ARGV_RUNNER_VALUE_PREFIXES, _ARGV_RUNNER_VALUE_PREFIXES,
    )
"""

# M6i - the whole runner value-consumption.
M6I_OLD = """    value, flag, prefixes, prefix_flags = _argv_runner_option_grammar(base)
    return _option_token(
        text, value, flag, long_prefixes=prefixes, long_prefix_flags=prefix_flags
    )[1]
"""
M6I_NEW = """    # MUTATION M6i: the whole value-consumption handling removed.
    return False
"""

# M6j - the systemctl arity mechanism, BOTH halves (the missing value
# options AND the non-terminal read-only parse).
M6J_OLD = """    "-p", "--property", "-P", "-n", "--lines", "-o", "--output", "-s", "--signal",
"""
M6J_NEW = """    "-p", "--property", "-n", "--lines", "-o", "--output", "-s", "--signal",
"""
M6J2_OLD = """    "--image-policy", "--boot-loader-menu", "--boot-loader-entry",
    # ``--legend=BOOL`` takes a MANDATORY value on this host (the help shows
    # no ``[...]``): without it ``systemctl --legend yes status <unit>`` was
    # refused — a read-only command.
    "--legend",
})"""
M6J2_NEW = """})
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
M6L_OLD = """    env_status, env_payload = _env_split_string_payload(words, env)
    if env_status == "unresolved":
        return UNRESOLVED_UNIT_TOKEN
    if env_status == "payload" and env_payload is not None:
        nested = _command_text_violation(env_payload, depth + 1, marker, True)
        if nested is not None:
            return nested
"""
M6L_NEW = """    # MUTATION M6l: the env split-string recursion removed.
"""

# M6m - the scheduler heredoc.
M6M_OLD = """    if base in _SHELLS or base in _SCHEDULERS:
        return True
"""
M6M_NEW = """    if base in _SHELLS:
        return True
"""

# M6n - timeout's signal value options.
M6N_OLD = """_TIMEOUT_VALUE_OPTIONS: frozenset[str] = frozenset({"-k", "--kill-after", "-s", "--signal"})"""
M6N_NEW = """_TIMEOUT_VALUE_OPTIONS: frozenset[str] = frozenset({"-k", "--kill-after"})"""

# M6o - the xargs long value options (kept KNOWN but value-less, so the
# tail anchors on the option's value - the bypass the table prevents).
M6O_OLD = """_XARGS_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a",
    "--arg-file", "--delimiter", "--max-lines", "--max-args", "--max-procs",
    "--max-chars", "--process-slot-var",
})
"""
M6O_NEW = """_XARGS_VALUE_OPTIONS: frozenset[str] = frozenset({
    "-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a",
})
_XARGS_LONGS_AS_FLAGS: frozenset[str] = frozenset({
    "--arg-file", "--delimiter", "--max-lines", "--max-args", "--max-procs",
    "--max-chars", "--process-slot-var",
})
"""
M6O2_OLD = """_XARGS_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-0", "--null", "--eof", "--replace", "-o", "--open-tty",
"""
M6O2_NEW = """_XARGS_FLAG_OPTIONS: frozenset[str] = frozenset({
    "-0", "--null", "--eof", "--replace", "--arg-file", "--delimiter",
    "--max-lines", "--max-args", "--max-procs", "--max-chars",
    "--process-slot-var", "-o", "--open-tty",
"""

# M6p - the attached command-string form.
M6P_OLD = """            elif flag.startswith("-c") and not flag.startswith("--") and len(flag) > 2:
                value = flag[2:]
"""
M6P_NEW = ""

# M6q - the xargs whole-tail recursion.
M6Q_OLD = """        tail = words[j:]
        if tail:
            violation = _segment_violation(tail, env, depth + 1, marker, executed)
            if violation is not None:
                return violation
        return None
"""
M6Q_NEW = """        tail = words[j:]
        sub_cmd, sub_ok = _resolve_word(tail[0], env, 0) if tail else ("", False)
        if sub_ok and _basename(sub_cmd) in _SHELLS:
            return _segment_violation(tail, env, depth + 1, marker, executed)
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
M6S_OLD = """                if not known:
                    # An option xargs does NOT have: its arity is unknown,
                    # so the tail could anchor on the option's VALUE and
                    # never analyse the command behind it ⇒ FAIL CLOSED.
                    return UNRESOLVED_UNIT_TOKEN
"""
M6S_NEW = ""

# M7a - the bundled / attached short-option normalisation.
M7A_OLD = """    body = text[1:]
    if not body:
        return True, False, None
    value_shorts = _short_letters(value_options)
"""
M7A_NEW = """    body = text[1:]
    if not body:
        return True, False, None
    if len(body) > 1:
        return False, False, None      # MUTATION M7a: no bundle analysis
    value_shorts = _short_letters(value_options)
"""

# M7b - the heredoc bodies handed to a pipe-fed executor.
M7B_OLD = """            resolved = []
            for seg_words in segments:
                seg_payload = _segment_payload_text(seg_words, env, data_bodies)
                if seg_payload:
                    resolved.append(seg_payload)
            if resolved and _text_has_mutating_ensemble_shape(" ".join(resolved)):
                return LAUNDERED_TOKEN
            # A heredoc body is REMOVED from the code text by
            # ``_strip_heredocs``, so the whole-text test cannot see it —
            # but a body handed to a pipe-fed executor IS executed
            # (``cat <<'EOF' | cat | bash``). Shape-test the bodies too.
            # A body that only lands in a FILE (no pipe, no executor) stays
            # DATA: the disclosed prose over-block class stays dropped.
            for _delim, body in data_bodies:
                if _text_has_mutating_ensemble_shape(body):
                    return LAUNDERED_TOKEN
"""
M7B_NEW = ""

# M7d - the verb-set derivation (the six verbs re-diverge).
M7D_OLD = """_MUTATING_VERB_RE = re.compile(
    _VERB_BOUNDARY_BEFORE
    + "(?:"
    + "|".join(
        re.escape(verb)
        for verb in sorted(_SYSTEMCTL_TAIL_MUTATING, key=len, reverse=True)
    )
    + ")"
    + _VERB_BOUNDARY_AFTER
)
"""
M7D_NEW = """_MUTATING_VERB_RE = re.compile(
    _VERB_BOUNDARY_BEFORE
    + "(?:"
    + "|".join(
        re.escape(verb)
        for verb in sorted(SYSTEMCTL_MUTATING, key=len, reverse=True)
    )
    + ")"
    + _VERB_BOUNDARY_AFTER
)
"""

# M7e - sudo's missing value options.
M7E_OLD = """    "-D", "--chdir", "-U", "--other-user", "-r", "--role", "-t", "--type",
})"""
M7E_NEW = """    "-D", "--chdir", "-U", "--other-user",
})"""

# M7f - the non-terminal read-only parse ALONE (the isolating leg).
M7F_OLD = """        for i, (tok, tok_ok) in enumerate(operands):
            if not tok_ok:
                continue
            if tok.lower() not in _SYSTEMCTL_TAIL_MUTATING:
                continue
            for later, later_ok in operands[i + 1:]:
                if later_ok and _unit_is_ensemble(later):
                    return _unit_base(later)
                if not later_ok or any(g in later for g in _GLOB_CHARS):
                    # a mutating verb followed by an operand that cannot be
                    # resolved statically (or is globbed) ⇒ FAIL CLOSED
                    return UNRESOLVED_UNIT_TOKEN
"""
M7F_NEW = """        # MUTATION M7f: the non-terminal read-only parse removed.
        return None
"""

# M7g - the resolved-payload scan (env substitution through a multi-stage
# pipe).
M7G_OLD = """            resolved = []
            for seg_words in segments:
                seg_payload = _segment_payload_text(seg_words, env, data_bodies)
                if seg_payload:
                    resolved.append(seg_payload)
            if resolved and _text_has_mutating_ensemble_shape(" ".join(resolved)):
                return LAUNDERED_TOKEN
"""
M7G_NEW = ""

# M7i - the stdin-execution extension for a BARE ssh host (the fix under
# proof): a heredoc body delivered to `ssh host` (no remote command) is
# EXECUTED remotely, because ssh forwards the client's stdin.
M7I_OLD = """    if base == "ssh":
        # A bare ``ssh <host>`` (NO remote command) starts a remote login
        # shell and FORWARDS the client's stdin: a heredoc body delivered
        # to it is EXECUTED remotely — the same mechanism as the pipe
        # form. With a remote command the body is that command's stdin
        # (DATA) and the remote-command path handles it.
        return not _ssh_has_remote_command(words, k, env)
"""
M7I_NEW = ""

# M7h - the ssh unknown-option fail-closed.
M7H_OLD = """        if cmd_words is None:
            return UNRESOLVED_UNIT_TOKEN
"""
M7H_NEW = ""

LEGS = {
    "M6g": [(M6G_OLD, M6G_NEW)],
    "M6h": [(M6H_OLD, M6H_NEW)],
    "M6i": [(M6I_OLD, M6I_NEW)],
    "M6j": [(M6J_OLD, M6J_NEW), (M6J2_OLD, M6J2_NEW), (M7F_OLD, M7F_NEW)],
    "M6k": [(M6K_OLD, M6K_NEW)],
    "M6l": [(M6L_OLD, M6L_NEW)],
    "M6m": [(M6M_OLD, M6M_NEW)],
    "M6n": [(M6N_OLD, M6N_NEW)],
    "M6o": [(M6O_OLD, M6O_NEW), (M6O2_OLD, M6O2_NEW)],
    "M6p": [(M6P_OLD, M6P_NEW)],
    "M6q": [(M6Q_OLD, M6Q_NEW)],
    "M6r": [(M6R_OLD, M6R_NEW)],
    "M6s": [(M6S_OLD, M6S_NEW)],
    "M7a": [(M7A_OLD, M7A_NEW)],
    "M7b": [(M7B_OLD, M7B_NEW)],
    "M7d": [(M7D_OLD, M7D_NEW)],
    "M7e": [(M7E_OLD, M7E_NEW)],
    "M7f": [(M7F_OLD, M7F_NEW)],
    "M7g": [(M7G_OLD, M7G_NEW)],
    "M7h": [(M7H_OLD, M7H_NEW)],
    "M7i": [(M7I_OLD, M7I_NEW)],
}

#: The documented red count of every leg. A run that does not match
#: EXACTLY is a failure - a leg silently losing pins must be loud.
EXPECTED = {
    "M6g": 11, "M6h": 2, "M6i": 12, "M6j": 9, "M6k": 1, "M6l": 9,
    "M6m": 2, "M6n": 1, "M6o": 2, "M6p": 1, "M6q": 5, "M6r": 3,
    "M6s": 2, "M7a": 5, "M7b": 4, "M7d": 7, "M7e": 3, "M7f": 6,
    "M7g": 1, "M7h": 2, "M7i": 1,
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_clean(worktree, path):
    """-> (is_clean, output); a FAILED git reads as NOT clean."""
    proc = subprocess.run(
        ["git", "-C", worktree, "status", "--porcelain",
         "--untracked-files=all", "--", path],
        capture_output=True, text=True,
    )
    # a FAILED git must read as NOT clean, or the refusal is vacuous
    return proc.returncode == 0 and proc.stdout.strip() == "", proc.stdout


def child_env():
    """-> a copy of os.environ with the ambient DB vars removed."""
    """The child pytest environment, with the ambient DB vars scrubbed."""
    env = dict(os.environ)
    for key in _SCRUB_ENV:
        env.pop(key, None)
    return env


def run_pins(worktree, python, test_file):
    """Run the scoped pin file; -> (returncode, sorted FAILED lines)."""
    proc = subprocess.run(
        [python, "-m", "pytest", "-q", "-p", "no:randomly", "--timeout=120",
         "--tb=no", test_file],
        cwd=worktree, capture_output=True, text=True, env=child_env(),
    )
    failed = sorted(
        line for line in proc.stdout.splitlines() if line.startswith("FAILED")
    )
    return proc.returncode, failed


def plant_and_prove(worktree, python, test_file, leg, discover):
    """Plant, prove, restore. Returns True when the leg is PROVEN."""
    target = os.path.join(worktree, TARGET)
    original = open(target, "rb").read()
    before = sha256(original)
    clean, dirty_out = git_clean(worktree, TARGET)
    if not clean:
        print("REFUSED %s: %s is not clean in git:\n%s" % (leg, TARGET, dirty_out))
        return None
    text = original.decode("utf-8")
    edits = LEGS[leg]
    for old, new in edits:
        if text.count(old) != 1:
            print("REFUSED %s: a plant anchor was not found exactly once" % leg)
            return None
        text = text.replace(old, new)
    failed_post_restore = None
    try:
        open(target, "w", encoding="utf-8").write(text)
        rc, failed = run_pins(worktree, python, test_file)
    finally:
        open(target, "wb").write(original)
    after = sha256(open(target, "rb").read())
    clean_after, _dirty = git_clean(worktree, TARGET)
    # post-restore GREEN: a pin red for an UNRELATED reason does not count
    rc_after, failed_post_restore = run_pins(worktree, python, test_file)
    green_after = (rc_after == 0)
    expected = EXPECTED.get(leg)
    count_ok = True if discover or expected is None else (len(failed) == expected)
    print("=== %s ===" % leg)
    print("  reds           : %d%s" % (
        len(failed), "" if count_ok else "  (EXPECTED %s - MISMATCH)" % expected))
    for line in failed[:40]:
        print("      %s" % line)
    print("  sha256 before  : %s" % before)
    print("  sha256 after   : %s" % after)
    print("  restore        : %s" % ("BYTE-IDENTICAL" if before == after else "MISMATCH"))
    print("  tree clean     : %s" % clean_after)
    print("  post-restore   : %s (%d red)" % ("GREEN" if green_after else "NOT GREEN", len(failed_post_restore)))
    ok = (len(failed) > 0) and (before == after) and clean_after and green_after and count_ok
    print("  VERDICT        : %s" % ("PROVEN" if ok else "NOT PROVEN"))
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worktree", required=True,
                    help="a THROWAWAY linked worktree (a .git FILE)")
    ap.add_argument("--python", default=sys.executable,
                    help="the interpreter that runs pytest")
    ap.add_argument("--test", default=DEFAULT_TEST,
                    help="the scoped pin file")
    ap.add_argument("--leg", default="all", choices=["all"] + sorted(LEGS))
    ap.add_argument("--discover", action="store_true",
                    help="print every leg's red count; check nothing")
    args = ap.parse_args()
    worktree = os.path.abspath(args.worktree)
    git_marker = os.path.join(worktree, ".git")
    if not os.path.isfile(git_marker):
        print("REFUSED: %s is not a linked worktree (.git is not a FILE) - "
              "give me a throwaway worktree, not a checkout or a foreign lane" % worktree)
        return 2
    if not os.path.isdir(worktree):
        print("ERROR: no such worktree: %s" % worktree)
        return 2
    legs = sorted(LEGS) if args.leg == "all" else [args.leg]
    results = []
    for leg in legs:
        results.append((leg, plant_and_prove(
            worktree, args.python, args.test, leg, args.discover)))
    print()
    for leg, res in results:
        print("SUMMARY %s: %s" % (leg, "PROVEN" if res else "NOT PROVEN"))
    return 0 if all(res for _, res in results) else 1


if __name__ == "__main__":
    sys.exit(main())
