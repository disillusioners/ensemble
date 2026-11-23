#!/usr/bin/env bash
# tests/packs/relcur_eviction_refuses_pointer_target_test.sh
#
# relcur-pointer M4 pin (b): EVICTION-REFUSES-POINTER-TARGET.
#
# Fixture with the alias -> oldest non-current release; run retention_evict;
# assert that dir SURVIVES + the pointer-pin log line. Mutation leg: remove
# the pin -> RED (dir evicted).

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

unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DATABASE_URL ENSEMBLE_SELF_ENV ENSEMBLE_UPGRADE_LIVE

run_evict() {  # <dir>  (sources lib.sh in subshell; captures stdout+stderr)
    ( export INSTALL_DIR="$1"; . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1; retention_evict; echo "rc=$?" )
}

# Build fixture: 4 releases; alias -> v1 (oldest, non-current)
make_fixture() {  # <dir>
    local d="$1"
    rm -rf "$d"; mkdir -p "$d/releases/v1" "$d/releases/v2" "$d/releases/v3" "$d/releases/v4"
    printf '{"current":"v4","previous":null}\n' > "$d/releases/state.json"
    # alias -> v1 (oldest non-current)
    ln -sfn "v1" "$d/releases/current"
}

# ── main leg: pin survives ───────────────────────────────────────────────
FIXT="$(mktemp -d -t relcur-evict.XXXXXX)"
make_fixture "$FIXT"
# give v1 oldest mtime (already by name but sort by mtime fallback; set mtimes)
# ensure v1 oldest
touch -d "2020-01-01" "$FIXT/releases/v1"
touch -d "2021-01-01" "$FIXT/releases/v2"
touch -d "2022-01-01" "$FIXT/releases/v3"
touch -d "2023-01-01" "$FIXT/releases/v4"

OUT="$(run_evict "$FIXT")"
RC="$(printf '%s\n' "$OUT" | grep -oE 'rc=[0-9]+' | head -1 | cut -d= -f2)"
assert_eq "retention_evict rc 0" "0" "$RC"
# v1 (alias target) survives
if [ -d "$FIXT/releases/v1" ]; then _pass "alias target v1 survives eviction"; else _fail "alias target v1 evicted (pin failed)" "survives" "evicted"; fi
# pointer-pin log line present
if printf '%s\n' "$OUT" | grep -q "pinned by releases/current alias pointer"; then
    _pass "pointer-pin log line present"
else
    _fail "pointer-pin log line missing" "present" "absent"
fi
# v1 specifically logged
if printf '%s\n' "$OUT" | grep -q "skipping v1"; then
    _pass "v1 skip logged"
else
    _fail "v1 skip not logged" "skipping v1" "absent"
fi
rm -rf "$FIXT"

# ── mutation leg: remove pin -> v1 evicted ───────────────────────────────
FIXT="$(mktemp -d -t relcur-evictmut.XXXXXX)"
make_fixture "$FIXT"
# remove the alias pin
rm -f "$FIXT/releases/current"
touch -d "2020-01-01" "$FIXT/releases/v1"
touch -d "2021-01-01" "$FIXT/releases/v2"
touch -d "2022-01-01" "$FIXT/releases/v3"
touch -d "2023-01-01" "$FIXT/releases/v4"

OUT="$(run_evict "$FIXT")"
if [ ! -d "$FIXT/releases/v1" ]; then
    _pass "mutation: without pin, v1 evicted (pin was load-bearing)"
else
    _fail "mutation: v1 survived even without pin (pin non-load-bearing)" "evicted" "survived"
fi
# log line absent
if printf '%s\n' "$OUT" | grep -q "pinned by releases/current alias pointer"; then
    _fail "mutation: pin log line still present without alias" "absent" "present"
else
    _pass "mutation: pin log line absent without alias"
fi
rm -rf "$FIXT"

printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
