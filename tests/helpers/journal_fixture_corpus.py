#!/usr/bin/env python3
"""journal_fixture_corpus.py — deterministic generator for the parity corpus.

Writes the promote-preflight-hang journal fixtures under
``tests/fixtures/journal/``.  Every fixture is generated from the byte
literals in this file, so the corpus is reproducible, reviewable and
byte-exact (NUL bytes, invalid UTF-8 and multibyte payloads included -- the
kinds of bytes a hand-edited fixture would destroy).

Phase 0 T0.2 / T0.9 / AC2 / AC9: **22 corpus cases + G1-G5 = 27 fixtures**.
The expected verdicts are NOT authored here -- they are CAPTURED from the
BASE bash scanner by the differential harness
(``tests/packs/preflight_journal_parity_test.sh``) and frozen into
``EXPECTED.json``.  A disagreement between BASE and the helper is a defect in
the helper, never a "correction" of the fixture.

Usage:
    python3 tests/helpers/journal_fixture_corpus.py [--out DIR]
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_OUT = os.path.join(REPO_ROOT, "tests", "fixtures", "journal")

# ── the 22 corpus cases ──────────────────────────────────────────────────────
# (name, bytes, note)  -- the note lands in CORPUS.md

LIVE_SHAPED = (
    b'{"current":"v0.27.11","previous":"v0.27.8","in_flight":null,'
    b'"rollback_window_count":{"24h":1,"window_start":"2026-10-26T00:00:00Z"},'
    b'"cooldown_until":null,"quarantined":[],"history":['
    b'{"ts":"2026-10-28T17:44:05Z","event":"manual_release","detail":"operator manual release v0.27.11"},'
    b'{"ts":"2026-10-28T17:43:19Z","event":"sweep","detail":"pending_op closed by reconcile"},'
    b'{"ts":"2026-10-28T17:42:44Z","event":"halt","detail":"killed by SIGTERM at phase promote:preflight"},'
    b'{"ts":"2026-10-28T17:04:21Z","event":"nonce_consumed","detail":"CONFIRM-UMY2-WJTH"}]}'
)

CASES = [
    # 1
    ("01-canonical-empty-journal",
     b'{"current":null,"previous":null,"in_flight":null,'
     b'"rollback_window_count":{"24h":0,"window_start":null},'
     b'"cooldown_until":null,"quarantined":[],"history":[]}',
     "the initialisation shape journal_init writes; ACCEPT"),
    # 2
    ("02-live-shaped-journal", LIVE_SHAPED,
     "a realistic populated journal; ACCEPT"),
    # 3
    ("03-empty-file", b"",
     "0 bytes -> the EMPTY pre-check (lib.sh:969-972); REFUSE"),
    # 4
    ("04-truncated-mid-object", b'{"current":null,"prev',
     "unbalanced `{` + unterminated string; REFUSE (torn write)"),
    # 5
    ("05-truncated-mid-array", b'{"quarantined":["v0.26.1',
     "unbalanced `[` + unterminated string; REFUSE"),
    # 6
    ("06-unterminated-string", b'{"a":"x',
     "in_str=1 at EOF; REFUSE"),
    # 7
    ("07-escaped-quote-in-string", b'{"a":"x\\"y"}',
     'an escaped quote inside a value; the `esc` state machine; ACCEPT'),
    # 8
    ("08-trailing-backslash-outside-string", b'{"a":1}\\',
     "a standalone trailing backslash OUTSIDE any string: esc is never set, "
     "the counts stay balanced -> ACCEPT (BASE). The SAME backslash inside a "
     "string REFUSES (case 6 semantics) -- the fixture pins the position. "
     "Encode BASE, not the 'correct' behaviour."),
    # 9
    ("09-braces-inside-string", b'{"a":"{}[]"}',
     "string-awareness: braces/brackets inside a value are not counted; ACCEPT"),
    # 10
    ("10-multibyte-utf8", b'{"a":"h\xc3\xa9llo \xe2\x86\x92 \xe2\x9c\x93"}',
     "valid multibyte UTF-8 payload; byte-scan == char-scan; ACCEPT"),
    # 11
    ("11-invalid-utf8", b'{"a":"\xff\xfe"}',
     "invalid UTF-8 bytes: bash tolerates them, a json.loads helper would "
     "refuse -- this row is the D1-1 decision boundary; ACCEPT"),
    # 12
    ("12-single-quoted-pseudo-json", b"{'a':1}",
     "not JSON at all; proves the scanner is a STRUCTURAL check, not a "
     "parser; ACCEPT"),
    # 13
    ("13-trailing-comma", b'{"a":1,}',
     "trailing comma; a strict parser refuses, the scanner accepts; ACCEPT"),
    # 14
    ("14-top-level-array", b"[]",
     "top-level array; the scanner counts brackets too; ACCEPT"),
    # 15
    ("15-binary-garbage-no-braces", b"\x00\x01binary garbage\x02",
     "no braces at all (ob=0 cb=0) -> balanced -> ACCEPT. Also exercises the "
     "NUL-drop: the stdout digest is the NUL-dropped echo. The most "
     "counter-intuitive row; the parity test's value is highest here."),
    # 16
    ("16-whitespace-only", b"   ",
     "whitespace-only: non-empty after `$(cat)`, balanced -> ACCEPT"),
    # 17
    ("17-newline-only", b"\n\n\n",
     "newline-only: `$(cat)` strips ALL trailing newlines -> EMPTY -> REFUSE"),
    # 18
    ("18-trailing-garbage-bracefree", b'{"a":1}garbage',
     "valid JSON + brace-free trailing garbage: the scanner scans the whole "
     "string and only counts braces; ACCEPT"),
    # 19
    ("19-trailing-garbage-with-brace", b'{"a":1}}',
     "valid JSON + an extra `}`: ob=1, cb=2 -> REFUSE"),
    # 20
    ("20-nul-inside-string", b'{"a":"x\x00y"}',
     "NUL inside a string: `$(cat)` DROPS it (stdout digest is the dropped "
     "echo, stderr carries the warning class); ACCEPT"),
    # 21
    ("21-duplicate-key", b'{"x":10,"b":2,"x":20}',
     "duplicate key: read ACCEPT; `sub x` = FIRST occurrence (10); "
     "`update x 99` = the exact corrupt bytes "
     '{"x":10,"b":2,"x":99,"b":2,"x":20} (BASE-BUG-PRESERVING)'),
    # 22
    ("22-escape-then-multibyte",
     b'{"a":"x\\\xc3\xa9y","b":2}',
     "escape then multibyte (backslash + U+00E9) inside a value: the "
     "journal_update value scan's 2-character escape skip. MEASURED: the "
     "verdict is locale-invariant (C / C.UTF-8 / en_US.UTF-8 byte-identical) "
     "and the 2-char skip is behaviourally identical to a 2-byte skip "
     "(skipped bytes can only be continuation bytes, never a quote)."),
]

# ── G1-G5 (guard boundary + the processed-bytes pin) ─────────────────────────

GUARD_BYTES = 98304  # JOURNAL_SIZE_GUARD_BYTES default (phase1 D1-17 / W1)


def _pad_to(size, seed=b'{"current":null,"previous":null,"in_flight":null,'
                        b'"rollback_window_count":{"24h":0,"window_start":null},'
                        b'"cooldown_until":null,"quarantined":[],"history":['
                        b'{"ts":"2026-01-01T00:00:00Z","event":"pad","detail":"'):
    """A valid journal padded to EXACTLY `size` bytes."""
    tail = b'"}]}'
    filler_len = size - len(seed) - len(tail)
    if filler_len < 0:
        raise SystemExit("pad target %d smaller than the seed" % size)
    return seed + b"x" * filler_len + tail


def guard_fixtures():
    return [
        ("G1-guard-threshold-minus-1", _pad_to(GUARD_BYTES - 1),
         "JOURNAL_SIZE_GUARD_BYTES-1 on the LEGACY class (helper absent) -> "
         "ALLOWED (the legacy walk is inside the 120 s step budget)"),
        ("G2-guard-threshold-exact", _pad_to(GUARD_BYTES),
         "exactly JOURNAL_SIZE_GUARD_BYTES on the LEGACY class -> REFUSE "
         "loudly (size-named row + non-zero rc)"),
        ("G3-guard-threshold-plus-1", _pad_to(GUARD_BYTES + 1),
         "JOURNAL_SIZE_GUARD_BYTES+1 on the LEGACY class -> REFUSE loudly"),
        ("G4-guard-on-fast-path", _pad_to(GUARD_BYTES + 1),
         "threshold+1 WITH the python helper present -> NO refusal (the guard "
         "is not consulted on the O(N) path: the next ceremony, which reads a "
         "455 KB+ live journal, must never be blocked)"),
        ("G5-processed-bytes-divergence", b'{"a":"x\\\x00"}',
         "backslash + NUL before the closing quote: `$(cat)` DROPS the NUL, so "
         "the backslash escapes the quote, the string never closes -> REFUSED "
         "by the bash walk; a RAW-byte scan lets the NUL consume the escape "
         "flag and ACCEPTS. This row pins that the verdict domain is the "
         "PROCESSED bytes. Mutation-meaningful: a raw-byte helper turns RED."),
    ]


def write_all(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name, data, _note in CASES + guard_fixtures():
        path = os.path.join(out_dir, name + ".json")
        with open(path, "wb") as fh:
            fh.write(data)
        written.append(path)
    with open(os.path.join(out_dir, "CORPUS.md"), "w", encoding="utf-8") as fh:
        fh.write("# Journal read-path parity corpus\n\n")
        fh.write(
            "Generated by `tests/helpers/journal_fixture_corpus.py` — do not "
            "hand-edit the fixture files (NUL bytes, invalid UTF-8 and "
            "multibyte payloads do not survive an editor).\n\n"
            "Expected verdicts are CAPTURED from the BASE bash scanner by\n"
            "`tests/packs/preflight_journal_parity_test.sh` and frozen in\n"
            "`EXPECTED.json`; a BASE/helper disagreement is a helper defect,\n"
            "never a correction of the fixture.\n\n"
            "| fixture | what it pins |\n|---|---|\n"
        )
        for name, data, note in CASES:
            fh.write("| `%s` | %s |\n" % (name, note))
        fh.write("\n## Guard boundary + processed-bytes pin (G1–G5)\n\n")
        fh.write("| fixture | what it pins |\n|---|---|\n")
        for name, data, note in guard_fixtures():
            fh.write("| `%s` | %s |\n" % (name, note))
        fh.write(
            "\nG1–G4 are consumed by `tests/packs/preflight_size_guard_test.sh`\n"
            "(shell-level guard behaviour); G5 is consumed by the parity\n"
            "harness (byte-level verdict). Together: 22 + 5 = 27 fixtures.\n"
        )
    return written


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)
    written = write_all(args.out)
    sys.stdout.write("wrote %d fixtures under %s\n" % (len(written), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
