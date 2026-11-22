#!/bin/bash
# ============================================================================
# tests/test_hbk_err_inode_churn.sh — the hbk-err inode-churn pins.
#
# THE DEFECT (Emperor commission 2026-11-22, anomaly 84be2e4e): scripts/
# upgrade/lib.sh minted THREE /tmp side-channel files AT SOURCE TIME via
# `mktemp` (which CREATES): ensemble-hbk-err.<pid>.<XXXXXX>,
# ensemble-hbk-verify.<pid>.<XXXXXX>, ensemble-hbk-velapsed.<pid>.<XXXXXX>.
# Every shell that sourced lib.sh therefore burned three zero-byte inodes —
# ~10.9k files on the host, unbounded churn driven by the test suite (the
# daemon itself spawns only two lib.sh-sourcing shells, both one-shot
# ceremony paths => 3 files per ceremony, NOT per second).
#
# THE FIX (lazy mint): the PATH is agreed at source time with `mktemp -u`
# (NON-CREATING); the FILE is created at WRITE time (the payload-bearing
# moment) with an O_EXCL (`set -C`) claim. A file therefore exists ONLY when
# it carries a payload. The readers are UNCHANGED — all three gate on
# `[ -s "$_f" ]`, so an absent file reads exactly like an empty one.
#
# PINS:
#   L1  the INODE-CHURN pin (behavioral, load-bearing): 50 source
#       operations in a scratch TMPDIR mint ZERO ensemble-hbk-* files. A
#       regression to per-event creation cannot slip through — 50 sources
#       would mint 150 files.
#   L2  the PAYLOAD pin: a write creates EXACTLY ONE file per channel,
#       with the expected content, and the reader returns it.
#   L3  the REPEAT-WRITE pin: last-writer-wins is preserved (the reason
#       channel is written up to 15 times per verify).
#   L4  the READER CONTRACT: an absent file reads as "nothing captured".
#   L5  the STRUCTURAL no-regression guard: the three source-time
#       assignments must be NON-CREATING (`mktemp -u`). Exact, and proven
#       non-vacuous by L9 (the guard is fed a creating mktemp and MUST go
#       red).
#   L6  the LEGACY FALLBACK: the $$-keyed no-suffix fallback still names a
#       valid path, and the writer is PATH-driven (it writes wherever the
#       variable points).
#   L7  the LOGGING-SYMMETRY pin — ONE rule per channel, all three covered:
#         err      the writer HELPER carries a normal-logging call (this is
#                  the ONE hole the fix closes — it fails at the pre-fix
#                  tree by design);
#         reason   EVERY call site carries a `_hb_verify_diag` sibling (the
#                  pre-existing 15/15 symmetry — the pin freezes it);
#         elapsed  the writer HELPER carries a normal-logging call (stderr
#                  only — the verify runs inside a $() whose stdout is the
#                  captured MainPID, so stdout is never a legal seam there).
#   L8  the CLEANUP pins: all three channels are removed on exit, the
#       ceremony scripts clean the elapsed channel too, and the reason
#       channel is cleared at verify entry (the stale-read hole).
#   L9  the MUTATION SELF-TEST: reverting the fix in a scratch COPY of
#       lib.sh makes L1/L5 go RED, and the real tree makes them green — a
#       pin that cannot fail is not a pin.
#
# FENCES: read-only against the repo; scratch dirs only; no daemon, no
# network, no live-install contact; nothing outside the test's own scratch
# dirs is ever created or removed.
#
# Run:
#   bash tests/test_hbk_err_inode_churn.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIB="$REPO_ROOT/scripts/upgrade/lib.sh"

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n' "$2" >&2
    [ $# -gt 2 ] && printf '      actual:   %s\n' "$3" >&2
    return 0
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
assert_ne() {
    local name="$1" unexpected="$2" actual="$3"
    if [ "$unexpected" != "$actual" ]; then _pass; else _fail "$name" "not $unexpected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

# ── fences ──────────────────────────────────────────────────────────────────
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env; scrub per-variable and re-run)" >&2
    exit 78
fi
setup_fake_home
preflight_guard

# The scratch roots this run owns (registered with the fixture registry so
# the house trap removes them — nothing outside them is ever touched).
HBK_SCRATCH="$(mktemp -d /tmp/hbk-churn-pin.XXXXXX)"
_register_fixture "$HBK_SCRATCH"

# _hbk_files <dir> — the number of ensemble-hbk-* files under <dir>.
_hbk_files() {
    find "$1" -type f -name 'ensemble-hbk-*' 2>/dev/null | wc -l | tr -d ' '
}

# ════════════════════════════════════════════════════════════════════════════
section "L1 — the INODE-CHURN pin (50 sources ⇒ ZERO minted files)"

L1_DIR="$HBK_SCRATCH/l1"
mkdir -p "$L1_DIR"
L1_N=50
i=0
while [ "$i" -lt "$L1_N" ]; do
    TMPDIR="$L1_DIR" bash -c ". '$LIB'" >/dev/null 2>&1
    i=$((i + 1))
done
L1_FILES="$(_hbk_files "$L1_DIR")"
assert_eq "L1 $L1_N source operations mint ZERO ensemble-hbk-* files" "0" "$L1_FILES"
# The whole scratch tree, not just the top level (a regression could bury
# the mints one level down).
L1_TREE="$(find "$L1_DIR" -name 'ensemble-hbk-*' 2>/dev/null | wc -l | tr -d ' ')"
assert_eq "L1 the scratch tree holds ZERO ensemble-hbk-* entries" "0" "$L1_TREE"
# Non-vacuity of the COUNT itself: the finder really can see such files.
mkdir -p "$L1_DIR/probe"
: > "$L1_DIR/probe/ensemble-hbk-probe.1.aaaaaa"
assert_eq "L1 the counter really counts an ensemble-hbk-* file (non-vacuous)" "1" "$(_hbk_files "$L1_DIR")"
rm -f "$L1_DIR/probe/ensemble-hbk-probe.1.aaaaaa"

# ════════════════════════════════════════════════════════════════════════════
section "L2 — the PAYLOAD pin (a write creates EXACTLY ONE file, with the payload)"

L2_DIR="$HBK_SCRATCH/l2"
mkdir -p "$L2_DIR"
L2_OUT="$(TMPDIR="$L2_DIR" bash -c "
    . '$LIB'
    _handback_err_file_write 'DIAG-ERR-PAYLOAD'
    printf 'files=%s\n' \"\$(find \"\$TMPDIR\" -type f -name 'ensemble-hbk-*' | wc -l | tr -d ' ')\"
    printf 'errreader=[%s]\n' \"\$(_handback_last_error_text)\"
    printf 'errsize=%s\n' \"\$(find \"\$TMPDIR\" -type f -name 'ensemble-hbk-err.*' -exec wc -c {} \; 2>/dev/null | awk '{print \$1}' | head -1)\"
" 2>/dev/null)"
L2_FILES="$(printf '%s\n' "$L2_OUT" | sed -n 's/^files=//p')"
L2_READER="$(printf '%s\n' "$L2_OUT" | sed -n 's/^errreader=//p')"
L2_SIZE="$(printf '%s\n' "$L2_OUT" | sed -n 's/^errsize=//p')"
assert_eq "L2 the err write creates EXACTLY ONE file" "1" "$L2_FILES"
assert_eq "L2 the err reader returns the payload" "[DIAG-ERR-PAYLOAD]" "$L2_READER"
assert_ne "L2 the payload file is NOT empty" "0" "${L2_SIZE:-0}"

L2B_OUT="$(TMPDIR="$L2_DIR" bash -c "
    . '$LIB'
    _handback_verify_reason_write 'REASON-PAYLOAD'
    printf 'reasonreader=[%s]\n' \"\$(_handback_verify_reason_text)\"
    _handback_verify_elapsed_write '42'
    printf 'elapsedreader=[%s]\n' \"\$(_handback_verify_elapsed_text)\"
" 2>/dev/null)"
assert_contains "L2 the reason reader returns the payload" "reasonreader=[REASON-PAYLOAD]" "$L2B_OUT"
assert_contains "L2 the elapsed reader returns the payload" "elapsedreader=[42]" "$L2B_OUT"
L2_TOTAL="$(_hbk_files "$L2_DIR")"
assert_eq "L2 all three channels wrote exactly one file each" "3" "$L2_TOTAL"

# ════════════════════════════════════════════════════════════════════════════
section "L3 — the REPEAT-WRITE pin (last-writer-wins preserved)"

L3_OUT="$(TMPDIR="$L2_DIR" bash -c "
    . '$LIB'
    _handback_verify_reason_write 'REASON-FIRST'
    _handback_verify_reason_write 'REASON-SECOND'
    printf 'reader=[%s]\n' \"\$(_handback_verify_reason_text)\"
" 2>/dev/null)"
assert_contains "L3 a repeat write overwrites (the reason channel is written up to 15x per verify)" \
    "reader=[REASON-SECOND]" "$L3_OUT"

# ════════════════════════════════════════════════════════════════════════════
section "L4 — the READER CONTRACT (absent == empty == nothing captured)"

L4_OUT="$(TMPDIR="$L1_DIR" bash -c "
    . '$LIB'
    printf 'err=[%s]\n' \"\$(_handback_last_error_text)\"
    printf 'reason=[%s]\n' \"\$(_handback_verify_reason_text)\"
    printf 'elapsed=[%s]\n' \"\$(_handback_verify_elapsed_text)\"
" 2>/dev/null)"
assert_contains "L4 the err reader is empty when the file was never created" "err=[]" "$L4_OUT"
assert_contains "L4 the reason reader is empty when the file was never created" "reason=[]" "$L4_OUT"
assert_contains "L4 the elapsed reader is empty when the file was never created" "elapsed=[]" "$L4_OUT"

# ════════════════════════════════════════════════════════════════════════════
section "L5 — the STRUCTURAL no-regression guard (the source-time path must NOT create)"

# The guard: every source-time assignment of a hand-back channel path must
# use the NON-CREATING form. A bare `mktemp ` (creating) is a regression.
# Exact: it inspects the three assignment lines themselves.
_structural_guard() {
    # $1 = the file to inspect. Prints the offending lines; returns 0 when
    # the guard is SATISFIED, 1 when it is violated.
    local f="$1" bad=0 line
    while IFS= read -r line; do
        case "$line" in
            *HANDBACK_ERROR_FILE=*|*HANDBACK_VERIFY_REASON_FILE=*|*HANDBACK_VERIFY_ELAPSED_FILE=*)
                case "$line" in
                    *"mktemp -u "*) : ;;
                    *) bad=1; printf '%s\n' "$line" ;;
                esac
                ;;
        esac
    done < "$f"
    return "$bad"
}

L5_BAD="$(_structural_guard "$LIB" 2>/dev/null)"
L5_RC=$?
assert_eq "L5 the three source-time assignments are NON-CREATING (mktemp -u)" "0" "$L5_RC"
assert_eq "L5 the structural guard reports no offending line" "" "$L5_BAD"
# The guard must actually SEE the three assignments (non-vacuity: a rename
# that moved them out of the guard's pattern must go red).
L5_SEEN="$(grep -cE '^HANDBACK_(ERROR_FILE|VERIFY_REASON_FILE|VERIFY_ELAPSED_FILE)=' "$LIB" || true)"
assert_eq "L5 the guard's pattern still matches all three assignments" "3" "$L5_SEEN"

# ════════════════════════════════════════════════════════════════════════════
section "L6 — the LEGACY FALLBACK (the $$-keyed no-suffix path still works)"

L6_SRC="$(grep -c 'ensemble-hbk-verify\.\$\$' "$LIB" || true)"
assert_ne "L6 the legacy no-suffix fallback path is still present in lib.sh" "0" "$L6_SRC"
# The writer is PATH-driven: it writes wherever the variable points, so the
# legacy form (and any other path) is honoured without a code change.
L6_OUT="$(TMPDIR="$L1_DIR" bash -c "
    . '$LIB'
    HANDBACK_ERROR_FILE=\"\$TMPDIR/ensemble-hbk-err.\$\$.legacy\"
    _handback_err_file_write 'LEGACY-PAYLOAD'
    printf 'reader=[%s]\n' \"\$(_handback_last_error_text)\"
    printf 'legacyexists=%s\n' \"\$( [ -s \"\$TMPDIR/ensemble-hbk-err.\$\$.legacy\" ] && echo yes || echo no )\"
" 2>/dev/null)"
assert_contains "L6 the writer honours an explicitly-set (legacy-shaped) path" \
    "legacyexists=yes" "$L6_OUT"
assert_contains "L6 the reader reads the legacy-shaped path" "reader=[LEGACY-PAYLOAD]" "$L6_OUT"

# ════════════════════════════════════════════════════════════════════════════
section "L7 — the LOGGING-SYMMETRY pin (one rule per channel; all three covered)"

# A "normal-logging call" in a writer helper: a WARN, the dual-sink diag
# emitter, or an explicit unbuffered stderr write.
_has_logging_call() {
    grep -qE '_warn |_hb_verify_diag |>&2' "$1"
}

# (a) err channel — the writer HELPER must log. This is the ONE hole the
# fix closes; it is RED at the pre-fix tree by construction.
L7_ERR_BODY="$(sed -n '/^_handback_err_file_write()/,/^}/p' "$LIB")"
L7_ERR_N="$(printf '%s\n' "$L7_ERR_BODY" | grep -cE '_warn |_hb_verify_diag |>&2' || true)"
assert_ne "L7 the err writer helper carries a normal-logging call" "0" "$L7_ERR_N"

# (b) elapsed channel — the writer HELPER must log (stderr only: the
# verify runs inside a $() whose stdout IS the captured MainPID).
L7_EL_BODY="$(sed -n '/^_handback_verify_elapsed_write()/,/^}/p' "$LIB")"
L7_EL_N="$(printf '%s\n' "$L7_EL_BODY" | grep -cE '_warn |_hb_verify_diag |>&2' || true)"
assert_ne "L7 the elapsed writer helper carries a normal-logging call" "0" "$L7_EL_N"
assert_eq "L7 the elapsed helper never writes to stdout (the capture seam)" "0" \
    "$(printf '%s\n' "$L7_EL_BODY" | grep -cE 'printf .*[^>]>$' || true)"

# (c) reason channel — EVERY call site must carry a `_hb_verify_diag`
# sibling (the pre-existing 15/15 symmetry; the pin freezes it).
# The window is +/-4 lines around the call site.
_reason_sites_without_sibling() {
    local f="$1"
    local -a lines
    mapfile -t lines < "$f" 2>/dev/null || return 1
    local bad=0 i n=${#lines[@]} line window j
    for ((i = 0; i < n; i++)); do
        line="${lines[$i]}"
        case "$line" in
            \#*) continue ;;
            *_handback_verify_reason_write\ *)
                case "$line" in
                    _handback_verify_reason_write\(\)*) continue ;;
                esac
                # the +/-4 line window around the call site
                window=""
                for ((j = i - 4; j <= i + 4; j++)); do
                    if [ "$j" -ge 0 ] && [ "$j" -lt "$n" ]; then
                        window="$window${lines[$j]}
"
                    fi
                done
                if ! printf '%s' "$window" | grep -qE '_hb_verify_diag |_warn '; then
                    bad=1
                    printf '%s\n' "$line"
                fi
                ;;
        esac
    done
    return "$bad"
}

L7_REASON_BAD="$(_reason_sites_without_sibling "$LIB" 2>/dev/null)"
L7_REASON_RC=$?
assert_eq "L7 EVERY reason write site carries a logging sibling" "0" "$L7_REASON_RC"
assert_eq "L7 the reason symmetry census reports no offending site" "" "$L7_REASON_BAD"
L7_REASON_SITES="$(grep -cE '^\s+_handback_verify_reason_write ' "$LIB" || true)"
assert_eq "L7 the reason symmetry census sees all 15 reason write sites" "15" "$L7_REASON_SITES"

# ════════════════════════════════════════════════════════════════════════════
section "L8 — the CLEANUP pins (all three channels are cleaned)"

L8_TRAP="$(grep -c '_handback_channels_cleanup' "$LIB" || true)"
assert_ne "L8 lib.sh's EXIT trap body calls the channel cleanup" "0" "$L8_TRAP"
L8_CLEAN="$(sed -n '/^_handback_channels_cleanup()/,/^}/p' "$LIB" | grep -c 'rm -f' || true)"
assert_eq "L8 the cleanup removes ALL THREE channels" "3" "$L8_CLEAN"
for s in promote restart rollback; do
    L8_EL="$(grep -c 'rm -f "${HANDBACK_VERIFY_ELAPSED_FILE:-}"' "$REPO_ROOT/scripts/upgrade/$s.sh" || true)"
    assert_ne "L8 $s.sh cleans the elapsed channel" "0" "$L8_EL"
done
# The reason channel is cleared at VERIFY ENTRY (the stale-read hole).
L8_ENTRY="$(sed -n '/^_supervision_handback_unit()/,/^}/p' "$LIB" | grep -c '_handback_verify_reason_clear' || true)"
assert_ne "L8 the verify entry clears the reason channel (stale-read hole)" "0" "$L8_ENTRY"

# ════════════════════════════════════════════════════════════════════════════
section "L9 — the MUTATION SELF-TEST (a pin that cannot fail is not a pin)"

# Revert the fix in a scratch COPY of lib.sh: the non-creating `mktemp -u`
# becomes the CREATING `mktemp` — exactly the pre-fix shape. The mutant
# MUST mint files (so L1 would go red) and MUST trip the structural guard.
MUT_DIR="$HBK_SCRATCH/mutant"
mkdir -p "$MUT_DIR"
MUT_LIB="$MUT_DIR/lib.sh"
sed 's|mktemp -u "${TMPDIR:-/tmp}|mktemp "${TMPDIR:-/tmp}|g' "$LIB" > "$MUT_LIB"
MUT_CHANGED="$(grep -c 'mktemp -u ' "$MUT_LIB" || true)"
assert_eq "L9 the mutant reverted the non-creating mktemp (mutation applied)" "0" "$MUT_CHANGED"

MUT_SCRATCH="$HBK_SCRATCH/mutant-tmp"
mkdir -p "$MUT_SCRATCH"
j=0
while [ "$j" -lt 5 ]; do
    TMPDIR="$MUT_SCRATCH" bash -c ". '$MUT_LIB'" >/dev/null 2>&1
    j=$((j + 1))
done
MUT_FILES="$(_hbk_files "$MUT_SCRATCH")"
assert_ne "L9 the MUTANT mints files (so the L1 pin WOULD go red)" "0" "$MUT_FILES"
MUT_GUARD_RC=0
_MUT_GUARD_OUT="$(_structural_guard "$MUT_LIB" 2>/dev/null)" || MUT_GUARD_RC=$?
assert_ne "L9 the structural guard TRIPS on the mutant (guard is non-vacuous)" "0" "$MUT_GUARD_RC"

# The real tree, same conditions ⇒ green (the contrast that proves the pin
# discriminates).
REAL_SCRATCH="$HBK_SCRATCH/real-tmp"
mkdir -p "$REAL_SCRATCH"
j=0
while [ "$j" -lt 5 ]; do
    TMPDIR="$REAL_SCRATCH" bash -c ". '$LIB'" >/dev/null 2>&1
    j=$((j + 1))
done
assert_eq "L9 the REAL tree mints nothing (the same conditions are green)" "0" "$(_hbk_files "$REAL_SCRATCH")"
REAL_GUARD_RC=0
_structural_guard "$LIB" >/dev/null 2>&1 || REAL_GUARD_RC=$?
assert_eq "L9 the structural guard passes on the real tree" "0" "$REAL_GUARD_RC"

# ════════════════════════════════════════════════════════════════════════════
teardown_fake_home
section "RESULT"
printf '%s passed / %s failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
