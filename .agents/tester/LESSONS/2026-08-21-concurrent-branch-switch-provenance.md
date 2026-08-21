# Lesson: Concurrent external branch-switch during a verification session — commit provenance drift

Date: 2026-08-21 | Session: round-3 hide-button verification (fix/hide-button-editor-only)

## What happened
Mid-session (~15:35–15:45Z), while my e2e workers were committing spec fixes, an EXTERNAL actor
(developer working plane-sync) switched the checked-out branch from `fix/hide-button-editor-only`
to a NEW branch `fix/plane-sync-auth` (off the same base 053bfb22) and left `daemon/clients/plane_http_client.py`
dirty. Two of the three tester spec commits (200de4dc, 9a03ee7d) landed on `fix/plane-sync-auth`;
only 2ff77d52 reached `fix/hide-button-editor-only`. No worker error — `git commit` targets the
checked-out HEAD, which silently changed between dispatches.

## Why it matters
- Pack verdicts stayed VALID: the FE serves the working tree, and the 5 round-3 fix files were
  uncommitted and carried across the checkout unchanged — every live run exercised the fix under test.
- Commit provenance did NOT: "worker committed to branch X" is only true if no one re-points HEAD
  between spawn and commit. Report claims about commit→branch mapping require a FINAL provenance
  snapshot, not per-worker assertions.

## Rule going forward
1. **Pin provenance at session end**: final worker runs `git branch -vv`, `git merge-base
   --is-ancestor <sha> <branch>` per tester commit, and `git diff --stat <branch> -- <fix files>`.
   Report the actual topology, never the intended one.
2. **Watch for the smell**: a pack worker reporting a branch name different from the session's
   branch (e.g. "working tree on fix/plane-sync-auth" from the regression worker) = immediate
   provenance check, before the next dispatch if possible.
3. **No git surgery under concurrent operation**: never cherry-pick/reset while another actor is
   mid-flight; surface the topology + recommended repair (cherry-pick list) to the leader instead.
4. Uncommitted-fix + shared-checkout sessions: the working tree is the source of behavioral truth;
   branch tips are only bookkeeping until the owner commits.

## Cost
Zero wasted test runs; one provenance-repair action item (cherry-pick 200de4dc + 9a03ee7d) that
would have silently produced a wrong report claim if the final snapshot had been skipped.

Refs: RESULTS/2026-08-21-hide-button-editor-only-r3.md (Provenance Hazard section); worker 1681292f snapshot.
