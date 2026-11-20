#!/bin/bash
# ============================================================================
# tests/packs/rollback_target_chain_parity_test.sh — AC-D5-12
# (self-service-promote Wave 3): ONE fixture table resolved IDENTICALLY by
# launcher.sh, lib.sh, AND the Python resolver — 3-way parity.
#
# The TSV (tests/fixtures/rollback_target_chain_cases.tsv) pins resolved
# VALUES and rung ORDER for every case (R0 wins over conflicting
# R2/R3/R4), INCLUDING present-but-invalid R0 ⇒ HALT capture_invalid
# (never fall-through, F3) and lost-capture/new-format-absent ⇒ the
# documented fall-through disposition.
#
# Mutation legs (F3, MINOR-6): a planted mutation in ANY of the three
# resolvers must redden this pack — the parity pin is discriminating, not
# tautological.
#
# Run:   timeout 300 bash tests/packs/rollback_target_chain_parity_test.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
LAUNCHER="$REPO_ROOT/launcher.sh"
PYVENV="$REPO_ROOT/.venv/bin/python"
TSV="$REPO_ROOT/tests/fixtures/rollback_target_chain_cases.tsv"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
section() { printf '\n== %s ==\n' "$1"; }

# ── the fixture: a releases dir with every version the TSV names ──────────
FIX="$(mktemp -d -t d5parity.XXXXXX.$$)"
# T9 (tidier fold, 2026-11-20): the old trap was `rm -rf "$FIX"` alone — a
# SIGKILL/tool-timeout landing between a sed-plant and its restore left a
# planted defect in TRACKED sources AND destroyed the only backup (the
# exact class that poisoned the mid-wave verify run). Mirrors
# preflight_mutation_matrix_test.sh's restore_inflight: an EXIT/INT/TERM
# trap with a PLANTED_LIST-scoped reclaim — nothing planted ⇒ nothing
# touched (a refusing pre-run check fires the trap harmlessly).
PLANTED_LIST="$FIX/planted.list"
: > "$PLANTED_LIST"
restore_plants() {
    [ -s "$PLANTED_LIST" ] || return 0
    local f bak
    while IFS="$(printf "\t")" read -r f bak; do
        [ -n "$f" ] || continue
        if [ -f "$bak" ]; then
            cp "$bak" "$f" && printf 'trap: restored plant %s\n' "$f" >&2
        fi
    done < "$PLANTED_LIST"
    : > "$PLANTED_LIST"
}
pack_cleanup() {
    restore_plants
    rm -rf "$FIX"
}
trap pack_cleanup EXIT INT TERM
for v in vA vB vC vD vE vT; do
    mkdir -p "$FIX/releases/$v"
    printf '{"manifest_version":"1.0.0","binary_version":"%s","rollback_safe":true}\n' "$v" \
        > "$FIX/releases/$v/manifest.json"
done
# vZ deliberately ABSENT (the missing-dir case).

# ── normalize the TSV into an args file: tgt live cap to cur dis prev ─────
# `-` (absent) maps to the literal `null` — every resolver treats `null`
# as ABSENT at every rung, and a tab-IFS `read` collapses EMPTY fields
# (empirically), so the args file never carries empty columns.
ARGS="$FIX/args.tsv"
MAP="$FIX/name-map.tsv"
awk -F'\t' '
    /^#/ { next }
    NF < 8 { next }
    {
        tgt=$2; live=$3; cap=$4; to=$5; cur=$6; dis=$7; prev=$8
        if (live == "-") live = "null"; if (cap == "-") cap = "null"
        if (to == "-") to = "null"; if (cur == "-") cur = "null"
        if (dis == "-") dis = "null"; if (prev == "-") prev = "null"
        printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\n", tgt, live, cap, to, cur, dis, prev > "'"$ARGS"'"
        printf "%s\t%s\t%s\t%s\n", $1, $9, $10, $11 > "'"$MAP"'"
    }
' "$TSV"
N_ROWS="$(wc -l < "$ARGS" | tr -d ' ')"
[ "$N_ROWS" -ge 8 ] || { echo "FATAL: TSV produced $N_ROWS rows (need >= 8)"; exit 1; }

# ── engine 1: lib.sh ───────────────────────────────────────────────────────
run_lib() {
    INSTALL_DIR="$FIX" bash -c '
        . "'"$LIBSH"'" >/dev/null 2>&1
        while IFS="$(printf "\t")" read -r tgt live cap to cur dis prev; do
            out="$(_rollback_target_resolve "'"$FIX"'/releases" "$tgt" "$live" "$cap" "$to" "$cur" "$dis" "$prev")"
            rc=$?
            printf "%s\t%s\n" "$rc" "$out"
        done < "'"$ARGS"'"
    ' 2>/dev/null
}

# ── engine 2: launcher.sh (the _js_ twin) ─────────────────────────────────
run_launcher() {
    INSTALL_DIR="$FIX" bash -c '
        . "'"$LAUNCHER"'" >/dev/null 2>&1
        while IFS="$(printf "\t")" read -r tgt live cap to cur dis prev; do
            out="$(_js_rollback_target_resolve "'"$FIX"'/releases" "$tgt" "$live" "$cap" "$to" "$cur" "$dis" "$prev")"
            rc=$?
            printf "%s\t%s\n" "$rc" "$out"
        done < "'"$ARGS"'"
    ' 2>/dev/null
}

# ── engine 3: the Python resolver ──────────────────────────────────────────
run_python() {
    while IFS="$(printf "\t")" read -r tgt live cap to cur dis prev; do
        out="$("$PYVENV" -m daemon.services.rollback_target_chain \
            --releases-dir "$FIX/releases" --target "$tgt" \
            --live-link "$live" --capture "$cap" --to "$to" \
            --current "$cur" --displaced "$dis" --previous "$prev" 2>/dev/null)"
        rc=$?
        printf '%s\t%s\n' "$rc" "$out"
    done < "$ARGS"
}

# render one engine output line to the canonical expectation string:
#   rc=0 + "resolve vD captured"  -> "resolve vD captured"
#   rc=1 + "halt capture_invalid" -> "halt capture_invalid"
render() {
    # $1=rc $2=out
    if [ "$1" = "0" ]; then printf 'resolve %s' "${2#resolve }"
    else printf 'halt %s' "${2#halt }"; fi
}

section "3-way parity over $N_ROWS cases (AC-D5-12)"

LIB_OUT="$(run_lib)"
LAUNCHER_OUT="$(run_launcher)"
PY_OUT="$(run_python)"

paste "$ARGS" "$MAP" /dev/null 2>/dev/null >/dev/null || true
# zip through the four streams in lockstep
LIB_N="$(printf '%s\n' "$LIB_OUT")"
LAU_N="$(printf '%s\n' "$LAUNCHER_OUT")"
PY_N="$(printf '%s\n' "$PY_OUT")"

i=0
while IFS="$(printf "\t")" read -r tgt live cap to cur dis prev \
      && IFS="$(printf "\t")" read -r name expkind expval expsrc <&3 \
      && IFS="$(printf "\t")" read -r lrc lout <&4 \
      && IFS="$(printf "\t")" read -r urc uout <&5 \
      && IFS="$(printf "\t")" read -r prc pout <&6; do
    i=$(( i + 1 ))
    if [ "$expkind" = "resolve" ]; then
        expect="resolve $expval $expsrc"
        exprc=0
    else
        expect="halt $expval"
        exprc=1
    fi
    lrender="$(render "$lrc" "$lout")"
    urender="$(render "$urc" "$uout")"
    prender="$(render "$prc" "$pout")"
    assert_eq "[$name] lib.sh resolves per the TSV" "$expect" "$lrender"
    assert_eq "[$name] launcher.sh twin resolves per the TSV" "$expect" "$urender"
    assert_eq "[$name] the Python resolver resolves per the TSV" "$expect" "$prender"
    assert_eq "[$name] lib.sh rc matches kind" "$exprc" "$lrc"
    assert_eq "[$name] python rc matches kind" "$exprc" "$prc"
done < "$ARGS" 3< "$MAP" 4< <(printf '%s\n' "$LIB_N") 5< <(printf '%s\n' "$LAU_N") 6< <(printf '%s\n' "$PY_N")

assert_eq "every TSV row was exercised" "$N_ROWS" "$i"

# ── mutation legs (F3, MINOR-6): each planted mutation MUST redden ────────
section "mutation legs — a broken resolver MUST redden the parity pin"

restore_file() {
    # $1 = file, $2 = sha256 captured before the mutation, $3 = backup path

    cp "$3" "$1"
    now_sha="$(sha256sum "$1" | cut -d' ' -f1)"
    assert_eq "mutation restore byte-proven ($1)" "$2" "$now_sha"
}

mut_lib_sha="$(sha256sum "$LIBSH" | cut -d' ' -f1)"
cp "$LIBSH" "$FIX/lib.sh.mutation-backup"
printf '%s\t%s\n' "$LIBSH" "$FIX/lib.sh.mutation-backup" >> "$PLANTED_LIST"
# M-LIB (F3): the invalid-R0 halt collapses to a fall-through
sed -i "s|printf 'halt capture_invalid'|: # MUTATION|" "$LIBSH"
if grep -q "MUTATION" "$LIBSH"; then
    M_LIB_OUT="$(run_lib)"
    m_bad=0
    while IFS="$(printf "\t")" read -r tgt live cap to cur dis prev; do
        [ "$cap" = "vT" ] || [ "$cap" = "vZ" ] || [ "$cap" = "vB" ] || continue
        : # the invalid-capture rows
    done < "$ARGS"
    # simpler discriminator: any row whose expectation is capture_invalid
    # must now MISMATCH — count capture_invalid expectations produced
    produced="$(printf '%s\n' "$M_LIB_OUT" | grep -c 'halt capture_invalid' || true)"
    assert_eq "M-LIB: the capture_invalid halts VANISH (the mutation is discriminating)" "0" "$produced"
else
    _fail "M-LIB: the mutation did not plant"
fi
restore_file "$LIBSH" "$mut_lib_sha" "$FIX/lib.sh.mutation-backup"

mut_lau_sha="$(sha256sum "$LAUNCHER" | cut -d' ' -f1)"
cp "$LAUNCHER" "$FIX/launcher.sh.mutation-backup"
printf '%s\t%s\n' "$LAUNCHER" "$FIX/launcher.sh.mutation-backup" >> "$PLANTED_LIST"
# M-LAU (rung order): R0 wins → demoted to "current wins" (drop the
# short-circuit by making R0 never set the candidate)
sed -i "s|cand=\"\$cap\"; src=\"captured\"|: # MUTATION|" "$LAUNCHER"
if grep -q "MUTATION" "$LAUNCHER"; then
    M_LAU_OUT="$(run_launcher)"
    produced="$(printf '%s\n' "$M_LAU_OUT" | grep -c 'resolve vD captured' || true)"
    assert_eq "M-LAU: the r0-wins row no longer resolves at the captured rung" "0" "$produced"
else
    _fail "M-LAU: the mutation did not plant"
fi
restore_file "$LAUNCHER" "$mut_lau_sha" "$FIX/launcher.sh.mutation-backup"

mut_py_sha="$(sha256sum "$REPO_ROOT/daemon/services/rollback_target_chain.py" | cut -d' ' -f1)"
cp "$REPO_ROOT/daemon/services/rollback_target_chain.py" "$FIX/rollback_target_chain.py.mutation-backup"
printf '%s\t%s\n' "$REPO_ROOT/daemon/services/rollback_target_chain.py" "$FIX/rollback_target_chain.py.mutation-backup" >> "$PLANTED_LIST"
# M-PY (AC-D5-7): the R1-vs-R0 halt is removed
sed -i "s|if cand and r1to != cand:|if False and cand and r1to != cand:|" "$REPO_ROOT/daemon/services/rollback_target_chain.py"
if grep -q "if False and cand" "$REPO_ROOT/daemon/services/rollback_target_chain.py"; then
    # CYCLE-2 MAJOR-1: row selection is NAME-ANCHORED (the numeric/none
    # fold rows were inserted mid-table and shifted every later position —
    # a hardcoded 7p broke; anchor on the TSV's case name, never the
    # positional index).
    R1_CONFLICT_ROW="$(awk -F'\t' '/^#/ {next} NF>=8 {n++; if ($1=="r1-valid-conflicts-r0") {print n; exit}}' "$TSV")"
    if [ -n "$R1_CONFLICT_ROW" ]; then
        M_PY_OUT="$(run_python)"
        # the r1-valid-conflicts-r0 row: the mutation turns its halt into
        # a resolve (R0 wins silently) — the parity pin catches the flip.
        m_row="$(printf '%s\n' "$M_PY_OUT" | sed -n "${R1_CONFLICT_ROW}p")"
        assert_eq "M-PY: the r1-conflict row FLIPS halt->resolve under the mutation (discriminating)" \
            "0	resolve vD captured" "$m_row"
    else
        _fail "M-PY: the r1-valid-conflicts-r0 row is missing from the TSV"
    fi
else
    _fail "M-PY: the mutation did not plant"
fi
restore_file "$REPO_ROOT/daemon/services/rollback_target_chain.py" "$mut_py_sha" "$FIX/rollback_target_chain.py.mutation-backup"

# ── summary ────────────────────────────────────────────────────────────────
printf '\n== summary ==\n'
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '=== ALL TESTS PASSED ===\n'
exit 0
