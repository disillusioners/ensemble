# Agent Group Chat — Phase-1 ARCHITECTURE RECOMMENDATION

**Doc-set Rev 6** (single alignment stamp across all five docs; 2026-11-15 final fix pass: D1–D5 + iteration-003 notes applied). **Status:** COMPLETE (Phase-1 design; planning-only — no code, no git ops per D-S7).
**Date:** 2026-11-15 · **Commission:** Emperor directive via Ari.
**Method:** Architect controller → 🏛️ substrate council (2/2, `agentic`+`coding`, UNANIMOUS, governor `0e8f6375`) + 4 parallel dimension analysts; architect aggregated and writes this file.
**Evidence base:** substrate-map.md §A–§J (tip `bf4bfde6e`) + in-worktree verification. **Rev-6 corrections each preceded by a direct read at pin:** 10-state enum `models.py:20-35` (idle, running, waiting, paused, completed, error, terminated, queued, waiting_children, failed — **`waiting` = "Active but no in-flight work"**); `task_context` stamp `instance_messaging.py:5157-5162`; language-check `create_language_check_node` :4758 / skip :4770 / fail-open :4807-4812; DEFECT-A guard at the router call site `instance.py:4792-4816`; F-1 predicate SQL `message_queue/repository.py:1312-1322` (block within :1289-1330); post-graph chain definition `manager.py:11703` (+ facade :11717, call sites :8368/:11880/:15746, internal `services/child_reports.py:1019`); END-chain region graph.py:13378-13415; wrapper quartet :4942-4952 / :4984-4986 / :4995 / :5040, cap :4933; `TurnRepairLatch` graph-builder-local :13159; todo reminder-return todo_manager.py:403-461 + `_compute_reminder` :1198-1276; context kinds = 14; `ALIVE_INSTANCE_STATUSES` constants.py:1066-1072; `MISSION_RECEIPT_SCAN_CAP` daemon/services/midflight_qa.py:194; `QA_WATCHER_ROW_CAP` daemon/services/work_notifier.py:1339 (def :1583); hoisting compaction.py:337/:425/:3233 (budget :1404, threshold :1589, caps :1650); `CHECKPOINT_TTL_HOURS` constants.py:170; `daemon/loader.py:972/:1356`; `services/skill_meta_parser.py:94`; TitleGenerationService class :17.
**⚠️ DEGRADED — skill bank miss (design-skill family):** design skills absent from the project bank (known gap); manual discipline prompts used; flag stands.
**Companion:** emperor-open-questions.md (7 items + 3 adaptation disclosures).

---

## 0. Verdict at a glance

- **A Substrate (D-O4):** **Hybrid (iv)** — NEW minimal 3-table store (one `.sql` — 3 CREATE TABLE + 1 comment, 130→131) + every existing lane reused as transport. `message_queue`/`shared_meta_kv` untouched.
- **B Lifecycle (D-O1):** active→closed→archived; **ONE published capability row** (§2.1); close = parent tool only; GC = one maintenance-seam job (whole-room; P-close 14d / P-idle 72h + zero non-terminal among members ∪ owner ∪ owner-ancestry — **including `waiting`**); accepted loss names EXACTLY `archived`.
- **C Message model:** BoardNote-adapted log; per-room seq under the LRU lock + `UNIQUE(room_id,seq)`; **TWO cursors on every participant row — including the CREATOR, who is seeded as an owner membership row at room_create (D2)**; lateral read predicate (state-aware); re-derivation predicate `seq > max(last_read_seq, last_delivered_seq)`.
- **D Delivery:** D-S4 router; **lane map TOTAL over the 10-state enum (D1, pasted in §4b)**; tagged durability = log+cursor re-derivation; **proposals are ONE HOP — a terminal parent leaves a durable pending proposal, never a revive, never a recursive re-route (D3)**; nudge (a) on the every-update todo reminder; (a2) gate chain `language_check → room_completion_gate → attestation_gate → END`; ONE manager-level shared latch.
- **E Joined state:** `CONTEXT_KIND_JOINED_ROOM`, supersede, hoisting (compaction.py:337/:425), 14 kinds; caps 2000/4000.
- **F Naming:** 4-tier cascade; T=3.0s bounded quick-model (bounded SUCCESS-path wait, each spawn NOT blocked on failure — ratification line, emperor #6); suffix at registration.
- **G Tasking:** proposals via D-S4; no verb mints work; one-hop rule; milestones never auto-escalate.
- **H Visibility:** members (incl. owner) ∪ owner-ancestry (fail-closed, state-aware); HTTP role scope; SSE observability-only; content-free `room_*` EventKinds (registry in P1).
- **I Concurrency/cost:** sequential fan-out; `ROOM_MAX_MEMBERS=16` (**active rows only — LEFT rows do not consume seats; owner counts**), `ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8`; static caps (post **≤4000** chars); 8000/turn ONE-composer budget; naming ≤3.0s minority path.
- **J Boot:** outside F-1 blast radius; **pinned pair (D4):** regen re-derives TAGGED undelivered rows for MEMBERS incl. the OWNER row (active+closed, never archived) and does NOT route/wake/revive parent-directed proposals.

---

## 1. Substrate choice (A · D-O4)

**VERDICT — Hybrid (iv).** Council 2/2 UNANIMOUS. Core principle: the log write is the commit point; delivery is best-effort on top of it.

### 1.1 Five-axis matrix

| Approach | Cplx | Scal | Maint | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| (i) message_queue + room-id | HIGH | MED | LOW | HIGH | HIGH | **FAIL** — per-recipient delivery mechanism asked to be an N-reader log; its boot machinery destroys the log (predicate SQL `message_queue/repository.py:1312-1322`); Task-per-recipient enqueue; no seq; its enqueue auto-revives terminals (`instance_messaging.py:2537-2579` — Ruling-A hazard) |
| (ii) shared_meta_kv rooms | MED→HIGH | LOW | LOW | HIGH | MED-HIGH | **FAIL as log** — LWW, 4KB, no seq, no GC, no notification; watermark inexpressible |
| (iii) new subsystem alone | MED | HIGH | HIGH | MED | MED | **HOLDS as storage** — mints the missing primitives but re-builds delivery (RK-11) |
| (iv) **hybrid** | MED | HIGH | HIGH | MED | MED | **RECOMMENDED** — same substrate + lanes that exist |

Cost line: ≤3.0s spawn latency on the naming tier-2 minority success path (dominant path ~0; failure path never blocks — deterministic fallback instant); constants env-tunable.

### 1.2 Why the simpler options fail

- **(i)** F-1 doomed predicate (SQL `message_queue/repository.py:1312-1322`; block within :1289-1330; orchestration `manager.py:1407-1600`) wipes fully-delivered rows at `discard_on_startup`; enqueue mints Task per recipient (`instance_messaging.py:2130+`); no seq; ordering ties (`repository.py:330/:348`); "completed" = delivered; enqueue auto-revives terminal (:2537-2579).
- **(ii)** LWW; >4096 hard-fail; no seq/history/GC/notification; create_all-no-migration deviation (`manager.py:1205-1214`). Auxiliary state only.
- **(iii)** correct storage; alone re-builds delivery.

### 1.3 Schema (one `.sql`, 130→131; multi-table-single-file precedented — the board migration carries 3 tables; **this migration: 3 CREATE TABLE + 1 explanatory comment**)

| Table | Shape | Purpose |
|---|---|---|
| `chatrooms` | `room_id` UUID PK · `name` · `tree_root_id` (indexed; enforced by `_validate_room_join`) · **`owner_instance_id`** (canonical opener id) · `state (active\|closed\|archived)` · `last_post_at` · counters · timestamps | identity + scope + lifecycle |
| `chatroom_members` | `(room_id FK NO ACTION, instance_id)` unique · `member_name` · **`UNIQUE(room_id, member_name)`** · `agent_id` · `role (owner\|parent\|child)` · `joined_at` · `last_read_seq` · `last_delivered_seq` · `state (active\|left)` | roster truth; **the cursor substrate for EVERY participant — including the owner (D2)**; naming tier-4 |
| `room_messages` | BoardNote shape (`models/board.py:301-345`) · `room_id` FK NO ACTION · `seq BIGINT` · **`UNIQUE(room_id, seq)`** · `author_instance_id` + `author_name` denormalized · `text` · `tagged_members` JSONB · `kind (message\|system\|task_proposal\|task_decision)` · `created_at` | durable log |
| *(no 4th table)* | *(watermark table folded into `chatroom_members`)* | cursors |

**D2 — THE OWNER MEMBERSHIP SEED (stated once, referenced from §2/§3/§10 + D-O4):** `room_create` mints a `chatroom_members` row for the CREATOR in the same transaction as the `chatrooms` row: `role=owner`, `member_name` = the creator's `instance_name` if set else `owner` (same per-room suffix registry), cursors seeded 0. **The parent is thereby a first-class delivery target** — tagged `@parent` rows and `task_proposal` rows re-derive from the owner's cursors after any RAM-lane loss; owner unread/digest are defined; the roster includes the owner; the membership cap counts the owner row; the owner row is retired only WITH the room (leave does not apply to `role=owner`). This closes the pre-existing-creator gap: join-at-spawn-only cannot enrol the creator, so the seed is the ONLY way the creator gets cursors — and D-O4's at-least-once guarantee is otherwise broken for the most common proposal target.

**Seq:** per-room `asyncio.Lock` LRU registry (port of `sources/dispatcher.py:451-452/:810-838`; MAX_ROOM_LOCKS=1000) + single-statement `INSERT … COALESCE(MAX(seq),0)+1 …` + UNIQUE backstop; multi-process hardening documented, not v1.
**Rollback:** additive-only; rollback = drop 3 tables + delete migration row; `rollback_safe=true`.
**Boot safety:** doomed predicate over message_queue × task only; assertion pin test.

### 1.4 Alternatives rejected
**DB SEQUENCE / per-room sequence table — inline reason:** per-room sequences require DDL per runtime room (violates the migration doctrine), and a global sequence destroys read-cursor locality; the app-level room lock + single-statement insert + UNIQUE backstop suffices for single-daemon v1 (see RK-13). · shared_meta_kv · room row on message_queue · synthetic instance rows · tree-root read scope · **filesystem flock log (`tools/filesystem.py:1451` `_atomic_write_text`)**: per-instance file locality cannot express N-reader room scoping; DB append already gives transactional seq+cursor semantics.

---

## 2. Room entity + lifecycle (B · D-O1)

**Creation & admission (B2'/iteration-002):** parent via `room_create` (creator non-terminal; binds `tree_root_id`; records `owner_instance_id`; **seeds the owner membership row — D2, §1.3**; fails closed at `ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8`). **`_validate_room_join(joiner, room_id)`:** room exists ∧ state=active ∧ `get_tree_root_id(joiner) == room.tree_root_id` ∧ **active membership rows (owner included) < `ROOM_MAX_MEMBERS` (LEFT rows do NOT consume seats)** ∧ active-rooms-per-tree < cap; **typed fail-closed spawn errors**. No cross-tree enrolment by UUID knowledge. Grant scope: privileged category + per-agent `tools.allow` + in-body fail-closed gate.

### 2.1 THE PUBLISHED CAPABILITY ROW (the ONE definition of `closed` — cited verbatim from §3, §5, §10, decisions D-O1, emperor item #4)

| Capability | `active` | `closed` | `archived` |
|---|---|---|---|
| read tool | yes (member(state=active) / owner-ancestry) | **yes — read-only** | no — "room archived" announcement |
| posts | yes | **no — typed "room closed" error** | no — typed "room archived" error |
| digest block | yes | **yes (read-only variant; shows `unread: N`)** | **excluded entirely** |
| unread computed | yes | **yes** | **no (excluded)** |
| nudges (a)/(a2) | yes | **no (closed rooms are quiet; the digest carries unread)** | no |
| tagged re-derivation (turn + boot) | yes | **yes — until archive (at-least-once preserved through the closed window)** | no |
| delivery guarantee | at-least-once (log+cursor) | **at-least-once for TAGGED until archive; untagged via digest only** | **none — accepted loss** |

**Accepted-loss statement (names EXACTLY `archived`):** unread/tagged rows in an **ARCHIVED** room are never delivered and never resolvable. **Closed rooms lose nothing the log+cursors cannot re-derive.**

### 2.2 Lifecycle & GC
- States forward-only; close = parent tool only; **no auto-close on parent-terminal** — reconciled with P-idle: revive-and-continue works while ANY ACL participant is non-terminal; 72h of total silence ends that budget (accepted; fresh room later).
- **GC:** one `MaintenanceService.register()` job (`services/maintenance.py:141`; call sites `manager.py:4735/:4752`), 6h min-interval, SourcesGC-shaped (repo fence, dry-run first, copy-then-delete per `20261107_122648`).
  - **P-close:** `state=closed AND closed_at < now−14d` → whole-room archive.
  - **P-idle:** `state=active AND last_post_at < now−72h AND zero non-terminal instances among {members(state=active) ∪ owner ∪ owner-ancestry chain}` — **non-terminal = the 6-status complement of {completed, error, terminated, failed}: {idle, running, waiting, paused, queued, waiting_children} — `waiting` IS alive (D1)**.
  - Cursor guard: permitted on P-idle only (skip rooms holding undelivered TAGGED rows above any participant's delivered cursor); P-close = whole-room with the accepted-loss column.
  - Non-terminal set = ONE shared constant consulted by GC and boot regen; enum-totality pin (§4b).
- **Membership: HYBRID** — table = roster truth + cursor substrate (owner row seeded at create); `instance_metadata["join_room"]` = spawn hook + restore re-read. `instance_hierarchy` REJECTED.
- **D5 — RESTORE TWO-CONJUNCT RULE (stated once; mirrored in §3 predicate + leave semantics):** **restore re-grants room tools only if `chatrooms.state ∈ {active, closed}` AND `chatroom_members.state = active` — BOTH conjuncts; a LEFT member's restore grants nothing** (pin test: left-member restore → no room tools). Read in `closed` = read tool only; `archived` = no room tools (digest reports archived).
- **Leave:** membership row `state=left` (owner exempt — retired only with the room); LEFT rows do not count against the join cap; the JSONB echo is tolerated (inert).
- **Join-later (N7):** join-at-spawn v1; spawn-with-join at cap/unknown/archived/cross-tree = typed fail-closed; late join of a RUNNING instance deferred.
- **Warm-vs-late-join:** warm reuse INSIDE a room = revive/wake an existing member (supported); adopting a pre-existing instance into a NEW room = v1-deferred.
- **Survival:** revive (`instance_messaging.py:2538-2572`), pause/resume, restart — membership survives; restore re-read per D5.

---

## 3. Message model (C)

- **Interface:** `append` · `read` · `unread(room, participant) → COUNT(*) WHERE seq > last_read_seq` · `advance_read/advance_delivered` (monotonic MAX guards). **Every participant has a cursor row — including the owner (D2).**
- **COUNT rationale:** rows persist for the room's entire ACTIVE and CLOSED life (capability row: unread computed in both); archived rooms are excluded from the unread path entirely — the formula never meets deleted rows; COUNT is also robust to any future partial-trim policy within live states.
- **Two cursors:** `last_read_seq` = the only read ack; `last_delivered_seq` = delivery floor advanced on lane admission (and on Ruling-A proposal admission — Y14, §4c).
- **Re-derivation predicate:** a row needs delivery iff `seq > max(last_read_seq, last_delivered_seq)` for that participant.
- **Read predicate:** `_validate_room_read(caller, room)` — **`chatroom_members.state = active` member (owner included) ∨ owner-ancestry chain**; state-aware per the capability row (active/closed → allow, archived → deny-with-announcement); fail-closed on degenerates. (`_validate_subtree_target` `instance.py:1917-1981`, deny :1969 — rendering shape only.)

---

## 4. Delivery mechanics (D)

**Router:** branch order PINNED: lane assignment FIRST (per the total map), and the room router owns branch (c) — it never calls `_route_send_message` (def `instance.py:1610-1728`).

### 4.1 THE LANE MAP — TOTAL OVER THE 10-STATE ENUM (D1; pasted once, cited by §2.2/§10/decisions D-S4/plan P3)

Enum source: `InstanceStatus` (`repositories/instance/models.py:20-35`): `idle · running · waiting · paused · completed · error · terminated · queued · waiting_children · failed`.

| Status(es) | Lane | Behaviour |
|---|---|---|
| `completed`, `error`, `terminated`, `failed` | **(c) parent proposal (Ruling A)** | proposal → parent via (a)/(b); one hop (§4c) |
| `paused` | **(d) park + audit** | `room_tag_delivery` outcome=`parked_paused`; no auto-resume |
| `running` | **(a) injection** | `has_live_graph_task` → `set_injection`; graphless-running → DEFECT-A downgrade to (b); rowcount-0 race → one bounded retry, then `deferred_cursor` |
| `idle`, `queued`, `waiting`, `waiting_children` | **(b) enqueue-family under the status CAS** | CAS allowed set derived FROM THE ENUM: **all non-terminal statuses − {running, paused} = {idle, queued, waiting, waiting_children}**. `waiting` = "Active but no in-flight work" — a LIVE awaiting-input agent: **never misrouted to (c)**, never dropped |

**Totality pin:** a test asserts every enum member maps to exactly one lane (union = all 10; a future enum addition reddens). **`waiting` is in the CAS set and in boot regen; `waiting` counts as alive in GC liveness.** Derivation is from the ENUM, not from `ALIVE_INSTANCE_STATUSES` (which omits `waiting` — constants.py:1066-1072).

### 4.2 Lane details
- **(a)** `set_injection(..., source="room:{room_id}:{sender_name}:{seq}")` (stamp `graph.py:8765-8773`); **DEFECT-A guard at the router call site `instance.py:4792-4816` copied**; content = NOTIFICATION (≤300-char lead + pointer).
- **(b)** the CAS is the room's own status update (`SET status='running' WHERE status IN (<the four>)`) — defeats the enqueue's terminal auto-revival (`instance_messaging.py:2537-2579`). **Exhaustive rowcount-0 fallback** (the total map above is the fallback map: fresh-read status is one of the 10 ⇒ its row dictates the branch; no unassigned case exists). Busy-guard: the copied context-fallback shape (`instance.py:4895-4930`) re-checks fresh status then injects-or-enqueues — **"never silently dropped"** (infra failures surface as errors; Y2).
- **(c)** Ruling A: proposal row + parent delivery; **Y14 cursor rule:** the terminal member's `last_delivered_seq` advances at proposal ADMISSION; duplicates prevented by `tagged_message_id` uniqueness on proposal rows.
- **(d)** as mapped.
- **D3 — THE ONE-HOP PROPOSAL RULE (stated once; cited by §7 and §10):** *"A proposal whose target parent is TERMINAL has exactly ONE terminating outcome: it stays durable as a pending `kind:"task_proposal"` row — surfaced when the parent next runs through any legitimate lane (a human/HTTP send, or its own scheduling) — it NEVER auto-revives the parent and NEVER re-routes upward: proposals are ONE HOP, never recursive. This rule applies identically to proposals minted at boot."*
- **Tagged durability:** re-derive from log+cursors; predicate = §3; no pending table, no enqueue mirror.
- **Untagged → IDLE:** no wake; digest at next turn (ratification #2).

### 4.3 Nudge family
- **(a) reminder carrier:** EVERY todo-manager update returns `{"todos": [...], "reminder": str}` (`todo_manager.py:403-461`; `_compute_reminder` :1198-1276); room nudge appends when guards pass; filter = shared latch + watermark, NEVER done-only. *(Adaptation #3.)*
- **Shared latch:** manager-level RAM registry keyed `(instance_id, room_id)`, threaded into both closures (graph-builder-local `TurnRepairLatch` :13159 cannot serve the tool-side arm); `last_nudged` RAM payload.
- **(a2) `room_completion_gate`:** **chain `language_check → room_completion_gate → attestation_gate → END`** (region `graph.py:13378-13415`); disabled-language seam: between the base terminal router and the attestation wrapper; the gate's own path map is `create_should_continue`-shaped **with an explicit `"agent"` key** (`{"agent": "agent", END: <attestation wrapper or END>}`). Gate body cites `create_language_check_node` :4758 (skip logic :4770), fail-open :4807-4812. **Strong form:** tagged pending at completion ⇒ reminder names the @tag AND the CONTINUE verdict re-enters so lane (a) re-attempts the tag injection; the re-entry consumes at most ONE continuation slot per turn (≤1 reminder + ≤1 tag injection) against `INJECTION_CONTINUATION_MAX=8` (`graph.py:4933`) — leaving the remaining 7 for other injection kinds; P4 pins the interaction test.
- **Demoted drain backstop:** terminal-verdict peek (`graph.py:4955-5040`, cap :4933) under the SAME latch.
- **Post-graph fact:** definition `manager.py:11703` (facade :11717 → `ChildReportsService`); post-graph call sites `manager.py:8368`, `:11880`, `:15746`; internal parent-update `services/child_reports.py:1019`. ⇒ nudge must fire IN-GRAPH; post-graph too late.

---

## 5. Joined-state injection (E · D-O5)

- `CONTEXT_KIND_JOINED_ROOM` (context_messages.py:2558; `_make_context_message` :181; **14 kinds verified**), stable id ⇒ supersede (memoir_refresh :134-173). *(Adaptation #2.)*
- Content: own name FIRST; guidelines (+ warm steer); **roster includes the owner row (D2)** ≤600; digest last-3 + `unread: N` (computed only for active+closed per the capability row); banner. Caps ≤2000 soft / 4000 hard.
- Compaction: hoisting `compaction.py:337/:425/:3233`; budget :1404; threshold :1589; caps :1650.
- Multi-room: v1 one room/instance; builder multi-room-capable.

---

## 6. Naming (F)

| Tier | Mechanics |
|---|---|
| 1 Parent-provided | roster read + suffix under room lock; <10ms |
| 2 Quick-model inline | `model_quick` field (config.py:172-182; bypass :449-458); TitleGenerationService shape (class `services/title_generation.py:17`; region :75-130; belt-and-braces :137-146); **sync-bounded wall_clock_cap_s=3.0 + wait_for** — bounded SUCCESS-path wait; failure path never blocks (instant fallback) |
| 3 agent-name+N | instant |
| 4 Per-room suffix | registration under room lock; first bearer bare; colliders smallest-free `-N`; UNIQUE(room_id,member_name) backstop; **owner's member_name assigned via the same registry at room_create (D2)**; final name in the spawn toolcall result (`instance.py:3745` region) |

@tag: exact per-room match; no fuzzy; UUID fallback iff roster-active; unresolvable/non-member → post succeeds, delivery skips, warning; cross-room fails closed. Gate: room clause beside `_check_team_membership` (`tools/_auth.py:90-256`; :169/:173); **fails CLOSED — the `if target_agent_id:` skip `instance.py:4723` is NOT inherited**; RK-2 pin + counter-mutation.

---

## 7. Tasking + parent authority (G)

Proposal (`kind:"task_proposal"`, `tagged_message_id` unique) → parent **via the owner/participant cursors (D2)** → `task_decision`. **Parent-target matrix:** parent ACTIVE/RUNNING → lane (a); parent IDLE/`waiting`/other alive → lane (b); parent PAUSED → park + audit (mirrors (d)); **parent TERMINAL → the ONE-HOP rule (§4.2 D3): durable pending proposal, surfaced at the parent's next legitimate run, never revived, never re-routed.** No room verb mints work. Milestones ride `daemon/tools/midflight_report.py:64`; never auto-escalate.

---

## 8. Visibility / privacy + UI / audit (H · D-O2, D-O3)

- Agent-tool read = active members (owner included) ∪ owner-ancestry (state-aware per §2.1); HTTP = `instances:read`; SSE observability only (+room-resolution pre-branch, `routers/messages.py:960-963`; hub `services/live_event_hub.py:15/:150/:175`).
- `room_*` EventKind additions in **P1** (registry before emission); content-free rows (post/join/leave/tag-delivery incl. `parked_paused`/`deferred_cursor`/proposal/decision); nudges not audited. **Enum range note:** `repositories/event/models.py:27-54` is the ILLUSTRATIVE range — the EventKind enum extends additively beyond it (additive members precedented).

---

## 9. Concurrency + cost discipline (I)

- Sequential fan-out, per-member isolation (`services/work_notifier.py:1496`); failure degrades to nudge-only.
- Caps: `ROOM_MAX_MEMBERS=16` (**active rows only; owner counts; LEFT rows free**) + `ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8` — typed fail-closed.
- Write ordering: per-room lock for seq+append; LRU port; one INSERT.
- Off-loop: 9 paths span P1–P6, each phase owns its own (digest builder async day one; exemplar `instance_messaging.py:1179-1184`).
- Cost: **post ≤4000 chars** · ≤4 posts/member/turn · no daily quota · 8000/turn ONE-composer budget (tagged ≤4000×≤2, digest ≤2000, nudge ≤200; overflow tagged→digest→nudge). Naming ≤3.0s minority path.
- To_thread / EventKind / category-mint phase placements: P1 / P1 / P5 respectively (see §13).

---

## 10. Boot story (J) — THE PINNED PAIR (D4)

- **By construction:** doomed predicate over `message_queue` × `task` only (SQL `:1312-1322`); room tables are a different table/PK space; assertion pin test. FORBIDDEN: taskless READY room rows.
- **PINNED PAIR (replaces the two former contradictory sentences — one statement, no variants):**
  1. *"Boot regen re-derives TAGGED undelivered rows for MEMBERS — INCLUDING THE OWNER ROW — per the capability row: active and closed rooms, never archived; the predicate is `seq > max(last_read_seq, last_delivered_seq)`; lane assignment follows the total 10-state map (§4.1) with the §9 caps."*
  2. *"Boot regen does NOT route, wake, or revive parent-directed proposals: a proposal for a terminal parent remains a durable pending row under the one-hop rule (§4.2/D3), surfaced only when the parent next runs through a legitimate lane."*
- At-least-once re-derivation is correct (dedup by seq per participant). Orphan rooms → P-idle at next tick. Hard-reboot mid-fan-out: same re-derivation.

---

## 11. Risk register response

| ID | Response |
|---|---|
| RK-1 anchor drift | Re-read at pin each pass; Rev-6 refinements: task_context stamp :5157-5162; language-check :4758/:4770/:4807-4812; DEFECT-A :4792-4816; F-1 SQL :1312-1322; (Rev-5: 12 cites — router :1610, WC :1572, midflight_qa :194, work_notifier :1339/:1583, hoisting compaction.py:337/:425, constants :170, skill_meta_parser services, title-gen class :17, loader path, post-graph chain). |
| RK-2 fail-open | fail-closed gate; skip at `instance.py:4723` not inherited; pin + counter-mutation. |
| RK-3 push-bloat | notification-only; supersede+hoisting; ONE-composer. |
| RK-4 boot | §10 pinned pair + assertion pin + capability-row scoping. |
| RK-5 growth | whole-room GC; accepted loss EXACTLY archived; room-count cap; **`waiting` counted alive** in liveness. |
| RK-6 tag volatility | log+cursor re-derivation (Y1); **owner cursors exist (D2)** so @parent/tagged re-derivation is real. |
| RK-7 tool-mint | category #23 pinned to P5 (14-file budget; 22→23). |
| RK-8 baseline | context-kind only. |
| RK-9 authz | room clause + `_validate_room_join` + **D5 two-conjunct restore** + LEFT rows out of the cap. |
| RK-10 nudge durability | RAM registry latches; durable cursors P1 (incl. owner). |
| RK-11 over-build | deferrals explicit; cursor guard on P-idle only. |
| RK-13 | single-daemon assumption + bounded naming latency; RK-12 lives in plan-overview (fan-out/ordering; its response is §9 — cross-reference line added in plan-overview §7). |

---

## 12. Build-vs-reuse ledger

Counting convention: one row per coined/extended artifact; the 3 tables count as three rows; the room-ACL predicate pair counts as ONE row; totals **[R] 17 · [A] 11 · [M] 14 = 42** — reconciled with the approver's count, no difference.

**[R] 17** (injection lane+drain; live-graph gate; DEFECT-A guard (call-site :4792-4816); busy-fallback shape; enqueue atomic trio; `source=` stamp; drain terminal wrapper; todo reminder-return carrier; language-check HOLD shape; TurnRepairLatch shape; context-kind lane+supersede+hoisting; mid_flight_report; EventKind audit; LiveEventHub publish; QA-watcher isolation; tree-root walk; MaintenanceService seam).
**[A] 11** (BoardNote → room_messages; per-key lock → per-room; `_route_send_message` → inverted+cas'd branch; team-gate → room clause; subtree → rendering; restore re-read → join_room hook; metadata JSONB → join_room; TitleGeneration → naming; SourcesGC → room GC; SSE route → pre-branch; job_inject contract → room envelope).
**[M] 14** (3 tables; one `.sql`; Ruling-A branch + one-hop rule; room ACL predicate pair; room_create (+ **owner membership seed, D2**); room_post; room_read (+#23); JOINED_ROOM builder; push composer; completion gate; boot regen pass; `model_quick`).

---

## 13. Phase-2+ shape

| Phase | Content |
|---|---|
| P1 | 3 tables + cursors + one .sql · per-room lock · two cursors + re-derivation helper · **owner-seed** write path lives in P2 (room_create is P5; the SEED rule is schema-level) · boot regen pass (pinned pair D4) + assertion pin · enum-totality pin · lifecycle columns · EventKind registry additions · P1-scope off-loop paths |
| P2 | join param → membership + JSONB one tx · `_validate_room_join` + exit pins (cross-tree, cap incl. owner-row counting and LEFT-row exclusion, unknown/archived, room-count) · 4-tier cascade · restore re-read per **D5 two-conjunct rule** + left-member pin · roster resolution |
| P3 | router per the **total 10-state map (D1)** + derived CAS + rowcount-0 map · Ruling-A branch + **one-hop rule (D3)** + Y14 + proposal dedup · room allow-clause + pin · @tag resolution · fan-out + caps · closed/archived refusals |
| P4 | nudge (a) every-update reminder · `room_completion_gate` (chain + disabled seam + `"agent"` map) · demoted backstop · manager-level shared latch · strong-form composition **+ continuation-cap interaction test (note 5)** |
| P5 | category #23 mint · room tools (grant pinned; **room_create seeds the owner row — D2**) · **parent mid-life room-tool availability stated: the CREATOR holds the category from spawn via the static allow-list + in-body gate — no mid-life mutation needed (note 11)** · digest builder (async day one; roster incl. owner) · push composer |
| P6 | SSE pre-branch + FE · GC sweeper (whole-room; capability row; `waiting` alive; dry-run) + archive tables |

---

## 14. Confidence + flip assumptions

- **HIGH:** substrate; capability row; join predicate; total 10-state lane map (D1); owner-cursor substrate (D2); one-hop rule (D3); pinned boot pair (D4); two-conjunct restore (D5); post-graph anchor chain; two-cursor model; Ruling-A inversion; boot-by-construction; nudge seams.
- **MEDIUM:** cost constants (env-tunable; note 18 — no measurement by design at Phase-1).
- **Flips:** (1) untagged→IDLE wake (#2); (2) enqueue-mirror (#3); (3) cross-tree rooms; (4) accepted-loss GC (#4 → cursor-respecting P-close).
