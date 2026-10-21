#!/usr/bin/env bash
# ============================================================================
# test/packs/hard_reboot_drill_e2e_test.sh
#
# Pack:   hard_reboot_drill_e2e_test
# Scope:  hard-reboot recovery (feature/hard-reboot-recovery) — P4 drill harness.
#
# Binding spec:
#   .agents/shared/planning/hard-reboot-recovery/p4-drill-harness-spec.md
# Binding drill procedure (reconciled, NOT replaced):
#   docs/runbooks/hard-reboot-recovery.md  §6 (+ §1/§2/§4 for mechanism/RTO)
# Feature code under drill:
#   daemon/services/boot_epoch.py
#   daemon/services/message_queue_boot_recovery.py
#   daemon/services/auto_continue_boot_pass.py
#   daemon/repositories/message_queue/repository.py
#   daemon/api.py            (lifespan wiring :736 capture_boot_epoch,
#                            :745-749 MQ boot pass, :776 setup_worker_pool)
#   scripts/systemd/ensemble-daemon.service
# Seed shape reused from:
#   tests/unit/test_message_queue_boot_recovery.py  +  tests/helpers/hard_reboot_fixtures.py
#
# ── PHASE SELECTOR ───────────────────────────────────────────────────────────
#   HRR_PHASE="P0,P1,P2"   run a subset (comma list). Supported values:
#     P0  preflight + env-poison fence self-test (no side effects)
#     P1  scratch PostgreSQL role+DB on the LOCAL 16/main cluster only
#     P2  scratch env file + scratch CWD + FAKE_HOME + free port
#     P3  isolated daemon boot (systemd transient unit) + cgroup assertion
#     P4  seed the pre-crash state (instance + schedule + fresh-dead MQ row)
#     P5  hard kill (kill -9) of the drill daemon
#     P6  simulated reboot (unit stop + FRESH systemd-run start)
#     P7  observe the recovery markers + MQ accounting + timings
#     P8  cleanup / zero-residue verification
#   Default: all of P0..P8.
#
# ── DUAL-LAYER TIMEOUT ───────────────────────────────────────────────────────
#   Layer 1 (outer): `timeout 300 bash test/packs/hard_reboot_drill_e2e_test.sh`
#   Layer 2 (inner): INTERNAL_DEADLINE = start + 280s, enforced by a watchdog
#                     that SIGTERMs this script; breach ⇒ RESULT: TIMEOUT (124).
#
# ── HARD FENCES (non-negotiable) ─────────────────────────────────────────────
#   * ENV POISON: the ambient shell carries LIVE POSTGRES_HOST=10.44.0.2 /
#     POSTGRES_DB=ensemble_prod / ENSEMBLE_SELF_ENV=live / a live password.
#     Every DB-touching command runs through `scrub`, and the fence
#     assertion is evaluated INSIDE the scrubbed env (asserting in the parent
#     shell spuriously fires). P0 self-tests the fence with a DELIBERATE
#     ensemble_prod pointer and requires a refusal.
#   * NEVER TOUCHED: live ensemble-main.service (:9797), live
#     ensemble-demo.service (:7979), /home/nea/agents-ensemble,
#     /home/nea/agents-ensemble-demo, remote PG 10.44.0.2 / ensemble_prod,
#     port 8088, /etc/systemd/system/*, the installed units.
#     scripts/stop-ensemble.sh is NEVER invoked (legacy $HOME/agents-ensemble
#     shape — the 2026-10-12 live-install-deletion incident class).
#   * ISOLATION: the drill daemon runs in its OWN system-level transient unit
#     (systemd-run --unit=… --wait --collect ⇒ /system.slice/<unit>.service).
#     Tool/bash children inherit the LIVE daemon's cgroup, so isolation is
#     ASSERTED by reading /proc/<pid>/cgroup — never assumed.
#   * SIGNAL FENCE: only PIDs this harness started, and only after their
#     cgroup was verified to be the drill's own unit.
#   * NO REAL HOST REBOOT: the "reboot" leg is unit stop + fresh start.
#   * FAKE HOME: HOME is a mktemp -d fake; the scratch CWD absorbs data/,
#     data/logs/, data/ensemble.json, data/launcher.err.log, .launcher-state.
#   * SCRATCH PG: local 16/main on 127.0.0.1:5432 only; a throwaway role+DB
#     per run; NEVER CREATEDB on a shared role; DROP verified gone.
#   * PORT: free port from 10000-10100 (dynamic probe; fallback 10007/10008).
#     NEVER 8079 / 9797 / 7979 / 8088.
#
#   /bin/sh is dash → $RANDOM is EMPTY (use $$ / date +%s).
#   `grep -w ":9797"` gives false negatives → use grep -E ":9797[[:space:]]".
#
# ── MOVING TREE ──────────────────────────────────────────────────────────────
#   A concurrent dev lane mutates this worktree. HEAD + `git status
#   --porcelain` are captured at boot AND at exit into tree_state.json; a
#   difference is flagged TREE DRIFT DURING RUN. A dirty tree NEVER fails the
#   drill.
#
# ── EVIDENCE BUNDLE ──────────────────────────────────────────────────────────
#   /tmp/hrr-drill-<ts>/ : preflight.json pg_setup.log boot.log
#   journal-<unit>.log seed.json pre_crash.json post_restart.json
#   timings.json cleanup.json tree_state.json
#
# Run:  timeout 300 bash test/packs/hard_reboot_drill_e2e_test.sh
# ============================================================================

set -u
set -o pipefail

PACK_NAME="hard_reboot_drill_e2e_test"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

HRR_PHASE="${HRR_PHASE:-P0,P1,P2,P3,P4,P5,P6,P7,P8}"
START_EPOCH="$(date +%s)"
DEADLINE_EPOCH=$((START_EPOCH + 280))
TIMEOUT_FLAG=0

TS="$(date -u +%Y%m%d%H%M%S)"
RUN_ID="$$"
BUNDLE="/tmp/hrr-drill-${TS}"
SCRATCH=""
FAKE_HOME=""
PG_ROLE="hrr_drill_${TS}_${RUN_ID}"
PG_DB="hrr_drill_${TS}_${RUN_ID}"
PG_PW="hrr${TS}${RUN_ID}"
PG_PORT=5432
PG_CREATED=0
DRILL_PORT=""
UNIT="hrr-drill-${TS}-${RUN_ID}"
DAEMON_PID=""
SYSTEMD_RUN_PID=""
WATCHDOG_PID=""
INSTANCE_ID=""
MESSAGE_ID=""
SCHEDULE_ID=""
T_RESTART=0
T_READYZ=0
T_RECOVERY=0
RECOVERY_SEEN=0
FENCE_PROOF=""
PASS=0
FAIL=0
STEP="startup"

# ── helpers ────────────────────────────────────────────────────────────────
phase_on() { case ",${HRR_PHASE}," in *",$1,"*) return 0 ;; *) return 1 ;; esac; }
ok()   { echo "[PASS] $*"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
note() { echo "[INFO] $*"; }
time_left() { [ "$(date +%s)" -lt "$DEADLINE_EPOCH" ]; }
deadline_hit() { TIMEOUT_FLAG=1; bad "internal deadline (${DEADLINE_EPOCH}s) exceeded"; }
jstr() { printf '%s' "${1:-}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr '\n' ' '; }
now_s() { date +%s; }

# ── ENV-POISON FENCE ────────────────────────────────────────────────────────
# Exact wrapper form from the binding spec §1.1: the assertion is evaluated
# INSIDE the scrubbed env. The command's own args run AFTER the assertion.
scrub() {
    env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_RESTART_UNIT \
        sh -c '[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh "$@"
}

# Deliberate negative proof: point the scrubbed env AT ensemble_prod and
# require the wrapper to refuse (non-zero exit + "FENCE BREACH").
fence_selftest() {
    local out rc
    out="$(env -u POSTGRES_HOST -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
        -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_RESTART_UNIT \
        POSTGRES_DB=ensemble_prod \
        sh -c '[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH"; exit 1; }; exec "$@"' sh true 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "FENCE BREACH"; then
        FENCE_PROOF="refused rc=${rc} output=${out}"
        ok "env-poison fence self-test REFUSED a deliberate ensemble_prod pointer (rc=${rc})"
        return 0
    fi
    FENCE_PROOF="NOT_REFUSED rc=${rc} output=${out}"
    bad "env-poison fence self-test did NOT refuse (rc=${rc}, output=${out})"
    return 1
}

# SQL against the SCRATCH database as the throwaway role (TCP + scram).
psql_scratch() {
    scrub env PGPASSWORD="$PG_PW" psql -h 127.0.0.1 -p "$PG_PORT" \
        -U "$PG_ROLE" -d "$PG_DB" -v ON_ERROR_STOP=1 -t -A -c "$1" 2>&1
}
psql_super() { scrub sudo -n -u postgres psql -v ON_ERROR_STOP=1 -t -A -c "$1" 2>&1; }

# MQ accounting — the runbook's counting rules:
#   processing_count = status='processing'
#   pending_count    = status='ready' OR (status='retrying' AND next_retry_at <= now())
# (mirrors SQLModelMessageQueueRepository.get_stats — daemon/repositories/message_queue/repository.py)
mq_counts() {
    psql_scratch "SELECT
      (SELECT count(*) FROM message_queue WHERE instance_id='$1' AND status='processing')::text || ' ' ||
      (SELECT count(*) FROM message_queue WHERE instance_id='$1'
         AND (status='ready' OR (status='retrying' AND next_retry_at <= now())))::text;"
}

# Only /tmp/hrr-* paths may ever be removed (live-shape delete guard).
safe_rm() {
    local p="${1:-}"
    [ -n "$p" ] || { echo "REFUSE rm: empty path"; return 1; }
    case "$p" in
        /tmp/hrr-*) rm -rf -- "$p" ;;
        *) echo "REFUSE rm: $p (outside /tmp/hrr-*)"; return 1 ;;
    esac
}

# ── teardown (idempotent; used by P8 and by the EXIT trap) ─────────────────
teardown() {
    [ -n "$UNIT" ] && sudo -n systemctl stop "$UNIT" >/dev/null 2>&1
    [ -n "$UNIT" ] && sudo -n systemctl reset-failed "$UNIT" >/dev/null 2>&1
    if [ "$PG_CREATED" = "1" ]; then
        psql_super "DROP DATABASE IF EXISTS \"${PG_DB}\";" >/dev/null 2>&1
        psql_super "DROP ROLE IF EXISTS \"${PG_ROLE}\";" >/dev/null 2>&1
        PG_CREATED=0
    fi
    [ -n "$SCRATCH" ] && safe_rm "$SCRATCH"
    [ -n "$FAKE_HOME" ] && safe_rm "$FAKE_HOME"
}
cleanup() {
    local rc=$?
    trap - EXIT
    teardown
    [ -n "$WATCHDOG_PID" ] && kill "$WATCHDOG_PID" >/dev/null 2>&1
    if [ -n "$SYSTEMD_RUN_PID" ] && kill -0 "$SYSTEMD_RUN_PID" 2>/dev/null; then
        kill "$SYSTEMD_RUN_PID" >/dev/null 2>&1
    fi
    exit "$rc"
}
# TERM/INT set the timeout flag (never exit from the handler — the main flow
# must still reach the RESULT line). EXIT does the teardown.
trap 'TIMEOUT_FLAG=1' TERM INT
trap cleanup EXIT

# ── internal watchdog (layer 2) ─────────────────────────────────────────────
watchdog() {
    while sleep 5; do
        [ "$(date +%s)" -lt "$DEADLINE_EPOCH" ] || { kill -TERM "$$" 2>/dev/null; exit 0; }
    done
}

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:      ${REPO_ROOT}"
echo "Phases:    ${HRR_PHASE}"
echo "Started:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Bundle:    ${BUNDLE}"
echo

mkdir -p "$BUNDLE" || { echo "FAIL: cannot create bundle $BUNDLE"; echo "RESULT: FAIL"; exit 1; }
watchdog & WATCHDOG_PID=$!

# ════════════════════════════════════════════════════════════════════════════
# P0 — preflight + fence (no side effects)
# ════════════════════════════════════════════════════════════════════════════
if phase_on P0; then
    STEP=P0
    P0_RAN=1
    note "P0 preflight + env-poison fence"

    fence_selftest || { echo "RESULT: FAIL"; exit 1; }

    for b in systemd-run sudo psql curl ss git; do
        command -v "$b" >/dev/null 2>&1 || { bad "missing required binary: $b"; echo "RESULT: FAIL"; exit 1; }
    done
    ok "required binaries present"

    if sudo -n true 2>/dev/null; then ok "passwordless sudo available"; else bad "sudo -n unavailable"; fi

    PY="$REPO_ROOT/.venv/bin/python"
    if [ -x "$PY" ]; then
        RESOLVED="$("$PY" -c 'import daemon;print(daemon.__file__)' 2>/dev/null | tail -1)"
        case "$RESOLVED" in
            "$REPO_ROOT"/*) ok "worktree venv resolves daemon inside the worktree ($RESOLVED)" ;;
            *) bad "venv resolves daemon OUTSIDE the worktree: ${RESOLVED:-<none>}" ;;
        esac
    else
        bad "worktree venv missing: $PY"
    fi

    # live baseline (never-touched surfaces) — captured for the P8 comparison
    LIVE_MAIN_PID="$(systemctl show ensemble-main.service -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
    LIVE_MAIN_RESTARTS="$(systemctl show ensemble-main.service -p NRestarts --value 2>/dev/null | tr -d '[:space:]')"
    LIVE_MAIN_TS="$(systemctl show ensemble-main.service -p ActiveEnterTimestamp --value 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    LIVE_DEMO_PID="$(systemctl show ensemble-demo.service -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
    LIVE_LINK_MAIN="$(readlink -f /home/nea/agents-ensemble/current 2>/dev/null)"
    LIVE_LINK_DEMO="$(readlink -f /home/nea/agents-ensemble-demo/current 2>/dev/null)"
    ok "live baseline: main=${LIVE_MAIN_PID:-?} demo=${LIVE_DEMO_PID:-?} link=${LIVE_LINK_MAIN:-?}"

    # port band: dynamic probe 10000-10100, fallback 10007 then 10008
    for p in $(seq 10000 10100) 10007 10008; do
        if ! (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then DRILL_PORT="$p"; break; fi
    done
    case "$DRILL_PORT" in
        8079|9797|7979|8088) DRILL_PORT="" ;;
    esac
    if [ -n "$DRILL_PORT" ]; then ok "drill port selected: ${DRILL_PORT}"; else bad "no free port in 10000-10100"; fi

    # tree state (moving target)
    TREE_HEAD_BOOT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
    TREE_PORCELAIN_BOOT="$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)"

    cat > "$BUNDLE/preflight.json" <<EOF
{
  "phase": "P0",
  "utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "phases_selected": "${HRR_PHASE}",
  "fence_self_test": "$(jstr "$FENCE_PROOF")",
  "drill_port": ${DRILL_PORT:-0},
  "unit": "${UNIT}",
  "bundle": "${BUNDLE}",
  "repo_root": "${REPO_ROOT}",
  "live_baseline": {
    "main_pid": "${LIVE_MAIN_PID}",
    "main_nrestarts": "${LIVE_MAIN_RESTARTS}",
    "main_active_enter": "$(jstr "$LIVE_MAIN_TS")",
    "demo_pid": "${LIVE_DEMO_PID}",
    "install_link": "$(jstr "$LIVE_LINK_MAIN")",
    "demo_link": "$(jstr "$LIVE_LINK_DEMO")"
  },
  "tree_head_boot": "${TREE_HEAD_BOOT}",
  "tree_porcelain_boot": "$(jstr "$TREE_PORCELAIN_BOOT")"
}
EOF
    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P1 — scratch PostgreSQL (LOCAL 16/main only)
# ════════════════════════════════════════════════════════════════════════════
if phase_on P1; then
    STEP=P1
    note "P1 scratch PostgreSQL role+DB (local cluster only)"
    psql_super "CREATE ROLE \"${PG_ROLE}\" LOGIN PASSWORD '${PG_PW}';" > "$BUNDLE/pg_setup.log" 2>&1
    RC_ROLE=$?
    psql_super "CREATE DATABASE \"${PG_DB}\" OWNER \"${PG_ROLE}\";" >> "$BUNDLE/pg_setup.log" 2>&1
    RC_DB=$?
    if [ "$RC_ROLE" -ne 0 ] || [ "$RC_DB" -ne 0 ]; then
        cat "$BUNDLE/pg_setup.log"
        bad "scratch role/database creation failed (role rc=${RC_ROLE}, db rc=${RC_DB})"
        echo "RESULT: FAIL"; exit 1
    fi
    PG_CREATED=1

    # TCP connect as the throwaway role (pg_hba: 127.0.0.1 = scram-sha-256)
    if ! psql_scratch "SELECT 1;" > "$BUNDLE/pg_setup.log.tcp" 2>&1; then
        cat "$BUNDLE/pg_setup.log.tcp"
        bad "TCP connect as ${PG_ROLE} failed"
        echo "RESULT: FAIL"; exit 1
    fi
    rm -f "$BUNDLE/pg_setup.log.tcp"
    {
        echo
        echo "== tcp connect as ${PG_ROLE} OK =="
        psql_scratch "SELECT current_user, current_database();"
    } >> "$BUNDLE/pg_setup.log" 2>&1
    ok "scratch role+db created on local cluster (${PG_ROLE} / ${PG_DB})"

    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P2 — scratch env + CWD + FAKE_HOME
# ════════════════════════════════════════════════════════════════════════════
if phase_on P2; then
    STEP=P2
    note "P2 scratch env file + scratch CWD + FAKE_HOME"
    SCRATCH="$(mktemp -d /tmp/hrr-scratch-XXXXXX 2>/dev/null)"
    FAKE_HOME="$(mktemp -d /tmp/hrr-fakehome-XXXXXX 2>/dev/null)"
    if [ -z "$SCRATCH" ] || [ -z "$FAKE_HOME" ]; then
        bad "mktemp -d failed (SCRATCH='${SCRATCH}' FAKE_HOME='${FAKE_HOME}')"
        echo "RESULT: FAIL"; exit 1
    fi
    case "$SCRATCH" in /tmp/hrr-scratch-*) ;; *) bad "SCRATCH not under /tmp/hrr-scratch-*"; echo "RESULT: FAIL"; exit 1 ;; esac
    case "$FAKE_HOME" in /tmp/hrr-fakehome-*) ;; *) bad "FAKE_HOME not under /tmp/hrr-fakehome-*"; echo "RESULT: FAIL"; exit 1 ;; esac
    if [ -e "$FAKE_HOME/agents-ensemble" ] || [ -e "$FAKE_HOME/agents-ensemble-demo" ]; then
        bad "FAKE_HOME contains live-shaped install paths"; echo "RESULT: FAIL"; exit 1
    fi
    ok "FAKE_HOME=${FAKE_HOME} (empty of live-shaped paths); SCRATCH=${SCRATCH}"

    mkdir -p "$SCRATCH/data/logs" "$SCRATCH/data/reports" "$SCRATCH/data/tmp_images"
    # ./agents is CWD-relative in config.yaml (agents.directory) — link it so
    # the source registry path resolves from the scratch CWD.
    ln -sfn "$REPO_ROOT/agents" "$SCRATCH/agents"

    # The daemon env file — ONLY these vars reach the daemon process.
    cat > "$SCRATCH/daemon.env" <<EOF
ENSEMBLE_SELF_ENV=dev
ENSEMBLE_CONFIG=${REPO_ROOT}/config.yaml
PYTHONPATH=${REPO_ROOT}
HOME=${FAKE_HOME}
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LANG=C.UTF-8
DATA_DIR=${SCRATCH}/data
ENSEMBLE_DATA_DIR=${SCRATCH}/data
DAEMON_LOG_DIR=${SCRATCH}/data/logs
PERSISTENCE_DB_PATH=${SCRATCH}/data/instances.db
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=${PG_PORT}
POSTGRES_DB=${PG_DB}
POSTGRES_USER=${PG_ROLE}
POSTGRES_PASSWORD=${PG_PW}
POSTGRES_URL=postgresql://${PG_ROLE}:${PG_PW}@127.0.0.1:${PG_PORT}/${PG_DB}
PORT=${DRILL_PORT}
DAEMON_PORT=${DRILL_PORT}
HOST=127.0.0.1
DAEMON_HOST=127.0.0.1
LOG_LEVEL=info
# LLM: dead LOCAL endpoint — the drill is structural (blind spot §7.8); no
# live proxy is contacted and no live credential is used.
OPENAI_BASE_URL=http://127.0.0.1:1/v1
OPENAI_API_KEY=drill-mock-key-not-real
OPENAI_MODEL=drill-mock
OPENAI_REQUEST_GZIP=0
# Source-mode default is already OFF; pin it explicitly (kill-capable path).
ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0
EOF
    chmod 600 "$SCRATCH/daemon.env"
    ok "scratch daemon env written (${SCRATCH}/daemon.env, mode 600)"

    # Prove the resolution chain from a foreign CWD (the daemon's CWD is the
    # scratch dir): daemon package + config.yaml + agents/ must all resolve.
    RESOLVE="$(
        cd "$SCRATCH" || exit 1
        env -i PATH=/usr/bin:/bin HOME="$FAKE_HOME" \
            PYTHONPATH="$REPO_ROOT" ENSEMBLE_CONFIG="$REPO_ROOT/config.yaml" \
            "$REPO_ROOT/.venv/bin/python" -c \
            'import daemon,os,pathlib;print(daemon.__file__);print(pathlib.Path(daemon.__file__).parent.parent/"agents");print(os.environ.get("ENSEMBLE_CONFIG"))' 2>/dev/null
    )"
    if printf '%s' "$RESOLVE" | grep -q "^${REPO_ROOT}/daemon/__init__.py$"; then
        ok "foreign-CWD resolution proven: daemon package + agents/ + config.yaml"
    else
        bad "foreign-CWD resolution FAILED: $(printf '%s' "$RESOLVE" | tr '\n' ' ')"
    fi
    printf '%s\n' "$RESOLVE" > "$BUNDLE/scratch_resolution.txt"

    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P3 — isolated boot (systemd transient unit) + cgroup assertion
# ════════════════════════════════════════════════════════════════════════════
daemon_cgroup() { cat "/proc/$1/cgroup" 2>/dev/null; }
assert_isolated() {
    local pid="$1" cg
    cg="$(daemon_cgroup "$pid")"
    case "$cg" in
        *"/system.slice/${UNIT}.service"*) return 0 ;;
    esac
    case "$cg" in
        *"/system.slice/ensemble-main.service"*) bad "REFUSING: pid $pid is in the LIVE daemon cgroup"; return 1 ;;
    esac
    bad "cgroup isolation FAILED for pid ${pid}: ${cg:-<none>} (expected /system.slice/${UNIT}.service)"
    return 1
}
start_unit() {
    # `python -m daemon` (NOT dev.sh, NOT `uvicorn daemon.api:app`):
    #   * dev.sh re-sources .env (re-poisons the env) and uses --reload
    #     (the reloader respawns the child, so kill -9 would hit the wrong pid);
    #   * `python -m daemon` runs the G6 boot preflight
    #     ("Boot preflight: PostgreSQL reachable") that the drill observes.
    # PORT/HOST win over DAEMON_PORT/DAEMON_HOST (config.yaml is shipped),
    # so BOTH are set in daemon.env.
    systemd-run --unit="$UNIT" --uid="$(id -u)" --gid="$(id -g)" \
        --property="WorkingDirectory=${SCRATCH}" \
        --property="EnvironmentFile=${SCRATCH}/daemon.env" \
        --property="StandardOutput=journal" --property="StandardError=journal" \
        --collect --wait \
        /bin/sh -c "exec '${REPO_ROOT}/.venv/bin/python' -m daemon" \
        >> "$BUNDLE/systemd-run.log" 2>&1 &
    SYSTEMD_RUN_PID=$!
}
wait_unit_pid() {
    local t0 now
    t0="$(now_s)"
    while :; do
        DAEMON_PID="$(systemctl show "$UNIT" -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
        if [ -n "$DAEMON_PID" ] && [ "$DAEMON_PID" != "0" ]; then return 0; fi
        now="$(now_s)"; [ $((now - t0)) -ge 25 ] && return 1
        time_left || return 1
        sleep 1
    done
}
wait_livez() {
    local port="$1" limit="$2" t0 now code
    t0="$(now_s)"
    while :; do
        code="$(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${port}/livez" 2>/dev/null)"
        [ -z "$code" ] && code=000
        [ "$code" = "200" ] && return 0
        now="$(now_s)"; [ $((now - t0)) -ge "$limit" ] && return 1
        time_left || return 1
        sleep 1
    done
}
wait_unit_gone() {
    local t0 now ls
    t0="$(now_s)"
    while :; do
        ls="$(systemctl show "$UNIT" -p LoadState --value 2>/dev/null | tr -d '[:space:]')"
        [ -z "$ls" ] || [ "$ls" = "not-found" ] && return 0
        now="$(now_s)"; [ $((now - t0)) -ge 30 ] && return 1
        time_left || return 1
        sleep 1
    done
}
# wait_marker <regex> <limit_s> — polls the unit journal until the marker
# appears; returns 0 on sight. Used to time T_recovery honestly.
wait_marker() {
    local re="$1" limit="$2" t0 now
    t0="$(now_s)"
    while :; do
        grep -qE "$re" "$BUNDLE/journal-${UNIT}.log" 2>/dev/null && return 0
        now="$(now_s)"; [ $((now - t0)) -ge "$limit" ] && return 1
        time_left || return 1
        sleep 1
        journalctl -u "$UNIT" --no-pager --output=short-iso 2>/dev/null > "$BUNDLE/journal-${UNIT}.log"
    done
}
capture_journal() {
    journalctl -u "$UNIT" --no-pager --output=short-iso 2>/dev/null > "$BUNDLE/journal-${UNIT}.log"
    if [ -f "$SCRATCH/data/logs/ensemble.log" ]; then
        cp -f "$SCRATCH/data/logs/ensemble.log" "$BUNDLE/boot.log" 2>/dev/null || true
    fi
}

if phase_on P3; then
    STEP=P3
    note "P3 isolated boot in transient unit ${UNIT}.service"
    start_unit
    if ! wait_unit_pid; then
        bad "daemon MainPID never appeared (unit ${UNIT})"
        capture_journal
        echo "RESULT: FAIL"; exit 1
    fi
    ok "daemon pid=${DAEMON_PID}"
    if assert_isolated "$DAEMON_PID"; then
        ok "cgroup isolation ASSERTED: $(daemon_cgroup "$DAEMON_PID")"
    else
        capture_journal; echo "RESULT: FAIL"; exit 1
    fi
    if ! wait_livez "$DRILL_PORT" 90; then
        bad "/livez never returned 200 within 90s"
        capture_journal; echo "RESULT: FAIL"; exit 1
    fi
    ok "/livez 200 on 127.0.0.1:${DRILL_PORT}"
    READYZ_CODE="$(curl -s --max-time 10 -o "$BUNDLE/readyz.json" -w '%{http_code}' "http://127.0.0.1:${DRILL_PORT}/readyz" 2>/dev/null)"
    [ -z "$READYZ_CODE" ] && READYZ_CODE=000
    if [ "$READY_CODE" = "200" ]; then ok "/readyz 200"; else bad "/readyz returned ${READY_CODE}"; capture_journal; echo "RESULT: FAIL"; exit 1; fi
    curl -s "http://127.0.0.1:${DRILL_PORT}/livez" > "$BUNDLE/livez.json" 2>/dev/null || true
    capture_journal
    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P4 — seed the pre-crash state
# ════════════════════════════════════════════════════════════════════════════
if phase_on P4; then
    STEP=P4
    note "P4 seed: instance + schedule + fresh-dead stranded MQ row"

    # 4a — real API surface: create the instance (auth is OFF by default).
    INSTANCE_ID="hrr-drill-${TS}-${RUN_ID}"
    INST_CODE="$(curl -s --max-time 30 -o "$BUNDLE/instance_create.json" -w '%{http_code}' \
        -X POST "http://127.0.0.1:${DRILL_PORT}/api/instances" \
        -H 'Content-Type: application/json' \
        -d "{\"agent_id\":\"ari\",\"instance_id\":\"${INSTANCE_ID}\"}" 2>/dev/null)"
    [ -z "$INST_CODE" ] && INST_CODE=000
    if [ "$INST_CODE" != "201" ] && [ "$INST_CODE" != "200" ]; then
        bad "POST /api/instances returned ${INST_CODE}: $(head -c 400 "$BUNDLE/instance_create.json" 2>/dev/null)"
        echo "RESULT: FAIL"; exit 1
    fi
    ok "instance created via API (HTTP ${INST_CODE}) instance_id=${INSTANCE_ID}"

    # 4b — the fixture shape: status='running' (tests/unit/test_message_queue_boot_recovery.py M1)
    psql_scratch "UPDATE instances SET status='running' WHERE instance_id='${INSTANCE_ID}';" >/dev/null
    INST_STATUS="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${INSTANCE_ID}';")"
    if [ "$INST_STATUS" = "running" ]; then ok "instance status='running' (fixture shape)"; else bad "instance status=${INST_STATUS}"; fi

    # 4c — the fresh-dead stranded MQ row (M1 fixture, NO task row)
    MESSAGE_ID="hrr-msg-${TS}-${RUN_ID}"
    psql_scratch "INSERT INTO message_queue
      (message_id, instance_id, content, type, status, priority, retry_count, max_retries,
       enqueued_at, last_activity_at, processing_started_at)
      VALUES ('${MESSAGE_ID}', '${INSTANCE_ID}', 'fixture content', 'agent', 'processing', 1, 0, 5,
              now() - interval '2 minutes', now() - interval '2 minutes', now() - interval '2 minutes');" >/dev/null
    SEED_ROW="$(psql_scratch "SELECT status||'|'||COALESCE(last_activity_at::text,'')||'|'||COALESCE(processing_started_at::text,'') FROM message_queue WHERE message_id='${MESSAGE_ID}';")"
    case "$SEED_ROW" in
        processing\|*) ok "fresh-dead stranded row seeded (status=processing, ts=${SEED_ROW#*|})" ;;
        *) bad "seed row unexpected: ${SEED_ROW:-<none>}" ;;
    esac

    # 4d — a schedule (survival observation)
    RUN_AT="$(date -u -d '+1 hour' +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)"
    SCHEDULE_ID="hrr-sched-${TS}-${RUN_ID}"
    SCHED_CODE="$(curl -s --max-time 30 -o "$BUNDLE/schedule_create.json" -w '%{http_code}' \
        -X POST "http://127.0.0.1:${DRILL_PORT}/api/schedules" \
        -H 'Content-Type: application/json' \
        -d "{\"source_id\":\"${SCHEDULE_ID}\",\"agent_id\":\"ari\",\"message\":\"hrr drill survival probe\",\"project_id\":\"hrr-drill\",\"recurrence\":\"once\",\"run_at\":\"${RUN_AT}\"}" 2>/dev/null)"
    [ -z "$SCHED_CODE" ] && SCHED_CODE=000
    if [ "$SCHED_CODE" = "201" ]; then ok "schedule created (HTTP 201) source_id=${SCHEDULE_ID}"; else bad "POST /api/schedules returned ${SCHED_CODE}: $(head -c 400 "$BUNDLE/schedule_create.json" 2>/dev/null)"; fi

    PRE_COUNTS="$(mq_counts "$INSTANCE_ID")"
    cat > "$BUNDLE/seed.json" <<EOF
{
  "phase": "P4",
  "instance_id": "${INSTANCE_ID}",
  "instance_status": "${INST_STATUS}",
  "message_id": "${MESSAGE_ID}",
  "message_row": "$(jstr "$SEED_ROW")",
  "schedule_id": "${SCHEDULE_ID}",
  "schedule_http": "${SCHED_CODE}",
  "mq_counts": "$(jstr "$PRE_COUNTS")",
  "note": "fresh-dead row: status='processing', last_activity_at=now()-2min, processing_started_at=same, NO task row (mirrors tests/helpers/hard_reboot_fixtures.py + tests/unit/test_message_queue_boot_recovery.py M1)"
}
EOF
    cat > "$BUNDLE/pre_crash.json" <<EOF
{
  "phase": "P4-pre_crash",
  "utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "daemon_pid": "${DAEMON_PID}",
  "daemon_cgroup": "$(jstr "$(daemon_cgroup "$DAEMON_PID")")",
  "instance_status": "${INST_STATUS}",
  "schedule_row": "$(jstr "$(psql_scratch "SELECT source_id||'|'||status||'|'||enabled||'|'||autostart FROM source_configs WHERE source_id='${SCHEDULE_ID}';")")",
  "mq_processing_count": "$(printf '%s' "$PRE_COUNTS" | awk '{print $1}')",
  "mq_pending_count": "$(printf '%s' "$PRE_COUNTS" | awk '{print $2}')",
  "busy_guard_predicate": "pending_count > 0 or processing_count > 0"
}
EOF
    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P5 — hard kill (kill -9), only after the cgroup check
# ════════════════════════════════════════════════════════════════════════════
if phase_on P5; then
    STEP=P5
    note "P5 hard kill of the drill daemon (kill -9, no clean shutdown)"
    if ! assert_isolated "$DAEMON_PID"; then echo "RESULT: FAIL"; exit 1; fi
    kill -9 "$DAEMON_PID" 2>/dev/null || true
    sleep 2
    if kill -0 "$DAEMON_PID" 2>/dev/null; then bad "daemon pid ${DAEMON_PID} survived kill -9"; else ok "daemon pid ${DAEMON_PID} killed (SIGKILL)"; fi
    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P6 — simulated reboot (unit stop + FRESH systemd-run start)
# ════════════════════════════════════════════════════════════════════════════
if phase_on P6; then
    STEP=P6
    note "P6 simulated reboot: stop the transient unit, then a FRESH start"
    T_RESTART="$(now_s)"
    sudo -n systemctl stop "$UNIT" >/dev/null 2>&1 || true
    wait_unit_gone || note "unit row still visible after stop (proceeding)"
    start_unit
    if ! wait_unit_pid; then bad "daemon MainPID never appeared after the fresh start"; capture_journal; echo "RESULT: FAIL"; exit 1; fi
    if ! assert_isolated "$DAEMON_PID"; then capture_journal; echo "RESULT: FAIL"; exit 1; fi
    ok "fresh daemon pid=${DAEMON_PID} (new boot epoch)"
    if wait_livez "$DRILL_PORT" 90; then
        T_READYZ="$(now_s)"
        ok "/livez 200 after the fresh start"
    else
        bad "/livez never returned 200 after the fresh start"; capture_journal; echo "RESULT: FAIL"; exit 1
    fi
    # T_recovery = fresh start → the recovery markers observed (MQ boot pass
    # completed). Polled from the journal, not estimated.
    capture_journal
    if wait_marker "\[HardReboot\] MQ boot pass: reset=" 60; then
        T_RECOVERY="$(now_s)"
        ok "MQ boot pass marker observed after the fresh start (T_recovery=${T_RECOVERY})"
    else
        T_RECOVERY="$(now_s)"
        bad "MQ boot pass marker never observed within 60s of the fresh start"
    fi
    capture_journal
    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P7 — observe
# ════════════════════════════════════════════════════════════════════════════
if phase_on P7; then
    STEP=P7
    note "P7 observe: recovery markers, MQ accounting, timings"
    READYZ_CODE="$(curl -s --max-time 10 -o "$BUNDLE/readyz_post.json" -w '%{http_code}' "http://127.0.0.1:${DRILL_PORT}/readyz" 2>/dev/null)"
    [ -z "$READY_CODE" ] && READYZ_CODE=000
    [ "$READY_CODE" = "200" ] && ok "/readyz 200 on the restarted daemon" || bad "/readyz returned ${READY_CODE}"
    capture_journal
    JLOG="$BUNDLE/journal-${UNIT}.log"

    # Marker strings — VERBATIM from the source (assert on these, not on
    # paraphrases):
    #   "Boot preflight: PostgreSQL reachable"            daemon/__main__.py:246
    #   "DependencyBus started"                           daemon/api.py:3992
    #   "AutoContinue boot pass: %s"                      daemon/api.py:2608
    #   "[HardReboot] MQ boot pass: reset=%d"             message_queue_boot_recovery.py:102
    #   "Boot epoch captured (DB clock): %s"              boot_epoch.py:132
    #   "[BOOT_CONTINUE] orphan-sweep summary: ..."       auto_continue_boot_pass.py:866
    MARKER_LIST=(
        "Boot preflight: PostgreSQL reachable"
        "DependencyBus started"
        "AutoContinue boot pass:"
        "\[HardReboot\] MQ boot pass: reset="
        "Boot epoch captured \(DB clock\):"
        "\[BOOT_CONTINUE\] orphan-sweep summary:"
    )
    for m in "${MARKER_LIST[@]}"; do
        LINE="$(grep -E "$m" "$JLOG" 2>/dev/null | tail -1)"
        if [ -n "$LINE" ]; then ok "marker present: ${LINE}"; else bad "marker ABSENT: ${m}"; fi
    done
    RESET_N="$(grep -E "\[HardReboot\] MQ boot pass: reset=" "$JLOG" 2>/dev/null | tail -1 | grep -oE 'reset=[0-9]+' | cut -d= -f2)"
    [ -n "$RESET_N" ] && ok "MQ boot pass reset count = ${RESET_N}" || bad "MQ boot pass reset count not parsed"

    # boot epoch is a PROCESS-GLOBAL (no DB column) — parse it from the log
    BOOT_EPOCH_LOG="$(grep -E "Boot epoch captured \(DB clock\):" "$JLOG" 2>/dev/null | tail -1 | sed -E 's/.*Boot epoch captured \(DB clock\): //')"
    note "boot epoch (from log, NOT a DB column): ${BOOT_EPOCH_LOG:-<not found>}"

    MSG_STATUS="$(psql_scratch "SELECT status FROM message_queue WHERE message_id='${MESSAGE_ID}';")"
    POST_COUNTS="$(mq_counts "$INSTANCE_ID")"
    PROC_C="$(printf '%s' "$POST_COUNTS" | awk '{print $1}')"
    PEND_C="$(printf '%s' "$POST_COUNTS" | awk '{print $2}')"
    GUARD="false"; { [ "$PEND_C" -gt 0 ] || [ "$PROC_C" -gt 0 ]; } && GUARD="true"

    # A3 (CORRECTED): the reset flips processing->ready, so processing_count==0
    # holds ALWAYS after the reset. What the override gates is the BUSY-GUARD
    # CLEARING (the row lands in pending → the guard stays tripped on the
    # shipped default). See tests/unit/test_message_queue_boot_recovery.py M5.
    [ "$MSG_STATUS" = "ready" ] && ok "P4-1 stranded row status='ready' after boot" || bad "P4-1 stranded row status='${MSG_STATUS}'"
    [ "$PROC_C" = "0" ] && ok "P4-2 processing_count == 0" || bad "P4-2 processing_count == ${PROC_C}"
    [ "${PEND_C:-0}" -ge 1 ] && ok "P4-3 pending_count == ${PEND_C} (row still counted)" || bad "P4-3 pending_count == ${PEND_C} (expected >= 1)"
    [ "$GUARD" = "true" ] && ok "P4-4 busy-guard predicate TRUE (${PEND_C}>0 or ${PROC_C}>0) — shipped-default branch" \
                         || ok "P4-7 busy-guard predicate FALSE — QUEUE_DISCARD_ON_STARTUP=true branch observed"
    [ "${PEND_C:-0}" -ge 1 ] && note "busy-guard branch: VISIBLE-PENDING (shipped default, QUEUE_DISCARD_ON_STARTUP=false)" \
                            || note "busy-guard branch: CLEARED (QUEUE_DISCARD_ON_STARTUP=true override)"

    SCHED_ROW="$(psql_scratch "SELECT source_id||'|'||status||'|'||enabled||'|'||autostart FROM source_configs WHERE source_id='${SCHEDULE_ID}';")"
    [ -n "$SCHED_ROW" ] && ok "schedule survived the reboot: ${SCHED_ROW}" || bad "schedule row missing after the reboot"
    INST_STATUS2="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${INSTANCE_ID}';")"
    note "instance status after reboot: ${INST_STATUS2} (D13 task-less RUNNING ghosts are OUT OF SCOPE — reported, never failed)"
    GHOST="$(psql_scratch "SELECT count(*) FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.instance_id AND t.status IN ('pending','running','paused'));")"
    note "D13 task-less RUNNING ghost count (out of scope, informational): ${GHOST}"

    # timings — T_restart (unit stop → fresh start issued), T_readyz (fresh
    # start → /readyz 200), T_recovery (fresh start → recovery markers seen),
    # T_end_to_end = T_recovery − T_restart.
    [ "$T_RECOVERY" -gt 0 ] || T_RECOVERY="$(now_s)"
    if [ -n "$T_RESTART" ] && [ "$T_RESTART" -gt 0 ] && [ "$T_RECOVERY" -gt 0 ]; then
        T_E2E=$((T_RECOVERY - T_RESTART))
    else
        T_E2E=-1
    fi
    cat > "$BUNDLE/timings.json" <<EOF
{
  "phase": "P7",
  "t_restart_epoch": ${T_RESTART},
  "t_readyz_epoch": ${T_READYZ},
  "t_recovery_epoch": ${T_RECOVERY},
  "t_end_to_end_seconds": ${T_E2E},
  "ac6_criterion_seconds": 30,
  "ac6_meets_criterion": $( [ "${T_E2E:-0}" -ge 0 ] && [ "${T_E2E}" -le 30 ] && echo true || echo false ),
  "boot_epoch_from_log": "$(jstr "$BOOT_EPOCH_LOG")",
  "mq_reset_count": "${RESET_N}",
  "ac6_reproduced": "daemon-path only — unit stop + fresh start (NO real host reboot), local PG already running (NO parallel PG start race), transient unit is NOT the production unit",
  "ac6_not_reproduced": ["real host power-loss semantics", "PG started in parallel with the daemon", "production unit Restart=/RestartSec= semantics", "G6 After=postgresql.service ordering"]
}
EOF
    note "timings: T_restart=${T_RESTART} T_readyz=${T_READYZ} T_recovery=${T_RECOVERY} T_end_to_end=${T_E2E}s (AC6 criterion ≤30s, daemon-path only)"
    cat > "$BUNDLE/post_restart.json" <<EOF
{
  "phase": "P7-post_restart",
  "utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "daemon_pid": "${DAEMON_PID}",
  "daemon_cgroup": "$(jstr "$(daemon_cgroup "$DAEMON_PID")")",
  "readyz_http": "${READY_CODE}",
  "message_status": "${MSG_STATUS}",
  "mq_processing_count": "${PROC_C}",
  "mq_pending_count": "${PEND_C}",
  "busy_guard_predicate_value": "${GUARD}",
  "busy_guard_branch": "$( [ "$GUARD" = "true" ] && echo visible-pending-shipped-default || echo cleared-override-true )",
  "instance_status": "${INST_STATUS2}",
  "schedule_row": "$(jstr "$SCHED_ROW")",
  "d13_taskless_running_ghosts": "${GHOST}",
  "blind_spot_llm": "structural observation only — the drill daemon points OPENAI_BASE_URL at a dead local endpoint (http://127.0.0.1:1/v1); no live proxy call, no LLM-dependent assertion"
}
EOF
    time_left || { deadline_hit; echo "RESULT: TIMEOUT"; exit 124; }
fi

# ════════════════════════════════════════════════════════════════════════════
# P8 — cleanup (zero residue)
# ════════════════════════════════════════════════════════════════════════════
if phase_on P8; then
    STEP=P8
    note "P8 cleanup + zero-residue verification"
fi

# tree state at exit + drift flag
TREE_HEAD_EXIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
TREE_PORCELAIN_EXIT="$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)"
DRIFT="no"
if [ "${P0_RAN:-0}" = "1" ]; then
    if [ -n "${TREE_HEAD_BOOT:-}" ] && [ "$TREE_HEAD_BOOT" != "$TREE_HEAD_EXIT" ]; then DRIFT="yes"; fi
    if [ "${TREE_PORCELAIN_BOOT:-}" != "$TREE_PORCELAIN_EXIT" ]; then DRIFT="yes"; fi
fi
cat > "$BUNDLE/tree_state.json" <<EOF
{
  "head_boot": "${TREE_HEAD_BOOT:-}",
  "head_exit": "${TREE_HEAD_EXIT}",
  "porcelain_boot": "$(jstr "${TREE_PORCELAIN_BOOT:-}")",
  "porcelain_exit": "$(jstr "$TREE_PORCELAIN_EXIT")",
  "tree_drift_during_run": "${DRIFT}",
  "note": "a dirty tree NEVER fails the drill; a concurrent dev lane owns this worktree"
}
EOF
if [ "$DRIFT" = "yes" ]; then note "TREE DRIFT DURING RUN (HEAD ${TREE_HEAD_BOOT:-?} -> ${TREE_HEAD_EXIT}) — recorded, not a failure"; fi

# teardown + residue verification
DB_AFTER=""; ROLE_AFTER=""; PORT_AFTER=""; UNITS_AFTER=""
teardown
DB_AFTER="$(psql_super "SELECT count(*) FROM pg_database WHERE datname='${PG_DB}';" 2>/dev/null | tr -d '[:space:]')"
ROLE_AFTER="$(psql_super "SELECT count(*) FROM pg_roles WHERE rolname='${PG_ROLE}';" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$DRILL_PORT" ] && (exec 3<>/dev/tcp/127.0.0.1/$DRILL_PORT) 2>/dev/null; then PORT_AFTER="bound"; else PORT_AFTER="free"; fi
UNITS_AFTER="$(systemctl list-units --all --no-legend 'hrr-*' 2>/dev/null | grep -c . || true)"
LIVE_MAIN_PID2="$(systemctl show ensemble-main.service -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
LIVE_MAIN_RESTARTS2="$(systemctl show ensemble-main.service -p NRestarts --value 2>/dev/null | tr -d '[:space:]')"
LIVE_MAIN_TS2="$(systemctl show ensemble-main.service -p ActiveEnterTimestamp --value 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
LIVE_DEMO_PID2="$(systemctl show ensemble-demo.service -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
LIVE_LINK_MAIN2="$(readlink -f /home/nea/agents-ensemble/current 2>/dev/null)"

[ "$DB_AFTER" = "0" ] && ok "scratch database dropped (verified gone)" || bad "scratch database still present (count=${DB_AFTER})"
[ "$ROLE_AFTER" = "0" ] && ok "scratch role dropped (verified gone)" || bad "scratch role still present (count=${ROLE_AFTER})"
[ "$PORT_AFTER" = "free" ] && ok "drill port ${DRILL_PORT} free" || bad "drill port ${DRILL_PORT} still bound"
[ "$UNITS_AFTER" = "0" ] && ok "no hrr-* units remain" || bad "hrr-* units remain (count=${UNITS_AFTER})"
if [ "${P0_RAN:-0}" = "1" ]; then
    if [ "$LIVE_MAIN_PID" = "$LIVE_MAIN_PID2" ] && [ "$LIVE_MAIN_RESTARTS" = "$LIVE_MAIN_RESTARTS2" ] \
       && [ "$LIVE_MAIN_TS" = "$LIVE_MAIN_TS2" ] && [ "$LIVE_DEMO_PID" = "$LIVE_DEMO_PID2" ] \
       && [ "$LIVE_LINK_MAIN" = "$LIVE_LINK_MAIN2" ]; then
        ok "live daemon/demo/install UNCHANGED (pid, NRestarts, ActiveEnterTimestamp, symlink)"
    else
        bad "live surfaces CHANGED — main ${LIVE_MAIN_PID}->${LIVE_MAIN_PID2}, restarts ${LIVE_MAIN_RESTARTS}->${LIVE_MAIN_RESTARTS2}, demo ${LIVE_DEMO_PID}->${LIVE_DEMO_PID2}, link ${LIVE_LINK_MAIN}->${LIVE_LINK_MAIN2}"
    fi
else
    note "P0 did not run — live-surface baseline not captured, comparison SKIPPED (reported, not a silent pass)"
fi

cat > "$BUNDLE/cleanup.json" <<EOF
{
  "phase": "P8",
  "utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "scratch_db_rows_after_drop": "${DB_AFTER}",
  "scratch_role_rows_after_drop": "${ROLE_AFTER}",
  "drill_port": "${DRILL_PORT}",
  "drill_port_after": "${PORT_AFTER}",
  "hrr_units_after": "${UNITS_AFTER}",
  "scratch_cwd_removed": "$([ -d "$SCRATCH" ] && echo false || echo true)",
  "fake_home_removed": "$([ -d "$FAKE_HOME" ] && echo false || echo true)",
  "live_after": {
    "main_pid": "${LIVE_MAIN_PID2}",
    "main_nrestarts": "${LIVE_MAIN_RESTARTS2}",
    "main_active_enter": "$(jstr "$LIVE_MAIN_TS2")",
    "demo_pid": "${LIVE_DEMO_PID2}",
    "install_link": "$(jstr "$LIVE_LINK_MAIN2")"
  },
  "never_touched": ["ensemble-main.service", "ensemble-demo.service", "/home/nea/agents-ensemble", "/home/nea/agents-ensemble-demo", "10.44.0.2/ensemble_prod", "port 8088", "/etc/systemd/system/*"]
}
EOF

echo
echo "Evidence bundle: ${BUNDLE}"
echo "Failed checks: ${FAIL}"
if [ "$TIMEOUT_FLAG" = "1" ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$FAIL" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    exit 1
fi
