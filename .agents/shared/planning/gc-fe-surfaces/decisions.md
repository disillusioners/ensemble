# DECISIONS — Group-Chat FE Fast-Follow Surfaces (`gc-fe-surfaces`)

Lane: `gc-fe-surfaces` · task id `gc-fe-20261117`
Branch: `feature/gc-fe-surfaces` · base `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (v0.31.0)
Worktree: `/home/nea/ensemble-src-wt-gc-fe-surfaces`
Commission: Emperor, Discord 2026-11-17 (verbatim in `spec.md`)
Author: worker via technical-analysis skill · Status: **DRAFT for architect adjudication → reviewer council → fresh approver**

---

## How to read this ledger

Every decision below carries three parts:

- **RULING** — bold, unambiguous, implementable as written.
- **EVIDENCE** — `file:line` citations, verified by spot-read in the worktree at the base SHA above. A citation is a claim about the CURRENT tree, not about a stale note.
- **RATIONALE** — why this ruling and not the alternative; the alternative is named so a reviewer can re-rule it.

This ledger is the ONLY place interpretation lives. `spec.md` is verbatim-faithful by construction.

**Citation convention:** `path:line` (or `path:start-end`) points into the worktree at base `0181dadbf`. Line numbers were verified by direct read; where a dispatch-supplied anchor was off, the corrected anchor is used and the discrepancy is called out inline.

---

## D1. READ-API POSTURE + NEW-ROUTE DISCLOSURE

*(constraint 1 — "reuse the agents' room-read API predicate (members ∪ owner-ancestry, FAIL-CLOSED) … no leaking room titles for rooms the principal cannot read … READ-ONLY, FAIL-CLOSED addition with NO new auth surface … same repository-layer predicate function — shared code, never duplicated logic — and the report must disclose it")*

### RULING

**D1.1 — ROUTE SHAPE: Candidate A.** Adopt **ONE** new read-only route — `GET /api/rooms` (nest-wide chatroom list) — and **REUSE** the existing `GET /api/rooms/{room_id}/messages` for the detail pane. Candidate B (`GET /api/instances/{id}/rooms/joined`) is **REJECTED**.

Route contract (the wire shape is BINDING — the frontend codes against it):

```
GET /api/rooms[?instance_id=<scope-anchor>][&limit=<int>]
  → 200 {"rooms": [{
        "room_id":            str,
        "name":               str,
        "tree_root_id":       str,
        "owner_instance_id":  str,
        "state":              "active" | "closed",
        "member_count":       int,
        "member_instance_ids":[str, ...],     # ACTIVE roster
        "last_message_at":    iso|null,
        "last_message_seq":   int|null,
        "created_at":         iso
      }],
      "truncated":           bool}
```

**D1.1-a — THE LIST IS BOUNDED (AM-4; the contract is BINDING, so the bound lands with the route).** `limit` is an OPTIONAL query param: **default 200**, **server-clamped to a hard maximum of 500** (`limit = max(1, min(limit, 500))` — the house `MAX_PAGE_LIMIT` clamp pattern, `daemon/routers/instances.py:486`); the **server** clamps, a client value is advisory. The envelope carries **`"truncated": bool`** — `true` iff the server's clamp cut rows (more rooms exist than were returned), `false` on a complete page — mirroring `InstanceListResponse.truncated` (`daemon/models/instance.py:139-149`). **Ordering is unchanged**: `(created_at, room_id)`; the bound never reorders. Rationale for binding it NOW: purge + closed-room GC bound the resident set only partly (closed rooms purge at closed+14 d, `daemon/services/room_gc.py:89-90`; active rooms cap at 8/tree, `daemon/repositories/room/membership.py:126`) but growth remains **unbounded in tree count**, and the payload scales as rooms × roster; retrofitting a bound later is a **contract change**, which the binding-contract rule (`daemon/routers/rooms.py:11-30`) forbids. The FE normalizer **tolerates the extra envelope field** (the house "extra fields are tolerated silently" invariant, `frontend/src/app/models/room.model.ts:20-27`).

`GET /api/rooms/{room_id}/messages?after_seq=&limit=` is **UNCHANGED** — it already serves the detail pane and is not touched by this lane.

**D1.2 — SCOPE + AUTH POSTURE.** `scope_required("instances:read")` — the **existing** scope constant, the **existing** `AuthMiddleware`, the **existing** `api_router` (`APIRouter(prefix="/api")`). **NO new scope, NO new auth model, NO new lane, NO new middleware.**

**D1.3 — MEMBERSHIP IS CARRIED IN THE ROW.** `member_instance_ids` = the **ACTIVE** roster, via the **existing** batched repository helper `RoomRepository.list_active_member_instance_ids` (`daemon/repositories/room/repository.py:1327-1341`). The FE derives **both** (b) the per-instance JOINED-room list and (c) the conditional-visibility gate from **this one response** — **zero N+1, ONE backend addition**.

**D1.4 — PREDICATE POSTURE (the fail-closed rule).** The route is **scope-anchored, never naked**:

- **Anchor PRESENT** (`?instance_id=X`): **EVERY** row is predicate-checked against X through the shared predicate core; a room X cannot read is **DROPPED from the list** (fail-closed — never rendered with a redacted title, never rendered at all).
- **Anchor ABSENT**: the route answers the **operator-lane nest-wide list**. Its breadth is **exactly** the operator lane's *existing* message-read breadth — see D1.6. **What is genuinely NEW in this route is the ACTIVE member roster** (`member_instance_ids` — the nest-wide who-chats-with-whom relation; see D1.6/D1.11): room ids, names, state, timestamps and `member_count` were **already** discoverable by the same principal through `GET /api/instances` → `GET /api/instances/{id}/rooms` (`daemon/routers/rooms.py:194-206`).

**D1.5 — SHARED PREDICATE, NEVER DUPLICATED.** The predicate **logic** is implemented **once**. The per-room path (`_require_room_access`, `repository.py:740-772`) and the list path call the **same** core. Concretely: extract the predicate's decision core out of `_require_room_access` into a shared helper that takes `(room, caller_is_active_member, caller_ancestor_set)`, and have BOTH the existing per-room check and the new list check call it. The list path pre-computes the caller's **DESCENDANT closure** **once** — `InstanceRepository.get_tree_ids_permanent(caller_id)` (`daemon/repositories/instance/repository.py:838-903`), a **caller-anchored DOWNWARD** walk over `instances.parent_id` (children by `Instance.parent_id == current_id`, `:869-880`; it never climbs — the `root_id` parameter name is a traversal-start label, NOT an upward-root resolver; the upward resolver is `get_tree_root_id`, `:769-787`) — and the caller's ACTIVE membership room-id set **once** (one batched query), then evaluates the shared core per room in O(1) each.

> → **AMENDED (architect gate 1 — Q5 ACCEPTED, AM-13):** the closure form is **technically sound** and preserves D1.5's substance (one decision rule, implemented once, two application sites, O(1) per room). **Coupling + query shape (AM-13):** the room repository reaches the instance repository over the **manager's single engine** — ONE `InstanceRepository` instance constructed from that engine (no second engine, no cross-session state); `get_tree_ids_permanent` is **O(subtree) SELECTs — one call, N queries** — still ≪ the `N_rooms × depth` per-room walks it replaces. **Implementation hygiene (AM-13):** pass the caller id **positionally** (or via a named local such as `caller_id`), so no reader re-imports the `root_id` intuition. Depth caps are symmetric (up-walk ≤256, `room/repository.py:134`/`:727-737`; down-walk ≤256 BFS levels, `instance/repository.py:50`/`:869-880`) and cycle behaviour agrees.

> → **AMENDED (AM-10 — the ONE narrowing edge, disclosed):** the closure form **DENIES** a caller whose **intermediate ancestor row was hard-deleted** (instance rows can be hard-deleted — `instance/repository.py:3306`/`:3580`/`:3709`): the shipped up-walk still admits that caller id (it reads `parent_id` from the child's row and then breaks, `room/repository.py:725-737`), while the closure returns `[]` and denies. **Direction: fail-closed, NEVER wider.** It is **unreachable via HTTP** — the router 404s a missing anchor (`daemon/routers/rooms.py:139-144`; `manager.get_instance` raises `KeyError`, `daemon/manager.py:16336-16351`). Pinned by one spec leg (P1 task 5) and disclosed in the final report. **A parallel re-implementation of "members ∪ owner-ancestry" anywhere in this lane is a defect** — it is the exact failure mode constraint 1 names.

**D1.6 — THE BREADTH, STATED HONESTLY — AND THE NEW-INFORMATION CLASS (AM-1).** Today the shipped messages route resolves its **default** scope to the **room's own OWNER** (`daemon/routers/rooms.py:271-277` — `scope = str(room.get("owner_instance_id") or "")`), and the owner is by construction inside its own owner-ancestry chain (`repository.py:717-738`). Therefore **an `instances:read` caller can already read ANY non-purged room's messages by id, cross-tree** — this is the report-only spot-check at `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63` ("the message route's default scope anchor is the ROOM OWNER → an `instances:read` caller can read any room by id across trees … Report-only").

> → **AMENDED (architect gate 1 — AM-1, 🔴 blocker): BOTH directions corrected.** The earlier wording ("the route adds **DISCOVERABILITY** of room ids and titles") **OVER-STATED** the delta: room ids, names, state, timestamps and `member_count` were **already** discoverable by the same principal — `GET /api/instances` (`instances:read`, paginated) enumerates the instance universe and `GET /api/instances/{id}/rooms` (`daemon/routers/rooms.py:122-207`) returns every tree's rooms (`:194-206`). And the earlier wording **UNDER-STATED** it by naming no new class at all: the **genuinely NEW information class is `member_instance_ids` — the ACTIVE member roster, i.e. a nest-wide "who chats with whom" relational graph**. **No HTTP surface exposes it today**: the tree route returns `member_count` only (`rooms.py:194-206`); the messages payload exposes only per-message **authors** (`rooms.py:314-327` — a silent member is invisible, a LEFT member absent); `list_active_member_instance_ids` (`room/repository.py:1327-1341`) has **no HTTP caller** (GC sweeper + tests only); rosters are exposed only on the **agent tool lane** (participant-gated). The roster is **strictly less sensitive than the message content the same principal can already read**, and it is **load-bearing** for surfaces (b)/(c) (D1.3/D3.5) — but it **IS** a new disclosure class and is named as such. **Roster minimization is NOT recommended** (the one-response design of (b)+(c) depends on it); the correction is to the **disclosure**, not to the row shape.

> → **AMENDED (AM-6 — citation status corrected):** the SSE room-resolution pre-branch finding referenced below is **CLOSED at this lane's base** (A-P6-26, option b — `5d13a1c6c` + `652203f95`, both ancestors of `0181dadbf`; the room-channel registry is `daemon/services/live_event_hub.py:41-56`). It is **not** the open item behind this breadth: the **leader-routed follow-up is the messages route's DEFAULT-SCOPE breadth** (the very paragraph above), which the leader ruling of 2026-11-17 (Q2) routed as a **disclosed pre-existing follow-up** — this lane does **not** touch the messages route.

> **Citation-label correction (record it — UPDATED by AM-6):** the dispatch cited this as "F-3" at the P6 gate `:63`. The `:63` spot-check is the **operator-lane authz breadth** note (correct substance). The file's **own** `F-3` label at `:72` is a **DIFFERENT** finding — the SSE room-resolution pre-branch predicate asymmetry (`daemon/routers/messages.py:984-1049`). **Status correction (AM-6):** that SSE pre-branch finding is **CLOSED at this lane's base** (A-P6-26, option b — `5d13a1c6c` + `652203f95` ⊆ `0181dadbf`; the room-channel registry is `daemon/services/live_event_hub.py:41-56`), so the P6 file's `:83` "disposition requested" line is **stale** at this base. The label collision stands, and the two items stay separate: this ledger refers to the `:63` item as **"the operator-lane breadth spot-check"** (the **leader-routed** messages-route default-scope follow-up) and to `:72` as **"the F-3 SSE pre-branch finding"** (**closed at base**).

**D1.7 — THE COUPLING (leader-significant; carried to the Open Questions).** The anchor-less mode of the new list is safe **today only because** of the operator-lane breadth above. **If the leader rules the F-3 SSE pre-branch finding to option (c) — mirror the read predicate — and the same reasoning is applied to tighten the messages route, then the anchor-less list becomes a NEW title leak** and must be tightened in the same change, or the FE must be required to pass an anchor. Therefore: **the new list route is implemented so that switching its default from "operator lane" to "anchor-only" is a ONE-PARAMETER change, not a redesign**, and the F-3 disposition is recommended to be settled **before** the route is cut.

> → RESOLVED by leader ruling 2026-11-17 (Q2): **PROCEED** with `GET /api/rooms` exactly as planned (every row predicate-checked fail-closed via the shared `_require_room_access`); the `_ALLOW_ANCHORLESS_GLOBAL_LIST` knob ships **IN THE SAME COMMIT** as the route; the messages-route scope tightening is routed as a **disclosed pre-existing follow-up** (owner: leader-tracked queue) and this lane does **NOT** touch the messages route's default scope. See **LEADER RULINGS (2026-11-17, post-planning)**.

**D1.8 — ERROR VOCABULARY (closed; imitates `rooms.py` exactly).**

| Code | `error` | Trigger |
|---|---|---|
| 422 | (FastAPI validation) | malformed query params |
| 401 | (AuthMiddleware) | unauthenticated — raised by the existing middleware, not by this route |
| 403 | `SCOPE_INSUFFICIENT` | `instances:read` not granted |
| 404 | `instance_not_found` | the `?instance_id=` anchor names a missing instance, **or** the anchor's tree/scope could not be resolved (fail-closed — the same shape the tree route uses at `rooms.py:157-174`) |
| 404 | `instance_not_found` | **the anchor-less mode is switched OFF** (`_ALLOW_ANCHORLESS_GLOBAL_LIST = False`) — the refusal is the house "unresolvable scope" 404 (the exact precedent of the tree route's scope 404, `rooms.py:157-174`). **Empty-200 is BANNED** (indistinguishable from "this nest has no rooms" — it would break FE honesty) and **403 `SCOPE_INSUFFICIENT` is BANNED** (misattributes: the token DOES hold `instances:read`) |
| 404 | `room_not_found` | a faulted room **resolution** (never distinguishes "broken" from "absent" on the wire) |
| 503 | `room_service_unavailable` | the room service or the instance repository is absent, or a faulted **LIST** read (present-but-faulted ⇒ retryable) |

**AMENDED (AM-3):** the knob-OFF refusal is **404 `instance_not_found`** — inside the closed D1.8 vocabulary, no new code, no new body shape; **empty-200 and 403 are BANNED** (both misrepresent the situation). **FE degraded posture (AM-3, pinned):** the global dialog shows its **error state + Retry** (the honest-error lane, `frontend/src/app/components/room-dialog/room-dialog.component.html:28-36`); the instances-list row button is **HIDDEN** and the surface shows the **honest error** (D3.5's fail-closed-on-the-button / honest-on-the-surface split). Body shape is the house `{detail: {error, message}}` envelope (`rooms.py:103-107`). **Infra faults stay INSIDE this vocabulary** — a faulting call is wrapped into the typed envelope, never allowed to escape to the global 500 handler (whose `{code: INTERNAL_ERROR, message: str(exc)}` body has the wrong shape AND leaks raw fault text — the A-P6-25/M3 rationale at `rooms.py:56-68`). Fault detail goes to the server log, never into a response body.

**D1.9 — REUSE POINTS (the no-duplication map).**

| Need | Reuse | Anchor |
|---|---|---|
| per-room read (detail pane) | `RoomChatService.read_messages` → `RoomRepository.list_messages` → `_require_room_access` | `service.py:464-479`; `repository.py:973-1005`, `:990`; `rooms.py:282-287` |
| active member counts | `RoomRepository._active_member_counts` (batched, `IN`-expanded) | `repository.py:1274-1292` |
| last-message sequence | `RoomRepository._last_message_seqs` (batched `MAX(seq)`) | `repository.py:1294-1309` |
| ACTIVE roster per room | `RoomRepository.list_active_member_instance_ids` | `repository.py:1327-1341` |
| ancestor chain | `RoomRepository._owner_ancestry_chain` (+ `_ANCESTRY_WALK_DEPTH = 256`) | `repository.py:717-738`, `:134` |
| room rows | NEW sibling of `list_rooms_for_tree` — `list_all_rooms_with_extras()` (ONE session, batched extras, **no N+1**) | modelled on `repository.py:1233-1272` |
| tree-scoped list (existing route, UNTOUCHED) | `RoomRepository.list_rooms_for_tree` | `repository.py:1233-1272`; `service.py:491-496` |
| service pass-through | NEW `RoomChatService.list_all_rooms()` (off-loop `asyncio.to_thread`, mirroring `list_rooms_for_tree`) | `service.py:491-496` |

**Purge semantics (R2):** a purged room has **no row at all** (purge DELETEs the `chatrooms` row — `models.py:33`, "NO `ondelete=` clause" family, `models.py:193-270`), so a plain `SELECT` over `chatrooms` **naturally excludes** purged rooms. The nest-wide list therefore lists `active` + `closed` and never a purged room — no tombstone, no special filter needed. `ChatRoom.state` is exactly `active | closed` (`models.py:96`).

**D1.10 — THE (b)-SEMANTICS NUANCE (tree-scope vs JOINED).**

| Surface | Scope | Ruling |
|---|---|---|
| per-instance popup **(b)** — row button | **MEMBERSHIP** (rooms the instance JOINED) | **Commission rules** ⇒ membership-based. Source: `member_instance_ids` on the global-list rows. |
| conditional visibility **(c)** | **MEMBERSHIP** (≥1 joined room) | **Commission rules** ⇒ membership-based. |
| **shipped** v0.31.0 header-icon modal | **TREE** (`chatrooms.tree_root_id`) | **UNTOUCHED** — no-regression duty (constraint 4). Its list source stays `GET /api/instances/{id}/rooms`. |

The tree-scope vs JOINED delta is a **named delta** (see D5) and the in-repo records that say "rooms created by that instance('s tree)" (`group-chat/decisions.md:122-124` D-S13; `group-chat/architecture-recommendation.md:273-277` §13.1) govern the **shipped** modal only — **not** the new row-button surfaces.

> → **AMENDED (AM-15 — freshness asymmetry, disclosed):** the **row popup (b) polls at 10 s** (D2), while the **same instance's shipped `instance`-mode modal never refreshes** (timer-free by the architect-confirmed Q4 ruling; it re-fetches only on open and on an explicit Retry). The asymmetry is **deliberate** — the shipped mode's byte-identity is the constraint-4 duty — and it is **disclosed** in the final report rather than silently shipped. A second asymmetry: the row popup's list source is **ANCHORED** (`GET /api/rooms?instance_id=<row id>`, AM-9) while the visibility gate's map comes from the **anchor-less** list (D3.5) — both come from the same route, and anchored rows are a **superset** of the instance's joined rooms, so the FE membership filter is unchanged.

**D1.11 — THE DISCLOSURE PARAGRAPH (lane-authored prose; CORRECTED per AM-1)** *(this exact text is what the final report must carry under constraint 1)*:

> **Fold note (architect gate 1, AM-1):** D1.11 is **lane-authored disclosure prose** — only `spec.md` is verbatim-faithful — so it is **edited directly** here. Constraint 1 requires the disclosure to be **truthful in BOTH directions**: the earlier text over-stated the delta (calling room ids/titles "new" when they were already enumerable) **and** under-stated it (never naming the new class — the ACTIVE roster). The paragraph below is the corrected artifact; the **old** wording is superseded, not deleted from the record (see D1.6's AM-1 note).

> **DISCLOSURE (constraint 1) — NEW READ-ONLY HTTP SURFACE, CORRECTED (AM-1).** This lane adds **ONE** new read-only HTTP route, `GET /api/rooms` (nest-wide chatroom list), to `daemon/routers/rooms.py`. It reuses the **EXISTING** `instances:read` scope (`daemon/routers/rooms.py:95`) and the **EXISTING** `AuthMiddleware` — **NO new scope, NO new auth model, NO new lane** (`daemon/routers/rooms.py:124`, `:212`; `daemon/api.py:5898`, `:6152`). Its rows are built by the **SAME repository layer** the agents' `room_read` tool rides (`daemon/tools/room.py:824`, `:862` → `daemon/repositories/room/service.py:464-479` → `daemon/repositories/room/repository.py:973-1005`, `:990`): the ACTIVE roster comes from the **existing** batched helper `RoomRepository.list_active_member_instance_ids` (`repository.py:1327-1341`), the member counts and last-message sequences from the **existing** batched helpers (`repository.py:1274-1292`, `:1294-1309`), and the **READ PREDICATE** is the **SAME** fail-closed predicate the agent tool uses — `RoomRepository._require_room_access` (`repository.py:740-772`) with its owner-ancestry chain (`_owner_ancestry_chain`, `repository.py:717-738`) walking `instances.parent_id` under the depth cap `_ANCESTRY_WALK_DEPTH = 256` (`repository.py:134`). The predicate's **logic is implemented once** and applied at two sites (the per-room read path and the list path) — **no parallel re-implementation**. When the caller supplies the optional `?instance_id=` scope anchor, **every** row is predicate-checked against that instance and a room the instance cannot read is **dropped from the list** (fail-closed). When the anchor is **absent**, the route answers the **operator-lane** nest-wide list, whose breadth **equals the operator lane's EXISTING message-read breadth**: the shipped `GET /api/rooms/{room_id}/messages` resolves its default scope to the room's **OWNER** (`daemon/routers/rooms.py:271-277`), which is always inside the owner-ancestry chain — so an `instances:read` caller can **already** read any non-purged room's messages by id (report-only spot-check: `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63`). **WHAT IS NEW — stated precisely, in BOTH directions (AM-1):** room **ids, names, state, timestamps and `member_count` were ALREADY discoverable** by the same principal through `GET /api/instances` → `GET /api/instances/{id}/rooms` (`daemon/routers/rooms.py:122-207`, `:194-206`); the **genuinely new information class is `member_instance_ids` — the ACTIVE member roster, i.e. a nest-wide "who chats with whom" relational graph** — which **no HTTP surface exposes today** (the tree route returns `member_count` only; the messages payload exposes only per-message **authors**, so a silent member is invisible and a LEFT member absent — `rooms.py:314-327`; `list_active_member_instance_ids` has no HTTP caller; rosters exist only on the participant-gated agent tool lane). The roster is **strictly less sensitive than the message content the same principal can already read**, and it is **load-bearing** for the per-instance joined-room popup (b) and the conditional-visibility gate (c) — but it **IS** a new disclosure class and is named as such. The list is **bounded** (`limit` default 200, server-clamped max 500, `truncated` envelope flag — AM-4) and its ordering is unchanged (`created_at`, `room_id`). The anchor-less mode is gated by `_ALLOW_ANCHORLESS_GLOBAL_LIST` (default `True`, shipped in the **same commit** as the route); when that knob is OFF the route answers **404 `instance_not_found`** (AM-3) — never an empty 200, never a 403. The **messages-route default-scope tightening** remains a **disclosed pre-existing follow-up** (leader ruling 2026-11-17, Q2 — owner: leader-tracked queue); this lane does **not** touch the messages route. Should that follow-up land, switching this route's default to anchor-only is a **one-parameter** change, not a redesign.

### EVIDENCE

- **The fail-closed read predicate** — `RoomRepository._require_room_access`: `daemon/repositories/room/repository.py:740-772`. Resolution order: room row exists → ACTIVE membership row → owner-ancestry chain; a LEFT membership row satisfies neither; all-miss ⇒ typed `RoomNotFoundError`.
- **Owner-ancestry chain** — `daemon/repositories/room/repository.py:717-738`; walk bound `_ANCESTRY_WALK_DEPTH = 256` at `daemon/repositories/room/repository.py:134`; cycle-safe (a repeated parent breaks the walk).
- **The predicate is ALREADY shared by both surfaces** — the agent tool (`daemon/tools/room.py:824` `room_read` → `:862` `room_service.read_messages`) and the FE route (`daemon/routers/rooms.py:282-287`) both land on `RoomRepository.list_messages` → `_require_room_access` (`repository.py:990`). **One predicate, two callers.**
- **Existing FE surface = exactly 2 read-only GETs** — `daemon/routers/rooms.py:122-207` (`GET /api/instances/{instance_id}/rooms`) and `:210-327` (`GET /api/rooms/{room_id}/messages`); the module docstring declares the wire contract BINDING at `rooms.py:11-30`. The tree route is tree-scoped via `chatrooms.tree_root_id` (`repository.py:1233-1272`) and returns rows `{room_id, name, created_at, closed, member_count, last_message_at, last_message_seq}` — **NO member ids** (`rooms.py:194-206`).
- **Operator-lane breadth** — `daemon/routers/rooms.py:271-277` (owner-anchored default) + `daemon/routers/rooms.py:56-68` (infra-fault wrap rationale) + `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63`.
- **The F-3 SSE pre-branch finding is a SEPARATE item** — `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:72` (disposition requested at `:83`), on `daemon/routers/messages.py:984-1049`.
- **Repository functions available but NOT HTTP-exposed** — `list_member_rooms` (`repository.py:686-713`; the subject of the query IS the caller — its own membership rows, so nothing to authorize) and `list_active_member_instance_ids` (`repository.py:1327-1341`).
- **Scope machinery** — `scope_required` is the FastAPI dependency form of the house authorization layer; it reads `request.state.auth_context`, is **inert when auth is OFF**, and is **fail-closed when auth is ON** (`403 SCOPE_INSUFFICIENT`, never 404) — `daemon/auth/decorators.py:1-56`.
- **Router mounting** — `daemon/api.py:119` (import), `:5898` (`APIRouter(prefix="/api")`), `:6152` (`include_router(rooms_router)`).
- **Purge = literal row deletion** — `daemon/repositories/room/models.py:193-270` (the `chatrooms` table, `state` exactly `active | closed` at `:96`), `:33` (no `ondelete=` on the chatroom FKs ⇒ purge DELETEs the rows).

### RATIONALE

**Why Candidate A over Candidate B.** Candidate B (`GET /api/instances/{id}/rooms/joined` over `list_member_rooms`) matches "joined" semantics **server-side**, which is the one thing it buys. But it **leaves commission item (a) — the GLOBAL browser — entirely unserved**, so a second global route would be needed anyway: **strictly more backend surface for identical frontend needs.** Candidate A serves all three items from **one** response — (a) the global browser reads it directly; (b) the per-instance popup filters it by `member_instance_ids`; (c) the visibility gate is a pure function of the same map — with **one** backend addition and **zero N+1**. The "joined" semantics are **not lost** by carrying them in the row: membership is an explicit, typed, ACTIVE-only field, and the frontend filter is a one-line membership test against a server-computed roster. **A membership filter in the FE is not a predicate widening** — the FE never decides readability; it decides *which of the server-listed rooms to show*, and the server's row list is itself predicate-bounded (D1.4).

**Why the anchor is optional rather than required.** The commission asks for a **whole-nest** global view (a). A nest-wide view has **no** per-instance scope to anchor on — the session operator is not an instance. Forcing an anchor would make the global browser impossible. Forcing the operator lane alone, with no disclosure, would be the silent widening constraint 1 forbids. Hence: **anchor optional, anchor semantics = the predicate, no-anchor semantics = the operator lane's already-existing message breadth, and the whole thing DISCLOSED verbatim.**

**Why "the operator lane's existing breadth" is an acceptable anchor point.** Under the current semantics, the operator lane can already read every non-purged room's messages (`rooms.py:271-277`). A nest-wide title list therefore discloses titles for rooms whose **messages the same principal can already read** — it makes ids **discoverable** rather than guessing-guarded. That satisfies constraint 1's substance ("no leaking room titles for rooms the principal cannot read") **on today's semantics**. It does NOT satisfy it if the leader tightens the messages route — which is exactly why D1.7 records the coupling and routes it to the Open Questions rather than burying it.

**Why the predicate core must be refactored rather than re-implemented.** Constraint 1 says "same repository-layer predicate function — shared code, never duplicated logic". A list-shaped predicate that re-derives "members ∪ owner-ancestry" in the router or in a new repository method would be a **second implementation** — exactly what constraint 1 forbids, and exactly the drift class the house P11 guards exist to catch. One core, two application sites, is the only shape that satisfies the letter and the spirit.

**Why the 503/404 split is copied rather than reinvented.** The shipped routes already encode the M3 adjudication (fault ⇒ typed envelope, never a raw 500 escape that leaks `str(exc)`); the new route inherits that vocabulary verbatim so the frontend's error handling has **one** shape to implement.

---

## D2. SSE-VS-POLLING RULING

*(constraint 3 — "prefer the existing SSE/events surface … IF cheap; else the house polling pattern (bounded interval, supersede tokens, stop on tab-hide) used by the metrics tabs. The plan must make this decision ON EVIDENCE … and record choice + rationale")*

### RULING

**D2.1 — MECHANISM: BOUNDED POLLING.** Rejected: SSE for message freshness. Chosen: the house polling pattern — **fetch-on-open + bounded interval while a room surface is OPEN + manual retry + tab-hide stop**. The **room lifecycle** SSE channel is **explicitly DEFERRED**, not rejected forever (D2.6).

> → RESOLVED by leader ruling 2026-11-17 (Q4): the shipped `instance` mode stays **TIMER-FREE** — constraint 4 (stability, byte-behaviour-identical shipped mode) outranks D2.1's breadth; polling is enabled for the **new** `global` / `joined` modes only. The formalization is **left marked for architect adjudication at gate 1**, with the leader's leaning recorded. See **LEADER RULINGS (2026-11-17, post-planning)**.

> → **FORMALIZED by architect gate 1 (2026-11-17 — Q4 CONFIRMED):** the shipped `instance` mode ships with **no timer, exactly as v0.31.0 behaves** (the timer object never exists in that mode). Reasoning: zero behavioural delta by construction; the mechanism is structurally clean (the lane is built in P1, mode-gated in P2); it is mechanically pinned by P2-8 case (iii); it is cheaply reversible (one mode-gate line); and the staleness cost is the status quo the live pilot already runs under. Constraint 4 outranks D2.1's breadth. **Q4 CLOSED.** (The asymmetric freshness between the new row popup and the shipped modal is disclosed — see D1.10 / AM-15.)

**D2.2 — CADENCE: 10 s.** Align with the metrics **overview** tab, the fastest-cadence tab in the house family: `POLL_INTERVAL_MS = 10_000` (`frontend/src/app/pages/metrics/overview/overview-tab.component.ts:157`; the charts tab is 15 s — `charts-tab.component.ts:155`; the house pin asserts both — `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts:65-66`). One timer per **open surface**, driving **both** lanes (the rooms list and the selected room's conversation) on the same tick. A single shared 10 s tick, not two.

**D2.3 — WHERE THE TIMER LIVES: the SERVICE lane, never the chat page.** The timer lives in the **room service / dialog lane** and is **started when a room surface OPENS and stopped when it CLOSES**. It **must not** live in `chat.component.ts` — the chat page's own spec **BANS** timers: `expect(PROD_CHAT_SOURCE).not.toMatch(/setTimeout|setInterval/)` (`frontend/src/app/pages/chat/chat-send-error.spec.ts:82-88`). The polling surface is therefore the **dialog**, which lives in its own component and its own service.

**D2.4 — THE HOUSE POLLING CONTRACT, MANDATORY ELEMENTS.** The new poll MUST carry every element of the house exemplar (`overview-tab.component.ts`):

| Element | House anchor | Requirement |
|---|---|---|
| **named interval constant** | `readonly POLL_INTERVAL_MS = 10_000` (`:157`) | a **named** constant, never a bare literal (the house doc-comment pins this at `:32`) |
| **`setInterval` + `clearInterval`** | `:230` / `:233-238` | torn down on close / destroy — never a leaked handle |
| **skip-if-in-flight** | `inFlight` guard, `:239-241` | a tick that lands while the previous is in flight is skipped (no request pile-up) |
| **monotonic supersede token** | `requestSeq` (`:172-173`), compared at `:248-251` | "a newer response always wins" — a slow response never overwrites a newer one |
| **visibilitychange gate** | `browserVisible` (`:166-168`), listeners at `:208` / `:212`, `shouldPoll()` at `:224-225` | the poll **stops when the tab is hidden** and resumes on return |
| **stale degradation** | `stale` signal + the stale/no-last-good copy split (`:262-281`) | a failed tick degrades honestly; a last-good is retained and labelled |
| **honest error states + manual retry** | `roomsError` / `messagesError` lanes (`frontend/src/app/services/room.service.ts:52-55`, `:60-63`) | never fabricate rows; the pane shows the error + a Retry button (`room-dialog.component.html:28-36`) |
| **open-room state (AM-5)** | the service must know WHICH room is open — the shipped `openRoom(roomId)` **discards** the id (`room.service.ts:140-143`) | the service holds `openRoomId` (set in `openRoom(roomId)`, cleared in `resetMessages()`); the tick **SKIPS** when no room is open — **no request, and NO error state** (a tick with nothing open is not a failure) |
| **cursor policy (AM-5)** | the POLL cursor vs the LOAD-MORE cursor are **different cursors** | the **poll cursor** = the max `seq` of the loaded messages (0 when empty) — computed from the loaded state, **never** from `nextAfterSeq`; the **load-more cursor** (`nextAfterSeq`) advances **only** when the server reports a non-null `next_after_seq`. The poll tick does **NOT** mutate `nextAfterSeq` — a **deliberate freeze**, never accidental: a frozen exhausted cursor freezes `loadMore`, and new rows arrive through `mergeMessagesBySeq` (`room.service.ts:211-222`) |

The **requestId guard** discipline the room service already implements — "increment-and-capture; a mismatch means a newer request superseded this one → drop" (`frontend/src/app/services/room.service.ts:31-37`, `:56`, `:69`) — **is the existing supersede-token mechanism** and is reused, not reinvented.

**D2.5 — WHY SSE IS REJECTED FOR THIS COMMISSION (three independent reasons, all evidence-backed — reason 4 WITHDRAWN per AM-6).**

1. **The room SSE channel is CONTENT-FREE BY DESIGN.** `LiveEventHub._ROOM_CHANNEL_CONTENT_KEYS` (`daemon/services/live_event_hub.py:199-212`) denylists `message/messages/content/text/text_body/body/tool_output/pack/checkpoint/html`; `broadcast_room_event` **DROPS** any event carrying one of those keys (`live_event_hub.py:244-253`) and also refuses any non-`room_`-prefixed `event_type` (`:237-243`). The events that actually ride the channel are **lifecycle only**: `room_created` / `room_closed` (`daemon/repositories/room/service.py:206`, `:241`) and `room_joined` / `room_left` (`:350`, `:378`, `:398`). **New-message TEXT never rides SSE.** ⇒ SSE cannot deliver the commission's "DETAIL view of a selected room (its messages)" at all.
2. **No frontend consumer exists.** `frontend/src/app/services/sse.service.ts` (1015 lines) contains **zero** `room` handlers — the grep for `room|Room` returns nothing. Building a consumer is a **new** frontend integration, not a cheap reuse.
3. **A global browser cannot use the lifecycle channel structurally.** The room channel is **per-room** (`add_room_channel`, `daemon/routers/messages.py:1101`; the hub's per-room buckets at `live_event_hub.py:56`, `:166`). To learn about a room, the client must already **subscribe to that room's id** — which it can only know **after** the list arrives. A room created *after* the list was fetched has **no subscription** ⇒ the lifecycle signal is **structurally incomplete** for a nest-wide browser, which is commission item (a). N per-room `EventSource`s would have to be opened to learn only lifecycle facts — the definition of not cheap.
4. ~~**The SSE room lane carries an OPEN, UNRESOLVED finding.**~~ **WITHDRAWN (AM-6) — the premise is STALE at this lane's base.** The SSE room-resolution pre-branch finding (`daemon/routers/messages.py:984-1049`) is **CLOSED** under A-P6-26 (option b — commits `5d13a1c6c` + `652203f95`, **both ancestors of `0181dadbf`**); the room-channel registry now lives in `daemon/services/live_event_hub.py:41-56` and the SSE room lane no longer routes a caller's subscription to the owner's content-bearing stream. The P6 gate file's `:72`/`:83` "disposition requested" wording is **stale at this base** (the finding's own text is preserved in the P6 record). ⇒ **The polling ruling rests on reasons 1–3**, each of which is independently sufficient. (The **separate**, still-live item is the messages route's **default-scope** breadth — the operator-lane breadth spot-check at `:63` — which the leader ruling of 2026-11-17 routed as a disclosed pre-existing follow-up; see D1.6/D1.7. It is a **messages-route** concern and does not bear on the mechanism choice here.)

**D2.6 — DEFERRED (not rejected): a content-free lifecycle INVALIDATION channel.** The lifecycle events that *do* ride SSE (`room_created` / `room_joined` / `room_left` / `room_closed`) are exactly the right signal to **invalidate the rooms list** — a later lane could open **one** per-room subscription per **listed** room and refresh the list on any lifecycle event. That is a legitimate future refinement (cheap once the list exists, and it is content-free by construction). It is **OUT OF SCOPE for this lane**: it adds a second freshness mechanism to one surface for a signal that polling already delivers at 10 s. **AMENDED (AM-6):** the deferral no longer rests on any open finding — the SSE room lane is a **closed, content-free contract** at this base (A-P6-26; `daemon/services/live_event_hub.py:41-56`) — it rests on the plain economics of **one** freshness mechanism per surface. Recorded so the reviewer can see the deferral is deliberate, not an oversight.

### EVIDENCE

- Content-free guard: `daemon/services/live_event_hub.py:199-212` (denylist), `:214-267` (`broadcast_room_event`, including the two drop guards at `:237-243` and `:244-253`).
- Lifecycle event emitters: `daemon/repositories/room/service.py:206`, `:241`, `:350`, `:378`, `:398` (kinds `room_created`, `room_closed`, `room_joined`, `room_left`).
- Per-room channel registration: `daemon/routers/messages.py:1101` (`add_room_channel`), `:1204` (`EventSourceResponse(..., ping=SSE_PING_INTERVAL)`), `daemon/constants.py:44` (`SSE_PING_INTERVAL: int = 30`), hub per-room buckets `daemon/services/live_event_hub.py:56`, `:166`.
- FE has **no** room SSE consumer: `frontend/src/app/services/sse.service.ts` — zero `room`/`Room` matches across 1015 lines.
- House polling exemplar: `frontend/src/app/pages/metrics/overview/overview-tab.component.ts:32-36` (contract doc-comment), `:157` (`POLL_INTERVAL_MS = 10_000`), `:166-168` (`browserVisible`), `:172-173` (`requestSeq`), `:175` (`pollHandle`), `:208`/`:212` (`visibilitychange` attach/detach), `:224-225` (`shouldPoll`), `:227-238` (`startPolling` / `stopPolling`), `:239-251` (in-flight guard + token compare), `:262-281` (stale / no-last-good copy). Sibling: `frontend/src/app/pages/metrics/charts/charts-tab.component.ts:155`, `:262`. House pin on the named constant: `frontend/src/app/pages/metrics/metrics.bindings.pins.spec.ts:44-46`, `:65-66`.
- Room service already carries the supersede guard: `frontend/src/app/services/room.service.ts:31-37`, `:56` (`roomsRequestId`), `:69` (`messagesRequestId`), `:113-` (`loadRooms`).
- Chat page timer ban: `frontend/src/app/pages/chat/chat-send-error.spec.ts:82-88`.

### RATIONALE

Constraint 3 says "prefer SSE **IF cheap**". The evidence says it is **not cheap** on three independent axes — it **cannot carry** the required content, it has **no** consumer to reuse, and it is **structurally incomplete** for the nest-wide case — and it is additionally coupled to an open finding. The house polling pattern, by contrast, is a **proven, pinned, four-times-repeated** house idiom (overview / charts / nest-reports / jobs) that already satisfies every guard the commission names: bounded interval, supersede tokens, stop on tab-hide. Choosing it is the cheap option; choosing SSE would be the expensive one.

The 10 s cadence is chosen over a faster one because the payload is **small and slow-moving** (room metadata; a conversation page), and because the house precedent for "the primary metrics surface" is exactly 10 s. The 15 s charts cadence is for heavier payloads.

The placement ruling (service/dialog lane, never the chat page) is a **hard** constraint, not a preference: the chat page's own spec fails closed on any `setTimeout`/`setInterval` in its source, and the chat page is the surface the video-team pilot exercises live (constraint 4).

---

## D3. MODAL/MENU PATTERN CHOICE

*(constraint 2 — "existing Angular component/service/SCSS/spec conventions"; commission (a) "HEADER MENU that opens the list of chatrooms + a DETAIL view of a selected room"; commission (b) "BIG POPUP (large modal — not a cramped tooltip/dropdown)")*

### RULING

**D3.1 — HEADER MECHANISM: a header control that opens the GLOBAL BROWSER dialog directly.** The chat-page header's `.toggle-buttons` family gains **one** new control, placed **beside** the shipped v0.31.0 room icon (which is **NOT** removed, moved, or regressed — constraint 4). The control opens the **global browser** — a two-pane dialog whose **left pane IS the "menu" of chatrooms** and whose **right pane is the DETAIL view** of the selected room (its messages).

**Rejected:** a `mat-menu` whose items open a detail pane. Reasons: (i) a Material menu is a single-column dropdown — the house mat-menu shells are widened only to `calc(100vw - 16px)` and remain **dropdowns** (`frontend/src/styles.scss:124-149`), and cannot carry a paginated conversation pane; (ii) the commission forbids cramped surfaces for the sibling feature and demands a DETAIL view of messages; (iii) the house precedent for a message-bearing surface is a **Material dialog** — **25** components under `frontend/src/app/components/` import `MatDialogModule`.

**"HEADER MENU" — how the words are honored:** the commission's phrase is realized as **the room list pane inside the browser dialog**, opened from the header. A `mat-menu` dropdown that then opens a dialog would be a **double-click** to reach the same content, and the commission's own modifier ("+ a DETAIL view of a selected room (its messages)") makes a dropdown the wrong container. *(Interpretation — flagged for the leader only if the Emperor's intent was a literal dropdown; see Open Questions.)*

> → RESOLVED by leader ruling 2026-11-17 (Q3): **SETTLED-CONFIRMED** — a header control that opens the **global dialog** directly; the "menu" is realized as the browser's room-list pane. No further adjudication required. See **LEADER RULINGS (2026-11-17, post-planning)**.

**D3.2 — THE GLOBAL BROWSER: a GENERALIZED `RoomDialogComponent`, not a sibling.** The shipped `RoomDialogComponent` (`frontend/src/app/components/room-dialog/`) is **generalized in place** into a **three-mode** dialog:

| Mode | List source | Backed by |
|---|---|---|
| `instance` (**existing, unchanged**) | `GET /api/instances/{id}/rooms` (TREE-scoped) | the shipped v0.31.0 modal — **byte-unchanged behaviour** |
| `global` (**new** — commission (a)) | `GET /api/rooms` (nest-wide) | the new D1 route |
| `joined` (**new** — commission (b)) | `GET /api/rooms`, **filtered to** rows whose `member_instance_ids` contains the row's instance id | the same ONE response; membership is server-computed |

**One dialog component, one conversation pane, one geometry constant** (`ROOM_DIALOG_SIZE` — `frontend/src/app/components/room-dialog/room-dialog.component.ts:14-19`: 82vw × 82vh, capped 96vw/96vh) and **one** global stylesheet override block (`.room-dialog-panel` / `.room-dialog-backdrop`, `frontend/src/styles.scss:59-88`). The **global browser inherits the BIG geometry** — it is a superset of the shipped modal, so it must be at least as large.

**Rejected:** a sibling `GlobalRoomBrowserComponent`. It would duplicate the two-pane template, the conversation pane, the error/retry states, the geometry constant, and the service lanes — precisely the duplication the house conventions forbid, and it would double the SCSS surface against the 8 kB per-component budget (D4).

**D3.3 — PER-INSTANCE POPUP (b): `RoomDialogComponent` reuse in `joined` mode.** The shipped dialog already renders rooms + conversation for a given instance (`room-dialog.component.ts:88-91` → `roomService.loadRooms(data.instanceId)`). The row button opens the **same** dialog in **`joined`** mode with that instance's id. The only change to the shipped path is the **list source switch**, which is the D1 membership filter — the shipped `instance` mode is untouched.

> → **AMENDED (AM-9 — the joined mode's ANCHOR POSTURE is now PINNED: ANCHORED).** `joined` mode fetches **`GET /api/rooms?instance_id=<row id>`** — the ANCHORED form — not the anchor-less form. Three consequences, all pinned: (i) the **production path exercises the anchor-present predicate** (every row predicate-checked, fail-closed) rather than the anchor-less breadth; (ii) the wire is **narrower** (the instance's readable rooms only); (iii) **the FE membership filter is UNCHANGED** — anchored rows are a **superset** of the instance's JOINED rooms (a room the instance joined is by construction readable by it), so filtering the anchored rows by `member_instance_ids` yields the same joined set. The **anchor-less** form remains in use for exactly two callers: the **global browser** (commission (a)) and the **membership gate** (commission (c), D3.5) — both of which are nest-wide by construction. P1's URL-shape spec cases and the P2/P3 data flow agree with this pinning.

**D3.4 — SERVICE-STATE ISOLATION (a real defect risk, ruled now).** `RoomService` is `providedIn: 'root'` (`frontend/src/app/services/room.service.ts:39-40`) and holds **shared singleton lanes** (`rooms`, `messages`, `nextAfterSeq`). With **two** dialog surfaces now able to exist (the global browser from the chat page; the joined popup from the instances list), the two would **share and stomp** those lanes. **Ruling:** the dialog component MUST provide its **own** `RoomService` instance (component-level `providers: [RoomService]`), so every open gets a **fresh** lane set. This is safe and contained **at base**: `RoomService` is injected **only** by the dialog component today (`grep RoomService` over `frontend/src/app/` → the dialog component + the two specs), so at base the change has exactly one production consumer. **AMENDED (AM-2 — the consumer count becomes TWO at P3, and that is the intended split):** at P3 the **instances-list component** consumes the **root singleton** (the one-shot `fetchAllRooms` for the membership gate — a read-only one-shot that touches **no shared lane**), while the **dialog** consumes its **own component-scoped instance** (the mutable `rooms`/`messages`/polling lanes). The two never share mutable state: the gate's fetch is a **stateless one-shot** whose result lands in a component-local signal, so the root singleton's shared lanes are never written by the gate path. The requestId guards keep working unchanged inside the per-dialog instance. **Consequence for the shipped spec (AM-2):** one shipped case (`frontend/src/app/components/room-dialog/room-dialog.component.spec.ts:258-288`, "drops a stale room list when a newer rooms request superseded it") resolves the service through `TestBed.inject(RoomService)` — the **root** instance — while the component holds a **component-scoped** one; that case must be **RE-POINTED** at `fixture.debugElement.injector.get(RoomService)`. It is **re-pointed, never weakened**: the guard it proves (a stale response is dropped) is unchanged, only the injector through which the service is reached changes.

**D3.5 — THE VISIBILITY GATE (c) + THE visible≡actionable DISCIPLINE.**
- **Gate signal:** a dedicated membership map — `{instance_id → has_joined_rooms}` — computed from the **one** global-list response's `member_instance_ids`. The instances list fetches that response **once on mount** and derives the map.
- **Rule:** the row button renders **iff** the map says the instance has **≥1** joined room. **No dead icon on room-less instances** (commission (c)).
- **visible ≡ actionable (fix-G discipline):** the gate and the click handler **MUST read the SAME signal**. The shipped precedent is explicit: the header icon's `@if (renderedInstance())` gate and its `onOpenChatrooms()` guard read the **same** `renderedInstance` signal (`frontend/src/app/pages/chat/chat.html:76-99`; `frontend/src/app/pages/chat/chat.component.ts:1910-1913`), with the comment *"so 'visible' and 'actionable' cannot drift apart"* (`chat.html:80-88`). The row button repeats that discipline verbatim: one signal, read by both.
- **Failure posture:** if the global-list fetch **fails**, the membership map is **EMPTY** ⇒ the button is **HIDDEN** (fail-closed on the visibility claim: a hidden convenience affordance never breaks a function, whereas a shown-but-broken button is a dead icon — the exact defect (c) forbids). The instances list surfaces an **honest error state + Retry** rather than silently hiding. **Recorded explicitly** because "fail-closed" and "honest error" pull in opposite directions here and the lane must choose: **fail-closed on the BUTTON, honest on the SURFACE.**
- **No polling for the gate.** The map is fetched once on mount and refreshed by the D2 poll **only while a room surface is open** — the gate is a mount-time fact, not a live one. A room joined while the list page is open will appear on the next mount/refresh. **AMENDED (AM-9):** the gate's map comes from the **anchor-less** list (`fetchAllRooms(null)`) — it must, because the map spans **every** instance in the nest; the **`joined` popup's own list is ANCHORED** (D3.3) — one route, two callers, two anchor postures.
- **AMENDED (AM-15 — "remount" means BROWSER RELOAD on the chat page).** The gate's map is a **mount-time** fact, and on the **chat page** "mount" is coarser than it sounds: the chat host (and with it the instances list) is created **once per session and never destroyed** (`frontend/src/app/app.ts:361-376`, `:391-400` — "the host must be created exactly once per session for cache semantics"), so the map refreshes on a **browser reload** (or a fresh session), not on a chat-page re-open. On the `/instances` route the list component mounts per navigation, so a re-navigation refreshes it. **Disclosed** in the final report (AM-15). *(Recorded as a deliberate simplicity choice; a live membership feed is out of scope.)*

**D3.6 — THE RECORDED CONTRADICTION: there is NO WATCHOVER button in the instances list.** The commission places the row button "(next to the WATCHOVER button)". **The instances list has no WATCHOVER control.** `grep -rn "watchover|WATCHOVER" frontend/src/app/components/instance-list/` returns **zero** matches; WATCHOVER exists **only** in `frontend/src/app/pages/chat/chat.html`, `frontend/src/app/pages/chat/chat.component.ts`, `frontend/src/app/components/watchover-dialog/`, `frontend/src/app/services/api.service.ts`, and `frontend/src/app/styles.scss`. The instances-list row is an `<a [routerLink]>` carrying a row-action cluster of `pin-btn` / `pause-btn` / `terminate-btn` (`frontend/src/app/components/instance-list/instance-list.html:212-257` — AM-6-corrected anchor).

**Ruling:** place the row button **inside the existing row-action cluster** (the pin/pause/terminate cluster), **without adding any WATCHOVER row control** — creating a WATCHOVER button on the instances list would be a **scope expansion** the commission did not ask for, and the commission's (b) is about the **chatroom** button. The row button MUST call `$event.stopPropagation()` so the nested button does not trigger the row's `routerLink` navigation — the **existing** precedent in that exact row is `onTogglePin(instance.instance_id, instance.pinned, $event)` / `onPauseInstance(..., $event)` / `onTerminateInstance(..., $event)` (`instance-list.html:221`, `:233`, `:252`), and the house comment states the reason — the row is an `<a [routerLink]>`, so nested buttons must not trigger the row's navigation (`instance-list.html:161-163` — AM-6-corrected anchor; the tag-picker trigger comment).
`MatDialog` is **already injected** in the row's component (`frontend/src/app/components/instance-list/instance-list.component.ts:78`) — the row button needs **no** new DI. (The service-broker idiom `LiveViewActionsService` — `frontend/src/app/services/live-view-actions.service.ts:51-98` — is available for callers that should not inject `MatDialog` directly; the row's component already injects it, so **direct injection** is the house-consistent choice here.)
**Carried to the Open Questions** — the placement is a leader confirmation, not a silent decision.

> → RESOLVED by leader ruling 2026-11-17 (Q1): **RESOLVED-ACCEPTED** as recommended — the button goes in the **existing** row action cluster (pin/pause/terminate); **no** WATCHOVER control exists on the instances-list row (WATCHOVER is a chat-page header control), so the Emperor's "(next to the WATCHOVER button)" phrasing has **no row anchor**; the action cluster IS the row's control group. **FINAL-REPORT DISCLOSURE DUTY:** the report to the Emperor carries this disclosure **and** the one-line move if overruled. See **LEADER RULINGS (2026-11-17, post-planning)**.

**D3.7 — CONDITIONAL-RENDER PRECEDENTS.** The row's conditional-render idioms are established: `@if (!instance.parent_id)` (`instance-list.html:217`, `:248`) and the status-gated pause/resume branch (`:230-246`) — all inside the AM-6-corrected cluster anchor `:212-257`. The new button follows the **same** idiom (a scoped `@if` on the membership signal).

### EVIDENCE

- House dialog pattern: **25** components under `frontend/src/app/components/` import `MatDialogModule`; the large-popup exemplar is `components/room-dialog/` (4 files: `.html` 147 lines, `.scss` 262, `.spec.ts` 289, `.ts` 128).
- Geometry: `ROOM_DIALOG_SIZE` — `frontend/src/app/components/room-dialog/room-dialog.component.ts:14-19` (82vw × 82vh / max 96vw × 96vh), spread into `dialog.open` by the chat component (`frontend/src/app/pages/chat/chat.component.ts:1913`) and asserted byte-identically by the spec. Global overrides: `frontend/src/app/styles.scss:59-88` — `.room-dialog-panel` sets `--mat-dialog-container-color: #1e293b` and `.mat-mdc-dialog-content { max-height: none }` (Material caps the content at 65vh — the two-pane layout manages its own scrolling); `.room-dialog-backdrop` at `:88`. The stylesheet comment records **why** the size is inline (*"inline styles beat the stylesheet, which is what lifts Material's 560px pane cap"*, `styles.scss:63-68`).
- Tester-measured size: **1312 × 738 px = 82vw × 82vh @ 1600×900**, `tooltipPresent=false` — `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:30-32` (FE web automation table, rows (a)/(b)).
- Service-broker idiom: `frontend/src/app/services/live-view-actions.service.ts:51-98` (`inject(MatDialog)`, centralized open-config, returns the `MatDialogRef`).
- Chat-page header structure: `frontend/src/app/pages/chat/chat.html:62-123` — `.toggle-buttons` at `:62`; WATCHOVER toggle `:63-75`; the **v0.31.0 room icon** `:76-99` (gated `@if (renderedInstance())`, with the fix-G visible≡actionable comment at `:80-88`); Think/Tools/System `:100-123`. The chat page has **no** `mat-menu` usage (zero `matMenuTriggerFor` matches in `chat.html`).
- mat-menu shell widening precedent: `frontend/src/app/styles.scss:124-149` (`.mat-mdc-menu-panel.job-queue-dropdown`, `.notification-dropdown`) — the comment records that these are **dropdowns** whose shells were widened, not panes.
- Instance-list row: `frontend/src/app/components/instance-list/instance-list.html:116-258` — the row is `<a [routerLink]>` (`:124`); row-action cluster **`:212-257`** (AM-6-corrected; pin `:217-229`, pause/resume `:230-246`, terminate `:249-256`); the nested-button/`<a>` rationale **`:161-163`** (AM-6-corrected; the tag-picker trigger comment — "both triggers share the same menu and prevent the parent `<a [routerLink]>` from navigating"); `MatDialog` already injected at `frontend/src/app/components/instance-list/instance-list.component.ts:78`.
- **No WATCHOVER in the instances list**: `grep -rn "watchover\|WATCHOVER\|Watchover" frontend/src/app/components/instance-list/` → **zero** matches; WATCHOVER's real locations are `pages/chat/chat.html`, `pages/chat/chat.component.ts`, `pages/chat/chat.scss`, `pages/chat/chat.chatrooms.spec.ts`, `pages/chat/chat.component.spec.ts`, `components/watchover-dialog/*`, `models/index.ts`, `services/api.service.ts`, `styles.scss`.
- Existing v0.31.0 FE room pins: `frontend/src/app/pages/chat/chat.chatrooms.spec.ts:247` (describe), `:372` (icon next to watchover), `:401` (hidden when no instance selected), `:432` (opens the BIG modal), `:457` (no-op when nothing rendered).
- `RoomService` is root-provided with shared lanes and is injected **only** by the dialog: `frontend/src/app/services/room.service.ts:39-40`, `:44-68` (shared signals); `frontend/src/app/components/room-dialog/room-dialog.component.ts:67`.

### RATIONALE

**Why one generalized dialog rather than two.** The commission's (a) and (b) are the **same surface at two scopes** — a room list plus a conversation. The shipped `RoomDialogComponent` already IS that surface (two levels inside one large modal, `room-dialog.component.ts:36-47`). Generalizing it (three modes, one list-source discriminator) is **one** component, **one** geometry constant, **one** stylesheet block, **one** service contract. A sibling browser component would duplicate all of it and double the SCSS surface against the 8 kB per-component budget. The generalization also **preserves the shipped modal byte-for-byte** in `instance` mode, which is exactly what constraint 4's no-regression duty demands.

**Why the header control opens the dialog directly.** The commission's own words — "a DETAIL view of a selected room (its messages)" — require a container that can hold a conversation pane. A Material menu cannot. The house's own BIG-popup exemplar (`ROOM_DIALOG_SIZE`) is the pattern that already satisfies "large modal, NOT a cramped tooltip". The "menu" is honored by the browser's **left pane** (the list of chatrooms), so the user still gets "a menu of rooms → a detail" in two clicks-inside-one-surface.

**Why the row button goes in the existing action cluster.** The commission's parenthetical "(next to the WATCHOVER button)" describes a spatial relationship that **does not exist** on the instances surface — WATCHOVER lives only on the chat page's header. Creating a WATCHOVER row control would invent a feature nobody asked for. The existing cluster (pin / pause / terminate) is the established home for **row-level affordances**, is already a set of nested buttons with the `$event.stopPropagation()` discipline, and already has `MatDialog` injected in its component. The placement is therefore the minimal, convention-following choice — and it is carried to the leader as an open question rather than decided silently, because the Emperor's spatial intent is the one thing the code cannot tell us.

**Why the service must be per-dialog-instance.** Two dialog surfaces over one root singleton with shared signal lanes is a **cross-talk** defect waiting for a user to open both (the instances list and the chat page are reachable in the same session). The fix is one line in one component's `providers` and it has exactly one production consumer today. The alternative — splitting the service into two services — would duplicate the guard machinery.

**Why the visibility gate fails CLOSED on a failed fetch.** Commission (c)'s stated purpose is "**no dead icon** on room-less instances". A button that is VISIBLE but cannot function is precisely the dead icon (c) forbids. When the membership map is unknown (fetch failed), the honest and safe rendering is: **button hidden, surface shows the error + Retry**. The alternative (show the button optimistically) would re-create the dead icon under a network failure, and the commission's rule is about the VISIBILITY of a non-actionable control, not about a transient data state.

---

## D4. BUDGET CONFORMANCE PLAN

*(constraint 2 — "ZERO new npm deps; respect the /metrics lazy-route budget + chunk discipline (read the CURRENT budget constants/guard in the repo — do not trust stale numbers); house SVG renderer patterns untouched")*

### RULING

**D4.1 — DEPENDENCIES: ZERO additions.** The new surfaces use only packages already present in `frontend/package.json:27-62` (27 runtime dependencies): `@angular/material` (dialog / menu / icon / button / tooltip / spinner), `@angular/common/http` (`HttpClient`, `HttpParams`), `@angular/core` (signals, `inject`), `rxjs`. **No new npm dep, no new devDep.** The house SVG renderer (`frontend/src/app/components/charts/house-svg-renderer/`) is **untouched** — the new surfaces render **no charts**.

**D4.2 — THE BUDGETS ARE EXACTLY WHERE `angular.json` SAYS THEY ARE.** Read, not remembered:

| Guard | Value | Anchor |
|---|---|---|
| `initial` (bundle) | warn `1MB` / **error `6MB`** | `frontend/angular.json:64-70` |
| `anyComponentStyle` | warn `8kB` / **error `24kB`** | `frontend/angular.json:71-75` |
| `outputHashing` | `all` | `frontend/angular.json:76` |
| dev configuration | **NO budgets at all** | `frontend/angular.json:77-82` |

⇒ **The ONLY leg in which the budgets apply is `ng build --configuration production`.** The dev configuration carries no `budgets` block, so a dev-config build **cannot** fail a budget — the exact trap the AM-23 lesson records ("task 5.9's SCSS-budget assertion via the dev build checks nothing → add one production-config leg", `.agents/shared/planning/central-metrics-v1/architecture-recommendation-v2.md:59`; AM-23 itself at `:174`).

**D4.3 — CHUNK DISCIPLINE: NO NEW ROUTE, NO NEW EAGER CHUNK.** Every route in `frontend/src/app/app.routes.ts:134-225` is lazy (`loadComponent`). The new surfaces add **no route at all** — they are **imperatively-opened dialogs**, reached from two already-lazy surfaces:

| Surface | Host chunk | Why it is lazy | Anchor |
|---|---|---|---|
| global browser (commission (a)) | the **chat** chunk | the chat host is **dynamically imported** at the app root on the first `detailVisible` flip — `import('./pages/chat/chat.component').then(...)` + `vcr.createComponent(Chat)` | `frontend/src/app/app.ts:313-397` (`:391` the dynamic import); the "BUG 3" pin at `frontend/src/app/app.component.spec.ts:1733-1745` records *why*: the static `<app-chat>` element once dragged the chat chunk into the initial bundle (**6.09 MB > 6 MB error**) |
| joined popup (commission (b)) + visibility gate (c) | the **instances** chunk | `instances` is a lazy route | `frontend/src/app/app.routes.ts:135` |
| the generalized `RoomDialogComponent` | its **own lazy chunk** (already) | the room dialog is imported **statically** by the chat component (`frontend/src/app/pages/chat/chat.component.ts:52`) but the chat component is itself lazy | release-plan R12 (`.agents/shared/planning/group-chat-release/release-plan.md:870`) records the room dialog shipping in its own lazy chunk **and** the S3 verification trap (esbuild mangles `RoomDialogComponent` / `ROOM_DIALOG_SIZE` → a naive grep or `index.html` check **false-negatives**) |

**Declared manifest delta:** adding a **second** importer of the generalized dialog (the instances-list component) may cause the build to **re-home the dialog into a shared chunk** between the chat and instances chunks. That is a **chunk-file rename/re-home inside the lazy graph**, **not** initial-bundle growth. **The expected FE-manifest delta is therefore: ZERO initial-bundle growth, and ONE lazy chunk file added / re-homed — measured at build time and DECLARED in the stage-prep checklist** (AM-23's "declare the expected FE-manifest delta", `architecture-recommendation-v2.md:174`).

**D4.4 — SCSS BUDGET PER COMPONENT.** Each new/modified component's SCSS must stay **under 8 kB** (the warn threshold) per component. The generalized dialog reuses the **existing** `room-dialog.component.scss` (262 lines) and the **existing** global override block (`styles.scss:59-88`) — the global browser's extra styles are a **mode-scoped** addition, not a second stylesheet. **Ruling: if the generalized dialog's SCSS crosses 8 kB, SPLIT the global-browser-specific rules into a separate stylesheet/component — NEVER raise the budget constants.** The budget is a **house guard, not a knob**; raising it is a scope expansion the commission did not authorize.

**D4.5 — THE LEGS TO RUN (all four, in order).**

1. **Type-check** — `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` → EXIT 0.
   **House-critical idiom:** use the **local** `node_modules/.bin/tsc` from inside `frontend/`. A bare `tsc` / `npx tsc` outside a package directory resolves the **`tsc` npm package** (not TypeScript) or performs a **remote fetch-and-execute** — this is a standing house prohibition, not a style preference.
2. **Unit** — jest, whole-tree (or at minimum: the room specs + the touched specs + the guard specs). The **must-stay-green** set: `frontend/src/app/services/room.service.spec.ts`, `frontend/src/app/components/room-dialog/room-dialog.component.spec.ts`, `frontend/src/app/pages/chat/chat.chatrooms.spec.ts` (4 pins at `:372`, `:401`, `:432`, `:457`), `frontend/src/app/pages/chat/chat-send-error.spec.ts` (the timer ban at `:82-88`), `frontend/src/app/pages/chat/chat.component.spec.ts`, `frontend/src/app/app.component.spec.ts` (the "BUG 3" initial-bundle pin at `:1733-1745`), `frontend/src/app/app.routes.spec.ts` (the route-shape pin precedent at `:24-29`).
3. **Production build** — `cd frontend && ./node_modules/.bin/ng build --configuration production` → **EXIT 0, and NO budget WARN.** This is the only leg that exercises `angular.json:64-75`. **Any budget WARN is a REPORTED finding** (AM-23), not a silent pass.
4. **Backend** — the room-surface unit/pack legs (the FE has no BE counterpart here, but the new route needs its own spec): the route's typed-error vocabulary, the anchor-present predicate filter, the anchor-absent operator breadth, and the purge-exclusion behaviour.

**D4.6 — DRIFT-GUARD MARKERS (a HARD gate, not a nicety).** The source-gc drift guard is **hunk-scoped and path-scoped**:

- **Marker vocabulary:** `FRONTEND_UI_LANE_MARKERS = ("room", "chat")` — every committed `frontend/` hunk must carry one of these markers on its lowercased ± lines (`tests/unit/repositories/test_source_gc_repository.py:771`; the rule at `:968-980`).
- **PATH allowlist:** `FRONTEND_UI_LANE_SANCTIONED_FILES` — **14** exact paths (`test_source_gc_repository.py:784-806`); a `frontend/` path **outside** that list is an **offender REGARDLESS of wording** (`:921-926`, `:968`). Base `FRONTEND_UI_LANE_SANCTIONED_BASE = 858a85e31…` (`:813`).
- **MAINTENANCE RULE (binding):** *"extending the sanctioned surface = add the path to `FRONTEND_UI_LANE_SANCTIONED_FILES` in the SAME change that touches it"* (A-P6-24 / F-1, `group-chat/decisions.md:514`). ⇒ **Any NEW file this lane creates under `frontend/` (e.g. a new spec for the row button, a new membership service) MUST be added to the allowlist in the SAME commit** — otherwise the guard REDs and the lane's own commit is the offender.
- **Daemon-side hunks** near `daemon/manager.py` / `daemon/api.py` should carry a **`group chat`** / **`room`** marker (the manager.py sanctioned-lane marker convention, A-P6-23, `group-chat/decisions.md:512`) — the FE markers are deliberately **NOT** folded into `SANCTIONED_LANE_MARKERS`, so a manager.py hunk needs its own marker.
- **Rule of the parse:** the guard's diff parse **flushes on `diff --git` boundaries** — a markerless trailing hunk in one file must **not** piggyback on the next file's first hunk (A-P6-23). Each new file's hunks must carry their own marker.

### EVIDENCE

- Budgets: `frontend/angular.json:64-75` (`initial` 1MB/6MB at `:64-70`; `anyComponentStyle` 8kB/24kB at `:71-75`); `outputHashing: "all"` at `:76`; the `development` configuration carries **no** budgets (`:77-82`).
- AM-23 (the production-config leg is the only leg that can fail a budget): `.agents/shared/planning/central-metrics-v1/architecture-recommendation-v2.md:59` and `:174`.
- Lazy routes: `frontend/src/app/app.routes.ts:134-225` — `instances` at `:135`; every entry is a `loadComponent`.
- The chat host's lazy root-mount: `frontend/src/app/app.ts:22-30` (the "NOT statically imported" note), `:313-397` (`:391` the dynamic import, `:397` the `createComponent`); the "BUG 3" pin and its measured cause (6.09 MB > 6 MB): `frontend/src/app/app.component.spec.ts:1733-1745`.
- The room dialog's own lazy chunk + the S3 grep trap: `.agents/shared/planning/group-chat-release/release-plan.md:870` (R12).
- Route-shape pin precedent: `frontend/src/app/app.routes.spec.ts:24-29`.
- FE dependency baseline: `frontend/package.json:27-62` — 27 runtime dependencies; the room dialog's imports are all `@angular/material` + `@angular/core` + `@angular/common/http` + `rxjs` (`frontend/src/app/components/room-dialog/room-dialog.component.ts:1-11`).
- Per-component SCSS: the shipped room-dialog SCSS is 262 lines; the global override block is `frontend/src/styles.scss:59-88`.
- Drift guard: `tests/unit/repositories/test_source_gc_repository.py:771` (markers), `:784-806` (the 14-path allowlist), `:813` (base), `:921-926` + `:968-980` (the path-first rule); A-P6-23 at `.agents/shared/planning/group-chat/decisions.md:512`; A-P6-24 / F-1 (the path allowlist + the maintenance rule) at `:514`.

### RATIONALE

The dispatch explicitly warned: *"read the CURRENT budget constants/guard in the repo — do not trust stale numbers"*. Read, they are exactly `1MB/6MB` initial and `8kB/24kB` anyComponentStyle — **and only in the production configuration**. That single fact drives the whole plan: a dev-config build **cannot** prove budget conformance, so the production leg is **mandatory**, not optional (AM-23's recorded lesson).

The chunk strategy is chosen to make the budget question **moot**: the new surfaces add **no route**, so they inherit the laziness of the chat chunk and the instances chunk, and the generalized dialog stays in the lazy graph. The **only** manifest effect is a possible chunk re-home — declared, measured, and reported.

The drift-guard ruling is elevated to a hard gate because of a **measured** failure mode recorded in the repo: the P6 FE lane originally tripped `test_post_merge_span_exactly_sanctioned_hunk` because it touched 14 `frontend/` files (A-P6-23), and the subsequent F-1 fix made the rule **path-first** — so a **new** file with correct wording is **still** an offender. A lane that creates new FE files without extending the allowlist in the same change will RED its own commit.

---

## D5. RECONCILIATION SECTION

*(the commission's explicit duty — reconcile the new commission against every in-repo record of the group-chat FE surface)*

### D5.1 — WHAT EXISTS IN-REPO (adopted + verified)

| Record | Anchor | Status |
|---|---|---|
| **NO** in-repo "D1 fast-follow" spec under the `gc-fe-surfaces` label | `grep -rln "fast-follow\|fast follow" .agents/shared/planning/ .agents/tester/` → hits only in **unrelated** lanes (`pause-resume-terminate-tree-fix`, `leader-completion-attestation`, …); **zero** under `group-chat*` or `gc-fe-surfaces` | **CONFIRMED absent** — the commission is NET-NEW as a document |
| The commission is recorded **only** as | project history note **2026-11-17** + KV `wt.active.feature/gc-fe-surfaces.gc-fe-20261117` (commission + `expected_scope` one-liners) | the commission's in-repo footprint is the KV + history note |
| D-S13 (P6 UI direction) | `.agents/shared/planning/group-chat/decisions.md:122-124` | **authoritative for the per-instance modal DESIGN** (BIG POPUP, predicate parity, "instances surface" wording) |
| Architecture §13.1 | `.agents/shared/planning/group-chat/architecture-recommendation.md:273-277` | **authoritative for the per-instance modal DESIGN** |
| Release-plan §7.1 (icon placement, open adjudication) | `.agents/shared/planning/group-chat-release/release-plan.md:872-878` | **SUPERSEDED BY the commission** on the surface adjudication |
| Release-plan §9 OQ-1 | `.agents/shared/planning/group-chat-release/release-plan.md:967` | **SUPERSEDED BY the commission** (the open question is now answered) |
| A-P6-23 / A-P6-24 (the frontend drift sanction) | `.agents/shared/planning/group-chat/decisions.md:512`, `:514` | **authoritative for the D4 marker/allowlist obligations** |
| P6 tester gate — the operator-lane breadth spot-check | `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63` | **authoritative for the D1 breadth posture** |

### D5.2 — MATCHES / DELTAS

| # | Commission item | In-repo record | Verdict | Resolution |
|---|---|---|---|---|
| 1 | **(a) GLOBAL header browser** (nest-wide) | — no in-repo record of a nest-wide room list | 🟡 **DELTA — net-new** | D1 adds the ONE route that serves it; D3.1/D3.2 add the header control + the `global` dialog mode. |
| 2 | **(b) ROW BUTTON → JOINED rooms** | §13.1's literal *"instances surface / instance row"* reading + *"from the instance row the user already looks at"* (`architecture-recommendation.md:273-277`); release-plan §7.1 flagged the mismatch and called it *"a follow-up FE change"* (`:872-878`) | 🟢 **MATCH of the anticipated placement-only follow-up** | The anticipated follow-up exists and is now commissioned. **DELTA inside it:** the *"next to the WATCHOVER button"* anchor does not exist on the instances surface → see #6. **DELTA inside it:** the semantics are JOINED (membership), not tree-scoped → see #5. |
| 3 | **(c) CONDITIONAL VISIBILITY** | A-P6-36 — the shipped header icon gates on `renderedInstance()` (selection), **not** on membership | 🟡 **DELTA — extends** | The new row button gates on **membership** (≥1 joined room). The shipped header icon's gate is **untouched** (constraint 4). |
| 4 | **Constraint 1 (read predicate)** | §13.1's *"SAME room-read API … members ∪ owner-ancestry, fail-closed"* (`architecture-recommendation.md:273-277`); the shipped 2-route surface (`rooms.py:122-327`) | 🟢 **MATCH** | D1: the predicate is the SAME core; the addition is read-only, same scope, same middleware, disclosed verbatim. |
| 5 | **(b) semantics: JOINED vs tree-scoped** | D-S13 + §13.1 both say *"rooms created by that instance('s tree)"* | 🟡 **NAMED DELTA** | **Resolved in favour of the COMMISSION:** the row popup + the visibility gate are **MEMBERSHIP**-based; the **shipped** header-icon modal stays **TREE**-scoped and untouched (D1.10). |
| 6 | **"(next to the WATCHOVER button)" on the ROW** | **no WATCHOVER control exists in the instances list** (`components/instance-list/` → zero matches) | 🟡 **NAMED DELTA — unresolved anchor** | **Recommended resolution:** place the button in the **existing row-action cluster** (pin/pause/terminate) and **do not** create a WATCHOVER row control (D3.6). **Carried to the leader.** |
| 7 | **Constraint 2 (FE discipline)** | the house Angular/metrics-tab conventions (`architecture-recommendation.md:277`); AM-23 (production-config budget leg) | 🟢 **MATCH** | D4. |
| 8 | **Constraint 3 (live updates)** | v0.31.0's item #7 rides LiveEventHub; the house polling family | 🟢 **MATCH — decided on evidence** | D2 (polling; SSE rejected with four evidence-backed reasons). |
| 9 | **Constraint 4 (stability / no regression)** | the shipped header icon + its 4 FE pins (`chat.chatrooms.spec.ts:372`, `:401`, `:432`, `:457`) | 🟢 **MATCH** | D3.2 (`instance` mode byte-unchanged) + D4.5 (the pin set is in the must-stay-green list). |
| 10 | **Constraint 5 (gates)** | the house chain | 🟢 **MATCH** | carried by the lane charter; **staged release, NO live promote**. |
| 11 | **Constraint 6 (concurrency)** | in-flight lanes are disjoint from `frontend/**` | 🟢 **MATCH** | the only coordination is **release-slot sequencing** with other staging lanes. |

### D5.3 — AUTHORITY MAPPING

| Record | Authoritative for | NOT authoritative for |
|---|---|---|
| **D-S13** (`group-chat/decisions.md:122-124`) + **§13.1** (`architecture-recommendation.md:273-277`) | the **per-instance modal DESIGN** — BIG POPUP geometry, the two-level shape (rooms → conversation), predicate parity, the FE-convention inputs, and the P6 exit criteria | the **surface placement** (the "instances surface / instance row" wording is superseded by the 2026-11-17 commission) |
| **release-plan §7.1 / §9 OQ-1** | the **historical record** of the placement tension (chat-page header vs instances-list row) and of the fact that a list-row placement was known to be a **follow-up FE change** | **nothing forward-looking** — the commission supersedes both on the surface adjudication |
| **A-P6-23 / A-P6-24** (`group-chat/decisions.md:512`, `:514`) | the **frontend drift-guard obligations** (marker vocabulary, the 14-path allowlist, the same-change maintenance rule) | — |
| **P6 tester gate** (`:63`, `:72`, `:83`) | the **operator-lane breadth spot-check** (`:63`) and the **open F-3 SSE pre-branch finding** (`:72`) | — |
| **THE 2026-11-17 COMMISSION** (`spec.md`) | **sole source** for (a) the global browser, (c) the conditional-visibility rule, the (b) JOINED semantics, and the **staged-release / NO-live-promote** disposition | the per-instance modal's internal design (deferred to D-S13/§13.1) |

**Record authoritative for what — one paragraph.** The **group-chat Phase-2 record** (D-S13 + §13.1) is authoritative for **how the per-instance modal looks and behaves** (the BIG popup, the rooms→conversation shape, predicate parity) and for the **frontend drift-guard obligations** (A-P6-23/A-P6-24); the **release plan** is authoritative only for the **historical record** of the placement tension it flagged and explicitly deferred. The **2026-11-17 commission** is the **sole source** for the three NEW surfaces (the nest-wide global browser, the membership-based per-instance popup, the membership-based conditional visibility) and for the disposition (**staged release, NO live promote**). Where the commission and the older records describe the same *surface* differently — **JOINED (membership) vs tree-scoped** — the **commission governs the new row-button surfaces**, while the **shipped tree-scoped modal stays exactly as built** (no-regression duty). Where the commission describes an **anchor that does not exist in the code** — "next to the WATCHOVER button" on the instances row — the **code governs the mechanism** (the existing row-action cluster) and the question goes to the leader rather than being decided silently.

---

## OPEN QUESTIONS FOR THE LEADER

*(collected here, deliberately — one section, per house practice)*

> → **ALL THREE questions below are dispositioned by the leader ruling of 2026-11-17** — see **LEADER RULINGS (2026-11-17, post-planning)**. **Q1 → RESOLVED-ACCEPTED** · **Q2 → RESOLVED-PROCEED** · **Q3 → SETTLED-CONFIRMED**. The question texts below are **preserved verbatim** as the record of what was asked; the rulings are folded as a dated section, not as edits to these bodies.

**Q1 — D3.6 / D5 #6 — the row button's anchor.** The commission places the row button "(next to the WATCHOVER button)", but **the instances list has no WATCHOVER control** (WATCHOVER exists only on the chat-page header). **Recommendation:** place the chatroom row button in the **existing row-action cluster** (pin / pause / terminate) and **do not** create a WATCHOVER row control. **If the Emperor's intent was a different spatial position** (e.g. immediately left of the pin button, or a distinct visual cluster), the answer is a one-line template move — **no architectural consequence either way**. *Decision needed before the FE lane starts; nothing else blocks on it.*

**Q2 — D1.7 — the F-3 coupling (LEADER-SIGNIFICANT).** The anchor-less mode of the new nest-wide list is safe **only** because the shipped messages route defaults its scope to the room OWNER, letting an `instances:read` caller read any room's messages by id (`.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63`). The F-3 SSE pre-branch finding (`:72`) is **still open** for a disposition (`:83`). **Recommendation: settle F-3 BEFORE the new route is cut**, and land any tightening of the messages route **in the same change** as the list route. If F-3 is ruled to option (c) (mirror the read predicate), the list route's default **must** flip to anchor-only in that same commit — which is why D1.7 requires the default to be a **one-parameter** switch. *If the leader prefers to decouple, the fallback is: the route ships anchor-ONLY from day one (the FE then supplies a scope anchor), at the cost of the nest-wide browser needing a per-instance anchor — i.e. commission item (a) would need re-specification.* **This is the one question that can change the commission's outcome; it is raised rather than assumed.**

**Q3 — D3.1 — the "HEADER MENU" reading.** The commission says the header "grows a HEADER MENU that opens the list of chatrooms + a DETAIL view of a selected room (its messages)". **Recommendation:** a header control that opens the **global browser dialog** directly, with the room list as the dialog's left pane and the conversation as its right pane — a Material menu cannot carry a message pane, and the commission's own detail requirement makes a dropdown the wrong container. **If the Emperor meant a literal dropdown menu** whose items open a second surface, the deviation is one component and one interaction step. *Raised for confirmation only; the recommendation is strong.*


---

## LEADER RULINGS (2026-11-17, post-planning)

Folded into the ledger on **2026-11-17**. **Rulings only — no decision body above was rewritten.** Each entry
is labeled with the question id it answers; the one-line pointer at each affected site points here. The
question texts in the Open Questions section above are **preserved verbatim** as the record of what was
asked.

### Q1 — WATCHOVER placement → **RESOLVED-ACCEPTED** (as recommended)

> **Ruling (leader, 2026-11-17):** ACCEPTED as recommended: button goes in the EXISTING row action cluster
> (pin/pause/terminate); no WATCHOVER control exists on the instances-list row (WATCHOVER is a chat-page header
> control).

**Recorded in the ledger:**
- The Emperor's phrasing **"(next to the WATCHOVER button)" has no row anchor** — the instances-list row
  carries **no** WATCHOVER control (WATCHOVER is a chat-page header control only; D3.6's zero-match grep
  stands).
- The **action cluster IS the row's control group** — the button is placed there, carrying the
  `$event.stopPropagation()` discipline the existing pin/pause/terminate buttons already use
  (`instance-list.html:212-257` — AM-6-corrected anchor).
- **FINAL-REPORT DISCLOSURE DUTY:** the report to the Emperor must carry (i) **this disclosure** — the
  commission's spatial phrasing had no anchor on the instances surface, and the action cluster was used
  instead — **and** (ii) **the one-line move if overruled** (the placement is a single template-line move; no
  architectural consequence either way).

**Affects:** D3.6 (ruling text stands), D5 #6 (the named delta is now **resolved**), plan-overview R3, Open
Questions Q1.

### Q2 — F-3 coupling → **RESOLVED-PROCEED**

> **Ruling (leader, 2026-11-17):** PROCEED with GET /api/rooms exactly as planned: every row
> predicate-checked fail-closed via the shared _require_room_access; the _ALLOW_ANCHORLESS_GLOBAL_LIST knob
> ships IN THE SAME COMMIT as the route. The F-3 messages-route scope tightening itself is ROUTED as a
> disclosed PRE-EXISTING follow-up (owner: leader-tracked queue) — this lane does NOT touch the messages
> route default scope.

**Rationale to record:** the **video-team pilot is live on this release** exercising room machinery
(constraint 4); a **mid-flight authz tightening on the messages route is exactly the regression class the
commission forbids**. The tightening touches the read path a live pilot depends on; the **new** route's shape
does not — it ships fail-closed, every row predicate-checked, with the flip knob **in the same commit** so the
tightening stays **one parameter** whenever the owner picks it up.

**Architect remit at gate 1:** the architect re-examines the **security posture of the NEW route's shape** —
it may tighten the **new** route, **not** the pre-existing messages route.

**Affects:** D1.4 / D1.6 / D1.7 (the coupling is accepted; the knob is same-commit), D1.11 (the disclosure
paragraph stands; the F-3 disposition is now **routed**, not open), plan-overview R2, Open Questions Q2.

### Q3 — header menu → **SETTLED-CONFIRMED**

> **Ruling (leader, 2026-11-17):** CONFIRMED: header control opening the global dialog.

**Recorded in the ledger:** a header control that opens the **global browser dialog** directly; the "menu" is
realized as the browser's **room-list pane** (D3.1's realization stands unchanged). No further adjudication
required.

**Affects:** D3.1 (settled), D5 #1, Open Questions Q3.

### Q4 — instance modal stays timer-free → **ARCHITECT GATE-1 ITEM** (leader leaning: YES)

> **Ruling (leader, 2026-11-17):** Leader leaning YES: constraint 4 (stability, byte-behavior-identical
> shipped mode) outranks D2.1 breadth. Leave marked for architect formalization at gate 1, with the leaning
> recorded.

**Recorded in the ledger:** the **leaning is YES** — the shipped `instance` mode stays **timer-free**;
constraint 4 (stability; byte-behaviour-identical shipped mode) outranks D2.1's breadth, so the polling lane
is enabled for the **new** `global` / `joined` modes only. The formalization is **left open for the architect
at gate 1** — the leader's ruling is a **leaning, not a formal adjudication** — and the leaning is recorded
here so the architect adjudicates against a known position rather than an open question.

**Affects:** D2.1 / D2.3 (placement unchanged: the timer never lives in the chat page, and the `instance` mode
adds none), plan-overview C-2, Open Questions Q4.

### Q5 — D1.5 descendant closure → **ARCHITECT GATE-1 ITEM**

> **Ruling (leader, 2026-11-17):** Marked for architect adjudication at gate 1; accept the re-formulation if
> technically sound.

**Recorded in the ledger:** the C-1 re-formulation stands as the item under adjudication — D1.5's literal
"caller's ancestor set" inverts the shipped predicate, and the implementable one-shot pre-compute is the
caller's **DESCENDANT closure** (`InstanceRepository.get_tree_ids_permanent`, depth cap 256), with the
per-room test `owner == caller or owner ∈ descendant_ids`. **Substance preserved:** one decision rule,
implemented once, two application sites, O(1) per room. The leader's ruling is **conditional on technical
soundness**, so the architect at gate 1 either accepts the re-formulation or states the defect.

**Affects:** D1.5 (the pre-compute direction is the open item), plan-overview R9 / C-1, Open Questions Q5.

### RELEASE-SLOT NOTE (2026-11-17)

**`origin/latest` drift past this lane's base is EXPECTED** — parallel lanes merge continuously, so the base
this lane was opened at (`0181dadbf`) will be behind `origin/latest` by cut time. **Drift-sync at merge is the
giter's standard business**; the merge/reconcile step is not this lane's work product and is not re-litigated
here.

**This lane NEVER RACES the release slot.** The cut is sequenced by the leader at cut time against the other
in-flight staging lanes (prompt_reload compact-note; the Emperor's own ceremony queue). The lane's disposition
stays **staged, NOT promoted** (commission constraint 5 + the commission metadata). The lane's only obligation
at merge time is to be **rebaseable cleanly** — it does not hold the slot, does not fast-forward the local
`latest` ref (that is the `ari-stop-int` lane's, per `group-chat-release/release-plan.md:865`), and does not
treat expected drift as an anomaly.

**Affects:** plan-overview R6 (release-slot sequencing) — the drift **expectation** is recorded; the ownership
boundary is unchanged.
