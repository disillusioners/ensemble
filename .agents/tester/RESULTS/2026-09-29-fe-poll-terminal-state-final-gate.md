# FE poll-terminal-state fix — FINAL VERIFICATION GATE (2026-09-29)

**Commission:** final gate for the checkpoint-cleanup FE poll fix (leader-approved fix contract: poll budget = min(max(hint×2, 15-min floor), 60-min ceiling); backoff continuation 2s→30s linear ramp; re-entry recovery; verbatim error pass-through; no backend changes).
**Target under test:** worktree `/home/nea/ensemble-src-fe-poll-fix`, branch `fix/fe-poll-terminal-state`, tip **`0d20480c`** (chain `b56a1a7e` → `10f1b9ab` → `0d20480c`). Reviewer delta verdict was APPROVE 0C/0M with ONE flagged unverified claim: E2E 15/15 was developer-claimed, not executed by reviewer. This gate closed that gap by REAL execution.
**Workers:** W1 recon `5e43df7d` (no skill) · W2 jest `deb6b2e2` (`test-pack-execution`) · W3 playwright `f8a49d30` (`test-pack-execution`). Zero source modifications, zero commits (verification-only; merge owned by giter per shared KV).

## VERDICT: **PASS — GATE GREEN. READY TO MERGE.**

| Check | Target | Result |
|---|---|---|
| 1. E2E suite (real execution, reviewer-gap closure) | 15/15 | ✅ **15 passed, 0 failed, 0 skipped, 0 retries, 1.6m wall** — test 15 ✓ 14.1s |
| 2. Unit suites (service + component) | 109/109 | ✅ **109/109** (service 51/51 incl. 6 cadence-pinning; component 58/58), 3.995s |
| 3. Scope check | frontend-only | ✅ 7 files, ALL under `frontend/`; zero backend/daemon/scripts changes; worktree CLEAN at HEAD 0d20480c |
| 4. Judgment: test 15 pins original symptom | genuine | ✅ **GENUINELY PINS** (verdict + evidence below) |
| 5. Optional A/B vs pre-fix base | cheap-or-skip | ⏭️ **SKIPPED — documented** (rationale below; would-fail-on-base structurally forced) |

## 1. E2E execution detail (W3)

Invocation (worktree `frontend/`, parent-process env: `ENSEMBLE_DB_DSN=postgresql://ensemble@127.0.0.1:15432/ensemble_e2e_maint_disposable`, dummy `OPENAI_API_KEY`, CI unset → retries 0):

```
timeout 300 npx playwright test -c playwright.maintenance.config.ts maintenance-checkpoint-cleanup --project maintenance --reporter=list
```

**Intentional deviations from the commission's literal command string** (both honor intent, both precedent-backed):
- **Arg order** — positional filter placed BEFORE `--project`. The commission's literal order (`--project maintenance maintenance-checkpoint-cleanup`) hits the documented variadic-`--project` trap (LESSONS 2026-09-28-int4: trailing positional is swallowed as an additional project-name filter → silently matches nothing). Used form is the twice-proven one (v32_pw_e2e14, mc_pw_e2e both 14/14).
- **`--reporter=list`** instead of `line` — required for per-test durations (contractual in this commission). `-c` ≡ `--config`.

Per-test outcomes (all ✓): #1 3.6s · #2 2.8s · #3 2.9s · #4 2.2s · #5 2.3s · #6 2.6s · #7 2.4s · #8 3.4s · #9 5.3s · #10 3.0s · #11 5.4s · #12 5.1s · #13 384ms · #14 2.5s · **#15 14.1s**.

**Test 15 verbatim:** `✓ 15 [maintenance] › e2e/maintenance-checkpoint-cleanup.spec.ts:683:7 › … › 15. post-budget continuation: a run that outlives the budget and completes later transitions the UI to success (ORIGINAL SYMPTOM DEAD) (14.1s)` — passed, no assertion diff, no retries.

Teardown: ports 8099/4299/15432 all free post-run (`ss -ltn` empty for the trio); disposable PG cluster + `data_e2e_maintenance/` removed; **live :9797 untouched, still listening**. `git status --porcelain` EMPTY (zero tracked mods, no untracked leftovers).

**Anomaly (environmental, resolved, not code):** first attempt exit 127 at webServer boot — `initdb`/`pg_ctl`/`pg_isready`/`uv` not on default PATH. Fixed via documented LESSONS pattern (parent-process `PATH="/home/nea/.local/bin:/usr/lib/postgresql/16/bin:$PATH"`), env-only, zero file changes. No flakes: single clean run, retries 0.

## 2. Unit execution detail (W2)

`CI=true timeout 300 npx jest <service.spec.ts> <component.spec.ts> --watch=false --verbose` → service **51/51**, component **58/58**, combined **109/109**, 0 failed, 0 skipped, 3.995s. All 6 cadence-pinning tests PASS, incl. `tickIdx 0 → 2s`, `tickIdx 1 → 4s`, `tickIdx 14 → 30s ceiling + clamp`, and the MAJOR-1 iter3 regression pair (early ramp gaps 2s/4s/6s; ceiling reached exactly at idx 14, clamped beyond). Pre-existing ts-jest TS151001 bootstrap warnings (unrelated). Zero tracked mods.

## 3. Scope check (W1)

```
frontend/e2e/maintenance-checkpoint-cleanup.spec.ts          | 215 +++++-
frontend/src/app/models/index.ts                             |  18 +-
frontend/.../checkpoint-cleanup.component.html               |   9 +-
frontend/.../checkpoint-cleanup.component.spec.ts            | 446 ++++++++--
frontend/.../checkpoint-cleanup.component.ts                 | 123 ++++-
frontend/.../checkpoint-cleanup.service.spec.ts              | 494 +++++++++++++++++++--
frontend/.../checkpoint-cleanup.service.ts                   | 279 +++++++++---
7 files changed, 1401 insertions(+), 183 deletions(-)
```
Every path under `frontend/` — **zero backend/daemon/scripts changes** (fix contract honored: NO backend changes). Commits: `10f1b9ab` + `0d20480c`. Worktree clean, HEAD = `0d20480c9645…`, matches commission. New service exports added (`computePollBudgetMs`, `computeBackoffMs`, floor/ceiling/step constants); `POLL_MAX_DURATION_MS` (pre-fix 10-min hard cap) **removed**.

## 4. Judgment verdict — test 15 GENUINELY PINS the original symptom

Original symptom: run `ckpt-20260929_045639524546-3f6e42a4` succeeded after 12m33.5s; FE poller hit its 10-min cap at 05:06:39Z, permanently unsubscribed, UI never showed success.

Test 15 (spec lines 683–843) is **not vacuous** — it replays that exact incident (`RUN_ID = 'ckpt-20260929_045639524546-3f6e42a4'`) through real route interception and real component rendering, and asserts observable DOM state at three checkpoints:

1. **Post-budget continuation** — virtual clock drives 450×2s = 15 min past execute (crosses both the old 10-min cap and the new 15-min floor budget), then asserts ≥1 poll strictly AFTER the budget-boundary marker, and `pollCount > pollsAtBudgetExit` across a further 5-virtual-minute backoff window. The old code's permanent unsubscribe makes BOTH impossible (pollCount freezes at the cap).
2. **Zero error banners ×3** — `ck-error-banner` count==0 at budget exit, at backoff window, and at terminal. Old code synthesized `fe_synthesized_poll_timeout` → `poll_stale` banner at the cap → first checkpoint alone fails on base.
3. **Terminal success surfaces** — route flips to `status:'succeeded'`; asserts `ck-result` contains `'succeeded'` and `ck-active-run-id` disappears (executing() flipped false). Old code (dead-ended poller, error state) never renders success — this is the literal "I didn't see FE updated with success" closure.

Cadence note: the e2e continuation assertions are deliberately cadence-agnostic; the exact ramp (2s,4s,6s,…,30s ceiling at tickIdx 14 — incl. the `0d20480c` expand-index-binding fix) is pinned by the 6 unit cadence tests. Behavior at e2e level + cadence at unit level = the fix contract is fully pinned. Assertions are DOM/behavior-level (no service-internals mocking) — tests behavior, not implementation.

## 5. A/B leg — SKIPPED (documented, per commission's cheap-or-skip rule)

Physical base-red run rejected as **fiddly**: a scratch checkout of `b56a1a7e` has no `.venv`, and the playwright webServer boot runs `uv run` from repo-root (would trigger a fresh venv sync, or a symlink hack that risks mutating the worktree's shared venv — unacceptable for a verification-only gate) + needs node_modules provisioning. Cheap-path criteria not met.
Residual assurance instead: (a) W1 verified the spec **compiles against base** (imports only `@playwright/test`; all `data-testid`s exist in pre-fix HTML) — so a base run would be meaningful, not a compile error; (b) would-fail-on-base is **structurally forced** — pre-fix `POLL_MAX_DURATION_MS` 10-min cap synthesizes the `poll_stale` banner that test 15's zero-banner assertions forbid, and the permanent unsubscribe freezes `pollCount` that the continuation assertions require to strictly increase; (c) reviewer already verified would-fail-on-old-code logically. Confidence high without the physical leg.

## Environment / trap notes (for the record)

- **Editable-install trap confirmed:** `import daemon` resolves to MAIN checkout (`/home/nea/ensemble-src/daemon`) from both main and worktree venvs. Benign here: daemon is a disposable fixture serving the maintenance API; diff is frontend-only so the API contract is identical. Disposable stack booted from the worktree root on :8099/:4299/:15432.
- E2E needs PATH additions for the PG toolchain + uv (LESSONS 2026-09-28 pattern); `ENSEMBLE_DB_DSN` on the PLAYWRIGHT PARENT (webServer env does not propagate to test workers).
- Live daemon :9797 — zero requests, zero kills, verified listening throughout.

## ensure.md status (blast-radius scoped)

FE-only change → in-scope Core requirements: **#1 no regressions in changed packs** ✅ (changed packs = FE unit + FE e2e ad-hoc packs, both PASS); **#4 dev.sh static check** ✅ (`--timeout-graceful-shutdown 10` at dev.sh:102, W1 grep). Core #2/#3 (backend concurrency/asyncio packs) out of blast radius — scope check proves zero backend changes. Release Gate not warranted (FE-scoped fix, not big/critical/architecture). No ensure.md contradictions encountered.

## Gaps

None mandatory. A/B leg skipped by design (see §5). Everything the commission required was executed for real.

## Final

**PASS** — both suites green at exact required counts (109/109 unit, 15/15 e2e incl. test 15 at 14.1s), scope clean (frontend-only), symptom genuinely pinned by test 15's three-checkpoint assertion structure, zero flakes (retries 0), zero source modifications, live untouched. **The reviewer-flagged unverified claim (E2E 15/15) is now executed-and-confirmed. Gate is GREEN; branch is READY TO MERGE.**
