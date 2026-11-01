#!/bin/bash
cd /home/nea/ensemble-src-wt-obligation
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
  ENSEMBLE_SELF_ENV=dev PORT=8079 \
  timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest \
  "tests/integration/test_attestation_live_descendants.py::TestFacadeBfsCap" \
  "tests/integration/test_attestation_live_descendants.py::TestBusyClassification::test_idle_without_work_not_live_not_busy" \
  --tb=long -q > /tmp/wi1f-g9disp-probes/facade-busy.log 2>&1
echo "exit=$?"
