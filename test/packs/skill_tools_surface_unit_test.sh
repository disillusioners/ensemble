#!/usr/bin/env bash
# Test Pack: skill_tools_surface_unit_test — D-G6 (REGISTRATION of the
# PRE-EXISTING, UNREGISTERED consumer file `tests/tools/test_skill_tools.py`).
#
# WHY THIS PACK EXISTS
#   `tests/tools/test_skill_tools.py` — the Dynamic-Skill TOOL-CATEGORY
#   consumer file (6 tools: skill_search / skill_list / skill_view /
#   skill_create / skill_fix / skill_feedback) — was run by NO registered
#   pack. That gap is recorded in the D-G6 note of the
#   `skill_tools_display_hygiene` row of `.agents/tester/PACKS.md`
#   (`grep -rn "test_skill_tools\.py" test/packs/` => 0 hits). It is a
#   PRE-EXISTING condition, NOT created by the D-G5 pass.
#
#   House rule `packconv-01-extend-file-owes-no-new-row` is satisfied: a
#   new pack script + row are owed only for a test file NOT covered by an
#   existing pack. This file was covered by none.
#
#   This pack REGISTERS the file. It adds NO new assertions, NO new pin
#   file and NO mutation matrix: it turns an already-green consumer file
#   into a REGULARLY-RUN corpus, so its existing assertions (the D-G5
#   re-pointed `FIX_HEADER_NEEDLE` shape pins, the soft-fail branches, the
#   dispatcher-vs-no-dispatcher branch) can no longer rot unnoticed.
#
#   NOT DONE HERE (deliberate, out of scope for a registration pack):
#   the file's assertions are SHAPE-ONLY (header shape + the
#   dispatcher-missing note). The DURABILITY coverage lives in the
#   `skill_id_verbatim_contract` row's lane-gate pin, the D-G1 coherence
#   pins and the F-G2 outcome pins. This pack does not restate, weaken or
#   re-aim any of them.
#
# MEASURED — NOT GUESSED (2026-11-10, this worktree, `.venv` CPython
# 3.14.7, `tests/tools/test_skill_tools.py` alone, full per-variable env
# scrub: 7 x ENSEMBLE_AUTH_* + 5 x POSTGRES_*):
#   --collect-only -q .......... 37 tests collected (0.22 s; ~5 s wall once
#                                 tests/tools/conftest.py's SQLAlchemy model
#                                 imports are paid)
#   full run (-q --tb=short) ... 37 passed, 1 warning in 1.64 s
#   outer wall, 3 repeat runs .. 6 s / 6 s / 6 s  (rc=0 every time)
#   slowest node ............... 0.48 s  TestFactory::test_factory_returns_six_tools
#   2nd slowest ................ 0.18 s  TestEdgeCases::test_skill_fix_uses_dispatcher_when_available
#   remaining 35 nodes ........ <= 0.02 s each (72 further phases < 0.005 s)
#
# => ONE pack is sufficient; NO split is needed or wanted. The house cap is
#    300 s and the measured cost is ~13 s end-to-end — a >20x margin.
#
# EXPECTED NODE COUNT: 37
#   TestFactory 5 + TestServicesMissing 8 + TestServicesAvailable 11
#   + TestEdgeCases 7 + TestReviewFixes 6 = 37.
#   The pack FAILS LOUDLY when the node count drifts OR when the CLASS
#   ROSTER drifts OR when the EXECUTED node count drifts (junit xml).
#   A registration pack that silently ran fewer nodes than the file
#   contains would be a VACUOUS registration; these three drift checks are
#   what make the registration non-vacuous. All three are mutation-proven
#   (6 mutant legs, each RED with its own message).
#
# ── TRANSPARENCY ─────────────────────────────────────────────────────────────
#   No deselection, no -x, no skip, no markers added: the whole file runs.
#   `--override-ini="addopts="` clears the repo-wide marker filter so the
#   pack is marker-agnostic; this file carries no integration/postgres/
#   slow marker of its own.
#
# ── DUAL-LAYER TIMEOUT ───────────────────────────────────────────────────────
#   Layer 1 (outer): the caller wraps with `timeout 300 bash <pack>.sh`.
#   Layer 2 (inner): EACH pytest invocation is itself wrapped in
#   `timeout ${PYTEST_TIMEOUT_S}s` (a hang can never silently eat the
#   cap), PLUS a script-internal watchdog at ${WATCHDOG_S}s that SIGTERMs
#   the whole run group; the TERM trap turns that into RESULT: TIMEOUT and
#   exit 124. The pack's own SELF-CAP is ${PACK_BUDGET_S}s (checked after
#   every phase) — 100 < 110 < 240 < 300.
#
# ── ENV-POISON FENCE ─────────────────────────────────────────────────────────
#   The ambient shell carries the LIVE coordinates (POSTGRES_HOST=10.44.0.2,
#   POSTGRES_DB=ensemble_prod, ENSEMBLE_SELF_ENV=live + a live password).
#   Every command that can reach a DB or spawn pytest runs through the
#   scrub: (1) it unsets every POSTGRES_*/libpq/SSL var IN THE CHILD ENV
#   (the `env -u POSTGRES_*` GLOB form is a SILENT NO-OP — bash does not
#   glob env names); (2) it evaluates the fence assertion INSIDE that
#   scrubbed child env (asserting in the PARENT spuriously fires — the
#   parent still carries ensemble_prod); (3) it hands off to the house
#   canonical wrapper scripts/run_tests_scrubbed.sh, which adds the
#   FAMILY-WIDE leak verification (ANY POSTGRES_* spelling, not an
#   enumerated list). A negative self-test PROVES the fence refuses a
#   deliberate ensemble_prod pointer BEFORE the real run starts.
#
# Usage:
#   timeout 300 bash test/packs/skill_tools_surface_unit_test.sh
#   PACK_BUDGET_S=300 bash test/packs/skill_tools_surface_unit_test.sh
#     — override the internal wall-clock self-cap (seconds).
#   SKILL_TOOLS_SURFACE_EXPECTED_NODES=38 bash test/packs/skill_tools_surface_unit_test.sh
#     — override the expected node count (when the file legitimately grows).
#
# Pack exit: 0 PASS / 1 FAIL / 124 TIMEOUT. The LAST stdout line is exactly
# `RESULT: PASS|FAIL|TIMEOUT`; every count/detail sits on a PRECEDING line.
set -euo pipefail

PACK_NAME="skill_tools_surface_unit_test"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_DIR" || {
    echo "FAIL: cannot cd to repo root"
    echo "RESULT: FAIL"
    exit 1
}

TARGET_FILE="tests/tools/test_skill_tools.py"
EXPECTED_NODES="${SKILL_TOOLS_SURFACE_EXPECTED_NODES:-37}"
EXPECTED_ROSTER="TestEdgeCases TestFactory TestReviewFixes TestServicesAvailable TestServicesMissing"
EXPECTED_COUNTS="TestEdgeCases:7 TestFactory:5 TestReviewFixes:6 TestServicesAvailable:11 TestServicesMissing:8"

PACK_BUDGET_S="${PACK_BUDGET_S:-240}"
PYTEST_TIMEOUT_S="${PYTEST_TIMEOUT_S:-100}"
WATCHDOG_S="${WATCHDOG_S:-110}"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[pack] budget: ${PACK_BUDGET_S}s (house cap 300s) | expected nodes: ${EXPECTED_NODES}"
echo

PACK_START="$(date +%s)"
elapsed_s() { echo $(( $(date +%s) - PACK_START )); }
check_budget() {
  local elapsed label="${1:-}"
  elapsed="$(elapsed_s)"
  if [ "$elapsed" -gt "$PACK_BUDGET_S" ]; then
    echo "Pack TIMEOUT: internal timer — wall ${elapsed}s > PACK_BUDGET_S=${PACK_BUDGET_S}s ${label}"
    echo "RESULT: TIMEOUT"
    exit 124
  fi
}

# ── ENV-POISON FENCE ─────────────────────────────────────────────────────────
# The full house scrub list (scripts/run_tests_scrubbed.sh) plus the two
# SSL vars several house packs unset, plus the FULL ENSEMBLE_AUTH_* family
# (7 vars — a PARTIAL scrub breaks create_app()'s origin check into
# collection ERRORs), plus the workspace/self-env selectors.
SCRUB_VARS=(
    POSTGRES_HOST POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_PORT
    POSTGRES_URL DATABASE_URL_POSTGRES DATABASE_URL
    PGHOST PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE
    PGSSLMODE PGSERVICE PGSERVICEFILE
    SSL_CERT_FILE SSL_CERT_DIR
    ENSEMBLE_UPGRADE_LIVE ENSEMBLE_RESTART_UNIT
    ENSEMBLE_SELF_ENV ENSEMBLE_CONFIG ENSEMBLE_DATA_DIR PORT
    ENSEMBLE_AUTH_ALLOWED_ORIGINS
    ENSEMBLE_AUTH_CLIENT_ID
    ENSEMBLE_AUTH_CLIENT_SECRET
    ENSEMBLE_AUTH_DISCOVERY_URL
    ENSEMBLE_AUTH_ENABLED
    ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
    ENSEMBLE_AUTH_ISSUER
)

ENV_UNSET=()
for _v in "${SCRUB_VARS[@]}"; do
    ENV_UNSET+=(-u "$_v")
done

# The fence ASSERTION — evaluated inside the scrubbed child env.
FENCE_SNIPPET='[ "${POSTGRES_DB:-}" != "ensemble_prod" ] || { echo "FENCE BREACH: POSTGRES_DB=${POSTGRES_DB} points at the LIVE database"; exit 1; }; [ "${POSTGRES_HOST:-}" != "10.44.0.2" ] || { echo "FENCE BREACH: POSTGRES_HOST=${POSTGRES_HOST} points at the LIVE PG host"; exit 1; }; exec "$@"'

FAKE_HOME="$(mktemp -d /tmp/skill-tools-surface-pack-XXXXXX)"

# scrub <cmd> [args...] — scrubbed env + fence. `timeout` cannot run a shell
# FUNCTION, so the timeout-wrapped invocation uses the argv array below.
scrub() {
    env "${ENV_UNSET[@]}" ENSEMBLE_SELF_ENV=dev \
        HOME="$FAKE_HOME" \
        XDG_CACHE_HOME="$FAKE_HOME/cache" \
        PYTHONPATH="$PROJECT_DIR" \
        sh -c "$FENCE_SNIPPET" sh "$@"
}

# Negative proof: point the scrubbed env AT ensemble_prod and require the
# fence to refuse (non-zero exit + "FENCE BREACH"). Runs BEFORE the real run.
FENCE_SELFTEST_RC=0
FENCE_SELFTEST_OUT="$(env "${ENV_UNSET[@]}" POSTGRES_DB=ensemble_prod \
    sh -c "$FENCE_SNIPPET" sh true 2>&1)" || FENCE_SELFTEST_RC=$?
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

# ── the house canonical wrapper (adds the FAMILY-WIDE leak gate) ───────────
# SCRUB_CMD is an argv ARRAY (a shell function cannot be wrapped by `timeout`).
SCRUB_CMD=(env "${ENV_UNSET[@]}" ENSEMBLE_SELF_ENV=dev
    HOME="$FAKE_HOME"
    XDG_CACHE_HOME="$FAKE_HOME/cache"
    PYTHONPATH="$PROJECT_DIR"
    sh -c "$FENCE_SNIPPET" sh ./scripts/run_tests_scrubbed.sh)

# ── phase 0: the IMPORT GUARD ──────────────────────────────────────────────
# Proves `import daemon` resolves under THIS checkout. realpath-EXACT, NOT a
# `startswith` prefix test (a sibling-prefix trap exists against the shared
# checkout).
echo "--- phase 0: the import guard ---"
IMPORT_GUARD_LOG="$FAKE_HOME/import-guard.log"
IMPORT_GUARD_RC=0
scrub "$PROJECT_DIR/.venv/bin/python" - "$PROJECT_DIR" >"$IMPORT_GUARD_LOG" 2>&1 <<'GUARDEOF' || IMPORT_GUARD_RC=$?
import os
import sys

project_dir = os.path.realpath(sys.argv[1])
try:
    import daemon
except Exception as exc:  # pragma: no cover — the guard must speak up
    sys.stderr.write(f"IMPORT GUARD: FAIL — 'import daemon' raised: {exc!r}\n")
    sys.exit(1)

pkg_dir = os.path.realpath(os.path.dirname(daemon.__file__))
expected = os.path.join(project_dir, "daemon")
sys.stderr.write(f"daemon.__file__={daemon.__file__}\n")
sys.stderr.write(f"expected package dir={expected}\n")
if pkg_dir != expected:
    sys.stderr.write(
        "IMPORT GUARD: FAIL — 'import daemon' did NOT resolve under "
        f"{project_dir}\n"
        "  (the run would have tested the FOREIGN tree)\n"
    )
    sys.exit(1)
sys.stderr.write("IMPORT GUARD: PASS — import daemon resolved under "
                 f"{project_dir}\n")
GUARDEOF
cat "$IMPORT_GUARD_LOG"
if [ "$IMPORT_GUARD_RC" -ne 0 ]; then
    echo "FAIL: the import guard did not pass (rc=${IMPORT_GUARD_RC})."
    echo "RESULT: FAIL"
    exit 1
fi
echo "[timer] phase 0 done: $(elapsed_s)s elapsed / budget ${PACK_BUDGET_S}s"
check_budget "after phase 0"
echo

# ── LAYER 2 — internal self-watchdog ────────────────────────────────────────
# Backstop: if the pytest wrapper itself ever fails to return, this SIGTERMs
# the whole run group at WATCHDOG_S and the TERM trap reports
# RESULT: TIMEOUT (124).
on_term() {
    echo "[watchdog] INTERNAL WATCHDOG ${WATCHDOG_S}s EXCEEDED — SIGTERM to the run group"
    echo "RESULT: TIMEOUT"
    exit 124
}
trap on_term TERM

cleanup() {
    if [ -n "${WATCHDOG_PID:-}" ]; then
        kill -TERM "$WATCHDOG_PID" 2>/dev/null || true
        wait "$WATCHDOG_PID" 2>/dev/null || true
        WATCHDOG_PID=""
    fi
    rm -rf "$FAKE_HOME" || true
    return 0
}
trap cleanup EXIT

# Concurrency fence: one pack run per checkout.
LOCK_KEY="$(printf '%s' "$PROJECT_DIR" | sha256sum | cut -d' ' -f1 | cut -c1-16)"
LOCK_FILE="/tmp/skill-tools-surface-pack-${LOCK_KEY}.lock"
exec 8>"$LOCK_FILE"
if ! flock -n 8; then
    echo "FAIL: another pack run holds this checkout's lock: ${LOCK_FILE}"
    echo "RESULT: FAIL"
    exit 1
fi

# Start the internal self-watchdog (Layer 2 backstop). It sleeps the full
# WATCHDOG_S budget and then SIGTERMs the whole run group; the TERM trap
# above turns that into RESULT: TIMEOUT / exit 124. The nested `sleep` is
# killed explicitly so no orphan survives a fast pass.
(
    sleep "$WATCHDOG_S" &
    _SPID=$!
    trap 'kill -TERM "$_SPID" 2>/dev/null; exit 0' TERM
    wait "$_SPID" || exit 0
    echo "[watchdog] INTERNAL WATCHDOG ${WATCHDOG_S}s EXCEEDED — SIGTERM to the run group" >&2
    kill -TERM "-$$" 2>/dev/null || kill -TERM "$$" 2>/dev/null
) &
WATCHDOG_PID=$!

# ── phase 1: the node-count + class-roster ASSERTIONS ──────────────────────
echo "--- phase 1: the node-count + class-roster assertions ---"
COLLECT_LOG="$FAKE_HOME/collect.log"
COLLECT_RC=0
timeout "${PYTEST_TIMEOUT_S}s" "${SCRUB_CMD[@]}" "$TARGET_FILE" \
    --collect-only -q --override-ini="addopts=" --tb=short \
    >"$COLLECT_LOG" 2>&1 || COLLECT_RC=$?
if [ "$COLLECT_RC" -eq 5 ]; then
    tail -5 "$COLLECT_LOG" || true
    echo "FAIL: pytest collected no tests from ${TARGET_FILE} (exit 5)."
    echo "RESULT: FAIL"
    exit 1
fi
if [ "$COLLECT_RC" -eq 124 ]; then
    echo "Pack TIMEOUT: the census invocation hit the ${PYTEST_TIMEOUT_S}s inner cap."
    echo "RESULT: TIMEOUT"
    exit 124
fi
if [ "$COLLECT_RC" -ne 0 ]; then
    tail -20 "$COLLECT_LOG" || true
    echo "FAIL: collection failed (rc=${COLLECT_RC})."
    echo "RESULT: FAIL"
    exit 1
fi

COLLECTED_NODES="$(grep -c "^${TARGET_FILE}::" "$COLLECT_LOG" || true)"
ACTUAL_ROSTER="$(grep "^${TARGET_FILE}::" "$COLLECT_LOG" \
    | sed "s#^${TARGET_FILE}::\([^:]*\)::.*#\1#" | sort -u | tr '\n' ' ' \
    | sed 's/ $//' || true)"
echo "[pack] collected nodes: ${COLLECTED_NODES} (expected ${EXPECTED_NODES})"
echo "[pack] collected roster: ${ACTUAL_ROSTER}"

if [ "$COLLECTED_NODES" != "$EXPECTED_NODES" ]; then
    echo "FAIL: node-count drift — collected ${COLLECTED_NODES}, expected ${EXPECTED_NODES}."
    echo "  (a legitimate change to the file must bump EXPECTED_NODES in this pack AND the PACKS.md row)"
    echo "RESULT: FAIL"
    exit 1
fi
if [ "$ACTUAL_ROSTER" != "$EXPECTED_ROSTER" ]; then
    echo "FAIL: class-roster drift — got '${ACTUAL_ROSTER}', expected '${EXPECTED_ROSTER}'."
    echo "RESULT: FAIL"
    exit 1
fi
for pair in $EXPECTED_COUNTS; do
    cls="${pair%%:*}"
    want="${pair##*:}"
    got="$(grep -c "^${TARGET_FILE}::${cls}::" "$COLLECT_LOG" || true)"
    if [ "$got" != "$want" ]; then
        echo "FAIL: per-class node drift — ${cls} collected ${got}, expected ${want}."
        echo "RESULT: FAIL"
        exit 1
    fi
done
echo "[pack] node-count + roster assertions: PASS"
echo "[timer] phase 1 done: $(elapsed_s)s elapsed / budget ${PACK_BUDGET_S}s"
check_budget "after phase 1"
echo

# ── phase 2: the registered corpus ─────────────────────────────────────────
echo "--- phase 2: the registered corpus (${TARGET_FILE}) ---"
PHASE2_LOG="$FAKE_HOME/phase2.log"
JUNIT_XML="$FAKE_HOME/phase2-junit.xml"
PHASE2_RC=0
timeout "${PYTEST_TIMEOUT_S}s" "${SCRUB_CMD[@]}" "$TARGET_FILE" \
    -q --override-ini="addopts=" --tb=short --junitxml="$JUNIT_XML" \
    >"$PHASE2_LOG" 2>&1 || PHASE2_RC=$?
tail -3 "$PHASE2_LOG" || true
echo "[timer] phase 2 done: $(elapsed_s)s elapsed / budget ${PACK_BUDGET_S}s"
check_budget "after phase 2"

# ── executed-node accounting (junit xml — no text scraping) ────────────────
COUNTS_FILE="$FAKE_HOME/junit-counts.txt"
JUNIT_RC=0
scrub "$PROJECT_DIR/.venv/bin/python" - "$JUNIT_XML" \
    >"$COUNTS_FILE" 2>"$FAKE_HOME/junit-parse.err" <<'PYEOF' || JUNIT_RC=$?
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()


def totals(node):
    if node.get("tests") is not None:
        return (
            int(node.get("tests") or 0),
            int(node.get("failures") or 0),
            int(node.get("errors") or 0),
            int(node.get("skipped") or 0),
        )
    t = f = e = s = 0
    for child in node:
        ct, cf, ce, cs = totals(child)
        t += ct
        f += cf
        e += ce
        s += cs
    return t, f, e, s


t, f, e, s = totals(root)
print(f"{t} {f} {e} {s}")
PYEOF
if [ "$JUNIT_RC" -ne 0 ]; then
    cat "$FAKE_HOME/junit-parse.err" || true
    echo "FAIL: could not read the junit xml (rc=${JUNIT_RC})."
    echo "RESULT: FAIL"
    exit 1
fi
COUNTS="$(cat "$COUNTS_FILE")"
EXECUTED_NODES="$(echo "$COUNTS" | cut -d' ' -f1)"
FAILURES="$(echo "$COUNTS" | cut -d' ' -f2)"
ERRORS="$(echo "$COUNTS" | cut -d' ' -f3)"
SKIPPED="$(echo "$COUNTS" | cut -d' ' -f4)"
echo "[pack] executed: ${EXECUTED_NODES} node(s) | failures=${FAILURES} errors=${ERRORS} skipped=${SKIPPED}"

if [ "$EXECUTED_NODES" != "$EXPECTED_NODES" ]; then
    echo "FAIL: executed ${EXECUTED_NODES} node(s), expected ${EXPECTED_NODES}."
    echo "RESULT: FAIL"
    exit 1
fi

# ── verdict ────────────────────────────────────────────────────────────────
# Stop the self-watchdog now that the run has returned.
if [ -n "${WATCHDOG_PID:-}" ]; then
    kill -TERM "$WATCHDOG_PID" 2>/dev/null || true
    wait "$WATCHDOG_PID" 2>/dev/null || true
    WATCHDOG_PID=""
fi

echo "[timer] total wall: $(elapsed_s)s / budget ${PACK_BUDGET_S}s"
check_budget "at verdict"
if [ "$PHASE2_RC" -eq 124 ]; then
    echo "Pack TIMEOUT: the pytest invocation hit the ${PYTEST_TIMEOUT_S}s inner cap."
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "$PHASE2_RC" -eq 5 ]; then
    echo "Pack FAILED: pytest collected no tests from ${TARGET_FILE} (exit 5)."
    echo "RESULT: FAIL"
    exit 1
elif [ "$PHASE2_RC" -ne 0 ]; then
    echo "Pack FAILED: pytest exited ${PHASE2_RC}."
    echo "RESULT: FAIL"
    exit 1
elif [ "$FAILURES" != "0" ] || [ "$ERRORS" != "0" ]; then
    echo "Pack FAILED: ${FAILURES} failure(s) / ${ERRORS} error(s)."
    echo "RESULT: FAIL"
    exit 1
elif [ "$SKIPPED" != "0" ]; then
    echo "Pack FAILED: ${SKIPPED} node(s) SKIPPED — a registration pack must EXECUTE its corpus."
    echo "RESULT: FAIL"
    exit 1
else
    echo "RESULT: PASS"
    exit 0
fi
