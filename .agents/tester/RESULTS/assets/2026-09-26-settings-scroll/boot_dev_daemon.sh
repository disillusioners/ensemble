#!/bin/bash
# Standalone boot script for the dev daemon (port 8079).
# Mandatory pattern: STANDALONE #!/bin/bash (never source'd, never via /bin/sh)
# + echo-verify ZERO POSTGRES_* survivors before ANY boot — this host has
# live-probe incident history where `source`-under-dash scrubs silently no-op'd.
# Artifact path: .agents/tester/RESULTS/assets/2026-09-26-settings-scroll/

set +e  # Defensive: never let set -e abort the scrub loop on a transient miss.
set +u

LOG_DIR="/home/nea/ensemble-src/.agents/tester/RESULTS/assets/2026-09-26-settings-scroll"
LOG="${LOG_DIR}/dev_daemon.log"
mkdir -p "$LOG_DIR"

# SCRUB PHASE: unset every POSTGRES_* variable; repeat for safety (3 passes
# is plenty — even a single pass is sufficient if unset is reliable).
for safety_round in 1 2 3; do
    PG_VARS=$(env | cut -d= -f1 | grep '^POSTGRES_' || true)
    if [ -z "$PG_VARS" ]; then
        break
    fi
    for v in $PG_VARS; do
        unset "$v"
    done
done

# ECHO-VERIFY: MUST show 0 before exec. Required by safety policy
# (2026-09-21 + 2026-09-26 live-probe incident lessons).
echo "POSTGRES_SURVIVORS=$(env | grep -c '^POSTGRES_' || true)"

# Capture evidence to artifact log FIRST (so we capture the POSTGRES_SURVIVORS=0 line
# even if dev.sh errors out). Real daemon logs are also captured by proc_run.
exec >>/home/nea/ensemble-src/.agents/tester/RESULTS/assets/2026-09-26-settings-scroll/dev_daemon.log 2>&1
echo "=== boot_dev_daemon.sh @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
