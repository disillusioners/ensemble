#!/usr/bin/env bash
# RIDER (cheap): mint an ensemble_sid session row in the drill dev DB and prove
# the public GET serves the view DIRECTLY (no interstitial, no view_grant).
set -uo pipefail
WT=/home/nea/ensemble-src-wt-views-session-bypass
cd "$WT"
set -a; source .env; set +a
export PGPASSWORD="$POSTGRES_PASSWORD"
[ "$POSTGRES_DB" = "ensemble_dev" ] || { echo "ABORT: not dev DB"; exit 98; }
FENCE=$(cat /tmp/g1fence.ready)

SID="g1drillsession$(openssl rand -hex 8)"
NOW=$(date -u +"%Y-%m-%dT%H:%M:%S")
EXP=$(date -u -d '+3 hours' +"%Y-%m-%dT%H:%M:%S")

psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -q \
  -c "INSERT INTO auth_sessions (id, subject, email, scopes, auth_method, user_agent, ip_subnet, created_at, last_used_at, expires_at)
      VALUES ('$SID', 'g1-rider-operator', 'g1@drill.invalid', 'ensemble:read', 'session', 'g1-gate', '127.0.0.0/24', '$NOW', '$NOW', '$EXP');" \
  || { echo "INSERT FAILED"; exit 96; }
echo "$SID" > "$FENCE/rider-session-id"
echo "== rider session minted (id prefix ${SID:0:14}…) expires $EXP"

echo
echo "=== E) public GET WITH ensemble_sid, NO view_grant (expect direct view, no interstitial) ==="
curl -sk -D /tmp/g1-drill/curl/e.headers -o /tmp/g1-drill/curl/e.body \
  -H "Cookie: ensemble_sid=$SID" -H "Accept: text/html,application/xhtml+xml" \
  "https://127.0.0.1:8443/views/public/g1root2/gate.md"
head -1 /tmp/g1-drill/curl/e.headers
grep -i '^content-type:\|^set-cookie:' /tmp/g1-drill/curl/e.headers || true
echo "--- interstitial present? ---"
grep -c "Enter passcode" /tmp/g1-drill/curl/e.body || true
echo "--- content marker present? ---"
grep -c "G1-CONTENT-MARKER" /tmp/g1-drill/curl/e.body || true
echo "--- JSON body? ---"
head -c 80 /tmp/g1-drill/curl/e.body; echo
