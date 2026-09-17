#!/usr/bin/env bash
# LCA stage3 attestation matrix pack (verification gate) - frozen at tip f8e78a40
# Pack: lca3_matrix_13_migration_feature_test  (Job 1/N; stage3 = single-path endstate, R1-R8 + ledger a-e retired)
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 290s guard below.
# Invocation contract: timeout 300 bash test/packs/lca3_matrix_13_migration_feature_test.sh  (from worktree root)
# Stage3 specifics:
#   - 3 stage2-branch-scoped artifacts (budget_parity, incident_abc, incident_de) DELETED in this delta — NOT in family.
#   - test_attestation_stage3_census.py is NEW (23 negative-pin tests; R1-R8 + ledger (c)).
#   - tests/integration/test_attestation_bound_escalation.py is EXCLUDED — single test pre-existing
#     asyncio-selector wedge (verified hanging at BASELINE; environmental; left for the separate
#     tester-lane characterization worker; NOT a stage3-introduced defect).
#   - PG lane (tests/postgres/test_attestation_live_descendants_pg_lca.py) is a SEPARATE lane — NOT in this matrix.
# KNOWN PRE-EXISTING FOREIGN DEFECT (NOT a stage3-introduced defect; verified identical at base f7588291):
#   tests/migration/test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations
#   ::test_no_boolean_int_literal_default fails on the critical-notes migration
#   20260915_120000 (BOOLEAN NOT NULL DEFAULT 0 is PG-invalid). Documented in the
#   stage3 retirement report §5 + critical-notes board 🟢 row.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca3_matrix_13_migration_feature_test"
FILES=(
  tests/migration/test_attestation_migration.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 290 uv run python -m pytest "${FILES[@]}" --tb=short -q
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
