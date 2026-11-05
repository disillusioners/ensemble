#!/usr/bin/env bash
# Test Pack: memoir_tools_unit_test
#
# Memoir Store v1 — phase 4 (plan:
# .agents/shared/planning/ari-memoir-store/phase4-plan.md AC1 / AC5 / AC7).
#
# The pack runs the tool suite
#   tests/unit/test_memoir_tools_unit.py
# (the enforced gate order, the kill-switch, the typed refusal matrix,
# the write/pin/unpin behaviors, the verbatim budget footer, the
# timeline signature, the write purity) and proves FIVE mutation legs:
#
#   M1  the gate order inverted (the knob read ahead of the auth gate)
#       ⇒ the behavioral gate-order test goes RED (AM-16)
#   M2  the actor made optional (the keepsake actor-required branch
#       disabled) ⇒ the keepsake-without-actor refusal changes
#   M3  the admission bypassed at the tool layer (the write drops the
#       keepsake flag) ⇒ the keepsake claim test goes RED
#   M4  the budget FINAL RE-CHECK removed (the service's own guard)
#       ⇒ the post-recheck footer test goes RED
#   M5  an inline embed planted in the write body ⇒ the purity pin
#       goes RED (AM-12/D4.11)
#
# Every leg: plant → run → capture the RED node IDs → restore → re-run
# GREEN. The trap restores byte-identically even on an early failure,
# and the pack ends with a byte-identity gate.
#
# EXIT-CODE CONTRACT (the house shape): pack 0 = PASS · 1 = FAIL.
#
# FENCES: per-variable env scrub (the `env -u POSTGRES_*` glob form is a
# SILENT NO-OP — bash does not glob env names), `-p no:randomly`,
# addopts clear, dual-layer timeout. The suite runs on throwaway SQLite
# only; no manager is ever constructed; no PG is touched.
#
# Interpreter: MEMOIR_PACK_PYTHON wins, then the worktree-local .venv;
# FAIL LOUD on a missing interpreter — never bare python3.
set -uo pipefail

PACK_START=${SECONDS:-0}
PACK_BUDGET_S=900

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

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
echo "=== Test Pack: memoir_tools_unit_test ==="
echo "Interpreter: $PY"

TEST_FILE="tests/unit/test_memoir_tools_unit.py"
TOOL_PY="daemon/tools/memoir.py"
SERVICE_PY="daemon/services/memoir_retrieval.py"
TIMEOUT_S="${MEMOIR_PACK_TIMEOUT_S:-300}"
LOG_DIR="${MEMOIR_TOOLS_LOG_DIR:-/tmp/memoir-tools-gate}"
mkdir -p "$LOG_DIR"

SCRUB=(env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT
       -u POSTGRES_PASSWORD -u DATABASE_URL -u PORT -u DAEMON_PORT \
       -u ENSEMBLE_SELF_ENV)

run_suite() {
  local label="$1"; shift
  echo "--- ${label} ---"
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest "$@" \
    -q --tb=short -p no:randomly --override-ini="addopts=" -rfE 2>&1
  local rc=$?
  echo "--- ${label} exit=${rc} ---"
  return ${rc}
}

# ── the mutation scaffolding ─────────────────────────────────────────────
TOOL_BAK="${TOOL_PY}.packbak"
SERVICE_BAK="${SERVICE_PY}.packbak"

backup_files() {
  cp "${TOOL_PY}" "${TOOL_BAK}"
  cp "${SERVICE_PY}" "${SERVICE_BAK}"
}

restore_all() {
  cp "${TOOL_BAK}" "${TOOL_PY}" 2>/dev/null || true
  cp "${SERVICE_BAK}" "${SERVICE_PY}" 2>/dev/null || true
}

# prove_red <node-id> <log-path> — the mutated run MUST fail with a
# captured FAILED node id (a vacuous red is a failure).
prove_red() {
  local node="$1"; local log="$2"
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest \
    "${node}" -q --tb=line -p no:randomly --override-ini="addopts=" -rfE \
    >"${log}" 2>&1
  local rc=$?
  if [ ${rc} -eq 0 ]; then
    echo "RESULT: FAIL (the mutation did NOT turn ${node} RED — the pin is"
    echo "        not mutation-meaningful)"
    exit 1
  fi
  if [ ${rc} -eq 124 ]; then
    echo "RESULT: FAIL (the mutated run TIMED OUT instead of failing)"
    exit 1
  fi
  FAILED_IDS=$(grep -E "^FAILED " "${log}" | sed 's/^FAILED //' || true)
  if [ -z "${FAILED_IDS}" ]; then
    echo "RESULT: FAIL (no FAILED node id captured — vacuous red)"
    exit 1
  fi
  echo "MUTATION: RED as required; FAILED node id(s):"
  echo "${FAILED_IDS}" | sed 's/^/    /'
}

# prove_green <node-id> — after the restore the target is GREEN again.
prove_green() {
  local node="$1"
  timeout "${TIMEOUT_S}" "${SCRUB[@]}" "${PY}" -m pytest \
    "${node}" -q --tb=line -p no:randomly --override-ini="addopts=" \
    >/dev/null 2>&1
  local rc=$?
  if [ ${rc} -ne 0 ]; then
    echo "RESULT: FAIL (after the restore ${node} is still red: rc=${rc})"
    exit 1
  fi
  echo "restore: ${node} GREEN again"
}

backup_files
trap 'restore_all; rm -f "${TOOL_BAK}" "${SERVICE_BAK}"' EXIT INT TERM

# ── Leg 0: the baseline suite is GREEN ───────────────────────────────────
run_suite "leg 0 baseline (full suite)" "${TEST_FILE}"
BASE_RC=$?
if [ ${BASE_RC} -ne 0 ]; then
  echo "RESULT: FAIL (baseline suite is not green: rc=${BASE_RC})"
  exit 1
fi

# ── Leg M1: the gate order inverted ──────────────────────────────────────
echo "--- leg M1 mutation: the knob read ahead of the auth gate ---"
"${PY}" - "${TOOL_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
old = (
    "            # ── D4.2 gate 1: the factory-captured identity, FIRST ────\n"
    "            if not _is_memoir_authorized(caller_agent_id):"
)
new = (
    "            # MUTATION M1: the gate order inverted (knob before auth).\n"
    "            if not memoir_enabled(getattr(manager, \"config\", None)):\n"
    "                return _refuse(\n"
    "                    label,\n"
    "                    MEMOIR_CODE_DISABLED,\n"
    "                    \"mutated: knob first\",\n"
    "                )\n"
    "            if not _is_memoir_authorized(caller_agent_id):"
)
if text.count(old) != 1:
    sys.exit(
        "MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"auth-gate anchor, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new, 1))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M1)"; exit 1; fi
prove_red \
  "tests/unit/test_memoir_tools_unit.py::TestTheGateOrder::test_an_unauthorized_caller_is_refused_before_any_knob_or_db" \
  "${LOG_DIR}/M1-gate-order-inverted.log"
restore_all
prove_green \
  "tests/unit/test_memoir_tools_unit.py::TestTheGateOrder::test_an_unauthorized_caller_is_refused_before_any_knob_or_db"

# ── Leg M2: the actor made optional ──────────────────────────────────────
echo "--- leg M2 mutation: the keepsake actor-required branch disabled ---"
"${PY}" - "${TOOL_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
old = (
    "                if not isinstance(actor, str) or not actor.strip():\n"
    "                    return _refuse(\n"
    "                        label,\n"
    "                        MEMOIR_CODE_ACTOR_REQUIRED,"
)
new = (
    "                if False:  # MUTATION M2: the actor made optional\n"
    "                    return _refuse(\n"
    "                        label,\n"
    "                        MEMOIR_CODE_ACTOR_REQUIRED,"
)
if text.count(old) != 1:
    sys.exit(
        "MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"keepsake actor branch, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new, 1))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M2)"; exit 1; fi
prove_red \
  "tests/unit/test_memoir_tools_unit.py::TestTheRefusalMatrix::test_keepsake_without_actor_is_refused" \
  "${LOG_DIR}/M2-actor-optional.log"
restore_all
prove_green \
  "tests/unit/test_memoir_tools_unit.py::TestTheRefusalMatrix::test_keepsake_without_actor_is_refused"

# ── Leg M3: the admission bypassed at the tool layer ─────────────────────
echo "--- leg M3 mutation: the write drops the keepsake flag ---"
"${PY}" - "${TOOL_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
old = "                is_keepsake=bool(keepsake),"
new = (
    "                is_keepsake=False,  "
    "# MUTATION M3: the admission bypassed"
)
if text.count(old) != 1:
    sys.exit(
        "MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"keepsake flag binding, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new, 1))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M3)"; exit 1; fi
prove_red \
  "tests/unit/test_memoir_tools_unit.py::TestWritePinUnpin::test_a_keepsake_write_records_the_claim_and_provenance" \
  "${LOG_DIR}/M3-admission-bypassed.log"
restore_all
prove_green \
  "tests/unit/test_memoir_tools_unit.py::TestWritePinUnpin::test_a_keepsake_write_records_the_claim_and_provenance"

# ── Leg M4: the budget FINAL RE-CHECK removed ────────────────────────────
echo "--- leg M4 mutation: the service's final re-check removed ---"
"${PY}" - "${SERVICE_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
old = "        while total_tokens > MEMOIR_RECALL_TOKEN_BUDGET and shipped:"
new = (
    "        while False and shipped:  "
    "# MUTATION M4: the re-check removed"
)
if text.count(old) != 1:
    sys.exit(
        "MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"final re-check, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new, 1))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M4)"; exit 1; fi
prove_red \
  "tests/unit/test_memoir_tools_unit.py::TestTheReads::test_the_footer_tips_the_envelope_and_the_tool_says_so" \
  "${LOG_DIR}/M4-recheck-removed.log"
restore_all
prove_green \
  "tests/unit/test_memoir_tools_unit.py::TestTheReads::test_the_footer_tips_the_envelope_and_the_tool_says_so"

# ── Leg M5: an inline embed planted in the write body ────────────────────
echo "--- leg M5 mutation: an inline embed in the write body ---"
"${PY}" - "${TOOL_PY}" <<'PLANT'
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
    text = handle.read()
old = "            result: dict[str, Any] = {"
new = (
    "            from daemon.services.memoir_embedding_worker import "
    "_default_embed  # MUTATION M5\n"
    "            await asyncio.to_thread(_default_embed, text)\n"
    "            result: dict[str, Any] = {"
)
if text.count(old) != 1:
    sys.exit(
        "MUTATION PLANT FAILED: expected exactly one occurrence of the "
        f"write result anchor, found {text.count(old)}"
    )
with open(path, "w", encoding="utf-8") as handle:
    handle.write(text.replace(old, new, 1))
print("mutation planted")
PLANT
if [ $? -ne 0 ]; then echo "RESULT: FAIL (could not plant M5)"; exit 1; fi
prove_red \
  "tests/unit/test_memoir_tools_unit.py::TestTheWritePurity::test_the_module_holds_no_embedder_symbol" \
  "${LOG_DIR}/M5-inline-embed.log"
restore_all
prove_green \
  "tests/unit/test_memoir_tools_unit.py::TestTheWritePurity::test_the_module_holds_no_embedder_symbol"

# ── the byte-identity gate ───────────────────────────────────────────────
# The restored files must be byte-identical to their pre-pack backups
# (the lane's own edits are fine — the gate guards the PACK's restores).
if ! cmp -s "${TOOL_BAK}" "${TOOL_PY}"; then
  echo "RESULT: FAIL (${TOOL_PY} was NOT restored byte-identically)"
  exit 1
fi
if ! cmp -s "${SERVICE_BAK}" "${SERVICE_PY}"; then
  echo "RESULT: FAIL (${SERVICE_PY} was NOT restored byte-identically)"
  exit 1
fi

ELAPSED=$((SECONDS - PACK_START))
echo "RESULT: PASS (baseline + 5 mutation legs proven; ${ELAPSED}s)"
exit 0
