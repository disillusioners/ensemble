# Phase 4 Gate Evidence — gc-fe-surfaces

Base: 0181dadbf8641c60d7158ad59edfa4a4b36944c8 (v0.31.0)
HEAD: d66b2e260 (feature/gc-fe-surfaces)
Worktree: /home/nea/ensemble-src-wt-gc-fe-surfaces

## Bootstrap (Task 0)
- uv sync --frozen: "Checked 117 packages in 9ms" (worktree .venv present)
- npm ci: completed (lockfile; no new deps)
- jest --version: 30.2.0; tsc --version: 5.9.3

## Base evidence (Task 1) — /tmp/gc_fe_base_evidence/
- Base production build EXIT 0; budget warn set: initial bundle (5.91 MB vs 1.00 MB) + 9 stylesheets:
  agent-selector 8.77 kB, instance-list 10.17 kB, snapshot-detail-drawer 8.70 kB,
  add-source-modal 8.32 kB, checkpoint-cleanup 8.70 kB, snapshots 12.29 kB,
  jobs 14.77 kB, blueprint 18.32 kB, chat-interface 16.15 kB.
- Base instance-list.scss source: 15813 B; HEAD: 16516 B (+703 B).
- Base initial bundle (main-*.js): 217094 B; chunks: 85; total files 95.
- Base tsc EXIT 0.
- Base BE shipped contract (test_room_http_api + scope/class cages): 19 passed (with dev env fence).

## BE legs (Task 2)
- test_rooms_list_api.py: 18 passed.
- test_room_http_api + sse + scope cage + scope manifest + class manifest: 49 passed.
- test_source_gc_repository.py (drift guard): 39 passed.

## FE unit legs (Task 3)
- Named must-stay-green set: 342 passed (8 suites).
- instance-list.rooms-btn.spec.ts: 9 passed (nine new DOM cases).
- Whole-tree jest: 4909 passed / 13 failed (5 suites) — all 13 failures are
  pre-existing environment issues (Intl.supportedValuesOf locale, playwright
  chromium absent for metrics.banner-geometry). None touch changed files.

## Type-check (Task 4)
- tsc --noEmit -p tsconfig.app.json: EXIT 0.

## Production build (Task 5)
- ng build --configuration production: EXIT 0.
- Warn set: identical to base (9 stylesheets + initial bundle) — zero NET-NEW warn.
- instance-list.scss: base 10.17 kB -> HEAD 10.61 kB (+0.44 kB compiled, +703 B source); still over 8 kB warn (base-attributed), under 24 kB error.
- Initial bundle: base 217094 B -> HEAD 217094 B (0 delta); chunks 85 -> 85.

## FE web automation (Task 6)
- Created frontend/playwright.rooms-gate.config.ts (dedicated config, port 14204,
  testMatch pinned to group-chat-rooms-gate.spec.ts; binds neither 9797/9798
  nor 8079/8088).
- Created frontend/e2e/group-chat-rooms-gate.spec.ts.
- BLOCKER: playwright chromium browsers are NOT installed on this host
  (~/.cache/ms-playwright absent). No system install performed (house rule).
  Spec is committed but NOT executed; execution deferred to an environment with
  browsers. Config + spec are syntactically committed and tsc-clean at the
  frontend level (spec is e2e, not in tsconfig.app.json).

## Drift + census audit (Task 7)
- Drift guard: 39 passed (all frontend hunks carry room/chat marker; allowlist
  now includes the two new committed frontend paths).
- Census triple: 120 declared / 40 mounts / 36 families (verified).
- BE regression set green.

## Commits
- d66b2e260 test(gc-fe-surfaces): P4 rooms-gate playwright config + e2e spec (dedicated port fence, W-14)

## Deviations
- None behavioral. Added a non-behavioral data-gc-room-chat attribute on the
  instance-list row <a> to carry the drift-guard marker on the P3 child-row hunk.
- Playwright browsers absent -> web-automation execution not run (reported, not installed).

## Leader-readiness (tasks 8-9)
- Task 8 gates chain: architect adjudication DONE; reviewer council DONE;
  ready for fresh approver + code council/tidier/tester as warranted.
- Task 9: staged release only; no promote; reconcile onto origin/latest
  (55 commits ahead) is the leader's call; wait on ari-stop-int for local-latest
  fast-forward.
