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
