# Quick Fix: B4 composition pin — idle-bound receipts (2026-09-19)

**Context**: FE gate for `feature/job-queue-hide-idle-instances` @ `93bd86eb`. Static audit (mock-fidelity + edge matrix) found 6/7 edge cases COVERED + CODE-CONFIRMED, but one composition gap (B4): a job/receipt whose `mission_id ?? instance_id` references an idle (filter-dropped) instance had both routing halves pinned individually (non-terminal → `queued`, terminal → `recentFlat`) yet no single test composed idle-filtered rows with a bound job end-to-end. The docblock promise (instance-node.model.ts:185-194) was unpinned.

**Root cause class**: half-pinned composition — each branch of `route()` had a fixture, but the idle-specific composition (filter removes the target node → lookup miss → fallback bucket) was only proven by code reading. Routing is instance-status-blind by construction, so risk was low, but the leader's edge case #4 ("receipts under idle instance still surfaced via queued/recentFlat") deserved an executable pin.

**Fix** (test-code only, quick-fix eligible): +2 tests in `describe('buildInstanceTree')` of `frontend/src/app/models/instance-node.model.spec.ts`, immediately after the two routing pins they compose. Reuse `mkRow` + `createMockJob`; apply `filterIdleInstanceRows` → `buildInstanceNodes` in production order; assert bucket membership of the bound job AND absence of the idle root from `liveRoots`/`recentRoots`.

- Names: `composition: idle (filter-dropped) root — its NON-TERMINAL bound job surfaces via queued` / `… TERMINAL bound job surfaces via recentFlat`
- Verification: model spec 70→72 all-pass (0.73s); focused pattern total 204→206
- Commit: `f4ae1b9f` (selective single-file `git add`; `git show --stat` one-file proof; repo foreign artifacts untouched)

**Audit flags worth keeping** (non-blocking, from the same analysis):
1. Component-spec comments cross-reference an "identity-grep pin" that actually lives in the model spec — misleading comment only; the pin exists (`hide-idle production source pins`: `filterIdleInstanceRows(source)` present + `callSites.length === 1`).
2. One component-spec regex pin (`/buildInstanceNodes\s*\([\s\S]*this\.instancesPayload\(\)/`) matches via a docblock occurrence (`[\s\S]*` spans comments) — not load-bearing, but a brittle pattern to avoid in future pins.
3. FE pack consolidation debt: untracked `test/packs/fe_unit_full_test.sh` (2026-09-16) vs tracked `frontend_full_unit_test.sh` near-duplicates.

**Lesson**: when an edge case is "confirmed by construction" (status-blind routing), still pin the composition — construction guarantees rot silently when someone later adds an instance-status consult to routing.
