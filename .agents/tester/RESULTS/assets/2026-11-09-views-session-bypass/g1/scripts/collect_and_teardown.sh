#!/usr/bin/env bash
# Collect all G1 artifacts into the evidence dir, then tear down (port-verified).
set -uo pipefail
WT=/home/nea/ensemble-src-wt-views-session-bypass
EV="$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/g1"
FENCE=$(cat /tmp/g1fence.ready)
case "$FENCE" in /tmp/g1lv.*) ;; *) echo "FENCE FAIL"; exit 99;; esac

mkdir -p "$EV/logs"
cp -f "$FENCE/logs/daemon.log" "$EV/logs/daemon.log"
cp -f "$FENCE/logs/edge.log"   "$EV/logs/edge.log"
cp -f /tmp/g1-drill/curl/a.headers "$EV/logs/curl-a-null-origin-same-origin.headers"
cp -f /tmp/g1-drill/curl/a.body    "$EV/logs/curl-a-null-origin-same-origin.body"
cp -f /tmp/g1-drill/curl/b.headers "$EV/logs/curl-b-null-origin-crosssite.headers"
cp -f /tmp/g1-drill/curl/b.body    "$EV/logs/curl-b-null-origin-crosssite.body"
cp -f /tmp/g1-drill/curl/c.headers "$EV/logs/curl-c-evil-origin.headers"
cp -f /tmp/g1-drill/curl/d.headers "$EV/logs/curl-d-json-client-crosssite.headers"
cp -f /tmp/g1-drill/curl/d.body    "$EV/logs/curl-d-json-client-crosssite.body"
cp -f /tmp/g1-drill/curl/e.headers "$EV/logs/curl-e-session-bypass.headers"
cp -f /tmp/g1-drill/curl/e.body    "$EV/logs/curl-e-session-bypass.body"
cp -f /tmp/g1-drill/browser.json   "$EV/logs/run1-playwright-api-headers.json" 2>/dev/null || true
cp -f /tmp/g1-drill/control/base_fn.py "$EV/logs/base-f49006b92-origin-check.py"
echo "== artifacts copied:"; ls -la "$EV" "$EV/logs" | sed -n 1,40p

echo
echo "== teardown (port-verified kills only) =="
bash "$WT/.agents/tester/RESULTS/assets/2026-11-09-views-session-bypass/g1/scripts/g1-lib.sh" stop

echo
echo "== delete the rider session row (dev DB, scoped) =="
cd "$WT"; set -a; source .env; set +a; export PGPASSWORD="$POSTGRES_PASSWORD"
SID=$(cat "$FENCE/rider-session-id" 2>/dev/null || true)
if [ -n "$SID" ] && [ "$POSTGRES_DB" = "ensemble_dev" ]; then
  psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
    "delete from auth_sessions where id='$SID'" && echo "== rider session row deleted"
else
  echo "== no rider session id / wrong DB — skipped"
fi

echo
echo "== fence removal (escape-guarded) =="
case "$FENCE" in /tmp/g1lv.*) rm -rf "$FENCE"; echo "removed $FENCE";; esac
rm -f /tmp/g1fence.ready
echo "== done"
