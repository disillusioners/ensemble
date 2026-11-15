# Agent Group Chat — Phase-1 ARCHITECTURE RECOMMENDATION

**Doc-set Rev 7 — Phase-2 amendment (2026-11-15):** Emperor RATIFIED all 7 open-question items AS RECOMMENDED and issued TWO binding refinements + ONE P6 UI direction — folded here (canonical wording: shared_meta_kv keys `gc_phase2_go` + `gc_ui_refinement_p6`, quoted verbatim in §4.4/§13.1 and decisions.md D-S10..D-S13). Implementation follows per the phased plan. **Status:** COMPLETE (design; planning-only — no code, no git ops per D-S7).
**Date:** 2026-11-15 · **Commission:** Emperor directive via Ari.
**Method:** Architect controller → 🏛️ substrate council (2/2, `agentic`+`coding`, UNANIMOUS, governor `0e8f6375`) + 4 parallel dimension analysts; architect aggregated and writes this file.
**Evidence base:** substrate-map.md §A–§J (tip `bf4bfde6e`) + in-worktree verification. **Verified anchors (each read at pin before stamping):** 10-state enum `models.py:20-35` (`waiting` = "Active but no in-flight work"); `task_context` stamp `instance_messaging.py:5157-5162`; language-check :4758/:4770/:4807-4812; DEFECT-A guard `instance.py:4792-4816`; F-1 predicate SQL `message_queue/repository.py:1312-1322`; post-graph chain def `manager.py:11703` (+ facade :11717; call sites :8368/:11880/:15746; internal `services/child_reports.py:1019`); END-chain region graph.py:13378-13415; wrapper quartet :4942-4952 / :4984-4986 / :4995 / :5040, cap :4933; `TurnRepairLatch` :13159; todo reminder-return todo_manager.py:403-461 + `_compute_reminder` :1198-1276; context kinds = 14; `ALIVE_INSTANCE_STATUSES` constants.py:1066-1072; `MISSION_RECEIPT_SCAN_CAP` daemon/services/midflight_qa.py:194; `QA_WATCHER_ROW_CAP` daemon/services/work_notifier.py:1339 (def :1583); hoisting compaction.py:337/:425/:3233 (budget :1404, threshold :1589, caps :1650); `CHECKPOINT_TTL_HOURS` constants.py:170; `daemon/loader.py:972/:1356`; `services/skill_meta_parser.py:94`; TitleGenerationService class :17.
**⚠️ DEGRADED — skill bank miss (design-skill family):** design skills absent from the project bank (known gap); manual discipline prompts used; flag stands.
**Companion:** emperor-open-questions.md (7 items — **ALL RATIFIED 2026-11-15 as recommended**; decision record unaltered, banner added) + 3 adaptation disclosures.

---

## 0. Verdict at a glance

- **A Substrate (D-O4, ratified #1):** **Hybrid (iv)** — NEW minimal 3-table store (one `.sql` — 3 CREATE TABLE + 1 comment, 130→131) + every existing lane reused as transport. `message_queue`/`shared_meta_kv` untouched.
- **B Lifecycle (D-O1, ratified #4):** active→closed→**PURGED**; close = parent tool only; GC = one maintenance-seam job (P-close 14d / P-idle 72h + zero non-terminal among members ∪ owner ∪ owner-ancestry — `waiting` alive); **R2: purge = ACTUAL DB DELETION** (all room rows; accepted loss = literal DB cleanup, NOT a hidden flag; one content-free `room_purged` audit event is the only trace; no tombstone, no archive tables).
- **C Message model:** BoardNote-adapted log; per-room seq under the LRU lock + `UNIQUE(room_id,seq)`; two cursors on EVERY participant row (owner seeded at create); lateral read predicate (state-aware; **purged = nonexistent**); re-derivation predicate `seq > max(last_read_seq, last_delivered_seq)`; **R1: `addressed_members` JSONB routing field** on the row.
- **D Delivery:** D-S4 router; lane map TOTAL over the 10-state enum; tagged durability = log+cursor re-derivation; proposals ONE HOP; nudge (a) every-update reminder; (a2) gate chain `language_check → room_completion_gate → attestation_gate → END`; ONE manager-level shared latch. **R1: addressing = a NON-ESCALATING routing dimension** — `addressed_to` routes (never interrupts): terminal addressee → parent notice (extends Ruling A); alive → digest, NO wake; running → digest at next boundary (addressing alone never injects).
- **E Joined state:** `CONTEXT_KIND_JOINED_ROOM`, supersede, hoisting (compaction.py:337/:425), 14 kinds; caps 2000/4000.
- **F Naming:** 4-tier cascade; T=3.0s bounded quick-model (bounded SUCCESS-path wait; never blocked on failure); suffix at registration.
- **G Tasking:** proposals via D-S4; no verb mints work; one-hop rule; milestones never auto-escalate; **addressed-to-terminal notices reuse the proposal machinery (trigger=`addressed_terminal`)**.
- **H Visibility:** active members (incl. owner) ∪ owner-ancestry (fail-closed; purged = nonexistent); HTTP role scope; SSE observability-only; content-free `room_*` EventKinds (registry in P1) **+ `room_purged`**.
- **I Concurrency/cost:** sequential fan-out; `ROOM_MAX_MEMBERS=16` (active rows; owner counts; LEFT free), `ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8`; post ≤4000 chars; 8000/turn ONE-composer budget; naming ≤3.0s minority path.
- **J Boot:** outside F-1 blast radius; pinned pair: regen re-derives TAGGED undelivered rows for MEMBERS incl. OWNER (active+closed; purged nonexistent) and does NOT route/wake/revive parent-directed proposals.

---

## 1. Substrate choice (A · D-O4)

**VERDICT — Hybrid (iv).** Council 2/2 UNANIMOUS. Core principle: the log write is the commit point; delivery is best-effort on top of it.

### 1.1 Five-axis matrix

| Approach | Cplx | Scal | Maint | Risk | Cost | Verdict |
|---|---|---|---|---|---|---|
| (i) message_queue + room-id | HIGH | MED | LOW | HIGH | HIGH | **FAIL** — per-recipient delivery mechanism asked to be an N-reader log; its boot machinery destroys the log (predicate SQL `message_queue/repository.py:1312-1322`); Task-per-recipient enqueue; no seq; its enqueue auto-revives terminals (`instance_messaging.py:2537-2579` — Ruling-A hazard) |
| (ii) shared_meta_kv rooms | MED→HIGH | LOW | LOW | HIGH | MED-HIGH | **FAIL as log** — LWW, 4KB, no seq, no GC, no notification; watermark inexpressible |
| (iii) new subsystem alone | MED | HIGH | HIGH | MED | MED | **HOLDS as storage** — mints the missing primitives but re-builds delivery (RK-11) |
| (iv) **hybrid** | MED | HIGH | HIGH | MED | MED | **RECOMMENDED (RATIFIED)** — same substrate + lanes that exist |

Cost line: ≤3.0s spawn latency on the naming tier-2 minority success path (dominant path ~0; failure path never blocks); constants env-tunable.

### 1.2 Why the simpler options fail

- **(i)** F-1 doomed predicate (SQL `:1312-1322`; orchestration `manager.py:1407-1600`) wipes fully-delivered rows at `discard_on_startup`; enqueue mints Task per recipient (`instance_messaging.py:2130+`); no seq; ordering ties (`repository.py:330/:348`); enqueue auto-revives terminal (:2537-2579).
- **(ii)** LWW; >4096 hard-fail; no seq/history/GC/notification; create_all deviation (`manager.py:1205-1214`).
- **(iii)** correct storage; alone re-builds delivery.

### 1.3 Schema (one `.sql`, 130→131; **3 CREATE TABLE + 1 explanatory comment**)

| Table | Shape | Purpose |
|---|---|---|
| `chatrooms` | `room_id` UUID PK · `name` · `tree_root_id` (indexed; enforced by `_validate_room_join`) · **`owner_instance_id`** · `state (active\|closed)` — *purge is a terminal TRANSITION (row deletion), not a persisted state (R2)* · `last_post_at` · counters · timestamps | identity + scope + lifecycle |
| `chatroom_members` | `(room_id FK NO ACTION, instance_id)` unique · `member_name` · **`UNIQUE(room_id, member_name)`** · `agent_id` · `role (owner\|parent\|child)` · `joined_at` · `last_read_seq` · `last_delivered_seq` · `state (active\|left)` | roster truth; cursor substrate for EVERY participant incl. owner; naming tier-4 |
| `room_messages` | BoardNote shape (`models/board.py:301-345`) · `room_id` FK NO ACTION · `seq BIGINT` · **`UNIQUE(room_id, seq)`** · `author_instance_id` + `author_name` denormalized · `text` · `tagged_members` JSONB · **`addressed_members` JSONB (R1 — resolved routing targets)** · `kind (message\|system\|task_proposal\|task_decision)` · `created_at` | durable log |
| *(no 4th table)* | *(watermark table folded into `chatroom_members`)* | cursors |

**D2 — THE OWNER MEMBERSHIP SEED (stated once; referenced §2/§3/§7/§10 + D-O4):** `room_create` mints a `chatroom_members` row for the CREATOR in the same tx as the `chatrooms` row: `role=owner`, `member_name` = creator's `instance_name` if set else `owner` (same suffix registry), cursors 0. **The parent is a first-class delivery target** — tagged `@parent`, `task_proposal`, and **addressed-to-terminal notices (R1)** re-derive from the owner's cursors; owner unread/digest defined; roster includes the owner; the cap counts the owner row; the owner row is retired only WITH the room.

**Seq:** per-room `asyncio.Lock` LRU registry (port of `sources/dispatcher.py:451-452/:810-838`; MAX_ROOM_LOCKS=1000) + single-statement `INSERT … COALESCE(MAX(seq),0)+1 …` + UNIQUE backstop.

**RK-13 acceptance note (P3 carry-over D1a — eviction-while-held is ACCEPTED, not a defect):** the LRU registry holds at most 1000 locks; with MORE than 1000 cycling rooms the eviction path can drop a lock that is CURRENTLY HELD, letting a second writer enter the same room concurrently. The consequence is bounded and benign: exactly ONE winner, the loser's append hits the `UNIQUE(room_id, seq)` backstop and surfaces as the typed `RoomSeqConflictError` (always surfaced, never swallowed), and the next append self-heals to `MAX(committed seq) + 1` — no duplicate, no gap, no skip. This is the accepted §1.4 posture: the per-room lock is a THROUGHPUT layer (serialization of the common case); correctness lives in the DB backstop layer. Pinned by the repository's typed-integrity tests; the "never expected" docstring over-claim on `RoomSeqConflictError` was amended in the same pass (repository.py, D1b).
**Rollback:** additive-only; rollback = drop 3 tables + delete migration row; `rollback_safe=true`.
**Boot safety:** doomed predicate over message_queue × task only; assertion pin test.

### 1.4 Alternatives rejected
**DB SEQUENCE / per-room sequence table:** DDL per runtime room violates migration doctrine; global seq destroys cursor locality; lock + single-statement + UNIQUE suffices (RK-13). · shared_meta_kv · room row on message_queue · synthetic instance rows · tree-root read scope · **filesystem flock log (`tools/filesystem.py:1451`)**: per-instance file locality cannot express N-reader scoping; DB append gives transactional seq+cursor.

---

## 2. Room entity + lifecycle (B · D-O1)

**Creation & admission:** parent via `room_create` (creator non-terminal; binds `tree_root_id`; records `owner_instance_id`; seeds the owner row; fails closed at `ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8`). **`_validate_room_join(joiner, room_id)`:** room exists ∧ state=active ∧ `get_tree_root_id(joiner) == room.tree_root_id` ∧ active membership rows (owner included) < `ROOM_MAX_MEMBERS` (LEFT rows free) ∧ active-rooms-per-tree < cap; typed fail-closed spawn errors. Grant scope: privileged category + per-agent `tools.allow` + in-body fail-closed gate.

### 2.1 THE PUBLISHED CAPABILITY ROW (the ONE definition of `closed` and `purged` — cited verbatim from §3, §5, §10, decisions D-O1/D-S12, emperor item #4)

| Capability | `active` | `closed` | `purged` (R2 — terminal transition) |
|---|---|---|---|
| read tool | yes (member(state=active) / owner-ancestry) | **yes — read-only** | **no — room nonexistent: not-found, not "archived"** |
| posts | yes | **no — typed "room closed" error** | **no — not-found** |
| digest block | yes | **yes (read-only variant; shows `unread: N`)** | **excluded — the room no longer exists** |
| unread computed | yes | **yes** | **no — nonexistent** |
| nudges (a)/(a2) | yes | **no (closed rooms are quiet; the digest carries unread)** | no |
| tagged re-derivation (turn + boot) | yes | **yes — until purge (at-least-once preserved through the closed window)** | no |
| delivery guarantee | at-least-once (log+cursor) | **at-least-once for TAGGED until purge; untagged via digest only** | **none — accepted loss = LITERAL DB CLEANUP** |

**Accepted-loss statement (R2, names EXACTLY `purged`):** **a PURGED room is DELETED FROM THE DATABASE — `chatrooms` + `chatroom_members` + `room_messages` rows all removed: not readable, not restorable, no rows remain. The only trace is ONE content-free `room_purged` audit event (ids + counts only), emitted in the purge transaction. Purged rooms are treated as NONEXISTENT by every predicate (read/ACL/boot/digest) — no tombstone dependency; lingering references (old digests, UI links) resolve as not-found.** Closed rooms lose nothing the log+cursors cannot re-derive (tagged re-derivation runs through the closed window, up to purge).

### 2.2 Lifecycle & GC
- States forward-only: `active → closed → (purged)`. Close = parent tool only; no auto-close on parent-terminal — reconciled with P-idle: revive-and-continue works while ANY ACL participant is non-terminal; 72h of total silence ends that budget.
- **GC — PURGE, not archive (R2):** one `MaintenanceService.register()` job (`services/maintenance.py:141`; call sites `manager.py:4735/:4752`), 6h min-interval, repo-fenced + dry-run first. Triggers unchanged:
  - **P-close:** `state=closed AND closed_at < now−14d` → **purge**.
  - **P-idle:** `state=active AND last_post_at < now−72h AND zero non-terminal instances among {members(state=active) ∪ owner ∪ owner-ancestry chain}` — non-terminal = {idle, running, waiting, paused, queued, waiting_children} (`waiting` IS alive) → **purge** (counts as close).
  - **Purge mechanics:** ONE transaction per room: insert the content-free `room_purged` event → DELETE `room_messages` rows → DELETE `chatroom_members` rows → DELETE the `chatrooms` row (event + deletes commit or roll back atomically). **NO archive tables** — the Emperor's "cleanup from db" makes retention tables moot; the design simplifies (RK-11 win; the formerly deferred `*_archive` migration is dropped entirely).
  - Cursor guard: permitted on P-idle only (skip rooms holding undelivered TAGGED rows above any participant's delivered cursor); P-close = whole-room purge with the accepted-loss column above.
  - Non-terminal set = ONE shared constant (GC + boot regen); enum-totality pin.
- **Membership: HYBRID** — table = roster truth + cursor substrate (owner row seeded at create); `instance_metadata["join_room"]` = spawn hook + restore re-read. `instance_hierarchy` REJECTED.
- **D5 — RESTORE TWO-CONJUNCT RULE:** restore re-grants room tools only if `chatrooms.state ∈ {active, closed}` AND `chatroom_members.state = active` — BOTH conjuncts; a LEFT member's restore grants nothing (pin). Read in `closed` = read tool only; a purged room grants nothing (nonexistent).
- **Leave:** membership row `state=left` (owner exempt — removed only by purge); LEFT rows free vs the cap; JSONB echo tolerated (inert).
- **Join-later:** join-at-spawn v1; spawn-with-join at cap/unknown/purged/cross-tree = typed fail-closed; late join of a RUNNING instance deferred.
- **Warm-vs-late-join:** warm reuse INSIDE a room = revive/wake an existing member (supported); adopting a pre-existing instance into a NEW room = v1-deferred.
- **Survival:** revive (`instance_messaging.py:2538-2572`), pause/resume, restart — membership survives; restore re-read per D5.

---

## 3. Message model (C)

- **Interface:** `append(room, sender, text, tags?, addressed_to?) → seq` · `read` · `unread(room, participant) → COUNT(*) WHERE seq > last_read_seq` · `advance_read/advance_delivered` (monotonic MAX guards). Every participant has a cursor row — including the owner.
- **COUNT rationale:** rows persist for the room's entire ACTIVE and CLOSED life (unread computed in both); **purged rooms no longer exist — there is nothing to exclude because there are no rows**; the digest/unread builder simply finds no room. COUNT is robust to any future partial-trim within live states.
- **Two cursors:** `last_read_seq` = the only read ack; `last_delivered_seq` = delivery floor advanced on lane admission (and on Ruling-A proposal admission, trigger-aware — Y14/§4.4).
- **Re-derivation predicate:** a row needs delivery iff `seq > max(last_read_seq, last_delivered_seq)` for that participant.
- **Read predicate:** `_validate_room_read(caller, room)` — active member (owner included) ∨ owner-ancestry chain; state-aware per the capability row (active/closed → allow; **purged/nonexistent → typed not-found**); fail-closed on degenerates. (`_validate_subtree_target` `instance.py:1917-1981` — rendering shape only.)

---

## 4. Delivery mechanics (D)

**Router:** branch order PINNED: lane assignment FIRST (per the total map); the room router owns branch (c) — it never calls `_route_send_message` (def `instance.py:1610-1728`). **Two routing dimensions (R1): TAGS escalate (this section's lane map); ADDRESSING routes without escalating (§4.4).**

### 4.1 THE LANE MAP — TOTAL OVER THE 10-STATE ENUM (D1; pasted once, cited by §2.2/§10/decisions D-S4/plan P3)

Enum source: `InstanceStatus` (`repositories/instance/models.py:20-35`): `idle · running · waiting · paused · completed · error · terminated · queued · waiting_children · failed`.

| Status(es) | Lane | Behaviour |
|---|---|---|
| `completed`, `error`, `terminated`, `failed` | **(c) parent proposal (Ruling A)** | proposal → parent via (a)/(b); one hop (§4.2 D3) |
| `paused` | **(d) park + audit** | `room_tag_delivery` outcome=`parked_paused`; no auto-resume |
| `running` | **(a) injection** | `has_live_graph_task` → `set_injection`; graphless-running → DEFECT-A downgrade to (b); race → one bounded retry, then `deferred_cursor` |
| `idle`, `queued`, `waiting`, `waiting_children` | **(b) enqueue-family under the status CAS** | CAS set derived FROM THE ENUM: non-terminal − {running, paused} = **{idle, queued, waiting, waiting_children}**; `waiting` = live awaiting-input — never misrouted, never dropped |

**Totality pin:** a test asserts every enum member maps to exactly one lane. Derivation is from the ENUM, not from `ALIVE_INSTANCE_STATUSES` (omits `waiting`).

### 4.2 Lane details
- **(a)** `set_injection(..., source="room:{room_id}:{sender_name}:{seq}")` (stamp `graph.py:8765-8773`); DEFECT-A guard at the call site `instance.py:4792-4816` copied; content = NOTIFICATION (≤300-char lead + pointer).
- **(b)** the room's own status CAS (`SET status='running' WHERE status IN (<the four>)`) — defeats the enqueue's terminal auto-revival (`instance_messaging.py:2537-2579`). Rowcount-0 fallback = the total map (fresh status ⇒ its row; no unassigned case). Busy-guard: copied context-fallback shape (`instance.py:4895-4930`) — never silently dropped.
- **(c)** Ruling A: proposal row + parent delivery; Y14 cursor rule — the terminal member's `last_delivered_seq` advances at proposal ADMISSION; proposal rows unique on `(source_message_id, trigger, member)` (trigger ∈ {`tag_terminal`, `addressed_terminal`} — R1 folds its uniqueness into the same constraint).
- **(d)** as mapped.
- **D3 — THE ONE-HOP PROPOSAL RULE:** *"A proposal whose target parent is TERMINAL has exactly ONE terminating outcome: it stays durable as a pending `kind:"task_proposal"` row — surfaced when the parent next runs through any legitimate lane (a human/HTTP send, or its own scheduling) — it NEVER auto-revives the parent and NEVER re-routes upward: proposals are ONE HOP, never recursive. This rule applies identically to proposals minted at boot and to addressed-to-terminal notices (§4.4)."*
- **Tagged durability:** re-derive from log+cursors (§3 predicate); no pending table, no enqueue mirror.
- **Untagged → IDLE (ratified #2):** no wake; digest at next turn — **EXCEPT untagged-but-ADDRESSED-to-TERMINAL, which nudges the parent (§4.4, R1).**

### 4.3 Nudge family
- **(a) reminder carrier:** EVERY todo-manager update returns `{"todos": [...], "reminder": str}` (`todo_manager.py:403-461`; `_compute_reminder` :1198-1276); filter = shared latch + watermark, NEVER done-only. *(Adaptation #3.)*
- **Shared latch:** manager-level RAM registry keyed `(instance_id, room_id)`, threaded into both closures; `last_nudged` RAM payload.
- **(a2) `room_completion_gate`:** chain `language_check → room_completion_gate → attestation_gate → END` (region `graph.py:13378-13415`); disabled-language seam = between the base terminal router and the attestation wrapper; the gate's own path map carries an explicit `"agent"` key. Body cites `create_language_check_node` :4758 (skip :4770; fail-open :4807-4812). Strong form: tagged pending at completion ⇒ reminder names the @tag AND the CONTINUE verdict re-enters so lane (a) re-attempts the tag injection (≤1 reminder + ≤1 tag per turn vs `INJECTION_CONTINUATION_MAX=8` :4933; P4 pins the interaction test).
- **Demoted drain backstop:** terminal-verdict peek (`graph.py:4955-5040`) under the SAME latch.
- **Post-graph fact:** definition `manager.py:11703` (facade :11717 → `ChildReportsService`); call sites `:8368/:11880/:15746`; internal `services/child_reports.py:1019`. ⇒ nudges fire IN-GRAPH.

### 4.4 ADDRESSING — R1 (Phase-2 refinement, BINDING; canonical wording KV `gc_phase2_go`, verbatim excerpt)

> *"REFINEMENT 1 (item #2): untagged→idle-but-LIVE member = digest/nudge, NO wake (as recommended); untagged-but-ADDRESSED-to-TERMINAL member → NUDGE THE PARENT (parent decides revive/reroute/answer; extends Ruling A; exact mechanics of how an untagged-but-addressed post identifies its target = to be designed + pinned)."*

**The field — `addressed_to: list[str]`, optional, a STRUCTURED `room_post` parameter** (never in-body text parsing — no false positives from prose name mentions). Syntax: bare per-room member names, NO `@` sigil (the sigil IS the tag/interrupt signal; its absence signals routing-only). Resolution: the SAME machinery as @tag — exact case-sensitive per-room roster match; 32-hex UUID fallback iff roster-active-resolvable; unresolvable / non-member / LEFT / cross-room → post SUCCEEDS, routing skips that name, resolution-warning on the row + tool result. Resolved names persist as `addressed_members` JSONB (§1.3).

**Co-presence rules:** a post MAY carry both tags (@names) and `addressed_to`, overlapping or disjoint. **Tags ESCALATE (interrupt per §4.1); addressing ROUTES (never interrupts).** A member both tagged and addressed on one post: TAG semantics dominate (strictly stronger); addressing adds nothing. Tagged members are implicitly addressed for routing (tag-to-terminal already routes via branch (c), `trigger="tag_terminal"`).

**Addressing routing predicate (orthogonal to the 10-state lane map; pinned) — for each resolved addressee X not also tagged on the post:**

| X's status / membership | Routing |
|---|---|
| terminal {completed, error, terminated, failed} | **(c′) PARENT NOTICE** — proposal row `trigger="addressed_terminal"` (shape below) |
| `running` | **NO delivery action** — lands via X's digest/next boundary. **Pinned: addressing alone NEVER injects; only tags inject.** |
| alive-not-running {idle, queued, waiting, waiting_children} | **digest path, NO wake** (ratified #2 first clause) |
| `paused` | digest at resume; no auto-resume (no lane action) |
| membership `state=left` | routing skips + warning (same as unresolvable) |

**The parent-notice shape (c′):** ONE notice per (message, addressee) — a `kind:"task_proposal"` row REUSING the Ruling-A machinery (proposal_id, options, decision loop-back) with `trigger="addressed_terminal"`; uniqueness `(source_message_id, trigger, member)` ⇒ at most one nudge per addressed-post per parent; re-fan-out/boot mint no duplicates (the proposal row is the durable record; boot regen does not re-route it — D4 pair). Y14 applies trigger-aware: the addressee's `last_delivered_seq` advances at notice ADMISSION. Parent-facing text (content-light pointer + one excerpt line):
```
[ROOM NOTICE — addressed to a completed member] room=<id> · <sender_name>'s untagged post is addressed to
<member_name> (<iid>, prior_status=<state>) · excerpt: "<first line, ≤120 chars>" (seq N)
options: revive <member> | reroute | answer in-room · full context: room_read(room=<id>, after_seq=N)
```
**Parent lane:** RUNNING → ONE injection (notice ≤400 chars, charged to the §9 notice budget); alive-not-running → enqueue-wake (a decision is required — same lane as tag-proposals); PAUSED → park + audit; **parent TERMINAL → the ONE-HOP rule (D3): durable pending, no recursion** (an addressed-to-terminal-parent notice never mints further proposals). **Never-auto-revive invariant (restated):** the MEMBER is never revived by the room; revival remains the parent's explicit option via its own send — the notice is information, not action.

**Pin tests (P3):** (1) addressed-to-terminal → notice minted + delivered per parent lane; member NOT revived; (2) addressed-to-alive → no enqueue/wake (assert no MessageQueue row) — digest-only; (3) addressed-to-running → NO injection (assert no set_injection call); (4) co-presence: same target → tag effects only; disjoint targets → both dimensions act independently; (5) cross-room addressing fails closed; (6) unresolvable/LEFT → post succeeds + warning, no routing; (7) dedupe: no duplicate notice on re-derivation/boot; (8) parent-terminal → one-hop, no recursion.

---

## 5. Joined-state injection (E · D-O5)

- `CONTEXT_KIND_JOINED_ROOM` (context_messages.py:2558; `_make_context_message` :181; 14 kinds verified), stable id ⇒ supersede (memoir_refresh :134-173). *(Adaptation #2.)*
- Content: own name FIRST; guidelines (+ warm steer + **addressing convention: `to:` for routing, `@` for interrupt**); roster incl. owner ≤600; digest last-3 + `unread: N` (active+closed rooms; purged = nonexistent); banner. Caps ≤2000 soft / 4000 hard.
- Compaction: hoisting `compaction.py:337/:425/:3233`; budget :1404; threshold :1589; caps :1650.
- Multi-room: v1 one room/instance; builder multi-room-capable.

---

## 6. Naming (F)

| Tier | Mechanics |
|---|---|
| 1 Parent-provided | roster read + suffix under room lock; <10ms |
| 2 Quick-model inline | `model_quick` field (config.py:172-182; bypass :449-458); TitleGenerationService shape (class `services/title_generation.py:17`; region :75-130; belt-and-braces :137-146); sync-bounded 3.0s — bounded SUCCESS-path wait; failure never blocks |
| 3 agent-name+N | instant |
| 4 Per-room suffix | registration under room lock; first bearer bare; colliders smallest-free `-N`; UNIQUE backstop; owner's name via the same registry at create; final name in the spawn result (`instance.py:3745` region) |

@tag + `addressed_to`: exact per-room match; no fuzzy; UUID fallback iff roster-active; unresolvable → post succeeds, skip + warning; cross-room fails closed. Gate: room clause beside `_check_team_membership` (`tools/_auth.py:90-256`); **fails CLOSED — the `if target_agent_id:` skip `instance.py:4723` NOT inherited**; RK-2 pin + counter-mutation.

---

## 7. Tasking + parent authority (G)

Proposal (`kind:"task_proposal"`, unique `(source_message_id, trigger, member)`, trigger ∈ {`tag_terminal`, `addressed_terminal`}) → parent via the owner/participant cursors → `task_decision`. **Parent-target matrix:** RUNNING → (a); alive-not-running → (b); PAUSED → park + audit; TERMINAL → the ONE-HOP rule. No room verb mints work. Milestones ride `daemon/tools/midflight_report.py:64`; never auto-escalate.

---

## 8. Visibility / privacy + UI / audit (H · D-O2, D-O3)

- Agent-tool read = active members (owner included) ∪ owner-ancestry (state-aware; **purged = nonexistent**); HTTP = `instances:read`; SSE observability only (+room-resolution pre-branch, `routers/messages.py:960-963`; hub `services/live_event_hub.py:15/:150/:175`). **P6 UI = the human observability surface — §13.1 (R3).**
- `room_*` EventKind additions in **P1** (registry before emission); content-free rows (post/join/leave/tag-delivery incl. `parked_paused`/`deferred_cursor`/proposal/decision) **+ `room_purged` (R2 — ids + counts only, emitted in the purge transaction)**; nudges not audited. **Enum range note:** `repositories/event/models.py:27-54` is illustrative — the enum extends additively.

---

## 9. Concurrency + cost discipline (I)

- Sequential fan-out, per-member isolation (`services/work_notifier.py:1496`); failure degrades to nudge-only.
- Caps: `ROOM_MAX_MEMBERS=16` (active rows; owner counts; LEFT free) + `ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8` — typed fail-closed.
- Write ordering: per-room lock for seq+append; LRU port; one INSERT.
- Off-loop: 9 paths span P1–P6, each phase owns its own (digest builder async day one; exemplar `instance_messaging.py:1179-1184`).
- Cost: post ≤4000 chars · ≤4 posts/member/turn · no daily quota · 8000/turn ONE-composer budget (tagged ≤4000×≤2, digest ≤2000, nudge/notice ≤400 — R1 notices charge here, overflow tagged→digest→nudge). Naming ≤3.0s minority path.

---

## 10. Boot story (J) — THE PINNED PAIR (D4)

- **By construction:** doomed predicate over `message_queue` × `task` only (SQL `:1312-1322`); room tables are a different table/PK space; assertion pin test. FORBIDDEN: taskless READY room rows. **Purged rooms have no rows — boot sees them as nonexistent (no tombstone dependency, R2).**
- **PINNED PAIR:**
  1. *"Boot regen re-derives TAGGED undelivered rows for MEMBERS — INCLUDING THE OWNER ROW — per the capability row: active and closed rooms (purged rooms no longer exist); the predicate is `seq > max(last_read_seq, last_delivered_seq)`; lane assignment follows the total 10-state map (§4.1) with the §9 caps."*
  2. *"Boot regen does NOT route, wake, or revive parent-directed proposals or addressed-to-terminal notices: they remain durable pending rows under the one-hop rule (§4.2/D3), surfaced only when the parent next runs through a legitimate lane."*
- At-least-once re-derivation correct (dedup by seq per participant). Orphan rooms → P-idle purge at next tick. Hard-reboot mid-fan-out: same re-derivation.

---

## 11. Risk register response

| ID | Response |
|---|---|
| RK-1 anchor drift | Re-read at pin each pass (Rev-5: 12 cites; Rev-6: 4 refinements; all recorded). |
| RK-2 fail-open | fail-closed gate; `instance.py:4723` skip not inherited; pin + counter-mutation. |
| RK-3 push-bloat | notification-only; supersede+hoisting; ONE-composer (R1 notices budgeted within it). |
| RK-4 boot | §10 pinned pair + assertion pin; **purged = nonexistent** (no tombstone). |
| RK-5 growth | whole-room **PURGE** (R2 — literal DB cleanup; no archive tables, no tombstone); `room_purged` audit same-tx; room-count cap; `waiting` alive in liveness. |
| RK-6 tag volatility | log+cursor re-derivation; owner cursors (D2); trigger-aware Y14. |
| RK-7 tool-mint | category #23 pinned to P5 (14-file budget; 22→23). |
| RK-8 baseline | context-kind only. |
| RK-9 authz | room clause + `_validate_room_join` + D5 two-conjunct restore + LEFT rows free vs cap. |
| RK-10 nudge durability | RAM registry latches; durable cursors P1 (incl. owner). |
| RK-11 over-build | deferrals explicit; **R2 simplified GC (archive tables dropped)**; R1 reuses the proposal machinery (no new durable state). |
| RK-13 | single-daemon assumption + bounded naming latency; RK-12 lives in plan-overview (response = §9). |

---

## 12. Build-vs-reuse ledger

Counting convention: one row per coined/extended artifact; 3 tables = three rows; the room-ACL predicate pair = ONE row; totals **[R] 17 · [A] 11 · [M] 14 = 42** — reconciled with the approver's count. **Phase-2 amendment folds into existing rows, totals unchanged:** R1's `addressed_members` JSONB extends the `room_messages` row; R1's `trigger` field + uniqueness extension fold into the Ruling-A branch row; R2's `room_purged` EventKind folds into the audit row; R3's UI is FE-only (P6, no daemon mint).

**[R] 17** (injection lane+drain; live-graph gate; DEFECT-A guard :4792-4816; busy-fallback shape; enqueue atomic trio; `source=` stamp; drain terminal wrapper; todo reminder-return carrier; language-check HOLD shape; TurnRepairLatch shape; context-kind lane+supersede+hoisting; mid_flight_report; EventKind audit; LiveEventHub publish; QA-watcher isolation; tree-root walk; MaintenanceService seam).
**[A] 11** (BoardNote → room_messages; per-key lock → per-room; `_route_send_message` → inverted+cas'd branch; team-gate → room clause; subtree → rendering; restore re-read → join_room hook; metadata JSONB → join_room; TitleGeneration → naming; SourcesGC → room GC (**now purge-shaped**); SSE route → pre-branch; job_inject contract → room envelope).
**[M] 14** (3 tables; one `.sql`; Ruling-A branch + one-hop rule (**+ addressed-terminal trigger, R1**); room ACL predicate pair; room_create (+ owner seed); room_post (+ `addressed_to` param, R1); room_read (+#23); JOINED_ROOM builder; push composer; completion gate; boot regen pass; `model_quick`).

---

## 13. Phase-2 shape (ratified — KV `gc_phase2_go`: "implement per phased plan P1 substrate/persistence+boot → P2 membership/spawn/naming → P3 delivery → P4 nudges ∥ P5 tool+guidelines → P6 UI/audit+GC; full house gates per phase; prompt-artifact discipline; staged release lane when ready; live promote ceremony = Emperor's")

| Phase | Content |
|---|---|
| P1 | 3 tables + cursors + one .sql · per-room lock · re-derivation helper · boot regen pass (pinned pair) + assertion pin · enum-totality pin · lifecycle columns · EventKind registry additions (**incl. `room_purged`**) · P1-scope off-loop paths |
| P2 | join param → membership + JSONB one tx · `_validate_room_join` + exit pins · 4-tier cascade · restore per D5 + left-member pin · roster resolution |
| P3 | router per the total 10-state map + derived CAS + rowcount-0 map · Ruling-A branch + one-hop + Y14 + proposal dedup `(source_message_id, trigger, member)` · **R1 addressing: `addressed_to` param + `addressed_members` persistence + the §4.4 routing predicate + the 8 pin tests** · room allow-clause + pin · @tag resolution · fan-out + caps · closed/purged refusals |
| P4 | nudge (a) every-update reminder · `room_completion_gate` (chain + disabled seam + `"agent"` map) · demoted backstop · manager-level shared latch · strong-form composition + continuation-cap interaction test |
| P5 | category #23 mint · room tools (grant pinned; room_create seeds the owner row) · creator mid-life availability (static allow-list + in-body gate) · digest builder (async day one; roster incl. owner; **addressing convention in guidelines: `to:` routes, `@` interrupts**) · push composer |
| P6 | **UI per §13.1 (R3)** + SSE pre-branch + audit surfacing · **GC PURGE sweeper (R2: transactional `room_purged` + deletes; no archive tables; dry-run first)** |

### 13.1 P6 UI — THE HUMAN OBSERVABILITY SURFACE (R3, binding — KV `gc_ui_refinement_p6`, verbatim)

> *"CHATROOM ICON next to the WATCHOVER button on the frontend instances surface. Click → BIG POPUP (large modal, NOT a cramped tooltip) showing (a) chatrooms created by that instance('s tree) and (b) messages in each room — pick instance → see its rooms → read any room's conversation. This is the PRIMARY human observability surface for group chat (complements the SSE live view), on demand from the instance row the user already looks at. Rides the SAME room-read API as the agents' read tool (members ∪ owner-ancestry predicate, fail-closed) with the FE's own auth posture. Fold into P6 UI/audit design + FE conventions (Angular / metrics-tab patterns)."*

**Design:** a chatroom icon on the instances surface adjacent to the WATCHOVER control; click opens a **large modal** (explicitly not a tooltip/popover) with two levels: **(a)** the chatrooms created by that instance's tree (rooms whose `owner_instance_id`/`tree_root_id` match), **(b)** the selected room's conversation — pick instance → its rooms → read any room's messages (paged via the room-read API). On-demand, from the instance row the user already inspects; **complements** (does not replace) the SSE live view. **API parity requirement:** the modal rides the SAME room-read API as the agents' read tool — the members ∪ owner-ancestry predicate, FAIL-CLOSED — surfaced through the FE's own auth posture (operator-tier routes; no new agent-facing auth surface; purged rooms render as not-found). **FE conventions:** Angular component patterns per the metrics-tab precedents — a P6 plan-time design input. **P6 exit criterion:** *"chatroom icon + large modal wired to the room-read API (predicate parity: members ∪ owner-ancestry, fail-closed) on the instances surface; instance → rooms → conversation navigation works; SSE live view parity retained; `room_purged` visible in the audit trail; purged rooms render as not-found."*

---

## 14. Confidence + flip assumptions

- **HIGH:** substrate (ratified); capability row incl. purge (R2); join predicate; total 10-state lane map; addressing mechanics (R1, pinned); owner-cursor substrate; one-hop rule; pinned boot pair; two-conjunct restore; post-graph chain; nudge seams.
- **MEDIUM:** cost constants (env-tunable).
- **Ratified items are no longer flips:** #1–#7 settled by the Emperor's ratification (banner in emperor-open-questions.md). Remaining design-level contingencies: cross-tree rooms (join + read predicates re-scope) and a future multi-writer topology (RK-13 hardening path).
