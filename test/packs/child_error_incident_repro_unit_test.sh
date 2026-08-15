#!/usr/bin/env bash
# Test Pack: child_error_incident_repro_unit_test
# Scope: FUNCTIONAL incident-chain repro (not pytest) — the verified prod
# incident f10b7694 (2026-08-15): provider returned a bare JSON string body
# → SDK passthrough → LangChain AttributeError('str' has no model_dump) →
# classified non-retryable → instance died. This pack drives the REAL chain:
# poisoned provider client → ThinkingChatOpenAI.invoke() guard → classifier
# → tenacity retry-exhaust → ErrorReportingService._send_error_report with
# the [RECOVERY GUIDANCE] hint, plus the AttributeError regression net.
# Layer 1 (outer, caller-side): `timeout 300 ./test/packs/child_error_incident_repro_unit_test.sh`
# Layer 2 (inner, this script): `timeout 120s` around the python process
# (unit hard limit 2 min); the python script additionally self-guards with
# signal.alarm(120) and exits 124 on its own timer.
set -u
cd /Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=== Test Pack: child_error_incident_repro_unit_test ==="

timeout 120s "$PY" test/packs/child_error_incident_repro_unit_test.py 2>&1
RC=$?

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
