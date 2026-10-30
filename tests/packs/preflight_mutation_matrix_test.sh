#!/usr/bin/env bash
# tests/packs/preflight_mutation_matrix_test.sh [--legs M1,M2,...] [--dry-run]
#
# The promote-preflight-hang MUTATION MATRIX (phase5 §2, T5.10, AC10).
#
# House rule: "regression pins MUST be mutation-verified" — every pin is
# PLANTED (the defect is re-introduced), shown to turn RED, then restored and
# shown to turn GREEN. The planted digest is recorded.
#
# The matrix covers M1–M32 (the plan-frozen span) PLUS M33–M40, the
# verifier-fix wave appended by the 2026-10-30 fix iteration
# (implementation-decisions.md §10 — the plan set's own span claim is left
# untouched: it is a frozen approval artefact). Each row states its
# verification mode:
#   executed   — planted + RED-proven + restored + GREEN-proven (this runner)
#   deferred   — the pinned behaviour does not exist yet (Pack I / phase 6);
#                recorded with the named symptom, NEVER reported as passed.
#
# SAFETY
#   * Plants are EXACT-STRING replacements with an exactly-once guard
#     (tests/helpers/preflight_mutation_apply.py) — a drifted source fails the
#     leg loudly instead of silently planting nothing.
#   * Every leg backs the target file up, records its sha256 before/after, and
#     restores from the backup; an EXIT trap restores whatever is in flight.
#   * Each pack runs with an outer `timeout` so a mutation that reintroduces
#     the hang cannot wedge the matrix.
#   * The runner NEVER touches the live install and never invokes a pipeline
#     script itself — it only runs the packs (each of which owns its fence).
#
# Usage:
#   bash tests/packs/preflight_mutation_matrix_test.sh                # all legs
#   bash tests/packs/preflight_mutation_matrix_test.sh --legs M1,M6   # a subset
#   bash tests/packs/preflight_mutation_matrix_test.sh --dry-run      # plan only

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APPLY="$REPO_ROOT/tests/helpers/preflight_mutation_apply.py"
RESULTS="${RESULTS:-/tmp/preflight-mutation-matrix.tsv}"
SB="$(mktemp -d "${TMPDIR:-/tmp}/pf-mutmatrix.XXXXXX")" || exit 2

INFLIGHT_FILE=""
restore_inflight() {
    if [ -n "$INFLIGHT_FILE" ] && [ -f "$SB/inflight.bak" ]; then
        cp "$SB/inflight.bak" "$REPO_ROOT/$INFLIGHT_FILE"
        printf 'trap: restored %s\n' "$INFLIGHT_FILE" >&2
    fi
    reclaim_leftover_plants
    case "$SB" in "${TMPDIR:-/tmp}"/pf-mutmatrix.*) rm -rf "$SB" ;; esac
}
trap restore_inflight EXIT INT TERM

PLANT_TARGETS="scripts/upgrade/lib.sh scripts/upgrade/journal_read.py scripts/upgrade/stage.sh scripts/upgrade/promote.sh daemon/tools/upgrade_journal.py daemon/tools/upgrade_tools.py daemon/manager.py daemon/config.py"

# LEAVE-NO-PLANT-BEHIND guard. The legs rewrite REAL tracked files, and a
# SIGKILL (a runaway plant, a tool timeout) can land between the plant and the
# restore — a planted defect left in the tree would poison the next commit.
#   1. refuse to start if any target is already dirty (a pre-existing edit is
#      indistinguishable from a leftover plant);
#   2. after every leg and at exit, every target must be CLEAN vs HEAD; a
#      dirty target is restored with `git checkout --` and reported loudly.
plants_clean() { # prints the offending paths, rc 1 when any is dirty
    local f bad=""
    for f in $PLANT_TARGETS; do
        git -C "$REPO_ROOT" diff --quiet -- "$f" 2>/dev/null || bad="$bad $f"
    done
    [ -z "$bad" ] && return 0
    printf '%s' "$bad"
    return 1
}

assert_plants_clean_or_die() {
    if ! out="$(plants_clean)"; then
        printf 'FATAL: plant targets are DIRTY before the run:%s\n' "$out" >&2
        printf '       commit or revert them first — the matrix will not mix its plants with your edits\n' >&2
        exit 2
    fi
}

reclaim_leftover_plants() {
    local dirty f
    dirty="$(plants_clean)" || {
        for f in $dirty; do
            printf 'PLANT-LEFTOVER: %s differed from HEAD — restoring from git\n' "$f" >&2
            git -C "$REPO_ROOT" checkout -- "$f"
        done
        plants_clean >/dev/null || { printf 'FATAL: could not restore the plant targets\n' >&2; }
    }
}

ONLY=""
DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --legs) ONLY="$2"; shift 2 ;;
        --dry-run) DRY=1; shift ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

# The interpreter that can import/pytest the daemon tree (the worktree has no
# .venv — house convention).
PF_PY=""
for cand in "${PF_PYTHON_OVERRIDE:-}" \
            "$REPO_ROOT/.venv/bin/python" \
            "$REPO_ROOT/../ensemble-src/.venv/bin/python" \
            "$(command -v python3.13 || true)"; do
    [ -n "$cand" ] && [ -x "$cand" ] || continue
    if ( cd "$REPO_ROOT" && "$cand" -c 'import daemon.tools.upgrade_journal' ) >/dev/null 2>&1; then
        PF_PY="$cand"; break
    fi
done
[ -n "$PF_PY" ] || { printf 'FATAL: no interpreter can import the daemon package\n' >&2; exit 2; }

run_pack() { # run_pack <pack-spec> ; prints output, returns rc
    local spec="$1"
    case "$spec" in
        pytest:*)
            ( cd "$REPO_ROOT" && timeout -k 5 600 "$PF_PY" -m pytest \
                "${spec#pytest:}" -q -p no:cacheprovider 2>&1 ) ;;
        pack:*)
            ( cd "$REPO_ROOT" && timeout -k 5 400 bash "tests/packs/${spec#pack:}" 2>&1 ) ;;
        # packi: — a pack invoked in --expect-implemented mode (the phase-6
        # final-gate semantics: a deferral IS a failure). Phase 6 wave 4.
        packi:*)
            ( cd "$REPO_ROOT" && timeout -k 5 400 bash "tests/packs/${spec#packi:}" \
                --expect-implemented 2>&1 ) ;;
        *) printf 'bad pack spec: %s\n' "$spec" >&2; return 2 ;;
    esac
}

# leg | pack | expected-RED substring | extra env (space-separated VAR=VAL)
LEGS="
M1|pack:preflight_journal_scale_test.sh|lib.sh integration failed|JOURNAL_LEGACY_READ_BUDGET_S=5
M2|pack:preflight_journal_parity_test.sh|07-escaped-quote-in-string|
M3|pack:preflight_journal_parity_test.sh|01-canonical-empty-journal|
M4|pack:preflight_journal_parity_test.sh|03-empty-file|
M5|pack:preflight_journal_parity_test.sh|update parity:|
M6|pack:preflight_size_guard_test.sh|G2: rc=|
M7|pack:preflight_journal_parity_test.sh|single read: found|
M8|pack:preflight_repro_test.sh|HEAD wall|
M9|pack:preflight_watchdog_test.sh|D1 rc=|
M10|pack:preflight_watchdog_test.sh|row lost (rows=0)|
M11|pack:preflight_watchdog_test.sh|D1 halt rows=|
M12|pack:preflight_logging_test.sh|E1 stdout not empty|
M13|pack:preflight_logging_test.sh|E2 logwrite_failed rows=|
M14|pytest:tests/unit/tools/test_upgrade_preflight_daemon_side.py::TestPerRunLogTargets::test_payload_append_targets_the_per_run_payload_file|test_payload_append_targets_the_per_run_payload_file|
M15|pack:preflight_stage_lock_test.sh|F2 rows=|
M16|pack:preflight_stage_lock_test.sh|no explicit release before the success exit|
M17|pack:preflight_census_test.sh|a per-character walk exists outside the legacy fallbacks|
M18|pack:preflight_anchor_index_test.sh|anchored constructs disappeared from HEAD|
M19|pytest:tests/unit/tools/test_upgrade_preflight_daemon_side.py::TestKindScopedRuntimeMaxSec::test_promote_arm_carries_the_derived_bound|test_promote_arm_carries_the_derived_bound|
M20|pytest:tests/unit/tools/test_upgrade_preflight_daemon_side.py::TestNoClientPipe::test_client_stderr_is_a_file|test_client_stderr_is_a_file|
M21|pytest:tests/unit/tools/test_upgrade_preflight_daemon_side.py::TestStillRunningRowEnriched::test_reaper_timeout_default_is_still_660|test_reaper_timeout_default_is_still_660|
M29|pack:preflight_size_guard_test.sh|recovery class: rc=|
M30|pack:preflight_size_guard_test.sh|G2 refusal does not name the size|
M31|pack:preflight_size_guard_test.sh|G1: rc=|
M22|packi:preflight_ceremony_readiness_test.sh|DEFERRED_RED C1|
M23|packi:preflight_ceremony_readiness_test.sh|DEFERRED_RED C5|
M24|packi:preflight_ceremony_readiness_test.sh|DEFERRED_RED C6|
M25|packi:preflight_ceremony_readiness_test.sh|I4 FAIL|
M26|packi:preflight_ceremony_readiness_test.sh|I2 FAIL|
M27|packi:preflight_ceremony_readiness_test.sh|I3 FAIL|
M28|packi:preflight_ceremony_readiness_test.sh|I5 FAIL|
M32|packi:preflight_ceremony_readiness_test.sh|DEFERRED_RED I11|
M33|packi:preflight_ceremony_readiness_test.sh|SCENARIO I13 FAIL raised|
M34|packi:preflight_ceremony_readiness_test.sh|SCENARIO I13 FAIL expected=refuse got=arm|
M35|packi:preflight_ceremony_readiness_test.sh|I15 FAIL|
M36|pack:preflight_watchdog_test.sh|A4 halt rows=2|
M37|packi:preflight_ceremony_readiness_test.sh|I16a stamp gate MISMATCH|
M38|packi:preflight_ceremony_readiness_test.sh|I16f stamp gate MISMATCH|
M39|pytest:tests/unit/tools/test_upgrade_tools.py::TestScriptsDirProvenanceArmPath::test_w3a_floor_unset_on_an_explicit_pin_refuses|test_w3a_floor_unset_on_an_explicit_pin_refuses|
M40|pytest:tests/unit/tools/test_upgrade_tools.py::TestScriptsDirProvenanceArmPath::test_w3b_fallback_dir_downgrades_refusal_to_warning|test_w3b_fallback_dir_downgrades_refusal_to_warning|
"

deferred_row() { # deferred_row <id> <symptom>
    printf '%s\tdeferred\t-\t-\t-\t-\t-\t%s\n' "$1" "$2" >> "$RESULTS"
    printf '%s\tdeferred\t-\t-\t-\t-\t-\t%s\n' "$1" "$2" >> "$CUM"
}

assert_plants_clean_or_die
printf 'leg\tmode\tfile\tplanted_sha256\tred_pin\tred_rc\tgreen_rc\tnote\n' > "$RESULTS"
# CUMULATIVE session log (append; header once) so multi-batch runs keep every
# row instead of each batch clobbering the previous one.
CUM="${CUM:-/tmp/preflight-mutation-matrix-all.tsv}"
[ -s "$CUM" ] || printf 'leg\tmode\tfile\tplanted_sha256\tred_pin\tred_rc\tgreen_rc\tnote\n' >> "$CUM"
PASS=0; FAIL=0

while IFS= read -r row; do
    [ -n "$row" ] || continue
    id="${row%%|*}"
    rest="${row#*|}"
    pack="${rest%%|*}"; rest="${rest#*|}"
    expect="${rest%%|*}"; rest="${rest#*|}"
    extra_env="${rest%%|*}"
    if [ -n "$ONLY" ]; then
        case ",$ONLY," in *",$id,"*) : ;; *) continue ;; esac
    fi

    file="$(/usr/bin/python3 "$APPLY" --leg "$id" --file)" || { FAIL=$((FAIL+1)); continue; }
    sha_before="$(shasum -a 256 "$REPO_ROOT/$file" | awk '{print $1}')"
    cp "$REPO_ROOT/$file" "$SB/inflight.bak"
    INFLIGHT_FILE="$file"

    if [ "$DRY" = "1" ]; then
        printf '%s\tdry-run\t%s\t-\t%s\t-\t-\t-\n' "$id" "$file" "$expect" >> "$RESULTS"
        printf '%s\tdry-run\t%s\t-\t%s\t-\t-\t-\n' "$id" "$file" "$expect" >> "$CUM"
        INFLIGHT_FILE=""; continue
    fi

    # ── plant ───────────────────────────────────────────────────────────
    if ! /usr/bin/python3 "$APPLY" --leg "$id" --plant > "$SB/plant.out" 2>&1; then
        printf '%s\tERROR-plant\t%s\t-\t%s\t-\t-\t%s\n' "$id" "$file" "$expect" \
            "$(tr '\n' ' ' < "$SB/plant.out")" >> "$RESULTS"
        printf '%s\tERROR-plant\t%s\t-\t%s\t-\t-\t%s\n' "$id" "$file" "$expect" \
            "$(tr '\n' ' ' < "$SB/plant.out")" >> "$CUM"
        cp "$SB/inflight.bak" "$REPO_ROOT/$file"; INFLIGHT_FILE=""
        FAIL=$((FAIL+1)); continue
    fi
    sha_planted="$(shasum -a 256 "$REPO_ROOT/$file" | awk '{print $1}')"
    if [ "$sha_planted" = "$sha_before" ]; then
        printf '%s\tERROR-plant-noop\t%s\t%s\t%s\t-\t-\tplant changed nothing\n' \
            "$id" "$file" "$sha_planted" "$expect" >> "$RESULTS"
        printf '%s\tERROR-plant-noop\t%s\t%s\t%s\t-\t-\tplant changed nothing\n' \
            "$id" "$file" "$sha_planted" "$expect" >> "$CUM"
        INFLIGHT_FILE=""; FAIL=$((FAIL+1)); continue
    fi

    # ── RED leg ─────────────────────────────────────────────────────────
    # shellcheck disable=SC2086
    RED_OUT="$(env $extra_env bash -c "$(declare -f run_pack); REPO_ROOT='$REPO_ROOT'; PF_PY='$PF_PY'; run_pack '$pack'")"
    RED_RC=$?
    if [ "$RED_RC" -ne 0 ] && printf '%s' "$RED_OUT" | grep -qF "$expect"; then
        red_ok=1
    else
        red_ok=0
    fi

    # ── restore ─────────────────────────────────────────────────────────
    /usr/bin/python3 "$APPLY" --leg "$id" --restore-from "$SB/inflight.bak"
    sha_after="$(shasum -a 256 "$REPO_ROOT/$file" | awk '{print $1}')"
    INFLIGHT_FILE=""

    # ── GREEN leg ───────────────────────────────────────────────────────
    # shellcheck disable=SC2086
    GREEN_OUT="$(env $extra_env bash -c "$(declare -f run_pack); REPO_ROOT='$REPO_ROOT'; PF_PY='$PF_PY'; run_pack '$pack'")"
    GREEN_RC=$?
    green_ok=0
    [ "$GREEN_RC" -eq 0 ] && green_ok=1

    if [ "$sha_after" != "$sha_before" ]; then
        note="RESTORE-MISMATCH"
    elif [ "$red_ok" = "1" ] && [ "$green_ok" = "1" ]; then
        note="ok"; PASS=$((PASS+1))
    else
        note="RED-not-proven(rc=$RED_RC)" ; [ "$red_ok" = "1" ] && note="GREEN-not-proven(rc=$GREEN_RC)"
        FAIL=$((FAIL+1))
    fi
    printf '%s\texecuted\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$id" "$file" "$sha_planted" "$expect" "$RED_RC" "$GREEN_RC" "$note" >> "$RESULTS"
    printf '%s\texecuted\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$id" "$file" "$sha_planted" "$expect" "$RED_RC" "$GREEN_RC" "$note" >> "$CUM"
    printf '  %-4s %-12s red_rc=%-4s green_rc=%-4s %s\n' "$id" "$note" "$RED_RC" "$GREEN_RC" "$expect"
    if [ "$note" = "ok" ]; then MARK=PASS; else MARK=FAIL; fi
    printf '%s: %s (rc=%s)\n' "$id" "$MARK" "$RED_RC"
    [ "$note" = "ok" ] || printf '        red-output head: %s\n' "$(printf '%s' "$RED_OUT" | grep -E 'FAIL|FAILED' | head -2 | tr '\n' ' ')"
done <<LEGSEOF
$LEGS
LEGSEOF

# ── the formerly-deferred legs (Pack I / phase 6) became EXECUTABLE in wave 4
# (the guard landed; see preflight_mutation_apply.py PLANTS M22–M28/M32). The
# span is now the full 32/32 with zero deferrals; this hook remains only to
# surface any NEW deferral loudly instead of letting it pass silently.
_NEW_DEFERRED="$(/usr/bin/python3 "$APPLY" --list 2>/dev/null | awk -F'\t' '$2 == "deferred" {print $1}')"
if [ -n "$_NEW_DEFERRED" ]; then
    for id in $_NEW_DEFERRED; do
        deferred_row "$id" "$( /usr/bin/python3 "$APPLY" --leg "$id" --why 2>&1 | head -1)"
        FAIL=$((FAIL+1))
    done
    printf 'FATAL: the matrix regressed to deferred legs: %s\n' "$_NEW_DEFERRED" >&2
fi

reclaim_leftover_plants
if plants_clean >/dev/null; then
    printf 'TREE: clean — no plant left in the worktree\n'
else
    printf 'TREE: DIRTY — a plant survived; see PLANT-LEFTOVER above\n'; FAIL=$((FAIL+1))
fi
printf '\n-- matrix: %d executed legs green-and-red-proven, %d failures --\n' "$PASS" "$FAIL"
printf 'results: %s\n' "$RESULTS"
if [ "$FAIL" -ne 0 ]; then
    printf 'MUTATION MATRIX: FAIL\n'
    exit 1
fi
if [ "$DRY" = "1" ]; then
    printf 'MUTATION MATRIX: DRY-RUN (nothing planted)\n'
    exit 0
fi
printf 'MUTATION MATRIX: PASS (span M1–M32 complete; deferred rows recorded)\n'
exit 0
