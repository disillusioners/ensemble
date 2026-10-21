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
#     P4  seed the pre-crash state (task-less RUNNING ghost + schedule +
#         fresh-dead MQ row + a SECOND, genuinely resumable in-flight turn
#         parked on the local stalling mock LLM)
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
#   * PORT: free ports from 10000-10100 (dynamic probe; fallback
#     10007/10008) — TWO ports: the drill daemon's + the mock LLM's.
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
#   journal-<unit>.log journal-postrestart-<unit>.log mock_llm.log
#   seed.json pre_crash.json post_restart.json
#   timings.json cleanup.json tree_state.json
#
# ── POST-RESTART MARKER ANCHORING (defect-1 fix) ─────────────────────────────
#   The unit journal ACCUMULATES across the simulated reboot (same unit
#   name ⇒ journalctl -u returns pre-crash AND post-restart entries). A
#   naive grep for "[HardReboot] MQ boot pass: reset=" can therefore
#   match the PRE-CRASH boot's line and report T_recovery / marker
#   presence from a boot that never ran the pass — a false positive on
#   the drill's most important assertion. Every post-restart observation
#   in P6/P7 is therefore anchored: the journal CURSOR is captured
#   immediately BEFORE the unit stop, and all post-restart markers /
#   parses read journalctl --after-cursor=<that cursor> only.
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
RESUME_INSTANCE_ID=""
RESUME_MESSAGE_ID=""
RESUME_TASK_ID=""
RESUME_WORK_ID=""
MOCK_PORT=""
MOCK_PID=""
MOCK_SCRIPT=""
MOCK_PY=""
PRE_RESTART_CURSOR=""
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

# wait_resume_task — bounded poll for the RESUMABLE instance to reach the
# state that makes it genuinely resumable: instance status='running' AND
# a task row with status='running' (a real task row ⇒ the graph is
# executing ⇒ a LangGraph checkpoint exists for that thread). Bounded
# (default 30s) so a stuck dispatch fails the drill loudly instead of
# hanging the pack.
wait_resume_task() {
    local limit="${1:-30}" t0 now st tk
    t0="$(now_s)"
    while :; do
        st="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${RESUME_INSTANCE_ID}';" | tr -d '[:space:]')"
        tk="$(psql_scratch "SELECT count(*) FROM task WHERE instance_id='${RESUME_INSTANCE_ID}' AND status='running';" | tr -d '[:space:]')"
        if [ "$st" = "running" ] && [ "${tk:-0}" -ge 1 ]; then
            RESUME_TASK_ID="$(psql_scratch "SELECT id FROM task WHERE instance_id='${RESUME_INSTANCE_ID}' AND status='running' ORDER BY created_at ASC LIMIT 1;" | tr -d '[:space:]')"
            RESUME_WORK_ID="$(psql_scratch "SELECT work_id FROM task WHERE id='${RESUME_TASK_ID}';" | tr -d '[:space:]')"
            return 0
        fi
        now="$(now_s)"; [ $((now - t0)) -ge "$limit" ] && return 1
        time_left || return 1
        sleep 1
    done
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
    # Mock LLM server: drill-owned process, killed by the EXACT PID we
    # started (never by pattern — signal fence). It is killed AFTER the
    # unit stop so the post-restart resumed turn is never racing a dead
    # endpoint during observation.
    if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
        kill "$MOCK_PID" >/dev/null 2>&1
        sleep 1
        kill -0 "$MOCK_PID" 2>/dev/null && kill -9 "$MOCK_PID" >/dev/null 2>&1
    fi
    # Belt: if the mock's LISTEN socket is STILL held (starter/wrapper fork
    # class), kill the exact socket owner — never a pattern.
    if [ -n "$MOCK_PORT" ]; then
        MOCK_STRAGGLER="$(ss -tlnpH "sport = :${MOCK_PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\),.*/\1/p' | head -1)"
        if [ -n "$MOCK_STRAGGLER" ] && kill -0 "$MOCK_STRAGGLER" 2>/dev/null; then
            kill "$MOCK_STRAGGLER" >/dev/null 2>&1
            sleep 1
            kill -0 "$MOCK_STRAGGLER" 2>/dev/null && kill -9 "$MOCK_STRAGGLER" >/dev/null 2>&1
        fi
    fi
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

    # port band: dynamic probe 10000-10100, fallback 10007 then 10008.
    # TWO ports: DRILL_PORT (the drill daemon) and MOCK_PORT (the local
    # stalling mock LLM). Both exclude the reserved set and each other.
    for p in $(seq 10000 10100) 10007 10008; do
        case "$p" in 8079|9797|7979|8088) continue ;; esac
        if ! (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then DRILL_PORT="$p"; break; fi
    done
    case "$DRILL_PORT" in
        8079|9797|7979|8088) DRILL_PORT="" ;;
    esac
    for p in $(seq 10000 10100) 10007 10008; do
        [ "$p" = "$DRILL_PORT" ] && continue
        case "$p" in 8079|9797|7979|8088) continue ;; esac
        if ! (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null; then MOCK_PORT="$p"; break; fi
    done
    case "$MOCK_PORT" in
        8079|9797|7979|8088) MOCK_PORT="" ;;
    esac
    [ -n "$DRILL_PORT" ] && [ -n "$MOCK_PORT" ] && [ "$DRILL_PORT" != "$MOCK_PORT" ] \
        && ok "ports selected: daemon=${DRILL_PORT} mock=${MOCK_PORT}" \
        || { bad "no free port pair in 10000-10100 (drill=${DRILL_PORT:-none} mock=${MOCK_PORT:-none})"; }

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
  "mock_port": ${MOCK_PORT:-0},
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
# LLM: LOCAL stalling mock (127.0.0.1:${MOCK_PORT}, started by this drill
# in P2 and killed by the cleanup trap by exact PID). The mock SLEEPS
# before answering so the dispatched turn is still IN FLIGHT when the
# daemon is kill -9'd — that is what leaves a genuinely resumable orphan
# (task row + LangGraph checkpoint) for the auto-continue pass. NO live
# proxy is contacted; the key is a clearly-fake constant.
OPENAI_BASE_URL=http://127.0.0.1:${MOCK_PORT}/v1
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

    # ── the LOCAL STALLING MOCK LLM (one-file, stdlib-only) ────────────────
    # Modeled on tests/mock_llm_server.py (build_non_stream_response) —
    # the response shape this daemon's ThinkingChatOpenAI provably
    # accepts: non-streaming chat.completion, assistant message with
    # content + reasoning_content:null, finish_reason "stop", usage
    # block, NO tool call. The handler SLEEPS STALL_SECONDS before
    # answering so the turn stays IN FLIGHT (the first superstep's
    # LangGraph checkpoint is already written while the LLM call is
    # pending). Started here (BEFORE the P3 boot) so no daemon LLM call
    # ever hits connection-refused; killed by exact PID in teardown.
    MOCK_PY="$REPO_ROOT/.venv/bin/python"
    [ -x "$MOCK_PY" ] || MOCK_PY="$(command -v python3 || echo python3)"
    MOCK_SCRIPT="$SCRATCH/hrr_mock_llm.py"
    cat > "$MOCK_SCRIPT" <<'MOCK_EOF'
#!/usr/bin/env python3
"""HRR-drill-owned mock: OpenAI-compatible /v1/chat/completions that STALLS.

Stdlib only. The stall keeps the dispatched turn in flight across the
kill -9 so the auto-continue boot pass has a genuinely resumable orphan.
Response shape mirrors tests/mock_llm_server.py::build_non_stream_response
(non-streaming, reasoning_content null, finish_reason stop, NO tool call).
"""
import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STALL_SECONDS = 300.0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if self.path.split("?")[0].rstrip("/") in ("/health", "/healthz"):
            self._send_json(200, {"status": "ok", "stall_seconds": STALL_SECONDS})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.split("?")[0].rstrip("/").endswith("/chat/completions"):
            self._send_json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        req = {}
        if length > 0:
            try:
                req = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeDecodeError):
                req = {}
        time.sleep(STALL_SECONDS)  # the stall IS the feature
        self._send_json(200, {
            "id": "mockchat-%d" % int(time.time() * 1000),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": (req.get("model") if isinstance(req, dict) else None) or "drill-mock",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "hrr drill mock: turn kept in flight (stalled %.0fs)" % STALL_SECONDS,
                    "reasoning_content": None,
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 9, "total_tokens": 10},
        })

    def log_message(self, fmt, *args):  # silence per-request stderr noise
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    main()
MOCK_EOF
    # Start with an EXPLICIT MINIMAL env (env -i — the mock touches no DB,
    # so the poison fence is honored by construction). `env` execs python
    # in-place, so $! IS the server process; the ss-based adoption below
    # verifies this (a wrapper shell would fork and $! would point at the
    # wrapper, not the listener — the socket owner is the authoritative
    # exact PID).
    env -i PATH=/usr/bin:/bin HOME="$FAKE_HOME" \
        "$MOCK_PY" "$MOCK_SCRIPT" --host 127.0.0.1 --port "$MOCK_PORT" \
        > "$BUNDLE/mock_llm.log" 2>&1 &
    MOCK_PID=$!
    MOCK_UP=0
    mt0="$(now_s)"
    while :; do
        mcode="$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${MOCK_PORT}/health" 2>/dev/null)"
        [ -z "$mcode" ] && mcode=000
        [ "$mcode" = "200" ] && { MOCK_UP=1; break; }
        mnow="$(now_s)"; [ $((mnow - mt0)) -ge 10 ] && break
        sleep 1
    done
    if [ "$MOCK_UP" = "1" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
        # Adopt the LISTENING socket's owner as the authoritative exact PID
        # (never a pattern): $! may be a wrapper shell if anything in the
        # start chain forked, and killing a wrapper would orphan the server.
        MOCK_SOCK_PID="$(ss -tlnpH "sport = :${MOCK_PORT}" 2>/dev/null | sed -n 's/.*pid=\([0-9][0-9]*\),.*/\1/p' | head -1)"
        if [ -n "$MOCK_SOCK_PID" ] && [ "$MOCK_SOCK_PID" != "$MOCK_PID" ]; then
            note "mock server real pid=${MOCK_SOCK_PID} (starter pid ${MOCK_PID} forked) — tracking the socket owner"
            MOCK_PID="$MOCK_SOCK_PID"
        fi
        ok "stalling mock LLM up: pid=${MOCK_PID} port=${MOCK_PORT} (GET /health 200)"
    else
        bad "mock LLM failed to start (pid=${MOCK_PID:-?} health=${mcode:-000})"; head -20 "$BUNDLE/mock_llm.log" 2>/dev/null
        echo "RESULT: FAIL"; exit 1
    fi

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
# journal_cursor — the CURRENT head cursor of the unit journal (last
# entry + "-- cursor: s=…"). Captured immediately BEFORE the simulated
# reboot; everything the drill observes after the restart MUST come from
# --after-cursor=<that cursor> so a pre-crash marker can never satisfy a
# post-restart poll (defect-1 fix: stale-marker false positive).
journal_cursor() {
    journalctl -u "$UNIT" --no-pager --show-cursor -n 1 --output=short-iso 2>/dev/null \
        | sed -n 's/^-- cursor: //p' | tail -1 | tr -d '[:space:]'
}
# capture_journal — FULL unit journal (pre-crash + post-restart, evidence)
# plus the daemon's own ensemble.log snapshot.
capture_journal() {
    journalctl -u "$UNIT" --no-pager --output=short-iso 2>/dev/null > "$BUNDLE/journal-${UNIT}.log"
    if [ -f "$SCRATCH/data/logs/ensemble.log" ]; then
        cp -f "$SCRATCH/data/logs/ensemble.log" "$BUNDLE/boot.log" 2>/dev/null || true
    fi
}
# capture_journal_post — ONLY the entries after the pre-restart cursor:
# the authoritative view for every post-restart observation (P6 markers,
# P7 marker list, parses, AutoContinue counts).
capture_journal_post() {
    if [ -n "$PRE_RESTART_CURSOR" ]; then
        journalctl -u "$UNIT" --no-pager --output=short-iso \
            --after-cursor="$PRE_RESTART_CURSOR" 2>/dev/null \
            > "$BUNDLE/journal-postrestart-${UNIT}.log"
    else
        # No anchor → an EMPTY post-restart view is the only honest view
        # (fail-closed: markers will "never appear" and the drill fails
        # loudly instead of silently matching a pre-crash line).
        : > "$BUNDLE/journal-postrestart-${UNIT}.log"
    fi
}
# wait_marker <regex> <limit_s> — polls the POST-RESTART journal view
# (cursor-anchored) until the marker appears; returns 0 on sight. Used to
# time T_recovery honestly: a match here is proof the POST-RESTART boot
# emitted the line, never the pre-crash one.
wait_marker() {
    local re="$1" limit="$2" t0 now
    t0="$(now_s)"
    while :; do
        grep -qE "$re" "$BUNDLE/journal-postrestart-${UNIT}.log" 2>/dev/null && return 0
        now="$(now_s)"; [ $((now - t0)) -ge "$limit" ] && return 1
        time_left || return 1
        sleep 1
        capture_journal_post
    done
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
    READY_CODE="$(curl -s --max-time 10 -o "$BUNDLE/readyz.json" -w '%{http_code}' "http://127.0.0.1:${DRILL_PORT}/readyz" 2>/dev/null)"
    [ -z "$READY_CODE" ] && READY_CODE=000
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
    # The API is NOT obliged to honour a client-supplied instance_id — the
    # daemon may mint its own. Every fixture query below keys on
    # instance_id, so re-read the id the daemon ACTUALLY assigned and use it
    # for the UPDATE, the MQ row and the counting queries.
    API_INSTANCE_ID="$(sed -n 's/^{"instance_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$BUNDLE/instance_create.json" 2>/dev/null | head -1)"
    if [ -n "$API_INSTANCE_ID" ]; then
        if [ "$API_INSTANCE_ID" != "$INSTANCE_ID" ]; then
            note "instance API minted its own instance_id (${API_INSTANCE_ID} != requested ${INSTANCE_ID}) — using the minted id for all fixture queries"
        fi
        INSTANCE_ID="$API_INSTANCE_ID"
        ok "instance_id resolved from the API response: ${INSTANCE_ID}"
    else
        bad "could not parse instance_id from the API response"
    fi

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

    # 4e — the RESUMABLE instance (defect-2 fix): a REAL in-flight turn.
    # The ghost above proves the passes FIRE; this instance proves they
    # RESUME. The turn is dispatched against the LOCAL stalling mock LLM
    # (P2), so at kill time it is parked inside the graph's LLM call with
    # its first superstep checkpoint already written → a real task row
    # (status='running', task_type='process_message') + a real checkpoint
    # = exactly the shape find_auto_continue_candidates + _has_checkpoint
    # need to report scheduled>=1 after the restart.
    RESUME_CREATE_CODE="$(curl -s --max-time 30 -o "$BUNDLE/resume_instance_create.json" -w '%{http_code}' \
        -X POST "http://127.0.0.1:${DRILL_PORT}/api/instances" \
        -H 'Content-Type: application/json' \
        -d '{"agent_id":"ari"}' 2>/dev/null)"
    [ -z "$RESUME_CREATE_CODE" ] && RESUME_CREATE_CODE=000
    if [ "$RESUME_CREATE_CODE" != "201" ] && [ "$RESUME_CREATE_CODE" != "200" ]; then
        bad "POST /api/instances (resumable) returned ${RESUME_CREATE_CODE}: $(head -c 400 "$BUNDLE/resume_instance_create.json" 2>/dev/null)"
        echo "RESULT: FAIL"; exit 1
    fi
    RESUME_INSTANCE_ID="$(sed -n 's/^{"instance_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$BUNDLE/resume_instance_create.json" 2>/dev/null | head -1)"
    if [ -n "$RESUME_INSTANCE_ID" ]; then
        ok "resumable instance created via API (HTTP ${RESUME_CREATE_CODE}) instance_id=${RESUME_INSTANCE_ID}"
    else
        bad "could not parse the resumable instance_id from the API response"
        echo "RESULT: FAIL"; exit 1
    fi

    # Dispatch the turn (IDLE instance → durable enqueue path, HTTP 200).
    RESUME_MSG_CODE="$(curl -s --max-time 30 -o "$BUNDLE/resume_message_create.json" -w '%{http_code}' \
        -X POST "http://127.0.0.1:${DRILL_PORT}/api/instances/${RESUME_INSTANCE_ID}/messages" \
        -H 'Content-Type: application/json' \
        -d '{"content":"hrr drill resumable turn: think carefully about the fixture content and answer."}' 2>/dev/null)"
    [ -z "$RESUME_MSG_CODE" ] && RESUME_MSG_CODE=000
    RESUME_MESSAGE_ID="$(sed -n 's/.*"message_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$BUNDLE/resume_message_create.json" 2>/dev/null | head -1)"
    if [ "$RESUME_MSG_CODE" = "200" ] || [ "$RESUME_MSG_CODE" = "202" ]; then
        ok "turn dispatched to the resumable instance (HTTP ${RESUME_MSG_CODE}, message_id=${RESUME_MESSAGE_ID:-<none>})"
    else
        bad "POST /api/instances/${RESUME_INSTANCE_ID}/messages returned ${RESUME_MSG_CODE}: $(head -c 400 "$BUNDLE/resume_message_create.json" 2>/dev/null)"
        echo "RESULT: FAIL"; exit 1
    fi

    # Bounded poll: instance running AND a running task row ⇒ in-flight
    # turn with a checkpoint. This is the kill precondition — do NOT
    # proceed to P5 without it (a ghost-only kill would repeat the old
    # candidates=0 weakness).
    if wait_resume_task 30; then
        ok "resumable instance IN FLIGHT: status=running task_id=${RESUME_TASK_ID} work_id=${RESUME_WORK_ID}"
    else
        RESUME_ST="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${RESUME_INSTANCE_ID}';")"
        RESUME_TK="$(psql_scratch "SELECT count(*) FROM task WHERE instance_id='${RESUME_INSTANCE_ID}';")"
        bad "resumable instance never reached (running + running-task) within 30s (instance=${RESUME_ST:-?} task_rows=${RESUME_TK:-?})"
        capture_journal
        echo "RESULT: FAIL"; exit 1
    fi

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
  "resumable_instance_id": "${RESUME_INSTANCE_ID}",
  "resumable_message_id": "${RESUME_MESSAGE_ID}",
  "resumable_task_id": "${RESUME_TASK_ID}",
  "resumable_work_id": "${RESUME_WORK_ID}",
  "note": "fresh-dead row: status='processing', last_activity_at=now()-2min, processing_started_at=same, NO task row (mirrors tests/helpers/hard_reboot_fixtures.py + tests/unit/test_message_queue_boot_recovery.py M1); the SECOND instance is a genuinely resumable in-flight turn (real task row + LangGraph checkpoint) parked on the local stalling mock LLM"
}
EOF
    RESUME_PRE_STATUS="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${RESUME_INSTANCE_ID}';")"
    cat > "$BUNDLE/pre_crash.json" <<EOF
{
  "phase": "P4-pre_crash",
  "utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "daemon_pid": "${DAEMON_PID}",
  "daemon_cgroup": "$(jstr "$(daemon_cgroup "$DAEMON_PID")")",
  "instance_status": "${INST_STATUS}",
  "schedule_row": "$(jstr "$(psql_scratch "SELECT source_id||'|'||status||'|'||enabled||'|'||autostart FROM source_configs WHERE name='${SCHEDULE_ID}';")")",
  "mq_processing_count": "$(printf '%s' "$PRE_COUNTS" | awk '{print $1}')",
  "mq_pending_count": "$(printf '%s' "$PRE_COUNTS" | awk '{print $2}')",
  "busy_guard_predicate": "pending_count > 0 or processing_count > 0",
  "resumable": {
    "instance_id": "${RESUME_INSTANCE_ID}",
    "instance_status": "${RESUME_PRE_STATUS}",
    "task_id": "${RESUME_TASK_ID}",
    "work_id": "${RESUME_WORK_ID}",
    "message_id": "${RESUME_MESSAGE_ID}",
    "shape": "real in-flight turn: instance running + task running (process_message) + LangGraph checkpoint; parked on the local stalling mock LLM"
  },
  "mock_llm": {
    "port": "${MOCK_PORT}",
    "pid": "${MOCK_PID}",
    "stall_seconds": 300
  }
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
    # DEFECT-1 ANCHOR: capture the journal cursor BEFORE the stop. Every
    # post-restart observation is read --after-cursor=<this>, so a stale
    # pre-crash marker can never satisfy a post-restart poll.
    PRE_RESTART_CURSOR="$(journal_cursor)"
    if [ -z "$PRE_RESTART_CURSOR" ]; then
        bad "could not capture the pre-restart journal cursor — post-restart marker anchoring impossible (fail-closed)"
        capture_journal; echo "RESULT: FAIL"; exit 1
    fi
    ok "journal cursor anchored pre-restart: ${PRE_RESTART_CURSOR}"
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
    # completed). Polled from the POST-RESTART journal view (cursor-anchored),
    # not estimated — a match here is proof the new boot ran the pass.
    capture_journal_post
    if wait_marker "\[HardReboot\] MQ boot pass: reset=" 60; then
        T_RECOVERY="$(now_s)"
        ok "MQ boot pass marker observed in the POST-RESTART journal (T_recovery=${T_RECOVERY})"
    else
        T_RECOVERY="$(now_s)"
        bad "MQ boot pass marker never observed in the POST-RESTART journal within 60s of the fresh start"
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
    READY_CODE="$(curl -s --max-time 10 -o "$BUNDLE/readyz_post.json" -w '%{http_code}' "http://127.0.0.1:${DRILL_PORT}/readyz" 2>/dev/null)"
    [ -z "$READY_CODE" ] && READY_CODE=000
    [ "$READY_CODE" = "200" ] && ok "/readyz 200 on the restarted daemon" || bad "/readyz returned ${READY_CODE}"
    capture_journal
    capture_journal_post
    # DEFECT-1 ANCHOR: all post-restart observations read the CURSOR-ANCHORED
    # view (entries after the pre-restart cursor only). JLOG_FULL keeps the
    # full pre+post journal as evidence.
    JLOG="$BUNDLE/journal-postrestart-${UNIT}.log"
    JLOG_FULL="$BUNDLE/journal-${UNIT}.log"

    # Marker strings — VERBATIM from the source (assert on these, not on
    # paraphrases):
    #   "Boot preflight: PostgreSQL reachable"            daemon/__main__.py:246
    #   "DependencyBus started"                           daemon/api.py:3992
    #   "AutoContinue boot pass: %s"                      daemon/api.py:2608
    #   "[HardReboot] MQ boot pass: reset=%d"             message_queue_boot_recovery.py:102
    #   "Boot epoch captured (DB clock): %s"              boot_epoch.py:132
    #   "[BOOT_CONTINUE] orphan-sweep summary: ..."       auto_continue_boot_pass.py:866
    MARKER_LIST=(
        # G6 boot preflight. The SUCCESS record ("Boot preflight: PostgreSQL
        # reachable") is emitted at INFO by daemon/__main__.py BEFORE
        # daemon.api installs the logging handlers; with no handler bound the
        # root logger DROPS INFO records (lastResort level = WARNING), so that
        # marker never reaches stdout/journal nor the daemon's own
        # ensemble.log — it is structurally UNOBSERVABLE on this entry point.
        # The preflight is FAIL-CLOSED (exit 75 EX_TEMPFAIL on definite PG
        # unreachability), so a daemon that BOOTED proves the preflight saw a
        # reachable PG. The observable equivalent is the engine creation
        # (INFO, after handler install), which also proves the daemon bound
        # to the SCRATCH postgres — a stronger claim than the preflight line.
        "Creating PostgreSQL engine: .*${PG_DB}"
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
    note "G6 preflight: the SUCCESS marker 'Boot preflight: PostgreSQL reachable' is INFO-level and emitted BEFORE the logging handlers are installed, so it is dropped (unobservable in journal AND ensemble.log); the fail-closed exit-75 contract plus the observed scratch-PostgreSQL engine creation cover the same property"
    RESET_N="$(grep -E "\[HardReboot\] MQ boot pass: reset=" "$JLOG" 2>/dev/null | tail -1 | grep -oE 'reset=[0-9]+' | cut -d= -f2)"
    [ -n "$RESET_N" ] && ok "MQ boot pass reset count = ${RESET_N} (POST-RESTART boot)" || bad "MQ boot pass reset count not parsed"

    # ── DEFECT-2 OBSERVATION: the auto-continue pass must RESUME, not just fire ──
    # candidates/scheduled parsed from the POST-RESTART boot's ContinueResult
    # line; the success line "[BOOT_CONTINUE] instance=<id8> work_id=<wid8>
    # epoch=<iso>" (auto_continue_boot_pass.py:463) proves a checkpoint-backed
    # resume was actually scheduled for the in-flight turn seeded in P4-4e.
    AC_LINE="$(grep -E "AutoContinue boot pass: ContinueResult\(" "$JLOG" 2>/dev/null | tail -1)"
    AC_CAND="$(printf '%s' "$AC_LINE" | grep -oE 'candidates=[0-9]+' | head -1 | cut -d= -f2)"
    AC_SCHED="$(printf '%s' "$AC_LINE" | grep -oE 'scheduled=[0-9]+' | head -1 | cut -d= -f2)"
    AC_SUCCESS="$(grep -E '\[BOOT_CONTINUE\] instance=[0-9a-f]{8} work_id=[0-9a-f]{8} epoch=' "$JLOG" 2>/dev/null | tail -1)"
    [ -n "$AC_LINE" ] && ok "AutoContinue ContinueResult (POST-RESTART boot): ${AC_LINE}" || bad "AutoContinue ContinueResult line ABSENT from the post-restart journal"
    [ "${AC_CAND:-0}" -ge 1 ] && ok "auto-continue candidates=${AC_CAND} (>=1 — the in-flight turn was selected)" || bad "auto-continue candidates=${AC_CAND:-<none>} (expected >=1)"
    [ "${AC_SCHED:-0}" -ge 1 ] && ok "auto-continue scheduled=${AC_SCHED} (>=1 — a checkpoint-backed resume was scheduled)" || bad "auto-continue scheduled=${AC_SCHED:-<none>} (expected >=1)"
    [ -n "$AC_SUCCESS" ] && ok "[BOOT_CONTINUE] success line (POST-RESTART boot): ${AC_SUCCESS}" || bad "[BOOT_CONTINUE] success line ABSENT from the post-restart journal"

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

    # The daemon mints its OWN source_id (UUID PK) and stores the requested
    # id as the schedule LABEL — which is source_configs.name (see
    # scheduling_service: label=row.name). Querying by source_id found
    # nothing; query by the label instead.
    SCHED_ROW="$(psql_scratch "SELECT source_id||'|'||status||'|'||enabled||'|'||autostart FROM source_configs WHERE name='${SCHEDULE_ID}';")"
    [ -n "$SCHED_ROW" ] && ok "schedule survived the reboot: ${SCHED_ROW}" || bad "schedule row missing after the reboot"
    INST_STATUS2="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${INSTANCE_ID}';")"
    note "ghost instance status after reboot: ${INST_STATUS2} (D13 task-less RUNNING ghost — OUT OF SCOPE, reported, never failed)"
    GHOST="$(psql_scratch "SELECT count(*) FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.instance_id AND t.status IN ('pending','running','paused'));")"
    note "D13 task-less RUNNING ghost count (out of scope, informational): ${GHOST}"
    # the RESUMABLE instance: the auto-continue resume is continue-in-place
    # (the orphan task stays status='running'), so the expected post shape is
    # instance running + task running again on the resumed turn.
    RESUME_STATUS2="$(psql_scratch "SELECT status FROM instances WHERE instance_id='${RESUME_INSTANCE_ID}';")"
    RESUME_TASK_STATUS2="$(psql_scratch "SELECT status FROM task WHERE instance_id='${RESUME_INSTANCE_ID}' ORDER BY created_at ASC LIMIT 1;")"
    RESUME_AC_STAMP="$(psql_scratch "SELECT auto_continued_at::text FROM task WHERE id='${RESUME_TASK_ID}';")"
    note "resumable instance after reboot: instance=${RESUME_STATUS2:-?} task=${RESUME_TASK_STATUS2:-?} auto_continued_at=${RESUME_AC_STAMP:-<null>}"

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
  "d13_taskless_running_ghosts": "${GHOST}",
  "ghost_instance": {
    "instance_id": "${INSTANCE_ID}",
    "status": "${INST_STATUS2}",
    "labelling": "D13 task-less RUNNING ghost — OUT OF SCOPE by design, reported never failed"
  },
  "resumable_instance": {
    "instance_id": "${RESUME_INSTANCE_ID}",
    "status": "${RESUME_STATUS2}",
    "task_id": "${RESUME_TASK_ID}",
    "task_status": "${RESUME_TASK_STATUS2}",
    "auto_continued_at": "${RESUME_AC_STAMP}",
    "labelling": "defect-2 fix: genuinely resumable in-flight turn (real task row + LangGraph checkpoint parked on the local stalling mock LLM)"
  },
  "auto_continue": {
    "continue_result_line": "$(jstr "$AC_LINE")",
    "candidates": "${AC_CAND}",
    "scheduled": "${AC_SCHED}",
    "success_line": "$(jstr "$AC_SUCCESS")",
    "journal_view": "cursor-anchored POST-RESTART entries only (--after-cursor=${PRE_RESTART_CURSOR})"
  },
  "marker_anchor": {
    "mechanism": "journal cursor captured before the unit stop; every post-restart marker/parse reads journalctl --after-cursor",
    "pre_restart_cursor": "${PRE_RESTART_CURSOR}"
  },
  "schedule_row": "$(jstr "$SCHED_ROW")",
  "mock_llm": {
    "port": "${MOCK_PORT}",
    "pid": "${MOCK_PID}",
    "note": "LOCAL stalling mock only — no live proxy is contacted, no real credential used; killed by exact PID in the cleanup trap"
  }
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
MOCK_PORT_AFTER="free"; MOCK_PID_AFTER="not-started"
teardown
DB_AFTER="$(psql_super "SELECT count(*) FROM pg_database WHERE datname='${PG_DB}';" 2>/dev/null | tr -d '[:space:]')"
ROLE_AFTER="$(psql_super "SELECT count(*) FROM pg_roles WHERE rolname='${PG_ROLE}';" 2>/dev/null | tr -d '[:space:]')"
if [ -n "$DRILL_PORT" ] && (exec 3<>/dev/tcp/127.0.0.1/$DRILL_PORT) 2>/dev/null; then PORT_AFTER="bound"; else PORT_AFTER="free"; fi
if [ -n "$MOCK_PORT" ] && (exec 3<>/dev/tcp/127.0.0.1/$MOCK_PORT) 2>/dev/null; then MOCK_PORT_AFTER="bound"; fi
if [ -n "$MOCK_PID" ]; then
    if kill -0 "$MOCK_PID" 2>/dev/null; then MOCK_PID_AFTER="alive"; else MOCK_PID_AFTER="gone"; fi
fi
UNITS_AFTER="$(systemctl list-units --all --no-legend 'hrr-*' 2>/dev/null | grep -c . || true)"
LIVE_MAIN_PID2="$(systemctl show ensemble-main.service -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
LIVE_MAIN_RESTARTS2="$(systemctl show ensemble-main.service -p NRestarts --value 2>/dev/null | tr -d '[:space:]')"
LIVE_MAIN_TS2="$(systemctl show ensemble-main.service -p ActiveEnterTimestamp --value 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
LIVE_DEMO_PID2="$(systemctl show ensemble-demo.service -p MainPID --value 2>/dev/null | tr -d '[:space:]')"
LIVE_LINK_MAIN2="$(readlink -f /home/nea/agents-ensemble/current 2>/dev/null)"

[ "$DB_AFTER" = "0" ] && ok "scratch database dropped (verified gone)" || bad "scratch database still present (count=${DB_AFTER})"
[ "$ROLE_AFTER" = "0" ] && ok "scratch role dropped (verified gone)" || bad "scratch role still present (count=${ROLE_AFTER})"
[ "$PORT_AFTER" = "free" ] && ok "drill port ${DRILL_PORT} free" || bad "drill port ${DRILL_PORT} still bound"
if [ -n "$MOCK_PORT" ]; then
    [ "$MOCK_PORT_AFTER" = "free" ] && ok "mock LLM port ${MOCK_PORT} free (mock server gone)" || bad "mock LLM port ${MOCK_PORT} still bound"
    [ "$MOCK_PID_AFTER" = "gone" ] && ok "mock LLM server process gone (pid ${MOCK_PID})" || bad "mock LLM server process still alive (pid ${MOCK_PID}, state=${MOCK_PID_AFTER})"
else
    note "mock LLM not started (phase subset without P2) — mock residue check SKIPPED"
fi
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
  "mock_llm_port": "${MOCK_PORT}",
  "mock_llm_port_after": "${MOCK_PORT_AFTER}",
  "mock_llm_pid": "${MOCK_PID}",
  "mock_llm_pid_after": "${MOCK_PID_AFTER}",
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
