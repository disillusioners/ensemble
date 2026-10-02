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
