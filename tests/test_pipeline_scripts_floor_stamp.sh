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
section "D4-6 — the single-writer pair bumper (scripts/bump-pipeline-floor.sh)"

# AC-D4-6: ONE command writes BOTH constants in ONE commit and re-verifies
# (floor sha-shaped ∧ ancestor; REV 40-hex ∧ resolvable ∧ == floor; 17-path
# stamp coverage empty); a half-applied pair is IMPOSSIBLE. The bumper is
# deliberately OUTSIDE scripts/upgrade/ so the family list does not widen.
# Wave 5 builds the MECHANISM only — these legs run it against FIXTURE
# CLONES in mktemp, never against this tree's live pair (which the final
# leg asserts is byte-untouched).

BUMPER="$REPO_ROOT/scripts/bump-pipeline-floor.sh"
assert_contains "D4-6a the bumper exists OUTSIDE scripts/upgrade/" "scripts/bump-pipeline-floor.sh" "$BUMPER"
if [ -e "$REPO_ROOT/scripts/upgrade/bump-pipeline-floor.sh" ]; then
    _fail "D4-6a no copy may live inside scripts/upgrade/ (the family list must not widen)" "absent" "present"
else
    _pass
fi
if bash -n "$BUMPER" 2>/dev/null; then _pass; else _fail "D4-6a the bumper passes bash -n"; fi

# The fixture: a MINI repo with both constants + the family + the bumper.
BUMP_FIX="$(mktemp -d "${TMPDIR:-/tmp}/bump-floor-fixture.XXXXXX.$$")" || exit 2
_bump_fixture() { # <root> <floor_line_in_utools>
    local root="$1" utools_line="$2" p
    mkdir -p "$root/scripts/upgrade" "$root/daemon/tools"
    cp "$REPO_ROOT/scripts/upgrade/executor_family.sh" "$root/scripts/upgrade/executor_family.sh"
    cp "$BUMPER" "$root/scripts/bump-pipeline-floor.sh"
    while IFS= read -r p; do
        [ -n "$p" ] || continue
        # never clobber the REAL family library copy made above (it IS a
        # family member — the loop must not stub it out)
        [ "$p" = "scripts/upgrade/executor_family.sh" ] && continue
        mkdir -p "$root/$(dirname "$p")"
        printf '#!/usr/bin/env bash\n# bump fixture\n' > "$root/$p"
    done <<EOF
$(_ef_paths)
EOF
    printf '#!/usr/bin/env bash\nPIPELINE_SCRIPTS_REV=%s\n' \
        "2222222222222222222222222222222222222222" > "$root/scripts/upgrade/lib.sh"
    printf 'UTOOLS = 1\nPIPELINE_SCRIPTS_FLOOR_COMMIT = "%s"\n' \
        "$utools_line" > "$root/daemon/tools/upgrade_tools.py"
    (
        cd "$root" || exit 1
        git init -q -b main . >/dev/null 2>&1
        git config user.email b@t; git config user.name b
        git add -A >/dev/null 2>&1
        git commit -q -m base >/dev/null 2>&1
    ) >/dev/null 2>&1
}
# the family list via the fixture's own executor_family.sh copy
_ef_paths() { bash "$BUMP_FIX/scripts/upgrade/executor_family.sh" --paths 2>/dev/null; }

# bootstrap the fixture root once for _ef_paths, then build the real one
mkdir -p "$BUMP_FIX/scripts/upgrade"
cp "$REPO_ROOT/scripts/upgrade/executor_family.sh" "$BUMP_FIX/scripts/upgrade/executor_family.sh"

OLD_FLOOR="3333333333333333333333333333333333333333"
_bump_fixture "$BUMP_FIX/repo" "$OLD_FLOOR"
BREPO="$BUMP_FIX/repo"
# the lane-merge commit T (ancestor of HEAD; family fully present)
(
    cd "$BREPO" || exit 1
    printf '#!/usr/bin/env bash\n# lane change\nPIPELINE_SCRIPTS_REV=%s\n' \
        "4444444444444444444444444444444444444444" > scripts/upgrade/lib.sh
    git add -A >/dev/null 2>&1
    git commit -q -m "lane merge" >/dev/null 2>&1
    printf '# post-merge touch\n' >> daemon/tools/upgrade_tools.py
    git add -A >/dev/null 2>&1
    git commit -q -m "post-merge" >/dev/null 2>&1
) >/dev/null 2>&1
BUMP_T="$(git -C "$BREPO" rev-parse HEAD~1 2>/dev/null || true)"
BUMP_HEAD_BEFORE="$(git -C "$BREPO" rev-parse HEAD 2>/dev/null || true)"

# D4-6b: the happy path — rc 0, BOTH constants == T, ONE commit, exactly
# the two paths, and the post-commit re-verifies passed.
BUMP_OUT="$(bash "$BREPO/scripts/bump-pipeline-floor.sh" "$BUMP_T" 2>&1)"
BUMP_RC=$?
assert_eq "D4-6b the bumper succeeds on a valid ancestor" "0" "$BUMP_RC"
assert_contains "D4-6b the success line names the pair" "cannot half-apply" "$BUMP_OUT"
assert_eq "D4-6b lib.sh REV == T" "PIPELINE_SCRIPTS_REV=$BUMP_T" \
    "$(grep '^PIPELINE_SCRIPTS_REV=' "$BREPO/scripts/upgrade/lib.sh")"
assert_eq "D4-6b upgrade_tools FLOOR == T" "PIPELINE_SCRIPTS_FLOOR_COMMIT = \"$BUMP_T\"" \
    "$(grep '^PIPELINE_SCRIPTS_FLOOR_COMMIT' "$BREPO/daemon/tools/upgrade_tools.py")"
BUMP_HEAD_AFTER="$(git -C "$BREPO" rev-parse HEAD 2>/dev/null || true)"
[ "$BUMP_HEAD_AFTER" != "$BUMP_HEAD_BEFORE" ] && _pass || _fail "D4-6b a new commit exists"
BUMP_COMMIT_FILES="$(git -C "$BREPO" show --name-only --pretty=format: HEAD 2>/dev/null | sed '/^$/d' | sort | tr '\n' ' ')"
assert_eq "D4-6b the ONE commit touches exactly the two constants" \
    "daemon/tools/upgrade_tools.py scripts/upgrade/lib.sh " "$BUMP_COMMIT_FILES"

# D4-6c: idempotence — re-running the SAME sha is a no-op (no new commit)
BUMP_OUT2="$(bash "$BREPO/scripts/bump-pipeline-floor.sh" "$BUMP_T" 2>&1)"
BUMP_RC2=$?
assert_eq "D4-6c the re-run exits 0" "0" "$BUMP_RC2"
assert_contains "D4-6c the re-run says nothing to do" "nothing to do" "$BUMP_OUT2"
assert_eq "D4-6c no second commit" "$BUMP_HEAD_AFTER" "$(git -C "$BREPO" rev-parse HEAD)"

# D4-6d: a non-sha refuses BEFORE any write (nothing modified, no commit)
BAD_OUT="$(bash "$BREPO/scripts/bump-pipeline-floor.sh" "not-a-sha" 2>&1)"
BAD_RC=$?
assert_eq "D4-6d a non-sha refuses (rc 1)" "1" "$BAD_RC"
assert_eq "D4-6d no commit appeared" "$BUMP_HEAD_AFTER" "$(git -C "$BREPO" rev-parse HEAD)"

# D4-6e: a NON-ancestor refuses (T6.15 — the floor names a commit the
# release contains)
(
    cd "$BREPO" || exit 1
    git checkout -q --orphan divergent >/dev/null 2>&1
    printf 'x\n' > orphan.txt
    git add -A >/dev/null 2>&1
    git commit -q -m orphan-root >/dev/null 2>&1
    git checkout -q main >/dev/null 2>&1
) >/dev/null 2>&1
ORPHAN_T="$(git -C "$BREPO" rev-parse divergent 2>/dev/null || true)"
NA_OUT="$(bash "$BREPO/scripts/bump-pipeline-floor.sh" "$ORPHAN_T" 2>&1)"
NA_RC=$?
assert_eq "D4-6e a non-ancestor refuses (rc 1)" "1" "$NA_RC"
assert_contains "D4-6e the refusal names the ancestry rule" "NOT an ancestor" "$NA_OUT"
assert_eq "D4-6e lib.sh UNTOUCHED by the refusal" "PIPELINE_SCRIPTS_REV=$BUMP_T" \
    "$(grep '^PIPELINE_SCRIPTS_REV=' "$BREPO/scripts/upgrade/lib.sh")"

# D4-6f: HALF-APPLY IMPOSSIBILITY — a drifted anchor in upgrade_tools.py
# refuses BEFORE any write (lib.sh stays untouched: no partial pair).
HALF_FIX="$BUMP_FIX/half"
_bump_fixture "$HALF_FIX" "drifted-constant"
(
    cd "$HALF_FIX" || exit 1
    git add -A >/dev/null 2>&1
    git commit -q -m drift >/dev/null 2>&1
) >/dev/null 2>&1
HALF_T="$(git -C "$HALF_FIX" rev-parse HEAD~0 2>/dev/null || true)"
HALF_OUT="$(bash "$HALF_FIX/scripts/bump-pipeline-floor.sh" "$HALF_T" 2>&1)"
HALF_RC=$?
assert_eq "D4-6f a drifted FLOOR anchor refuses (rc 1)" "1" "$HALF_RC"
assert_contains "D4-6f the refusal names the drifted anchor" "anchor drifted" "$HALF_OUT"
assert_eq "D4-6f lib.sh NOT half-written" "PIPELINE_SCRIPTS_REV=2222222222222222222222222222222222222222" \
    "$(grep '^PIPELINE_SCRIPTS_REV=' "$HALF_FIX/scripts/upgrade/lib.sh")"

# D4-6g: coverage hole — a T missing a family path refuses
HOLE_FIX="$BUMP_FIX/hole"
_bump_fixture "$HOLE_FIX" "$OLD_FLOOR"
(
    cd "$HOLE_FIX" || exit 1
    git rm -q scripts/upgrade/status.sh >/dev/null 2>&1
    git commit -q -m hole >/dev/null 2>&1
    printf 'y\n' >> daemon/tools/upgrade_tools.py
    git add -A >/dev/null 2>&1
    git commit -q -m after-hole >/dev/null 2>&1
) >/dev/null 2>&1
HOLE_T="$(git -C "$HOLE_FIX" rev-parse HEAD~1 2>/dev/null || true)"
HOLE_OUT="$(bash "$HOLE_FIX/scripts/bump-pipeline-floor.sh" "$HOLE_T" 2>&1)"
HOLE_RC=$?
assert_eq "D4-6g a family hole at T refuses (rc 1)" "1" "$HOLE_RC"
assert_contains "D4-6g the refusal names the missing path" "scripts/upgrade/status.sh" "$HOLE_OUT"

# D4-6h: dirty-target refusal (the bumper never sweeps foreign work)
printf '# dirty edit\n' >> "$BREPO/scripts/upgrade/lib.sh"
DIRTY_OUT="$(bash "$BREPO/scripts/bump-pipeline-floor.sh" "$BUMP_HEAD_AFTER" 2>&1)"
DIRTY_RC=$?
assert_eq "D4-6h a dirty target file refuses (rc 1)" "1" "$DIRTY_RC"
assert_contains "D4-6h the refusal names the dirty file" "uncommitted changes" "$DIRTY_OUT"
git -C "$BREPO" checkout -- scripts/upgrade/lib.sh 2>/dev/null || true

# D4-6i (BUILD-ONLY guarantee): THIS tree's live pair is byte-UNTOUCHED —
# the bumper ran only inside fixture clones; Wave 6 owns the real stamp.
assert_eq "D4-6i the live tree's stamp is unchanged" "$STAMP_REV" \
    "$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$LIBSH" | head -1)"
assert_eq "D4-6i the live tree's floor is unchanged" "$FLOOR_COMMIT" \
    "$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f][0-9a-f]\{6,39\}\)"$/\1/p' "$UPGRADE_TOOLS" | head -1)"
rm -rf "$BUMP_FIX"

# ════════════════════════════════════════════════════════════════════════════
section "summary"

printf 'floor=%s\nstamp=%s\nhead=%s\n' "$FLOOR_COMMIT" "$STAMP_REV" "$HEAD_SHA"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
