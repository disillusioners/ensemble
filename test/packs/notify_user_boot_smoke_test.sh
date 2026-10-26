#!/usr/bin/env bash
# Test Pack: notify_user boot smoke — the B1 RULED end-to-end sequence
#             (ari-notify-user commission, 2026-10-25, base 7af1d2632d).
# Timeout: 10 minutes (600s) — a REAL daemon boot + scratch PG is minutes.
#
# WHAT IT PROVES (the pinned B1 sequence, executed — not just prepared):
#   scratch TELEGRAM source_configs row (real adapter code, real registry
#   build) -> the scratch aiohttp sink answers getMe -> the source reaches
#   SourceStatus.RUNNING -> the pre-RUNNING refusal is asserted first as
#   the NEGATIVE case (notify_source_disabled) -> the source="scheduler"
#   alarm-shaped wake rides the REAL manager.enqueue_message_job primitive
#   -> the Ari turn runs on the scripted mock LLM and emits the
#   notify_user tool call (no real-LLM) -> the tool executes through the
#   REAL resolution chain + adapter send -> the sink receives EXACTLY ONE
#   sendMessage with the expected payload -> notify_audit rows land
#   (refused + the accepted->dispatched terminal) -> teardown clean,
#   cgroup-isolated from the live daemon, the scratch PG database DROPPED.
#
# The base-URL redirection happens WITHOUT production-code change: a
# scratch sitecustomize.py shim (PYTHONPATH, transient unit ONLY) patches
# the Telegram adapter's TELEGRAM_API_BASE module constant at import
# time — the smallest seam (B1: NOT a whole-file shadow copy).
#
# REQUIREMENTS (the tester lane provisions):
#   ENSEMBLE_NOTIFY_SMOKE=1        (the module gate)
#   ENSEMBLE_NOTIFY_SMOKE_UNIT=1   (the transient-unit gate)
#   PG_TEST_HOST/PORT/DB/USER/PASSWORD — a scratch PG16 server; the pack
#   creates a uniquely-named database and DROPS it at teardown (verified:
#   zero notify_smoke_% databases remain after the run).
#
#   The smoke flags are the CALLER's contract: the pack FAILS LOUD
#   (exit 1, the fence message below) when they are missing, because the
#   flags are forced inside the pytest subprocess — a bare run would
#   otherwise SKIP with exit 0, a false-PASS shape (the smoke never ran).
#
# FENCES (binding):
#   * per-var env scrub of POSTGRES_* before launch (the env -u POSTGRES_*
#     glob is a SILENT NO-OP — never trusted as a fence);
#   * the transient unit carries explicit scratch env only (FAKE_HOME,
#     scratch data dir, scratch PG, mock-LLM base URL);
#   * the live daemon's identity (MainPID/epoch/NRestarts) is snapshotted
#     before and asserted unchanged after;
#   * never touches the live daemon, port 9797, or the main checkout.
#
# NOTE: the transient unit's tmp artifacts are mode-restricted after the
# systemd-run teardown (the same property as the wedge boot smoke) —
# pytest's tmp-dir garbage collection logs PermissionError warnings.
# They are cosmetic; the pack reads the observations INSIDE the run.

set -uo pipefail

# ─── SSL cleanup ──────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: notify_user boot smoke ==="

cd "$PROJECT_DIR"

if [ ! -x ".venv/bin/pytest" ]; then
  echo "FENCE: no .venv in the worktree — run 'uv sync' first"
  echo "RESULT: FAIL"
  exit 1
fi

# The caller MUST provision the smoke flags. They are forced INSIDE the
# pytest subprocess below, so without this gate a caller that simply ran
# the pack would get "SKIP" + exit 0 — a FALSE-PASS shape (the smoke
# never ran, but the pack looked green). Missing flags are a FENCE
# breach: fail loud, naming exactly what this gate requires.
for gate in ENSEMBLE_NOTIFY_SMOKE ENSEMBLE_NOTIFY_SMOKE_UNIT; do
  if [ "${!gate:-}" != "1" ]; then
    echo "FENCE: $gate is not '1' (value: '${!gate:-<unset>}') — the notify_user boot smoke is opt-in and the CALLER must provision the smoke flags:"
    echo "  ENSEMBLE_NOTIFY_SMOKE=1        (the module gate)"
    echo "  ENSEMBLE_NOTIFY_SMOKE_UNIT=1   (the transient-unit gate)"
    echo "  plus the scratch PG16 server: PG_TEST_HOST / PG_TEST_PORT / PG_TEST_DB / PG_TEST_USER / PG_TEST_PASSWORD"
    echo "RESULT: FAIL"
    exit 1
  fi
done

for var in PG_TEST_HOST PG_TEST_PORT PG_TEST_DB PG_TEST_USER PG_TEST_PASSWORD; do
  if [ -z "${!var:-}" ]; then
    echo "FENCE: $var is not set — the smoke needs a scratch PG16 server"
    echo "RESULT: FAIL"
    exit 1
  fi
done

# Per-var scrub of the ambient POSTGRES_* family — the daemon must see
# ONLY the scratch values the test passes explicitly. The explicit
# `env -u ...` prefix on the pytest invocation below is the
# load-bearing scrub (verbose on purpose — the `env -u POSTGRES_*`
# glob idiom is a silent NO-OP and is never trusted as a fence).

timeout 590 env \
  -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_URL \
  PG_TEST_HOST="$PG_TEST_HOST" \
  PG_TEST_PORT="$PG_TEST_PORT" \
  PG_TEST_DB="$PG_TEST_DB" \
  PG_TEST_USER="$PG_TEST_USER" \
  PG_TEST_PASSWORD="$PG_TEST_PASSWORD" \
  ENSEMBLE_NOTIFY_SMOKE=1 \
  ENSEMBLE_NOTIFY_SMOKE_UNIT=1 \
  .venv/bin/pytest \
  tests/integration/test_notify_user_boot_smoke.py \
  --override-ini="addopts=" \
  --tb=short -q -p no:warnings 2>&1

EXIT_CODE=$?

# ── scratch-PG hygiene: the fixture drops its own DB; a killed run can
#    orphan one. The pack reports (and cleans) any leftover.
LEFTOVER="$(PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d postgres -Atc \
  "select datname from pg_database where datname like 'notify_smoke_%';" 2>/dev/null || true)"
if [ -n "$LEFTOVER" ]; then
  echo "CLEANUP: dropping orphaned scratch database(s): $LEFTOVER"
  for db in $LEFTOVER; do
    PGPASSWORD="$PG_TEST_PASSWORD" psql -h "$PG_TEST_HOST" -p "$PG_TEST_PORT" -U "$PG_TEST_USER" -d postgres \
      -c "DROP DATABASE IF EXISTS \"$db\" WITH (FORCE);" >/dev/null 2>&1 || true
  done
fi

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
