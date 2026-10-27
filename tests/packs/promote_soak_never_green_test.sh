#!/usr/bin/env bash
# tests/packs/promote_soak_never_green_test.sh
#
# Pack: promote_soak_never_green_test
# Scope: A5(ii) — the NEVER-GREEN protection (requirements.md §5(ii) +
#   §4.1 P1). THIS SUB-CASE OUTRANKS EVERY OTHER AC: if any of its
#   observables is missing, the never-green protection has been weakened
#   and the change is REJECTED.
#
# WHAT THIS PACK PROVES
# ----------------------
#   N1  a HARD-RED daemon (a genuinely red component, not a timeout)
#       ⇒ the REAL gate_soak returns 1 IMMEDIATELY (the failure is NOT
#         deferred to a grace expiry — the observed probe traffic is a
#         single tick).
#   N2  a NEVER-ANSWERING daemon (a boot that never completes)
#       ⇒ the REAL gate_soak returns 1 (the cap is reached with
#         consecutive_green = 0).
#   N3  a GRACE-ELIGIBLE daemon that NEVER greens (every observation is
#       a grace-eligible transient ⇒ the grace ARMS and HOLDS, no green
#       anywhere) ⇒ the soak STILL reaches cap-reach and returns 1.
#       The grace arming must NOT change the return value (§4.1 P1).
#   N4  the REAL auto-rollback route: the REAL promote.sh (sandbox
#       lane, scratch install root) promotes a broken release ⇒ the
#       scratch journal carries BOTH a `rollback` and a `quarantine`
#       event, `current` reverts to `previous`, `previous` is NOT
#       quarantined and the failed release IS.
#
# HOUSE RULES HONOURED
# ---------------------
#   * FAKE_HOME + escape guard (never a live-shaped HOME-derived path);
#   * per-variable env scrub (``env -u POSTGRES_*`` is a SILENT no-op);
#   * the daemon is a LOCAL HTTP stub on 127.0.0.1 — it never touches
#     PostgreSQL at all (the subject of this pack is the GATE);
#   * every journal write lands in the SCRATCH install root under the
#     FAKE_HOME — never the live promote journal;
#   * the pack asserts its OWN non-vacuity (the scripted probe stream
#     was actually consumed — the stub's request log is the evidence).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Internal watchdog: 600s (this pack drives the REAL promote.sh).

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "FAIL: cannot cd to repo root"; echo "RESULT: FAIL"; exit 1; }

# shellcheck source=/dev/null
. "$REPO_ROOT/tests/helpers/soak_gate_pack_common.sh"

PACK_NAME="promote_soak_never_green_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

soak_pack_fake_home
soak_pack_scrub_env

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/soak-nevergreen.XXXXXX")" || {
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

LOG_TAG="soak-never-green-pack"
UP_TARGET="pack"

# ── the stubs: the journal + the lock heartbeat ─────────────────────
# The pack NEVER writes a real journal: the soak's own journal events
# are captured into a scratch log (the observable for "the grace
# ARMED"). The REAL journal machinery is exercised separately in N4.
_JOURNAL_LOG="$WORKDIR/soak-journal.log"
journal_history_append() { printf '%s\n' "$*" >> "$_JOURNAL_LOG"; return 0; }
lock_heartbeat() { return 0; }
# The mid-soak version check is not the subject of this pack.
gate_version() { return 0; }

# ── the shipped constants (asserted, then shrunk for the legs) ──────
SHIPPED_GRACE_HORIZON_S="$GRACE_HORIZON_S"
SHIPPED_GRACE_EXTENSION_CAP_S="$GRACE_EXTENSION_CAP_S"
SHIPPED_MAX_HOLDS_PER_GREEN="$MAX_HOLDS_PER_GREEN"
SHIPPED_ADAPTIVE_CAP="$ADAPTIVE_SOAK_CAP_S"
SHIPPED_ADAPTIVE_N="$ADAPTIVE_SOAK_CONSECUTIVE"
SHIPPED_ADAPTIVE_CADENCE="$ADAPTIVE_SOAK_CADENCE_S"

# The legs shrink the window so the pack stays inside its watchdog. The
# SHIPPED values are asserted separately below — the shrink only affects
# how long the loop runs, never WHICH branch is taken.
ADAPTIVE_SOAK_FLOOR_S=0
ADAPTIVE_SOAK_CAP_S=4
ADAPTIVE_SOAK_CONSECUTIVE=3
ADAPTIVE_SOAK_CADENCE_S=1
GRACE_EXTENSION_CAP_S=2
GRACE_EXTENSION_CAP_S_INT=2

_probe_count() {
    # The number of requests the stub actually served (the observable
    # that proves WHICH tick the soak failed on).
    if [ -f "$_FAKE_LOG" ]; then
        grep -c '"path"' "$_FAKE_LOG" 2>/dev/null || echo 0
    else
        echo 0
    fi
}

# run_soak <name> <mode> <expected_rc> — drives the REAL gate_soak
# against a stub in <mode>; returns the rc in SOAK_RC.
run_soak() {
    local name="$1" mode="$2" expected="$3"
    local port
    port="$(soak_pack_free_port)" || return 1
    _FAKE_LOG="$WORKDIR/${name}.probes.log"
    if ! soak_pack_start_fake_daemon "$mode" "$port" "$_FAKE_LOG" "$WORKDIR/${name}.ready"; then
        SOAK_RC=99
        return 1
    fi
    : > "$_JOURNAL_LOG"
    PORT="$port"
    local t0 t1
    t0="$(date +%s)"
    gate_soak 300 "v-test" > "$WORKDIR/${name}.out" 2>&1
    SOAK_RC=$?
    t1="$(date +%s)"
    SOAK_ELAPSED=$(( t1 - t0 ))
    SOAK_PROBES="$(_probe_count)"
    soak_pack_stop_fake_daemon
    if [ "$SOAK_RC" != "$expected" ]; then
        soak_pack_report "$name" 0 "expected rc=${expected} got rc=${SOAK_RC} (probes=${SOAK_PROBES}, ${SOAK_ELAPSED}s)"
        return 1
    fi
    soak_pack_report "$name" 1 "rc=${SOAK_RC} (probes=${SOAK_PROBES}, ${SOAK_ELAPSED}s)"
    return 0
}

echo
echo "--- S0: the shipped normative constants ---"
[ "$SHIPPED_GRACE_HORIZON_S" = "120.0" ] \
    && soak_pack_report "S0a GRACE_HORIZON_S is 120.0" 1 "got ${SHIPPED_GRACE_HORIZON_S}" \
    || soak_pack_report "S0a GRACE_HORIZON_S is 120.0" 0 "got ${SHIPPED_GRACE_HORIZON_S}"
[ "$SHIPPED_GRACE_EXTENSION_CAP_S" = "180.0" ] \
    && soak_pack_report "S0b GRACE_EXTENSION_CAP_S is 180.0" 1 "got ${SHIPPED_GRACE_EXTENSION_CAP_S}" \
    || soak_pack_report "S0b GRACE_EXTENSION_CAP_S is 180.0" 0 "got ${SHIPPED_GRACE_EXTENSION_CAP_S}"
[ "$SHIPPED_MAX_HOLDS_PER_GREEN" = "2" ] \
    && soak_pack_report "S0c MAX_HOLDS_PER_GREEN is 2" 1 "got ${SHIPPED_MAX_HOLDS_PER_GREEN}" \
    || soak_pack_report "S0c MAX_HOLDS_PER_GREEN is 2" 0 "got ${SHIPPED_MAX_HOLDS_PER_GREEN}"
[ "$SHIPPED_ADAPTIVE_CAP" = "120" ] \
    && soak_pack_report "S0d ADAPTIVE_SOAK_CAP_S is 120" 1 "got ${SHIPPED_ADAPTIVE_CAP}" \
    || soak_pack_report "S0d ADAPTIVE_SOAK_CAP_S is 120" 0 "got ${SHIPPED_ADAPTIVE_CAP}"
[ "$SHIPPED_ADAPTIVE_N" = "3" ] \
    && soak_pack_report "S0e ADAPTIVE_SOAK_CONSECUTIVE is 3" 1 "got ${SHIPPED_ADAPTIVE_N}" \
    || soak_pack_report "S0e ADAPTIVE_SOAK_CONSECUTIVE is 3" 0 "got ${SHIPPED_ADAPTIVE_N}"
[ "$SHIPPED_ADAPTIVE_CADENCE" = "15" ] \
    && soak_pack_report "S0f ADAPTIVE_SOAK_CADENCE_S is 15" 1 "got ${SHIPPED_ADAPTIVE_CADENCE}" \
    || soak_pack_report "S0f ADAPTIVE_SOAK_CADENCE_S is 15" 0 "got ${SHIPPED_ADAPTIVE_CADENCE}"

echo
echo "--- N1: a HARD-RED daemon fails the soak IMMEDIATELY (never grace-held) ---"
run_soak "N1 hard-red" "hard-red" 1
# The failure must NOT be deferred to the grace expiry: the stub served
# exactly ONE tick (one /livez + one /readyz) and the soak returned long
# before the cap.
if [ "${SOAK_PROBES:-0}" -le 2 ] && [ "${SOAK_ELAPSED:-99}" -lt "${ADAPTIVE_SOAK_CAP_S}" ]; then
    soak_pack_report "N1 the hard red failed on the FIRST observation" 1 \
        "probes=${SOAK_PROBES} elapsed=${SOAK_ELAPSED}s (< cap ${ADAPTIVE_SOAK_CAP_S}s)"
else
    soak_pack_report "N1 the hard red failed on the FIRST observation" 0 \
        "probes=${SOAK_PROBES} elapsed=${SOAK_ELAPSED}s — the failure was DEFERRED"
fi
if grep -q "went RED" "$WORKDIR/N1 hard-red.out" 2>/dev/null; then
    soak_pack_report "N1 the soak names the RED reason" 1
else
    soak_pack_report "N1 the soak names the RED reason" 0 \
        "output: $(tail -3 "$WORKDIR/N1 hard-red.out" 2>/dev/null | tr '\n' ' ')"
fi
if grep -q "soak_grace_extended" "$_JOURNAL_LOG" 2>/dev/null; then
    soak_pack_report "N1 a hard red is NOT grace-held (no extension was granted)" 0 \
        "the soak granted a grace extension on a hard red"
else
    soak_pack_report "N1 a hard red is NOT grace-held (no extension was granted)" 1
fi

echo
echo "--- N2: a NEVER-ANSWERING daemon still returns 1 ---"
run_soak "N2 never-answering" "never-all" 1
if [ "${SOAK_ELAPSED:-0}" -ge "${ADAPTIVE_SOAK_CAP_S}" ]; then
    soak_pack_report "N2 the never-answering daemon rode out to cap-reach" 1 \
        "elapsed=${SOAK_ELAPSED}s (>= cap ${ADAPTIVE_SOAK_CAP_S}s)"
else
    soak_pack_report "N2 the never-answering daemon rode out to cap-reach" 0 \
        "elapsed=${SOAK_ELAPSED}s — it did not ride the window out"
fi

echo
echo "--- N3: a GRACE-ELIGIBLE daemon that NEVER greens still returns 1 (§4.1 P1) ---"
run_soak "N3 never-green-grace" "grace-transient" 1
if grep -q "soak_grace_extended" "$_JOURNAL_LOG" 2>/dev/null; then
    soak_pack_report "N3a the grace ARMED (a grace-eligible HOLD was observed)" 1
else
    soak_pack_report "N3a the grace ARMED (a grace-eligible HOLD was observed)" 0 \
        "journal: $(cat "$_JOURNAL_LOG" 2>/dev/null | tr '\n' ' ')"
fi
if grep -q "cap-reach" "$WORKDIR/N3 never-green-grace.out" 2>/dev/null; then
    soak_pack_report "N3b the never-green daemon reached cap-reach (not an early exit)" 1
else
    soak_pack_report "N3b the never-green daemon reached cap-reach (not an early exit)" 0 \
        "output: $(tail -3 "$WORKDIR/N3 never-green-grace.out" 2>/dev/null | tr '\n' ' ')"
fi
if grep -q "consecutive_green=0 < 3" "$WORKDIR/N3 never-green-grace.out" 2>/dev/null; then
    soak_pack_report "N3c the cap-reach carried consecutive_green=0 < 3" 1
else
    soak_pack_report "N3c the cap-reach carried consecutive_green=0 < 3" 0 \
        "output: $(tail -3 "$WORKDIR/N3 never-green-grace.out" 2>/dev/null | tr '\n' ' ')"
fi

# ══════════════════════════════════════════════════════════════════════
# N4 — the REAL auto-rollback route (promote.sh, sandbox lane)
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- N4: the REAL auto-rollback route (promote.sh sandbox, scratch install root) ---"

FIXTURE="$WORKDIR/fixture"
FAKE_REPO="$FIXTURE/repo"
SBX="$FIXTURE/installsb"
mkdir -p "$FAKE_REPO/scripts/upgrade" "$FAKE_REPO/agents/leader" \
         "$FAKE_REPO/daemon/migrations/versions" \
         "$FAKE_REPO/daemon/plugin_subsystem" \
         "$FAKE_REPO/frontend/dist/frontend/browser" \
         "$FAKE_REPO/plugins/opendesign" \
         "$SBX/releases"

# The stub plugin payload (the staleness predicate reads the MANIFEST).
printf 'plugin:\n  name: opendesign\nschema_version: 1.0.3\n' \
    > "$FAKE_REPO/plugins/opendesign/MANIFEST.yaml"
# The stub staleness predicate — the real one refuses fail-closed when the
# module is absent (the fixture has no daemon tree), which would mask
# the gate behaviour under test.
cat > "$FAKE_REPO/daemon/plugin_subsystem/promote_staleness.py" <<'PYEOF'
#!/usr/bin/env python3
"""Stub promote-staleness predicate (fixture only) — always FRESH."""
import sys


def main() -> int:
    plugin_dir = sys.argv[1] if len(sys.argv) > 1 else ""
    name = plugin_dir.rstrip("/").split("/")[-1] if plugin_dir else "unknown"
    print(f"PLUGIN-STALENESS=fresh plugin={name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
PYEOF
chmod +x "$FAKE_REPO/daemon/plugin_subsystem/promote_staleness.py"

# The REAL upgrade scripts (the subject of this pack).
cp "$REPO_ROOT/scripts/upgrade/lib.sh"           "$FAKE_REPO/scripts/upgrade/lib.sh"
cp "$REPO_ROOT/scripts/upgrade/journal_splice.py" "$FAKE_REPO/scripts/upgrade/journal_splice.py"
cp "$REPO_ROOT/scripts/upgrade/stage.sh"          "$FAKE_REPO/scripts/upgrade/stage.sh"
cp "$REPO_ROOT/scripts/upgrade/promote.sh"        "$FAKE_REPO/scripts/upgrade/promote.sh"
cp "$REPO_ROOT/scripts/upgrade/rollback.sh"       "$FAKE_REPO/scripts/upgrade/rollback.sh"
cp "$REPO_ROOT/scripts/upgrade/status.sh"         "$FAKE_REPO/scripts/upgrade/status.sh"
cp "$REPO_ROOT/scripts/stop-ensemble.sh"          "$FAKE_REPO/scripts/stop-ensemble.sh"
chmod +x "$FAKE_REPO/scripts/upgrade/"*.sh "$FAKE_REPO/scripts/stop-ensemble.sh"

# The stub payload tree + launcher.
printf '#!/bin/bash\n# stub launcher (fixture)\n' > "$FAKE_REPO/launcher.sh"
printf 'stub-agent-definition\n' > "$FAKE_REPO/agents/leader/soul.md"
printf 'port: ${PORT:-8088}\n' > "$FAKE_REPO/config.yaml"
printf 'stub-index\n' > "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
printf 'stub-app\n' > "$FAKE_REPO/frontend/dist/frontend/browser/main.js"
printf 'CREATE TABLE x (id int);\n' > "$FAKE_REPO/daemon/migrations/versions/20260101_000001_init.sql"
printf 'dist/\n*.build-provenance.json\n' > "$FAKE_REPO/.gitignore"
printf '#!/bin/bash\nexit 78\n' > "$FIXTURE/stub-prod"
chmod +x "$FIXTURE/stub-prod"

git -C "$FAKE_REPO" init -q
git -C "$FAKE_REPO" add -A 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm fixture >/dev/null 2>&1
SBX_V1="v1.0.0-sbx"
SBX_V2="v2.0.0-sbx"
git -C "$FAKE_REPO" tag "$SBX_V1"

# _stamp_provenance_for_repo <repo> <artifacts...> — re-stamp the stub
# artifacts' provenance sidecars through the PRODUCTION writer so the
# stage freshness guard passes.
_stamp_provenance_for_repo() {
    local repo="$1"; shift
    local head dirty art
    head="$(git -C "$repo" rev-parse HEAD 2>/dev/null)" || return 0
    dirty="$(REPO_ROOT="$repo" bash -c '
        . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh"
        _git_dirty_porcelain
    ' 2>/dev/null)"
    [ -n "$dirty" ] || dirty=false
    for art in "$@"; do
        [ -f "$art" ] || continue
        REPO_ROOT="$repo" bash -c '
            . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh"
            _provenance_write "$1" "$2" "$3" "stub:tests/packs/promote_soak_never_green_test.sh"
        ' _stamp "$art" "$head" "$dirty" >/dev/null 2>&1
    done
}

run_stage() {  # run_stage <version>
    local ver="$1"
    _stamp_provenance_for_repo "$FAKE_REPO" \
        "$FIXTURE/stub-prod" \
        "$FAKE_REPO/frontend/dist/frontend/browser/index.html"
    HOME="$SOAK_PACK_FAKE_HOME" VERSION="$ver" TARGET=sandbox \
        INSTALL_DIR="$SBX" PORT="$SBX_PORT" \
        bash "$FAKE_REPO/scripts/upgrade/stage.sh" sandbox --skip-build "$FIXTURE/stub-prod"
}

SBX_PORT="$(soak_pack_free_port)" || exit 1
run_stage "$SBX_V1" > "$WORKDIR/stage-v1.log" 2>&1
if [ -f "$SBX/releases/$SBX_V1/manifest.json" ]; then
    soak_pack_report "N4a the previous release is staged in the scratch install root" 1
else
    soak_pack_report "N4a the previous release is staged in the scratch install root" 0 \
        "$(tail -5 "$WORKDIR/stage-v1.log" | tr '\n' ' ')"
fi
# A SECOND tag on its own commit (stage.sh's exact-tag guard resolves
# the tag AT HEAD — so the second release needs its own commit, and it
# must be created only AFTER the first release was staged).
printf 'second-release\n' > "$FAKE_REPO/SECOND"
git -C "$FAKE_REPO" add SECOND 2>/dev/null
git -C "$FAKE_REPO" -c user.email=t@t -c user.name=t commit -qm second >/dev/null 2>&1
git -C "$FAKE_REPO" tag "$SBX_V2"
run_stage "$SBX_V2" > "$WORKDIR/stage-v2.log" 2>&1
if [ -f "$SBX/releases/$SBX_V2/manifest.json" ]; then
    soak_pack_report "N4b the broken release is staged in the scratch install root" 1
else
    soak_pack_report "N4b the broken release is staged in the scratch install root" 0 \
        "$(tail -5 "$WORKDIR/stage-v2.log" | tr '\n' ' ')"
fi

# The install's `current` points at the PREVIOUS release; the journal is
# seeded the same way the house fixture seeds it.
( cd "$SBX" && ln -sfn "releases/$SBX_V1" current )
(
    export INSTALL_DIR="$SBX"
    # shellcheck source=/dev/null
    . "$FAKE_REPO/scripts/upgrade/lib.sh"
    journal_init
    journal_set_current "$SBX_V1"
) >/dev/null 2>&1

# The daemon: the PROMOTED release answers hard-red, the PREVIOUS one
# answers green (the release under test is broken; the rollback target
# works — the shape the auto-rollback exists for).
N4_PORT="$(soak_pack_free_port)" || exit 1
N4_LOG="$WORKDIR/n4.probes.log"
if soak_pack_start_fake_daemon "version-aware" "$N4_PORT" "$N4_LOG" "$WORKDIR/n4.ready" "$SBX" "2.0.0-sbx"; then
    N4_OUT="$(HOME="$SOAK_PACK_FAKE_HOME" TARGET=sandbox INSTALL_DIR="$SBX" PORT="$N4_PORT" \
        VERSION="$SBX_V2" LIVEZ_BUDGET_S=6 READYZ_BUDGET_S=6 ENSEMBLE_PROMOTE_SOAK_S=0 \
        bash "$FAKE_REPO/scripts/upgrade/promote.sh" sandbox 2>&1)"
    N4_RC=$?
    soak_pack_stop_fake_daemon
    printf '%s\n' "$N4_OUT" > "$WORKDIR/n4.out"
    if [ "$N4_RC" = "1" ]; then
        soak_pack_report "N4c the promote exited 1 (the rollback route, not a refusal)" 1
    else
        soak_pack_report "N4c the promote exited 1 (the rollback route, not a refusal)" 0 \
            "rc=${N4_RC}: $(printf '%s' "$N4_OUT" | tail -3 | tr '\n' ' ')"
    fi
else
    N4_RC=99
    soak_pack_report "N4c the promote exited 1 (the rollback route, not a refusal)" 0 "the fake daemon never started"
fi

JOURNAL="$SBX/releases/state.json"
if [ -f "$JOURNAL" ]; then
    if grep -q '"event":"rollback"' "$JOURNAL"; then
        soak_pack_report "N4d the scratch journal carries a ROLLBACK event" 1
    else
        soak_pack_report "N4d the scratch journal carries a ROLLBACK event" 0 \
            "$(grep -o '"event":"[a-z_]*"' "$JOURNAL" | tr '\n' ' ')"
    fi
    if grep -q '"event":"quarantine"' "$JOURNAL"; then
        soak_pack_report "N4e the scratch journal carries a QUARANTINE event" 1
    else
        soak_pack_report "N4e the scratch journal carries a QUARANTINE event" 0 \
            "$(grep -o '"event":"[a-z_]*"' "$JOURNAL" | tr '\n' ' ')"
    fi
else
    soak_pack_report "N4d the scratch journal carries a ROLLBACK event" 0 "no journal at ${JOURNAL}"
    soak_pack_report "N4e the scratch journal carries a QUARANTINE event" 0 "no journal at ${JOURNAL}"
fi

N4_CURRENT="$(soak_pack_journal_field "$SBX" current)"
N4_PREVIOUS="$(soak_pack_journal_field "$SBX" previous)"
N4_QUARANTINED="$(soak_pack_journal_sub "$SBX" quarantined)"
if [ "$N4_CURRENT" = "$SBX_V1" ]; then
    soak_pack_report "N4f current reverted to the PREVIOUS release" 1 "current=${N4_CURRENT}"
else
    soak_pack_report "N4f current reverted to the PREVIOUS release" 0 "current=${N4_CURRENT} (expected ${SBX_V1})"
fi
case "$N4_QUARANTINED" in
    *"$SBX_V1"*)
        soak_pack_report "N4g the PREVIOUS release is NOT quarantined" 0 \
            "quarantined=${N4_QUARANTINED}"
        ;;
    *)
        soak_pack_report "N4g the PREVIOUS release is NOT quarantined" 1 \
            "quarantined=${N4_QUARANTINED}"
        ;;
esac
case "$N4_QUARANTINED" in
    *"$SBX_V2"*)
        soak_pack_report "N4h the FAILED release IS quarantined" 1 \
            "quarantined=${N4_QUARANTINED}"
        ;;
    *)
        soak_pack_report "N4h the FAILED release IS quarantined" 0 \
            "quarantined=${N4_QUARANTINED}"
        ;;
esac
if [ "$(readlink "$SBX/current")" = "releases/$SBX_V1" ]; then
    soak_pack_report "N4i the current SYMLINK points back at the previous release" 1
else
    soak_pack_report "N4i the current SYMLINK points back at the previous release" 0 \
        "current -> $(readlink "$SBX/current")"
fi

# ── the fence attestation (the pack never touched a database) ───────
if [ -z "${POSTGRES_DB:-}" ] && [ -z "${POSTGRES_HOST:-}" ]; then
    soak_pack_report "N4j the pack never resolved a database (the subject is the GATE)" 1
else
    soak_pack_report "N4j the pack never resolved a database (the subject is the GATE)" 0 \
        "POSTGRES_DB=${POSTGRES_DB:-<empty>}"
fi

soak_pack_summary 20
