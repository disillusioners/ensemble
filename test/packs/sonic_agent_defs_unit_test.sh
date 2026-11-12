#!/usr/bin/env bash
# Sonic Team (ENSEMBLE-31) — agent-definition contract pack.
# Pack: sonic_agent_defs_unit_test
# Gate: feature/sonic-team-v1 @ base ac9849bf7 (ancestor-pinned, not branch-name-pinned).
# Scope: agents/sonic-{leader,coder,reviewer,tester}/{meta.json,*.md} +
#        agents/ari/rule.md (note + exactly-1 Plane) + agents/.prompt-artifacts.sha256
#        + tests/unit/test_ari_pm_terminology_pins.py + the ari/rule.md row of
#        tests/test_chart_image_delivery_e2e.py::SEALED_SHA_BASELINES.
# Dual-layer timeout: outer `timeout 300` at invocation + inner 240s guard.
# Invocation contract: timeout 300 bash test/packs/sonic_agent_defs_unit_test.sh
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; cd "$ROOT"

# Drift pin (ancestor form — this pack must also run on latest post-merge):
git merge-base --is-ancestor ac9849bf7 HEAD || {
  echo "RESULT: FAIL (DRIFT — base ac9849bf7 not ancestor of HEAD)"; exit 1; }

PACK="sonic_agent_defs_unit_test"
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)

# Layer A — static contract checks + mutation self-test (stdlib only).
python3 test/packs/sonic_agent_defs_checks.py --static --self-test || { echo "RESULT: FAIL (static)"; exit 1; }

# Layer B — REAL resolver + terminology pins (venv required; uv run lazily syncs).
timeout 180 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
  -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
  uv run python test/packs/sonic_agent_defs_checks.py --real || { echo "RESULT: FAIL (real-resolver)"; exit 1; }
timeout 120 env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
  -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
  uv run python -m pytest tests/unit/test_ari_pm_terminology_pins.py --tb=short -q \
  || { echo "RESULT: FAIL (ari terminology pins)"; exit 1; }

END=$(date +%s); echo "Pack inner runtime: $((END-START))s"; echo "RESULT: PASS"; exit 0
