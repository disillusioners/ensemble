# Tester Functional Gate — `gc-fe-surfaces` (group-chat FE fast-follow)

- **Date**: 2026-11-18
- **Tester**: Tester agent (test leader) — planning + aggregation; all execution delegated to worker instances (one pack per worker, single skill per worker)
- **Lane**: `gc-fe-surfaces` · task `gc-fe-20261117` · Emperor commission 2026-11-17
- **Worktree**: `/home/nea/ensemble-src-wt-gc-fe-surfaces` · branch `feature/gc-fe-surfaces`
- **Base**: `0181dadbf8641c60d7158ad59edfa4a4b36944c8` (v0.31.0 bump commit)
- **Gate HEAD**: `c479cef98cdf6bd1d106bafe6b1b0fa608329b04` — 38 commits over base (32 lane + 6 tester-gate commits)
- **Disposition**: staged release, NO live promote (commission constraint 5)
- **Verdict**: **GO-WITH-DISCLOSURES** — 8/8 gate legs PASS, net-new reds **0** (one net-new red surfaced by G3a at the live tip was closed by a committed, mutation-proven quick fix). Staged-release ready; promote remains downstream / leader-owned.

---

## 0. Scope decision

The commission asked for a **functional gate** (G1–G5) plus the leader-owned daemon-fenced adjacency row — not a promote. Blast radius of the lane (mechanical, from `git diff --name-only 0181dadbf..HEAD`): **17 `frontend/` files · 3 `daemon/` modules · 4 `tests/` files · 12 `.agents/shared/planning/` docs · 0 migrations · 0 `agents/` files**.

**Regression tier: TIER-1 (blast-radius dual-side)** — the lane touches `daemon/routers/rooms.py`, `daemon/repositories/room/repository.py`, `daemon/repositories/room/service.py`. None of these is on `ensure.md`'s mechanical core-seam list (`daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/{instance_messaging,task_processor,job_queue_service}.py`) ⇒ Tier-2 escalation is **mechanical and does not trigger**. The FE whole-tree jest baseline diff runs as its own dual-side leg (jest has no tier recipe; measured-rate + identical flags both legs).

**Not run** (and why): the `ensure.md` Release Gate (slow E2E/full-suite arm) — the lane is a feature lane, not a big/critical/architecture change, and promote is explicitly downstream of a staged cut.

---

## 1. Gate summary

| Gate | Subject | Verdict |
|---|---|---|
| **G1** | mock/vacuity adjudication (W2 counter-mutation · 4 tidier flags · A6 AST pin) | ✅ **PASS** |
| **G2** | original-symptom functional, daemon-fenced (adjacency row + legs (a)–(i)) | ✅ **PASS** (9/9 legs) |
| **G3a** | BE blast-radius dual-side regression (rooms family + census/contract/drift) | ✅ **PASS** (net-new 0) |
| **G3b** | v0.31.0 shipped-surface stability (four pins, mirror spec, icon) | ✅ **PASS** |
| **G3c** | FE whole-tree jest dual-side baseline diff vs base | ✅ **PASS** (net-new 0) |
| **G4** | budget: production build deltas, 0-dep proof, lazy-route guard | ✅ **PASS** (1 advisory) |
| **G5** | staged-readiness facts (version, migrations, agents/, payload hygiene) | ✅ **PASS** |

**Net-new red ledger: 0** (see §6).

---

## 2. G1 — MOCK / VACUITY ADJUDICATION

### G1a — independent re-run of the published W2 counter-mutation — **PASS**
Publisher of the claim: `gate-evidence.md` §W2(c) (BE close-out lane).

| Step | Result |
|---|---|
| Mutated leg | `daemon/repositories/room/repository.py` — deleted the `or owner in descendant_set` disjunct (one-hunk diff) |
| RED | `tests/unit/routers/test_rooms_list_api.py::test_w2_grandparent_anchor_sees_grandchilds_room` — **1 failed / 40 passed**; assert `assert {r["room_id"] for r in rooms} == {rid}` → `AssertionError: assert set() == {'6cec3b64-…'}` (exact-set collapses to empty — the published failure mode) |
| Restore | `git restore` + `sha256sum` PRE `73d8adee8c100563ee0abd0dea63f277f1e8e0c303e9c0350e742dc18ae6acc5` == POST (identical) + independent `cmp` against a `/tmp` pre-mutation backup (rc=0) + `git diff` empty |
| Clean re-run | **41 passed** (baseline before mutation: 41 passed) |

**Corrected anchor**: the disjunct is at `daemon/repositories/room/repository.py:1479` (the `:1460-1462` range carries `descendant_set = set(descendant_ids)` at `:1462`). The published claim is **independently reproduced**.

### G1b — tidier-owned flags, adjudicated — **PASS (4/4 adjudicated, 4 test-artifact fixes landed)**

| Flag | Verdict | Evidence | Fix |
|---|---|---|---|
| (i) neutered `httpMock.verify()` — `instance-list.rooms-btn.spec.ts:118` | **DEFECT (nice-to-have, test hygiene)** | `afterEach` wrapped `verify()` in `try { … } catch (e) { /* ignore */ }`; the spec DOES issue HTTP (`/api/rooms` via `flushRooms`/`flushRoomsError`) ⇒ `verify()` is the only unmatched-request guard, and swallowing it makes a dead ceremony that can never fail while appearing to guard | `be0c9e1c3` (un-neutered); re-run **11/11** |
| (ii) e2e `RoomRow` missing `state` | **INTENTIONAL (harness type-completeness nit — no behavioural hole)** | UI branches on `room.closed` (`room-dialog.component.html:72/:127/:167-168`), derived by the normalizer `room.model.ts` `normalizeGlobalRoomListResponse` (`closed: base.closed \|\| r['state'] === 'closed'`); the stub already supplies `closed`; `room-dialog.component.spec.ts:102` pins `'closed'` rendering ⇒ no state-dependent branch is untested | `2572342fb` (wire-contract fidelity) |
| (iii) Playwright NaN-port + fixed `/tmp` evidence dir | **DEFECT (latent boot hazard + environmental collision)** | `Number(process.env.ROOMS_GATE_PORT ?? '14204')` — `??` does not guard empty/non-numeric ⇒ `NaN`/`0`, and the webServer binds `${PORT}` ⇒ latent hazard, **not** inert; `outputDir`/`results.json` hardcoded `/tmp/rooms-gate-evidence/…` ⇒ collision + stale-evidence risk across concurrent runs | `9509be275` (finite/positive port validation + PID-suffixed evidence dir); gate re-run **4 passed / 1 skipped** |
| (iv) AsyncMock census on new `await` paths | **CLEAN** — one async-correctness nit found | New awaits: 4, all in `daemon/routers/rooms.py` `list_rooms` — `:376 await room_service.list_all_rooms(...)`, `:394 await asyncio.to_thread(manager.get_instance_info, anchor)`, `:411 await asyncio.to_thread(manager.get_tree_ids_permanent, anchor)`, `:424 await room_service.list_readable_rooms(...)`; covered by `tests/unit/routers/test_rooms_list_api.py`; manager methods are real sync wrapped in `to_thread`, service methods real async ⇒ **no "never-resolves" vacuity**. Frontend product code: **0** new awaits (Observable-based). Nit: `test_faulted_service_503` patched the async `list_all_rooms` with a SYNC `side_effect=RuntimeError` ⇒ `await MagicMock()` raises `TypeError`, caught by the handler's `except Exception` ⇒ 503 passed **without exercising the intended RuntimeError** | `0d122e0a3` (async side_effect) |

**No product-code defects were found by G1b.** The `test.skip` adjacency row was left untouched by this leg.

### G1c — council A6 AST knob pin — **PASS (adjudicated non-vacuous; one shape re-run)**

The pin is genuinely AST-based and **non-prose-satisfiable**: `tests/unit/routers/test_rooms_list_api.py:635-751` defines `_knob_gate_violations(src)`, which `ast.parse`s the real router source and walks it; the node `test_am12_single_branch_knob_grep_pin` (`:754-762`) asserts `== []`. Five structural pins: exactly one `ast.Name` **store** + one module-level `ast.AnnAssign` of the knob (`:656-687`); exactly one `ast.Name` **load** (`:662-691`); exactly one `ast.If` whose test references the knob, shaped `ast.UnaryOp(Not, ast.Name)` (`:693-712`); **no** `ast.IfExp`/`ast.BoolOp`/`ast.Compare` mentioning it (`:714-727`); exactly one `room_service.list_all_rooms` call positioned **after** the gate (`:729-750`). The four published shapes are documented at `:765-773`, each with a fixture at `:779-821` asserting the checker reddens.

Empirically: a comment/docstring/string containing the gate text contributes **zero** AST nodes (measured If-referencing-knob=1, IfExp=0, Name-loads=1 with and without prose) — the tokenizer drops comments before `ast.parse`.

**Shape re-run (i) ternary second route** — applied to `daemon/routers/rooms.py:368` (`+ _knob_mode = 1 if _ALLOW_ANCHORLESS_GLOBAL_LIST else 0` inside the gate body):

```
E  AssertionError: assert ['knob LOADED 2x (want exactly 1 — the single gate test)',
                           'TERNARY at line 369 references the knob — a branchless second decision route'] == []
```
**2 failed, 39 passed** — failing nodes: `::test_am12_single_branch_knob_grep_pin` and `::test_a6_knob_gate_ast_counter_mutation` (the latter reads the REAL router file, so it independently doubles as a canary — the catch is **stronger**, not weaker). Restore: PRE == POST `ebe3bbc64c0058c42ec9d90fdc26080b09ac1f4e047c2457fef8b6046c8e7188`; clean re-run **41 passed** == pre-mutation baseline.

**Disclosed residuals (low severity, no action required for this gate)**: the pin is `rooms.py`-local; a *string-keyed* indirection (`globals()["_ALLOW…"]`) adds no `ast.Name` load and could escape the load/If counts; pin 5 is a **line-order** check (relocating the gate into an earlier helper keeps it green) — that hole is covered by the functional legs `test_knob_off_refusal_serial` (`:456-474`) and `test_n9_422_holds_under_knob_off` (`:488-505`), which red on knob-OFF.

---

## 3. G2 — ORIGINAL-SYMPTOM FUNCTIONAL, DAEMON-FENCED — **PASS (9/9 legs)**

### 3.1 The leader-owned adjacency row — council condition (a)(ii) SATISFIED

| Execution | Config | Verdict |
|---|---|---|
| **Literal un-skip** (sed'd, then byte-restored) | stubbed `playwright.rooms-gate.config.ts` (port 14204) | **FAIL — fixture defect, root-caused** |
| **Real daemon** | new `playwright.rooms-gate.daemon.config.ts` | **PASS** (leg (a)) |

Literal-run failure verbatim: `Error: element(s) not found / Locator: locator('.toggle-buttons')`. **Faithful cause**: the row's fixture is `page.goto('/')`, which renders **HomeComponent** (banner + nav); `.toggle-buttons` exists only inside `.chat-header` on the **instance-detail deep link** — a fixture defect **independent of the stubs**. Byte-restore proof: PRE `0914adf0…f664` == POST `0914adf0…f664`; `test.skip(true` count still 1; `git diff HEAD` on the file = 0 lines. The skipped row was left visible and unskipped-in-repo (deviation (a) condition iii honoured).

**Real-daemon adjacency proof (leg a)**: toggle cluster = `watchover-btn | room-btn | rooms-browser-btn | think-btn | tools-btn | system-btn` ⇒ `roomIdx=1`, `browserIdx=2` — the new browser control is **adjacent (Δ=1)** to the shipped room icon.

### 3.2 Legs (a)–(i) against the real daemon (nothing stubbed except (g) and one real-body 404 replay)

| Leg | Assertion | Measured |
|---|---|---|
| (a) | header menu → global browser + room detail | title `All chatrooms in this nest`; 200 listed ⊇ r1,r2,r3; R1 detail messages `["…one","…two","…three"]`; adjacency Δ=1 (above) |
| (b) | instances-list row button → BIG popup | `aria-label="Chatrooms joined by gcfe_e2e_alpha"`; pane **1050×590 px = 82vw×82vh of 1280×720 (0 px delta)**; joined list `[r1,r2]` (R3 absent); R1 renders 3 messages |
| (c) | room-less instance → NO button | beta buttons = **0**, alpha buttons = **1**, mapError = **0** (absence genuine + live control) |
| (d) | 422/404 taxonomy live | `?limit=abc` → **422**; unknown room → **404** `error=room_not_found`; UI typed copy `Not found — this conversation is no longer available (it may have been purged).` |
| (e) | anchor-less vs anchored | anchor-less ⇒ 500 rows, `truncated=true`, ⊇ r1,r2,r3; anchored(alpha) ⇒ **exactly `["gcfe_e2e_r1","gcfe_e2e_r2"]`**; UI global ∋ R3, joined ∌ R3 |
| (f) | truncated render at a small limit | `?limit=1` ⇒ rows=1, `truncated=true`; notice `More rooms not shown — showing the first 200 of the nest.` |
| (g) | W1/W7 no-transport-URL error copy (forced 5xx) | 500 ⇒ `The server could not load the chatroom list — try again.` (**leak=false**, rows=0); 418 ⇒ `Failed to load chatrooms.` (**leak=false**) — the transport string (`Http failure response for …`) does not reach the pane |
| (h) | polling freshness | psql insert mid-open ⇒ new post visible after **9 368 ms** (10 s tick) |
| (i) | tab-hide stop + supersede | visiblePolls=1, **hiddenPolls=0** over 13 s, resumed=1, **maxConcurrent=1** |

Suite runs: (a)–(g) **7 passed / 24.3 s** · (h)(i) **2 passed / 42.4 s** · stubbed sibling regression **4 passed / 1 skipped / 37.6 s**.

### 3.3 Fence + teardown proof
- Daemon: `ensemble_dev` at 127.0.0.1:5432 (throwaway DB refused — `ensemble_dev_app` has `rolcreatedb=false` ⇒ fallback to `ensemble_dev` with `gcfe_e2e_`-prefixed rows), `ENSEMBLE_SELF_ENV=dev`, `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly, all five `POSTGRES_*` set per-variable, `PORT`/`DAEMON_PORT` exported explicitly, whole `ENSEMBLE_AUTH_*` family scrubbed per-variable.
- Killed by **recorded PID only**, each pid verified as the port owner first: daemon `2797996` (8079 + fenced metrics 8092), ng serve `2799893` (::1:14205). Ports after: **8079 / 8091 / 8092 / 14205 all free**; live 9797/9798 untouched throughout.
- Rows deleted: messages 7, members 4, rooms 3, instances 3 — leftovers **0/0/0/0**; `chatrooms` back to its pre-seed 674.
- Artifacts committed: `6fe3ad005` — 3 new files, +477 (no `git add -A`; `.agents/tidier/notes.md` runtime dirt untouched).

### 3.4 G2 fence deviations (disclosed)
1. `ENSEMBLE_DB_AMBIENT_ALLOW=1` was **required** — boot refused with `DB RESOLUTION REFUSED (rogue-containment): tier=ambient`.
2. `LIMITS_METRICS_LISTENER_PORT=8092` added — the listener derives `PORT+1` (8080); fenced explicitly so nothing lands near live 9798.
3. Test-data technique: the 3 seeded rooms were **backdated** (`created_at` 2026-11-15) on those rows only — anchor-less ordering is `created_at ASC` with a 500 cap, so fresh seeds would have been invisible behind 674 existing rooms.
4. Fixture required `metadata.title` — the instances API exposes **no `instance_name`**; the popup title falls back to *"Chatrooms joined by this instance"* otherwise (there is no `title` column; it is `metadata->>'title'`). **Product observation for the lane backlog**: for an instance carrying neither a name nor a title in the payload, the popup cannot name itself.
5. Row locators must anchor on `href` — visible row labels are CSS-truncated (`gcfe_e2e_bet…`), so text filters fail.
6. Leg (i) counts the **messages-lane** tick (the tick drives the open conversation, never the list — the AM-5 fold behind the F-E erratum); tab-hide used a `visibilitychange` + `document.hidden` shim (headless has no OS tab switch) — disclosed, not a real OS hide.
7. Leg (d)'s UI typed-404 replays the **daemon's own live 404 body** via one intercept (transport injected, body byte-real) — there is no purge API to produce a genuine UI-level 404.
8. **Shared dev-DB side effects** (anomaly `b41983a9` filed by the worker): the daemon's routine boot maintenance deleted 16 `task` + 16 `message_queue` rows and pruned 112 `message_metadata` rows + 72 checkpoints; `instances` drifted **52 → 48** across the window, **unattributed** (the gate's own SQL touched only `gcfe_e2e_%` rows; 0 leftovers; the daemon log contains no instance deletion). Not attributable to the lane under test; recorded for the dev-DB hygiene backlog.

---

## 4. G3 — REGRESSION

### G3b — v0.31.0 shipped-surface stability — **PASS**

| Requirement | Result |
|---|---|
| Four shipped chat pins unedited | **Substance PASS.** The literal "empty diff" proxy is wrong-shaped for an appending lane: `git diff 0181dadbf..HEAD -- frontend/src/app/pages/chat/chat.chatrooms.spec.ts` = **+73 / −0** (one appended `describe('the all-chatrooms browser control (gc-fe-surfaces)')` block at the file tail; insertions begin at line 464, immediately after the last shipped pin's `});` at 463). Pin byte-stability proven four independent ways: (a) **zero deletion lines** ⇒ every base line survives verbatim; (b) region sha256 `ff46464715318ffad1b6818b84924e7a849af47bb660334700b790d086776a1d` for `head -461` at base == HEAD; (c) same-line identity — the four `it(...)` headers byte-identical at the same lines 372/401/432/457; (d) runtime green. Pin anchors matched with no drift. |
| Instance-mode pane byte-identity | `chat.html` lines 89–99 (base) == lines 89–99 (HEAD) ⇒ both `f58b0acf965d6e4bbd23fa0c2dab5a332c6f0b4f817114061f8dc275ece23f9a`. The new control is inserted **after** the `@if (renderedInstance()) {` block closes ⇒ the shipped pane is untouched. |
| Chat-page diff shape | `chat.html` **+19/−0** (pure-append) · `chat.component.ts` **+27/−0** (pure-append) · `chat.scss` **+6/−1** — the single deletion is a **selector-list widening** (`.toggle-btn.room-btn` → `.toggle-btn.room-btn, .toggle-btn.rooms-browser-btn`); the shipped rule's property body is byte-identical (sha `aa5707005401e3734a4d7cfae86357c136ca0af20f25956f0dfa508a7296f3eb`). **Disclosure**: the lane's blanket "pure-append" phrasing is imprecise for the SCSS (append + one semantics-preserving selector rewrite). |
| Shipped icon renders | guard `@if (renderedInstance())` at `chat.html:89`; the shipped `room-btn` / `<mat-icon>forum</mat-icon>` sits inside it; pinning test `:401` **PASSED**. |
| Mirror spec | `instance-list.component.spec.ts` → **84/84** (2.64 s) |
| Shipped chat spec | `chat.chatrooms.spec.ts` → **7/7** (4 shipped pins + 3 new gc-fe-surfaces cases) |

### G3a — BE blast-radius dual-side regression (Tier-1) — **PASS (net-new 0)**

Declared set: **33 files / 558 nodes** at HEAD vs **32 / 517** at base (the +41 = the lane-NEW `tests/unit/routers/test_rooms_list_api.py`; base-side impossible by construction — the new `GET /api/rooms` route). Derivation = three discovery passes unioned: (a) `*room*` path match, (b) import/reference scan for `daemon.routers.rooms` / `daemon.repositories.room`, (c) census/contract/drift companions (room HTTP/SSE/scope-cage/scope-manifest + `test_attestation_lca_note_removed.py` + `test_metrics_query_scope_cage.py` + `test_router_scope_manifest.py`).

| Group | Files | Nodes base → head |
|---|---|---|
| G1 rooms-router | `test_room_http_api`, `test_room_sse_resolution`, + lane-NEW `test_rooms_list_api` | 30 → 71 |
| G2 rooms-repo + drift | `test_room_repository`, `test_room_gc_repository`, `test_room_membership`, `test_room_models_and_migration`, `test_room_migration_runner_execution`, `test_room_boot_f1_pin`, `test_room_capability_row_pin`, `test_room_close_producer`, `test_source_gc_repository` | 188 → 188 |
| G3 rooms-services | `test_joined_room_context`, `test_lifecycle_room_join`, `test_room_delivery`, `test_room_delivery_refactor_pins`, `test_room_gc_sweeper`, `test_room_naming`, `test_room_nudge`, `test_room_p6_wiring_pin`, `test_room_post_cap`, `test_spawn_room_join_broadcast`, `test_context_module_split`, `test_group_chat_ledger_gate` | 180 → 180 |
| G4 rooms-tools | `test_room_tools`, `test_room_close`, `test_room_post_cap`, `test_room_registration`, `test_spawn_room_join` | 91 → 91 |
| G5 census + manifests | `test_metrics_query_scope_cage`, `test_router_scope_manifest`, `test_post_restart_arm_notify_no_parallel`, `test_attestation_lca_note_removed` | 28 → 28 |
| **Total** | 33 files | **517 → 558** |

- **Slicing proof**: executed set == declared set on both legs (HEAD 33 files, BASE 32); per-file node counts match `--collect-only` (558 / 517).
- **FAILED-digest equality (BARE-ID form — sorted node ids, prefix stripped)**: every group base 0 / head 0, `cmp` identical; aggregate base sha256 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` == head sha256 (empty digest == empty digest) ⇒ **net-new 0, pre-existing 0**; the 3× flaky adjudication was not triggered (net-new = 0).
- **Exclusions with compensating pins**: `tests/integration/*` (commission directive) — incl. `test_router_class_manifest.py` (36 class families) → compensated by the unit-level copies of the 36 count in `test_router_scope_manifest.py` + `test_metrics_query_scope_cage.py`; `tests/performance/test_message_api_cost.py` (>300 s solo) → `regression_performance` pack; `tests/packs/g7` (`sys.exit(0)` collection trap) → packs run via registered scripts; e2e live-`:8079` cells → `e2e_workflows_ensure_test`. `tests/unit/test_lcancheck_family_separation.py` **does not exist** (named in the commission) → dropped with justification; 6 incidental "room"-substring hits (headroom/room-for prose) dropped with justification.
- **Fence + mode**: per-variable `POSTGRES_*` + `ENSEMBLE_SELF_ENV=dev` + `DAEMON_PORT`/`PORT=8079` + `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` + `ENSEMBLE_ALLOW_ANCHORLESS_GLOBAL_LIST` unset + all 7 `ENSEMBLE_AUTH_*` unset + `DATABASE_URL` unset + `PG_TEST_ALLOWED_DBS` set explicitly, with a per-invocation sibling-env assertion; `import daemon` asserted to resolve INSIDE each tree (shared-`.venv` editable-`.pth` trap defended via `PYTHONPATH`). G1 serial (the lane-NEW file carries `no_xdist`-marked knob legs); G2–G5 under `xdist -n auto` within one leg (recipe-endorsed).
- **Re-scope (one attempt)**: the live worktree HEAD moved mid-gate (`0d122e0a3` → `6fe3ad005`, a concurrent FE-only commit) ⇒ both legs were measured in **detached worktrees at the pinned shas** (recipe-canonical two-worktree form); both detached worktrees removed + `git worktree prune`; main-checkout porcelain sha byte-identical before/after.
- **Live-tip finding (outside the pinned digest)**: the drift guard `tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange` reports **2 offending hunks at the live branch tip** because the gate's own G2 artifacts committed in `6fe3ad005` (`frontend/e2e/group-chat-rooms-gate.daemon.spec.ts`, `frontend/playwright.rooms-gate.daemon.config.ts`, `frontend/proxy.gcfe-e2e.conf.json`) are not in the sanctioned FE-UI lane allowlist. At the pinned HEAD the guard is green. **This gate owns the fix** (the artifacts are the gate's own) — **CLOSED by committed quick fix `c479cef98`**: the three paths were sanctioned in `FRONTEND_UI_LANE_SANCTIONED_FILES` (the guard's F-1 path allowlist, `tests/unit/repositories/test_source_gc_repository.py:796-805`) and the one file lacking a lane marker (`frontend/proxy.gcfe-e2e.conf.json`) received the house line-1 marker comment — proven JSON-safe (Angular's dev-server parses proxy confs with **jsonc-parser**: `parseErrors: 0`, byte-identical parsed proxy object). Guard **after commit**: **39 passed / 0 failed**; the three new allowlist entries removed again ⇒ the guard REDS exactly as pre-fix (**2 failed / 37 passed**) and the file was restored byte-identical (sha `db4703769794…` PRE == POST) ⇒ the green depends on the fix, not on incidental state. Sanity: `test_rooms_list_api.py` **41 passed**.

### G3c — FE whole-tree jest dual-side baseline diff — **PASS (net-new 0)**

| Leg | Suites | Cases | Passed | Failed | Jest time |
|---|---|---|---|---|---|
| HEAD `6fe3ad005` | 168 (164 pass / 4 fail) | 4939 | 4930 | **9** | 35.27 s |
| BASE `0181dadbf` | 167 (163 pass / 4 fail) | 4886 | 4877 | **9** | 43.59 s |

Identical flags both legs: `jest --ci --silent --maxWorkers=8 --cacheDirectory <per-leg> --json`; base worktree `/tmp/gcfe-base-fe` (detached at base + `frontend/node_modules` symlink; `jest.config.js`, `setup-jest.ts`, `tsconfig.json`, `tsconfig.spec.json`, `package.json` verified identical) — **removed** afterwards.

- **9 deterministic failures byte-identical on `assertionResults[].fullName`** (base == HEAD): `schedule-create-dialog.component.spec.ts` ×3 · `jobs-grouping.model.spec.ts` ×3 · `settings.component.spec.ts` ×2 · `jobs-filter-state.model.spec.ts` ×1 — the `Intl.supportedValuesOf` locale class. **The dev's attribution is verified, not assumed.**
- **`metrics.banner-geometry.spec.ts` ×4** — ENVIRONMENTAL: passed in **both** whole-tree runs and solo ×2 (3.48 s / 2.75 s). Load/RAM dependent; reproduces only on a starved host.
- **HEAD-only failing cases: 0 · BASE-only: 0.** 53 purely additive cases, all green: `room.service.spec.ts` +27 · `room-dialog.component.spec.ts` +12 · `chat.chatrooms.spec.ts` +3 · `instance-list.rooms-btn.spec.ts` +11 (new suite).
- Host note carried forward: jest's default 15 workers on Angular TestBed peaked **8.79 GiB** and tripped the 8.37 GiB guard ⇒ **pin `--maxWorkers=8`** for whole-tree FE runs.

---

## 5. G4 — BUDGET — **PASS** (1 advisory)

| Metric | Base `0181dadbf` | HEAD `6fe3ad005` | Delta |
|---|---|---|---|
| Initial bundle (`main-*.js`) | 217 094 B | **217 094 B** | **0 B** ✅ |
| Chunk count | 85 | 85 | 0 ✅ |
| Total output files (browser dir) | 95 | 95 | 0 ✅ |
| Warn headers | 18 | 18 | **0 — identical SET** ✅ |
| Initial total (builder closure) | 5 909 018 B | 5 909 596 B | +578 B (limit ±2048 B) ✅ |
| `initial_file_count` / `lazy_chunk_count` | 36 / 52 | 36 / 52 | 0 ✅ |

- **Zero net-new warnings.** Normalized warn-set diff (paths compared, amounts stripped) = **EMPTY**; the single raw-diff line is the **base-attributed** `instance-list.scss` overrun amount (10.17 → 10.61 kB, same warning path, present at base). House guard's own B6 SET DIFFERENCE confirms: `baseline_set_size 9 / measured_set_size 9 / added 0 / removed 0`.
- **Dependency 0-delta proof**: `git diff 0181dadbf..HEAD -- frontend/package.json frontend/package-lock.json` ⇒ **0 bytes** (constraint 2 — ZERO new npm deps — holds).
- **`/metrics` lazy-route guard untouched AND GREEN**: guard family (`.agents/shared/planning/metrics-unification/fe-t1-build/{bundle-guard.js,bundle-baseline.json,bundle-guard.measure.js}`, `frontend/src/app/pages/metrics/{metrics.bundle-guard.spec.ts,metrics.baseline.pins.spec.ts,metrics-baseline.ts}`, `frontend/angular.json` budgets) — every diff **EMPTY**; guard run against the HEAD build ⇒ `RESULT: GREEN (B1+B2+B3+B6+vendor-invariant+measure-propagation)`.
- ⚠ **ADVISORY — B4b headroom 134 B.** The `/metrics` lazy-only route fetch set = **184 866 B vs the 185 000 B bound** (gz chart chunk 71 054 B vs 82 000 B bound). GREEN, but the lane consumed ~97 % of B4b's remaining margin ⇒ **any further FE addition to the shared lazy graph can red the `/metrics` guard**. Threshold/baseline files untouched. (Count-scope trap disclosed: the published base manifest is browser-output-dir scoped — a whole-`dist/` count reads 97 and would fake a "+2 files" delta.)

---

## 6. G5 — STAGED-READINESS FACTS — **PASS**

| Axis | Result |
|---|---|
| Version bump | **No bump** — `pyproject.toml:3` = `0.31.0`, `daemon/__init__.py:3` = `0.31.0`; `git diff 0181dadbf..HEAD -- pyproject.toml daemon/constants.py daemon/__init__.py` **empty**. Base *is* the v0.31.0 bump commit ⇒ the lane sits ON the current version; the release cut is downstream. (Cut invariant for the release lane: `pyproject.toml` + `daemon/__init__.py` + `tests/test_api.py` + `uv.lock` move together — none touched here.) |
| Migration census | **EMPTY** — `git diff --stat 0181dadbf..HEAD -- daemon/migrations/versions/` empty; zero `.sql` added. HEAD count **133** (supersedes the 119–122 figures in older notes); newest migration `20261116_083000_add_room_resolution_warnings.sql` predates the lane ⇒ `known_schema_gen` untouched. |
| `agents/` tree | **EMPTY** diff ⇒ prompt-artifact baseline (181) and every seal pin byte-stable. |
| Release / boot surfaces | `releases/ scripts/ config.yaml dev.sh` — **EMPTY** diff (no `releases/` in the worktree; install-dir only). |
| Commit inventory | **37** over base: 32 lane (17 docs · 3 feat · 2 refactor · 1 style · 3 test P4 + 5 council/review fixes) + **5 tester-gate** (`be0c9e1c3`, `2572342fb`, `9509be275`, `0d122e0a3`, `6fe3ad005`). |
| Payload hygiene | `git status --porcelain` → only ` M .agents/tidier/notes.md` (agent runtime state) at census time; `.log` additions = **0**; `dist`/`node_modules` additions = **0**; no scratch/evidence inside the tree (821 tracked `.txt` are pre-existing planning fixtures, none from this lane). New-file inventory for the cut: **16 added** (12 planning docs — branch-only, stripped from the stage payload — + `frontend/e2e/group-chat-rooms-gate.spec.ts`, `frontend/playwright.rooms-gate.config.ts`, `frontend/src/app/components/instance-list/instance-list.rooms-btn.spec.ts`, `tests/unit/routers/test_rooms_list_api.py`) + 20 modified + 0 deleted. The e2e/config files are outside the prod `tsconfig.app.json` graph and the stage payload copies `frontend/dist` only ⇒ harmless to the binary — stated explicitly rather than assumed. |

---

## 7. Net-new red ledger

| Class | Count | Detail |
|---|---|---|
| Net-new BE reds | **0** | G3a dual-side digest byte-identical (empty == empty, sha `e3b0c442…`); 33 files / 558 nodes HEAD vs 32 / 517 base; both legs green |
| Net-new red found BY this gate and CLOSED | 1 → 0 | The FE-UI drift guard `TestP11BootNoBehaviorChange` reddened at the live tip on the gate's OWN G2 artifacts (3 unsanctioned `frontend/` paths). Fixed in `c479cef98` (allowlist + lane marker); guard green 39/39 **after commit**; counter-mutation-proven non-vacuous (reverting the three entries re-reds it 2F/37P) |
| Net-new FE reds | **0** | HEAD-only failing cases = 0; BASE-only = 0; 53 additive cases all green |
| Net-new e2e reds | **0** | G2 legs (a)–(i) all PASS against the real daemon |
| Net-new warnings | **0** | warn SET identical (18 == 18) |
| Quarantined / skipped in this gate | 0 | no test was quarantined by this gate; no pre-existing red was left red |

**Pre-existing reds carried forward (unchanged, not lane-attributable)**: the 9 `Intl.supportedValuesOf` FE failures (byte-identical at base) + the 4 load-only `metrics.banner-geometry` failures (green when the host is not starved).

---

## 8. Disclosures

1. **Council condition (a)(ii) — the adjacency row executed.** The literal un-skip FAILED under the stubbed config for a **fixture defect** (`page.goto('/')` renders HomeComponent; `.toggle-buttons` lives on the instance-detail deep link) — root-caused and byte-restored; the **real-daemon execution PASSES** (adjacency Δ=1). The row stays visible and skipped in the repo (deviation (a) condition iii). The promotion note must **not** cite the stubbed e2e gate as header-icon-adjacency evidence — the load-bearing evidence is this daemon-fenced run.
2. **B4b headroom 134 B** on the `/metrics` lazy-route guard (§5) — green, but effectively exhausted.
3. **`chat.scss` one deleted shipped line** — a semantics-preserving selector-list widening; the lane's "pure-append" phrasing needs this qualifier (§G3b).
4. **Popup title needs `metadata.title`** — the instances API exposes no `instance_name`; an instance with neither name nor title in the payload cannot be named in the popup (§3.4-4). Product observation for the lane backlog.
5. **Shared dev-DB side effects** during the G2 daemon boot (routine `discard_on_startup` + pruning; `instances` 52→48 unattributed) — anomaly `b41983a9` (§3.4-8).
6. **G2 fence additions**: `ENSEMBLE_DB_AMBIENT_ALLOW=1` (boot refusal otherwise) and `LIMITS_METRICS_LISTENER_PORT=8092` (PORT+1 derivation) — disclosed, both fenced away from live.
7. **W1 shipped-surface delta (council condition 1)** — `describeHttpError`'s status-class split changes the 5xx copy on **three** call sites (`:200` shipped rooms lane, `:247` shipped messages lane, `:311` new global lane). Verified LIVE in G2 leg (g): the pane shows the fixed subject-specific constants with **zero** transport-string/URL leak. This is a **shipped-surface 5xx-copy delta** and must be disclosed in the promotion note.
8. **W2 is closed with a published, independently reproduced counter-mutation**; **A6 is closed** with an AST pin that reddened on a re-run shape. **W3** (leader-quote correction) and the **F-E erratum** (D2.2 stale clause — one timer drives the conversation lane only) remain **routed to the leader** and are NOT closed by this gate.
9. **Fence discipline applied throughout**: no live contact (9797/9798 untouched, live install never touched, `ensemble_prod` never contacted), all `POSTGRES_*` set per-variable (the `env -u POSTGRES_*` glob is a silent no-op and was never used), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned explicitly on every daemon-child shell, the whole `ENSEMBLE_AUTH_*` family scrubbed per-variable (issuer-only scrubs surface as census ERRORs), scratch `umask 077`, evidence named `*.txt`, every mutation window byte-restored with PRE==POST sha256 proofs, and no process killed without a port-ownership check (port 8088 never touched).
10. **Gate-owned net-new red, found and closed.** The FE-UI drift guard (`tests/unit/repositories/test_source_gc_repository.py::TestP11BootNoBehaviorChange`) reddened at the live tip on the gate's OWN G2 artifacts — `frontend/e2e/group-chat-rooms-gate.daemon.spec.ts`, `frontend/playwright.rooms-gate.daemon.config.ts`, `frontend/proxy.gcfe-e2e.conf.json` were outside `FRONTEND_UI_LANE_SANCTIONED_FILES`. **Closed in `c479cef98`** (allowlist entries + the one missing lane marker), guard **39/39 green after commit**, counter-mutation-proven (reverting the entries re-reds it 2F/37P, byte-identical restore). **Carry-forward for the lane**: the sanctioned FE-UI surface must be extended **in the same change** that adds any new `frontend/` path — the guard re-reds otherwise.
11. **Credential-hygiene incident (harness defect, not a repo defect).** During the G3a worker's env reconnaissance an unredacted grep echoed the live `ENSEMBLE_AUTH_CLIENT_SECRET` into that worker's transcript. Anomaly filed by the worker: `ebaad48b-2fe9-46a8-b441-87dea1967cec`. This repeats the standing trap already captured in the project's `deferred_key_rotation` harness rule (**scrub the ambient env BEFORE capture; capture name + value sha256 prefix, never the raw value; umask 077**). **User-gated**: rotation remains the user's call under the standing deferred item — the gate takes no rotation action. Recommend the leader treat the standing item as now carrying a second, repeat exposure path.
12. **Gate-clock overage (Tier-1 budget).** The G3a task window ran ~34 min against `ensure.md`'s 30-min Tier-1 hard budget — attributable to the fence re-run, base-worktree setup, and the one allowed re-scope to detached worktrees. Leg execution itself stayed inside per-pack 5-min caps (all groups ≤ ~44 s). Disclosed, non-blocking.
13. **JUnit/jest cache discipline carried forward**: whole-tree jest on Angular TestBed must pin `--maxWorkers=8` (the default 15 peaked 8.79 GiB and tripped the host's 8.37 GiB guard) — the 4 `metrics.banner-geometry` failures are a symptom of that starvation, not a product defect.

---

## 9. Verdict

### **GO-WITH-DISCLOSURES** — 8/8 gate legs PASS · net-new reds **0** · staged-release ready

| Gate | Verdict | One-line evidence |
|---|---|---|
| G1 mock/vacuity | ✅ PASS | W2 counter-mutation independently reproduced (RED at the exact-set assert; byte-exact restore); A6 AST pin non-vacuous (prose → 0 AST nodes) + shape (i) re-run (2 nodes RED, 39 green); 4 tidier flags adjudicated — 2 real defects fixed, 1 intentional, 1 mock async nit, **0 product defects** |
| G2 functional (daemon-fenced) | ✅ PASS | 9/9 legs vs the REAL daemon; adjacency row **EXECUTED** (browser control Δ=1 from the shipped room icon); popup 1050×590 = 82vw×82vh exact; room-less instance 0 buttons; 422/404 live; truncated render; W1/W7 copy with **zero** transport leak; polling 9.4 s; tab-hide 0 polls, maxConcurrent=1 |
| G3a BE dual-side (Tier-1) | ✅ PASS | 33 files / 558 nodes HEAD vs 32 / 517 base; both legs green; FAILED-digest byte-identical (empty == empty) |
| G3b shipped stability | ✅ PASS | 4 shipped pins byte-stable (4 independent proofs); mirror 84/84; chat 7/7; icon pin green; instance-mode pane sha-identical |
| G3c FE jest dual-side | ✅ PASS | 9 == 9 byte-identical failing sets; net-new 0; `banner-geometry` environmental |
| G4 budget | ✅ PASS | 0 B / 0 chunk / 0 warn-set delta; package 0-diff; `/metrics` guard untouched + GREEN |
| G5 staged facts | ✅ PASS | no version bump; migration census EMPTY (133 `.sql`); `agents/` + release/boot surfaces untouched; payload hygiene clean |

**Gate-owned net-new red: 1 found → 0 open.** The only red this gate produced was the FE-UI drift guard reddening on the gate's own G2 artifacts at the live tip — closed in `c479cef98` (allowlist + lane marker), re-verified green **after commit** (39/39) and counter-mutation-proven non-vacuous.

**What this verdict does NOT cover (routed elsewhere, unchanged by the gate)**: council **W3** (leader-quote correction) and the **F-E erratum** (D2.2's stale "both lanes on the same tick" clause — one timer drives the conversation lane only) remain **leader-routed**; the promotion note must **not** cite the stubbed e2e gate as adjacency evidence (disclosure 1).

**Release-lane carry-overs**: the W1 shipped-surface 5xx-copy delta (3 call sites, 2 shipped) must be disclosed in the promotion note; the `/metrics` B4b guard has **134 B** of headroom; the popup title needs `metadata.title` (the API exposes no `instance_name`); the sanctioned FE-UI surface must be extended **in the same change** for any future `frontend/` path.

**Fence discipline**: no live contact (9797/9798 untouched; `ensemble_prod` never contacted; the live install never touched); per-variable `POSTGRES_*` only (the `env -u POSTGRES_*` glob is a silent no-op and was never used); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pinned on every daemon-child shell; whole `ENSEMBLE_AUTH_*` family scrubbed per-variable; scratch `umask 077`; evidence `*.txt`; every mutation window byte-restored with PRE==POST sha256 proofs; no process killed without a port-ownership check (port 8088 never touched).

**Final HEAD at gate close**: `c479cef98cdf6bd1d106bafe6b1b0fa608329b04` (38 commits over base `0181dadbf`). Tester-gate commits: `be0c9e1c3`, `2572342fb`, `9509be275`, `0d122e0a3`, `6fe3ad005`, `c479cef98` — plus the docs commit carrying this report.

---

*Gate executed by the Tester agent; every leg delegated to a worker instance with exactly one skill (test-pack-execution ×3, mock-test ×1, e2e-test ×1, quick-fix ×1, infra ×5). Workers: `gcfe-preflight` (c84e0911), `gcfe-w2mut` (69072086), `gcfe-a6pin` (6a41ecbb), `gcfe-tidier` (6876647b), `gcfe-g2-e2e` (3062077a), `gcfe-budget` (bf2b5bae), `gcfe-v0310` (eddbd9e1), `gcfe-be-reg` (1d8c0c10), `gcfe-fe-jest` (96ad41ef), `gcfe-driftfix` (2a5b9eec).*
