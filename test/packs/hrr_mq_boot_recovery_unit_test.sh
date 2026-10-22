#!/usr/bin/env bash
# test/packs/hrr_mq_boot_recovery_unit_test.sh
#
# Pack: hrr_mq_boot_recovery_unit_test
# Scope: hard-reboot recovery (feature/hard-reboot-recovery) — Phase 2 / G2,
#        the MESSAGE-QUEUE BOOT-RECOVERY pass.
#
# Files covered:
#   tests/unit/test_message_queue_boot_recovery.py   (21 tests, verified via
#                                                     --collect-only on 2026-10-21)
#     M1  — boot-epoch-relative selection: a row claimed SECONDS before the
#           power loss (the common hard-kill shape, which the 3600s age
#           predicate misses) IS selected; a row touched AFTER the boot is not
#     M2  — the NULL-last_activity_at branch is selected too
#     M3  — the skip-guards: a row on a TERMINAL instance (revive hazard) and
#           a row backed by a RUNNING/PAUSED task (AutoContinue owns it) are
#           left untouched
#     M4  — the reset is a guarded CAS: the row flips to 'ready' and a second
#           pass is a no-op (reboot-loop idempotency)
#     M5  — the post-pass accounting: the REAL pre/post get_stats() counts and
#           the busy-guard's literal predicate on those REAL post-pass values
#     M6  — the kill-switch (ENSEMBLE_HARD_REBOOT_BOOT_PASS=0) skips the pass
#     M7  — never-wedge: a raising repository does not propagate out of the pass
#     D19 — the lane-6 composition is MUTUALLY EXCLUSIVE (the pass and the
#           RDRS lane 6 never co-hold on one row), pinned before and after
#           the pass plus the controls proving the fixture IS lane-6-eligible
#
#   Code under test:
#     daemon/services/message_queue_boot_recovery.py
#     daemon/repositories/message_queue/repository.py
#   Shared harness (house convention, no byte-copied harness trio):
#     tests/helpers/hard_reboot_fixtures.py — REAL file-backed SQLite
#     (NullPool + per-connection PRAGMAs; NOT ":memory:" — the pass opens
#     more than one connection) plus the row seeds.
#
# ── NO EXTERNAL SERVICES ─────────────────────────────────────────────────────
#   No running daemon, no real LLM endpoint, no real PostgreSQL: the harness
#   is a per-test tmp_path SQLite FILE. The scrub below is still MANDATORY
#   (the ambient shell carries the live PG coordinates, and the repo's
#   factory resolves POSTGRES_* with precedence over the config).
#
# ── DUAL-LAYER TIMEOUT ───────────────────────────────────────────────────────
#   Layer 1 (outer): `timeout 300 bash test/packs/hrr_mq_boot_recovery_unit_test.sh`
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
# Run:  timeout 300 bash test/packs/hrr_mq_boot_recovery_unit_test.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

PACK_NAME="hrr_mq_boot_recovery_unit_test"
TEST_FILE="tests/unit/test_message_queue_boot_recovery.py"
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
