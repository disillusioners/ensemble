#!/usr/bin/env bash
# Embedding-Gzip-Exemption — gzip trio unit-test pack (48 tests).
# Pack: embed_gzip_unit_test
# Gate: fix/embedding-gzip-exemption @ ce644d4a (1-commit-clean on base
# v0.15.2 @ 139ba352; diff = daemon/services/skill_embedding_service.py
# + new tests/unit/test_embedding_gzip_exempt.py + re-contracted
# tests/unit/test_llm_request_gzip_edge_cases.py).
# Scope: gzip trio — tests/unit/test_embedding_gzip_exempt.py (6) +
# tests/unit/test_llm_request_gzip.py (26) + tests/unit/test_llm_request_gzip_edge_cases.py
# (16) = 48. Inner 150s; env-scrubbed POSTGRES_*; --tb=short -q, no -x.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 150s guard.
# Invocation contract: timeout 300 bash test/packs/embed_gzip_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin (per PACKS.md / MOCK_TESTS.md close-out spec):
# * Branch MUST equal fix/embedding-gzip-exemption (single-commit fix on
#   the dedicated branch — sibling commits are NOT expected here, unlike
#   LCAFC; if the branch tip advances, that is a separate branch and the
#   pack refuses until refreshed).
# * 139ba352 MUST be an ancestor of HEAD (the base v0.15.2 commit the
#   fix sits on top of).
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "fix/embedding-gzip-exemption" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != fix/embedding-gzip-exemption)"; exit 1
fi
if ! git merge-base --is-ancestor 139ba352 HEAD; then
  echo "RESULT: FAIL (DRIFT — base 139ba352 not ancestor of HEAD)"; exit 1
fi

PACK="embed_gzip_unit_test"
FILES=(
tests/unit/test_embedding_gzip_exempt.py
tests/unit/test_llm_request_gzip.py
tests/unit/test_llm_request_gzip_edge_cases.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 150 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python -m pytest "${FILES[@]}" --tb=short -q
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
