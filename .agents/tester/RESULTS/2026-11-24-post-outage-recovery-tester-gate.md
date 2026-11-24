# Test Report: fix/post-outage-recovery — Tester Gate (Emperor commission, post-outage wedge taxonomy)

Date: 2026-11-24 (gate window 12:57–16:15 UTC)
Gate lane: `fix/post-outage-recovery` @ HEAD `285187a80` (pack-wiring commit `ee27ae970`), base `origin/latest` `a52b63dce` — 30 commits, 48 files, +14,698/−270
Worker instances: `d5c3513f` (discovery) · `d13b2512` (boot smoke) · `f0bc1066` (pack wiring) · `4243b412` (repros) · `83c30f1e` (regression) · `b0d91dfb` (ensure.md Core)
Tier: **Tier-2 (whole-tree dual-side) — MECHANICAL trigger** (diff touches `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py` from the ensure.md core-seam list)

## Summary

- **Deliverable 1 (pack wiring): PASS** — 5 lane packs created + registered + executed at HEAD: **187/187 nodes green**; source-gc extension 43/44 (1 base-attributed red). Commit `ee27ae970`.
- **Deliverable 2 (regression, both legs): REJECT — 9 confirmed branch-attributable net-new defects** (41k nodes per leg, FAILED-only digest + 3× true-solo adjudication). Budget breached (disclosed fail-loud, §2.2).
- **Deliverable 3 (three-shape repros): PASS with 2 mandated-assertion gaps** — Shape A and Shape C **GENUINE-RED at base** (live-wedge symptom reproduced), Shape B import-blocked-by-construction (census-proven), no flakes, HEAD re-attested 107/107.
- **Deliverable 4 (boot smoke): FAIL — merge-blocking P1** — b4 sweep's first tick dies on production PostgreSQL; SQLite pin substrate false-passes.
- **ensure.md Core: 6/6 independent requirements PASS**; changed-packs row PASS on packs (187/187 + 43/44 base-attributed) — the Tier-2 REJECT is the lane verdict, not a Core-pack failure.
- **Skill bank: LOADS** for the tester lane (346 skills via `skill_list`, all five `pf:tester:*` gate-runner recipes) — the implementer's skill-bank miss does not reproduce here.

**Overall: ❌ NOT READY — 12 implementer-bound findings (1× P1, 2× mandated-assertion gaps, 9× net-new regression defects) + 1 flake classified by name.**

### Census correction
The brief's "~90 new pin nodes" is **2× low**: mechanical `--collect-only` census = **13 new pin files, 187 nodes** (b=31, b2=24, b3=32, b4=51, c=49), plus **+4 two-way pins** in the registered `test_source_gc_repository.py` (40→44). Lane-added nodes = **191** — confirmed independently by the whole-tree per-file attribution (BASE 41,149 vs HEAD 41,340 collected).

### Scope Decision
Full gate commissioned; Tier-2 whole-tree dual-side is mechanically mandated (3 core seams) — no judgment call per ensure.md § Gate Tiers. The 13 pin files were **not orphaned** (dir-glob sweeps execute them incidentally) but **unregistered** — the wiring gap was real and is closed. Red-classification form: `grep '^FAILED'` ONLY per the commission directive (empirically confirmed: 55/61 daemon LOG lines would have been phantom reds under `^(FAILED|ERROR)`); ERROR lines carried in a separate explained ledger.

---

## §1 Deliverable 1 — PACK WIRING (worker `f0bc1066`, commit `ee27ae970`)

| Pack | Script | Nodes | Result | Wall |
|---|---|---|---|---|
| `por_b_boot_consistency_unit_test` | `test/packs/por_b_boot_consistency_unit_test.sh` | 31 | ✅ PASS 31/31 | 37s |
| `por_b2_wedge_repro_pins_unit_test` | `test/packs/por_b2_wedge_repro_pins_unit_test.sh` | 24 | ✅ PASS 24/24 | 58s |
| `por_b3_receipt_supervisor_unit_test` | `test/packs/por_b3_receipt_supervisor_unit_test.sh` | 32 | ✅ PASS 32/32 | 44s |
| `por_b4_stale_running_sweep_unit_test` | `test/packs/por_b4_stale_running_sweep_unit_test.sh` | 51 | ✅ PASS 51/51 (SQLite substrate — see §4/D1 caveat) | 25s |
| `por_c_checkpoint_retention_unit_test` | `test/packs/por_c_checkpoint_retention_unit_test.sh` | 49 | ✅ PASS 49/49 | 19s |
| (extension) `test_source_gc_repository.py` | existing registration (no new script owed — house rule) | 44 | ⛔ 43/44 — 1 RED, BASE-ATTRIBUTED | 50s |

- PACKS.md block `<!-- post-outage-recovery-pack-registration-20261124-begin/end -->` at top (newest-first), 6 rows, census provenance per row; two stale `test_source_gc_repository.py` rows re-annotated 39/33 → **44**.
- Each pack embeds its own slicing proof (`--collect-only` re-derivation, fails on drift).
- Base-attribution proof for the 1 red (known frontend drift debt): the pin diffs a FIXED base `858a85e31..HEAD` over `frontend/`; lane adds zero frontend files; the offender (`d3997ec3e`) predates the lane base. **Confirmed base debt, not lane-caused (dual-side confirmation §6).**
- Two pytest-9.0.2 adaptations disclosed in the block (`--rf` removed → `--ff`; vacuous-count sed false-trip under `-q` → token-anchored grep).
- Quarantine finding: `test_scheduler_deferred_delivery_pin.py` (lane-modified, QUARANTINE rows :147/:156) has **ad-hoc-only** registered coverage — hangs at node 17 in full-file runs (rc=124 at 120s), passes solo (11.47s); the lane's own helper modification (4 green call sites) is covered by the solo-file run. Registration debt flagged (D5).

## §2 Deliverable 2 — REGRESSION, BOTH LEGS (worker `83c30f1e`, skill `whole-tree-base-proven-diff`) — **REJECT**

### 2.1 Slicing proof (D1/D2)

| Side | Collected | Deselected | Nodes run | Wall |
|---|---:|---:|---:|---:|
| BASE `a52b63dce` | 41,149 | 1,278 | 41,106 | 40.55 min (8 shards) |
| HEAD `285187a80` | 41,340 | 1,278 | 41,297 | 37.37 min (9 shards) |
| Delta | +191 | 0 | +191 | — |

- **D1 (HEAD-only, lane-owned): 187 nodes — ALL PASS at HEAD** (shard-08: 187P/0F). Per-file counts match the census exactly.
- **D2 (dual-side comparable):** per-file attribution shows exactly ONE modified file with a count delta — `test_source_gc_repository.py` 40→44 (+4 two-way pins); all other 7 lane-modified files count-identical.
- **Quarantine applied identically on both legs (43 deselects)**: scheduler_deferred_delivery ×27 (its 4 non-quarantined `TestScheduleFireNotifyDeferred` remainder nodes RAN), lcancheck ×9, job_driven_enqueue ×3, nest-report metrics ×3, scheduler_adapter tz ×1; skill_evolution AB node = 0 in the collected set (satisfied by construction via default addopts).
- Pre-exclusions with named compensating pins: `test_message_api_cost.py` (→ `regression_performance` pack), `tests/packs` g7 trap (→ packs via registered scripts), `test_context_injection_hybrid.py` (import-HTTP trap). Zero collection errors both legs.

### 2.2 Budget accounting — **BREACH, disclosed fail-loud**

Total wall **~161 min** (13:34→16:15) vs ≤90-min advisory — **breach by ~71 min (179%)**. Shard-0 re-projection (probe floor satisfied: 5,487 nodes, 977 nodes/min): projected 120.4 min → breach **survives** the re-projection checkpoint = true STOP + one re-scope. **Re-scope taken on overhead only** (batched canonical adjudication; byte-identical mutation reverts) — the commission's whole-tree dual-side net-new guarantee cannot be reduced without voiding the deliverable. Host contention measured throughout (load 14; a qemu VM at 255% CPU + 1.9 GB). Reported loudly, never silently continued.

### 2.3 FAILED-only digests + both-direction diff

| | BASE | HEAD |
|---|---:|---:|
| FAILED-ONLY/BARE-ID nodes | 507 | 529 |
| sha256 | `3610a01a…69ba95ec` | `ae210f7b…a3734faf` |

- `cmp` → DIFFER; `comm -23` (base-only ⇒ FIXED at head): **18** (14 confirmed-fixed, 4 base flakes/composition); `comm -13` (head-only ⇒ NET-NEW candidates): **40**.
- Accounting closure: BASE 40,173P + 507F + 25E + 395S + 6X = 41,106 ✓; HEAD 40,342P + 529F + 25E + 395S + 6X = 41,297 ✓.
- House tool `sweep_digest_verdict.sh` → exit 1, non-vacuous verdict block, form tags `FAILED-ONLY/BARE-ID`.

### 2.4 Net-new adjudication (40 head-only reds → 3× true-solo ladder)

| Classification | Count |
|---|---:|
| **BRANCH-ATTRIBUTABLE DEFECTS** (3/3 FAIL head, 3/3 PASS base, true-solo) | **9** |
| PRE-EXISTING (red on BOTH legs in isolation — sweep composition artifacts) | 30 |
| FLAKY(head) (mixed isolated outcomes) | 1 |

**The 9 confirmed net-new defects** (escalated to implementer — full table §Defect ledger R1–R9):
- R1–R4 `test_ri_off_behavioral_probe.py` (:535/:620, 4 nodes) — report-integrity OFF-state: "the instance-side post-commit fan-out still ran — completion proceeded through the normal path" (seam: `daemon/services/instance_messaging.py`).
- R5–R6 constitution drift (:1289/:62) — `KNOWN_ADMISSION_STATE_WRITERS` missing the new `find_scheduler_delivery_evidence` (seam: `daemon/repositories/source/repository.py`).
- R7 `test_checkpoint_metrics_vocabulary.py:106` — new sweep lane added without updating the frozen vocabulary.
- R8 `test_mapper_e1_stranger_spawn_refusal_pin.py:719` — refused delivery writes `failed-to-dispatch` where DEC-9 CRIT-1 expects `failed` + refusal reason (seams: scheduler adapter + mapper).
- R9 `test_instance_tools.py:2234` — JobItem count 4 vs expected 3 in the send_message path (JAFP violation; seam: `daemon/tools/instance.py`).

**Flake classified by name:** `test_atomic_dequeue.py::...::test_dequeue_with_instance_filter_under_concurrency` — mixed isolated outcomes at HEAD, 3/3 PASS at BASE (lane-suspect race OR host-contention artifact; quarantine decision deferred to leader with the data — not auto-quarantined to avoid masking a potential lane race).

### 2.5 ERROR ledger (GAP-2 compliant)

- Node-id ERRORs: 25 on BOTH legs, IDENTICAL set (builtin_mcp_servers ×17, context7 ×4, wc_wake_pure_hang ×3, pg_round_trip ×1) — **zero net-new ERRORs**.
- Phantom daemon LOG lines (not test outcomes): BASE 55 / HEAD 61, shared patterns (migrations-runner syntax-error ×36 both legs, LLM retry exhaustion, memoir knob, task_processor kwarg, job_feedback_observer signature) — this is the empirical confirmation of the commission's `grep '^FAILED'`-only directive.
- HEAD-only log lines: `JobFeedbackObserver._execution_receipt_callback` attribute error on a bare test stub (×4 — robustness gap, production wires via setter; note to implementer); E1 fire-time refusal line (×1 — intended, test-verified behavior, benign).

### 2.6 Frontend base-debt confirmation (known debt — confirmed, not fixed)

(a) `git diff --name-only a52b63dce..285187a80 -- frontend/` = **EMPTY**; (b) the node is RED at BOTH legs with **byte-identical failure signature** (sha `f2f39841…` both sides, 3/3 deterministic at HEAD). **Confirmed base debt, NOT net-new.**

### 2.7 Drift-guard two-way pins A–D re-derivation — all four RED under mutation, restores byte-identical

| Pin | Mutation | RED | Revert |
|---|---|---|---|
| A `:1284` | neuter `_manager_hunks_missing_sanctioned_marker_from_diff` → `return []` | ✓ | sha `fe9df6e0…` restored |
| B `:1302` | drop `"boot-recovery"` from `SANCTIONED_LANE_MARKERS` | ✓ | same |
| C `:1318` | exempt `daemon/sources/cleanup.py` in `_blanket_checked_paths()` | ✓ | same |
| D `:1337` | re-add `daemon/sources/registry.py` to `NOT_TOUCHED_PATHS` | ✓ | same |

Final restore proof: 44-node file → 43P/1F (the confirmed base-debt node, expected). Porcelain 0.

### 2.8 Teardown + invariance

Both throwaway worktrees removed + pruned (`worktree list | grep -c por-gate` = 0); lane worktree tracked-porcelain unchanged (the untracked RESULTS file is sibling tester activity).

## §3 Deliverable 3 — THREE-SHAPE REPRO EXECUTION (worker `4243b412`, skill `test-pack-execution`)

Evidence: `/tmp/por-repro-evidence/` (28 files). HEAD re-attested at `ee27ae970`: **107/107 PASS**.

| Shape | Pin file(s) | HEAD | BASE | Classification |
|---|---|---|---|---|
| **A** — boot-window GC row integrity | `test_boot_wipe_receipt_pins.py` (5) | 5 PASS | **3 F / 2 P** | **GENUINE-RED** — pins ran against base production code; defect reproduced |
| **B** — terminal-then-de-run (b4 sweep) | sweep trio (51) | 51 PASS | 0 run — 3× collection ERROR | **IMPORT-ERROR-IMPOSSIBLE** — `daemon.services.stale_running_sweep` absent at base (git-object census: 0 base / 14 head refs); at base NO mechanism de-runs an all-terminal running instance — that absence IS the defect |
| **C** — pending task claimed + turns (live wedge 19:00Z) | `test_resume_pending_redrive_pins.py` (8) | 8 PASS | **5 F / 1 E / 2 P** | **GENUINE-RED (5)** — base resume returns None → `no_active_job` (base log: `route_outcome=invalid_or_missing_handle — no suspended or paused turn found`), task pending, message ready. 1 import-blocked node; 2 green = designated control + negative arm |
| dedup-latch (b2, wedge 6105e198) | `test_shape_b_dedup_latch_pins.py` (11) | 11 PASS | 2 F / 7 E / 2 P | GENUINE-RED (2) + IMPORT-ERROR (7 — new module-level symbols) + 1 green-at-base by design (root-cause anchor, D4) |
| **Re-arm** (b3) | receipt + supervisor pins (32) | 32 PASS | 0 run — 2× collection ERROR | **IMPORT-ERROR-IMPOSSIBLE** — both modules absent at base; entire Addendum A vocabulary absent at base |

- **no_active_job pin (F2):** 5/8 nodes red at base on exactly the live-wedge symptom; **3× isolated → 3/3 FAIL each** (deterministic, no flake).
- **Re-arm proof assertion coverage:** (a) next-fire **persisted** — **⛔ NOT PINNED** (only in-memory weekday; pin's own comment says persistence out of scope) → **D2**; (b) turn taken — pinned (claim + status + `processing_started_at`); (c) receipt records it — pinned. Addendum A vocabulary: `executed`✓ `dispatched`✓ `never-started`✓ `failed-to-dispatch`✓ `queued`(transient)✓ `deferred`✓ **`suppressed` ✗ zero pins** → **D3**.
- Tester decisions (verification mandate): re-arm gaps → **pre-merge repro addendum to the implementer**; the p5-node import-block is **accepted** as impossibility (file-split = nice-to-have). `test_p2c` base red double-caused (harness attribute gap) — noted.

## §4 Deliverable 4 — FENCED FIRST-BOOT SMOKE (worker `d13b2512`) — **FAIL, P1**

- Ports: main `8090`, metrics `8091` (9798 held by live `ensemble-prod` — pinning mattered); 8079 free; live pids unchanged before/after.
- Boot wall-clock **45.18s** vs 120s Emperor SLO (internal ≈13.1s) — PASS.
- First sweep tick landed immediately (13:15:52, during lifespan startup) and **errored**: `[STALE_RUNNING_SWEEP_DB_ERROR] pass=selection errors=1 : ProgrammingError: (psycopg.errors.UndefinedColumn) column "task_completed_at" does not exist` — summary line never emitted (early return).
- **Root cause:** `_select_candidates` ORDER-BY `CASE` over SELECT-list aliases — PostgreSQL resolves complex ORDER-BY expressions against FROM columns; SQLite resolves aliases anywhere → **the 51-node b4 pin suite false-passes on `sqlite:///:memory:`**. A/B on fenced dev PG 16.15 (SQL captured by spying `sqlalchemy.text`): branch SQL verbatim FAILs; simple-name ORDER-BY passes (loses NIT-4 ranking); **derived-table wrapper passes (fix shape, preserves ranking)**.
- **Impact:** b4 dead on production PG — every boot + 300s tick: 1 ERROR, 0 candidates, 0 heals, no summary. Fail-safe (read-only) but the commissioned wedge remedy never fires. **Merge-blocking.**
- Fence hygiene: ZERO kills (the `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` pin was load-bearing — the ambient value is set-but-EMPTY, which ENABLES the kill sweep in both lanes); clean SIGTERM shutdown (exit 143, ports freed, no orphans); porcelain byte-identical.
- Worker follow-ups: anomaly `d1130072` (critical), POK `sql-dialect-01-pg-order-by-alias` + `por-d1-b4-sweep-pg-blocker`, skill `postgres-order-by-alias-resolution-sqlite-false-pass`, KB entry.

## §5 ensure.md Core validation (worker `b0d91dfb`, skill `ensure-validation`)

| REQ | Tier | Status |
|---|---|---|
| Deadlock/concurrency — `concurrency_atomic_unit_test` | Critical | ✅ PASS (quarantine-adjudicated): 94P/74S/5F — all 5F are QUARANTINE row-154 members node-for-node, base-proven at `f49006b92` |
| No sync DB calls on the event loop | Critical | ✅ PASS — 12 thread-identity nodes cited |
| `dev.sh` `--timeout-graceful-shutdown 10` | Critical | ✅ PASS — `dev.sh:159` |
| Converted async callers await | Important | ✅ PASS — static, all sites cited |
| Original deadlock scenario | Important | ✅ PASS — covered |
| No dead code from the fix | Nice-to-have | ✅ PASS — 2 "deleted" symbols relocated + still referenced |
| No regressions in changed packs | Critical | ✅ PASS on packs (187/187 + 43/44 base-attributed) — the lane-level verdict is the §2 REJECT, not a pack failure |

Standing pre-existing debt observed (not lane-caused): the concurrency pack's `set -euo pipefail` makes its `RESULT:` line unreachable on pytest failure (rc≠0 + no RESULT = FAIL-as-a-pack-run, already documented in QUARANTINE row 154) — pack-hygiene fix suggested, left unfixed mid-gate by scope decision.

## Defect ledger

**Critical → implementer; flake → classified by name.**

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| **D1** | 🔴 P1 (merge-blocking) | b4 sweep `_select_candidates` SQL dead on production PG (ORDER-BY alias resolution); SQLite pins false-pass; sweep silently non-functional on PG | → implementer. Fix verified read-only: derived-table wrapper (preserves NIT-4 ranking) + PG-shaped pin. Re-run boot smoke after fix |
| **D2** | 🔴 critical | Mandated re-arm assertion (a) — next-fire **persisted** — pinned by ZERO tests | → implementer, pre-merge repro addendum |
| **D3** | 🟠 major | Addendum A state `suppressed` asserted by ZERO pins | → implementer, same addendum |
| **R1–R4** | 🔴 net-new (merge-blocking set) | `test_ri_off_behavioral_probe.py` :535/:620 ×4 — report-integrity OFF-state broken: post-commit fan-out still runs when flag off (seam `instance_messaging.py`) | → implementer (regression REJECT) |
| **R5–R6** | 🔴 net-new | Constitution drift ×2: `KNOWN_ADMISSION_STATE_WRITERS` missing `find_scheduler_delivery_evidence` (`source/repository.py`) | → implementer (update KNOWN set or revert writer) |
| **R7** | 🔴 net-new | Checkpoint metrics vocabulary: new sweep lane not added to frozen set | → implementer |
| **R8** | 🔴 net-new | E1 refusal writes `failed-to-dispatch` where DEC-9 CRIT-1 pin expects `failed` + reason (scheduler adapter + mapper) | → implementer (reconcile vocabulary vs DEC-9) |
| **R9** | 🔴 net-new | JAFP: JobItem count 4 vs 3 in send_message path (`tools/instance.py`) | → implementer |
| **F1** | 🟡 flake | `test_atomic_dequeue.py::...test_dequeue_with_instance_filter_under_concurrency` — HEAD-mixed / BASE-3P (lane-suspect race or contention artifact) | classified by name; quarantine decision deferred to leader with data |
| **D4** | 🟡 minor | `test_p1_masquerade_root_cause…` passes base+head (root-cause anchor, not fix-proof) — excluded from branch evidence | labeling note |
| **D5** | 🟡 minor | `sched_alarm_acceptance` ad-hoc-only registration; quarantined scheduler pin file hangs at node 17 in shard runs (solo-safe) | → scheduler/pack owner |
| **D6** | 🟡 minor | Source-gc F-2 compensator's fixed base stale vs frontend drift in origin/latest | → drift-guard owner (pre-existing; lane innocent, dual-proven) |
| D7/D8 | 🟢 nice-to-have | Shape C p5 import-block accepted; `test_p2c` double-caused red | notes to implementer |
| — flake (packs) | — | **None** — all headline base reds 3×-deterministic | n/a |

## Action Needed

- [ ] **Implementer: fix D1** (derived-table wrapper + PG-shaped pin), then re-run the fenced boot smoke — the PG-shaped evidence is deliverable 4's harness
- [ ] **Implementer: repro addendum D2/D3** (persistence pin + suppressed-state pin) before merge
- [ ] **Implementer: resolve R1–R9** (9 net-new regressions; the Tier-2 gate REJECTs until net-new = 0 after adjudication)
- [ ] Leader: F1 quarantine decision (lane-suspect flake — data in §2.4)
- [ ] Drift-guard owner: re-anchor the F-2 fixed base (D6)
- [ ] Scheduler/pack owner: named solo-file pack with quarantine deselect (D5)

## Documentation Updated

- [x] `.agents/tester/PACKS.md` — post-outage-recovery registration block (6 rows) + 2 stale rows re-annotated; commit `ee27ae970`
- [x] `test/packs/por_{b,b2,b3,b4,c}_*.sh` — 5 new lane packs; commit `ee27ae970`
- [x] `.agents/tester/RESULTS/2026-11-24-post-outage-recovery-tester-gate.md` — this report (final; evidence commit follows)
- [x] POK: `repro-assertion-coverage-01` + `por-d1-b4-sweep-pg-blocker` (tester), `sql-dialect-01-pg-order-by-alias` + `post-outage-01/02` (workers), `repro-base-leg-classification-01` (repro worker)
- [ ] QUARANTINE.md — no new rows (F1 deferred by decision; no lane-caused pack-blocking flake)

## Code Changes Summary

- Commit `ee27ae970e0ddf5d38a6bcdebbd1e213524389e0` — `test(packs): register post-outage-recovery lane packs (b/b2/b3/b4/c, 187 nodes) — executed at HEAD` (5 pack scripts + PACKS.md; 939 insertions; explicit-path staging; NO push — branch 31+ commits ahead of origin/latest, push fence honored)
- This RESULTS file + the b4 pack-row PG caveat — evidence commit (hash appended below on landing)

---

### Overall Status

- Pack wiring: ✅ PASS (187/187, registered + executed)
- Regression (both legs): ❌ **REJECT — 9 net-new branch-attributable defects** (budget breach disclosed)
- Three-shape repros: ✅ PASS with mandated-assertion gaps (D2/D3)
- Boot smoke: ❌ FAIL (D1 — merge-blocking P1)
- ensure.md Core: ✅ 7/7 (scoped)
- **Testing Complete: ❌ NOT READY — D1 + R1–R9 + D2/D3 are implementer-bound; re-gate after fixes (packs re-run + a fresh Tier-2 dual-side pass + re-run boot smoke)**
