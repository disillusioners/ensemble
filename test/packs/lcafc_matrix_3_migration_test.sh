#!/usr/bin/env bash
# LCA false-completion merge gate — MATRIX pack (pack 3/9, Set A migration (OWN pack))
# Pack: lcafc_matrix_3_migration_test
# Gate: feature/lca-false-complete-fixes @ d5c50994 (delta 316a849b..d5c50994, 1 commit;
# incident 7d4a3bd9 attestation false-completion fixes; 32 files +2981/-161).
# Splitter-generated 2026-09-26. 1 file / 18 collected tests; est 1-5s.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcafc_matrix_3_migration_test.sh  (from worktree root)
# EXPECTED RED (pre-existing, NOT caused by this delta): per prior-gate evidence
# (lcancheck_matrix_3 2026-09-23 PASS* with 1 foreign red) — TestNoBooleanIntegerDefaultInShippedMigrations
# / TestNoBooleanDefaultConstantInShippedMigrations surfaces a BOOLEAN NOT NULL DEFAULT 0
# migration (foreign critical-notes lineage 20260915_120000, PG rejects DEFAULT 0 on BOOLEAN).
# Runners: treat that single red as PASS-equivalent for gate math; any OTHER failure in
# this pack is a real regression.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin: must be on the gate branch with d5c50994 as ancestor and zero production diff
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "feature/lca-false-complete-fixes" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != feature/lca-false-complete-fixes)"; exit 1
fi
if ! git merge-base --is-ancestor d5c50994 HEAD; then
  echo "RESULT: FAIL (DRIFT — d5c50994 not ancestor of HEAD)"; exit 1
fi
if [ -n "$(git diff d5c50994 HEAD -- daemon/ frontend/ scripts/ migrations/)" ]; then
  echo "RESULT: FAIL (DRIFT — daemon/, frontend/, scripts/, or migrations/ has diff vs d5c50994)"; exit 1
fi

PACK="lcafc_matrix_3_migration_test"
FILES=(
tests/migration/test_attestation_migration.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q
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
