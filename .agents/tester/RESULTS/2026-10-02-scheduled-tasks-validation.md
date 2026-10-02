# Independent Validation — SCHEDULED TASKS feature

- **Date:** 2026-10-02
- **Commissioner:** leader (independent empirical TEST VALIDATION)
- **Change set:** `8cebe211..3a939a76` (9 commits: feature phases 1–5, D4 fix, W4/W5, regression tests, tidier pass)
- **Worktree:** `/home/nea/ensemble-src-wt-scheduled-tasks` (branch `feature/scheduled-tasks`, HEAD `3a939a76d8d18d07a7a3bf532cc532cc5aa3f385bc909` verified by every worker; shared checkout `/home/nea/ensemble-src` never touched; no daemon boot; no ports 9797/7979/8088 contact)
- **Method:** 7 parallel dispatched workers (val-pack, val-suites-core, val-suites-rest, val-tz, val-reg, val-rest, val-regress); dispatcher executed nothing directly. All runs `ENSEMBLE_SELF_ENV=dev`, `daemon.__file__` verified in-worktree on every leg, per-invocation `timeout` wrappers, no code changes / no quick fixes applied (independent-gate mandate).

## VERDICT: **FAIL** — 3 findings (F2 🔴, F1 🟠, F3 🟠); everything else green

Per the commission's verdict rule (conjunctive):

| Condition | Result |
|---|---|
| Acceptance pack reproduces 157P/0F/0S, twice | ✅ PASS (exact, deterministic) |
| All 9 commissioned suites exact vs claims | ✅ PASS (277/277) |
| Integration one-uuid contract proven | ✅ PASS (real-stack + real-loop driver) |
| TZ/DST spot-proofs match independent expectations | ❌ **FAIL — scenarios (b) and (d)** → F1, F2 |
| Registration proof complete | ✅ PASS (5/5 static checks) |
| No new reds vs documented pre-existing set | ❌ **FAIL — 1 diff-attributed red** → F3 |

**Feature is NOT ready to merge on the empirical gate as-is.** All 277 commissioned tests are green and the core D4 one-uuid/dispatch-once contract is genuinely proven — the failures are in DST-transition behavior of the recurring path (F2, most severe), the anchor gap-shift contract (F1), and one stale test pin (F3, mechanical).

---

## 1. Acceptance pack (item 1) — MATCHES BASELINE

`ENSEMBLE_SELF_ENV=dev timeout 600 bash tests/packs/scheduled_tasks_acceptance.sh` (registered invocation per PACKS.md):

| Run | Counts | Exit | Wall |
|---|---|---|---|
| 1 | 157P / 0F / 0S | 0 | 22.00 s |
| 2 | 157P / 0F / 0S | 0 | 21.88 s |

- Identical test set AND execution order across runs (sorted + ordered diffs empty) — deterministic, zero flake.
- Pack internals: dual-layer timeout (outer 600 s caller wrapper + pyproject `timeout=30`/thread via pytest-timeout); hermetic (TestClient `127.0.0.1:123`, no daemon boot, no live ports); aggregates adapter 85 + api 66 + e2e 6 = 157; `set -euo pipefail`, exit = pytest exit.
- Cosmetic: 9 identical pre-existing SQLAlchemy sqlite-datetime DeprecationWarnings per run.

## 2. Commissioned surface (item 2) — all 9 exact

| Suite | Claim | Actual | Exit | Wall |
|---|---|---|---|---|
| tests/test_scheduler_adapter.py | 85P | 85P | 0 | 10.28 s |
| tests/test_scheduler_api.py | 66P | 66P | 0 | 4.67 s |
| tests/test_scheduler_instance_mode.py | 52P | 52P | 0 | 4.42 s |
| tests/unit/test_scheduling_registration.py | 16P | 16P | 0 | <1 s |
| tests/unit/test_tz_resolver.py | 7P | 7P | 0 | <1 s |
| tests/unit/services/test_scheduling_service.py | 23P | 23P | 0 | ~1 s |
| tests/unit/tools/test_scheduling_tools.py | 14P | 14P | 0 | ~1 s |
| tests/unit/test_scheduler_api_phase3_contract.py | 8P | 8P | 0 | <1 s |
| tests/integration/test_scheduled_tasks_e2e.py | 6P | 6P (needs `-m integration`) | 0 | 10 s |

**277/277 match.** Caveat (non-defect): the e2e module is marker-gated (`addopts` deselects `integration`); bare invocation collects 0 and exits 5 — the acceptance pack's `-m "integration or not integration"` is what runs it. Any future gate script must use the marker.

## 3. Integration happy path + one-uuid contract (item 3) — PROVEN

All six contract legs verified with quoted assertions; two e2e coverage gaps were closed by a driver through the REAL adapter loop (`/tmp/schedval/happy_path_driver.py`, 2/2 PASS, 83 s):

- **(a) one-shot → exactly ONE dispatch at due time, targeted agent:** e2e asserts `len(items) == 1` + `item.instance_id == seeded_instance`; due-time firing (e2e drove `_emit_scheduled_message()` manually) closed by driver: real loop, due=now+3 s, exactly 1 JobItem, no re-fire +5 s.
- **(b) one-uuid `Task.work_id == JobItem.job_id`:** real `TaskRepository` read-back (`task.work_id == item.job_id` + uuid-parse + re-lookup identity); reinforced by real-stack dedup class.
- **(c) recurring daily advance:** computed advance proven in adapter units; loop-level advance closed by driver (fires the HH:MM occurrence, next trigger strictly future ~24 h, no double-fire). Design note: advance is recompute-based (`croniter(expr, now)` per loop pass) — no persisted next-run write-back exists to assert.
- **(d) cancel prevents dispatch:** eviction + zero JobItems + terminal row + second-cancel raises; plus stop-doesn't-clobber and cancelled-never-boot-starts.
- **(e) reboot/crash idempotency:** `TestIdempotencyRestart::test_no_double_dispatch_after_restart` (byte-identical key across restart → 1 row, same job_id) + `TestEnqueueMessageJobDedupRealPath` ×4 (real `InstanceMessagingService.enqueue_message_job` + real repositories on SQLite; crash-window, compensation, no-key→2-rows).
- **(f) mock hygiene:** single signature-faithful stub (`spawn_instance_with_mcp`), documented; adapter stubs funnel into the REAL `create_or_get_by_idempotency_key`. No tautologies.

Positive incidental: dispatch failures log ERROR loudly (driver's first failed attempt proved it).

## 4. TZ/DST spot-proofs (item 4) — driven, NOT trusted → **2 findings**

Production code driven: `daemon/util/tz.py::resolve_timezone` + `anchor_local_to_utc` + `detect_host_local_timezone`; `daemon/services/scheduling_service.py::_compute_next_run` (croniter path). Driver `/tmp/schedval/tz_spotproof.py` computes all expectations itself from zoneinfo.

| Scenario | Independent expectation | Actual | Verdict |
|---|---|---|---|
| (a) default tz, env unset | documented chain step 3/4 | step 3 host-local (`Etc/UTC`), no warning — chain honored | **MATCH** |
| (b) spring-forward gap, NY 2026-03-08 02:30 | shift-forward to 03:00 EDT = `07:00Z` (commission + docstring "next valid local time") | **`03:30` EDT = `07:30Z`** (`naive + gap_seconds`, tz.py:416-419) | **MISMATCH → F1** |
| (c) fall-back fold, NY 2026-11-01 01:30 | first occurrence `05:30Z` (EDT) | `05:30Z` EDT, no warning | **MATCH** |
| (d) daily 06:00 NY across both transitions | Mar 7 11:00Z / Mar 8 10:00Z / Mar 9 10:00Z; Oct 31 10:00Z / Nov 1 11:00Z / Nov 2 11:00Z | **Mar 8 fires TWICE (05:00 + 06:00 EDT); Nov 1 fires 07:00 EST (1 h late); chain-of-drift thereafter** | **MISMATCH → F2** |

### F1 🟠 — anchor gap-shift semantics diverge from documented contract
`anchor_local_to_utc` docstring (tz.py:364) promises "shift forward to the next valid local time" (= 03:00 for a 02:30 anchor in a 1-h gap); implementation adds `gap_seconds` (= 03:30). The unit tests encode the implementation value, not the documented contract (assertion-drift family). Blast radius: one-shot anchors landing inside a DST gap dispatch 30 min later than documented. Fix: pick one semantics (next-valid per docstring/plan vs +gap arithmetic) and align docstring + impl + tests.

### F2 🔴 — croniter 6.0.0 DST defects on the recurring cron path (production path)
Empirically characterized through `_compute_next_run` + direct croniter, for daily 06:00 America/New_York:
- **Spring-forward day double-firing:** phantom 05:00 EDT + real 06:00 EDT on 2026-03-08 (2 firings/day).
- **Fall-back day 1 h late:** 07:00 EST on 2026-11-01 (expected 06:00 EST).
- **Gap-day nonexistent emission:** cron `02:30` returns nonexistent local time with pre-DST offset instead of skipping the gap.

Consequence: since the D4 idempotency gate is one-shot-only by design (cron fires mint fresh JobItems), the spring-forward phantom = **real duplicate-dispatch exposure for cron schedules in non-UTC timezones**. Scope note: proven at the service `_compute_next_run` path + direct croniter characterization; the adapter loop shares the croniter call pattern (`scheduler.py:640-641`), so adapter-level double-dispatch is inferred from the shared path, not observed end-to-end. The commission's spot-proof (d) — "recurring daily cron across both transitions lands on correct UTC instants" — **does not hold**. Fix directions: pin/replace croniter, or post-validate each computed trigger by re-anchoring via `anchor_local_to_utc` and rejecting phantom/nonexistent instants. (The existing DST unit tests pass because they assert the anchor path and narrower cron cases — they do not cross real transitions on the cron path.)

## 5. Registration static proof (item 5) — 5/5 PASS

- `scheduling` in `tools.allow` for ari, leader, jober (meta diffs vs base = exactly one `+"scheduling"` line each).
- Registry: exactly 4 tools — `task_schedule`, `task_schedule_list`, `task_schedule_cancel`, `task_schedule_update` — category `scheduling`, non-privileged (`PRIVILEGED_TOOL_CATEGORIES` = {system_upgrade, system-log, ens-db} unchanged).
- `KNOWN_TOOL_NAMES` 206→210 (+4/−0); `DYNAMIC_TOOL_NAMES` 57→61 (+4/−0) — frozen list regenerated.
- Companion `tools_note.md` edits purely additive (ari +70, leader +9, jober +52).

## 6. REST surface (item 6) — R1–R8 covered

6 behaviors covered by existing tests (cited file:line); 2 gaps closed by driver `/tmp/schedval/rest_gap_driver.py` (5/5 PASS, 4.69 s): R6 DELETE→GET round-trip (200 + `status="cancelled"` + `cancelled_at`) and R8 write-pause on PUT/trigger/start/stop (all 503; all 6 `is_write_paused` guards in `daemon/routers/schedules.py` fire). Write-pause is simulated via the established `mock_manager.is_write_paused = True` TestClient pattern. Tautology audit: no pure tautologies; 5 thin pass-through field assertions characterized (each retains an independent route-behavior assertion).

## 7. Regression neighborhood + attribution (item 7)

36 reds observed across messaging/job-queue/tools neighborhoods, classified:

| Class | Count | Detail |
|---|---|---|
| **FINDING (diff-attributed)** | **1** | F3 below — passes at BASE, fails at HEAD |
| Documented pre-existing | 13 | `test_enqueue_shared` idle→running (BASE-CONFIRMED, identical signature `assert 2 == 1` @ :498) + 9 quarantine-family in tests/unit/tools (exactly 9 ✓) + TestSite1InlineMirrorFinalize ×3 (known since v0.13.10) |
| Undocumented, worker-attributed pre-existing at BASE | 22 | each byte-identical-module-at-BASE or direct BASE re-run (incl. TestRecordMetricsWiring ×10, fake_sync 5-vs-6-arg family, tool-order/count pins, watch_events drift, census ×2 — census module manager.py IS diff-touched but both reds empirically pre-date the branch) |
| py3.13 collection errors | 0 | none in any executed chunk |

### F3 🟠 — one new red attributable to the diff (test-debt, mechanical)
`tests/services/test_instance_messaging_queue_routing.py::TestEnqueueMessageJobQueueIdResolution::test_manager_wrapper_forwards_queue_id` (:695) — exact-kwargs pin; the diff threads `idempotency_key=None` through the manager wrapper (`instance_messaging.py`, `manager.py` both in diff) and the pin was not updated. PASSES at BASE 8cebe211, FAILS at HEAD. One-line test fix (update expected call); **not applied** per the independent-gate no-fix mandate.

BASE attribution leg: detached worktree `/tmp/schedval/base` @ 8cebe211, fresh `uv sync` venv, in-worktree import verified, removed after use.

## ensure.md status (scoped to this change set)

- **Core #1** (no regressions in changed packs): PASS — `scheduled_tasks_acceptance` green ×2; F3 lives in the messaging neighborhood (captured as a finding, not a changed-pack regression).
- **Core #2/#3** (concurrency integrity / no sync DB on loop): PASS — `concurrency_atomic_unit_test` 98P/0F/74S in 62.89 s, baseline-exact.
- **Core #4** (dev.sh `--timeout-graceful-shutdown 10`): PASS (dev.sh:102).
- **Release Gate:** not triggered (feature-branch validation, not a release/big-architecture gate).

## Scope decision & deviations

- Commissioned matrix executed as scoped; the only additions are the cheap ensure.md Core #2–#4 legs (scheduling adds async/locking code → in blast radius). No other expansion.
- Pack outer wrapper 600 s is the PACKS.md-registered invocation (dual-layer preserved: pyproject pytest-timeout 30 s inner).
- Workers dispatched WITHOUT `load_skill`: ensemble-side defect 2026-10-02 (empty `tools.allow` → `capability_missing: bash` at skill pre-flight) blocks the skill lane; all pack-execution constraints were embedded in the dispatch bodies instead.
- Worktree pre-existing dirt (`.agents/approver/active.md` modified, `.agents/approver/scheduled-tasks-tracking.md` untracked) left untouched; this commit adds ONLY the files under `.agents/tester/`.

## Action needed

1. **F2 (🔴, blocking):** decide recurring-DST strategy — pin/replace croniter OR add trigger post-validation (re-anchor + phantom/nonexistent rejection). Add cron-across-transition regression tests driven against zoneinfo-computed expectations (the current DST tests do not cover this).
2. **F1 (🟠):** align anchor gap-shift semantics across docstring/impl/tests (commission expectation: next-valid 03:00; impl: +gap 03:30).
3. **F3 (🟠, mechanical):** update the exact-kwargs pin for `idempotency_key` (one-line, quick-fix eligible on the fix commission).
4. Nice-to-have: e2e wall-clock leg in a non-UTC tz; quarantine sweep for the 22 undocumented pre-existing reds (separate commission).

## Evidence

Worker reports (verbatim) in tester session; driver scripts + logs under `/tmp/schedval/` (pack_run{1,2}.log, core_{1..3}.log, rest_{1..8}.log, tz_spotproof.py, rest_gap_driver.py, happy_path_driver.py, base-leg logs).

---

# RE-GATE (fix round) — tip `34b4db96` (commits `95931aae` + `34b4db96` on `5408c98b`)

- **Date:** 2026-10-02 · 4 dispatched workers (val-tz revived, val-pack2, val-suites2, val-regress revived); all at HEAD `34b4db96b7ce091a3d18ba65ba43e091fc3229ae`, daemon verified in-worktree on every leg.
- **Fix under test:** F2 = croniter roundtrip phantom detector + literal reconstruct via `anchor_local_to_utc(fold_preference="post")` + monotonic guard (`daemon/util/tz.py:599-676`); F1 = gap-shift to first-existing local (`tz.py:439-474`); F3 = strengthened kwargs pin; ADR-008 amendment (cron=post / one-shot=pre) in `decisions.md:210`.

## RE-GATE VERDICT: **FAIL — one blocking residual (F4); every other condition met**

| Commission PASS condition | Result |
|---|---|
| F2 scenarios green vs independent expectations | ⚠️ 25/26 MATCH — F4 residual (missed fire) |
| Zero phantoms | ✅ zero across ALL scenarios + adversarial 09:00Z window probes |
| F1 verified (impl + tests, no weakening) | ✅ first-existing live-verified (02:30→03:00=07:00Z; 02:01→03:00); tests +38/−3, all removals superseded, net +35, no new skips |
| F3 verified | ✅ pin GREEN, strengthened (`idempotency_key=None` + dated rationale) |
| Fold decision ratified | ✅ **RATIFIED** (below, with rationale-correction note) |
| Pack + suites exact | ✅ 164P/0F/0S ×3 deterministic; 9/9 suites exact (92/12/66/52/16/8/23/14/6) |
| No new reds | ✅ red set = prior 36 − 1 (F3 green); zero new/changed-signature reds |

## F2 verification detail (driver `/tmp/schedval/tz_reproof.py`, 26 scenarios, expectations computed from zoneinfo in-driver)

- **Daily `0 6 * * *` NY:** Mar 7 11:00Z · **Mar 8 EXACTLY ONE 10:00Z** (old 09:00Z phantom dead — 3-call adversarial window probe all land 10:00Z, 0 warnings) · Mar 9 10:00Z · Oct 31 10:00Z · **Nov 1 EXACTLY ONE 11:00Z** (old 12:00Z drift dead) · Nov 2 11:00Z. Chain gaps 24h±DST correct; no duplicate dates.
- **In-gap `30 2 * * *`:** Mar 7 07:30Z · **Mar 8 07:00Z (first-existing 03:00 EDT, +2 expected gap-shift WARNs)** · Mar 9 06:30Z. Monotonic guard does NOT skip the legitimate repair (probe from seed 07:30:01Z Mar 7).
- **`30 3 * * *`:** Mar 8 07:30Z (post-gap valid, no shift). **Weekly `0 4 * * 3`:** Mar 4 09:00Z → Mar 11 08:00Z (23h DST gap) → Mar 18 08:00Z. **Fold-night `30 1`:** Oct 31 05:30Z · Nov 1 06:30Z (post) · Nov 2 06:30Z; one-shot unchanged 05:30Z (fold=0).
- **No false-positive WARNINGs** on 12 benign non-transition points.
- **Non-literal loud fallback ACTIVE:** `0 * * * *` fold-night call #8 emits the documented WARNING verbatim and returns the re-anchored 07:00Z; net fire sequence correct.

### F4 🔴 (blocking) — missed fire on `0 23 * * *` across fall-back; reconstruct trusts croniter's wrong DATE
`compute_next_cron_fire("0 23 * * *", after=2026-10-31T23:00 EDT, NY)` returns **Nov 2 23:00 EST (Nov 3 04:00Z)** — expected **Nov 1 23:00 EST (Nov 2 04:00Z)**. The Nov 1 fire is **skipped entirely** (missed dispatch for daily-23:00 schedules seeded in the post-fire window; reproduced twice incl. seed 2026-11-01T00:00 EDT).
Root cause chain: (1) croniter 6.0.0 emits a wrong-hour+wrong-date fire (Nov 2 00:00 EST) for this shape when the tzinfo is `ZoneInfo` — see also the **croniter tz-object-identity bug** (same expression + same instant, fixed-offset tzinfo → correct fire; ZoneInfo → wrong fire; croniter appears to do `is`-identity checks); (2) the roundtrip detector correctly flags it; (3) the literal reconstruct uses `candidate_aware.astimezone(tz).replace(hour=23, minute=0)` — **inheriting croniter's wrong DATE**; (4) result re-anchors to the wrong day.
**Fix path (small, test-code-adjacent production fix):** derive the reconstruct date from `after_aware` (advance from the cursor's local date), not from croniter's emission — e.g. `phantom_date = max(candidate_local_date, after_aware.astimezone(tz).date())` or day-advance from `after_aware` until roundtrip-clean. Add regression: `0 23` across fall-back from the post-fire seed (both legs), and pin the ZoneInfo-normalized seed (wrapper already normalizes — keep it).
Note: Mar-side `0 23` legs (F1–F3) all MATCH; the falsified part of the round-1 claim is specifically the fall-back leg. "Zero phantoms" still holds everywhere — F4 is a *missed* fire, not a double fire (no dedupe safety net exists for cron by design, hence blocking).

## Fold adjudication (item 2): **RATIFIED** — cron=`fold_preference="post"`, one-shot=`fold=0/pre`

Empirical basis (H3 hourly fold-night enumeration + I-scenarios): wrapper fires exactly ONE pass of the ambiguous hour (post: 06:00Z, skipping 05:00Z); native croniter fires the pre pass; **neither fires both** — across the 25h night each leaves exactly one 2h UTC hole (04Z→06Z wrapper). Post is defensible: exactly one fire per wall-clock HH:MM, deterministic, conservative (later pass), fully pinned by tests, cleanly documented in the ADR-008 amendment with supersession trail. Cost of pre instead: one parameter + 2 test expectations — not warranted for a 1-hour/1-night/year difference on fold-window schedules.
**Two documentation conditions (non-blocking, should land in a doc pass):**
1. The stated rationale "hourly keeps exact 1h UTC spacing" is factually imprecise — no fold preference yields uniform spacing across a 25h night; the accurate properties are: exactly one fire per wall-clock HH:MM, exactly one 2h UTC hole for sub-daily schedules, post = later pass. decisions.md should be corrected to the accurate wording.
2. Fold behavior is inconsistent between literal crons (post) and the non-literal fallback (native = pre pass, WARNING'd "DST-correct NOT guaranteed") — should be listed alongside the existing non-literal residual.

## Pack + suites + regression (re-gate)

- **Pack ×3:** 164P/0F/0S, exit 0 (hard-captured ×2), ~27s, node-ID sets identical. Growth 157→164 = exactly 5 `TestDstSemantics` + 2 `TestCatchUpSemantics` W1 pins (all brand-new defs in `95931aae`); def-set diffs vs `5408c98b`/`3a939a76`/`8ce2a788` = additions only, zero removals/renames/unparked-skips; 0 skips/deselects.
- **Suites 9/9 exact:** 92/12/66/52/16/8/23/14/6 (+ the F3 pin solo GREEN; sibling router_forwards stays red as documented).
- **Red set:** 35 = prior 36 − F3(now green); zero new, zero signature drift; scheduling-owned tests 231P; zero py3.13 collection errors. Concurrency pack 98P/0F/74S baseline-exact; `dev.sh:102` flag present.

## Re-gate action needed

1. **F4 fix** (date-source change in the reconstruct path + `0 23` fall-back regression from post-fire seed) → then a targeted re-drive of F4 + C/D/E/G scenarios (~5 min worker) clears the last blocker.
2. Doc pass: fold rationale wording (condition 1) + literal/non-literal fold residual note (condition 2).

Evidence: worker reports in session; `/tmp/schedval/tz_reproof.py`, `/tmp/schedval2/pack_run{1,2,2b}.log`, suite logs.

---

# CLOSING GATE — F4 fix at tip `0cf56de0` (commit 11, on `41752298`)

- **Date:** 2026-10-02 · 2 workers (val-tz revived, val-pack2 revived) · HEAD `0cf56de0a9ed0da915e8358daedecf654f212c6c`, daemon in-worktree verified.
- Diff `34b4db96..0cf56de0` = 7 files = my 3 re-gate artifacts (`41752298`) + dev's 4 commit-11 files (`decisions.md`, `daemon/util/tz.py`, `tests/test_scheduler_adapter.py`, `tests/unit/test_tz_resolver.py`).

## FINAL VERDICT: **PASS — empirically merge-ready** (one pre-merge doc correction, text provided below; no code change)

| Closing-gate condition | Result |
|---|---|
| F4 dead vs independent expectations | ✅ all 4 legs + exactly-once window + spring mirror `0 0` — exact instants, 0 warnings |
| Date-from-`after_aware` branch verified | ✅ DOW-wildcard reconstruct derives date from `after_aware` (quoted, driver-verified routing); DOW-restricted keeps candidate date (correct DOW-match arithmetic) |
| No regression in C/D/E/G/B/I + probes | ✅ 22/22 MATCH; 09:00Z adversarial window clean; monotonic guard preserves in-gap repair; 0 false-positive warnings on 6 benign points |
| Pack + suites exact | ✅ 166P/0F/0S ×2 deterministic (growth 164→166 = exactly 2 F4 regressions, additions-only); suites 9/9 (94/13/66/52/16/8/23/14/6); F3 pin green; concurrency 98P/0F/74S; dev.sh flag |
| Doc conditions verbatim (item 4) | ⚠️ **(a) NOT landed — self-contradictory wording; (b) partial** → pre-merge doc correction (below), non-gating |

## F4 verification detail (driver `/tmp/schedval/tz_f4.py`, 31 scenarios)

`0 23 * * *` NY: Oct 30 seed → Oct 31 23:00 EDT (03:00Z) · **Oct 31 23:00 EDT post-fire seed → Nov 1 23:00 EST = 04:00Z Nov 2 (the F4 leg — was Nov 3 04:00Z)** · Nov 1 00:00 EDT seed → same Nov 1 23:00 EST · Nov 1 23:00 EST post-fire seed → Nov 2 23:00 EST (04:00Z Nov 3). Exactly-once window walk: 1 unique fire, no Oct 31 duplicate, no Nov 2 skip, 0 warnings. Spring mirror `0 0 * * *`: Mar 8 00:00 EST = 05:00Z single fire (no phantom/skip); Mar 9 → 04:00Z.

Branch behavior (`_cron_dow_is_wildcard`, tz.py:511-526 + reconstruct :664-709): DOW-`*` → date from `after_aware` local date (+1 day advance if literal HH:MM already passed — prevents the monotonic guard eating a same-date past fire); DOW-restricted (e.g. `0 4 * * 3`) → croniter's candidate date retained (DOW-match arithmetic trusted; legs G Mar 4/11/18 all exact). **Accepted design residual (documented in code):** the roundtrip detector catches HH:MM mismatches, not DATE mismatches within the same DOW — if croniter ever emits a wrong DATE on a DOW-restricted expression, the reconstruct would inherit it. Low exposure; noted, not gating.

tz-suite rename adjudicated: `test_same_date_phantom_advances_and_logs_warning` → `…_advances_to_next_day_via_f4_fix` is a legitimate contract update (the old pin encoded pre-F4 guard-trip behavior; post-F4 the same-date phantom advances +1 day via the after_aware date), with a NEW defense-in-depth pin for the retained guard. Outside the pack; not test-debt.

## Doc conditions (item 4) — confirmation FAILED for (a), partial (b); corrective text supplied

- **(a) decisions.md:210** still reads "UTC-continuity across the fold (exact 1h UTC spacing; fold=0 opens a 2h UTC hole)" — internally contradictory, and none of the three tester-required properties are stated. **Replace with:** "Fold rationale (tester-ratified accurate wording): across the 25-hour fall-back night, NO fold preference yields uniform UTC spacing for sub-daily schedules — both `post` and `pre` leave exactly one 2h UTC hole (mirrored positions). The ratifiable properties are: exactly ONE fire per wall-clock HH:MM, monotonic in UTC, deterministic pass choice — `post` = the later (second) pass, the conservative choice; one-shot stays fold=0/pre per ADR-008."
- **(b) decisions.md:212** documents the non-literal loud fallback but does not contrast the fold behavior. **Append:** "Fold-inconsistency residual (documented): literal crons reconstruct with fold_preference='post' (later pass), while non-literal crons fall back to croniter's raw emission, which on fold nights resolves the ambiguous hour to the EARLIER (pre-fold) pass — the two families fire differently on the fold night. DST correctness on non-literal shapes is NOT guaranteed (WARNING emitted)."

## Findings ledger (all rounds)

| ID | Finding | Status |
|---|---|---|
| F1 | anchor gap-shift docstring/impl divergence | ✅ DEAD @ 34b4db96 (first-existing; re-verified @ 0cf56de0) |
| F2 | croniter DST phantom/double-fire + fall-back drift | ✅ DEAD @ 34b4db96 (re-verified: zero phantoms, 09:00Z window clean) |
| F3 | stale exact-kwargs pin | ✅ DEAD @ 34b4db96 (strengthened; green through 0cf56de0) |
| F4 | reconstruct inherited croniter wrong DATE → missed fire | ✅ DEAD @ 0cf56de0 (all legs + exactly-once + spring mirror) |
| DOC-1/2 | fold rationale wording + literal/non-literal residual note | ⚠️ pre-merge doc correction (text above) — non-gating |

Gate history: round 1 FAIL (F1+F2+F3) @ 3a939a76 → re-gate FAIL (F4) @ 34b4db96 → **closing gate PASS** @ 0cf56de0. Feature is empirically merge-ready pending the 2-minute decisions.md doc correction.

Evidence: worker reports in session; `/tmp/schedval/tz_f4.py`, `/tmp/schedval3/pack_run{1,2}.log`, suite logs.
