# Group-Chat P6 — Independent Tester Gate (FINAL PHASE: UI/audit + GC + backlog)

- **Lane**: `feature/group-chat-p6` @ **`e6d98427296159d47bd3e432aec5ad2d1fa56974`** (32 commits over base **`858a85e31`** = the P4‖P5 wave merge; both verified by `rev-parse` in-leg) · Worktree `/home/nea/ensemble-src-wt-group-chat-p6` (fenced, porcelain-clean before/after every leg; HEAD unmoved; **no fixes, no commits, no pushes — validate-only**).
- **Gate tier**: **TIER-2 mechanical** (commission: `manager.py` + graph-adjacent touched). Floor = dual-side whole `tests/` tree (recipe: `whole-tree-base-proven-diff` + `failed-digest-equality-gate` + `ab-base-leg-provisioning-artifacts`, all read before execution).
- **VERDICT: ❌ FAIL** — exactly ONE blocking axis: **§1 full regression, net-new = 6 deterministic lane-attributable reds** (all test-pin debt; root-caused; fix list below). Every other axis PASS: FE web automation (a)–(f) · GC behavior 3/3 · §9-cap + M3 envelopes + SSE observability · all 4 registered packs · spot-checks. One important report-only finding (SSE pre-branch predicate asymmetry, F-3) + two advisories.
- **Workers** (10 dispatches incl. 1 liveness probe; **0 re-dispatches, 0 worker failures, 0 terminations** — the one watchdog hang-notice on the regression leg was adjudicated ALIVE from artifacts per the house lesson, probe evidence `p6-gate-probe`): reg `c6f62161` · fe `b5009d3c` · spot `ff6cb206` · probe `6eef1074` · gc `4862225c` · cap `7e0b9074` · scen `6d41cb55` · roomfam `0e086bb2` · lanescope `c211f576` · mut `31b215ac`.
- **Evidence roots**: `/tmp/gc_p6_gate/{digesteq,fe,gc,cap,scen,roomfam,lanescope,mut,spot}/` (adjudication summary at `digesteq/head_only_ladder_adjudication.txt`; FE index at `fe/browser_checks.json`; consolidated behavior index at `cap/EVIDENCE_INDEX.md`).

## Scope Decision

Full-tier commission (final phase gate, FE surface ships this phase) → full dual-side tree + the mandatory FE web-automation pass + service-level behavior legs + all registered pack re-runs at HEAD + reviewer-deferred spot-checks. No scope reduction. Regression budget **BREACHED, reported loudly**: ≈2h13m vs the 90-min advisory (drivers: whole-tree 38.7k nodes, 4 shard-legs 14–18 min each, one ladder restart after a cwd bug ≈30 min lost; per the tier contract the breach stands disclosed, not silent).

## §1 Full regression (Tier-2 dual-side) — **FAIL: NET-NEW = 6**

Collections **exact-exact** (BASE 38,630 / HEAD 38,727; Δ+97 = 9 lane-added files + 10 common-file growth; head_only FILES == lane-added EXACTLY both directions; base_only files 0). Accounting closure PASS on all 8 legs. Digest equality (BARE-ID, independently re-extracted): base_failed 627 / head_failed 619 / both 559 / base_only 68 / **head_only 60** → ladder (3× isolated serial): **52 PPP** (composition flakes, zero lane-modified files; 2 files ride the P5 ledger family) + **8 FFF** = **6 net-new + 2 ledgered** (T1 QUARANTINE rows: `test_bearer_p5.py` bootstrap-consumption; `test_nest_report_rest_route.py` AM1 family — both exact-row matches).

**The 6 net-new (deterministic, root-caused, green at BASE / FFF at HEAD):**

| # | Node | Signature | Root cause |
|---|---|---|---|
| 1–4 | `tests/unit/metrics/test_metrics_query_scope_cage.py::TestThreeWayCage::` `test_the_declared_route_census_reads_117` / `test_the_mount_census_stays_39` / `test_the_class_manifest_family_count_stays_35` / `test_the_three_way_form_holds_together` | `assert (119, 40, 36) == (117, 39, 35)` | The lane's new `/api/rooms` mount moved the censuses to 119/40/36 (A-P6-9). The lane updated BOTH sibling manifest sites (`test_router_scope_manifest.py`, `test_router_class_manifest.py`) but **missed the cage file's own pinned copy**. Cage file untouched by the lane; 4P in isolation at BASE. |
| 5 | `tests/integration/test_lcancheck_family_separation.py::TestS3WholeTreeCensus::test_s3_whole_tree_census` | 4 docstring hits in `daemon/services/context_block.py` classified LIVE | Lane-ADDED file (A-P6-18) whose module docstring honestly documents the retired `completion_check_note` surface — literal needles trip the whole-tree census; 1P at BASE. |
| 6 | `tests/unit/test_attestation_lca_note_removed.py::test_no_completion_check_note_constant_or_factory_anywhere_in_daemon` | same `context_block.py` docstring needles | Same root cause; 1P at BASE. |

**base_only 68** = favorable direction, mechanism-classified: gitless-extraction artifacts (git-pins vacuous in `git archive` tree — the declared known class), 1 provisioning artifact (`plugin_registry` checkout-name family, P at HEAD), composition flakes (mirror image), T2 ledger families. **Pre-existing-red ledger**: BASE 627 → T1 23 / T2 177 / T3 427; HEAD 619 → T1 24 / T2 180 / T3 415 (full id lists `/tmp/gc_p6_gate/{base,head}_leg/failed_ids.txt`). Known inputs verified: AM1 family 3 nodes; skill-evolution A/B node not exercised red; **A-P6-18** (`.agents/shared/planning/group-chat/decisions.md:502`) verified to hold at HEAD — the metrics-family offender `test_the_daemon_diff_is_confined_to_the_allowed_files` is red at BOTH legs (`both` set) = base-proven, exactly as adjudicated. TestP11 39/0 at HEAD — exact match to dev baseline.

## §2 FE web automation (mandatory) — **PASS**

Real Angular UI, headless browser, real fenced dev daemon (port 8093; first boot exit-78 resolved via the fence's own sanctioned `ENSEMBLE_DB_AMBIENT_ALLOW=1` first-boot path; POST+DAEMON_PORT both pinned; sweep=0; 8088/9797/8079 never touched), FE at 4290 (temp proxy conf — zero repo edits).

| Check | Verdict | Key evidence |
|---|---|---|
| (a) icon adjacent to WATCHOVER | PASS | `.toggle-btn.room-btn` + `.watchover-btn` share `.toggle-buttons`; `roomBtn.previousElementSibling === watchBtn`; `aria-label="Chatrooms"` |
| (b) LARGE modal | PASS | `cdk-overlay-pane room-dialog-panel`, computed **1312×738 px = 82vw×82vh** @1600×900; `tooltipPresent=false` |
| (c) rooms pane lists seeded room | PASS | seeded room rendered (name/active/2-members); other tree's private room NOT listed |
| (d) ascending + load-more | PASS | page1 50 msgs strictly ascending (wire seq proof); load-more → 61; |
| (f) clamped `next_after_seq` | PASS | wire: page1 `next_after_seq=50` (last returned), page2 `null` — never bogus |
| (e) fail-closed | PASS | member-of-no-room → clean empty state; non-member probe → typed `404 room_not_found`, zero rows (control: owner 200); fabricated id → honest error state + API `404 instance_not_found` |

FE unit baseline: jest whole-tree **9F/4876P** — the 9 reds **base-attributable** (4 spec files byte-identical base↔HEAD, untouched by the lane; only shared edit is a type-only barrel re-export — spot-leg two-proof corroboration). tsc `tsconfig.app.json` EXIT 0 clean; spec project's 978 strictness errors pre-existing, **0 mention "room"**. Cleanup attested (rooms purged via service, 4 instance trees hard-deleted, ports released, porcelain 0).

Notes: room HTTP surface is **read-only by design** → seeding via `RoomChatService` + instance repository (same rows the tools write) — disclosed deviation; seq renders as sort key, not a DOM numeral (🟢 nicety); unselected-modal lower ~48% empty panel (🟢 cosmetic, anomaly `bdcff188`).

## §3 Behavior (service level) — **PASS 3/3 + 1 finding**

**GC end-to-end (leg A, 37/37 ad-hoc + 34/34 cage nodes):** dry-run reports candidate `{trigger: p_close, reason: closed since … (> 14d)}`, purges NOTHING, zero events → arm = durable KV flag (`dry_run_completed`, restart-durable, proven) → real sweep purges **all 3 tables** (`chatrooms`, `room_messages`, `chatroom_members`) → **exactly one** content-free `room_purged` event (`{room_id, owner_instance_id, tree_root_id, messages_purged, members_purged}`) → post-purge read = typed `RoomNotFoundError("… absent or purged — R2 …")` + HTTP `404 room_not_found`. **Idle-72h**: liveness map = status-only over `{idle, paused, queued, running, waiting, waiting_children}` (waiting IS alive — a waiting-member room survives the sweep, `skipped[].reason="live-participants:alive-waiter"`); missing instance row ≠ alive; cursor-guard on P-idle only. **M1 retry-not-pin**: KV-read fault → not latched, retried (read_attempts 1→2), rows intact, room still reported next dry-run; purge-layer fault → `errors` channel, rows intact, no event, retryable; post-clear purge emits exactly one event.

**§9 cap + M3 + SSE (leg B, 12/12 cages + 3 probes):** cap = 4 land (seq 1..4) → 5th typed refusal `Error: room_post REFUSED — reason=post-cap: … budget is 4 posts per member per turn…`, no row written → new root graph task = turn-boundary reset (lands, counter 1) → M2: service-raise AND `sqlite3.IntegrityError` append faults yield `reason=internal-error` with counter UNCHANGED; next valid post lands; new turn still exhausts at 4. **M3 envelopes** (2 fault flavors × 3 sites): `503 {"detail":{"error":"room_service_unavailable",…}}` / `404 {"detail":{"error":"room_not_found",…}}`; leak scan 0 hits; key sets exactly `{detail}→{error,message}`. **SSE 3a observability-only PASS**: zero route_post/enqueue/injection, zero table drift, alias registration only. **SSE 3b FINDING (F-3 below)**. Leg B's 2 census reds (`test_api_router_extraction.py` ×2) proven red at BASE (git-archive base extraction) — base-attributable per A-P6-20, not net-new.

**Pilot scenario re-run**: **9/9** (C1 named members · C2 parallel posts · C3 parent-decides · C4 digest milestones · C5 cross-lane tag · C6 nudge loop-guard · C7 Ruling A no-silent-revive · C8 §9 cap · C9 P6 exit criteria incl. purge + SSE 404).

## §4 Registered packs at HEAD — **4/4 PASS**

| Pack | Result |
|---|---|
| `gc_p6_pilot_scenario` | PASS 9/9 (~10 s; dual-layer timeouts intact) |
| `gc_p6_room_mutation_matrix` | PASS 6/6 (markers exact GC-M01..M06; byte-identity manifest + independent `--verify` CLEAN; tree clean before/after; solo-run discipline) |
| `gc_p6_room_family` | PASS 385/385, 21 files (same census as registration; fence verified at `/proc/<pid>/environ` level) |
| `gc_p6_lane_scope` | PASS 216P/1S/1F — the 1 F is EXACTLY the A-P6-18 base-attributable node (matches the P5-gate red) |

## §5 Spot-checks (reviewer-deferred, observe+report)

- **Operator-lane authz breadth**: room HTTP = exactly 2 read-only GETs, both `instances:read`; room-scope predicate fail-closed (`_require_room_access`, members ∪ owner-ancestry → typed `RoomNotFoundError`). NOTE: the message route's default scope anchor is the ROOM OWNER → an `instances:read` caller can read any room by id across trees; the FE's need is narrower (never sends `?instance_id=`). Report-only.
- **Cursor backward echo**: forward-only (no `before_seq` anywhere); HTTP exhaustion = `next_after_seq: null` vs tool = clamped int (room's last seq) — **cross-surface divergence unpinned as a pair**; negative-`after_seq`→0 clamp unpinned; HTTP beyond-max unpinned.
- **N+1s**: clear — rooms pane = 1 session/3 batched queries (grouped COUNT + MAX over IN); read path = 3 sessions + O(depth≤256) ancestry walks; tool read evaluates the predicate twice (constant, not fan-out).
- **FE jest attribution**: confirmed base-attributable (see §2).

## Findings & fix list (for the dev lane / leader — NOT fixed by this gate, validate-only)

- **F-1 (🔴 blocking, test-debt)**: update `tests/unit/metrics/test_metrics_query_scope_cage.py` pinned counts/docstring 117/39/35 → 119/40/36 (cite A-P6-9), mirroring the two sibling sites the lane already updated. One file. Closes net-new #1–4.
- **F-2 (🔴 blocking, doc-debt)**: re-anchor `daemon/services/context_block.py` module docstring off the literal retired needles (`completion_check_note` / `_make_completion_check_note_message`) — lane-side reword preferred over registering a census witness exemption (owner's call). Closes net-new #5–6.
- **F-3 (🟠 important, report-only; anomaly `de77fd54`)**: SSE room-resolution pre-branch (`daemon/routers/messages.py:984-1049`) takes no caller identity and carries no membership predicate — a non-member naming a room id is routed to the OWNER's live stream and receives content-bearing owner events (checkpoint content, tool results, question-pack replay) + owner-id disclosure. Contrast: the room-read door IS fail-closed for the same outsider. Disposition options (worker analysis): (a) accept+document as operator-lane property; (b) room-scoped content-free channel (smallest change removing content exposure); (c) mirror the read predicate (breaks the owner-ancestry premise the design leans on). Design call → leader.
- **F-4 (🟠 advisory; anomaly `b19a3878`)**: M1 docstring drift in `room_gc.py` — after a failed durable flag WRITE, the RAM mirror lets the next sweep purge without re-reading (the docstring claims read-retry). Bounded (dry-run reported in-tick; restart re-arms); the pinned M1 concern does NOT reproduce. Doc-only fix candidate for the dev lane.
- **F-5 (🟢 cosmetic)**: unselected-modal lower ~48% empty panel (Close link floats). F-6 (🟢): seq not rendered as a DOM numeral in the conversation pane.

## Pre-existing-red ledger (standing)

T1 QUARANTINE rows honored (AM1 route-set family ×3; skill-evolution A/B — not exercised red; launcher 8b-c2 documentary); T2 file-family rows 177/180; T3 unledgered 427/415 (documentation-debt class, whole-tree amplification — same class carried by P1–P5 gates); A-P6-18 metrics offender base-proven (in `both`); A-P6-20 api-router census pair base-proven by leg B. **No new quarantines** (nothing flaky — all deterministic reds attributed).

## Open questions (leader)

1. **Fix-cycle routing**: F-1/F-2 are one-file test/doc fixes — fold into the existing P6 lane (P5 precedent: FAIL → fix → focused re-gate)? This gate stands FAIL until a focused re-gate shows net-new = 0.
2. **F-3 disposition** (a/b/c above) — design decision.
3. **POK 256-cap**: project POK store is at terminal cap; one archive (`sqlmodel-select-import-gotcha`) needs curator authority (anomalies `868e088d`, plus worker-time refusals). Durable facts routed to RAG meanwhile.

## Documentation Updated

- [x] RESULTS/2026-11-16-group-chat-p6-tester-gate.md (this file)
- [x] PACKS.md — P6 tester-gate block (4 pack re-runs at HEAD)
- [x] LESSONS/2026-11-16-p6-gate-census-pin-sibling-miss.md
- [ ] QUARANTINE.md — no changes (no new quarantines)
- [ ] rules/ensure.md — user-maintained, untouched; no contradictions found (tier doctrine followed as written)

## Overall Status

- Regression (Tier-2): ❌ FAIL (net-new 6 — F-1/F-2 fix list)
- FE web automation: ✅ PASS · GC behavior: ✅ PASS · §9/M3/SSE-observability: ✅ PASS · Registered packs: ✅ 4/4 · Spot-checks: ✅ done
- **Testing Complete: ❌ NOT READY** — pending the F-1/F-2 fix cycle + focused re-gate; F-3 disposition requested.
