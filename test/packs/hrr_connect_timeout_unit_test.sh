#!/usr/bin/env bash
# test/packs/hrr_connect_timeout_unit_test.sh
#
# Pack: hrr_connect_timeout_unit_test
# Scope: hard-reboot recovery (feature/hard-reboot-recovery) — Phase 1 / AC1-AC4,
#        the PostgreSQL CONNECT-BUDGET pins (G6).
#
# Files covered:
#   tests/unit/test_connect_timeout.py          (9 tests, verified via
#                                                --collect-only on 2026-10-21)
#     (a)  create_postgres_engine binds connect_timeout=BOOT_DB_TIMEOUT_S on
#          the URL query, and the UTC-session connect_args slot stays intact
#     (b)  firewall-DROP semantics — the bound reaches libpq. The DRIVER
#          connect is MOCKED (PGDialect_psycopg.connect), so there is no
#          real network and no real 75-130s TCP wait
#     (c)  the boot preflight lane is REUSED, not duplicated: exactly ONE
#          "SELECT 1" probe per attempt (sqlalchemy.create_engine is mocked)
#     (d)+(e) the systemd unit template orders AFTER postgresql.service and
#              declares NO Wants= / Requires= on it (a remote-PG host must not
#              be forced to start a local PG)
#     (f)  ONLY the canonical PG engine carries the bound — the SQLite branch
#          and the dialect-agnostic branch stay free of connect_timeout
#     (g)  a URL-special-character password keeps the bound AND the intended
#          host/port/database (the old f-string form mangled the host)
#     (h1) the preflight URL is byte-identical to the pre-change string form
#     (h2) a special-character password no longer corrupts the HOST
#     (h3) the preflight rides the bound on connect_args, NOT the URL query
#
#   Shared harness (house convention, no byte-copied harness trio):
#     tests/helpers/hard_reboot_fixtures.py — the PG-coordinate neutraliser
#     `no_pg_env` (deletes POSTGRES_HOST/PORT/DB/USER/PASSWORD) plus the
#     fake PG config (127.0.0.1:15432/ensemble_test — a clearly-fake
#     coordinate that is never connected to).
#
# ── DUAL-LAYER TIMEOUT ───────────────────────────────────────────────────────
#   Layer 1 (outer): `timeout 300 bash test/packs/hrr_connect_timeout_unit_test.sh`
#   Layer 2 (inner): the pytest invocation is ITSELF wrapped in `timeout 100s`
#                     (a hang can never silently eat the cap), PLUS a
#                     script-internal self-watchdog at 110s that SIGTERMs the
#                     whole run group. Unit-pack budget: <= 2 min (house
#                     test-pack contract). Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# ── ENV-POISON FENCE ─────────────────────────────────────────────────────────
#   The ambient shell carries the LIVE coordinates (POSTGRES_HOST=10.44.0.2 /
#   POSTGRES_DB=ensemble_prod / ENSEMBLE_SELF_ENV=live + a live password).
#   Every command that can reach a DB or spawn pytest runs through `scrub`:
#     1) it unsets every POSTGRES_*/libpq/SSL var IN THE CHILD ENV (house
#        scrub list — scripts/run_tests_scrubbed.sh + SSL_CERT_FILE/DIR);
#     2) it evaluates the fence assertion INSIDE that scrubbed child env
#        (asserting in the PARENT shell spuriously fires — the parent still
#        carries ensemble_prod, so the assertion would be meaningless there);
#     3) it then hands off to the house canonical wrapper
#        scripts/run_tests_scrubbed.sh, which adds the FAMILY-WIDE leak
#        verification (ANY POSTGRES_* spelling, not an enumerated list).
#   A negative self-test PROVES the fence refuses a deliberate ensemble_prod
#   pointer BEFORE the real run starts.
#
# ── TRANSPARENCY ─────────────────────────────────────────────────────────────
#   No deselection, no -x, no skip, no markers added: the whole file runs.
#   `--override-ini="addopts="` clears the repo-wide marker filter
#   ("-m 'not integration and not postgres and not slow'") so the pack is
#   marker-agnostic; neither file carries an integration/postgres/slow marker.
#
# ── NO EXTERNAL SERVICES ─────────────────────────────────────────────────────
#   No running daemon, no real LLM endpoint, no real PostgreSQL: every DB
#   touch is either a lazy (never-connected) engine, a mocked driver connect,
#   a mocked sqlalchemy.create_engine, or a file read.
#
# Run:  timeout 300 bash test/packs/hrr_connect_timeout_unit_test.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

PACK_NAME="hrr_connect_timeout_unit_test"
TEST_FILE="tests/unit/test_connect_timeout.py"
PYTEST_TIMEOUT_S=100
WATCHDOG_S=110

OUT="$(mktemp)"
WATCHDOG_PID=""
EXIT_CODE=0

echo "=== Test Pack: ${PACK_NAME} ==="

cd "$PROJECT_DIR" || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# ── ENV-POISON FENCE ─────────────────────────────────────────────────────────
# The full house scrub list (scripts/run_tests_scrubbed.sh) plus the two
# SSL vars several house packs unset (rthook_ssl.py sets them for the
# PyInstaller bundle; a stale bundle CA path would be inherited otherwise).
SCRUB_VARS=(
    POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT
    POSTGRES_URL DATABASE_URL_POSTGRES
    PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE
    PGSSLMODE PGSERVICE PGSERVICEFILE
    SSL_CERT_FILE SSL_CERT_DIR
    ENSEMBLE_UPGRADE_LIVE ENSEMBLE_RESTART_UNIT
)

ENV_UNSET=()
for _v in "${SCRUB_VARS[@]}"; do
    ENV_UNSET+=(-u "$_v")
done

# The fence ASSERTION — evaluated inside the scrubbed child env.
FENCE_SNIPPET='[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH: POSTGRES_DB=${POSTGRES_DB} points at the LIVE database"; exit 1; }; [ "${POSTGRES_HOST:-}" != "10.44.0.2" ] || { echo "FENCE BREACH: POSTGRES_HOST=${POSTGRES_HOST} points at the LIVE PG host"; exit 1; }; exec "$@"'

# scrub <cmd> [args...] — scrubbed env + fence + house canonical wrapper.
scrub() {
    env "${ENV_UNSET[@]}" ENSEMBLE_SELF_ENV=dev \
        sh -c "${FENCE_SNIPPET}" sh "$@"
}

# Negative proof: point the scrubbed env AT ensemble_prod and require the
# fence to refuse (non-zero exit + "FENCE BREACH"). Runs BEFORE the real run.
FENCE_SELFTEST_OUT="$(env "${ENV_UNSET[@]}" POSTGRES_DB=ensemble_prod \
    sh -c "${FENCE_SNIPPET}" sh true 2>&1)"
FENCE_SELFTEST_RC=$?
if [ "$FENCE_SELFTEST_RC" -ne 0 ] && printf '%s' "$FENCE_SELFTEST_OUT" | grep -q "FENCE BREACH"; then
    echo "[env-poison fence] self-test REFUSED a deliberate ensemble_prod pointer (rc=${FENCE_SELFTEST_RC}) — fence armed"
else
    echo "[env-poison fence] self-test did NOT refuse (rc=${FENCE_SELFTEST_RC}, output=${FENCE_SELFTEST_OUT})"
    echo "FAIL: the env-poison fence is not armed — refusing to run."
    echo "RESULT: FAIL"
    exit 1
fi
# Report the REAL scrubbed child env (never the parent — the parent still
# carries the live coordinates). Only non-secret names are printed.
SCRUB_STATE="$(scrub sh -c 'echo "POSTGRES_DB=${POSTGRES_DB:-<unset>} POSTGRES_HOST=${POSTGRES_HOST:-<unset>} ENSEMBLE_SELF_ENV=${ENSEMBLE_SELF_ENV:-<unset>}"')"
echo "[env-poison fence] scrubbed child env (asserted in the CHILD, not the parent): ${SCRUB_STATE}"
echo

# ── LAYER 2 — internal self-watchdog ─────────────────────────────────────────
# Backstop: if the pytest wrapper itself ever fails to return, this SIGTERMs
# the whole run group at 110s and the TERM trap reports RESULT: TIMEOUT (124).
on_term() {
    echo "[watchdog] INTERNAL WATCHDOG ${WATCHDOG_S}s EXCEEDED — SIGTERM to the run group"
    if [ -f "$OUT" ]; then
        echo "--- captured output (partial) ---"
        cat "$OUT"
        echo "--- end captured output ---"
    fi
    echo "RESULT: TIMEOUT"
    exit 124
}
trap on_term TERM

cleanup() {
    if [ -n "${WATCHDOG_PID}" ]; then
        kill -TERM "$WATCHDOG_PID" 2>/dev/null
        wait "$WATCHDOG_PID" 2>/dev/null
    fi
    rm -f "$OUT"
    return 0
}
trap cleanup EXIT

# ── RUN ──────────────────────────────────────────────────────────────────────
PYTEST_ARGS=(
    "$TEST_FILE"
    --override-ini="addopts="
    --tb=short
    -q
)

# Start the internal self-watchdog (Layer 2 backstop). It sleeps the full
# 110s budget and then SIGTERMs the whole run group; the TERM trap above
# turns that into RESULT: TIMEOUT / exit 124. The nested `sleep` is killed
# explicitly so no orphan survives a fast pass.
(
    sleep "$WATCHDOG_S" &
    _SPID=$!
    trap 'kill -TERM "$_SPID" 2>/dev/null; exit 0' TERM
    wait "$_SPID" || exit 0
    echo "[watchdog] INTERNAL WATCHDOG ${WATCHDOG_S}s EXCEEDED — SIGTERM to the run group" >&2
    kill -TERM "-$$" 2>/dev/null || kill -TERM "$$" 2>/dev/null
) &
WATCHDOG_PID=$!

echo "Running: .venv/bin/pytest ${PYTEST_ARGS[*]}"
echo

# `timeout` needs an external command, so the scrub wrapper is built as an
# argv array rather than a shell function.
SCRUB_CMD=(env "${ENV_UNSET[@]}" ENSEMBLE_SELF_ENV=dev
    sh -c "${FENCE_SNIPPET}" sh ./scripts/run_tests_scrubbed.sh)

timeout "${PYTEST_TIMEOUT_S}s" "${SCRUB_CMD[@]}" "${PYTEST_ARGS[@]}" 2>&1 | tee "$OUT" || EXIT_CODE=$?

# Stop the self-watchdog now that the run has returned.
if [ -n "${WATCHDOG_PID}" ]; then
    kill -TERM "$WATCHDOG_PID" 2>/dev/null
    wait "$WATCHDOG_PID" 2>/dev/null
    WATCHDOG_PID=""
fi

echo
if [ "$EXIT_CODE" -eq 124 ]; then
    echo "Pack TIMEOUT: the pytest invocation hit the ${PYTEST_TIMEOUT_S}s inner cap."
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$EXIT_CODE" -eq 5 ]; then
    echo "Pack FAILED: pytest collected no tests from ${TEST_FILE} (exit 5)."
    echo "RESULT: FAIL"
    exit 1
elif [ "$EXIT_CODE" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "Pack FAILED: pytest exited ${EXIT_CODE}."
    echo "RESULT: FAIL"
    exit 1
fi
