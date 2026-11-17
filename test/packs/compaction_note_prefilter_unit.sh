#!/usr/bin/env bash
# test/packs/compaction_note_prefilter_unit.sh
#
# Pack: compaction_note_prefilter_unit
#
# The compact-note lane's DORMANT pre-filter legs (plan §10.3).
# Owned artifact (lane-owned and NEW):
#   tests/unit/tools/test_compaction_note_prefilter.py
#
# Leg covered: L13
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No external services. No PostgreSQL contact.
#
# Dual-layer timeout (per the test-pack skill):
#   - Layer 1 (command-level): the caller wraps this with `timeout 300`.
#   - Layer 2 (script-internal): `timeout 210s` on the pytest process.
#
# Exit codes:
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
#
# NOTE (house rule): the pytest exit code is captured with `|| RC=$?` and
# NOT under `set -e` — a `set -euo pipefail` + unguarded `RC=$?` script
# exits BEFORE it can report a red. A red MUST be reportable.
set -uo pipefail
IFS=$'\n\t'
export PATH="/home/nea/.local/bin:$PATH"

PACK_NAME="compaction_note_prefilter_unit"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${PROJECT_DIR}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# Per-variable env scrub — NEVER `env -u POSTGRES_*` (bash does not glob
# env names, so that idiom is a silent NO-OP).
env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
    -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD \
    -u PGSERVICE -u PGOPTIONS \
    -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER \
    -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_CLIENT_ID \
    -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS \
    -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
    timeout 210s .venv/bin/python -m pytest \
    tests/unit/tools/test_compaction_note_prefilter.py \
    -q --tb=short -p no:cacheprovider
RC=$?

echo
if [ "$RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT (internal 210s cap exceeded)"
    exit 124
elif [ "$RC" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    echo "Exit: $RC"
    exit 1
fi
