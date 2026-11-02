#!/usr/bin/env bash
# GATE E — phase-6 staged-smoke REHEARSAL — per-leg fenced boot (SOURCE-RUN form).
#
# Boot form:  cd <worktree> && /home/nea/ensemble-src/.venv/bin/python -m daemon
# Env form:   `env -i` with a FULL explicit allowlist — nothing ambient is
#             inherited (the worker shell is live-poisoned: POSTGRES_DB=ensemble_prod,
#             ENSEMBLE_SELF_ENV=live). The armed worktree .env is NEVER sourced.
# Credential: PLANE_MCP_API_KEY is the per-leg DUMMY (presence-only semantics);
#             PLANE_MCP_URL is the PUBLIC url. No real bearer anywhere.
set -euo pipefail

RUN=/tmp/plane-pm-smoke-2026-11-02
WT=/home/nea/ensemble-src-wt-plane-pm
VENV_PY=/home/nea/ensemble-src/.venv/bin/python
LEG="$1"

# shellcheck disable=SC1090
source "$RUN/leg-${LEG}.env"

# ── port preflight: never a live/live-demo/dev/8088 port, never bound ─────
for p in 9797 7979 8079 8088; do
    [ "$PORT" != "$p" ] || { echo "FATAL: forbidden port $PORT" >&2; exit 94; }
done
if ss -ltn 2>/dev/null | grep -qE ":${PORT}\b"; then
    echo "FATAL: port $PORT already bound" >&2; exit 94
fi

# ── the env -i allowlist (names only are printed; no value is ever echoed) ─
ENVARGS=(
    "PATH=/usr/local/bin:/usr/bin:/bin"
    "HOME=$FAKE_HOME"
    "PYTHONPATH=$WT"
    "PYTHONUNBUFFERED=1"
    "PYTHONDONTWRITEBYTECODE=1"
    "TMPDIR=$SCRATCH/tmp"
    "LANG=C.UTF-8"
    "LOG_LEVEL=info"
    "RAG_IS_REQUIRED=false"
    # ── the environment identity + the ABSOLUTE boot-sweep guard ──
    "ENSEMBLE_SELF_ENV=$SELF_ENV"
    "ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0"
    # ── scratch filesystem fences ──
    "ENSEMBLE_DATA_DIR=$SCRATCH/data"
    "ENSEMBLE_CONFIG=$WT/config.yaml"
    # ── scratch PG (LOCAL 127.0.0.1:5432 only) ──
    "POSTGRES_HOST=127.0.0.1"
    "POSTGRES_PORT=5432"
    "POSTGRES_DB=$DB"
    "POSTGRES_USER=$ROLE"
    "POSTGRES_PASSWORD=$ROLE_PW"
    # ── explicit scratch port (never sourced from a .env) ──
    "HOST=127.0.0.1"
    "PORT=$PORT"
    "DAEMON_PORT=$PORT"
    # ── the PLANE lane: real PUBLIC url + DUMMY key; the slug varies per leg ──
    "PLANE_MCP_URL=$PLANE_URL"
    "PLANE_MCP_API_KEY=$DUMMY_KEY"
    # ── keep the smoke free of npm-fetch stdio children (context7 is a stdio
    #    builtin; the plane claims are independent of it) ──
    "MCP_DISABLE_BUILT_IN_CONTEXT7=true"
)
if [ "$SLUG_MODE" != "unset" ]; then
    ENVARGS+=("PLANE_MCP_WORKSPACE_SLUG=$SLUG_VALUE")
fi

# ── VACUITY TRIPWIRE — the key-NAME census of the constructed env ────────
# (names only; no value is printed)
printf '%s\n' "${ENVARGS[@]}" | cut -d= -f1 | sort > "$RUN/leg-${LEG}.envnames.txt"
for req in PLANE_MCP_URL PLANE_MCP_API_KEY; do
    grep -qx "$req" "$RUN/leg-${LEG}.envnames.txt" || {
        echo "FATAL: tripwire — $req MISSING from the constructed env" >&2; exit 95; }
done
if [ "$SLUG_MODE" = "unset" ]; then
    if grep -qx "PLANE_MCP_WORKSPACE_SLUG" "$RUN/leg-${LEG}.envnames.txt"; then
        echo "FATAL: tripwire — PLANE_MCP_WORKSPACE_SLUG must be ABSENT in L3" >&2; exit 95
    fi
else
    grep -qx "PLANE_MCP_WORKSPACE_SLUG" "$RUN/leg-${LEG}.envnames.txt" || {
        echo "FATAL: tripwire — PLANE_MCP_WORKSPACE_SLUG missing in $LEG" >&2; exit 95; }
fi
# hard proof that no REAL bearer can be present: the key value is the literal
# dummy (the file carries no other PLANE key material)
grep -qx "PLANE_MCP_API_KEY=plane_api_staged_smoke_dummy_${LEG}" <(printf '%s\n' "${ENVARGS[@]}") || {
    echo "FATAL: the PLANE key is not the per-leg dummy" >&2; exit 95; }

echo "BOOT leg=$LEG self_env=$SELF_ENV slug=$SLUG_MODE port=$PORT (env-name census: $(wc -l < "$RUN/leg-${LEG}.envnames.txt") names)"

cd "$WT"
exec env -i "${ENVARGS[@]}" "$VENV_PY" -m daemon > "$RUN/leg-${LEG}.boot.log" 2>&1
