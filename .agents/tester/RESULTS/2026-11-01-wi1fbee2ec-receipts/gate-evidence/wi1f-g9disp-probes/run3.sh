#!/bin/bash
cd /home/nea/ensemble-src-wt-obligation
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
  ENSEMBLE_SELF_ENV=dev PORT=8079 \
  timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest \
  "tests/unit/services/test_report_obligation_emission_pins.py::TestWidenedLivenessWalk" \
  --tb=short -q > /tmp/wi1f-g9disp-probes/pins.log 2>&1
echo "exit=$?"
