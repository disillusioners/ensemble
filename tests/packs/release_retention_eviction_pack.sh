#!/usr/bin/env bash
# tests/packs/release_retention_eviction_pack.sh
# ============================================================================
# RELEASE-RETENTION-EVICTION CONTRACT PACK (commission release-retention-eviction-gap)
#
# WHAT THIS PINS (four contract pins, against the REAL scripts, never source-grep)
#   P1  staged-awaiting SURVIVES: a release with a `staged` journal row and NO
#       later commit row is PINNED. p1 (5 dirs, no overrun) proves NO pin_hold
#       row fires; p1b (6 dirs, v1+v2 staged) proves the pin_hold row + held=
#       key and the over-retention (4 dirs remain) safe direction.
#   P2  every eviction JOURNALS:
#        (a) staged V + LATER commit row for W>V  -> V evicted, retention_evict
#            row with reason=superseded-by-promoted-<W>;
#        (b) plain oldest (no staged row)         -> evicted, retention_evict
#            row with reason=retention-oldest staged_row=no.
#       The close anchor is EVENT `commit` (not `promote`) — A's
#       _retention_closed_after / _retention_superseded_by gate on it.
#       MUTATION LEG (pin ii): sed-strip the journal_history_append call from a
#       COPY of lib.sh into scratch, re-run the two eviction assertions against
#       the stripped copy, and PROVE they REDden (the journal-row assertions
#       must fail) while eviction ITSELF still happens (non-vacuity).
#   P3  absent-target ceremony FAILS LOUDLY: promote.sh sandbox with a missing
#       VERSION exits 78, stderr carries reason=not-staged + retention guidance
#       + a re-stage path, a refusal row is journaled, and NO flip occurs.
#   P4  POINTER GATE (engages ONLY when an on-disk `current` symlink exists —
#       journal `current` alone is not a pointer): (a) healthy symlink v5 ==
#       journal current survives pressure; (b) dangling symlink vGONE ->
#       retention_refuse reason=pointer-dangling + ZERO evictions; (c) symlink
#       v1 vs journal current v5 -> retention_refuse reason=pointer-mismatch,
#       v1 survives, ZERO evictions.
#
# FROZEN FORMATS (modules A+B land before this pack at integration; base a1130e257
# does NOT contain them). The pack asserts these exact tokens, nothing broader:
#   staged row      : event `staged`, detail begins `release <V> staged at `
#   commit row      : EVENT `commit`, detail contains `promote <W> committed (`
#   eviction rows   : events retention_evict / retention_refuse / retention_pin_hold
#                     with details carrying version= / reason= / pointer_target=
#   reasons         : superseded-by-promoted-<W>, retention-oldest staged_row=no,
#                     staged-awaiting-ceremony-no-promoted-descendant (pin_hold),
#                     pointer-dangling / pointer-mismatch (refuse)
#   pin_hold detail : held=<V,...> reason=... keep=<N> total=<N>  (NOT version=)
#   refusal         : event `refusal`, detail contains reason=not-staged
#
# FENCES (hard)
#   * INSTALL_DIR is CREATED here by mktemp; the top fence refuses any resolved
#     path outside $PACK_ROOT and any path equal to /home/nea/agents-ensemble.
#   * ALL scratch state lives under $PACK_ROOT; the EXIT trap removes it.
#   * No daemon boot, no live-install write, no PostgreSQL, no network.
#   * bash only (pipefail); captured logs are *.txt (never *.log — .gitignore).
#   * The journal is built ONLY through the REAL helpers (journal_init /
#     journal_update / journal_history_append); no hand-written state.json.
#     manifest.json is a per-release FIXTURE input (no helper writes it).
#
# MUTATION MECHANISM (why the strip is non-vacuous)
#   make_stripped_lib() copies lib.sh and, inside the retention_evict function
#   body only, rewrites the token `journal_history_append` to a no-op shim
#   (awk tracks the function span by its column-0 closing brace, so multi-line
#   calls are neutralized too). The setup helpers are left intact, so the
#   stripped run still BUILDS staged/commit rows and still EVICTS dirs; only the
#   eviction JOURNAL ROWS vanish. core_rows_ok() therefore goes RED on the
#   stripped copy and GREEN on the real copy — proving the journal-row
#   assertions actually exercise A's journaling, not an always-true grep.
#
# Usage: bash tests/packs/release_retention_eviction_pack.sh
#   Optional integration seam: RETENTION_PACK_LIBSH / RETENTION_PACK_PROMOTE
#   point the pack at a MERGED A+B tree's scripts without editing that tree.
# Exit:  0 = all PASS; nonzero = at least one FAIL (or a refused fence).
# ============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Test seam: an integration gate can point this pack at a MERGED A+B tree
# without editing that tree (primary proof runs against the real merged files).
LIBSH="${RETENTION_PACK_LIBSH:-$REPO_ROOT/scripts/upgrade/lib.sh}"
PROMOTE="${RETENTION_PACK_PROMOTE:-$REPO_ROOT/scripts/upgrade/promote.sh}"

# ---- HARD FENCE: scratch-only INSTALL_DIR --------------------------------
LIVE_DIR=/home/nea/agents-ensemble
PACK_ROOT="$(mktemp -d /tmp/retention-evict-pack.XXXXXX)" || {
    echo "FATAL: cannot create mktemp scratch root" >&2; exit 90; }
INSTALL_DIR="$PACK_ROOT/install"
mkdir -p "$INSTALL_DIR/releases"

_fence_canon="$(cd "$INSTALL_DIR" 2>/dev/null && pwd -P)" || _fence_canon="$INSTALL_DIR"
case "$_fence_canon" in
    "$LIVE_DIR"|"$LIVE_DIR"/*)
        echo "REFUSE: INSTALL_DIR resolves to the LIVE install ($_fence_canon)" >&2
        rm -rf "$PACK_ROOT"; exit 90 ;;
esac
case "$_fence_canon" in
    "$PACK_ROOT"/*) ;;
    *)
        echo "REFUSE: INSTALL_DIR ($_fence_canon) is not under scratch root ($PACK_ROOT)" >&2
        rm -rf "$PACK_ROOT"; exit 90 ;;
esac
export INSTALL_DIR
# Override any ambient live-shaped identity; never boot the daemon here.
export ENSEMBLE_SELF_ENV=dev
export ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
export PYTHONDONTWRITEBYTECODE=1
trap 'rm -rf "$PACK_ROOT"' EXIT

# ---- counters -------------------------------------------------------------
PASS=0; FAIL=0; FAILED_LIST=""
_pass() { PASS=$((PASS + 1)); printf 'PASS: %s\n' "$1"; }
_fail() {
    FAIL=$((FAIL + 1)); FAILED_LIST="$FAILED_LIST\n  x $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -ge 3 ] && printf '        expected: %s\n        actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" exp="$2" got="$3"
    if [ "$exp" = "$got" ]; then _pass "$name"; else _fail "$name" "[$exp]" "[$got]"; fi
}
assert_contains() {
    local name="$1" needle="$2" hay="$3"
    case "$hay" in *"$needle"*) _pass "$name";; *) _fail "$name" "contains '$needle'" "[$hay]";; esac
}

# ---- contract preflight (clear diagnosis when A/B are not yet merged) ----
_preflight_missing=""
for tok in retention_evict retention_pin_hold retention_refuse; do
    grep -q "$tok" "$LIBSH" 2>/dev/null || _preflight_missing="$_preflight_missing lib.sh:$tok"
done
grep -q 'not-staged' "$PROMOTE" 2>/dev/null || _preflight_missing="$_preflight_missing promote.sh:not-staged"
if [ -n "$_preflight_missing" ]; then
    echo "PREREQUISITE NOT MET — missing contract tokens:$_preflight_missing" >&2
    echo "Modules A (retention-lib) / B (retention-stage-promote) must be merged before this pack can run." >&2
    exit 90
fi
echo "PREFLIGHT: contract tokens present (lib.sh + promote.sh)."

# ---- scratch builders (use the REAL helpers) ------------------------------
CASE_N=0
NEW_INSTALL=""
new_install() {
    CASE_N=$((CASE_N + 1))
    NEW_INSTALL="$PACK_ROOT/case-$CASE_N"
    mkdir -p "$NEW_INSTALL/releases"
}
seed_release() {  # <install> <ver> <staged_at ISO>
    local id="$1" ver="$2" iso="$3"
    mkdir -p "$id/releases/$ver"
    printf '{"staged_at":"%s"}\n' "$iso" > "$id/releases/$ver/manifest.json"
}
seed_five() {  # <install> — v1..v5 with increasing staged_at
    local id="$1"
    seed_release "$id" v1 2026-01-01T00:00:00Z
    seed_release "$id" v2 2026-02-01T00:00:00Z
    seed_release "$id" v3 2026-03-01T00:00:00Z
    seed_release "$id" v4 2026-04-01T00:00:00Z
    seed_release "$id" v5 2026-05-01T00:00:00Z
}
seed_six() {  # <install> — v1..v6 (p1b over-retention fixture)
    seed_five "$1"
    seed_release "$1" v6 2026-06-01T00:00:00Z
}

# ---- worker: sources a chosen lib.sh, builds state, sweeps, emits facts ---
# env: LIBSH (chosen lib), SC (scenario id), INSTALL_DIR (scratch install)
worker() {
    set -u
    . "$LIBSH" || { echo "WORKER_LIB_FAIL"; return 99; }
    journal_init || { echo "WORKER_INIT_FAIL"; return 98; }
    case "$SC" in
        p1|p2a|p2b|p4a|p4b|p4c)
            seed_five "$INSTALL_DIR"
            journal_update current '"v5"'
            journal_update previous '"v4"'
            ;;
        p1b)
            seed_six "$INSTALL_DIR"
            journal_update current '"v6"'
            journal_update previous '"v5"'
            ;;
    esac
    case "$SC" in
        p1)  journal_history_append staged "release v1 staged at 2026-01-01T00:00:00Z" ;;
        p1b) journal_history_append staged "release v1 staged at 2026-01-01T00:00:00Z"
             journal_history_append staged "release v2 staged at 2026-02-01T00:00:00Z" ;;
        p2a) journal_history_append staged  "release v1 staged at 2026-01-01T00:00:00Z"
             journal_history_append commit  "promote v2 committed (reason=promote)" ;;
        p2b) : ;;
        # A's pointer gate engages ONLY when a symlink exists; the journal
        # `current` alone is not an on-disk pointer. Plant it explicitly.
        p4a) ln -s v5 "$INSTALL_DIR/current" ;;
        p4b) journal_update current '"vGONE"'
             ln -s vGONE "$INSTALL_DIR/current" ;;
        p4c) ln -s v1 "$INSTALL_DIR/current" ;;
    esac
    retention_evict >/dev/null 2>&1
    echo "EVICT_RC=$?"
    local d n
    for d in "$INSTALL_DIR"/releases/*/; do
        [ -d "$d" ] || continue
        n="${d%/}"; n="${n##*/}"
        [ "$n" = current ] && continue
        echo "DIR=$n"
    done
    echo "CUR=$(_json_field "$(journal_read)" current)"
    journal_read | python3 -c 'import sys,json; d=json.load(sys.stdin); [print("HIST\t"+str(r.get("event",""))+"\t"+str(r.get("detail",""))) for r in d.get("history",[])]'
}

REPORT=""
run_case() { REPORT="$(LIBSH="$1" SC="$2" INSTALL_DIR="$3" worker 2>/dev/null)"; }
rhist() {  # <report> <event> <substr>
    printf '%s\n' "$1" | awk -F '\t' -v e="$2" -v s="$3" \
        '$1=="HIST" && $2==e && index($3,s){f=1} END{exit !f}'
}
rdir() { printf '%s\n' "$1" | grep -qx "DIR=$2"; }
rcur() { printf '%s\n' "$1" | sed -n 's/^CUR=//p'; }
rcount_dirs() { printf '%s\n' "$1" | grep -c '^DIR='; }

assert_dir() { if rdir "$2" "$3"; then _pass "$1"; else _fail "$1" "dir $3 present" "absent"; fi; }
assert_nodir() { if rdir "$2" "$3"; then _fail "$1" "dir $3 absent" "present"; else _pass "$1"; fi; }
assert_hist() { if rhist "$2" "$3" "$4"; then _pass "$1"; else _fail "$1" "row $3 ~ '$4'" "missing"; fi; }
assert_nohist() { if rhist "$2" "$3" "$4"; then _fail "$1" "NO row $3 ~ '$4'" "present"; else _pass "$1"; fi; }

core_rows_ok() {  # <report_p2a> <report_p2b> — the two journal-row assertions
    rhist "$1" retention_evict "reason=superseded-by-promoted-v2" || return 1
    rhist "$2" retention_evict "reason=retention-oldest staged_row=no" || return 1
    return 0
}

# ---- MUTATION: strip eviction journaling from a COPY of lib.sh ------------
STRIPPED="$PACK_ROOT/lib_stripped.sh"
awk '
    /^retention_evict[[:space:]]*\(\)[[:space:]]*\{/ { inside=1 }
    inside { gsub(/journal_history_append/, "_retention_pack_mut_noop") }
    inside && /^\}/ { inside=0 }
    { print }
' "$LIBSH" > "$STRIPPED"
printf '%s\n' '_retention_pack_mut_noop() { :; }' >> "$STRIPPED"
# Extract the LAST retention_evict definition (integration tree has one; a
# scratch harness may append an override — the last one is the live one).
extract_fn() {
    awk '/^retention_evict[[:space:]]*\(\)/{f=1;buf=""} f{buf=buf $0 "\n"} f&&/^\}/{last=buf;f=0} END{printf "%s", last}' "$1"
}
_orig_body="$(extract_fn "$LIBSH")"
_strp_body="$(extract_fn "$STRIPPED")"
case "$_orig_body" in
    *journal_history_append*) _pass "MUTATION original retention_evict journals" ;;
    *) _fail "MUTATION original retention_evict journals" "journal_history_append in body" "absent — strip is vacuous" ;;
esac
case "$_strp_body" in
    *journal_history_append*) _fail "MUTATION strip removed the call" "no journal_history_append" "still present" ;;
    *) _pass "MUTATION strip removed the call" ;;
esac
if bash -n "$STRIPPED" 2>/dev/null; then _pass "MUTATION stripped copy parses (bash -n)"; else _fail "MUTATION stripped copy parses (bash -n)"; fi

# ============================== P1 / P1b ===================================
new_install; i="$NEW_INSTALL"; run_case "$LIBSH" p1 "$i"
assert_dir    "P1 staged-awaiting v1 survives" "$REPORT" v1
assert_nodir  "P1 v2 evicted under pressure"  "$REPORT" v2
# 5 dirs / keep=3: evictables v2,v3 == evict_count 2, so NO overrun — pin_hold
# correctly never fires (frozen format carries held=/reason=, not version=).
assert_nohist "P1 no pin_hold when no overrun" "$REPORT" retention_pin_hold "reason=staged-awaiting"
assert_eq     "P1 keep=3 dirs remain" 3 "$(rcount_dirs "$REPORT")"

# p1b: SIX dirs, v1+v2 staged-awaiting (no commits) → only v3,v4 evictable of
# 3 needed ⇒ pin_hold; v1,v2 held, v5,v6 pinned ⇒ 4 dirs remain (over-retention).
new_install; i1b="$NEW_INSTALL"; run_case "$LIBSH" p1b "$i1b"
assert_nodir  "P1b v3 evicted" "$REPORT" v3
assert_nodir  "P1b v4 evicted" "$REPORT" v4
assert_dir    "P1b held v1 survives" "$REPORT" v1
assert_dir    "P1b held v2 survives" "$REPORT" v2
assert_eq     "P1b over-retention: 4 dirs remain" 4 "$(rcount_dirs "$REPORT")"
assert_hist   "P1b pin_hold reason=staged-awaiting-ceremony-no-promoted-descendant" "$REPORT" retention_pin_hold "reason=staged-awaiting-ceremony-no-promoted-descendant"
assert_hist   "P1b pin_hold held=v1" "$REPORT" retention_pin_hold "held=v1"

# ============================== P2 (a,b) ===================================
new_install; ia="$NEW_INSTALL"; run_case "$LIBSH" p2a "$ia"; RA="$REPORT"
new_install; ib="$NEW_INSTALL"; run_case "$LIBSH" p2b "$ib"; RB="$REPORT"
assert_nodir "P2a superseded v1 evicted" "$RA" v1
assert_hist "P2a reason=superseded-by-promoted-v2" "$RA" retention_evict "reason=superseded-by-promoted-v2"
assert_nodir "P2b plain-oldest v1 evicted" "$RB" v1
assert_hist "P2b reason=retention-oldest staged_row=no" "$RB" retention_evict "reason=retention-oldest staged_row=no"
if core_rows_ok "$RA" "$RB"; then _pass "P2 journal rows present (real lib)"; else _fail "P2 journal rows present (real lib)"; fi

# ============================== MUTATION RED ===============================
new_install; sa="$NEW_INSTALL"; run_case "$STRIPPED" p2a "$sa"; SA="$REPORT"
new_install; sb="$NEW_INSTALL"; run_case "$STRIPPED" p2b "$sb"; SB="$REPORT"
if core_rows_ok "$SA" "$SB"; then
    _fail "MUTATION journal-row assertions REDden on stripped copy" "RED (assertions fail)" "GREEN — mutation did NOT bite"
else
    _pass "MUTATION journal-row assertions REDden on stripped copy"
fi
# Non-vacuity: the stripped lib still EVICTS (only journaling was removed).
if rdir "$SA" v1 || rdir "$SB" v1; then
    _fail "MUTATION non-vacuity: eviction still occurred" "v1 absent in both stripped runs" "v1 survived"
else
    _pass "MUTATION non-vacuity: eviction still occurred"
fi

# ============================== P3 =========================================
new_install; p3="$NEW_INSTALL"
# journal must pre-exist for the refusal row (promote's not-staged check is
# pre-journal_init on the base tree; a virgin journal would lose the row).
INSTALL_DIR="$p3" bash -c '. "$1"; journal_init' _ "$LIBSH" >/dev/null 2>&1
_p3_before="$(INSTALL_DIR="$p3" bash -c '. "$1"; _json_field "$(journal_read)" current' _ "$LIBSH" 2>/dev/null)"
FH="$(mktemp -d "$PACK_ROOT/fakehome.XXXXXX")"
set +e
P3_OUT="$(env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT \
    HOME="$FH" INSTALL_DIR="$p3" PORT=8377 UP_TARGET=sandbox VERSION=vNOPE-absent \
    ENSEMBLE_SELF_ENV=dev ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    bash "$PROMOTE" sandbox 2>&1)"
P3_RC=$?
set -e
assert_eq "P3 absent target exits 78" 78 "$P3_RC"
assert_contains "P3 stderr reason=not-staged" "reason=not-staged" "$P3_OUT"
assert_contains "P3 stderr re-stage path (stage.sh)" "stage.sh" "$P3_OUT"
case "$(printf '%s' "$P3_OUT" | tr '[:upper:]' '[:lower:]')" in
    *retention*) _pass "P3 stderr retention guidance" ;;
    *) _fail "P3 stderr retention guidance" "mentions retention" "absent" ;;
esac
_p3_hist="$(INSTALL_DIR="$p3" bash -c '. "$1"; journal_read' _ "$LIBSH" 2>/dev/null)"
assert_contains "P3 journal refusal row reason=not-staged" "reason=not-staged" "$_p3_hist"
_p3_after="$(INSTALL_DIR="$p3" bash -c '. "$1"; _json_field "$(journal_read)" current' _ "$LIBSH" 2>/dev/null)"
assert_eq "P3 no flip (current unchanged)" "$_p3_before" "$_p3_after"
if [ -d "$p3/releases/vNOPE-absent" ]; then _fail "P3 no release dir for absent target"; else _pass "P3 no release dir for absent target"; fi

# ============================== P4 (a,b,c) =================================
new_install; pa="$NEW_INSTALL"; run_case "$LIBSH" p4a "$pa"
assert_dir "P4a healthy current v5 survives sweep" "$REPORT" v5
assert_eq  "P4a current still v5" v5 "$(rcur "$REPORT")"
assert_eq  "P4a keep=3 dirs remain" 3 "$(rcount_dirs "$REPORT")"
if [ -d "$pa/releases/v5" ]; then _pass "P4a symlink target dir exists post-sweep"; else _fail "P4a symlink target dir exists post-sweep"; fi

new_install; pb="$NEW_INSTALL"; run_case "$LIBSH" p4b "$pb"
assert_hist "P4b dangling reason=pointer-dangling" "$REPORT" retention_refuse "reason=pointer-dangling"
assert_hist "P4b dangling pointer_target=vGONE" "$REPORT" retention_refuse "pointer_target=vGONE"
assert_eq   "P4b ZERO dirs evicted (all 5 remain)" 5 "$(rcount_dirs "$REPORT")"

new_install; pc="$NEW_INSTALL"; run_case "$LIBSH" p4c "$pc"
assert_hist "P4c mismatch reason=pointer-mismatch" "$REPORT" retention_refuse "reason=pointer-mismatch"
assert_dir  "P4c pointer-targeted oldest v1 not evicted" "$REPORT" v1
assert_eq   "P4c ZERO evictions (all 5 dirs remain)" 5 "$(rcount_dirs "$REPORT")"

# ---- summary --------------------------------------------------------------
echo "------------------------------------------------------------"
printf 'RESULT: PASS=%d FAIL=%d\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%b\n' "$FAILED_LIST" >&2
    exit 1
fi
echo "ALL PASS"
exit 0
