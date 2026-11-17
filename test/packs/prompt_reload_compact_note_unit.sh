#!/usr/bin/env bash
# test/packs/prompt_reload_compact_note_unit.sh
#
# Pack: prompt_reload_compact_note_unit
#
# The compact-note lane's TOOL-level + ENGINE-level + GRAPH-SEAM unit
# legs (plan §10.3), PLUS every SIBLING file whose assertions depend on
# the lane's changed symbols (the refusal census, the armed outcome
# literals and the house doc-format markers). The file set is selected by
# SYMBOL, not by name: a name-pattern selection missed
# tests/unit/services/test_prompt_refresh_primitive.py and hid the stale
# 9-vs-14 census pin (tidier H1).
#
# Owned artifacts (lane-owned):
#   tests/unit/tools/test_prompt_reload_compact_note.py   (tool level)
#   tests/unit/tools/test_compact_note_graph_wiring.py    (graph seam)
#   tests/unit/test_compaction_compact_note.py              (engine level)
#
# SIBLING files (symbol-dependent — included so the pack cannot hide a
# red the lane caused):
#   tests/unit/services/test_prompt_refresh_primitive.py   (REFUSAL_CODES census)
#   tests/unit/tools/test_prompt_reload_refusals.py        (REFUSAL_CODES census)
#   tests/unit/tools/test_prompt_reload_tool.py            (tool surface)
#   tests/unit/tools/test_prompt_reload_pins.py            (tool surface)
#   tests/unit/test_compaction.py                          (doc-format markers)
#   tests/unit/services/test_compact_executor.py           (doc-format markers)
#   tests/unit/services/test_compact_executor_revive_brick_e2e.py (doc format)
#   tests/unit/services/test_injected_notes_hoisting_sweep_gaps.py (doc format)
#
# Legs covered: L1 L2 L3 L4 L5 L6 L7 L10 L11 L12 L14 L15 L16 L17
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No external services. No PostgreSQL contact.
#
# Dual-layer timeout (per the test-pack skill):
#   - Layer 1 (command-level): the caller wraps this with `timeout 300`.
#   - Layer 2 (script-internal): `timeout 210s` on the pytest process.
#
# Exit codes:
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
#
# NOTE (house rule): the pytest exit code is captured with `|| RC=$?` and
# NOT under `set -e` — a `set -euo pipefail` + unguarded `RC=$?` script
# exits BEFORE it can report a red (measured: the compaction_unit_test.sh
# defect). A red MUST be reportable.
set -uo pipefail
IFS=$'\n\t'
export PATH="/home/nea/.local/bin:$PATH"

PACK_NAME="prompt_reload_compact_note_unit"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${PROJECT_DIR}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# Per-variable env scrub — NEVER `env -u POSTGRES_*` (bash does not glob
# env names, so that idiom is a silent NO-OP).
env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER \
    -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL \
    -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGPASSWORD \
    -u PGSERVICE -u PGOPTIONS \
    -u ENSEMBLE_AUTH_ENABLED -u ENSEMBLE_AUTH_ISSUER \
    -u ENSEMBLE_AUTH_DISCOVERY_URL -u ENSEMBLE_AUTH_CLIENT_ID \
    -u ENSEMBLE_AUTH_CLIENT_SECRET -u ENSEMBLE_AUTH_ALLOWED_ORIGINS \
    -u ENSEMBLE_AUTH_EXTERNAL_REDIRECT_BASE \
    timeout 210s .venv/bin/python -m pytest \
    tests/unit/tools/test_prompt_reload_compact_note.py \
    tests/unit/tools/test_compact_note_graph_wiring.py \
    tests/unit/test_compaction_compact_note.py \
    tests/unit/services/test_prompt_refresh_primitive.py \
    tests/unit/tools/test_prompt_reload_refusals.py \
    tests/unit/tools/test_prompt_reload_tool.py \
    tests/unit/tools/test_prompt_reload_pins.py \
    tests/unit/test_compaction.py \
    tests/unit/services/test_compact_executor.py \
    tests/unit/services/test_compact_executor_revive_brick_e2e.py \
    tests/unit/services/test_injected_notes_hoisting_sweep_gaps.py \
    -q --tb=short -p no:cacheprovider
RC=$?

echo
if [ "$RC" -eq 124 ]; then
    echo "RESULT: TIMEOUT (internal 210s cap exceeded)"
    exit 124
elif [ "$RC" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    echo "Exit: $RC"
    exit 1
fi
