#!/usr/bin/env bash
# Embedding-Gzip-Exemption — direct embedding consumers unit-test pack (125 tests).
# Pack: embed_consumers_unit_test
# Gate: fix/embedding-gzip-exemption @ ce644d4a (same drift-pin as
# embed_gzip_unit_test). Scope: direct embedding consumers —
# test_snapshot_embedding_service + test_snapshot_search_service +
# tests/services/test_skill_embedding_service + test_skill_search_service +
# test_skill_store_service + test_skill_evolution_service +
# test_skill_phase2_integration. Inner 240s; env-scrubbed POSTGRES_*;
# --tb=short -q, no -x.
#
# NOTE on pyproject.toml [tool.pytest.ini_options] addopts:
# Default addopts = "-m 'not integration and not postgres'" — this FILTER
# is harmless for the consumers pack (zero file in the list declares
# @pytest.mark.integration or @pytest.mark.postgres — verified
# 2026-09-26). The conftest in tests/services/ provides in-memory SQLite
# engine/skill_repo/usage_repo/etc. fixtures; it does NOT depend on
# POSTGRES_* env. Therefore NO --override-ini="addopts=" is needed.
# Disclosed: zero-pack-interference from default addopts; consumers
# pack runs the standard pytest collection with the default marker
# filter on top of --tb=short -q.
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard.
# Invocation contract: timeout 300 bash test/packs/embed_consumers_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin (matches embed_gzip_unit_test.sh):
# * Branch MUST equal fix/embedding-gzip-exemption.
# * 139ba352 MUST be an ancestor of HEAD.
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "fix/embedding-gzip-exemption" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != fix/embedding-gzip-exemption)"; exit 1
fi
if ! git merge-base --is-ancestor 139ba352 HEAD; then
  echo "RESULT: FAIL (DRIFT — base 139ba352 not ancestor of HEAD)"; exit 1
fi

PACK="embed_consumers_unit_test"
FILES=(
tests/unit/test_snapshot_embedding_service.py
tests/unit/test_snapshot_search_service.py
tests/services/test_skill_embedding_service.py
tests/services/test_skill_search_service.py
tests/services/test_skill_store_service.py
tests/services/test_skill_evolution_service.py
tests/services/test_skill_phase2_integration.py
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
