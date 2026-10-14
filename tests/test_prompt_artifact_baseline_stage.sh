#!/bin/bash
# ============================================================================
# tests/test_prompt_artifact_baseline_stage.sh — bash pack for the T13
# stage.sh prompt-artifact drift leg (S9 / D-BASH-ESCAPE control)
# ============================================================================
# Plain self-asserting bash (no bats), mirroring test_journal_splice.sh /
# test_release_journal.sh fixture discipline.
#
# The leg lives INSIDE stage.sh as a function (prompt_artifact_drift_leg).
# Running stage.sh end-to-end needs a binary + FE build + install dir, so
# this pack extracts JUST the function (sed between its column-0 markers —
# the function body contains no other column-0 braces) and sources it into
# a harness that stubs _log/_warn/journal_history_append. The checker CLI
# itself is exercised for real (scripts/protection/
# generate_prompt_artifact_baseline.py) against throwaway trees.
#
# Coverage:
#   Section 1 — leg + drift fixture: journals `warn` + `prompt-artifact-drift`
#               token, rc 0 (NON-BLOCKING — the leg NEVER fails staging)
#   Section 2 — leg + clean tree: NO journal entry, rc 0, clean log line
#   Section 3 — leg + absent baseline: rc 0, skip line, no journal entry
#   Section 4 — leg + checker missing: rc 0, skip line (non-fatal degrade)
#   Section 5 — structural pins: stage.sh parses (bash -n), leg call site
#               sits AFTER journal_init (journaling precondition), leg is
#               non-blocking by construction (no exit in the function body)
#
# Run:
#   bash tests/test_prompt_artifact_baseline_stage.sh
#
# FENCE (2026-10-12 critical note, zero tolerance): every fixture is a
# mktemp dir under /tmp registered in the shared EXIT-trap registry; no
# HOME-derived path is ever resolved; no ports, no daemon, no network.
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

# ENV FENCE (env-poison 3-guard, hardened posture for any run):
# pin the dev self-env + refuse to run with a live-shaped ambient DB.
export ENSEMBLE_SELF_ENV=dev
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    printf 'FATAL: ambient POSTGRES_DB=ensemble_prod in test env — refusing to run\n' >&2
    exit 1
fi

STAGE="$REPO_ROOT/scripts/upgrade/stage.sh"
CHECKER="$REPO_ROOT/scripts/protection/generate_prompt_artifact_baseline.py"

_pass_count=0; _fail_count=0
_pass() { _pass_count=$((_pass_count + 1)); printf '  ok - %s\n' "${1:-}"; }
_fail() { _fail_count=$((_fail_count + 1)); printf '  FAIL - %s: expected %s, got: %s\n' "$1" "$2" "$3"; }

# _extract_leg_fn — pull prompt_artifact_drift_leg() out of stage.sh.
_extract_leg_fn() {
    sed -n '/^prompt_artifact_drift_leg() {/,/^}/p' "$STAGE"
}

# _make_release_fixture <dir> — mint a fake staged-release tree (agents/ +
# baseline + one drifted/clean variant per $2).
_make_release_fixture() {
    local rel="$1"
    mkdir -p "$rel/agents/alpha" "$rel/agents/beta"
    printf 'alpha soul\n'  > "$rel/agents/alpha/soul.md"
    printf 'alpha rule\n'  > "$rel/agents/alpha/rule.md"
    printf 'beta soul\n'   > "$rel/agents/beta/soul.md"
    printf 'beta tools\n'  > "$rel/agents/beta/tools_note.md"
}

# _run_leg <rel_dir> <version> — source the extracted leg with harness
# stubs and invoke it. Records: JOURNAL (arg1|arg2 per append), LOGS.
_run_leg() {
    local rel="$1" version="$2"
    JOURNAL="$FIXTURE/journal-lines.log"
    LOGS="$FIXTURE/leg-logs.log"
    : > "$JOURNAL"; : > "$LOGS"
    _log()   { printf 'LOG: %s\n'  "$*" >> "$LOGS"; }
    _warn()  { printf 'WARN: %s\n' "$*" >> "$LOGS"; }
    journal_history_append() { printf '%s|%s\n' "$1" "$2" >> "$JOURNAL"; }
    SCRIPT_DIR="$REPO_ROOT/scripts/upgrade"
    _extract_leg_fn > "$FIXTURE/leg-fn.sh"
    # shellcheck disable=SC1090
    . "$FIXTURE/leg-fn.sh"
    prompt_artifact_drift_leg "$rel" "$version"
}

FIXTURE="$(mktemp -d -t prompt-drift-leg-XXXXXX)"; _register_fixture "$FIXTURE"

printf '== Section 1: leg + drift fixture — journals, never blocks ==\n'
REL="$FIXTURE/rel-drift"
_make_release_fixture "$REL"
python3 "$CHECKER" --tree "$REL" >/dev/null    # baseline from the clean tree
printf 'TAMPERED-BY-BASH\n' > "$REL/agents/alpha/soul.md"   # simulate drift
_rc=0
_run_leg "$REL" "vTEST" || _rc=$?
[ "$_rc" -eq 0 ] && _pass "leg rc 0 on drift (non-blocking)" \
    || _fail "leg rc on drift" "0" "$_rc"
_j="$(cat "$JOURNAL")"
assert_contains "journal event class" "warn|prompt-artifact-drift" "$_j"
assert_contains "journal drift token" "prompt-artifact-drift at stage of vTEST" "$_j"
assert_contains "journal carries summary" "PROMPT-DRIFT:" "$_j"
assert_contains "journal names the drifted file" "agents/alpha/soul.md" "$_j"
_g="$(cat "$LOGS")"
assert_contains "WARN surfaced to operator" "PROMPT-DRIFT:" "$_g"

printf '== Section 2: leg + clean tree — silent, no journal entry ==\n'
REL="$FIXTURE/rel-clean"
_make_release_fixture "$REL"
python3 "$CHECKER" --tree "$REL" >/dev/null
_rc=0
_run_leg "$REL" "vTEST" || _rc=$?
[ "$_rc" -eq 0 ] && _pass "leg rc 0 on clean tree" \
    || _fail "leg rc on clean" "0" "$_rc"
[ ! -s "$JOURNAL" ] && _pass "no journal entry on clean tree" \
    || _fail "journal on clean" "empty" "$(cat "$JOURNAL")"
assert_contains "clean log line" "PROMPT-BASELINE: OK" "$(cat "$LOGS")"

printf '== Section 3: leg + absent baseline — skip, rc 0 ==\n'
REL="$FIXTURE/rel-absent"
_make_release_fixture "$REL"   # no baseline generated
_rc=0
_run_leg "$REL" "vTEST" || _rc=$?
[ "$_rc" -eq 0 ] && _pass "leg rc 0 with absent baseline" \
    || _fail "leg rc absent baseline" "0" "$_rc"
[ ! -s "$JOURNAL" ] && _pass "no journal entry when baseline absent" \
    || _fail "journal on absent" "empty" "$(cat "$JOURNAL")"
assert_contains "absent-baseline skip line" "baseline absent" "$(cat "$LOGS")"

printf '== Section 4: leg + checker missing — non-fatal degrade, rc 0 ==\n'
REL="$FIXTURE/rel-nochecker"
_make_release_fixture "$REL"
python3 "$CHECKER" --tree "$REL" >/dev/null
_rc=0
_run_leg "$REL" "vTEST" || _rc=$?   # SCRIPT_DIR reset inside _run_leg —
# re-run with a SCRIPT_DIR pointing at a dir lacking protection/: rebuild
# the harness with the bad SCRIPT_DIR.
JOURNAL="$FIXTURE/journal-lines.log"; LOGS="$FIXTURE/leg-logs.log"
: > "$JOURNAL"; : > "$LOGS"
_log()   { printf 'LOG: %s\n'  "$*" >> "$LOGS"; }
_warn()  { printf 'WARN: %s\n' "$*" >> "$LOGS"; }
journal_history_append() { printf '%s|%s\n' "$1" "$2" >> "$JOURNAL"; }
SCRIPT_DIR="$FIXTURE/empty-scripts"
mkdir -p "$SCRIPT_DIR"
prompt_artifact_drift_leg "$REL" "vTEST" || _rc=$?
[ "$_rc" -eq 0 ] && _pass "leg rc 0 with checker missing" \
    || _fail "leg rc checker missing" "0" "$_rc"
[ ! -s "$JOURNAL" ] && _pass "no journal entry when checker missing" \
    || _fail "journal on missing checker" "empty" "$(cat "$JOURNAL")"
assert_contains "checker-missing skip line" "checker missing" "$(cat "$LOGS")"

printf '== Section 5: structural pins on stage.sh ==\n'
bash -n "$STAGE" && _pass "stage.sh parses (bash -n)" \
    || _fail "stage.sh syntax" "clean" "bash -n failed"
_leg_line="$(grep -n '^prompt_artifact_drift_leg "\$REL" "\$VERSION"$' "$STAGE" | head -1 | cut -d: -f1)"
_jinit_line="$(grep -n '^journal_init$' "$STAGE" | head -1 | cut -d: -f1)"
if [ -n "$_leg_line" ] && [ -n "$_jinit_line" ] && [ "$_leg_line" -gt "$_jinit_line" ]; then
    _pass "leg call site sits after journal_init (line $_leg_line > $_jinit_line)"
else
    _fail "leg/journal_init ordering" "leg after journal_init" "leg=$_leg_line jinit=$_jinit_line"
fi
_fn="$(sed -n '/^prompt_artifact_drift_leg() {/,/^}/p' "$STAGE")"
case "$_fn" in
    *exit*) _fail "leg contains exit" "no exit in function body" "exit found" ;;
    *) _pass "leg body has no exit (non-blocking by construction)" ;;
esac
# The function resolves the checker from the protection/ dir (repo-shipped,
# NOT a throwaway) and references the committed baseline filename.
case "$_fn" in
    *protection/generate_prompt_artifact_baseline.py*) _pass "leg references the committed checker" ;;
    *) _fail "checker reference" "protection/generate_prompt_artifact_baseline.py" "absent" ;;
esac
case "$_fn" in
    *".prompt-artifacts.sha256"*) _pass "leg references the committed baseline filename" ;;
    *) _fail "baseline reference" ".prompt-artifacts.sha256" "absent" ;;
esac

printf '\n%d passed, %d failed\n' "$_pass_count" "$_fail_count"
[ "$_fail_count" -eq 0 ]
