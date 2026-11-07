#!/usr/bin/env bash
# Test Pack: sources_pagination_regression_pin_unit_test
#
# Commission wi-b97ec302 — sources pagination regression pin (unit, hermetic).
# Root cause is a DATA-GROWTH-TRIGGERED LATENT DEFECT (the implicit 100-row
# page cap in list_source_configs evicting the oldest rows), NOT a release
# regression — the release window was exonerated by same-data repro on the
# prior build. Do not describe it as a version-introduced regression.
#
# Scope: boot loader paging + stale-running reconciliation + GET /api/sources
# pagination/total/clamp + same-seam schedules surfaces. 14 nodes.
# Substrate: scratch file-backed SQLite (tmp_path, NullPool) — NO daemon boot,
# no PostgreSQL, no ports, no network, no live contact.
#
# MUTATION LEG (lane-executed, both directions, digests in the lane report):
# restore the old single default-limited call in SourceRegistry.start_all
# (`list_source_configs()` with defaults) — 4 legs go RED; restore the fix
# (byte-identical, sha-verified) before committing.
#
# Timeout: 5 minutes (300s) — the clamp leg seeds 1005 rows (~40s wall).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: sources_pagination_regression_pin_unit_test ==="

cd "$PROJECT_DIR"

if [ ! -x ".venv/bin/pytest" ]; then
  echo "FAIL: .venv/bin/pytest missing — run 'uv sync' in this tree first"
  echo "RESULT: FAIL"
  exit 1
fi

timeout 300s .venv/bin/pytest \
  tests/unit/test_sources_pagination_regression_pin.py \
  --tb=short -q -p no:cacheprovider 2>&1

EXIT_CODE=$?

if [ "$EXIT_CODE" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$EXIT_CODE" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
