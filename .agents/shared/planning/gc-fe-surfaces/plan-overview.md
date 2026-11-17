# Plan Overview: Group-Chat FE Fast-Follow Surfaces (`gc-fe-surfaces`)

Date: 2026-11-17
Author: plan-creation worker (plan set) for the `gc-fe-surfaces` lane
Status: **Draft — ready for architect adjudication → reviewer council → fresh approver**
Lane: `gc-fe-surfaces` · task id `gc-fe-20261117`
Branch: `feature/gc-fe-surfaces` · base `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (v0.31.0)
Worktree: `/home/nea/ensemble-src-wt-gc-fe-surfaces`
Disposition: **staged release, NO live promote**

**Authoritative inputs (this plan implements them; it does not re-litigate them):**

- [`spec.md`](./spec.md) — the Emperor's commission, verbatim ((a) global header browser, (b) per-instance big popup, (c) conditional row-button visibility; six binding constraints).
- [`decisions.md`](./decisions.md) — the ruled decision ledger **D1–D5**. Where this plan says "per D3.4", the ledger is the authority.

---

## Objective

Ship the three commissioned group-chat FE surfaces — a **nest-wide room browser opened from the chat-page header** (a), a **per-instance joined-rooms big popup opened from an instances-list row button** (b), and **conditional visibility of that row button** (c) — served by **one** new read-only backend route, **zero** new npm dependencies, **zero** new routes/chunks, and with the shipped v0.31.0 header icon + its `instance`-mode modal **byte-behaviour-identical** for the live video-team pilot.

The feature is complete when: an operator with `instances:read` can, from the chat header, browse every room in the nest and read any room's messages in the same 82vw × 82vh dialog; from either instances-list host, a row button appears **only** on instances that have joined ≥1 room and opens that instance's joined rooms with their conversations; and the full gates chain passes with a staged release cut and **no live promote**.

---

## The Three Surfaces

| Surface | Commission item | Entry point | Dialog mode | List source | Backend |
|---|---|---|---|---|---|
| **Global browser** | (a) | chat-page header control, **beside** the shipped v0.31.0 room icon (`frontend/src/app/pages/chat/chat.html:76-99`) | `global` | `GET /api/rooms` (nest-wide) | **NEW** read-only route (D1) |
| **Per-instance joined popup** | (b) | instances-list row action cluster (pin/pause/terminate — `frontend/src/app/components/instance-list/instance-list.html:216-256`); appears in **both** hosts (chat sidebar `chat.html:8`, `/instances` page `instances.html:22`) | `joined` | the **same** `GET /api/rooms` response, filtered to rows whose `member_instance_ids` contains the row's instance id (D1.10, D3.3) | reuse (no second route) |
| **Conditional visibility** | (c) | the row button's `@if` | — | membership map derived from the same one response (D3.5) | reuse |

Shipped surface, deliberately **untouched** (constraint 4): the chat-page header icon (gate `@if (renderedInstance())`) and the `instance`-mode tree-scoped modal riding `GET /api/instances/{id}/rooms`.

---

## Scope

### In Scope

- **ONE** new read-only HTTP route: `GET /api/rooms[?instance_id=<scope-anchor>]` in `daemon/routers/rooms.py`, reusing `instances:read`, the existing `AuthMiddleware`, and the **shared** fail-closed read predicate (D1.1–D1.9).
- Repository/service additions that **reuse** the existing batched helpers (`list_active_member_instance_ids`, `_active_member_counts`, `_last_message_seqs`) — one session, no N+1 (D1.9).
- FE model + service layer: global-lane fetch, membership map, per-dialog-instance service posture (D3.4), bounded-polling lane (D2).
- `RoomDialogComponent` generalization into `instance` / `global` / `joined` modes (D3.2).
- Chat-page header control (a) + `chat.scss` family extension.
- Instances-list row button (b) + conditional gate (c) + `instance-list.scss` family extension.
- Tests at every layer: new BE router spec, extended FE specs, the drift-guard allowlist extension.
- The verification pack (jest / tsc / production build + manifest delta / BE legs / FE web automation) and the gates chain.

### Out of Scope (with reason)

| Item | Reason |
|---|---|
| An SSE-based room freshness channel | Rejected on evidence: the room channel is content-free by design (`daemon/services/live_event_hub.py:199-212`, `:244-253`), has **no** FE consumer (`frontend/src/app/services/sse.service.ts` → zero `room` matches), is structurally incomplete for a nest-wide browser (per-room subscriptions, `daemon/routers/messages.py:1101`), and is coupled to the open F-3 finding (D2.5). A **lifecycle invalidation** channel is **deferred**, not rejected forever (D2.6). |
| A second route `GET /api/instances/{id}/rooms/joined` (Candidate B) | Rejected: leaves item (a) unserved and needs a second global route anyway — strictly more backend surface (D1 rationale). |
| Any WATCHOVER row control on the instances list | No WATCHOVER control exists there (`components/instance-list/` → zero matches); creating one is an uncommissioned scope expansion (D3.6 / Q1). |
| A sibling `GlobalRoomBrowserComponent` | Duplicates the template, conversation pane, error states, geometry constant and service lanes; doubles the SCSS surface against the 8 kB per-component budget (D3.2). |
| A new lazy route / any `app.routes.ts` change | The surfaces are imperatively-opened dialogs reached from two already-lazy hosts (D4.3). |
| Any new npm dependency, any budget-constant change | Constraint 2 + D4.1/D4.4 — the budget is a house guard, not a knob. |
| Touching `sse.service.ts`, the house SVG renderer, watchover surfaces, `app.routes.ts` | Constraint 2 (house patterns untouched) — explicitly listed as no-change in the dispatch. |
| A live promote | Commission disposition: staged release only (constraint 5). |

---

## Phase Map

| # | Phase | Objective | Tasks | Coupling | Status |
|---|---|---|---|---|---|
| 1 | **Read surface + FE service layer** | `GET /api/rooms` (router + service wrapper + repo reuse, shared predicate core, no duplication) **and** the FE model/service lanes (global fetch, membership map, per-dialog-instance posture, bounded polling) | 10 | tight (contract) with P2, P3 | pending |
| 2 | **Surface (a) — header global browser** | Header control beside the shipped room icon; `global` dialog mode; specs + placement pins | 8 | tight with P1 (contract), tight with P3 (shared dialog) | pending |
| 3 | **Surfaces (b) + (c) — row button, joined popup, visibility gate** | Row button in the existing action cluster, `joined` mode, membership gate, drift-guard allowlist extension | 9 | tight with P1, tight with P2 | pending |
| 4 | **Gates, budget, no-regression evidence** | The full verification pack, marker/allowlist audit, census re-pin audit, gates chain, staged-release prep | 9 | loose (consumes P1–P3) | pending |

Dependency order is strict: **P1 → P2 → P3 → P4**. P1 lands the wire contract and the service lanes both surfaces consume; P2 and P3 both touch the same dialog component (serialized, single-owner per file); P4 verifies the assembled branch.

---

## Coupling Map

| | P1 | P2 | P3 | P4 |
|---|---|---|---|---|
| **P1** | — | tight — `GET /api/rooms` wire shape + `RoomService` lanes are the contract P2 codes against | tight — same contract + membership map + polling lane consumed by `joined` mode and the gate | loose — P4 re-runs P1's legs |
| **P2** | tight | — | tight — **shared file** `room-dialog.component.{ts,html,scss,spec.ts}`; single-owner per phase, serialized edits | loose |
| **P3** | tight | tight | — | loose |
| **P4** | loose | loose | loose | — |

**Single-ownership note (REJECT-grade if violated).** `frontend/src/app/components/room-dialog/*` is owned by **P2 only** for the generalization itself. P3 must **not** re-open those four files: the `joined` mode is *data-selected* (mode + filter, both established in P2), so P3's only dialog-side requirement is verification. If P3 discovers a genuine need to edit the dialog, it reports the need and P2's owner absorbs it — two phases claiming the same edit is a defect.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| **R1** | **Video-team pilot stability (constraint 4).** The pilot is LIVE on this release exercising room machinery. A regression in the chat page or in the shipped header icon breaks a live workflow. | High | Medium | (i) `instance` mode is **byte-behaviour-identical** — no list-source change, no polling added to it, no copy change (P2 tasks 1–3 keep the mode/behaviour, P2 task 8 pins the regression); (ii) the shipped header icon's `@if (renderedInstance())` block and `onOpenChatrooms()` are untouched (new control inserted **after** it, so `watchover-btn.nextElementSibling === room-btn` stays true — the existing pin at `chat.chatrooms.spec.ts:372` keeps passing); (iii) the chat page keeps its **timer ban** (`chat-send-error.spec.ts:82-88`) — the polling lane lives in the dialog/service only (D2.3); (iv) P4 evidence includes the must-stay-green spec set. |
| **R2** | **F-3 pending disposition (D1.7 / Q2).** The anchor-less nest-wide list is safe *today* only because the shipped messages route defaults its scope to the room OWNER (`daemon/routers/rooms.py:271-277`). If the leader tightens that route, the anchor-less list becomes a NEW title leak. | High | Medium | The route is built so the flip to anchor-only is a **one-parameter** change: module-level `_ALLOW_ANCHORLESS_GLOBAL_LIST = True` consulted in exactly one branch of the handler (P1 task 3), pinned by a test that flips the constant (P1 task 5). Q2 is carried to the leader **before** the route is cut; if the leader rules option (c), the flip lands in the same commit. **STATUS 2026-11-17 (leader ruling Q2): RESOLVED-PROCEED** — the route ships as planned with `_ALLOW_ANCHORLESS_GLOBAL_LIST` in the **same commit**; the messages-route scope tightening is a **disclosed pre-existing follow-up** (owner: leader-tracked queue) and this lane does **not** touch the messages route's default scope. |
| **R3** | **WATCHOVER-row contradiction (Q1 / D3.6).** The commission says "(next to the WATCHOVER button)" but the instances list has **no** WATCHOVER control. Building the wrong placement wastes a round; inventing a WATCHOVER row control expands scope. | Medium | High (the anchor does not exist) | Implement the ledger's recommendation: place the button in the **existing** row-action cluster (pin/pause/terminate) with `$event.stopPropagation()`, add **no** WATCHOVER row control. Placement is a one-line template move if the Emperor rules otherwise. Q1 carried in the open-questions section. **STATUS 2026-11-17 (leader ruling Q1): RESOLVED-ACCEPTED** — the existing row-action cluster placement is confirmed; **no** WATCHOVER row control is created; the final report to the Emperor carries the disclosure **and** the one-line move if overruled. |
| **R4** | **Budget / manifest conformance (constraint 2 + D4).** Budgets apply **only** in the production configuration (`frontend/angular.json:64-75`; the dev config carries none, `:77-82`) — a dev build proves nothing (AM-23). A second importer of `RoomDialogComponent` can **re-home** the dialog into a shared lazy chunk. | Medium | Medium | Mandatory production leg (`ng build --configuration production`, EXIT 0, **no WARN**; any WARN is a reported finding). Declared manifest delta in the stage-prep checklist: **ZERO initial-bundle growth**, one lazy chunk file added/re-homed, measured and reported (D4.3). Per-component SCSS kept under 8 kB; if crossed, split mode-scoped rules into a separate stylesheet — **never** raise the constant (D4.4). |
| **R5** | **Drift-guard markers / allowlist (D4.6).** The source-gc guard is **path-first**: a new `frontend/` file outside `FRONTEND_UI_LANE_SANCTIONED_FILES` is an offender **regardless of wording** (`tests/unit/repositories/test_source_gc_repository.py:784-806`, `:921-926`, `:968-980`). | High | High (P3 necessarily creates new FE hunks in unlisted paths) | P3 adds all four `frontend/src/app/components/instance-list/*` paths to the allowlist **in the same commit** that first touches them (maintenance rule A-P6-24 / F-1, `group-chat/decisions.md:514`), and every committed hunk carries a `room`/`chat` marker on its ± lines. P4 audits per-hunk markers + allowlist completeness + the `diff --git` flush rule. |
| **R6** | **Release-slot sequencing with other staging lanes.** `origin/latest` has **advanced 55 commits** past this lane's base (now `eca98397d`, the prompt_reload compact-note merge) and the **local `latest` ref lags 232 commits** (`cf43ac00d`, v0.30.12 lane). `stage.sh`'s tip-identity gate refuses `non-tip-tree` on a mismatch (`scripts/upgrade/stage.sh:220`; `scripts/upgrade/lib.sh:4243`, token at `:3387`). | High | High | (i) The cut **requires** a reconcile/merge onto the then-current `origin/latest` before staging (P4 task 9); (ii) the **local-latest fast-forward is owned by the `ari-stop-int` lane**, not this lane — recorded in `group-chat-release/release-plan.md:865`, `:888-892`; this lane **waits** for it rather than racing it; (iii) staging order versus other in-flight staging lanes (prompt_reload compact-note, and the Emperor's own ceremony queue) is sequenced by the leader at cut time — this lane's disposition stays **staged, NOT promoted**. |
| **R7** | **Gates chain (constraint 5).** Skipping or short-circuiting architect → reviewer council → fresh approver (or the subsequent code council / tidier / tester rounds "as warranted") invalidates the release. | Medium | Low (chain is explicit) | P4 task 8 dispatches the chain in order with the evidence pack attached; the staged release is cut **only** after the approver sign-off. No live promote. |
| **R8** | **Route-census pins.** Three sites carry the declared-route count; adding one route moves it (119 → **120**), and the test **names** embed the number. | Low | High (deterministic) | P1 task 8 updates `tests/unit/metrics/test_metrics_query_scope_cage.py:52` (+ node name `:96`, docstring `:51`) and `tests/unit/test_router_scope_manifest.py:466` (+ docstring `:455`); the mount census (40) and class-family count (36) stay **unchanged** (the `/api/rooms` family and its mount already exist — verified at `test_router_scope_manifest.py:198`, `test_router_class_manifest.py:170`). P4 re-audits all three sites + the `api_router` mount census (`tests/unit/test_post_restart_arm_notify_no_parallel.py`, A-P6-20) as **verify-only**. |
| **R9** | **Predicate pre-compute correction (C-1).** D1.5 says the list path pre-computes "the caller's ancestor set once". Taken literally that inverts the predicate: readable ⇔ **caller is an ancestor of the room owner** (equivalently **owner ∈ caller's descendant closure**). | Medium | High (a literal implementation would silently deny legitimate rooms — the safe direction — or, if "fixed" carelessly, widen it) | The single decision RULE is implemented once and both sites feed it; the list site pre-computes the caller's **descendant closure** (`InstanceRepository.get_tree_ids_permanent`, `daemon/repositories/instance/repository.py:838`, cap `_MAX_TRAVERSAL_DEPTH = 256` at `:50`) plus one batched membership set. Recorded as **C-1** below; substance of D1.5 (one core, two sites, O(1)/room) preserved. Direction stays fail-closed: a missing caller row ⇒ empty closure ⇒ only membership passes. |
| **R10** | **Cross-surface state cross-talk (D3.4).** `RoomService` is `providedIn: 'root'` with shared signal lanes (`frontend/src/app/services/room.service.ts:39-40`, `:44-68`); two simultaneously-open dialogs would stomp each other. | Medium | Medium | The dialog component declares `providers: [RoomService]` so each open gets a fresh lane set (P2 task 2). Verified safe: `RoomService` is injected **only** by the dialog today, and `room-dialog.component.spec.ts` drives everything through HTTP mocks + DOM (never by injecting the service instance), so component-level provisioning does not disturb the shipped specs. A regression spec proves two concurrent dialogs keep independent lanes (P2 task 7). |
| **R11** | **Timer leak / poll pile-up.** A polling lane that outlives its dialog, or a tick that stacks requests, degrades a live surface. | Medium | Low | Named constant `POLL_INTERVAL_MS = 10_000` (D2.2/D2.4), `inFlight` skip, monotonic supersede token, `clearInterval` on close **and** `ngOnDestroy`, `visibilitychange` stop/resume — each pinned by its own spec case (P1 task 10). |
| **R12** | **Membership-map staleness (D3.5).** The gate is a mount-time fact: a room joined while the list page is open does not add a button until remount. | Low | High (by design) | Recorded, deliberate simplicity (D3.5, "no polling for the gate"). Documented in the phase notes; a live membership feed is out of scope. |
| **R13** | **Instances-list scope surprise.** `app-instance-list` is consumed by **both** the chat sidebar (`chat.html:8`) and the `/instances` page (`instances.component.html:22`) — the row button therefore appears in both. | Low | High (by design) | Declared as **intended behaviour** in the phase plan and in the P4 evidence; both hosts are the same component with the same inputs, so no per-host branching is introduced. |
| **R14** | **Fail-closed vs honest-error tension at the gate (D3.5).** A failed membership fetch must hide the button yet not hide the failure. | Medium | Medium | Ruled: **fail-closed on the BUTTON, honest on the SURFACE** — empty map ⇒ button hidden; the surface shows an error + Retry. Pinned by two specs (hidden-on-failure; error state + retry present). |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|---|---|---|
| 1 | `GET /api/rooms` answers the binding wire shape | New BE router spec asserts the exact field set of each row (`room_id, name, tree_root_id, owner_instance_id, state, member_count, member_instance_ids, last_message_at, last_message_seq, created_at`) and the `{rooms: [...]}` envelope | Exact set equality (no missing, no extra) |
| 2 | Anchor-present scope is fail-closed | BE spec: an anchor that is neither an active member nor an owner-ancestor of room R ⇒ R **absent** from the list; a member and an owner-ancestor both see it | 0 leak rows in the denial case; both positive cases list R |
| 3 | Anchor-absent breadth equals the operator lane | BE spec: unlimited anchor lists every non-purged room (`active` + `closed`), never a purged one | purged rooms: 0 occurrences |
| 4 | The predicate is implemented ONCE | Code audit + spec: `_require_room_access` and the list path both route through the same decision rule; no second "members ∪ owner-ancestry" implementation exists in the diff | grep of the diff: exactly one decision-rule definition |
| 5 | Typed error vocabulary holds | BE spec: 404 `instance_not_found` (bad anchor), 404 `room_not_found`, 503 `room_service_unavailable` (faulted list), body shape `{detail:{error,message}}`, no raw fault text | 4/4 codes; leak scan 0 hits of fault text |
| 6 | One-parameter F-3 flip | BE spec that flips `_ALLOW_ANCHORLESS_GLOBAL_LIST` to `False` and asserts the anchor-less call is refused/served-empty per the ruling, with the constant restored in `finally` | Flip is 1 line, test green both ways |
| 7 | Global browser works from the header | FE spec: the new header control is a `.toggle-btn` sibling **inside** `.toggle-buttons`, adjacent to the shipped room icon, aria-labelled, always rendered (no instance gate), and opens `RoomDialogComponent` in `global` mode with `ROOM_DIALOG_SIZE` | DOM assertions + `dialog.open` call args |
| 8 | Joined popup works from either host | FE spec: clicking the row button on an instance with ≥1 joined room opens `RoomDialogComponent` in `joined` mode with that instance id, geometry = `ROOM_DIALOG_SIZE`; the click does **not** navigate (row is an `<a [routerLink]>`) | `dialog.open` called once; router navigation spy not called |
| 9 | Conditional visibility is exact | FE spec: instance with 0 joined rooms ⇒ **no** row button in the DOM (not disabled); instance with ≥1 ⇒ button present; failed fetch ⇒ button absent **and** an error state with Retry present | 3/3 cases |
| 10 | visible ≡ actionable (fix-G discipline) | FE spec: the gate and the click handler read the **same** signal; a click with the gate falsified is a no-op | One signal read by both (code audit) + no-op spec |
| 11 | Polling conforms to the house contract | FE spec: named `POLL_INTERVAL_MS = 10_000`; interval cleared on close/destroy; in-flight tick skipped; newer response wins over a slower older one; no polling while the tab is hidden | 5/5 pinned cases |
| 12 | The chat page still contains no timers | `chat-send-error.spec.ts` timer ban + a source grep | 0 matches of `setTimeout`/`setInterval` in `chat.component.ts` |
| 13 | Shipped surface unregressed | The must-stay-green set: `chat.chatrooms.spec.ts` (4 pins at `:372`, `:401`, `:432`, `:457`), `room-dialog.component.spec.ts`, `room.service.spec.ts`, `chat.component.spec.ts`, `app.component.spec.ts` ("BUG 3" initial-bundle pin `:1733-1745`), `app.routes.spec.ts` | All green, **no edit to the four shipped chat.chatrooms pins** |
| 14 | Type-check clean | `cd frontend && ./node_modules/.bin/tsc --noEmit -p tsconfig.app.json` | EXIT 0 |
| 15 | Production build within budget | `cd frontend && ./node_modules/.bin/ng build --configuration production` | EXIT 0 and **zero** budget WARN |
| 16 | Manifest delta declared | Build output diff vs the base build: initial bundle bytes, chunk file inventory | Initial growth 0 bytes (or a reported, justified figure); every added/re-homed chunk named in the stage-prep checklist |
| 17 | Drift guard green | `tests/unit/repositories/test_source_gc_repository.py` (span pin + compensator + tree-level allowlist) | Green; every `frontend/` hunk carries a `room`/`chat` marker; allowlist includes all four instance-list paths |
| 18 | Census pins coherent | The three pinned sites + the mount census | 120 / 40 / 36, mount census unchanged, `api_router` mount list unchanged |
| 19 | FE web automation passes | P6-gate §2-style leg (headless browser, fenced dev daemon): adjacency of the new header control, measured `82vw × 82vh` modal for `global` **and** `joined`, rooms-pane isolation (another tree's room not listed in `instance` mode), fail-closed empty + typed-error states | All rows PASS, measured values recorded |
| 20 | Gates chain complete | Architect adjudication → reviewer council → fresh approver → code council / tidier / tester as warranted | Sign-offs recorded; staged release cut; **no live promote** |

---

## Plan-Level Corrections & Adjudication Notes (correction carry-through)

**C-1 — D1.5's pre-compute direction (mechanical, substance preserved).** D1.5 says the list path pre-computes "the caller's ancestor set once". Taken literally, testing a room against that set answers "is the **owner** an ancestor of the caller", which is the **inverse** of the shipped predicate (shipped: `caller ∈ owner_ancestry_chain(room)` ⇔ caller **is** an ancestor of the owner, or is the owner — `daemon/repositories/room/repository.py:717-772`). The implementable equivalent one-shot pre-compute is therefore the **caller's DESCENDANT closure**, i.e. `InstanceRepository.get_tree_ids_permanent(caller)` (`daemon/repositories/instance/repository.py:838`, depth cap 256 at `:50`), with the per-room test `owner == caller or owner ∈ descendant_ids`. The **ruling's substance is untouched**: one decision rule, implemented once, two application sites, O(1) per room after two one-shot pre-computes. **Raised to the architect at adjudication** (P4 gate 1) rather than silently re-worded into the ledger.

**C-2 — polling scope vs. the shipped `instance` mode (plan-level ruling, flagged for adjudication).** D2.1 describes polling for "a room surface"; D3.2 requires the shipped `instance` mode to be **byte-unchanged behaviour**. This plan resolves the tension in the direction that protects constraint 4: the polling lane is **built** in P1 for all modes but **enabled only for `global` and `joined`** in P2/P3; `instance` mode keeps its current fetch-on-open behaviour (no new network traffic, no new timer). Enabling it for `instance` later is a one-line mode-gate change. **Flagged for the architect**, because it is a reading of D2 against D3.2.

**C-3 — dispatch anchor drift.** The dispatch placed `daemon/api.py` registration at `:6152`; verified: `daemon/api.py:119` (import) and `:6152` (`api_router.include_router(rooms_router)`). No change needed on this file. The dispatch's `chat.component.ts:1910-1924` onOpenChatrooms family verified at `:1910-1924`. The dispatch's note that `instance-list.html` action cluster lives at `:217-257` verified (`:216-256`). One dispatch-supplied anchor corrected: the drift-guard allowlist constant lives at `tests/unit/repositories/test_source_gc_repository.py:784-806` (not `test_source_gc_repository.py:771`, which is the markers block header region).

---

## Research Insights

- **The wire contract is binding and lives in two places** — `daemon/routers/rooms.py:11-30` (module docstring) and `tests/unit/routers/test_room_http_api.py:1-34` (exact field-set pins). The new route's spec follows the same shape, and the FE model mirrors it (`frontend/src/app/models/room.model.ts:1-30`).
- **The batched helpers already exist** — `_active_member_counts` (`repository.py:1274-1292`), `_last_message_seqs` (`:1294-1309`), `list_active_member_instance_ids` (`:1327-1341`); `list_rooms_for_tree` (`:1233-1272`) is the exact template for the nest-wide sibling (one session, extras batched after the row fetch).
- **Both error-envelope manifests already cover the family** — `/api/rooms` is pinned in the scope cage (`tests/unit/test_router_scope_manifest.py:198` → `instances:read`) and the class cage (`tests/integration/test_router_class_manifest.py:170` → `pd`). A new route **inside** the existing mount needs **no** manifest edit; only the declared-route census moves.
- **A test harness already exists** — `tests/helpers/room_harness.py` (`make_room`, `join_member`, `seed_instance`, `resolve_tree_root_id`, `make_room_engine`) makes anchor-present / anchor-absent / purged cases cheap to write.
- **The room service is already root-provided with requestId guards** (`frontend/src/app/services/room.service.ts:31-37`, `:56`, `:69`) — the supersede token the polling lane needs already exists; D2.4 explicitly reuses it.
- **The house polling exemplar is fully documented** — `overview-tab.component.ts:32-36` (contract comment), `:157` (named constant), `:166-168` (visibility gate), `:172-173` (seq token), `:227-251` (start/stop + in-flight), `:262-281` (stale copy). The plan copies the shape, not a paraphrase of it.
- **The shipped pins constrain DOM insertion order** — `chat.chatrooms.spec.ts:372` asserts `watchover-btn.nextElementSibling === room-btn`. The new header control must therefore be inserted **after** the shipped room-icon block, never before the watchover toggle.
- **`app-instance-list` has two hosts** — `chat.html:8` and `instances.component.html:22`; the row button lands in both by design.
- **The local `latest` ref is 232 commits behind `origin/latest`** (measured: `git rev-list --count cf43ac00d..origin/latest` = 232; `origin/latest` = `eca98397d`), and the tip-identity gate refuses `non-tip-tree` (`scripts/upgrade/lib.sh:4243`).

---

## Open Questions (carried verbatim from `decisions.md` — do not re-word)

**STATUS 2026-11-17 (leader ruling): RESOLVED-ACCEPTED.** Q1 is **closed** — the button goes in the **existing** row action cluster (pin/pause/terminate); **no** WATCHOVER control exists on the instances-list row (WATCHOVER is a chat-page header control), so the Emperor's "(next to the WATCHOVER button)" phrasing has **no row anchor**; the action cluster IS the row's control group. **FINAL-REPORT DISCLOSURE DUTY:** the report to the Emperor carries this disclosure **and** the one-line move if overruled (the placement is a one-line template move). *The question text below is preserved verbatim.*

**Q1 — D3.6 / D5 #6 — the row button's anchor.** The commission places the row button "(next to the WATCHOVER button)", but **the instances list has no WATCHOVER control** (WATCHOVER exists only on the chat-page header). **Recommendation:** place the chatroom row button in the **existing row-action cluster** (pin / pause / terminate) and **do not** create a WATCHOVER row control. **If the Emperor's intent was a different spatial position** (e.g. immediately left of the pin button, or a distinct visual cluster), the answer is a one-line template move — **no architectural consequence either way**. *Decision needed before the FE lane starts; nothing else blocks on it.*

**STATUS 2026-11-17 (leader ruling): RESOLVED-PROCEED.** Q2 is **closed** — `GET /api/rooms` proceeds exactly as planned (every row predicate-checked fail-closed via the shared `_require_room_access`); the `_ALLOW_ANCHORLESS_GLOBAL_LIST` knob ships **in the same commit** as the route; the F-3 messages-route scope tightening is routed as a **disclosed pre-existing follow-up** (owner: leader-tracked queue) and this lane does **not** touch the messages route's default scope. Rationale: the video-team pilot is live on this release (constraint 4); a mid-flight authz tightening on the messages route is exactly the regression class the commission forbids. The architect at gate 1 re-examines the **new** route's security posture, not the pre-existing one. *The question text below is preserved verbatim.*

**Q2 — D1.7 — the F-3 coupling (LEADER-SIGNIFICANT).** The anchor-less mode of the new nest-wide list is safe **only** because the shipped messages route defaults its scope to the room OWNER, letting an `instances:read` caller read any room's messages by id (`.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63`). The F-3 SSE pre-branch finding (`:72`) is **still open** for a disposition (`:83`). **Recommendation: settle F-3 BEFORE the new route is cut**, and land any tightening of the messages route **in the same change** as the list route. If F-3 is ruled to option (c) (mirror the read predicate), the list route's default **must** flip to anchor-only in that same commit — which is why D1.7 requires the default to be a **one-parameter** switch. *If the leader prefers to decouple, the fallback is: the route ships anchor-ONLY from day one (the FE then supplies a scope anchor), at the cost of the nest-wide browser needing a per-instance anchor — i.e. commission item (a) would need re-specification.* **This is the one question that can change the commission's outcome; it is raised rather than assumed.**

**STATUS 2026-11-17 (leader ruling): SETTLED-CONFIRMED.** Q3 is **closed** — a header control opening the global dialog directly; the "menu" is realized as the browser's room-list pane. No further adjudication required. *The question text below is preserved verbatim.*

**Q3 — D3.1 — the "HEADER MENU" reading.** The commission says the header "grows a HEADER MENU that opens the list of chatrooms + a DETAIL view of a selected room (its messages)". **Recommendation:** a header control that opens the **global browser dialog** directly, with the room list as the dialog's left pane and the conversation as its right pane — a Material menu cannot carry a message pane, and the commission's own detail requirement makes a dropdown the wrong container. **If the Emperor meant a literal dropdown menu** whose items open a second surface, the deviation is one component and one interaction step. *Raised for confirmation only; the recommendation is strong.*

### Additional questions raised by this plan (for the architect, at P4 gate 1)

**STATUS 2026-11-17 (leader ruling): ARCHITECT GATE-1 ITEM — leader leaning YES.** Constraint 4 (stability, byte-behaviour-identical shipped mode) outranks D2.1 breadth; the shipped `instance` mode stays **timer-free**. The leaning is recorded; the formalization remains open for the architect at gate 1. *The question text below is preserved verbatim.*

**Q4 — C-2 (polling scope for the shipped `instance` mode).** D2.1 vs D3.2/BYTE-UNCHANGED. This plan keeps `instance` mode timer-free; confirm, or rule that the shipped modal should poll too (then P2's byte-behaviour-identical claim becomes "polling added, rendering unchanged", and the regression pins must widen).

**STATUS 2026-11-17 (leader ruling): ARCHITECT GATE-1 ITEM.** Marked for architect adjudication at gate 1; the re-formulation is accepted **if technically sound** (the descendant-closure pre-compute; one decision rule, implemented once, two application sites). *The question text below is preserved verbatim.*

**Q5 — C-1 (pre-compute direction).** Confirm the descendant-closure formulation of the list-site pre-compute (D1.5's literal "caller's ancestor set" inverts the predicate).

---

## Links

- Commission verbatim: [`spec.md`](./spec.md)
- Ruled ledger: [`decisions.md`](./decisions.md) (§D1, §D2, §D3, §D4, §D5 + Open Questions)
- Upstream design record: `.agents/shared/planning/group-chat/architecture-recommendation.md:273-277` (§13.1), `.agents/shared/planning/group-chat/decisions.md:122-124` (D-S13), `:512`/:514 (A-P6-23 / A-P6-24)
- Release lineage: `.agents/shared/planning/group-chat-release/release-plan.md:865`, `:888-892` (local-latest ownership), `:870` (R12 — the dialog's own lazy chunk)
- P6 gate evidence: `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md` (§2 FE automation rows, §5 spot-checks `:63`/:72/:83)
