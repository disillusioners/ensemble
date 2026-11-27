#!/usr/bin/env bash
# =============================================================================
# test/packs/tool_usage_artifacts_payload_unit_test.sh
#
# Pack: tool_usage_artifacts_payload_unit_test
# Scope: the tool-usage-artifacts/ RELEASE PAYLOAD seam — the staged payload
# carries EXACTLY tool-usage-artifacts/{allowlists.json,tool-weights.json} and
# nothing else; manifest.json carries `tool_usage_artifacts_tree_sha256` +
# `tool_usage_artifacts_manifest` and the per-file map matches a fresh hash;
# the stage-local re-hash leg REJECTS a corrupted copy; the staged tree passes
# `_no_env_in_release`; and D4 pinning assertion (ii) holds — a file dropped
# into the DERIVED digest output dir changes NO tree hash.
#
# WHY A WRAPPER: a `.sh` file has no pytest home, so without this pack the
# suite never reaches the gate. It is the house pattern of the sibling
# stage-payload shells (test/packs/executor_family_payload_unit_test.sh
# wrapping tests/test_executor_family_payload.sh; test/packs/
# guidelines_read_tool_unit_test.sh leg 2 wrapping tests/test_guidelines_payload.sh).
# The wrapper adds ZERO nodes to the pytest census — the pack's slice collects
# `.py` files with --collect-only, so a shell test cannot be one of them.
#
# The suite is HERMETIC: every fixture is built under a fresh mktemp dir; the
# LIVE install dir is never read or written; stage.sh is never executed
# end-to-end (the shipped seam is extracted and called in isolation); no daemon
# is started and no database is contacted.
#
# CONTRACT (house shape — mirrors test/packs/tool_usage_pins_unit_test.sh)
#   - Dual-layer timeout: inner `timeout 480` around the suite; the caller
#     wraps with the outer `timeout 480`.
#   - `set -u` ONLY, never `set -e` (under `set -e` a bare `X=$?` after a
#     failing run NEVER executes, so `RESULT: FAIL` becomes unreachable).
#   - PER-VARIABLE env scrub (a `POSTGRES_*` glob is a SILENT NO-OP in bash —
#     bash does not glob env-var names in `env -u`).
#   - ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 pinned EXPLICITLY (never `env -u`
#     on it: stripping the ambient guard makes the boot sweep SIGTERM/SIGKILL
#     live processes).
#   - ENSEMBLE_SELF_ENV normalised to `dev` (the host's ambient value is
#     `live`); the fence assert makes the guarantee LOUD.
#   - PYTHONDONTWRITEBYTECODE=1 — no .pyc writes into the repo tree.
#   - VACUOUS-VERDICT GUARD: no summary line, or zero passed assertions, is a
#     FAIL (a pack that can PASS from an empty run is worthless).
#   - Output: header, suite body, `RESULT: PASS|FAIL|TIMEOUT`.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
# =============================================================================

set -u

PACK_NAME="tool_usage_artifacts_payload_unit_test"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$PROJECT_DIR" || {
    echo "RESULT: FAIL (cannot cd to ${PROJECT_DIR})"
    exit 1
}

SUITE="tests/test_tool_usage_artifacts_payload.sh"
if [ ! -f "$SUITE" ]; then
    echo "FATAL: required suite missing: $SUITE" >&2
    echo "RESULT: FAIL"
    exit 1
fi

PYTHON_BIN="$PROJECT_DIR/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
    echo "FATAL: .venv/bin/python not found at $PYTHON_BIN" >&2
    echo "RESULT: FAIL"
    exit 1
fi

# ── the per-variable env scrub (never a POSTGRES_* glob) ─────────────────
SCRUB=(
  -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_USER -u POSTGRES_PASSWORD
  -u POSTGRES_DB -u POSTGRES_URL -u DATABASE_URL -u ENSEMBLE_DB_DSN
  -u SSL_CERT_FILE -u SSL_CERT_DIR
  -u OPENAI_API_KEY
  -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER -u ENSEMBLE_AUTH_CLIENT_ID
  -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_DISCOVERY_URL
  -u ENSEMBLE_AUTH_ALLOWED_ORIGINS -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE
  -u ENSEMBLE_BASH_TRACKING_ID
  -u ENSEMBLE_SELF_ENV
)

# NORMALISE, never refuse: the host's ambient ENSEMBLE_SELF_ENV is `live`
# (measured on this host) and every PACKS.md Invocation cell runs this pack
# with NO prefix — so the pack normalises to `dev` and the assert below can
# only fire if a future edit drops THIS export.
export ENSEMBLE_SELF_ENV=dev
if [ "${ENSEMBLE_SELF_ENV:-}" != "dev" ]; then
    echo "FENCE FAILURE: ENSEMBLE_SELF_ENV='${ENSEMBLE_SELF_ENV:-<unset>}' — this pack requires 'dev'." >&2
    echo "RESULT: FAIL (env fence)"
    exit 1
fi

OUT="$(mktemp)"
set -o pipefail
timeout 480s env \
    "${SCRUB[@]}" \
    ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0 \
    ENSEMBLE_SELF_ENV=dev \
    PYTHONDONTWRITEBYTECODE=1 \
    bash "$SUITE" < /dev/null 2>&1 | tee "$OUT"
RC=$?

SUMMARY="$(grep -E '[0-9]+ passed' "$OUT" | tail -1)"
PASSED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ passed' | head -1 | grep -oE '^[0-9]+')"
FAILED="$(printf '%s\n' "$SUMMARY" | grep -oE '[0-9]+ failed' | head -1 | grep -oE '^[0-9]+')"
rm -f "$OUT"

echo
if [ -n "$SUMMARY" ]; then echo "SUMMARY: ${SUMMARY}"; fi

if [ "$RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT (the suite hit the inner 480s timeout)"
    exit 124
fi
if [ "$RC" -ne 0 ]; then
    echo "RESULT: FAIL (exit=${RC})"
    exit 1
fi
if [ -z "$SUMMARY" ]; then
    echo "RESULT: FAIL (VACUOUS — the suite produced NO summary line: it did not run)"
    exit 1
fi
if [ -z "$PASSED" ] || [ "$PASSED" -eq 0 ]; then
    echo "RESULT: FAIL (VACUOUS — zero assertions passed: '${SUMMARY}')"
    exit 1
fi
if [ -n "$FAILED" ] && [ "$FAILED" -ne 0 ]; then
    echo "RESULT: FAIL (exit=0 but ${FAILED} assertion(s) failed)"
    exit 1
fi

echo "RESULT: PASS"
exit 0
