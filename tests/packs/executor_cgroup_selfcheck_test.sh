#!/usr/bin/env bash
# tests/packs/executor_cgroup_selfcheck_test.sh — AC-D3-6 (self-service-
# promote Wave 5, design §2.4 E4): the executor CGROUP SELF-CHECK.
#
# THE SUBJECT
# -----------
# `supervision_executor_cgroup_selfcheck` (scripts/upgrade/lib.sh): a
# ceremony process that runs INSIDE the supervision unit's cgroup must
# REFUSE (new additive token `executor-inside-supervision-cgroup`,
# exit-78) BEFORE the DUAL_FIGHT check and before any mutation — the
# 2026-11-18 double-outage shape (a stop killed the caller with its own
# cgroup before the start half ran) becomes a clean no-op. Positive-
# match-only: an unprobeable ControlGroup (or unreadable self cgroup) is
# a DIAGNOSTIC SKIP, never evidence of containment (the DUAL_FIGHT
# ControlGraph-unobservable precedent).
#
# PINS
#   E4-1  self cgroup == unit ControlGroup        ⇒ refuse 78 + token
#   E4-2  self cgroup UNDER the unit ControlGroup ⇒ refuse 78 + token
#         (the double-outage shape exactly: a daemon-child running the
#         ceremony script)
#   E4-3  self cgroup outside                     ⇒ rc 0, check passed
#   E4-4  unit ControlGroup unprobeable           ⇒ rc 0 + diagnostic skip
#   E4-5  no resolved SUPERVISION_UNIT            ⇒ rc 0 (not applicable)
#   E4-6  WIRING: supervision_preflight calls the self-check BEFORE
#         supervision_dualfight_check (the §3.2 step order 3 → 4)
#   E4-7  syntax: lib.sh parses (bash -n)
#
# FENCES: fake HOME, scratch INSTALL_DIR, no live install, no real
# systemctl (SYSTEMCTL_BIN stub), no daemon, no network, no PG.
#
# Run:
#   bash tests/packs/executor_cgroup_selfcheck_test.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${0}")/../.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"

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
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

# ── fences ──────────────────────────────────────────────────────────────────
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env; scrub per-variable and re-run)" >&2
    exit 78
fi
setup_fake_home
preflight_guard

SB="$(mktemp -d "${TMPDIR:-/tmp}/e4-cgroup-selfcheck.XXXXXX.$$")" || exit 2
_register_fixture "$SB"
export INSTALL_DIR="$SB/install"
mkdir -p "$INSTALL_DIR/releases"

# The REAL self cgroup path of THIS test process (the stub systemctl runs
# as our fork — same cgroup; stubbing the unit's ControlGroup to this
# value fabricates a POSITIVE containment match hermetically on ANY host).
SELF_LINE="$(tail -n 1 /proc/self/cgroup 2>/dev/null || true)"
SELF_CG="${SELF_LINE##*:}"
if [ -z "$SELF_CG" ] || [ "$SELF_CG" = "$SELF_LINE" ]; then
    printf 'SKIP (environment): /proc/self/cgroup unparseable on this host (%s) — the positive legs need a readable unified-hierarchy path\n' "${SELF_LINE:-<empty>}" >&2
    printf 'PASS=%s FAIL=0\n' "$PASS"
    exit 0
fi
SELF_PARENT="${SELF_CG%/*}"

# The systemctl stub: $1 selects the scenario.
STUB="$SB/systemctl-stub.sh"
cat > "$STUB" <<'STUBEOF'
#!/usr/bin/env bash
# executor-cgroup-selfcheck test stub — ignores argv, prints per SC_STUB_MODE
case "${SC_STUB_MODE:-}" in
    equal)   printf '%s\n' "$SC_STUB_CG" ;;
    parent)  printf '%s\n' "$SC_STUB_CG" ;;
    outside) printf '%s\n' "/system.slice/ensemble-unrelated-probe.service" ;;
    fail)    exit 1 ;;
    empty)   printf '\n' ;;
    *)       exit 1 ;;
esac
exit 0
STUBEOF
chmod +x "$STUB"
export SYSTEMCTL_BIN="$STUB"

# shellcheck source=scripts/upgrade/lib.sh
. "$LIBSH" >/dev/null 2>&1

run_check() { # sets CHECK_RC + CHECK_OUT
    CHECK_OUT="$(SUPERVISION_UNIT="$1" supervision_executor_cgroup_selfcheck 2>&1)"
    CHECK_RC=$?
}

# ── E4-1: self == unit ControlGroup ⇒ refuse 78 + the token ─────────────────
SC_STUB_MODE=equal SC_STUB_CG="$SELF_CG" run_check "ensemble-main.service"
assert_eq "E4-1 self==unit refuses with exit 78" "78" "$CHECK_RC"
assert_contains "E4-1 the refusal carries the additive token" \
    "executor-inside-supervision-cgroup" "$CHECK_OUT"

# ── E4-2: self UNDER unit ControlGroup ⇒ refuse 78 + the token ──────────────
# (the exact double-outage shape: a daemon child inside the unit cgroup)
if [ -n "$SELF_PARENT" ] && [ "$SELF_PARENT" != "$SELF_CG" ]; then
    SC_STUB_MODE=parent SC_STUB_CG="$SELF_PARENT" run_check "ensemble-main.service"
    assert_eq "E4-2 self under unit refuses with exit 78" "78" "$CHECK_RC"
    assert_contains "E4-2 the refusal carries the additive token" \
        "executor-inside-supervision-cgroup" "$CHECK_OUT"
    assert_contains "E4-2 the refusal names the double-outage shape" \
        "2026-11-18" "$CHECK_OUT"
else
    printf 'SKIP E4-2: self cgroup %s has no parent component on this host\n' "$SELF_CG" >&2
fi

# ── E4-3: self outside ⇒ rc 0, the pass line names both paths ───────────────
SC_STUB_MODE=outside run_check "ensemble-main.service"
assert_eq "E4-3 outside passes (rc 0)" "0" "$CHECK_RC"
assert_contains "E4-3 the pass line records the observation" \
    "outside the supervision cgroup" "$CHECK_OUT"

# ── E4-4: unit ControlGroup unprobeable ⇒ rc 0 + diagnostic skip ────────────
SC_STUB_MODE=fail run_check "ensemble-main.service"
assert_eq "E4-4 unprobeable ControlGroup is a skip, not a refusal" "0" "$CHECK_RC"
assert_contains "E4-4 the skip is named (diagnostic only)" \
    "unobservable" "$CHECK_OUT"

# ── E4-5: no resolved unit ⇒ rc 0 immediately (not applicable) ──────────────
SC_STUB_MODE=equal run_check ""
assert_eq "E4-5 no SUPERVISION_UNIT is not applicable (rc 0)" "0" "$CHECK_RC"

# ── E4-6: WIRING — the self-check runs BEFORE DUAL_FIGHT in the preflight ───
PREFLIGHT_BODY="$(sed -n "$(grep -n '^supervision_preflight()' "$LIBSH" | cut -d: -f1),/^}/p" "$LIBSH")"
SELF_POS="$(printf '%s' "$PREFLIGHT_BODY" | grep -n 'supervision_executor_cgroup_selfcheck' | head -1 | cut -d: -f1)"
DF_POS="$(printf '%s' "$PREFLIGHT_BODY" | grep -n 'supervision_dualfight_check' | head -1 | cut -d: -f1)"
if [ -n "$SELF_POS" ] && [ -n "$DF_POS" ] && [ "$SELF_POS" -lt "$DF_POS" ]; then
    _pass
else
    _fail "E4-6 supervision_preflight must call the cgroup self-check BEFORE supervision_dualfight_check" "selfcheck<dfight" "self=$SELF_POS df=$DF_POS"
fi

# ── E4-7: syntax ────────────────────────────────────────────────────────────
if bash -n "$LIBSH" 2>/dev/null; then _pass; else _fail "E4-7 lib.sh fails bash -n"; fi

printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
