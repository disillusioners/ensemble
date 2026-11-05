#!/usr/bin/env bash
# Test Pack: memoir_embedding_worker_unit_test
#
# Memoir Store v1 — phase 3 (plan:
# .agents/shared/planning/ari-memoir-store/phase3-plan.md AC1/AC3/AC4/
# AC5/AC6; amendments AM-28..AM-36).
#
# The pack runs the worker's unit suite AND proves FIVE mutation legs —
# each defect is PLANTED in the source, the TARGET node must go RED,
# the source is RESTORED, and the full suite must be GREEN again:
#
#   M1  asyncio.to_thread substituted for the dedicated executor
#       ⇒ the thread-prefix pin goes RED (AM-29)
#   M2  a write-back failure not counted as an attempt
#       ⇒ the AM-30 write-back test goes RED
#   M3  401 treated as transient ⇒ the dead-credential test goes RED
#       (no immediate terminal, no first-occurrence latch, AM-32)
#   M4  the latch threshold 3 → 1 ⇒ the latch boundary test goes RED
#   M5  the tick line's M reporting this-tick instead of the TOTAL
#       ⇒ the tick-line honesty test goes RED (D3.11)
#
# EXIT-CODE CONTRACT (the house shape): pack 0 = PASS · 1 = FAIL.
# GREEN-AT-ZERO IS A FAILURE: every mutated run must produce at least
# one FAILED node id (captured from pytest -rfE), and the pack FAILS if
# a mutation does not bite.
#
# FENCES: the per-variable env scrub (the `POSTGRES_*` glob form is a
# SILENT NO-OP — bash does not glob env names), `-p no:randomly`, the
# `--override-ini` addopts clear, and the dual-layer timeout. The suite
# itself runs on throwaway SQLite only — the scrub is the standing
# fence against the ambient env (it points at LIVE on this host).
#
# Interpreter resolution (house convention, metrics_read precedent):
# MEMOIR_PACK_PYTHON wins, then the worktree-local .venv — and the pack
# FAILS LOUD on a missing interpreter. It NEVER falls back to bare
# `python3`.
set -uo pipefail

PACK_START=${SECONDS:-0}
PACK_BUDGET_S=1500

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

# ── interpreter (FAIL LOUD, never bare python3) ─────────────────────────
PY="${MEMOIR_PACK_PYTHON:-}"
if [ -z "$PY" ] && [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
fi
if [ -z "$PY" ] || [ ! -x "$PY" ]; then
  echo "RESULT: FAIL (MISSING-INTERPRETER — set MEMOIR_PACK_PYTHON to the"
  echo "        venv python; this worktree has no .venv of its own and a"
  echo "        bare-python3 fallback is banned)"
  exit 1
fi
echo "=== Test Pack: memoir_embedding_worker_unit_test ==="
echo "Interpreter: $PY"

TEST_FILE="tests/unit/test_memoir_embedding_worker_unit.py"
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG_DIR="${MEMOIR_EMBED_LOG_DIR:-/tmp/memoir-embed-gate}"
mkdir -p "$LOG_DIR"

# Per-variable scrub — `env -u POSTGRES_*` is a SILENT NO-OP.
SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT)

run_suite() {
  # $1 = label ; $2... = extra pytest args. Prints the exit code.
  local label="$1"; shift
  echo "--- ${label} ---"
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest "$@" \
    -q --tb=short -p no:randomly --override-ini="addopts=" 2>&1
  local rc=$?
  echo "--- ${label} exit=${rc} ---"
  return ${rc}
}

WORKER_PY="daemon/services/memoir_embedding_worker.py"

# ── Leg 1: the baseline suite is GREEN ───────────────────────────────────
run_suite "leg 1 baseline (full suite)" "${TEST_FILE}"
BASE_RC=$?
if [ ${BASE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (baseline suite is not green: rc=${BASE_RC})"
  exit 1
fi

# ── the mutation harness ─────────────────────────────────────────────────
# The backup lives for the WHOLE pack: every leg restores from the SAME
# pristine copy (a per-restore delete would strand legs 2..N with a
# vanished backup and let the mutations accumulate).
BACKUP="$(mktemp -t memoir-embed-mutation-XXXXXX.py)"
cp "${WORKER_PY}" "${BACKUP}"
restore() {
  cp "${BACKUP}" "${WORKER_PY}"
}
cleanup() {
  rm -f "${BACKUP}"
}
trap 'restore 2>/dev/null; cleanup' EXIT INT TERM

# plant <name> — apply one mutation via an exact-string replacement
# (count==1 guard); aborts the pack when the anchor drifts.
plant() {
  local name="$1" old="$2" new="$3"
  "${PY}" - "${WORKER_PY}" "${name}" "${old}" "${new}" <<'PLANT'
import sys

path, name, old, new = sys.argv[1:5]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
if text.count(old) != 1:
    sys.exit(
        f"MUTATION PLANT FAILED ({name}): expected exactly one occurrence, "
        f"found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new))
print(f"mutation planted: {name}")
PLANT
  return $?
}

# run_mutation <name> <target-node> — the TARGET node must go RED and
# its FAILED id must be captured (-rfE).
run_mutation() {
  local name="$1" target="$2"
  local log="$LOG_DIR/${name}.log"
  echo "--- mutation leg ${name}: target ${target} must go RED ---"
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest "${TEST_FILE}::${target}" \
    -q --tb=line -p no:randomly --override-ini="addopts=" -rfE >"${log}" 2>&1
  local rc=$?
  echo "--- ${name} mutated exit=${rc} ---"
  if [ ${rc} -eq 0 ]; then
    echo "RESULT: FAIL (${name}: the mutation did NOT turn the target RED"
    echo "        — the pin is not mutation-meaningful)"
    exit 1
  fi
  if [ ${rc} -eq 124 ]; then
    echo "RESULT: FAIL (${name}: the mutated run TIMED OUT instead of failing)"
    exit 1
  fi
  local failed_ids
  failed_ids=$(grep -E "^FAILED " "${log}" | sed 's/^FAILED //' || true)
  if [ -z "${failed_ids}" ]; then
    echo "RESULT: FAIL (${name}: no FAILED node id captured — vacuous red)"
    exit 1
  fi
  echo "MUTATION LEG ${name}: RED as required; FAILED node id(s):"
  echo "${failed_ids}" | sed 's/^/    /'
}

# ── M1: to_thread substitution ⇒ thread-prefix pin RED ──────────────────
plant "M1-to-thread" \
"            future = loop.run_in_executor(
                _EMBED_EXECUTOR,
                lambda: self._embedder(" \
"            future = asyncio.to_thread(
                lambda: self._embedder(" || exit 1
run_mutation "M1-to-thread" "test_embeds_run_on_the_memoir_embed_thread_prefix"
restore

# ── M2: write-back failure not counted ⇒ AM-30 test RED ─────────────────
plant "M2-write-back-not-an-attempt" \
'        except Exception as exc:  # noqa: BLE001 — AM-30: the write-back counts
            return await self._record_failure(
                row,
                f"embedding_write_back_failed: {exc}",
                immediate_terminal=False,
            )' \
'        except Exception as exc:  # noqa: BLE001 — AM-30: the write-back counts
            return "embedding_write_back_failed"' || exit 1
run_mutation "M2-write-back-not-an-attempt" "test_write_back_failure_counts_as_an_attempt"
restore

# ── M3: 401 treated as transient ⇒ dead-credential test RED ─────────────
plant "M3-401-transient" \
'            return ("embedding_auth_rejected", True)' \
'            return ("embedding_auth_rejected", False)' || exit 1
run_mutation "M3-401-transient" "test_401_is_terminal_at_once_and_latches_that_tick"
restore

# ── M4: the latch threshold 3 → 1 ⇒ latch boundary test RED ─────────────
plant "M4-latch-threshold" \
"LATCH_THRESHOLD_TICKS = 3" \
"LATCH_THRESHOLD_TICKS = 1" || exit 1
run_mutation "M4-latch-threshold" "test_latch_slows_the_tick_interval"
restore

# ── M5: tick-line M reports this-tick ⇒ honesty test RED ────────────────
plant "M5-tickline-this-tick" \
'            f"errors={errors} terminal={terminal_total} "' \
'            f"errors={errors} terminal={errors} "' || exit 1
run_mutation "M5-tickline-this-tick" "test_tick_line_m_is_the_total_terminal_not_this_tick"
restore

# ── the restore proof: byte-identity + the FULL suite green again ────────
if ! cmp -s "${WORKER_PY}" "${BACKUP}"; then
  echo "RESULT: FAIL (the worker module is not byte-identical to the"
  echo "        pristine copy after the restore — a plant leaked)"
  cleanup
  exit 1
fi
run_suite "final restored (full suite)" "${TEST_FILE}"
FINAL_RC=$?
if [ ${FINAL_RC} -ne 0 ]; then
  echo "RESULT: FAIL (the suite is not green after the restore: rc=${FINAL_RC})"
  cleanup
  exit 1
fi

cleanup
trap - EXIT INT TERM

echo "PACK DURATION: $((SECONDS - PACK_START))s"
echo "RESULT: PASS"
exit 0
