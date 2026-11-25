#!/usr/bin/env python3
r"""tests/shell_continuation_scan.py — TREE-WIDE lint for the bash
"continuation followed by a comment" parse-class defect.

THE DEFECT CLASS (why this file exists)
---------------------------------------
Bash removes a backslash-newline during TOKENIZATION, BEFORE comment
recognition.  So a line that ends with ``\\`` and is followed by a
comment-only line does NOT continue onto the next command line — the
continuation is consumed, the comment swallows the rest of the joined
line, and the LOGICAL LINE ENDS AT THE COMMENT:

    journal_history_append subprocess_wait_timeout \\      <-- the `\`
        # a resolution note, pasted into the argument list  <-- comment
        "site=handback_start kind=budget reason=..." \\       <-- NEW command
        || _warn "journal event append FAILED (best-effort)"  <-- OR-tail

Bash then executes TWO commands instead of one:

  1. ``journal_history_append subprocess_wait_timeout`` — the argument
     list is TRUNCATED at parse level.  If the callee reads ``$2`` and
     the shell runs under ``set -u``, the shell DIES with an
     unbound-variable FATAL; without ``set -u`` the row is appended with
     an EMPTY detail.
  2. ``"site=handback_start kind=budget reason=..." || _warn ...`` — the
     quoted argument becomes a COMMAND NAME (command not found, rc=127)
     and the ``||`` tail fires the misleading "append FAILED" warning.

``bash -n`` returns 0 on this construct: it is syntactically LEGAL.
Only a runtime/parse-level check catches it, which is why every
text-diff / line-set audit walked straight past it.

WHAT THIS FILE GATES
--------------------
R1  An EXECUTABLE shell line that ends with a backslash, where the
    NEXT physical line is comment-only.  The argument list of the
    continued command is silently truncated.

The scanner is a small bash lexer, not a line grep: it tracks single
quotes, double quotes, ``$( ... )`` nesting, backticks and here-document
bodies, so that a ``#`` inside a string or a heredoc body is NOT
mistaken for a comment, and a ``\\`` inside a single-quoted string is
NOT mistaken for a continuation.

Exit codes (CLI): 0 = clean (or exactly the pinned known findings),
1 = at least one occurrence outside the pinned baseline,
2 = usage / IO error.
Usage: shell_continuation_scan.py <repo-root> [--quiet] [--partial-root|--ignore-missing-pins]
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

# ── directory classification ────────────────────────────────────────────────
# Directories that never hold shell source we gate.
_VENDOR_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
    "dist", "build", ".mypy_cache", ".ruff_cache",
}
# Test scaffolding: shell that only ever runs under the test harness.
TEST_SCAFFOLD_DIRS = {"test", "tests"}
# Agent scratch: planning/pin/harness material, not shipped runtime shell.
AGENT_SCRATCH_DIRS = {".agents"}
# Frontend asset trees (they ship .sh snippets that are templates).
FRONTEND_DIRS = {"frontend"}


@dataclass(frozen=True)
class Hit:
    """One R1 occurrence."""

    path: str  # repo-relative, posix separators
    line: int  # 1-based line number of the CONTINUATION line
    next_line: int  # 1-based line number of the comment-only line
    current: str  # the continuation line (stripped)
    following: str  # the comment-only line (stripped)

    @property
    def key(self) -> str:
        return f"{self.path}:{self.line}"


# ── the pinned pre-existing findings ───────────────────────────────────────
# These are PRE-EXISTING occurrences in TEST SCAFFOLDING, found by this
# scanner and NOT fixed by the C-1 lane (out of scope for that lane).
# They are PINNED here, not hidden:
#   * the scanner PRINTS every occurrence (pinned ones included) on
#     every run;
#   * a NEW occurrence anywhere (production OR test scaffolding) is RED;
#   * an occurrence that DISAPPEARS is RED as well (baseline drift) —
#     INCLUDING deletion of the entire pinned file (full-tree scan only;
#     a partial/explicit root opts out via --partial-root): the pin must
#     be updated in the same change that fixes the site, so a fix can
#     never land silently.
#
# Each entry is "path:line" -> one-line reason.
KNOWN_PRE_EXISTING: dict[str, str] = {
    "test/packs/por_b2_wedge_repro_pins_unit_test.sh:137": (
        "pre-existing: the env-scrub prefix is truncated by the NOTE "
        "comment that follows it (pytest then runs without the scrub)"
    ),
    "test/packs/por_b_boot_consistency_unit_test.sh:133": (
        "pre-existing: the env-scrub prefix is truncated by the NOTE "
        "comment that follows it (pytest then runs without the scrub)"
    ),
    "test/packs/por_b3_receipt_supervisor_unit_test.sh:136": (
        "pre-existing: the env-scrub prefix is truncated by the NOTE "
        "comment that follows it (pytest then runs without the scrub)"
    ),
    "test/packs/por_b4_stale_running_sweep_unit_test.sh:139": (
        "pre-existing: the env-scrub prefix is truncated by the NOTE "
        "comment that follows it (pytest then runs without the scrub)"
    ),
    "test/packs/por_c_checkpoint_retention_unit_test.sh:136": (
        "pre-existing: the env-scrub prefix is truncated by the NOTE "
        "comment that follows it (pytest then runs without the scrub)"
    ),
    "tests/packs/soak_gate_mcp_warmup_acceptance.sh:389": (
        "pre-existing: the env-scrub prefix is truncated by the comment "
        "that follows it (pytest then runs without the scrub)"
    ),
    "tests/packs/soak_gate_mcp_warmup_acceptance.sh:442": (
        "pre-existing: the env-scrub prefix is truncated by the comment "
        "that follows it (pytest then runs without the scrub)"
    ),
}


# ── the lexer ──────────────────────────────────────────────────────────────
def _comment_only(line: str) -> bool:
    """True when a physical line carries NOTHING but a comment.

    Only meaningful when the line starts in the shell's NORMAL lexer
    state (the caller guarantees that).
    """
    stripped = line.strip()
    return stripped.startswith("#")


def _read_heredoc_delim(line: str, i: int) -> tuple[str | None, int]:
    """Parse a here-document delimiter at ``line[i:]`` (just past ``<<``).

    Returns ``(delimiter, next_index)``; ``delimiter`` is None when the
    construct is NOT a here-document (e.g. ``<<<`` here-string, or a
    ``<<`` that is part of a longer operator).
    """
    j = i
    if j < len(line) and line[j] == "-":  # <<- strips leading tabs
        j += 1
    if j < len(line) and line[j] == "<":  # <<< here-string — NOT a heredoc
        return None, i
    # delimiter word: optionally quoted, terminated by whitespace / meta
    if j >= len(line):
        return None, i
    if line[j] in ("'", '"'):
        quote = line[j]
        k = j + 1
        while k < len(line) and line[k] != quote:
            k += 1
        if k >= len(line):
            return None, i
        return line[j + 1 : k], k + 1
    k = j
    while k < len(line) and line[k] not in " \t;&|<>()":
        k += 1
    if k == j:
        return None, i
    return line[j:k], k


def scan_text(text: str) -> list[tuple[int, int, str, str]]:
    """Scan shell source for R1 occurrences.

    Returns a list of ``(line_no, next_line_no, current_line, next_line)``
    tuples (1-based line numbers, lines stripped of trailing newline).
    """
    lines = text.split("\n")
    hits: list[tuple[int, int, str, str]] = []
    # lexer state carried across physical lines
    mode = "n"  # n = normal, s = single-quoted, d = double-quoted
    heredocs: list[tuple[str, bool]] = []  # (delimiter, strip_tabs)
    subst_depth = 0  # $( ... ) nesting
    in_backtick = False

    for idx, raw in enumerate(lines):
        line_no = idx + 1
        nxt = lines[idx + 1] if idx + 1 < len(lines) else None

        # ── inside a here-document body? ──────────────────────────────────
        if mode == "n" and heredocs:
            # the body of the FIRST pending heredoc starts here
            delim, strip_tabs = heredocs[0]
            probe = raw.lstrip("\t") if strip_tabs else raw
            if probe == delim:
                heredocs.pop(0)
                # the terminator line itself is shell code again
            else:
                continue  # body content — not shell code

        i = 0
        top_level_continuation = False
        while i < len(raw):
            c = raw[i]
            if mode == "n":
                if c == "\\":
                    if i == len(raw) - 1:
                        # the line ends with a backslash
                        if subst_depth == 0 and not in_backtick:
                            top_level_continuation = True
                        break
                    i += 2
                    continue
                if c == "'":
                    mode = "s"
                    i += 1
                    continue
                if c == '"':
                    mode = "d"
                    i += 1
                    continue
                if c == "`":
                    in_backtick = not in_backtick
                    i += 1
                    continue
                if c == "$" and raw[i : i + 2] == "$(":
                    subst_depth += 1
                    i += 2
                    continue
                if c == ")" and subst_depth > 0:
                    subst_depth -= 1
                    i += 1
                    continue
                if c == "<" and raw[i : i + 2] == "<<":
                    delim, after = _read_heredoc_delim(raw, i + 2)
                    if delim is not None:
                        strip_tabs = raw[i + 2 : i + 3] == "-"
                        heredocs.append((delim, strip_tabs))
                        i = after
                        continue
                    i += 2
                    continue
                if c == "#" and (i == 0 or raw[i - 1] in " \t"):
                    # comment to end of line — but only outside a $( ) body
                    if subst_depth == 0 and not in_backtick:
                        break
                    i += 1
                    continue
                i += 1
                continue
            if mode == "s":
                # inside single quotes a backslash is LITERAL
                if c == "'":
                    mode = "n"
                i += 1
                continue
            # mode == "d"
            if c == "\\":
                if i == len(raw) - 1:
                    # the double-quoted string CONTINUES onto the next line
                    break
                i += 2
                continue
            if c == '"':
                mode = "n"
                i += 1
                continue
            if c == "$" and raw[i : i + 2] == "$(":
                subst_depth += 1
                i += 2
                continue
            if c == ")" and subst_depth > 0:
                subst_depth -= 1
                i += 1
                continue
            i += 1

        if top_level_continuation and nxt is not None:
            # the continuation joins the NEXT physical line; if that line
            # is comment-only, the logical line ends at the comment and
            # the argument list is truncated.
            if mode == "n" and not heredocs and _comment_only(nxt):
                hits.append((line_no, line_no + 1, raw.rstrip(), nxt.rstrip()))

    return hits


def _is_shell_file(name: str) -> bool:
    return name.endswith(".sh") or name.endswith(".bash")


def _classify(rel: str) -> str:
    parts = rel.split("/")
    for part in parts[:-1]:
        if part in TEST_SCAFFOLD_DIRS:
            return "test-scaffolding"
        if part in AGENT_SCRATCH_DIRS:
            return "agent-scratch"
        if part in FRONTEND_DIRS:
            return "frontend"
    return "production"


def scan_tree(root: str) -> list[Hit]:
    """Scan every shell source under ``root`` for R1 occurrences."""
    hits: list[Hit] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _VENDOR_DIRS
        ]
        for name in filenames:
            if not _is_shell_file(name):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                continue
            for line_no, next_line, cur, following in scan_text(text):
                hits.append(Hit(rel, line_no, next_line, cur, following))
    hits.sort(key=lambda h: (h.path, h.line))
    return hits


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    quiet = "--quiet" in argv[1:]
    partial_root = ("--partial-root" in argv[1:]) or ("--ignore-missing-pins" in argv[1:])
    if len(args) != 1:
        print("usage: shell_continuation_scan.py <repo-root> [--quiet] "
              "[--partial-root|--ignore-missing-pins]", file=sys.stderr)
        return 2
    root = os.path.abspath(args[0])
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    hits = scan_tree(root)
    seen = {h.key for h in hits}
    unexpected = [h for h in hits if h.key not in KNOWN_PRE_EXISTING]
    if partial_root:
        # Partial/explicit-root scan: pinned files may legitimately be
        # absent, so drop pins whose file does not exist (the original
        # behaviour). A scratch-root invocation MUST opt in explicitly.
        stale = sorted(
            key
            for key in set(KNOWN_PRE_EXISTING) - seen
            if os.path.isfile(os.path.join(root, key.rsplit(":", 1)[0]))
        )
    else:
        # Full-tree scan (the default): a missing pinned file is itself a
        # stale pin (baseline drift) and goes RED — file deletion can no
        # longer pass silently.
        stale = sorted(set(KNOWN_PRE_EXISTING) - seen)

    if not quiet:
        print(f"shell_continuation_scan: {len(hits)} occurrence(s) of "
              f"'continuation followed by a comment' under {root}")
        for h in hits:
            known = h.key in KNOWN_PRE_EXISTING
            tag = "PINNED" if known else "NEW"
            print(f"  [{tag}] {h.path}:{h.line} -> :{h.next_line}")
            print(f"      current : {h.current}")
            print(f"      next    : {h.following}")
            if known:
                print(f"      reason  : {KNOWN_PRE_EXISTING[h.key]}")
        for key in stale:
            print(f"  [STALE PIN] {key} — {KNOWN_PRE_EXISTING[key]}")
            print("      the pinned occurrence is GONE from disk — update the")
            print("      baseline in the same change that fixed it.")

    if unexpected:
        print(
            f"RESULT: FAIL — {len(unexpected)} NEW occurrence(s) of a "
            f"continuation followed by a comment:",
            file=sys.stderr,
        )
        for h in unexpected:
            print(f"  {h.path}:{h.line} -> {h.next_line}", file=sys.stderr)
        return 1
    if stale:
        print(
            f"RESULT: FAIL — {len(stale)} pinned occurrence(s) no longer "
            f"present (baseline drift)",
            file=sys.stderr,
        )
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
