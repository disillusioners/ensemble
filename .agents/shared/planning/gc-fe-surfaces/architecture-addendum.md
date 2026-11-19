# Architecture Addendum — Gate-1 Adjudication (`gc-fe-surfaces`)

Date: 2026-11-17 · Adjudicator: Architect (gate 1 of the constraint-5 gates chain)
Lane: `gc-fe-surfaces` · task id `gc-fe-20261117` · branch `feature/gc-fe-surfaces`
Inputs adjudicated: plan set @ `099192116` + leader-rulings fold @ `edb8a84c8` (files re-read on disk; all load-bearing anchors re-verified at the worktree tip — anchors-are-leads rule applied).
Method: two read-only analysis workers (security-posture lane; structural-soundness lane), dispatched skill-less — the project skill bank carries **no** `security-design` / `structural-design` entries (the known 2026-10-11 bank gap; run flagged **DEGRADED — skill bank miss**; analysis frameworks were embedded in the dispatches). Worker instances: `5a60cb41` (security), `e4ef0d0f` (structural).

---

## VERDICT: **SOUND-WITH-AMENDMENTS**

The plan set is architecturally sound: the one-route design (D1), the polling ruling (D2), the generalized three-mode dialog (D3), the budget posture (D4) and the phase structure (P1→P4) all survive evidence-checking. **Fifteen amendments** are required below — **AM-1 and AM-2 are 🔴 must-fold blockers** (a truthfulness defect in the constraint-1 compliance artifact, and a refuted "specs unedited" claim that will red the shipped suite). All amendments are load-bearing and foldable by the planner without re-opening any leader ruling; none re-opens the pre-existing messages-route scope (F-3 follow-up stays leader-routed, untouched by this lane).

| Remit item | Outcome |
|---|---|
| 1. Overall soundness | **SOUND** — with AM-2/5/7/8/11/15 folded (spec-machinery, polling-mechanics, placement, docstring, disclosure gaps) |
| 2. Security posture of `GET /api/rooms` | **SOUND with a corrected disclosure** — AM-1 (blocker), AM-3/4/10/12/13/14 |
| 3. Q4 (timer-free `instance` mode) | **CONFIRMED** (leader leaning upheld; formalized below) |
| 4. Q5 (descendant-closure re-formulation) | **ACCEPTED — technically sound** (one narrowing edge disclosed; formalized below) |
| 5. Amendment pass | 15 amendments (2 🔴 blockers, 8 🟡, 5 🟢) |

---

## Q4 — FORMALIZED: the shipped `instance` mode stays TIMER-FREE (leader leaning CONFIRMED)

**Ruling: YES — the shipped `instance` mode ships with no timer, exactly as v0.31.0 behaves.**

Reasoning (evidence-cited):

1. **Zero behavioural delta by construction.** The shipped dialog fetches only in `ngOnInit` (`room-dialog.component.ts:90`) and on explicit `retryRooms()` (`:114`); no timer, no visibility listener exists in it today. Keeping it timer-free is *identical shipped behaviour*, which is precisely what constraint 4 demands and what D3.2's "byte-unchanged behaviour" claims.
2. **The mechanism is structurally clean.** The polling lane is **built in P1** (service-level, inert until `startPolling()`), **mode-gated in P2** (`ngOnInit` → `global`/`joined` only) — for `instance` mode *the timer object never exists*. The chat-page timer ban (`chat-send-error.spec.ts:88`) is untouched because the timer lives in the dialog/service lane (D2.3).
3. **Mechanically pinned.** P2-8 case (iii) asserts `instance` mode issues exactly `GET /api/instances/{id}/rooms`, no `GET /api/rooms`, and zero requests after 30 s of fake time; the shipped suite's `afterEach httpMock.verify()` (`room-dialog.component.spec.ts:66`) plus per-case `expectOne` pins make any extra request red.
4. **Cheaply reversible, deliberately deferred.** Enabling polling for `instance` later is a one-line mode-gate change at the P2 `ngOnInit` branch plus widening P2-8 case (iii) — recorded in C-2; nothing locks the decision in.
5. **The staleness cost is the status quo, not a regression.** An open `instance`-mode modal never refreshes until reopened — that is exactly the shipped v0.31.0 behaviour the live video-team pilot already runs under. The new modes do *better*; the shipped mode does not change. (The asymmetric freshness between the row popup and the shipped modal is real and must be *disclosed* — AM-15 — but it is not a reason to break byte-identity on a live pilot surface.)

Constraint 4 (stability) outranks D2.1 breadth, exactly as the leader leaned. **Q4 CLOSED.**

---

## Q5 — ADJUDICATED: the descendant-closure re-formulation is ACCEPTED (technically sound)

**Ruling: ACCEPT.** D1.5's literal "caller's ancestor set" wording did invert the predicate; the C-1 re-formulation (pre-compute the caller's **descendant closure**; per-room test `owner == caller or owner ∈ descendant_ids`) is the correct implementable equivalent. Verification results:

1. **The hinge — `get_tree_ids_permanent` is CALLER-anchored, not tree-root-anchored.** `daemon/repositories/instance/repository.py:838-903`: `visited=[root_id]`, then children selected by `Instance.parent_id == current_id` — it walks **downward from the passed id and never climbs**. `get_tree_ids_permanent(caller_id)` = `{caller} ∪ descendants(caller)`: no siblings, no ancestors. (The parameter name `root_id` is a traversal-start name; the upward root resolver is a different function, `get_tree_root_id` `:769-787`.) The plan's per-room test is therefore sound **as written** — no replacement walk needed. *Implementation hygiene: pass the caller id positionally or via a named local so no reader re-imports the `root_id` intuition (fold into AM-13).*
2. **Equivalence holds on live data.** Both walks traverse the same relation (`instances.parent_id`, single-valued ⇒ ancestor/descendant are inverse relations on the same forest); depth caps are **symmetric** (up-walk ≤256 ancestors, `room/repository.py:134/:727-737`; down-walk ≤256 BFS levels, `instance/repository.py:50/:869-880`); cycle behavior agrees.
3. **Membership parity is exact.** Composite PK `(room_id, instance_id)` (`room/models.py:299-318`) ⇒ at most one membership row per pair ⇒ the batched ACTIVE filter (`room/repository.py:1334-1336`) ≡ the shipped `member.state == ACTIVE` test (`:762-763`); LEFT rows excluded on both sides; the two branches are independent and OR-ed — the shared rule `is_active_member OR owner_ancestry_ok` (false ⇒ deny) is faithful.
4. **Fail-closed direction verified.** Unknown caller id ⇒ `get_tree_ids_permanent` returns `[]` (`:863-866`) ⇒ empty closure ⇒ membership-only. Missing anchor never widens.
5. **ONE divergence — narrower, never wider (must be disclosed + pinned):** if an intermediate ancestor row was **hard-deleted** (instance rows can be: `instance/repository.py:3306/:3580/:3709`), the shipped up-walk still admits that caller id (it reads `parent_id` from the child's row then breaks, `room/repository.py:725-737`), while the closure form returns `[]` and **denies**. Direction = fail-closed; unreachable via HTTP (the router 404s a missing anchor, `rooms.py:139-144`; `manager.get_instance` raises `KeyError`, `daemon/manager.py:16336-16351`). → **AM-10**.

**Q5 CLOSED (accepted).** D1.5's substance — one decision rule, implemented once, two application sites, O(1)/room after two one-shot pre-computes — is preserved.

---

## SECURITY POSTURE — `GET /api/rooms[?instance_id=]` (the ONE new route)

### S-1. Shared predicate, anchor-present mode — **SOUND**

- Shipped predicate confirmed (`daemon/repositories/room/repository.py:740-772`): readable ⇔ **room row exists AND (caller is ACTIVE member OR caller ∈ owner-ancestry chain)**; resolution order room→membership→ancestry; all-miss ⇒ typed `RoomNotFoundError`. Precision the plan's restatement must carry: the **"room row exists" conjunct** (absent/purged rooms never reach either branch). A LEFT member falls through to the ancestry branch (not short-circuited) — parity requires the shared rule preserve this.
- Extracting the decision core into one helper re-used by `_require_room_access` and the list path satisfies constraint 1's "shared code, never duplicated" exactly. **No second implementation may appear in the diff** (P1-2 acceptance already audits this).
- Anchor handling fail-closed: bad/missing anchor ⇒ 404 `instance_not_found` (house precedent `rooms.py:139-174`).

### S-2. Anchor-less semantics + the corrected new-information analysis — **accept with AM-1 (🔴 blocker)**

The leader's Q2 ruling (PROCEED; anchor-less breadth justified by the operator lane's existing message-read breadth) stands on **verified ground**: `daemon/routers/rooms.py:271-277` resolves the messages route's default scope to the room **owner**, who trivially passes the predicate ⇒ **today an `instances:read` principal can already read ANY non-purged room's messages by id, cross-tree** (corroborated: `.agents/tester/RESULTS/2026-11-16-group-chat-p6-tester-gate.md:63`). Purged rooms 404 (purge = row deletion; child FKs carry no `ondelete=`, `room/models.py:31-36`).

**But the plan's disclosure names the wrong new-information class** (AM-1):

- Room **ids, names, state, timestamps, member_count were ALREADY discoverable** by the same principal today — `GET /api/instances` (`instances:read`, paginated) enumerates the instance universe, and `GET /api/instances/{id}/rooms` (`rooms.py:122-207`) returns every tree's rooms. D1.6's "adds DISCOVERABILITY of room ids and titles" **overstates the delta**.
- The **genuinely NEW class is `member_instance_ids` — the ACTIVE member roster, i.e. a nest-wide "who chats with whom" relational graph.** No HTTP surface exposes it today: the tree route returns `member_count` only (`rooms.py:194-206`); the messages payload exposes only per-message *authors* (`rooms.py:314-327` — a silent member is invisible, a LEFT member absent); `list_active_member_instance_ids` (`room/repository.py:1327-1341`) has **no HTTP caller** (GC sweeper + tests only); rosters are exposed only on the agent tool lane (participant-gated).
- Severity 🔴 because D1.11 is the verbatim constraint-1 compliance artifact the Emperor reads; as written it both **understates** (roster undisclosed) and **overstates** (ids/titles "new"). The corrected disclosure: *the anchor-less list's new information is the active membership relation; room identifiers and metadata were already enumerable; message content was already readable. The roster is strictly less than the content the same principal can already read, and it is load-bearing for surfaces (b)/(c) — but it IS a new disclosure class and is named as such.*

`member_instance_ids` **minimization is NOT recommended**: the one-response design (b)+(c) is functionally dependent on it (D1.3/D3.5), and the roster is strictly less sensitive than the messages the same principal can already read. Corrected disclosure, not row-shape surgery.

### S-3. The `_ALLOW_ANCHORLESS_GLOBAL_LIST` knob — **right shape, three tightenings**

- Default `True` is **load-bearing and correct**: both the global browser (P2-6, no query) and the membership gate (P3-1, `fetchAllRooms(null)`) call anchor-less; `False` breaks commission items (a) and (c). Same-commit shipping per leader ruling Q2 is consistent.
- **AM-3:** the knob-OFF refusal shape is currently unspecified ("refused/served-empty per the ruling" — the ruling is silent). **Ruled here: 404 `instance_not_found`** (inside the closed D1.8 vocabulary; the exact precedent of the tree route's unresolvable-scope 404, `rooms.py:157-174`). **Empty-200 is BANNED** (indistinguishable from "no rooms" — breaks FE honesty) and **403 `SCOPE_INSUFFICIENT` is BANNED** (misattributes — the token does hold `instances:read`). FE degraded posture pinned: global dialog → error+Retry; row button → hidden + honest surface error (D3.5).
- **AM-12:** a grep-pin that **every** anchor-less branch consults the constant (the flip test flips it; the pin proves no second anchor-less path can fail open), plus a lane-record note that the knob is a **code-level switch**, not the DB-backed `settings:write` kill channel (`daemon/routers/settings.py:837/:969`) — the F-3 follow-up owner ships a code change. Flip test must not run under parallel pytest workers (process-global mutation).

### S-4. Bounds — **bind the list now (AM-4)**

Purge and closed-room GC bound the resident set better than the plan knew (closed rooms purge at **closed+14 d**, `daemon/services/room_gc.py:89-90`, sweeper registered unconditionally at 6 h, `daemon/manager.py:4679-4683`; active rooms capped **8/tree**, `room/membership.py:126`) — but growth remains **unbounded in tree count**, the payload scales as rooms × roster, and the wire contract is declared **BINDING** (retrofitting a cap later = contract change). House precedent is mixed (tree route unbounded but per-tree; messages route bounded; instances list bounded with `truncated`). **Ruling: `limit` param (default 200, server-clamped hard max 500 — implementer-tunable within that shape) + `truncated: bool` in the envelope**, mirroring `InstanceListResponse.truncated` (`daemon/models/instance.py:139-149`) and the `MAX_PAGE_LIMIT` clamp pattern (`daemon/routers/instances.py:486`). Ordering stays `(created_at, room_id)`.

### S-5. Scope wiring — **VERIFIED, no new lane**

`instances:read` is the existing family scope (`rooms.py:95/:124/:212`; family already declared in the scope manifest, `tests/unit/test_router_scope_manifest.py:198` — **no manifest edit needed**, only the two declared-route count pins move 119→120, both verified at tip: `test_metrics_query_scope_cage.py:52/:96`, `test_router_scope_manifest.py:466`; mount census 40 and family count 36 unchanged). Scope machinery: inert when auth OFF, fail-closed 403 when ON (`daemon/auth/decorators.py:88-112`).

---

## STRUCTURAL SOUNDNESS — findings that drive amendments

- **Surfaces (a)/(b)/(c) land as planned** — header structure, adjacency pin (`chat.chatrooms.spec.ts:372` asserts `nextElementSibling`), `.room-btn` query key, action cluster, `ROOM_DIALOG_SIZE`, both instances-list hosts: all verified. Two placement facts the plan must add: the action cluster is **absolutely positioned** (`right:8/40/72px` for terminate/pause/pin, `instance-list.scss:503-580`) ⇒ the new button needs an explicit `right: 104px` (**AM-8**); and the cluster anchors are `:212-257` with the nested-button rationale at `:161-163` (drifted citations — **AM-6**).
- **SSE-vs-polling: ruling CORRECT, evidence list needs one correction.** Reasons 1–3 verified (content-free channel by design, `live_event_hub.py:199-212/:237-253`; zero FE room consumers; per-room subscriptions structurally incomplete for a nest-wide browser). Reason 4's premise is **stale**: the SSE pre-branch finding is **CLOSED at this lane's base** (A-P6-26, option b — `5d13a1c6c`+`652203f95`, both ancestors of `0181dadbf`; `live_event_hub.py:41-56`). D2.5 reason 4, D2.6 and the D1.6/Q2 parentheticals must be corrected (**AM-6**); the ruling stands on reasons 1–3. The messages-route **default-scope** follow-up remains real and leader-routed (distinct finding — the plan's own label correction at `decisions.md:66` already separates them).
- **Polling contract: exemplar + guards verified; two mechanics gaps.** `refreshOpenRoom()` as specified **cannot compose its URL** — `openRoom(roomId)` discards the id (`room.service.ts:140-143`) and the P1-9 signature takes none; the tick must also skip when no room is open and state its `nextAfterSeq` cursor policy (advance vs deliberate freeze — a frozen exhausted cursor freezes `loadMore`). The `loadMessages(...,true)` no-op claim is verified (`:153/:166`). → **AM-5**.
- **Dialog generalization: credible and pinned — one refuted claim.** Byte-identical `instance` mode is real (per-case `expectOne` + `afterEach httpMock.verify()`), **but** `providers: [RoomService]` (D3.4) breaks shipped case `room-dialog.component.spec.ts:258-288`: `TestBed.inject(RoomService)` resolves the root instance while the component holds a component-scoped one — the supersede-guard case reds. The fix **re-points** that one case at `fixture.debugElement.injector.get(RoomService)` (strengthening, not loosening); the plan's "existing cases green UNEDITED" must be re-worded for that case. → **AM-2 (🔴 blocker)**. Also: D3.4's "exactly one production consumer" is a base-time fact — P3 makes it two (root singleton for the gate, component-scoped for dialogs — which is exactly the intended split; record it).
- **P3-7's six DOM cases are unscheduled work.** `instance-list.component.spec.ts` (1,284 lines, 84 cases) is a mirror-class + raw-HTML-regex spec with **zero TestBed** — DOM assertions need a component harness (DI: InstanceService, prefs, TabState, ApiService, MatDialog, Router, HttpClient) or a new spec file **+ its allowlist row in the same commit**; P3's "Create: none" contradicts its own acceptance. → **AM-7**.
- **Budget/chunk posture: VERIFIED** (production-only budgets `angular.json:64-75`; 26 lazy routes, 0 eager; no preloading; chat host dynamic import + BUG-3 pin; `instance-list.scss` 15,813 B pre-existing warn posture — base-attribution plan in P4-1 is correct; `room-dialog.component.scss` 5,221 B with ample headroom).
- **Phase structure: single ownership CLEAN, symbol ordering SOUND, census grounding EXACT at tip.** 14 modified + 1 created file, each owned by exactly one phase; `RoomDialogMode` (P2) precedes P3's use; P1 symbols precede both consumers; knob same-commit-as-route. Two acceptance-reachability gaps are AM-2/AM-7. Also: the rooms router docstring's "TWO ROUTES" header (declared BINDING, `rooms.py:11-30`) becomes false when P1-3 lands — update it in the same change (**AM-11**).
- **Disclosures to add:** the freshness asymmetry (row popup polls at 10 s; the same instance's shipped modal never refreshes) and R12's "remount" on the chat page meaning **browser reload** (the chat host is created once per session and never destroyed, `app.ts:361-376/:391-400`) (**AM-15**); the room-repo→instance-repo coupling and the O(subtree)-SELECT shape of the closure pre-compute (**AM-13**); the no-scope-manifest-edit fact (**AM-14**).

---

## AMENDMENTS (fold by the planner; all load-bearing)

| # | Sev | Amendment | Fold where |
|---|---|---|---|
| **AM-1** | 🔴 | **Correct the constraint-1 disclosure (D1.6/D1.4/D1.11).** The new information class is the **ACTIVE member roster** (`member_instance_ids` — a nest-wide who-chats-with-whom graph; no HTTP surface exposes it today). Room ids/names/state/timestamps/member_count were **already discoverable** (`GET /api/instances` → `GET /api/instances/{id}/rooms`). Name both directions truthfully. *Gate-1 ruling: D1.11 is lane-authored disclosure prose (only `spec.md` is verbatim-faithful) — edit it directly; constraint 1 requires the disclosure be truthful.* | decisions.md D1.6/D1.4/D1.11 + final-report paragraph |
| **AM-2** | 🔴 | **Re-point, don't loosen, `room-dialog.component.spec.ts:258-288`** at the component's own injector (`fixture.debugElement.injector.get(RoomService)`) once `providers: [RoomService]` lands; re-word the plan's "existing cases green UNEDITED" for that single case ("re-pointed, never weakened"). The shipped assertion reds *as written* (root vs component-scoped instance) while the guard it proves remains correct. | P2 task 8 + plan-overview S-criterion 13 wording |
| **AM-3** | 🟡 | **Fix the knob-OFF refusal shape: 404 `instance_not_found`** (closed D1.8 vocabulary; precedent `rooms.py:157-174`). Ban empty-200 (indistinguishable from "no rooms") and 403 (misattributes scope). Pin FE degraded posture: global dialog error+Retry; row button hidden + honest surface error. | P1 tasks 3/5; decisions D1.8; success criterion 6 |
| **AM-4** | 🟡 | **Bound the anchor-less list**: `limit` param (default 200, server-clamped max 500; implementer-tunable shape) + `truncated: bool` envelope flag, mirroring `InstanceListResponse.truncated` and the `MAX_PAGE_LIMIT` clamp. Ordering `(created_at, room_id)` unchanged. The contract is BINDING — retrofitting later is a contract change. | D1 wire contract + P1 tasks 1/3/5 + FE normalizer (tolerates extra fields) |
| **AM-5** | 🟡 | **Close the polling mechanics gaps**: give the service the open room id (`openRoomId` state set in `openRoom`, or `refreshOpenRoom(roomId)`); the tick **skips** (no error) when no room is open; state the `nextAfterSeq` cursor policy explicitly (advance on merge, or deliberate freeze — never accidental). | P1 task 9 + task 10 cases |
| **AM-6** | 🟡 | **Stale/drifted citation pass**: (i) D2.5 reason 4, D2.6, and the D1.6/Q2 parentheticals cite the SSE pre-branch finding as "open" — it is **closed at base** (A-P6-26; `5d13a1c6c`+`652203f95`; `live_event_hub.py:41-56`); the polling ruling rests on reasons 1–3; (ii) `instance-list.html` cluster is `:212-257`, nested-button rationale `:161-163` (not `:213-215`); (iii) BUG-3 describe block at `app.component.spec.ts:1750`; (iv) `test_router_class_manifest.py` lives under `tests/integration/`. | decisions.md + phase plans |
| **AM-7** | 🟡 | **Schedule P3-7's spec machinery**: the current mirror-only spec cannot make DOM assertions — either add the TestBed harness work to P3's tasks, or create a new spec file **and add its allowlist row in the same commit**; reconcile "Create: none". | P3 tasks 7/Files |
| **AM-8** | 🟡 | **Give `.rooms-btn` explicit placement**: the action cluster is absolutely positioned (terminate `right:8px`, pause `40px`, pin `72px`) — the new button needs `right: 104px` (plus any row `padding-right` compensation); DOM-first carries no layout here. | P3 tasks 3/5 |
| **AM-9** | 🟡 | **Pin `joined` mode's anchor posture** (currently undecided; both correct but the URL differs). **Recommendation: ANCHORED** (`GET /api/rooms?instance_id=<row id>`) — exercises the anchor-present predicate path in production and narrows the wire; the FE membership filter is unchanged (anchored rows ⊇ joined rooms). Whatever is chosen, P1-10's URL-shape cases and P2/P3 data flow must agree. | P1 task 9/10; D3.3 |
| **AM-10** | 🟡 | **Disclose + pin the hard-deleted-ancestor narrowing**: the closure form *denies* a caller whose intermediate ancestor row was hard-deleted (shipped chain admits); direction fail-closed, never wider; unreachable via HTTP (anchor 404). One spec leg + one disclosure line. | P1 task 5; plan-overview C-1/R9 |
| **AM-11** | 🟢 | **Update the `rooms.py:11-30` "TWO ROUTES" binding docstring** in P1's same change (it becomes three). | P1 task 3 |
| **AM-12** | 🟢 | **Single-branch grep-pin**: every anchor-less branch consults `_ALLOW_ANCHORLESS_GLOBAL_LIST` (flips fail closed, never open); record the knob is a code-level switch (not the `settings:write` kill channel); flip test runs serially with `finally` restore. | P1 task 5 |
| **AM-13** | 🟢 | **Name the coupling + query shape in P1-2's acceptance**: room-repo → `InstanceRepository` (same engine — construct from the manager's single engine); `get_tree_ids_permanent` is O(subtree) SELECTs (one call, N queries — still ≪ N_rooms × depth walks); pass the caller id positionally (avoid the `root_id` misreading). | P1 task 2 |
| **AM-14** | 🟢 | **Record: no scope-manifest edit needed** (family `/api/rooms` already declared → `instances:read`, `test_router_scope_manifest.py:198`); only the two declared-route count pins move (119→120). | P1 task 6/7 |
| **AM-15** | 🟢 | **Disclose the freshness asymmetry + remount semantics**: row popup polls at 10 s while the same instance's shipped modal never refreshes; on the chat page "remount" = browser reload (host created once per session). | decisions D1.10/D3.5; plan-overview R12 |

**Not amendments (checked, no action):** row-shape minimization of `member_instance_ids` (rejected — load-bearing for (b)/(c); see S-2); moving the knob to the settings surface (out of lane; recorded in AM-12 instead); any change to the pre-existing messages route (leader-routed, untouched).

---

## DEGRADATION NOTE

Run flagged **DEGRADED — skill bank miss (`security-design`, `structural-design`)**: the project skill bank carries none of the architecture execution skills (the standing 2026-10-11 critical-note gap). Both workers were dispatched skill-less with analysis frameworks embedded in the dispatch prompts; both confirmed `NO SKILL LOADED` as their first line and applied the embedded frameworks. Evidence quality was unaffected — every verdict above is code-anchored and was spot-re-verified at the worktree tip.

## EVIDENCE INDEX

- Worker A (security-posture lane): instance `5a60cb41-d0b8-4f2e-809a-786fc10f262c` — predicate semantics (`room/repository.py:740-772`, chain `:717-738`, cap `:134`), closure verification (`instance/repository.py:838-903`, `:769-787`, `:50`), operator-lane breadth (`rooms.py:271-277`), roster exclusivity (`rooms.py:194-206`, `:314-327`; `room/repository.py:1327-1341`), bounds evidence (`room_gc.py:89-90`, `manager.py:4679-4683`, `room/membership.py:126`, `models/instance.py:139-149`, `routers/instances.py:486`), scope wiring (`rooms.py:95/:124/:212`, `auth/decorators.py:88-112`, manifest `:198`).
- Worker B (structural lane): instance `e4ef0d0f-337b-4907-a7fb-2024a5f408c3` — surface anchors (`chat.html:62-123`, `chat.chatrooms.spec.ts:349-351/:372/:392`, `instance-list.html:161-163/:212-257`, `instance-list.component.ts:78/:296-297/:326-327/:418-419`, `room-dialog.component.ts:14-19/:26-34/:67/:90/:114`, `styles.scss:59-88`), SSE evidence (`live_event_hub.py:41-56/:199-253`, `sse.service.ts` zero matches, `messages.py:1101`), polling exemplar (`overview-tab.component.ts:157-280`, `room.service.ts:56/:69/:140-143/:153/:166/:211-221`), spec-machinery findings (`room-dialog.component.spec.ts:66/:258-288`, `instance-list.component.spec.ts:49/:1243-1282`), budget/census (`angular.json:64-82`, `app.routes.ts:134-226`, `app.ts:391`, `app.component.spec.ts:1733-1749`, `test_metrics_query_scope_cage.py:52/:96/:109/:120`, `test_router_scope_manifest.py:198/:339/:455-466`, `tests/integration/test_router_class_manifest.py:170`), A-P6-26 closure (`5d13a1c6c`, `652203f95` ⊆ `0181dadbf`).
