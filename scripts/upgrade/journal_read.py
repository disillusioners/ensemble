#!/usr/bin/env python3
"""journal_read.py — O(N) read-side twin for the upgrade journal.

Read-only helper for ``scripts/upgrade/lib.sh``. It replaces the three
per-character bash scans that made the 2026-10-28 promote preflight spin for
37 minutes on a 455,783-byte journal (``journal_read`` =
O(N^2) balance scan, ``journal_update`` = O(N^2) value scanner,
``_json_sub`` = O(N^2) bracket counter).

Why this file exists and why it is separate from ``journal_splice.py``
----------------------------------------------------------------------
``journal_splice.py`` is the *write* helper; it refuses any path under the
live install unless the pipeline lock is held (``:549-:560``) and refuses a
missing journal (``:564-:568``).  The read path has no such policy: three of
its callers run with no lock at all (``status.sh``) or BEFORE the lock is
acquired (``rollback.sh``, ``restart.sh``).  Routing reads through the write
helper would re-open a mutation-verified refusal surface for zero incident
value, so the read path gets its own file, its own policy and its own thin
path resolver (the shell passes the path in; nothing is derived here).

Three verbs, three DISTINCT algorithms
--------------------------------------
The three bash constructs this file replaces are NOT the same state machine
and must not be unified (plan phase1 D1-12 / A16 / R-N3):

``verify``  string-AWARE brace/bracket/quote balance scan (``journal_read``)
``sub``     string-NAIVE first-occurrence bracket counter (``_json_sub``)
``update``  string-NAIVE first-match value consumer + 2-byte escape skip
            (``journal_update``)

Sharing the string-aware machine across them CHANGES the extraction set
(e.g. ``_json_sub`` today *is* redirected by a bracket that sits inside a
string value -- that is BASE behaviour).

The stdout contract: bash command-substitution emulation
--------------------------------------------------------
``journal_read``'s stdout is not the raw file.  It is the bytes bash holds
after ``json="$(cat "$jp")"``:

* every NUL byte is DROPPED (bash cannot store NUL in a string; it prints
  ``warning: command substitution: ignored null byte in input``), and
* ALL trailing newlines are stripped.

Every real journal ends in exactly one newline (``journal_write``'s
``printf '%s\\n'``), so a raw-dump helper fails stdout parity on every real
journal.  ``verify`` therefore prints the PROCESSED bytes and computes its
verdict over those same bytes -- which is also why the NUL-adjacent-to-
backslash case (``{"a":"x\\<NUL>"}``) is REFUSED here exactly as BASE
refuses it, while a raw-byte scan would accept it (corpus row G5).

Exit codes
----------
``0``  ok (stdout carries the answer)
``1``  authoritative refusal (absent / unreadable / empty / malformed /
       not-found) -- the shell's ``journal_read`` maps this to its own rc 1
``2``  helper UNUSABLE (unexpected internal error, unsupported interpreter)
       -- the shell falls back to the legacy bash walk (never a refusal)
``3``  ``update``: the field is absent (BASE's "field not found" warn)
``4``  ``update``: the splice produced nothing (BASE's "splice failed" warn)

Python 3.7+ stdlib only, no imports outside the stdlib: the same floor as
``journal_splice.py`` (``_splice_python_floor_probe``).  The file is
deliberately byte-oriented: every character that is semantically significant
to any of the three algorithms is ASCII, and a UTF-8 continuation byte is
always >= 0x80, so a byte scan is equivalent to bash's character scan on
valid multibyte input; on invalid input bash treats each invalid byte as one
character, which is what a byte scan does too.  The one char-vs-byte cell
(``journal_update``'s 2-character escape skip) is behaviourally identical
for the same reason: the skipped bytes can only be continuation bytes, which
can never be a quote.
"""

import os
import sys

__version__ = "1.0.0"

# The bash warning class for a dropped NUL byte (matched on class/prefix --
# bash's own wording varies by version; this host emits
# "...ignored null byte in input").
NUL_WARNING = "warning: command substitution: ignored null byte in input"

# bash's [[:space:]] class (the leading-whitespace trim in journal_update /
# _json_sub).  bash's definition is fixed: space, \t, \n, \v, \f, \r.
WHITESPACE = b" \t\n\v\f\r"

CH_LBRACE = 0x7B
CH_RBRACE = 0x7D
CH_LBRACK = 0x5B
CH_RBRACK = 0x5D
CH_QUOTE = 0x22
CH_BACKSLASH = 0x5C


def _warn_prefix():
    """Reproduce lib.sh's ``_warn`` prefix byte-for-byte.

    ``_warn`` is: printf '%s[%s]: WARN: %s\\n' "$LOG_TAG" "${UP_TARGET:-lib}" "$*"
    so a warning emitted here is indistinguishable from one emitted by the
    shell function (which is what the parity corpus asserts).
    """
    tag = os.environ.get("LOG_TAG") or ""
    target = os.environ.get("UP_TARGET") or "lib"
    return "%s[%s]: WARN: " % (tag, target)


def _warn(msg):
    sys.stderr.write(_warn_prefix() + msg + "\n")
    sys.stderr.flush()


class _Unusable(Exception):
    """The helper cannot answer authoritatively -> the shell falls back."""


# ── shared I/O helpers (no algorithm lives here) ──────────────────────────────


def _read_processed(path):
    """Read the journal ONCE and return the bytes bash would hold.

    Single read by construction (plan D1-16): a second read could straddle
    the writer's atomic temp+rename replace and yield an inconsistent pair.
    Returns ``None`` when the file cannot be read (BASE: ``cat`` failed ->
    silent rc 1).
    """
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    if b"\x00" in raw:
        sys.stderr.write(NUL_WARNING + "\n")
        sys.stderr.flush()
        raw = raw.replace(b"\x00", b"")
    # $(cat) strips every trailing newline, not just one.
    return raw.rstrip(b"\n")


def _strip_leading_ws(buf):
    i = 0
    n = len(buf)
    while i < n and buf[i] in WHITESPACE:
        i += 1
    return buf[i:]


# ── verb 1: verify — string-AWARE balance scan (journal_read) ────────────────


def _balance_scan(data):
    """Byte-level port of lib.sh's journal_read state machine.

    The algorithm is reproduced term for term (``ob/cb/os/cs/in_str/esc``):
    a leading backslash consumes the NEXT character while inside a string; a
    quote toggles string state; braces/brackets are counted only outside a
    string.  Returns True when the journal is balanced.
    """
    ob = cb = os_ = cs = 0
    in_str = 0
    esc = 0
    for c in data:
        if esc:
            esc = 0
            continue
        if c == CH_BACKSLASH:
            if in_str:
                esc = 1
            continue
        if c == CH_QUOTE:
            in_str = 1 - in_str
            continue
        if in_str:
            continue
        if c == CH_LBRACE:
            ob += 1
        elif c == CH_RBRACE:
            cb += 1
        elif c == CH_LBRACK:
            os_ += 1
        elif c == CH_RBRACK:
            cs += 1
    return ob == cb and os_ == cs and in_str == 0


def cmd_verify(path):
    data = _read_processed(path)
    if data is None:
        return 1
    if not data:
        _warn("journal at %s is EMPTY (torn write?) \u2014 refusing to trust it" % path)
        return 1
    if not _balance_scan(data):
        _warn(
            "journal at %s is MALFORMED (torn write: unbalanced json) \u2014 refusing to trust it"
            % path
        )
        return 1
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return 0


# ── verb 2: sub — string-NAIVE bracket counter (_json_sub) ───────────────────


def _json_sub(data, key):
    """Port of lib.sh's ``_json_sub``: string-NAIVE, first occurrence.

    A ``}``/``]`` inside a string value still decrements the depth -- that is
    BASE behaviour and is pinned (plan D1-12).  Returns the raw ``{...}`` /
    ``[...]`` slice or ``None``.
    """
    needle = key.encode("utf-8", "surrogateescape")
    at = data.find(needle)
    if at < 0:
        return None
    rest = data[at + len(needle):]
    colon = rest.find(b":")
    if colon < 0:
        # `${rest#*:}` leaves the string untouched when there is no colon.
        pass
    else:
        rest = rest[colon + 1:]
    rest = _strip_leading_ws(rest)
    if not rest or rest[0] not in (CH_LBRACE, CH_LBRACK):
        return None
    depth = 0
    out = bytearray()
    for c in rest:
        if c == CH_LBRACE or c == CH_LBRACK:
            depth += 1
        elif c == CH_RBRACE or c == CH_RBRACK:
            depth -= 1
            out.append(c)
            if depth == 0:
                return bytes(out)
            continue
        out.append(c)
    return None


def cmd_sub(key):
    data = sys.stdin.buffer.read()
    got = _json_sub(data, key)
    if got is None:
        return 1
    sys.stdout.buffer.write(got)
    sys.stdout.buffer.flush()
    return 0


# ── verb 3: update — string-NAIVE value consumer (journal_update) ────────────


def _splice_value(data, field, raw):
    """Port of lib.sh's ``journal_update`` splice.

    ``head`` is the prefix up to AND INCLUDING the LAST ``"field"``
    occurrence (``${rest%*"field"*}"field"``); ``rest`` resumes after the
    FIRST occurrence (``${rest#*"field"}``), then after the first ``:``.
    The value is consumed by a string-naive scan: a quoted value is skipped
    with a 2-byte escape skip; anything else is bracket-depth consumed, and a
    top-level ``,`` ends it.

    The duplicate-key asymmetry (head at the LAST occurrence, value scan at
    the FIRST) is BASE behaviour and is pinned as BASE-BUG-PRESERVING:
    ``{"x":10,"b":2,"x":20}`` + ``update x 99`` ->
    ``{"x":10,"b":2,"x":99,"b":2,"x":20}``.  A "fix" would be a behaviour
    change on the read-modify-write path of a safety-critical script.
    """
    needle = b'"' + field.encode("utf-8", "surrogateescape") + b'"'
    first = data.find(needle)
    if first < 0:
        return None
    last = data.rfind(needle)
    head = data[:last] + needle
    rest = data[first + len(needle):]
    colon = rest.find(b":")
    if colon >= 0:
        rest = rest[colon + 1:]
    rest = _strip_leading_ws(rest)

    if rest[:1] == b'"':
        rest = rest[1:]
        i = 0
        n = len(rest)
        while i < n:
            c = rest[i]
            if c == CH_BACKSLASH:
                i += 2          # ${rest:2} -- 2 characters, all ASCII-skippable
                continue
            if c == CH_QUOTE:
                i += 1
                break
            i += 1
        rest = rest[i:]
    else:
        depth = 0
        i = 0
        n = len(rest)
        while i < n:
            c = rest[i]
            if c == CH_LBRACE or c == CH_LBRACK:
                depth += 1
            elif c == CH_RBRACE or c == CH_RBRACK:
                if depth == 0:
                    break
                depth -= 1
            elif c == 0x2C:      # ','
                if depth == 0:
                    break
            i += 1
        rest = rest[i:]

    return head + b":" + raw.encode("utf-8", "surrogateescape") + rest


def cmd_update(field, raw):
    data = sys.stdin.buffer.read()
    out = _splice_value(data, field, raw)
    if out is None:
        return 3
    if not out:
        return 4
    sys.stdout.buffer.write(out)
    sys.stdout.buffer.write(b"\x1f")     # sentinel: survives $( ) newline strip
    sys.stdout.buffer.flush()
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

_USAGE = (
    "usage: journal_read.py verify <journal_path>\n"
    "       journal_read.py sub <key>            < json\n"
    "       journal_read.py update <field> <raw> < json\n"
    "       journal_read.py --probe\n"
)


def main(argv):
    if not argv:
        sys.stderr.write(_USAGE)
        return 2
    op = argv[0]
    if op == "--probe":
        sys.stdout.write("journal_read.py %s python %s\n" % (__version__, sys.version.split()[0]))
        return 0
    if op == "verify":
        if len(argv) != 2:
            sys.stderr.write(_USAGE)
            return 2
        return cmd_verify(argv[1])
    if op == "sub":
        if len(argv) != 2:
            sys.stderr.write(_USAGE)
            return 2
        return cmd_sub(argv[1])
    if op == "update":
        if len(argv) != 3:
            sys.stderr.write(_USAGE)
            return 2
        return cmd_update(argv[1], argv[2])
    sys.stderr.write(_USAGE)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except _Unusable as exc:                     # pragma: no cover - defensive
        sys.stderr.write("journal_read.py: unusable: %s\n" % exc)
        sys.exit(2)
    except BrokenPipeError:                       # pragma: no cover
        sys.exit(0)
    except Exception as exc:                      # pragma: no cover - defensive
        # An unexpected internal failure must NEVER masquerade as a
        # refusal: the shell falls back to the legacy walk on rc 2.
        sys.stderr.write("journal_read.py: internal error: %r\n" % (exc,))
        sys.exit(2)
