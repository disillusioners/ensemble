#!/usr/bin/env bash
# Test Pack: origin_contract_e2e_probe_test — REAL-router probe of the
# POST /api/jobs source-validation contract on feature/security-boundary-hygiene.
#
# Background. The branch ships two commits that close Reviewer Warning #2
# (source field validation hardening): 974b06de (reserve internal origins)
# and a77647bf (complete the contract + bus-pending type fix). The branch's
# own 27P suite exercises the contract in-process; this probe is an
# INDEPENDENT end-to-end re-derivation that drives the REAL router via
# httpx ASGITransport against a minimal FastAPI app with:
#   - real Pydantic validation on the body (no MagicMock of validation)
#   - real is_reserved_source gate from daemon/constants.py:424-470
#   - real JobCreateRequest envelope from daemon/routers/schemas.py:12-22
#   - stubbed manager + JobQueueService downstream (capture + return success)
#
# Three sections:
#   Part 1 — 422 surface matrix (30 cases covering omitted/null/empty,
#            8 reserved prefixes, 8 reserved exact, 3 mixed-case,
#            5 legitimate user sources, 3 near-miss non-reserved,
#            1 reserved scheduler that is daemon-minted)
#   Part 2 — Census re-derivation (static grep of every daemon/*.py mint
#            site for each reserved member; diff vs the 17-member set)
#   Part 3 — USER_ORIGIN_SOURCES zero-overlap claim vs RESERVED set
#
# TEST-ENV ONLY. No production code changes, no commits, .agents/tester/
# untouched, no ports (ASGI in-process transport).
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 150s` around the python process
#     (probe target <2 min; the python script additionally self-guards
#     with signal.alarm(150) and exits 124 on its own timer).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -u
cd "$(dirname "$0")/../.."
PROJECT_DIR="$(pwd)"

echo "=== Test Pack: origin_contract_e2e_probe_test ==="
echo "(POST /api/jobs 422 surface + census re-derivation —"
echo " feature/security-boundary-hygiene @ 16c59375)"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
    PY=python3
fi

# Layer 2 (script-internal): 150s hard cap on the python process.
timeout 150s "$PY" test/packs/origin_contract_e2e_probe_test.py 2>&1
RC=$?

# The python script emits its own "RESULT: PASS|FAIL|TIMEOUT" line;
# we keep this mapping as a safety net (in case the script is killed
# before printing its own result, e.g. via `kill -9`).
if [ "$RC" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
elif [ "$RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
else
    echo "RESULT: FAIL"
    exit 1
fi
