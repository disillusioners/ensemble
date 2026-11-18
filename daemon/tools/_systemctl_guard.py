"""Shared, pure intent guard for agent-issued ``systemctl`` / kill shapes.

WHY THIS MODULE EXISTS (2026-11-18 triple outage)
-------------------------------------------------
``daemon/tools/bash.py`` classified ``systemctl`` correctly at the *verb*
level but matched the live-unit family with a plain SUBSTRING test over the
raw command text. Two consequences, both observed in production:

* **UNDER-BLOCK (caused three live outages today).** A variable-indirected or
  composed unit token never produces the literal substring, so
  ``UNIT=ensemble-main.service; systemctl restart "$UNIT"`` and
  ``S=ensemble; systemctl restart ${S}-main`` sailed through the guard.
  The only reason the first shape was ever refused is that the *assignment
  text* happened to contain ``ensemble-main`` — an accident, not a control.
* **OVER-BLOCK.** Because the verb scan ran over the entire command text (not
  over execution positions), non-executing prose was refused: a heredoc that
  merely *documents* ``systemctl restart ensemble-main``, a ``grep`` for that
  string, a ``printf`` of it. Read-only inspection also tripped the same
  text scan whenever a *property name* was a mutating-verb word
  (``systemctl show ensemble-main -p Restart`` => refused).

FIX SHAPE
---------
Intent is decided over **execution positions**, not over raw text:

1. The command string is lexed into simple-command segments. Heredoc bodies
   are DATA unless the heredoc is the stdin of a shell interpreter. Single
   quotes are data. Double quotes are data *except* ``$(...)`` / backticks,
   which execute and are recursed into.
2. Same-string variable assignments (``NAME=value``) are collected and
   resolved statically, including quoted composition (``"$A"-main``),
   ``${A}-main`` and trivial substitutions (``$(echo ensemble-main)``).
3. For a ``systemctl`` segment: read-only verbs are ALLOWED against any unit.
   Any other verb (mutating, or unknown => fail-closed) whose unit operand
   resolves into the ensemble's own unit family is REFUSED. An operand that
   cannot be resolved statically (indirected from the environment, globbed,
   produced by a non-trivial substitution) is REFUSED (fail-closed).
4. ``pkill`` / ``killall`` / ``fuser`` / ``kill`` / ``xargs kill`` keep the
   original text-scoped semantics unchanged (defence in depth; a quoted
   target such as ``pkill -f 'ensemble-prod'`` must stay refused).
5. Wrappers (``sudo``, ``env``, ``nohup``, ...), ``bash -c``/``sh -c``,
   ``eval`` and ``xargs <cmd>`` are peeled/recursed so the shapes cannot be
   laundered through a runner.

Everything here is a PURE function: no I/O, no process state, no environment
reads. The ambient-only fence (``ENSEMBLE_FENCED_TEST_CONTEXT``) is read by
the callers (``daemon/tools/bash.py``; ``proc_run`` reaches the same wrapper
through its existing delegation) so the fence channel stays exactly where it
was (fix-round 2 / B6 — the agent-settable tool-call env overlay is ignored).
"""

from __future__ import annotations

import re
from typing import Sequence

__all__ = [
    "LIVE_NAME_PATTERNS",
    "MEANINGFUL_PATH_PATTERNS",
    "SYSTEMCTL_MUTATING",
    "SYSTEMCTL_READONLY",
    "UNRESOLVED_UNIT_TOKEN",
    "is_kill_like",
    "kill_leg_violation",
    "overlay_violation",
    "systemctl_violation",
]

#: The ensemble's own unit / install-root name family (unchanged from the
#: original overlay table). The ``ens`` prefix joins it for composed operand
#: shapes (``ENS-"main"``): a unit token composed of our short stem is not
#: "clearly non-ensemble", so it is treated as ours (refuse).
LIVE_NAME_PATTERNS: tuple[str, ...] = (
    "ensemble-prod",
    "ensemble-daemon",
    "ensemble-main",
    "ensemble-demo",
    "agents-ensemble",
)

#: Port tokens stay whole-word (``19797`` must not match).
MEANINGFUL_PATH_PATTERNS: tuple[tuple[str, str], ...] = (
    ("9797", r"\b9797\b"),
    ("9798", r"\b9798\b"),
    ("7979", r"\b7979\b"),
)

#: Read-only ``systemctl`` verbs — ALLOWED against any unit, including the
#: ensemble's own (the outage-fixing operator needs this).
SYSTEMCTL_READONLY: frozenset[str] = frozenset({
    "status",
    "show",
    "is-active",
    "is-enabled",
    "is-failed",
    "is-system-running",
    "cat",
    "list-units",
    "list-unit-files",
    "list-sockets",
    "list-timers",
    "list-jobs",
    "list-machines",
    "list-dependencies",
    "list-automounts",
    "list-paths",
    "get-default",
    "show-environment",
    "help",
})

#: Mutating ``systemctl`` verbs (the contract's set, unchanged).
SYSTEMCTL_MUTATING: frozenset[str] = frozenset({
    "stop",
    "restart",
    "kill",
    "disable",
    "mask",
    "reload",
    "daemon-reload",
    "enable",
    "start",
    "try-restart",
    "isolate",
})

#: Returned when an operand/verb could not be resolved statically. Any
#: non-None return from this module means REFUSE; this token makes the
#: fail-closed branch legible in refusals and tests.
UNRESOLVED_UNIT_TOKEN = "unresolved-unit"

#: ``systemctl`` options that consume the following token as a value, so the
#: option's argument is not mistaken for the verb (``systemctl -t service
#: restart <unit>``).
_OPTIONS_WITH_VALUE: frozenset[str] = frozenset({
    "-H", "--host", "-M", "--machine", "-t", "--type", "--state",
    "-p", "--property", "-n", "--lines", "-o", "--output", "-s", "--signal",
    "--kill-whom", "--kill-value", "--root", "--image", "--job-mode",
    "--what", "--drop-in", "--message", "--preset-mode", "--when",
    "--timestamp", "--mkdir", "--reboot-argument", "--check-inhibitors",
})

#: Shell reserved words that can precede a command on the same line
#: (``if systemctl restart <unit>; then``). Skipped like wrappers so the real
#: command word is analysed.
_SHELL_KEYWORDS: frozenset[str] = frozenset({
    "if", "then", "else", "elif", "while", "until", "do", "done", "fi",
    "case", "esac", "in", "select", "time", "{", "}", "!", ":",
})

#: Prefix words that run another command; peeled before the command word.
_WRAPPER_WORDS: frozenset[str] = frozenset({
    "sudo", "doas", "env", "command", "exec", "nohup", "setsid", "stdbuf",
    "nice", "ionice", "time", "builtin", "busybox", "chronic", "unbuffer",
})

#: Wrapper words that consume one non-option argument (a duration / priority).
_WRAPPER_ARG_WORDS: frozenset[str] = frozenset({"timeout", "nice", "ionice", "stdbuf"})

#: Shell interpreters whose ``-c`` payload — and whose heredoc stdin — is
#: executed as commands rather than kept as data.
_SHELLS: frozenset[str] = frozenset({"bash", "sh", "zsh", "dash", "ksh", "ash", "fish", "mksh"})

_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
_UNIT_FAMILY_RE = re.compile(r"^(?:ensemble|ens|agents[-_]?ensemble)(?:[-@.:]|$)")
_GLOB_CHARS = ("*", "?", "[")

_UNIT_SUFFIXES: tuple[str, ...] = (
    ".service", ".socket", ".timer", ".target", ".mount", ".path", ".slice",
    ".device", ".automount", ".swap", ".scope",
)

_MAX_DEPTH = 6

_LIT = "lit"
_EXP = "exp"
_VAR = "var"

# A word is a list of (kind, value) parts; a segment is a list of words.
_Part = tuple[str, str]
_Word = list[_Part]
_Segment = list[_Word]


# ---------------------------------------------------------------------------
# kill legs — UNCHANGED text-scoped semantics
# ---------------------------------------------------------------------------


def _as_text(command) -> str:
    if isinstance(command, (list, tuple)):
        return " ".join(str(c) for c in command)
    return str(command)


def is_kill_like(cmd_lower: str) -> bool:
    """Text-scoped kill-shape detection (the original overlay legs).

    ``systemctl`` is deliberately NOT decided here — structure decides it.
    """
    if re.search(r"\bpkill\b", cmd_lower):
        return True
    if re.search(r"\bkillall\b", cmd_lower):
        return True
    if re.search(r"\bfuser\b", cmd_lower):
        return True
    # "kill" as a command word (not inside another word, not "skill").
    if re.search(r"(^|[\s;&|(`])\s*kill\b", cmd_lower):
        return True
    if "xargs kill" in cmd_lower or re.search(r"\bxargs\s+kill\b", cmd_lower):
        return True
    return False


def kill_leg_violation(command) -> str | None:
    """Return the matched live pattern for a kill-shaped command, else None.

    Text-scoped on purpose: a quoted target (``pkill -f 'ensemble-prod'``) is
    still kill intent, so quote-stripping analysis would be a regression.
    """
    lower = _as_text(command).lower()
    if not is_kill_like(lower):
        return None
    for name in LIVE_NAME_PATTERNS:
        if name in lower:
            return name
    for port, pattern in MEANINGFUL_PATH_PATTERNS:
        if re.search(pattern, lower):
            return port
    return None


# ---------------------------------------------------------------------------
# lexer
# ---------------------------------------------------------------------------


def _read_balanced_paren(text: str, i: int) -> tuple[str, int]:
    """``text[i]`` is just past ``$(``; return (inner, index past ``)``)."""
    depth = 1
    out: list[str] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if ch == "'":
            j = text.find("'", i + 1)
            if j < 0:
                out.append(text[i:])
                i = n
                break
            out.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                if text[j] == "\\":
                    j += 1
                j += 1
            out.append(text[i:j + 1])
            i = min(j + 1, n)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return "".join(out), i + 1
        out.append(ch)
        i += 1
    return "".join(out), n


def _read_dollar(text: str, i: int) -> tuple[_Part, int]:
    """``text[i] == '$'``; return (part, next index)."""
    n = len(text)
    if i + 1 < n and text[i + 1] in "'\"":
        # ANSI-C quoting (``$'\\x65nsemble-main'``) / locale quoting
        # (``$"..."``) — escapes make the value unresolvable statically;
        # emit an unresolvable var so the operand FAILS CLOSED.
        return (_VAR, ""), i + 1
    if i + 1 < n and text[i + 1] == "(":
        inner, j = _read_balanced_paren(text, i + 2)
        return (_EXP, inner), j
    if i + 1 < n and text[i + 1] == "{":
        j = text.find("}", i + 2)
        if j < 0:
            return (_VAR, text[i + 1:]), n
        return (_VAR, text[i + 2:j]), j + 1
    m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i + 1:])
    if m:
        return (_VAR, m.group(0)), i + 1 + m.end()
    return (_LIT, "$"), i + 1


def _read_backtick(text: str, i: int) -> tuple[_Part, int]:
    j = text.find("`", i + 1)
    if j < 0:
        return (_EXP, text[i + 1:]), len(text)
    return (_EXP, text[i + 1:j]), j + 1


def _merge_literals(parts: _Word) -> _Word:
    """Collapse adjacent literal parts (the lexer emits per-character lits)."""
    merged: _Word = []
    for kind, value in parts:
        if kind == _LIT and merged and merged[-1][0] == _LIT:
            merged[-1] = (_LIT, merged[-1][1] + value)
        else:
            merged.append((kind, value))
    return merged


def _scan(text: str) -> list[_Segment]:
    """Lex a command string into simple-command segments of words."""
    segments: list[_Segment] = []
    words: _Segment = []
    cur: _Word = []
    started = False
    i = 0
    n = len(text)

    def end_word() -> None:
        nonlocal cur, started
        if started:
            words.append(_merge_literals(cur))
            cur = []
            started = False

    def end_segment() -> None:
        nonlocal words
        end_word()
        if words:
            segments.append(words)
            words = []

    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            cur.append((_LIT, text[i + 1]))
            started = True
            i += 2
            continue
        if ch == "'":
            j = text.find("'", i + 1)
            if j < 0:
                cur.append((_LIT, text[i + 1:]))
                i = n
            else:
                cur.append((_LIT, text[i + 1:j]))
                i = j + 1
            started = True
            continue
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                c2 = text[i]
                if c2 == "\\" and i + 1 < n:
                    cur.append((_LIT, text[i + 1]))
                    i += 2
                    continue
                if c2 == "$":
                    part, i = _read_dollar(text, i)
                    cur.append(part)
                    continue
                if c2 == "`":
                    part, i = _read_backtick(text, i)
                    cur.append(part)
                    continue
                cur.append((_LIT, c2))
                i += 1
            i = i + 1 if i < n else n
            started = True
            continue
        if ch == "$":
            part, i = _read_dollar(text, i)
            cur.append(part)
            started = True
            continue
        if ch == "`":
            part, i = _read_backtick(text, i)
            cur.append(part)
            started = True
            continue
        if ch == "#" and not started:
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch in " \t\r":
            end_word()
            i += 1
            continue
        if ch == "\n" or ch in ";&|()":
            end_segment()
            i += 1
            continue
        cur.append((_LIT, ch))
        started = True
        i += 1
    end_segment()
    return segments


# ---------------------------------------------------------------------------
# heredocs — bodies are data unless they are a shell interpreter's stdin
# ---------------------------------------------------------------------------


def _heredoc_operators(text: str) -> list[tuple[int, int, bool, str]]:
    """Find ``<<``/``<<-`` operators in CODE positions (quote-aware).

    Returns (op_start, op_end, strip_tabs, delimiter) tuples.
    """
    ops: list[tuple[int, int, bool, str]] = []
    i = 0
    n = len(text)
    in_single = in_double = False
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "\\":
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            j = i + 2
            strip_tabs = False
            if j < n and text[j] == "-":
                strip_tabs = True
                j += 1
            while j < n and text[j] in " \t":
                j += 1
            quote = ""
            if j < n and text[j] in "'\"":
                quote = text[j]
                j += 1
            k = j
            while k < n and (text[k].isalnum() or text[k] in "_-."):
                k += 1
            delim = text[j:k]
            if quote and k < n and text[k] == quote:
                k += 1
            if delim:
                ops.append((i, k, strip_tabs, delim))
                i = k
                continue
        i += 1
    return ops


def _line_start(text: str, pos: int) -> int:
    nl = text.rfind("\n", 0, pos)
    return 0 if nl < 0 else nl + 1


def _line_end(text: str, pos: int) -> int:
    nl = text.find("\n", pos)
    return len(text) if nl < 0 else nl


def _is_shell_stdin_line(line_text: str, env: dict[str, str]) -> bool:
    """Is the heredoc attached to a shell interpreter reading stdin?"""
    segments = _scan(line_text)
    if not segments:
        return False
    words = segments[0]
    k = _command_word_index(words, env)
    if k is None:
        return False
    text, ok = _resolve_word(words[k], env, 0)
    if not ok:
        return False
    return text.rsplit("/", 1)[-1].lower() in _SHELLS


def _strip_heredocs(text: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Remove heredoc bodies from ``text``; return (code_only, executed_bodies)."""
    ops = _heredoc_operators(text)
    if not ops:
        return text, []

    removed: list[tuple[int, int]] = []
    executed: list[str] = []
    cursor = 0
    i = 0
    while i < len(ops):
        op_start, op_end, strip_tabs, delim = ops[i]
        line_start = _line_start(text, op_start)
        line_end = _line_end(text, op_end)
        line_ops = [o for o in ops if _line_start(text, o[0]) == line_start]
        is_shell = _is_shell_stdin_line(text[line_start:line_ops[0][0]], env)
        body_cursor = line_end + 1
        for idx, (o_start, o_end, o_tabs, o_delim) in enumerate(line_ops):
            if o_start < cursor:
                continue
            if idx == 0:
                body_cursor = _line_end(text, o_end) + 1
            body_start = body_cursor
            scan = body_start
            body_end = len(text)
            term_end = len(text)
            while scan <= len(text):
                le = _line_end(text, scan)
                line = text[scan:le]
                candidate = line.lstrip("\t") if o_tabs else line
                if candidate.rstrip("\r") == o_delim:
                    body_end = scan
                    term_end = min(len(text), le + 1)
                    break
                if le >= len(text):
                    break
                scan = le + 1
            body = text[body_start:body_end]
            if is_shell:
                executed.append(body)
            removed.append((body_start, term_end))
            body_cursor = term_end
            cursor = term_end
        # skip the remaining ops on this line (already consumed as bodies)
        while i < len(ops) and _line_start(text, ops[i][0]) == line_start:
            i += 1

    out: list[str] = []
    pos = 0
    for start, end in sorted(removed):
        if start < pos:
            start = pos
        if start > pos:
            out.append(text[pos:start])
        pos = min(end, len(text))
    out.append(text[pos:])
    return "".join(out), executed


# ---------------------------------------------------------------------------
# static same-string variable / substitution resolution
# ---------------------------------------------------------------------------


def _resolve_dollar_expr(expr: str, env: dict[str, str]) -> tuple[str, bool]:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-=+])(.*))?$", expr, re.S)
    if not m:
        # $1, $@, $?, $_ , #{...} etc — not statically resolvable.
        return "", False
    name, op, default = m.group(1), m.group(2), m.group(3)
    if name in env:
        return env[name], True
    if op in (":-", "-", "=", ":=") and default is not None:
        if "$" in default or "`" in default:
            return "", False
        return default, True
    return "", False


def _resolve_substitution(inner: str, env: dict[str, str], depth: int) -> tuple[str, bool]:
    """Resolve a trivial ``$(echo <literal>)`` / ``$(printf %s <literal>)``."""
    if depth > _MAX_DEPTH:
        return "", False
    code, executed = _strip_heredocs(inner, env)
    if executed:
        return "", False
    segments = _scan(code)
    if len(segments) != 1:
        return "", False
    words = segments[0]
    k = _command_word_index(words, env)
    if k is None:
        return "", False
    cmd, ok = _resolve_word(words[k], env, depth + 1)
    if not ok:
        return "", False
    cmd = cmd.rsplit("/", 1)[-1].lower()
    args: list[str] = []
    for w in words[k + 1:]:
        text, ok_arg = _resolve_word(w, env, depth + 1)
        if not ok_arg or any(g in text for g in _GLOB_CHARS):
            return "", False
        args.append(text)
    if cmd == "echo":
        args = [a for a in args if not (a.startswith("-") and set(a[1:]) <= {"n", "e", "E"})]
        return " ".join(args), True
    if cmd == "printf" and args:
        fmt = args[0]
        if "%" not in fmt:
            return " ".join(args), True
        if fmt.strip() in ("%s", "%s\n"):
            return " ".join(args[1:]), True
    return "", False


def _resolve_parts(parts: Sequence[_Part], env: dict[str, str], depth: int) -> tuple[str, bool]:
    out: list[str] = []
    for kind, value in parts:
        if kind == _LIT:
            out.append(value)
        elif kind == _VAR:
            text, ok = _resolve_dollar_expr(value, env)
            if not ok:
                return "", False
            out.append(text)
        elif kind == _EXP:
            text, ok = _resolve_substitution(value, env, depth + 1)
            if not ok:
                return "", False
            out.append(text)
        else:  # pragma: no cover - defensive
            return "", False
    return "".join(out), True


def _resolve_word(word: _Word, env: dict[str, str], depth: int = 0) -> tuple[str, bool]:
    return _resolve_parts(word, env, depth)


def _collect_assignments(segments: Sequence[_Segment]) -> dict[str, str]:
    """Collect ``NAME=value`` prefixes (same-string, static) and resolve them."""
    raw: dict[str, _Word] = {}
    for words in segments:
        k = 0
        while k < len(words):
            head_text, head_ok = _resolve_word(words[k], {}, 0)
            if head_ok and head_text in ("export", "declare", "local", "readonly", "typeset"):
                k += 1
                continue
            break
        while k < len(words):
            head = words[k]
            if not head or head[0][0] != _LIT:
                break
            m = _ASSIGN_RE.match(head[0][1])
            if not m:
                break
            name = m.group(1)
            rest: _Word = [(_LIT, head[0][1][m.end():])] + list(head[1:])
            raw[name] = rest
            k += 1

    env: dict[str, str] = {}
    for _ in range(3):
        for name, parts in raw.items():
            text, ok = _resolve_parts(parts, env, 0)
            if ok:
                env[name] = text
    return env


# ---------------------------------------------------------------------------
# systemctl intent analysis
# ---------------------------------------------------------------------------


def _unit_base(unit: str) -> str:
    name = unit.strip().strip("\"'").lower()
    if "@" in name:
        name = name.split("@", 1)[0]
    for suffix in _UNIT_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _unit_is_ensemble(unit: str) -> bool:
    return bool(_UNIT_FAMILY_RE.match(_unit_base(unit)))


def _command_word_index(words: _Segment, env: dict[str, str]) -> int | None:
    """Index of the command word after peeling wrapper prefixes."""
    k = 0
    n = len(words)
    while k < n:
        text, ok = _resolve_word(words[k], env, 0)
        if not ok:
            return k
        # Leading ``NAME=value`` prefixes are environment assignments, not the
        # command word (``UNIT=x systemctl restart "$UNIT"``).
        if _ASSIGN_RE.match(text):
            k += 1
            continue
        base = text.rsplit("/", 1)[-1].lower()
        if base in _SHELL_KEYWORDS:
            k += 1
            continue
        if base not in _WRAPPER_WORDS and base not in _WRAPPER_ARG_WORDS:
            return k
        k += 1
        # peel the wrapper's own option / value arguments
        if base in _WRAPPER_ARG_WORDS:
            while k < n:
                arg, arg_ok = _resolve_word(words[k], env, 0)
                if arg_ok and arg.startswith("-"):
                    if arg in ("-n", "--adjustment", "-c", "-i", "-o"):
                        k += 2
                        continue
                    k += 1
                    continue
                if arg_ok and re.fullmatch(r"[0-9]+(\.[0-9]+)?[smhd]?", arg):
                    k += 1
                    continue
                break
        if base == "env":
            while k < n:
                arg, arg_ok = _resolve_word(words[k], env, 0)
                if not arg_ok:
                    break
                if arg.startswith("-"):
                    if arg in ("-u", "--unset", "-C", "--chdir"):
                        k += 2
                    else:
                        k += 1
                    continue
                if _ASSIGN_RE.match(arg):
                    k += 1
                    continue
                break
    return None


def _systemctl_segment_violation(words: _Segment, env: dict[str, str], depth: int) -> str | None:
    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return None
    cmd_text, cmd_ok = _resolve_word(words[k], env, 0)
    if not cmd_ok:
        return None
    if cmd_text.rsplit("/", 1)[-1].lower() != "systemctl":
        return None

    verb: str | None = None
    operands: list[tuple[str, bool]] = []
    idx = k + 1
    while idx < len(words):
        text, ok = _resolve_word(words[idx], env, 0)
        if ok and text.startswith("-"):
            if text in _OPTIONS_WITH_VALUE:
                idx += 2
                continue
            idx += 1
            continue
        if verb is None:
            if not ok:
                return UNRESOLVED_UNIT_TOKEN
            verb = text.lower()
            idx += 1
            continue
        operands.append((text, ok))
        idx += 1

    if verb is None:
        return None
    if verb in SYSTEMCTL_READONLY:
        return None
    if not operands:
        # No unit operand: nothing to target (``systemctl daemon-reload``,
        # a bare ``systemctl stop`` which is an error anyway) => allowed.
        return None

    for operand, ok in operands:
        if not ok:
            return UNRESOLVED_UNIT_TOKEN
        if any(g in operand for g in _GLOB_CHARS):
            return UNRESOLVED_UNIT_TOKEN
        if _unit_is_ensemble(operand):
            return _unit_base(operand)
    return None


def _segment_violation(words: _Segment, env: dict[str, str], depth: int) -> str | None:
    """Analyse one simple-command segment (recursing into executing hooks)."""
    if depth > _MAX_DEPTH:
        return UNRESOLVED_UNIT_TOKEN

    violation = _systemctl_segment_violation(words, env, depth)
    if violation is not None:
        return violation

    # Command substitutions execute wherever they appear — including inside
    # another command's arguments (``echo $(systemctl restart <unit>)``).
    for word in words:
        for kind, value in word:
            if kind == _EXP and value.strip():
                nested = _command_text_violation(value, depth + 1)
                if nested is not None:
                    return nested

    k = _command_word_index(words, env)
    if k is None or k >= len(words):
        return None
    cmd_text, cmd_ok = _resolve_word(words[k], env, 0)
    if not cmd_ok:
        return None
    base = cmd_text.rsplit("/", 1)[-1].lower()

    if base in _SHELLS:
        # ``bash -c '<payload>'`` — the payload is a command string.
        for i in range(k + 1, len(words) - 1):
            flag, flag_ok = _resolve_word(words[i], env, 0)
            if not flag_ok:
                continue
            if flag in ("-c", "-lc", "-ic", "--command"):
                payload, payload_ok = _resolve_word(words[i + 1], env, 0)
                if not payload_ok:
                    return UNRESOLVED_UNIT_TOKEN
                return _command_text_violation(payload, depth + 1)
        return None

    if base == "eval":
        payload_text = " ".join(
            _resolve_word(w, env, 0)[0] for w in words[k + 1:]
        )
        for w in words[k + 1:]:
            if not _resolve_word(w, env, 0)[1]:
                return UNRESOLVED_UNIT_TOKEN
        return _command_text_violation(payload_text, depth + 1)

    if base == "xargs":
        # ``xargs systemctl restart <unit>`` executes the tail as a command.
        j = k + 1
        while j < len(words):
            text, ok = _resolve_word(words[j], env, 0)
            if ok and text.startswith("-"):
                if text in ("-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"):
                    j += 2
                    continue
                j += 1
                continue
            break
        if j >= len(words):
            return None
        sub_violation = _systemctl_segment_violation(words[j:], env, depth + 1)
        if sub_violation is not None:
            return sub_violation
        sub_cmd, sub_ok = _resolve_word(words[j], env, 0)
        if sub_ok and sub_cmd.rsplit("/", 1)[-1].lower() in _SHELLS:
            return _segment_violation(words[j:], env, depth + 1)
        return None

    return None


def _command_text_violation(text: str, depth: int = 0) -> str | None:
    if depth > _MAX_DEPTH:
        return UNRESOLVED_UNIT_TOKEN
    code, executed = _strip_heredocs(text, {})
    segments = _scan(code)
    env = _collect_assignments(segments)
    # Heredoc bodies may carry assignments too (``bash <<EOF`` with UNIT=...).
    for words in segments:
        violation = _segment_violation(words, env, depth)
        if violation is not None:
            return violation
    for body in executed:
        nested = _command_text_violation(body, depth + 1)
        if nested is not None:
            return nested
    return None


def systemctl_violation(command) -> str | None:
    """Return a refusal token when the command mutates an ensemble unit."""
    return _command_text_violation(_as_text(command), 0)


def overlay_violation(command) -> str | None:
    """Combined guard: kill legs (text-scoped) then systemctl (structural)."""
    violation = kill_leg_violation(command)
    if violation is not None:
        return violation
    return systemctl_violation(command)
