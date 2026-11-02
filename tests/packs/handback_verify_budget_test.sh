#!/usr/bin/env bash
# ============================================================================
# tests/packs/handback_verify_budget_test.sh — promote-handback-halt Phase 1
# (incident r-20261101-212542-8279): the hand-back verify budget knob quartet
# + the /livez serving conjunct + the deadline-aware grace close-out.
# ============================================================================
# Legs L1-L15 and mutations P1-M1..M13 per
# .agents/shared/planning/promote-handback-halt/phase1-plan.md §9 (incl. the
# [PLANNER r2] AM-P1-5 grace-arithmetic close-out and the [ARCHITECT r1]
# amendments AM-P1-1..4). Split into a sibling pack per §9's pack-budget
# rule (the registered supervision_stop_handback pack stays ~140s; these legs
# + the mutation lane add ~3min on their own clock).
#
# Every behavioural leg is RED at base (base borrows LIVEZ_BUDGET_S=2 in the
# harness → a serving-at-8s daemon fails at 2s) and GREEN at HEAD — the
# mutation matrix plants each defect in a COPY of lib.sh and proves the leg
# turns RED (label namespace P1-Mn per the blast-radius recipe v1.0.2).
#
# FENCES (house rules): mktemp FAKE_HOME; the sibling-env assertion
# (POSTGRES_DB must not be ensemble_prod — per-variable scrubs are the
# CALLER's job, `env -u POSTGRES_*` is a silent no-op); ports 9797/8079
# untouched (lsof/curl are PATH stubs — nothing binds); fixtures are
# throwaway mktemp dirs; no live-install path is ever resolved.
#
# Run:   timeout 420 bash tests/packs/handback_verify_budget_test.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
LIBSH="$UPGRADE_DIR/lib.sh"

# ── fences ──────────────────────────────────────────────────────────────────
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env; scrub per-variable and re-run)" >&2
    exit 78
fi
FAKE_HOME="$(mktemp -d -t hbv-fakehome.XXXXXX)"
export HOME="$FAKE_HOME"

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
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass "$name" ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass "$name" ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

HOST_HAS_SYSTEMD=0
[ "$(uname -s)" = "Linux" ] && [ -d /run/systemd/system ] && HOST_HAS_SYSTEMD=1

# ── stubs (same SC_* contract as the registered pack) ──────────────────────
make_systemctl_stub() {
    local dir="$1"
    mkdir -p "$dir"
    cat > "$dir/systemctl" <<'STUB'
#!/bin/bash
echo "$*" >> "${SC_LOG:-/dev/null}"
sub="$1"; shift
case "$sub" in
    is-active)
        if [ -f "${SC_LOG}.killed" ]; then echo inactive; exit 0; fi
        n="$(cat "${SC_LOG}.isactive.n" 2>/dev/null || echo 0)"
        n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.isactive.n"
        if [ -n "${SC_IS_ACTIVE_N:-}" ] && [ "$n" -gt "${SC_IS_ACTIVE_N}" ] \
           && [ -n "${SC_IS_ACTIVE2:-}" ]; then
            echo "${SC_IS_ACTIVE2}"; exit 0
        fi
        echo "${SC_IS_ACTIVE:-inactive}"; exit 0 ;;
    show)
        prop=""; unit=""
        while [ $# -gt 0 ]; do
            case "$1" in
                -p) prop="$2"; shift 2 ;;
                --value) shift ;;
                *) unit="$1"; shift ;;
            esac
        done
        case "$prop" in
            MainPID)
                if [ -f "${SC_LOG}.killed" ]; then echo 0; exit 0; fi
                n="$(cat "${SC_LOG}.mp.n" 2>/dev/null || echo 0)"
                n=$((n + 1)); printf '%s' "$n" > "${SC_LOG}.mp.n"
                if [ -n "${SC_MAINPID_N:-}" ] && [ "$n" -gt "${SC_MAINPID_N}" ] \
                   && [ -n "${SC_MAINPID2:-}" ]; then
                    echo "${SC_MAINPID2}"; exit 0
                fi
                echo "${SC_MAINPID:-0}" ; exit 0 ;;
        esac
        exit 0 ;;
    start)
        if [ -n "${SC_START_ERR}" ]; then echo "${SC_START_ERR}" >&2; fi
        exit "${SC_START_RC:-0}" ;;
    stop)  exit 0 ;;
    kill)  : > "${SC_LOG}.killed"; exit 0 ;;
    reset-failed) exit 0 ;;
    *) exit 0 ;;
esac
STUB
    chmod +x "$dir/systemctl"
}

make_lsof_stub() {
    local dir="$1" rc="$2"
    mkdir -p "$dir"
    cat > "$dir/lsof" <<STUB
#!/bin/bash
exit $rc
STUB
    chmod +x "$dir/lsof"
}

make_curl_stub() {
    local dir="$1"
    mkdir -p "$dir"
    cat > "$dir/curl" <<'STUB'
#!/bin/bash
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -w) shift 2 ;;
        --max-time) shift 2 ;;
        -sS|-s|-S) shift ;;
        *) url="$1"; shift ;;
    esac
done
mode="${CURL_MODE:-ok}"
if [ -n "${CURL_DELAY_S:-}" ] && [ -n "${CURL_START_EPOCH:-}" ]; then
    now="$(date +%s)"
    if [ $(( now - CURL_START_EPOCH )) -lt "${CURL_DELAY_S}" ]; then
        mode="unreachable"
    fi
fi
case "$mode" in
    ok)
        [ -n "$out" ] && printf '{"status":"alive","version":"%s"}\n' "${CURL_VERSION:-0.28.5}" > "$out"
        printf '200'
        exit 0 ;;
    wrongver)
        [ -n "$out" ] && printf '{"status":"alive","version":"0.0.0"}\n' > "$out"
        printf '200'
        exit 0 ;;
    noversion)
        [ -n "$out" ] && printf '{"status":"alive"}\n' > "$out"
        printf '200'
        exit 0 ;;
    non2xx)
        [ -n "$out" ] && : > "$out"
        printf '404'
        exit 0 ;;
    unreachable)
        printf '000'
        exit 7 ;;
    *)
        printf '000'; exit 7 ;;
esac
STUB
    chmod +x "$dir/curl"
}

# make_nolsof_dir <dir> — a PATH with the curl stub and the coreutils the
# verifier path needs, but NO lsof anywhere (the L6d degraded leg). Real
# /usr/bin must NOT appear on the PATH (real lsof lives there).
make_nolsof_dir() {
    local dir="$1" t
    mkdir -p "$dir"
    for t in bash sh date sleep mktemp rm cat head uname grep sed awk chmod \
             ln mkdir touch tr wc env ps kill printf; do
        [ -n "$(command -v "$t" 2>/dev/null)" ] && ln -sf "$(command -v "$t")" "$dir/$t"
    done
    make_curl_stub "$dir/curlbin"
    ln -sf "$dir/curlbin/curl" "$dir/curl"
}

# ── fixture + runner ────────────────────────────────────────────────────────
HBFIX="$(mktemp -d -t hbv-fix.XXXXXX)"
mkdir -p "$HBFIX/releases"
cat > "$HBFIX/releases/state.json" <<'JOURNAL'
{"current":"v1","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
printf '#!/bin/bash\nexit 0\n' > "$HBFIX/launcher.sh"
chmod +x "$HBFIX/launcher.sh"

STUBBIN="$(mktemp -d -t hbv-stub.XXXXXX)"
make_systemctl_stub "$STUBBIN"
SC="$STUBBIN/systemctl"
LSOF_OK="$(mktemp -d -t hbv-lsofok.XXXXXX)";  make_lsof_stub "$LSOF_OK" 0
LSOF_NO="$(mktemp -d -t hbv-lsofno.XXXXXX)";  make_lsof_stub "$LSOF_NO" 1
CURL_DIR="$(mktemp -d -t hbv-curl.XXXXXX)";   make_curl_stub "$CURL_DIR"
NOLSOF_DIR="$(mktemp -d -t hbv-nolsof.XXXXXX)"; make_nolsof_dir "$NOLSOF_DIR"

reset_stub_state() {
    local log="$1"
    : > "$log"
    rm -f "$log".* 2>/dev/null || true
}

# run_hb <libsh-path> <path-extra|-> <setup> — sources the given lib.sh
# (the REAL one for legs, a mutated COPY for the mutation lane), applies the
# supervision globals, runs restart_via_launcher. LIVEZ_BUDGET_S=2 stays
# exported in every leg — it is the L1/L2 discriminating lever (base borrowed
# it; HEAD must ignore it).
run_hb() {
    local libsh="$1" path_extra="$2" setup="$3" pth
    if [ "$path_extra" = "-" ]; then
        pth="\$PATH"
    elif case "$path_extra" in ONLY:*) true ;; *) false ;; esac; then
        # ONLY:<dir> — the PATH is EXACTLY <dir> (the lsof-absent leg: a
        # trailing :$PATH would re-expose the REAL lsof in /usr/bin).
        pth="${path_extra#ONLY:}"
    else
        pth="$path_extra:\$PATH"
    fi
    # NOTE: the HANDBACK_*/CURL_*/PROMOTE_* knobs are OUTER-shell exports
    # (the SC_* pattern of the registered pack) — the inner shell does NOT
    # unset them (it would kill the leg's own levers). Leg isolation is the
    # outer shell's job: every leg unsets its exports after its run.
    PATH="$(eval printf '%s' "\"$pth\"")" bash -c '
        export INSTALL_DIR="'"$HBFIX"'"
        export SYSTEMCTL_BIN="'"$SC"'"
        export SC_LOG="'"$HBFIX"'/sc.log"
        export PORT=19997
        export LIVEZ_BUDGET_S=2
        unset ENSEMBLE_SUPERVISION ENSEMBLE_RESTART_UNIT 2>/dev/null || true
        . "'"$libsh"'" >/dev/null 2>&1
        '"$setup"'
        restart_via_launcher
        echo "rvl-rc=$?"
    ' 2>&1
}
rvl_rc() { printf '%s' "$1" | grep -o 'rvl-rc=[0-9]*' | head -1; }

# ── mutation plumbing ───────────────────────────────────────────────────────
MUTROOT="$(mktemp -d -t hbv-mut.XXXXXX)"
# make_mut <name> <sed-expr> — plants a defect in a copy of lib.sh; prints
# the mutated path. Every pattern is grep-proven unique in lib.sh (the
# ceremony-time-exhausted string appears exactly twice — both floor-band
# verdicts are planted together, by design). FAIL-LOUD: the copy MUST
# differ from the source (a sed pattern that silently matches nothing would
# produce a false-positive "mutation turned the leg red" — the exact class
# the blast-radius recipe forbids).
make_mut() {
    local mname="$1" mexpr="$2"
    local mdir="$MUTROOT/$mname"
    mkdir -p "$mdir"
    cp "$LIBSH" "$mdir/lib.sh"
    sed -i "$mexpr" "$mdir/lib.sh"
    if cmp -s "$LIBSH" "$mdir/lib.sh"; then
        printf 'make_mut[%s]: sed planted NOTHING (pattern mismatch) — refusing a vacuous mutation\n' "$mname" >&2
        return 78
    fi
    printf '%s' "$mdir/lib.sh"
}
# mut_ok <label> <path> — a mutation whose planting failed is a LOUD fail,
# never a vacuous pass (the recipe's false-positive class).
mut_ok() {
    if [ -n "$2" ] && [ -f "$2" ]; then
        return 0
    fi
    _fail "$1 (mutation planting FAILED — no mutated lib)" "file" "absent"
    return 1
}
# expect_red <label> <libsh> <path-extra> <setup> <assert-fn...> is embedded
# per-leg below: every mutation re-runs ONE leg's run and requires that at
# least one of that leg's assertions FAILS against the mutated lib (the leg
# is green against the real lib by construction — the legs above just ran).

UNIT_SETUP='
    SUPERVISION_MODE=unit; SUPERVISION_STATE=UNIT_MANAGED
    SUPERVISION_UNIT=ensemble-hb.service; SUPERVISION_PRESTOP_MAINPID=777
'

# ════════════════════════════════════════════════════════════════════════════
section "legs L1-L15 (red at base, green at HEAD)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then

    # L1 — budget-source discrimination: the verifier does NOT borrow
    # LIVEZ_BUDGET_S. Serving appears at ~8s; LIVEZ_BUDGET_S=2 (base: rc=1
    # at 2s); HANDBACK_VERIFY_BUDGET_S=30 ⇒ HEAD confirms at ~8s.
    reset_stub_state "$HBFIX/sc.log"
    SC_IS_ACTIVE=inactive SC_MAINPID=9999 SC_START_RC=0
    export SC_IS_ACTIVE SC_MAINPID SC_START_RC
    export CURL_MODE=ok CURL_DELAY_S=8 CURL_START_EPOCH="$(date +%s)"
    export HANDBACK_VERIFY_BUDGET_S=30
    L1_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    unset HANDBACK_VERIFY_BUDGET_S CURL_DELAY_S CURL_START_EPOCH
    assert_eq "L1 rc=0 (serving at 8s confirmed; base stops at 2s)" "rvl-rc=0" "$(rvl_rc "$L1_OUT")"
    assert_contains "L1 names the effective budget 30s" "effective=30s" "$L1_OUT"
    assert_contains "L1 hand-back completes" "unit hand-back COMPLETE" "$L1_OUT"
    assert_not_contains "L1 not stopped at the livez-gate budget" "not confirmed" "$L1_OUT"

    # L2 — default-budget coverage: knob UNSET, serving at ~8s ⇒ the DEFAULT
    # (300) covers it (base: rc=1 at 2s).
    reset_stub_state "$HBFIX/sc.log"
    export CURL_MODE=ok CURL_DELAY_S=8 CURL_START_EPOCH="$(date +%s)"
    L2_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    unset CURL_DELAY_S CURL_START_EPOCH
    assert_eq "L2 rc=0 under the 300s default" "rvl-rc=0" "$(rvl_rc "$L2_OUT")"
    assert_contains "L2 default effective=300s, ceiling source named" "effective=300s source=handback-ceiling(no-ceremony-clock)" "$L2_OUT"

    # L4 — budget expiry (fail direction): the daemon never serves; the
    # timeout warn names the EFFECTIVE budget, never LIVEZ_BUDGET_S.
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    L4_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    assert_eq "L4 rc=1 at the budget (bounded, fail direction)" "rvl-rc=1" "$(rvl_rc "$L4_OUT")"
    assert_contains "L4 timeout warn present" "hand-back verify: unit ensemble-hb.service not confirmed" "$L4_OUT"
    assert_contains "L4 warn names the effective budget" "within 4s" "$L4_OUT"
    assert_not_contains "L4 never names the borrowed knob" "LIVEZ_BUDGET_S" "$L4_OUT"

    # L6a — port up but NOT serving → NOT a success (the incident's shape:
    # a bound socket that answers nothing is not serving).
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=0 CURL_MODE=unreachable
    L6A_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    assert_eq "L6a port-up-but-not-serving: rc=1 at the deadline" "rvl-rc=1" "$(rvl_rc "$L6A_OUT")"
    assert_contains "L6a not confirmed (lsof alone insufficient)" "not confirmed" "$L6A_OUT"
    assert_not_contains "L6a never a false COMPLETE" "hand-back COMPLETE" "$L6A_OUT"

    # L6b — serving but WRONG version → fail FAST (one poll, no retry loop).
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_GRACE_S=5 CURL_MODE=wrongver
    L6B_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_EXPECTED_VERSION=0.28.5")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    assert_eq "L6b wrong version: rc=1 (fail-fast)" "rvl-rc=1" "$(rvl_rc "$L6B_OUT")"
    assert_contains "L6b names the observed wrong version" "version=0.0.0" "$L6B_OUT"
    assert_contains "L6b probe-level rationale (no disposition language)" "NOT serving the right release" "$L6B_OUT"
    assert_eq "L6b fail-fast: exactly ONE MainPID poll" "1" "$(cat "$HBFIX/sc.log.mp.n" 2>/dev/null || echo 0)"

    # L6b′ — 2xx with the version field ABSENT while the expected version is
    # KNOWN → fail-fast (AM-P1-1's unenumerated cell, the L6b extension).
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 CURL_MODE=noversion
    L6B2_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_EXPECTED_VERSION=0.28.5")"
    unset HANDBACK_VERIFY_BUDGET_S CURL_MODE
    assert_eq "L6b′ version-absent: rc=1 (fail-fast)" "rvl-rc=1" "$(rvl_rc "$L6B2_OUT")"
    assert_contains "L6b′ unverifiable-as-right-release rationale" "version field is absent/unparseable" "$L6B2_OUT"

    # L6c — serving with the RIGHT version → success (the conjunct's
    # positive direction; also the L12 7b-trap carrier).
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 CURL_MODE=ok CURL_VERSION=0.28.5
    L6C_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_EXPECTED_VERSION=0.28.5")"
    unset HANDBACK_VERIFY_BUDGET_S CURL_MODE CURL_VERSION
    assert_eq "L6c right version: rc=0" "rvl-rc=0" "$(rvl_rc "$L6C_OUT")"
    assert_contains "L6c COMPLETE names the new MainPID" "serves the flipped release (new MainPID 9999" "$L6C_OUT"
    assert_not_contains "L12/L6c 7b trap: no banned literal on the success path" "systemctl start" "$L6C_OUT"

    # L6d — lsof ABSENT, curl present → degraded: the serving check carries
    # the verdict (never fault on a missing tool; never MainPID-alone here).
    # ONLY: — the PATH is exactly the stub dir (no /usr/bin ⇒ no real lsof).
    reset_stub_state "$HBFIX/sc.log"
    export CURL_MODE=ok CURL_VERSION=0.28.5
    L6D_OUT="$(run_hb "$LIBSH" "ONLY:$NOLSOF_DIR" "$UNIT_SETUP
        export PROMOTE_EXPECTED_VERSION=0.28.5")"
    unset CURL_MODE CURL_VERSION
    assert_eq "L6d lsof-absent serving-only: rc=0" "rvl-rc=0" "$(rvl_rc "$L6D_OUT")"
    assert_contains "L6d COMPLETE on the serving conjunct alone" "unit hand-back COMPLETE" "$L6D_OUT"
    assert_not_contains "L6d 7b trap" "systemctl start" "$L6D_OUT"
    # PATH-shape proof: with the ONLY: dir as PATH, lsof is genuinely
    # unresolvable (the degraded branch is reachable ONLY then).
    if PATH="$NOLSOF_DIR" command -v lsof >/dev/null 2>&1; then
        _fail "L6d the fixture PATH still exposes lsof" "absent" "present"
    else
        _pass "L6d the fixture PATH genuinely hides lsof (degraded branch)"
    fi

    # L6e — PORT unset → MainPID-alone (the degenerate branch, preserved
    # and NOT tightened to a failure).
    reset_stub_state "$HBFIX/sc.log"
    export CURL_MODE=unreachable   # unreachable livez must NOT matter here
    L6E_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        PORT=\"\"")"
    unset CURL_MODE
    assert_eq "L6e PORT-unset: rc=0 on MainPID alone" "rvl-rc=0" "$(rvl_rc "$L6E_OUT")"
    assert_contains "L6e degenerate branch still completes" "unit hand-back COMPLETE" "$L6E_OUT"

    # L7 — squeeze + floor: a squeezed ceremony cannot grant less than the
    # floor (monotone-safe). Scaled knobs preserve the relationships:
    # remaining≈9, tail=4 ⇒ raw 5 < floor 6 ⇒ effective=6 (the floor wins,
    # band 2) with the distinct verdict + the tail-reserve note.
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    L7_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 9 ))")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    assert_eq "L7 floor wins: rc=1 (never serves)" "rvl-rc=1" "$(rvl_rc "$L7_OUT")"
    assert_contains "L7 effective budget is the FLOOR (6s)" "effective=6s source=ceremony-floor(tail-reserve-unavailable)" "$L7_OUT"
    assert_contains "L7 distinct verdict (band 2)" "ceremony time exhausted" "$L7_OUT"
    assert_contains "L7 the tail-reserve note" "tail reserve" "$L7_OUT"

    # L8 — squeeze, no squeeze: (a) with room the ceremony-clamped budget
    # reaches the CEILING; (b) mid-band the squeeze is observable (the M5
    # discriminator — with the plan's ~800s-only shape both mutated and
    # unmutated compute the ceiling, so the leg carries the mid-band too).
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=ok
    L8A_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 800 ))")"
    assert_eq "L8a room to spare: rc=0" "rvl-rc=0" "$(rvl_rc "$L8A_OUT")"
    assert_contains "L8a effective=30s (the ceiling)" "effective=30s source=ceremony-clamped" "$L8A_OUT"
    # L8b runs at the DEFAULTS (floor 180 / tail 120): 350−120=230 sits in
    # the mid-clamp band — the observable squeeze (with the scaled knobs of
    # L8a still set it would saturate to the ceiling and prove nothing).
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S
    reset_stub_state "$HBFIX/sc.log"
    L8B_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 350 ))")"
    unset CURL_MODE
    assert_eq "L8b mid-band: rc=0" "rvl-rc=0" "$(rvl_rc "$L8B_OUT")"
    assert_contains "L8b the squeeze is observable (350−120=230 at the defaults)" "effective=230s" "$L8B_OUT"

    # L9 — grace, progress case: budget 4s, grace 6s; the daemon is up
    # (new MainPID + bound port) but /livez answers only at ~6s ⇒ the ONE
    # progress-gated extension fires and recovers.
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=6 \
           CURL_MODE=ok CURL_DELAY_S=6 CURL_START_EPOCH="$(date +%s)"
    L9_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE CURL_DELAY_S CURL_START_EPOCH
    assert_eq "L9 grace recovers a slow answer: rc=0" "rvl-rc=0" "$(rvl_rc "$L9_OUT")"
    assert_contains "L9 the extension is NAMED (post-mortem visible)" "progress-gated grace extension of 6s in force" "$L9_OUT"
    assert_contains "L9 hand-back completes after the extension" "unit hand-back COMPLETE" "$L9_OUT"
    assert_not_contains "L9/L12 7b trap on the extension path" "systemctl start" "$L9_OUT"

    # L10 — grace, no-progress case: no MainPID at all ⇒ the grace does NOT
    # fire; rc=1 at the first budget, bounded well under budget+grace.
    reset_stub_state "$HBFIX/sc.log"
    SC_MAINPID=0; export SC_MAINPID
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=6 CURL_MODE=ok
    L10_T0="$(date +%s)"
    L10_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    L10_T1="$(date +%s)"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    SC_MAINPID=9999; export SC_MAINPID
    assert_eq "L10 dead daemon: rc=1 at the budget" "rvl-rc=1" "$(rvl_rc "$L10_OUT")"
    assert_not_contains "L10 the grace does NOT fire without progress" "grace extension" "$L10_OUT"
    assert_contains "L10 the ordinary timeout verdict fires" "not confirmed" "$L10_OUT"
    if [ "$(( L10_T1 - L10_T0 ))" -lt 9 ]; then
        _pass "L10 bounded: halted in $(( L10_T1 - L10_T0 ))s (< budget+grace=10s)"
    else
        _fail "L10 bounded: halted in $(( L10_T1 - L10_T0 ))s" "<9s" "$(( L10_T1 - L10_T0 ))s"
    fi

    # L11 — knob validation: a garbage override falls back to the default
    # with a LOUD stderr line. (a) direct _numeric_knob unit pin (the
    # source-time stderr is visible here — run_hb swallows it at source
    # time); (b) end-to-end: effective=300 reaches the verifier.
    L11_ERR="$(mktemp -t hbv-l11err.XXXXXX)"
    L11_VAL="$(bash -c '
        export INSTALL_DIR="$(mktemp -d -t hbv-l11.XXXXXX)"
        . "'"$LIBSH"'" >/dev/null 2>/dev/null
        _numeric_knob HANDBACK_VERIFY_BUDGET_S abc 300
    ' 2>"$L11_ERR")"
    assert_eq "L11a garbage override falls back to the default" "300" "$L11_VAL"
    assert_contains "L11a the refusal is LOUD (named, on stderr)" "HANDBACK_VERIFY_BUDGET_S=\"abc\" is not a decimal integer" "$(cat "$L11_ERR")"
    rm -f "$L11_ERR"
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=abc CURL_MODE=ok
    L11B_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP")"
    unset HANDBACK_VERIFY_BUDGET_S CURL_MODE
    assert_eq "L11b end-to-end: rc=0 (default took over)" "rvl-rc=0" "$(rvl_rc "$L11B_OUT")"
    assert_contains "L11b the verifier ran at the DEFAULT 300s" "effective=300s" "$L11B_OUT"

    # L13 — grace close-out, the tail-reserve band (FLOOR ≤ remaining <
    # FLOOR+TAIL): scaled remaining≈7, floor 6, tail 4, grace 2 ⇒ grace
    # CLAMPED to ≤1 (remaining−effective) and total ≤ remaining; the
    # DISTINCT verdict with the tail-reserve note. (Scaled knobs keep the
    # plan's band arithmetic; the grace is clamped here so M12 is
    # discriminating — at the plan's 240/180/60 numbers min(60,60)=60
    # saturates and an unconditional grace is indistinguishable.)
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    L13_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 7 ))")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    L13_GRACE="$(printf '%s' "$L13_OUT" | grep -o 'grace=[0-9]*s' | head -1)"
    assert_contains "L13 band 2: the floor wins" "effective=6s source=ceremony-floor(tail-reserve-unavailable)" "$L13_OUT"
    if [ "$L13_GRACE" = "grace=0s" ] || [ "$L13_GRACE" = "grace=1s" ]; then
        _pass "L13 grace clamped to remaining−effective ($L13_GRACE)"
    else
        _fail "L13 grace clamped to remaining−effective" "grace=0s|grace=1s" "$L13_GRACE"
    fi
    assert_contains "L13 the DISTINCT verdict (never the ordinary wording)" "ceremony time exhausted" "$L13_OUT"
    assert_contains "L13 the tail-reserve note" "tail reserve was unavailable" "$L13_OUT"

    # L14 — grace close-out, the deep-floor band (remaining < FLOOR): grace
    # is ZERO, total = FLOOR, the deliberate overshoot is exactly
    # FLOOR−remaining (scaled: 6−5=1 or 6−4=2 — second-boundary tolerant),
    # and the verdict is DISTINCT (the ordinary wording would blame a
    # healthy-but-slow release).
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    L14_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 5 ))")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    assert_contains "L14 band 3: clock-too-short source named" "effective=6s source=ceremony-floor(clock-too-short)" "$L14_OUT"
    assert_contains "L14 grace is ZERO (never granted on top of an overrun)" "grace=0s" "$L14_OUT"
    assert_contains "L14 total == FLOOR" "total=6s" "$L14_OUT"
    assert_contains "L14 the DISTINCT verdict" "ceremony time exhausted" "$L14_OUT"
    assert_contains "L14 the abort is the ceremony's arithmetic, not the daemon's health" "cannot succeed inside the ceremony budget no matter how healthy the daemon is" "$L14_OUT"
    assert_contains "L14 the bounded overshoot is disclosed" "deliberate bounded overshoot" "$L14_OUT"

    # L15 — the total ≤ remaining assertion leg (the ruling's own bound),
    # asserted NUMERICALLY from the budget line, not implied.
    reset_stub_state "$HBFIX/sc.log"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=ok
    L15_OUT="$(run_hb "$LIBSH" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 800 ))")"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    L15_TOT="$(printf '%s' "$L15_OUT" | grep -o 'total=[0-9]*' | head -1 | cut -d= -f2)"
    L15_REM="$(printf '%s' "$L15_OUT" | grep -o 'ceremony-remaining=[0-9]*' | head -1 | cut -d= -f2)"
    assert_eq "L15 rc=0" "rvl-rc=0" "$(rvl_rc "$L15_OUT")"
    if [ -n "$L15_TOT" ] && [ -n "$L15_REM" ] && [ "$L15_TOT" -le "$L15_REM" ]; then
        _pass "L15 total(${L15_TOT}s) ≤ remaining(${L15_REM}s) — asserted, not implied"
    else
        _fail "L15 total ≤ remaining" "total<=remaining" "total=${L15_TOT}s remaining=${L15_REM}s"
    fi

    # L3 — static pins (the declared defaults + the structural no-borrow
    # rule). Complement to L1/L2 per the plan's declared caveat.
    assert_eq "L3 the ceiling default is declared (300)" "1" "$(grep -cF 'HANDBACK_VERIFY_BUDGET_S="$(_numeric_knob HANDBACK_VERIFY_BUDGET_S "${HANDBACK_VERIFY_BUDGET_S:-}" 300)"' "$LIBSH")"
    assert_eq "L3 the floor default is declared (180)" "1" "$(grep -cF 'HANDBACK_VERIFY_FLOOR_S="$(_numeric_knob HANDBACK_VERIFY_FLOOR_S "${HANDBACK_VERIFY_FLOOR_S:-}" 180)"' "$LIBSH")"
    assert_eq "L3 the grace default is declared (60)" "1" "$(grep -cF 'HANDBACK_VERIFY_GRACE_S="$(_numeric_knob HANDBACK_VERIFY_GRACE_S "${HANDBACK_VERIFY_GRACE_S:-}" 60)"' "$LIBSH")"
    assert_eq "L3 the tail-reserve default is declared (120)" "1" "$(grep -cF 'HANDBACK_TAIL_RESERVE_S="$(_numeric_knob HANDBACK_TAIL_RESERVE_S "${HANDBACK_TAIL_RESERVE_S:-}" 120)"' "$LIBSH")"
    VERIFIER_BODY="$(sed -n '/^_supervision_unit_serving()/,/^}/p' "$LIBSH")"
    assert_eq "L3/R-P1-9 structural: the verifier never references LIVEZ_BUDGET_S" "0" "$(printf '%s' "$VERIFIER_BODY" | grep -c 'LIVEZ_BUDGET_S')"
    assert_contains "L3/R-P1-9 structural: the verifier derives from the new knob" "HANDBACK_VERIFY_BUDGET_S" "$VERIFIER_BODY"
    PROBE_BODY="$(sed -n '/^_handback_serving_probe()/,/^}/p' "$LIBSH")"
    assert_contains "L3/AM-P1-2 structural: the serving conjunct routes through the house classifier" "_probe_once_classified" "$PROBE_BODY"
    assert_not_contains "L3/AM-P1-2 structural: no second taxonomy derived in the probe" "_PROBE_STATUS=\"red\" ;" "$PROBE_BODY"

else
    _skip_missing() { :; }
    printf 'SKIP: legs require /run/systemd/system on this host (named fence)\n' >&2
    SKIP=1
fi

# ════════════════════════════════════════════════════════════════════════════
section "mutation matrix P1-M1..M13 (each leg RED when its defect is planted)"

if [ "$HOST_HAS_SYSTEMD" = "1" ]; then

    # mut_run <label> <mutname> <sed-expr> <path-extra> <setup> <expect-desc> <assert...>
    # Each mutation re-runs its leg against the mutated lib and requires the
    # leg's key assertion to FLIP (the leg must fail). Implemented by
    # capturing the run and asserting the MUTATED outcome.
    mut_run_fail_rc0() {
        # legs whose GREEN expectation is rc=0 — RED means rc!=0
        local label="$1" mut="$2" pextra="$3" setup="$4" needle="$5"
        reset_stub_state "$HBFIX/sc.log"
        local out; out="$(run_hb "$mut" "$pextra" "$setup")"
        if [ "$(rvl_rc "$out")" = "rvl-rc=0" ]; then
            _fail "$label (mutation did NOT turn the leg red)" "rc!=0" "$(rvl_rc "$out")"
        else
            _pass "$label"
        fi
    }
    mut_run_fail_rc1() {
        # legs whose GREEN expectation is rc=1 — RED means rc=0
        local label="$1" mut="$2" pextra="$3" setup="$4"
        reset_stub_state "$HBFIX/sc.log"
        local out; out="$(run_hb "$mut" "$pextra" "$setup")"
        if [ "$(rvl_rc "$out")" = "rvl-rc=1" ]; then
            _fail "$label (mutation did NOT turn the leg red)" "rc!=1" "$(rvl_rc "$out")"
        else
            _pass "$label"
        fi
    }
    mut_run_fail_contains() {
        # legs whose GREEN expectation asserts a needle — RED means absent
        local label="$1" mut="$2" pextra="$3" setup="$4" needle="$5"
        reset_stub_state "$HBFIX/sc.log"
        local out; out="$(run_hb "$mut" "$pextra" "$setup")"
        case "$out" in
            *"$needle"*) _fail "$label (mutation did NOT turn the leg red)" "needle absent" "present" ;;
            *) _pass "$label" ;;
        esac
    }
    mut_run_fail_notcontains() {
        # legs whose GREEN expectation asserts absence — RED means present
        local label="$1" mut="$2" pextra="$3" setup="$4" needle="$5"
        reset_stub_state "$HBFIX/sc.log"
        local out; out="$(run_hb "$mut" "$pextra" "$setup")"
        case "$out" in
            *"$needle"*) _pass "$label" ;;
            *) _fail "$label (mutation did NOT turn the leg red)" "needle present" "absent" ;;
        esac
    }

    CLOCK9_SETUP="$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 9 ))"
    CLOCK7_SETUP="$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 7 ))"
    CLOCK5_SETUP="$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 5 ))"
    CLOCK350_SETUP="$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 350 ))"
    VER_SETUP="$UNIT_SETUP
        export PROMOTE_EXPECTED_VERSION=0.28.5"

    export SC_IS_ACTIVE=inactive SC_MAINPID=9999 SC_START_RC=0

    # P1-M1 — revert the deadline to LIVEZ_BUDGET_S ⇒ L1 red. Variant note:
    # the mutation run neutralizes the grace (GRACE=0) — with the default
    # grace the extension would RESCUE the borrowed deadline at t=2 and the
    # borrow would be invisible (the leg's own green run keeps grace=60).
    M1="$(make_mut m1 's|deadline=$(( $(_now_epoch) + effective ))|deadline=$(( $(_now_epoch) + LIVEZ_BUDGET_S ))|')"
    mut_ok "P1-M1" "$M1" || M1="/nonexistent"
    reset_stub_state "$HBFIX/sc.log"
    export CURL_MODE=ok CURL_DELAY_S=8 CURL_START_EPOCH="$(date +%s)" HANDBACK_VERIFY_BUDGET_S=30
    mut_run_fail_notcontains "P1-M1⇒L1 red (the borrow returns: serving at 8s fails at 2s)" \
        "$M1" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP
        export HANDBACK_VERIFY_GRACE_S=0" "not confirmed"
    unset CURL_DELAY_S CURL_START_EPOCH HANDBACK_VERIFY_BUDGET_S

    # P1-M2 — drop the serving conjunct (probe always green) ⇒ L6a red.
    M2="$(make_mut m2 's|_probe_once_classified "/livez" "${PORT:-}" \|\| hspc_rc=\$?|_probe_once_classified "/livez" "${PORT:-}" \|\| hspc_rc=$?; hspc_rc=0|')"
    mut_ok "P1-M2" "$M2" || M2="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=0 CURL_MODE=unreachable
    mut_run_fail_rc1 "P1-M2⇒L6a red (lsof alone would confirm again)" \
        "$M2" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP" ""
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE

    # P1-M3 — version mismatch non-fatal ⇒ L6b red (rc flips to 0).
    M3="$(make_mut m3 's|if \[ "\$running" != "\$PROMOTE_EXPECTED_VERSION" \]; then|if false; then|')"
    mut_ok "P1-M3" "$M3" || M3="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=30 CURL_MODE=wrongver
    mut_run_fail_rc1 "P1-M3⇒L6b red (a wrong version would pass)" \
        "$M3" "$LSOF_OK:$CURL_DIR" "$VER_SETUP" ""
    unset HANDBACK_VERIFY_BUDGET_S CURL_MODE

    # P1-M4 — remove the floor clamp ⇒ L7 red (effective != the floor).
    M4="$(make_mut m4 's|\[ "\$effective" -lt "\$HANDBACK_VERIFY_FLOOR_S" \] \&\& effective="\$HANDBACK_VERIFY_FLOOR_S"|true|')"
    mut_ok "P1-M4" "$M4" || M4="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    mut_run_fail_contains "P1-M4⇒L7 red (the squeezed budget would drop below the floor)" \
        "$M4" "$LSOF_OK:$CURL_DIR" "$CLOCK9_SETUP" "effective=6s source=ceremony-floor(tail-reserve-unavailable)"

    # P1-M5 — remove the deadline-aware squeeze (always the ceiling) ⇒ L8b red.
    M5="$(make_mut m5 's|budget_source="ceremony-clamped"|budget_source="ceremony-clamped"; effective="$HANDBACK_VERIFY_BUDGET_S"|')"
    mut_ok "P1-M5" "$M5" || M5="/nonexistent"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S
    export CURL_MODE=ok
    mut_run_fail_contains "P1-M5⇒L8b red (the mid-band squeeze value would vanish)" \
        "$M5" "$LSOF_OK:$CURL_DIR" "$CLOCK350_SETUP" "effective=230s"

    # P1-M6 — remove the grace extension ⇒ L9 red.
    M6="$(make_mut m6 's|if \[ "\$extended" = "0" \] \&\& \[ "\$grace" -gt 0 \] \&\& \[ "\$progress" = "1" \]; then|if false; then|')"
    mut_ok "P1-M6" "$M6" || M6="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=6 \
           CURL_MODE=ok CURL_DELAY_S=6 CURL_START_EPOCH="$(date +%s)"
    mut_run_fail_rc0 "P1-M6⇒L9 red (no extension: the slow answer is missed)" \
        "$M6" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP" ""
    unset CURL_DELAY_S CURL_START_EPOCH

    # P1-M7 — grace unconditional ⇒ L10 red (the extension warn appears with
    # NO progress).
    M7="$(make_mut m7 's|if \[ "\$extended" = "0" \] \&\& \[ "\$grace" -gt 0 \] \&\& \[ "\$progress" = "1" \]; then|if [ "$extended" = "0" ] \&\& [ "$grace" -gt 0 ]; then|')"
    mut_ok "P1-M7" "$M7" || M7="/nonexistent"
    SC_MAINPID=0; export SC_MAINPID
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=6 CURL_MODE=ok
    mut_run_fail_notcontains "P1-M7⇒L10 red (a dead daemon would be granted extra time)" \
        "$M7" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP" "grace extension"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE
    SC_MAINPID=9999; export SC_MAINPID

    # P1-M8 — change the declared default 300→180 ⇒ the L3 static pin red
    # (run against the mutated copy: the 300-declaration is absent).
    M8="$(make_mut m8 '/_numeric_knob HANDBACK_VERIFY_BUDGET_S /s/300/180/')"
    mut_ok "P1-M8" "$M8" || M8="/nonexistent"
    if [ -f "$M8" ] && grep -qF 'HANDBACK_VERIFY_BUDGET_S="$(_numeric_knob HANDBACK_VERIFY_BUDGET_S "${HANDBACK_VERIFY_BUDGET_S:-}" 300)"' "$M8"; then
        _fail "P1-M8⇒L3 red (the 300 default declaration survives the mutation)" "absent" "present"
    else
        _pass "P1-M8⇒L3 red"
    fi

    # P1-M9 — the timeout warn names LIVEZ_BUDGET_S again ⇒ L4 red.
    M9="$(make_mut m9 's|(HANDBACK_VERIFY_BUDGET_S effective budget;|(LIVEZ_BUDGET_S budget;|')"
    mut_ok "P1-M9" "$M9" || M9="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    mut_run_fail_notcontains "P1-M9⇒L4 red (the warn would name the borrowed knob)" \
        "$M9" "$LSOF_OK:$CURL_DIR" "$UNIT_SETUP" "LIVEZ_BUDGET_S"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_GRACE_S CURL_MODE

    # P1-M10 — the banned systemctl-start literal lands on the budget line
    # ⇒ L12 red.
    M10="$(make_mut m10 's|hand-back verify: budget effective=|systemctl start hand-back verify: budget effective=|')"
    mut_ok "P1-M10" "$M10" || M10="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=30 CURL_MODE=ok CURL_VERSION=0.28.5
    mut_run_fail_notcontains "P1-M10⇒L12 red (the 7b substring trap)" \
        "$M10" "$LSOF_OK:$CURL_DIR" "$VER_SETUP" "systemctl start"
    unset HANDBACK_VERIFY_BUDGET_S CURL_MODE CURL_VERSION

    # P1-M11 — pre-r2 grace formula (min(GRACE, remaining−TAIL)) ⇒ L14 red
    # (grace != 0 in the deep-floor band).
    M11="$(make_mut m11 's|grace=\$(( entry_remaining - effective ))|grace=$(( entry_remaining - HANDBACK_TAIL_RESERVE_S ))|')"
    mut_ok "P1-M11" "$M11" || M11="/nonexistent"
    export HANDBACK_VERIFY_BUDGET_S=30 HANDBACK_VERIFY_FLOOR_S=6 \
           HANDBACK_TAIL_RESERVE_S=4 HANDBACK_VERIFY_GRACE_S=2 CURL_MODE=unreachable
    CLOCK6_SETUP="$UNIT_SETUP
        export PROMOTE_CEREMONY_DEADLINE_EPOCH=\$(( \$(date +%s) + 6 ))"
    mut_run_fail_contains "P1-M11⇒L14 red (grace granted on top of an overrunning floor)" \
        "$M11" "$LSOF_OK:$CURL_DIR" "$CLOCK6_SETUP" "grace=0s"

    # P1-M12 — grace unconditional vs the ruling (grant GRACE regardless of
    # remaining) ⇒ L13 red. The leg's green assertion is the CLAMPED grace
    # (0s or 1s at remaining≈7) — the mutation inflates it to the knob (2s),
    # so BOTH green forms must be absent AND the inflated form present.
    M12="$(make_mut m12 's|\[ "\$grace" -gt "\$HANDBACK_VERIFY_GRACE_S" \] \&\& grace="\$HANDBACK_VERIFY_GRACE_S"|grace="$HANDBACK_VERIFY_GRACE_S"|')"
    mut_ok "P1-M12" "$M12" || M12="/nonexistent"
    reset_stub_state "$HBFIX/sc.log"
    M12_OUT="$(run_hb "$M12" "$LSOF_OK:$CURL_DIR" "$CLOCK7_SETUP")"
    case "$M12_OUT" in
        *"grace=2s"*)
            case "$M12_OUT" in
                *"grace=0s"*|*"grace=1s"*) _fail "P1-M12⇒L13 red" "clamped grace only" "inflated AND clamped both present" ;;
                *) _pass "P1-M12⇒L13 red (grace inflated to the knob: total exceeds remaining)" ;;
            esac
            ;;
        *) _fail "P1-M12⇒L13 red (the mutation did not inflate the grace)" "grace=2s" "absent" ;;
    esac

    # P1-M13 — ordinary wording in the floor-wins bands ⇒ L13/L14 red.
    M13="$(make_mut m13 's|ceremony time exhausted — the ceremony clock had|verify budget expired — the clock had|g')"
    mut_ok "P1-M13" "$M13" || M13="/nonexistent"
    mut_run_fail_contains "P1-M13⇒L13 red (the distinct verdict is lost)" \
        "$M13" "$LSOF_OK:$CURL_DIR" "$CLOCK7_SETUP" "ceremony time exhausted"
    mut_run_fail_contains "P1-M13⇒L14 red (the distinct verdict is lost)" \
        "$M13" "$LSOF_OK:$CURL_DIR" "$CLOCK5_SETUP" "ceremony time exhausted"
    unset HANDBACK_VERIFY_BUDGET_S HANDBACK_VERIFY_FLOOR_S \
          HANDBACK_TAIL_RESERVE_S HANDBACK_VERIFY_GRACE_S CURL_MODE

else
    printf 'SKIP: mutation matrix requires /run/systemd/system on this host (named fence)\n' >&2
fi

# ── summary ─────────────────────────────────────────────────────────────────
section "summary"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ -n "${SKIP:-}" ]; then
    printf 'SKIPPED (named fence: no /run/systemd/system)\n'
fi
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    rm -rf "$FAKE_HOME" "$MUTROOT"
    exit 1
fi
rm -rf "$FAKE_HOME" "$MUTROOT"
printf '\n=== ALL PHASE-1 HAND-BACK VERIFY LEGS PASSED ===\n'
