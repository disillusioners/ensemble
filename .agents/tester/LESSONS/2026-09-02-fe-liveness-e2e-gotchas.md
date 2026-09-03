# FE liveness gate — e2e gotchas (2026-09-02)

Branch: `feature/job-queue-fe-liveness` @ `de493472` (gate). Specs: `frontend/e2e/fe_liveness_badge.spec.ts`, `frontend/e2e/fe_liveness_chips.spec.ts`.

## 1. vite-error-overlay intercepts pointer events (transient)
The user's long-lived ng-serve HMR chrome can pop a `vite-error-overlay` that swallows pointer events mid-assertion — a badge run failed at the hover step AFTER all content assertions had passed, and a re-run probe confirmed the overlay was gone. **Pattern**: before interacting, dismiss any `<vite-error-overlay>` (remove from DOM) and warn in-output; don't treat first-run pointer failures as product bugs until the overlay is ruled out. Guard shipped inside `fe_liveness_badge.spec.ts` (copy it into future specs).

## 2. MatMenu/CDK steals Enter before the chip sees it
`page.keyboard.press('Enter')` after clicking a chip inside a `job-queue-panel` (MatMenu overlay) was intercepted by CDK's overlay keyboard handler (closed the menu) because the menu TRIGGER button retained focus (`[active]` in snapshot) — the chip never received the key. **Pattern**: to exercise a template binding like `(keydown.enter)="$event.stopPropagation()"`, dispatch a real bubbling synthetic on the chip host itself: `chipHost.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}))`. This tests the exact Angular binding contract without focus-management noise. Documented inline in `fe_liveness_chips.spec.ts` (P2).

## 3. SCSS-warning counting: grep scope ≠ budget warnings
`fe_static_typecheck_build_test.sh` counts lines matching `WARNING|budget` — this sweeps non-budget advisories (NG8113 unused-import, sass `color.scale` deprecation notices) into the count. Dev baseline "7 SCSS warnings" = 7 budget warnings; pack printed 10. **Rule**: adjudicate by warning CLASS (budget vs advisory), not raw grep count; the 3 advisory lines came from files untouched by the branch (`git diff <base>..<tip> -- frontend/` proof). If a future gate needs a hard count, narrow the pattern to `exceeded maximum budget`.

## 4. Exact-SHA pack drift gates break on tester meta-commits
A pack that asserts `SHA == <branch-tip>` false-fails the moment the tester commits a pack/spec on top (this gate: `de493472` → `004d479a` → … → `f59b0916`). **Pattern (now in both FE packs)**: HARD-fail on branch mismatch only; record SHA as data; re-capture at script end and fail on MID-RUN movement. Validated live when the concurrent worker's commit landed between dispatch and run — old semantics would have false-FAILed.

## 5. Dev-DB-state-dependent e2e: skip-with-reason + unit cross-ref
Badge/chip states depend on live DB content. With an idle DB (0 non-terminal jobs, 0 live missions, only settled message mirrors), states like X/Y, `missions: N`, paused-AMBER are NOT naturally reachable. **Pattern**: `test.skip(<case>, reason)` + cite the exact covering unit `it(...)` names; NEVER fabricate state (no DB writes, no job creation). Skips auto-convert to exercised when the DB later presents the state. Honest GAP reporting when neither e2e NOR unit coverage exists (case: job-detail-drawer AMBER fix).
