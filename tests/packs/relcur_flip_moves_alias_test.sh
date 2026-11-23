#!/usr/bin/env bash
# tests/packs/relcur_flip_moves_alias_test.sh
#
# relcur-pointer M4 pin (a): FLIP-MOVES-ALIAS + the r2 COMMIT-SITE CONTRACT.
#
# LEG 1  flip moves the alias: after flip, releases/current target == flipped
#        ver == journal current; top-level current retargeted.
# LEG 2  healthy-flip-silence (r2): after a healthy flip+commit in the fixture
#        (journal current set at COMMIT, not flip), ZERO pointer-alias warns
#        AND ZERO pointer-alias journal rows — no alarm fatigue.
# LEG 3  commit-site detection (r2): alias deliberately wrong AT COMMIT =>
#        the exact warn family `pointer alias: releases/current -> '<wrong>'
#        != journal current '<right>'`, a `pointer-alias:` journal warn row,
#        and the named remedy `scripts/upgrade/pointer-alias-repair.sh`.
# LEG 4  mutation: revert the alias hunk => alias does not move (RED).
# LEG 5  mutation: reintroduce a flip-site journal comparison => LEG 2 RED.
# LEG 6  mutation: gut pointer_alias_assert_commit => LEG 3 RED.
#
# Fences: mktemp FAKE install dirs only; per-variable POSTGRES_* scrub; no
# live install contact; no daemon; no network.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  x $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}

case "${TMPDIR:-/tmp}" in /home/nea/agents-ensemble*) echo "REFUSING live-shaped TMPDIR"; exit 2 ;; esac
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE

# ── helpers ───────────────────────────────────────────────────────────────
make_fixture() {  # <dir> <journal-current-at-start>
    local d="$1" cur="$2"
    rm -rf "$d"; mkdir -p "$d/releases/vA" "$d/releases/vB"
    printf '{"current":"%s","previous":null,"history":[]}\n' "$cur" > "$d/releases/state.json"
    ln -sfn "releases/vA" "$d/current"
}

run_flip() {  # <dir> <ver> [lib]
    local d="$1" ver="$2" lib="${3:-$UPGRADE_DIR/lib.sh}"
    ( export INSTALL_DIR="$d" REPO_ROOT="$REPO_ROOT"; . "$lib" >/dev/null 2>&1; atomic_flip "$ver"; echo "rc=$?" )
}

# run_ceremony <dir> <ver> <lib> <alias-at-commit> <outfile>
#   flip, optionally re-point the alias AT COMMIT ("__KEEP__" = leave it),
#   journal_set_current, then pointer_alias_assert_commit. All output -> outfile.
run_ceremony() {
    local d="$1" ver="$2" lib="$3" alias_at_commit="$4" out="$5"
    (
      export INSTALL_DIR="$d" REPO_ROOT="$REPO_ROOT"
      . "$lib"
      atomic_flip "$ver"
      if [ "$alias_at_commit" != "__KEEP__" ]; then
          ln -sfn "$alias_at_commit" "$d/releases/current"
      fi
      journal_set_current "$ver"
      pointer_alias_assert_commit "$ver" "$ver"
    ) > "$out" 2>&1
}

# silence_ok <outfile> <statejson> — 0 iff NO pointer-alias alarm anywhere.
silence_ok() {
    local out="$1" state="$2"
    grep -Eiq 'pointer-alias|pointer alias:|ASSERT FAILED' "$out" && return 1
    grep -qi 'pointer-alias' "$state" && return 1
    return 0
}

# detect_ok <outfile> <statejson> <wrong> <right> — 0 iff all 3 commit-site
# detection properties hold (warn family, journal row, named remedy).
detect_ok() {
    local out="$1" state="$2" wrong="$3" right="$4"
    grep -Fq "pointer alias: releases/current -> '$wrong' != journal current '$right'" "$out" || return 1
    grep -Fq 'pointer-alias:' "$state" || return 1
    grep -Fq 'scripts/upgrade/pointer-alias-repair.sh' "$out" || return 1
    return 0
}

# ── LEG 1: flip moves the alias ──────────────────────────────────────────
FIXT="$(mktemp -d -t relcur-flip.XXXXXX)"
make_fixture "$FIXT" vB
OUT="$(run_flip "$FIXT" vB)"
RC="$(printf '%s\n' "$OUT" | grep -oE 'rc=[0-9]+' | head -1 | cut -d= -f2)"
assert_eq "L1 flip rc 0" "0" "$RC"
assert_eq "L1 top-level current -> releases/vB" "releases/vB" "$(readlink "$FIXT/current")"
assert_eq "L1 alias releases/current -> vB" "vB" "$(readlink "$FIXT/releases/current")"
JOURNAL_CUR="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("current"))' "$FIXT/releases/state.json")"
assert_eq "L1 journal current == vB" "vB" "$JOURNAL_CUR"
assert_eq "L1 alias basename == journal current" "$JOURNAL_CUR" "$(basename "$(readlink "$FIXT/releases/current")")"
rm -rf "$FIXT"

# ── LEG 2: healthy-flip-silence (r2 contract) ────────────────────────────
# journal current is still vA at flip (ceremony window); it is set to vB at
# COMMIT. The r1 flip-site journal compare warned here ALWAYS (alarm fatigue).
FIXT="$(mktemp -d -t relcur-fliphealthy.XXXXXX)"
make_fixture "$FIXT" vA
OUT="$FIXT/ceremony.out"
run_ceremony "$FIXT" vB "$UPGRADE_DIR/lib.sh" "__KEEP__" "$OUT"
if silence_ok "$OUT" "$FIXT/releases/state.json"; then
    _pass "L2 healthy flip+commit: ZERO pointer-alias warns + ZERO journal rows"
else
    _fail "L2 healthy path emitted a pointer-alias alarm" "silent" "$(grep -Ei 'pointer-alias|pointer alias:|ASSERT FAILED' "$OUT" | head -3)"
fi
# explicit greps (non-vacuity of silence_ok)
assert_eq "L2 output pointer-alias token count == 0" "0" "$(grep -Eic 'pointer-alias|pointer alias:|ASSERT FAILED' "$OUT")"
assert_eq "L2 journal pointer-alias row count == 0" "0" "$(grep -ic 'pointer-alias' "$FIXT/releases/state.json")"
rm -rf "$FIXT"

# ── LEG 3: commit-site detection (r2 contract) ───────────────────────────
FIXT="$(mktemp -d -t relcur-flipcommit.XXXXXX)"
make_fixture "$FIXT" vA
OUT="$FIXT/ceremony.out"
# alias re-pointed to vA between flip (-> vB) and commit
run_ceremony "$FIXT" vB "$UPGRADE_DIR/lib.sh" "vA" "$OUT"
if detect_ok "$OUT" "$FIXT/releases/state.json" "vA" "vB"; then
    _pass "L3 commit-site detects wrong alias (warn family + journal row + remedy)"
else
    _fail "L3 commit-site detection incomplete" "all 3 properties" "out=$(grep -i 'pointer alias' "$OUT" | head -1) state=$(grep -i 'pointer-alias' "$FIXT/releases/state.json" | head -1)"
fi
if grep -Fq "pointer alias: releases/current -> 'vA' != journal current 'vB'" "$OUT"; then
    _pass "L3 exact warn family (wrong=vA right=vB)"
else
    _fail "L3 exact warn family missing" "pointer alias: releases/current -> 'vA' != journal current 'vB'" "$(grep -i 'pointer alias' "$OUT" | head -1)"
fi
if grep -Fq 'pointer-alias:' "$FIXT/releases/state.json"; then
    _pass "L3 pointer-alias journal warn row appended"
else
    _fail "L3 pointer-alias journal warn row missing" "present" "absent"
fi
if grep -Fq 'scripts/upgrade/pointer-alias-repair.sh' "$OUT"; then
    _pass "L3 named remedy in operator warn"
else
    _fail "L3 named remedy missing" "scripts/upgrade/pointer-alias-repair.sh" "absent"
fi
rm -rf "$FIXT"

# ── LEG 4: mutation — revert alias hunk ──────────────────────────────────
FIXT="$(mktemp -d -t relcur-flipmut.XXXXXX)"
make_fixture "$FIXT" vB
LIBCOPY="$FIXT/lib.sh"
cp "$UPGRADE_DIR/lib.sh" "$LIBCOPY"
python3 - "$LIBCOPY" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
s=s.replace('    if ln -sfn "$ver" "$_rel/current.alias.new.$$" 2>/dev/null; then',
            '    if false; then  # MUTATED: alias ln reverted')
open(p,'w').write(s)
PY
OUT="$(run_flip "$FIXT" vB "$LIBCOPY")"
ALIAS_NOW="$(readlink "$FIXT/releases/current" 2>/dev/null || echo '<absent>')"
if [ "$ALIAS_NOW" != "vB" ]; then
    _pass "L4 mutation: reverting alias hunk keeps alias from moving (RED proven)"
else
    _fail "L4 mutation: alias still moved after hunk revert" "not vB" "$ALIAS_NOW"
fi
rm -rf "$FIXT"

# ── LEG 5: mutation — reintroduce flip-site journal comparison => L2 RED ──
FIXT="$(mktemp -d -t relcur-flipmut2.XXXXXX)"
make_fixture "$FIXT" vA
LIBCOPY="$FIXT/lib.sh"
cp "$UPGRADE_DIR/lib.sh" "$LIBCOPY"
python3 - "$LIBCOPY" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
needle = '    return 0\n}\n\n# _pointer_alias_basename'
assert needle in s, "anchor for flip-site mutation not found"
inject = '''    # MUTATED: r1 flip-site journal comparison (alarm-fatigue defect)
    local _m_jcur _m_jbase
    _m_jcur="$(journal_read 2>/dev/null)" || _m_jcur=""
    _m_jbase="$(_json_field "$_m_jcur" current 2>/dev/null)" || _m_jbase=""
    [ "$_m_jbase" = "null" ] && _m_jbase=""
    if [ -n "$_m_jbase" ] && [ -n "$_alias_base" ] && [ "$_m_jbase" != "$_alias_base" ]; then
        _warn "atomic_flip: ASSERT FAILED alias basename '$_alias_base' != journal current '$_m_jbase' (ceremony window expected)"
        journal_history_append warn "pointer-alias: ASSERT alias '$_alias_base' != journal '$_m_jbase'" >/dev/null 2>&1 || true
    fi
'''
s=s.replace(needle, inject + needle, 1)
open(p,'w').write(s)
PY
OUT="$FIXT/ceremony.out"
run_ceremony "$FIXT" vB "$LIBCOPY" "__KEEP__" "$OUT"
if silence_ok "$OUT" "$FIXT/releases/state.json"; then
    _fail "L5 mutation: healthy path still silent (pin non-discriminating)" "alarm present" "silent"
else
    _pass "L5 mutation: reintroduced flip-site compare breaks L2 silence (RED proven)"
fi
rm -rf "$FIXT"

# ── LEG 6: mutation — gut pointer_alias_assert_commit => L3 RED ──────────
FIXT="$(mktemp -d -t relcur-flipmut3.XXXXXX)"
make_fixture "$FIXT" vA
LIBCOPY="$FIXT/lib.sh"
cp "$UPGRADE_DIR/lib.sh" "$LIBCOPY"
python3 - "$LIBCOPY" <<'PY'
import sys
p=sys.argv[1]; lines=open(p).read().splitlines(keepends=True)
start=None
for i,l in enumerate(lines):
    if l.startswith('pointer_alias_assert_commit() {'):
        start=i; break
assert start is not None, "pointer_alias_assert_commit not found"
end=None
for j in range(start+1,len(lines)):
    if lines[j].rstrip('\n')=='}':
        end=j; break
assert end is not None
lines[start:end+1]=['pointer_alias_assert_commit() {\n','    return 0  # MUTATED: gutted\n','}\n']
open(p,'w').writelines(lines)
PY
OUT="$FIXT/ceremony.out"
run_ceremony "$FIXT" vB "$LIBCOPY" "vA" "$OUT"
if detect_ok "$OUT" "$FIXT/releases/state.json" "vA" "vB"; then
    _fail "L6 mutation: commit-site detection still works (pin non-discriminating)" "RED" "GREEN"
else
    _pass "L6 mutation: gutted commit-site assert breaks L3 detection (RED proven)"
fi
rm -rf "$FIXT"

printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
