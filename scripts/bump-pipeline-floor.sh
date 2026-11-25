#!/bin/bash
# ============================================================================
# scripts/bump-pipeline-floor.sh — the SINGLE-WRITER floor/REV bumper
# (self-service-promote Wave 5 §2.6, AC-D4-6; the Wave-6 release lane
# executes it at MERGE time — THIS lane builds the mechanism only and does
# NOT run it against the live pair).
#
# THE GAP THIS CLOSES (§2.6): `scripts/bump_version.py` does NOT maintain
# the pair — PIPELINE_SCRIPTS_REV (scripts/upgrade/lib.sh) and
# PIPELINE_SCRIPTS_FLOOR_COMMIT (daemon/tools/upgrade_tools.py) are
# hand-edited in two files and only CAUGHT at stage time (stage.sh's
# W-2/T6.15 gates refuse exit 78). A hand-edit can half-apply.
#
# THE CONTRACT: ONE sha argument ⇒ BOTH constants rewritten ⇒ ONE commit.
# A half-applied pair is IMPOSSIBLE: every write is anchored (exactly-once
# sed, fail-closed), the two files ride ONE `git commit --only -- <paths>`,
# and the result is re-verified after the commit (floor sha-shaped ∧
# ancestor of HEAD; REV 40-hex ∧ resolvable ∧ == floor; 17-path
# executor-family stamp coverage at the sha EMPTY).
#
# Deliberately OUTSIDE scripts/upgrade/ so the executor family list does
# not widen (§2.6: release-time-only helpers stay outside — this script
# never executes on a ceremony host; the shipped family is unchanged).
#
# Verification order (ALL pre-write, fail-closed):
#   1. the sha is 40-hex;
#   2. it resolves as a commit in THIS repo;
#   3. it is an ANCESTOR of HEAD (the floor may only name a commit this
#      release contains — T6.15/AC15);
#   4. executor_family_verify_stamp_coverage <repo> <sha> is EMPTY (all 17
#      family paths exist at the sha);
#   5. both target constants parse EXACTLY ONCE from their files (a drifted
#      anchor refuses instead of silently writing nothing);
#   6. (idempotence) if both constants ALREADY equal the sha: exit 0 with
#      NO commit — runs BEFORE the dirty check by LEADER RULING (T-POLISH-C,
#      tidier fold 2026-11-20): an idempotent early-exit makes re-runs safe
#      on a tree whose target files may be mid-flight elsewhere (no write,
#      no commit, no dirty-check false refusal for a no-op);
#   7. neither target file is dirty (this commit sweeps ONLY its own two
#      paths — the house concurrent-commit rule).
#
# NO fetch, ever (ADR-009 D3 — builds come from the local checkout).
#
# Usage:
#   bash scripts/bump-pipeline-floor.sh <40-hex-commit-sha>
# Exit codes: 0 = bumped (or already stamped); 1 = refused (nothing
# written, no commit).
# ============================================================================
set -u

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_PATH/.." && pwd)"
LIBSH="$REPO_ROOT/scripts/upgrade/lib.sh"
UTOOLS="$REPO_ROOT/daemon/tools/upgrade_tools.py"
FAMILY="$REPO_ROOT/scripts/upgrade/executor_family.sh"

die() { printf 'bump-pipeline-floor: REFUSED: %s\n' "$*" >&2; exit 1; }
log() { printf 'bump-pipeline-floor: %s\n' "$*" >&2; }

[ $# -eq 1 ] || die "usage: bash scripts/bump-pipeline-floor.sh <40-hex-commit-sha> (got $# args)"
SHA="$1"

# 1. sha shape
printf '%s' "$SHA" | grep -Eq '^[0-9a-f]{40}$' \
    || die "'$SHA' is not a 40-hex commit sha"

# repo sanity
git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 \
    || die "$REPO_ROOT is not a git repo"

# 2. resolves as a commit
git -C "$REPO_ROOT" cat-file -e "${SHA}^{commit}" 2>/dev/null \
    || die "$SHA does not resolve to a commit in this repo (NO fetch is ever issued — the merge commit must already exist locally)"

# 3. ancestor of HEAD (T6.15/AC15)
git -C "$REPO_ROOT" merge-base --is-ancestor "$SHA" HEAD 2>/dev/null \
    || die "$SHA is NOT an ancestor of HEAD — the floor may only name a commit this release itself contains"

# 4. 17-path family coverage at the sha
# shellcheck source=scripts/upgrade/executor_family.sh
. "$FAMILY"
command -v executor_family_verify_stamp_coverage >/dev/null 2>&1 \
    || die "executor_family.sh did not load (executor_family_verify_stamp_coverage undefined at $FAMILY) — coverage cannot be verified"
COV="$(executor_family_verify_stamp_coverage "$REPO_ROOT" "$SHA")"
[ -z "$COV" ] || die "executor-family stamp coverage at $SHA is NON-EMPTY (the merge commit must contain the whole shipped family):"$'\n'"$COV"

# 5. both constants parse EXACTLY ONCE (anchored, fail-closed)
CUR_REV="$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$LIBSH" 2>/dev/null | head -1)"
[ -n "$CUR_REV" ] || die "PIPELINE_SCRIPTS_REV does not parse from $LIBSH (anchored sed found no line — the anchor drifted)"
REV_MATCHES="$(grep -cE '^PIPELINE_SCRIPTS_REV=' "$LIBSH")"
[ "$REV_MATCHES" = "1" ] || die "PIPELINE_SCRIPTS_REV matches $REV_MATCHES lines in lib.sh (want exactly 1 — the anchor drifted)"
FLOOR_MATCHES="$(grep -cE '^PIPELINE_SCRIPTS_FLOOR_COMMIT = "' "$UTOOLS")"
[ "$FLOOR_MATCHES" = "1" ] || die "PIPELINE_SCRIPTS_FLOOR_COMMIT matches $FLOOR_MATCHES lines in upgrade_tools.py (want exactly 1 — the anchor drifted)"
CUR_FLOOR="$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f]\{6,40\}\)"$/\1/p' "$UTOOLS" | head -1)"
[ -n "$CUR_FLOOR" ] || die "PIPELINE_SCRIPTS_FLOOR_COMMIT's current value does not parse as a hex sha from $UTOOLS (the anchor drifted — refusing BEFORE any write so the pair can never half-apply)"

# 6. idempotence (T-POLISH-C: renumbered to match execution order)
if [ "$CUR_REV" = "$SHA" ] \
   && [ "$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f]*\)"$/\1/p' "$UTOOLS" | head -1)" = "$SHA" ]; then
    log "both constants already equal $SHA — nothing to do (no commit)"
    exit 0
fi

# 7. neither target file dirty (the commit sweeps ONLY its two paths)
git -C "$REPO_ROOT" diff --quiet -- scripts/upgrade/lib.sh 2>/dev/null \
    || die "scripts/upgrade/lib.sh has uncommitted changes — commit or revert them first (this bumper never sweeps foreign work)"
git -C "$REPO_ROOT" diff --quiet -- daemon/tools/upgrade_tools.py 2>/dev/null \
    || die "daemon/tools/upgrade_tools.py has uncommitted changes — commit or revert them first"

# ── the write: BOTH constants, anchored, each exactly-once ──────────────────
if ! sed -i.bump-tmp "s/^PIPELINE_SCRIPTS_REV=[0-9A-Za-z._/-]\{1,\}$/PIPELINE_SCRIPTS_REV=$SHA/" "$LIBSH"; then
    rm -f "$LIBSH.bump-tmp"
    die "the lib.sh rewrite failed"
fi
rm -f "$LIBSH.bump-tmp"
grep -q "^PIPELINE_SCRIPTS_REV=$SHA$" "$LIBSH" \
    || die "the lib.sh rewrite did not land (post-write verify failed — restore scripts/upgrade/lib.sh from git and investigate)"

if ! sed -i.bump-tmp "s/^PIPELINE_SCRIPTS_FLOOR_COMMIT = \"[0-9a-f]*\"$/PIPELINE_SCRIPTS_FLOOR_COMMIT = \"$SHA\"/" "$UTOOLS"; then
    rm -f "$UTOOLS.bump-tmp"
    die "the upgrade_tools.py rewrite failed (lib.sh is DIRTY now — restore it: git -C $REPO_ROOT checkout -- scripts/upgrade/lib.sh)"
fi
rm -f "$UTOOLS.bump-tmp"
grep -q "^PIPELINE_SCRIPTS_FLOOR_COMMIT = \"$SHA\"$" "$UTOOLS" \
    || die "the upgrade_tools.py rewrite did not land (post-write verify failed — restore BOTH files from git)"

# pre-commit verify: the pair is WHOLE before the commit exists
NEW_REV="$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$LIBSH" | head -1)"
NEW_FLOOR="$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f]*\)"$/\1/p' "$UTOOLS" | head -1)"
if [ "$NEW_REV" != "$SHA" ] || [ "$NEW_FLOOR" != "$SHA" ]; then
    die "pre-commit verify failed: REV=$NEW_REV FLOOR=$NEW_FLOOR (want both == $SHA — the working tree carries whatever DID land; inspect git status)"
fi

# ── ONE commit (explicit paths only — never sweep foreign staged work) ──────
if ! ( cd "$REPO_ROOT" \
       && git add scripts/upgrade/lib.sh daemon/tools/upgrade_tools.py \
       && git commit --only -q \
            -m "chore(release): bump PIPELINE_SCRIPTS_REV + PIPELINE_SCRIPTS_FLOOR_COMMIT to $SHA (single-writer pair bump)" \
            -- scripts/upgrade/lib.sh daemon/tools/upgrade_tools.py ); then
    die "the pair commit failed — working tree carries BOTH rewrites uncommitted; commit them by hand or restore: git -C $REPO_ROOT checkout -- scripts/upgrade/lib.sh daemon/tools/upgrade_tools.py"
fi

# ── post-commit re-verify ────────────────────────────────────────────────────
NEW_HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true)"
git -C "$REPO_ROOT" merge-base --is-ancestor "$SHA" HEAD 2>/dev/null \
    || die "post-commit verify failed: $SHA is not an ancestor of the new HEAD $NEW_HEAD"
[ "$(sed -n 's/^PIPELINE_SCRIPTS_REV="\{0,1\}\([0-9A-Za-z._/-]*\)"\{0,1\}[[:space:]]*$/\1/p' "$LIBSH" | head -1)" = "$SHA" ] \
    || die "post-commit verify failed: the REV constant is not $SHA"
[ "$(sed -n 's/^PIPELINE_SCRIPTS_FLOOR_COMMIT = "\([0-9a-f]*\)"$/\1/p' "$UTOOLS" | head -1)" = "$SHA" ] \
    || die "post-commit verify failed: the FLOOR constant is not $SHA"
POST_COV="$(executor_family_verify_stamp_coverage "$REPO_ROOT" "$SHA")"
[ -z "$POST_COV" ] || die "post-commit verify failed: family coverage at $SHA became non-empty"

log "bumped BOTH constants to $SHA in ONE commit ($NEW_HEAD); the pair cannot half-apply"
exit 0
