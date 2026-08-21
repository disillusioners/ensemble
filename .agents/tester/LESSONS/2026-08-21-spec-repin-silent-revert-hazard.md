# Lesson: silent reverts of spec re-pins + mixed-state test records (r3 provenance hazard aftermath)

Date: 2026-08-21 | Session: post-merge gate for aca8aa2b (RESULTS/2026-08-21-workspace-banner-aca8aa2b.md)

## What happened
The round-3 hide-button session recorded "7/8 PASS" but its spec commits (200de4dc, 9a03ee7d) were stranded on a side branch during an external branch switch (documented in the r3 report as a provenance hazard). At HEAD afterwards:
- `b9a69e13` ("S7 stale-contract fix") had **silently reverted** the S1/S2 round-3 re-pin (the round-3 URL-nav variants live at parent `c6b89c1f`)
- S4/S5/S6/S5b were never re-pinned at all
- Result: 6/8 tests at HEAD expected pre-round-3 semantics ("Hide overlay", chat-hide branch). The r3 "7/8" was obtained on a mixed bundle/spec state and was NOT reproducible at HEAD.

## Detection signals (use next time)
1. A recorded pack result whose cited fix commits are not ancestors of HEAD → treat the record as unverified until re-run.
2. Spec asserts referencing labels/branches that the product's unit suite pins differently (unit `(g)` pinned button-ABSENCE while e2e expected visibility) → spec-vs-spec contract incoherence.
3. `git patch-id` comparison is the cheap tool to check whether "stranded" content exists as a twin at HEAD (200de4dc ≡ c6b89c1f by patch-id).

## Fixes applied
- Spec re-pin (Test Architecture Fix, 345 ins/361 del, commit 81219eaf): S1/S2 restored c6b89c1f URL-nav forms + explicit absence pins; S4/S5/S6/S5b re-pinned to round-3 contract; S3/S7 byte-identical. Pack: 8/8 PASS, 35.8s (was 90s+ with waiting-heavy old forms).

## Rules going forward
- After ANY external branch switch mid-session, re-verify that the spec commits you rely on are ancestors of the branch under test before recording PASS.
- Never record a pack result as authoritative when the spec and bundle were in different states; re-run the pack at a single coherent state first.
- e2e specs pinning UI labels must cross-check the component unit suite's pinned contract before green-lighting.

## Adjacent discovery (env gotcha)
- The workspace in-overlay "Hide workspace" affordance exists ONLY in `builtin` editor mode; env default is `vscode` (iframe). The header `.overlay-hide-btn` is the only hide path present in both modes. Spec S5 now uses the header button and documents this.
- While the workspace overlay (z=100) is open, tab-bar clicks are architecturally blocked by the overlay (by design) — e2e must not assert tab-bar interactivity while the overlay is open; use the sanctioned header-hide/dismiss paths. (This re-attributes part of the r3 S5 "banner intercepts clicks" diagnosis: the banner covered the tab bar visually — fixed by aca8aa2b — but the overlay, not the banner, blocks the click.)
