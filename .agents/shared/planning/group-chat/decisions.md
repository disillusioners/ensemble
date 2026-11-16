# Agent Group Chat — Decision Log (Phase-1 DESIGN)

**Doc-set Rev 6** (single alignment stamp; 2026-11-15 final fix pass: D1–D5 + iteration-003 notes).

Provenance: Emperor directive via Ari (Discord 2026-11-15); rulings settled 2026-11-15 (two naming-cascade additions arrived mid-design, same date); evidence = three investigation reports at repo tip bf4bfde6e, consolidated in substrate-map.md (§A–§J).
**Rev history:** F-A (verbatim ruling re-paste from the KV source of record) · F-B (wrapper anchors re-verified) · F-C (architect risk → RK-13) · iteration-001 (END-chain order B1, GC accepted-loss B2, carrier precision N3, post-graph cite N10) · iteration-002 (closed-state capability row B1', join-admission predicate B2', post-graph chain correction B3', constants-derived CAS B4') · **iteration-003 final fix (Rev 6): D1 — lane map TOTAL over the 10-state enum (`waiting` assigned); D2 — owner membership row seeded at room_create; D3 — one-hop proposal rule for terminal parents; D4 — pinned boot pair; D5 — two-conjunct restore rule.** **Phase-2 amendment (Doc-set Rev 7, 2026-11-15):** D-S10..D-S13 — Emperor ratification + refinements R1 (addressing→parent-nudge), R2 (purge-from-DB), R3 (P6 UI direction).

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

## SETTLED — Phase-2 ratification + refinements (2026-11-15; KV keys `gc_phase2_go` / `gc_ui_refinement_p6`)

### D-S10 · RATIFICATION — all 7 items as recommended
**Ruling (verbatim — KV `gc_phase2_go`):** "RATIFIED 2026-11-15: ALL 7 decision items AS RECOMMENDED. … PHASE 2 GO: implement per phased plan P1 substrate/persistence+boot → P2 membership/spawn/naming → P3 delivery → P4 nudges ∥ P5 tool+guidelines → P6 UI/audit+GC; full house gates per phase; prompt-artifact discipline; staged release lane when ready; live promote ceremony = Emperor's. Report at phase boundaries; escalate only on genuine blockers/design-changing decisions."
**Effect:** emperor-open-questions.md items #1–#7 are RATIFIED (the decision record there is unaltered; a banner records this); the Phase-1 recommendations are binding for Phase-2 implementation per the phased plan.

### D-S11 · REFINEMENT 1 — addressing extends Ruling A (item #2)
**Ruling (verbatim — KV `gc_phase2_go` excerpt):** "REFINEMENT 1 (item #2): untagged→idle-but-LIVE member = digest/nudge, NO wake (as recommended); untagged-but-ADDRESSED-to-TERMINAL member → NUDGE THE PARENT (parent decides revive/reroute/answer; extends Ruling A; exact mechanics of how an untagged-but-addressed post identifies its target = to be designed + pinned)."
**Pinned mechanics (architecture §4.4 — the designed+pinning the ruling required):** `addressed_to: list[str]` — a STRUCTURED `room_post` parameter (NO in-body parsing; bare per-room names, NO `@` sigil — the sigil is the tag/interrupt signal; its absence signals routing-only); resolution = the @tag machinery reused as a NON-escalating routing field (exact case-sensitive per-room match; UUID fallback iff roster-active-resolvable; unresolvable/non-member/LEFT/cross-room → post succeeds, routing skips, warning stamped). Routing predicate: **terminal addressee → parent notice** (a `kind:"task_proposal"` row, `trigger="addressed_terminal"`, unique `(source_message_id, trigger, member)`; content-light: sender + addressee + ≤120-char excerpt + options revive/reroute/answer); **running → digest at next boundary (PINNED: addressing alone NEVER injects — only tags inject)**; **alive-not-running → digest, NO wake**; **paused → digest at resume**; parent lane per the parent-target matrix ((a)/(b)/park/**one-hop**); **the MEMBER is never auto-revived**. Co-presence: tags and `addressed_to` may co-exist (overlap → tag dominates). 8 pin tests at P3 (architecture §4.4).

### D-S12 · REFINEMENT 2 — GC = purge from the DB (item #4)
**Ruling (verbatim — KV `gc_phase2_go` excerpt):** "REFINEMENT 2 (item #4): 'cleanup from db' — archived rooms PURGED FROM THE DATABASE (archive = actual deletion; accepted loss = literal DB cleanup, not a hidden flag)."
**Pinned posture (architecture §2.1/§2.2):** purge = ONE transaction per room: insert the content-free `room_purged` event (ids + counts only) → DELETE `room_messages` → DELETE `chatroom_members` → DELETE `chatrooms` (atomic commit/rollback). **NO archive tables (dropped from the design — RK-11 win); NO tombstone** — purged rooms are NONEXISTENT to every predicate (read/ACL/boot/digest resolve typed not-found; no tombstone dependency). Capability-row third column = `purged` — "none — accepted loss = LITERAL DB CLEANUP". Triggers unchanged: P-close (closed ≥14d) and P-idle (silent ≥72h + zero non-terminal among members ∪ owner ∪ owner-ancestry).

### D-S13 · REFINEMENT 3 — P6 UI direction (KV `gc_ui_refinement_p6`)
**Ruling (verbatim):** "BINDING 2026-11-15 (P6 UI/audit scope): CHATROOM ICON next to the WATCHOVER button on the frontend instances surface. Click → BIG POPUP (large modal, NOT a cramped tooltip) showing (a) chatrooms created by that instance('s tree) and (b) messages in each room — pick instance → see its rooms → read any room's conversation. This is the PRIMARY human observability surface for group chat (complements the SSE live view), on demand from the instance row the user already looks at. Rides the SAME room-read API as the agents' read tool (members ∪ owner-ancestry predicate, fail-closed) with the FE's own auth posture. Fold into P6 UI/audit design + FE conventions (Angular / metrics-tab patterns)."
**Pinned (architecture §13.1):** chatroom icon adjacent to WATCHOVER on the instances surface; click → LARGE modal; instance → rooms → conversation; SAME room-read API (predicate parity: members ∪ owner-ancestry, fail-closed) under the FE auth posture; Angular/metrics-tab conventions as P6 design inputs; P6 exit criterion recorded (icon+modal wired with predicate parity; SSE parity; purged rooms render not-found).

---

## Counts
SETTLED: 13 (9 Phase-1 rulings/frame + D-S10..D-S13 Phase-2 ratification & refinements). OPEN at framing time: 6 — **ALL RESOLVED**; live state = **RATIFIED 2026-11-15 — all 7 as recommended (D-S10) + refinements R1–R3 (D-S11..D-S13); Phase-2 implementation in flight**. Architect-input details inside settled rulings: D-S3 suffix mechanics (resolved; owner name included); D-S1 durable-fallback ownership (resolved; D2 closes the owner gap).

---

## P1 implementation ambiguity resolutions

Recorded 2026-11-15 by the P1 implementation lane (branch `feature/group-chat-p1`, tip 97293fb6a) — the six ambiguity resolutions made while implementing the P1 substrate, persisted in-repo per O-4 so they survive KV/context TTL. Load-bearing tokens kept verbatim.

1. **`derive_undelivered_tagged` = over-inclusion on purpose.** Returns per-(member, tagged-row) candidates WITHOUT @name-target resolution — targeting/lane assignment is P3's; over-inclusion is the fail-safe direction. Documented on the method.
2. **`role=parent` lives in the enum, not the P1 writes.** Carried in the `RoomMemberRole` enum per §1.3's closed set `owner|parent|child`; P1 writes `owner` only (the P2 join surface writes the rest) — the enum exists so P2 needs NO migration.
3. **`message_count` is the one denormalized counter.** Bumped inside the append transaction (no drift, proven under concurrency); member-count left to on-read COUNT.
4. **Purge event `instance_id` = room owner.** The trace rides the owner's event stream (room rows vanish); payload trimmed to the literal ids+counts pin `{room_id, owner_instance_id, tree_root_id, messages_purged, members_purged}` in 97293fb6a.
5. **Owner-ancestry read scope is INDEPENDENT of membership state.** A member who LEFT but sits in the owner's ANCESTRY chain (chain walks UP via `instances.parent_id`) still reads via the chain; pinned by `test_read_matrix_left_ancestor_member_reads_via_ancestry`; scope vs roster are different predicates.
6. **PG behavior proof ran against the worktree-fenced `ensemble_dev`.** (`ensemble_dev_scratch` denies instances INSERT to the dev role) with per-variable `POSTGRES_*` scrub + full teardown + verified zero leftovers; it caught two PG-only defects (JSONB bind via `JSONBType`; native-list JSONB read branch) fixed in-code.

---

## P2 implementation ambiguity resolutions (appendix A-P2)

Recorded 2026-11-15 by the P2 implementation lane (branch `feature/group-chat-p2`, base e3787d4b8) — the ambiguity resolutions made while implementing membership/spawn/naming, persisted in-repo per the P1 lesson (O-4) so they are never re-litigated.

**A-P2-1 · Join-side room-cap counting base EXCLUDES the target room.** The pinned `_validate_room_join` conjunct is `active-rooms-per-tree < ROOM_MAX_ACTIVE_ROOMS_PER_TREE`; the count is ACTIVE rooms sharing the room's `tree_root_id`, target room EXCLUDED. Rationale: the cap is a CREATION budget (room_create, P5, fails closed at 8); a join adds a member, not a room. Including the target would freeze joins into room #8 the moment 8 active rooms exist — a design that allows creating room #8 but refuses joining it is incoherent. The join-side conjunct is the fail-closed ANOMALY guard (a tree somehow over budget — config shrink / direct DB writes — refuses new members, typed `RoomJoinRoomCapError`). Pinned by `test_room_cap_counting_base_excludes_target` + `test_room_cap_counts_active_rooms_only`.

**A-P2-2 · The name registry spans ALL membership rows (LEFT included).** `UNIQUE(room_id, member_name)` is a hard table constraint spanning every row, so the tier-4 suffix registry's `taken` set reads ALL rows regardless of state — a LEFT member's name is NOT recycled; the next same-base join suffixes past it (`test_left_row_name_stays_taken`). Cap accounting stays INDEPENDENT: `ROOM_MAX_MEMBERS` counts ACTIVE rows only (owner counts, LEFT free) — pinned both directions (`test_member_cap_counts_active_only_left_free`, `test_owner_counts_toward_member_cap`).

**A-P2-3 · Suffix format = smallest-free `-N` starting at 1.** The KV ruling says "(capturer-1, capturer-2, …)" with "colliders smallest-free -N" (architecture §6 tier 4): the FIRST bearer of a base name gets it BARE; each collider takes the smallest free `-N` (N from 1). The tier-3 example's "capture-2" is the composite OUTCOME shape (agent-name base + tier-4 suffix), not a 1-based-first-suffix contradiction. Pinned by `test_resolve_smallest_free_suffix` + `test_join_room_bare_then_suffixed`.

**A-P2-4 · Joiner tree-root resolution = the CALLER's root (pre-spawn proxy).** The child being spawned does not exist when admission runs; its tree root is by definition the spawning caller's root (child.parent_id = caller, and `get_tree_root_id` walks up). `_prepare_room_join` therefore resolves `get_tree_root_id(current_instance_id)` — a `None` root (caller row unresolvable) fails CLOSED at the cross-tree conjunct, typed `RoomJoinCrossTreeError` (`test_unresolvable_tree_root_fails_closed`). P2 join-at-spawn is child-only: role is always `child` on this path; `parent` stays an enum member for P5 surfaces.

**A-P2-5 · Room-lock hold spans the whole spawn call (tier-2 wait stays OUTSIDE).** The per-room lock is taken by the spawn tool AROUND `manager.spawn_instance(...)` so the tier-4 registry's read→insert (inside `_spawn_instance_db_sync`'s ONE WriteGuardSession transaction — the "membership row + JSONB in one tx" exit criterion) cannot interleave with a concurrent same-room join; `UNIQUE(room_id, member_name)` remains the DB backstop (fingerprint-classified to `MemberNameCollisionError`, whole-spawn rollback). The 3.0s tier-2 naming wait runs BEFORE the lock so a slow model cannot serialize unrelated same-room writes. Pinned by `test_concurrent_same_room_spawns_distinct_names` (tool level) + `test_concurrent_same_name_joins_one_bare_rest_suffixed` (service level).

**A-P2-6 · Naming-cascade base names are ALWAYS sanitized, defense-in-depth at the cascade seam.** Tiers 1-3 each pass through `sanitize_member_name` (lowercase kebab `[a-z0-9-]`, collapsed dashes, `MEMBER_NAME_MAX_LEN=48`); an unusable candidate sanitizes to `None` and falls through (tier 1 dirty → tier 2 → tier 3 agent-id fallback, never an invented name). Tier 4 suffixing happens at REGISTRATION inside the spawn transaction. The FINAL name rides `instance_metadata["member_name"]` (additive JSONB key, zero migration, alongside the design-pinned `join_room` key) so the instance KNOWS ITS OWN NAME (Ruling B) and restore can re-read it. Pinned by the tier matrix (`test_tier1..test_tier4` at tool level; `test_dirty_provided_falls_through_to_tier2` at service level).

**A-P2-7 · Spawn-surface param names: `join_room`, `member_name` (exact strings).** `join_room` = the chatroom UUID (matches the design-pinned JSONB key `join_room`); `member_name` = the parent-provided purpose name (tier 1). `member_name` WITHOUT `join_room` is inert (no room context ⇒ ignored, no refusal) — the join surface is opt-in per spawn. Every tier's FINAL name is returned in the spawn toolcall result via a `room join: room=<id> member_name='<final>' (naming: tier N …)` line — no silent naming (Ruling B); the legacy (no join_room) result is byte-shape unchanged (empty segment filtered by the join protocol).

**A-P2-8 · No migration in P2.** The members table already carries `UNIQUE(room_id, member_name)` + role/name/state columns (P1's migration 131, ratified multi-table single file); the spawn JSONB keys are additive on the existing `instance_metadata` JSONB column (the D-S3 anchor: `join_room` = zero migration). F3's "chatrooms.name has no UNIQUE" is about the ROOM name — room identity stays `room_id` only, unchanged.

---

## P2 OPT resolution appendix (house review, 2026-11-15)

**A-P2-9 · OPT-3 — `model_quick` default resolution.** The `model_title` pattern is: field default `str | None = Field(default="<alias>", …)` where `<alias>` is a purpose id the deployment's proxy already serves, plus the `set_title_model_fallback` model_validator that clears `None`/empty → `model`. `model_quick` now mirrors it FULLY: `default="quick"` (not `None`) — resolved per the pattern because `'quick'` is a VERIFIED alias in this deployment: `agents/explorer/meta.json` declares `"llm_model": "quick"` (the explorer routes through it), and `'quick'` is a purpose-id alias exactly like `'vision'`/`'img-gen'`/`'decision'` that purpose-bound consumers bypass the allowlist to use. The validator (`set_title_model_fallback`) now also covers `model_quick` (cleared → `model`). `room_naming._model_for_naming` keeps its own `None`/empty fallback as defense-in-depth for a MagicMock/non-validator config. SCOPE note extended to list `model_quick`. No substrate ambiguity remains; no further decision needed.

**A-P2-10 · OPT-2 — cap-conjuncts-outside-lock residual (accepted, bounded).** A-P2-5 scopes the per-room lock to the registry's read→insert only. The admission validation (`_validate_room_join`, including the `ROOM_MAX_MEMBERS` and `ROOM_MAX_ACTIVE_ROOMS_PER_TREE` counts) runs BEFORE the lock is acquired, so two spawns admitted at the boundary (e.g. both validated at 15 active) can both insert → a bounded overshoot of ≤ the number of spawns racing the last slot. The `UNIQUE(room_id, member_name)` backstop does NOT catch this (names differ). Accepted because: (a) spawn-into-a-room is parent-driven and effectively sequential in practice; (b) the caps are cost-discipline bounds, not correctness invariants; (c) moving the admission reads inside the lock would hold the lock across DB reads for every spawn and was not warranted. Bound: overshoot ≤ concurrent racers at the boundary (in practice ≤ a handful; never unbounded). P3 inherits this posture unchanged — do not re-litigate; if a later phase needs a hard cap, move `validate_room_join` under `get_room_lock` (a one-block change) rather than adding a new mechanism.

**A-P2-11 · OPT-5 — `room_join` event asymmetry (join defers to P3; leave emits today).** `leave_room` emits a content-free `room_leave` EventKind row TODAY (ids + per-room name, owner's event stream). The spawn join path does NOT emit a `room_join` event at P2 — it emits only the spawn-scoped INFO log line ("spawn room join: room=… instance=… member_name=…") and persists the membership row + JSONB keys. The content-free `room_join` EventKind (registered in P1) is reserved for P3's delivery lanes, which own the event-emission surface alongside tag/address routing. P3: emit `room_join` on admission (content-free, same shape as `room_leave`) — no new kind needed. Do not re-litigate the split; the asymmetry is intentional and recorded.

---

## P3 implementation ambiguity resolutions (appendix A-P3)

Recorded 2026-11-15 by the P3 CORE implementation lane (branch
`feature/group-chat-p3`, base 09a683ddc) — the ambiguity resolutions
made while implementing the P3 delivery machinery, persisted in-repo
per the P1/P2 lesson (O-4) so they survive KV/context TTL.

**A-P3-1 · Injection source composite.** The commission requires BOTH
author provenance (`internal_agent:<author>`) and the room context
shape (`room:{room_id}:{sender_name}:{seq}`) on the injection source.
To avoid the dispatch-chain machinery mis-parsing the composite as an
instance id, the source is `room:{room_id}:{sender_name}:{seq}:author={author_instance_id}`
— the room shape stays the prefix (first four segments) and the author
identity rides the final `author=` segment. The drain site stamps the
string verbatim onto `HumanMessage.additional_kwargs["source"]`; no
downstream parser depends on the `internal_agent:` prefix for room
notifications, so no dispatch-chain side effect is introduced.

**A-P3-2 · Proposal row authorship.** A minted `task_proposal` row
carries `author_instance_id`/`author_name` of the SENDER of the
triggering post (the sibling whose @tag/addressing caused it), not the
room machinery or the parent. Speaker attribution rides the sender;
the proposal text itself names room + sender + member + options.

**A-P3-3 · `member` in the dedup uniqueness.** The `(source_message_id,
trigger, member)` uniqueness uses the per-room member NAME (not the
instance id). Names are per-room unique and never recycled (LEFT rows
keep their names — A-P2-2), so they are a stable dedup key; instance
ids would also work but the notice shape + roster resolution key on
names, so names are the consistent choice.

**A-P3-4 · Lane-(b) CAS lives in the room repository.** The
instances-table status CAS (`UPDATE instances SET status='running'
WHERE instance_id=? AND status IN (...)`) is exposed as
`RoomRepository.cas_instance_status_to_running` even though it touches
the instances table, because it is a policy-free mechanic (the caller
derives the allowed set from the enum) and all DB I/O must stay
engine-passed/sync/off-loop-wrapped. It is the only cross-table method
in the room repository and is documented as such.

**A-P3-5 · Boot delivery is a separate pass.** `run_room_boot_regen`
stays a pure derivation (its P1 test stays green); boot delivery per
the lane map runs as a separate best-effort pass
(`RoomDeliveryService.boot_deliver`) immediately after, re-using the
same `derive_undelivered_tagged` predicate. Pinned pair #2 is honored:
tagged terminal rows mint a durable proposal (Y14 cursor advance) but
NO parent routing occurs at boot. Non-terminal tagged rows route per
the lane map (injection/enqueue/park).

**A-P3-6 · `addressed_to` resolution reuses the active roster.** A LEFT
member or a cross-room name resolves to `None` (warning + skip, post
still succeeds) because `list_members(active_only=True)` is the
resolution base — this matches the capability row (LEFT = inactive
for routing) and the cross-room fail-closed rule without a separate
code path.

**A-P3-7 · Audit emission is best-effort.** Every `room_*` EventKind
emission on the delivery paths is wrapped so a failure logs a WARNING
and never breaks delivery. The content-free contract (ids/counts/
outcomes only, never message text) is enforced by construction in the
emitted payloads.

**A-P3-8 · Proposal dedup is checked in Python + the DB backstop.** The
router checks for an existing proposal row with the same
`(source_message_id, trigger, member)` before minting (idempotent
re-route / boot), and the UNIQUE index is the DB-level belt. Both
layers are needed: the Python check avoids a wasted append + the
unique-index violation on re-derivation, and the index covers races
the Python check cannot see.

**A-P3-9 · Corrupt tagged_members JSON warns with the row identity
(O-6, P3 carry-over lane).** The carried anchor for O-6
("repository.py:729-740, logger.debug") was STALE: the corrupt-JSON
branch actually lives in `RoomRepository.derive_undelivered_tagged`
and, as shipped by the core lane, logged NOTHING — the corrupt row was
swallowed into `tagged = []` in total silence. Resolved: the branch now
emits a `logger.warning` carrying room_id + message_id + seq, and the
row stays excluded from the pending set (a corrupt payload cannot be
tag-delivered). Rationale for warning over debug: the only writer
(`append_message`) always binds valid JSON through JSONBType, so a
corrupt payload implies EXTERNAL mutation of a delivery-critical
column — a data-integrity signal, not expected noise; and
`derive_undelivered_tagged` runs at boot + delivery routing, not a hot
path. "Neither raises nor silently vanishes" is pinned by
`test_corrupt_tagged_json_warns_and_excludes_row`.

**A-P3-10 · The live PG integrity fingerprint is the NAMED-CONSTRAINT
form (D1c, verified empirically).** On this deployment's PG (psycopg3,
fenced `ensemble_dev`, private-schema probe, 2026-11-15), a real
UNIQUE violation reports `duplicate key value violates unique
constraint "uq_chatroom_members_room_member_name"` — the named form;
the dotted-pair message (`chatroom_members.room_id,
chatroom_members.member_name`) is the SQLITE form and never fires on
PG. The classifiers accept EITHER shape (fingerprint-or), so both
drivers classify identically; the execution pin
(`test_room_migration_runner_execution.py::
TestConstraintFingerprintDriverDependence`) pins BOTH branches — the
SQLite form empirically off the executed schema, the PG form via the
documented psycopg3 message — and the composite-PK negative leg
(never mistyped as a name collision). The lockstep on constraint NAMES
is deliberately NOT pinned (SQLite auto-names inline UNIQUEs); the
driver-honest invariant is (column-set → unique) pairs.

**A-P3-11 · `after_seq` is EXCLUSIVE at the boundary — implemented
contract confirmed consistent with every doc claim (O-2).**
`list_messages` returns rows with `seq > after_seq` (strict), ordered
by seq ASC, truncated to `limit` — i.e. the FIRST `limit` rows above
the cursor (the pull-model digest window). The boundary row AT
`after_seq` is excluded; consecutive cursor walks cover the log
exactly once (no dup, no loss). The re-derivation predicate
(`seq > max(last_read_seq, last_delivered_seq)`), the unread COUNT
(`seq > last_read_seq`), and the room_read tool prose all use the same
strict form — no divergence existed to resolve. Pinned by the four
window tests (seed N=7 > limit=3: window, boundary exclusion,
pagination walk, beyond-max empty) in
`test_room_repository.py`.

**A-P3-12 · The L6 skip (P1) is recorded here — the in-repo referent
is unrecoverable (D2).** The P1 review record (KV-only,
`gc_p1_review_followups`) directed: "Record the L6 skip (dual-defaults
target + rationale) in-repo." Recovery attempt: NO L6-numbered item
exists anywhere in-repo — not in the group-chat planning docs, not in
the tests (the only in-repo L-number is the L8 read-surface symmetry
pin), not in git history of the group-chat lanes, not in the tester
gate record. The full L6 text therefore lived only in the review
channel, which has TTL'd. THIS ENTRY is the in-repo record the item
asked for: the skip STANDS (nothing was implemented for L6 in P1, and
P3 does not implement it either), the label "dual-defaults target" is
preserved verbatim for future identification, and if the referent
resurfaces (e.g. a model-field dual-default like `model_quick`'s
Field-default + validator-fallback pair — the closest in-repo concept)
it should be re-filed as a fresh A-P3-n rather than silently folded
here. Flagged to the reviewer for confirmation at the gate.

**A-P3-13 · `room_join` emission on admission is SAME-TRANSACTION and
the room read happens BEFORE the first pending add (A-P2-11 closed).**
`join_room_in_session` now adds ONE content-free `room_join` Event row
(ids + final per-room name; the room OWNER's event stream — the exact
`room_leave` shape) in the caller's transaction, so a rolled-back
admission (UNIQUE name-collision fire) leaves NO phantom event. One
mechanics note worth keeping: the owner stream is read via
`session.get(ChatRoom, room_id)` BEFORE the membership row is added —
a query AFTER the first `add` would trigger SQLAlchemy's
query-invoked autoflush and move the collision's failure point from
the CALLER's commit (where the typed backstop classifies it — the
spawn sim and the standalone path both rely on that ordering) into
the helper. Pinned by the emission/phantom/parity trio in
`test_room_membership.py::TestRoomJoinEventEmission`; the pre-existing
spawn/lifecycle/naming pins stayed green unchanged.

**A-P3-14 · Purge-path FK integrity classification is message-shaped,
uniform-typed (item 10, conditional — cheap, taken).** The purge's
IntegrityError wrap now distinguishes FK-shaped failures (PG named
prose "violates foreign key constraint" / SQLite terse "FOREIGN KEY
constraint failed" — one probe, both drivers) from every other
integrity shape, labelling the former `purge FK integrity failure`
(child-first violated? a referencing table exists?). The TYPE stays
the base `RoomError` either way — no new exception class, no new
call-site surface, no migration. The classifier is module-level
(`is_purge_fk_integrity` / `raise_typed_purge_integrity`) and pinned
by `TestPurgeFingerprintClassification` (both FK shapes labelled,
UNIQUE shape keeps the generic label, healthy purge path untouched).

**A-P3-15 · The per-room lock registry clears on stop — hygiene, not
correctness (item 9).** `RoomChatService.clear_lock_registry()` drops
every loop-local lock behind the registry guard (returns the count)
and `manager.shutdown` runs it as a best-effort step
(`clear_room_lock_registry`) before the DB pools go away. Locks are
never persisted and correctness lives in the DB backstops
(RK-13 posture), so the clear is symmetry/hygiene with
`get_room_lock`'s LRU maintenance — a restart re-derives locks on
demand. The manager hunks carry the lane marker per the house
drift-pin discipline; `TestP11BootNoBehaviorChange` stays green.

**A-P3-16 · §4.4 resolution-warning row persistence is DEFERRED to P5 (decision rule applied, not skipped).** §4.4 wants the tag/address resolution warnings on the ROW, not only the `route_post` result dict. The decision rule (existing JSONB field that can carry per-name warnings with NO new migration → persist now) was evaluated against `RoomMessage`'s only JSONB columns — `tagged_members` / `addressed_members` — and REFUSED for both: each is a typed `list[str] | None` carrying a distinct routing semantic (escalation / addressing dimension), and a warning-shaped entry would (a) violate the payload contract every reader assumes (`list(tagged_members or [])` normalization, the TAGGED filter in `derive_undelivered_tagged` → boot re-derivation would attempt lane-routing unresolvable names every boot), and (b) still need a shaped map (name → reason), which a `list[str]` cannot carry cleanly. The raw name lists ARE on the row (append persists them verbatim), so warnings remain re-derivable at read time by re-resolving against the roster; until the P5 tool surface lands a dedicated row-persistence shape (its own migration), warnings ride the `route_post` result dict only. This entry IS the required disposition record.

**A-P3-17 · The lane-(b) CAS-miss / fresh==running branch now DELIVERS (fix) — the pre-fix shape was a silent tagged-notification drop.** Pre-fix (3b092ea85): when the lane-(b) CAS missed (rowcount 0) and the fresh re-read showed `running`, the branch advanced the delivered cursor and returned `"enqueued"` WITHOUT injecting or enqueueing — diverging from the total lane map (running → (a) injection) AND defeating boot re-derivation, because the pre-advanced cursor hid the row from `derive_undelivered_tagged` forever. Fixed shape: attempt delivery now — `running ∧ has_live_graph_task` → lane-(a) injection with the A-P3-1 composite source stamp; `running ∧ ¬has_live_graph_task` → DEFECT-A downgrade to lane-(b) enqueue (a graphless-running member never sees a mid-turn injection); either failure (or any exception) → `deferred_cursor` with the cursor UNTOUCHED, so boot re-derivation retries. Outcome labels are now accurate (`injected` / `enqueued` / `deferred_cursor` — never `"enqueued"` for an undelivered row). Mutation-grade pins: `test_cas_miss_running_injects_and_advances_cursor`, `test_cas_miss_running_inject_failure_leaves_cursor_untouched`, `test_cas_miss_running_graphless_downgrades_to_enqueue` (deterministic race simulation wrapping `cas_instance_status_to_running`) — all six remediation pins redden against the pre-fix file and pass post-fix (verified by swapping `3b092ea85:daemon/services/room_delivery.py` in and re-running).

**A-P3-18 · The CAS→enqueue TOCTOU window is DOCUMENTED, accepted-risk (not guarded).** Window: between `cas_instance_status_to_running` succeeding (lane-(b) admission) and `enqueue_message`'s internal fresh status re-read (the revive gate in the dispatcher), a member completing a FULL turn in that window would land terminal again — and the enqueue would then auto-revive it, the exact terminal auto-revive the CAS pre-flip exists to defeat (Ruling A). Window size: two adjacent async DB operations (CAS commit → queue-stats probe → enqueue prepare) — milliseconds; a member would have to finish a multi-second turn precisely inside them (i.e. the turn completed concurrently with the routing). A post-enqueue status guard is NOT a one-liner: distinguishing "revived by our enqueue" from "went terminal on its own afterwards" needs revive-epoch correlation, not a re-read. Accepted risk: the probability is negligible and the failure mode (a revive the design wanted to prevent) is visible in the audit stream; revisit only if room-paced traffic ever makes the window hot.

**A-P3-19 · Graphless-running PARENT takes the DEFECT-A downgrade (fix) — the §7 "RUNNING → (a)" narrowing is closed.** Pre-fix, a parent in `running ∧ ¬has_live_graph_task` matched NO branch in either proposal-routing site (the addressed-lane parent-target matrix and `_route_proposal_to_parent`): the injection branch required the live graph task, the enqueue branch listed only the idle-family, parked handled only `paused` — so the notice stayed durable-pending with no delivery attempt, silently narrowing §7's total map for this parent state. Both sites now fold `running` into the enqueue branch: graphless-running parent → lane-(b) enqueue (same source/metadata shape as the idle-family), restoring DEFECT-A symmetry with the member-side downgrade. Boot behavior is unchanged: `boot_deliver` never parent-routes (pinned pair #2) and mints proposals directly. Pins: `test_tagged_terminal_graphless_running_parent_gets_enqueue`, `test_addressed_terminal_graphless_running_parent_gets_enqueue` (parent explicitly `running` in the DB + `live_graph=False` — an earlier draft seeded the parent idle and passed for the wrong reason; caught in the mutation pass and fixed).

**A-P3-20 · `boot_deliver` counts OUTCOMES, not attempts (fix, trivial tier taken).** Pre-fix, `delivered` incremented for every routed row regardless of the lane outcome — parked and deferred rows counted as "delivered". The pass now classifies: `deferred_cursor` / `parked_paused` → `deferred` (the row stays above the cursor and re-derives next boot); everything else (`injected` / `enqueued` / proposal-mint, which advances at admission per Y14) → `delivered`. Counts dict gains the `deferred` key (manager consumer only debug-logs the dict — no pinned shape). Skips (unreadable post / departed member / purged room) still count as neither. Pin: `test_boot_deliver_counts_reflect_outcomes` (parked row → deferred; same row delivers on the next boot after the member becomes routable).

**A-P3-21 · The boot pair-#2 race nick is CLOSED by an `at_boot` flag (fix taken, not accepted-risk).** Pre-fix, a member flipping terminal between `boot_deliver`'s lane-assignment status read and `_tagged_lane`'s own re-read took the FULL terminal branch — including `_route_proposal_to_parent` — i.e. parent routing COULD fire at boot across a ms-scale race window, nicking pair #2's absolute "never routed at boot" wording (member still never revived). Disposition: `_tagged_lane` gained `at_boot: bool = False`; the boot pass passes `at_boot=True`, making the terminal branch MINT-ONLY at boot (admission-gated cursor advance, NO parent routing). The C2 option "record accepted-risk" was NOT taken — the flag was genuinely small and restores the absolute wording. Pins: `test_boot_terminal_race_never_routes_parent` (deterministic race: first status read sees alive, lane re-read sees terminal; asserts ZERO parent set_injection/enqueue — mutation-grade, reddens both against the pre-fix 939cf171a file AND under targeted `if at_boot:` removal) and `test_boot_terminal_mints_exactly_once_and_never_routes_parent` (the pair-#2 DEDICATED pin that previously did not exist: terminal member + tagged row at boot → exactly ONE proposal row + Y14 cursor advance + zero parent calls + parent status unchanged — this pin pins an EXISTING guarantee and is not mutation-claimed).

**A-P3-22 · `_mint_proposal` returns an ADMISSION RESULT; all four cursor advances are admission-gated (F1 fix).** Pre-fix, `_mint_proposal` swallowed every append failure (warn + return None) while its four callers advanced the delivered cursor UNCONDITIONALLY — when the parent is terminal/paused the durable proposal row is the SOLE carrier of the D3 one-hop guarantee, so a mint failure silently and PERMANENTLY lost the task_proposal (the advanced cursor hides the row from the cursor-gated `derive_undelivered_tagged`), and violated Y14 (cursor advances at ADMISSION, not at attempt). Fixed contract: `_mint_proposal` returns True iff a durable task_proposal row EXISTS after the call — (a) minted here, (b) DEDUP-PRESENT (the row was already there — that IS admission; treating it as failure would re-deliver proposals forever), or (c) concurrent-winner (the UNIQUE(source_message_id, trigger, member) backstop raced us; the winner's row is verified via the read probe before admitting). Only NO-durable-row → False. All four sites gate BOTH the cursor advance AND the parent routing on admission; False → `deferred_cursor`, cursor untouched, boot re-derivation retries (never silently dropped). Sites: `_tagged_lane` terminal, `_lane_b_enqueue` fresh-terminal re-route, `_addressed_lane` terminal, `boot_deliver` terminal (the last counts deferred). Mutation-grade pins: `test_mint_failure_route_time_defers_and_keeps_cursor` (lane (c), route-time — append-raise injected; asserts deferred_cursor + cursor 0 + no row + no parent call) and `test_mint_failure_boot_time_defers_and_keeps_cursor` (lane (c′)/boot-time — asserts deferred count + cursor 0 + no row); BOTH redden against the pre-fix 939cf171a file (3 failed / 20 passed under the swap) and pass post-fix (23/23).

**A-P3-23 · §4.4's doc-promised "no-wake" pin landed; the carry-over "189" count is CORRECTED as a different-scope/different-tip figure.** Two bookkeeping items. (1) F3: the addressed-to-ALIVE-NOT-RUNNING → digest-only NO-WAKE behavior (long implemented, unpinned) now has its dedicated pin — `test_addressed_alive_not_running_is_digest_only_no_wake` asserts digest_only outcome + ZERO enqueue_message calls + ZERO set_injection calls (addressing alone never interrupts; no MessageQueue row written). This closes §4.4's promised pin #2. (2) C3 record correction: the carry-over lane's "189 passed" was relayed without scope/tip qualification and does NOT reproduce at any single current scope — the reviewer's 939cf171a measurements are the reproducible ones (9-file room set 185; +TestP11 191; TestP11 alone 6; delivery file 18). Reconciliation ARITHMETIC (all node counts measured via `--collect-only` at the remediation tip; the two non-room files are untouched by remediation so collection is tip-valid): the 9-file room set at the pre-remediation tip 3b092ea85 = 185 − 18 + 12 = 179 (delivery file grew 12 → 18 with the iteration-1 pins); + TestP11BootNoBehaviorChange 6 + test_f1_premark_p5.py 4 = 189 EXACTLY — i.e. "189" was the carry-over lane's OWN broader scope (room set + TestP11 + premark file) measured at the OLD tip. The canonical current-tip counts are the fresh re-runs in the iteration-2 report; measured-counts-only discipline applies to every report from here.

**A-P3-24 · The two-hop boot race is CLOSED — and A-P3-21's "pair #2 absolute wording restored" was FALSE as stated (append-only correction).** A-P3-21 closed only the ONE-HOP window (boot read alive → `_tagged_lane` terminal). The race survived one hop deeper: `_lane_b_enqueue` took NO `at_boot`, and its CAS-miss → fresh-terminal re-read branch parent-routed unconditionally — reachable from BOTH alive descents (the graphless-running downgrade and the lane-b CAS path). For running-graphless members the CAS-miss is UNCONDITIONAL (`running` ∉ `LANE_B_CAS_ALLOWED`), so the fresh-read transit there is GUARANTEED, not ms-luck. Fix: `at_boot` threads through both descents into `_lane_b_enqueue`; its fresh-terminal branch is mint-only at boot (`return "proposal"` before the parent-route call) — the boot counting needs no change ("proposal" already classifies delivered). Pin: `test_boot_two_hop_race_never_routes_parent` — member reads alive at BOTH pre-descent reads (boot lane read + `_tagged_lane` re-read), flips terminal hooked on the CAS (alive at descent → terminal at the fresh re-read); PARENT seeded routable-looking (running in DB + live-graph mock) and never mutated, per the C2 one-hop pin's construction lesson — under mutation the reddening assertion is the real `_route_proposal_to_parent` parent-inject call (`set_injection.call_count 1 != 0`), not a parent-status side-effect. Mutation evidence: targeted `if at_boot:` → `if False and at_boot:` sed (the 16-space guard, unique to the fresh-terminal branch) → pin RED (`AssertionError: two-hop window: NEVER parent-inject at boot`); byte-identical restore → GREEN. A first mutation invocation surfaced a transient pytest collection ERROR (not a test verdict); the rerun produced the deterministic red above and is the authoritative evidence. This entry SUPERSEDES the phrasing of A-P3-21's closure claim (the flag then covered only the one-hop branch); the one-hop pin and pair-#2 semantics themselves stand.

**A-P3-25 · The purged-mid-lane None-deref window at `room_delivery.py:377` is DEFERRED (accepted-risk; pairs with the A-P3-18 TOCTOU defer).** Hazard shape: a room purge (P-close/P-idle — ONE transaction deleting `chatrooms`+`chatroom_members`+`room_messages`, R2) landing between a delivery lane's early room reads and its subsequent room-scoped operations leaves those later calls (fresh re-reads, `advance_delivered_cursor`, audit emit) operating on a deleted room — a None-deref or raise on the gone row. Containment is structural: every lane body rides the per-row best-effort try/except (warn + skip), and R2 makes purged rooms NONEXISTENT to every predicate, so the row legitimately stops re-deriving — the failure mode is one contained warning, never a crash or a wrong delivery. Why deferred: the purge triggers are room-paced (closed ≥14d, idle ≥72h + zero non-terminal), so a purge landing inside a ms-scale lane window is negligible; a real guard needs purge-epoch/room-epoch correlation to distinguish "purged mid-lane" from "never existed" — the same not-a-one-liner shape as A-P3-18's revive-epoch need. Disposition mirrors A-P3-18: accepted risk, visible in the audit/warn stream, revisit only if room-paced purge traffic ever makes the window hot.

**A-P3-26 · The lane-(b) CAS writes `instances.status` directly, bypassing the instance state machine — DEFERRED; the delivery-shape substance is CLOSED by A-P3-17/22/24, the bypass itself stays open.** The record: `cas_instance_status_to_running` (room repository) flips `instances.status` via a targeted atomic UPDATE scoped to an explicit allowed set — it does NOT go through the canonical instance state machine (`job_state_machine.py` transition validation), so a room lane can flip a status the canonical machine might refuse, writes no state-machine audit trail (room flips are visible only via room audit events), and derives its allowed set from the room 10-state enum rather than the canonical transition table. What the remediations CLOSED: every delivery-shape consequence of the bypass — the CAS-miss silent drop (A-P3-17), the un-admission-gated cursor advance (A-P3-22), and both boot race windows (A-P3-21/24) — is fixed and mutation-pinned; the bypass no longer loses or over-delivers notifications. What remains OPEN: (1) enum↔machine drift — if the instance lifecycle states or canonical transitions ever change without the room enum following, the CAS set and the canonical allowed-transitions diverge silently (no drift pin exists); (2) the missing state-machine-level audit for room-initiated flips. Deferred because: the flip is atomic, narrowly-targeted (to `running`, only from the explicit allowed set), and every consumer is now gated — the bypass is by-design substrate, not a defect; the drift pin and shared derivation belong to a later surface (P5/P6 tooling or a lifecycle-lane pass) that owns the enum↔machine relationship.

---

## A-P5 APPENDIX — Phase 2 / P5 (room tool + guidelines + digest), 2026-11-16

**A-P5-1 · The TOOL-SURFACE DELTA is reconciled in favour of the design of record: FOUR verbs — `room_create` / `room_post` / `room_read` / `room_task_decision`.** The commission named "room_post / room_read (+ task_decision surface)"; plan-overview §6 (P5 row) named "room_create (seeds the OWNER membership row)/post/read tools"; architecture §13 (the ratified phase plan) named "room tools (grant pinned; **room_create seeds the owner row**)" and the §12 build-vs-reuse ledger counts "room_create (+ owner seed)" as one of the P5 [M] artifacts. The reconciliation is forced by reachability, not by preference: P1 landed the room-birth PERSISTENCE (`RoomChatService.create_room` + the OWNER-row seed) but NO daemon-side creator — the only production callers of `create_room` at the P5 base were TESTS. Without a `room_create` verb the entire P1-P3 substrate is unreachable from an agent, so the design of record's verb stands. The three other verbs follow the design: `room_post` (the milestone post + the `@tag`/`to:` addressing dimensions), `room_read` (the full history behind the read tool — the "system message = guidelines + short digest ONLY" ruling), and `room_task_decision` (the Ruling-A loop-back: the parent's answer, RECORDED ON THE ROOM). "Room creation + owner seeding landed in P2's spawn/join path" is TRUE for the JOIN half (the `join_room` spawn parameter) and FALSE for the CREATE half — hence this entry.

**A-P5-2 · The category grant census is EIGHT agent types, and the grant is CONSTRUCTION-TIME ONLY.** The `room` category is privileged entry #23; the explicit `tools.allow: ["room"]` entries land on: `ari`, `leader`, `sonic-leader`, `worker` (the generic warm lane) and the video-team pilot quartet `video-producer` / `video-capturer` / `video-editor` / `video-verifier` (the Emperor's designated FIRST pilot of group chat, KV `gc_pilot_video_team`). The room audience is NOT a fixed privileged panel — the AUTHORITY is the MEMBERSHIP gate in the tool bodies (a granted agent that is not an ACTIVE member of the room gets nothing, fail-closed, before any write). Extending the grant is a one-line allow-array edit per agent. "Construction-time grant only" is not a limitation but a consequence of the join-at-spawn world: membership is granted AT SPAWN (the `join_room` spawn parameter), so the membership row exists BEFORE the child's tools are built and the in-body gate is satisfiable from the first turn — no mid-life tool mutation is needed or supported.

**A-P5-3 · `route_post`'s sender identity is UNIFIED to ONE source — the post payload.** The pre-P5 shape took the sender as a SEPARATE argument AND read the same identity off the post: two sources for one fact that could DISAGREE. The hazard was concrete, not cosmetic — the roster read (`list_members`) rides the fail-closed ACCESS PREDICATE with `caller_instance_id=sender_instance_id`, so a caller-supplied `sender_instance_id` that differed from the post's author would have had the READ evaluated against a DIFFERENT identity than the routing (a non-member could have borrowed a member's read scope by passing their instance id). The post IS the committed record of who wrote (the append is the commit point, and the P5 tool enforces author membership BEFORE the append — RK-7-before-append), so the post is the ONE source now. The two redundant kwargs are gone from the signature; the P3 call sites were updated mechanically (every assertion unchanged).

**A-P5-4 · The UUID fallback is WIDENED — the 32-hex-only predicate was INERT.** `_is_uuid_like` accepted ONLY the 32-char un-dashed hex form, while every real instance id is the 36-char DASHED form (`8-4-4-4-12`): the fallback could therefore never resolve a real id, and only the un-dashed shape could ever match. BOTH shapes resolve now (the §6 text names the 32-hex form; the dashed form is the real id shape). The resolver is extracted into ONE module-level helper (`resolve_room_targets`) used by the router, the boot filter and the tool — the §6 semantics (exact case-sensitive per-room name match + the UUID fallback; unresolvable → warning, never a refusal) now live in exactly one implementation.

**A-P5-5 · The resolution-warning shape is STRUCTURED (`{kind, name, reason}`) — the P3 string shape is SUPERSEDED.** A-P3-16 refused the two existing JSONB routing columns as carriers precisely because a warning needs a name AND a reason; a bare string cannot carry both. The durable row shape is therefore a JSON array of `{"kind": "tag"|"addressed", "name": str, "reason": "unresolvable"}`. The P3 pins that asserted the string form ("addressed unresolved: 'x'") were re-aimed to the structured form — same behavior, same skip semantics, machine-readable carrier.

**A-P5-6 · The decision row extends the trigger vocabulary with `"decision"`.** A `task_decision` row points at the PROPOSAL row's own id (`source_message_id` = the proposal's id) with `trigger="decision"` and `member` = the member the proposal is about. The dedup UNIQUE `(source_message_id, trigger, member)` therefore allows exactly ONE decision per (proposal, member) — and never collides with the proposal row itself (a different source_message_id AND a different trigger). A second decision for the same proposal is refused by the backstop (the typed append error). The trigger stays a CLOSED vocabulary constant (`_TRIGGER_DECISION`), never free text.

**A-P5-7 · `room_read` ADVANCES THE READ CURSOR — the read ACK.** The read cursor pair exists to be advanced by a read; the re-derivation predicate is `seq > max(last_read_seq, last_delivered_seq)`, so an UNDELIVERED row still re-derives after the read ack (the delivered cursor is the binding one). Cross-lane note for P4 (the nudge lane): the unread count is the READ-cursor-based count, so a member that has read the room sees no further unread nudges — the intended semantics ("the nudge fires only when unread>0"). The P4 lane's surfaces (the todo-reminder slot, the graph END chain, the drain wrapper) were NOT touched.

**A-P5-8 · The joined-room GUIDELINES are a STATIC CONSTANT in the builder module — NEVER an agents/*.md file.** The prompt-artifact baseline (agents/*.md is DB-versioned and prompt-sealed) must not move for this feature (RK-8), so the guidelines text lives beside the code that renders it (`ROOM_GUIDELINES_TEXT` in `daemon/services/context_messages.py`). The digest reads the room's denormalized `message_count` to window the LAST N rows (the counter is bumped in the SAME transaction as every append, so it IS the latest seq — one append = one row = one seq); the digest is never the full history (the "system message = guidelines + short digest ONLY" ruling).

**A-P5-9 · The P3 tidier's H1/H2 items are LANDED; the pair-#2 invariant now has ONE implementation.** H1: `_lane_b_enqueue` (was ~160 lines at five levels of nesting) is a two-branch dispatcher over the named seams `_reroute_after_cas_miss` / `_deliver_after_cas_success`. H2: the TERMINAL branch (lane c / c′) lived in THREE copies — the tagged lane, the lane-(b) CAS-miss re-route and the boot pass — so the pinned pair #2 guard ("never parent-route at boot") was written three times, and A-P3-21/A-P3-24 had to repair TWO of the three copies SEPARATELY. It now lives in ONE implementation (`_terminal_outcome`), plus the paused branch (`_park_paused`). The ADDRESSED lane's terminal branch is deliberately NOT folded in: it mints the same durable row but delivers the (c′) PARENT NOTICE with a different shape (the excerpt-bearing notice + the notice event) — a different sequence, not a copy; the structural pin asserts exactly TWO `_mint_proposal` call sites (one per distinct lane) and exactly ONE `if at_boot:` guard. Mutation evidence: `if at_boot:` → `if False and at_boot:` → FIVE pins red (three P3 boot pins + the two new H2 pins) → byte-identical restore → GREEN.

**A-P5-10 · M-ro4 (the migration-test consolidation) landed WITH the PRAGMA equivalence proof FIRST.** The two migration-test modules each built their own file-backed SQLite engine with the same boilerplate; the builder moved to `tests/helpers/migration_execution.py::make_migration_engine` (ONE builder), with the per-call-site difference (the busy_timeout) as an explicit parameter. The equivalence proof pins that the consolidated builder reproduces BOTH original shapes EXACTLY: the runner-execution shape (FK pragma ON + a 10 s busy timeout) and the models-and-migration shape (the FK pragma only — SQLite's own 5 s default). The P5 migration (`20261116_083000_add_room_resolution_warnings.sql`) joined the executed-migration set, so the executed-schema pins now cover the new column AND its DOWN (the rollback drops exactly that column and nothing else).

**A-P5-11 · A PRE-EXISTING red at the P5 base was repaired in passing (base-proven, not introduced here).** The `nest-report-read` registration pin asserted devops's allow-array tail as `[..., "guidelines"]` while devops's meta.json has ended with `"project_future"` since before the P5 base (`adbc7bf21` — the base meta.json already carried it, the base expectation did not name it). The pin was RED at the base; the expectation was re-aimed to the live tail (the pin's INTENT — a mis-ordered array is caught mechanically — is unchanged).

**A-P5-12 · The room category's MUTATION TARGET (MT-R01) was INERT — the find-string was not UNIQUE in its target file. FIXED at the root cause; the uniqueness precondition is now pinned.** The mutation matrix's leg loop requires the find-string to occur EXACTLY ONCE in the target file (`text.count(find) != 1` → the leg is SKIPPED and the skip is counted as a leg FAILURE — the harness is loud here by design, not silent). The room pin file spelled the count literal out TWICE: once as the count pin itself (the intended mutation target) and once inside a self-referential meta-pin ("the count pin lives in this file") that asserted the literal VERBATIM. The room leg therefore SKIPPED — and that skip was the single FAILING leg of the matrix run. The SAME-PR requirement ("a category with no mutation target is not covered by the matrix") was consequently NOT met by the room category: the matrix reported 28 RED/GREEN legs and a FAIL, but the room category had NO working mutation target at all. The repair is at the ROOT CAUSE, not in the harness: the meta-pin now BUILDS the expected literal from the LIVE count (`f"assert len(PRIVILEGED_TOOL_CATEGORIES) == {len(PRIVILEGED_TOOL_CATEGORIES)}"`) instead of spelling it out, which leaves the count literal exactly ONCE in the file — and the meta-pin additionally PINS that uniqueness (`source.count(expected) == 1`), so the leg can never go inert again unnoticed. The harness's shared DERIVED find/replace shape (MT-01 / MT-G01 / MT-R01 all use `_COUNT_FIND`, computed from the live registry) is preserved — no hardcoded count was introduced, and the other two targets (board / guidelines) were left untouched. Proof: the mutation `23 → 22` now turns the room pack RED via the intended marker (`test_the_count_is_twenty_three`) and the restore returns it GREEN byte-identically. Why it was unreported: the predecessor never reached the matrix run (the lane wedged before it), so the skip was never observed.

**A-P5-13 · The P5 review re-gate lands ON THIS BRANCH as ONE commit (dispatcher decision).** The independent review of the 5-commit P5 range found three minor defects (exception text interpolated into the `room_task_decision` refusal payload; the per-tree active-room cap unenforced on the create path; unescaped agent-supplied names in the joined-room context block). The dispatcher ruled: all three fixes land on `feature/group-chat-p5` itself (the P5 re-gate), one commit, and the sibling P4 lane's merged-side files (`daemon/graph.py`, `daemon/tools/todo_tools.py`, `daemon/services/room_nudge.py`, `daemon/services/todo_manager.py`) are untouchable — none of the three fixes needs them. Re-gate scope is the touched code only (room tools / joined-room context / room repository + membership / registration sanity); the full suite remains the lane's house gates.

**A-P5-14 · The per-tree active-room cap is enforced AT THE REPOSITORY CREATE PATH (dispatcher decision; the lowest layer, fail-closed before the insert), sharing the join path's constant and typed error — and the join-side conjunct is thereby demoted to an anomaly backstop.** `RoomRepository.create_room` now counts ACTIVE rooms for the tree (the same counting base as `validate_room_join` — `state=active`, tree-scoped) and refuses with `RoomJoinRoomCapError` (the SAME class the join path raises — one condition, one error type, no second error minted) when the count is already at `ROOM_MAX_ACTIVE_ROOMS_PER_TREE`; the count is ACTIVE-only (closing a room frees its slot). Semantics: 8 active rooms allowed, the 9th create refused — which makes the join-side `>=`-on-siblings conjunct (fires at total ≥ 9) UNREACHABLE through the capped create path, exactly as its docstring anticipated ("the fail-closed anomaly guard for a tree somehow over its budget"). Consequence taken: the two join-side cap tests that built their over-budget tree via 8 `create_room` calls now seed the 9th room RAW (`_seed_raw_room` in `test_room_membership.py`) — the join-side backstop's semantics are unchanged and still pinned. New pins: `test_create_room_refuses_the_ninth_active_room_in_a_tree` + `test_create_room_cap_counts_active_only_closed_frees_the_slot` (repository), `test_room_create_refuses_when_the_tree_room_cap_is_reached` (tool, typed `room-cap` envelope).

**A-P5-15 · The joined-room block's NAME escaping — the dispatch's fix is exactly "route through the module's escape helper, no new helper"; the ambiguity (what that helper actually covers) is resolved as: the helper's documented `&<>` class is the contract, newlines are flattened by the module's OWN digest-text idiom, and the structural substrings (`](`, `[SYSTEM CONTEXT:`) round-trip BY DESIGN — disclosed, not silently widened.** `escape_for_context_block` rewrites ONLY `&`/`<`/`>` to `\uXXXX` (its docstring: ADR-7 dropped the XML fences, the escaping is defense-in-depth, and literal-text discipline is the system-prompt layer's job — "this helper protects the data body, the system-prompt-level instruction is a separate layer"). So the pin asserts the ESCAPED FORM renders byte-exactly (the `&<>` class provably rewritten — red against the pre-fix raw interpolation) and states the round-trip explicitly instead of claiming a rewrite that does not happen. Embedded NEWLINES in names, however, WERE a real structural hazard the helper does not cover (a name could forge block lines: a fake `ROSTER:` line, a fake block boundary). Resolution: names get the module's own two-step treatment that digest text already gets (`context_messages` newline-flatten at the digest text path, THEN the escape helper) — inline at each of the four interpolation sites (room label, own name, roster names, author name), no new helper minted. Join-path member names are kebab-sanitized since P3 (`sanitize_member_name`), so the member-name legs are contract-level defense (P1-era rows, direct repo writes and the unsanitized ROOM name are the live surfaces), and room names were never sanitized anywhere.

**A-P6-1 · §9's "≤4 posts/member/turn" cost-cap enforcement is P6 SCOPE; the converged shape is a manager-level per-`instance_id` counter reset at the turn boundary, incremented in `room_post`, refusing the 5th post.** P5 review adjudication 2 (2026-11-16): the cap is enforced SERVER-SIDE only, at the `room_post` PRE-APPEND region (the append is the commit point of the room log, so the counter check must precede it — the same posture as the membership gate). The counter lives at the MANAGER level (per `instance_id`), RESET at the turn boundary, mirroring the `_room_nudge_latch` precedent — no P4-owned seams are touched (the P4 merge-compatibility surfaces stay untouched). The refusal at the 5th post is TYPED (the house refusal envelope; spec-faithful "≤4" — four posts succeed, the fifth is refused). There is NO exposure window pre-promote: no live room traffic exists until the video-team pilot (`gc_pilot_video_team`), so the enforcement lands with P6 and is exercised by the pilot's E2E shakedown.

**A-P6-2 · P6 plan-row pointer:** P6 plan: carry the §9 cost-cap enforcement as a P6 plan row — room_post chokepoint design per A-P6 above.

**A-P5-16 · The absent-router signal is `routing_error: true` + a reason token — NOT a symmetric refusal — because the router-availability check sits AFTER the append, so refusing would negate an already-persisted row (dishonest the other way).** The M2 finding was that `room_post` reported `routing_error: false` when the delivery router was ABSENT — indistinguishable, for the calling agent, from "there was nobody to notify". The dispatcher's pre-decision made the symmetric-refusal fallback CONDITIONAL on the availability check preceding any write, and the code inspection settles that condition: the append (`room_service.append_message`) is committed at room.py:533 and the router handle is resolved at room.py:561 — the check is therefore DOWNSTREAM of the durable write, and P3's boot re-derivation exists precisely to recover tagged rows whose routing never ran. A refusal at that point would lie in the OTHER direction: the tool would answer "your post was refused" for a row that IS on the room log. The landed shape is the honest one: the post lands, the tool answers `routing_error: true` with `routing_reason: "delivery-service-unavailable"`, and the agent reads "the post is on the room, the routing never ran". The token is the SAME closed token the M5 split mints for the missing delivery service (one condition, one token — the M5 split gives the room service its own `room-service-unavailable` token and the delivery/routing service its own `delivery-service-unavailable`). The router-present path is UNCHANGED (`routing_error: false`, `routing_reason: ""`). Companion fixes in the same micro-lane: M1 — every `route_post` warning branch now mints the structured `{kind, name, reason}` map the router's own docstring publishes (the room-not-found branch used to return a bare string; the closed kind/reason vocabulary is `ROUTING_WARNING_KIND_ROOM` / `ROUTING_WARNING_KIND_POST` / `ROUTING_WARNING_ROOM_NOT_FOUND` / `ROUTING_WARNING_NO_AUTHOR`, and the loose `list[Any]` warning annotation is tightened to `list[dict[str, str]]`); M5 — the `room_task_decision` service check is split so each missing component names ITSELF. DISCLOSURE (scope, deliberately not fixed here): the EXCEPTION path (`route_post` raising) also sets `routing_error: true` but carries NO reason token — a follow-up, not this lane.

## A-P5-17 APPENDIX — the P5 tester-gate fix cycle (2026-11-16)

The P5 independent tester gate (`.agents/tester/RESULTS/2026-11-16-group-chat-p5-tester-gate.md`, HEAD `77a869057`) returned FAIL with two blocking areas + two nits. This appendix records EVERY disposition of that cycle, in the dispatcher's five-part split (artifact / ari grant / name bound / delivered cap / D2 wording).

**A-P5-17a · The tool-category ARTIFACT is regenerated and the vocabulary version bumps 10 → 11 (the F0.4/A24 pair contract: the constant and the artifact move in the SAME change).** The lane's own `room` PRIVILEGED category (#23) landed without regenerating `.agents/shared/metrics/tool-categories.json` — the artifact still carried 65 categories at vocabulary version 10, so the drift check and the coverage check were RED (2 of the tester's 12 net-new ids). Disposition: regenerate into a SCRATCH path (`ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=…`, never hand-edit, never rewrite the committed file from a test run) and copy it over — count 65 → 66 with `room` present — then bump `VOCABULARY_VERSION` to 11 with the version-history comment. The pair pins that assert the old numbers were re-pinned deliberately (the POK grant pack's vocabulary/artifact pair: 10 → 11 and 65 → 66; the metrics vocabulary-amendment pack's version marker: 10 → 11).

**A-P5-17b · ari IS design-granted — the two stale ari trailing-set pins are re-pinned DELIBERATELY, not papered over.** The dispatcher's pre-decision (and A-P5-2, decisions.md:379) settle it: the `room` grant census is EIGHT agent types — `ari`, `leader`, `sonic-leader`, `worker` (the generic warm lane) plus the video-team pilot quartet (`video-producer` / `video-capturer` / `video-editor` / `video-verifier`, the Emperor's designated FIRST pilot, KV `gc_pilot_video_team`) — and the grant is a ratified-phase contract, not a lane accident. So `room` STAYS in ari's allow and the pins move to the 15-entry trailing set: the POK grant pack's ari-tail assertion (6 → 7 tail entries) and the nest-anomaly-report pack's re-pinned ari literal (12 → 15 entries). Both re-pins carry the grant citation in their docstrings, so the diff reads as a CONTRACT UPDATE. The video quartet's `EXPECTED_ALLOW` constants move with the ordering fix below. The nest-anomaly-report pack's `GRANT_POST_TAIL` set gains `room` beside `guidelines` — the SAME documented-later-lane mechanism the guideline-read-tool lane used — so the `room` grant is not an offender of the "grant is last" discipline for `leader` / `sonic-leader` / `worker` (whose tails carry `guidelines` + `room`); `ari` remains the documented class exemption. DISCLOSURE: the `project_future` tails (13 agents) keep that node RED — a pre-existing debt from the v0.30.7 `project_future` lane, base-attributed (red at `adbc7bf21`), NOT this lane's; the lane's own `room` offenders are gone.

**A-P5-17c · The room NAME is BOUNDED AT CREATION — 64 chars, `^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$`, with a TYPED refusal (`invalid-room-name`), fail-closed before any DB touch.** The measured B-1 defect: the name rides the agent-facing joined-room block (the banner) on EVERY turn, so an unbounded name crowded the block until the blind whole-body slice dropped the ROSTER, then the guidelines (≥3362 chars → RECENT gone; ≥3396 → ROSTER gone; ≥3949 → guidelines gone). The bound values are the dispatcher's, unchanged: `ROOM_NAME_MAX_CHARS = 64` and the pattern above — the design's room-name idiom (`'scene-3 build'`), and a charset that keeps the escapable class (`&`/`<`/`>`) out of new names (the escape helper stays as defense-in-depth for LEGACY rows, A-P5-15). The bound is enforced in the TOOL BODY, not only in the pydantic schema: a schema-only constraint surfaces a generic validation string, whereas the body check mints the house refusal envelope with a STABLE token of its own (`invalid-room-name` — distinct from the generic `invalid-input`, because the condition is named and actionable). `room_create` is the ONLY production caller of the room service's `create_room`, so the tool body IS the creation door; the repository stays permissive so LEGACY rows remain renderable (and the escape-contract pin keeps its subject). FOLLOW-UP (not this lane): if an admin/API creation path ever appears, the same bound must ride the repository write boundary — the repository's `create_room` is the natural seam.

**A-P5-17d · The DELIVERED block is budgeted SECTION-AWARE — a section is never dropped; the DIGEST BODY is the elastic section, then the room LABEL; the guidelines / roster / own-name never shrink.** The blind whole-body slices (`context_messages.py` — the per-room cap and the concatenated-joined cap) are REPLACED by a per-room share: the joined body's 4000 chars are split into a per-room budget BEFORE any section is rendered (the envelope overhead is subtracted, so the DELIVERED message — wrapper + body — stays inside the cap), and each room's block is fitted inside its share: the digest body shrinks first, the room label second (its own cap `ROOM_LABEL_MAX_CHARS = 384` = the escaped worst case of the 64-char bound; its floor `ROOM_LABEL_MIN_CHARS = 16` keeps the room identity readable), and the mandatory content is never sacrificed. The multi-room overflow (an instance in many rooms — v1 is ONE room per instance, architecture §5 "Multi-room: v1 one room/instance; builder multi-room-capable") is LOGGED, never silent: the block overflows rather than dropping a room's orientation content. The pins are NON-VACUOUS by counter-mutation, each restored byte-identically: disabling the digest-truncation branch REDs `test_a_tight_budget_trims_the_digest_never_a_section`; replacing the per-room fair share with the full cap REDS the same pin; removing the label cap REDS `test_a_legacy_long_room_name_cannot_crowd_out_a_section`; disabling the charset check REDS the tool-side `test_room_create_refuses_a_name_outside_the_bound`.

**A-P5-17e · The create-cap refusal string is made TRUTHFUL — "at or near the active-room cap" — and the CHECK itself is unchanged.** The old wording ("this tree already holds the maximum number of active rooms") is FALSIFIED in an overshot tree (a tree holding 13 active rooms was told it held "the maximum"). The check still refuses at `active_rooms >= ROOM_MAX_ACTIVE_ROOMS_PER_TREE` (raw ≥ cap); the wording now says "this tree is at or near the active-room cap (ROOM_MAX_ACTIVE_ROOMS_PER_TREE=N) — close a room before opening another", which is true in BOTH the at-cap and the overshot case. The repository's own typed-error text already states the ACTUAL count and is unchanged. Authoritative enforcement over a legacy/overshot tree stays a GC/migration concern (P6), not this lane. The pinned text that asserted the old wording needed no change (the pin asserts the token, the constant and "close a room" — all three survive).
