#!/usr/bin/env bash
# tests/packs/promote_soak_fail_fast_integration_test.sh
#
# Pack: promote_soak_fail_fast_integration_test
# Scope: A5(iii) — the soak still FAILS FAST on a real regression
#   (requirements.md §5(iii)(b)). The phase-1 pack
#   (promote_soak_fail_fast_test.sh) pins the UNIT leg (a scripted
#   classification stream); THIS pack pins the END-TO-END leg through
#   the REAL lib.sh soak path — a daemon that is GREEN and then turns
#   HARD-RED must fail the soak within ONE CADENCE (≤ 15 s + the probe
#   time, i.e. ≤ ~20 s measured), NOT at the 120 s cap-reach.
#
# WHAT THIS PACK PROVES
# ----------------------
#   F1  the REAL gate_soak against a daemon that greens once and then
#       answers hard-red ⇒ rc 1, elapsed ≤ 20 s, the output names the
#       RED, and the failure is NOT a cap-reach.
#   F2  the SAME regression through the REAL promote.sh (sandbox lane,
#       scratch install root) ⇒ the gate passes, the soak fails inside
#       the ceremony, and the SCRATCH JOURNAL records the reason
#       ("gate fail: soak failure") — the journal shows WHY.
#   F3  non-vacuity: the daemon really served the probes (its request
#       log is the evidence) and the soak really probed it.
#
# The SHIPPED cadence (15 s) is used verbatim — the AC is about the
# cadence, so shrinking it would make the measurement meaningless.
#
# HOUSE RULES HONOURED
# ---------------------
#   * FAKE_HOME + escape guard; per-variable env scrub; the daemon is a
#     LOCAL HTTP stub on 127.0.0.1 (it never touches PostgreSQL);
#   * every journal write lands in the SCRATCH install root under the
#     FAKE_HOME — never the live promote journal;
#   * the pack asserts its OWN non-vacuity.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Internal watchdog: 600s.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "FAIL: cannot cd to repo root"; echo "RESULT: FAIL"; exit 1; }

# shellcheck source=/dev/null
. "$REPO_ROOT/tests/helpers/soak_gate_pack_common.sh"

PACK_NAME="promote_soak_fail_fast_integration_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

soak_pack_fake_home
soak_pack_scrub_env

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/soak-failfast-int.XXXXXX")" || {
    echo "FAIL: cannot create the workdir"; echo "RESULT: FAIL"; exit 1;
}
cleanup() {
    soak_pack_stop_fake_daemon
    rm -rf "$WORKDIR" "$SOAK_PACK_FAKE_HOME" 2>/dev/null || true
}
trap cleanup EXIT

# ── the REAL gate_soak (scripts/upgrade/lib.sh) ──────────────────────
export ENSEMBLE_UPGRADE_SCRIPTS_DIR="$REPO_ROOT/scripts/upgrade"
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/upgrade/lib.sh" || {
    echo "FAIL: cannot source lib.sh"; echo "RESULT: FAIL"; exit 1;
}

LOG_TAG="soak-fail-fast-integration-pack"
UP_TARGET="pack"

# The journal + the lock heartbeat are stubbed: the pack's own journal
# observations come from the SCRATCH install root (F2), never from a
# live journal. The mid-soak version check is not the subject here.
_JOURNAL_LOG="$WORKDIR/soak-journal.log"
journal_history_append() { printf '%s\n' "$*" >> "$_JOURNAL_LOG"; return 0; }
lock_heartbeat() { return 0; }
gate_version() { return 0; }

# The SHIPPED cadence is the measurement — it is NOT shrunk.
SHIPPED_CADENCE="$ADAPTIVE_SOAK_CADENCE_S"
SHIPPED_CAP="$ADAPTIVE_SOAK_CAP_S"
SHIPPED_N="$ADAPTIVE_SOAK_CONSECUTIVE"
# The floor only governs the SUCCESS path (a failure never waits for
# it) — it is zeroed so the leg is bounded; the failure semantics are
# unaffected.
ADAPTIVE_SOAK_FLOOR_S=0

_probe_count() {
    if [ -f "$_FAKE_LOG" ]; then
        grep -c '"path"' "$_FAKE_LOG" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

echo
echo "--- S0: the shipped cadence (the AC's unit) ---"
[ "$SHIPPED_CADENCE" = "15" ] \
    && soak_pack_report "S0a ADAPTIVE_SOAK_CADENCE_S is 15" 1 "got ${SHIPPED_CADENCE}" \
    || soak_pack_report "S0a ADAPTIVE_SOAK_CADENCE_S is 15" 0 "got ${SHIPPED_CADENCE}"
[ "$SHIPPED_CAP" = "120" ] \
    && soak_pack_report "S0b ADAPTIVE_SOAK_CAP_S is 120" 1 "got ${SHIPPED_CAP}" \
    || soak_pack_report "S0b ADAPTIVE_SOAK_CAP_S is 120" 0 "got ${SHIPPED_CAP}"
[ "$SHIPPED_N" = "3" ] \
    && soak_pack_report "S0c ADAPTIVE_SOAK_CONSECUTIVE is 3" 1 "got ${SHIPPED_N}" \
    || soak_pack_report "S0c ADAPTIVE_SOAK_CONSECUTIVE is 3" 0 "got ${SHIPPED_N}"

# ══════════════════════════════════════════════════════════════════════
# F1 — the REAL gate_soak fails fast on the mid-soak hard red
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- F1: green → hard-red MID-SOAK fails within ONE cadence ---"
F1_PORT="$(soak_pack_free_port)" || exit 1
_FAKE_LOG="$WORKDIR/f1.probes.log"
if soak_pack_start_fake_daemon "flip" "$F1_PORT" "$_FAKE_LOG" "$WORKDIR/f1.ready"; then
    PORT="$F1_PORT"
    : > "$_JOURNAL_LOG"
    F1_T0="$(date +%s)"
    # The soak is EXPECTED to fail here (the hard-red leg) — the rc
    # capture must be errexit-safe under `set -e`.
    F1_RC=0
    gate_soak 300 "v-test" > "$WORKDIR/f1.out" 2>&1 || F1_RC=$?
    F1_T1="$(date +%s)"
    F1_ELAPSED=$(( F1_T1 - F1_T0 ))
    F1_PROBES="$(_probe_count)"
    soak_pack_stop_fake_daemon
    if [ "$F1_RC" = "1" ]; then
        soak_pack_report "F1a the soak returned 1 on the mid-soak hard red" 1 \
            "rc=${F1_RC} elapsed=${F1_ELAPSED}s probes=${F1_PROBES}"
    else
        soak_pack_report "F1a the soak returned 1 on the mid-soak hard red" 0 \
            "rc=${F1_RC} elapsed=${F1_ELAPSED}s probes=${F1_PROBES}"
    fi
    # ONE CADENCE + the probe time. The probe against the local stub is
    # sub-second, so 20 s is the AC's own bound.
    if [ "$F1_ELAPSED" -le 20 ]; then
        soak_pack_report "F1b the failure came within ONE cadence (≤ 20s)" 1 \
            "elapsed=${F1_ELAPSED}s"
    else
        soak_pack_report "F1b the failure came within ONE cadence (≤ 20s)" 0 \
            "elapsed=${F1_ELAPSED}s — the soak rode the regression out"
    fi
    if grep -q "cap-reach" "$WORKDIR/f1.out" 2>/dev/null; then
        soak_pack_report "F1c the failure was NOT a cap-reach catch" 0 \
            "output: $(tail -3 "$WORKDIR/f1.out" | tr '\n' ' ')"
    else
        soak_pack_report "F1c the failure was NOT a cap-reach catch" 1
    fi
    if grep -q "went RED" "$WORKDIR/f1.out" 2>/dev/null; then
        soak_pack_report "F1d the soak names the RED reason" 1
    else
        soak_pack_report "F1d the soak names the RED reason" 0 \
            "output: $(tail -3 "$WORKDIR/f1.out" | tr '\n' ' ')"
    fi
    # NON-VACUITY: the daemon really served the probes — one green tick
    # (livez + readyz) then the red tick (livez + readyz).
    if [ "$F1_PROBES" -ge 4 ] && [ "$F1_PROBES" -le 6 ]; then
        soak_pack_report "F1e the daemon really served the probe traffic" 1 \
            "probes=${F1_PROBES} (one green tick + one red tick)"
    else
        soak_pack_report "F1e the daemon really served the probe traffic" 0 \
            "probes=${F1_PROBES} — the stream was not consumed as expected"
    fi
else
    soak_pack_report "F1a the soak returned 1 on the mid-soak hard red" 0 "the fake daemon never started"
    soak_pack_report "F1b the failure came within ONE cadence (≤ 20s)" 0 "the fake daemon never started"
    soak_pack_report "F1c the failure was NOT a cap-reach catch" 0 "the fake daemon never started"
    soak_pack_report "F1d the soak names the RED reason" 0 "the fake daemon never started"
    soak_pack_report "F1e the daemon really served the probe traffic" 0 "the fake daemon never started"
fi

# ══════════════════════════════════════════════════════════════════════
# F2 — the SAME regression through the REAL promote.sh (the journal
#      shows the reason)
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- F2: the real promote route — the journal records the soak failure ---"
if ! soak_pack_build_promote_fixture "$WORKDIR"; then
    echo "FAIL: cannot build the promote fixture"; echo "RESULT: FAIL"; exit 1;
fi
FAKE_REPO="$SOAK_FAKE_REPO"
SBX="$SOAK_SBX"
SBX_V1="$SOAK_SBX_V1"
SBX_V2="$SOAK_SBX_V2"

soak_pack_run_stage "$SBX_V1" > "$WORKDIR/stage-v1.log" 2>&1
if [ -f "$SBX/releases/$SBX_V1/manifest.json" ]; then
    soak_pack_report "F2a the previous release is staged in the scratch install root" 1
else
    soak_pack_report "F2a the previous release is staged in the scratch install root" 0 \
        "$(tail -5 "$WORKDIR/stage-v1.log" | tr '\n' ' ')"
fi
printf 'second-release\n' > "$FAKE_REPO/SECOND"
git -C "$FAKE_REPO" add SECOND 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm second >/dev/null 2>&1
git -C "$FAKE_REPO" tag "$SBX_V2"
soak_pack_run_stage "$SBX_V2" > "$WORKDIR/stage-v2.log" 2>&1
if [ -f "$SBX/releases/$SBX_V2/manifest.json" ]; then
    soak_pack_report "F2b the release under test is staged in the scratch install root" 1
else
    soak_pack_report "F2b the release under test is staged in the scratch install root" 0 \
        "$(tail -5 "$WORKDIR/stage-v2.log" | tr '\n' ' ')"
fi
soak_pack_seed_journal "$SBX_V1"

F2_PORT="$(soak_pack_free_port)" || exit 1
F2_LOG="$WORKDIR/f2.probes.log"
if soak_pack_start_fake_daemon "version-flip" "$F2_PORT" "$F2_LOG" "$WORKDIR/f2.ready" "$SBX" "2.0.0-sbx"; then
    F2_T0="$(date +%s)"
    # The promote is EXPECTED to exit 1 (the soak failure rolled it
    # back) — the rc capture must be errexit-safe under `set -e`.
    F2_RC=0
    F2_OUT="$(HOME="$SOAK_PACK_FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$F2_PORT" \
        VERSION="$SBX_V2" LIVEZ_BUDGET_S=6 READYZ_BUDGET_S=6 \
        bash "$FAKE_REPO/scripts/upgrade/promote.sh" sandbox 2>&1)" || F2_RC=$?
    F2_T1="$(date +%s)"
    F2_ELAPSED=$(( F2_T1 - F2_T0 ))
    soak_pack_stop_fake_daemon
    printf '%s\n' "$F2_OUT" > "$WORKDIR/f2.out"
    if [ "$F2_RC" = "1" ]; then
        soak_pack_report "F2c the promote exited 1 (the soak failure rolled it back)" 1 \
            "rc=${F2_RC} elapsed=${F2_ELAPSED}s"
    else
        soak_pack_report "F2c the promote exited 1 (the soak failure rolled it back)" 0 \
            "rc=${F2_RC}: $(printf '%s' "$F2_OUT" | tail -3 | tr '\n' ' ')"
    fi
else
    F2_RC=99
    soak_pack_report "F2c the promote exited 1 (the soak failure rolled it back)" 0 "the fake daemon never started"
fi

JOURNAL="$SBX/releases/state.json"
if [ -f "$JOURNAL" ] && grep -q '"event":"rollback"' "$JOURNAL"; then
    soak_pack_report "F2d the scratch journal carries a ROLLBACK event" 1
else
    soak_pack_report "F2d the scratch journal carries a ROLLBACK event" 0 \
        "$(grep -o '"event":"[a-z_]*"' "$JOURNAL" 2>/dev/null | tr '\n' ' ')"
fi
if grep -q "gate fail: soak failure" "$JOURNAL" 2>/dev/null; then
    soak_pack_report "F2e the journal records WHY (gate fail: soak failure)" 1
else
    soak_pack_report "F2e the journal records WHY (gate fail: soak failure)" 0 \
        "$(grep -o '"event":"rollback"[^}]*' "$JOURNAL" 2>/dev/null | head -1)"
fi
if printf '%s' "$F2_OUT" 2>/dev/null | grep -q "went RED"; then
    soak_pack_report "F2f the promote output names the RED reason" 1
else
    soak_pack_report "F2f the promote output names the RED reason" 0 \
        "$(printf '%s' "$F2_OUT" 2>/dev/null | grep -i 'soak' | tail -2 | tr '\n' ' ')"
fi
# The soak must NOT have ridden out to the 120 s cap: the whole promote
# (gate + soak + rollback) stays well inside that.
if [ "${F2_ELAPSED:-999}" -le 60 ]; then
    soak_pack_report "F2g the promote did NOT ride the regression out to the cap" 1 \
        "elapsed=${F2_ELAPSED}s"
else
    soak_pack_report "F2g the promote did NOT ride the regression out to the cap" 0 \
        "elapsed=${F2_ELAPSED}s"
fi

# ── the fence attestation ───────────────────────────────────────────
if [ -z "${POSTGRES_DB:-}" ] && [ -z "${POSTGRES_HOST:-}" ]; then
    soak_pack_report "F2h the pack never resolved a database (the subject is the GATE)" 1
else
    soak_pack_report "F2h the pack never resolved a database (the subject is the GATE)" 0 \
        "POSTGRES_DB=${POSTGRES_DB:-<empty>}"
fi

soak_pack_summary 14
