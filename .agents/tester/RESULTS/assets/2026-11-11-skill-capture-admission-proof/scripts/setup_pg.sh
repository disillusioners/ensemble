#!/bin/bash
# setup_pg.sh (v2) — disposable NATIVE PG cluster, TCP-only, port 18912
set -euo pipefail
EV=/tmp/ens-e2e-evidence/v2
TMPROOT=$(cat "$EV/tmp_root.txt")
PGBIN=/usr/lib/postgresql/16/bin
PGPORT=$(cat "$EV/pg_port.txt")
PGDATA="$TMPROOT/pgdata"
mkdir -p "$PGDATA"

"$PGBIN/initdb" -D "$PGDATA" -A trust -U pguser --no-sync -E UTF8 > "$EV/pg_initdb.log" 2>&1
echo "initdb rc=$?"

cat > "$TMPROOT/pg_hba.conf" <<'HBA'
local   all all trust
host    all all 127.0.0.1/32 trust
host    all all ::1/128 trust
HBA
cat > "$TMPROOT/pg_postgresql.conf" <<CONF
listen_addresses = '127.0.0.1'
port = $PGPORT
unix_socket_directories = ''
fsync = off
synchronous_commit = off
full_page_writes = off
CONF

"$PGBIN/pg_ctl" -D "$PGDATA" -l "$EV/pg_server.log" -o "-c config_file=$TMPROOT/pg_postgresql.conf -c hba_file=$TMPROOT/pg_hba.conf" -w -t 60 start
echo "pg_ctl start rc=$?"
export PGHOST=127.0.0.1 PGPORT=$PGPORT PGUSER=pguser
"$PGBIN/createdb" ens_e2e_v02917
echo "PG OK: $(psql -At -c 'select version();' | cut -c1-60) db=ens_e2e_v02917"
