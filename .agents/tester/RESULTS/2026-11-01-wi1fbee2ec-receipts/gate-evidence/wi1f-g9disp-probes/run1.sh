#!/bin/bash
cd /home/nea/ensemble-src-wt-obligation
env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u DATABASE_URL -u PORT \
  ENSEMBLE_SELF_ENV=dev PORT=8079 \
  timeout 300 /home/nea/ensemble-src/.venv/bin/python -m pytest \
  "tests/integration/test_attestation_idle_orphan_incident.py::test_b08f40fe_idle_orphan_tree_denies_instead_of_branch5_allow" \
  "tests/integration/test_attestation_incident_acceptance_lca.py::test_lca_b08f40fe_phase1_incident_phrase_denies_no_silent_completion" \
  --tb=long -q > /tmp/wi1f-g9disp-probes/b08f40fe-pair.log 2>&1
echo "exit=$?"
