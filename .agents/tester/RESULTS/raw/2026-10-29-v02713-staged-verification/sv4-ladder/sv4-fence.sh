#!/usr/bin/env bash
# sv4-fence.sh — the OUTER fence for every SV4 command.
#
# The ambient env on this host is PROD-SHAPED (POSTGRES_DB=ensemble_prod,
# POSTGRES_HOST=10.44.0.2, ENSEMBLE_SELF_ENV=live). The ladder/fence
# scripts scrub POSTGRES_{DB,USER,PASSWORD,HOST,PORT} + DAEMON_PORT
# internally, but NOT DATABASE_URL / SSL_CERT_* — those are scrubbed HERE.
#
# PER-VARIABLE scrub only: `env -u POSTGRES_*` is a SILENT NO-OP (bash does
# not glob env names) — house convention.
export PG_TEST_HOST=127.0.0.1
export PG_TEST_PORT=5462
export PG_TEST_DB=dbpool_sv4
export PG_TEST_USER=dbpool_sv4
export PG_TEST_PASSWORD=dbpool_sv4_pw
# THE harness-critical allowlist: tests/postgres/conftest.py enforces an
# EXACT-name scratch-DB allowlist and refuses anything undeclared.
export PG_TEST_ALLOWED_DBS=dbpool_sv4
export ENSEMBLE_SELF_ENV=dev
export SV4_WORKTREE=/home/nea/ensemble-src-wt-stage-v02713
export SV4_PY=/home/nea/ensemble-src/.venv/bin/python

sv4_env() {
  env -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD \
      -u POSTGRES_HOST -u POSTGRES_PORT -u DAEMON_PORT \
      -u DATABASE_URL -u SSL_CERT_FILE -u SSL_CERT_DIR \
      PG_TEST_HOST="$PG_TEST_HOST" PG_TEST_PORT="$PG_TEST_PORT" \
      PG_TEST_DB="$PG_TEST_DB" PG_TEST_USER="$PG_TEST_USER" \
      PG_TEST_PASSWORD="$PG_TEST_PASSWORD" \
      PG_TEST_ALLOWED_DBS="$PG_TEST_ALLOWED_DBS" \
      ENSEMBLE_SELF_ENV=dev "$@"
}
