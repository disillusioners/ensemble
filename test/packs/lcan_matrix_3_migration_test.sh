#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 3/10, Set A migration (OWN pack))
# Pack: lcan_matrix_3_migration_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 1 file / 18 collected tests; est 30-60s
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_3_migration_test.sh  (from worktree root)
# EXPECTED RED (pre-existing, NOT caused by this delta): exactly 1 failure —
# TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default
# Offender: 20260915_120000_critical_notes_lifecycle.sql (foreign critical-notes lineage,
# BOOLEAN NOT NULL DEFAULT 0; PG rejects DEFAULT 0 on BOOLEAN). Runners: treat this single
# red as PASS-equivalent for gate math; any OTHER failure in this pack is a real regression.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_3_migration_test"
FILES=(
  tests/migration/test_attestation_migration.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 uv run python -m pytest "${FILES[@]}" --tb=short -q
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi
