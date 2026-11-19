#!/usr/bin/env python3
"""tests/upgrade_refusal_scan.py — static census + class gate for the
upgrade-script family (scripts/upgrade/**).

THE DEFECT CLASS (why this file exists)
---------------------------------------
A refusal/message string that is DOUBLE-QUOTED can carry command-substitution
syntax as DOCUMENTATION. Bash then EXECUTES that documented text while it
assembles the message:

    "or `git push origin <branch>:latest` first ..."

  * the backticked region is a command substitution — the command RUNS;
  * its stdout REPLACES the region, so the operator sees the command VANISH
    from the message they were just shown;
  * `<branch>:latest` is re-parsed by the inner shell as REDIRECTIONS
    (`< branch` stdin, `> :latest` stdout) — the stdout redirection CREATES a
    file named `:latest` in the operator's CWD, whatever git then does.

The same trap exists for `$( ... )` inside a double-quoted message and for an
UNQUOTED heredoc (`<<EOF`) whose body carries substitution. Quoted heredoc
delimiters (`<<'EOF'`) are SAFE: no expansion happens inside the body.

WHAT THIS FILE GATES
--------------------
  R1  backtick command substitution in an EXECUTABLE context (double-quoted
      or unquoted). Backticks inside COMMENTS or inside SINGLE-QUOTED strings
      are literal data — allowed, but reported in the census for completeness.
  R2  `$( ... )` NESTED inside a double-quoted string (i.e. embedded in
      surrounding literal text) whose inner command has a MUTATING head.
  R3  an UNQUOTED heredoc whose body carries a command substitution/backtick
      whose inner command has a MUTATING head.

Read-only value producers (date, uname, id, printf, basename, git rev-parse,
...) are NOT violations: running them is the author's intent and they change
nothing. The gate is about MUTATION and about commands embedded in text that
is meant to be READ, not run.

Exit codes: 0 = clean, 1 = violations found, 2 = usage/IO error.
Usage: upgrade_refusal_scan.py <repo-root> [--quiet]
"""

import os
import re
import sys

# ── mutating command heads ───────────────────────────────────────────────────
# A command whose head is in this set CHANGES STATE (repo refs, the
# filesystem, a running service). Embedding one in an operator-facing message
# is the defect class; embedding one in a quoted heredoc that is later
# EXECUTED as a script is legitimate code generation and is not flagged here
# (the heredoc rule only looks at command substitutions INSIDE the body).
MUTATING_HEADS = {
    # version control / remote state
    "systemctl", "launchctl", "service", "rc-service", "reboot", "shutdown",
    "halt", "poweroff",
    "rm", "mv", "cp", "install", "mkdir", "rmdir", "touch", "chmod", "chown",
    "chgrp", "ln", "dd", "tee", "truncate", "shred", "mount", "umount", "mkfs",
    "pkill", "killall",
    "apt", "apt-get", "yum", "dnf", "brew", "pip", "pip3", "npm", "yarn",
    "pnpm", "poetry", "cargo",
    # `kill -0 <pid>` is an existence probe, not a signal — not mutating.
}

# git subcommands that only READ. Any other git subcommand mutates refs,
# the index, the worktree or a remote.
GIT_READONLY = {
    "rev-parse", "rev-list", "describe", "log", "diff", "status", "show",
    "ls-files", "ls-tree", "cat-file", "symbolic-ref", "for-each-ref",
    "hash-object", "check-ignore", "var", "help", "version", "shortlog",
    "blame", "name-rev", "merge-base", "count-objects", "show-ref",
    "annotate", "whatchanged", "diff-tree", "diff-index", "grep", "bundle",
}

# `git config` is read-only only with a read-only flag.
GIT_CONFIG_READ_FLAGS = {"--get", "--get-all", "--get-regexp", "--list", "-l"}


def _is_mutating(inner):
    """True when the command substitution's inner text has a mutating head."""
    text = inner.strip()
    if not text:
        return False, ""
    # Split the first pipeline segment and take its head.
    head = text.split()[0]
    # Strip env-assignment prefixes (FOO=bar cmd) and wrappers.
    while "=" in head and not head.startswith("-"):
        parts = text.split(None, 1)
        text = parts[1] if len(parts) > 1 else ""
        if not text:
            return False, ""
        head = text.split()[0]
    for wrapper in ("command", "env", "sudo", "nohup", "time"):
        if head == wrapper:
            parts = text.split(None, 1)
            if len(parts) < 2:
                return False, ""
            text = parts[1]
            head = text.split()[0]
    if head == "kill":
        # `kill -0 <pid>` is an existence probe.
        return ("-0" not in text.split()[1:2]), "kill"
    if head == "git":
        args = text.split()[1:]
        sub = ""
        for a in args:
            if a.startswith("-"):
                continue
            sub = a
            break
        if sub == "config":
            flags = {a for a in args if a.startswith("-")}
            return not (flags & GIT_CONFIG_READ_FLAGS), "git config"
        if sub in GIT_READONLY:
            return False, "git " + sub
        return True, "git " + (sub or "?")
    if head in MUTATING_HEADS:
        return True, head
    return False, head


def strip_comment(line, in_single, in_double):
    """Split a line into (code, comment) honoring quotes."""
    out = []
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if c == "\\":
            out.append(line[i:i + 2])
            i += 2
            continue
        if c == "'" and not in_double:
            in_single = not in_single
            out.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            out.append(c)
            i += 1
            continue
        if c == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in " \t\n;|&()":
                return "".join(out), line[i:]
            out.append(c)
            i += 1
            continue
        out.append(c)
        i += 1
    return "".join(out), ""


def find_matching(text, start, open_ch, close_ch):
    depth = 0
    i = start
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "'":
            j = text.find("'", i + 1)
            if j == -1:
                return -1
            i = j + 1
            continue
        if c == '"':
            j = text.find('"', i + 1)
            if j == -1:
                return -1
            i = j + 1
            continue
        if text.startswith(open_ch, i):
            depth += 1
            i += len(open_ch)
            continue
        if text.startswith(close_ch, i):
            depth -= 1
            if depth == 0:
                return i
            i += len(close_ch)
            continue
        i += 1
    return -1


def scan_shell(path):
    """Return (findings, census) for one shell file."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")
    findings = []
    census = {"backtick_comment": 0, "backtick_single": 0,
              "backtick_double": 0, "backtick_unquoted": 0,
              "dollar_paren_nested": 0, "dollar_paren_plain": 0,
              "heredoc_quoted": 0, "heredoc_unquoted": 0}
    in_single = False
    in_double = False
    for lineno, raw in enumerate(lines, 1):
        code, comment = strip_comment(raw, in_single, in_double)
        if comment and "`" in comment:
            census["backtick_comment"] += comment.count("`")
        i = 0
        while i < len(code):
            c = code[i]
            if c == "\\":
                i += 2
                continue
            if c == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue
            if c == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue
            if c == "`":
                end = code.find("`", i + 1)
                inner = code[i + 1:end] if end != -1 else code[i + 1:]
                if in_single:
                    census["backtick_single"] += 1
                elif in_double:
                    census["backtick_double"] += 1
                    findings.append({
                        "rule": "R1", "line": lineno, "col": i + 1,
                        "form": "backtick", "context": "double-quoted",
                        "inner": inner.strip(), "text": raw.strip()})
                else:
                    census["backtick_unquoted"] += 1
                    findings.append({
                        "rule": "R1", "line": lineno, "col": i + 1,
                        "form": "backtick", "context": "unquoted",
                        "inner": inner.strip(), "text": raw.strip()})
                i = (end + 1) if end != -1 else len(code)
                continue
            if code.startswith("$(", i) and not in_single:
                end = find_matching(code, i + 1, "(", ")")
                inner = code[i + 2:end] if end != -1 else code[i + 2:]
                # nested == embedded in surrounding literal text of the same
                # double-quoted word (not a whole-word `VAR="$(cmd)"`).
                nested = False
                if in_double:
                    j = i - 1
                    while j >= 0 and code[j] != '"':
                        if code[j] not in " \t":
                            nested = True
                            break
                        j -= 1
                    k = end + 1 if end != -1 else len(code)
                    while k < len(code) and code[k] != '"':
                        if code[k] not in " \t":
                            nested = True
                            break
                        k += 1
                if nested:
                    census["dollar_paren_nested"] += 1
                    mut, head = _is_mutating(inner)
                    if mut:
                        findings.append({
                            "rule": "R2", "line": lineno, "col": i + 1,
                            "form": "dollar-paren", "context": "double-quoted",
                            "inner": inner.strip(), "head": head,
                            "text": raw.strip()})
                else:
                    census["dollar_paren_plain"] += 1
                i = (end + 1) if end != -1 else len(code)
                continue
            i += 1
    # heredocs
    i = 0
    while i < len(lines):
        raw = lines[i]
        for m in re.finditer(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2", raw):
            quoted = bool(m.group(2))
            stripped = m.group(3)
            body = []
            j = i + 1
            while j < len(lines):
                cand = lines[j].strip() if m.group(1) else lines[j]
                if cand == stripped:
                    break
                body.append((j + 1, lines[j]))
                j += 1
            if quoted:
                census["heredoc_quoted"] += 1
            else:
                census["heredoc_unquoted"] += 1
                for bl, btxt in body:
                    for m2 in re.finditer(r"\$\(([^)]*)\)|`([^`]*)`", btxt):
                        inner = (m2.group(1) or m2.group(2) or "")
                        mut, head = _is_mutating(inner)
                        if mut:
                            findings.append({
                                "rule": "R3", "line": bl, "col": 1,
                                "form": "heredoc-unquoted",
                                "context": "heredoc body",
                                "inner": inner.strip(), "head": head,
                                "text": btxt.strip()})
            i = j
        i += 1
    return findings, census


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        sys.stderr.write("usage: upgrade_refusal_scan.py <repo-root> [--quiet]\n")
        return 2
    root = args[0]
    up_dir = os.path.join(root, "scripts", "upgrade")
    if not os.path.isdir(up_dir):
        sys.stderr.write("no scripts/upgrade under %s\n" % root)
        return 2
    targets = []
    for dirpath, _dirs, files in os.walk(up_dir):
        for f in sorted(files):
            if f.endswith(".sh"):
                targets.append(os.path.join(dirpath, f))
    targets.sort()
    all_findings = []
    totals = {}
    for t in targets:
        findings, census = scan_shell(t)
        all_findings.extend((t, f) for f in findings)
        for k, v in census.items():
            totals[k] = totals.get(k, 0) + v
    quiet = "--quiet" in sys.argv
    if not quiet:
        print("=== upgrade refusal-message substitution census ===")
        print("scanned %d shell file(s) under %s" % (len(targets), up_dir))
        for k in sorted(totals):
            print("  %-22s %d" % (k, totals[k]))
    if all_findings:
        print("VIOLATIONS: %d" % len(all_findings))
        for path, f in all_findings:
            rel = os.path.relpath(path, root)
            print("  [%s] %s:%d:%d %s ctx=%s head=%s inner=%r"
                  % (f["rule"], rel, f["line"], f["col"], f["form"],
                     f["context"], f.get("head", "-"), f["inner"][:80]))
            print("      %s" % f["text"][:160])
        return 1
    print("OK: no dangerous command-substitution shape in scripts/upgrade/**.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
