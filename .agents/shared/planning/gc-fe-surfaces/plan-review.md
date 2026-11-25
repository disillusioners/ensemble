# PLAN REVIEW — `gc-fe-surfaces` (group-chat FE fast-follow)

| Field | Value |
|---|---|
| Date | 2026-11-17 (reviewer council gate, per constraint 5) |
| Reviewer | reviewer (v2) controller — dispatch-only; all analysis by dispatched reviewers below |
| Review tracks | **Council** (governor `8c480dd7`; 2 councilors, models `agentic` + `coding`, skill `plan-review`) · **FE worker** (`20ace26e`, skill `code-review`) · **BE/security worker** (`64d39ffa`, skill `security-review`) |
| Target | Plan set @ `8fd8e6f83` (stack `099192116 → edb8a84c8 → 09ec8f9e0 → 8fd8e6f83`), base `0181dadbf` (v0.31.0) |
| Method | Read-only. Anchors verified at use time against the worktree. No file outside this review was modified. |

## VERDICT: **APPROVE-WITH-CONDITIONS**

**Counts: 4 CRITICAL · 14 WARNING · 16 ADVISORY (34 unique findings from 51 raw across three tracks).**

Rationale: the plan set is substantively sound — both gate-blocking amendment folds (AM-1, AM-2) are **genuinely folded and code-verified**; the predicate/polling/budget rulings survive evidence-checking; scope discipline is clean; single-ownership and symbol-ordering invariants hold; the fail-closed predicate direction is never widened anywhere in the set. The council was unanimous (2/2) on APPROVE-WITH-CONDITIONS; the FE track returned "verified with mandatory corrections"; the BE track returned "sound-with-fixes". **No finding re-opens a leader ruling; none invalidates the one-route / three-mode / bounded-polling / zero-dep design.** The conditions are the fold of all CRITICAL and WARNING findings below — a single planner pass over `plan-overview.md`, `decisions.md`, and the four phase plans. No re-plan.

**Council composition note:** the dispatch named a third model (`coding2`) not present in this deployment's allowed-models list; the governor ran the council at 2 models. This is a genuine two-source multi-model synthesis; no degraded-confidence caveat applies.

---

## 1. Amendment fold verification (AM-1 … AM-15, synthesized across all three tracks)

Gate-blockers first, plainly: **AM-1 VERIFIED FOLDED · AM-2 VERIFIED FOLDED.**

| # | Verdict | Note |
|---|---|---|
| **AM-1** 🔴 | **VERIFIED FOLDED** | D1.11 rewritten; names `member_instance_ids` (ACTIVE roster) as *the* new class in both directions. Code-verified independently by the BE track: no HTTP caller of `list_active_member_instance_ids` (callers = `room_gc.py:396`, `room/service.py:505-510`, tests); tree route returns `member_count` only (`rooms.py:194-206`); messages payload exposes per-message authors only (`:314-327`). |
| **AM-2** 🔴 | **VERIFIED FOLDED** (one residue → W-1) | Re-point at `fixture.debugElement.injector.get(RoomService)` carried in D3.4 + P2 task 8/Tests/No-Regression + criterion 13. The FE track confirmed the re-point is *technically correct, not merely defensible*: the case at `:258-288` genuinely reds at `:283`/`:287` under `providers:[RoomService]`, and the component-injector form is the minimal fix. |
| AM-3 | VERIFIED FOLDED | 404 `instance_not_found`; empty-200 and 403 banned; FE degraded posture pinned. Advisory A-3 tightens the message text. |
| **AM-4** | **PARTLY FOLDED → C-1** | Bound + flag present, but the anchored-path ordering, the truncation mechanism, the FE surfacing of `truncated`, and the clamp constant are unpinned/mis-cited. |
| AM-5 | VERIFIED FOLDED | `openRoomId`, skip-when-no-room, explicit cursor policy (D2.4; P1 tasks 9/10; criterion 22). |
| **AM-6** | **PARTLY FOLDED → W-4** | Folded at D1.6/D2.5/D2.6/R2/P1-7/P4-7; closure verified true. Six residual sites survive. |
| **AM-7** | **PARTLY FOLDED → C-3 / W-2** | Substance present (new spec file + harness + same-commit allowlist row + criterion 24), but P3's own Tests block and verification command still target the mirror-only spec; allowlist count wrong. |
| AM-8 | VERIFIED FOLDED (caveat → W-12) | `right: 104px` pinned; all three cluster SCSS anchors verified exact (8/40/72 px, 32 px pitch). Child-row geometry unaddressed. |
| AM-9 | VERIFIED FOLDED (caveat → W-11) | Anchored `joined` posture carried consistently in D3.3/D1.10/D3.5/P1-9/P2-3/P3-1/criterion 23. Superset true at the wire; lacks a BE pin. |
| **AM-10** | **PARTLY FOLDED → W-3** | Fold present in six places, but the disclosed edge is **mis-aimed**; the pinned spec leg cannot demonstrate the divergence as worded. |
| AM-11 | VERIFIED FOLDED | "TWO ROUTES" → three in P1's same change. |
| AM-12 | VERIFIED FOLDED (caveats → A-1, A-2) | Single-branch + grep-pin + code-level-switch note; serial/restore mechanics should use the house idiom. |
| AM-13 | VERIFIED FOLDED (caveat → A-6) | Single engine, O(subtree), positional caller id. Coupling shape has two acceptable variants. |
| AM-14 | VERIFIED FOLDED | Family map needs no edit; only the two count pins move 119→120 — verified at tip (`test_router_scope_manifest.py:198/:466`, cage `:52/:96`, 40 mounts, 36 families; class manifest correctly under `tests/integration/`). |
| AM-15 | VERIFIED FOLDED | Freshness asymmetry + "remount = browser reload" (verified: no `.destroy()` path for the chat host in `app.ts`). |

**Tally: 11 VERIFIED · 4 PARTLY · 0 NOT FOLDED.**

---

## 2. CRITICAL findings (fold before the approver gate)

### C-1 · The BINDING `GET /api/rooms` contract is under-specified on the anchored path (AM-4 residue)
**Anchors:** `decisions.md` D1.1-a; `phase1-plan.md` tasks 1/2/3/8; `plan-overview.md` criterion 21. **Sources:** council C1 [A1∪B6] + BE F2 + BE F11.

Four compounding gaps:
- **(a) Clamp-vs-filter ordering unpinned.** Clamp-then-filter on the anchored path silently drops a joined room created past the page boundary — corrupting items (b) and (c) (the `joined` popup and the visibility membership map report an instance as room-less when it is not). Filter-then-clamp is the unbounded nest-wide scan AM-4 exists to prevent. The plan states the rule for one method only (`phase1-plan.md:13`, anchor-absent) and is silent for the anchored path (`:14`).
- **(b) `truncated` mechanism unstated.** The house pattern is the limit+1 probe (`daemon/routers/rooms.py:290-293`).
- **(c) The FE normalizer is told to *tolerate* `truncated` and never render it** (P1 task 8) — no surface ever tells the operator truncation happened.
- **(d) The clamp citation mis-cites the constant.** "Mirroring the `MAX_PAGE_LIMIT` clamp pattern" (`daemon/routers/instances.py:486`) — but `MAX_PAGE_LIMIT` is **100** (`daemon/constants.py:19`), not the declared 500. An implementer reaching for the cited constant silently caps the list at 100.

**Fold:** in D1.1-a, one sentence per path pinning the ordering — anchor-absent: clamp **before** batched extras, `truncated` = clamp cut candidates; anchor-present: `(created_at, room_id)` scan → per-room predicate → clamp, `truncated` = **readable** rooms omitted (or, if clamp-then-filter is chosen for cost, state plainly that an instance whose joined room falls outside the page reports as having no joined rooms, and carry that into the disclosure). Pin the mechanism: "fetch `limit+1` ordered rows, drop the extra, `truncated = len(rows) > limit`". Name the bound as a NEW local constant (`ROOM_LIST_MAX_LIMIT = 500`) with an explicit note that `MAX_PAGE_LIMIT` = 100 is a shape precedent only (add a `limit=101` test leg proving no inheritance). Make P1 task 8 retain **and surface** `truncated` (global browser "more rooms not shown" state; instances-list gate honest warning). Split criterion 21 into anchor-absent / anchor-present rows.

### C-2 · Criterion 15 ("EXIT 0 and **zero** budget WARN") is unsatisfiable at this lane's own base
**Anchors:** `plan-overview.md` criterion 15 vs R4 and `phase4-plan.md` task 5; `decisions.md` D4.5 leg 3. **Sources:** council C2 [A7∪B1] + FE F-1.

Measured at base: `instance-list.scss` = **15,813 B source / ~10.3 kB compiled+minified** against `anyComponentStyle.maximumWarning: "8kB"` (`frontend/angular.json:70-74`, production-only — the dev config carries no budgets) — a WARN exists **before this lane touches a byte**, and P3 adds to that very file. The FE track further measured **9 stylesheets over the 8 kB warn** at base plus the initial-bundle warn (~4.92 MB vs 1 MB, corroborated by `.agents/tester/RESULTS/` 2026-06-27 / 07-02 / 07-10 / 07-12). As written the criterion can only be met by a false declaration or by raising the constant (which D4.4 forbids). This is exactly the repo POK defect `review-01-remeasure-base-red-claims`, and a body-vs-ledger divergence of the class `plan-body-ledger-agreement` names — **`phase4-plan.md` already uses the correct net-new form**; the top level contradicts its own phase plan.

**Fold:** restate criterion 15 and D4.5 leg 3 identically to P4's form: "EXIT 0; **zero NET-NEW** budget WARN vs the P4-task-1 base capture; the pre-existing `instance-list.scss` 8 kB WARN is base-attributed with its measured byte delta; the 24 kB error threshold must not be crossed." Identical wording in all three places so the gate cannot read two thresholds. Budget constants stay untouched (D4.4).

### C-3 · P3's Tests block and Verification Commands contradict the same phase's Files block and AM-7 — forking execution onto the AM-7 failure mode
**Anchors:** `phase3-plan.md` Tests (`:33`) and Verification Commands (`:41`) vs Files (`:23`/`:25`) and tasks 6/7; `plan-overview.md` criteria 13/24. **Sources:** council C3 [A3∪B2] + FE F-2 + BE F5.

`:23` says `instance-list.component.spec.ts` is **NOT** modified; `:25` creates the new `instance-list.rooms-btn.spec.ts`; task 7 + criterion 24 forbid DOM duties in the mirror-only spec (verified: **zero TestBed** usage in its 1,284 lines; **84 `it(` cases across 28 describes — re-measured twice, word-boundary method; an earlier 85-count claim was retracted in refinement). But `:33` says the mirror spec is "Extended (+6 cases)" and `:41` runs jest against **that file only** — an implementer following them in order edits the zero-TestBed mirror spec (the exact AM-7 failure mode), and the new DOM cases carrying items (b)/(c) **never execute** — P3's own gate passes with the new spec red or absent. Secondary: task 7's prose says "six DOM cases" but enumerates **seven** (i)–(vii); criterion 24 says "6/6".

**Fold:** point `:33`/`:41` at `instance-list.rooms-btn.spec.ts` (mirror spec explicitly listed as *not extended*); add the new file to P4's "FE — named set" leg; pin **seven** in P3 task 7 (both occurrences), its acceptance cell, criterion 24 (both), and the AM-7 disposition row. (Counts and allowlist consequences in W-2.)

### C-4 · The anchored path's existence check is pinned to a graph-restoring seam — and this lane puts it on a 10-second poll
**Anchors:** `phase1-plan.md:15` ("Anchor present ⇒ `manager.get_instance(instance_id)` (`KeyError` ⇒ 404)"). **Sources:** BE F1 (rated high-WARNING, flagged "candidate 🔴") — **promoted to CRITICAL by the aggregator** (severity discipline: availability/side-effect hazard on the lane's own constraint-4 surface; promotion mechanics re-verified by the controller at `daemon/manager.py:16336-16351` → `daemon/services/instance_lifecycle.py:4580-4615` → `_restore_instance` `:4702-5121` → `_recover_watchover_pending_termination` `:5119` → `terminate_instance(..., terminal_reason="watchover_terminated")` `:4645`; cache bounds `INSTANCE_CACHE_MAX_ENTRIES = 64` / `INSTANCE_CACHE_TTL_HOURS = 6`, `manager.py:306-312`).

`manager.get_instance(anchor)` is **not** a cheap existence check. On a cache miss it runs `ensure_mcp_preloaded()` (can spawn MCP stdio children) and `_restore_instance()` — builds a full LangGraph graph, registers it in `manager.instances`, and then runs `_recover_watchover_pending_termination`, which can **terminate an instance**. The cache is bounded (64 entries / 6 h), so cold anchors are common in any nest above 64 rows or after 6 h idle. The `joined` popup polls every **10 s** (D2.2/AM-9), so every tick of an open popup on a cold instance re-restores the graph — a GET that allocates, spawns children, and can kill instances, on the release where the **video-team pilot is live** (constraint 4). The pre-existing tree route shares the seam but is called once per modal open; the new route is called ~6×/min.

**Fold:** pin the existence check to a pure DB read — `manager.get_instance_info()` (the sibling instance-route idiom, `instances.py:566`, = `repository.get()` + child ids, `instance_lifecycle.py:5227-5251`) or `manager._instance_repository.get(instance_id) is None`. 404 shape/message unchanged. Add one spec leg asserting the list route never awaits `get_instance` (stub-not-called), so the cheap seam cannot silently regress. The pre-existing tree route's identical seam is out of this lane's scope — but the new route must not follow it, precisely because it is polled.

---

## 3. WARNING findings (fold before code review; all are plan-text edits)

**W-1 · AM-2 residue: P2's Exit Criterion still demands the state the architect proved will red.** `phase2-plan.md` Exit Criterion says "all shipped dialog cases **unedited**" while the five correct fold sites re-point exactly one (`:258-288`; 8 cases in file, 7 untouched). Replace with: "all shipped dialog cases green — exactly one (`:258-288`) re-pointed at `fixture.debugElement.injector.get(RoomService)`, the other seven unedited." *(council W1)*

**W-2 · Allowlist count "FIVE … the four modified" is wrong and sanctions a path the same phase forbids touching.** P3 modifies **three** instance-list files and creates **one** — four paths. The enumerated fifth (`instance-list.component.spec.ts`) is the file `:23` says is NOT modified; adding it to `FRONTEND_UI_LANE_SANCTIONED_FILES` widens the sanctioned surface with an untouched path (a future foreign hunk there would pass the path test; a room-feature lane trivially satisfies the marker test). Fix to "four paths — three modified + the new spec" in P3 task 6/Files/Exit Criterion, R5, criterion 17, and P4 task 7(b). *(council W2 + FE F-2 + BE F3)*

**W-3 · AM-10's disclosed narrowing edge is mis-aimed; the pinned P1-5 leg cannot demonstrate it.** Mechanics verified against `repository.py:717-738` by two tracks independently: the up-walk adds each `parent_id` read from the current row **without verifying the parent's row exists** — so the admitted-but-row-missing id is the **caller's own row**. A hard-deleted *intermediate* ancestor breaks the walk above the deletion and **also denies** — no divergence there. The plan's wording ("a caller whose **intermediate ancestor** row was hard-deleted") is therefore wrong; the divergence is "the **caller's own** instance row was hard-deleted (its id still referenced by a child's `parent_id`)". Direction stays fail-closed, never wider, HTTP-unreachable — but the pinned fixture as described (delete an intermediate ancestor) proves nothing. **Fold:** reword D1.5/R9/C-1 and rebuild the P1-5 fixture: subject id referenced by a child's `parent_id`, subject row deleted; assert list-path denial + shipped-path admission. *(council W3, adjudicated over a shallower verification; BE-track mechanics concur)*

**W-4 · AM-6 residuals survive in six places.** (i) `decisions.md` D2.5 RATIONALE "additionally coupled to an open finding"; (ii) `plan-overview.md:55` Out-of-Scope "coupled to the **open F-3 finding**"; (iii) `decisions.md:404` D5.3 lists the "open F-3 SSE pre-branch finding"; (iv) `plan-overview.md:31` `:216-256` → `:212-257`; (v) C-3 `:217-257` → `:212-257`; (vi) `phase3-plan.md` task 2 `:213-215` → the nested-button rationale is `:161-163` (`:213-215` is the pin-gate comment). The closure itself is verified true (`5d13a1c6c` + `652203f95` ⊆ `0181dadbf`; `live_event_hub.py:38-56`). Replace "open F-3" wording with "the `:63` leader-routed messages-route default-scope breadth — the SSE pre-branch finding is **closed at base** (A-P6-26)". *(council W4 + FE F-8 + BE F4)*

**W-5 · P1 task 1's "ONE `Session`" acceptance is unachievable with the named helper.** `list_active_member_instance_ids` (`repository.py:1327-1341`) opens its own `with Session(self._engine)`, unlike the session-parameterised `_active_member_counts`/`_last_message_seqs`. Authorise a session-accepting private variant (public method delegates; existing callers `room/service.py:505`, `room_gc.py:396` unchanged) — or restate the acceptance as "one row session + one roster session; batched; zero per-room queries". The no-N+1 claim holds either way. *(council W5)*

**W-6 · P1 task 4 façade signatures don't match task 3's call sites.** Task 4 declares `list_all_rooms()` / `list_readable_rooms(caller_instance_id)`; task 3 calls `list_readable_rooms(instance_id, limit)` / `list_all_rooms(limit)`. Give the façades `limit: int = 200` pass-through (matching `list_rooms_for_tree`, `service.py:491-497`) and note the same-named repository method is a different layer (or rename to avoid the collision). *(council W6 + BE F6)*

**W-7 · `fetchAllRooms` return type is ambiguous — `tsc --noEmit` EXIT 0 (criterion 14) will fail as written.** Task 9 declares `Observable<RoomListResponse>` (legacy tree envelope) while task 8's normalizer produces `GlobalRoomSummary[] + truncated`. The shipped `RoomSummary` has no `member_instance_ids` and `normalizeRoomSummary` copies only known fields — so the roster would be silently dropped and `buildMembershipMap` has no typed source. Declare `GlobalRoomListResponse { rooms: GlobalRoomSummary[]; truncated?: boolean }` + a `normalizeGlobalRoomListResponse` copying the three extra fields; type the global lane as its own signal (keeps the shipped `instance` mode byte-unchanged) and state which in P1-9. *(council W7 + FE F-4)*

**W-8 · Detail-pane messages anchor posture is undocumented — a constraint-1 honesty gap.** `fetchMessages` (`room.service.ts:92-106`) has no `instance_id` param: in `global` **and** `joined` modes the detail pane reads through the messages route's operator-lane default (room owner, `rooms.py:271-277`) — the pre-existing breadth the leader ruling routed out of this lane. Add one line to D1.10/D1.11 and P2 task 3: "the detail-pane messages call remains anchor-less in all three modes — unchanged shipped behaviour; no new anchor is introduced on that route." *(council W8)*

**W-9 · Every verification command in P1–P4 is unexecutable in the worktree as written.** No `.venv/` and no `frontend/node_modules/` in the lane worktree (lazy-venv rule); the commands invoke `.venv/bin/python` and `./node_modules/.bin/jest|tsc|ng`. Add an explicit P1/P4 task-0 bootstrap leg (`uv sync` in the worktree; `cd frontend && npm ci` — lockfile install, not a new dependency; state the network/offline contingency) or declare the reuse/symlink convention once in `plan-overview.md`. *(council W9 + FE F-6)*

**W-10 · The new unconditional mount-time `GET /api/rooms` is unmodelled.** `chat.chatrooms.spec.ts` mounts the real `ChatComponent` with `provideHttpClientTesting()` and no `httpMock.verify()` — it gains one unflushed `/api/rooms` request per case (expected green; record as a P4 expected-delta line). The live pilot chat page likewise gains a session-start fetch (two hosts ⇒ up to two per session) that the R12/AM-15 disclosure does not mention. Add both. *(council W10 + FE F-11)*

**W-11 · AM-9's load-bearing superset invariant ("anchored rows ⊇ the instance's joined rooms") has no backend leg.** True at the wire (membership branch passes `_require_room_access`, `repository.py:763-765`), but criterion 23 asserts it only with mocked HTTP. Add one BE leg to the new router spec: for an instance with N active memberships, every joined room id appears in `GET /api/rooms?instance_id=X`. *(council W11)*

**W-12 · Child-row geometry: `.rooms-btn` at fixed `right: 104px` floats with a ~64 px gap on child rows.** `pin-btn`/`terminate-btn` are gated on `@if (!instance.parent_id)` (`instance-list.html:217`, `:248`); on a child row only `pause-btn` (40 px) exists, and the plan explicitly wants child rows to carry the button while forbidding layout shift to the existing three. **Fold (reviewer ruling):** keep AM-8 for root rows and specify the offset **per row shape** (child rows get their own offset, e.g. 72 px), plus a DOM assertion for the child-row case, and disclose the rule in the final report. A cluster refactor (flex-anchored offsets) would touch the three existing buttons' positioning — rejected as scope expansion. *(FE F-3; reviewer ruling on the FE track's open question 3)*

**W-13 · R10 still carries the claim AM-2 refuted.** `plan-overview.md` R10 asserts the shipped dialog spec "never inject[s] the service instance" — false: `room-dialog.component.spec.ts:262` is `TestBed.inject(RoomService)`. R10 is the justification the plan leans on to call `providers:[RoomService]` safe. Rewrite R10's mitigation clause to the AM-2 wording (one case reaches the service via `TestBed.inject`; it is re-pointed; the guard it proves is unchanged). *(FE F-5)*

**W-14 · The P4 FE web-automation artifact has no declared home, and both candidate homes conflict with the plan's own rules.** The only configured `testDir` is `frontend/e2e/` (`playwright.config.ts:4`); a committed spec there is a new `frontend/` path — an offender under the path-first guard unless allowlisted — while task 6 requires `git status` porcelain **0**, and an uncommitted spec is deleted at cleanup, making the leg irreproducible. Pick one and write it into task 6: (a) spec lives in `/tmp` with a `--config` override pointing `testDir` at it (no repo path, no allowlist row), or (b) spec committed under `frontend/e2e/` + allowlist row in the same commit (drop the porcelain-0 clause for that file). *(FE F-7)*

---

## 4. ADVISORY findings (fold opportunistically; none blocks the gate)

- **A-1** Use the house `no_xdist` marker (`tests/conftest.py:319-341`) for the knob flip test, and flip via `monkeypatch.setattr(rooms_module, "_ALLOW_ANCHORLESS_GLOBAL_LIST", False)` — auto-restored, no `finally`, correct even under an accidental `-n`. *(council A7 + BE F9)*
- **A-2** The AM-12 grep-pin is prose-satisfiable (POK `mutation-01-guards-need-counter-mutation`). Add behavioral legs for every anchor-absent spelling under knob-OFF: `GET /api/rooms`, `?instance_id=`, `?instance_id=%20`, `?limit=5` — each asserting 404 `instance_not_found`, never 200/403. *(BE F10)*
- **A-3** Pin the knob-OFF message as a fixed, input-free string identical to the bad-anchor message (no knob disclosure, no client-input echo) so the two refusals are wire-indistinguishable. *(BE F8)*
- **A-4** Knob-OFF blast radius: (a) dark, **and (b) unreachable via its only entry point** (its anchored fetch still works; the gate that renders the button cannot load). State the whole fast-follow goes dark, plus the pinned FE posture (error + Retry). *(BE F13)*
- **A-5** In the AM-11 docstring rewrite, state why the new route emits `state` while the tree route emits `closed` (new route = fuller row; tree route = frozen v0.31.0 contract) so the divergence reads as decided. *(BE F7)*
- **A-6** Repo→repo coupling has two acceptable shapes; hard constraint: **no source edit to `daemon/manager.py`**, single engine. (a) Router computes `descendant_ids = manager.get_tree_ids_permanent(anchor)` (existing facade, correct parameter name, `manager.py:16466`) and passes the set into the repository — the repo→repo edge disappears; or (b) keep AM-13 and construct via `create_instance_repository(engine=self._engine)` with a lazy in-method import (keeps `room/repository.py` import-clean). *(BE F12 + council A8, merged)*
- **A-7** Success-criteria numbering runs 20 after 24 — renumber sequentially and re-point P4's per-criterion evidence. *(council A6)*
- **A-8** Drift-guard allowlist facts: **15** paths at `:784-804` (not "14 at `:784-806`") — repeated in D4.6, D5.3, R5, P2 drift note, P3 task 6. All eight P2 modified paths verified already listed. *(council A9)*
- **A-9** P4 fence: state plainly the harness binds NEITHER 9797/9798 (live) NOR 8079/8088 (other lanes) and asserts its own unused pair; keep the pinned sweep/ambient-allow guards (boot-smoke lesson). *(council A4)*
- **A-10** `RoomDialogData.instanceId` widening: state the shipped CALL SITE is unedited, the dialog internally narrows for `instance` mode, and pin a defensive no-request error state when `instance`/`joined` lack an `instanceId`. *(council A5)*
- **A-11** Record in D5 that (b)'s "with their messages" is realized as the shipped two-level surface (rooms → selected room's conversation), matching (a)'s explicit shape. *(council A10)*
- **A-12** Pin poll-tick behaviour when the open room vanishes from the refreshed list (selection preserved by id; honest closed state if gone) + one spec case. *(council A11)*
- **A-13** Anchor-refresh sweep (all verified at review time): `app.routes.ts:135→136`; `room/service.py:206→208` (`room_created`); `instances.html:22 → instances.component.html:22`; `styles.scss:59-88 → 59-91`; `angular.json:71-75 → 70-74`; `overview-tab` `:224-238 → :221-238`; D1.11 `api.py:5898` (one digit short in one citation), `rooms.py:990→991`, `decorators.py:88-112→108-114`, `rooms.py:139-144→137-144`. None changes a conclusion; D1.11 is the artifact the Emperor reads. *(council A1-A3 + FE F-10 + BE F14)*
- **A-14** D3.2's `joined` row still shows the anchor-less URL; AM-9 (immediately below) and P1-9/P2-3/P3-1 all pin ANCHORED. Update the table cell. *(FE F-9)*
- **A-15** Threat-model wording nits on D1.11: "strictly less sensitive" → "less revealing than content" (for a silent member the roster is information present nowhere the principal can read); add "read-" to D1.4 ("the operator lane's existing **read** breadth") so the pair cannot be quoted in isolation. *(BE track threat-model note)*
- **A-16** The new `.rooms-btn` is a `<button>` nested in the row's `<a [routerLink]>` — invalid HTML nesting, but the pre-existing house pattern (pin/pause/terminate identical), mitigated by `preventDefault()` + `stopPropagation()`. Note it in the P3 task-3 rationale comment; conscious choice, no change. *(FE F-12)*

---

## 5. Resolved during review (no longer open)

- **FE track Q1 (budget threshold)** → delta wording (C-2 fold). Clearing base warns would pull nine unrelated stylesheets into the lane — scope expansion, forbidden by constraint 2. *Reviewer ruling.*
- **FE track Q2 (P3 DOM cases)** → new spec file (AM-7's adjudicated shape; the plan's Files/task-7 already take it). Fix the contradicting Tests/Verification lines (C-3). *Reviewer ruling.*
- **FE track Q3 (child-row placement)** → disclose-and-pin, no cluster refactor (W-12). *Reviewer ruling.*
- **Council divergence "84 vs 85 cases"** → resolved **84** (28 describes) by double re-measurement; the 85-count was a `split(` match artifact; the claiming councilor retracted. Evidence method: word-boundary `grep -oE "(^|[^A-Za-z0-9_$\.])it\("`.
- **Council divergence "AM-10 verified vs partly"** → **PARTLY** (W-3); the sub-case trace is mechanically complete and the plan's wording mis-aims the edge.
- **Council divergence "AM-4 verified vs partly"** → **PARTLY** (C-1).
- **Severity calibration** (budget criterion; P3 fork) → CRITICAL for both (gate-scored acceptances that cannot be truthfully satisfied / an execution fork onto a plan-violating path).

## 6. What the plan gets right (recorded so fixes don't erase it)

- **Anchor discipline is the strongest element of the set** — ~90 anchors independently re-verified across three tracks, essentially all accurate; the handful of drifts are listed above.
- **Both gate-blocking folds (AM-1, AM-2) are genuine** — content-verified, code-verified, and in AM-2's case the re-point is the *minimal correct* fix, not merely defensible.
- **The constraint-1 disclosure (post-AM-1 D1.11) is truthful in both directions**, and the BE track could not find a stronger leak than the roster it already names: metadata-about-participation vs content the same principal can already read; cardinality already exposed; ids already enumerable; strictly required by (b)/(c); no new write path, scope, or middleware.
- **The descendant-closure predicate form was checked for widening and none was found** — ancestor/descendant are inverse relations over single-valued `parent_id`; membership parity is exact (composite PK); both depth caps truncate toward deny; the `owner == caller` disjunct is present and load-bearing; LEFT-member parity explicitly preserved.
- **Single-ownership invariant holds** (no file claimed by two phases); symbol-creation ordering sound; the drift guard is **green at base** (19 hunks, 0 offenders) so "green" is a reachable target.
- **"No scope-manifest edit needed" is correct** and was checked at both cages, including the `tests/integration/` path detail plans routinely get wrong.
- **Scope discipline is clean**: no WATCHOVER row control, no second route, no SSE consumer, no budget-constant change, no live promote, F-3 correctly routed out and not touched.

## 7. Gaps / not verified (disclosed, not silent)

1. **No build/test executed** (read-only review; no venv/node_modules in the worktree — itself W-9). The base budget-warn set is a deterministic byte-vs-threshold inference plus repo tester records; **P4 task 1's production-build capture is the authoritative measurement**.
2. **`providers:[RoomService]` runtime behaviour under MatDialog** reasoned from Angular injector semantics, not executed; the P4 web-automation leg should confirm.
3. **P1 task 7's claimed base-reds** (two known reds in `test_api_router_extraction.py`) not confirmed red-at-base.
4. **FE-manifest chunk delta** (RoomDialog second importer) unmeasurable without a build; P4 declares-and-measures — correct treatment.
5. **Video-team pilot liveness** taken from the commission (constraint 4), not derivable from the repo.
6. **F-3 messages-route live breadth** deliberately not reopened (leader-routed); all three tracks confirm the plan discloses it accurately, does not touch the route, and ships the new surface fail-closed with the same-commit knob.

## 8. Disposition

**APPROVE-WITH-CONDITIONS.** Conditions: fold C-1 … C-4 and W-1 … W-14 into the plan set (single planner pass; every fold above is a text edit — none re-opens a ruling or changes the design), then proceed to the fresh-approver gate per constraint 5. A-1 … A-16 are strongly recommended in the same pass but do not block. Re-review after the fold is **not** required for the advisories; the four CRITICAL folds should be spot-checkable by the approver against this document's fold instructions.
