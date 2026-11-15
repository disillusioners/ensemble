# Agent Group Chat — Phase-1 DESIGN · Plan Overview

**Doc-set Rev 6** (single alignment stamp; 2026-11-15 final fix pass). **Status:** Phase-1 DESIGN (planning-only). NO implementation, NO code changes, NO commits/pushes — a giter instance finalizes git at end of mission.
**Date:** 2026-11-15 · **Commission:** Emperor directive via Ari.
**Evidence base:** three investigation reports (gc-wanderer-msg, gc-wanderer-lifecycle, gc-wanderer-precedent), all anchors at repo tip `bf4bfde6e` — consolidated in **substrate-map.md** (§A–§J).
**Companion docs:** substrate-map.md · decisions.md (9 SETTLED; 6 OPEN **all resolved**, pointers per entry) · **architecture-recommendation.md (architect delivery, Rev 6)** · **emperor-open-questions.md (7 ratification items + 3 adaptation disclosures — the Phase-1 endpoint)**.
**Live state:** planner framing COMPLETE → architect delivery COMPLETE (C1, F-A/B/C, iterations 001–003 reconciled: capability row, join predicate, corrected post-graph chain, total 10-state lane map, owner cursor seed, one-hop proposals, pinned boot pair, two-conjunct restore) → **awaiting Emperor ratification**. Next dispatch after ratification: plan-creation cycle authoring `phaseN-plan.md` files.

---

## 1. Objectives

1. Make sibling collaboration first-class: parent opens a chat-room; agents work together ("This is how a real team work not just with their leader, they work together too." — Emperor).
2. Deliver the DESIGN DOC SET: requirements (verbatim-faithful), substrate map, decision log, risk register, architect recommendation, Emperor open-question list.
3. Frame in planner docs; architect answers (delivered); Emperor ratifies.

## 2. Emperor requirements (verbatim-faithful)

- When a parent needs a group of agents working together in real-time, it opens a chat-room.
- Agents created by the parent via create-instance can OPTIONALLY join a chatroom UUID (join param at spawn).
- When joined, a new tool + guidelines for working with the team (send milestone reports, changes to the chatroom via toolcall, ...) are injected into the instance's SYSTEM MESSAGE.
- Parent and children in the chatroom can always read the chatroom messages.
- Rationale: "This is how a real team work not just with their leader, they work together too."
- Related: warm/reusable lanes preferred over duplicates; handoffs via team messages; group chat = the general mechanism making sibling collaboration first-class.

## 3. SETTLED CONSTRAINTS — binding Emperor rulings (2026-11-15; record verbatim as constraints, NOT open questions)

1. **DELIVERY=HYBRID rhythm-aware:** (a) unread NUDGE at TODO-STEP boundaries (lightweight 'N unread' injection, no interruption of in-flight work); (a2) COMPLETION-BOUNDARY NUDGE — before the instance declares its turn/task done, last-chance 'check unread team messages' nudge; fires only when unread>0 at turn-completion; MUST carry a loop-guard (once-per-turn latch OR only-if-new-messages-since-last-room-read) so a busy room cannot block completion forever; tagged/blocking message at completion time = strong form, composes with parent-wake. (b) NAMED members + @TAGGING — TAGGED message = INJECTED MID-TURN immediately (importance escalation); untagged waits for nudges.
   *(Mechanism note — NOT ruling text: (a) rides the reminder slot EVERY todo update returns (todo_manager.py:403-461; `_compute_reminder` :1198-1276), filtered by the shared latch + watermark — not done-only; the drain wrapper is terminal-verdict-only (a2-side backstop; graph.py:4933 cap, :4955-5040); the (a2) chain is `language_check → room_completion_gate → attestation_gate → END`; the loop-guard's purpose is the clause above.)*
2. **RULING A:** tagging a COMPLETED agent == sibling task proposal → route to PARENT to decide (revive? reroute? answer directly?); completed agents are NEVER silently revived or messaged on their own.
3. **RULING B — naming MANDATORY when using chatrooms:** parent gives each instance its purpose NAME at spawn; instance KNOWS ITS OWN NAME (room name injected into its system message for @tag self-recognition); in-room addressing by NAME (UUID allowed but not recommended — soft rule). **FULL NAMING CASCADE (settled end-to-end):** (1) parent-provided purpose name (preferred/expected); (2) omitted → quick-model INLINE generation on the purpose-bound QUICK model (short prompt, purpose name only, e.g. 'capturer'/'log-analyzer'), name RETURNED IN SPAWN TOOLCALL RESULT (no silent naming); (3) quick-model FAILURE (error/timeout/unavailable) → deterministic fallback = agent-name + N (e.g. 'capture-2'), instant + predictable, no retry-loop, never blocks spawn, also returned in toolcall result; (4) any collision IN-ROOM → numeric suffix (capturer-1, capturer-2, …) — duplicates are FIRST-CLASS (same agent spawned multiple times for different task parts); uniqueness scoped PER-ROOM not global; applies to parent-provided AND auto-generated names; final suffixed name surfaced in toolcall result. *(KV-verbatim.)*
   *(Architect-detail note — NOT ruling text: exact suffix format + assignment mechanics = architect input; the ruling itself is settled.)*
4. **TAG-DELIVERY STATE MACHINE (explicit in architecture):** (a) RUNNING → mid-turn injection; (b) IDLE-but-live → wake/queued delivery; (c) COMPLETED/terminal → parent as task proposal (Ruling A).
5. **CONTEXT:** system message = guidelines + short digest ONLY; full history behind read tool.
6. **TASKING:** PARENT-DECIDED — siblings inform/discuss/propose; task creation injects/wakes parent; never auto-execute.

Full substrate citations per ruling: decisions.md D-S1..D-S6.

## 4. House constraints (binding on any design)

- **v0.27.14-class event-loop discipline:** off-loop message-path DB I/O (`asyncio.to_thread`).
- **message_queue boot-recovery machinery** (F-1 wipe + epoch reset): room persistence defines its boot story explicitly.
- **Prompt-artifact baseline discipline:** agents/*.md runtime-immutable; dynamic context-kind injection only.
- **Migration doctrine:** own `.sql` per table (130 files at tip; multi-table-single-file precedented).
- **Do NOT over-build:** the simpler mechanism that makes the doctrine true.

## 5. Phase-1 scope & non-goals

**Scope — DELIVERED 5-file set:** `.agents/shared/planning/group-chat/` in worktree `/home/nea/ensemble-src-wt-group-chat-design` — plan-overview.md · substrate-map.md · decisions.md · architecture-recommendation.md (Rev 6) · emperor-open-questions.md (7 items + 3 disclosures).

**Non-goals:** NO implementation · NO code changes · NO commits/pushes · NO agents/*.md edits · NO re-litigating settled rulings · NO implementation-instance spawns.

## 6. Phase-structure for Phase-2+ implementation

Architect regroup in architecture-recommendation.md §13. Proper `phaseN-plan.md` files post-ratification. Order: **P1 → P2 → P3 → {P4, P5} → P6**.

| Phase | Name | Core content (evidence-cited → map §) |
|---|---|---|
| **P1** | Substrate & persistence core | 3 tables + cursors + one .sql (additive; drop-rollback) · per-room write lock · two cursors + re-derivation helper (`seq > max(last_read, last_delivered)`) · boot regeneration pass (**pinned pair: members incl. owner, active+closed, never archived; NO parent-proposal routing**) + assertion pin + **10-state totality pin** · lifecycle columns at birth · **`room_*` EventKind registry additions** · off-loop idiom on P1-scope paths |
| **P2** | Membership, spawn & naming | join param → membership row + JSONB in one tx · **`_validate_room_join` (tree-root equality; cap counts ACTIVE rows only — owner counts, LEFT rows free; room-count cap) + typed fail-closed exit pins** · full 4-tier cascade (quick field T=3.0s; agent-name+N; suffix-at-registration; **owner member_name via the same registry**) · **restore per the D5 two-conjunct rule (`room.state ∈ {active, closed} ∧ member.state = active`) + left-member-restore pin** · roster resolution |
| **P3** | Delivery & injection | router per the **TOTAL 10-state map** (`waiting` → enqueue-family; CAS set = {idle, queued, waiting, waiting_children} derived from the enum) + rowcount-0 total fallback + enum-totality pin · Ruling-A branch + **one-hop rule (terminal parent = durable pending proposal, never revive/recursion)** + Y14 cursor rule + proposal dedup · room allow-clause fail-closed + pin · @tag resolution · sequential fan-out + caps · closed/archived write refusals |
| **P4** | Nudge hooks | (a) every-update todo reminder (never done-only) · `room_completion_gate` — chain `language_check → room_completion_gate → attestation_gate → END`; disabled-language seam; own path map with `"agent"` key · demoted drain backstop · manager-level shared latch registry · strong-form composition + **continuation-cap interaction test (≤1 reminder + ≤1 tag vs INJECTION_CONTINUATION_MAX=8)** · wiring-order verification |
| **P5** | Room tools + guidelines injection | **privileged category #23 mint pinned here** (14-file pin-test budget; count 22→23) · room_create (**seeds the OWNER membership row — cursors, unread, digest**)/post/read tools (grant pinned) · **parent mid-life room-tool availability stated: the CREATOR holds the category from spawn via the static allow-list + in-body gate — no mid-life mutation needed, so P2/P3 row references are ordering-safe** · `CONTEXT_KIND_JOINED_ROOM` builder (async day one; roster incl. owner; 14 kinds verified) · guidelines constant (warm steer) · push composer + 8000/turn budget |
| **P6** | UI/audit + room GC | SSE pre-branch + LiveEventHub + FE · **GC sweeper (whole-room; capability row; P-idle liveness counts `waiting` alive; P-close 14d accepted-loss — accepted loss EXACTLY `archived`; dry-run first) + archive tables migration** |

## 7. Risk register

| ID | Risk | Evidence | Sev | Mitigation |
|----|------|----------|-----|------------|
| RK-1 | Anchor drift | §J | Med | Re-verify at Phase-2 plan time (13 drifted cites corrected across Rev 5–6) |
| RK-2 | Inherited fail-open gate defect (instance.py:4723) | §C | High | Room gate fails closed; pin + counter-mutation |
| RK-3 | Push-bloat + speaker collapse | compaction.py:337/:1404 | High | Pull-first; digest-only; author denormalized |
| RK-4 | Boot-recovery mismatch | manager.py:1407-1600 → §B | High | Outside blast radius + assertion pin + pinned pair (members incl. owner; no proposal routing) |
| RK-5 | Unbounded growth | repository.py:1187 → §B | Med | Whole-room GC + accepted-loss row; room-count cap; `waiting` alive in liveness |
| RK-6 | Tagged volatility | manager.py:4907, :7120-7160 → §A | Med | Log+cursor re-derivation; **owner cursors (D2)**; Y1 predicate; Y14 |
| RK-7 | Tool-mint invisibility | _tool_registry.py:1240/1431/1467 → §D | Med | Mint checklist; category #23 pinned to P5; 14-file budget |
| RK-8 | Baseline/seal breach | prompt_artifacts.py:119; filesystem.py:1561/:1978 → §E | Med | Context-kind only |
| RK-9 | Authz scope creep | _auth.py:90-256 → §C | Med | Room clause + join predicate + **D5 two-conjunct restore**; ACL review |
| RK-10 | Nudge-state durability | todo_manager.py:12-15 → §F | Med | Durable cursors P1 (incl. owner); RAM latch registry |
| RK-11 | Over-build | §J | Med | Simpler alternative justified per decision |
| RK-12 | Fan-out/ordering gaps | models.py:56-128; work_notifier.py:1339 → §B/§I | Med | Per-room lock + seq + cursor (P1) |

**Register index (note 3):** plan-overview carries RK-1..RK-12 (this table); architecture §11 carries RK-1..RK-11 **plus RK-13** (single-daemon write-path + naming latency) and covers RK-12's response in its §9 — the two registers are cross-referenced, not duplicated.

**Top risks:** RK-2, RK-3, RK-4, RK-6, RK-9.

## 8. Success criteria — design phase

1. **Emperor-ruling coverage:** all 6 rulings (+ additions) SETTLED; verbatim blocks verified against the KV source.
2. **Anchor-grounded:** all report anchors preserved in substrate-map.md; drifts caught and corrected.
3. **Open questions distilled:** 6 framed, resolved, presented for ratification.
4. **Zero baseline/seal impact demonstrable.**
5. **Migration doctrine honored** (own .sql; additive; drop-rollback).
6. **Planning-only respected.**
7. **Phase sketch** regrouped by the architect with explicit dependencies.

## 9. Document map

- `substrate-map.md` — consolidated evidence §A–§J at bf4bfde6e (+ correction provenance), [R]/[A]/[M] tags, MISSING summary (12 items).
- `decisions.md` — 9 SETTLED; 6 OPEN all RESOLVED with pointers + ratification items; D1–D5 design rules recorded.
- `architecture-recommendation.md` — architect delivery of record (Rev 6): substrate verdict; dimensions B–J incl. the capability row, join predicate, total 10-state lane map, owner cursor seed, one-hop proposals, pinned boot pair, two-conjunct restore; RK responses; ledger 17R/11A/14M; Phase-2+ shape.
- `emperor-open-questions.md` — Phase-1 endpoint: 7 ratification items + 3 adaptation disclosures.
- `plan-overview.md` — this document.
