# Verification — source-edit-agent (Edit-Source modal agent selection)

Date: 2026-09-16
Branch: `feature/fix-source-edit-agent` @ `55de0a16` (5 commits on base `f2611f07` = latest), UNMERGED
Change under test: FE-only — `edit-source-modal.component.ts` + `add-source-modal.component.ts` (WeakMap memoization of `toSelectOptions` keyed on source-array identity + frozen shared `EMPTY_SELECT_OPTIONS`), both `.spec.ts` files NEW on branch (+487/−10 across 4 files; `edit-source-modal.html` byte-identical to base — confirmed in diffstat).
Worker instances: recon `1d68911f` · static `cbd6e865` · jest `268594ec` · dom-smoke `070f5c22`

## Summary

- **Verdict: ✅ GO — all acceptance criteria met, 3/3 packs PASS, 5/5 DOM scenarios PASS, 0 branch-caused failures**
- Packs: fe_static_typecheck_build (PASS) · fe_unit_full_test (PASS 3209/3209) · fe_edit_agent_e2e_test (PASS 5/5)
- ensure.md Core (FE-scoped): ✅ "no regressions in changed packs" — every blast-radius pack PASS
- Quick fixes applied: 0 (verification-only gate; none needed)
- Quarantined: 0 new (FE suite fully green; no quarantine interaction)

## Scope Decision

Full FE suite requested AND warranted (baseline 3190/3190 known; suite runs in ~13s; FE convention requires full jest + tsc + build + ≥1 live-DOM smoke pre-merge). Backend suite NOT warranted: diff is 4 frontend files, zero daemon/; the BE update path (`PUT /sources/{id}` → `update_source_config`, config passed through verbatim) was independently verified untouched per task context. Skipped: all BE packs, e2e daemon-required packs.

## Pack Results

| Pack | Result | Evidence |
|---|---|---|
| `fe_static_typecheck_build_test` (`EXPECTED_BRANCH=feature/fix-source-edit-agent`) | ✅ PASS | Stage 0 branch bracket matched, no mid-run SHA drift (55de0a16→55de0a16); `npx tsc --noEmit` 0 errors; `ng build` exit 0 in 11.1s (bundle 5.88 MB / 1.26 MB transfer). 10 warnings all pre-existing pattern noise (1× NG8113 unused import, 2× Sass `lighten()` deprecation, 7× budget overruns incl. pre-existing `add-source-modal.scss` 8.32 kB). Runtime ~2.5-3 min. |
| `fe_unit_full_test` (new ad-hoc pack, `timeout 240` internal / `300` outer) | ✅ PASS | `Tests: 3209 passed, 3209 total` · 91/91 suites · 13.17s. Count reconciles EXACTLY: baseline 3190 + 19 new branch specs (edit-source-modal 16 + add-source-modal 3). Zero failures → no base-comparison needed. |
| `fe_edit_agent_e2e_test` (new ad-hoc pack; fresh `npm run build` ~13s, bundle grep-verified for `WeakMap`/`EMPTY_SELECT_OPTIONS`; inner `timeout 240`, pack runtime 17s) | ✅ PASS 5/5 | Real-DOM Playwright (as-library, bundled headless Chromium) vs one-origin mock: dist + `/api` mocks on **127.0.0.1:10180** (mock range ✓). Precedent: 2026-09-14 monitoring-followups §A2 pattern. |

## Acceptance Criteria Verdicts

### Criterion 1 — ADD flow with agent selection must not regress → ✅ PASS
S2: ADD modal on telegram source → typed `test` → typed text retained → selected `tester` → captured **POST /api/sources** body carries `config.default_agent: "agent-tester"` (payload: `2026-09-16-source-edit-agent-smoke-artifacts/payload-S2-post-api-sources.json`).

### Criterion 2 — EDIT can select a DIFFERENT agent and the choice persists (PUT config carries new agent) → ✅ PASS
S1 (core bug discriminator): EDIT on Slack source preselecting `leader` → typed `cod` → **input retained `"cod"`** after 2× mouse moves + extra input events + 1.6s wait (pre-fix, the SearchableSelect options-effect rewrote displayText to the preselected label on every CD cycle when the options array identity changed; post-fix memoization returns the same array → effect does not re-fire) → filtered panel showed `coder` (not just the preselected agent) → clicked → control shows `coder` → captured **PUT /api/sources/src-slack-s1** body:
```json
{"name": "S1 Slack Production", "config": {"default_agent": "agent-coder", "channel_require_mention": true}, "enabled": true, "autostart": true}
```

### Edge checks → ✅ 3/3 PASS
- **E1 (S3) null default_agent**: EDIT on source with `default_agent: null` → input empty (correct) → typed `wan` retained → selected `wanderer` → PUT `/api/sources/src-telegram-s2` carries `config.default_agent: "agent-wanderer"` with sibling config keys (`polling_enabled`, `polling_timeout`) preserved.
- **E2 (S4) re-select SAME agent**: EDIT S1 (`leader` preselected) → panel open without typing → re-clicked `leader` → PUT carries `config.default_agent: "agent-leader"`; displayText correct.
- **E3 (S5) switch sources, no stale state**: EDIT S1 → Cancel (no save) → EDIT S3 → input showed S3's own `wanderer` (NOT stale `leader`) → typed `rev` retained → selected `reviewer` → PUT routed to **S3's id** (`src-slack-s3`) carrying `agent-reviewer`.

All 5 captured payloads verbatim in `.agents/tester/RESULTS/2026-09-16-source-edit-agent-smoke-artifacts/` (5× payload JSON, 16 screenshots, console/page-error logs, captured-requests.jsonl).

## DOM Smoke Environment & Hygiene

- One-origin Node mock server: static `frontend/dist/` + `/api` (7 fake agents incl. shared-prefix pair coder/code-reviewer; 4 sources; minimal boot endpoints + SSE). NEVER the stock playwright e2e config (boots daemon — forbidden).
- Port discipline: only **10180** bound; pre-check free; post-run `lsof` zero listeners. **Prod daemon 9797 untouched (same PID 51714, still LISTEN)**; 8079/4199/8088 never opened.
- Console: 0 uncaught page errors; 60 errors/warnings ALL harness-noise (35× 404 on unmocked app-level polling endpoints `/api/queues`, `/api/missions`, … + 25× JobQueueIndicator degraded-leg warnings, direct consequence of the 404s).
- CD-cycle discrimination design note (from smoke worker): retention assertion triggers multiple real CD cycles (mouse moves + input events); avoids Escape (panel-close intentionally restores selected label — separate behavior). Design-based discrimination: this test would FAIL on the pre-fix parent (`55de0a16~1`), PASS on tip. An actual base A/B browser run was NOT executed (not required by acceptance; suite + spec + DOM-direct evidence sufficient).
- Timing: build ~13s + pack 17s + evidence copy ≈ 30s wall. Dual-layer timeout intact; outer 300 never hit.

## ensure.md Validation (blast-radius scoped)

- **Core Critical #1** (no regressions in changed packs): ✅ — all FE packs in the change set PASS.
- Core items scoped OUT (BE-side, zero daemon files in diff): concurrency_atomic_unit_test, sync-DB/await greps, dev.sh static check — not touched by this change; running them would be off-blast-radius.
- Release Gate: NOT TRIGGERED (FE bugfix, single component family, no architecture change).

## Findings / Follow-ups (leader routes)

1. 🟢 `fe_static_typecheck_build_test.sh` existed since the 2026-09-14 gate but was never registered in PACKS.md — registered this session (backfill). Its `EXPECTED_BRANCH` default (`feature/mission-class`) is a drift trap for every non-default branch; consider defaulting to current branch.
2. 🟢 FE full-suite pack duplication debt: `frontend_full_unit_test.sh` (Sep 13, defaults `feature/jobs-page-improvement`) vs new `fe_unit_full_test.sh` — near-duplicates; consolidate (both untracked on their respective arcs).
3. 🟢 Skill-system note from dom-smoke worker: `fe-realdom-gate-no-daemon` skill matches this exact recipe (real-DOM FE gate without daemon) more specifically than `e2e-test`; dispatcher should prefer it for future FE gates (worker fed back 8/10 on e2e-test).
4. 🟢 7× bundle/SCSS budget overruns (incl. `add-source-modal.scss` 8.32 kB) — pre-existing cosmetic debt, not branch-blocking.
5. ℹ️ Foreign tracked edit `.agents/tidier/notes.md` (+8 lines, tidier review addendum re this branch) present in shared worktree — disclosed by recon, left untouched (read-only convention).

## Documentation Updated

- [x] RESULTS/2026-09-16-source-edit-agent-verification.md (this file) + smoke-artifacts/ evidence bundle (24 files)
- [x] PACKS.md — Ad-Hoc Pack Registration (2026-09-16): fe_static_typecheck_build_test (backfill), fe_unit_full_test, fe_edit_agent_e2e_test
- [x] MOCK_TESTS.md — smoke harness spec (10180 one-origin mock)
- [ ] rules/ensure.md — untouched (user-owned)
- [ ] QUARANTINE.md — no changes (nothing flaky/quarantined this gate)

## Code Changes Summary

None to production/source. Untracked tester-created infra (NOT committed, per branch no-source-commit constraint — for the feature owner/giter to land or discard): `test/packs/fe_unit_full_test.sh`, `test/packs/fe_edit_agent_e2e_test.sh`, `test/packs/fe_edit_agent_e2e_test/{mock_server.js,harness.js}`. Committed path-scoped this gate: `.agents/tester/**` only (RESULTS + PACKS.md + MOCK_TESTS.md).

## Overall Status

- Static (tsc+build): ✅ PASS
- Full FE unit suite: ✅ PASS (3209/3209, exact baseline reconciliation)
- Live-DOM smoke: ✅ PASS (5/5 incl. add-flow regression + 3 edges; payloads captured; prod daemon untouched)
- ensure.md (scoped): ✅ PASS
- **Testing Complete: ✅ READY — GO for merge (FE-only; activation on FE build/deploy)**
