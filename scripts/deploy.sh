#!/bin/bash
# ============================================================================
# scripts/deploy.sh — build + stage + restart + health-gate an install
# (Auto-Restart Phase 1; plan §5/§7 — the deploy half of the stage/promote
# direction, Phase-1-safe: no releases/ layout, no flip, no rollback)
# ============================================================================
# TARGETS (3-env topology, dev/demo/live):
#   demo  ~/agents-ensemble-demo   port 7979  DB ensemble_demo
#         (rehearsal target for auto-restart/upgrade — REAL prod shape)
#   live  ~/agents-ensemble        port 9797  DB ensemble_prod
#         (READ-ONLY until the auto-restart feature is done — ENSEMBLE_
#         DEPLOY_LIVE=1 required as explicit operator confirmation)
#   dev (8079, repo) is NEVER a deploy target.
#
# PHASES:
#   0 preflight   target dir + guards (live-confirm / port report / the
#                 live-9797 daemon must be exactly as we found it)
#   1 build       PyInstaller binary — ALWAYS bare `uv run python -m
#                 PyInstaller ensemble.spec`. NEVER `make pyinstaller` /
#                 `make build` / `make install` from this script: the
#                 ensure-latest chain (git checkout latest && git pull)
#                 would yank the feature branch out from under us.
#   2 stage       binary + agents/ + frontend/dist + config.yaml +
#                 launcher.sh + env file → INSTALL_DIR/.env
#   3 stop        ownership-scoped ONLY (scripts/stop-ensemble.sh) — reused,
#                 never duplicated. A FOREIGN daemon holding the port is
#                 reported, not killed.
#   4 start       INSTALL_DIR/launcher.sh, nohup, logs → data/launcher.log
#                 (the same path the plist template expects).
#   5 health gate /livez then /readyz on the target port (≤60s / ≤120s),
#                 JSON printed, LOUD nonzero failure — this is the seed of
#                 the Phase-3 promote gate.
#
# ENV SOURCE (demo): .env.prod.demo in the repo root — GITIGNORED (real
# keys copied from .env.prod). This script generates it ON DEMAND from
# .env.prod with PORT=7979 + POSTGRES_DB=ensemble_demo overridden, and
# never commits it. ENV source for live is the plain .env.prod.
# Config table DB names are REPORT-ONLY for live (this script never
# creates/drops the live DB); the demo DB may be created if missing
# (guarded: only with --create-db, never implicitly).
#
# USAGE:
#   scripts/deploy.sh [demo|live] [--dry-run] [--build] [--skip-build]
#                     [--no-start] [--create-db]
#   TARGET=demo scripts/deploy.sh          (env override, same as arg)
#   make deploy-demo / make deploy-live    (thin wrappers)
#
# LIVE SAFETY: deploying to live additionally requires
#   ENSEMBLE_DEPLOY_LIVE=1   (explicit operator confirmation)
#
# EXIT CODES:
#   0   ok
#   1   generic failure (bad stage copy, bad flag, health gate RED)
#   75  health gate could not reach the daemon after start (transient-
#       flavored — retry is reasonable)
#   78  config refuse: missing env source / missing binary / unknown
#       target / unconfirmed live deploy
#
# Idempotent: re-running redeploys cleanly. Bash 3.2 / BSD tools only.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STOP_SCRIPT="$REPO_ROOT/scripts/stop-ensemble.sh"

# ── Flags ───────────────────────────────────────────────────────────────────
DRY_RUN=0
FORCE_BUILD=0
SKIP_BUILD=0
NO_START=0
CREATE_DB=0
TARGET_ARG=""

for arg in "$@"; do
    case "$arg" in
        demo|live) TARGET_ARG="$arg" ;;
        --dry-run)    DRY_RUN=1 ;;
        --build)      FORCE_BUILD=1 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --no-start)   NO_START=1 ;;
        --create-db)  CREATE_DB=1 ;;
        -h|--help)    sed -n '2,64p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "deploy: unknown flag or target '$arg' — usage: deploy.sh [demo|live] [--dry-run] [--build] [--skip-build] [--no-start] [--create-db]" >&2; exit 78 ;;
    esac
done

# ── Target config table ─────────────────────────────────────────────────────
TARGET="${TARGET_ARG:-${TARGET:-demo}}"
case "$TARGET" in
    demo)
        INSTALL_DIR="$HOME/agents-ensemble-demo"
        PORT=7979
        ENV_SOURCE="$REPO_ROOT/.env.prod.demo"
        ENV_BASE="$REPO_ROOT/.env.prod"     # generator input (demo only)
        DB_NAME=ensemble_demo
        ;;
    live)
        INSTALL_DIR="$HOME/agents-ensemble"
        PORT=9797
        ENV_SOURCE="$REPO_ROOT/.env.prod"
        ENV_BASE=""
        DB_NAME=ensemble_prod
        ;;
    *)
        echo "deploy: unknown target '$TARGET' (demo|live)" >&2
        exit 78
        ;;
esac

# Health-gate budgets (seconds).
LIVEZ_BUDGET_S=60
READYZ_BUDGET_S=120

# The live daemon we must leave untouched (observed at preflight; PID set
# means "we recorded it" — the check is pids unchanged, not pid-alive, so
# an operator cycling prod mid-deploy cannot crash OUR proof).
LIVE_BASELINE_PIDS=""

_log() { printf 'deploy[%s]: %s\n' "$TARGET" "$*"; }
_logv() { printf '%s\n' "$*"; }                       # verbatim (JSON etc.)
_dry()  { _log "DRY-RUN: $*"; }

run() {
    # run <what-happens> -- <cmd...>  (dry-run prints instead of executing)
    local what="$1"; shift
    [ "$1" = "--" ] && shift
    if [ "$DRY_RUN" = "1" ]; then
        _dry "$what"
    else
        "$@" || { _log "FAILED: $what"; exit 1; }
    fi
}

# ── Phase 0: preflight ──────────────────────────────────────────────────────
_log "phase 0/5 preflight — target=$TARGET dir=$INSTALL_DIR port=$PORT db=$DB_NAME"

# 0a. live-guard: deploying to live requires an explicit operator ack.
if [ "$TARGET" = "live" ] && [ "${ENSEMBLE_DEPLOY_LIVE:-0}" != "1" ]; then
    cat >&2 <<EOF
deploy: REFUSING to deploy to live.
  Auto-restart work targets DEMO (\$HOME/agents-ensemble-demo, :7979,
  ensemble_demo) until the feature is done. Live is the running
  orchestrator itself — read-only by ruling.
  To deploy live anyway:   ENSEMBLE_DEPLOY_LIVE=1 scripts/deploy.sh live
EOF
    exit 78
fi

# 0b. target dir: demo is created on demand; live must already exist (we
# never bootstrap the live install from a deploy).
if [ ! -d "$INSTALL_DIR" ]; then
    if [ "$TARGET" = "demo" ]; then
        run "mkdir -p $INSTALL_DIR" -- mkdir -p "$INSTALL_DIR"
    else
        _log "live install dir $INSTALL_DIR does not exist — deploy does not bootstrap live; run 'make install' once first"
        exit 78
    fi
fi

# 0c. port report — REPORT ONLY, never a kill selector (ownership logic in
# the stop script owns process selection; a foreign listener on the port
# is surfaced, and anything of OURS is about to be stopped + replaced).
PORT_PIDS="$(lsof -ti:"$PORT" 2>/dev/null | tr '\n' ' ')"
if [ -n "$PORT_PIDS" ]; then
    _log "port $PORT currently held by: $PORT_PIDS (report only; owned processes are stopped in phase 3)"
else
    _log "port $PORT is free"
fi

# 0d. live-9797-unrelated assertion: record the live daemon pids BEFORE we
# do anything, so the post-deploy check can prove we did not touch them.
# (Only meaningful when demo is the target — for live the stop in phase 3
# is the sanctioned action.)
if [ "$TARGET" = "demo" ]; then
    LIVE_BASELINE_PIDS="$(lsof -ti:9797 2>/dev/null | tr '\n' ' ')"
    _log "live 9797 baseline pids: ${LIVE_BASELINE_PIDS:-<none>} (asserted unchanged after every phase)"
fi

# ── Phase 1: build ──────────────────────────────────────────────────────────
_log "phase 1/5 build"
BINARY="$REPO_ROOT/dist/ensemble-prod"
NEED_BUILD=0
if [ "$FORCE_BUILD" = "1" ]; then
    NEED_BUILD=1
    _log "build forced (--build)"
elif [ "$SKIP_BUILD" = "1" ]; then
    _log "build skipped (--skip-build)"
elif [ ! -x "$BINARY" ]; then
    NEED_BUILD=1
    _log "no binary at $BINARY — building"
fi

if [ "$NEED_BUILD" = "1" ]; then
    # NEVER `make pyinstaller` / `make build` / `make install` here: the
    # ensure-latest chain (git checkout latest && git pull) would yank
    # the feature branch. Bare invocation of the same underlying command.
    run "PyInstaller build (bare, branch-safe)" -- sh -c \
        "cd '$REPO_ROOT' && rm -rf build/ && uv run python -m PyInstaller ensemble.spec"
    [ -x "$BINARY" ] || { _log "build produced no $BINARY"; exit 78; }
fi

# ── Phase 2: stage ──────────────────────────────────────────────────────────
_log "phase 2/5 stage → $INSTALL_DIR"

# 2a. demo env-source generation (on demand, gitignored, real keys — the
# ONLY sed-permitting step: PORT + POSTGRES_DB overridden from .env.prod).
# Generates to a temp path and MOVES, so the redirection itself can never
# leak into the target file on a dry-run (a caller-side `>` executes even
# when the command is suppressed).
if [ "$TARGET" = "demo" ] && [ ! -f "$ENV_SOURCE" ]; then
    if [ ! -f "$ENV_BASE" ]; then
        _log "cannot generate $ENV_SOURCE: $ENV_BASE missing (create it from .env.prod.example)"
        exit 78
    fi
    _log "generating $ENV_SOURCE from $ENV_BASE (PORT=$PORT, POSTGRES_DB=$DB_NAME) — gitignored, never commit"
    if [ "$DRY_RUN" = "1" ]; then
        _dry "sed-generate $ENV_SOURCE (PORT=$PORT, POSTGRES_DB=$DB_NAME)"
    else
        ENV_TMP="$ENV_SOURCE.tmp.$$"
        if ! sed -e "s/^PORT=.*/PORT=$PORT/" \
                 -e "s/^POSTGRES_DB=.*/POSTGRES_DB=$DB_NAME/" \
                 "$ENV_BASE" > "$ENV_TMP" \
                 || ! mv -f "$ENV_TMP" "$ENV_SOURCE"; then
            rm -f "$ENV_TMP" 2>/dev/null
            _log "FAILED: generating $ENV_SOURCE"
            exit 1
        fi
    fi
fi

# 2b. env source must exist — no silent fallback (same rule as make install).
# Dry-run intends "plan without side effects" — so in dry-run a missing demo
# env source reports the GENERATION plan rather than failing (the generator
# above suppressed its file writes; the copy below is also dry).
if [ ! -f "$ENV_SOURCE" ]; then
    if [ "$DRY_RUN" = "1" ] && [ "$TARGET" = "demo" ]; then
        _dry "env source $ENV_SOURCE will be generated (see above), then staged"
    else
        _log "env source $ENV_SOURCE not found — refusing (no silent fallback; see .env.prod.example)"
        exit 78
    fi
else
    _log "env source: $ENV_SOURCE"
fi

# 2c. demo DB creation — guarded, opt-in, never for live.
if [ "$CREATE_DB" = "1" ]; then
    if [ "$TARGET" = "live" ]; then
        _log "--create-db is demo-only; live DB (ensemble_prod) is report-only by design"
    else
        if psql -h "${POSTGRES_HOST:-localhost}" -p "${POSTGRES_PORT:-5432}" \
             -U "$(sed -n 's/^POSTGRES_USER=//p' "$ENV_SOURCE" | head -1)" \
             -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'" 2>/dev/null \
             | grep -q 1; then
            _log "demo DB $DB_NAME already exists — skipping create"
        else
            PGPASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_SOURCE" | head -1)" \
            run "CREATE DATABASE $DB_NAME" -- psql -h "${POSTGRES_HOST:-localhost}" \
                -p "${POSTGRES_PORT:-5432}" \
                -U "$(sed -n 's/^POSTGRES_USER=//p' "$ENV_SOURCE" | head -1)" \
                -d postgres -c "CREATE DATABASE $DB_NAME;"
            _log "created demo DB $DB_NAME"
        fi
    fi
fi

# 2d. stage the payload (binary + agents/ + frontend/dist + config.yaml +
# launcher.sh), then the env file. Plain copies — no port munging (the
# port lives in .env; ADR-014). Agents are copied WITHOUT .agents-style
# leakage: agents/ in the repo contains no .agents content, and the
# explicit-path staging below cannot pick anything else up.
run "mkdir data dir"            -- mkdir -p "$INSTALL_DIR/data"
run "stage binary"              -- cp "$BINARY" "$INSTALL_DIR/ensemble-prod"
run "chmod binary"              -- chmod +x "$INSTALL_DIR/ensemble-prod"
run "stage agents/ (clean)"     -- sh -c "rm -rf '$INSTALL_DIR/agents' && cp -R '$REPO_ROOT/agents' '$INSTALL_DIR/agents'"
run "stage config.yaml"         -- cp "$REPO_ROOT/config.yaml" "$INSTALL_DIR/config.yaml"
run "stage launcher.sh"         -- cp "$REPO_ROOT/launcher.sh" "$INSTALL_DIR/launcher.sh"
run "chmod launcher.sh"         -- chmod +x "$INSTALL_DIR/launcher.sh"
if [ -d "$REPO_ROOT/frontend/dist/frontend/browser" ]; then
    run "stage frontend/dist (clean)" -- sh -c \
        "rm -rf '$INSTALL_DIR/frontend/dist' && mkdir -p '$INSTALL_DIR/frontend/dist/frontend/browser' && cp -R '$REPO_ROOT/frontend/dist/frontend/browser/.' '$INSTALL_DIR/frontend/dist/frontend/browser/'"
else
    _log "WARN: no frontend build at frontend/dist/frontend/browser — UI not staged (run 'cd frontend && npm run build')"
fi
run "stage env → $INSTALL_DIR/.env" -- cp "$ENV_SOURCE" "$INSTALL_DIR/.env"

# ── Phase 3: stop (ownership-scoped, REUSED — never duplicated) ─────────────
_log "phase 3/5 stop (ownership-scoped: only processes owned by $INSTALL_DIR)"
run "stop owned processes" -- bash "$STOP_SCRIPT" "$INSTALL_DIR" "$PORT"

# ── Phase 4: start ──────────────────────────────────────────────────────────
_log "phase 4/5 start"
if [ "$NO_START" = "1" ]; then
    _log "--no-start: skipping start + health gate"
else
    if [ "$DRY_RUN" = "1" ]; then
        _dry "nohup $INSTALL_DIR/launcher.sh >> $INSTALL_DIR/data/launcher.log 2>&1 &"
    else
        # nohup + & : the launcher is its own supervisor from here on.
        # data/launcher.log is the same path the plist template uses —
        # switching to launchd later changes nothing about where logs go.
        mkdir -p "$INSTALL_DIR/data"
        ( cd "$INSTALL_DIR" && nohup ./launcher.sh >> data/launcher.log 2>&1 & )
        _log "launcher started (nohup) — logs: $INSTALL_DIR/data/launcher.log"
    fi
fi

# ── Phase 5: health gate (seed of the Phase-3 promote gate) ─────────────────
if [ "$NO_START" = "0" ]; then
    _log "phase 5/5 health gate (livez ≤${LIVEZ_BUDGET_S}s / readyz ≤${READYZ_BUDGET_S}s) on :$PORT"
    if [ "$DRY_RUN" = "1" ]; then
        _dry "poll http://localhost:$PORT/livez (≤${LIVEZ_BUDGET_S}s), then http://localhost:$PORT/readyz (≤${READYZ_BUDGET_S}s) — print JSON, fail nonzero"
    else
        _probe() {
            # _probe <path> <budget_s> → body on stdout; nonzero when the
            # budget expires without an HTTP response (curl itself failed
            # on EVERY attempt — unreachable, not merely slow).
            local path="$1" budget="$2" waited=0 body=""
            while [ "$waited" -le "$budget" ]; do
                body="$(curl -fsS --max-time 5 "http://localhost:$PORT$path" 2>/dev/null)" \
                    && { printf '%s\n' "$body"; return 0; }
                sleep 2
                waited=$((waited + 2))
            done
            return 1
        }
        if LIVEZ_JSON="$(_probe "/livez" "$LIVEZ_BUDGET_S")"; then
            _log "livez OK:"; _logv "$LIVEZ_JSON"
        else
            _log "HEALTH GATE FAILED: /livez unreachable on :$PORT after ${LIVEZ_BUDGET_S}s (daemon not answering — transient-flavored exit 75; see $INSTALL_DIR/data/launcher.log)"
            exit 75
        fi
        if READYZ_JSON="$(_probe "/readyz" "$READYZ_BUDGET_S")"; then
            _log "readyz OK:"; _logv "$READYZ_JSON"
        else
            _log "HEALTH GATE FAILED: /readyz unreachable on :$PORT after ${READYZ_BUDGET_S}s (degraded daemon — transient-flavored exit 75; see $INSTALL_DIR/data/launcher.log)"
            exit 75
        fi
        _log "health gate GREEN — $TARGET deploy complete"
    fi
fi

# ── Post-deploy: live-9797 survival assertion ───────────────────────────────
if [ "$TARGET" = "demo" ] && [ -n "$LIVE_BASELINE_PIDS" ]; then
    if [ "$DRY_RUN" = "1" ]; then
        _dry "assert live 9797 pids unchanged: '$LIVE_BASELINE_PIDS'"
    else
        LIVE_NOW="$(lsof -ti:9797 2>/dev/null | tr '\n' ' ')"
        if [ "$LIVE_NOW" != "$LIVE_BASELINE_PIDS" ]; then
            _log "ASSERT FAILED: live 9797 pids changed: before='$LIVE_BASELINE_PIDS' after='$LIVE_NOW'"
            exit 1
        fi
        _log "live 9797 survival: pids unchanged ($LIVE_BASELINE_PIDS)"
    fi
fi

_log "done — $TARGET deployed to $INSTALL_DIR (port $PORT, db $DB_NAME)"
exit 0
