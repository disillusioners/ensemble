#!/usr/bin/env bash
# ============================================================================
# tests/test_pipeline_scripts_floor_stamp.sh — promote-handback-halt Phase 3
# (the DEPLOYMENT pins): the two-sided release-bump requirement.
#
# THE REQUIREMENT (phase3-plan.md §4.3.2, decisions.md §6.2, P3-T07/T08):
# the release bump commit MUST set BOTH
#   - daemon/tools/upgrade_tools.py → PIPELINE_SCRIPTS_FLOOR_COMMIT (the
#     daemon-side arm-time floor), and
#   - scripts/upgrade/lib.sh         → PIPELINE_SCRIPTS_REV (the scripts-side
#     stamp),
# to the SAME merge commit. They are two provenance channels for one fact
# and they travel together (D6-8 / T6.15 / AC15).
#
# Enforcement is mechanical and ALREADY SHIPPED — stage.sh refuses to stage
# (exit 78) when either side is missing, malformed, unresolvable or
# unequal. What was MISSING was the EXECUTABLE PROOF: stage.sh cannot run
# without a full PyInstaller build, so the two verdicts are now exercised
# as functions (the stamp verdict was ALREADY extracted for exactly this
# reason — lib.sh:590-593; the floor verdict is extracted here, mirroring
# it).
#
# PINS:
#   P3-PIN-13  the STAMP gate: 40-hex, resolvable in the tree, and EQUAL to
#              the floor — each violation class refuses (exit-78 class).
#   P3-PIN-14  the FLOOR gate: set, sha-shaped, and an ANCESTOR of the
#              built tree — each violation class refuses; plus the wiring
#              assertion (stage.sh really calls the verdict function, so
#              the extraction is not decorative).
#   P3-T08     the two-sided requirement IN THIS TREE: the daemon constant
#              and the scripts stamp carry the SAME sha, it is 40-hex, it
#              resolves, and it is an ancestor of HEAD — with the mutation
#              leg (one side moved ⇒ the requirement breaks).
#
# The lane does NOT set the constants (the release bump owns the VALUE —
# it can only name a commit that exists once the lane is merged); this
# pack pins the REQUIREMENT executable so the bump cannot half-apply it.
#
# FENCES: read-only git queries against the repo; no live-install contact;
# no daemon; no network; mktemp scratch only.
#
# Run:
#   bash tests/test_pipeline_scripts_floor_stamp.sh
# ============================================================================
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
STAGE_SH="$REPO_ROOT/scripts/upgrade/stage.sh"
UPGRADE_TOOLS="$REPO_ROOT/daemon/tools/upgrade_tools.py"

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}
section() { printf '\n== %s ==\n' "$1"; }

# ── fences ──────────────────────────────────────────────────────────────────
if [ "${POSTGRES_DB:-}" = "ensemble_prod" ]; then
    echo "FENCE ABORT: ambient POSTGRES_DB=ensemble_prod (prod-poisoned env; scrub per-variable and re-run)" >&2
    exit 78
fi
setup_fake_home
preflight_guard

# The verdict functions live in lib.sh; source it once (its own constants
# are harmless in a scratch HOME).
# shellcheck source=scripts/upgrade/lib.sh
. "$LIBSH" >/dev/null 2>&1

# ── the tree's own two constants (the same extraction stage.sh performs) ───
FLOOR_COMMIT="$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f][0-9a-f]\{6,39\}\)"$/\1/p' "$UPGRADE_TOOLS" 2>/dev/null | head -1)"
STAMP_REV="$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$LIBSH" 2>/dev/null | head -1)"
HEAD_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo '')"
# A commit that resolves in this repo but is NOT an ancestor of HEAD (the
# "floor names a commit this release does not contain" violation class).
NON_ANCESTOR="$(git -C "$REPO_ROOT" rev-list --all --not HEAD 2>/dev/null | head -1)"
UNRESOLVABLE="0000000000000000000000000000000000000000"

# ════════════════════════════════════════════════════════════════════════════
section "P3-PIN-13 — the STAMP gate (40-hex, resolvable, EQUAL to the floor)"

OUT="$(_pipeline_scripts_stamp_verdict "$STAMP_REV" "$FLOOR_COMMIT" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-13 the stamp verdict ACCEPTS the tree's own (stamp == floor)" "0" "$RC"

OUT="$(_pipeline_scripts_stamp_verdict "$STAMP_REV" "0000000000000000000000000000000000000001" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-13 a stamp != floor is REFUSED" "1" "$RC"
assert_contains "P3-PIN-13 the refusal names the equality requirement (D6-8)" "travel together" "$OUT"

OUT="$(_pipeline_scripts_stamp_verdict "unreleased-<lane>" "$FLOOR_COMMIT" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-13 a non-hex placeholder stamp is REFUSED" "1" "$RC"
assert_contains "P3-PIN-13 the refusal names the 40-hex rule" "40-hex" "$OUT"

OUT="$(_pipeline_scripts_stamp_verdict "$UNRESOLVABLE" "$UNRESOLVABLE" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-13 an unresolvable stamp is REFUSED" "1" "$RC"
assert_contains "P3-PIN-13 the refusal names the resolution rule" "does not resolve" "$OUT"

# ════════════════════════════════════════════════════════════════════════════
section "P3-PIN-14 — the FLOOR gate (set, sha-shaped, an ANCESTOR of the tree)"

OUT="$(_pipeline_scripts_floor_verdict "$FLOOR_COMMIT" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-14 the floor verdict ACCEPTS the tree's own floor" "0" "$RC"

OUT="$(_pipeline_scripts_floor_verdict "" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-14 an UNSET floor is REFUSED" "1" "$RC"
assert_contains "P3-PIN-14 the refusal names the unset floor" "unset" "$OUT"

OUT="$(_pipeline_scripts_floor_verdict "not-a-sha" "$REPO_ROOT")"
RC=$?
assert_eq "P3-PIN-14 a non-sha floor is REFUSED" "1" "$RC"
assert_contains "P3-PIN-14 the refusal names the sha shape" "not a commit sha" "$OUT"

if [ -n "$NON_ANCESTOR" ]; then
    OUT="$(_pipeline_scripts_floor_verdict "$NON_ANCESTOR" "$REPO_ROOT")"
    RC=$?
    assert_eq "P3-PIN-14 a NON-ancestor floor is REFUSED" "1" "$RC"
    assert_contains "P3-PIN-14 the refusal names the ancestry rule" "NOT an ancestor" "$OUT"
else
    _fail "P3-PIN-14 a non-ancestor commit was available to test with" "one commit not reachable from HEAD" "none found in this clone"
fi

# The wiring: stage.sh must CALL the verdict function (the extraction is
# not decorative — an inline duplicate would leave the pack and the
# ceremony on two code paths).
assert_contains "P3-PIN-14 stage.sh calls the extracted floor verdict" "_pipeline_scripts_floor_verdict" "$(cat "$STAGE_SH")"
assert_contains "P3-PIN-14 stage.sh still refuses with exit 78 on a floor violation" "exit 78" "$(cat "$STAGE_SH")"
assert_contains "P3-PIN-13 stage.sh calls the stamp verdict" "_pipeline_scripts_stamp_verdict" "$(cat "$STAGE_SH")"

# ════════════════════════════════════════════════════════════════════════════
section "P3-T08 — the two-sided requirement IN THIS TREE"

assert_eq "P3-T08 the daemon floor and the scripts stamp carry the SAME sha" "$FLOOR_COMMIT" "$STAMP_REV"
printf '%s' "$FLOOR_COMMIT" | grep -Eq '^[0-9a-f]{40}$' && _pass || _fail "P3-T08 the shared sha is 40-hex" "40-hex" "$FLOOR_COMMIT"
if [ -n "$HEAD_SHA" ] && git -C "$REPO_ROOT" merge-base --is-ancestor "$FLOOR_COMMIT" HEAD >/dev/null 2>&1; then
    _pass
else
    _fail "P3-T08 the shared sha is an ANCESTOR of HEAD (the floor may only name a commit this release contains)" "an ancestor of HEAD" "$FLOOR_COMMIT (HEAD=$HEAD_SHA)"
fi
# The manifest carries the floor (stage.sh:595) — the staged artifact is
# self-describing about the floor it enforces.
assert_contains "P3-T08 the staged manifest carries the floor" "pipeline_scripts_floor_commit" "$(cat "$STAGE_SH")"

# The mutation leg: moving ONE side breaks the requirement (the release
# bump cannot half-apply the two-sided change).
MUT_FLOOR="1111111111111111111111111111111111111111"
OUT="$(_pipeline_scripts_stamp_verdict "$STAMP_REV" "$MUT_FLOOR" "$REPO_ROOT")"
RC=$?
assert_eq "P3-T08 a half-applied bump (stamp != floor) is REFUSED" "1" "$RC"

# ════════════════════════════════════════════════════════════════════════════
section "summary"

printf 'floor=%s\nstamp=%s\nhead=%s\n' "$FLOOR_COMMIT" "$STAMP_REV" "$HEAD_SHA"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
