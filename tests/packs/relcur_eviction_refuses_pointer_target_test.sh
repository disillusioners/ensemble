#!/usr/bin/env bash
# tests/packs/relcur_eviction_refuses_pointer_target_test.sh
#
# relcur-pointer M4 pin (b): EVICTION REFUSES POINTER TARGET (MERGED semantics).
#
# The sibling retention lane rewrote retention_evict (merged at aaa930622).
# New contract: pointer derivation reads the TOP-LEVEL current first, legacy
# releases/current alias as fallback; primary-pointer divergence/missing-target
# OR alias-divergence (top healthy AND alias dangling/!=journal current)
# => REFUSE ALL evictions (journaled refusal row + _warn, zero deletions).
#
# LEG A  divergent-alias fixture (alias -> oldest non-current release, top
#        healthy) => ZERO deletions + journaled refusal row naming
#        alias-divergence.
# LEG B  healthy-alias fixture (alias == journal current) => normal eviction
#        proceeds; hard-pin SKIP lines protect cur/prev/in_flight/pending_op/
#        $tgt, including the on-disk pointer-target SKIP line.
# LEG C  mutation: remove the alias-divergence refusal (scratch lib copy) =>
#        stale-alias-target eviction happens => LEG A RED.
#
# Fences: mktemp FAKE install dirs only; per-variable POSTGRES_* scrub;
# no live install contact; no daemon; no network; fake proc root.

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

# build a journal with v1 superseded by v4
make_journal() {  # <dir>
    printf '{"current":"v4","previous":"v3","in_flight":null,"pending_op":null,"history":[{"ts":"2026-01-01T00:00:00Z","event":"commit","detail":"promote v1 committed"},{"ts":"2026-01-02T00:00:00Z","event":"commit","detail":"promote v4 committed"}]}\n' > "$1/releases/state.json"
}

make_releases() {  # <dir>
    mkdir -p "$1/releases/v1" "$1/releases/v2" "$1/releases/v3" "$1/releases/v4"
    printf '{"staged_at":"2020-01-01T00:00:00Z"}\n' > "$1/releases/v1/manifest.json"
    touch -d "2020-01-01" "$1/releases/v1"
    touch -d "2021-01-01" "$1/releases/v2"
    touch -d "2022-01-01" "$1/releases/v3"
    touch -d "2023-01-01" "$1/releases/v4"
}

run_evict() {  # <dir> [lib]
    local d="$1" lib="${2:-$UPGRADE_DIR/lib.sh}"
    local procroot="$d/proc"; mkdir -p "$procroot"
    (
      export INSTALL_DIR="$d" REPO_ROOT="$REPO_ROOT" ENSEMBLE_RETENTION_PROC_ROOT="$procroot"
      . "$lib" >/dev/null 2>&1
      retention_evict
    ) 2>&1
}

# ── LEG A: divergent-alias => zero deletions + refusal row ────────────────
FIXT="$(mktemp -d -t relcur-evictA.XXXXXX)"
make_releases "$FIXT"
make_journal "$FIXT"
# alias -> v1 (oldest, divergent); top -> v4 (healthy)
ln -sfn "v1" "$FIXT/releases/current"
ln -sfn "releases/v4" "$FIXT/current"
OUT="$(run_evict "$FIXT")"
# zero deletions: all 4 release dirs remain
for v in v1 v2 v3 v4; do
    if [ -d "$FIXT/releases/$v" ]; then _pass "LEG A $v survives (zero deletions)"; else _fail "LEG A $v was deleted" "survives" "deleted"; fi
done
if grep -q 'retention_refuse' "$FIXT/releases/state.json"; then _pass "LEG A journaled refusal row present"; else _fail "LEG A refusal row missing" "present" "absent"; fi
if grep -q 'alias-divergence' "$FIXT/releases/state.json"; then _pass "LEG A refusal row names alias-divergence"; else _fail "LEG A alias-divergence not named" "present" "absent"; fi
if printf '%s\n' "$OUT" | grep -q 'REFUSING all evictions'; then _pass "LEG A _warn REFUSING all evictions"; else _fail "LEG A refuse warn missing" "present" "absent"; fi
rm -rf "$FIXT"

# ── LEG B: healthy-alias => normal eviction + hard-pin SKIP lines ──────────
FIXT="$(mktemp -d -t relcur-evictB.XXXXXX)"
make_releases "$FIXT"
make_journal "$FIXT"
# alias == journal current (healthy)
ln -sfn "v4" "$FIXT/releases/current"
ln -sfn "releases/v4" "$FIXT/current"
OUT="$(run_evict "$FIXT")"
# v1 evicted (superseded)
if [ ! -d "$FIXT/releases/v1" ]; then _pass "LEG B v1 evicted (superseded)"; else _fail "LEG B v1 not evicted" "evicted" "present"; fi
# pointer-target SKIP line
if printf '%s\n' "$OUT" | grep -q 'SKIP .* on-disk pointer target'; then _pass "LEG B on-disk pointer-target SKIP line present"; else _fail "LEG B pointer-target SKIP line missing" "present" "absent"; fi
# hard-pin for v4 (current==tgt) — the SKIP line covers it; also v3 (previous) held? no, pinned silently
# pin_hold row present (v2 held, no-journal-evidence)
if grep -q 'retention_pin_hold' "$FIXT/releases/state.json"; then _pass "LEG B retention_pin_hold row present"; else _fail "LEG B pin_hold row missing" "present" "absent"; fi
# v2 held no-journal-evidence
if printf '%s\n' "$OUT" | grep -q 'HOLD v2 — no-journal-evidence'; then _pass "LEG B v2 held no-journal-evidence"; else _fail "LEG B v2 hold missing" "present" "absent"; fi
# evict row present
if grep -q 'retention_evict' "$FIXT/releases/state.json"; then _pass "LEG B retention_evict row present"; else _fail "LEG B evict row missing" "present" "absent"; fi
rm -rf "$FIXT"

# ── LEG C: mutation — remove alias-divergence refusal => v1 evicted (RED) ─
FIXT="$(mktemp -d -t relcur-evictC.XXXXXX)"
make_releases "$FIXT"
make_journal "$FIXT"
ln -sfn "v1" "$FIXT/releases/current"
ln -sfn "releases/v4" "$FIXT/current"
LIBCOPY="$FIXT/lib.sh"
cp "$UPGRADE_DIR/lib.sh" "$LIBCOPY"
python3 - "$LIBCOPY" <<'PY'
import sys, re
p=sys.argv[1]; s=open(p).read()
pat = re.compile(r"    # Alias-divergence seam.*?        fi\n    fi\n", re.S)
new, n = pat.subn("", s, count=1)
assert n==1, "pattern not found (n=%d)" % n
open(p,'w').write(new)
PY
bash -n "$LIBCOPY" || { echo "mutation syntax FAIL"; exit 2; }
OUT="$(run_evict "$FIXT" "$LIBCOPY")"
if [ ! -d "$FIXT/releases/v1" ]; then
    _pass "LEG C mutation: alias-divergence removed => v1 evicted (RED proven)"
else
    _fail "LEG C mutation: v1 still present (pin non-discriminating)" "evicted" "present"
fi
if grep -q 'retention_refuse' "$FIXT/releases/state.json"; then
    _fail "LEG C mutation: refusal row still present" "absent" "present"
else
    _pass "LEG C mutation: no refusal row (refusal removed)"
fi
rm -rf "$FIXT"

printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
