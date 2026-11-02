#!/bin/bash
set -u
WT="$1"; SHARD="$2"; OUT="$3"
cd "$WT" || exit 99
FAKEHOME=$(mktemp -d)
mapfile -t NODEIDS < "$SHARD"
env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD -u PGSERVICE -u PGOPTIONS -u ENSEMBLE_SELF_ENV -u ENSEMBLE_UPGRADE_LIVE -u ENSEMBLE_UPGRADE_SCRIPTS_DIR -u ENSEMBLE_RESTART_UNIT -u DAEMON_PORT \
  OPENAI_BASE_URL=http://127.0.0.1:9/v1 \
  OPENAI_BASE_URL_BACKUP=http://127.0.0.1:9/v1 \
  OPENAI_REQUEST_TIMEOUT=5 \
  QUEUE_LLM_RETRY_TRANSIENT_ATTEMPTS=1 \
  QUEUE_LLM_RETRY_TIMEOUT_ATTEMPTS=1 \
  PYTHONDONTWRITEBYTECODE=1 \
  ENSEMBLE_SELF_ENV=dev \
  HOME="$FAKEHOME" \
  timeout 1500 .venv/bin/python -m pytest -p no:cacheprovider -p no:randomly -n auto \
  --timeout=120 --override-ini="timeout=120" \
  "${NODEIDS[@]}" -q --tb=no -rEf > "$OUT" 2>&1
rc=$?
rm -rf "$FAKEHOME"
echo "RC=$rc" >> "$OUT"
exit $rc
