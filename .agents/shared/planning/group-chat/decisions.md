# Agent Group Chat — Decision Log (Phase-1 DESIGN)

**Doc-set Rev 6** (single alignment stamp; 2026-11-15 final fix pass: D1–D5 + iteration-003 notes).

Provenance: Emperor directive via Ari (Discord 2026-11-15); rulings settled 2026-11-15 (two naming-cascade additions arrived mid-design, same date); evidence = three investigation reports at repo tip bf4bfde6e, consolidated in substrate-map.md (§A–§J).
**Rev history:** F-A (verbatim ruling re-paste from the KV source of record) · F-B (wrapper anchors re-verified) · F-C (architect risk → RK-13) · iteration-001 (END-chain order B1, GC accepted-loss B2, carrier precision N3, post-graph cite N10) · iteration-002 (closed-state capability row B1', join-admission predicate B2', post-graph chain correction B3', constants-derived CAS B4') · **iteration-003 final fix (Rev 6): D1 — lane map TOTAL over the 10-state enum (`waiting` assigned); D2 — owner membership row seeded at room_create; D3 — one-hop proposal rule for terminal parents; D4 — pinned boot pair; D5 — two-conjunct restore rule.**

Legend: **SETTLED** = binding (Emperor rulings verbatim-faithful). **OPEN** = framed by the planner, owned by the ARCHITECT — **all six RESOLVED**; pointers per entry; ratification item numbers refer to emperor-open-questions.md.

---

## SETTLED — Emperor rulings (2026-11-15)

### D-S1 · Delivery = HYBRID, rhythm-aware
**Ruling (verbatim — KV source of record, key `gc_emperor_rulings_20261115`):** DELIVERY=HYBRID rhythm-aware: (a) unread NUDGE at TODO-STEP boundaries (lightweight 'N unread' injection, no interruption of in-flight work); (a2) COMPLETION-BOUNDARY NUDGE — before the instance declares its turn/task done, last-chance 'check unread team messages' nudge; fires only when unread>0 at turn-completion; MUST carry a loop-guard (once-per-turn latch OR only-if-new-messages-since-last-room-read) so a busy room cannot block completion forever; tagged/blocking message at completion time = strong form, composes with parent-wake. (b) NAMED members + @TAGGING — TAGGED message = INJECTED MID-TURN immediately (importance escalation); untagged waits for nudges.

**Substrate implications (map §A, §F; corrected C1, re-verified iterations 001–003):**
- (a) rides the **reminder slot returned by EVERY todo-manager update** (`{"todos": [...], "reminder": str}` — todo_manager.py:403-461; `_compute_reminder` :1198-1276); filter = shared latch + watermark, NEVER done-only. Not an enqueue, not an injection. *[Correction record: Rev 1 hooked (a) at the drain wrapper's terminal branch — terminal verdicts only (graph.py:4955-5040; enumeration :4942-4952, `terminal_routes` frozenset :4984-4986, check :4995, cap :4933), so todo-step passes went through with ZERO nudges. Ruling re-pasted verbatim (F-A).]*
- Drain wrapper = the (a2)-side COMPLETION backstop. **Latch home:** graph-builder-local `TurnRepairLatch` (:13159) cannot serve the tool-side arm ⇒ **manager-level RAM registry** threaded into both closures; ≤1 room nudge/turn family-wide (the ruling's "so a busy room cannot block completion forever", mechanized).
- (a2) fires IN-GRAPH at the END decision. **END chain (code-verified): `language_check → room_completion_gate → attestation_gate → END`** (region graph.py:13378-13415); disabled-language seam = between the base terminal router and the attestation wrapper. Gate body shape: `create_language_check_node` :4758 (skip :4770; fail-open :4807-4812); counter-reset scan :4778-4790. **Post-graph chain (B3-corrected):** definition **manager.py:11703** (facade :11717 → `ChildReportsService`); post-graph call sites **:8368 / :11880 / :15746**; internal parent-update **services/child_reports.py:1019**.
- (b) tagged mid-turn = RAM-FIFO lane (`set_injection` manager.py:4909-5020; drain graph.py:8752-8830); volatile — durable fallback = log+cursor re-derivation (D-O4; owner cursors per D2).
- **MISSING PRIMITIVE:** per-room read-watermark — minted per participant.
- Untagged = nudge-only ⇒ pull-first.

### D-S2 · Ruling A — tagging a COMPLETED agent
**Ruling (verbatim — KV):** RULING A: tagging a COMPLETED agent == sibling task proposal → route to PARENT to decide (revive? reroute? answer directly?); completed agents are NEVER silently revived or messaged on their own.

**Substrate implications:** terminal-revive lane exists (instance.py:1577-1590, 4999-5030) and the enqueue lane auto-revives terminals (instance_messaging.py:2537-2579) ⇒ room router INVERTS the terminal branch and defeats the enqueue flip with the derived CAS (D-S4). **The parent-target side is governed by the ONE-HOP rule (D3, below): a terminal parent leaves a durable pending proposal — no revive, no upward recursion.** Implementation: room-router branch (c) — status-split template `_route_send_message` (def instance.py:1610-1728); proposal rows dedup by `tagged_message_id`; the terminal member's `last_delivered_seq` advances at proposal ADMISSION (Y14).

### D-S3 · Ruling B — naming MANDATORY + FULL NAMING CASCADE
**Ruling B (verbatim — KV):** naming MANDATORY when using chatrooms — parent gives each instance its purpose NAME at spawn; instance KNOWS ITS OWN NAME (room name injected into its system message for @tag self-recognition); in-room addressing by NAME (UUID allowed but not recommended — soft rule).

**FULL NAMING CASCADE (settled end-to-end):** (1) parent-provided purpose name (preferred/expected); (2) omitted → quick-model INLINE generation on the purpose-bound QUICK model (short prompt, purpose name only, e.g. 'capturer'/'log-analyzer'), name RETURNED IN SPAWN TOOLCALL RESULT (no silent naming); (3) quick-model FAILURE (error/timeout/unavailable) → deterministic fallback = agent-name + N (e.g. 'capture-2'), instant + predictable, no retry-loop, never blocks spawn, also returned in toolcall result; (4) any collision IN-ROOM → numeric suffix (capturer-1, capturer-2, …) — duplicates are FIRST-CLASS (same agent spawned multiple times for different task parts); uniqueness scoped PER-ROOM not global; applies to parent-provided AND auto-generated names; final suffixed name surfaced in toolcall result. *(KV-verbatim.)*

**Ruling additions (PARAPHRASE record; KV cascade above is the wording source):** suffix mechanics detail; quick-model failure detail; **Never block spawn on naming** — read as "never block *on naming failure*": tier-2's bounded 3.0s SUCCESS-path wait is the one place a spawn waits on naming (explicit ratification line, emperor #6; failure path instant).

**Substrate implications (map §G):** instance_name metadata [A]; quick lane purpose-bound field [M→A] (config.py:172-182; bypass :449-458); inline shape TitleGenerationService (class services/title_generation.py:17; region :75-130; belt-and-braces :137-146); no-block precedent instance.py:3620-3630; tier-4 registry = roster state [M] (**owner's member_name assigned via the same registry at room_create — D2**); result-string assembly instance.py:3745 region; `instance_metadata["join_room"]` zero migration.

### D-S4 · TAG-DELIVERY STATE MACHINE
**Ruling (verbatim — KV):** TAG-DELIVERY STATE MACHINE (explicit in architecture): (a) RUNNING → mid-turn injection; (b) IDLE-but-live → wake/queued delivery; (c) COMPLETED/terminal → parent as task proposal (Ruling A).

**Substrate implications — THE LANE MAP IS TOTAL OVER THE 10-STATE ENUM (D1; enum models.py:20-35):**

| Status(es) | Lane |
|---|---|
| `completed`, `error`, `terminated`, `failed` | **(c)** parent proposal (Ruling A) |
| `paused` | **(d)** park + audit (`parked_paused`) |
| `running` | **(a)** injection (DEFECT-A downgrade → enqueue; race → retry → `deferred_cursor`) |
| `idle`, `queued`, `waiting`, `waiting_children` | **(b)** enqueue-family under the status CAS |

- **CAS allowed set = all non-terminal statuses − {running, paused} = {idle, queued, waiting, waiting_children}** — derived from the ENUM, not from `ALIVE_INSTANCE_STATUSES` (which omits `waiting`, constants.py:1066-1072). **`waiting` ("Active but no in-flight work") is a live awaiting-input agent: it belongs to the enqueue-family — never misrouted to (c), never dropped.**
- **Totality pin:** a test asserts every enum member maps to exactly one lane; a future enum addition reddens.
- (b)'s CAS defeats the enqueue's terminal auto-revival (instance_messaging.py:2537-2579); rowcount-0 ⇒ the total map's branch for the fresh status (no unassigned case exists).
- (c) = NEW branch; one-hop rule (D3); Y14 cursor rule; `job_inject` (job_queue.py:3405-3572) must ADD `source=` (:3561 anonymous = do not copy); W2 trap (messaging_types.py:38-62).
- **State machine must be EXPLICIT** (Emperor).

**D3 — THE ONE-HOP PROPOSAL RULE (binding design rule; restated in architecture §4.2/§7):** *a proposal whose target parent is TERMINAL stays durable as a pending `kind:"task_proposal"` row — surfaced when the parent next runs through any legitimate lane — it NEVER auto-revives the parent and NEVER re-routes upward: proposals are ONE HOP, never recursive (including at boot).*

### D-S5 · CONTEXT discipline — digest-only in system message
**Ruling (verbatim — KV):** CONTEXT: system message = guidelines + short digest ONLY; full history behind read tool.

**Substrate implications (map §E):** persona SystemMessage per-AGENT cached (daemon/loader.py:972/:1356) — [REJECTED SEAM]; the block rides the per-turn context-kind lane (context_messages.py:2558; `_make_context_message` :181; **14 kinds verified**; memoir_refresh :134-173); hoisting compaction.py:337/:425/:3233 (budget :1404; threshold :1589; caps :1650); zero baseline/seal impact. *(Adaptation #2.)* Pull-first safer (no speaker model). Digest discipline instance.py:116.

### D-S6 · TASKING parent-decided
**Ruling (verbatim — KV):** TASKING: PARENT-DECIDED — siblings inform/discuss/propose; task creation injects/wakes parent; never auto-execute.

**Substrate implications:** sibling→sibling sends fail closed (gate = sender team_members, _auth.py:90-256) ⇒ room membership = additional allow-source [A]; **MUST NOT copy the fail-open skip instance.py:4723**; parent INJECT/WAKE = D-S4 (a)/(b) pointed at the parent — **with the D3 one-hop rule for terminal parents**; no verb mints work; `mid_flight_report` (daemon/tools/midflight_report.py:64).

---

## SETTLED — commission-fixed frame

### D-S7 · Planning-only boundary
Phase-1 = DESIGN ONLY: markdown doc set under `.agents/shared/planning/group-chat/`; NO implementation/code/commits — giter finalizes git. Absolute paths.

### D-S8 · Lane boundary
Planner framed; ARCHITECT delivered (architecture-recommendation.md Rev 6 + emperor-open-questions.md). **Live state: awaiting Emperor ratification.**

### D-S9 · House constraints
Off-loop async I/O (exemplars instance_messaging.py:1179-1184); explicit boot story vs F-1 (manager.py:1407-1600; predicate SQL repository.py:1312-1322; pinned pair in architecture §10); zero baseline/seal impact; migration doctrine; no over-build.

---

## OPEN — all RESOLVED by the architect (ratification pointers live)

### D-O1 · Room lifecycle / TTL — RESOLVED
**RESOLVED → architecture §2 + the published capability row (§2.1)** — active→closed→archived; close parent-tool-only; whole-room GC (P-close 14d; P-idle 72h + zero non-terminal among members ∪ owner ∪ owner-ancestry — **`waiting` counts alive**); capability row = the SINGLE definition of `closed`; **accepted loss names EXACTLY `archived`**; **owner membership row seeded at create (D2) so the opener's unread/digest/re-derivation are defined**; archive tables + sweeper at P6. **Ratification: item #4.**

### D-O2 · Persistence + audit/UI visibility — RESOLVED
**RESOLVED → architecture §8** — room_messages = content record; content-free `room_*` EventKinds (**registry additions in P1**; enum range :27-54 illustrative, extends additively); SSE observability only (+room-resolution pre-branch; no synthetic instance rows). **Ratification: item #7.**

### D-O3 · Non-member read access — RESOLVED
**RESOLVED → architecture §8/§3** — read = **active members (owner included via the seeded row)** ∪ owner-ancestry, fail-closed, state-aware per the capability row. Membership ADMISSION = `_validate_room_join` (tree-root equality, caps with **owner counted and LEFT rows excluded**, typed fail-closed). **Restore = two conjuncts (D5): room.state ∈ {active, closed} ∧ member.state = active — a LEFT member's restore grants nothing.** **Ratification: item #5.**

### D-O4 · Substrate choice — RESOLVED
**RESOLVED → architecture §1** — hybrid (iv): 3-table store + reused lanes; **re-derivation predicate `seq > max(last_read_seq, last_delivered_seq)`**; **every participant has a cursor row — the creator via the seeded owner membership row (D2), closing the pre-existing-creator gap**; tagged durability = log+cursor. **Ratification: item #1 (+ #3).**

### D-O5 · Compaction interaction with digest — RESOLVED
**RESOLVED → architecture §5 + §9** — JOINED_ROOM stable-id supersede; hoisting verified; digest ≤2000/4000; 8000/turn ONE-composer budget. **Ratification: item #6.**

### D-O6 · Todo-nudge composition — RESOLVED
**RESOLVED → architecture §4.3** — carrier = every-update reminder slot (todo_manager.py:403-461; `_compute_reminder` :1198-1276), filtered by the manager-level shared latch + watermark (never done-only); todo store untouched; durable state = P1 cursors. **No ratification item** (mechanism settled by D-S1).

---

## Counts
SETTLED: 9. OPEN at framing time: 6 — **ALL RESOLVED**; live state = awaiting Emperor ratification (items 1–7). Architect-input details inside settled rulings: D-S3 suffix mechanics (resolved; owner name included); D-S1 durable-fallback ownership (resolved; D2 closes the owner gap).
