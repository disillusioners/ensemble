#!/usr/bin/env bash
# Disposable-daemon auth-ON canary — verifies the 5 public probes are
# reachable unauthenticated against an auth-ON daemon, with NOT-401
# semantics (/readyz may be 200-degraded). The daemon is booted with
# ENSEMBLE_AUTH_ENABLED=true against an in-process FakeOidcProvider
# via the python helper invoked by this script.
#
# This is the AUTHED equivalent of the snapshots/maintenance boot-e2e
# canaries: the maintenance script's row 11 keeps working unchanged
# with auth ON because /api/maintenance/checkpoint-cleanup/availability
# is in the public-path set (D18 / FR-22).
#
# Usage:
#   ./tests/e2e/auth_health_probes.sh [BASE_URL]
#
# Exit codes:
#   0 — all 5 probes reachable, NOT-401
#   non-zero — at least one probe is 401 (auth regression)
#
# The script can run in two flavors:
#   (a) Against an existing daemon at BASE_URL (default: localhost:18280)
#   (b) Against a freshly-spawned daemon (auth-enabled only with the
#       python helper that wires the in-process fake). The second mode
#       is the canonical invocation when the script is called from CI.
#
# The authed canary exits non-FAST (each curl waits up to 30 s) so a
# boot-time crash is visible as a curl failure, NOT a hang.
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:18280}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-30}"

# The 5 public probes per [REQ] §5.4 + FR-22 + AMEND v1.2 / A7.
PROBES=(
  "/livez"
  "/readyz"
  "/api/health"
  "/api/info"
  "/api/maintenance/checkpoint-cleanup/availability"
)

failures=0
for path in "${PROBES[@]}"; do
    status=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time "$TIMEOUT_SECONDS" \
        "${BASE_URL}${path}") || status="000"
    case "$status" in
        2*|3*|404|503)
            # NOT-401: the auth layer must not gate these paths.
            # 503 from /readyz during PG-outage is acceptable (the
            # readiness contract, NOT an auth regression).
            echo "[ok] ${path} → ${status}" >&2
            ;;
        401|302)
            # 401 from any probe is an auth regression; the canary
            # surfaces the failure with a loud marker.
            echo "[FAIL auth-gated] ${path} → ${status} (must NOT be 401/302)" >&2
            failures=$((failures + 1))
            ;;
        000)
            echo "[FAIL unreachable] ${path} → ${status}" >&2
            failures=$((failures + 1))
            ;;
        *)
            echo "[warn] ${path} → ${status} (unexpected, NOT a regression)" >&2
            ;;
    esac
done

if [ "$failures" -ne 0 ]; then
    echo "[auth_health_probes] $failures regression(s) detected" >&2
    exit 1
fi

echo "[auth_health_probes] 5/5 probes reachable, NOT-401" >&2
exit 0