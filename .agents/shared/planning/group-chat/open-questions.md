# Agent Group Chat — Open Questions for the user

**Doc-set Rev 6** (single alignment stamp; 2026-11-15 final fix pass). **Status:** Phase-1 endpoint deliverable · **Date:** 2026-11-15.
**Provenance:** distilled from architecture-recommendation.md (🏛️ council 2/2 unanimous + 4 dimension analyses, all anchor-verified at tip `bf4bfde6e`).
> ✅ **RATIFIED 2026-11-15 — ALL 7 ITEMS AS RECOMMENDED** (the user, via KV key `gc_phase2_go`). Two binding refinements (R1: untagged-but-ADDRESSED-to-TERMINAL member → NUDGE THE PARENT; R2: archived rooms PURGED FROM THE DATABASE — literal DB cleanup) + the P6 UI direction (chatroom icon → big modal on the instances surface) are recorded as settled design in decisions.md D-S10..D-S13 and architecture-recommendation.md Rev 7 (§4.4 / §2 / §13.1). The 7 items below are the UNALTERED decision record.

**Reading guide:** 7 items, ordered by decision-impact. Each is a genuine choice with real trade-offs; everything else is settled within the rulings or is an implementation detail. Recommendations are ours to ratify or flip — the design bends at any of these without re-opening the substrate. Three adaptation disclosures follow the items (not decision items).

---

### 1. Substrate — ratify the hybrid (D-O4, the headline)

**QUESTION:** Adopt the hybrid substrate — a new minimal 3-table room store (one `.sql`, migration 130→131) as single source of truth, with every existing delivery lane reused as transport, `message_queue` and `shared_meta_kv` untouched?
**CONTEXT:** Council (2/2 models) reached this UNANIMOUSLY and independently; 4 parallel analyses converged on the same shape. The two simpler options fail on verified facts, not preference: message_queue's boot wipe deletes every fully-delivered room row (doomed-predicate SQL `message_queue/repository.py:1312-1322`) and it has no seq column; its enqueue lane also auto-revives terminal instances (Ruling-A hazard); shared_meta_kv is last-writer-wins, 4KB-capped, no history — the unread watermark is inexpressible on it.
**OPTIONS:** (a) Hybrid: new store + reused lanes — medium complexity, reuses 28 of 42 surfaces; (b) New store alone, new delivery too — re-builds lanes that exist (over-build); (c) Stretch message_queue — its own boot machinery destroys the log (rejected on facts).
**OUR RECOMMENDATION:** (a) — the simplest mechanism that makes the doctrine true; the mint is unavoidable, and everything else already exists.

### 2. Untagged messages to IDLE members — wake or wait?

**QUESTION:** When an untagged room message lands for a member with NO live turn (IDLE), does the system wake them or wait?
**CONTEXT:** The ruling says untagged "waits for nudges" — but nudges fire at turn boundaries, and an idle agent has no turns. Waiting means the message surfaces in the member's context digest at whatever next turn happens (any dispatch, any tag); waking means minting a full agent turn per chat line.
**OPTIONS:** (a) Wait (digest at next turn) — zero cost, but an idle team can sit unaware until something else wakes it; (b) Wake on untagged (enqueue) — immediate awareness, but every chat line mints a turn: the stream-of-consciousness enabler the cost discipline exists to prevent.
**OUR RECOMMENDATION:** (a) — ruling-faithful, cost-faithful; the parent retains the @tag instrument whenever immediacy is actually needed.

### 3. Tagged-message durability — watermark+pull or enqueue-mirror?

**QUESTION:** When a tagged (importance-escalated) message cannot be delivered immediately (RAM injection lost to pause/restart/1h-TTL), what backstops it?
**CONTEXT:** The durable room log + per-participant cursors ALWAYS contain the message — the question is whether the system ALSO mirrors every tagged push into the durable enqueue lane at post time. Council flagged this as its #1 open lean (both leaned watermark+pull). Re-derivation predicate: `seq > max(last_read_seq, last_delivered_seq)` — already-read rows are never re-injected. **Every participant has cursors, including the room's opener (seeded as an owner membership row at room_create) — so tagged `@parent` rows and task proposals are themselves re-derivable.**
**OPTIONS:** (a) Watermark+pull: re-derive undelivered tagged messages at next turn/boot from the log — zero new state, at-least-once effective delivery, but re-delivery waits for a turn boundary; (b) Enqueue-mirror: also enqueue durably at post time — stronger immediacy, but double-delivery by construction and permanent checkpointed bloat (compaction has no speaker model).
**OUR RECOMMENDATION:** (a) — the log is already durable; a second durable state machine adds failure modes without adding a guarantee the digest+nudge doesn't already cover.

### 4. Room retention & GC — archive-then-delete, or keep forever?

**QUESTION:** Do closed/abandoned rooms get archived-and-deleted on a schedule, or retained forever?
**CONTEXT:** Nothing sweeps today; without GC the room log grows unboundedly (risk RK-5). The house already has the template: an hourly maintenance seam (`MaintenanceService.register()`) and the archive-then-delete migration precedent (`20261107_122648`). **The recommended shape archives WHOLE rooms. Published capability row — the accepted-loss column reads: `archived` = "none — accepted loss" (unread/tagged rows in an ARCHIVED room are never delivered and never resolvable; the unread path excludes archived rooms entirely, so there are no phantom counts). `closed` loses nothing the log+cursors cannot re-derive: read-only, unread computed, tagged re-derivation continues UNTIL archive; only nudges are silenced. Archive triggers: a room closed ≥14 days, or an active room silent ≥72h with zero non-terminal instances among members ∪ opener ∪ opener-ancestry (a `waiting` member — alive, awaiting input — blocks it).**
**OPTIONS:** (a) Archive-then-delete with the accepted-loss column above — bounded growth, full audit trail, small new job; (b) Retain forever, no sweeper — simplest, zero deletion risk, unbounded growth; (c) Tombstone-only — loses the transcript, the room's post-commission value; (d) Cursor-respecting P-close — never archives unread rows on closure, more machinery (a cheap cursor guard is already permitted on the 72h idle arm).
**OUR RECOMMENDATION:** (a) with env-tunable defaults and a dry-run observe pass first — the acceptance window is bounded by deliberate closure + liveness; if you prefer zero loss on the closure path, (d) is the alternative at more machinery.

### 5. Non-member read access — how wide does the read predicate open?

**QUESTION:** Members and the room-opening parent always read (settled). Who ELSE can read a room — the opening parent's own ancestors (grandparent leadership), or nobody until the HTTP operator tier?
**CONTEXT:** Rooms are lateral scopes; the existing subtree read predicate cannot express them, so a room-scoped predicate is minted either way. The agent-tool predicate decides width; HTTP/operator read stays role-scoped (`instances:read`) in every option. (The opener is itself a participant — an owner membership row is seeded at room_create — so "parent can always read" is structural, not an exception. Membership ADMISSION is separately gated: only a child whose tree root matches the room's tree root can join, and a LEFT member's restore grants nothing.)
**OPTIONS:** (a) Members ∪ owner-ancestry chain — the commissioning leadership chain sees its rooms, lateral cousins/uncles excluded; (b) Strict members ∪ owner only — tightest, but the layer that commissioned the work is blind to it; (c) Members ∪ owner ∪ named-invite — most flexible, adds an invite mechanism (over-build for v1).
**OUR RECOMMENDATION:** (a) — fail-closed, one upward walk; ownership-consistent: the room lives inside a tree, and the tree's leadership chain is its accountability chain.

### 6. Cost posture — strict caps now, or relaxed until abuse shows?

**QUESTION:** Ratify the strict cost constants: ≤16 members/room (active rows; LEFT rows free), ≤8 active rooms/tree, posts ≤4000 chars, ≤4 posts/member/turn, 8000 chars/turn total pushed room content (≤2 tagged pushes), digest ≤2000 chars, name-generation wait ≤3.0s?
**CONTEXT:** These enforce the milestone-level messaging doctrine structurally. All are env-tunable; none affect correctness. The trade-off dial is teamwork velocity vs context bloat — pushed room content permanently occupies each member's compacted context floor. **Naming-wait ratification line: the ruling says naming must never block spawn — the design reads that as "never block on naming FAILURE" (failure path is instant, deterministic fallback). The 3.0s is a bounded SUCCESS-path wait: the one place a spawn waits on naming, capped hard and env-tunable.**
**OPTIONS:** (a) Strict (the listed constants) — protects the compaction floor from day one, may occasionally pinch a verbose member into batching; (b) Relaxed (2× everything) — freer flow, higher permanent-context cost, revisit after abuse data; (c) No caps, guidelines only — cheapest, trusts the prompts, invites the exact bloat the pull-first stance guards against.
**OUR RECOMMENDATION:** (a) — start strict, loosen on evidence; loosening is a config change, tightening after sprawl is surgery.

### 7. Room observability — SSE surface in v1?

**QUESTION:** Surface rooms in the UI now via the existing instance-events SSE route (plus a ~3-line room-resolution fix), or defer all room UI to a later phase?
**CONTEXT:** The reuse claim ("zero new auth surfaces") held ONLY with a room-resolution pre-branch — the route currently 404s on any non-instance id. The stream is operator/FE-tier observability (lossy, at-most-once), never a delivery lane; members read via the room tool. Durable content-free audit rows (post/join/leave/tag-delivery/proposal) are recommended regardless.
**OPTIONS:** (a) v1: pre-branch + room-keyed SSE publish + audit EventKinds — humans watch the team work live, tiny change; (b) Audit rows only, SSE later — less surface, but the user's "this is how a real team works" is invisible to humans until later; (c) No room observability in v1.
**OUR RECOMMENDATION:** (a) — observability was part of the commission's spirit, the change is ~3 lines + additive event kinds, and it forces the audit lane to exist from day one.

---

## Adaptation disclosures (scope notes — not decision items)

1. **Warm reuse vs late join (v1 limitation):** v1 rooms are join-at-spawn-only — a pre-existing warm/completed instance CANNOT be adopted into a room later (late-join tool-regrant deferred). The house warm-lane preference is served differently: warm reuse INSIDE a room = revive/wake an EXISTING member (supported; the guidelines steer it), and warm agents can be reused for NEW spawns that join at spawn time.
2. **"System message" delivery shape:** the requirement wording says the room block is injected into the instance's SYSTEM MESSAGE; the substrate delivers it as a per-turn context-kind injection (`CONTEXT_KIND_JOINED_ROOM`, stable-id supersede) because the persona SystemMessage is cached per-AGENT (daemon/loader.py:972/:1356) and cannot carry per-instance room state. Visible effect equivalent (present every turn); zero prompt-baseline impact.
3. **"N unread injection" delivery shape:** the ruling words the todo-step nudge as a lightweight 'N unread' injection; the design carries it as a reminder string inside the todo tool's own result payload (the every-update reminder slot), which the model reads mid-turn. Effect-equivalent; no context-message injection is spent on it.
