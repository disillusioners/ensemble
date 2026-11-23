#!/usr/bin/env bash
# tests/packs/relcur_flip_moves_alias_test.sh
#
# relcur-pointer M4 pin (a): FLIP-MOVES-ALIAS.
#
# Exercising the lib.sh atomic_flip helper in a mktemp FAKE install dir:
# after flip, the releases/current alias target == flipped ver == journal current.
# Mutation leg: revert the alias hunk -> RED (alias not moved).
#
# Fences: mktemp FAKE install dir only; per-variable POSTGRES_* scrub; no live
# install contact; no daemon; no network.

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

# Escape guard
case "${TMPDIR:-/tmp}" in /home/nea/agents-ensemble*) echo "REFUSING live-shaped TMPDIR"; exit 2 ;; esac

# Per-variable scrub
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE

run_flip() {  # <dir> <ver>  (sources lib.sh in subshell)
    ( export INSTALL_DIR="$1"; . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1; atomic_flip "$2"; echo "rc=$?" )
}
extract_rc() { printf '%s\n' "$1" | grep -oE 'rc=[0-9]+' | head -1 | cut -d= -f2; }

# ── main leg ──────────────────────────────────────────────────────────────
FIXT="$(mktemp -d -t relcur-flip.XXXXXX)"
rm -rf "$FIXT"; mkdir -p "$FIXT/releases/vA" "$FIXT/releases/vB"
printf '{"version":"vA"}\n' > "$FIXT/releases/vA/manifest.json"
printf '{"version":"vB"}\n' > "$FIXT/releases/vB/manifest.json"
printf '{"current":"vA","previous":null}\n' > "$FIXT/releases/state.json"
# journal current vA; alias absent initially; top-level current -> vA
ln -sfn "releases/vA" "$FIXT/current"

OUT="$(run_flip "$FIXT" vB)"
RC="$(extract_rc "$OUT")"
assert_eq "flip rc 0" "0" "$RC"
# top-level current -> releases/vB
assert_eq "top-level current -> releases/vB" "releases/vB" "$(readlink "$FIXT/current")"
# alias releases/current -> vB
assert_eq "alias releases/current -> vB" "vB" "$(readlink "$FIXT/releases/current")"
# journal current unchanged vA? No — flip does not write journal; journal stays vA.
# Wait: brief says alias target == flipped ver == journal current. But atomic_flip
# does NOT update journal. The repair script updates alias to journal current.
# For lib.sh flip, journal is NOT updated by flip. Hmm.
#
# Re-read brief (a): "after flip, alias releases/current target == flipped ver == journal current"
# This implies the journal current must equal flipped ver. So we should set journal
# current to vB BEFORE? No—flip takes ver arg. The test scenario: flip to vB while
# journal current is vB? But then alias already? Let me reconsider.
#
# Actually atomic_flip is called during promote with ver=new version, and journal
# current is updated to new version by promote BEFORE/AROUND flip. So at flip time
# journal current == ver. For our test, set journal current = vB initially, then
# flip to vB. But then alias absent initially; after flip alias -> vB == journal.
# However top-level current before flip -> vA (old). Flip to vB. Journal vB.
# That satisfies: alias == flipped(vB) == journal(vB).

rm -rf "$FIXT"; mkdir -p "$FIXT/releases/vA" "$FIXT/releases/vB"
printf '{"current":"vB","previous":"vA"}\n' > "$FIXT/releases/state.json"
ln -sfn "releases/vA" "$FIXT/current"

OUT="$(run_flip "$FIXT" vB)"
RC="$(extract_rc "$OUT")"
assert_eq "flip rc 0 (journal current vB)" "0" "$RC"
assert_eq "top-level current -> releases/vB" "releases/vB" "$(readlink "$FIXT/current")"
assert_eq "alias releases/current -> vB" "vB" "$(readlink "$FIXT/releases/current")"
JOURNAL_CUR="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("current"))' "$FIXT/releases/state.json")"
assert_eq "journal current == vB" "vB" "$JOURNAL_CUR"
assert_eq "alias basename == journal current" "$JOURNAL_CUR" "$(basename "$(readlink "$FIXT/releases/current")")"
rm -rf "$FIXT"

# ── mutation leg: revert alias hunk -> RED ────────────────────────────────
# Build a scratch copy of lib.sh with the alias mv removed; run flip; the
# alias should NOT move (proving the hunk is load-bearing).
FIXT="$(mktemp -d -t relcur-flipmut.XXXXXX)"
rm -rf "$FIXT"; mkdir -p "$FIXT/releases/vA" "$FIXT/releases/vB"
printf '{"current":"vB","previous":"vA"}\n' > "$FIXT/releases/state.json"
ln -sfn "releases/vA" "$FIXT/current"

LIBCOPY="$FIXT/lib.sh"
cp "$UPGRADE_DIR/lib.sh" "$LIBCOPY"
# Revert the alias hunk: delete the whole pointer-alias maintenance block
python3 - "$LIBCOPY" <<'PY'
import sys
p=sys.argv[1]
s=open(p).read()
s=s.replace('    if ln -sfn "$ver" "$_rel/current.alias.new.$$" 2>/dev/null; then',
            '    if false; then  # MUTATED: alias ln reverted')
open(p,'w').write(s)
PY

# Run flip using mutated lib
OUT="$( ( export INSTALL_DIR="$FIXT"; . "$LIBCOPY" >/dev/null 2>&1; atomic_flip vB; echo "rc=$?" ) )"
RC="$(extract_rc "$OUT")"
# top-level still flips (the hunk is only alias), alias should NOT be vB
TOP_NOW="$(readlink "$FIXT/current" 2>/dev/null)"
ALIAS_NOW="$(readlink "$FIXT/releases/current" 2>/dev/null || echo '<absent>')"
# Mutation leg passes if alias did NOT move to vB
if [ "$ALIAS_NOW" != "vB" ]; then
    _pass "mutation: reverting alias hunk keeps alias from moving (RED proven)"
else
    _fail "mutation: alias still moved after hunk revert (pin non-discriminating)" "not vB" "$ALIAS_NOW"
fi
rm -rf "$FIXT"

printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
