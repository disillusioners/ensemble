# Phase 2: Surface (a) — Header Global Browser

## Objective

Deliver commission item **(a)**: the chat-page header grows **one** control that opens the **nest-wide** room browser — a two-pane dialog whose left pane is the room "menu" and whose right pane is the selected room's conversation — implemented as the **`global` mode** of the generalized `RoomDialogComponent`, with the shipped v0.31.0 header icon and its `instance` mode **byte-behaviour-identical**.

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| **1** | **Dialog — mode discriminator.** In `frontend/src/app/components/room-dialog/room-dialog.component.ts`, add `export type RoomDialogMode = 'instance' \| 'global' \| 'joined'` and widen `RoomDialogData` to `{ mode?: RoomDialogMode; instanceId?: string \| null; instanceName?: string \| null }`. **`mode` DEFAULTS to `'instance'` when absent**, and `instanceId` is required-by-contract only in `'instance'`/`'joined'` — so the shipped caller (`chat.component.ts:1913`) and the shipped spec (`{instanceId:'inst-1', instanceName:'Tester'}`) keep working **without a single edit**. | P1 | The shipped data payload resolves to `'instance'`; no shipped call site changes. |
| **2** | **Dialog — per-open service posture (D3.4) + polling wiring (D2.3).** Add `providers: [RoomService]` to the `@Component` decorator so every open gets a fresh lane set (no root-singleton cross-talk). Wire the polling lane: `ngOnInit` → for `'global'`/`'joined'` call `roomService.loadGlobalRooms(mode, instanceId)` then `roomService.startPolling()`; `ngOnDestroy` → `roomService.stopPolling()` **then** the existing `resetMessages()`. **`'instance'` mode starts NO polling** (C-2: constraint 4's byte-unchanged duty outranks D2.1's general phrasing; the lane stays built and mode-gated). | 1, P1-9 | Two dialogs open simultaneously keep independent `rooms`/`messages` lanes; closing clears the interval (no handle leak); `'instance'` mode issues exactly the requests it issues today. |
| **3** | **Dialog — mode-aware list source + copy (AM-9: the URL posture is mode-determined).** In `room-dialog.component.ts`, `retryRooms()` re-issues **the current mode's** fetch (never `loadRooms(instanceId)` unconditionally — that would silently flip a `global` dialog into tree scope). **AM-9 — the list source per mode is fixed here and must agree with P1/P3**: `instance` ⇒ `GET /api/instances/{id}/rooms` (**unchanged**); `global` ⇒ `GET /api/rooms` (**anchor-less**); `joined` ⇒ `GET /api/rooms?instance_id=<row id>` (**ANCHORED** — the production path exercises the anchor-present predicate; the anchored rows are a **superset** of the instance's joined rooms, so the membership filter yields the same set). In `room-dialog.component.html`, add mode-aware branches **only for the non-instance modes**: title text (`global` → nest-wide label; `joined` → the instance's joined-rooms label) and the empty-state copy (`'No chatrooms in this nest yet.'` / `'This instance has not joined any chatroom.'`). **Every `'instance'`-mode string asserted by the shipped spec is preserved verbatim** — the mode branches are additive `@if` arms, never a rewrite of the existing text. | 1, 2 | The shipped copy renders unchanged in `'instance'` mode; `global`/`joined` never show the instance-scoped empty copy. |
| **4** | **Dialog — SCSS, budget-safe.** In `room-dialog.component.scss`, add **mode-scoped** rules only (e.g. `.room-dialog--global .pane-header-text { … }`). Keep the file under the **8 kB** `anyComponentStyle` warn threshold (`frontend/angular.json:71-75`); if it crosses, move the global-only rules into a separate stylesheet — **never** raise the constant (D4.4). Geometry stays `ROOM_DIALOG_SIZE` (`room-dialog.component.ts:14-19`) and the global override block stays `styles.scss:59-88` (**no** `styles.scss` edit; no new `panelClass`). | 3 | `wc -c` of the component SCSS under 8 kB; `styles.scss` untouched. |
| **5** | **Chat page — the header control.** In `frontend/src/app/pages/chat/chat.html`, insert ONE control **after** the shipped room-icon `@if (renderedInstance()) { … }` block (`:76-99`) and **before** the Think button (`:100`): `class="toggle-btn rooms-browser-btn"`, `aria-label="All chatrooms"`, `title="All chatrooms (this nest)"`, a distinct `mat-icon` glyph, `(click)="onOpenRoomsBrowser()"`, **no instance gate** (the browser is nest-wide). **Do not touch the shipped room-icon block**: the pin at `chat.chatrooms.spec.ts:372` asserts `watchover-btn.nextElementSibling === room-btn`, which stays true only if the new control lands **after** the room icon. **Do not reuse the `room-btn` class** — the shipped spec's `roomButton()` helper queries `.room-btn` (`chat.chatrooms.spec.ts:349-351`). | 1 | DOM order: `watchover-btn` → `room-btn` (shipped, gated) → `rooms-browser-btn` (new, ungated) → `think-btn`. |
| **6** | **Chat page — handler.** In `frontend/src/app/pages/chat/chat.component.ts`, add `onOpenRoomsBrowser()` next to `onOpenChatrooms()` (`:1910-1924`): `this.dialog.open(RoomDialogComponent, { data: { mode: 'global' }, ...ROOM_DIALOG_SIZE, panelClass: 'room-dialog-panel', backdropClass: 'room-dialog-backdrop' })`. **No instance guard** (deliberate — nest-wide), and **no timer** (the chat page keeps its ban, `chat-send-error.spec.ts:82-88`). Leave `onOpenChatrooms()` **byte-identical**. | 5 | `chat.component.ts` still contains zero `setTimeout`/`setInterval`. |
| **7** | **Chat page — SCSS family extension.** In `frontend/src/app/pages/chat/chat.scss`, extend the icon-only toggle rule (`.toggle-btn.room-btn`, `chat.scss:176-184`) with a comma selector `.toggle-btn.rooms-browser-btn` so the new control inherits the family chrome **without** touching the shipped `.toggle-btn.room-btn` rule. | 5 | `room-btn` computed styles unchanged; the new control matches the family (padding, 1.05rem glyph, no `.active` state). |
| **8** | **Specs — dialog (global mode + instance regression + isolation) — AM-2: ONE shipped case is RE-POINTED, NEVER WEAKENED.** In `frontend/src/app/components/room-dialog/room-dialog.component.spec.ts`: every existing case stays green, and **exactly one** — `room-dialog.component.spec.ts:258-288`, *"drops a stale room list when a newer rooms request superseded it"* — is **re-pointed** from `TestBed.inject(RoomService)` (the **root** instance) to `fixture.debugElement.injector.get(RoomService)` (the **component-scoped** instance that `providers: [RoomService]` creates). The case **reds as written** without the re-point (root vs component-scoped injector) while the guard it proves stays correct — the re-point changes only **which injector the service is reached through**, never what is asserted (a stale response is still dropped). All other existing cases stay **unedited**. Then add (i) `global` mode GETs `/api/rooms` (no query) and renders the nest-wide rows; (ii) `global` mode empty state uses the nest-wide copy; (iii) `'instance'` mode issues **exactly** `GET /api/instances/{id}/rooms` and **no** `GET /api/rooms` and starts **no** polling (fake timers: zero requests after 30 s); (iv) `global` mode polls on the 10 s cadence and stops on destroy (fake timers: `advanceTimersByTime(10_000)` ⇒ one refresh; after destroy, none); (v) two fixture instances opened concurrently keep independent lanes (newest-response-wins per instance). | 2–4 | Existing cases green; **the one shipped case `:258-288` is re-pointed at the component injector (RE-POINTED, NEVER WEAKENED — AM-2)**; the other 7 shipped cases **unedited**; 5 new cases green. |

## Files

**Modify:** `frontend/src/app/components/room-dialog/room-dialog.component.ts` · `room-dialog.component.html` · `room-dialog.component.scss` · `room-dialog.component.spec.ts` · `frontend/src/app/pages/chat/chat.html` · `frontend/src/app/pages/chat/chat.component.ts` · `frontend/src/app/pages/chat/chat.scss` · `frontend/src/app/pages/chat/chat.chatrooms.spec.ts`

**Create:** none (avoids a new allowlist row in this phase).

**Verify-only (expect ZERO edits):** `frontend/src/styles.scss` (reuse `room-dialog-panel`) · `frontend/src/app/app.routes.ts` (no new route) · `frontend/src/app/services/sse.service.ts` · `frontend/angular.json` (no budget change) · `frontend/src/app/pages/chat/chat-send-error.spec.ts` (timer ban stays green).

**Drift-guard note:** all eight modified paths are **already** inside `FRONTEND_UI_LANE_SANCTIONED_FILES` (`tests/unit/repositories/test_source_gc_repository.py:784-806`) — this phase adds **no** allowlist entry. Every hunk must still carry a `room`/`chat` marker on its ± lines (`:771`, rule at `:968-980`).

## Tests

- **Extended:** `frontend/src/app/components/room-dialog/room-dialog.component.spec.ts` (+5 new cases; of the 8 shipped cases, **one is re-pointed** at `fixture.debugElement.injector.get(RoomService)` — AM-2 — and the other **7 stay untouched**).
- **Extended:** `frontend/src/app/pages/chat/chat.chatrooms.spec.ts` — a **new** `describe` block for the header browser control (placement pin, ungated render, `dialog.open` args, geometry, panelClass). The **four shipped pins** (`:372`, `:401`, `:432`, `:457`) stay **unedited**.
- **Must-stay-green (unedited):** `frontend/src/app/pages/chat/chat-send-error.spec.ts`, `frontend/src/app/pages/chat/chat.component.spec.ts`, `frontend/src/app/app.component.spec.ts` ("BUG 3" initial-bundle pin `:1733-1745`), `frontend/src/app/app.routes.spec.ts`.

## Verification Commands

```bash
cd /home/nea/ensemble-src-wt-gc-fe-surfaces/frontend
./node_modules/.bin/jest src/app/components/room-dialog/room-dialog.component.spec.ts \
                     src/app/pages/chat/chat.chatrooms.spec.ts \
                     src/app/pages/chat/chat-send-error.spec.ts \
                     src/app/pages/chat/chat.component.spec.ts
./node_modules/.bin/tsc --noEmit -p tsconfig.app.json
wc -c src/app/components/room-dialog/room-dialog.component.scss

# drift-guard markers on every committed frontend/ hunk
cd /home/nea/ensemble-src-wt-gc-fe-surfaces
git diff --stat 0181dadbf..HEAD -- frontend/
python -m pytest -q tests/unit/repositories/test_source_gc_repository.py
```

## Risks

| Risk | Mitigation |
|---|---|
| **R1 (pilot stability):** the shipped icon or modal regresses. | The shipped `chat.html:76-99` block and `onOpenChatrooms()` are untouched; the new control is inserted **after** it (protecting the `:372` adjacency pin); `'instance'` mode keeps its exact request set and copy; the four shipped pins are never edited. |
| **R10 (lane cross-talk):** two dialogs over one root singleton. | `providers: [RoomService]` on the component (task 2) + a concurrency spec (task 8.v). |
| **R4 (SCSS budget):** mode-scoped rules push the component stylesheet over 8 kB. | Measured in the exit criterion; split rather than raise (D4.4). |
| **New control reusing `room-btn`** would silently break the shipped `roomButton()` helper. | Distinct class `rooms-browser-btn` mandated (task 5) + the shipped pins assert the retained class set. |
| **`retryRooms()` mode drift:** a global dialog quietly reverting to tree scope on Retry. | Task 3 makes the retry path mode-derived; spec case (i) exercises list + retry. |
| **Marker-less hunk** on a `chat.html`/`chat.scss` comment-only edit. | Every hunk carries the marker; P4 audits per-hunk. |
| **AM-2 — the shipped supersede-guard case reds** once `providers: [RoomService]` lands (root vs component-scoped injector). | The case is **re-pointed** at `fixture.debugElement.injector.get(RoomService)` — the assertion is **unchanged** (the guard still proves that a stale response is dropped); the plan's "existing cases green UNEDITED" wording is corrected to "re-pointed, never weakened" for that single case. |
| **AM-9 — a mode/URL mismatch** between the dialog, the service and P3's row button. | The mode→URL table is pinned in **three** places that must agree: P1 task 9 (service), P2 task 3 (dialog), P3 task 1 (row button) — `instance` ⇒ tree route, `global` ⇒ anchor-less, `joined` ⇒ anchored. |

## No-Regression Notes

- **Shipped header icon:** markup, gate (`@if (renderedInstance())`), handler and a11y attributes unchanged.
- **Shipped `instance` modal:** same list source (`GET /api/instances/{id}/rooms`), same copy, same geometry, same error/retry states, **no timer** (Q4 CONFIRMED at architect gate 1).
- **Shipped dialog spec:** every case green; **exactly one** case (`:258-288`) is **re-pointed** at the component's own injector because `providers: [RoomService]` makes the component's service component-scoped — the assertion it makes is **unchanged** (strengthening, never loosening — AM-2). The plan's earlier "existing cases green UNEDITED" wording is **corrected** for that single case.
- **Chat page:** no timers, no new route, no SSE consumer.
- **`styles.scss` / `angular.json` / `app.routes.ts`:** untouched.

## Coupling

- **Tight with P1** — consumes `loadGlobalRooms`, `POLL_INTERVAL_MS`, `startPolling`/`stopPolling`, `buildMembershipMap` (only the dialog's mode plumbing is new here).
- **Tight with P3** — P3 must **not** edit the four dialog files; the `joined` mode is data-selected (`mode: 'joined'` + `instanceId`), which this phase's discriminator already supports. If P3 needs a dialog change, it reports and this phase's owner absorbs it.
- **Loose with P4.**

## Exit Criterion

From the chat header an operator can open the global browser with **one click**, browse every room in the nest and read any room's conversation in the 82vw × 82vh dialog; the dialog declares its own `RoomService`; polling runs only in `global`/`joined` and stops on close/destroy; the dialog + chat specs are green with the four shipped pins and all shipped dialog cases **unedited**; `tsc` exits 0; the component SCSS is under 8 kB; the source-gc drift pin is green (`room`/`chat` marker on every hunk).
