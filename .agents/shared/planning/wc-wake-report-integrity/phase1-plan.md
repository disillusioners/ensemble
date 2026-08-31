# Phase 1 Implementation Plan — Component 1 (#8): WC-Target Waking

**Feature:** `wc-wake-report-integrity`
**Branch:** `feature/wc-wake-report-integrity` @ `1f8f8ed4` (verified: checkout clean, HEAD matches)
**Status:** DRAFT — plan only, no source touched. D3 was a leader-facing recommendation at draft time — since **LOCKED Option A** (2026-08-30); D7 row added.
**Author:** worker (plan-only pass, 2026-08-30)
**All `file:line` anchors below were re-verified against `1f8f8ed4` by reading the code in this session.**

---

## 1. Objective

Make both public message lanes treat a **WAITING_CHILDREN (WC)** target like the parked, between-turns state it is:

- **HTTP `POST /instances/{id}/messages`** and the **agent-tool `send_message`** route WC targets via `enqueue_message` (durable `MessageQueue` row + PENDING `Task` → WC→RUNNING flip → `worker_pool.notify_work()`), producing a **first-class graph turn** — instead of today's RAM-FIFO `set_injection`, which strands the message because a parked WC parent runs no `agent_node` pass to drain it.
- RUNNING targets keep the FIFO-injection fast path, unchanged.

This implements the "Park/Wake Primitive Selection" rule already documented in the Core blueprint: *`set_injection` is a RAM FIFO append that does NOT wake a parked WC parent; `enqueue_message`-class calls are the only internal paths that wake one.* Today the two user-facing lanes violate that rule for WC targets (`messages.py:351-381`, `instance.py:877-878`).

## 2. Scope

**In scope**
- Routing change in both lanes (WC: injection → enqueue).
- D1: tool-pairing tail-guard at the enqueue entry seam (all enqueue traffic).
- D2: parked-FIFO leftovers drain into the graph input before the new message.
- D4: HTTP WC contract 202-injected → 200-enqueued, documented.
- D5: marker/provenance semantics for WC-wake messages, verified.
- D6: busy-gate semantics change, documented.
- Riders R1 (deterministic placeholder ids) and R2 (CLE-retry regression test) — same seam, from the 84fd8018 arc.
- W5: user-msg-vs-child-report ordering becomes a claim-order race — tests updated.
- D3: `job_inject` WC-target handling — **LOCKED Option A** (leader, 2026-08-30; §4, T7).

**Out of scope (untouched)**
- The WC parking mechanism itself (`child_reports`, dependency watchers, `waiting_children_watchdog`).
- The RUNNING injection lane and all three in-graph drain sites in `daemon/graph.py`.
- The DB-backed `ReportInjection` lane (`report_injections`, `report_delivery_recovery`, resume-router).
- PAUSED branches in both lanes (HTTP auto-resume C4; agent-tool verbatim reject R-O1).
- `claim_pending_task` SQL, schema, migrations — **no DB changes in this component**.
- FE code (FE-latency interplay documented in §8-T8, not implemented here).

## 3. Locked decisions (requirements, from the leader)

| ID | Decision | Plan section |
|----|----------|--------------|
| D1 | Pairing tail-check/guard runs at the **enqueue entry seam** for ALL enqueue traffic (closes poisoned-tail→2013 exposure; satisfies CLE-mirror convention) | §5.1, §6-T6 |
| D2 | Parked-FIFO leftovers drain INTO the graph input BEFORE the new message (oldest-first; one turn when both exist) | §5.2, §6-T5 |
| D3 | `job_inject` WC-target — **LOCKED — Option A** (superseded by the LOCKED row below; `architecture-recommendation.md` §8) | §4 |
| D4 | HTTP WC contract changes 202-injected → 200-enqueued; documented; FE-latency interplay noted | §6-T4, §8 |
| D5 | WC-wake messages LOSE the `injected_message` marker (first-class turns), keep source provenance; D12/SSE consumers checked | §5.3, §8-T8 |
| D6 | Busy-gate semantics change accepted — a queued PROCESS_MESSAGE counts busy; documented | §5.4, §6-T3 |
| R1 | Deterministic placeholder ids `pairing-synth-{tc_id}` | §6-T1 |
| R2 | CLE-retry regression test | §6-T6, §7 |
| W5 | Ordering becomes a claim-order race (two turns possible); tests updated | §5.5, §6-T9 |
| D3 | **LOCKED — Option A** (leader, 2026-08-30; §4 recommendation accepted — see `architecture-recommendation.md` §8 and the C1-D3 LOCKED row in `decisions.md`) | §4, §6-T7 |
| **D7 (new)** | Legacy `:1060` bypass deletion → **LOCKED** (reviewer C3 + architect §7.1): the `Manager.send_message` → `InstanceMessagingService.send_message` → direct `graph.ainvoke` chain is deleted; implemented as T6b | §6-T6b |

## 4. D3 — Option A **leader-LOCKED** (recommendation accepted 2026-08-30; see `architecture-recommendation.md` §8 and the C1-D3 LOCKED row in `decisions.md`)

> **STATUS UPDATE (2026-08-30 reconciliation pass):** Option A is **LOCKED by the leader** — recorded in `architecture-recommendation.md` §8 and in `decisions.md` (C1-D3 LOCKED row, directly below the retained OPEN row). The analysis below is retained as the accepted rationale; Option B is superseded (audit only). T7 implements Option A; the **T7 → T10** dependency is added per architect correction 3 (S6).

**Question:** `job_inject` (`daemon/tools/job_queue.py:1827-1881`) currently accepts RUNNING **and** WC targets (`:1857` checks `INJECTION_ELIGIBLE_STATUSES`), then `set_injection` (:1868, RAM FIFO, no source). For a parked WC parent this is the same stranding exposure this component fixes for the other two lanes.

**Important forcing fact:** this component shrinks `INJECTION_ELIGIBLE_STATUSES` to `{"running"}` (§6-T2). `job_inject:1857` tests against that set, so **Option B is not free** — the eligibility check and its error text (`:1858-1864`, "only works on RUNNING or WAITING_CHILDREN") must be edited either way, and `_FULL_DOCS["job_inject"]` (`:352-390`) must be rewritten either way.

### Option A (RECOMMENDED): WC-target `job_inject` moves to `enqueue_message`
- **Behavior:** RUNNING → `set_injection` exactly as today. WC → `manager.enqueue_message(instance_id, message, source=f"internal_agent:{current_instance_id}")` — durable row, WC→RUNNING flip (:1533-1537), real wake, first-class turn.
- **Rationale:**
  1. *Primitive-selection rule*: a parked WC parent has no live turn to absorb an injection; enqueue is the only path that both delivers and wakes ("who wakes the target?" — answered: enqueue).
  2. *Durability*: RAM FIFO dies on daemon restart and is TTL-purged at ~1h (`_cleanup_stale_injections`, manager.py:3650). Under Option B an injected-into-WC message is silently deleted if no wake ever arrives. Enqueue never loses it.
  3. *Consistency*: post-change, all three `set_injection` callers treat WC the same way. Leaving `job_inject` on FIFO creates a third WC semantic — exactly the fork this component eliminates.
  4. *Blast radius*: `job_inject` is a low-frequency tool; response-shape change is contained to its callers.
- **Touches:** `job_queue.py:1853-1881` (split branch: RUNNING keeps injection; WC calls enqueue + returns `{status: "enqueued", message_id, ...}`); `_FULL_DOCS["job_inject"]` :352-390; new/updated tests. Consider a `has_instance_busy` pre-check (mirroring `job_continue` 5a, :975-995) so a WC target that already has a queued wake gets a clean error instead of a silently queued second turn — recommended, small.
- **Note:** under Option A the enqueue carries no `load_skill`/`context` channels — `job_inject` has neither parameter today, so nothing is lost.

### Option B (viable, minimum-touch): leave `job_inject` on RAM injection for WC
- **Behavior:** replace the `:1857` set-membership test with an explicit status check (`in ("running", "waiting_children")`), keep `set_injection`, keep docs minus the set reference.
- **Rationale:** after D2 lands, a stranded FIFO entry drains on the WC parent's next wake (oldest-first, before the new message) — the stranding is mitigated for any parent that wakes again. Zero new enqueue surface; `job_inject`'s "piggyback, no new job" contract text stays honest.
- **Residual risk:** a WC parent that *never* wakes (hung children, stalled reports) still silently loses the entry to the ~1h TTL sweep. Provenance is also weaker (no `MessageQueue.source` row; no wake).

**Recommendation: Option A.** The marginal code is ~15 lines plus tests, and it closes the last WC-injection hole instead of documenting it. If the leader prefers minimal Phase-1 surface, Option B is acceptable *because* D2 mitigates the drain-on-next-wake case — but the never-wakes TTL-loss case should then be recorded in critical notes as accepted.

## 5. Current-state map (verified @ 1f8f8ed4)

### 5.1 Lanes and routing today

**HTTP lane — `daemon/routers/messages.py`**
| Anchor | What |
|--------|------|
| :148-167 | `send_message` endpoint; routing-table docstring :157-163 (must be updated, T4) |
| :189-207 | S4 empty-content 400 + images validation (unchanged) |
| :210 | `current_status` captured once for routing |
| :219-337 | PAUSED auto-resume branch → 200 (C4: UNCHANGED) |
| :351 | `if current_status in INJECTION_ELIGIBLE_STATUSES:` — **WC enters here today** |
| :356 | `manager.set_injection(instance_id, message.content)` — note: **no `source=`** (None path) |
| :357, :359-366 | `get_injection_count` + `injection_pending` SSE via `_emit_injection_sse` |
| :374-381 | **202** + body `{status:"injected", instance_id, content, timestamp, pending_count}` — no `message_id`/`job_id` |
| :396-402 | IDLE/WAITING/QUEUED/terminal → `manager.enqueue_message_job(source="api", images, queue_id)` |
| :412-429 | **200** `MessageResponse{message_id, ..., job_id, queued}` |
| :453+ | GET `/{instance_id}/injection` — RAM-queue fallback reader (keep; WC entries will simply never appear post-change) |

**Agent-tool lane — `daemon/tools/instance.py`**
| Anchor | What |
|--------|------|
| :810-891 | `_route_send_message` — exhaustive 10-status map; :877-878 injection if `prior_status in INJECTION_ELIGIBLE_STATUSES` (**WC here today**); :885-886 terminal → `enqueue-revive`; :891 fallthrough `enqueue`; :873-874 PAUSED → `paused` |
| :2748-2754 | Routing call + unpack at the `send_message` call site |
| :2756-2778 | Enqueue-only parameter override: `load_skill`/`context` forces `routed_via="enqueue"` (:2775-2778) — after T2 this override remains meaningful for RUNNING only |
| :2786-2793 | PAUSED verbatim reject (R-O1) |
| :2803-2850 | Injection branch: :2811-2815 `set_injection(..., source=f"internal_agent:{current_instance_id}")`; :2842-2850 success text incl. W3 stranding sentence |
| :2861-2867 | **Queue-busy guard**: `get_queue_stats` pending/processing > 0 → busy ERROR (stays for enqueue routes) |
| :2890-2896 | Revive-once guard (enqueue-revive only) |
| :2904-2909 | `manager.enqueue_message(source=internal_agent:{caller}, metadata={task_context})` — **no JobItem** (JAFP: internal traffic never mints JobItems) |
| :2915-2916 | `note_agent_tool_revive` AFTER successful enqueue |
| :2940-2944 | `_register_child_completion_watcher` (no-op unless target is a child of sender) |
| :2949-2964 | Enqueue success text |
| :2966-2997 | `send_message._full_doc_` routing table (LLM-visible contract; must be updated, T3) |

**`job_inject` — `daemon/tools/job_queue.py`**: :1827-1881 tool; :1857 set-membership eligibility; :1868 `set_injection` (no source); :1871-1878 return `{status:"injected", pending_count, content, timestamp}`; `_FULL_DOCS` :352-390. (`job_continue` :946-960 rejects only TERMINATED/ERROR/PAUSED and already wakes WC via Task creation — unaffected.)

**`set_injection` production callers (complete list):** `messages.py:356` (HTTP), `instance.py:2811` (agent tool), `job_queue.py:1868` (job_inject). No other callers exist.

### 5.2 Wake mechanics (the target path — mostly exists already)

`daemon/services/instance_messaging.py`:
- `_prepare_enqueued_message` :1256-1601 — one transaction writes `MessageQueue` + `Task` (+ `MESSAGE_RECEIVED` event), and **already flips IDLE / WAITING_CHILDREN / terminal → RUNNING** at :1533-1537 with `last_activity_at`/`version` bump :1546-1547. `msg_type` from source prefix :1343-1358 (`internal_agent:` → AGENT). Deferred-pause marker branch can skip the Task row (:1279-1311) — PAUSED targets keep the claim-gate semantics, untouched by this component.
- `enqueue_message` :1603-1750 — to_thread wrapper :1661-1672; `status_change` SSE :1696-1699; title generation :1703-1705; **`worker_pool.notify_work()` :1711-1712**.
- `enqueue_message_job` :1759+ — JobItem-mirror variant used by the HTTP lane (external entry ⇒ JobItem is correct per JAFP).
- Turn build: `_build_graph_input` :176-243 (centralized; three call sites :3402/:3411/:3420); `user_msg = HumanMessage(...)` :3475; `user_message` SSE pre-emit :3477-3484; `graph.astream` :3530.
- `get_queue_stats` :3953-4017 — counts **MessageQueue rows** (READY/PROCESSING) via `_queue_repository.get_stats`; terminal-status short-circuit :3974-3994; fail-open WARNING :3995-4010.
- `extract_load_skill` used at :2239 (enqueue-pipeline-only `<meta>` parser — the reason the instance.py :2775 override exists).

Claim side (`daemon/repositories/task/repository.py`): `claim_pending_task` :1146-1520 — per-instance single-RUNNING guard, pause gate (excludes instances PAUSED/TERMINATED, :1414-1428), defer/background/queue-awareness gates, **`ORDER BY created_at ASC LIMIT 1` :1486** (basis of the W5 race). `has_instance_busy` :543+ (PENDING+RUNNING+PAUSED) is the canonical busy predicate (consumers: `job_continue` 5a, zombie reaper, bus recovery, concurrency gate).

Turn-execution path (architect correction 4 — verified anchors): `enqueue_message[_job]` → `WorkerPool.claim` → **processor dispatch table** `task_processor.py:1077-1098` (`"process_message"` AND `"process_report"` both register `ProcessMessageProcessor` — the report lane is a second alias of the same processor, NOT a separate "fallback inject" site) → `ProcessMessageProcessor.process` → `MessageProcessingPipeline.execute` (`pipeline.py:387`) → `_do_process` (`:399-400`) → `_process_message_with_tracking` → `graph.astream` (`:3530`). No production path bypasses this chain — except the dead `Manager.send_message` legacy pair (`manager.py:6245-6258` → `instance_messaging.py:1007`/`:1060`), deleted by T6b (D7).

### 5.3 Graph-side injection machinery (unchanged, but load-bearing for D1/D2/R1/R2)

`daemon/graph.py`:
- `_ensure_tool_result_pairing` :271-384 — O(1) tail check, bounded walk (`_TOOL_PAIRING_MAX_TRAVERSAL = 8` :264), dedupe against existing `tool_call_id`s, in-place insert. Placeholder `ToolMessage` built at :364-368 **without an explicit `id`** → langchain mints a random UUID on every synthesis (**R1 target**). Placeholder text :265-268.
- Site 1 (RAM FIFO drain in `agent_node`) :2937-3005 — builds `HumanMessage` per FIFO entry with `{"injected_message": True}` + optional `source` (:2950-2961); guard :2971-2973; `full_messages.extend` :2975; `clear` :2980 with the await-free atomicity note :2977-2979.
- Site 2 (DB report drain) :3126-3176 — guard :3144-3146; `{"injected_message": True, "source": "internal_report:{child}"}` :3159-3170.
- Loop-breaker re-append re-arm :3299-3312; CLE reactive-compaction rebuild guard :3402-3408 (**the CLE-mirror convention exemplars**).
- `pairing_synthesized_msgs` flow into the C2 return :3515-3530 (checkpoint heal).

### 5.4 Marker/provenance surface (for D5)

- The enqueue turn-build already creates the wake `HumanMessage` with **no** `additional_kwargs` (:3475) — D5's "lose the marker" is natively true on this path; the work is *verification + tests*, not new code.
- Provenance lives on the durable row: `MessageQueue.source` (`"api"` / `internal_agent:{caller}`), `MESSAGE_RECEIVED` event data (:1554-1570), and the `message_metadata` JSONB channel via `metadata=` (`task_context` precedent, instance.py:2908).
- D12 subtree filter uses the structured `injected_message is True` marker (not the `[SYSTEM CONTEXT:` prefix); SSE dedup hash covers only content/tool_calls/role; `serialize_message` surfaces markers as additive keys — none of these perturb for unmarked first-class turns. `watcher_context_builder.py` uses a distinct serializer (unaffected).

### 5.5 W5 ordering (verified mechanics)

With WC→enqueue, a user message and a child-completion report targeting the same parked parent both become claimable Task rows. `claim_pending_task` picks strictly by `created_at ASC` (:1486); the per-instance single-RUNNING guard serializes execution. Result: whichever row was created first claims first; the other runs as a second turn. Today the user message is absorbed INTO the report turn via FIFO injection (single turn). The two-turn outcome is the accepted W5 trade-off.

### 5.6 Tests that pin today's contracts (must be updated)

| File | What it pins |
|------|--------------|
| `tests/unit/tools/test_instance_tools.py` | :131 pins `INJECTION_ELIGIBLE_STATUSES == frozenset({"running", "waiting_children"})`; :1023 `test_waiting_children_injects` asserts the injection success text; :1062/:1172 parametrized `running+waiting_children` routing; :1307/:1799/:1924 WC→"injection" map entries |
| `tests/tools/test_send_message_status_guard.py` | `_route_send_message` status-map / busy-guard behavior |
| `tests/test_injection_api.py`, `tests/test_injection_sse.py` | HTTP 202-injected contract + `injection_pending` SSE for WC |
| `tests/integration/test_pause_race_resume_drain.py` | pause/resume × injection drain interplay |
| `tests/unit/graph/test_injection_tool_pairing.py` | pairing-guard behavior (R1/R2 extend this file) |

## 6. Task breakdown

Dependency order: **T1 → T2 → (T3, T4 in parallel) → T5 → T6 → T6b → T7 (D3 Option A, LOCKED) → T8 → T9 → T10**, with the explicit **T7 → T10** dependency (S6 / architect correction 3: the T10 pure-hang test exercises the T7 `job_inject` wake lane). T5 has NO dependency on T2 (architect §7 confirmation). No task modifies schema or migrations.

---

### T1 — R1: deterministic placeholder ids (do FIRST; D1 composes on it)

**File:** `daemon/graph.py:364-368`

```python
tm = ToolMessage(
    content=_TOOL_PAIRING_PLACEHOLDER_TEXT,
    tool_call_id=tc_id,
    name=tc_name,
    id=f"pairing-synth-{tc_id}",
)
```

**Why:** (a) `add_messages` reducer dedups by id — a re-synthesis after a crash-between-insert-and-checkpoint (or after the new D1 seam re-heals the same tail) *replaces* instead of duplicating; (b) checkpoint dumps become forensically greppable; (c) makes the D1 seam's prepended placeholders idempotent against the in-graph sites' own synthesis. `tc_id`s are unique per tool call, so collisions are only the exact re-heal case we want to dedup.

**Tests (extend `tests/unit/graph/test_injection_tool_pairing.py`):** synthesized placeholder carries `id == f"pairing-synth-{tc_id}"`; second `ensure` call against the healed list re-synthesizes nothing (dedupe path already covers this via `existing_tool_call_ids`, :341-344 — now also id-stable); `add_messages`-level dedup assertion if the test harness builds state.

---

### T2 — Shrink `INJECTION_ELIGIBLE_STATUSES` to `{"running"}` (the routing pivot; T3/T4/T7 hang off it)

**File:** `daemon/constants.py:213-218` — value becomes `frozenset({"running"})`; update the surrounding single-home comment to record that WC was removed by wc-wake-report-integrity (a parked WC parent has no live turn; injection cannot wake it).

**Why shrink the shared set instead of adding per-caller exceptions:** the set's one-home reason (constants.py:213-215) is preventing semantic forks. "Injection-eligible" now honestly means "has a live turn that can absorb a mid-turn injection". Every consumer gets the new semantics in one move:
- `messages.py:351` — WC falls through to the enqueue branch (:396-429) → 200. ✓
- `instance.py:877` — WC no longer matches; falls past terminal (:885) to the `enqueue` return (:891). The exhaustive map absorbs WC with **no new branch**. ✓
- `job_queue.py:1857` — WC no longer eligible; error text and branch must be rewritten **regardless** (this is what makes D3 unavoidable — see §4).

**Alternatives rejected:** (i) keep WC in the set and add `if status == "waiting_children"` exceptions at three call sites — recreates exactly the fork the set exists to prevent, and the set's name becomes a lie; (ii) new constant `WC_WAKE_STATUSES` — a fourth routing vocabulary for one status.

**Tests:** update the pin at `test_instance_tools.py:131` to `frozenset({"running"})`; update the 3-consumer grep-import pin tests (they assert import-not-fork, so they should pass unchanged — verify); all routing map fixtures in §5.6.

---

### T3 — Agent-tool lane follows from T2 (small, mostly text)

**File:** `daemon/tools/instance.py`

1. `_route_send_message` docstring :832-835: `injection` = "RUNNING only"; note WC routes via `enqueue` and *why* (parked parent; enqueue wakes).
2. `send_message._full_doc_` :2966-2997: move WAITING_CHILDREN from the injection bullet to the enqueue bullet; state the durability + wake semantics and the busy-gate consequence (D6).
3. Behavior verification (no code change expected): WC send now flows :2861 busy-guard → :2904 `enqueue_message(source=f"internal_agent:{caller}")` → :2940 watcher no-op → :2955 "Message queued and sent…" text. The W3 stranding sentence (:2842-2850) is injection-branch-only — WC automatically stops emitting it (correct: the message is now durable).
4. Provenance INFO log (:2921-2932) already parametrizes `routed_via` — assert it logs `"enqueue"` + `prior_status="waiting_children"`.
5. The :2775-2778 override stays as-is (now RUNNING-only in practice); add one test that RUNNING+`load_skill` still overrides.
6. (S13) Pin the busy-gate ERROR text (:2861-2867) as caller-facing contract for the WC-with-queued-wake case — the wording ("ERROR: Instance '…' already has a message in progress. Pending: N, Processing: M. …") is what agent callers see during the enqueue→claim window; assert it verbatim on a WC target that already has a queued wake.

---

### T4 — HTTP lane (D4): WC falls to the 200-enqueued branch

**File:** `daemon/routers/messages.py`

1. No branch code change needed — T2 makes :351 RUNNING-only and WC falls to :396-429 (`enqueue_message_job(source="api", images, queue_id)` → 200 `MessageResponse`). Verify WC + images now **works** (enqueue carries `images` :400; today's 202 path silently drops them — a documented defect in the FE-latency analysis that this change retires for WC).
2. Update the endpoint docstring routing table :157-163: `RUNNING → 202 injection`; `WAITING_CHILDREN → 200 enqueue (durable, wakes the parked parent)`; PAUSED/IDLE/terminal rows unchanged.
3. Confirm `injection_pending` SSE and the GET `/{id}/injection` fallback (:453+) simply never fire/return for WC (docs comment; no code).
4. **FE-latency interplay (D4 note, no FE code here):** the WC slow case (bubble render delayed to turn end — the dominant slow path in `.agents/shared/planning/message-display-latency/architecture-recommendation.md` §1) disappears from the 202 path entirely; WC sends get a real `message_id` at POST (fixing the FE `MessageResponse.message_id` required-type lie for WC) and the normal turn-start `user_message` pre-emit (:3477-3484). If the message-display-latency arc lands first, its echo-at-POST + id-threading machinery becomes RUNNING-only in scope — no double-echo (200 path never touches `set_injection`). Coordinate landing order in the feature's `decisions.md`.

---

### T5 — D2: seam-drain of parked-FIFO leftovers before the new message

**File:** `daemon/services/instance_messaging.py`, in `_process_message_with_tracking`, before final `graph_input` assembly (after the three `_build_graph_input` sites :3402/:3411/:3420 converge, before :3475/:3530).

**Mechanics:**
1. Drain via the existing manager FIFO API: `pending = manager.get_injection(instance_id)` (:2434) → build `HumanMessage` per entry preserving `additional_kwargs` (`injected_message: True` + optional `source`, mirroring graph.py:2950-2961 — leftovers ARE injections; they keep the marker so C3 compaction preservation and D12 filtering keep working) → `cleared = manager.clear_injection(instance_id)` (:2474). Keep get→build→clear **await-free** (same cooperative-atomicity rule as graph.py:2977-2979).
2. **Requeue safeguard** (cheap, closes the get/clear race): entries present in `cleared` but not in the `pending` snapshot were appended mid-drain by a concurrent `set_injection` — re-append them via a small new manager helper `requeue_injections(iid, entries)` (prepend-order-preserving). This risk pre-exists at graph.py site 1; do not worsen it at the new site.
3. **`_build_graph_input` seam parameter (S4)**: `_build_graph_input` (`instance_messaging.py:176-243`) has NO prepend seam today — extend it with an explicit keyword parameter, e.g. `prepended_msgs: list[BaseMessage] | None = None`, composing the returned list as `[pairing placeholders?] + persistent_context_msgs + leftover FIFO msgs (oldest-first) + [user_message]`. Default `None` keeps all three existing call sites (:3402/:3411/:3420) byte-identical.
4. **Input order (exact, S4 ordering unit test)**: `graph_input["messages"] = pairing_placeholders? + persistent_context_msgs + leftover_fifo_msgs(oldest-first) + [user_msg]` — placeholders only when T6's guard fired; the placeholder must sit immediately after the poisoned checkpoint tail, so it is first in the appended batch; leftovers precede the new message per D2 ("one turn when both exist"). Pin the exact order with a dedicated unit test on the extended `_build_graph_input`: assert the composed list positionally across all four slots (placeholders present/absent × persistent present/absent × leftovers × user).
5. Because the seam now drains, graph.py site 1 finds an empty FIFO on the wake turn — no double-add. Site 1 remains for genuine mid-RUNNING-turn injections (unchanged lane).
6. Side benefit: this also drains leftovers stranded on instances that went IDLE/terminal with a non-empty FIFO (previously waited for the next turn's in-graph drain) — strictly earlier delivery, same semantics.

**Tests:** FIFO has 2 leftovers (with/without `source`) + new message → single `astream` input with order `[persistent?, left1, left2, user]`; leftovers keep markers; user msg unmarked; `manager.get_injection` empty after; requeue safeguard unit test (append between get and clear); crash-window note (drain clears before astream → crash loses leftovers — accepted parity with site 1; record in task notes).

---

### T6 — D1: pairing tail-guard at the enqueue entry seam + R2 regression test

**File:** `daemon/services/instance_messaging.py` (new module-level helper + one call site) and `daemon/graph.py` (export/reuse only — no behavior change).

**Mechanics:**
1. New helper, e.g. `_heal_poisoned_checkpoint_tail(graph, config, graph_input, instance_short) -> list[ToolMessage]`:
   - `state = await graph.aget_state(config)` — pattern already used in this file (:753).
   - Tail-check identical in spirit to `_ensure_tool_result_pairing`'s O(1) happy path: if the tail is **not** `AIMessage` with unanswered `tool_calls`, return `[]`. (Reuse the bounded-walk helper by extracting its inspection core or by calling it on a scratch copy of the tail window — implementation detail; do NOT mutate checkpoint state here.)
   - On poison: synthesize placeholder `ToolMessage`s (R1 ids) and **prepend them to `graph_input["messages"]`**. `add_messages` then checkpoints `[tail AIMessage(tc), placeholder(s), …turn messages…]` in the same superstep commit — the history is healed for this request *and* persisted, with **no** separate `aupdate_state` round-trip.
2. Call it once in `_process_message_with_tracking` after final `graph_input` assembly, immediately before `graph.astream` (:3530). All three graph_input sites and **all enqueue traffic** (HTTP, agent-tool, completion/error reports, watchdog, deferred resumes) are covered by this single choke point — that is the D1 requirement (verified chain, architect §7: dispatch table `task_processor.py:1077-1098` → `ProcessMessageProcessor.process` → `pipeline.execute` `pipeline.py:387` → `_do_process` `:399-400` → this method).
3. **None-skip (S5 / architect correction 2)**: the `:3407` silent-resume branch sets `graph_input = None` (pure checkpoint resume — silent mode or no content). The seam heal/prepend MUST SKIP a `None` graph_input: that path injects no new mid-turn HumanMessage at the seam and is already covered by the in-graph pairing guard (graph.py:2971 / :3145). Guard the call site: `if graph_input is not None: <heal/prepend>`. Unit test: silent-resume invocation (`graph_input = None`) → helper not invoked, nothing prepended, `astream` receives `None`.
4. CLE-mirror convention: record the new seam in the convention comment (graph.py:227-262 block + blueprint note): *any path building an LLM-bound list from checkpoint state runs the guard — in-graph sites, loop-breaker re-append (:3299-3312), CLE rebuild (:3402-3408), and now the enqueue seam.*
5. Cost note: one `aget_state` read per enqueued turn. The pipeline is already checkpoint-heavy (persistent-context + resume reads); the O(1) tail check after the read is free. If profiling ever flags it, gate on a cheap signal later — correctness first.

**R2 (regression test, same PR):** in `tests/unit/graph/test_injection_tool_pairing.py` (or a sibling), a test that drives the **CLE reactive-compaction retry path** (graph.py:3227 rebuild → :3278/:3402-3408 guard) over a checkpoint whose tail is `AIMessage(tc)` and asserts: placeholders synthesized on the rebuilt `compact_messages`, and they flow into the C2 return. This pins the CLE-mirror convention the D1 seam relies on. Plus seam tests: poisoned tail + HTTP-enqueued turn → placeholders prepended (ids `pairing-synth-*`), order `[placeholder, (persistent), (leftovers), user]`; healthy tail → zero placeholders, `graph_input` byte-identical shape.

---

### T6b — D7 (reviewer C3, blocking + architect §7.1 correction 1): DELETE the legacy `:1060` bypass

**Rationale (one line):** every surviving path must cross the T6 choke point and the in-graph pairing guard; this dead method pair silently re-opens the poisoned-tail→2013 exposure that D1 exists to close.

**Files:** `daemon/manager.py:6245-6258` (`Manager.send_message` — def :6245, delegation into the messaging service at :6258) and `daemon/services/instance_messaging.py:1007` (`InstanceMessagingService.send_message`), whose `:1060` `result = await graph.ainvoke({"messages": [message]}, config)` bypasses `_build_graph_input`, the T6 seam choke point, AND the in-graph pairing guard.

**Spec:**
1. DELETE both methods. Zero production callers (architect §7.1 exhaustive grep: test files + one docs example only — re-run the grep at implementation time to confirm nothing new landed).
2. Migrate test fixtures to `enqueue_message`: `tests/test_manager.py` — 8 call sites (:232, :247, :431, :462, :492, :521, :550, :578); `tests/integration/test_inner_soul.py` — 3 sites (:145, :201, :250) and `tests/integration/test_inner_soul_standalone.py` — 2 sites (:258, :357), both via the `Manager.send_message` facade (the old `tests/unit/tools/test_inner_soul*.py` glob matched only inner-soul tool tests — zero messaging callers); `tests/integration/test_agent_bootstrap.py:144`; three omitted direct `InstanceMessagingService.send_message` callers — `tests/unit/test_question_deferred_pause_edge_cases.py` (3 sites: :316, :331, :395), `tests/unit/test_question_deferred_pause_callback.py` (6 sites: :189, :231, :263, :335, :430, :535), `tests/unit/services/test_title_generation_trigger.py` (4 sites: :976, :1016, :1099, :1168) — 13 sites total; `tests/unit/test_phase4_manager_decomposition.py:794-795`.
3. Update the docs example: `docs/features/job-queue.md:1125` calls `self._instance_manager.send_message(...)`.
4. Verify `daemon/api.py:124` re-export is unaffected — it re-exports the HTTP endpoint (`from daemon.routers.messages import send_message as send_message`), a distinct name; pin with an import test.
5. Companion LOCKED register row is being added in parallel — cite **"decisions.md C1 register — legacy :1060 bypass deletion"** (D7) in the PR description.

---

### T7 — D3: `job_inject` (Option A — leader-LOCKED 2026-08-30)

**LOCKED decision:** Option A (§4 status note; `decisions.md` C1-D3 LOCKED row; `architecture-recommendation.md` §8). **Dependency: T7 gates T10 (S6)** — the T10 pure-hang integration test exercises the `job_inject`→enqueue wake lane, so T7 must land before T10 runs.

**Scope (Option A):** `daemon/tools/job_queue.py:1853-1881` — split: RUNNING → `set_injection` (byte-identical behavior/return); WC → optional `has_instance_busy` pre-check (mirrors `job_continue` 5a, :975-995), then `manager.enqueue_message(instance_id, message, source=f"internal_agent:{current_instance_id}")`, return `{job_id, instance_id, status: "enqueued", message_id, queued: True}`. Rewrite eligibility error text (:1858-1864) to "job_inject injects into RUNNING turns; WAITING_CHILDREN/IDLE/terminal targets get the message enqueued (WC) or should use job_continue". Rewrite `_FULL_DOCS["job_inject"]` :352-390. Tests: RUNNING unchanged; WC → enqueue (durable row + WC→RUNNING flip asserted via fake manager); busy pre-check.
**Option B (superseded — audit only):** :1857 set-test → explicit `("running", "waiting_children")` literals + comment referencing this plan; docs updated; TTL-loss residual recorded in critical notes.

---

### T8 — Documentation (D4/D5/D6) + consumer verification

1. D5 verification tasks (mostly assertions, expect no code changes):
   - Wake `HumanMessage` (:3475) carries **no** `injected_message` marker — both lanes.
   - Provenance present on the durable row: `MessageQueue.source` = `"api"` / `"internal_agent:{caller}"`; `MESSAGE_RECEIVED` event data carries `source` (:1554-1570).
   - D12 subtree filter: WC-wake turns (unmarked) are *included* in descendant views as first-class messages; injected (marked) traffic still excluded — add a filter-level test.
   - SSE dedup hash (content/tool_calls/role only) unaffected — assert via existing serialize/dedup tests; `serialize_message` additive-keys grep-shape asserts still pass; `watcher_context_builder` distinct serializer untouched.
2. D6 documentation: agent-tool busy gate (:2861-2867) now trips for a WC target while its wake Task is queued (MessageQueue-row counts; window = enqueue→claim); `job_continue` 5a (`has_instance_busy`) likewise. HTTP lane has no busy gate — multiple WC sends queue multiple first-class turns (accepted). Write this into `_full_doc_` (T3), the endpoint docstring (T4), and the feature `decisions.md`.
3. **S10 — FE zero-change checklist (verify, do not assume)**: FE 202/200 handling branches on `response.queued`, not the status code — `frontend/src/app/pages/chat/chat.component.ts:1153-1160` treats both 200 and 202 as 2xx success, and the queued indicator runs off `response.queued` (the `queuedMessage` signal, chat.component.ts:174-189) plus the `injection_pending` SSE shape (`frontend/src/app/services/sse.service.ts:16-31`). Checklist: (a) WC 202→200 keeps the `subscribe` success path; (b) `queued=true` drives the existing indicator for WC sends; (c) WC no longer emits `injection_pending` — confirm no WC-specific FE branch depends on it (the pendingInjection card becomes RUNNING-scoped post-change); (d) run the FE locally against a WC target once before sign-off.
4. Update `.agents/shared/context.md` + critical-notes candidate after landing.

### T9 — W5: ordering-race test updates

Grep the suites from §5.6 for assertions that a user message is absorbed into the report turn (single-turn) when sent while WC, and update to the two-turn claim-order semantics: both rows claimable, `created_at ASC` decides (repository.py:1486), second turn runs after the first completes. Keep one test asserting the FIFO-leftover path (T5) still yields a single turn for *pre-existing* injections — that is the invariant W5 deliberately does *not* extend to new enqueues.

Additions (reconciliation pass):
- **S9 — terminal-after-turn-1 edge**: the claim pause gate (`task/repository.py:1414-1428`) excludes only PAUSED/TERMINATED instances — a queued user-msg Task still CLAIMS on a parent that went COMPLETED after turn 1 (the terminal-revive path in `_prepare_enqueued_message` :1527-1545 then reactivates it). Pin this W5 edge with a test: queued Task + parent COMPLETED → claim succeeds, revive runs, turn executes — documenting that terminal-after-enqueue does not silently strand the row.
- **S13 cross-ref**: the T3 busy-gate ERROR-text pin covers the WC-with-queued-wake caller experience inside the two-turn window; reference it from the W5 test notes so the two contracts are read together.

### T10 — Full verification batch

1. `uv sync` sanity (dev group), then targeted: `pytest tests/unit/graph/test_injection_tool_pairing.py tests/unit/tools/test_instance_tools.py tests/tools/test_send_message_status_guard.py tests/test_injection_api.py tests/test_injection_sse.py tests/unit/routers/ -x`, then the full unit suite (`-n auto` per repo convention) + `tests/integration/test_boot_report_recovery.py::TestBootSmokeRegression` (we add **no** `manager.__init__` attrs — this guard should stay green; verify).
2. Re-verify every edit with grep/diff after each batch (repo multi-edit verification discipline; write-then-verify, count==1 assertions for scripted edits).
3. Acceptance corollary (from the blueprint's park/wake section): one **pure-hang integration test**, parameterized over **ALL THREE wake surfaces** (S6 / architect correction 3): (a) HTTP `POST /messages`, (b) agent-tool `send_message`, (c) `job_inject`→enqueue (requires T7 — the explicit T7→T10 dependency). Each variant: single hung child, parent WC, no sibling termination, wake message → assert WC→RUNNING flip + real-engine turn consumes the message + child's later report delivers. Service-boundary mock tests do not catch this defect class; this is the test that proves the feature.

## 7. New/updated test inventory (summary)

| Test | File | Kind |
|------|------|------|
| R1 placeholder id format + dedup | tests/unit/graph/test_injection_tool_pairing.py | unit |
| R2 CLE-retry guard regression | same file | unit |
| D1 seam: poisoned tail + enqueue → placeholders prepend; healthy → none | new tests/unit/… (pipeline-level, fake graph) | unit |
| D2 seam drain: order, markers, requeue safeguard | new, next to pipeline tests | unit |
| Set pin `{"running"}` | tests/unit/tools/test_instance_tools.py:131 | unit |
| Routing maps: WC → enqueue (tool + HTTP + job_inject per D3) | test_instance_tools.py, test_send_message_status_guard.py, tests/test_injection_api.py | unit |
| HTTP WC → 200 `MessageResponse{message_id, job_id, queued}`; RUNNING → 202 unchanged; PAUSED unchanged | tests/test_injection_api.py (+ new) | unit |
| D5 marker/provenance assertions (incl. D12 filter) | new + test_serialize_message.py-adjacent | unit |
| D6 busy gate on queued WC wake; job_continue 5a | test_instance_tools.py / job-queue tests | unit |
| W5 two-turn ordering | integration-adjacent update | unit/int |
| Pure-hang WC wake (real engine) — **ALL THREE wake surfaces**: HTTP lane, agent-tool `send_message`, `job_inject`→enqueue (S6; T7→T10 dependency) | new tests/integration/… | integration |
| WC+images works post-change (S11: enqueue carries `images` at messages.py:400; today's 202 path silently drops them — FE-latency analysis defect #2) | HTTP endpoint tests (extend) | unit |
| Busy-gate ERROR text verbatim on WC-with-queued-wake (S13) | test_instance_tools.py (extend) | unit |
| Terminal-after-turn-1: queued Task claims on COMPLETED parent (S9) | claim-gate tests (extend) | unit |
| T6b/D7: legacy `:1060` bypass deleted; fixtures migrated to `enqueue_message`; `api.py:124` re-export import intact | tests/test_manager.py + migrated fixtures | unit |

## 8. Risks / open questions

**Risks**
1. **D6 agent-facing surprise**: LLM callers may hit the busy ERROR on WC targets during queue saturation. Mitigation: `_full_doc_` text (T3) is the LLM-visible contract; error text already tells the agent to wait.
2. **W5 two-turn latency** for parent+user simultaneity — accepted by the leader; tests updated (T9).
3. **D1 per-turn `aget_state` cost** — one extra checkpoint read per enqueued turn; measured-acceptable; optimization seam documented.
4. **D2 crash window** (drained-but-not-streamed leftovers lost on crash before astream) — parity with the existing site-1 exposure; noted in T5, not worsened.
5. **CI red if the set-pin and consumers land in different commits** — T2 must land atomically with T3/T4 test updates (single PR).
6. **Landing-order interplay** with `feature/message-display-latency` — either order works (§6-T4.4); record the decision.

**Open questions for the leader** (updated 2026-08-30 reconciliation pass)
1. ~~D3: Option A (recommended) or B?~~ **RESOLVED — Option A LOCKED 2026-08-30** (§4 status note; `decisions.md` C1-D3 LOCKED row; `architecture-recommendation.md` §8).
2. ~~Kill-switch or clean cutover?~~ **RESOLVED — kill-switch semantics (leader, 2026-08-30; `decisions.md` C1-Q2):** ships as a config flag (naming candidate `ENSEMBLE_WC_WAKE_ENQUEUE`), **OFF during soak**, **flipped per the D2.5-FLIP policy**. The "clean cutover" default is superseded.
3. Confirm T5's input order `[placeholders] + [persistent] + [leftovers] + [user]` (placeholders-first is forced by the poisoned-tail adjacency; persistent-before-user preserves the existing `_build_graph_input` contract).
4. Confirm D2's seam drain applies at *every* enqueued turn start (not just post-WC) — recommended yes; it strictly subsumes the IDLE/terminal leftover case.

## 9. References

- Blueprint: Core Architecture §Park/Wake Primitive Selection; Daemon Core Execution §Tool-pairing guard + CLE-mirror rule; Pause/Resume Report Delivery §marker serialization (W1).
- Prior arc: 84fd8018 + 7822aebd (tool-pairing follow-ups: R1, R2 originate here).
- FE interplay: `.agents/shared/planning/message-display-latency/architecture-recommendation.md` (202-path analysis; §1 defects #1-#3 all retired-for-WC by this component).
- Feature planning dir: `.agents/shared/planning/wc-wake-report-integrity/` (this file is `phase1-plan.md`; `decisions.md` now exists and carries the C1 register — C1-D3 LOCKED 2026-08-30, plus the parallel D7/:1060-deletion row).
