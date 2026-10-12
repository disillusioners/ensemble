# Part 3 — Daily Prune Validation (mid-flight scope addendum)

> **STATUS: RESOLVED — KEEP (Branch B SELECTED).** The wanderer investigation (instance `1b4c569e`) confirmed the daily scheduled checkpoint-prune run IS load-bearing. Branch A (DISABLE) is REJECTED with evidence (audit trail retained in §2.a and §3). Branch B (KEEP) is SELECTED. **No Part 3 implementation required** — the daily run is intentionally retained. Only a non-blocking cosmetic micro-fix lands in the BE cluster (`be-vacuum-full.md` task 2.8, stale docstring at `daemon/services/maintenance.py:537-540`).

---

## 1 · Frame and status

### 1.1 Frame (post-verdict)

A separate job/report claimed the daily scheduled checkpoint-prune run "isn't needed." The user added "**VALIDATE and likely DISABLE the daily scheduled checkpoint-prune run**" to this commission as a mid-flight scope addendum (Part 3). The wanderer investigation (instance `1b4c569e`, READ-ONLY, worktree @ `c8a3fbf6a` + prod-DB SELECT) examined the claim and produced evidence: the original claim (recovered from prod task `648c2701`, 2026-10-12T07:40:00Z, prior wanderer instance `ba169395` dispatched 07:17Z) actually answered the user's "do I need to schedule anything daily?" with **NO** — referring to **EXTERNAL scheduling (cron/scripts)** — and the entire argument was premised on the **BUILT-IN 24h idle-gated run staying on** ("A built-in maintenance job (runs every 24h, only when the system is idle) hard-deletes everything"). The relayed commission claim ("the daily scheduled BE run isn't needed") **INVERTED** the original meaning. The original claim is NOT present in the repo corpus — it was recovered via read-only prod SELECT.

### 1.2 Status (post-verdict)

| Field | Value |
|---|---|
| Part | 3 (mid-flight scope addendum) |
| Mode | Mode 2 (delta-fold — verdict landed) |
| Wanderer instance | `1b4c569e` (READ-ONLY) |
| Evidence status | **RECEIVED** (see §6 for filled evidence slots) |
| Caller verdict | **NEEDED — DO NOT DISABLE** (2026-10-12) |
| Branch selection | **B (KEEP) — SELECTED** |
| Implementation status | **NOT STARTED. No Part 3 code change required.** |
| Part 2 4-layer guard | **PRIMARY enforcement** (was: PRIMARY for Branch B, defense-in-depth for Branch A; now: PRIMARY for the selected path) |
| Micro-fix | `be-vacuum-full.md` task 2.8 (stale docstring at `maintenance.py:537-540` — non-blocking cosmetic) |

### 1.3 Hard constraint (post-verdict)

**No Part 3 implementation required.** Branch B (KEEP) means the daily run continues unchanged. The Part 2 4-layer guard (`decisions.md §D-4`) remains as **PRIMARY enforcement** for the active scheduled path — exactly as planned. The only Part 3 deliverable that lands in code is the non-blocking micro-fix (task 2.8). Plan review comes next; no implementation starts before then.

---

## 2 · Decision logic (post-verdict)

### 2.a — Branch A (DISABLE): REJECTED-WITH-EVIDENCE

**Verdict: REJECTED.** The wanderer evidence shows the daily run is load-bearing — it is the **only automated drain** of live-tuple pair-excess blobs (Op E, `maintenance.py:1310-1364` → `checkpoint_prune.py:156-402`, destructive default, LIMIT 1000/chunk). This is the exact 23GB / ~460MB-per-day incident class v0.20.0 fixed (`feature/blob-prune-default-on`). The `VacuumMaintenanceJob` (plain `VACUUM (ANALYZE)` only, `vacuum_maintenance.py:1-58`) and the autovacuum reloptions reclaim DEAD tuples only; they **never bound live-row growth**. A stop-direction analysis shows: retention/revive window, Agent Snapshot capture, blob-prune reference extraction all benign or improved; the real casualty is **unbounded DB growth with ZERO automated alerts** (no size gauge, no staleness alarm, no disk alert anywhere in code — independently confirmed twice). The escape hatch (manual ≥weekly + active growth watching) is **strictly worse** than the idle-gated, single-flighted, TOCTOU-guarded, pinned-exempt 24h cycle. Full evidence in §6.

**Audit trail:** the 4 disable-surface options evaluated in §3 are retained as a reference for future commissions that may consider disabling the daily run (e.g. if growth patterns change or if the run is moved to a different scheduler). The options mapping is preserved verbatim; the recommendation (option i-a) is preserved for future reference. The chosen branch is B; the audit trail is the cost of having done the analysis.

### 2.b — Branch B (KEEP): SELECTED

**Verdict: SELECTED.** The daily scheduled checkpoint-prune run continues unchanged. The Part 2 4-layer guard (`decisions.md §D-4`) remains as the **primary enforcement** of the structural impossibility of VACUUM FULL in the scheduled lane. The interactive lane (Part 2's checkbox + `vacuum_full: true` escalation) is **an operator convenience**, not the primary reclaim path (per §D-3.1 resolution; the contingency that "manual FULL becomes PRIMARY reclaim" did not materialize). The micro-fix (task 2.8) corrects a stale class docstring and lands as a non-blocking cosmetic change.

### 2.c — Branch C (out of scope)

Branch C is not in the current decision space. The "rename daily run" / "replace daily run with a different cadence" question was not raised by the user and is not addressed by Part 3.

---

## 3 · Disable-surface options (Branch A only)

If Branch A is selected, the disable can land at one of FOUR surfaces, ordered from least invasive to most invasive. Each option's mechanism, rollback story, manual-path intactness verification, and `/status` display implications are pre-drafted.

### Verification of file:line refs (all checked against worktree base `c8a3fbf6a`)

| Reference | Verified | Source |
|---|---|---|
| `daemon/manager.py:3068-3071` — the `register("checkpoint_cleanup", ...)` site | YES | `daemon/manager.py:3045-3072` (read in worktree) |
| `daemon/services/maintenance.py:289-297` — the never-due docblock (the actual `if min_interval_hours <= 0: return False` is at `:306-307`) | YES | `daemon/services/maintenance.py:286-313` (read in worktree) |
| `tests/test_maintenance.py:206-227` — the existing never-due tests (`test_is_due_zero_interval_never_due_when_never_run`, `test_is_due_zero_interval_never_due_when_previously_run`) | YES | `grep` returned both test names at the cited lines |
| `daemon/services/maintenance_api_service.py:196` — the `/status` `config.cleanup_interval_hours` field | YES | `daemon/services/maintenance_api_service.py:175-234` (read in worktree) |
| `daemon/constants.py:148` — `CHECKPOINT_CLEANUP_INTERVAL_HOURS: int = 24` (the default) | YES | `daemon/constants.py:148` |
| `daemon/config.py:642` — `checkpoint_cleanup_interval: int = Field(default=CHECKPOINT_CLEANUP_INTERVAL_HOURS)` (the Pydantic field) | YES | `daemon/config.py:642` |
| 5 frozen endpoints: `/availability`, `/status`, `/dry-run`, `/execute`, `/runs/{run_id}` | YES | `daemon/routers/maintenance.py:10-14` (all 5 listed in the docblock) |
| **No existing test pins the `register("checkpoint_cleanup", ...)` call** | YES | `grep` returned zero matches in `tests/` (the wiring pin at `test_checkpoint_cleanup_job_wiring_pin.py` covers the construction site + manager handles + auto-execute wiring, but NOT the registration call) |

### 3 · Disable-surface options (Branch A only) — REJECTED — AUDIT TRAIL

> **[RESOLVED 2026-10-12: verdict KEEP — Branch A contingency void.]** The 4 disable-surface options below are RETAINED VERBATIM as audit-trail material for any future commission that may reconsider the daily run (e.g. growth-pattern changes, scheduler migration). The active branch is B (KEEP); the 4 options were MAPPED but NOT TAKEN. The recommended option (i-a) and the option matrix are preserved for future reference; the §5.1 "Under Branch A" tests are retained as audit-trail as well.

If Branch A is selected, the disable can land at one of FOUR surfaces, ordered from least invasive to most invasive. Each option's mechanism, rollback story, manual-path intactness verification, and `/status` display implications are pre-drafted.

### 3.1 — Option (i-a): un-register from `MaintenanceService` (code-level removal of the registration call)

**Mechanism.** Delete the 5-line `register("checkpoint_cleanup", ...)` block at `daemon/manager.py:3068-3072`. The `CheckpointCleanupJob` instance (`self._checkpoint_cleanup_job = checkpoint_cleanup` at `:3067`) is RETAINED — the manual `MaintenanceApiService.execute` path calls `run_checkpoint_prunes` on this instance (per the wiring at `daemon/manager.py:3062-3066`), so the job's manual entry point must stay alive. The auto execution function `checkpoint_cleanup.execute` is no longer wired to a periodic timer.

**Rollback story.** Re-add the 5 lines. Trivial; single-file revert. The construction site stays untouched.

**Manual-path intactness.** `/availability`, `/status`, `/dry-run`, `/execute`, `/runs/{run_id}` all stay functional — they all use the `MaintenanceApiService` + the `self._checkpoint_cleanup_job` instance, neither of which is touched. Verified by reading `daemon/routers/maintenance.py:10-14` + `daemon/services/maintenance_api_service.py`.

**`/status` display.** The `config.cleanup_interval_hours` field (from `daemon/services/maintenance_api_service.py:196`) still reports `self._config.checkpoint_cleanup_interval`, which is 24 (the default at `daemon/config.py:642`). This is **misleading** — the field reads as if the schedule is active when it is not. **Recommended addition:** extend the `/status` `config` block with a derived `cleanup_disabled: bool` flag that reads from a new boolean config field (e.g. `checkpoint_cleanup_disabled: bool = Field(default=False)`), and the registration call is wrapped in `if not self.config.persistence.checkpoint_cleanup_disabled:`. The status would show `cleanup_disabled: true` + `cleanup_interval_hours: 24` (the latter becomes a no-op marker).

**Existing test that pins the behavior.** NONE — `grep` confirms no existing test pins the `register("checkpoint_cleanup", ...)` call. The pre-existing `test_is_due_zero_interval_never_due_*` tests cover the `_is_due` never-due path but the registration site is not pinned.

**New test required.** A new test asserting the registration call is conditionally skipped when `checkpoint_cleanup_disabled: True`. The natural home: a new `tests/integration/test_checkpoint_cleanup_registration_pin.py` (parallel to `test_checkpoint_cleanup_job_wiring_pin.py`).

**Recommendation: **OPTION (i-a) IS THE RECOMMENDED DISABLE SURFACE.** Reasoning:
1. Single 5-line deletion + a guard condition; minimal diff, easy review.
2. Manual path is structurally separate (the wiring at `daemon/manager.py:3062-3066` documents this).
3. The `_checkpoint_cleanup_job` instance is retained, so the manual `run_checkpoint_prunes` and the new `vacuum_full: true` path are intact.
4. Rollback is a trivial revert.
5. The lack of an existing test that pins the registration call means a new pin is needed regardless of the chosen surface — the pin is co-located with the code change.

### 3.2 — Option (i-b): config-level never-due via `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0` default

**Mechanism.** Change the default `CHECKPOINT_CLEANUP_INTERVAL_HOURS` at `daemon/constants.py:148` from `24` to `0`. The `MaintenanceService._is_due` never-due arm at `daemon/services/maintenance.py:306-307` (`if job.min_interval_hours <= 0: return False`) trips; the scheduled cycle never fires. The registration call at `daemon/manager.py:3068-3071` stays as-is (the job is registered, just never due).

**Rollback story.** Change the default back to `24`. Trivial; single-line revert. Operators who set the env var explicitly to a non-zero value continue to get a working schedule (the default is the only thing that changes; per-operator overrides win).

**Manual-path intactness.** Identical to (i-a) — the manual `MaintenanceApiService` + `_checkpoint_cleanup_job` are untouched.

**`/status` display.** The `config.cleanup_interval_hours` field shows `0`, which IS a clear signal that the schedule is disabled (the `0` value is the existing emergency-stop convention per the `daemon/services/maintenance.py:289-297` docblock). No new field needed. **However:** the `/status` consumer (FE wizard) does not currently read `cleanup_interval_hours`; the field is informational. Operators see `cleanup_interval_hours: 0` in the API response, but the FE does not surface this as a UI signal.

**Existing test that pins the behavior.** YES — `tests/test_maintenance.py:206-227` pins the `interval=0` never-due path (`test_is_due_zero_interval_never_due_when_never_run` and `test_is_due_zero_interval_never_due_when_previously_run`). The existing pin is EXACTLY the disable mechanism for this option — the new disable is just a change in the default value that activates an already-pinned code path.

**New test required.** A new test asserting `CHECKPOINT_CLEANUP_INTERVAL_HOURS == 0` as the default (or a test that the config field defaults to 0 in the absence of env override). This is a soft pin; the real guarantee is the existing never-due tests.

**Trade-off.** A default-of-0 is INVISIBLE to operators who upgrade — they see the field as 24 in their `.env` overrides but the daemon boots with 0 from the constant. The change is silent on the operator side. A migration note in CHANGELOG.md is recommended.

**Recommendation: OPTION (i-b) IS A VIABLE ALTERNATIVE if (i-a) is rejected for any reason** (e.g. the caller prefers a config-driven approach over a code change). The 0-default is the existing emergency-stop convention; the convention is already pinned. The trade-off is the silent-default-change.

### 3.3 — Option (i-c): env-default change (set `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0` as the deployed env default)

**Mechanism.** Do NOT change the code. Change the deployed `.env` file to set `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0`. The boot-read at `daemon/constants.py:148` (or the `daemon/config.py:642` Pydantic field's env-var fallback) picks up `0`. Same `_is_due` never-due arm trips.

**Rollback story.** Edit the `.env` to remove the line (or set it to `24`). Trivial; one-line change in the deployment. But: requires a daemon restart to re-read env (the constant is `_boot_read_int_env` at `daemon/constants.py:181-188`).

**Manual-path intactness.** Identical to (i-a) and (i-b).

**`/status` display.** Identical to (i-b) — `cleanup_interval_hours: 0` is the visible signal.

**Existing test that pins the behavior.** Same as (i-b) — the `_is_due` never-due path is pinned. The env-var path is NOT pinned (boot-read semantics are integration-only).

**New test required.** An integration test that boots the daemon with `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=0` and asserts `config.checkpoint_cleanup_interval == 0` after boot. Not in the current test corpus; would be a new file.

**Trade-off.** This is a DEPLOYMENT change, not a code change. It does not affect source repos, PRs, or CI. It is the LEAST invasive option for the source tree but the MOST invasive for the deployment (every install needs the env change). The existing `feature/blob-prune-default-on` precedent (which removed an env-flag ladder) suggests the project prefers code-level config defaults over per-deployment env files; this option REVERSES that preference.

**Recommendation: OPTION (i-c) IS NOT RECOMMENDED.** Reasoning: the project has been moving AWAY from per-deployment env knobs (the `feature/blob-prune-default-on` commission retired the `BLOB_PRUNE_DESTRUCTIVE_ENABLED` env flag in favor of an unconditional destructive default; the same logic applies here). A code-level change is more reviewable than a deployment change.

### 3.4 — Option (ii): FULL REMOVAL of the scheduled wiring

**Mechanism.** Delete the `register("checkpoint_cleanup", ...)` call (option i-a) AND optionally delete the `CheckpointCleanupJob` class entirely (if no other code path uses it). The manual path calls `run_checkpoint_prunes` on the `_checkpoint_cleanup_job` instance, so if the class is deleted, the manual path breaks — `run_checkpoint_prunes` is a method on `CheckpointCleanupJob`. **Therefore, "full removal" in practice means: delete the registration call AND keep the class alive for the manual path.** This is identical to option (i-a) in code terms.

**Rollback story.** Same as (i-a) — trivial revert.

**Manual-path intactness.** Same as (i-a) — the class stays, the manual path stays.

**`/status` display.** Same as (i-a) — needs the `cleanup_disabled: bool` flag addition.

**Existing test that pins the behavior.** None for the registration call; same as (i-a).

**New test required.** Same as (i-a).

**Trade-off.** "Full removal" as a name suggests deleting more than the registration, but the class is needed for the manual path. In practice, (ii) collapses to (i-a). The only true "full removal" would be deleting the class, which breaks the manual path — that is OUT OF SCOPE (the manual path is the only path left after disable; deleting the class means deleting the entire maintenance console for checkpoint-cleanup).

**Recommendation: OPTION (ii) IS NOT A DISTINCT OPTION — it collapses to (i-a).** The "full removal" framing is a misnomer; the only thing that can be removed is the registration call. The class stays.

### 3.5 — Option summary table

| Option | Mechanism | Rollback | Manual path | /status display | Existing test | New test | Recommendation |
|---|---|---|---|---|---|---|---|
| (i-a) un-register | 5-line deletion + new `cleanup_disabled` config field | trivial revert | intact | new flag needed | none | new wiring pin | **RECOMMENDED** |
| (i-b) config-default 0 | 1-line default change at `constants.py:148` | trivial revert | intact | `interval: 0` (existing) | `_is_due` never-due pinned | soft pin on default | viable alternative |
| (i-c) env-default 0 | `.env` file change | 1-line revert | intact | `interval: 0` (existing) | `_is_due` never-due pinned | new boot-read test | NOT recommended (reverses project direction) |
| (ii) full removal | collapses to (i-a) | same as (i-a) | same as (i-a) | same as (i-a) | same as (i-a) | same as (i-a) | NOT a distinct option |

---

## 4 · Guard interplay per branch

The Part 2 4-layer guard (per `decisions.md §D-4`) makes the scheduled path structurally incapable of escalating to VACUUM FULL. Part 3 changes WHICH branches of the code are reachable, which changes the guard's role.

### 4.1 — Option (i-a) selected: registration removed at code level

**Config re-enable is possible** (an operator can set `checkpoint_cleanup_disabled: False` in their config or remove the env override). The Part 2 4-layer guard STAYS as defense-in-depth for the case where a future operator re-enables the schedule.

**Guard status:** ACTIVE. The 4 layers (MANUAL-ONLY method on `CheckpointCleanupJob`, explicit `vacuum_full=False` kwarg, AST pin in `execute()`, AST pin on the new method's definition site) are unchanged. The guard is defense-in-depth, not the primary gate (the registration is the primary gate; the guard is the backstop).

**AST pin changes:** NONE. The existing pins at `tests/integration/test_checkpoint_cleanup_job_wiring_pin.py` (INV-1/INV-9 + the new `_run_vacuum_full_after_cleanup` pins from Cluster 4) continue to pass against the code.

### 4.2 — Option (i-b) selected: config-level never-due

**Config re-enable is possible** (an operator can set `PERSISTENCE_CHECKPOINT_CLEANUP_INTERVAL=24` or change the constant default). The Part 2 4-layer guard STAYS as defense-in-depth, identical to (i-a).

**Guard status:** ACTIVE. Unchanged.

**AST pin changes:** NONE.

### 4.3 — Option (i-c) selected: env-default change

**Config re-enable is trivial** (remove the env line). The Part 2 4-layer guard STAYS as defense-in-depth, identical to (i-a) and (i-b).

**Guard status:** ACTIVE. Unchanged.

**AST pin changes:** NONE.

### 4.4 — Option (ii) selected: same as (i-a) (collapses)

Same as 4.1.

### 4.5 — Summary: guard interplay (post-verdict, Branch B SELECTED)

> **[RESOLVED 2026-10-12: verdict KEEP — Branch A contingency void.]** The "guard's role shifts from primary defense to defense-in-depth backstop" framing below was a Branch-A contingency. Under Branch B (KEEP, the active branch), the Part 2 4-layer guard remains the **PRIMARY** enforcement of the structural impossibility of VACUUM FULL in the scheduled lane. The contingency text is RETAINED below as audit-trail reference for any future commission that may consider disabling the daily run.

**Original (Branch A) contingency — RETAINED AS AUDIT TRAIL ONLY, NOT ACTIVE:**

Under Branch A (DISABLE), the Part 2 4-layer guard STAYS in every option (i-a, i-b, i-c, ii). The guard's role shifts from "primary defense" to "defense-in-depth backstop" (because the disable surface is the primary gate), but the guard itself is unchanged. The AST pins in `test_checkpoint_cleanup_job_wiring_pin.py` continue to pass. The new `test_vacuum_maintenance_job_no_full.py` pin (Cluster 4) is unaffected by Part 3 (it guards `VacuumMaintenanceJob`, not the checkpoint cleanup job; Part 3 does not touch `VacuumMaintenanceJob`).

**No AST pin changes are required for Branch A.** The pins guard a code surface that still exists; they just guard a path that is no longer reachable by default. The pins are cheap (AST parse + a few assertions) and protect against a future commission that re-enables the schedule.

### 4.6 — Hypothetical: if Branch A removed the code surface entirely — RETIRED

> **[RESOLVED 2026-10-12: verdict KEEP — contingency void.]** This §4.6 hypothetical (tombstone management for a removed `CheckpointCleanupJob` class) is **RETIRED**. Branch A is REJECTED, so the tombstone-management question never materializes. The text below is preserved verbatim for any future commission that may propose a deeper removal of the scheduled wiring. If such a commission arises, the keep-as-tombstone recommendation can be re-evaluated then.

If Branch A involved deleting the `CheckpointCleanupJob` class (it does NOT, per §3.4), the AST pins that reference the class would become TOMBSTONES — they guard a removed surface. The three options for tombstone pins are:

1. **Keep-as-tombstone (recommended).** The pins remain in the test file with a docblock note explaining the surface was removed by Part 3. If a future commission re-adds the class, the pins automatically guard it. Cost: a few lines of dead code in the test file.
2. **Retire (delete).** Remove the pins entirely. The test file shrinks. Cost: a future re-add commission must re-derive the guard from scratch.
3. **Repoint (move to a new surface).** If a new class takes over the role, the pins are updated to guard the new class. Cost: requires the new class to be in place.

**Recommendation for the hypothetical: KEEP-AS-TOMBSTONE.** The cost is trivial; the protection against a future re-add is real.

**This sub-section is HYPOTHETICAL for current Part 3 options — it documents the policy for a future commission that may consider deeper removal.** No current Part 3 option requires tombstone management.

---

## 5 · Test additions per branch

### 5.1 — Under Branch A (DISABLE)

| Test | File | Acceptance |
|---|---|---|
| `test_scheduled_lane_never_starts_when_disabled` | `tests/integration/test_checkpoint_cleanup_registration_pin.py` (new) | Boot the daemon with `checkpoint_cleanup_disabled=True` (option i-a) or `checkpoint_cleanup_interval=0` (option i-b/c); assert `_maintenance_service._jobs` does not contain a job named `checkpoint_cleanup` (option i-a) or the job's `_is_due` always returns False (option i-b/c). |
| `test_manual_path_intact_when_scheduled_disabled` | `tests/integration/test_maintenance_api_service.py` (extend) | With the scheduled lane disabled, `POST /api/maintenance/checkpoint-cleanup/dry-run` and `POST /api/maintenance/checkpoint-cleanup/execute` both succeed. The 5 endpoints (`/availability`, `/status`, `/dry-run`, `/execute`, `/runs/{run_id}`) all return their FROZEN shapes. |
| `test_status_shows_disabled_state` | `tests/integration/test_maintenance_api_service.py` (extend) | `GET /api/maintenance/checkpoint-cleanup/status` returns a `config` block with `cleanup_disabled: True` (option i-a) or `cleanup_interval_hours: 0` (option i-b/c) under the disable surface. The `last_run` and `in_flight` blocks continue to return the historical values (the disable does not clear the audit history). |
| `test_no_registration_call_when_disabled` (option i-a only) | `tests/integration/test_checkpoint_cleanup_registration_pin.py` (new) | AST-level assertion: when `checkpoint_cleanup_disabled=True`, the `register("checkpoint_cleanup", ...)` call site is NOT executed. Use `unittest.mock.patch` on `MaintenanceService.register` and assert the call was skipped. |
| `test_existing_is_due_zero_pin_still_passes` | `tests/test_maintenance.py` (existing) | The pre-existing `test_is_due_zero_interval_never_due_when_never_run` and `test_is_due_zero_interval_never_due_when_previously_run` continue to pass. |

### 5.2 — Under Branch B (KEEP) — SELECTED — NO NEW TESTS

> **[RESOLVED 2026-10-12: per caller directive.]** Per the caller's directive in the 2026-10-12 verdict message: "Part 3 contributes NO implementation cluster and NO test additions — strike the Branch-B test-evidence-file test from the plan (caller directive; the evidence lives in the plan doc itself). Final Part 3 deliverable = the evidence document + a line in plan-overview saying the daily run is INTENTIONALLY RETAINED."

**No new tests under Branch B.** The previously proposed `test_evidence_documentation_logged` test is **STRUCK** per the caller directive. The evidence lives in this plan document (§6 below) and in `decisions.md §D-8`; no separate test or evidence fixture file is required. The existing test surface (Part 1+2 + pre-existing wiring pins) is unchanged. Branch B is the cheapest branch in implementation terms: **zero code changes, zero test additions, zero new artifacts.** The only Part 3 deliverable in code is the non-blocking micro-fix in `be-vacuum-full.md` task 2.8 (stale class docstring at `daemon/services/maintenance.py:537-540`).

### 5.3 — Under Branch C (out of scope)

No tests. Branch C is not in the current decision space.

---

## 6 · Evidence slots (FILLED — verdict received 2026-10-12)

The wanderer investigation completed; the caller's verdict is in. All 9 evidence slots + the implementation-readiness checklist are now filled.

### 6.1 — Wanderer evidence (FILLED)

**`[wanderer verdict]`** = **NEEDED — DO NOT DISABLE.** The daily scheduled checkpoint-prune run is load-bearing.

**`[wanderer evidence citations]`** = the following 5 evidence points, recovered via read-only prod SELECT + worktree code inspection (worktree @ `c8a3fbf6a` + prod-DB SELECT; original claim from prod task `648c2701`, 2026-10-12T07:40:00Z; prior wanderer instance `ba169395` dispatched 07:17Z):

1. **Original claim recovered (prod DB SELECT).** The original claim answered the user's "do I need to schedule anything daily?" with **NO** — referring to **EXTERNAL scheduling (cron/scripts)** — and its entire argument was premised on the **BUILT-IN 24h idle-gated run staying on** ("A built-in maintenance job (runs every 24h, only when the system is idle) hard-deletes everything"). The relayed commission claim ("the daily scheduled BE run isn't needed") **INVERTED** the original meaning. The original claim is **NOT** present in the repo corpus — recovered via read-only prod SELECT.

2. **Op E is the only automated drain of live-tuple pair-excess blobs.** The daily cycle's Op E (pair-excess blob prune, `daemon/services/maintenance.py:1310-1364` → `daemon/services/checkpoint_prune.py:156-402`, destructive default, LIMIT 1000/chunk) is the **ONLY** automated drain of live-tuple pair-excess blobs — the exact 23GB / ~460MB-per-day incident class that `feature/blob-prune-default-on` (v0.20.0) fixed. `VacuumMaintenanceJob` (plain `VACUUM (ANALYZE)` only, `daemon/services/vacuum_maintenance.py:1-58`) and the autovacuum reloptions reclaim **DEAD** tuples only; they **never bound live-row growth**.

3. **Stop-direction degradation.** Retention/revive window, Agent Snapshot capture, blob-prune reference extraction all benign or improved; the real casualty is **unbounded DB growth with ZERO automated alerts** (no size gauge, no staleness alarm, no disk alert anywhere in code — independently confirmed twice). The escape hatch (manual ≥weekly + active growth watching) is **strictly worse** than the idle-gated, single-flighted, TOCTOU-guarded, pinned-exempt 24h cycle.

4. **Escape hatch is strictly worse than the 24h cycle.** Manual-only reclaim cadence with active growth watching is operator-burden-heavy, race-prone (no single-flight lock, no TOCTOU guard, no pinned-exempt logic), and unbounded. The 24h cycle is operator-blind, idle-gated, single-flighted (`MaintenanceRunLock`), TOCTOU-guarded (per-pair SERIALIZABLE wrap), and pinned-exempt (`pinned_subtree_terminal_count` metric protects descendants under pinned roots from TTL purge). Replacing the cycle with manual reclaim is a clear regression.

5. **Sole scheduled-lane coupling point (for the record, UNUSED).** `register("checkpoint_cleanup", ...)` at `daemon/manager.py:3068-3072` (the outer 5-line block; inner content is at `:3070-3073` — minor range convention drift, same call site). The interactive lane is independently constructed at `daemon/manager.py:3046` (the `CheckpointCleanupJob(...)` construction; the api.py lifespan reads `manager._checkpoint_cleanup_job` for manual execution). Disable options were MAPPED but NOT TAKEN (Branch A REJECTED).

**`[wanderer date]`** = 2026-10-12 (wanderer instance `1b4c569e`, READ-ONLY).

### 6.2 — Caller final branch selection (FILLED)

**`[final branch selection]`** = **B (KEEP).** The daily scheduled checkpoint-prune run is **INTENTIONALLY RETAINED**. The relay's "likely DISABLE" recommendation is overridden by the evidence; the run is load-bearing and the disable would create a gap that no other automated mechanism covers.

**`[final branch rationale]`** = per the 2026-10-12 caller verdict: "VERDICT: NEEDED — DO NOT DISABLE. Branch B (KEEP) SELECTED. Part 3 = evidence + NO code change." The full verdict message records Branch A (DISABLE) as REJECTED-WITH-EVIDENCE (audit trail, options mapping retained) and Branch B as SELECTED. The Part 2 4-layer guard (`decisions.md §D-4`) remains PRIMARY enforcement for the active scheduled path.

**`[caller date]`** = 2026-10-12.

### 6.3 — Branch-specific data (FILLED)

**`[branch-A: chosen option + rollout plan]`** = **NOT APPLICABLE.** Branch A REJECTED; no option chosen, no rollout plan needed. The 4 disable-surface options (i-a un-register, i-b config-default-0, i-c env-default-0, ii full-removal) are retained in §3 as audit-trail reference for any future commission that may reconsider the daily run.

**`[branch-B: evidence file path]`** = **NOT APPLICABLE (per caller directive).** Per the 2026-10-12 verdict: "the evidence lives in the plan doc itself." The evidence is in this file (`part3-daily-prune-validation.md §6.1`) and in `decisions.md §D-8`. No separate evidence fixture file is committed to the repo.

**`[branch-C: out-of-scope confirmation]`** = Branch C is not in the current decision space. The "rename daily run" / "replace daily run with a different cadence" question was not raised by the user. If it is raised in a future commission, this evidence base applies: any rename or replacement must preserve the Op E live-tuple drain mechanism (otherwise the same 23GB-class gap reopens).

### 6.4 — Implementation-readiness check (FILLED — Branch B path is N/A)

The implementation-readiness checklist is updated for the Branch B outcome. Most items are N/A (no code change); the only active item is the micro-fix.

- [x] Wanderer verdict received — **B (KEEP)**.
- [x] Caller final branch selection received — **B (KEEP)**.
- [N/A] (Branch A) Disable option chosen — N/A, Branch A REJECTED.
- [N/A] (Branch A) Rollout plan documented — N/A.
- [N/A] (Branch B) Evidence file committed — N/A per caller directive; evidence lives in plan doc.
- [x] (Branch B) `decisions.md §D-8` added with verdict, evidence pointer, guard-primary consequence, micro-fix, and §D-3.1 resolution line.
- [x] `plan-overview.md` Part-3 section REPLACED per its own §8 instruction; pre-existing 7 sections frozen, not renumbered.
- [x] `part3-daily-prune-validation.md` updated: status table → RESOLVED-KEEP, §2 branches marked, §3 audit-trail, §4 guard-interplay §4.5/§4.6 RESOLVED markers, §5.2 evidence test STRUCK, §6 evidence slots FILLED, §7/§8/§9 updated.
- [x] `be-vacuum-full.md` task 2.8 added (non-blocking cosmetic micro-fix for stale docstring at `daemon/services/maintenance.py:537-540`).
- [ ] **PENDING — plan review.** No implementation starts before plan review (per the dispatcher's hard constraint: "no Part 3 implementation required... plan review comes next").
- [ ] **PENDING — reviewer + tester gates green; promote ready.**

---

## 7 · Cross-references (post-verdict)

- `decisions.md §D-3.1` — the verbatim caller note "If Part 3 ends up disabling the daily run, manual FULL becomes the PRIMARY reclaim path — further reason not to gate it." **CONTINGENCY DID NOT MATERIALIZE** (Branch B SELECTED, daily run retained). The note is RETAINED as an adjudication record (the caller's reasoning at the time of adjudication); an appended resolution line in `decisions.md §D-8` clarifies that manual FULL is an operator convenience, not the primary reclaim path.
- `decisions.md §D-4` — the Part 2 4-layer guard. **PRIMARY enforcement** for the active scheduled path (Branch B). The Branch A contingency (defense-in-depth backstop) is void.
- `decisions.md §D-7` — the Adjudication Log (7 caller adjudications from 2026-10-12, all CONFIRMED).
- `decisions.md §D-8` — **NEW** (added in this fold-in): the Part 3 verdict, evidence pointer to this part3 doc, guard-primary consequence, micro-fix pointer, and the §D-3.1 resolution line.
- `be-vacuum-full.md` — the Part 2 BE work. **Unchanged by Part 3**; task 2.8 (stale docstring micro-fix at `daemon/services/maintenance.py:537-540`) is the only Part 3 deliverable in the BE cluster.
- `test-plan.md` — the Part 2 test plan. **Unchanged by Part 3** (Branch B = no new tests per caller directive).
- `plan-overview.md` — the canonical plan overview. The Part-3 pending section was **REPLACED** per its own §8 instruction; pre-existing 7 sections frozen, not renumbered. The new Part-3 section records the Branch B verdict + an out-of-scope observation about the absence of automated checkpoint-table growth alerting.

---

## 8 · plan-overview.md update (post-verdict) — DONE

The `plan-overview.md` Part-3 pending section has been **REPLACED** in this fold-in (per its own §8 instruction) with a final Part-3 section recording the Branch B verdict + the out-of-scope observation about the absence of automated checkpoint-table growth alerting. The pre-existing 7 sections (Objective / Scope / Phases / Coupling / Risks / Success Criteria / Research Insights / Open Questions) are NOT renumbered or rewritten (Mode 2 additivity; the 6-cluster plan set is frozen as of 2026-10-12).

The Part-3 section in `plan-overview.md` now reads (verbatim):

> **Part 3 — Final verdict: KEEP (Branch B SELECTED).** The wanderer investigation (instance `1b4c569e`, 2026-10-12) confirmed the daily scheduled checkpoint-prune run is load-bearing: Op E (pair-excess blob prune, `maintenance.py:1310-1364` → `checkpoint_prune.py:156-402`, destructive default, LIMIT 1000/chunk) is the ONLY automated drain of live-tuple pair-excess blobs — the exact 23GB / ~460MB-per-day incident class v0.20.0 fixed. The Part 2 4-layer guard remains PRIMARY enforcement for the active scheduled path. No code change required; the only Part 3 deliverable is a non-blocking cosmetic micro-fix (`be-vacuum-full.md` task 2.8, stale docstring at `daemon/services/maintenance.py:537-540`). **Out-of-scope observation for the final user report:** no automated checkpoint-table growth alerting exists anywhere in code; with the daily run retained, this is acceptable, but worth surfacing to the user. Full evidence in `part3-daily-prune-validation.md` and `decisions.md §D-8`.

---

## 9 · Open questions for the caller (RESOLVED — Branch B SELECTED)

The 5 questions that were open in the pre-verdict skeleton are resolved by the Branch B verdict. The questions and their resolutions:

1. **Wanderer scope.** RESOLVED — the wanderer covered the checkpoint-prune run only (per the verdict message). The `VacuumMaintenanceJob` is a SEPARATE scheduled job, registered at `daemon/manager.py:3098-3099`; it was not in scope. If a future commission considers the vacuum job, a separate wanderer investigation would be needed.

2. **Disable scope.** N/A — Branch A REJECTED, no disable.

3. **Rollback window.** N/A — Branch A REJECTED, no rollback needed. The existing `feature/blob-prune-default-on` precedent (1-cycle-ledger) is unaffected by Part 3.

4. **Operator notification.** N/A — Branch B (KEEP) means no operator-facing change. The non-blocking micro-fix (stale docstring) is a developer-facing change only; no operator notification needed.

5. **Documentation update.** PARTIALLY APPLIED. `daemon/constants.py:264-285` policy comment is unchanged (the Part 2 §D-6 supersession still applies; scheduled lane stays plain VACUUM only). `daemon/services/maintenance.py:537-540` stale docstring is corrected in micro-fix task 2.8. CHANGELOG.md, the runbook, and the maintenance API `/status` response are unchanged.
