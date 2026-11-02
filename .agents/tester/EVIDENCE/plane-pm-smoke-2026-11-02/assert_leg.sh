#!/usr/bin/env bash
# GATE E — per-leg assertions (run under bash; the tool shell may be /bin/sh).
set -uo pipefail
RUN=/tmp/plane-pm-smoke-2026-11-02
LEG="$1"
# shellcheck disable=SC1090
. "$RUN/leg-${LEG}.env"
OUT="$RUN/leg-${LEG}.assert.txt"
: > "$OUT"
say() { echo "$*" | tee -a "$OUT"; }

say "=== LEG $LEG  self_env=$SELF_ENV slug_mode=$SLUG_MODE port=$PORT db=$DB ==="

# ── F8: the port under test must be the SCRATCH port and must be bound ─────
BOUND=$(ss -ltnp 2>/dev/null | grep -E ":${PORT}\b" || true)
say "port $PORT bound: $(echo "$BOUND" | tr -s ' ' | cut -c1-160)"

# ── /livez + /readyz on the SCRATCH port ───────────────────────────────────
curl -s -m 10 -o "$RUN/leg-${LEG}.livez.json" -w "livez http=%{http_code}\n" "http://127.0.0.1:${PORT}/livez" | tee -a "$OUT"
curl -s -m 10 -o "$RUN/leg-${LEG}.readyz.json" -w "readyz http=%{http_code}\n" "http://127.0.0.1:${PORT}/readyz" | tee -a "$OUT"
say "livez:  $(cat "$RUN/leg-${LEG}.livez.json")"
python3 - "$RUN/leg-${LEG}.readyz.json" <<'PY' | tee -a "$OUT"
import json,sys
d=json.load(open(sys.argv[1]))
print("readyz: status=%s components=%s reasons=%s" % (d.get("status"), d.get("components"), (d.get("detail") or {}).get("reasons")))
PY

# ── the mcp_servers REST view (bearer REDACTED at the writer) ──────────────
curl -s -m 15 -o "$RUN/leg-${LEG}.mcp-servers.raw.json" -w "mcp-servers http=%{http_code}\n" "http://127.0.0.1:${PORT}/api/mcp-servers" | tee -a "$OUT"

# ── the DB row: the AUTHORISATION is redacted IN SQL ──────────────────────
PGPASSWORD="$ROLE_PW" psql -h 127.0.0.1 -p 5432 -U "$ROLE" -d "$DB" -tA -F'|' -c \
  "select name, is_builtin, is_active, config_schema_version,
          coalesce(config->'headers'->>'x-workspace-slug','<absent>'),
          left(coalesce(config->'headers'->>'Authorization',''),7) || '<redacted,len=' || length(coalesce(config->'headers'->>'Authorization','')) || '>',
          coalesce(config->>'transport','<absent>'),
          coalesce(config->>'url','<absent>')
     from mcp_servers where name='plane'" > "$RUN/leg-${LEG}.row.txt" 2>&1
say "DB plane row: $(cat "$RUN/leg-${LEG}.row.txt")"
say "DB plane row count: $(PGPASSWORD="$ROLE_PW" psql -h 127.0.0.1 -p 5432 -U "$ROLE" -d "$DB" -tAc "select count(*) from mcp_servers where name='plane'")"

# ── the scratch ensemble.json proves the FILE-PIN vector was not used ──────
say "scratch ensemble.json: $(cat "$SCRATCH/data/ensemble.json" 2>/dev/null | head -c 300)"

# ── the boot-log plane lines (verbatim) ───────────────────────────────────
say "--- boot log: plane / builtin / workspace lines ---"
grep -n -i "plane workspace resolution\|Created MCP server\|Created built-in MCP server\|Builtin 'plane'\|skipped — \|deactivated — \|workspace refused\|missing environment configuration" \
    "$RUN/leg-${LEG}.boot.log" | sed 's/^/    /' | tee -a "$OUT"

# ── THE VACUITY TRIPWIRE ───────────────────────────────────────────────────
say "--- vacuity tripwire ---"
say "  env-name census (names only): $(grep -c . "$RUN/leg-${LEG}.envnames.txt") names"
for n in PLANE_MCP_URL PLANE_MCP_API_KEY; do
    if grep -qx "$n" "$RUN/leg-${LEG}.envnames.txt"; then say "  PRESENT: $n"; else say "  MISSING: $n"; fi
done
if grep -qx "PLANE_MCP_WORKSPACE_SLUG" "$RUN/leg-${LEG}.envnames.txt"; then
    say "  PRESENT: PLANE_MCP_WORKSPACE_SLUG"
else
    say "  ABSENT: PLANE_MCP_WORKSPACE_SLUG (by design for the unset leg)"
fi
if grep -q "missing environment configuration" "$RUN/leg-${LEG}.boot.log"; then
    say "  TRIPWIRE ARMED: a missing-credential skip reason IS present in the log → the leg is VACUOUS"
else
    say "  TRIPWIRE CLEAR: no missing-credential skip reason in the log"
fi
say "=== END LEG $LEG ==="
