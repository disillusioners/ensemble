#!/usr/bin/env bash
# tests/packs/relcur_repair_idempotent_test.sh
#
# relcur-pointer M4 pin (d): REPAIR-IDEMPOTENT.
#
# Run scripts/upgrade/pointer-alias-repair.sh TWICE against a FAKE install dir
# with a stubbed livez probe: run2 = no-op success with inode UNCHANGED.
# Wrong-precondition legs (missing dir, version mismatch) refuse with named
# reasons and non-zero exit.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPAIR="$REPO_ROOT/scripts/upgrade/pointer-alias-repair.sh"

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

# Escape guard
case "${TMPDIR:-/tmp}" in /home/nea/agents-ensemble*) echo "REFUSING live-shaped TMPDIR"; exit 2 ;; esac

# Build a fake install dir with journal current=vX, releases/vX dir, top-level
# current -> vY (wrong), alias absent initially.
make_fixture() {  # <dir> <ver>
    local d="$1" ver="$2"
    rm -rf "$d"; mkdir -p "$d/releases/$ver"
    printf '{"current":"%s","previous":null}\n' "$ver" > "$d/releases/state.json"
    # top-level current points somewhere else to force a repair on run1
    ln -sfn "releases/vOLD" "$d/current" 2>/dev/null || true
    mkdir -p "$d/releases/vOLD" 2>/dev/null || true
}

# ── idempotency leg ──────────────────────────────────────────────────────
FIXT="$(mktemp -d -t relcur-repair.XXXXXX)"
make_fixture "$FIXT" "v0.31.42"
# top-level current -> vOLD; alias absent. Run1 repairs alias to v0.31.42.
SKIP_LIVEZ=1 "$REPAIR" --install-dir "$FIXT" --skip-livez >/tmp/rr1.$$ 2>&1
RC1=$?
OUT1="$(cat /tmp/rr1.$$)"
assert_eq "run1 rc 0" "0" "$RC1"
assert_eq "run1 alias -> v0.31.42" "v0.31.42" "$(readlink "$FIXT/releases/current")"
# capture inode after run1
INODE1="$(stat -c %i "$FIXT/releases/current")"

# run2: no-op, inode unchanged
SKIP_LIVEZ=1 "$REPAIR" --install-dir "$FIXT" --skip-livez >/tmp/rr2.$$ 2>&1
RC2=$?
OUT2="$(cat /tmp/rr2.$$)"
assert_eq "run2 rc 0" "0" "$RC2"
assert_eq "run2 no-op message" "1" "$(printf '%s\n' "$OUT2" | grep -c 'IDEMPOTENT-NOOP')"
INODE2="$(stat -c %i "$FIXT/releases/current")"
assert_eq "run2 inode unchanged" "$INODE1" "$INODE2"
rm -f /tmp/rr1.$$ /tmp/rr2.$$
rm -rf "$FIXT"

# ── wrong-precondition: missing release dir ───────────────────────────────
FIXT="$(mktemp -d -t relcur-repair2.XXXXXX)"
rm -rf "$FIXT"; mkdir -p "$FIXT/releases"
printf '{"current":"vMISSING","previous":null}\n' > "$FIXT/releases/state.json"
SKIP_LIVEZ=1 "$REPAIR" --install-dir "$FIXT" --skip-livez >/tmp/rr3.$$ 2>&1
RC3=$?
OUT3="$(cat /tmp/rr3.$$)"
# expect non-zero
if [ "$RC3" -ne 0 ]; then _pass "missing-dir leg non-zero exit"; else _fail "missing-dir leg exited 0" "nonzero" "0"; fi
if printf '%s\n' "$OUT3" | grep -qi "dir missing\|FAIL"; then _pass "missing-dir leg named reason"; else _fail "missing-dir leg no named reason" "reason" "absent"; fi
rm -f /tmp/rr3.$$
rm -rf "$FIXT"

# ── wrong-precondition: version mismatch (livez) ──────────────────────────
# Stub curl on PATH to return a mismatched version.
FIXT="$(mktemp -d -t relcur-repair3.XXXXXX)"
make_fixture "$FIXT" "v0.31.42"
STUBDIR="$(mktemp -d -t relcur-curlstub.XXXXXX)"
cat > "$STUBDIR/curl" <<'STUB'
#!/bin/bash
echo '{"version":"0.0.0"}'
STUB
chmod +x "$STUBDIR/curl"
# Run WITHOUT --skip-livez so livez check runs; stub curl returns mismatch
PATH="$STUBDIR:$PATH" "$REPAIR" --install-dir "$FIXT" --port 9999 >/tmp/rr4.$$ 2>&1
RC4=$?
OUT4="$(cat /tmp/rr4.$$)"
if [ "$RC4" -ne 0 ]; then _pass "version-mismatch leg non-zero exit"; else _fail "version-mismatch leg exited 0" "nonzero" "0"; fi
if printf '%s\n' "$OUT4" | grep -qi "livez version.*!= journal\|FAIL"; then _pass "version-mismatch leg named reason"; else _fail "version-mismatch leg no named reason" "reason" "absent"; fi
rm -f /tmp/rr4.$$
rm -rf "$FIXT" "$STUBDIR"

printf '\n== summary: %d passed, %d failed ==\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
