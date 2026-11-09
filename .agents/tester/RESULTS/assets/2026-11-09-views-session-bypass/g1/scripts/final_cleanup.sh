#!/usr/bin/env bash
# final cleanup: delete rider session rows (dev DB) + drop the fence
set -uo pipefail
WT=/home/nea/ensemble-src-wt-views-session-bypass
cd "$WT"
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT \
      PGHOST PGPORT PGUSER PGPASSWORD SSL_CERT_FILE SSL_CERT_DIR 2>/dev/null
set -a; source .env; set +a
[ "$POSTGRES_DB" = "ensemble_dev" ] || { echo "ABORT: not dev DB ($POSTGRES_DB)"; exit 98; }
export PGPASSWORD="$POSTGRES_PASSWORD"
echo "== deleting rider session rows (subject=g1-rider-operator)"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "delete from auth_sessions where subject='g1-rider-operator'" 
echo "== remaining rider rows: $(psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "select count(*) from auth_sessions where subject='g1-rider-operator'")"
echo "== passcode rows left by this gate (left in place, dev DB):"
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
  "select root_name||'/'||rel_path||' gen='||generation||' failed='||failed_attempts||' locked='||locked from live_view_passcodes where root_name like 'g1root%'"
FENCE="$(cat /tmp/g1fence.ready 2>/dev/null || true)"
case "$FENCE" in
  /tmp/g1lv.*) rm -rf "$FENCE"; echo "== fence removed: $FENCE";;
  "") echo "== no fence to remove";;
  *) echo "== REFUSED fence removal (guard): $FENCE";;
esac
rm -f /tmp/g1fence.ready
echo "== ports:"
for p in 8081 8443 8088; do
  if ss -ltn "sport = :$p" | grep -q LISTEN; then echo "  :$p LISTEN"; else echo "  :$p free"; fi
done
