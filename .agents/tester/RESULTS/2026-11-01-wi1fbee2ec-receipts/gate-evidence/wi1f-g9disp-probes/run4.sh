#!/bin/bash
cd /home/nea/ensemble-src-wt-obligation
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
  ENSEMBLE_SELF_ENV=dev PORT=8079 \
  timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest \
  "tests/integration/test_attestation_live_descendants.py::TestFacadeBfsCap::test_terminal_set_excludes_completed_terminated_error_failed" \
  "tests/integration/test_attestation_live_descendants.py::TestBusyClassification::test_terminal_descendant_not_live_not_busy" \
  "tests/integration/test_attestation_live_descendants.py::TestBusyClassification::test_idle_with_unprocessed_message_live_not_busy" \
  --tb=short -q > /tmp/wi1f-g9disp-probes/terminal-polarity.log 2>&1
echo "exit=$?"
