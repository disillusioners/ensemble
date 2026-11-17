# Phase 1: Read Surface + FE Service Layer

## Objective

Land the **single** new backend read surface `GET /api/rooms[?instance_id=…]` (router handler + service wrapper + repository reuse with the **shared** fail-closed predicate) and the **FE service layer** both new surfaces consume: the global-list fetch, the membership map, the per-dialog-instance posture, and the house-conformant bounded-polling lane.

This phase carries the **wire contract** every later phase codes against. Nothing here renders UI.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| **1** | **BE — repository: nest-wide list with batched extras.** In `daemon/repositories/room/repository.py`, add `list_all_rooms_with_extras() -> list[dict[str, Any]]`: ONE `Session`, `select(ChatRoom)` ordered by `(created_at, room_id)`, no state filter (purge = row deletion ⇒ purged rooms naturally absent, `models.py:96`/`:193-270`), then reuse `_active_member_counts` (`:1274-1292`), `_last_message_seqs` (`:1294-1309`), and `list_active_member_instance_ids` (`:1327-1341`) **batched on the same room-id list**. Emits, per room: the existing `room_payload(...)` fields plus `member_count`, `last_message_seq`, `member_instance_ids`. **No N+1, no new SQL helpers.** | none | A spec call over ≥3 rooms issues ONE session and produces exactly the batched helper calls; `member_instance_ids` carries the ACTIVE roster only (a LEFT member absent). |
| **2** | **BE — repository: extract the shared decision rule (D1.5, C-1).** Extract the members-∪-owner-ancestry **decision** out of `_require_room_access` (`repository.py:740-772`) into one module-level helper, e.g. `_room_readable(*, is_active_member: bool, owner_ancestry_ok: bool) -> bool` (fail-closed: `False` when both inputs are falsey). Re-point `_require_room_access` at it (its behaviour and `RoomNotFoundError` messages unchanged) and add `list_readable_rooms(caller_instance_id: str) -> list[dict]` for the list site: ONE batched membership set + ONE `InstanceRepository.get_tree_ids_permanent(caller)` descendant closure (`daemon/repositories/instance/repository.py:838`, cap 256 at `:50`), then the shared rule per room in O(1). | 1 | Exactly **one** definition of the decision rule in the diff (audit: no second "members ∪ owner-ancestry" expression anywhere); `_require_room_access`'s existing pins stay green; the list path is O(1)/room after two one-shot pre-computes. |
| **3** | **BE — router: `GET /api/rooms`.** In `daemon/routers/rooms.py`, add the handler beside the existing two (`:122-207`, `:210-327`), modelled on `list_instance_rooms` for shape/typed errors (`:97-107`, `:157-206`): `dependencies=[scope_required(_SCOPE_READ)]`; optional `instance_id: str = Query("")`. **Anchor present** ⇒ `manager.get_instance(instance_id)` (`KeyError` ⇒ 404 `instance_not_found`) then `room_service.list_readable_rooms(instance_id)` (every row predicate-checked, non-readable rows DROPPED). **Anchor absent** ⇒ `room_service.list_all_rooms()`, gated by a **module-level, one-parameter switch** `_ALLOW_ANCHORLESS_GLOBAL_LIST: bool = True` (C-1/D1.7 = the F-3 tightening knob). Faulted list ⇒ 503 `room_service_unavailable` with a generic message, fault detail server-side only. Row shape exactly as the D1 contract (note: `state` not `closed`; `member_instance_ids` present). | 1, 2 | Handler present; `_ALLOW_ANCHORLESS_GLOBAL_LIST` consulted in exactly ONE branch; error vocabulary is the closed set; body shape `{detail:{error,message}}`. |
| **4** | **BE — service wrapper.** In `daemon/repositories/room/service.py`, add `list_all_rooms()` and `list_readable_rooms(caller_instance_id)` as `asyncio.to_thread` facades, mirroring `list_rooms_for_tree` (`:491-496`) — off-loop, no logic. | 1, 2 | Wrappers are pure pass-throughs (no predicate, no shaping). |
| **5** | **BE — new router spec.** Create `tests/unit/routers/test_rooms_list_api.py`, modelled on the exemplar `tests/unit/routers/test_room_http_api.py` (fixtures `engine`/`svc`/`_Manager`/`client` at `:70-130`) and the harness `tests/helpers/room_harness.py` (`make_room`, `join_member`, `seed_instance`, `resolve_tree_root_id`). Cover: exact row field set; anchor-present denial (non-member/non-ancestor ⇒ row **absent**); anchor-present positives (active member; owner-ancestor); anchor-absent breadth (`active` + `closed` listed, purged **not**); 404 `instance_not_found` on a bad anchor; 503 `room_service_unavailable` on a faulting service (leak scan 0 hits of fault text); the `_ALLOW_ANCHORLESS_GLOBAL_LIST=False` flip (restored in `finally`). | 3, 4 | All legs green in isolation; exact-set assertions (no subset matching). |
| **6** | **BE — census re-pins.** Update **both** pinned declared-route counts: `tests/unit/metrics/test_metrics_query_scope_cage.py:52` (`DECLARED_ROUTE_CENSUS = 119` → `120`), its node name `:96` (`…_reads_119` → `…_reads_120`) and docstring `:8`/`:32`/`:51`; and `tests/unit/test_router_scope_manifest.py:466` (`== 119` → `120`) plus docstring `:455`. **Mount census (40) and class-family count (36) stay unchanged** — verified: `/api/rooms` already exists as a family (`test_router_scope_manifest.py:198`, `test_router_class_manifest.py:170`) and the mount is unchanged (`daemon/api.py:6152`). | 3 | The identified sites green; the mount census still reads 40; the family count still reads 36. |
| **7** | **BE — verify-only set (no edits expected; record the audit).** Confirm unchanged: `daemon/api.py` (import `:119`, mount `:6152`), `tests/integration/test_router_class_manifest.py:411` (36), `tests/unit/test_post_restart_arm_notify_no_parallel.py` mount census (A-P6-20), `tests/unit/test_api_router_extraction.py` (its two known reds are base-attributable per A-P6-20). If any of these **does** need a change, it is disclosed here and in the P4 report — never silently edited. | 3, 6 | Audit note recorded; zero surprise edits. |
| **8** | **FE — model: global rows + fail-closed normalizer.** In `frontend/src/app/models/room.model.ts`, add `GlobalRoomSummary extends RoomSummary { tree_root_id: string; owner_instance_id: string; member_instance_ids: string[] }` and `normalizeGlobalRoomListResponse(raw)` mapping the wire `state` (`'active' \| 'closed'`) onto the existing `closed: boolean` field, defaulting `member_instance_ids` to `[]` and using the existing `asRecord/asString/asFiniteNumber/asNullableString` helpers. Keep the file's two documented invariants (tolerate extra fields silently; DROP rows that cannot be rendered honestly). Also export a pure `buildMembershipMap(rows: GlobalRoomSummary[]): Map<string, boolean>` (`instance_id → has ≥1 joined room`). | none | Normalizer drops a row with no `room_id`; `state:'closed'` ⇒ `closed:true`; unknown fields never reach the render path; the map builder is pure and unit-testable. |
| **9** | **FE — service: global lane + per-dialog posture + polling lane.** In `frontend/src/app/services/room.service.ts`: (i) `fetchAllRooms(scopeInstanceId?: string \| null): Observable<RoomListResponse>` hitting `GET /api/rooms` (with `HttpParams` only when a scope anchor is supplied); (ii) `loadGlobalRooms(mode: 'global' \| 'joined', scopeInstanceId?: string \| null)` publishing into the **existing** `rooms` signal behind its **own** requestId guard (so `joined` mode publishes the membership-filtered subset — the FE filters *which of the server-listed rooms to show*, it never decides readability, D1 rationale); (iii) the polling lane per D2.4: `readonly POLL_INTERVAL_MS = 10_000`, `startPolling()` / `stopPolling()`, `pollHandle`, `inFlight` skip, monotonic `pollSeq` supersede token, `browserVisible` + `visibilitychange` attach/detach, `refreshRooms()` (re-issues the current mode's list) and `refreshOpenRoom()` (fetches `after_seq = max(loaded seq) \|\| 0` and merges via the existing `mergeMessagesBySeq` (`:211`) — **not** via `loadMessages(..., true)`, whose `nextAfterSeq() === null` guard would no-op on a short conversation); (iv) `stale` degradation flag + honest copy. Keep every existing public member and guard untouched. | 8 | New members added, existing members unchanged; the lane is inert until `startPolling()`; `stopPolling()` clears the handle and detaches listeners. |
| **10** | **FE — extend `room.service.spec.ts`.** Add cases: global URL shape (no query when unanchored; `instance_id=` when anchored); `joined` filtering by `member_instance_ids`; membership map (0 / 1 / N rooms, instance in several rooms); `POLL_INTERVAL_MS === 10_000`; a tick while in flight is skipped; a slower older response never overwrites a newer one (supersede token); `stopPolling()` stops ticks; hidden tab ⇒ no tick, visible ⇒ resumes; `refreshOpenRoom()` appends only new seqs and dedupes; polling failure keeps last-good rows and sets `stale`. | 9 | The existing 17 spec cases stay green **unedited**; new cases all green. |

## Files

**Modify:** `daemon/repositories/room/repository.py` · `daemon/repositories/room/service.py` · `daemon/routers/rooms.py` · `tests/unit/metrics/test_metrics_query_scope_cage.py` · `tests/unit/test_router_scope_manifest.py` · `frontend/src/app/models/room.model.ts` · `frontend/src/app/services/room.service.ts` · `frontend/src/app/services/room.service.spec.ts`

**Create:** `tests/unit/routers/test_rooms_list_api.py`

**Verify-only (expect ZERO edits):** `daemon/api.py` · `tests/integration/test_router_class_manifest.py` · `tests/unit/test_post_restart_arm_notify_no_parallel.py` · `tests/unit/test_api_router_extraction.py`

**Explicitly untouched:** `frontend/src/app/pages/chat/**`, `frontend/src/app/components/room-dialog/**`, `frontend/src/app/components/instance-list/**`, `frontend/src/app/app.routes.ts`, `frontend/src/app/services/sse.service.ts`, `frontend/src/styles.scss`, `frontend/angular.json`.

## Tests

- **New BE spec:** `tests/unit/routers/test_rooms_list_api.py` (8 legs, see task 5).
- **BE regression (must stay green):** `tests/unit/routers/test_room_http_api.py` (the shipped contract pins, untouched), `tests/unit/metrics/test_metrics_query_scope_cage.py`, `tests/unit/test_router_scope_manifest.py`, `tests/integration/test_router_class_manifest.py`, `tests/unit/routers/test_room_sse_resolution.py`.
- **FE:** `frontend/src/app/services/room.service.spec.ts` (extend only).

## Verification Commands

```bash
# BE (worktree venv — never the main .venv)
cd /home/nea/ensemble-src-wt-gc-fe-surfaces
.venv/bin/python -m pytest -q tests/unit/routers/test_rooms_list_api.py
.venv/bin/python -m pytest -q tests/unit/routers/test_room_http_api.py \
    tests/unit/metrics/test_metrics_query_scope_cage.py \
    tests/unit/test_router_scope_manifest.py \
    tests/integration/test_router_class_manifest.py

# FE
cd /home/nea/ensemble-src-wt-gc-fe-surfaces/frontend
./node_modules/.bin/jest src/app/services/room.service.spec.ts
./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
```

## Risks

| Risk | Mitigation |
|---|---|
| **R2 / Q2 (F-3 coupling):** the anchor-less breadth is the leak surface if the messages route is later tightened. | One-parameter switch (`_ALLOW_ANCHORLESS_GLOBAL_LIST`) consulted in a single branch + a flip test; Q2 raised before the route is cut. |
| **R9 / C-1 (pre-compute direction):** a literal reading of "caller's ancestor set" inverts the predicate. | The descendant-closure formulation implemented and disclosed in `plan-overview.md` C-1; the denial direction is fail-closed either way, so a mis-rule can never *widen* reads — it can only under-serve, which the anchor-present positive legs would catch immediately. |
| **R8 (route census):** three pinned sites, one of which embeds the count in a test NAME. | Task 6 names all three sites; P4 re-audits. |
| **R10 (service-state isolation):** the new lanes live on the root singleton today. | P2 adds component-level `providers: [RoomService]`; P1's new lane members are written to be instance-safe (no module-level mutable state). |
| **R11 (timer leak):** a lane that outlives its consumer. | Every element pinned by its own spec case (task 10); P2 wires `stopPolling()` into BOTH close and `ngOnDestroy`. |
| Polling correctness on a paged conversation (last-good rows retained, new seqs appended, no duplicates) | `refreshOpenRoom()` uses `mergeMessagesBySeq` (existing, `room.service.ts:211-222`) with `after_seq = max(loaded seq)`; dedicated spec case. |

## No-Regression Notes

- The shipped `GET /api/instances/{id}/rooms` handler and `GET /api/rooms/{room_id}/messages` handler are **not touched** — new code is additive in the same module.
- `_require_room_access`'s external behaviour is unchanged: same resolution order, same `RoomNotFoundError` messages (the existing spec pins them).
- `RoomService`'s existing public members (`rooms`, `messages`, `loadRooms`, `openRoom`, `loadMessages`, `resetMessages`, …) keep their signatures and semantics; the new polling lane is opt-in and inert by default.

## Coupling

- **Tight with P2** — the `global` mode codes against `loadGlobalRooms('global')` + `POLL_INTERVAL_MS` + `startPolling`/`stopPolling`; the D1 row shape is the dialog's list contract.
- **Tight with P3** — `joined` mode consumes `loadGlobalRooms('joined', instanceId)`; the visibility gate consumes `buildMembershipMap` over the same one response.
- **Loose with P4** — P4 re-runs this phase's legs.

## Exit Criterion

`GET /api/rooms` answers both modes with the exact contract shape and the closed error vocabulary; the predicate decision rule exists **once** with both sites routed through it; the new BE spec and the census pins are green; `RoomService` exposes the global lane, the membership map, and a fully-pinned polling lane; `room.service.spec.ts` green with the existing 17 cases unedited; `tsc --noEmit -p tsconfig.app.json` exits 0.
