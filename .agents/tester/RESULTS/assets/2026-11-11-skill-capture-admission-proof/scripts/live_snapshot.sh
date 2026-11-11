#!/bin/bash
# live_snapshot.sh <outdir> — READ-ONLY live fence snapshot
OUT="$1"; mkdir -p "$OUT"
{
  echo "=== ts_utc ==="; date -u +%FT%TZ
  echo "=== current_symlink ==="; readlink -f /home/nea/agents-ensemble/current; readlink /home/nea/agents-ensemble/current
  echo "=== state_json_stat ==="; stat -c '%n mtime=%Y(%y) size=%s mode=%a' /home/nea/agents-ensemble/releases/state.json
  echo "=== state_json_sha ==="; sha256sum /home/nea/agents-ensemble/releases/state.json
  echo "=== state_json_upgrade_fields ==="; python3 -c "
import json
d=json.load(open('/home/nea/agents-ensemble/releases/state.json'))
for k in ('current','previous','pending_op','promoted_at','version','rollback_count','rollback_safe','nonce','nonce_consumed','pending_nonce'):
    if k in d: print(k,'=',repr(d[k]))
print('history_len =', len(d.get('history',[])) if isinstance(d.get('history'),list) else 'n/a')
print('top_keys =', sorted(d.keys()))
"
  echo "=== upgrade_dir ==="; find /home/nea/agents-ensemble/data/upgrade -type f 2>/dev/null | sort | head -40
  echo "=== upgrade_dir_files_stat_sha ==="; find /home/nea/agents-ensemble/data/upgrade -type f 2>/dev/null | sort | while read -r f; do stat -c '%n mtime=%Y size=%s' "$f"; sha256sum "$f"; done
  echo "=== releases_dir_stat ==="; stat -c '%n mtime=%Y size=%s' /home/nea/agents-ensemble/releases
  echo "=== systemd ==="; systemctl show ensemble-main.service -p MainPID -p NRestarts -p ActiveState -p SubState -p ActiveEnterTimestamp -p ExecMainStartTimestamp -p NRestarts 2>&1
  echo "=== systemd_uptime ==="; systemctl show ensemble-main.service -p ActiveEnterTimestampMonotonic -p ExecMainStartTimestampMonotonic 2>&1
  echo "=== livez ==="; curl -sS --max-time 10 http://127.0.0.1:9797/livez; echo
  echo "=== readyz ==="; curl -sS --max-time 10 http://127.0.0.1:9797/readyz; echo
  echo "=== live_capture_lane ==="; curl -sS --max-time 10 http://127.0.0.1:9797/api/metrics/capture-lane; echo
  echo "=== live_skill_capture_sink_files ==="; ls -la /home/nea/agents-ensemble/data/skill-capture-observability/ 2>/dev/null
  echo "=== live_procs ==="; ps -eo pid,ppid,lstart,cmd | grep -E "ensemble-prod|launcher.sh" | grep -v grep
} > "$OUT/snapshot.txt" 2>&1
echo "snapshot written to $OUT/snapshot.txt"
