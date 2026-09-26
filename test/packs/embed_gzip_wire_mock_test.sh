#!/usr/bin/env bash
# Embedding-Gzip-Exemption — wire-mock driver pack (green-side permanent).
# Pack: embed_gzip_wire_mock_test
# Gate: fix/embedding-gzip-exemption @ ce644d4a (same drift-pin as the
# unit-test packs). Scope: wire-level green proof — strict embeddings
# server on 18771 + REAL SkillEmbeddingService.embed_text with
# request_gzip=True and base_url→local strict server → assert 200, NO
# Content-Encoding: gzip on the embeddings request, valid JSON parse
# server-side; chat-path gzip client → assert Content-Encoding: gzip
# PRESENT (proves the fix is scope-limited to the embedding path).
# Inner 240s; env-scrubbed POSTGRES_* (no DB contact); port hygiene
# (driver refuses-and-reports on port collision, NEVER kills anything).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard.
# Invocation contract: timeout 300 bash test/packs/embed_gzip_wire_mock_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Drift pin (matches the other two packs):
# * Branch MUST equal fix/embedding-gzip-exemption.
# * 139ba352 MUST be an ancestor of HEAD.
BR=$(git rev-parse --abbrev-ref HEAD)
if [ "$BR" != "fix/embedding-gzip-exemption" ]; then
  echo "RESULT: FAIL (DRIFT — branch $BR != fix/embedding-gzip-exemption)"; exit 1
fi
if ! git merge-base --is-ancestor 139ba352 HEAD; then
  echo "RESULT: FAIL (DRIFT — base 139ba352 not ancestor of HEAD)"; exit 1
fi

PACK="embed_gzip_wire_mock_test"
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL uv run python tests/mocks/embed_gzip_wire_mock.py --expect green --port 18771
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
