#!/usr/bin/env bash
# tests/packs/lane_zero_plants_sweep.sh — the LANE-END ZERO-PLANTS SWEEP
# (cycle-3 set F, gate VIII.1: in-place mutation planting contaminated the
# shared worktree four separate times during the 2026-11-20 gate — one
# false D5-2g red, one Wave-6 block, one plant still live at 10:44Z that a
# gate worker had to restore; a plain `git add -A` at finalize would sweep
# a plant into the release line).
#
# The sweep FAILS LOUDLY (exit 1) on ANY of:
#   1. a dirty TRACKED file anywhere in the worktree (a plant, a stray
#      edit — expected at lane end: NOTHING);
#   2. untracked files OUTSIDE the sanctioned evidence directories
#      (.agents/tester/RESULTS/ gate artifacts are the allowed set —
#      pass their basenames as arguments to allowlist them);
#   3. a `# PLANT ` marker in any of the mutation-matrix targets;
#   4. CAS debris from the advance-lock family next to any pin root
#      (.steal-won / .steal-claim. / .retired. / .stale. siblings).
#
# Usage:
#   bash tests/packs/lane_zero_plants_sweep.sh [allowlisted-basename ...]
# Exit 0 = clean; exit 1 = PLANT RESIDUE (names printed, loudly).

set -u
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
rc=0

echo "== zero-plants sweep @ $REPO_ROOT =="

# 1. tracked dirt
DIRTY="$(git -C "$REPO_ROOT" status --porcelain | grep -v '^??' || true)"
if [ -n "$DIRTY" ]; then
    printf 'PLANT-RESIDUE: dirty tracked files:\n%s\n' "$DIRTY"
    rc=1
else
    echo "tracked tree: CLEAN"
fi

# 2. untracked strays (minus the allowlist)
UNTRACKED="$(git -C "$REPO_ROOT" status --porcelain | grep '^??' \
    | awk '{print $2}' || true)"
STRAYS=""
for f in $UNTRACKED; do
    ok=0
    for a in "$@"; do
        case "$f" in *"$a") ok=1; break ;; esac
    done
    [ "$ok" = "1" ] || STRAYS="$STRAYS $f"
done
if [ -n "$STRAYS" ]; then
    printf 'PLANT-RESIDUE: untracked strays outside the allowlist:%s\n' "$STRAYS"
    rc=1
else
    echo "untracked surface: CLEAN (allowlist: $*)"
fi

# 3. planted markers in the mutation targets
MARKS="$(grep -l '# PLANT ' \
    "$REPO_ROOT"/scripts/upgrade/lib.sh \
    "$REPO_ROOT"/scripts/upgrade/stage.sh \
    "$REPO_ROOT"/scripts/upgrade/promote.sh \
    "$REPO_ROOT"/launcher.sh \
    "$REPO_ROOT"/daemon/tools/upgrade_journal.py \
    "$REPO_ROOT"/daemon/tools/upgrade_tools.py \
    "$REPO_ROOT"/daemon/manager.py 2>/dev/null || true)"
if [ -n "$MARKS" ]; then
    printf 'PLANT-RESIDUE: live mutation markers in:%s\n' "$MARKS"
    rc=1
else
    echo "mutation markers: NONE"
fi

# 4. advance-lock CAS debris (any pin-root sibling)
DEBRIS="$(find "$REPO_ROOT/.." -maxdepth 1 \
    \( -name '*.scripts-advance.lock.d.steal-won' \
    -o -name '*.scripts-advance.lock.d.steal-claim.*' \
    -o -name '*.scripts-advance.lock.d.retired.*' \
    -o -name '*.scripts-advance.lock.d.stale.*' \) 2>/dev/null || true)"
if [ -n "$DEBRIS" ]; then
    printf 'PLANT-RESIDUE: advance-lock CAS debris:\n%s\n' "$DEBRIS"
    rc=1
else
    echo "advance-lock debris: NONE"
fi

if [ "$rc" = "0" ]; then
    echo "RESULT: ZERO-PLANTS — tree clean"
else
    echo "RESULT: PLANT RESIDUE FOUND — refusing (fix before finalize)" >&2
fi
exit "$rc"
