# Toolset Reshape Design — "remove + resolve" (implementation-ready)

**Status: PROPOSAL — pre-implementation gate. NO implementation done.**
Date: 2026-09-19 · Prior doc: `architecture-recommendation.md` (same dir) · Scope: agent-facing toolset + prompts ONLY (user hard constraint: engine untouched; reliability parity with today's watch_job accepted — silent-loss stranding class remains, heals at restart).
Provenance: worker `data-flow-design` c094e246 (Q1–Q4 + addendum), worker `structural-design` a0fd6ead (Q5–Q6 + census), architect synthesis with spot-verification (§11).

---

## 0. VERDICT — validate with 3 adjustments; no engine touch required

The user-approved shape is **feasible toolset-only**, with three adjustments where the approved wording is either infeasible or would breach the constraint:

| # | Approved wording | Adjustment | Why (evidence) |
|---|---|---|---|
| A1 | `watch_mission(target)` "mission-terminal semantics" | ✅ Keep — implemented as **resolver front-end + one receipt-keyed watcher row per current receipt** (`events=["mission_terminal"]`), NOT a mission_id-keyed row | The engine's notify path is strictly receipt-keyed: `get_watchers_for_job` queries `WHERE job_id == work_id` (`watcher_repository.py:133-144`); mission-keyed rows never match (Blocker B-list §10) |
| A2 | "job_create response gains mission_id" | ⚠️ **Infeasible pre-dispatch** — response keeps `job_id` (the receipt, a valid `watch_mission` handle); add `mission_id` ONLY when already known (mirror jobs / post-dispatch re-reads) | `instance_id` is `None` at enqueue for task jobs (`job_queue_service.py:595` → `_repository.create` :851); instance minted later at `job_processor.py:996-1026`. `to_dict` has no mission field (`models.py:440-462`) |
| A3 | watch semantics via `job_create(watch:true)` (today: transport events) | ⚠️ **Do NOT globally retarget** the shared `watch:true` param (workers depend on it — census §6). Ari/jober path: `job_create(...)` → `watch_mission(job_ref)`. Optional explicit opt-in value (`watch="mission"`) if a one-call form is wanted | `job_create` is category-`job` tooling shared across agents; global semantic change = blast radius beyond ari/jober |

Multi-receipt correctness — the sharpest edge — **works at tool-layer**: registration-time fan-out (one row per receipt) + the engine's existing terminal candidate-set fan-out (all Task.work_ids for the instance, verbatim §3). The cde5017f incident pair (task b8b6ead7 + mirror ac213421 on mission bc145c7e) would have fired correctly under this design had the observer consumed event 77148.

---

## 1. Tool surface (final shape)

| Tool | Change | Spec |
|---|---|---|
| `watch_mission(target, events=["mission_terminal"])` | **NEW** (ari/jober) | `target` = mission_id OR job reference (resolver pattern of `job_continue`, `job_queue.py:1102-1194`). Resolve → mission → **all Task.work_ids for the instance** (`task_repo.get_by_instance`, `task/repository.py:284-299`) → register ONE `job_watchers` row per work_id, `events=["mission_terminal"]` (HOLD semantics — `work_notifier.py:332-376` — default, not opt-in). Replicates the 50-watch cap semantics (`job_queue.py:1601-1604`) counting every row it mints. Already-terminal mission → register-then-notify-immediately (§4). Assembly follows `create_mission_tools()` precedent (`missions.py:548`) |
| `job_create` | response field | `mission_id` added only when the value is already known at response time; otherwise absent — agents use the returned `job_id` as the `watch_mission` handle (A2). No semantic change to `watch:true` (A3) |
| `await_mission` | unchanged | pure in-turn poll (`missions.py:641-707`), timeout returns snapshot (not error) — prompt note, not code |
| `watch_job` / `watch_jobs` | **removed from ari/jober** via meta.json deny (§6). Registry, factory (`job_queue.py:2385`), category decorators, `_FULL_DOCS` all untouched — other agents keep them |
| `unwatch_job` / `list_watched_jobs` | **kept, resolver-extended** (§7) | `unwatch_job(handle)` where handle ∈ {receipt job_id, mission_id → resolves to all its watched receipts}; `list_watched_jobs()` unchanged signature, labels rows by resolving handles |

---

## 2. Q1 — Mission-not-yet-born edge

**(a) job_create response today:** kwargs at `job_queue.py:671-694`; pre-generates UUID4 at `:722`; enqueues via `job_service.enqueue`; returns `job_item.to_dict()` `:765` — fields per `models.py:440-462`; `instance_id=None` for fresh task jobs (`job_queue_service.py:595`). Instance minted at dispatch: `job_processor.py:996-1000` (`spawn_instance_with_mcp`), UUID stamped onto `JobItem.instance_id` `:1001-1026`, and **`Task.work_id = proc_job.job_id` stamped at `:1024`** — this stamp is what carries the pre-generated receipt UUID into the terminal candidate set later.

**(b) Pre-mission registration:** `watch_mission(job_ref-from-job_create)` BEFORE spawn registers on the pre-generated UUID4 — valid by construction (it IS the future mission's first task receipt; its Task row lands at dispatch `:1024`, entering `inst_tasks`). Mission-not-born ⇒ not terminal ⇒ the watch simply waits — correct semantics. A synthetic UUID or a mission_id key would strand (never matches — `watcher_repository.py:143`); the receipt-UUID key does not.

**`watch:true` retarget decision (A3):** rejected as a global change. The ari/jober flow is two calls: `job_create(...)` → `watch_mission(<returned job_id>)`. An explicit `watch="mission"` opt-in VALUE (not redefining `true`) is acceptable if a one-call form is wanted — implementer's choice, must not alter `true`'s transport semantics.

## 3. Q2 — Multi-receipt-per-mission (incident edge: b8b6ead7 + ac213421 → bc145c7e)

**Engine mechanics (verbatim, worker addendum):** terminal notify builds `candidate_work_ids = {ctx.job_id} ∪ {all Task.work_ids for instance}` (`job_feedback_observer.py:1913-1927`; TERMINATED branch builds the same shape at `:1364-1381`). `task_repo.get_by_instance` returns **all Tasks, newest first, NO status filter** (`task/repository.py:284-299`) — terminal Tasks persist. Every `Task.work_id` equals a `JobItem.job_id` (task stamp `job_processor.py:1024`; message branch `instance_messaging.py:2050-2057` — **mirror receipts are Task.work_ids too**). Fan-out at `:1969-1992` calls `notify_watchers(work_id, per_kind_status)` per candidate → `get_watchers_for_job(work_id)` (`watcher_repository.py:133-144`) → CAS claim (`:289-352`) → `[JOB_EVENT]`.

**Design — registration-time fan-out (row-per-receipt):**
1. `watch_mission(mission_id)` → resolve → all current Task.work_ids → one row each, `mission_terminal` events.
2. At instance-terminal, EVERY watched receipt's row fires (HOLD gate releases on mission liveness terminal). The incident pair: both rows would have fired.
3. **Consequence:** one mission-terminal produces **N `[JOB_EVENT]`s for N watched receipts** — source strings carry receipt UUIDs (`internal_agent:job_event:{receipt_uuid}:{status}`), byte-identical envelope. Prompt rule: *first event after your watch = the signal; the rest are echoes — act once* (§8).

**Remaining gap (accepted-parity, documented not designed around):** receipts minted AFTER `watch_mission` returned (a later `job_continue`) have no row and no mint-hook exists (Blocker 1, §10). Mitigation is a prompt rule: **re-call `watch_mission` after any `job_continue`**. Pre-condition of full coverage: all receipts exist at watch time.

**Rejected mappings:** mission-keyed row (never resolves — Blockers 4/5), auto row-per-receipt on mint (no hook — Blocker 1), single-row-on-freshest (loses pre-existing receipts — strictly dominated by fan-out).

## 4. Q3 — Terminal-at-registration

Mirror of `watch_job` today (`job_queue.py:1606-1656`: `is_terminal(record.status)` → register → immediate `notify_watchers`):
1. `MissionResolver.resolve(mission_id)` (`mission_resolver.py:424-478`) → `liveness` + `terminal_reason`.
2. Terminal set: `{completed, failed, cancelled, dead_letter}` (`mission_resolver.py:386`). **W4 hazard:** linked JobItem `admission_state='dead'` flips terminal_reason to `dead_letter` regardless of instance state (`:391-392`) — the resolver already encodes it.
3. Register rows (as §3) → immediate notify via `notify_watchers(receipt_job_id, per_kind_status)`.
4. Known pre-existing edge (unchanged, not new): already-terminal mission with NO JobItem row for the instance → rows cannot key on a valid receipt; notify no-ops and reconcile skips silently (`job_queue_service.py:500-521`). Document as accepted pre-existing class.

## 5. Q4 — Epoch semantics (F7) + payload facts

- F7: revival bumps epoch; `mission_id` stable (`decisions.md:69`). `MissionResolver` reports `epoch_count=1` constant (`mission_resolver.py:394-401`) — epoch is NOT a first-class resolver field today.
- **Firing rule:** fires on epoch-terminal (instance liveness terminal). The row is CAS-claimed + deleted at first terminal — **a revived mission needs a FRESH `watch_mission` call** (matches `await_mission`'s re-poll behavior, `missions.py:724-729`).
- **Payload today (engine, untouched):** `[JOB_EVENT] Job {work_id[:8]}... {status_display}` + `Agent:` + `Result:`/`Error:`/`Progress:` lines (`work_notifier.py:443-456`, contract `:51-77`); source `internal_agent:job_event:{work_id}:{status}` (`:466`). **No epoch, no mission_id, no terminal_reason field.** Adding epoch = engine + parser-contract change (Blocker 7) — do-not-improvise. Agent guidance: call `get_mission` post-notify for epoch/details.
- Under this design the `work_id` in the envelope is the **receipt UUID** — parser stays byte-compatible; agents correlate via "any watched receipt of my mission".

## 6. Q5 — Removal mechanics: meta.json deny (Option 1)

**Edits (verified shapes):**
- `agents/ari/meta.json:30` — existing `"deny": ["edit_file","write_file"]` → add `"watch_job","watch_jobs"`.
- `agents/jober/meta.json:11` — allow-only (`"allow": ["job", ...]`) → add a `"deny": ["watch_job","watch_jobs"]` key (established shape: maintenancer `:13`, approver[v2] `:16`, developer[v2] `:16`, explorer `:12`).
- Meta version bumps: ari & jober minor bump (additive+removal precedent).

**Why deny over factory-strip (worker's 5-axis, adopted):** blast radius limited to ari/jober resolution (factory `job_queue.py:2385`, registry `_tool_registry.py:684/786-789`, decorators, `_FULL_DOCS` all untouched); **zero test impact** — factory index pins (`tests/test_job_queue_tools.py:2098/2155/2203/2241/2283/2333`) and the ~14 test files touching watch_job stay green because the factory contract is unchanged; one-line revert; other agents (worker etc.) keep the tools via the `job` category. Factory-strip would break 6 index pins + 4 unit + 4 integration assertions and diverge registry/factory — engine-adjacent blast the constraint forbids.

**Census (adopted, reconciled):** watch_job-family mentions = **6 files, 49 hits**: ari/workflow.md:261 (list_watched_jobs ×1); jober/rule.md:11/12/41/49/139/325/326 (×7); jober/soul.md:13 (×1); jober/tools_note.md ×23 (:40/43/50/55/62/67/74/79/88/93/396/410/419/420/441/458/459/465/482/490/492/500/528); jober/workflow.md ×11 (:115/118/121/165/292/319/425/436/449/476/494); skill.md ×6 (:17/39/85/107/198/235). **Zero** hits: frontend/ (FE uses HTTP `/api/jobs/{id}/watch`, not the tool), any `agents/*/meta.json` (verified by grep — all use the `job` category), governor/council configs. `ari/{rule,soul,tools_note}.md` carry ZERO watch_job mentions (already mission-vocabulary at ari/soul.md:26/102) — vocabulary-refresh only.

**File-count reconciliation (dispatch said 9):** migration scope = 9 files total (8 agent files + skill.md); of these, 6 carry tool-name mentions requiring removal/replacement; ari/{rule,soul,tools_note}.md get decision-rule refresh only.

**[JOB_EVENT] parser contract:** envelope emitted by `work_notifier.py` (engine, untouched) ⇒ **byte-identical** — header/source/body per skill.md:148-178 unchanged. Re-scope is the EVENT SET only (`{mission_terminal}` vs default transport set, `job_queue.py:1546-1557`). skill.md parser block needs zero byte changes; edits are prose/example swaps around it (§8).

## 7. Q6 — unwatch/list: keep names, resolver-extend (Option C, minus schema creep)

- `unwatch_job(handle)`: handle = receipt job_id OR mission_id (resolve → all its watched receipt rows → delete). Same rows, same repo — **no new table, no discriminator column** (worker report's "new mission_watcher table / discriminator column" suggestion is REJECTED: schema changes are outside the toolset-only constraint and unnecessary — `events=["mission_terminal"]` already distinguishes intent where behavior needs it).
- `list_watched_jobs()`: signature unchanged; label rows by resolving handles (mission vs receipt) in the response text.
- Rationale: "receipts leave agent vocabulary" governs the **work-wait pair**; unwatch/list are lifecycle verbs over what YOU watch — one vocabulary, backward-compatible for receipt-watchers.
- **Stranded pre-reshape rows: no cleanup path needed.** Pre-reshape ari/jober rows live the normal lifecycle (terminal → CAS claim, or boot `reconcile_terminal_watches` at restart — accepted parity). Owners can always `unwatch_job` them.

## 8. Q7 — Prompt migration (9 files; concrete)

**Decision-rule text (insert into ari & jober soul/rule + skill.md, adapted per file voice):**
> **You wait on MISSIONS, not receipts.** Create work with `job_create` — you hold a receipt (job_id). To wait on the WORK: `await_mission(mission_id)` in-turn, or `watch_mission(mission_id_or_job_ref)` to yield and be revived at mission-terminal. `watch_mission` accepts the receipt you created or the mission_id, and watches every receipt that exists at call time. After `job_continue`, call `watch_mission` again — new receipts are not auto-watched. The FIRST `[JOB_EVENT]` after your watch is the signal; later events on the same mission's other receipts are echoes — act once. Receipts answer transport questions only (`job_get`); never watch a receipt for a work question. A revived mission needs a fresh `watch_mission`; the event carries no epoch — `get_mission` for details. `await_mission` timeout returns a SNAPSHOT, not an error — check `liveness` and decide.

**Edit table:**

| File | Delete | Add/replace |
|---|---|---|
| jober/tools_note.md (23 hits) | all watch_job/watch_jobs examples (:40-93 transport section, :396-528) | watch_mission spec + the decision rule; `unwatch_job/list_watched_jobs` handle-tolerance note |
| jober/rule.md (:11/12/41/49/139/325/326) | watch_job references | decision rule; job-ref tolerance; re-watch-after-job_continue rule |
| jober/workflow.md (:115/121/165/292/319/425/436/449/476/494 + :118) | watch_job steps | watch_mission steps; echo rule (act once) |
| jober/soul.md (:13) | `watch_job(events='mission_terminal')` opt-in phrasing | watch_mission as THE durable watch |
| ari/workflow.md (:261) | `list_watched_jobs()` polling habit | decision rule pointer (mission vocabulary) |
| ari/{rule,soul,tools_note}.md | — (zero watch_job hits) | decision-rule refresh; job-ref tolerance; timeout=snapshot note |
| skill.md (:17/39/85/107/198/235) | watch_job patterns | watch_mission notification example + handle-semantics; **parser block :148-178 byte-untouched** |

## 9. Q8 — Test plan sketch (tool-layer only)

1. **Resolution paths:** mission_id (live, 2 receipts) → 2 rows `events=["mission_terminal"]`; job-ref → same; unresolvable → explicit error.
2. **Pre-registration:** `job_create` → `watch_mission(returned job_id)` pre-dispatch → row on pre-generated UUID → after dispatch + instance-terminal, row fires (Task stamp `job_processor.py:1024` enters candidate set).
3. **Already-terminal:** completed / failed / dead_letter (W4 flip) missions → register-then-notify-immediately; dead_letter terminal_reason surfaced.
4. **Multi-receipt fan-in:** task + mirror receipts registered → instance-terminal → both rows CAS-claimed exactly once; two envelopes, receipt-UUID source strings, byte-format per skill.md contract.
5. **Post-registration mint gap (pin the limitation):** `job_continue` after `watch_mission` → new receipt unwatched; re-call covers it. Assert documented behavior, not engine change.
6. **Epoch:** row claimed+deleted at epoch-terminal; revived mission NOT auto-watched; fresh call re-registers.
7. **Removal pins:** resolved-toolset specs — ari/jober exclude watch_job/watch_jobs AND include watch_mission; worker still includes both; factory index pins (`tools[17]`/`tools[20]`) stay green (factory untouched).
8. **Cap:** `watch_mission` mints N rows → cap accounting counts N against the 50 limit.
9. **Prompt pins (grep specs):** no `watch_job` tokens in jober/* + skill.md prose sections; decision-rule text present; skill.md parser block byte-identical (fixture pin).

## 10. Q9 — Blocker list (engine touches this design does NOT need; reported per STOP rule)

Multi-receipt **auto-fan-out on mint** and **mission-keyed rows** remain infeasible without engine edits — precisely: ① `job_processor.py:996-1026` (no watcher hook on receipt mint); ② `job_feedback_observer.py:1261-1285` (resolver is in_progress-only); ③ `:1902-1999` (candidate fan-out is event-driven, not instance-indexed registration); ④ `work_notifier.py:118-470` (`notify_work_watchers` looks up by work_id only); ⑤ `watcher_repository.py:133-144`/`:289-352` (receipt-keyed WHERE); ⑥ `job_queue_service.py:500-521` (reconcile skips mission-keyed rows); ⑦ `work_notifier.py:443-467` + skill.md (epoch-in-payload). **None are required by this design** — they define the future engine-phase scope if the accepted-parity gaps are later closed.

## 11. Verification & discrepancy log

- Architect spot-verified: sole boot call site (`api.py:992`); FK-free watcher string column (`watcher_models.py:57-67`); event-bus drop paths (`event_bus.py:332/:348`); meta shapes (ari deny exists `:30`; jober allow-only `:11`; no meta references watch_job by name); verbatim candidate-set construction quoted by worker addendum.
- Discrepancies adjudicated (most-specific-wins): "9 vs 7 vs 6 files" → 9 in scope / 6 with tool-name hits (worker table over prose); semantics worker's initial "task→task strands" → corrected by addendum to "fires via candidate fan-out" (verbatim code); mechanics worker's "new mission_watcher table / discriminator column" → rejected (schema creep outside constraint).
- Unverified residuals: `epoch_count=1` constant means epoch is unobservable via resolver today (`mission_resolver.py:394-401`) — payloads/rules written accordingly; already-terminal-without-JobItem edge documented as pre-existing.
