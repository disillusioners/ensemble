#!/usr/bin/env bash
# tests/packs/preflight_anchor_index_test.sh
#
# T0.1 / AC1 / phase0 P0-D4 — the anchor index is re-verified BY A SCRIPT.
#
# Two independent assertions per row of tests/packs/preflight_anchor_index.txt:
#   (A) LINE-EXACT at the PINNED REV: `git show $PF_BASE_REV:<path>` line N
#       contains the literal. This proves the plan's index is correct at the
#       rev it claims, immune to any edit this lane makes.
#   (B) CONSTRUCT-PRESENT at HEAD: the literal still appears somewhere in the
#       working tree's file. The three refactored verbs survive verbatim as
#       the `*_legacy` fallbacks, so this holds for every row — a row that
#       VANISHES means a construct was deleted rather than moved.
# Plus (C) the NEW constructs this lane introduces.
# Plus (D) a mutation self-test: a construct deleted from a scratch copy makes
# the checker exit non-zero (a checker that cannot fail is not a check).
#
# Usage: bash tests/packs/preflight_anchor_index_test.sh [--check-only]

set -u
PF_PACK_NAME="preflight-anchor-index"
. "$(cd "$(dirname "$0")/.." && pwd)/helpers/preflight_pack_common.sh"

# SELF-EXEC SAFETY (the 2026-10-30 recursion incident): the mutation
# self-test re-invokes this script with the EXPLICIT ARGUMENT `--check-only`,
# a STRUCTURALLY non-recursive entrypoint (it contains no mutation section at
# all). The earlier env-prefix guard did not converge (805 processes).
PF_CHECK_ONLY=0
for a in "$@"; do
    case "$a" in
        --check-only) PF_CHECK_ONLY=1 ;;
        *) pf_die "unknown argument: $a" ;;
    esac
done

pf_fence_init
trap pf_fence_cleanup EXIT

TABLE="$PF_REPO_ROOT/tests/packs/preflight_anchor_index.txt"
WS="${PF_ANCHOR_WS:-$PF_REPO_ROOT}"
REV="${PF_ANCHOR_REV:-$PF_BASE_REV}"

[ -f "$TABLE" ] || pf_die "anchor table missing: $TABLE"

_rows() { grep -v '^[[:space:]]*#' "$TABLE" | grep -v '^[[:space:]]*$'; }

# ── (A) line-exact at the pinned rev ───────────────────────────────────────
printf '== A. line-exact at %s ==\n' "$REV"
MISMATCH=0
ROWCOUNT=0
while IFS=$'\t' read -r path line pat what; do
    [ -n "${path:-}" ] || continue
    ROWCOUNT=$((ROWCOUNT + 1))
    actual="$(cd "$PF_REPO_ROOT" && git show "$REV:$path" 2>/dev/null | sed -n "${line}p")"
    case "$actual" in
        *"$pat"*) : ;;
        *)
            MISMATCH=$((MISMATCH + 1))
            printf '  MISS %s:%s  want=[%s]  got=[%s]  (%s)\n' "$path" "$line" "$pat" "$actual" "$what"
            ;;
    esac
done < <(_rows)
if [ "$MISMATCH" -eq 0 ]; then
    pf_ok "all $ROWCOUNT anchors are line-exact at $REV"
else
    pf_bad "$MISMATCH/$ROWCOUNT anchors drifted from $REV"
fi

# ── (B) construct-present at HEAD ─────────────────────────────────────────
printf '\n== B. construct-present in %s ==\n' "$WS"
# RELOCATED-AT-HEAD rows (phase-2/3 wraps): a base anchor whose construct
# the lane WRAPPED survives at HEAD in the wrapped form (moved != deleted).
# Key = the base literal; value = the new form that must be present at HEAD.
head_alias_for() {
    case "$1" in
        'J="$(journal_read)"')
            printf '%s' 'J="$(_run_bounded_step journal-read journal_read)"' ;;
        'CUR_JSON="$(journal_read)"')
            printf '%s' 'CUR_JSON="$(_run_bounded_step journal-read-current journal_read)"' ;;
        *) return 1 ;;
    esac
}
GONE=0
while IFS=$'\t' read -r path line pat what; do
    [ -n "${path:-}" ] || continue
    if [ ! -f "$WS/$path" ]; then
        GONE=$((GONE + 1)); printf '  GONE %s (file missing)\n' "$path"; continue
    fi
    if ! grep -qF -- "$pat" "$WS/$path" 2>/dev/null; then
        if alias_pat="$(head_alias_for "$pat")" && grep -qF -- "$alias_pat" "$WS/$path" 2>/dev/null; then
            printf '  MOVE %s  relocated-at-HEAD (%s)\n' "$path" "$what"
            continue
        fi
        GONE=$((GONE + 1))
        printf '  GONE %s  pattern=[%s]  (%s)\n' "$path" "$pat" "$what"
    fi
done < <(_rows)
if [ "$GONE" -eq 0 ]; then
    pf_ok "every anchored construct is still present at HEAD (moved != deleted)"
else
    pf_bad "$GONE anchored constructs disappeared from HEAD"
fi

# ── (C) the NEW constructs this lane introduces ───────────────────────────
printf '\n== C. new constructs (D1) ==\n'
newcheck() { # newcheck <path> <pattern> <label>
    if grep -qF -- "$2" "$WS/$1" 2>/dev/null; then pf_ok "present: $3"; else pf_bad "MISSING: $3 ($1 :: $2)"; fi
}
newcheck scripts/upgrade/journal_read.py 'def _balance_scan(data):' 'the string-AWARE balance scan (verify)'
newcheck scripts/upgrade/journal_read.py 'def _json_sub(data, key):' 'the string-NAIVE bracket counter (sub)'
newcheck scripts/upgrade/journal_read.py 'def _splice_value(data, field, raw):' 'the string-NAIVE value consumer (update)'
newcheck scripts/upgrade/journal_read.py 'NUL_WARNING = ' 'the command-substitution NUL-drop warning class'
newcheck scripts/upgrade/lib.sh '_journal_read_probe()' 'the O(N) helper resolver/probe'
newcheck scripts/upgrade/lib.sh '_journal_read_legacy()' 'the legacy walk fallback'
newcheck scripts/upgrade/lib.sh '_journal_read_fallback()' 'the guarded fallback (size guard + bound)'
newcheck scripts/upgrade/lib.sh 'JOURNAL_SIZE_GUARD_BYTES="${JOURNAL_SIZE_GUARD_BYTES:-98304}"' 'the guard constant'
newcheck scripts/upgrade/lib.sh 'JOURNAL_LEGACY_READ_BUDGET_S="${JOURNAL_LEGACY_READ_BUDGET_S:-120}"' 'the legacy-walk bound'
newcheck scripts/upgrade/lib.sh '_json_sub_legacy()' 'the _json_sub fallback'
newcheck scripts/upgrade/lib.sh '_journal_update_legacy_from()' 'the journal_update fallback'
newcheck scripts/upgrade/lib.sh '_stage_tree_walk()' 'the bounded heartbeating stage walk'
newcheck scripts/upgrade/lib.sh 'STAGE_TREE_BUDGET_S="${STAGE_TREE_BUDGET_S:-60}"' 'the stage walk budget'
newcheck scripts/upgrade/promote.sh 'JOURNAL_READ_STRICT=1' 'the promote-only strict marker'
newcheck scripts/upgrade/stage.sh '_stage_tree_walk agents' 'stage uses the bounded walk (agents)'
newcheck scripts/upgrade/stage.sh '_stage_tree_walk plugins' 'stage uses the bounded walk (plugins)'
printf '\n== C2. phase-2/3 constructs ==\n'
newcheck scripts/upgrade/lib.sh 'PREFLIGHT_STEP_BUDGET_S="${PREFLIGHT_STEP_BUDGET_S:-120}"' 'step budget (phase 2)'
newcheck scripts/upgrade/lib.sh 'PREFLIGHT_PHASE_BUDGET_S="${PREFLIGHT_PHASE_BUDGET_S:-300}"' 'phase budget (phase 2)'
newcheck scripts/upgrade/lib.sh '_budget_breach()' 'the L2a breach handler (journal-first order)'
newcheck scripts/upgrade/lib.sh '_run_bounded_step()' 'the per-step bounded wrapper'
newcheck scripts/upgrade/lib.sh '_budget_in_force()' 'the value-in-force resolver'
newcheck scripts/upgrade/lib.sh '_HALT_JOURNALED=0' 'the halt re-entrancy guard (D2-4)'
newcheck scripts/upgrade/lib.sh '_df_systemctl()' 'the bounded dual-fight probe (T2.7)'
newcheck scripts/upgrade/promote.sh 'preflight_deadline=$(( $(_now_epoch) + PREFLIGHT_PHASE_BUDGET_S ))' 'the preflight phase deadline (T2.5)'
newcheck scripts/upgrade/promote.sh 'exceeds 300 on the live lane' 'the live soak refusal (T2.17)'
newcheck scripts/upgrade/status.sh 'a read-only tool never files itself as a' 'status.sh exits 1 (SC4b)'
newcheck daemon/tools/upgrade_journal.py 'def derived_runtime_max_sec(' 'the derived RuntimeMaxSec (D2-9)'

# the trap-install ORDER at HEAD: handlers BEFORE lock_acquire (D2-7)
TRAP_LINE="$(grep -n '_trap_install_signal_handlers "promote:preflight"' "$WS/scripts/upgrade/promote.sh" | head -1 | cut -d: -f1)"
LOCK_LINE="$(grep -n '^if ! lock_acquire; then' "$WS/scripts/upgrade/promote.sh" | head -1 | cut -d: -f1)"
[ -n "$TRAP_LINE" ] && [ -n "$LOCK_LINE" ] && [ "$TRAP_LINE" -lt "$LOCK_LINE" ] \
    && pf_ok "trap handlers installed BEFORE lock_acquire (lines $TRAP_LINE < $LOCK_LINE)" \
    || pf_bad "trap/lock order wrong (trap=$TRAP_LINE lock=$LOCK_LINE)"

# the strict marker must NOT be exported (child scripts must not inherit it)
if grep -qE '^[[:space:]]*export[[:space:]]+JOURNAL_READ_STRICT' "$WS/scripts/upgrade/promote.sh"; then
    pf_bad "JOURNAL_READ_STRICT is EXPORTED in promote.sh (child scripts would inherit a promote-only refusal)"
else
    pf_ok "JOURNAL_READ_STRICT is a plain (non-exported) shell variable"
fi

# ── (D) mutation self-test (structurally non-recursive) ───────────────────
printf '\n== D. mutation self-test ==\n'
if [ "$PF_CHECK_ONLY" = "1" ]; then
    pf_note "mutation self-test skipped (--check-only entrypoint)"
else
MUT="$PF_SB/mutant"
mkdir -p "$MUT"
(cd "$WS" && tar cf - scripts/upgrade 2>/dev/null) | (cd "$MUT" && tar xf -) 2>/dev/null
if [ -f "$MUT/scripts/upgrade/lib.sh" ]; then
    /usr/bin/python3 - "$MUT/scripts/upgrade/lib.sh" <<'PYEOF'
import sys
p = sys.argv[1]
lines = open(p, encoding="utf-8").read().splitlines(True)
out = [l for l in lines if not l.startswith("_json_sub_legacy() {")]
open(p, "w", encoding="utf-8").write("".join(out))
PYEOF
    if PF_ANCHOR_WS="$MUT" bash "$0" --check-only >/dev/null 2>&1; then
        pf_bad "mutation self-test: the checker PASSED with a construct deleted (it is vacuous)"
    else
        pf_ok "mutation self-test: the checker FAILS when an anchored construct is deleted"
    fi
else
    pf_bad "mutation self-test could not stage a scratch copy"
fi
fi

pf_summary
