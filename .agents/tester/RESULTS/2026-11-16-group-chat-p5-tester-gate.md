# Group-Chat P5 — Independent Tester Gate (FULL/REGRESSION + behavior)

- **Lane**: `feature/group-chat-p5` @ **`77a869057`** (11 commits from base: implementation `9dbcebe1d` + tidier folds `db54b61f5` + honesty micro-lane `77a869057`) · base **`adbc7bf21`** (= `origin/latest`, P3 merge; verified)
- **Worktree**: `/home/nea/ensemble-src-wt-group-chat-p5` (fenced `.env` mode 600, `POSTGRES_DB=ensemble_dev` @127.0.0.1:5432, `DAEMON_PORT=8079`, `ENSEMBLE_SELF_ENV=dev`, Python 3.14.7 / pytest 9.0.2). Porcelain empty before/after every leg; HEAD unmoved; **no commits, no edits, no fixes** (validate-only).
- **Gate tier**: **TIER-2 mechanically** — `daemon/manager.py` in the diff (+ `daemon/tools/_tool_registry.py`, `daemon/tools/room.py`, and **`daemon/loader.py`** — a boot-path seam NOT in the 6-seam list but touched; treat as Tier-2-relevant). Floor = dual-side `tests/unit` (P1/P2/P3 precedent).
- **VERDICT: ❌ FAIL** — two independent blocking findings:
  1. **R-1 (regression): NET-NEW = 12 deterministic lane-attributable reds** (green at BASE, F/F/F at HEAD). Root cause = the lane's own data changes: 8 agents' `meta.json` `"room"` grants + the new privileged category, with the committed tool-category artifact left stale and 3 committed order/exact-set pins unupdated.
  2. **B-1 (behavior): the joined-room digest block loses its REQUIRED content under an unbounded room name** — name ≥3362 chars (or ~700 chars of `&`, escape expands 6×) silently voids RECENT → ROSTER → guidelines in every turn's block; delivered 4029 vs body-cap 4000. Live path (`room_create` has no name length/charset cap). Contradicts `architecture-recommendation.md:181` (required block content) + `decisions.md:60` ("system message = guidelines + short digest ONLY"). The lane's own A-P5-15 (`decisions.md:405`) already records the unsanitized room name as a live surface.
- **Everything else PASS or NOTE** — FULL/REGRESSION accounting closed exactly on all 10 legs; behavior 4/6 legs fully PASS + 2 with the findings above; packs/ensure.md all green (quarantine-adjudicated); pre-existing-red ledger documented.
- **Workers** (14 dispatches: 13 legs + 2 focused corroborations, 0 re-dispatches, 0 worker failures): gc-p5-infra `6a00efc3` · gc-p5-regbase `0fe67744` · gc-p5-reghead `1ec71b95` · gc-p5-digesteq `8ba4914d` · gc-p5-cat23 `9d325360` · gc-p5-verbs-a `2f7963db` · gc-p5-verbs-b `3ed663d7` · gc-p5-digestblk `7c8ab9c5` · gc-p5-warnid `23ef1cc2` · gc-p5-spot `fe5d0b96` · gc-p5-caprace `87e4216b` · gc-p5-f2confirm `8c47db62` · gc-p5-packs `bd8cb389`.
- **Evidence root**: `/tmp/gc_p5_gate/{infra,base_leg,head_leg,digesteq,cat23,verbs_a,verbs_b,digestblock,warnid,spot,caprace,f2confirm,packs}/`.

## Scope Decision

Full-tier request → mechanical Tier-2 fired (`manager.py` + `loader.py` + `tools/` in the diff). Floor = dual-side `tests/unit` + serial `tests/unit/repositories/` + fence-family closure, both sides; `tests/performance/test_message_api_cost.py` and `tests/packs/g7*` excluded per the pre-exclusion registry (compensating pins named in ensure.md). Regression budget: BASE legs ≈ 2,139 s, HEAD legs ≈ 2,252 s (≈ 73 min total incl. adjudication) — under the 90-min advisory, no breach, no rc=124, no INTERNALERROR, no xdist worker death. No web automation (backend-only surface; FE icon is P6).

## 1. Changeset & collections reconciliation — PASS (exact)

- Diff: **51 files, +4299/−330**. Production ×11 (`daemon/loader.py`, `daemon/manager.py`, `daemon/repositories/room/{models,repository,service}.py`, `daemon/services/context_messages.py`, `daemon/services/room_delivery.py`, `daemon/tools/{_tool_registry,instance,room}.py` [room NEW]); migration ×1 (`20261116_083000_add_room_resolution_warnings.sql`; dir now 133 `.sql`); tests ×29 (2 helpers + 27 test files; **4 NEW**: `test_joined_room_context.py`, `test_room_delivery_refactor_pins.py`, `test_room_registration.py`, `test_room_tools.py`); docs ×2 (`group-chat/decisions.md`, `.agents/tidier/notes.md`); **agents ×8, all `meta.json`, ZERO `*.md`** (the `room` grant allow-list entries only); `test/packs/metrics_read_mutation_matrix_test.py` ×1 (lane-legitimate, verified: the category-count FIND/REPLACE pair is derived from `len(PRIVILEGED_TOOL_CATEGORIES)`; live count = 23 with `room` present; the old id would have gone dead → SKIPPED+FAIL).
- Collections: whole `tests/` BASE **38,514** / HEAD **38,584** (**+70**); `tests/unit` unique BASE **26,659** → HEAD **26,729** (**+70**). Reconciliation: head_only FILES (4) == lane-ADDED files EXACTLY, both directions; base_only files **0**; 0 zero-node / 0 non-test head-only files. Node delta = **+64 new + 6 common-file growth** (room_delivery +1, room_repository +2, room_migration_runner_execution +3) — all in the room family.
- **Baseline corrections (carried from the leg workers; superseed earlier figures)**: the commission's `test_room_*.py` glob = **10 files / 224 nodes**, not 13. The true 13-file room set = every `*room*`-named test file under `tests/` → **192 @ BASE → 262 @ HEAD**; the **257** figure is the intermediate `db54b61f5` (262−5 = 257, the micro-lane's +5 defs). The tidier/shared-KV **"299" does not reproduce** under any definition — do not use it. TestP11 file = `tests/unit/**repositories**/test_source_gc_repository.py` (6 nodes; the `services/` path was wrong). `language_check` = 124 standalone.
- Base-debt parity (standing chain): `tests/manager/test_skill_service_init.py` + `tests/test_instance_title.py` — lane diff EMPTY; serial both-sides → **5F/6P both legs, ids identical**, signatures identical modulo a printed object address. Exact parity.

## 2. Full regression (dual-side, recipe-mandated) — **FAIL: NET-NEW = 12**

### 2.1 Dual-side execution (identical fence + manifest rule; `-p no:randomly`, junitxml, `-n 8`, `t=1750`/shard)

| Leg | Files | Nodes | rc | Counts | Wall |
|---|---|---|---|---|---|
| BASE shard A | 512 | 13,046 | 1 | 112F/1E · 12,873P · 60S | 788 s |
| BASE shard B | 518 | 13,046 | 1 | 108F/24E · 12,884P · 30S | 1,002 s |
| BASE repositories (serial) | 23 | 402 | 1 | 4F · 398P | 307 s |
| BASE fence-family (×2 shapes) | 4 | 165 | **0** | **165P** | 42/45 s |
| HEAD shard A | 511 | 13,079 | 1 | 167F/4E · 12,866P · 42S | 789 s |
| HEAD shard B | 523 | 13,078 | 1 | 70F/18E · 12,947P · 42S +1XF | 1,006 s |
| HEAD repositories (serial) | 23 | **407** | **0** | **407P** | 359 s |
| HEAD fence-family (×2 shapes) | 4 | 165 | **0** | **165P** | 56/42 s |

Accounting closure `collected == P+F+E+S == executed`: **PASS on all 10 legs** (three independent forms). 2 BASE xdist worker crashes — both landed as `<error>` testcases, both in the digest; no node lost. Note: headline node totals are asymmetric because the BASE summary counts the fence family once and the HEAD twice → the true unique delta is **+70**, not +235.

### 2.2 Digest equality (BARE-ID from junitxml `<failure>`/`<error>`, `LC_ALL=C sort -u`; independently re-extracted byte-identically)

| Set | Count | sha256 (prefix) |
|---|---|---|
| base_failed | **249** | `ab01fb46e862c83c…` |
| head_failed | **259** | `e0eb80543cc5037d…` |
| both | **212** | — |
| base_only | **37** | — |
| head_only | **47** | — |

Arithmetic exact (212+37=249; 212+47=259); per-leg junit F+E == digest count on every leg; 0 duplicates.

### 2.3 head_only ladder (47 → **30 flake + 4 pre-existing + 1 ledgered + 12 lane-attributable**)

- **30 composition flakes** — P/P/P isolated at HEAD, P at BASE, files lane-unmodified. Mechanism **proven**: the lane's 4 added test files re-balanced the greedy 2-way split → **702 of 1,034 files changed shard** between legs → different xdist load re-triggered the documented pre-existing event-loop flake (`BaseHTTPMiddleware … Future attached to a different loop`; the same family reds 25 nodes at BASE in `test_slash_commands_router.py`). Files: `test_messages_router_ref_path.py` ×3 · `test_slash_commands_router.py` ×6 · `test_work_router.py` ×4 · `test_graphless_running_durable_dispatch.py` ×2 · `test_paused_auto_resume_fallback.py` ×1 · `test_scheduler_deferred_delivery_pin.py` ×1 · `test_source_stamp_matrix_d4i.py` ×1 · `test_wedge_capture_readback.py` ×1 · `test_prompt_reload_refusals.py` ×2 · `test_prompt_reload_tool.py` ×9. **NOT net-new** (ledger row added below — consolidated, same class as the P3 §2.5 49-flake set).
- **4 deterministic pre-existing** — red at BASE in isolation too (`test_hide_kb_instances.py` ×3 = QUARANTINE:70; `test_slash_commands_router.py::…running_injection` = QUARANTINE:178 routers family).
- **1 deterministic ledgered stale pin** — `test_nest_report_rest_route.py::TestAM1TheRouteSetPin::…` = QUARANTINE:10 (standing, base-attributed).
- **12 DETERMINISTIC LANE-ATTRIBUTABLE, UNLEDGERED → NET-NEW** (3× isolated serial at HEAD = F/F/F; 1× at BASE = PASS; test files BYTE-IDENTICAL to base — lane reach is DATA-side, which is a stronger attribution than a test diff):

| # | id | signature |
|---|---|---|
| 1 | `tests/unit/tools/test_tool_category_artifact.py::TestToolCategoryArtifact::test_artifact_covers_every_registered_category` | `categories missing from the artifact: ['room']` |
| 2 | `tests/unit/tools/test_tool_category_artifact.py::TestToolCategoryArtifact::test_artifact_is_generated_from_the_tool_registry` | artifact drifted — the pin prints the regeneration command (`ENSEMBLE_REGENERATE_TOOL_CATEGORY_ARTIFACT=/tmp/tool-categories.json` + bump `VOCABULARY_VERSION`) |
| 3 | `tests/unit/test_video_capturer_agent.py::TestToolAllowContract::test_allow_exact_set_and_order` | `tools.allow drifted — expected [… 'nest-report']` |
| 4 | `tests/unit/test_video_capturer_agent.py::TestToolAllowContract::test_nest_report_is_last` | `assert 'room' == 'nest-report'` |
| 5 | `tests/unit/test_video_editor_agent.py::TestToolAllowContract::test_allow_exact_set_and_order` | same |
| 6 | `tests/unit/test_video_editor_agent.py::TestToolAllowContract::test_nest_report_is_last` | `assert 'room' == 'nest-report'` |
| 7 | `tests/unit/test_video_producer_agent.py::TestToolAllowContract::test_allow_exact_set_and_order` | same |
| 8 | `tests/unit/test_video_producer_agent.py::TestToolAllowContract::test_nest_report_is_last` | `assert 'room' == 'nest-report'` |
| 9 | `tests/unit/test_video_verifier_agent.py::TestToolAllowContract::test_allow_exact_set_and_order` | same |
| 10 | `tests/unit/test_video_verifier_agent.py::TestToolAllowContract::test_nest_report_is_last` | `assert 'room' == 'nest-report'` |
| 11 | `tests/unit/test_anomaly_report_grant_pins.py::TestTheRepinnedAllowArrayConstants::test_the_ari_trailing_set_is_exactly_the_seven` | ari trailing set drifted vs the pin's expected order |
| 12 | `tests/unit/test_pok_grant_pins.py::TestThePokGrantOrdering::test_the_ari_tail_is_preserved_after_the_grant` | tail list drifted |

Root cause (verified): `agents/video-{capturer,editor,producer,verifier}/meta.json` append `"room"` AFTER `"nest-report"` (breaks nest-report-LAST + exact-allow), `agents/ari/meta.json` gains `"room"` in the trailing set (breaks both ari pins), and `.agents/shared/metrics/tool-categories.json` was never regenerated for the new `room` category (artifact count 65, vocabulary_version 10, `room` absent).

- **base_only (37)** — favourable direction: 1 lane-reach (`test_nest_report_read_registration.py::TestTheGrants::test_the_grant_arrays_keep_a_clean_append_point` — the lane's edit turned it green) + 36 gitless-extraction/composition artifacts (BASE tree has no `.git`; same families as P3 §2.6).

### 2.4 Pre-existing-red ledger

- BASE 249 → T1 exact-id 6 / T2 file-family 112 / T3 unledgered **131**.
- HEAD 259 → 6 / 124 / **129** (102 shared base-red corpus + 15 composition flakes + 12 deterministic).
- **None of the 12 appear in QUARANTINE.md** (0 hits each; 0 hits for `tool_category_artifact`). Ledger documentation debt: the unledgered class is the same class carried by P1/P2/P3 (documentation debt, not a new class; grew vs P3 — see §7 note).

## 3. Behavior validation (service/tool level, real PG private schemas, worktree venv, dev fence)

| Leg | Verdict | Evidence highlights |
|---|---|---|
| **Category #23** | ✅ **PASS 4/4** | Registry `daemon/tools/_tool_registry.py:483/932` — `len(PRIVILEGED_TOOL_CATEGORIES) == 23`, `room` present, member set EXACTLY the 4 verbs (4 independent derivations agree). Grant matrix: **8 granted** (ari, leader, sonic-leader, worker, video-×4) → 4/4 present; **5 negatives** (sonic-developer, project-manager, maintenancer — 3 same-family; watcher — empty-allow trap; designer) → 0/4. Construction-time wiring PROVEN: `_apply_tool_filter` (`instance.py:8062`) recorder logged 14 invocations — the 4 room verbs were PRESENT pre-filter and stripped for every negative; the category extend (`:7537`) PRECEDES the filter (late-extend bypass pinned by source comment). Corroboration: 287 pin nodes green (21+21+2+234+9) incl. the non-mutating MT-R01 liveness proof (find-string unique; unmutated GREEN; `==22` RED; missing-`room` RED). |
| **room_create + room_post + RK-7** | ✅ **PASS 21/21** | Owner seeded in the same transaction (`repository.py:440-450/:482-484`). Cap-8: 9th raises `RoomJoinRoomCapError` / tool token `room-cap`, count stays 8; closing frees the slot. Tagged/addressed params PROPAGATE to `delivery.route_post` (payload + DB columns + `tag_outcomes`/`addressed_outcomes`; addressee≠taggee → `digest_only`, zero side effects). Typed refusals: `not-a-member` / `room-closed` / `room-not-found` (distinct). **M2 exact**: absent router → post persists + `routing_error:true` + `routing_reason:"delivery-service-unavailable"` (`room.py:582-597`). RK-7 fail-closed on BOTH absent and raise for every write verb, with a control proving the gate is load-bearing; leak discipline held (`internal-error` only, no exception text). |
| **room_read + room_task_decision** | ⚠️ **5/6 PASS + 1 DEVIATION** | Predicate members ∪ owner-ancestry fail-closed (6 legs probed: member allow, outsider deny, owner-ancestor allow, cross-room deny, LEFT deny, member's descendant deny). Cursor ACK: boundary excluded, no rewind. Task-decision: loop-back row exactly-once; duplicate → typed `duplicate-decision` + PG backstop `uq_room_messages_proposal_dedup` (raw DETAIL stays server-side). **M5 split tokens 14/14**: room-service vs delivery-service each name their own component, zero cross-attribution. **DEVIATION (Check 3)**: there is **no degraded-epilogue concept** — a faulted room is SILENTLY DROPPED from the multi-room block (server-side warning only; same block id supersedes the healthy one) and single-room faults collapse to generic `internal-error`. Finding N-8. |
| **Digest block** | ⚠️ **4/6 PASS + F2 DEFECT + F1/F3 notes** | Stamp verbatim `context_kind='joined_room'` (`context_messages.py:195`); own member name + roster (owner marker) + last-3 (exactly, oldest→newest) + guidelines + unread header. Caps 600/2000/4000/3/160 enforced exactly on their targets. ONE block per turn: stable id `joined_room:{instance_id}`, real langgraph `add_messages` supersede, both assembly branches. Compaction hoisting: `context_kind` byte-identical 769→769. `agents/` diff = 8 `meta.json`, **zero `.md`**. **F2 (DEFECT)**: name ≥3362 → RECENT gone; 3396 → ROSTER; 3405 → guidelines incomplete; 3949 → all guidance gone; delivered 4029 = body 4000 + 29-char header. **F1 (NOTE)**: cap applies to the body, not the delivered message; the committed pin `test_the_block_never_exceeds_the_hard_cap` (`test_joined_room_context.py:179/192`) is **vacuous** — counter-mutation (`ROOM_BLOCK_MAX_CHARS`→10⁷) still passes, because its only over-long input is line-capped to 160 (delivered 714). **F3 (NOTE)**: with 3 fat-banner rooms, section 3 vanishes with no marker — but the 3-room state is NOT agent-reachable in v1 (`spawn_instance` takes a single `join_room`). |
| **M1 warnings + H1/H2 + M-ro4 + UUID** | ✅ **PASS 4/4** | M1: closed vocabulary enumerated (`tag|addressed|room|post` × `unresolvable|room-not-found|no-author`); **one** value-supplying writer (`room.py:545`), every site structured; **no `UPDATE room_messages` anywhere** (append-only); live proof — 4 durable `{kind,name,reason}` rows across all three skip classes; PG `jsonb` round-trip. H1/H2: `test_room_delivery.py` **26/26** (incl. the P3 F-1 pin), refactor pins 10/10, tools 21/21; SINGLE implementation proven (one `route_post`, one resolver, one delivery module, one wiring site). M-ro4: 16/16 — drives `make_migration_engine` (no `_executable_sql`/raw-text), PRAGMA equivalence both shapes, driver-dependent constraint fingerprint BOTH directions (SQLite dotted-pair present/named absent; PG named → `MemberNameCollisionError`). UUID: 36-char dashed resolves via tag AND addressing (identical to name-based); exact-match semantics for other forms. |
| **Spot-checks D1/D2/D5/D6/D9** | ✅ observed | D1: read hides at TOKEN level (`room-not-found` for both) but the prose distinguishes — post splits tokens (`room-not-found` vs `not-a-member`) = an existence oracle. D2: cap race REAL (see below). D5: cap is a CHAR cap (`ROOM_BLOCK_MAX_CHARS=4000`), no room-count cap; effective K≈5; earliest-joined win; no truncation marker (bare `...`). D6: **11.0 queries/room** (reviewer's ~4 = service-call level; SQL-level is ~2.75×). D9: agent-visible tokens DIFFER (no conflation as documented); the conflation is latent in the service (`RoomNotFoundError` for both), plus wrong-kind→`no-such-proposal`. |
| **Cap-race adjudication** | ✅ **NOTE** (not DEFECT) | Independent reproduction: overshoot ≤ racer count (16 racers → 13–16; 32 → 25–28; 64 → 19/45; 100 → 59/50; preseed 6+4 → 10 deterministic 3/3; **8/8 is safe** 3/3). Mechanism confirmed (COUNT→raise→INSERT; no lock/SERIALIZABLE/`FOR UPDATE`/trigger; create path takes no lock while append/join do). Contract: `decisions.md:171` (A-P2-10) — *"the caps are cost-discipline bounds, not correctness invariants"* + *"overshoot ≤ concurrent racers at the boundary"* → NOTE. BUT: A-P2-10 scoped the posture to the spawn/join path; the CREATE path never discusses atomicity, and the agent-facing refusal string is **FALSIFIED** in an overshot tree (*"this tree already holds the maximum number of active rooms"* while holding 13) → truthfulness sub-item N-7. Pins are SERIAL-only (prose-satisfiable against the race); RED-ness statically proven (cap absent at `9dbcebe1d^`). Join-side sibling likewise racy (15+4 → 19, cap 16) — covered by A-P2-10. |

## 4. Findings

### Blocking

- **R-1 — 12 deterministic net-new reds** (§2.3 table; fix list §8). Net-new ≠ EMPTY ⇒ regression leg FAILS.
- **B-1 (= F2) — digest-block required content voided by an unbounded room name** (live path; §3 digest-block row). Severity: critical for the block's truthfulness contract; not a crash/data-loss.

### Notes (non-blocking; ranked)

- **N-1** Delivered block exceeds its own hard cap by 29 (`4029 > 4000`, cap on body) and the covering pin is vacuous (counter-mutation survives) — fold into B-1's fix.
- **N-2** No degraded-epilogue behavior: faulted room silently dropped (multi) / generic `internal-error` (single). Design call for P6 (UI/audit) + pilot.
- **N-3** Silent truncation at the block cap: no marker names omitted rooms; bare `...` indistinguishable from a truncated message (D5).
- **N-4** Digest builder cost: **11 queries/room** N+1 (D6).
- **N-5** D1 existence oracle: `room_post` token split reveals existence to any agent holding the tools; `room_read` hides only at token level (prose leaks).
- **N-6** D9 latent service conflation (not agent-visible); wrong-kind seq also maps to `no-such-proposal`.
- **N-7** Create-cap race (NOTE) + **falsified refusal string** under overshoot; create path not explicitly covered by A-P2-10's accepted-residual; pins serial-only. (Join-side covered by A-P2-10.)
- **N-8** No degraded epilogue (see N-2).
- **N-9** `next_after_seq` unclamped (`room.py:712`) — an adopted echoed cursor can skip later messages (`read(after_seq=50)` in a 3-msg room echoes 50; two new posts then invisible to that cursor).
- **N-10** RK-7 is TOOL-layer only — `RoomChatService.append_message` has no author-membership predicate (not reachable as shipped; no defense-in-depth for future internal callers).
- **N-11** Membership-resolution RAISES → generic `internal-error` token (fail-closed, leak-free, less diagnosable; a `membership-unavailable` token would help).
- **N-12** M1 doc cardinality drift: `models.py:519-524`/design prose says 4 reasons (`unresolvable / not-a-member / left / cross-room`); the code's durable vocabulary has exactly ONE (`unresolvable`).
- **N-13** UUID predicate/lookup asymmetry: `_is_uuid_like` accepts UPPERCASE dashed, but the roster lookup is case-sensitive → unresolvable; 32-hex undashed of a dashed roster id also refuses (exact-match by design).
- **N-14** Escaping class is `&<>` only (ADR-7 intentional) — markdown/backtick/quotes and a literal `[SYSTEM CONTEXT:` in a room name stay raw (context-header spoofing vector worth a security-lane look).
- **N-15** The lane registered **ZERO PACKS.md rows** (F-1 from the packs leg) — the room-family pack was unregistered; **now registered by this gate** (§6).
- **N-16** Stale class docstring: `tests/integration/test_maintenancer_spawn_resolves_tools.py:345-346` still says "exactly twenty categories" / prose stops at #17 (assertion literal is correct, 23).
- **N-17** `language_check_unit_test` row claims `111/111 @ 2026-07-12` — stale vs measured **124/124**.
- **N-18** Coverage gap: the room/digest packs run on file-backed SQLite; the PG `jsonb` bind path for `resolution_warnings` has **no committed coverage** (probed live, works).
- **N-19** POK store at its **256-active cap** — writes refused for 4 workers + infra (`pok_active_cap`); needs a curator archive sweep (leader/PM).
- **N-20** POK/shared-KV drift: "299" unreproducible; honest chain **192 @ base → 257 @ db54b61f5 → 262 @ HEAD**.
- **N-21** Fence hazard observed by 3 workers: daemon-child shells carry ambient `ENSEMBLE_SELF_ENV=live` which overrides the worktree `.env` unless explicitly re-exported — every leg re-exported the five fence vars.
- **N-22** Report asymmetry: HEAD headline node count double-counts the fence family (+235 vs true +70).

## 5. Packs + ensure.md Core

- `gc_p5_room_family` (room 13-file set, HEAD): **262/262 PASS** (260 s) — 262 = 257 @ db54b61f5 + 5 micro-lane defs (fully attributed).
- TestP11: **6/6** (0.7 s) · `language_check` STANDALONE: **124/124** (4.4 s).
- Registered `gc_p3_room_concurrency` (verbatim row): **21/21** (24.7 s) — row count exact, no drift.
- Registered `concurrency_atomic_unit_test` (ensure.md Critical): **94P/5F/74S** — all 5 reds map node-for-node to QUARANTINE:153 (`test_instance_metadata_atomic.py` DetachedInstanceError ×5); **zero non-quarantined reds** → requirement satisfied under the quarantine rule.
- ensure.md statics: `dev.sh:159` carries `--timeout-graceful-shutdown 10` (comment :156); await sweep over the 3 P5 surfaces = **79 async sites, 78 AWAITED, 1 RETURNED, 0 BARE/UNCONSUMED** (incl. the P3-flagged `room_delivery.py:714`), 0 "never awaited" runtime warnings.
- **ensure.md Core**: changed-pack item **NOT met** (R-1's 12 reds are in the lane's blast radius) · concurrency integrity **PASS** (quarantine-adjudicated) · no-sync-DB-on-loop **PASS** · dev.sh flag **PASS** → **Critical 3/4**. Important: await-callers **PASS**. No ensure.md contradictions found (pack-mapped, scoped, quarantine-aware, no `-x`).

## 6. PACKS.md registration (gap closed)

Registered `gc_p5_room_family` + the gate legs (§ PACKS.md top block). Invocation: `timeout 600 .venv/bin/python -m pytest <the 13 room files> -p no:randomly --tb=short -q -rf --junitxml=…` → 262 nodes.

## 7. Quarantine status

- Active table **138 rows** (not 3 — the earlier read was the file head only). **+1 consolidated row added** this gate (the 30-file composition-flake class, pre-existing, mechanically explained, non-blocking). No deletions; no lane-new red quarantined (lane-new must be FIXED — R-1 is not quarantine-eligible).
- Coverage impact: none of the 30 is skipped by the tier-2 legs (the ledger is adjudication-grade there); packs implementing skip lists gain the exclusion. Rising backlog (unledgered class 129) is flagged as quality debt (same class as P1/P2/P3, larger).

## 8. Fix list (minimal; for the lane) + re-gate proposal

1. **R-1a (artifact, 2 ids)**: regenerate `.agents/shared/metrics/tool-categories.json` for the `room` category + bump `VOCABULARY_VERSION` (the pin prints the exact command; today count 65 / version 10).
2. **R-1b (8 ids)**: in the 4 video agents' `meta.json`, insert `"room"` **BEFORE** `"nest-report"` — preserves both the exact-allow and nest-report-LAST pins without re-pinning. (Alternative: re-pin deliberately — the contract change is a lane decision.)
3. **R-1c (2 ids)**: ari's trailing allow-set — re-pin the 14-entry value deliberately, or place `"room"` where the pin expects.
4. **B-1**: bound the room name at creation (`RoomCreateInput.name` `max_length` ≈200–256 + service guard; optionally charset) AND/OR budget the banner inside the block so required content (roster/last-3/guidelines) always survives; apply the cap to the DELIVERED message (N-1); add a non-vacuous pin (over-long name → roster+guidelines+digest present) and make the block-cap pin counter-mutation-sensitive.
5. **Re-gate (focused, not full)**: the 12 ids 3× + the digest-block leg (F2/F1) + the 13-file room set + the affected pin files. Rationale: the fix touches `agents/*/meta.json`, the artifact, and `context_messages.py` only — a scoped dual-side on those files is sufficient (full tier-2 optional if the lane prefers).

## 9. Process notes

- Validate-only honored end-to-end: worktree porcelain 0 at every leg start/end; HEAD `77a869057` unmoved; no commits/edits/checkouts. Two workers self-disclosed and fully reverted incidental probe contamination (shared `public` tables; rows deleted, counts verified back to baseline).
- Fences: every leg fail-closed dev-only (`ensemble_dev` @127.0.0.1:5432, `ENSEMBLE_SELF_ENV=dev`), per-variable scrubs (never `env -u POSTGRES_*`), zero leftover schemas, zero `ensemble_prod`/`:9797`/`:8088` contact, no secrets in any artifact (scan clean).
- Tester docs written by this gate (uncommitted — merge lane owns commits): `RESULTS/2026-11-16-group-chat-p5-tester-gate.md` (new), `PACKS.md` (gate section + `gc_p5_room_family` row), `QUARANTINE.md` (+1 consolidated row).


---

## 10. FOCUSED RE-GATE @ `345420588` (2026-11-16; fixes `2ccd579e0` + `345420588` on `77a869057`) — **✅ PASS-WITH-NOTES, both blockings CLOSED, phase axis CLOSED**

3 legs (gc-p5-regate-b1 `3620d86f` · gc-p5-regate-b2 `e32b36dd` · gc-p5-regate-rerun `67198d24`); evidence `/tmp/gc_p5_regate/{b1,b2,rerun}/`; porcelain = exactly the tester's 3 own artifacts before/after; fence dev-only on every run (ambient `ENSEMBLE_SELF_ENV=live` re-pinned per-variable); validate-only (all counter-mutations in-memory / /tmp HEAD copies; worktree untouched).

### B1 (R-1) — CLOSED
- **Artifact**: count **66**, `vocabulary_version` **11**, `room` present; live-registry cross-check = 4 tools exactly. **Regeneration provenance**: pin's own command → regen file **byte-identical** (sha256 `66832459…` both) ⇒ machine-generated, no hand-edit.
- **Orderings**: 4 video metas end `[…, "room", "pok", "nest-report"]` (nest-report LAST) — 76 ordering pins green; pok-adjacency pin green, `room` adds **zero** adjacency offenders (`GRANT_POST_TAIL = {"guidelines","room"}`).
- **Ari re-pin legitimate**: pinned sets == `meta.json` exactly; citations present (A-P5-2, `decisions.md:379`); census re-derived — exactly **8** `meta.json` carry `room` (ari, leader, sonic-leader, worker, video-×4) ⇒ deliberate grant-holding contract update, not a workaround. Note: one node id renamed by the fix (`test_the_ari_tail_…` → `test_ari_tail_is_preserved_after_the_grant`).
- **The 12 ids: GREEN ×3** (isolated serial rounds, 0 flakes).
- **D2 wording TRUTHIFIED**: "this tree is at or near the active-room cap (ROOM_MAX_ACTIVE_ROOMS_PER_TREE=8)…" (`room.py:497`); check unchanged; the typed error states the actual count.
- **A-P5-17a..e**: all five exist (`decisions.md:413-425`), each spot-checked against real code/tests — (d) non-vacuity independently re-proved **4/4 counter-mutations RED** (digest-branch, fair-share, label-cap, charset).

### B2 (B-1) — CLOSED
- **Name bound**: `ROOM_NAME_MAX_CHARS=64` + pattern `^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$` + typed `invalid-room-name` (`room.py:145/162/165/420-432`), body-enforced BEFORE any DB touch (8 refusals → **0** `create_room` calls, tables unchanged). **Boundary exact: 64 CREATED / 65 REFUSED**; 23/23 loophole matrix (injection-ish `[SYSTEM CONTEXT:`, newline, `&`×700, unicode, 3362/3396/3949-char, leading space/dot/dash, empty, angle brackets) all typed-refused pre-DB. Edge: trailing space is pattern-legal (benign, noted).
- **Legacy rendering**: 5000- and 3000-char names inserted directly in DB render with guidelines+ROSTER+own-name+RECENT **all present** (delivered 1581; label cut at 384) — the prior loss shapes are dead at render too.
- **Delivered-cap rework**: wrapper INCLUDED ≤ 4000 (3 fat rooms → delivered **3999**); digest shrinks FIRST (530→31); label 384 / floor 16 / digest lines 160; guidelines/roster/own-name **5/5 under maximal 5-room pressure**; overflow LOGS verbatim (`joined-room block exceeded the hard cap (6463 > 4000) for 5 room(s) — the mandatory per-room content is never dropped`) with **no false log** when it fits.
- **Non-vacuous pins**: 8 mutation legs — enforcement mutations (elastic cap, digest branch, fair-share, label cap) RED the delivered/legacy pins; charset/bound mutations RED the create-bound pin; restore green 15/15 + 22/22. Honest nuance recorded: `ROOM_BLOCK_MAX_CHARS→10⁷` reddens the CONSTANT pin (the drift guard), the enforcement-site mutations prove the delivered pins — layered by design, not vacuity.

### Re-runs — exact
room family **266/266** (262 + exactly the 4 new defs, reconciled to `345420588`) · digest leg **15/15** · tool tests **22/22** · TestP11 **6/6** · language_check **124/124 standalone**.

### Base attribution — confirmed, not chased
4 reds in the fix-commits' test-file set → **4/4 PRE-EXISTING at `adbc7bf21`** (detached-worktree probes, `daemon.__file__` asserted): project_future-tail ×2, leader rule.md seal staleness ×1, metrics-family sweep ×1 (already 93 offenders at base; P5 adds exactly 2 paths — `daemon/tools/room.py`, the resolution-warnings migration — to an already-red node; **if that sweep is ever repaired, record those 2 in `PRE_EXISTING_DAEMON_OFFENDERS`**). Method note: `git archive` extractions are NOT evidence for git-diff-shelling pins (no `.git` → rc=129 environmental false-red) — use a detached worktree.

### Standing notes carried (unchanged from §4; none blocking)
N-2 (no degraded epilogue — now partially mitigated by the overflow LOG + never-drop guarantee), N-3 (silent truncation — same), N-4 (11 queries/room), N-5 (D1 existence oracle), N-9 (unclamped `next_after_seq`), N-10 (RK-7 tool-layer only), N-11 (generic `internal-error` token on membership-raise), N-12 (M1 doc cardinality), N-13 (UUID predicate asymmetry), N-14 (escaping class — now materially mitigated: injection-ish names refused at create), N-16/N-17 (doc/row staleness), N-18 (PG jsonb coverage gap), N-19 (POK 256-cap — curator needed), N-22 (headline asymmetry). New minors: the renamed ari pin id; trailing-space-legal names; the 2 sweep paths riding an already-red pin.

### VERDICT
**P5 gate: ✅ PASS-WITH-NITS-CLOSED — recorded as PASS-WITH-NOTES.** Both commissioned blockings verified closed at `345420588` with mutation-grade evidence; zero lane-new reds; all commissioned counts exact. The carried notes are P6/design-lane material, not P5 blockers. **Phase axis: CLOSED** (tester axis; full suite re-run not required — the fix range is `meta.json` ×4 + artifact + room.py/context_messages.py pins; a full tier-2 remains optional for the lane if desired).
