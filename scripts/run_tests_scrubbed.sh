#!/usr/bin/env bash
# Env-scrub wrapper for running ensemble-src test suites.
#
# Required pattern (per the 2 prior live-probe incidents — most recently
# 2026-09-26): POSTGRES_* / POSTGRES_URL / DATABASE_URL_POSTGRES must
# NEVER reach a test invocation. Bare ``source`` under /bin/sh silently
# no-ops the unset; this wrapper is a standalone bash process and
# echo-verifies ZERO survivors before any subprocess is spawned.
#
# Usage:  ./scripts/run_tests_scrubbed.sh <pytest-args...>
#
# Examples:
#   ./scripts/run_tests_scrubbed.sh tests/unit/services/test_maintenance_checkpoint_cleanup_service.py -k TestExpectedBytesBigInteger
#   ./scripts/run_tests_scrubbed.sh tests/unit/services/test_maintenance_checkpoint_cleanup_service.py
#
# Hard-coded here (NOT opt-in via env): we always scrub, never carry
# ambient POSTGRES_* through. The bash unset is built-in; the verify
# gate fails LOUD if any unset didn't take.

set -euo pipefail

# 1. Scrub every variant the project warns about. Unset is a no-op
#    when the var is absent, so this is safe to run unconditionally.
unset POSTGRES_HOST
unset POSTGRES_DB
unset POSTGRES_USER
unset POSTGRES_PASSWORD
unset POSTGRES_PORT
unset POSTGRES_URL
unset DATABASE_URL_POSTGRES

# 2. Echo-verify ZERO survivors. The exit code is the bash contract:
#    if ANY scrubbed var is still set, fail loud BEFORE running tests
#    (the 2 prior live-probe incidents both rooted in this step being
#    skipped — the wrapper is load-bearing, not advisory).
LEAKED=0
for VAR in POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD \
           POSTGRES_PORT POSTGRES_URL DATABASE_URL_POSTGRES; do
    if [ -n "${!VAR:-}" ]; then
        echo "ENV-SCRUB FAILURE: $VAR is still set to '${!VAR}'" >&2
        LEAKED=1
    fi
done
if [ "$LEAKED" -ne 0 ]; then
    echo "Refusing to run tests with leaked POSTGRES_* vars." >&2
    exit 78  # EX_CONFIG — mirrors lib.sh exit-78 convention
fi

# 3. Run the venv pytest with the supplied args. The cwd must stay
#    inside the repo so pytest discovers pyproject.toml.
cd "$(dirname "$0")/.."
exec .venv/bin/pytest "$@"
