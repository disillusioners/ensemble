#!/usr/bin/env bash
# LCA Completion Check Note removal merge gate — MATRIX pack (pack 3/9, Set A migration (OWN pack))
# Pack: lcancheck_matrix_3_migration_test
# Gate: feature/lca-remove-check-note @ ff9eb849 (delta 6bf7bed7..ff9eb849, 1 commit).
# Splitter-generated 2026-09-23. 1 file / 18 collected tests; est 10-30s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcancheck_matrix_3_migration_test.sh  (from worktree root)
# EXPECTED RED (pre-existing, NOT caused by this delta): exactly 1 failure —
# TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default
# Offender: 20260915_120000_critical_notes_lifecycle.sql (foreign critical-notes lineage,
# BOOLEAN NOT NULL DEFAULT 0; PG rejects DEFAULT 0 on BOOLEAN). Runners: treat this single
# red as PASS-equivalent for gate math; any OTHER failure in this pack is a real regression.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin: must be on the gate branch with ff9eb849 as ancestor and zero daemon/scripts diff
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-remove-check-note" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-remove-check-note)"; exit 1
fi
if ! git merge-base --is-ancestor ff9eb849 HEAD; then
  echo "RESULT: FAIL (DRIFT — ff9eb849 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff ff9eb849 HEAD -- daemon/ scripts/)" ]; then
  echo "RESULT: FAIL (DRIFT — daemon/ or scripts/ has diff vs ff9eb849)"; exit 1
fi

PACK="lcancheck_matrix_3_migration_test"
FILES=(
  tests/migration/test_attestation_migration.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q
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
