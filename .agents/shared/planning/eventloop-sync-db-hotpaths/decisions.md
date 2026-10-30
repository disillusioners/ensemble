# Decision Log — event-loop sync-DB hot paths (board `wi-3f5bea95`)

Date: 2026-10-29 (fold round 1 — architect ruling + leader rulings folded in; **fold round 2 — reviewer verdict REJECT: C-1…C-8 + W-1…W-18 + A-1…A-11 + three leader rulings folded**)
Author: planner[v2] via plan-creation worker
Tree: `fix/eventloop-sync-db-hotpaths` @ `42764588c` (base `b1183a637` = `origin/latest` = v0.27.13 tag target; **live release = v0.27.12** — see D-57/W-6)
Lane posture: **STAGE-ONLY** — no live promote, no live daemon restarts, no `.env` edits.

This log records the decisions the plan is built on, their disposition, and their authority. Leader rulings are inherited (not re-litigated); planner adjudications are binding plan inputs.

**FOLD ROUND 1 (2026-10-29):** the architect's ruling (`architecture-recommendation.md`, 406 lines) and the leader's rulings R1–R8 are folded in. **D-0…D-18 are preserved verbatim in substance** — where a decision is reversed, it is marked **SUPERSEDED** with a pointer to its successor, and the reversal reason is recorded. **New decisions run D-19…D-41.**

**FOLD ROUND 2 (2026-10-29):** the plan-gate reviewer's verdict **REJECT** (`.agents/reviewer/RESULTS/2026-10-29-wi-3f5bea95-plan-gate-review.md`, 185 lines: 8 criticals, 18 warnings, 11 advisories) and **three leader rulings** are folded in. **New decisions run D-42…D-65.** Round 1's D-19…D-41 are preserved; the specific ones amended are named in each new row and tabulated in the round-2 preamble.

**Authority legend:** `LEADER` (binding, do not re-litigate) · `ARCHITECT` (binding ruling — the architect's file is authority for this lane) · `REVIEWER` (plan-gate reviewer; a critical/warning is binding on the plan text) · `PLANNER` (binding plan input — the dispatcher decided it) · `ANALYSIS` (inherited from the verified technical analysis) · `TESTER` (tester-lane ownership; not a developer task).

**Cross-map legend:** architect D-2/D-4/D-16 and §-references; leader R1–R8 (round 1) and **RULING 1/2/3** (round 2); reviewer **C-n / W-n / A-n**.

**⚠️ ID-COLLISION NOTE (round 2):** this lane's own decision ids continue **D-42…D-65**. The sibling `db-pool-idle-txn` lane also uses ids in this range (**sibling D-47, D-49, D-57, D-58**). Where this log cites a sibling decision it always prefixes **"sibling D-nn"**; an unprefixed **D-nn ≥ D-42 refers to THIS log**. The same caution applies to the round-1 sibling references (sibling D-15/D-33/D-34/D-36/D-49/D-57/D-58).

---

## D-0 — Lane posture: stage-only

**Disposition:** ADOPTED (LEADER / commission ground rule).
No live promote, no live daemon restarts, no `.env` edits; the artifact is staged only and the promote belongs to the user's 3-factor nonce ceremony. The lane's fence is asserted by task 5.8 and criterion G7.

## D-1 — Mechanism vocabulary: A (batch) + C (off-loop bridge)

**Disposition:** ADOPTED (LEADER Q3).
**A** = query-shape batch (N statements → 1); **C** = off-loop bridge (the unchanged sync callable executed on a dedicated executor thread). Async-native is **not** this lane's mechanism (see D-2, now decided at D-19). Q3 also fixes: A = one IN-query with an **explicit deterministic ORDER BY**; **Shape E rejected** (children are consumed by `daemon/tools/instance.py:6165` + the e2e pins).

## D-2 — The architecture question (§3): async-native vs to_thread bridge

**Disposition:** ~~ARCHITECT-DECIDABLE — Option 3 (hybrid) recommended~~ → **SUPERSEDED by D-19** (the architect has ruled).

The original option set is preserved for history: Option 1 = to_thread bridge on a dedicated executor; Option 2 = async-native (async SQLAlchemy / asyncpg on a dedicated engine + DSN); Option 3 = A-batches + C-bridge now, async-native deferred. The recommendation stood until the architect ruled; **D-19 records the ruling and the corrected framing** (D-2 is a *policy about the future*, not an architecture choice — architect §1, §2.7).

**Assumption recorded (ANALYSIS §3, retained):** the default executor stays shared (sibling A7) — if a future lane bounds default-executor usage system-wide, the bridge's dedicated pool remains correct. `uv.lock` already carries sqlalchemy 2.0.48 / asyncpg 0.31.0 / greenlet 3.3.2, keeping the Option-2 door open without new dependencies.

## D-3 — D-A: the router-default mismatch (`include_descendants`)

**Disposition:** ADOPT (c) keep + document (PLANNER adjudication, ratified by the analysis §5) → **AMENDED by D-33** (keep + NAME as a pair + make the two internal call sites explicit).

- **Fact:** `daemon/routers/instances.py:387` — `include_descendants: bool = Query(True, …)` ("default — back-compat for every existing consumer", docstring `:389-395`); `daemon/services/instance_lifecycle.py:5027` — `include_descendants: bool = False` (manager default). The agent tool at `daemon/tools/instance.py:6165` consumes the **manager** default (flat).
- **(a) router → False:** REJECTED for this lane — a response-semantics change for any consumer that omits the param. **Routed to the Q2 FE-shapes follow-up, contingent on an FE consumer audit.**
- **(b) manager → True:** REJECTED outright — every internal `list_instances` caller (including the tool at `:6165`) would load BFS trees.
- **(c) keep + document:** ADOPTED, and **amended by D-33** to (c)+ — keep both defaults, express them as a **named pair**, make the two internal production call sites **explicit**, and write the docstring.

**Binding carry-through:** the docstring at `daemon/routers/instances.py:389-395` must state the mismatch, the disposition, and both rejected alternatives (task 2.6). **No default value changes in this lane.**

## D-4 — D-B: orphan-miss closure in `_job_to_record` (EXPLICIT EXTENSION beyond the Q1 ruling text)

**Disposition:** ADOPT with architect-sight flag (PLANNER adjudication) → **ADOPTED AS REQUIRED and AMENDED by D-28** (three lookup sites, not one; degradation-aware absence set).

**What the ruling says:** Q1-i names the **Task branch** only — "`work_resolver.py:1396-1397` (batch mirroring `:1421-1424` byte-identically)".

**What this plan adds — and labels as an extension:** the closure of the authoritative-miss re-lookup in `_job_to_record`'s fallback (`daemon/services/work_resolver.py:1846-1849`: `if instance is None: instance = self._lookup_instance(job.instance_id)`).

**Why the extension is necessary (not optional):**
1. The Job branch is **already batched** (`:1421-1424`), yet BASELINE §5/§6 show per-row `_lookup_instance` singles dominating the jobs path — **92.3 % of statement TIME** and ~1,250–1,860 singles per request.
2. The singles fire in the **degradation fallback**: for an orphan JobItem (an `instance_id` with no `Instance` row — the fixture's 1,250-row population) the batch SELECT provably returns no row, the map lookup yields `None`, and the fallback pays one doomed `SELECT instances` per row that returns `None`.
3. Therefore Q1-i alone **does not move the measured number**. Without D-B, C-2/C-4 cannot move the 92.3 % statement-TIME cost and the commission's measurable-success criterion fails on ruled scope alone.

**Equivalence argument (ANALYSIS §2.2; architect §3.2, proven field-by-field):** a batch-proven-absent id yields `None` whether the code re-queries or not. The response bytes are identical in every non-race execution. Architect §3.2 enumerates the six `WorkRecord` fields that depend on `instance` (`status`, `mission_liveness`, `mission_id`/`mission_epoch`/`mission_terminal_reason`, `mission_ref`, `completion_gate_escalated`) and proves each renders identically for a batch-proven-absent id.

**The one disclosed semantic delta:** today, a row committed between the batch statement and the fallback statement **would** be found; after closure it would not (the batch's snapshot becomes authoritative). Each repo call opens its own session/transaction today, so this observation window already exists between batch and fallback — closure narrows it to the batch snapshot. Probability ≈ 0 in practice (an instance deleted then re-created with the same id mid-request). **Disclose, do not engineer.**

**Architect ruling (D-28) supersedes the single-site framing:** the closure must cover **three** sites — `work_resolver.py:1778-1779` (ACTIVE guard; **does not fire** for the fixture's orphans, which are `admission_state='done'`), `:1846-1847` (mission-liveness; ~50 %), and **`:1878` (the dominant, unguarded mission-fields fallback — 100 %)**. The architect's measured arithmetic: `/api/work` has **no limit param** (`daemon/routers/work.py:109-136`), so the limit lever cannot reach it ⇒ **D-B is the only lever there.**

**Fallback if the architect rejects D-B:** C-4 keeps (i)+(ii) exactly as ruled; the measured targets re-scope to **loop-offloading-only**, with disclosure that `/api/work` round-trips stay ~1,300+ and the 92.3 % item remains open (it would then need its own commission). This fallback is recorded here so the plan has no silent branch. **Status: the architect did NOT reject it — D-4 is now REQUIRED (D-28).**

## D-5 — D-C: the byte-safe limit composition

**Disposition:** ADOPT **α** — limit + budgeted patch-up → **SUPERSEDED by D-29**: α's patch-up must be **BATCHED**, not per-row `resolve_work`.

- **α (ADOPTED, form REWORKED by D-29):** propagate the limit AND guarantee coverage — any page-job missing from the limited map is patched up, but by a **batched** patch-up (one job_ids IN-select + one instance batch + one mission batch), not by per-row `resolve_work`.
- **β (REJECTED):** align orderings so the top-N of the broad population ⊇ the top-N of the page. Fragile under `created_at` ties, which bursts make common.

**The hazard α resolves (ANALYSIS §2.4):** the enrichment exists to source `work_records_by_id` for the **response page**, and the page comes from `service.list_jobs(...)` with filters (`job_types`, `mission_id`, `queue_id`, `include_deleted`) that are **not** passed to `list_work` — the enrichment population is deliberately **broader** than the page. Naively limiting it can drop a page-job's record → the documented degrade path at `daemon/routers/jobs_crud.py:900-902` ("degrades to the legacy JobItem-mirror path") would silently change response fields.

**This implements Q1-ii; it does not amend it.** "No server-side default cap" is preserved: the limit applies **only** when the caller explicitly sent one.

## D-6 — Tester pre-gate: the two-part children-order gate

**Disposition:** ADOPTED (PLANNER adjudication — a **required pre-gate design decision**) → **AMENDED by D-34** (five architect corrections: multiset, per-parent AND global, static SQL pin, all returned rows, detail path shares the ordered source).

1. **BASE re-run empirically captures and documents** whether the incidental children-array order on the fixture coincides with the pinned order (`parent_id ASC, instance_id ASC`). Today's per-row query has **no ORDER BY** (`daemon/repositories/instance/repository.py:432-436`) — BASE's order is unspecified by contract, so the capture makes the diff result interpretable either way.
2. **The byte-diff gate** compares exact bytes everywhere **EXCEPT one disclosed normalization** (the `children` array's element order), **PLUS a positive assertion** that HEAD's children order is exactly the Q3-pinned `ORDER BY parent_id ASC, instance_id ASC`.

**No other normalization is permitted** — no field omission, no key sorting, no whitespace/float tolerance, no whole-response sort. The normalizer is **field-scoped** (children only). **Set-equality per parent is pinned separately**, so the normalizer can reorder but never add or drop.

**Why this is settled in the plan rather than at the gate:** it is the lane's single most likely false-red (ANALYSIS §8 risk 1), and the two-part pairing is what keeps the gate strong — a normalization alone could mask a real drift; the set-equality pin plus the positive-order assertion cannot.

## D-7 — Bridge executor: dedicated pool, 4 workers

**Disposition:** ~~ADOPTED (ANALYSIS §2.0)~~ → **SUPERSEDED by D-21 (sizing = 6)** and **D-20 (module/API rename)** and **D-24 (shutdown premise corrected)**.

- **Dedicated module-level executor**, never the default executor. The default `ThreadPoolExecutor` is the pool the jobs badge already saturates (BASELINE §5: 5/12 workers inside repo statements) and 1,400 sites share it (sibling A7; see D-39 for the re-derived count). A bridge on the default executor would relocate the problem. **[carried forward]**
- **Sizing 4** — *"the four bridged endpoints firing concurrently"*. **[SUPERSEDED by D-21: 6. The architect measured the coincidence set at HEAD as already 4, so 4 = zero margin, and "may drop to 3" is negative margin.]***
- **Capacity 15** (`pool_size` 5 + `max_overflow` 10, cited at `daemon/repositories/factory.py:143-144`, `:155-156`, `:435`). **[SUPERSEDED by D-22: the manager engine is built at `factory.py:434-435` with `manager_pool_size` (default 15, `daemon/constants.py:264`) + `max_overflow=10` ⇒ capacity 25. The cited lines were the SQLite/generic dataclass defaults — a stale citation, not a category error in the number alone.]**
- **Precedents**: the bridge **shape** follows `daemon/services/job_queue_service.py:1732`, `:1831-1837` and `daemon/services/instance_messaging.py:6045`, `:6080`; the **dedicated-pool** shape follows `daemon/services/plane_sync_service.py:253`, `daemon/services/question_pause_set.py:137`, `daemon/services/upgrade_journal_sweep.py:233`. None of the three is reusable (task-specific contracts) → one new module. **[carried forward]**
- **`copy_context().run`** is mandatory (`run_in_executor` does not propagate context). **[carried forward]**
- **Shutdown:** *"daemon-lifetime pool; no explicit shutdown (no house precedent shuts one down)"*. **[SUPERSEDED by D-24: the premise was FALSE — `daemon/services/upgrade_journal_sweep.py:332-334` DOES call `executor.shutdown(wait=False, cancel_futures=True)` on its dedicated waitpid executor. The accurate statement is: no MODULE-GLOBAL dedicated executor is shut down. The bridge now gets a bounded drain seam (B3) before `dispose_db_pools`.]**
- **Forbidden:** bridging a `WriteGuardSession` participant or anything holding a scope across the boundary (sibling D-33/D-34). **[carried forward, strengthened by D-35 (TB-1..TB-7)]**

## D-8 — How "explicit limit" is detected (plan-level choice)

**Disposition:** ADOPTED — **raw query-param presence**, not a signature change (PLANNER) → **AMENDED by D-30** (the slice must land PRE-CONVERSION).

The endpoint's `limit: int = DEFAULT_JOB_LIST_LIMIT` (`daemon/routers/jobs_crud.py:734`, 50 at `daemon/constants.py:17`) is the **page** limit and must keep its current semantics. Changing the signature to `limit: int | None = None` would silently make the **page** unbounded — a response-semantics change the ruling does not ask for. The endpoint therefore gains a `Request` parameter (`Request` is already imported at `daemon/routers/jobs_crud.py:8`) and the limit is forwarded to the enrichment **only** when `"limit" in request.query_params`.

**Where the limit applies (plan-level choice):** at the **final assembly**, after the Task and Job branches merge and after the canonical `created_at DESC` ordering — a **post-sort slice**. A per-branch SQL LIMIT is rejected: it truncates each branch independently and changes the interleaving.

**Amendment (D-30):** the plan's cited sort anchor `work_resolver.py:1272-1276` is the **DOCSTRING**, not the slice site. The sort is at **`work_resolver.py:1520`** (`records.sort(key=lambda r: _normalize_sort_key(r.created_at), reverse=True)`). The slice must land **PRE-CONVERSION** — on the merged+sorted **source rows** — because a post-conversion slice happens *after* `_job_to_record`, by which point the per-row singles are already paid.

## D-9 — The prefs merge stays on-loop (disclosed residual)

**Disposition:** ADOPTED (ANALYSIS §2.1 recommendation, carried) — citation corrected by D-39.

`daemon/routers/instances.py:496` (`manager._instance_ui_prefs_repo.get_all(instance_ids)`) stays on-loop to minimize moved code, with the residual **disclosed**. The analysis names "the 1 residual on-loop rt"; BASELINE §2 shows **2** prefs statements in the flat shape — so the exact residual is pinned from the developer's first HEAD measurement, and the gate fence is **"instances-path frames on the loop thread = 0"**, not "total on-loop rts = 0". (`:495` is the comprehension; the call is at `:496` — see D-39.)

## D-10 — Phase shape: 5 phases, grouped by shared file context

**Disposition:** ADOPTED (PLANNER) → **AMENDED by D-40** (T-3 deferral changes the `jobs_crud.py` ownership row; the phase count stays 5).

Six site-groups ≠ six phases. Three files each carry two sites, and splitting them would put two developer instances in the same file:

| File | Sites | Phase |
|---|---|---|
| `daemon/routers/instances.py` | C-1 (`:476`), T-2 (`:346`, `:550`) | 2 |
| `daemon/services/work_resolver.py` | C-2(i) (`:1396-1397`), D-B (`:1779`/`:1847`/`:1878`), C-4(ii) limit param (`:1174`) | 3 |
| `daemon/routers/jobs_crud.py` | C-4(ii) (`:912`), T-3 (`:943`) | 3 |
| `daemon/routers/queues.py` + `daemon/routers/messages.py` | C-3 (`:663`), T-1 (`:204`) | 4 |

**[AMENDED by D-40: T-3 is DEFERRED (D-31) ⇒ `daemon/routers/jobs_crud.py` now carries ONE site (C-4(ii) at `:912`). The "two sites per file" rationale for that file is GONE and the plan says so rather than pretending it survives. The phase assignment is unchanged because P3 still owns `work_resolver.py` + `jobs_crud.py` + `work.py` as ONE resolver/enrichment work unit and the single-developer-owner constraint still binds.]**

P1 (bridge infrastructure) is first because it is the seam every later phase consumes and it is independently verifiable. P3 is the heaviest (4 files, 5 mechanisms) and is sized for **one** developer instance with a longer context budget.

## D-11 — T-3 is a separately-gated sub-task (the de-scope lever survives)

**Disposition:** ~~ADOPTED (ANALYSIS §1.2 names T-3 the first de-scope candidate)~~ → **SUPERSEDED by D-31: T-3 is DEFERRED outright** (not "gated"). T-1/T-2 are CARRIED.

**Reason for the reversal (architect §5.4):** T-3 satisfies **no positive criterion** (the criterion→site mapping is exhaustive: G1 is served by C-1 + T-2 only; G2 by C-1 + C-3 + C-4/D-B; G8 by the C-sites; G9 by C-1, C-2, C-3, C-4; G10 by C-4(ii)+α; G11/G12 lane-wide). Its measured cost is **≤2 rts per DEAD row**, and the harness fixture seeds only `done`/`active`/`queued` JobItems (`seed.py:279`/`:306`/`:335`) ⇒ **zero DEAD rows ⇒ zero measured value**. Worse, as planned it is **A applied to an ON-LOOP site** (`daemon/routers/jobs_crud.py:943` is a plain sync call inside `async def list_jobs` at `:730`), which contradicts the lane's own objective sentence.

**Re-entry rule:** if re-entered later it must be **A + C** (bridge the DLQ read as well as batching it) and the fixture must gain DEAD JobItems to make it measurable. The deferral is recorded as a **known remaining on-loop site**.

## D-12 — The tester-owned harness path is not edited by the developer

**Disposition:** ADOPTED (PLANNER) → **AMENDED and STRENGTHENED by D-37** (the harness HEAD-mode repair is a **scheduled, gated, tester-owned dependency**; the developer's functional gate is BLOCKED on it).

`.agents/tester/harness/eventloop-phase0/` is tester-owned. The developer's pins live under `tests/`. The harness's N4 formula expectations (BASE values: flat = 3+limit = 13; BFS = 4+levels+rows; defer-blocked = 2+holders; missions = 3) are updated **once** by the tester, from the developer's measured HEAD run, and the delta is recorded in the test report.

**Amendment (D-37):** the committed harness **requires the G1 pins to be POSITIVE** (`.agents/tester/harness/eventloop-phase0/main.py:337` `captures_collected >= 1`; `:423-427` `fam >= 1`; `:434-437` `narrow >= 1`) and its predicate is **thread-agnostic** (`:396-401` takes the FIRST thread section; `:410-415` computes `fam_any` over ALL sections and stores it under `frame_family_present_in_main_thread` at `:416`). ⇒ the gate cannot pass at HEAD without a **tester-lane** change to a **tester-owned** path. **D-12 stands unchanged in its substance: the developer never edits the harness.** The repair is scheduled in Phase 5 as a **gated dependency** with the BASE-still-red discipline (see D-37 and phase5 task 5.1).

> **DATED 2026-10-30 (fold round 3 / D-77, closing M-5): REPAIRED — the present-tense claims above described the harness as committed at `42764588c`/`d42ba2976`.** The HEAD-mode repair landed as the tester-identity commit **`c3d87af26`** (explicit `base`/`head` MODE switch; loop-thread-scoped §6.1 predicate with thread-agnostic counts demoted to `*_DIAGNOSTIC`; one-sided G2; per-shape G9; paired G4; the synthetic handler-load leg driving real concurrent HTTP; collection fences; `run_truth_table.sh` + `selftest.py`; tree sha `88009e7bfaba8c539d61afdb22de0767e0d92369`). **A tester increment #2 is IN FLIGHT** — the R1 council review REJECTED increment #1 with 3 blocking findings (G9 HEAD-mode **unit bug**: sample-summed `statements_by_shape` fed to per-request rules ⇒ a correct fix can never pass; `--reference` provenance unasserted; truth-table overclaim). Governance unchanged: D-12 substance stands, review closes BEFORE any HEAD run, BASE-still-red is non-negotiable.

## D-13 — Rollback-safety posture: schema-EMPTY ⇒ `rollback_safe=true, source=explicit`

**Disposition:** ADOPTED (ANALYSIS §7, lifted verbatim into plan-overview).

The lane ships **no** `daemon/migrations/versions/*.sql`; every mechanism is Python (a repo SELECT, an optional resolver limit, a skipped SELECT, one new module + one config constant, router-level await wrappers). Every touched table is read-only for this lane. Migration delta vs live v0.27.13: **EMPTY (115 == 115 projected)**.

> **CORRECTED 2026-10-30 (fold round 3 / D-76, closing M-4 and the G11 NOT-CLOSED line):** the live release is **`v0.27.12`** (`releases/current → v0.27.12`; `/livez` 0.27.12); **`v0.27.13` is staged-but-UNPROMOTED**. The operative adjudication sentence reads: *"Migration delta vs **LIVE `v0.27.12`**: EMPTY (115 == 115, **re-derived at stage**)."* The round-1 sentence above is preserved verbatim as unamended history — the same dated-correction discipline D-57 applied to the plan surfaces now applied to the adjudication surface itself.

**Plan-level additions:** (i) **no persistence of any kind** is introduced — no children-order table, no enrichment cache, no metrics table (in-process counter + log only); (ii) the bridge is **state-free** to revert — unwrap the call sites, delete one module; (iii) the env-overridable constant is additive (the old binary ignores an unknown var; the new binary's default matches today's behavior when unset).

**Standing rule applied:** delta-empty ⇒ `=1` legitimate → `rollback_safe=true, source=explicit` expected at stage, with the reasoning recorded in the stage journal and the tag annotation.

## D-14 — Version slot: `v0.27.14`, advisory only

**Disposition:** ADOPTED (PLANNER).

Advisory. **No reservation.** The giter lane re-derives the slot at stage time across all three axes (no `releases/` dir, no local tag, no remote tag) and must re-derive rather than assume — the sibling lane took `v0.27.12` after `v0.27.13` was cut, so a concurrent lane may take `v0.27.14` before this stage. The staged artifact is **daemon-only** (the lane touches no payload tree: agents/frontend/plugins/ unchanged); only the binary and the version fields differ.

## D-15 — The routed-out register is binding (18 entries → 19 after the fold)

**Disposition:** ADOPTED (ANALYSIS §1.2 + §2.1 + §4.4; PLANNER) → **AMENDED by D-31 (T-3 becomes entry 19)** and **D-32 (entry 16 is now a ROUTE-OUT, not a ruling request)**.

18 entries (now 19), listed with owner hints in plan-overview. Any re-entry re-opens the 3-cycle review budget and requires **dispatcher sign-off**. Entries carrying live obligations for this lane:

- **Entry 18 (defer-blocked dwell, R-1):** the in-source comment at `daemon/routers/queues.py:652-662` **must** be updated to say "moved off-loop by wi-3f5bea95, dwell still routed" (task 4.2). Leaving the old comment would let the routed owner close it as a non-issue.
- **Entry 16 (BFS-depth txn span, R-2):** now **ROUTED OUT** per D-32 (the architect's ruling text is carried verbatim).
- **Entry 19 (T-3 DLQ N+1, added by D-31):** DEFERRED — a known remaining on-loop site; re-entry requires A + C and DEAD rows in the fixture.

## D-16 — BFS-depth txn span: a ruling request, not a fix

**Disposition:** ~~FRAME ONLY — architect ruling requested~~ → **SUPERSEDED by D-32: ROUTED OUT to R-2** (the architect ruled).

**Verified shape:** one `with SQLModelSession(self.engine) as db_session:` at `daemon/repositories/instance/repository.py:1434` holds ONE session/transaction across the root count → root page query → per-level child-id queries → KB post-filter → `_enrich_instances` → return (`:1645`).

**Reachability in this lane:** C-1's bridge moves the WHOLE `list_instances` call — including this span — off-loop **without restructuring it**. The architect confirmed the plan's reachability claim (architect §4.1): the children loop is **outside** the span — `daemon/services/instance_lifecycle.py:5090` returns from `repo.list()` (span closed) and the loop at `:5113-5116` calls `list_child_ids_permanent`, which opens **its own** session at `daemon/repositories/instance/repository.py:432` and closes it at `:438`. ⇒ the new children batch runs in its own session **outside** the span, and the span's **duration and DB-side fingerprint are unchanged — only its thread changes**.

**If the architect rules "fix here":** the phase map needs one added phase and the loop budget is re-planned by the dispatcher. This is not a planner decision. **[The architect ruled: ROUTE OUT — see D-32.]**

## D-17 — What this lane does NOT do to the pool (sibling couplings carried as constraints)

**Disposition:** ADOPTED as constraints (sibling `decisions.md`: D-15, D-33, D-34, D-36, D-47, D-49, D-57, D-58, A7, A12, C4) → **AMENDED by D-23** (F4-A does not protect the bridge) and **D-22** (capacity 25, not 15) and **D-21** (6 workers).

| Coupling | Constraint on this lane |
|---|---|
| D-15 (`pool_timeout=30`) | A bridged request that finds the pool exhausted waits on an **executor thread** instead of blocking the loop — same outcome for that request, healthy loop for everyone else. The knob is untouched. |
| D-33/D-34 (F8 no-txn-across-await, three-phase shape) | Preserved trivially: the bridge callable is fully sync and the batches happen inside one sync function. A **static negative** pins that no bridged callable opens a scope it awaits across (task 1.11). |
| D-36/D-49 (three-pool attribution via per-pool conninfo) | Untouched — the bridge adds no pool; no `application_name` is added or moved. |
| D-47 (engine idle-in-txn backstop DISABLED + enablement gate) | Untouched. The lane inherits a clean pool and must not change its semantics. |
| D-57 (F4-A capacity 15+10, equality pin) | **[AMENDED by D-22: the manager engine's configured capacity is 25 — `pool_size` 15 (`daemon/constants.py:264` `MANAGER_POOL_SIZE_DEFAULT`) + `max_overflow` 10 (`daemon/repositories/factory.py:435`) — matching `daemon/services/readiness.py:165` `MANAGER_POOL_CONFIGURED_CAPACITY = 25` and the D-57 equality pin at `tests/postgres/test_dbpool_probe_starvation_pg.py:96`. The sibling's "5 + 10 = 15" is the STALE default tier.]** Untouched by this lane. Bridge concurrency (≤6) consumes ≤6 of 25 — the pool is **not** the binding constraint at any defensible bridge size; the 25-cap is a hard ceiling whose failure mode is a bounded `pool_timeout=30` wait, never a ceiling breach. |
| D-58 / R-9 (asyncpg adapter idle-in-txn hold — ROUTED) | Untouched — the lane adds no DSN and no driver. |
| A7 (default-executor ceiling, **1,400** sites at `b1183a637` — see D-39) | The lane's own sites must not use the default executor (D-7/D-21). The global question is a separate lane. |
| A12(i)/(ii) (dwell-multiplier naming; BFS routed) | This lane's contribution is exactly what A12(i) named — a dwell multiplier. The DWELL class remains unrepaired (`daemon/persistence.py:316-334` honesty comment stays true). |
| C4 (soak sizing 5+10 preserved) | No pool-sizing change; the manager engine stays 15+10. |

## D-18 — The G2 floor band's provenance is disclosed, not re-baselined

**Disposition:** ADOPTED with disclosure (PLANNER) → **AMENDED by D-36** (the band becomes ONE-SIDED; the lower anchor is re-purposed).

The `/api/instances` floor band (**0.067–0.23 s** proven floor; **0.51 s** = proven degraded) comes from the **commission body**, and is **not** re-derivable from the committed `BASELINE.md` — BASELINE §3 records the BASE loaded flat-leg p50 78 ms / **p95 914 ms** and the unloaded p95 67.8 ms (the 0.067 s anchor). The tester must confirm the 0.23 s upper anchor and the 0.51 s degraded marker against the sibling lane's composite record during the pre-gate, and any deviation is recorded as an **explicit test-plan amendment** — never a silent re-baseline.

**Amendment (D-36):** the architect showed the band as written **fails a lane that works** — the expected healthy HEAD loaded flat p95 is **0.02–0.10 s (point estimate ≈0.04 s)**, i.e. **BELOW** the band's lower anchor. The corrected fence is one-sided: `p95 ≤ 0.230 s → PASS`; `0.230 < p95 ≤ 0.510 → FAIL (degraded band)`; `p95 > 0.510 → FAIL (BASE-equivalent)`; **a measured p95 BELOW 0.067 s PASSES** and is recorded as an improvement.

---

# FOLD ROUND 1 — NEW DECISIONS (D-19 … D-41)

## D-19 — D-2 is DECIDED: Option 1 (bridge) + A-batches; async-native routed to D-47/D-58 — DEFERRED, UNFUNDED

**Authority:** ARCHITECT (§0, §1, §2.1, §2.6, §2.7) + LEADER R2. **Cross-map:** architect **D-2**; supersedes **D-2**.
**Disposition:** ADOPTED (binding).
**Evidence:** architect §1 ("Option 1 and Option 3 are mechanically identical for this lane's scope" — the hybrid's distinguishing content is only the *deferral claim*); §2.7 ("If §2.6's five artefacts are not produced, the honest ruling is **'Option 1 + the leader-ruled A-batches'**"); §2.1 trade-off matrix (adopted scope 4.00 vs Option 2 2.25, dominant axis Maintainability 25 %).

**Decision.**
1. **Mechanism set:** Option 1 (the off-loop bridge) + the leader-ruled A-batches. Async-native (Option 2) is **REJECTED for this lane** and routed to the **D-47/D-58** measurement campaign.
2. **D-2 is a POLICY about the future, not an architecture choice.** Option 2's deferral claim is half-true: mechanical at the router boundary (six sites), unsupported at the repository boundary (services reach past repositories into `.engine` — `work_resolver.py:2226`, `:2279`, `:2361`, `:2413`; `defer_block_resolver.py:274`, `:306`; `mission_resolver.py:691`), and it breaks the sync tool layer that shares the same three methods (`daemon/tools/instance.py:1547`/`:1632`/`:4408`/`:4626`/`:5191`/`:5636`/`:6188`; `daemon/tools/instance_read.py:1673`; `daemon/tools/tunables.py:178`) — creating the two-repo-truth drift class **by construction**.
3. **The deferral contract (architect §2.6's five named artefacts — production registry `db_bridge.BRIDGED_CALLABLES`, seam inventory + A-batch carry-over list, the invariants list, a parity harness, a static registry-coverage pin) is NOT FUNDED in this lane.** Per LEADER R2 this is recorded honestly as **DEFERRED, UNFUNDED** — **no fake promise and no unfunded commitment carried as if scheduled.** The honest label is therefore **"Option 1 + A-batches"**, not "hybrid".
4. **Explicit re-open trigger (the honest form of "we deferred it"):** the **`DB_OFFLOAD_QUEUE_WARN_DEPTH` alarm firing in production** — the bridge's own bounded pool becoming the bottleneck — **plus** the D-47/D-58/R-9 measurement campaign recording the manager engine's legitimate hold on the real fetch path (`daemon/constants.py:305-330`).
5. **Cost of the deferral (what keeps being deferred and how long it stays safe):** the D-47 engine-backstop enablement; the D-58/R-9 asyncpg DSN with `idle_in_transaction_session_timeout`; and the **R-1/R-2 DWELL class** — which *neither* option repairs and which Option 2 would structurally harden. The deferral stays safe while the bridge+batch shape keeps the loop free and no new sync-DB-on-loop site is added; it is **not indefinitely safe**.

**Cross-refs to fold:** architect §0 table row D-2; §1; §2.1; §2.6 items 1–5 (recorded as **unfunded**); §2.7; §9 open question 2.
**Fallback if the leader rejects the "unfunded" framing:** fall back to "Option 1 + A-batches" **with** the §2.6 artefacts funded — i.e. the label changes back to "hybrid" and phase 1 grows the registry + registry-coverage pin (architect §10 Q2 recommendation: fund at least items 1 and 5, which cost one module and one test file). The **work is identical either way**; only the label and the funded set differ.

## D-20 — The bridge module is RENAMED: `db_bridge.py` → `db_offload.py`; `run_db` → `offload`

**Authority:** ARCHITECT (A8 / B8). **Cross-map:** architect **A8**; amends D-7's module/API naming.
**Disposition:** ADOPTED (binding) — **before the Phase-1 API freeze**.
**Evidence:** architect B8 — "Bridge" is **already taken twice** in `daemon/services/` with different meanings: `main_loop_bridge.py:1-2` ("bridge **toward** the event loop"; this bridges **away** from it) and `tmp_image_bridge.py:1-3` (a path→data-URI transform). The `db` prefix also already denotes a **different subsystem** (`daemon/services/db_pool_manager.py` = asyncpg pools for *external* `db_connections`).

**Decision:** the new module is `daemon/services/db_offload.py`; the public seam is `await offload(fn, *args, **kwargs)`. The rename happens **before** the Phase-1 API freeze — after the freeze, the cost is the frozen-signature evidence record plus P2/P3/P4 edits.
**Fallback if the leader prefers the original name:** the name is cosmetic; nothing else in this log changes. (Recorded so a reviewer does not treat the rename as load-bearing.)

## D-21 — Bridge executor sizing = **6 workers** (the binding number)

**Authority:** ARCHITECT (A2 / §2.5) + LEADER R8. **Cross-map:** architect **A2**; **supersedes D-7's sizing of 4**.
**Disposition:** ADOPTED (binding).
**Evidence (architect §2.5, three derivations):**
- **Way A (the plan's own):** "the four bridged endpoints firing concurrently" — but the **coincidence set is already 4 at HEAD**: badge tick bridged legs {instances_flat, defer_blocked} = 2, home tick {instances BFS} = 1 (coincide at lcm(8,10) = 40 s), plus `/api/work` = 1 ⇒ **4 simultaneous**, with the FE's own agent traffic (T-1/T-2, unmeasured) on top. **4 = zero margin.**
- **Way B (Little's Law):** L ≈ **0.19** — the **wrong statistic** for a forkJoin **burst** (a time-average imposes no upward constraint; 0.19 is 21× below the peak).
- **Per-shape HEAD bridge occupancy:** flat ≤27 ms, BFS ~28 ms, `/api/work` ~26–43 ms, **defer-blocked 729 ms unchanged** — defer-blocked is 25–30× every other shape and sizes the pool almost entirely by itself.

**Decision:** `DB_OFFLOAD_MAX_WORKERS = 6` = measured burst 4 + 2 agent fan-out. **The plan's "may drop to 3 for margin" is REJECTED — 3 is below the already-reachable burst of 4 ⇒ negative margin.** Margin must be added, not subtracted.
**Fallback if the leader rejects 6:** 6 is the binding number; a lower value must come with a new measurement of the coincidence set, not with the word "margin". If the leader sets a different number, it lands in `daemon/constants.py` + `LimitsConfig` (D-26/B10) and the G2 diagnostic rule ("if HEAD p95 > 0.12 s, raise the pool **before** investigating anything else") is re-derived with it.

## D-22 — The manager-engine capacity is **25**, not 15 — and `max_overflow` is not a worker budget

**Authority:** ARCHITECT (A3 / §2.5). **Cross-map:** architect **A3**; **supersedes** the plan's "capacity 15 = pool 5 + overflow 10" and the "4 ≤ overflow headroom" claim (D-7, D-17).
**Disposition:** ADOPTED (binding).
**Evidence:** architect §2.5 corrects the plan **twice**, both in the lane's favour:
1. **Wrong construction site.** The plan cited `daemon/repositories/factory.py:143-144`/`:155-156`/`:435`; those lines are the **`DatabaseConfig` dataclass defaults**, consumed only by `create_engine_from_config` (`factory.py:225-226`) — the SQLite/generic path. The **manager engine** is built at **`factory.py:434-435`** with the config-driven `manager_pool_size` (default **15**, `daemon/constants.py:264` `MANAGER_POOL_SIZE_DEFAULT`) + `max_overflow=10` ⇒ **capacity 25**, matching the house's own `daemon/services/readiness.py:165` `MANAGER_POOL_CONFIGURED_CAPACITY = 25` and the D-57 equality pin at `tests/postgres/test_dbpool_probe_starvation_pg.py:96`. **The plan copied the sibling's stale text.**
2. **Category error in the phrasing.** `max_overflow` is a slot count *above* `pool_size`, not a budget that bridge workers "fit into". A bridge worker holds **at most one** manager-engine connection ⇒ B workers consume **≤ B of 25**. Worst case at B=6: bridge 6 + default-executor jobs enrichment ≤5 + readiness probe 1 + residual on-loop ~2 = **~14 of 25 (56 %)**.

**Decision:** capacity is **25**; the plan's "4 ≤ overflow headroom" line is deleted; the pool is **not** the binding constraint at any defensible bridge size; the 25-cap is a hard ceiling whose failure mode is a bounded `pool_timeout=30` wait, never a ceiling breach.
**Fallback if the leader rejects 25:** 25 is the house's own configured capacity (`readiness.py:165`) and the D-57 pin's number; a different figure would require re-deriving the D-57 pin, which is sibling-owned — so the leader would be changing a sibling's contract, not this lane's.

## D-23 — F4-A does **NOT** protect the bridge (the plan's reliance is unfounded)

**Authority:** ARCHITECT (A4 / §2.5 / §5.5). **Cross-map:** architect **A4**; **amends D-17** and plan-overview risk 4 + TEST/PIN PLAN §(f).
**Disposition:** ADOPTED (binding).
**Evidence:** `_refuse_pool_admission_at_capacity` (`daemon/services/readiness.py:1541`) is called from exactly **two** sites, **both inside `_probe()` closures** — `make_db_probe` (`:1627`) and `make_queue_probe` (`:1929`) — and its own docstring calls it probe-scoped. **It never sees a bridged request.** The bridge's pool protection is the pool's own 25-cap plus `pool_timeout=30` (D-15), **not** F4-A.

**Decision:** the plan **stops claiming** F4-A protects the bridge. The bridge's posture is argued from the 25-cap + `pool_timeout=30`. `daemon/services/readiness.py` remains **DO-NOT-TOUCH** (D-17, G12) — the lane still must not touch it, but it must not *claim* its protection either.
**Fallback if the leader rejects this correction:** the architect's evidence is a two-site call-graph; a rejection would have to name a third site.

## D-24 — The shutdown premise was FALSE; the bridge gets a bounded drain seam

**Authority:** ARCHITECT (A5 / B3) + LEADER R6. **Cross-map:** architect **A5**; **supersedes D-7's shutdown bullet** and plan-overview's phase1 task 1.3(b).
**Disposition:** ADOPTED (binding).
**Evidence (LEADER R6, re-verified in-tree):** `daemon/services/upgrade_journal_sweep.py:332-334` **DOES** call `executor.shutdown(wait=False, cancel_futures=True)` on its dedicated waitpid executor. The plan's claim that "no house precedent shuts one down" is **false**.

**Decision.**
1. **Accurate restatement:** no **module-global** dedicated executor is shut down (the sweep's executor is an *instance* attribute, `self._waitpid_executor`). The plan's claim is corrected, not merely softened.
2. **Drain seam (B3):** `daemon/manager.py:15223` `("dispose_db_pools", …)` runs during shutdown, reached via `daemon/api.py:3390` (`yield`) → `:3809` `await manager.shutdown()`. An in-flight bridged read would hit a **disposed pool inside a dying request**. ⇒ add **`await db_offload.shutdown()`** (bounded drain) to the lifespan **before** `await manager.shutdown()` (`daemon/api.py:3809`), ahead of `("dispose_db_pools", …)`.
**Fallback if the leader rejects the lifespan change:** the shutdown-race is real but low-frequency; without the drain, the lane must **disclose** it as a known residual (a `Engine is disposed` inside a dying request) in the same place as the other residuals — it may not simply be dropped.

## D-25 — The seam spec accepts **kwargs**

**Authority:** ARCHITECT (A6 / B5) + LEADER R7. **Cross-map:** architect **A6**; **supersedes `technical-analysis.md:81`**'s `run_db(fn, *args)` / `partial(fn, *args)` form.
**Disposition:** ADOPTED (binding).
**Evidence:** `daemon/routers/work.py:181-187` calls `resolver.list_work(project_id=…, instance_id=…, status=…, kind=…, root_only=…)` — **keyword-only**. Implementing the seam from the analysis's positional-only form **breaks `/api/work` with a `TypeError`.** (Verified in-tree: `daemon/routers/work.py:181` `records = resolver.list_work(` with six keyword arguments.)

**Decision:** the seam is `offload(fn, *args, **kwargs)`.
**Fallback if the leader rejects kwargs:** none available — the keyword-only call site is in-tree and non-negotiable.

## D-26 — The bridge's MANDATORY amendments B1, B2, B4, B6, B7, B9, B10 are adopted in full

**Authority:** ARCHITECT (A7 / §2.4). **Cross-map:** architect **A7** + **A10** (constant placement); adds to D-7/D-20/D-24.
**Disposition:** ADOPTED (binding) — each becomes a Phase-1 task **with a pin**.
**Evidence:** architect §2.4 — "These are **not optional**. Each is a probe-confirmed or in-tree-verified hazard, and each becomes a pin."

| # | Amendment | Substance (folded verbatim into phase1 tasks) |
|---|---|---|
| **B1** | Re-entrancy guard | A bridged callee that itself calls `offload` deadlocks **iff O ≥ W** (progress needs W − O ≥ 1 free worker). Probe-confirmed at W=4: O=2 ✓, O=3 ✓, **O=4 → permanent deadlock** — and D-7's own sizing rationale was "the four bridged endpoints firing concurrently", so the deadlock sits exactly at the designed concurrency. Enforce with a **runtime guard that raises a typed error (never hangs)** + a pin that drives the nested case. |
| **B2** | BaseException containment | A `KeyboardInterrupt`/`SystemExit` raised inside a bridged callable **escapes the event loop entirely** (CPython `Task.__step` re-raises them) ⇒ process death + systemd `Restart=on-failure` crash-loop (`scripts/systemd/ensemble-daemon.service:108`), **not** a 500. Contain and pin. (A `CancelledError` raised in a sync callee instead cancels the awaiting task — it looks like a client disconnect.) |
| **B4** | Admission gate — defer, never reject, never time out | `ThreadPoolExecutor.submit` never blocks and never rejects ⇒ the queue is unbounded. Use an **`asyncio.Semaphore` acquired with `await`** (a blocking acquire would re-block the loop). **A per-call timeout is the trap:** `run_in_executor` future cancellation does **not** stop the running thread, so on timeout the DB work continues to completion, still holding its connection and still consuming the GIL while the endpoint must return something — a byte-different error (violates byte-identity) or a late reply to a gone client. **A timeout amplifies the load it is meant to shed.** |
| **B6** | No executor parameter | Keep the executor module-private and expose only `offload(fn, *args, **kwargs)`. "A caller picked the wrong executor" becomes **unrepresentable**, not merely pin-detected. |
| **B7** | `thread_name_prefix="db-offload"` | Makes bridge threads distinguishable in the phase-0 `sys._current_frames` census (`BASELINE.md` §5) and gives the executor-identity pin real discriminating power — the proposed `is not loop._default_executor` assertion is **near-tautological**. Three house pools already set a prefix (`stdio_wrapper.py:130` `"mcp-procscan"`, `warmup_pool.py:116` `"mcp-secret"`, `question_pause_set.py:137` `"qpause-merge"`). |
| **B9** | TypeVar, not `-> Any` | Keeps `manager.list_instances`'s `tuple[list[dict], int, bool]` and `get_instance_info`'s `dict` typed at the router; `-> Any` drops the checking on `InstanceInfo(...)` construction (`daemon/routers/instances.py:348-360`) for free. |
| **B10** | Constant placement | Pool-sizing knobs live in `daemon/config.py`'s `LimitsConfig` (`env_prefix="LIMITS_"`; the `checkpoint_pool_max_size` precedent), with the default tier in `daemon/constants.py`. `daemon/constants.py:1-11` states the module imports **nothing**; its env helpers do a lazy `import os` inside the function (`:423-436`). ⇒ `DB_OFFLOAD_MAX_WORKERS_DEFAULT` in constants + `limits.db_offload_max_workers` in `LimitsConfig` (env `LIMITS_DB_OFFLOAD_MAX_WORKERS`) — **not** a bespoke env read in constants. |

**Fallback if the leader rejects any single item:** B2 (BaseException containment) and B4 (no timeout) are the two whose absence produces a *daemon crash* or a *byte-identity violation*; if either is rejected, the lane must disclose the hazard explicitly in the CHANGELOG and the phase evidence.

## D-27 — The executor-identity pin asserts the **thread-name prefix**, not `is not loop._default_executor`

**Authority:** ARCHITECT (A9 / B7). **Cross-map:** architect **A9**; **supersedes** plan-overview risk 18's mitigation and phase1 task 1.4's wording.
**Disposition:** ADOPTED (binding).
**Evidence:** architect A9 — "`is not loop._default_executor` is **near-tautological** (it can only fail on a literal `run_in_executor(None, …)`)". B7 supplies the discriminating alternative: the prefix.

**Decision:** the pin asserts the executing thread's name starts with `db-offload` (and that the executor is the module's dedicated pool). **The tautological assertion is retained only as a secondary check**, clearly labelled as non-discriminating.
**Fallback if the leader rejects the prefix assertion:** the pin then has no discriminating power against a silent default-executor fallback — the census in `BASELINE.md` §5 would also lose the ability to tell bridge threads from default-executor threads. Rejection therefore costs a stated piece of evidence.

## D-28 — D-B covers **THREE** lookup sites and must be **degradation-aware**

**Authority:** ARCHITECT (A11 / §3.1 / §3.2 / §3.4). **Cross-map:** architect **D-4 / D-B**; **amends D-4**.
**Disposition:** ADOPTED — D-B is **REQUIRED, not merely beneficial**.
**Evidence:** architect §3.1 — the plan cites the wrong site:

| Site | Guard | Fires for a fixture orphan? |
|---|---|---|
| `work_resolver.py:1778-1779` | `admission_state == "active"` | **No** — all 1,250 orphans are `admission_state='done'` (`seed.py:306`) |
| `work_resolver.py:1846-1847` | `job_type == "message"` | ~50 % |
| **`work_resolver.py:1878`** | **unguarded** (mission-fields fallback) | **100 % — the dominant site** |

**Implementable statement (architect §3.4, folded into phase3 task 3.2):**
1. `_batch_instances` (**sole production caller** `work_resolver.py:1424`; zero test callers) returns `(rows_by_id, degraded: bool)`; `degraded=True` on the `SQLAlchemyError` path (`:2416-2431`).
2. `list_work` computes `absent_ids = (instance_ids - rows_by_id.keys())` **only when `not degraded`**, else `set()`.
3. `_job_to_record` gains keyword-only `instance_absent: bool = False`; `:1779`, `:1847`, `:1878` become `if instance is None and not instance_absent:`.
4. `list_work` passes `instance_absent=(j.instance_id in absent_ids)` on the batch path only; `resolve_work` (`:1102`/`:1110`/`:1119`) keeps the default `False`.
5. **Rejected mechanisms:** a separate renderer (drift); a sentinel object (every `is not None` branch flips → `instance.status` raises); a thread-local absence memo (hidden state on a shared singleton).
6. **Pins:** (i) an orphan row costs **0** `SELECT instances` on the list path; (ii) the orphan record is field-for-field identical to BASE; (iii) the **degraded** batch still performs the per-row fallback — the existing W-1 pin (`tests/unit/services/test_fix_c_read_model_split.py:758-820`, esp. `:815-820`) stays green **and** a new node asserts the fallback *did* run.

**The one divergence (architect §3.2):** the absence set MUST be empty when the batch degraded. `_batch_instances` returns `{}` on `SQLAlchemyError` (`:2416-2431`) and its own comment says "the caller treats every requested instance as 'unknown'". If the absence set were computed as `requested − found`, a degraded batch would mark **every** instance absent and rows whose instance **does** exist would render instance-less — a real byte change. ⇒ **a degraded batch must NOT masquerade as "instance absent".**
**Fallback if the leader rejects the three-site scope:** the `:1878` site is the only one that fires for the fixture's orphans, so rejecting the other two does not move the measured number — but the three-site closure is what makes the mechanism *complete* rather than fixture-shaped.

## D-29 — D-C α's patch-up must be **BATCHED** (the per-row form is HARMFUL)

**Authority:** ARCHITECT (A12 / §3.5). **Cross-map:** architect **A12**; **supersedes D-5's α form**.
**Disposition:** ADOPTED (binding).
**Evidence:** architect §3.5 — the plan's per-row `resolve_work` patch-up is measured at **8/10** (badge N=10), **76/100** (badge N=100), **34/100** (page N=100) patch-ups — **bounded by page size, not by "ordering-tie boundary misses" as D-5 claims** — and it **misses G9 on BOTH jobs shapes** (the architect's table: limit + per-row patch-up with D-B ⇒ ~49 rts at N=10 (✗ against ~15–30) and ~179 rts at N=100 (✗)). Drivers: the **`root_only` divergence** (the page does not apply it; the enrichment does) + the **status-predicate divergence** (`_canonical_to_job_filters` at `work_resolver.py:646-689` requires `terminal_reason='completed'` and never matches `'settled'`) + different ordering (`created_at DESC, priority DESC` at `daemon/repositories/job_queue/repository.py:1487-1490` vs `created_at DESC` at `work_resolver.py:2320`).

**Decision:** the patch-up is **one job_ids IN-select + one instance batch + one mission batch** — not per-row `resolve_work`.
**Fallback if the leader rejects the batched form:** the per-row form **fails G9 on both jobs shapes** (measured), so rejection re-opens G9 rather than closing it; the lane would then have to re-scope G9 with numbers, which D-41 already flags as D-B-dependent.

## D-30 — D-8's slice must land **PRE-CONVERSION**, on the merged+sorted **source rows**

**Authority:** ARCHITECT (A13 / §3.5). **Cross-map:** architect **A13**; **amends D-8**.
**Disposition:** ADOPTED (binding).
**Evidence:** architect §3.5 — a post-conversion slice happens **after** `_job_to_record`, so **the singles are already paid**. The plan's anchor `work_resolver.py:1272-1276` is the **DOCSTRING**; the sort is at **`work_resolver.py:1520`** (`records.sort(key=lambda r: _normalize_sort_key(r.created_at), reverse=True)`) — verified in-tree.

**Decision:** the limit slice is applied to the **merged + sorted source rows**, upstream of the per-row record conversion.
**Fallback if the leader rejects it:** the slice-after-conversion form cannot reduce the round-trips (the singles precede it) — it would satisfy only the population bound, not the statement-count bound.

## D-31 — SCOPE: T-3 is **DEFERRED**; T-1 and T-2 are **CARRIED**

**Authority:** ARCHITECT (A14 / §5.4) + LEADER R3. **Cross-map:** architect **A14**; **supersedes D-11**; **amends D-10** (via D-40).
**Disposition:** ADOPTED (binding).
**Evidence (architect §5.4):** the criterion→site mapping is **exhaustive** — G1 is served by C-1 + T-2 only; G2 by C-1 + C-3 + C-4/D-B; G8 by the C-sites; G9 by C-1, C-2, C-3, C-4; G10 by C-4(ii)+α; G11/G12 lane-wide. **T-1, T-2 and T-3 satisfy no positive criterion** (T-2 contributes a *risk reduction* to G1 — an unbridged detail path is a genuine on-loop instance-repo read).

| Group | Measured (BASE) | Criterion value | Ruling |
|---|---|---|---|
| **T-1** | 2 rts/send (`messages.py:204` → `instance_lifecycle.py:5143`); **no harness leg** | none directly; **shares T-2's bridged callable** | **KEEP** — one line on a seam that already exists after T-2. Its "highest-frequency internal path" claim is an **estimate** (`technical-analysis.md:42`/`:46`, Unverified) — but every message send performs a DB read on the loop, so leaving it unbridged contradicts the lane's own objective sentence. |
| **T-2** | ~2–4 ms per panel refetch (estimate); no harness leg | G1 risk-reduction | **KEEP** — same file, same phase, same bridge seam as C-1; near-zero marginal cost. |
| **T-3** | ≤2 rts per DEAD row; **fixture has 0 `dead` rows** (`seed.py:279`/`:306`/`:335` seed only `done`/`active`/`queued`) ⇒ **0 measured** | none | **DEFER — with disclosure.** T-3 as planned is **A applied to an ON-LOOP site** (`jobs_crud.py:943` is a plain sync call inside `async def list_jobs` at `:730`), contradicting the lane's objective, and it is invisible to every gate. |

**LEADER R3 (R3 — T-1/T-2 CARRY, T-3 DEFER):** the leader follows the architect's same-seam argument. **The divergence from the criterion mapping is recorded EXPLICITLY here:** T-1/T-2 satisfy **no positive criterion** and are carried on the **objective-sentence + same-seam** argument, not on criterion coverage. The architect's own framing (§10 Q3) offers the leader's definition-of-done as the tie-breaker: *"If the leader's definition of done is strictly G1..G12 and the objective sentence (`plan-overview.md:21`) is subordinate, defer both."* **The leader has ruled: carry.** The divergence is therefore a **recorded, accepted divergence**, not an oversight.
**Re-entry rule for T-3:** if re-entered it must be **A + C**, and the fixture must gain DEAD JobItems to make it measurable; it is recorded as a **known remaining on-loop site**.
**Fallback if the leader reverses:** deferring T-1/T-2 as well costs two one-line wraps that are already on a seam the lane creates; the recorded cost is the send path and the detail path remaining on-loop.

## D-32 — D-16 is ROUTED OUT to R-2, with the architect's routing memo carried verbatim

**Authority:** ARCHITECT (§4.1 / §4.2 / §4.3). **Cross-map:** architect **D-16**; **supersedes D-16**.
**Disposition:** ADOPTED (binding) — ROUTE OUT.
**Evidence (architect §4.1, re-verified):** the span is real (`daemon/repositories/instance/repository.py:1434` opens `with SQLModelSession(self.engine) as db_session:` and holds it across the root COUNT `:1447-1460`, the root page query `:1472`, the BFS level loop `:1548-1620` (one `SELECT … WHERE parent_id IN (...)` per level at `:1567`, up to `_MAX_TRAVERSAL_DEPTH = 256` at `:50`), the KB post-filter `:1640`, and `_enrich_instances` `:1645`); the warning semantics live inside it (`truncated` set `:1617` when `MAX_DESCENDANTS_PER_PAGE = 1000` fires at `:1596`, returned at `:1645`; the rate-limited WARN/DEBUG `:1600-1616`; `hit_depth_limit` `:1625`; the depth WARN `:1628-1633`). The span has **NO declared residence budget** — the Δ3 declaration table (`tests/integration/test_db_pool_idle_in_transaction_guard.py:20-24`) budgets only the guard's own cycle (`DBPOOL_IDLE_IN_TXN_RESIDENCE_BUDGET_S = 0.05`, `daemon/constants.py:383`) and the deferred-report reconcile P2 (`MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S = 0.25`, `daemon/constants.py:394`). ⇒ the BFS span is currently judged against the 0.05 s default and is **uncovered**.

**Routing memo (architect §4.3 — carried verbatim into plan-overview's routed-out register and phase5's disclosure register):**

> **R-2 — BFS-depth transaction span (`daemon/repositories/instance/repository.py:1434-1645`) — ROUTED OUT of `wi-3f5bea95`.** This lane relocates the span off the event loop (bridge, C-1) and removes the children N+1 that surrounds it; it does **not** restructure the span, does not change its statement sequence, its `_MAX_TRAVERSAL_DEPTH` / `MAX_DESCENDANTS_PER_PAGE` warning assembly (`:1596-1633`), or its `xact_age_s` fingerprint. The DWELL class remains unrepaired and is already disclosed as open in `daemon/constants.py:344-368` and `daemon/persistence.py:320-333`. **Owner:** the DB-pool / R-2 lane (sibling `fix/db-pool-idle-txn` family; entry A12(ii) of the 18-entry routed-out register). **Seam the future commission picks up at:** `SQLModelInstanceRepository.list` — the `include_descendants=True` branch at `repository.py:1434` — whose replacement must (a) open/close a session **per level** (or use a recursive CTE) so no transaction spans the traversal, (b) preserve the `truncated` boolean and the depth-cap WARN semantics (`:1596-1633`) **byte-for-byte**, and (c) declare its per-site residence budget in the Δ3 table (`tests/integration/test_db_pool_idle_in_transaction_guard.py:20-24`), since the BFS span currently has **no** declared budget and is therefore judged against the 0.05 s default.

**Why the architect routed it (not fixed here):** dwell risk is **pool-independent of this lane** (the bridge relocates it; `xact_age_s` is a PG-side backend property, unchanged by the client thread); blast radius is **the highest available** (a response-assembling repository method whose `truncated`/depth-warning semantics are assembled inside it and returned by it — a per-level-session or recursive-CTE rewrite changes the statement sequence, the isolation windows, and the warning assembly in one edit); duration under the new shape is **shorter** (the children N+1 leaves the span; post-C-1 the span holds ~5 statements instead of 248); and the sibling explicitly scoped it out (`db-pool-idle-txn` A12(ii); entry 16 of the 18-entry routed-out register).
**Fallback if the leader rejects the route-out:** the phase map needs one added phase and the loop budget is re-planned by the dispatcher (recorded in D-16's fallback, now carried here).

## D-33 — `include_descendants`: KEEP + NAME as a pair + make the two internal call sites explicit

**Authority:** ARCHITECT (§5.1) + LEADER R5. **Cross-map:** architect **A12-adjacent §5.1**; **amends D-3**.
**Disposition:** ADOPTED (binding) — option **(c)+**.
**Evidence (architect §5.1, re-verified in-tree):** router default `True` (`daemon/routers/instances.py:387`), docstring `:390` "back-compat for every existing consumer"; manager facade default `False` (`daemon/manager.py:14735` — verified: `include_descendants: bool = False` in the facade signature at `:14735`); lifecycle default `False` (`daemon/services/instance_lifecycle.py:5027` — verified). The agent tool at `daemon/tools/instance.py:6165-6171` calls `manager.list_instances(limit=DEFAULT_PAGE_LIMIT)` **implicitly** (its own comment at `:6167-6169` says "this tool uses the default (false)"); `daemon/tools/instance_read.py:1673` likewise (verified: `rows, total, _repo_truncated = manager.list_instances(` — the call spans `:1673-1675`); the router call at `daemon/routers/instances.py:476-485` is **explicit**.

**Decision (the honest form of "one constant"):**
- **Keep both defaults** — express them as a **named pair**: `INSTANCE_LIST_HTTP_INCLUDE_DESCENDANTS_DEFAULT = True` / `INSTANCE_LIST_INTERNAL_INCLUDE_DESCENDANTS_DEFAULT = False`.
- **Make the two internal production call sites explicit** — `daemon/tools/instance.py:6170` (RESTORED at iteration 003 @ `3e976696b`: the `manager.list_instances(...)` call IS at `:6170`; `:6168-6169` is a comment block — the fold-4 `:6168` correction is REVERSED) and `daemon/tools/instance_read.py:1673` pass `include_descendants` explicitly; **both sites keep `False`**.
- **Write the docstring** (architect §5.1 text, folded into phase2 task 2.6).
- **Zero byte impact, zero signature change, no default flipped.**
- **Rejected:** (a) router→False (response-semantics change for any consumer that omits the param → routed to the FE-shapes follow-up contingent on an external-consumer audit) and (b) manager→True (widens the expensive path for every internal caller). Removing the manager default entirely (making it required) is **out of scope** for a stage-only latency lane (a public-surface signature change; an unknown external consumer would `TypeError`).
**Fallback if the leader rejects the named pair:** the pair is a naming device with zero behavioral effect; rejecting it leaves D-3's (c) keep+document in force and the two internal call sites still made explicit (the part with the actual effect: it makes the manager default non-load-bearing).

## D-34 — The children-order byte-diff gate is ACCEPTED with **FIVE** corrections

**Authority:** ARCHITECT (§5.2). **Cross-map:** architect **§5.2**; **amends D-6**.
**Disposition:** ADOPTED (binding).
**Evidence (architect §5.2, re-verified):** `daemon/repositories/instance/repository.py:432-437` is `select(Instance.instance_id).where(Instance.parent_id == instance_id)` with **no ORDER BY**; `parent_id` is indexed (`models.py:61`) ⇒ today's order is heap order, **unspecified by contract**.

**The five corrections (folded into phase2 task 2.7):**
1. **MULTISET** equality (`sorted(BASE_list) == sorted(HEAD_list)`) — a set pin passes a duplicated child id.
2. **Per-parent AND global** comparison — per-parent alone is blind to a child reassigned to an **off-page** parent; global alone is blind to a re-grouping that preserves the global id set.
3. **A static SQL `ORDER BY` pin** — a byte gate cannot see an ORDER BY implemented in Python instead of SQL.
4. **The batch must cover ALL returned rows, not just the roots** — the BFS shape returns **241** rows (100 roots + 141 descendants), each with its own `children` array; batching only the root ids would empty 141 children arrays.
5. **The DETAIL path must share the ordered source** (`daemon/services/instance_lifecycle.py:5143` uses the same unordered query) — otherwise the list and detail surfaces disagree about the same parent's children.

**Plus:** `/api/instances/{id}`'s `children` must be added to the compared set.
**What the gate still masks (stated in the plan, per architect §5.2):** it cannot prove the new order is the *product* order (a pinned constant does that), cannot see a Python-side sort (static pin), and covers only the shapes the harness requests.
**Fallback if the leader rejects a correction:** each correction closes a distinct blind spot (1 = duplicate ids; 2 = re-grouping; 3 = Python-side sort; 4 = the 141 descendants; 5 = list-vs-detail disagreement); rejection leaves the named blind spot open and it must then be disclosed in the gate's own limitations list.

## D-35 — The transaction-boundary rule TB-1 … TB-7 is adopted IN FULL

**Authority:** ARCHITECT (§5.3). **Cross-map:** architect **§5.3**.
**Disposition:** ADOPTED (binding). A callable is **bridge-eligible iff all hold** — a developer applies this to a candidate without asking anyone.

- **TB-1 — atomic open/close.** A repository method that opens `with Session(engine)` must open **and close** it entirely inside the bridged callable.
- **TB-2 — no session across an `await`** (sibling F8, D-33/D-34).
- **TB-3 — no session across the bridge boundary.** The session is created **and** closed inside the bridged callable; the bridge takes a **callable**, never a session.
- **TB-4 — the callee must not await**, and must not return an awaitable.
- **TB-5 — no callback into the loop** (`asyncio.run_coroutine_threadsafe`, `loop.call_soon_threadsafe` to a coroutine) — it would re-block the loop the work was moved off. *(**AMENDED 2026-10-30, fold round 4 / D-80** — verbatim: "Coroutine-scheduling callbacks into the loop remain FORBIDDEN (`run_coroutine_threadsafe`, `call_soon_threadsafe` targeting a coroutine). Thread-safe FUTURE RESOLUTION via `loop.call_soon_threadsafe` on a parked `asyncio.Future` is the SANCTIONED wake primitive and the only one.")*
- **TB-6 — no `WriteGuardSession` participant** (`daemon/write_pause_guard.py:277`; the manager's write scope at `daemon/manager.py:2785-2799`). The guard's enter/exit accounting is process-global and the session's lifetime would be split across threads.
- **TB-7 — read-only.** A bridged callable must not perform a **write**; the whole lane is read-only.

**Verification already performed by the architect on three callees (folded as the phase-1 eligibility evidence):** C-1 `manager.list_instances` → `instance_lifecycle.py:5021` → `repo.list()` opens `repository.py:1434`, closes at the end of that block (after `:1645`); then `list_child_ids_permanent` opens `:432`, closes `:438`. ✔ C-2 `manager.get_instance_info` → `instance_lifecycle.py:5119` → `repo.get()` opens `:621`, closes `:623`; then `:432`→`:438`. ✔ C-3 `resolver.resolve()` (`defer_block_resolver.py:278`): `engine.connect()` opens `:306`, closes `:340` — one connection pinned across the WS2 classification loop (`:336`). Under the bridge the connection still opens and closes **inside** the callable ✔ TB-3, but the **dwell duration is unchanged** — the in-source comment at `daemon/routers/queues.py:651-662` must be updated to "moved off-loop by wi-3f5bea95, dwell still routed" (phase4 task 4.2).
**Fallback if the leader rejects a rule:** TB-2/TB-3/TB-6 are the sibling's F8 invariants (D-33/D-34) and are inherited, not this lane's to relax; TB-7 (read-only) is what makes the lane schema-EMPTY (D-13) — relaxing it re-opens the rollback adjudication.

## D-36 — The blocking gate defects G1, G2, G9 and G4 are corrected

**Authority:** ARCHITECT (§6). **Cross-map:** architect **§6.1–§6.4**.
**Disposition:** ADOPTED (binding). **These are BLOCKING: the lane cannot pass its own gate as the harness is committed today.**

> **DATED 2026-10-30 (fold round 3 / D-77): the four §6 fences are IMPLEMENTED at `c3d87af26`** — the "is committed today" clause described `42764588c`. **The block now lives in the R1 REVIEW + the tester increment #2 (unit normalization at the gate boundary), not in the instrument's absence.** The fence texts below remain binding **as the gate's specification**; their unit classes are declared by D-75.

**G1 (architect §6.1) — the harness predicate is thread-agnostic AND asserts the pins POSITIVE.**
- Thread-agnostic: `main.py:410-415` computes `fam_any` over **all** thread sections, then stores it under `frame_family_present_in_main_thread` (`:416`). The main-thread identification **is** computed (`main_native` `:385-393`; `main_section` `:396-401`) and then **never used** — `main_section` is dead code. At HEAD the **bridge thread's** stack contains `daemon/repositories/instance/repository.py` + sqlalchemy (the new batch method lives in that file) ⇒ the family pin reads POSITIVE ⇒ **G1 as instrumented is a near-certain FALSE-RED at HEAD, not a flaky one.** The plan (`plan-overview.md:202`) asserted a MAIN-thread scoping the code does not implement.
- The capture-count fences are **BASE-only**: `main.py:337` `captures_collected >= 1`; `:423-427` `fam >= 1`; `:434-437` (full profile) `narrow >= 1`. **At HEAD — where the pins must go NEGATIVE — the harness exits 1 on the success condition.** It has no HEAD mode.
- **Corrected fence (paste into the harness expectation):**
  > A capture FAILS G1 **iff the LOOP-thread section** of `stacks.txt` contains the substring `daemon/repositories/instance/repository.py` **AND** the substring `sqlalchemy` (case-insensitive). The loop thread is the section whose stack contains `asyncio/base_events.py` (do **not** match the faulthandler header against `threads.csv` `native_id` — that is the kernel TID, a different id space from faulthandler's pthread id). The router file `daemon/routers/instances.py` **alone is NOT a violation** — dropping it is exactly what excludes the disclosed UI-prefs residual (`instances.py:496` → `instance_ui_prefs/repository.py`), which never contains `daemon/repositories/instance/repository.py`. **Non-vacuity:** G1 is INCONCLUSIVE (not PASS) if the capture phase collected 0 captures; the synthetic handler-load leg must supply ≥1 capture at BASE and 0 instances-path frames at HEAD.

**G2 (architect §6.2) — the band is two-sided and will false-red on a healthy lane.** Post-lane flat shape = 1 count+page + 1 children-batch (off-loop) + **2 prefs (on-loop)** = **4–5 rts** (BASE 13). Defensible expected HEAD loaded flat p95: **0.02–0.10 s (point estimate ≈0.04 s)** — own DB work 13→3 statements (59 ms → ~10–15 ms), bridge hop ~0.5 ms, prefs ~2 ms, bridge queueing ~0, the loop's remaining blocker is the missions' 3 on-loop rts (~18 ms/tick), and D-B collapses the default executor's 1,439-statement storms. **That expected value is BELOW the band's lower anchor ⇒ the band as written fails a lane that works.**
> LOADED flat(10) p95 (badge composite, paired BASE↔HEAD run, same session/fixture): **p95 ≤ 0.230 s → PASS; 0.230 s < p95 ≤ 0.510 s → FAIL (degraded band); p95 > 0.510 s → FAIL (BASE-equivalent). A measured p95 BELOW 0.067 s PASSES** — record it as an improvement. Non-vacuity: `badge_ticks ≥ 5`, every flat-leg response HTTP 200, and the flat shape's round-trip count ≤ 6 (G9). Expected healthy value **0.02–0.10 s**. If the developer's HEAD p95 exceeds **0.12 s**, raise the bridge pool to 6 workers **before** investigating anything else — C-3 parks a 729 ms job on the pool that G2's own leg competes for.

**G9 (architect §6.3) — "instances-path on-loop 0 in every shape" false-reds on the routed-out missions shape.** The harness classifies statements by **table set** (`child.py:69-77` `statement_shape()` = "Verb + sorted table set"), so the missions page leg's `SELECT instances` statements classify as `SELECT instances` **while on-loop** (`BASELINE.md:62`). The plan intends this; G9's wording did not admit it.
> instances flat(10) ⇒ exactly `{"SELECT instance_ui_prefs": 2}` (the D-9 residual; **no** `SELECT instances` on-loop); instances BFS(100) ⇒ same; missions(20) ⇒ exactly `{"SELECT instances": 2, "SELECT job_queue_items": 1}` (routed-out census #4 — **explicitly exempt**, not a failure); every other shape ⇒ on-loop = 0. Round-trip totals per the plan's §(d) table.

**G4 (architect §6.4) — the bare p95 comparison is not falsifiable at the tick floor (non-blocking, but must be amended).** The probe measures `moment - last_exec_at` (`lag_probe.py:254`) — the interval between successive loop-callback executions ⇒ **the healthy value IS the watchdog tick period (~0.5 s)**. BASE p95 = 0.529 / 0.508 / 0.510 s are all one tick ±30 ms of jitter ⇒ **the minimum detectable effect of the p95 comparison is ≈30 ms, below the noise floor.** A strictly-better HEAD can measure *equal or greater* ⇒ **false-red**; and p95 is nearest-rank over 169–288 samples (`lag_probe.py:107`), so a regression affecting <5 % of ticks is invisible in p95 and visible only in **max** (BASE 0.903 / 1.408 / 2.215 s).
> For each profile (badge / home / jobs), using the same window-local lag series the BASE run uses: TICK = 0.5 s, STALL = samples > TICK + 0.25 s (0.75 s). G4 PASS iff, per profile: (a) HEAD's STALL count ≤ BASE's STALL count; (b) HEAD's **max** ≤ BASE's max (no tolerance — a clean loop's max ≈0.55 s vs BASE 0.90–2.22 s); (c) HEAD's p95 ≤ BASE's p95 **+ one TICK** — the bare p95 comparison is forbidden; (d) non-vacuity: each profile's sample count ≥ 0.5 × BASE's, and the synthetic handler-load leg's series is non-empty. **The synthetic handler-load leg is what carries the discriminating signal** (STALL count > 0 at BASE, 0 at HEAD).

**The synthetic handler-load leg does not exist today** (`grep -rn synthetic .agents/tester/harness/eventloop-phase0/` → 0 hits). It is a **tester-lane** deliverable the plan referenced but never scheduled → now scheduled (D-37, phase5 task 5.1).
**Fallback if the leader rejects a correction:** G1's correction is a **behavioural** change to a tester-owned instrument; without it the gate false-reds at HEAD by construction. Rejecting any of G1/G2/G9 leaves the gate unable to distinguish "the lane worked" from "the lane did nothing" — the leader would be accepting a false-red-prone or false-green-prone gate.

## D-37 — The harness HEAD-mode repair is TESTER-owned, SCHEDULED and GATED (BASE-still-red discipline)

**Authority:** ARCHITECT (§6 / §10 Q1 / A16) + LEADER R1. **Cross-map:** architect **A16**, **§10 Q1**; **amends D-12**.
**Disposition:** ADOPTED (binding).

**Decision.**
1. **Ownership: TESTER.** D-12 is preserved — the **developer never edits the tester harness** (`.agents/tester/harness/eventloop-phase0/`). The HEAD-mode repair is a **tester-lane work item**.
2. **It is a SCHEDULED, GATED DEPENDENCY, not vague "harness HEAD expectations":** Phase 5's first task is *"harness HEAD-mode repair (tester) + pin pack"*, and **the developer's functional gate is BLOCKED on it.** The repairs are: G1 thread-scoping + a HEAD mode (the harness currently asserts `captures_collected >= 1` `:337`, `fam >= 1` `:423-427`, `narrow >= 1` `:434-437` — it requires the pins **POSITIVE**); G2's one-sided band; G9's per-shape residual set; G4's STALL/max fence; and the **synthetic handler-load leg** (does not exist).
3. **The BASE-still-red discipline (LEADER R1, binding):** the repaired harness must be run **at BASE** and **MUST STILL GO RED on the same assertions**. **A repair that makes BASE green is a neutered instrument and is REJECTED OUTRIGHT.** (Concretely: at BASE the family pin must still read ≥1 of 8 and the narrow pin ≥1 of 4 on the full profile, and the synthetic leg's STALL count must be > 0 — the same assertions that were red/positive at BASE.)
4. **Process discipline:** the repair lands as **its own reviewable commit** and is **council-reviewed BEFORE any HEAD run.** No HEAD run is executed against an unreviewed harness. *(DATED 2026-10-30, fold round 3 / D-77: the own commit **LANDED — `c3d87af26`**; the council review ran on it and **REJECTED increment #1 with 3 blocking findings** (units / reference provenance / truth-table honesty) — **tester increment #2 is IN FLIGHT**. Items 1–3 and 5 unchanged and binding.)*
5. **The developer's obligation:** the lane must not edit the harness (D-12), must not gate on a harness whose BASE-redness is unverified, and must record the repaired harness's BASE-red evidence in the lane's evidence before handing off.
**Fallback if the leader relaxes D-12 for this path:** the developer would then own a tester-owned instrument, and the fence-audit item "the tester-owned harness path unmodified by the developer" (phase5 task 5.8) would be void for this one file — which the leader would have to accept explicitly.

## D-38 — `/api/work` response serialization: MOVE with the fetch (request-context-free, verified) — else disclose with numbers

**Authority:** ARCHITECT (§7) + LEADER R4. **Cross-map:** architect **§7**.
**Disposition:** ADOPTED (binding).
**Evidence (architect §7):** `daemon/routers/work.py:189` — `return [r.to_dict() for r in records]` — runs **after** the bridged call at `:181`, **on the loop**, and is **O(population)** (1,808 records in the fixture). The plan's phase-3 task 3.6 wrapped only `:181`, so this residual loop-blocking work was neither moved nor disclosed.

**R4's verification requirement, discharged:** `WorkRecord.to_dict()` is defined at **`daemon/services/work_resolver.py:329`** (class `WorkRecord` at `:165`) and its body is a **pure field projection**: it reads only `self.*` attributes and calls two module-level helpers (`_serialize_created_at`, `canonicalize_status`). A scan of the body (`:329`–`:420`) finds **no** `request`/`Request`, no `contextvar`, no `session`, no `engine`, no `environ`, and no repository access. ⇒ **`to_dict()` IS request-context-free.**

**Decision:** the serialization **moves with the fetch** — the bridged callable returns the serialized payload (or the records are serialized inside the same off-loop callable), keeping the bytes identical because the projection is pure and the bridge carries only plain data. **The developer re-confirms this property in Phase 3** before relying on it (the verification above is the plan's evidence, not a substitute for the developer's own check).
**If the re-check finds a request-context dependency:** the serialization **stays on the loop** and is **disclosed as a MEASURED residual WITH NUMBERS** (the O(population) cost, measured at the fixture's 1,808 records) — **no hand-wave**. Either way the lane evidence carries it.
**Fallback if the leader rejects the move:** the residual must be disclosed with numbers in the CHANGELOG and the phase-3 evidence; a silent residual is not acceptable.

## D-39 — Citation corrections (A15)

**Authority:** ARCHITECT (A15). **Cross-map:** architect **A15**.
**Disposition:** ADOPTED (binding) — the following citations in the pre-fold plan are corrected:

| Item | Pre-fold citation | Corrected |
|---|---|---|
| prefs call | `daemon/routers/instances.py:495` | **`daemon/routers/instances.py:496`** (`:495` is the `instance_ids = [...]` comprehension) |
| `_MAX_TRAVERSAL_DEPTH` | `daemon/repositories/instance/repository.py:33` (per the in-repo docstring at `:828`) | **`daemon/repositories/instance/repository.py:50`** (the in-repo docstring at `:828` is itself stale — the architect's A15 names the true site) |
| default-executor site count | 1,351 | **1,400 at `b1183a637`** (the sibling's 1,351 was accurate at `6bebb9ba0` — **drift, not error**); **must be re-derived with the commit pinned** |
| default executor `max_workers` | "12-worker pool" (stated as configured) | **`min(32, cpu+4)` on an 8-CPU host — NOT a configured value** (there is no `set_default_executor` in `daemon/`) |

**Fallback if the leader rejects a correction:** each is a factual citation whose correction changes no behavior; rejecting one leaves a wrong line-number citation in the plan (which the reviewer and tester adjudicate against).

## D-40 — The phase map is regrouped after the T-3 deferral (file-ownership honesty)

**Authority:** PLANNER (fold consequence of D-31) + ARCHITECT §5.4/§7/§2.4. **Cross-map:** **amends D-10**.
**Disposition:** ADOPTED.
**Decision.** The phase count stays **5**; the phase assignments stay P1/P2/P3/P4/P5. What changes is the **file-ownership rationale**, which is now recorded honestly:

| File | Sites (post-fold) | Phase | Owner |
|---|---|---|---|
| `daemon/services/db_offload.py` (NEW) | the seam itself | 1 | P1 |
| `daemon/constants.py` + `daemon/config.py` | the sizing constants (D-21, D-22, D-26/B10) | 1 | P1 |
| `daemon/api.py` | the drain seam (D-24/B3) | 1 | P1 |
| `daemon/routers/instances.py` | **C-1** (`:476`) + **T-2** (`:346`, `:550`) | 2 | P2 |
| `daemon/services/instance_lifecycle.py` | the children-batch merge (`:5113-5116`) + the detail path (`:5143`) | 2 | P2 |
| `daemon/repositories/instance/repository.py` | the new `list_child_ids_for_parents` (beside `:417`) — **the BFS span `:1434-1645` is NOT touched** | 2 | P2 |
| `daemon/services/work_resolver.py` | **C-2(i)** (`:1396-1397`) + **D-B** (`:1779`, `:1847`, `:1878`, `:1174` limit param) | 3 | P3 |
| `daemon/routers/jobs_crud.py` | **C-4(ii)** (`:912`) — **T-3 deferred, so this file now carries ONE site** | 3 | P3 |
| `daemon/routers/work.py` | **C-2 fetch** (`:181`) + **serialization** (`:189`, D-38) | 3 | P3 |
| `daemon/routers/queues.py` + `daemon/routers/messages.py` | **C-3** (`:663`) + **T-1** (`:204`) | 4 | P4 |

**Honest delta:** the "three files each carry two sites" grouping rationale survives for **two** of the three files (`daemon/routers/instances.py`, `daemon/services/work_resolver.py`) and is **GONE** for `daemon/routers/jobs_crud.py` after the T-3 deferral. The phase assignment for `jobs_crud.py` is unchanged because P3 owns the resolver/enrichment path as one work unit; the **single-developer-owner constraint still binds** (one developer instance per file, never two). P3's file count drops from 4 to **3 production files** (`work_resolver.py`, `jobs_crud.py`, `work.py`); `dead_letter_service.py` is no longer in the cut.
**Fallback if the leader wants `jobs_crud.py` split out:** splitting it would put C-4(ii)'s limit propagation in a different phase from the resolver's `limit` parameter — the two halves of one contract (D-5/D-8/D-30) — and would break the "single developer per file" property only by *creating* a second file owner, which the grouping exists to prevent.

## D-41 — G2 is **D-B-dependent**, not bridge-dependent (the lane's headline benefit is reframed)

**Authority:** ARCHITECT (§2.2 / §3.6). **Cross-map:** architect **§2.2**.
**Disposition:** ADOPTED (binding) — this changes the plan's *interpretation*, not its gates.
**Evidence:** the bridge removes the event-loop bottleneck; it **MOVES** the DB/GIL bottleneck. The GIL is process-wide, so a bridge thread performing 1,434 round-trips starves the loop exactly as a default-executor thread did. ⇒ **the relief comes from D-B (A-class), not from C-class (the bridge).** With D-B rejected, a *perfect* bridge still fails G2. With D-B adopted, the jobs path's 1,439-statement storm collapses to ~10–25 and the default executor frees.
**Decision:** the plan states G2's dependency explicitly, and D-4/D-28 are recorded as **REQUIRED** (not "beneficial extension") — matching the architect's §0 ruling. The pre-fold framing ("D-B is an explicit extension beyond the Q1 ruling text, architect-sight flagged") is superseded by D-28.
**Fallback if the leader rejects the framing:** architect §3.6 gives the consequence of the rejected option: `/api/work` stays at **~1,300+ round-trips** (no limit param exists ⇒ the limit lever cannot reach it); the `/api/jobs` page shape misses G9 (~38 rts vs the ~15–30 target); the **92.3 % of statement TIME** item stays open and needs its own commission; **G2 is at risk even with a perfect bridge**; and the measured targets re-scope to **loop-offloading only** — the lane's headline benefit halves.

---

## Summary table

| ID | Decision | Disposition | Authority | Fallback / note |
|---|---|---|---|---|
| D-0 | Stage-only posture | ADOPTED | LEADER | promote = user's nonce ceremony |
| D-1 | A + C mechanism set | ADOPTED | LEADER Q3 | Shape E rejected |
| D-2 | async-native vs bridge | **SUPERSEDED by D-19** | ANALYSIS §3 | Option 2 ⇒ re-plan with the dispatcher |
| D-3 | D-A router/manager default | ADOPT (c) → **AMENDED by D-33** | PLANNER | (a) → Q2 follow-up; (b) rejected |
| D-4 | D-B orphan-miss closure | ADOPT → **AMENDED by D-28** (REQUIRED, 3 sites) | PLANNER | if rejected → targets re-scope to loop-offloading-only |
| D-5 | D-C limit composition | ADOPT α → **SUPERSEDED by D-29** (batched) | PLANNER | β rejected (`created_at` tie fragility) |
| D-6 | Children-order byte-diff gate | ADOPTED (two-part) → **AMENDED by D-34** (5 corrections) | PLANNER | one field-scoped normalization + positive order assertion + multiset pins |
| D-7 | Dedicated bridge executor | **SUPERSEDED by D-20 / D-21 / D-24** | ANALYSIS §2.0 | module renamed; sizing 6; drain seam added |
| D-8 | Explicit-limit detection | ADOPTED → **AMENDED by D-30** (pre-conversion slice) | PLANNER | signature change rejected (would unbound the page) |
| D-9 | Prefs merge stays on-loop | ADOPTED (residual disclosed) → citation fixed by D-39 | ANALYSIS §2.1 | fence = "instances-path on loop = 0" |
| D-10 | 5-phase, file-grouped shape | ADOPTED → **AMENDED by D-40** | PLANNER | P3 = one instance; jobs_crud now 1 site |
| D-11 | T-3 conditional / de-scope lever | **SUPERSEDED by D-31** (DEFERRED) | ANALYSIS §1.2 | re-entry needs A+C + DEAD fixture rows |
| D-12 | Harness path is tester-owned | ADOPTED → **AMENDED/STRENGTHENED by D-37** | PLANNER | repair is scheduled + gated; BASE must stay red |
| D-13 | Rollback posture | ADOPTED (schema-EMPTY) | ANALYSIS §7 | `rollback_safe=true, source=explicit` expected |
| D-14 | Version slot `v0.27.14` | ADOPTED, advisory | PLANNER | re-derived by giter at stage; no reservation |
| D-15 | Routed-out register | BINDING (18 → **19**) | ANALYSIS §1.2 | entry 16 ⇒ ROUTE-OUT; entry 19 ⇒ T-3 |
| D-16 | BFS txn span | **SUPERSEDED by D-32** (ROUTED OUT to R-2) | ANALYSIS §6 | architect carried the routing memo verbatim |
| D-17 | Pool semantics untouched | ADOPTED → **AMENDED by D-22 / D-23** | sibling couplings | capacity 25; F4-A does not protect the bridge |
| D-18 | G2 floor-band provenance | ADOPTED → **AMENDED by D-36** (one-sided band) | PLANNER | tester confirms anchors; explicit amendment only |
| **D-19** | **D-2 decided: Option 1 + A-batches; async-native DEFERRED, UNFUNDED** | **ADOPTED** | ARCHITECT §0/§1/§2.7 + LEADER R2 | label = "Option 1 + A-batches"; re-open trigger = bridge-queue alarm |
| **D-20** | **Bridge module renamed `db_offload.py`; `run_db` → `offload`** | **ADOPTED** | ARCHITECT A8/B8 | cosmetic; before the P1 API freeze |
| **D-21** | **Executor sizing = 6 workers** | **ADOPTED** | ARCHITECT A2/§2.5 + LEADER R8 | "drop to 3" rejected (negative margin) |
| **D-22** | **Manager capacity = 25; `max_overflow` is not a worker budget** | **ADOPTED** | ARCHITECT A3/§2.5 | "4 ≤ overflow headroom" deleted |
| **D-23** | **F4-A does not protect the bridge** | **ADOPTED** | ARCHITECT A4/§5.5 | readiness.py still DO-NOT-TOUCH |
| **D-24** | **Shutdown premise false; drain seam before `dispose_db_pools`** | **ADOPTED** | ARCHITECT A5/B3 + LEADER R6 | `upgrade_journal_sweep.py:332-334` does shut one down |
| **D-25** | **Seam accepts kwargs** | **ADOPTED** | ARCHITECT A6/B5 + LEADER R7 | keyword-only call at `work.py:181` |
| **D-26** | **B1/B2/B4/B6/B7/B9/B10 mandatory amendments** | **ADOPTED** | ARCHITECT A7 | each becomes a P1 task + a pin |
| **D-27** | **Executor-identity pin = thread-name prefix** | **ADOPTED** | ARCHITECT A9/B7 | the tautological check is secondary only |
| **D-28** | **D-B covers 3 sites; degradation-aware absence set** | **ADOPTED (REQUIRED)** | ARCHITECT A11/§3.1-3.4 | degraded batch ⇒ empty absence set |
| **D-29** | **α patch-up must be BATCHED** | **ADOPTED** | ARCHITECT A12/§3.5 | per-row form fails G9 on both jobs shapes |
| **D-30** | **Slice lands PRE-CONVERSION** | **ADOPTED** | ARCHITECT A13/§3.5 | sort at `:1520`; `:1272-1276` is the docstring |
| **D-31** | **T-3 DEFERRED; T-1/T-2 CARRIED** | **ADOPTED** | ARCHITECT A14/§5.4 + LEADER R3 | divergence from criterion mapping recorded explicitly |
| **D-32** | **D-16 ROUTED OUT to R-2 (memo verbatim)** | **ADOPTED** | ARCHITECT §4.3 | owner = DB-pool/R-2 lane; Δ3 budget missing |
| **D-33** | **`include_descendants`: keep + named pair + explicit internal sites** | **ADOPTED (c)+** | ARCHITECT §5.1 + LEADER R5 | zero byte impact; no default flipped |
| **D-34** | **Children-order gate: 5 corrections** | **ADOPTED** | ARCHITECT §5.2 | multiset / per-parent+global / static SQL / all 241 rows / detail shares source |
| **D-35** | **TB-1 … TB-7 transaction-boundary rule** | **ADOPTED IN FULL** | ARCHITECT §5.3 | verified on three callees |
| **D-36** | **G1/G2/G9/G4 gate-fence corrections (BLOCKING)** | **ADOPTED** | ARCHITECT §6 | the gate cannot pass as committed today |
| **D-37** | **Harness HEAD-mode repair: tester-owned, scheduled, gated** | **ADOPTED** | ARCHITECT §6/A16/§10 Q1 + LEADER R1 | BASE must stay RED on the same assertions |
| **D-38** | **`/api/work` serialization moves with the fetch** | **ADOPTED** | ARCHITECT §7 + LEADER R4 | `to_dict()` verified request-context-free at `:329` |
| **D-39** | **Citation corrections (A15)** | **ADOPTED** | ARCHITECT A15 | `:496`, `:50`, 1,400 sites, `min(32, cpu+4)` |
| **D-40** | **Phase map regrouped after the T-3 deferral** | **ADOPTED** | PLANNER (fold consequence) | P3 → 3 production files |
| **D-41** | **G2 is D-B-dependent, not bridge-dependent** | **ADOPTED** | ARCHITECT §2.2 | the lane's headline benefit halves without D-B |

---

## Open items after the fold

**Resolved by the fold (were open before):**
- ~~D-2 — architect ruling on the architecture question~~ → **RESOLVED by D-19** (architect §0/§1/§2.7).
- ~~D-16 — architect ruling on the BFS span~~ → **RESOLVED by D-32** (ROUTED OUT, memo carried verbatim).
- ~~D-4 — architect sight on the D-B extension~~ → **RESOLVED by D-28** (ADOPTED AS REQUIRED).

**Still open for the leader (2) — the architect's §9/§10 items the fold could not close:**

1. **Is the deferral contract funded?** (architect §10 Q2.) The architect's five artefacts are **unfunded** here per LEADER R2, so D-2 is recorded as **"Option 1 + A-batches"**. If the leader prefers the "hybrid" label, the fallback is to fund §2.6 items 1 and 5 (the production registry + the registry-coverage pin — one module and one test file). **No phase budget changes** either way; only the label and the funded set.
2. **The G2 anchors (D-18 / D-36) remain commission-body values.** The 0.23 s upper anchor and the 0.51 s degraded marker are not re-derivable from the committed `BASELINE.md`. The tester must confirm them against the sibling lane's composite record during the pre-gate; any deviation is an **explicit test-plan amendment**, never a silent re-baseline. (This is the architect's unchanged non-blocking item.)

**Carried as a recorded divergence (not open):** T-1/T-2 are carried on the objective-sentence + same-seam argument although they satisfy **no positive criterion** (D-31) — the leader ruled carry; the divergence is recorded per LEADER R3.

---

# FOLD ROUND 2 — REVIEWER REJECT CLOSED (D-42 … D-65)

**Input:** `.agents/reviewer/RESULTS/2026-10-29-wi-3f5bea95-plan-gate-review.md` — **VERDICT: REJECT**, "8 criticals, all plan-text/ownership fixes; **no architecture redesign required**".

**Prior decisions amended in this round** (each new row below names its own amendment; this table is the index):

| Prior | Amended by | What changed |
|---|---|---|
| **D-26 (B4 admission gate)** | **D-43** | permits == workers; release in the executor future's **done-callback**, never the caller's `finally`; cancellation pins (ix)/(x) |
| **D-26 (B1 re-entrancy guard)** | **D-52** | detection mechanism named: **thread identity** (`db-offload` prefix), because `copy_context()` is taken in the loop thread at submit time and therefore cannot carry the marker |
| **D-26 (B2 BaseException)** | **D-52** | `except asyncio.CancelledError: raise` **before** any `BaseException` handler + pin (xi) |
| **D-24 (drain seam)** | **D-53** | **four-step** drain + `DB_OFFLOAD_DRAIN_BOUND_S` + the waiter-failure pin + `TimeoutStopSec=90` context |
| **D-30 (pre-conversion slice)** | **D-42** | slice **restructured and specified**: `source_rows` accumulation → same `_normalize_sort_key` sort → slice → convert only the slice; `:1520` re-cited as the **comparator contract**, not the slice site; stable tie-break specified; post-conversion-filter disclosure added |
| **D-36 (G1/G2/G9/G4)** | **D-45, D-46, D-50, D-51, D-56** | G4 condition (e); G1 three-part + attribution table; G2 annotation; G3 mechanisation; G9 frozen totals |
| **D-36 / G10 (no-page-job-mirror-regression)** | **D-47** | **PHANTOM ANCHOR corrected**: `jobs_crud.py:900-902` is a comment block; real fallback at `:153` + `:384-405`, trigger `:951`; observable defined |
| **D-37 (tester-owned gated repair)** | **D-49** | turned from **instruction** into **mechanically asserted** dependency (tree-sha + porcelain + attribution) |
| **D-38 … D-41** | unchanged | — |

## D-42 — C-1: the pre-conversion limit slice is RESTRUCTURED and fully specified

**Authority:** REVIEWER (C-1). **Cross-map:** reviewer **C-1**; **amends D-30** (round 1).
**Disposition:** ADOPTED (binding) — the round-1 text was **unimplementable as written**.
**Evidence (reviewer C-1, re-verified in-tree at `42764588c`):** `phase3-plan.md` round-1 task 3.3 required the slice "on the merged + sorted SOURCE ROWS" and cited `daemon/services/work_resolver.py:1520` as the sort. **`:1520` is `records.sort(key=lambda r: _normalize_sort_key(r.created_at), reverse=True)` — the LAST statement of `list_work`, sorting already-converted `WorkRecord`s.** Conversion is inline: Task branch `record = self._task_to_record(task, instance=instance)` at **`:1406`**; Job branch `records.extend(self._job_to_record(...))` at **`:1461`** and **`:1472`**. **No pre-conversion sort exists in the function.** The **stable tie-break is unspecified**: equal `created_at` currently orders by append order (Task block then Job block), so a naive pre-conversion sort changes the delivered set at `limit=N`.

**Prescribed implementation (folded as written into phase3 task 3.3):**
1. Accumulate `source_rows = [(normalized_created_at, row)]` from **both** branches **in CURRENT APPEND ORDER** (Task block first, then Job block — preserving today's tie-break).
2. Sort with **the same comparator** `_normalize_sort_key(..., reverse=True)` — re-cite `:1520` as the **COMPARATOR CONTRACT**, **not** the slice site.
3. **Slice** the sorted `source_rows`.
4. **Convert only the slice** (`_task_to_record` / `_job_to_record`).
5. **Specify the stable tie-break explicitly:** equal `created_at` ⇒ **append order is the tiebreak** (Task block before Job block), i.e. the sort must be **stable** and applied to a list built in append order. Python's `list.sort` is stable — the requirement is that the accumulation order be preserved and that the sort key be applied without an index-perturbing pre-pass.
6. **Disclose:** the Task branch's `project_id` / `root_only` filters run **POST-conversion** (`:1402-1406` — the `root_only and instance is not None and instance.parent_id: continue` guard at `:1404` and the `record.project_id != project_id: continue` filter at `:1407-1408`, both **after** `:1406`'s conversion) ⇒ **a sliced Task population can deliver fewer than N records.** This is recorded as a disclosed property, not fixed (moving those filters pre-conversion would change which rows are converted and is out of scope).

**Fallback if the reviewer's prescribed form is rejected:** the only alternative that preserves the statement bound is a two-pass design (sort a projection of `(created_at, branch, index)` then re-walk) — strictly more code for the same result; and any design that sorts post-conversion fails the whole point (the singles are already paid). Recorded so there is no silent branch.

## D-43 — C-2: the admission gate is specified — permits, release site, cancellation pins

**Authority:** REVIEWER (C-2). **Cross-map:** reviewer **C-2**; **amends D-26 / B4** (round 1).
**Disposition:** ADOPTED (binding) — both failure modes the round-1 text left open are closed.
**Evidence (reviewer C-2):** round-1 task 1.4 said only "an `asyncio.Semaphore` acquired with `await`". **Permit count unstated. Release site unstated. No cancellation pin.** Two open hazards: (a) releasing in the **caller's `finally`** on cancellation returns the permit **while the thread still runs** ⇒ the gate stops bounding and the executor queue becomes the unbounded path B4 exists to close; (b) **skipping** the release on a cancellation path **leaks permits** ⇒ the seam deadlocks permanently after N cancellations — **worse than the B1 hazard that *is* guarded**. Pin 1.9(vi) cannot see either (it never cancels).

**Decision:**
1. **Permits == `DB_OFFLOAD_MAX_WORKERS`** (6 at the default tier) — the gate and the pool are the same size by construction.
2. **Release in the EXECUTOR FUTURE'S DONE-CALLBACK** — thread-side truth — **never the caller's `finally`.** The permit is returned when the worker actually finishes, so cancellation of the awaiting task cannot over-release (the thread still holds the slot) and cannot leak (the callback fires regardless of the await's fate).
3. **New pins:** **(ix)** cancel while awaiting `acquire` → permits return to full **and** the seam still completes afterwards; **(x)** cancel while awaiting the result → permits return to full **and** the seam still completes afterwards. Both are required; neither exists in round 1.
4. **Waiter accounting (with W-15 / D-53):** the gate counts **waiters + in-flight** and alarms on the sum vs permits — the executor queue depth is ~0–1 by construction behind the gate, so a queue-depth alarm is structurally blind.

**Fallback if the done-callback release is rejected:** the caller-`finally` form must then be accompanied by a **thread-side liveness reconciliation** (a permit is returned only when the future reports done) — which is the same thing implemented less directly. There is no third option that is safe under cancellation.

## D-44 — C-3: the four exact-count test packs are re-derived, with an adjudication rubric

**Authority:** REVIEWER (C-3). **Cross-map:** reviewer **C-3**.
**Disposition:** ADOPTED (binding) — without it **P5/G6 fails deterministically** with no owner.
**Evidence (reviewer C-3, re-verified in-tree):**

| Pack | Site | Current assertion |
|---|---|---|
| `tests/unit/services/test_work_resolver_query_budget.py` | `:295` | `assert spy.counts["instances"] == 2` ("_batch_instances + _batch_child_instance_ids") |
| same | `:324` | `assert spy.counts["instances"] == 1` |
| same | `:410` | `assert spy.counts["instances"] == 0` |
| `tests/unit/services/test_fix_c_read_model_split.py` | `:750` | `call_count <= 5` ("job_repo SELECT + at most 3 instance-repo queries … plus the S4 batched _batch_mission_fields JobItem SELECT") |
| `tests/unit/services/test_mission_resolver.py` | `:770` | patches `_batch_instances` with `side_effect=SQLAlchemyError(...)` (degradation shape) |
| same | `:998` | the dual-spy DML-absence shape that opens its own session via `_batch_instances` |
| `tests/unit/services/test_job_queue_proxy_phase1.py` | `:703` | spy over `_batch_instances`' single `SELECT … IN (...)` |
| same | `:734` | count clause covering both `_lookup_instance` and `_batch_instances` |

**Task 3.1 adds a second `_batch_instances` call** (one for the Task branch, one for the Job branch) ⇒ `== 2` becomes `== 3` for **task-bearing** shapes, `<= 5` becomes `<= 6`, and the `== 1` / `== 0` shapes need per-shape adjudication.

**Decision:** a **new P3 task (3.10)** re-derives those exact-count expectations and **adjudicates each in the phase evidence** against the rubric: *legitimate count increase* (a new batch call site on a **different** population, one statement per site, no per-row growth) vs *N+1 regression* (count grows with page size). The four packs are **named in P3's exit criterion** and **in P5 5.4's scoped-suite list**, so G6 has an owner.
**Fallback if a pack's expected value cannot be adjudicated as legitimate:** the pack's assertion is amended **only** with a recorded per-shape justification; an unexplained increase REDs and blocks P3's exit (never silently re-baselined — the round-1 D-39 discipline).

## D-45 — C-4: G4 gains condition (e) — the discriminating signal becomes a predicate

**Authority:** REVIEWER (C-4). **Cross-map:** reviewer **C-4**; **amends D-36 / G4**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer C-4):** round-1 G4 PASS required only (a) `STALL_HEAD ≤ STALL_BASE`, (b) max, (c) p95 + one TICK, (d) non-vacuity. **"STALL > 0 at BASE, 0 at HEAD" appeared only in prose** ⇒ **a HEAD identical to BASE satisfies (a)–(d)** — the gate passes on a no-op.

**Decision:** add **PASS condition (e): `STALL_HEAD_synthetic == 0 AND STALL_BASE_synthetic > 0`**, with the BASE-red **proven on the same leg** (not inferred, not borrowed from another profile). G4 PASS ⟺ (a) ∧ (b) ∧ (c) ∧ (d) ∧ (e).
**Fallback if the synthetic leg cannot produce STALL > 0 at BASE:** G4 is then **INCONCLUSIVE, not PASS** — the same discipline as G1's non-vacuity clause (W-3/D-56). There is no "prose pass".

## D-46 — C-5: G6 gains collected-count, digest, collection-error and pre-agreed-fallback fences

**Authority:** REVIEWER (C-5). **Cross-map:** reviewer **C-5**; **amends D-36 / G6**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer C-5):** round-1 G6 was "net-new reds = 0" with non-vacuity scoped to **scoped suites only**. Two known mechanisms make HEAD collect **fewer** nodes: the **`g7` module-level `sys.exit(0)` class** (named as base debt, given no assertion) and the **async-hang class** that killed the v0.27.11 whole-tree gate (which shipped on compensating scoped evidence). Under the stated rule, **every BASE-red that vanishes because a module failed to collect scores "absent at HEAD → pass"**.

**Decision — four fences, all mandatory:**
1. **`collected_HEAD >= collected_BASE`**, with every declared skip enumerated by node id.
2. **Failed-count AND FAILED-digest comparison on both sides** (the digest is what makes "the same failures" checkable rather than assertable).
3. **Any collection error = FAIL, never a skip** (the `g7` class is a **fail**, not a documented exemption).
4. **A PRE-AGREED fallback for the async-hang class**, decided **before** the gate runs: the **v0.27.11 compensating-evidence recipe** (`staged_release_v02711_composition.gate3_disclosure` — scoped net-new EMPTY on the change surface + sha-pinned HEAD digests + the preserved replay recipe), sha-pinned, replay preserved. **Deciding mid-gate is how false greens are born** (reviewer's phrasing, adopted).

**Fallback if the leader declines the pre-agreed fallback:** then an async hang at either leg is a **hard FAIL** of G6 (no compensating evidence accepted) — which is stricter, and the plan says so explicitly so nobody improvises at hang time.

## D-47 — C-6: the legacy-mirror anchor is CORRECTED (`:900-902` is a comment block)

**Authority:** REVIEWER (C-6). **Cross-map:** reviewer **C-6**; **amends G10 and pin 3.9(b)**.
**Disposition:** ADOPTED (binding) — round 1's G10 and pin 3.9(b) were **vacuous**.
**Evidence (reviewer C-6, independently confirmed by two reviewers; verified in-tree here):** `daemon/routers/jobs_crud.py:898-904` is a **COMMENT BLOCK** about `work_resolver.list_work` / `_batch_instances` and filter alignment — `grep "legacy JobItem-mirror\|degrades to"` = **0 hits**. The **real** legacy-mirror fallback is:
- **the `else:` execution-state-mirror branch at `:152-153`** inside `_job_to_response` (execution state read straight off the JobItem mirror columns; **there is NO `if work_record is None:` at `:153`** — that `if` is at `:383`, inside `_derive_m2_mission_ref`), and
  *(corrected 2026-10-30, fold round 3 / D-78 — the C-6 🟢: the original bullet named a branch form that does not exist at `:153`; the anchor `:152-153`, the role and the trigger are unchanged)*
- the **mission-ref degrade** at **`:384-405`** ("No resolver view — degrade to the legacy fallback shape", with `mission_id` from `job.instance_id` and `liveness` derived from `terminal_reason`),
- **triggered by** `work_records_by_id.get(job.job_id)` at **`:951`** (the `work_record=` argument into `_job_to_response`, `:949-952`).

**Decision:** re-anchor **G10** and **pin 3.9(b)** to **`:951` (trigger) + `:152-153` (execution-state mirror — the `else:` legacy branch; corrected from ":153" by D-78) + `:383-405` (mission-ref degrade; `if work_record is None:` at `:383`, D-47's own ":384-405" cited the comment line inside it)**. **Define the observable:** the **per-request count of page-jobs rendered with `work_record is None`** — and assert **"0 that were 1 at BASE"** (per-job identity, not an aggregate count, so a swap cannot net out).
**Fallback if the leader wants the aggregate form:** an aggregate count is accepted **only** with per-job identity retained in the evidence; a bare count is not, because a swap (one job regressing while another recovers) nets to zero.

## D-48 — C-7: the lane gets a boot/startup/shutdown gate (arena-local boot smoke)

**Authority:** REVIEWER (C-7). **Cross-map:** reviewer **C-7**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer C-7):** the lane's **only lifespan edit** is `await db_offload.shutdown()` before `await manager.shutdown()` (`daemon/api.py:3809`; `daemon/manager.py:15223` `("dispose_db_pools", …)`), and the planning set contained **zero `livez|readyz|boot smoke` hits**. Every prior staged lane ran an arena-local boot smoke: "a drain that deadlocks or an executor that fails to construct is a daemon that will not start or will not stop, and no gate would see it."

**Decision — new P5 task (5.11) "staged-build boot smoke (arena-local)":**
1. **Banner version** matches the staged build.
2. **`/livez` + `/readyz` both 200.**
3. **SIGTERM teardown WITH A BRIDGED READ IN FLIGHT**, asserting **no `Engine is disposed`** and a **bounded exit** (within `DB_OFFLOAD_DRAIN_BOUND_S` + margin, inside the unit's `TimeoutStopSec=90` — `scripts/systemd/ensemble-daemon.service:115`).
4. **Fences (standing critical note — staged boot smokes are NOT isolated by default; path derivation resolves the LIVE install root):** a **scratch data dir**; **per-variable env scrub** (each `POSTGRES_*` unset **by name** — the `env -u POSTGRES_*` glob idiom is a silent no-op); the **live `state.json` sha256 unchanged** pre/post.
**Fallback if the boot smoke cannot run in this lane:** the lifespan edit **does not ship** — the drain seam is deferred with the shutdown-race disclosed as a residual (D-24's fallback). A daemon that cannot start/stop must not be staged unexercised.

## D-49 — C-8 / RULING 3: three commit units, mechanically asserted

**Authority:** **LEADER RULING 3** + REVIEWER (C-8). **Cross-map:** reviewer **C-8**, leader **RULING 3**; **amends D-37**.
**Disposition:** ADOPTED (binding) — round-1 task 5.1 was **instruction-only and already violated**.
**Evidence:** reviewer C-8 observed in this worktree: the tester repair exists as **uncommitted modifications + 4 untracked files** in `.agents/tester/harness/eventloop-phase0/`, **on the same branch the developer will implement in**. Consequences: (1) no reviewable commit for 5.1's acceptance; (2) "no HEAD run before review" unenforced — `run_all.sh` runs from a dirty tree; (3) **G7/G12 path-based fences are already red** (untracked planning files + modified harness are indistinguishable from developer writes **by path alone**); (4) `git add -A` / `commit -a` silently absorbs the tester's WIP — the untracked-collider class.

**LEADER RULING 3 — THREE separately-attributable units:**
1. **The tester harness repair lands as its OWN commit with TESTER identity BEFORE P1 starts** (the tester is already commissioned; it will be council-reviewed).
2. **The planning set rides the developer's W1 commit with EXPLICIT PATHS ONLY** — **the paths are named in the commit message.**
3. **Production code is separate.**

**Mechanically forbidden / asserted (all folded into phase5 task 5.1 and the DO-NOT list):**
- **`git add -A` (and `commit -a`) is BANNED everywhere in the lane** — a stated DO-NOT, not advice.
- **Recorded `git rev-parse HEAD:.agents/tester/harness/eventloop-phase0` equals the reviewed tree sha** (the tree-sha of the tester commit) at every HEAD run.
- **`git status --porcelain` over that path is EMPTY** at any HEAD run.
- **Byte-identical before/after** the developer's commits: the harness dir tree-sha must not change ⇒ the developer can neither absorb nor modify the tester instrument.
- **State which commit carries the planning set** (in the lane evidence and the commit message).
- **G12 is re-scoped to ATTRIBUTION** — *which commit owns which path class* — rather than a bare path fence (a path fence cannot distinguish a tester write from a developer write, which is exactly why it was already red).

**Fallback if the leader later relaxes the three-unit split:** the tree-sha + porcelain assertions **stay regardless** — they are what makes the instrument auditable. Only the "own commit / own identity" requirement is relaxable.

## D-50 — RULING 1: G1 becomes three-part, with a REQUIRED attribution table and named residuals

**Authority:** **LEADER RULING 1** (settles reviewer W-1 / "Leader decisions required" #1). **Cross-map:** reviewer **W-1**; **amends D-36 / G1**.
**Disposition:** ADOPTED (binding) — **widen the assertion, NOT the scope.**

**The ruling.** G1 becomes **three-part**:
- **(a)** in-scope family frames **ABSENT** from wedge captures at HEAD (the round-1 corrected fence, unchanged);
- **(b)** **ANY remaining `sustained_loop_lag` capture at HEAD must be ATTRIBUTABLE to an ENUMERATED routed-out site** — an unattributable capture is a **FAIL**, not a residual;
- **(c)** an **ATTRIBUTION TABLE** (capture → attributed site → owning routed item) is a **REQUIRED deliverable of the gate**.

**The criterion row must list the residuals BY NAME** — at minimum: **(1)** `daemon/routers/jobs_crud.py:943` DLQ N+1 (T-3, deferred — routed-out entry 19); **(2)** the **missions shape** (`daemon/routers/missions.py:277`, `:349` — census #4); **(3)** the **prefs merge** (`daemon/routers/instances.py:496`); **(4)** the **per-turn ContextSlot read** (`daemon/graph.py:868`).

**Rationale (folded verbatim in substance):** *a green G1 must never be quotable as "the incident class is closed" while a known site still wedges the loop.*

**Honesty clause folded into the plan's success-criteria section:** the claim is **SCOPED CLOSURE + enumerated residuals — NEVER class-closure.** The Objective, the G1 row, the closeout docs and the CHANGELOG all carry that phrasing; a green G1 quoted as class-closure is a **reporting defect**.
**Plus the reviewer's non-blocking companion assertion (W-1):** "captures at HEAD whose loop section contains **any** sqlalchemy/psycopg frame = 0" — recorded as a **non-blocking diagnostic** so residual fires are visible without either widening scope or blocking on them.
**Fallback if the leader reverses:** the plan reverts to the round-1 single-part G1 **and must then state in the criterion row that a green G1 does not cover the residual set** — **two classes (round-3.5 taxonomy): the four on-loop wedge residuals, and the executor-class measured shape which is a G9 non-regression item, never a G1 item** — the honesty clause survives either way; only the attribution table becomes optional.

## D-51 — RULING 2: the G2 PASS boundary stands, annotated `provenance: commission-body, unverified`

**Authority:** **LEADER RULING 2** (accepts the reviewer's A-6 fallback). **Cross-map:** reviewer **A-6**, **W-2**; **amends D-36 / G2**.
**Disposition:** ADOPTED (binding) — **no silent waiver, no re-litigating the anchor.**

**Decision.**
1. The **0.230 s / 0.510 s anchors STAND as commission-mandated**, and the PASS is annotated **`provenance: commission-body, unverified`**.
2. The **measured HEAD floor band is recorded alongside** the anchor (both numbers in the evidence — the anchor does not suppress the measurement).
3. **If HEAD measures BETTER than the band ⇒ PASS, annotated** (the annotation records "better than the commission band; band not independently verified").
4. **If HEAD measures WORSE ⇒ FAIL.** No discretion.
5. **No silent waiver and no re-litigating the anchor** — a change to the anchor is a leader decision recorded as a new D-id, never a gate-time adjustment.

**Fallback:** none exists inside the lane; A-6's fallback wording is now the standing position ("commission-mandated, annotated unverified, never re-baselined").

## D-52 — W-13 / W-14: the re-entrancy guard uses THREAD IDENTITY; cancellation propagates

**Authority:** REVIEWER (W-13, W-14). **Cross-map:** **amends D-26 / B1 and B2**.
**Disposition:** ADOPTED (binding).
**W-13 evidence:** B1 mandated "a runtime guard that raises a typed error" but **never named the detection mechanism**. The natural `ContextVar` marker is **defeated**: `copy_context()` is taken **in the loop thread at submit time**, so a worker-thread callee sees the *caller's* context, not a worker marker.
**W-13 decision:** detect **thread identity** — `threading.current_thread().name.startswith("db-offload")` ⇒ **raise before submitting**. Defence-in-depth, matching **B7**'s prefix requirement (and it makes B7 load-bearing twice).
**W-14 evidence:** "contain `BaseException`" as written **swallows `asyncio.CancelledError`**, converting cancellation into a "defined outcome" daemon-wide.
**W-14 decision:** **`except asyncio.CancelledError: raise` precedes any `BaseException` handler**, and **pin (xi)** asserts caller cancellation propagates unchanged **with the permit returned**.
**Fallback:** rejecting W-13 leaves the guard unimplementable (there is no working marker); rejecting W-14 converts every cancellation into a swallowed event — neither is survivable, so there is no fallback beyond "do not implement B1/B2".

## D-53 — W-12 / W-15: four-step drain + waiter counting (the real backlog)

**Authority:** REVIEWER (W-12, W-15). **Cross-map:** **amends D-24 (drain)** and **D-26 (observability)**; interacts with **D-43** (gate) and **D-19** (the alarm is the async-native re-open trigger).
**Disposition:** ADOPTED (binding).
**W-12 evidence:** round-1 drain "stops accepting and `cancel_futures=True`" — but **with C-2's gate the executor queue is empty by construction**, so the accumulating class is the **semaphore waiters**, which drain never touches. The bound value was unstated, and pool threads are **non-daemon** ⇒ the process can outlive the bound into **`TimeoutStopSec=90`** (`scripts/systemd/ensemble-daemon.service:115`).
**W-12 decision — four-step drain:**
1. set a **`closed` flag** (new submissions fail with the same defined error);
2. **fail all waiters** with **the same defined error** (so the caller contract is uniform);
3. **wait for in-flight** work up to **`DB_OFFLOAD_DRAIN_BOUND_S`** (new constant — default tier in `daemon/constants.py`, env-overridable in `LimitsConfig` per B10/D-26);
4. **`shutdown(wait=False, cancel_futures=True)`**.
Plus: **a pin for the waiter-failure path** (a waiter parked on `acquire` when drain begins receives the defined error, not a hang), and the **`TimeoutStopSec=90`** context recorded (the bound + in-flight must fit inside it).
**W-15 evidence:** `DB_OFFLOAD_QUEUE_WARN_DEPTH` alarms on **executor queue depth**, which is ~0–1 **by construction behind the gate**. The real backlog is the **waiter set**, which nothing counted — and this alarm is the **designated re-open trigger for the async-native campaign (D-19)**, so the deferral's escape hatch could not fire.
**W-15 decision:** count **waiters + in-flight** and alarm on **the sum relative to permits**; **pin it by saturating the gate** (not the pool). The alarm's identity as the D-19 re-open trigger is stated in the module docstring.
**Fallback:** the queue-depth alarm is retained as a **secondary** diagnostic; rejecting W-15 leaves D-19's re-open trigger structurally unable to fire, which would make the deferral dishonest.

## D-54 — W-9: the detail path routes through `list_child_ids_for_parents([id])`; `list_child_ids_permanent` is FORBIDDEN to touch

**Authority:** REVIEWER (W-9). **Cross-map:** **amends D-34 corr. 5 / phase2 task 2.4**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-9, re-verified in-tree):** `list_child_ids_permanent` has **six** production call sites: the two in scope (`daemon/services/instance_lifecycle.py:5115` list path, `:5143` detail path) and **four co-callers not in the compared set** — `daemon/tools/instance_read.py:818`, `daemon/tools/instance_ops.py:348`, `daemon/services/stale_pause_sweep.py:298`, `daemon/services/mission_strand_healer.py:1518`. The obvious mechanism (put an `ORDER BY` inside `list_child_ids_permanent`) would **silently reorder four other production surfaces**.
**Decision:** route the detail path `daemon/services/instance_lifecycle.py:5143` **through `list_child_ids_for_parents([instance_id])`**; **FORBID touching `list_child_ids_permanent`**; list the four co-callers as **unchanged / DO-NOT-TOUCH**; **extend pin 2.7(c)** (the static SQL pin) to the statements emitted on **both** paths.
**Fallback if the reviewer's routing is rejected:** the only alternative is to leave `:5143` unordered and **drop the "detail shares the ordered source" assertion** (D-34 corr. 5) — which re-opens the list-vs-detail disagreement that correction exists to close.

## D-55 — W-10: lazy `db_offload.configure(...)` in the lifespan; constant names aligned; subprocess pin

**Authority:** REVIEWER (W-10). **Cross-map:** **amends D-26 / B10 and phase1 tasks 1.1–1.3**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-10, verified):** round 1 wrote `ThreadPoolExecutor(max_workers=DB_OFFLOAD_MAX_WORKERS)` — **a name defined nowhere** (only `_DEFAULT` exists by design per B10). `config.limits.*` is **manager-reachable** while `offload()` is called from **routers**, and **construction timing was unstated** ⇒ the env knob is either **dead** or **import-order dependent**.
**Decision:** construct the executor **lazily via `db_offload.configure(max_workers=…)` called from the lifespan** (after config load, before the first request), so the knob is read **once, in the lifespan**, never at import. **Pin the override in a subprocess** (a child process with `LIMITS_DB_OFFLOAD_MAX_WORKERS=<n>` observes n workers) — an in-process pin would be defeated by module caching. **Align the names:** `DB_OFFLOAD_MAX_WORKERS_DEFAULT` (constants tier) → resolved to a concrete runtime value passed into `configure()`; **no bare `DB_OFFLOAD_MAX_WORKERS` symbol is referenced anywhere.**
**Fallback if lazy construction is rejected:** an eager import-time construction is acceptable **only** with the env read moved to import and documented as such — it forfeits the subprocess-observable override and must be disclosed.

## D-56 — W-3 / W-4 / W-5: G1, G3 and G9 are mechanised

**Authority:** REVIEWER (W-3, W-4, W-5). **Cross-map:** **amends D-36 / G1, G3, G9**.
**Disposition:** ADOPTED (binding).
- **W-3 (G1 ambiguity):** "0 captures at HEAD" read as **both PASS and INCONCLUSIVE** in one sentence. **Decision:** **`HEAD: captures >= 1 AND loop-section-hits == 0`** ⇒ PASS; **`captures == 0` ⇒ INCONCLUSIVE** (never PASS); **PASS additionally requires the BASE artifact cited by PATH + sha256** (so the positive control is a citation, not a claim).
- **W-4 (G3 independence):** "same as G1" is **unfalsifiable standalone** — no artifact fails if G1 never runs. **Decision:** **G3 PASS ⟺ G1 is not INCONCLUSIVE AND `narrow_BASE >= 4/8` AND `narrow_HEAD == 0`** (three conjuncts, each independently checkable).
- **W-5 (G9 tautology):** totals were "~" ranges and expectations were "updated from the developer's measured HEAD run" ⇒ the bound becomes a **drift detector between two runs of the same code**. **Decision:** **freeze the G9 totals from formula arithmetic BEFORE the HEAD run**; the measured run may **only confirm** them or **trigger a recorded amendment** — never silently set them. (The per-shape **sets** were already exact and stay exact.)
**Fallback:** none needed — each is a strictly stronger predicate; rejecting one leaves the named ambiguity in place.

## D-57 — W-6: the G11 baseline is restated — live is **v0.27.12**, not v0.27.13

**Authority:** REVIEWER (W-6). **Cross-map:** **amends G11 + the rollback paragraph + D-14/D-18 context**.
**Disposition:** ADOPTED (binding).
**Evidence (verified in-tree this round):** the live release is **v0.27.12** (`releases/current` → `v0.27.12`; `state.json` `current: v0.27.12`). Round 1 asserted "vs live v0.27.13". **The arithmetic survives** (115 files; v0.27.13's own migration delta was EMPTY) **but the sentence hid two facts.**
**Decision:** restate G11 as **"vs live **v0.27.12** (re-derive at stage)"**, and name in the rollback paragraph that **promoting this artifact SUPERSEDES the unpromoted v0.27.13** and that **the rollback target is v0.27.12**.
**Fallback:** none — this is a factual correction; a stale baseline label in a rollback adjudication is a defect regardless of the arithmetic.

## D-58 — W-7: the G7 `.env` fence is scoped to AUTHORED CONTENT, with machinery disclosed

**Authority:** REVIEWER (W-7). **Cross-map:** **amends G7 / task 5.8**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-7):** G7's "0 `.env` edits" **contradicts house machinery** — `stage.sh` re-stages `ENSEMBLE_SELF_ENV=live` and has chmod'd the live `.env` (v0.27.3 and v0.27.6 disclosures). As written the fence is either **red at stage** or **silently unenforced**.
**Decision:** scope G7 to **"no `.env` content authored by this lane; machinery-owned marker/chmod disclosed and verified byte-identical sha256 pre/post."**
**Fallback:** none — the strict form is unenforceable, and an unenforceable fence is worse than a scoped one.

## D-59 — W-17: a source-scan pin over `daemon/` enforces the bridged set (run at P5)

**Authority:** REVIEWER (W-17). **Cross-map:** **amends D-26 / task 1.11**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-17):** TB-1…TB-7's "a fourth bridged callable is a deliberate failure" had **no production anchor** — the registry is **UNFUNDED (D-19)**, so task 1.11 checks a **hardcoded list against itself**, and it is authored in **P1 before P2–P4 create the sites**.
**Decision:** add a **source-scan pin over `daemon/`** — every `offload(` call site must appear in the **expected set**; a new site REDs. **Run it at P5** (after P2–P4 create the sites), in addition to P1's static negative.
**Fallback:** without it, the bridged-callable discipline is a comment. (The funded-registry option remains available under D-19's funding fallback.)

## D-60 — W-18: the CLAMPED limit is forwarded; presence-only detection

**Authority:** REVIEWER (W-18). **Cross-map:** **amends D-8 / task 3.4**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-18, verified):** task 3.4 detected **presence** via raw query params but **never specified which VALUE is forwarded**; the endpoint **clamps at `daemon/routers/jobs_crud.py:815`** (`limit = max(1, min(limit, MAX_JOB_LIST_LIMIT))`, cap 100). Forwarding the **raw** param means **`?limit=10000` enriches 10,000 rows behind a 100-row page**.
**Decision:** forward the **clamped local `limit`** (the value after `:815`); detection stays **presence-only** via `query_params`; **pin `?limit=10000` ⇒ enrichment ≤ 100**.
**Fallback:** none — forwarding the raw value is a load amplifier that also widens the D-B batch.

## D-61 — W-16: readiness gets a BEHAVIOURAL leg, not only a diff assertion

**Authority:** REVIEWER (W-16). **Cross-map:** **amends D-23 / TEST/PIN PLAN (f)**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-16):** "readiness.py untouched" enforced by diff **proves the file unmodified, not that behaviour is unperturbed**: ≤6 persistent borrowers perturb the `checkedout()` series that `_checkedout_rising` requires to be strictly monotonic (`daemon/services/readiness.py:883`) — **biasing toward a false-negative (under-reported leak)**.
**Decision:** **keep DO-NOT-TOUCH**, and **add a behavioural leg** comparing `leak_signal.{detected, checkedout_rising, streak}` and `pool_state_sqlalchemy` (`readiness.py:353`) **BASE vs HEAD under steady-state bridge traffic**, recording the **occupancy delta**.
**Fallback:** the diff assertion remains as a secondary; rejecting the behavioural leg leaves the leak oracle's sensitivity unmeasured, and that must then be disclosed as a residual.

## D-62 — W-11: the synthetic handler-load leg's admissibility is defined

**Authority:** REVIEWER (W-11). **Cross-map:** **amends D-37 / task 5.1**.
**Disposition:** ADOPTED (binding).
**Evidence (reviewer W-11):** nothing defined what makes the synthetic leg **admissible**; the in-progress repair already carries `modes.py` with **literal stack text** — a valid harness self-test, **not evidence a daemon blocked**. If synthesis is allowed, **G1's BASE-positive and G4's signal are satisfiable with ZERO load.**
**Decision:** (i) the leg **must drive REAL concurrent requests at the bridged endpoints**; (ii) captures used as evidence **must come from the daemon's own wedge-capture**; (iii) **synthesized stacks are admissible ONLY for harness self-test and must be labelled as such** — they may never satisfy G1 or G4.
**Fallback:** none — a synthetic-only leg turns G1 and G4 into tautologies.

## D-63 — W-2 / W-8: BFS latency stated measured-not-gated; P2's file bookkeeping made mechanical

**Authority:** REVIEWER (W-2, W-8). **Cross-map:** **amends G2 and phase2 exit criterion 6**.
**Disposition:** ADOPTED (binding).
- **W-2:** the floor is gated on the **flat(10)** leg only; **BFS(100) is the same endpoint and the slowest shape** (p50 858.7 ms / p95 1000.1 ms at BASE — `BASELINE.md:39`), with only its **rts** count fenced (G9). **Decision:** G2 states BFS latency is **measured-not-gated**, **with the reason** (the floor band is a flat-leg commission value; there is no commission BFS anchor, and inventing one would be a fabricated anchor).
- **W-8:** P2's round-1 exit criterion said "exactly **five** production files … plus **two constant lines** in `constants.py`" — **that lists six files**, and `constants.py` is also counted in P1's four. **Decision:** restate as **six files**, and make **additive-only mechanical**: `git diff -U0` over the touched files has **zero deleted lines**, and **both** P1 constant names are still present.
**Fallback:** W-2's alternative is a BASE-relative BFS p95 leg — **offered as optional**; if added, it passes on the same relative rule as G4 (HEAD ≤ BASE, no absolute anchor).

## D-64 — A-11: TB-8 (idempotent reads, no shared mutation) is adopted

**Authority:** REVIEWER (A-11, advisory — **adopted**). **Cross-map:** **extends D-35 (TB-1…TB-7)**.
**Disposition:** ADOPTED (cheap, and the hazard is real).
**Evidence (verified in-tree):** TB-7 governs **DB writes only**; nothing regulated **shared in-process state**. One real instance exists: `daemon/repositories/instance/repository.py:197` `_descendant_cap_warn_last_emit: dict[int, float] = {}`, mutated at `:1606-1608` including `.clear()` when `len > 256`. **Six threads can lose a throttle entry — log-rate only, never results.**
**Decision:** **TB-8 — a bridged callable must be idempotent under concurrent execution and must not mutate shared in-process state.** The known instance is **accepted as a disclosed log-rate-only waiver** (documented at the mutation site and in the module docstring); any *new* shared mutation in a bridged callee REDs.
**Fallback:** if the leader prefers strictness, the `_descendant_cap_warn_last_emit` waiver is withdrawn and the BFS span's throttle must become thread-safe — which touches a DO-NOT-TOUCH file (`instance/repository.py`), so the waiver is the cheaper correct answer.

## D-65 — A-1 … A-10: textual and cheap fixes folded (table)

**Authority:** REVIEWER (advisories; PLANNER judgement to fold). **Cross-map:** **A-1…A-10**.
**Disposition:** **ALL TEN FOLDED** — each is text-level and none contradicts a C/W fix.

| A | Fix folded | Where |
|---|---|---|
| **A-1** | T-3 gets a **named owner**: a backlog board item (`wi-3f5bea95` backlog; owner = the board, re-entry rule as entry 19) — it was the only deferred item with no owner | plan-overview routed-out entry 19 + phase3 3.8 + phase5 5.7 |
| **A-2** | **G5 non-vacuity:** `9 fenced entries = 8 compared shapes + 1 detail endpoint` (the landed instrument form, `main.py:787-800`; D-68's `== 8` named the shape count and the round-1 `== 7` was a miscount — both superseded, W1-fold, so a stale fence value cannot be read as the fence) and **≥1 request per shape**. **G8 non-vacuity:** `len(selected) == <declared N>` asserted **in the pack file** | plan-overview G5/G8 rows |
| **A-3** | G2's diagnostic repointed at **`LIMITS_DB_OFFLOAD_MAX_WORKERS`** (6 is already the default; "raise to 6" was incoherent) — phase4 4.4 aligned | plan-overview G2 + phase4 4.4 |
| **A-4** | **New task 3.0** promotes 3.2's step (1) (`_batch_instances` returns `(rows_by_id, degraded)`) so each call site is written **once** and no signature changes after its first new caller | phase3 task 3.0 |
| **A-5** | P1 exit criterion 6 re-worded: "**no bridged call site** changed" (the phase does change `daemon/api.py`) | phase1 exit criterion 6 |
| **A-6** | G2's fallback wording: **"commission-mandated, annotated unverified, never re-baselined"** — now the standing position (aligns with RULING 2 / D-51) | plan-overview G2 + provenance note |
| **A-7** | The "~1,400 executor **sites**" label corrected: **~1,400 lines / 1,401 occurrences / 1,066 invocation sites** at HEAD. The 1,351 @ `6bebb9ba0` drift claim stays (it was exact) | plan-overview Research Insights + phase1 1.8(a) |
| **A-8** | **No chunking** — one sentence added: a chunking implementation **REDs the statement-count pin by design** | plan-overview §Implementation + phase2 2.1 |
| **A-9** | Pin 3.9 gains a **mixed non-degraded batch node**: present rows keep their Instance fields, the orphan keeps the mirror, `instance_absent` is True **only** for the orphan | phase3 3.9(a/f) |
| **A-10** | "six kwargs" → **five** (`work.py:181-187` really carries five: `project_id`, `instance_id`, `status`, `kind`, `root_only`) so nobody hunts a sixth; `queues.py:652` → **`:651`** (the comment opens at `:651`) | plan-overview site table + phase4 4.2 |

**Not folded:** none.
**Fallback:** n/a — all textual.

---

## ROUND 2 SUMMARY TABLE

| ID | Decision | Authority | Cross-map | Amends |
|---|---|---|---|---|
| **D-42** | Pre-conversion slice restructured + specified; `:1520` = comparator contract; stable tie-break; post-conversion-filter disclosure | REVIEWER | C-1 | D-30 |
| **D-43** | Gate: permits == workers; release in future done-callback; pins (ix)/(x) | REVIEWER | C-2 | D-26/B4 |
| **D-44** | Four exact-count packs re-derived with an adjudication rubric; named in P3 exit + P5 5.4 | REVIEWER | C-3 | — |
| **D-45** | G4 PASS condition (e): `STALL_HEAD_synth == 0 AND STALL_BASE_synth > 0` | REVIEWER | C-4 | D-36/G4 |
| **D-46** | G6: collected-count, digests, collection-error=FAIL, pre-agreed async-hang fallback | REVIEWER | C-5 | D-36/G6 |
| **D-47** | Legacy-mirror re-anchored to `:951` + `:153` + `:384-405`; observable defined | REVIEWER | C-6 | G10, 3.9(b) |
| **D-48** | Arena-local boot smoke with a bridged read in flight at SIGTERM + fences | REVIEWER | C-7 | — |
| **D-49** | Three commit units; `git add -A` banned; tree-sha + porcelain asserts; G12 → attribution | **LEADER RULING 3** | C-8 | D-37 |
| **D-50** | G1 three-part + attribution table + **the residual set as TWO CLASSES (four on-loop wedge residuals + one executor-class measured shape — G9 non-regression only; taxonomy final round 3.5)** + "scoped closure, never class-closure" | **LEADER RULING 1** | W-1 | D-36/G1 |
| **D-51** | G2 anchors stand, annotated `commission-body, unverified`; better→PASS annotated, worse→FAIL | **LEADER RULING 2** | A-6/W-2 | D-36/G2 |
| **D-52** | Re-entrancy guard by thread identity; `CancelledError` re-raised first; pin (xi) | REVIEWER | W-13, W-14 | D-26/B1,B2 |
| **D-53** | Four-step drain + `DB_OFFLOAD_DRAIN_BOUND_S`; waiter counting as the alarm | REVIEWER | W-12, W-15 | D-24, D-26 |
| **D-54** | Detail path via `list_child_ids_for_parents([id])`; `list_child_ids_permanent` forbidden; four co-callers named | REVIEWER | W-9 | D-34 corr.5 |
| **D-55** | Lazy `configure()` in lifespan; subprocess pin; constant names aligned | REVIEWER | W-10 | D-26/B10 |
| **D-56** | G1 mechanised (captures≥1 ∧ hits==0; 0 ⇒ INCONCLUSIVE; BASE artifact by path+sha); G3 three conjuncts; G9 totals frozen pre-run | REVIEWER | W-3, W-4, W-5 | D-36 |
| **D-57** | G11 restated vs live **v0.27.12**; v0.27.13 superseded; rollback target v0.27.12 | REVIEWER | W-6 | G11 |
| **D-58** | G7 scoped to authored `.env` content; machinery disclosed + sha-identical | REVIEWER | W-7 | G7 |
| **D-59** | Source-scan pin over `daemon/` for every `offload(` site; run at P5 | REVIEWER | W-17 | D-26/1.11 |
| **D-60** | Forward the CLAMPED limit; presence-only detection; `?limit=10000` pin | REVIEWER | W-18 | D-8/3.4 |
| **D-61** | Readiness behavioural leg (leak_signal + pool_state BASE vs HEAD under bridge traffic) | REVIEWER | W-16 | D-23 |
| **D-62** | Synthetic leg admissibility: real requests, daemon's own captures; synthesized = self-test only | REVIEWER | W-11 | D-37 |
| **D-63** | BFS latency measured-not-gated (reason stated); P2 six-file + additive-only mechanical | REVIEWER | W-2, W-8 | G2, P2 exit |
| **D-64** | **TB-8** idempotent reads / no shared mutation; the `_descendant_cap_warn_last_emit` waiver disclosed | REVIEWER (A-11) | A-11 | D-35 |
| **D-65** | A-1…A-10 textual fixes (all ten folded) | REVIEWER / PLANNER | A-1…A-10 | various |

## Open items after round 2

**Closed this round:** the reviewer's "Leader decisions required" #1 (W-1 → **RULING 1 / D-50**), #2 (A-6 → **RULING 2 / D-51**), #3 (C-8 → **RULING 3 / D-49**), #4 (C-5 pre-agreed fallback → **D-46**).

**Still open (1) — leader only:** the **D-19 funding question** (is the deferral contract funded? R2 says no → the label is "Option 1 + A-batches"). Unchanged by round 2; W-17's source-scan pin (D-59) reduces — but does not remove — the need for the unfunded production registry.

**Disclosed-inherited (not verified, per the reviewer's "Gaps/unverified"):** the five "measured" pre-fold numbers in D-29/D-30 (8/10, 76/100, 34/100 patch-ups; 49/179 rts) are **commission-body/architect-inherited**, not re-derivable from committed `BASELINE.md`; the G2 anchors (D-18/D-51) are commission-body by admission; the post-lane per-shape rts projections are arithmetic over BASE.

---

# FOLD ROUND 3 — APPROVER REJECT (B1–B5) + REVIEWER DELTA REJECT (F-1…F-5) CLOSED (D-66 … D-78)

**Inputs (both read in full):** `.agents/approver/eventloop-sync-db-hotpaths-tracking.md` (iteration 001, **REJECT** — B1–B5 + 12 non-blocking notes + verified positives) and `.agents/reviewer/RESULTS/2026-10-30-wi-3f5bea95-round2-delta-repair-review.md` (**PLAN = REJECT (narrow)**: F-1/F-3/F-4/F-5 + RULING-1 conjunct + G11 D-13 line + HEAD-PIN propagation + C-7 one-liners; **REPAIR = REJECT** — 3 blocking, **tester increment #2's domain, NOT this fold's**). **Round-2 scoreboard taken as read:** C-4, C-5, C-6, C-8 and RULING 2/3 CLOSED; C-1/C-2/C-7 and RULING 1 PARTIAL; C-3 NOT CLOSED; HEAD-PIN PARTIAL; G11 one line.

**Prior decisions amended in this round** (dated stamps applied in-place above; this table is the index):

| Prior | Amended by | What changed |
|---|---|---|
| **D-26/D-43 (executor call form + gate)** | **D-66, D-67** | the seam is specified on `Executor.submit` + `asyncio.wrap_future` with the release on the **concurrent** future; the gate is a **custom primitive set**, not `asyncio.Semaphore` |
| **D-34/§5.2 + D-36 (G5)** | **D-68 + LEADER RULING** | G5 re-spec: pinned fixture clock, `gate_g5` normalizes-then-re-hashes, `compared_shapes == 8`, detail request named; **`jobs_active_badge` joins the ENUMERATED RESIDUALS** |
| **D-29/D-44 (patch-up + pack re-derivation)** | **D-69, D-71** | the patch-up gains a **fourth batch** (task-timing) behind `ensure_job_ids`; task 3.10's counts were **all wrong or inapplicable** — rewritten |
| **D-63 (P2 additive-only)** | **D-70** | "ZERO deleted lines" re-scoped to the listed edit regions |
| **D-42 (slice restructure)** | **D-72** | the Job-branch `root_only` child filter stays **PRE-accumulation**; delivered-count pin; `_parse_iso_datetime` step |
| **D-50/D-56 (G1 predicate)** | **D-73** | the mechanised predicate gains the **attribution conjunct** |
| **D-48 (boot smoke)** | **D-74** | "bridged read in flight" operationalised; `DB_OFFLOAD_DRAIN_BOUND_S` numeric default with the 90 s arithmetic |
| **D-56 (G9 frozen totals)** | **D-75** | a **unit class** is declared beside every frozen total; units normalized at the gate boundary |
| **D-13 / D-12 / D-36 / D-37** | **D-76, D-77** | dated corrections/stamps (live baseline; repair landed; increment #2 in flight) |
| **D-47** | **D-78** | D-47's own `:153` bullet corrected (no `if work_record is None:` there) |

## D-66 — AB-1 / M-1 / F-3: the seam's executor call form is NAMED; the release rides the CONCURRENT future; pin (x) is strengthened

**Authority:** APPROVER (B1) + REVIEWER (C-2 PARTIAL / F-3). **Cross-map:** approver **B1**, reviewer **F-3**; **amends D-26 (B4 form) and D-43**.
**Disposition:** ADOPTED (binding) — as written, phase1 task 1.1 *contradicted* D-43.

**Evidence:** task 1.1 prescribed `loop.run_in_executor(EXECUTOR, ctx.run, partial(...))`. That returns the **asyncio wrapper future**, whose done-callback fires **at cancellation time while the thread still runs** ⇒ implementing D-43's "release in the future's done-callback" on the wrapper future is **the over-release hazard D-43 exists to close**. Reviewer: "pin (x) as worded passes under the wrong implementation; the claimed `finally`-release mutation cannot redden it."

**Decision (folded into phase1 task 1.1):**
1. The seam submits **directly to the executor**: `_EXECUTOR.submit(ctx.run, functools.partial(fn, *args, **kwargs))` — a **`concurrent.futures.Future`**.
2. `fut.add_done_callback(release)` is attached to that **CONCURRENT** future — thread-side truth, fires exactly when the work item finishes, regardless of the await's fate.
3. The caller awaits **`asyncio.wrap_future(fut)`**. **`loop.run_in_executor` is NEVER used on the seam path** (its asyncio wrapper cancels with the awaiting task).
4. **Pin 1.9(x) STRENGTHENED (M-1):** not merely "permits return to full and the seam still completes" — it must assert **`max concurrent thread executions ≤ W` under a cancellation storm** (spawn N > W awaits, cancel them mid-flight, and prove the pool never ran more than W bodies at once). The round-2 pin passed under the wrong implementation; the storm form reddens it.

**Fallback:** none — the substitution is the only form whose done-callback has the semantics D-43 requires.

## D-67 — AB-2: the admission gate is a CUSTOM primitive set (permit counter + waiter deque), with an atomic submit/close protocol and defined post-close errors

**Authority:** APPROVER (B2 — both workers converged). **Cross-map:** approver **B2**; **amends D-43 (gate form) and D-53 (drain step 2)**.
**Disposition:** ADOPTED (binding).

**Evidence:** task 1.4 named `asyncio.Semaphore`, which (i) **cannot fail parked waiters** (drain step 2, D-53), and (ii) **exposes no waiter count** (the D-53/W-15 alarm, pin (xiii)). Independently: a non-atomic closed-flag check vs `submit` lets a late submission hit `shutdown(wait=False, cancel_futures=True)` ⇒ the caller sees `RuntimeError: cannot schedule new futures after shutdown` instead of the defined error, and `cancel_futures=True` propagates `CancelledError` instead of the uniform contract error.

**Decision (folded into phase1 task 1.4):**
1. **Custom gate:** a permit counter + a `collections.deque` of `asyncio.Future` (the waiter queue). `acquire()` parks a future when permits == 0; `release()` (invoked from the **concurrent** future's done-callback, D-66) increments and wakes the head waiter. **Waiter count is a first-class attribute** — the alarm reads it directly.
2. **Atomic submit-vs-close protocol:** **register-then-check under the gate** (the waiter future is enqueued before the closed flag is consulted; close drains the remaining queue) — so `close()` and a late `acquire()` interleave safely: the late waiter receives the **defined error**, never a `RuntimeError` from a shut-down executor and never a raw `CancelledError`.
3. **Explicit post-close error mapping:** every path after close maps to **the defined uniform error** (the same one new submissions get); `cancel_futures=True` is never allowed to surface as the caller-visible exception.
4. Pins: the **waiter-failure pin (xiii)** now has a real primitive to exercise; a new node asserts **late-submit-after-close ⇒ the defined error** (not `RuntimeError`).

**Fallback:** none — `asyncio.Semaphore` cannot express drain-step-2 or the waiter alarm; the custom gate is the minimal primitive set that can.

## D-68 — AB-3 + LEADER RULING: G5 is re-specified, and `jobs_active_badge` joins the ENUMERATED RESIDUALS (8 shapes canonical)

**Authority:** APPROVER (B3) + **LEADER RULING (round 3)**. **Cross-map:** approver **B3**; **amends D-34/§5.2, D-36/G5, and D-50 (the residuals list)**.
**Disposition:** ADOPTED (binding).

**Evidence (approver B3):** G5 was **unsatisfiable AND unmechanised** — (a) the middleware hashes raw bytes with **no normalization hook**; (b) the fixture stamps `created_at`/`updated_at`/`last_activity_at` with `datetime.now(timezone.utc)` (`seed.py:116`, `:244`) ⇒ BASE vs HEAD bodies cannot match even for identical code; (c) **no `gate_g5` exists** and no `compared_shapes == 7` assertion anywhere; (d) instrument SHAPES has **8** entries — `jobs_active_badge` was omitted from the frozen 7, from the §(d) table and from the G9 targets, **and it is a jobs shape with NO `limit` param**, so C-4 cannot bound its enrichment.

**Decision:**
1. **Fixture clock PINNED** (`seed.py`): the three stamp sites use a fixed instant — normalization of the stamp is a **tester-lane harness change (increment #2 scope)**, coordinated, not a plan-time guess.
2. **`gate_g5` performs its own children-order body normalization, then RE-HASHES BOTH SIDES post-processing** and compares the post-normalization hashes (hash equality ⇒ byte equality of the normalized bodies). The middleware's raw hash remains a **diagnostic**, never the gate.
3. **The shape canon is 9 FENCED ENTRIES: 8 compared SHAPES + 1 detail endpoint** (`/api/instances/{id}`) — the landed `gate_g5` fences 9 (`main.py:787-800`); **the instrument is the stricter and is what runs** (W1-fold/iteration-003 restatement — D-68's `compared_shapes == 8` named the shape count, the round-2 `== 7` was a miscount; both are superseded by the 9-entry form, so plan and instrument cannot disagree).
4. **§(e)'s detail request is NAMED:** `/api/instances/{id}` is an explicit entry in the compared set.
5. **LEADER RULING (binding) — `jobs_active_badge` joins the ENUMERATED RESIDUALS (G1's list and the §(d) table):** the shape has **no `limit` param**, and bounding its enrichment would require a **server-side default cap**, which **RULING Q1 forbids** ("no server-side default cap" is the Q1-ii invariant D-8 preserves). Therefore: its **rts are MEASURED**, and it carries a **NON-REGRESSION gate only** (`rts_HEAD ≤ rts_BASE`), never a frozen G9 target. It is quoted as a residual with the reason, so a green G1/G9 can never be read as covering it.

**Fallback:** excluding the shape from the compared set with a recorded reason was available and rejected — measuring it costs nothing and keeps the residual honest.

## D-69 — AB-4: the patch-up gains a FOURTH batch (task-timing) behind `ensure_job_ids`; pins become FIELD-LEVEL

**Authority:** APPROVER (B4). **Cross-map:** approver **B4**; **amends D-29 (the three-batch recipe) and phase3 task 3.5**.
**Disposition:** ADOPTED (binding) — the round-2 recipe **broke byte-identity**.

**Evidence:** `_job_to_record` renders `started_at`/`completed_at` from **`task_timing`** (`work_resolver.py:1466`/`:1477`; helpers `:875-882`/`:903-913`), and `_task_started_at(None) → None`. The stated three-batch recipe (job_ids IN-select + instance batch + mission batch) **omits the per-page task-timing batch** — `get_timing_by_work_ids`, **already at `work_resolver.py:1440-1445`** ⇒ repaired page jobs render **null timestamps where BASE rendered real ones**. Separately: no owner/entry point existed (no JobItem get-by-ids method; adding one would breach exit criterion 8's three-file boundary).

**Decision (folded into phase3 task 3.5):**
1. **FOURTH batch = task-timing**: `get_timing_by_work_ids` over the patch-up's job-id set (no new repository method — it already exists).
2. **Entry point:** the patch-up lives **inside `list_work` behind an optional `ensure_job_ids` parameter** (a `set[work_id]` the router passes when it sent an explicit limit). **No new JobItem get-by-ids method** — exit criterion 8's three-file boundary holds.
3. **Pins become FIELD-LEVEL equality** for repaired page jobs: `started_at`, `completed_at`, `result_summary`, `mission_ref` each asserted equal to BASE — **not merely `work_record is not None`** (non-null is the failure mode B4 caught: present-but-null timestamps).

**Fallback:** none — without the fourth batch the gate recipe itself changes bytes.

## D-70 — AB-5: P2 exit criterion 6 is re-scoped (additive-only applies to the listed edit regions)

**Authority:** APPROVER (B5). **Cross-map:** approver **B5**; **amends D-63/W-8**.
**Disposition:** ADOPTED (binding).

**Evidence:** "git diff -U0 shows ZERO deleted lines over the six touched files" is **unsatisfiable** — tasks 2.2 (replace the per-row loop), 2.3/2.4 (wrap two manager calls) and 2.6(c) (add the explicit argument at two call sites) **each delete or modify lines** ⇒ ≥5 deletions for any correct implementation. A mechanical reviewer REDs a correct phase or induces a falsified proof.

**Decision (folded into phase2 exit criterion 6):** additive-only is scoped to **`daemon/constants.py`** (the two named-pair lines, still both P1 constant names present) + the **untouched `list_child_ids_permanent` body**; and "ZERO deleted lines" becomes **"no deletions outside the listed edit regions"** (the regions are enumerated in the criterion).

**Fallback:** none.

## D-71 — F-4: task 3.10 is REWRITTEN — every round-2 stated count was wrong or inapplicable

**Authority:** REVIEWER (C-3 **NOT CLOSED** / F-4 — the round's one new critical). **Cross-map:** reviewer **F-4**; **amends D-44**.
**Disposition:** ADOPTED (binding) — implementing 3.10 as written **actively damages tests**.

**Evidence (reviewer, re-verified in-tree):**
1. **All four previously-named packs are `kind="job"` fixtures — the Task branch never runs** (`work_resolver.py:1363`). Therefore `test_work_resolver_query_budget.py` `:295` **STAYS 2**, `:324` **STAYS 1**, `:410` **STAYS 0**; `test_fix_c_read_model_split.py:750` **STAYS ≤5**. Round-2's `==3` / `≤6` were a deterministic RED and a silent weakening — the rubric's own discipline forbids both.
2. **`test_mission_resolver.py:770/:998` are DROPPED** from the sweep: they exercise `MissionResolver._batch_instances` (`mission_resolver.py:787`) — a **different class this lane never touches**.
3. **`test_job_queue_proxy_phase1.py:703/:734` are DOCSTRING lines** — the round-1 phantom-anchor defect class **reintroduced by effect**. The real asserts are **`:788` (`<= 2`) and `:863` (`<= 2`)** — **unchanged** by this lane.
4. **The packs that CAN move are the task-bearing ones** (`kind="report"` and union fixtures) — named nowhere in round 2.

**Decision (folded into phase3 task 3.10):**
- The four `kind="job"` packs keep their **current expectations verbatim** (2 / 1 / 0 / ≤5) — the task's job for them is to **prove they do not move** (a guard, not a re-derivation).
- The MissionResolver pack is **removed** with the reason recorded.
- Pack 4 is **re-anchored to `:788`/`:863`**.
- A **task-bearing sweep** is ADDED: the `kind="report"`/union fixtures that DO exercise the new Task-branch batch, each with its expected delta (`+1` instance-batch statement per request on task-bearing shapes — one new call site on a different population, no per-row growth ⇒ *legitimate* under the D-44 rubric).
- **The union-batch alternative is acknowledged and moves nothing**: serving the union population with one batch would leave the job-only fixtures' counts identical — recorded so nobody "fixes" the counts to match an implementation that was not chosen.

**Fallback:** none — the numbers are facts about the fixtures, not choices.

## D-72 — F-1 / M-2: the Job-branch `root_only` child filter stays PRE-accumulation; a delivered-count pin is added; the ISO-key step is stated

**Authority:** REVIEWER (C-1 PARTIAL / F-1). **Cross-map:** reviewer **F-1**; **amends D-42**.
**Disposition:** ADOPTED (binding).

**Evidence:** D-42 specified the `source_rows` accumulation order and the tie-break but left the Job-branch **`root_only` child filter (`work_resolver.py:1469`) placement unspecified**. A post-slice placement **silently under-delivers** for the production caller that runs `kind="job"`, `root_only=True`: the slice would be taken over an unfiltered population, then the filter would drop rows ⇒ the page's own jobs arrive missing from the delivered top-N. No pin covered the **delivered population**. Minor: Job rows carry `created_at` as **ISO strings** — the accumulation key needs the `_parse_iso_datetime` step, which was never stated.

**Decision (folded into phase3 task 3.3):**
1. The Job-branch `root_only` child filter stays **PRE-accumulation** (applied while building `source_rows`, before the sort) — so the sliced population is the filtered population the production caller sees.
2. A **delivered-count pin** is added: for a `kind="job"`, `root_only=True` fixture with ≥ limit matching rows, **HEAD delivers exactly the same top-N work_ids as BASE** (delivered population equality, not just statement counts).
3. The accumulation key is stated: `_normalize_sort_key(_parse_iso_datetime(row.created_at))` — Job rows' ISO strings are parsed, not compared raw (mirroring `:1520`'s comparator).

**Fallback:** none — the other placement is the under-delivery bug.

## D-73 — M-3: the G1 mechanised predicate gains the ATTRIBUTION conjunct

**Authority:** REVIEWER (RULING 1 PARTIAL). **Cross-map:** reviewer **M-3**; **amends D-50 (b) and D-56 (W-3)**.
**Disposition:** ADOPTED (binding) — one line, closes a false-green.

**Evidence:** the gate row carried all five RULING-1 elements, but the mechanised predicate read `captures >= 1 AND loop-section-hits == 0 ⇒ PASS` — **omitting attribution**, so an unattributable capture with `hits == 0` PASSes while clause (b) calls the identical case a **FAIL**. That is a false-green path on a **new** on-loop wedge site — the exact class the ruling exists to close.

**Decision (folded into plan-overview G1):** **PASS ⟺ `captures ≥ 1 ∧ loop-section-hits == 0 ∧ ALL captures ATTRIBUTED to enumerated routed-out sites`**; an unattributable capture is a **FAIL**. The attribution table deliverable (D-50 (c)) is the evidence artifact that makes the third conjunct checkable.

**Fallback:** none.

## D-74 — M-6: the boot smoke's "bridged read in flight" is operationalised; `DB_OFFLOAD_DRAIN_BOUND_S` gets a NUMERIC default with the 90 s arithmetic

**Authority:** REVIEWER (C-7 PARTIAL). **Cross-map:** reviewer **M-6**; **amends D-48 and D-53**.
**Disposition:** ADOPTED (binding).

**Evidence:** "SIGTERM with a bridged read in flight" was **prose, not a predicate** (no deterministic recipe, no was-open-when-SIGTERM assertion); and `DB_OFFLOAD_DRAIN_BOUND_S` had **no numeric default** — an 80 s pick already violates the "⊂ `TimeoutStopSec=90`" arithmetic, and "+ margin" was undefined.

**Decision (folded into phase5 task 5.11 and phase1 task 1.2):**
1. **Deterministic in-flight recipe:** before SIGTERM, issue a bridged read known to outlive the signal (the C-3 defer-blocked resolve at ~729 ms is the natural one); the smoke asserts a **marker proving the read was OPEN when SIGTERM arrived** (a started-not-finished assertion — e.g. the callee sets a flag on entry and clears it on exit; the smoke requires flag==set at signal time) — never "we sent traffic and it seemed fine".
2. **Numeric default: `DB_OFFLOAD_DRAIN_BOUND_S = 10`**, with the arithmetic stated in the artifact: worst-case in-flight (C-3 resolve ≈ 0.73 s; every other shape ≪) + waiter-failure propagation (ms) + executor `shutdown()` call ≈ **≤ 2 s observed**, leaving **≥ 80 s of margin** inside the unit's 90 s — and 10 s is itself an order of magnitude above the worst in-flight item, so the bound binds on pathology, not on load. (An 80 s pick fails the arithmetic; 10 s is the stated default, env-overridable per B10.)

**Fallback:** if the leader prefers a different number, the constraint is `bound + worst-case in-flight ≤ 90 s with stated margin` — the arithmetic, not the constant, is the binding part.

## D-75 — F-5 / cross-axis: a UNIT CLASS is declared beside every frozen G9 total; units are normalized at the gate boundary

**Authority:** REVIEWER (F-5 + the cross-axis note, both verdicts). **Cross-map:** reviewer **F-5**; **amends D-56 (W-5)**.
**Disposition:** ADOPTED (binding).

**Evidence:** the same defect visible from both sides — the harness compares **sample-summed** `statements_by_shape` against **per-request** rules (repair Blocking 1; the R1 REJECT), while the plan's G9 row froze "round-trip totals" **without declaring the unit class** (per-request constant vs sample-summed count). A frozen number with an unstated unit is not a bound; it is a coin-flip at compare time.

**Decision (folded into plan-overview G9 + §(d)):**
1. Every frozen G9 total carries its **unit class** in place: **`[per-request]`** (a constant of the request shape — the plan's native unit) or **`[sample-summed]`** (an aggregate over N samples — the raw instrument output). The §(d) table rows are all **`[per-request]`**.
2. **Units are normalized at the gate boundary** — the harness-side division by sample count (or publication of a per-request variant) is **tester increment #2 scope**, coordinated with this declaration; the plan declares, the instrument normalizes, the gate compares like with like.
3. The `jobs_active_badge` row is `[per-request]`, measured, **NON-REGRESSION only** (D-68 RULING).

**Fallback:** none — this is the cross-axis half of the same fix the repair must land; folding only one side re-opens the reject.

## D-76 — M-4: D-13's live-baseline line is corrected IN PLACE with a dated stamp (applied above)

**Authority:** REVIEWER (G11 NOT-CLOSED, one line). **Cross-map:** reviewer **M-4**; **amends D-13**.
**Disposition:** ADOPTED (binding). `decisions.md:159` said "vs live v0.27.13" **inside the rollback-safety adjudication itself**. The dated correction (2026-10-30) now reads the operative sentence as **"migration delta vs LIVE `v0.27.12`: EMPTY (115 == 115, re-derived at stage)"**, preserving the round-1 sentence verbatim as unamended history. No silent edit.

## D-77 — M-5: the HEAD-PIN retraction is stamped everywhere it was still stale (applied above; phase5 next in this fold)

**Authority:** REVIEWER (HEAD-PIN PARTIAL). **Cross-map:** reviewer **M-5**; **amends D-12, D-36, D-37**.
**Disposition:** ADOPTED (binding). Dated 2026-10-30 stamps now mark: the harness repair **landed at `c3d87af26`** (mode switch, loop-thread scoping, one-sided G2, per-shape G9, paired G4, synthetic leg, collection fences); **`grep -c c3d87af26 decisions.md` > 0**; and the **R1 council REJECT with 3 blocking findings** (units / reference provenance / truth-table honesty) with **tester increment #2 IN FLIGHT** — so no present-tense "cannot express a HEAD run" claim survives anywhere in the set, and the governance (review before any HEAD run; BASE-still-red) is restated at each stamp.

## D-78 — Cheap notes bundle (all folded; includes the C-6 🟢, the C-5 🟢 and the C-8 🟢 ride-alongs)

**Authority:** APPROVER (non-blocking notes) + REVIEWER (🟢 items, INCONCLUSIVE wording, citation drift). **Disposition:** ALL FOLDED.

| Note | Fold |
|---|---|
| **P1 exit criterion 3 vs task 1.3 wording** (`api.py` "drain call only" vs `configure()` in the same lifespan edit) | phase1 exit criterion 3 re-worded: "the lifespan edit only (`configure()` at start, `shutdown()` at end)" |
| **`configure()` un-configured/idempotent contract** | phase1 task 1.3: `_EXECUTOR is None` ⇒ defined error **or** lazy init (one form, stated); a **second `configure()` must NOT leak the prior executor** (shut it down or refuse) |
| **Re-entrancy pin vector** | phase1 task 1.5: the pin drives the nested case via **`asyncio.run` inside the worker** — the D-52 detection vector; `run_coroutine_threadsafe` stays forbidden by **TB-5 discipline**, not by code |
| **`_batch_instances` empty-input early return** (`work_resolver.py:2408-2409`) | phase3 task 3.0: returns **`({}, False)`** — degraded is `True` **only** on the SQLAlchemyError path |
| **Task 2.4 `by_parent.get(instance_id, [])`** | phase2 task 2.4: a childless instance yields `[]`, matching BASE |
| **C-1 batched children SELECT shares D-B's observation window** | phase2: one disclosure sentence (mirrors risk 7) — a row committed between the batch and assembly is missed exactly as at BASE |
| **Router-side session rule** | plan-overview §Implementation: "**no caller holds a session across `await offload(...)`**" stated beside TB-2/TB-3 (a caller-side rule, not only callee-side) |
| **G9 "frozen" ranges → exact or one-sided** | plan-overview G9: `4–5` → **`≤ 5`**, `~8` → **`≤ 9`**, `~6–10` → **`≤ 10`**, each `[per-request]` (D-75); "89 unchanged" stays exact |
| **INCONCLUSIVE wording** | plan-overview: INCONCLUSIVE is a **gate-verdict label**; the tester increment maps it to a code state (non-zero exit with a distinct code) — the plan does not invent the code |
| **Citation drift set** | plan-overview: D-B sites cited as **`:1779`/`:1847`/`:1878`** (approver-verified) with the `:1778-1779`/`:1846-1847` pair-forms noted once; **`jobs_crud.py:942-943`/`:950-951`** both forms; SQLAlchemyError path **`:2416-2432`**; **`manager.py:2785-2799` resolves to `_session_scope`** (a scope provider, not a "write scope") — **the TB-6 argument survives** and is stated; `constants.py` env helpers **`:420-428`** |
| **C-6 🟢 (D-47's own `:153` bullet)** | corrected in place above (no `if work_record is None:` at `:153`); **`technical-analysis.md:121/:211` + `decisions.md`'s D-5 hazard paragraph retain the `:900-902` phantom as UNAMENDED HISTORY — deliberately not edited** (it is a read-only primary input; the round-2 G10/G6/G12 surfaces carry the corrected anchors) |
| **C-5 🟢 (dated "fallback ratified" slot)** | phase5 task 5.10 gains a dated slot for the leader's ratification of the pre-agreed async-hang fallback |
| **C-8 🟢 (5.8 wording + DO-NOT #17 boundary)** | phase5 5.8: "checked in the commit history" → **per-commit path-set ⊆ class** (verifiable from each commit's diff); DO-NOT #17 names its **file-list boundary** (the six plan files + decisions.md; **BASELINE.md is exempt — already committed at `42764588c`**, as are the harness files at `c3d87af26`) |
| **Task 4.3 "Depends On" cosmetic** | phase4 task 4.3 — **verified already `P1`; no change required** (recorded so the note cannot recur unverified) |

**Not folded:** nothing. **Fallback:** n/a.

---

## ROUND 3 SUMMARY TABLE

| ID | Decision | Authority | Cross-map | Amends |
|---|---|---|---|---|
| **D-66** | Seam = `Executor.submit` + `add_done_callback` on the **concurrent** future + `asyncio.wrap_future`; pin (x) asserts **max concurrent executions ≤ W under a cancellation storm** | APPROVER B1 + REVIEWER F-3 | B1, F-3, M-1 | D-26, D-43 |
| **D-67** | Custom gate primitives (permit counter + waiter deque, release in done-callback, atomic register-then-check close, defined post-close errors) | APPROVER B2 | B2 | D-43, D-53 |
| **D-68** | G5 re-spec (pinned clock, gate_g5 normalize-then-rehash, `compared_shapes == 8`, detail request named) + **LEADER RULING: `jobs_active_badge` → ENUMERATED RESIDUALS, measured, NON-REGRESSION only** | APPROVER B3 + **LEADER** | B3 | D-34, D-36/G5, D-50 |
| **D-69** | Fourth batch = task-timing (`get_timing_by_work_ids`); patch-up in `list_work` behind `ensure_job_ids`; **field-level** pins | APPROVER B4 | B4 | D-29 |
| **D-70** | P2 additive-only re-scoped to the listed edit regions | APPROVER B5 | B5 | D-63 |
| **D-71** | Task 3.10 rewritten: job-only packs keep 2/1/0/≤5; MissionResolver pack dropped; pack 4 → `:788`/`:863`; task-bearing sweep added; union-batch alternative acknowledged | REVIEWER F-4 | C-3, F-4 | D-44 |
| **D-72** | Job-branch `root_only` child filter **pre-accumulation**; delivered-count pin; `_parse_iso_datetime` key step stated | REVIEWER F-1 | C-1, F-1, M-2 | D-42 |
| **D-73** | G1 predicate gains **ALL captures ATTRIBUTED** as a conjunct | REVIEWER M-3 | RULING 1 | D-50, D-56 |
| **D-74** | In-flight-read recipe (started-not-finished marker) + **`DB_OFFLOAD_DRAIN_BOUND_S = 10`** with the ⊂90 s arithmetic | REVIEWER M-6 | C-7 | D-48, D-53 |
| **D-75** | Unit class beside every frozen G9 total; units normalized at the gate boundary (increment #2 scope) | REVIEWER F-5 + cross-axis | F-5 | D-56 |
| **D-76** | D-13 live-baseline dated correction → **v0.27.12** | REVIEWER M-4 | G11 | D-13 |
| **D-77** | HEAD-PIN retraction stamps (`c3d87af26` citations; increment #2 in flight) across decisions + phase5 | REVIEWER M-5 | HEAD-PIN | D-12, D-36, D-37 |
| **D-78** | Cheap notes bundle (13 items incl. the C-5/C-6/C-8 🟢 ride-alongs) | APPROVER + REVIEWER | non-blocking | various |

## Open items after round 3

**Closed this round:** approver B1–B5 (all five, via D-66…D-70); reviewer F-1/F-3/F-4/F-5, M-1/M-2/M-3/M-4/M-5/M-6, the RULING-1 conjunct, the G11 D-13 line, and the C-5/C-6/C-8 🟢 ride-alongs.

**Still open — tester increment #2 (NOT this fold's domain; the plan's obligations toward it are folded):** repair Blocking 1 (unit normalization + a mutation-meaningful unit selftest leg), Blocking 2 (`--reference` provenance asserts: `reference.mode == "base"` and `simulation.applied` absent/false), Blocking 3 (complete + honest truth table; commit the BASE-red artifacts), plus the G5/fixture-clock and shape-canon coordination named in D-68/D-75 and the INCONCLUSIVE code-state mapping (D-78).

**Still open — leader only (unchanged):** the D-19 funding question.

**Ride-alongs recorded, not blocking:** W-1 (G4 noise floor ~0.9 % same-tree false-red), W-3 (synthetic leg activity-fenced only; captures not attributed to the leg window), W-4 (simulated runs print `verdict: PASS` — the CLI must refuse; README wording), W-5 (capture non-vacuity aggregate, not per-capture). All four are **tester increment #2 scope**; the plan cites them so the increment's acceptance can check them.

---

# FOLD ROUND 4 — APPROVER ITERATION-002 REJECT CLOSED (D-79 … D-81)

**Input:** `.agents/approver/eventloop-sync-db-hotpaths-tracking.md` §Iteration 002 — **REJECTED** (2 blocking from W-core; W-phases APPROVED). Both blockers are **Phase-1 seam-spec edits touching no later-phase contract**. This round is MINIMAL by design: iteration 003 is the final approver call before the cap.

## D-79 — RB1: permit accounting is NO-RESERVE; pin (ix) restated

**Authority:** APPROVER (RB1) + **LEADER (final)**. **Cross-map:** approver **RB1**; **amends D-43 (pin (ix)) and D-53 (alarm model)**.
**Disposition:** ADOPTED (binding).

**Evidence:** pin (ix) asserted "cancel while awaiting `acquire` ⇒ permits return to full" while the alarm model says waiters do NOT hold permits — reserve-at-enqueue double-counts in the alarm; no-reserve makes the round-2/3 pin wording nonsensical. A coder could not implement without inventing semantics.

**Decision (folded into `phase1-plan.md` task 1.4 + pin 1.9(ix)):**
1. **ADOPTED MODEL: NO-RESERVE.** Waiters hold **NO** permits.
2. **Invariant: `permits == workers == in-flight` at all times**; the waiter deque holds **only parked, not-yet-admitted waiters**.
3. A **cancelled parked waiter REMOVES ITSELF from the deque** — it returns nothing because it never held anything.
4. **PIN (ix) RESTATED:** after a cancellation storm, assert **(a)** `permits == in-flight`; **(b)** `len(waiter_deque) == count of live parked waiters` (no cancelled-waiter residue); **(c)** the alarm-sum formula (`waiters + in-flight`) holds throughout the storm.
5. **Superseded wording:** every "cancelled waiter returns a permit" / pin-(ix) "permits return to full" phrasing is struck in `phase1-plan.md` (and the two summary phrases in `plan-overview.md` §Implementation/G8); the D-43/D-66 bodies above **keep their historical wording** — this row is the supersession record.

## D-80 — RB2: the cross-thread wake primitive is NAMED; TB-5 amended; the loop is captured at `configure()`

**Authority:** APPROVER (RB2) + **LEADER (final)**. **Cross-map:** approver **RB2**; **amends D-35/TB-5 (dated stamp applied in place)**.
**Disposition:** ADOPTED (binding).

**Evidence:** release runs in the executor future's done-callback on the worker thread (D-43), but the plan never named the primitive that wakes an event-loop-parked `asyncio.Future` waiter — and TB-5's literal wording forbade every legal option (read literally, the gate was unwakeable).

**Decision (folded into `phase1-plan.md` tasks 1.1/1.3/1.4 + `plan-overview.md` §TB-5 + D-35 above):**
1. **NAMED PRIMITIVE:** **`loop.call_soon_threadsafe(waiter.set_result, ...)` / `loop.call_soon_threadsafe(waiter.set_exception, ...)` — THREAD-SAFE FUTURE RESOLUTION, not coroutine scheduling.**
2. **The loop reference is captured ONCE at `configure()`** (`asyncio.get_running_loop()`), never per-call from a worker thread.
3. **TB-5 AMENDMENT (verbatim):** "Coroutine-scheduling callbacks into the loop remain FORBIDDEN (`run_coroutine_threadsafe`, `call_soon_threadsafe` targeting a coroutine). Thread-safe FUTURE RESOLUTION via `loop.call_soon_threadsafe` on a parked `asyncio.Future` is the SANCTIONED wake primitive and the only one."
4. The release path in the done-callback uses **exactly this** primitive.

## D-81 — Non-blocking note batch (wording-level only), one row per landing

**Authority:** APPROVER (W-phases N1/N4/N5/N6 + W-core F3/F4/F8/F11/F13/F14). **Disposition:** ALL FOLDED; **F2/F6/F9/F10/F12/F15 SKIPPED** (trivial stylistic / already-owned; none collided with the folded items).

| Note | Fold (landing) |
|---|---|
| **N1** | `phase3-plan.md` task 3.5 — the 2-line router call-site sketch (`ensure_job_ids` alongside `limit`) |
| **N3** | `plan-overview.md` G1 headline retitled to **"zero in-scope instances-path frames (exempt set per §routed-out)"** — no over-claim vs D-50 |
| **N4** | `phase5-plan.md` task 5.1 — the hard block on tester increment #2 stated as a **tracked dependency (board item)** |
| **N5** | `phase5-plan.md` 5.11 + the 5.2 guard — the in-flight marker is a **production touch: guarded + pinned BYTE-INERT** (must not alter any response byte) |
| **N6** | `phase4-plan.md` task 4.7 — fallback becomes **"produce or block"** (never silently skip); disclosure is the exception path |
| **F3** | `phase1-plan.md` task 1.1 — `wrap_future` wording corrected: **cancellation DOES propagate** (no-op stop for a running thread, which may complete anyway; the done-callback still releases; permits stay consistent under NO-RESERVE); "without wrapper-future cancellation semantics" struck |
| **F4** | `phase1-plan.md` task 1.4 — the **single-loop cooperativity assumption** stated explicitly (close-vs-register atomicity relies on it) |
| **F8** | `phase1-plan.md` task 1.7 — the **drain hang-case sentence**: past the bound the daemon's exit path fails (not an in-flight caller), with a defined timeout error naming the un-drained items; non-daemon threads bound the exit; systemd SIGTERM/SIGKILL at `TimeoutStopSec=90` — **the daemon never hangs silently** |
| **F11** | `phase1-plan.md` task 1.10 — the alarm fires when **`waiters > 0` OR `waiters + in-flight` exceeds `DB_OFFLOAD_WAIT_WARN_SUM`** (named, env-overridable, resolved at `configure()`; default form `workers + 2`) |
| **F13** | `plan-overview.md` G1 attribution — the **D-54 four co-callers of `list_child_ids_permanent`** disclosed as able to still produce narrow captures at HEAD; they attribute to **their own routed entries**, never the in-scope families |
| **F14** | Offset fixes, exactly the two listed: `tools/instance.py` **`:6170` → `:6168`** *(REVERSED at iteration 003 @ `3e976696b`: `:6170` was CORRECT — `:6168-6169` is a comment block)* and `instance_lifecycle.py` **`:5113-5117` → `:5110-5116`** — swept across the editable set (`phase2-plan.md` ×3, `plan-overview.md` ×1, `decisions.md` D-33 ×1); the read-only `architecture-recommendation.md:233` and the already-correct `:5113-5116` forms left as-is |
| *(skipped)* | **F2/F6/F9/F10/F12/F15** — trivial stylistic or already-owned (F12's IN-list ceiling stays a new-D-id trigger; F15's exit-code mapping stays tester-increment scope) |

---

# FOLD ROUND 4.5 — W1-FOLD MICRO (iteration 003 APPROVED, FINAL) — D-82

**Input:** approver iteration 003 = **APPROVED (FINAL)**; carried [W1 fold REQUIRED] items + 3 one-liners. **Context @ `3e976696b`:** tester increment #2 LANDED (harness tree `3e281cc5`; council review PENDING; one untracked harness WIP file reds the porcelain fence — fail-closed by design).

## D-82 — W1-fold micro: five edits

| # | Edit | Landing |
|---|---|---|
| 1 | **Harness tree-sha pin → RE-DERIVATION RULE (binding text; NO new sha pinned)** — "the pinned tree-sha is re-derived to the COUNCIL-REVIEWED harness tree at 5.1 close; any tree-sha not matching the reviewed commit fails the fence; porcelain must be clean under the harness dir at gate time." Stale `88009e7b…` disclosed as FAIL-CLOSED (pre-increment-#2); 5.1 already blocks all HEAD runs until increment #2 + council review close | `phase5-plan.md` 5.1 + 5.8; `plan-overview.md` G12, DO-NOT #16, risk 16, §Harness-state item 4 + the `c3d87af26` tree-sha sentence (kept as history, marked superseded as a comparison value) |
| 2 | **Shape canon = 9 FENCED ENTRIES (8 compared SHAPES + 1 detail endpoint)** — the landed `gate_g5` fences 9 (`main.py:787-800`); the instrument is the stricter and is what runs; no 8-vs-9 contradiction survives at any live site (round-3/3.5 history intact) | `plan-overview.md` G5 row + A-2 provenance row; `phase5-plan.md` 5.10 handoff; `decisions.md` D-68 item 3 + A-2 row |
| 3 | Exit-criterion count: 5.9's register governs — "all **twelve** items" | `phase5-plan.md` exit criterion 8 |
| 4 | Task 4.1 Depends-On self-reference `4.1` → **`P1`** | `phase4-plan.md` task 4.1 |
| 5 | **`tools/instance.py` anchor RESTORED to `:6170`** (iteration-003 adjudication @ `3e976696b`: the `manager.list_instances(...)` call IS at `:6170`; `:6168-6169` = comment block — fold-4's F14 `:6168` correction REVERSED; the `instance_lifecycle.py :5110-5116` half of F14 stands); history mentions amended with the dated adjudication, never silently rewritten | live: `phase2-plan.md` 2.6(c) + exit 6, `decisions.md` D-33 body, `plan-overview.md` In-Scope #7 + DO-NOT #11; history: `decisions.md` D-81/F14 row (dated note appended) |
