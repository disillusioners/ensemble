# Test Report: fix/post-outage-recovery — SCOPED RE-GATE (post R-round)

Date: 2026-11-24 (re-gate window ~17:00–18:40 UTC)
Lane: `fix/post-outage-recovery` @ `4ae666c4a` → tester-doc commits `39ab470df` (PACKS/QUARANTINE close) → `c992667e3` (PG-pin registration); base `a52b63dce`; measured code legs BASE `285187a80` (gate-1's adjudication point) vs HEAD `4ae666c4a`.
Workers: `fc305cb1` (boot smoke) · `bb2f6e86` (packs + site verification) · `c438c8f1` (delta dual-side) · `a0581182` (PG-pin registration) · `90b82eb2` (gate-1 evidence commit)
Commissioning corrections (measured beats briefed): census **191** pack-file nodes (b=31, b2=24, b3=34, b4=53, c=49), **197 total lane-owned nodes** vs base (+4 two-way +1 PG +1 persistence); D1′ (delta-new) = **6** nodes, not 4; R7 manifested as **2** nodes; the briefed arithmetic "189+4=193" does not reconcile — recorded by measurement.

## Summary — VERDICT: **✅ READY**

| Deliverable | Verdict | Evidence |
|---|---|---|
| 1. Five lane packs re-run | ✅ PASS | 31/24/34/53/49 — **all green**, slicing proofs OK (no census drift), walls 27–156s; commit-tracked re-annotation `39ab470df` |
| 2. Delta dual-side (Tier-2, scoped) | ✅ **NET-NEW = ∅ both forms** | BASE 12F/478P vs HEAD 2F/493P over the 21-file/496-node delta set; digests `ceccf774…`/`5ee18bd5…`; comm -13 = 0; house verdict tool exit 0; budget 37 min vs 45 target (legs 5.35 min) — **no breach** |
| 3. R1–R9 disposition verification | ✅ all verified | R1–R4: probes **5/5 green at head** (4 red at base, per-node); R5–R6: dict-call reader fix verified, 2/2 green; R7: exactly +2 lanes (stale_running, rdrs_wc_orphan_detector), 0 removals; R8: reason preserved on BOTH error_message + redrive_reason (pin asserts error_message; production writes both); R9: all 4 JobItem mentions are comments/docstrings, **zero allocation sites** — with the provenance correction that the +1 landed pre-freeze (`9c4694a76`), pin 3→4 honest |
| 4. Boot-smoke artifact + independent boot | ✅ D1 CLOSED | Artifact sha-verified (`da274039…`), claims faithful (31s boot, clean shutdown, zero sweep errors, summary line emitted); **independent fenced boot at 4ae666c4a**: boot 28s vs 120s SLO, `[STALE_RUNNING_SWEEP] summary: candidates=0 … errors=0 duration_s=0.083` **emitted** (gate 1: never emitted — selection died), zero sweep ERROR markers, clean SIGTERM shutdown, porcelain invariant, zero kills |
| 5. Drift-guard A–D re-check | ✅ all still bite | 4/4 mutations RED, byte-identical reverts (file sha `fe9df6e0…` == gate-1 restore), final 43P/1F — failure set unchanged (the known FE base debt only) |
| 6. PACKS rows 20–21 close | ✅ done | b3/b4 re-annotated 34/53 @ `4ae666c4a` with per-commit node attribution; PLUS the D1 PG pin registered (`por_b4_pg_selection_unit_test`, executed green 1/1 in 0.87s vs fenced dev PG, `_fenced_dev_target()` refuse-non-dev + transaction rollback) — commits `39ab470df`, `c992667e3` |
| 7. Final verdict | ✅ READY | no lane-attributable red remains; see Defects (all dispositions below) |

## D-series closure

- **D1 (P1, PG-dialect) — CLOSED, four independent legs:** (a) the implementer's persisted artifact (sha-verified); (b) my independent fenced boot (summary line emitted, zero errors); (c) the PG-shaped pin **executed green by the tester lane** (`por_b4_pg_selection_unit_test`, 1/1 vs fenced dev PG); (d) the pin **registered** (gate-admissible). The delta-leg SKIP in the dual-side run was the pin's own by-design contract (skip when fenced dev PG unreachable under scrubbed env) — not a gap; leg (c) is the green evidence.
- **D2 (re-arm persistence) — CLOSED, verified genuine:** the pin (`test_maintenance_rearm.py::TestConsumingFireRearms::test_the_persisted_run_at_keeps_the_chain_weekday`) drives the real re-arm then reads back the **DB row** (`session.get(SourceConfig)` → `config["run_at"]`), asserts the persisted weekday in the knob timezone + the chain-knob echo + the [01:00,05:00) window. Solo green. Disclosed note: the pin rides the whole-tree sweep (the file is sweep-covered, no named pack) — a registration-parity nice-to-have, not a gate gap (the delta dual-side executed it green at head).
- **D3 (`suppressed`) — CLOSED by tester adjudication (fail-honest RESERVED shape):** the pin freezes the closed Addendum-A vocabulary (membership + `SUPPRESSED.value == "suppressed"` + the RESERVED ratification note required in the enum source); production accepts the state on the registry callback path (`registry.py:594`) and the enum comment forbids inventing a writer — a produced-state pin would be vacuous (the exact non-discriminating class gate 1 excludes). The membership pin IS discriminating (member/RESERVED-note/value drift reddens). Shape disclosed for leader override; if a writer ever mints `suppressed`, a produced-state pin is owed.
- **F1 — QUARANTINED (row landed):** causal check re-verified EMPTY (no branch hunks on the dequeue/instance-filter path in `285187a80..4ae666c4a`); row added to QUARANTINE.md with the corrected node id `tests/message_queue_redesign/test_atomic_dequeue.py::TestDequeueAtomicClaim::test_dequeue_with_instance_filter_under_concurrency`, budget/attempts recorded, cross-ref to the standing 2026-09-03 partition-flake row for the same node.
- D4/D5/D8 (minor gate-1 notes) — unchanged, non-blocking, standing debt routed (D5 ad-hoc scheduler-pack registration; D6 FE base debt below).

## Regression proof — layered composition (stated precisely)

The re-gate's dual-side was **delta-scoped** (21 files / 496 nodes: the 7 delta TEST files + the commission-named suites + the 13 lane pin files), NOT a whole-tree re-run. The lane's whole-tree net-new claim composes: **gate-1 whole-tree** (a52b63dce vs 285187a80: 41,106/41,297 nodes, 9 net-new defects — every one in the delta set) + **re-gate delta** (285187a80 vs 4ae666c4a: those 9 FIXED per-node, 10 nodes total incl. R7's second node, plus zero head-only reds) ⇒ **no lane-attributable net-new red at `4ae666c4a` vs base**. The pre-existing corpus debt (gate-1's 507 base reds / 30 composition artifacts) is unmeasured at 4ae666c4a by scope — none of it is lane-attributable (both-legs red at gate 1; the 2 shared delta reds re-proven pre-existing 3/3 both legs true-solo).

## ERROR ledger

BASE 5 phantom daemon-LOG lines → HEAD 0: the observer stub-attribute gap (4×, fixed by the R-round getattr) + the old E1 fire-time refusal path (1×, replaced by the sanctioned failed-to-dispatch shape). Zero node-id ERRORs both legs; zero net-new. (Confirms the commission's FAILED-only directive: all 5 would have been phantom reds under ^(FAILED|ERROR).)

## Commission-premise corrections (for the template)

1. **Base-equivalence premise FALSE:** `285187a80 ≢ 97a1d7e57` — `1ab6cceae` (production: D1 fix + folds) interposes. The gate as run is VALID (BASE = 285187a80 = gate-1's exact adjudication point); but a future commission naming `97a1d7e57` as BASE would silently exclude the b4 PG fix from the delta. Recommend the commissioning template derive BASE from the prior gate's measured code tip, not its evidence commit.
2. **Census arithmetic:** measured 191 pack-file nodes (not 189/193); D1′ = 6 delta-new nodes (not 4 — the PG pin + the persistence pin live outside the pack scripts); R7 = 2 nodes (not 1).
3. **Digest-recipe publication (new canon):** the FE-base-debt signature recipe is now published: **sha256 of the path-normalized `^E ` assertion block from `--tb=long`** → this re-gate's both-legs signature `13d90b2f…` (base == head, byte-identical). Gate-1's published `f2f39841…` prefix is NOT reproducible from its record (12 candidate normalizations tried; the hash recipe was not published) — gate-1 RESULTS documentation gap, disclosed; the equivalent property (same node, byte-identical file sha `fe9df6e0…`, byte-identical both-legs failure) is proven independently.

## Defects (re-gate)

**Lane-attributable: NONE.** Standing, non-lane items:
- 🟡 FE base debt (gate-1 D6): unchanged, both-legs byte-identical, `frontend/` diff empty — drift-guard owner's re-anchor item.
- 🟡 2 pre-existing delta-set reds (`test_instance_tools.py::TestRegistration::test_planner_and_tester_resolve_subtree_messages_via_registry` + `TestSubtreeStatusRegistration::test_leader_planner_tester_opt_in_via_production_seam`): 3/3 F both legs true-solo — pre-existing, not branch-attributable.
- 🟢 D2-pin registration parity (sweep-covered, no named pack) — nice-to-have.
- 🟢 `sched_alarm_acceptance` ad-hoc registration (gate-1 D5) — unchanged standing debt.

## Action Needed

- [x] D1 fixed + PG pin registered + boot smoke re-run (independent) — CLOSED
- [x] R1–R9 resolved — verified per-node
- [x] D2/D3 closed — verified/adjudicated
- [x] F1 quarantined — row landed, causal check stands
- [ ] Drift-guard owner: FE base-debt re-anchor (standing, pre-existing)
- [ ] Scheduler/pack owner: named solo-file pack for the quarantined scheduler pin file (standing D5)
- [ ] Optional parity: named registration for the D2 persistence pin

## Documentation Updated

- [x] PACKS.md — rows 20–21 re-annotated (34/53 @ 4ae666c4a, per-commit attribution); new row `por_b4_pg_selection_unit_test`; b4 ⚠ tail repointed — commits `39ab470df`, `c992667e3`
- [x] QUARANTINE.md — F1 row landed (corrected node id + cross-ref)
- [x] RESULTS/2026-11-24-post-outage-recovery-REGATE-tester-gate.md — this report (evidence commit follows)
- [x] POK: `digest-recipe-01-publish-hash-recipe` (new), prior entries stand
- [x] Throwaway worktrees torn down; lane + main-checkout porcelain invariance proven; no push (branch ahead of origin/latest by the lane commits + 3 tester commits)

## Code Changes Summary (re-gate)

- `39ab470df` — `test(tester): re-gate close — PACKS b3/b4 34/53 @ 4ae666c4a + F1 quarantine row`
- `c992667e3` — `test(tester): register the D1 PG-selection pin (por_b4 pg leg) — executed green at the re-gate freeze`
- This RESULTS file — evidence commit pending below

---

### Overall Status — RE-GATE

- Deliverables 1–6: ✅ all PASS
- D1: ✅ CLOSED (four evidence legs) · D2: ✅ CLOSED (verified) · D3: ✅ CLOSED (adjudicated shape, disclosed) · F1: ✅ QUARANTINED (row landed)
- Regression: ✅ delta net-new = ∅; whole-tree claim composes (gate-1 + re-gate)
- ensure.md Core (carried): packs green; concurrency/DEADLOCK/await checks standing green from gate 1; changed packs PASS
- **Testing Complete: ✅ READY** — no lane-attributable defect remains; standing items are pre-existing/non-lane and routed to their owners.
