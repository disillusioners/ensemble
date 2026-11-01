#!/bin/bash
# wi-1fbee2ec G9 isolation matrix — 3x per side per candidate FILE (corpus flags, single file, timeout 300)
# plus a ONE-RUN-per-side T7 error-class probe (--tb=line diagnostic variant, disclosed).
set -u
BASE_WT=/home/nea/ensemble-src-wt-obligation-base
HEAD_WT=/home/nea/ensemble-src-wt-obligation
RUNNER=/tmp/wi1f-g9-py.sh
PY=/home/nea/ensemble-src/.venv/bin/python

iso () { # side wt tag file
  local side=$1 wt=$2 tag=$3 f=$4 i rc log
  for i in 1 2 3; do
    log=/tmp/wi1f-g9-iso-${side}-${tag}-r${i}.log
    echo "### iso ${side} ${tag} r${i} START=$(date -u '+%FT%TZ') file=$f"
    timeout -k 15 300 bash "$RUNNER" "$wt" "$f" > "$log" 2>&1
    rc=$?
    echo "### iso ${side} ${tag} r${i} rc=${rc} END=$(date -u '+%FT%TZ')"
    grep -E "^(FAILED|ERROR)" "$log" | sed -E 's/ - .*//' | sed 's/^/    /'
    tail -1 "$log" | sed 's/^/    SUMMARY: /'
  done
}

iso base "$BASE_WT" f1 tests/integration/test_attestation_idle_orphan_incident.py
iso head "$HEAD_WT" f1 tests/integration/test_attestation_idle_orphan_incident.py
iso base "$BASE_WT" f2 tests/integration/test_attestation_incident_acceptance_lca.py
iso head "$HEAD_WT" f2 tests/integration/test_attestation_incident_acceptance_lca.py
iso base "$BASE_WT" f3 tests/integration/test_attestation_live_descendants.py
iso head "$HEAD_WT" f3 tests/integration/test_attestation_live_descendants.py
iso base "$BASE_WT" f4 tests/test_manager_shutdown.py
iso head "$HEAD_WT" f4 tests/test_manager_shutdown.py

# T7 error-class probe (one per side, same command both sides)
for pair in "base:$BASE_WT" "head:$HEAD_WT"; do
  side=${pair%%:*}; wt=${pair#*:}
  log=/tmp/wi1f-g9-t7probe-${side}.log
  echo "### t7probe ${side} START=$(date -u '+%FT%TZ')"
  ( cd "$wt" && timeout -k 15 300 env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
      ENSEMBLE_SELF_ENV=dev PORT=8079 PYTHONDONTWRITEBYTECODE=1 \
      "$PY" -m pytest "tests/unit/services/test_child_report_next_turn_execution.py::TestT7NextTurnExecution" -q --tb=line -rf -p no:randomly --timeout=120 -p no:cacheprovider ) > "$log" 2>&1
  echo "### t7probe ${side} rc=$? END=$(date -u '+%FT%TZ')"
  grep -E "^(FAILED|/)" "$log" | head -6 | sed 's/^/    /'
done
echo "ISO DONE=$(date -u '+%FT%TZ')"
