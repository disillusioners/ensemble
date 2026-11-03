#!/usr/bin/env bash
# ============================================================================
# tests/test_launcher_cooldown_arm.sh — promote-handback-halt Phase 3 (the
# RIDER): the launcher-side rollback-cooldown arm.
#
# THE DEFECT THIS PACK CLOSES (phase3-plan.md §4.1): launcher.sh's
# `_js_count_rollback` armed `cooldown_until` with the BSD-ONLY
# `date -jur <epoch>` dialect. GNU coreutils REJECTS `-j` outright ⇒ the
# call failed ⇒ the `|| until="$(_js_now_iso)"` fallback wrote a
# BORN-EXPIRED stamp ⇒ the ADR-005 10-minute anti-flapping window was
# silently DISARMED on every GNU host. Live-corroborated 2026-11-01: the
# `cooldown_until` stamp PREDATED the sweep row that armed it (a
# zero-length cooldown) — the same wrongful-rollback incident that
# quarantined a healthy release.
#
# The function had ZERO pins of any kind before this pack
# (`grep -rn '_js_count_rollback' tests/ scripts/ daemon/` ⇒ no hits) —
# this pack gives it a home.
#
# PINS (phase3-plan.md §4.2.2):
#   P3-PIN-01  the born-FUTURE property on this GNU host (delta band around
#              SWEEP_COOLDOWN_S) + the mutation leg (the pre-fix dialect
#              planted ⇒ the property REDs — the leg is discriminating).
#   P3-PIN-02  arm SELECTION: Darwin ⇒ the BSD `date` form, Linux ⇒ the GNU
#              `date` form, unrecognized ⇒ the (iii)-scoped fail-closed
#              marker (no `date` call at all, loud stderr, a journal row).
#   P3-PIN-03  the static backstop: ZERO bare `date -jur` sites outside the
#              BSD arm (comments stripped first — a prose mention is not a
#              site), the GNU arm present, and the OTHER executable `date -j`
#              site (`date -ju -f`, in `_js_iso_to_epoch`) also inside a BSD
#              arm (the r4 note-(f) correction).
#   P3-PIN-04  the CROSS-COMPONENT CONSUMER ROUND-TRIP (the anchor pin):
#              the value the launcher writes is consumed by lib.sh's
#              `journal_cooldown_active` as an ACTIVE cooldown. Plus the
#              non-vacuity leg (a born-expired stamp reads NOT-active ⇒ the
#              consumer discriminates ⇒ the round-trip pin is meaningful)
#              and the (iii)-scoped leg (the unrecognized marker reads
#              ACTIVE ⇒ fail-closed, per ARCHITECT AM-P3-1).
#
# FENCES (house rules): mktemp fixtures only; a fake HOME (F1); the
# sibling-env assertion (an ambient POSTGRES_DB=ensemble_prod aborts the
# pack); nothing binds a port; no live-install path is ever resolved; the
# launcher is SOURCED (never executed) so no daemon is spawned.
#
# Run:
#   bash tests/test_launcher_cooldown_arm.sh
#   LAUNCHER_SH=/path/to/base/launcher.sh bash tests/test_launcher_cooldown_arm.sh
#     (the BASE leg: LAUNCHER_SH points at the pre-fix launcher ⇒ the
#      born-future + round-trip legs go RED, by construction)
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# The launcher under test. The base leg points this at the BASE copy.
LAUNCHER_SH="${LAUNCHER_SH:-$REPO_ROOT/launcher.sh}"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
REAL_DATE="$(command -v date || true)"

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
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
assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

# ── fences ──────────────────────────────────────────────────────────────────
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env; scrub per-variable and re-run)" >&2
    exit 78
fi
setup_fake_home
preflight_guard
if [ -z "$REAL_DATE" ]; then
    echo "FENCE ABORT: no real 'date' on PATH — the arm-selection legs need it" >&2
    exit 78
fi
if [ ! -f "$LAUNCHER_SH" ]; then
    echo "FENCE ABORT: LAUNCHER_SH=$LAUNCHER_SH is not a file" >&2
    exit 78
fi

# ── fixture ─────────────────────────────────────────────────────────────────
# A throwaway install dir with a minimal journal. window_start is null so
# `_js_count_rollback` never calls `_js_iso_to_epoch` (which would also
# dispatch on `uname` and pollute the arm-selection observation).
make_fix() {
    local fix
    fix="$(mktemp -d -t cdarm-fix.XXXXXX)"
    _register_fixture "$fix"
    mkdir -p "$fix/releases"
    cat > "$fix/releases/state.json" <<'JOURNAL'
{"current":"v0.28.4","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
    printf '%s' "$fix"
}

# ── the stub PATH: `uname` (platform) + `date` (argv recorder) ─────────────
# The `date` recorder logs every invocation's argv and then EXECS the real
# date — so the launcher's own time helpers keep working while the arm
# selection becomes observable.
make_stub_dir() {
    local dir="$1" platform="$2"
    mkdir -p "$dir"
    printf '#!/bin/sh\nprintf "%%s\\n" "%s"\n' "$platform" > "$dir/uname"
    chmod +x "$dir/uname"
    {
        printf '#!/bin/sh\n'
        printf 'printf "%%s\\n" "$*" >> "%s/date.calls"\n' "$dir"
        printf 'exec "%s" "$@"\n' "$REAL_DATE"
    } > "$dir/date"
    chmod +x "$dir/date"
    : > "$dir/date.calls"
}

# arm_cooldown <launcher> <platform> <fix> <stubdir>
# → prints "<rc>|<cooldown_until value>"; the date-argv log and stderr land
# in the stub dir.
arm_cooldown() {
    local launcher="$1" platform="$2" fix="$3" stubdir="$4"
    : > "$stubdir/date.calls"
    : > "$stubdir/stderr"
    PATH="$stubdir:$PATH" INSTALL_DIR="$fix" PORT=19999 \
        bash -c '
            . "$1" >/dev/null
            _js_count_rollback "$2"
            printf "rc=%s\n" "$?"
        ' _ "$launcher" "$fix/releases/state.json" 2>"$stubdir/stderr"
}

# cooldown_value <fix> — the cooldown_until value as written in the journal.
cooldown_value() {
    local fix="$1"
    grep -o '"cooldown_until"[[:space:]]*:[[:space:]]*"[^"]*"' "$fix/releases/state.json" \
        | head -1 | sed -e 's/.*:[[:space:]]*"//' -e 's/"$//'
}

# iso_to_epoch <iso> — GNU `date -d` (the pack runs on GNU; the BSD arm is
# pinned statically + by argv observation, never executed here).
iso_to_epoch() { date -u -d "$1" +%s 2>/dev/null || printf ''; }

# consumer_cooldown_active <fix> — the CEREMONY-side consumer's verdict.
# rc 0 = ACTIVE (entry refused), rc 1 = not active. Sourced from lib.sh in
# a subshell with INSTALL_DIR pointing at the fixture.
consumer_cooldown_active() {
    local fix="$1"
    INSTALL_DIR="$fix" bash -c '
        . "'"$LIBSH"'" >/dev/null 2>&1
        journal_cooldown_active
    ' >/dev/null 2>&1
    printf '%s' "$?"
}

# strip_comments — drop full-line and trailing comments (a prose mention of
# `date -jur` in a comment is NOT a site — the r4 note-(f)/P-31 discipline).
strip_comments() {
    sed -e '/^[[:space:]]*#/d' -e 's/[[:space:]]#.*$//' "$1"
}

# ════════════════════════════════════════════════════════════════════════════
section "P3-PIN-01 — the born-FUTURE property (GNU host)"

FIX1="$(make_fix)"
STUB1="$(mktemp -d -t cdarm-stub.XXXXXX)"; _register_fixture "$STUB1"
make_stub_dir "$STUB1" "Linux"
ARM1="$(arm_cooldown "$LAUNCHER_SH" "Linux" "$FIX1" "$STUB1")"
RC1="$(printf '%s' "$ARM1" | grep -o 'rc=[0-9]*' | head -1 | sed 's/rc=//')"
CV1="$(cooldown_value "$FIX1")"
assert_eq "P3-PIN-01 the count is written (rc 0)" "0" "$RC1"
assert_contains "P3-PIN-01 the cooldown stamp is written" "20" "$CV1"
CE1="$(iso_to_epoch "$CV1")"
NOW1="$(date -u +%s)"
if [ -n "$CE1" ] && [ -n "$NOW1" ]; then
    DELTA1=$((CE1 - NOW1))
    # the band: SWEEP_COOLDOWN_S (600) ± 60
    if [ "$DELTA1" -ge 540 ] && [ "$DELTA1" -le 660 ]; then
        _pass
    else
        _fail "P3-PIN-01 the cooldown is born ~600s in the FUTURE (delta 540..660)" "delta in [540,660]" "delta=$DELTA1 (until=$CV1 now=$NOW1)"
    fi
else
    _fail "P3-PIN-01 the cooldown stamp parses as an ISO instant" "a parseable ISO stamp" "until=$CV1"
fi
# The counter half of the same call.
CNT1="$(printf '%s' "$ARM1" | grep -o 'rc=[0-9]*' | head -1 | sed 's/rc=//')"
CNTJ="$(grep -o '"24h"[[:space:]]*:[[:space:]]*[0-9]*' "$FIX1/releases/state.json" | head -1 | grep -o '[0-9]*$')"
assert_eq "P3-PIN-01 the rollback counter was incremented" "1" "$CNTJ"

# ── the mutation leg (the discriminating proof) ────────────────────────────
# Plant tonight's defect in a COPY: the GNU arm reverts to the BSD-ONLY
# dialect ⇒ on GNU the date call fails ⇒ the fallback writes NOW ⇒ the
# born-future property REDS.
MUTDIR="$(mktemp -d -t cdarm-mut.XXXXXX)"; _register_fixture "$MUTDIR"
sed -e 's|date -u -d "@$until_epoch"|date -jur "$until_epoch"|' "$LAUNCHER_SH" > "$MUTDIR/launcher.sh"
if cmp -s "$LAUNCHER_SH" "$MUTDIR/launcher.sh"; then
    _fail "P3-PIN-01M the mutation was planted (the pre-fix dialect is back)" "a mutated launcher" "byte-identical to the source — the sed matched nothing"
else
    _pass
fi
FIXM="$(make_fix)"
STUBM="$(mktemp -d -t cdarm-stubm.XXXXXX)"; _register_fixture "$STUBM"
make_stub_dir "$STUBM" "Linux"
arm_cooldown "$MUTDIR/launcher.sh" "Linux" "$FIXM" "$STUBM" >/dev/null
CVM="$(cooldown_value "$FIXM")"
CEM="$(iso_to_epoch "$CVM")"
NOWM="$(date -u +%s)"
if [ -n "$CEM" ]; then
    DELTAM=$((CEM - NOWM))
    if [ "$DELTAM" -lt 60 ]; then
        _pass   # the pre-fix form arms a born-EXPIRED stamp ⇒ RED at base
    else
        _fail "P3-PIN-01M the pre-fix dialect arms a born-EXPIRED stamp (the mutation turns the property RED)" "delta < 60" "delta=$DELTAM"
    fi
else
    _fail "P3-PIN-01M the pre-fix dialect still writes a stamp" "a stamp" "$CVM"
fi

# ════════════════════════════════════════════════════════════════════════════
section "P3-PIN-02 — arm SELECTION (Darwin / Linux / unrecognized)"

# (a) Darwin ⇒ the BSD `date` form.
FIXD="$(make_fix)"
STUBD="$(mktemp -d -t cdarm-stubd.XXXXXX)"; _register_fixture "$STUBD"
make_stub_dir "$STUBD" "Darwin"
arm_cooldown "$LAUNCHER_SH" "Darwin" "$FIXD" "$STUBD" >/dev/null
CALLSD="$(cat "$STUBD/date.calls")"
assert_contains "P3-PIN-02a the Darwin arm calls the BSD date form (-jur <epoch>)" "-jur" "$CALLSD"
assert_not_contains "P3-PIN-02a the Darwin arm does NOT call the GNU form" "-u -d @" "$CALLSD"

# (b) Linux ⇒ the GNU `date` form.
FIXL="$(make_fix)"
STUBL="$(mktemp -d -t cdarm-stubl.XXXXXX)"; _register_fixture "$STUBL"
make_stub_dir "$STUBL" "Linux"
arm_cooldown "$LAUNCHER_SH" "Linux" "$FIXL" "$STUBL" >/dev/null
CALLSL="$(cat "$STUBL/date.calls")"
assert_contains "P3-PIN-02b the Linux arm calls the GNU date form (-u -d @<epoch>)" "-u -d @" "$CALLSL"
assert_not_contains "P3-PIN-02b the Linux arm does NOT call the BSD form" "-jur" "$CALLSL"

# (c) unrecognized platform ⇒ the (iii)-scoped fail-closed marker: NO date
#     call for the cooldown, an explicitly unparseable value, a loud stderr
#     and a journal history row. The consumer must read it as ACTIVE.
FIXU="$(make_fix)"
STUBU="$(mktemp -d -t cdarm-stubu.XXXXXX)"; _register_fixture "$STUBU"
make_stub_dir "$STUBU" "WeirdOS"
arm_cooldown "$LAUNCHER_SH" "WeirdOS" "$FIXU" "$STUBU" >/dev/null
CALLSU="$(cat "$STUBU/date.calls")"
CVU="$(cooldown_value "$FIXU")"
assert_not_contains "P3-PIN-02c the unrecognized arm makes NO date call for the cooldown" "-jur" "$CALLSU"
assert_not_contains "P3-PIN-02c the unrecognized arm makes NO GNU date call either" "-u -d @" "$CALLSU"
assert_eq "P3-PIN-02c the unrecognized arm writes the unparseable marker (not a timestamp)" "unparseable-unknown-platform" "$CVU"
assert_contains "P3-PIN-02c the unrecognized arm is LOUD on stderr" "unrecognized platform" "$(cat "$STUBU/stderr")"
assert_contains "P3-PIN-02c the unrecognized arm appends a journal history row" "cooldown_unavailable" "$(cat "$FIXU/releases/state.json")"
assert_eq "P3-PIN-02c the ceremony-side consumer reads the marker as ACTIVE (fail-closed)" "0" "$(consumer_cooldown_active "$FIXU")"

# ════════════════════════════════════════════════════════════════════════════
section "P3-PIN-03 — the static backstop (no bare BSD date outside its arm)"

STRIPPED="$(mktemp -t cdarm-stripped.XXXXXX)"; _register_fixture "$STRIPPED"
strip_comments "$LAUNCHER_SH" > "$STRIPPED"
BARE_JUR="$(grep -c 'date -jur' "$STRIPPED" || true)"
assert_eq "P3-PIN-03 exactly ONE bare 'date -jur' site survives (the BSD arm)" "1" "$BARE_JUR"
GNU_ARM="$(grep -c 'date -u -d "@' "$STRIPPED" || true)"
assert_eq "P3-PIN-03 the GNU arm is present" "1" "$GNU_ARM"
# Every executable `date -j` site must sit inside a BSD arm (the r4 note-(f)
# correction: `date -ju -f` in _js_iso_to_epoch is CORRECTLY dispatched).
ARMS="$(awk '
    /^[[:space:]]*(Darwin\|\*BSD\*\|\*bsd\*|Linux\|GNU\*\|\*GNU\*|\*)\)/ { arm = $0; sub(/^[[:space:]]*/, "", arm); sub(/\).*/, "", arm) }
    /date -j/ { print arm }
' "$STRIPPED")"
assert_eq "P3-PIN-03 both 'date -j' sites sit inside a BSD arm" "Darwin|*BSD*|*bsd*
Darwin|*BSD*|*bsd*" "$ARMS"
# A bare `date -jur` OUTSIDE an arm (or in the GNU arm) must be caught: the
# static check is the backstop for the behavioural legs.
STATIC_MUT="$(mktemp -d -t cdarm-staticmut.XXXXXX)"; _register_fixture "$STATIC_MUT"
{
    printf '#!/bin/bash\n'
    printf 'echo "%s"\n' "$(grep -n 'date -u -d "@' "$STRIPPED" | head -1 | cut -d: -f1)" > /dev/null
} >/dev/null 2>&1
sed -e 's|date -u -d "@$until_epoch"|date -jur "$until_epoch"|' "$STRIPPED" > "$STATIC_MUT/mut.sh"
if grep -q 'date -jur' "$STATIC_MUT/mut.sh" && [ "$(grep -c 'date -jur' "$STATIC_MUT/mut.sh")" -ge 2 ]; then
    _pass   # the planted bare site WOULD be counted by the static check
else
    _fail "P3-PIN-03 the static check catches a planted bare 'date -jur' outside the arm" ">= 2 bare sites" "$(grep -c 'date -jur' "$STATIC_MUT/mut.sh")"
fi

# ════════════════════════════════════════════════════════════════════════════
section "P3-PIN-04 — the cross-component consumer round-trip (the anchor pin)"

# The launcher-armed cooldown must be consumed by lib.sh's
# `journal_cooldown_active` as ACTIVE (the anti-flapping window is armed).
assert_eq "P3-PIN-04 the launcher-armed cooldown reads ACTIVE at the ceremony entry" "0" "$(consumer_cooldown_active "$FIXL")"
# Non-vacuity: the SAME consumer on a born-expired stamp reads NOT-active —
# so the round-trip leg above is discriminating (it is not a constant-true
# assertion).
FIXE="$(make_fix)"
NOWISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sed -i -e "s|\"cooldown_until\":null|\"cooldown_until\": \"$NOWISO\"|" "$FIXE/releases/state.json"
assert_eq "P3-PIN-04 the consumer reads a born-EXPIRED stamp as NOT active (non-vacuity)" "1" "$(consumer_cooldown_active "$FIXE")"

# ════════════════════════════════════════════════════════════════════════════
section "summary"

printf 'launcher under test: %s\n' "$LAUNCHER_SH"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
