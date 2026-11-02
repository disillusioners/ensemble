#!/usr/bin/env bash
# GATE E — per-leg teardown: orphan check, scratch DB/role drop, scratch removal.
# SIGTERM-only (the daemon was already SIGTERMed by the caller).
set -uo pipefail
RUN=/tmp/plane-pm-smoke-2026-11-02
LEG="$1"
# shellcheck disable=SC1090
. "$RUN/leg-${LEG}.env"
OUT="$RUN/leg-${LEG}.teardown.txt"
: > "$OUT"
say() { echo "$*" | tee -a "$OUT"; }

say "=== TEARDOWN leg=$LEG db=$DB role=$ROLE port=$PORT ==="

# ── orphan census: no `python -m daemon` and no MCP stdio children of ours ─
DAEMONS=$(pgrep -af "python -m daemon" 2>/dev/null | grep -v "grep" || true)
say "python -m daemon processes alive now:"
if [ -n "$DAEMONS" ]; then say "$DAEMONS" | sed 's/^/    /'; else say "    (none)"; fi
NODEKIDS=$(pgrep -af "context7-mcp|@upstash" 2>/dev/null || true)
say "MCP stdio children (context7/npm) alive now:"
if [ -n "$NODEKIDS" ]; then say "$NODEKIDS" | sed 's/^/    /'; else say "    (none)"; fi

# ── the scratch port must be free ─────────────────────────────────────────
if ss -ltn 2>/dev/null | grep -qE ":${PORT}\b"; then say "PORT $PORT STILL BOUND"; else say "port $PORT FREE"; fi

# ── drop the scratch DB + role (LOCAL cluster only) ───────────────────────
psql -U nea -d postgres -tAc "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$DB'" >/dev/null 2>&1
psql -U nea -d postgres -tAc "DROP DATABASE IF EXISTS \"$DB\"" 2>&1 | sed 's/^/    dropdb: /'
psql -U nea -d postgres -tAc "DROP ROLE IF EXISTS \"$ROLE\"" 2>&1 | sed 's/^/    droprole: /'
LEFT=$(psql -U nea -d postgres -tAc "select count(*) from pg_database where datname='$DB'")
LEFTR=$(psql -U nea -d postgres -tAc "select count(*) from pg_roles where rolname='$ROLE'")
say "scratch DB remaining: $LEFT (0 = dropped); scratch role remaining: $LEFTR (0 = dropped)"

# ── remove the scratch filesystem (ONLY the mktemp scratch paths) ─────────
rm -rf "$SCRATCH" "$FAKE_HOME"
say "scratch removed: $SCRATCH ; fake home removed: $FAKE_HOME"
say "=== END TEARDOWN $LEG ==="
