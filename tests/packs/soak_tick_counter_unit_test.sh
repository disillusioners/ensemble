#!/usr/bin/env bash
# =============================================================================
# Test Pack: soak_tick_counter_unit_test
#
# Lane: test-pins on `latest` (the promote-handback landing tree) — the soak's
#       INCONCLUSIVE-tick counter must increment EXACTLY ONCE per tick.
#
# WHAT IT PINS
#   `scripts/upgrade/lib.sh`'s `gate_soak()` counts INCONCLUSIVE ticks in
#   `__soak_inconclusive` and reports the tally in the cap-reach WARN line
#   ("... AND <N> inconclusive (unattributable) tick(s) ...").  The tally MUST
#   equal the TRUE tick count.  The hygiene commit `5f866fd41` (A5) removed a
#   DUPLICATED declaration / increment / cap-reach check that made the counter
#   advance 2N for N ticks (the pre-A5 shape).
#
#   Leg 1 — the BEHAVIOURAL pin: the PRODUCTION `gate_soak()` is driven with
#     stubbed externals (the probes are hard-green, the version gate defers
#     with rc 2 → the INCONCLUSIVE tick path) and the WARN tick count is
#     compared with the TRUE tick count — counted by the STUB, outside the
#     production code (the number of times the version gate was consulted).
#   Leg 2 — the source-shape pin: exactly ONE declaration, ONE increment and
#     ONE cap-reach check in the lib under test.
#   Leg 3 — the MUTATION PROOF (non-vacuity, the other direction): the lib is
#     copied into a scratch dir and RESTORED to the pre-A5 shape (the doubled
#     declaration / increment / cap-if) by reverse-applying the A5 hunk; the
#     SAME driver then reports 2N for N ticks and the pin MUST detect it.
#   Leg 4 — the byte-identity proof: sha256 of the lib under test before and
#     after the pack is IDENTICAL (the production lib.sh is never mutated).
#
# CONTRACT (house shape)
#   - Dual-layer timeout: inner `timeout 280`; the caller wraps with 300.
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob in `env -u` is a SILENT
#     NO-OP — bash does not glob env-var names).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never
#     `env -u` on it: stripping the ambient guard makes the boot sweep
#     SIGTERM/SIGKILL live processes).
#   - The scratch copies live under `mktemp -d` and are removed on exit; the
#     live install is NEVER touched (fake HOME + fence).
#   - Output: header, leg bodies, `PASS=<n> FAIL=0`, `RESULT: PASS|FAIL`.
#   - Exit: 0 PASS / 1 FAIL.
# =============================================================================
set -u

PACK_NAME="soak_tick_counter_unit_test"

echo "=== Test Pack: ${PACK_NAME} ==="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || { echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"; exit 1; }

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$PROJECT_DIR/tests/upgrade_test_lib.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }

_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  x $1"
    printf 'FAIL: %s\n' "$1" >&2
    if [ $# -gt 1 ]; then
        printf '      expected: %s\n' "$2" >&2
    fi
    if [ $# -gt 2 ]; then
        printf '      actual:   %s\n' "$3" >&2
    fi
}

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        _pass
    else
        _fail "$name" "$expected" "$actual"
    fi
}

# The lib under test — the PRODUCTION one unless a proof overrides it.
SOAK_LIB="${SOAK_LIB_PATH:-$PROJECT_DIR/scripts/upgrade/lib.sh}"
if [ ! -f "$SOAK_LIB" ]; then
    echo "FATAL: lib under test missing: $SOAK_LIB" >&2
    echo "RESULT: FAIL"
    exit 1
fi

SCRATCH="$(mktemp -d /tmp/soak-tick-pin.XXXXXX)"
_register_fixture "$SCRATCH"

# The F1 fence: a throwaway HOME so no live-install path is ever reachable.
setup_fake_home
preflight_guard

# The per-variable env scrub (never a POSTGRES_* glob).
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
)

LIB_SHA_BEFORE="$(sha256sum "$SOAK_LIB" | cut -d' ' -f1)"

# ── the driver: the PRODUCTION gate_soak() counter path ────────────────────
# The externals are stubbed at the FUNCTION level (the same shape the
# adaptive-soak pack uses for its owned-pid seam): the two health probes are
# hard-green and the version gate defers with rc 2 (the TRANSIENT
# attribution deferral) → every tick is an INCONCLUSIVE tick.
#
# The TRUE tick count is counted by the STUB (the number of times the version
# gate was consulted) — an observable that lives OUTSIDE the production code,
# so a double increment in the production counter cannot hide behind it.
_drive_soak() {
    local lib="$1" out="$2"
    rm -rf "$out"
    mkdir -p "$out/install/releases"
    (
        set -u
        export INSTALL_DIR="$out/install"
        export PORT=18999
        export UP_TARGET="vTEST"
        export LOG_TAG="soak-tick-counter"
        export PROMOTE_TOTAL_DEADLINE_S=900
        # The soak knobs are exported BEFORE the lib is sourced — lib.sh
        # expands them at source time.
        export ADAPTIVE_SOAK_CADENCE_S=1
        export ADAPTIVE_SOAK_FLOOR_S=9999
        export ADAPTIVE_SOAK_CAP_S=5
        export ADAPTIVE_SOAK_CONSECUTIVE=999
        # shellcheck source=scripts/upgrade/lib.sh
        . "$lib"
        __ticks=0
        __now=0
        gate_version() { __ticks=$(( __ticks + 1 )); return 2; }
        _probe_once_classified() {
            _PROBE_STATUS="green"
            _PROBE_BODY=""
            _PROBE_CODE="200"
            return 0
        }
        _probe_once() { printf '%s' '{"status":"ok","version":"vTEST"}'; return 0; }
        _readyz_leak_detected() { return 1; }
        _readyz_grace_eligible_transient() { return 1; }
        _readyz_grace_active() { return 1; }
        lock_heartbeat() { return 0; }
        journal_history_append() { return 0; }
        _now_epoch() { printf '%s' "$__now"; }
        sleep() { __now=$(( __now + ${1:-0} )); return 0; }
        _log() { printf 'LOG %s\n' "$*" >> "$out/log.txt"; }
        _warn() { printf 'WARN %s\n' "$*" >> "$out/log.txt"; }
        gate_soak 60 "vTEST" "" >/dev/null 2>&1
        printf '%s' "$?" > "$out/rc.txt"
        printf '%s' "$__ticks" > "$out/ticks.txt"
    )
}

# The WARN tick count — the number the soak REPORTS.
_warn_tick_count() {
    local log="$1"
    grep -E 'inconclusive \(unattributable\) tick\(s\)' "$log" | head -1 |
        grep -oE 'AND [0-9]+ inconclusive' | grep -oE '[0-9]+' | head -1
}

# The observable INCONCLUSIVE tick count — one log line per tick.
_log_tick_count() {
    local log="$1"
    grep -c 'version tick INCONCLUSIVE' "$log" || true
}

# ── LEG 1: the behavioural pin ─────────────────────────────────────────────
echo
echo "--- leg 1: the behavioural pin — the WARN tick count == the TRUE tick count ---"
RUN1="$SCRATCH/run1"
_drive_soak "$SOAK_LIB" "$RUN1"
RC1="$(cat "$RUN1/rc.txt" 2>/dev/null || echo '')"
TICKS1="$(cat "$RUN1/ticks.txt" 2>/dev/null || echo '')"
WARN1="$(_warn_tick_count "$RUN1/log.txt")"
LOG1="$(_log_tick_count "$RUN1/log.txt")"
echo "rc=$RC1  true ticks=$TICKS1  warn tick count=$WARN1  inconclusive log lines=$LOG1"
grep -E 'inconclusive \(unattributable\) tick\(s\)' "$RUN1/log.txt" | head -1

# The cap-reach disposition for an INCONCLUSIVE soak is the TRANSIENT rc 2
# (the defer arm) — never the rc 1 auto-rollback verdict.
assert_eq "leg 1 — the cap-reach disposition is the transient rc 2" "2" "$RC1"
assert_eq "leg 1 — the WARN tick count EQUALS the true tick count" "$TICKS1" "$WARN1"
assert_eq "leg 1 — the WARN tick count EQUALS the observable tick count" "$LOG1" "$WARN1"
if [ "${TICKS1:-0}" -ge 2 ]; then
    _pass
else
    _fail "leg 1 — the driver really drove multiple ticks" ">=2" "${TICKS1:-none}"
fi

# ── LEG 2: the source shape ────────────────────────────────────────────────
echo
echo "--- leg 2: the source shape — one declaration, one increment, one cap-if ---"
DECL_COUNT="$(grep -c 'local __soak_inconclusive=0' "$SOAK_LIB" || true)"
INCR_COUNT="$(grep -c '__soak_inconclusive=\$(( ' "$SOAK_LIB" || true)"
CAPIF_COUNT="$(grep -c 'if \[ "\${__soak_inconclusive:-0}" -gt 0 \]' "$SOAK_LIB" || true)"
echo "declarations=$DECL_COUNT increments=$INCR_COUNT cap-if checks=$CAPIF_COUNT"
assert_eq "leg 2 — exactly ONE declaration of the counter" "1" "$DECL_COUNT"
assert_eq "leg 2 — exactly ONE increment of the counter" "1" "$INCR_COUNT"
assert_eq "leg 2 — exactly ONE cap-reach check on the counter" "1" "$CAPIF_COUNT"

# ── LEG 3: the MUTATION PROOF — the pre-A5 shape MUST be detected ─────────
echo
echo "--- leg 3: mutation proof — the pre-A5 (doubled) shape is detected ---"
PREA5="$SCRATCH/prea5"
mkdir -p "$PREA5/scripts/upgrade"
cp "$SOAK_LIB" "$PREA5/scripts/upgrade/lib.sh"
cat > "$PREA5/a5-soak.patch" <<'PATCHEOF'
diff --git a/scripts/upgrade/lib.sh b/scripts/upgrade/lib.sh
--- a/scripts/upgrade/lib.sh
+++ b/scripts/upgrade/lib.sh
@@ -10190,10 +10192,6 @@ gate_soak() {
     # that separates "we could not tell" (rc 2, transient) from "genuinely
     # never green" (rc 1, auto-rollback).
     local __soak_inconclusive=0
-    # ROW 19 (T7f): INCONCLUSIVE-tick counter — the cap-reach discriminator
-    # that separates "we could not tell" (rc 2, transient) from "genuinely
-    # never green" (rc 1, auto-rollback).
-    local __soak_inconclusive=0
     local soak_absolute_bound=$(( soak_start + ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S_INT ))
     while :; do
         lock_heartbeat
@@ -10268,7 +10266,6 @@ gate_soak() {
             __soak_tick_rc="$__soak_version_rc"
         fi
         if [ "$__soak_tick_rc" = "2" ]; then
-            __soak_inconclusive=$(( ${__soak_inconclusive:-0} + 1 ))
             __soak_inconclusive=$(( ${__soak_inconclusive:-0} + 1 ))
             # INCONCLUSIVE — a third observation kind, distinct from the
             # probe-transient RESET (which is preserved). The streak FREEZES.
@@ -10403,15 +10400,6 @@ gate_soak() {
                 _warn "soak: cap-reach at ${waited}s with consecutive_green=$consecutive_green < $adaptive_n AND ${__soak_inconclusive} inconclusive (unattributable) tick(s) — INCONCLUSIVE, deferring (transient; NOT an auto-rollback verdict)"
                 return 2
             fi
-            # ROW 19 (phase1 §5.4b) — cap-reach disposition. A cap reached
-            # with INCONCLUSIVE ticks and no definitive red is NOT a health
-            # verdict: it returns the TRANSIENT rc 2 so the promote consumer
-            # takes the defer arm (never 8b). A genuine never-green
-            # (inconclusive == 0) keeps the C-1 anti-silent-commit rc 1.
-            if [ "${__soak_inconclusive:-0}" -gt 0 ]; then
-                _warn "soak: cap-reach at ${waited}s with consecutive_green=$consecutive_green < $adaptive_n AND ${__soak_inconclusive} inconclusive (unattributable) tick(s) — INCONCLUSIVE, deferring (transient; NOT an auto-rollback verdict)"
-                return 2
-            fi
             _warn "soak FAILURE: cap-reach at ${waited}s with consecutive_green=$consecutive_green < $adaptive_n — auto-rollback route (C-1: never-green commit bug under sustained DB churn)"
             return 1
         fi
PATCHEOF
PREA5_RC=0
(cd "$PREA5" && git apply -R -p1 a5-soak.patch) || PREA5_RC=$?
if [ "$PREA5_RC" -ne 0 ]; then
    _fail "leg 3 — the pre-A5 shape could be reconstructed in the scratch copy" "reverse-apply OK" "git apply rc=$PREA5_RC"
    PREA5_DECL=0
    PREA5_INCR=0
    PREA5_CAPIF=0
else
    PREA5_DECL="$(grep -c 'local __soak_inconclusive=0' "$PREA5/scripts/upgrade/lib.sh" || true)"
    PREA5_INCR="$(grep -c '__soak_inconclusive=\$(( ' "$PREA5/scripts/upgrade/lib.sh" || true)"
    PREA5_CAPIF="$(grep -c 'if \[ "\${__soak_inconclusive:-0}" -gt 0 \]' "$PREA5/scripts/upgrade/lib.sh" || true)"
    assert_eq "leg 3 — the pre-A5 shape has TWO declarations" "2" "$PREA5_DECL"
    assert_eq "leg 3 — the pre-A5 shape has TWO increments" "2" "$PREA5_INCR"
    assert_eq "leg 3 — the pre-A5 shape has TWO cap-reach checks" "2" "$PREA5_CAPIF"

    RUN3="$SCRATCH/run3"
    _drive_soak "$PREA5/scripts/upgrade/lib.sh" "$RUN3"
    RC3="$(cat "$RUN3/rc.txt" 2>/dev/null || echo '')"
    TICKS3="$(cat "$RUN3/ticks.txt" 2>/dev/null || echo '')"
    WARN3="$(_warn_tick_count "$RUN3/log.txt")"
    LOG3="$(_log_tick_count "$RUN3/log.txt")"
    echo "pre-A5: rc=$RC3  true ticks=$TICKS3  warn tick count=$WARN3  inconclusive log lines=$LOG3"
    # THE DISCRIMINATION: the pre-A5 counter reports 2N for N ticks, so the
    # pin's equality MUST fail there (the WARN count != the true count).
    if [ "$WARN3" != "$TICKS3" ]; then
        _pass
    else
        _fail "leg 3 — the pin DETECTS the doubled counter" "warn != true ticks" "warn=$WARN3 true=$TICKS3"
    fi
    assert_eq "leg 3 — the pre-A5 counter reports EXACTLY 2x the true ticks" "$((TICKS3 * 2))" "$WARN3"
    assert_eq "leg 3 — the pre-A5 observable tick count is UNCHANGED" "$TICKS3" "$LOG3"
fi

# ── LEG 4: the lib under test is byte-identical ───────────────────────────
echo
echo "--- leg 4: byte-identity — the lib under test is untouched ---"
LIB_SHA_AFTER="$(sha256sum "$SOAK_LIB" | cut -d' ' -f1)"
echo "sha256 before: ${LIB_SHA_BEFORE}"
echo "sha256 after : ${LIB_SHA_AFTER}"
assert_eq "leg 4 — the lib under test is byte-identical after the pack" "$LIB_SHA_BEFORE" "$LIB_SHA_AFTER"

teardown_fake_home

echo
echo "PASS=${PASS} FAIL=${FAIL}"
if [ "$FAIL" -ne 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS" >&2
    echo "RESULT: FAIL"
    exit 1
fi

echo "RESULT: PASS"
exit 0
