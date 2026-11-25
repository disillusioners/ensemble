# Engine Arc Phase 2 PILOT — Tester Gate Report (the operational greenlight evidence)

- **Date:** 2026-11-22 · **Tester role:** dispatch-only gate (all legs worker-executed)
- **Tree:** `/home/nea/ensemble-src-wt-pilot-orphanwatcher` · branch `feature/pilot-orphanwatcher-engine`
- **Tips exercised:** boots A/B @ `e6f7d7169` · flag-shadow, flag-exec, all 8 ceremony legs, the Tier-2 regression @ `21eb5bc3a0d71e061bda6a7f93d37742fa28d08c` (the lane moved +`5b562165e` rider fix +`21eb5bc3a` PACKS refresh mid-gate; every leg is commit-pinned and the movement is disclosed per-leg)
- **TRUE family base:** `8894f86a853c5fcba2173ab29405f02c71c1f3ef`
- **Evidence:** `raw/2026-11-22-engine-arc-p2-pilot-gate/` (24 artifacts: 4 boot daemon logs + evidence dumps, 8 ceremony transcripts, Tier-2 digests/ladder/verdict; the 18 shard transcripts + 337 per-node ladder runs + 2 collection dumps remain in `/tmp/pilot-tier2-*` / `/tmp/pilot-gate/` with paths recorded below)

---

## VERDICT

**GATE PASS — nothing blocks the FLAG-OFF release (the release ships dormant).** The flag-on execution leg — the strongest evidence — proves ENGINE-OWNS on a real boot with zero stage-blockers. The greenlight-for-flip scorecard (§6) shows every boot-scoped criterion SATISFIED; the two remaining OPEN items (the ≥48h live soak, the promote ceremony itself) are post-promote by definition.

---

## 1. Operational legs (≥2 independent boots, flag-UNSET default config)

| | Boot A | Boot B |
|---|---|---|
| Port / PG | 8091 / 19701 (disposable cluster, scratch DB `pilot_gate_a_*`) | 8092 / 19702 (FRESH cluster + FRESH DB) |
| Tip | `e6f7d7169` | `e6f7d7169` |
| **boot_id** | `e9be04f94d1443758adc932c574cfdec` | `bff98250ffa346b1b3fc850b5602ec9a` — **DIFFERENT ⇒ independence proven** |
| Observation | 192.05 s | 195.01 s (≥190 s = 2×90 s + margin) |
| Posture | **12 observer specs** incl. `orphan-watcher` (kill_switch `SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE`, interval 90 s, dead=0, enabled=1) | same (12 rows, all dead=0) |
| **Shadow cadence (orphan)** | **3 × SKIPPED_SHADOW** @ deltas 90.269 s / 90.115 s | **3 × SKIPPED_SHADOW** @ 90.235 s / 90.096 s |
| Zero executions | orphan non-shadow = 0; ANY-job non-SKIPPED_* = **0 rows** | same |
| Legacy OWNS | started-line ×2; SUPPRESSED/executor-registered/DEAD-DETECTION/still-dead all **0** | same |
| **Positive legacy-sweep proof** | planted stale PENDING watcher → legacy tick 2 CANCELLED it (DB `PENDING→CANCELLED` + both cancelled-lines, 12:51:13) | planted → legacy tick 2 cancelled (13:02:06) |
| **Boot SLO (host-frame)** | T0→readyz-200 **26.766 s** vs 120 s (**4.5× headroom**) | **24.687 s** |
| Posture line | `engine boot posture: enabled=True shadow=True registrations=12 observer specs (execute_fn withheld, USE_ENGINE all unset)` | same |
| Teardown | TERM → 1 s clean exit; 8091+19701 freed; scratch removed | TERM clean; 8092+19702 freed |

At flag-unset the orphan job registers as a **structural observer** (execute_fn withheld — JobSpec carries no executor field): the "12+1" question resolves to **12 specs, the orphan job AMONG them as observer**; the executor exists only under the full conjunct (§3).

## 2. Flag-ON shadow leg (staging evidence — port 8093, tip `21eb5bc3a`)

`SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=true`, shadow at shipped default (true). boot_id `caac92915e9c4d5a8d9e50eb31bb79a7`, 324 s observation. **ALL ASSERTIONS PASS:**
- **4 × SKIPPED_SHADOW** for the orphan job @ 90.300/90.120/90.108 s — the engine shadow-observes WITH the flag on.
- Zero engine executions; **legacy still owns** (started-line ×2; SUPPRESSED/executor-registered = 0) and **sweeps** (planted watcher cancelled by legacy tick 4 at 13:16:16).
- **⇒ flag-on-with-shadow is operationally IDENTICAL to flag-off** — proven behaviorally, not by absence-of-error. The flip is two switches and the first switch alone changes nothing.

## 3. Flag-ON execution leg (parity on a real boot — port 8094, tip `21eb5bc3a`)

`SERVICES_ORPHAN_WATCHER_SWEEP_USE_ENGINE=true` + `ENSEMBLE_ENGINE_SHADOW_MODE=false`. boot_id `3e3d6985b49644b99993a4afa1993346`, 235 s observation. **ENGINE-OWNS-PROVEN, 0 stage-blockers:**
- **3 × SUCCEEDED** engine runs @ 90.268/90.149 s cadence, `duration_seconds` 10.0/9.7/12.8 ms, attempt=1, error_class NULL.
- **Causal attribution exact**: the planted watcher's `fired_at` (13:11:00.426996) falls INSIDE engine run id=15's window (13:11:00.425458→.435196) — the engine executor performed the DB write.
- **Ownership in one query**: the orphan job is the SOLE non-shadow job; the other 11 each carry exactly one SKIPPED_SHADOW row (shadow gating still applies to them, not to the migrated one). Orphan shadow rows in this boot: **0**.
- **No legacy loop**: started-line = 0; `legacy loop SUPPRESSED` ×1 + `orphan-watcher pilot: engine executor registered` ×1 (quoted); DEAD-DETECTION/still-dead/**FAIL-TO-LEGACY** all 0 (no executor drop of any variant — the `5b562165e` truthful-drop-log wording never fired).
- Engine stamps the job row: `last_outcome=SUCCEEDED`, `last_run_at` advancing, dead=0.
- Posture line: `shadow_mode=False … shadow=False`; SLO 25 s.

## 4. Clean-checkout pack ceremony (REAL clones at `21eb5bc3a`, tree-identity asserted per leg) — 8/8 PASS

| Pack | Result | Note |
|---|---|---|
| **`engine_orphan_pilot_parity_test`** (the pilot pack) | ✅ **59/59** | the lane's refreshed count confirmed; the parity table printed in full (old-orphan CANCELLED/CANCELLED SWEPT · young-orphan SKIP_GRACE_WINDOW · live-parent SKIP_LIVE_PARENT · already-cancelled/fired SKIP_NOT_PENDING · cancelled 1/1 MATCH) |
| `engine_diff_audit_test` (pilot-modified) | ✅ 2/2 | containment **2 actual ⊆ 48 expected** (BASE_SHA re-anchored `8894f86a`); can-it-fail self-proofs green; real-clone git history valid |
| `engine_dormant_posture_test` | ✅ 3/3 | the spy posture intact under the pilot's boot-block edit |
| `engine_boot_unit_test` | ✅ 26/26 | wiring-order pins hold |
| `engine_boot_slo_gate` | ✅ 21/21 | method wiring only (standing disclosure: the SLO-1 number remains unestablished by this pack) |
| `engine_guardrails_unit_test` | ✅ 40/40 | 13/18/9 — the flag-family pins green |
| `engine_execution_unit_test` | ✅ 40/40 | the DB_OFFLOAD dispatch machinery green |
| `engine_observability_unit_test` | ✅ 93/93 | confinement pin GREEN (the pilot's 2 daemon paths already absorbed: api.py in ALLOWED_DAEMON_DIFF; orphan_watcher_sweep.py in PRE_EXISTING_DAEMON_OFFENDERS) |

## 5. Tier-2 whole-tree regression (core seam `daemon/api.py` touched ⇒ mechanical Tier 2)

Base `8894f86a` vs head `21eb5bc3a`; BASE's 40,549 node-ids run on BOTH legs (symmetric 9-shard composition); 23 head-only nodes (the new parity file) all green separately; tree-identity asserted both legs; 26-name per-variable scrub + a **33-name derived AUTH union**; LLM fence; pre-exclusion registry exactly per ensure.md (3 exclusions, named pins).
- **Sweep-level: 17 head-only / 39 base-only** (GREP-FULL and BARE-ID agree); comparator non-vacuous.
- **Adjudicated NET-NEW = ZERO**: all 56 union ids re-run 3× isolated per side (**336 runs**) — 0 ids are HEAD-FAIL-while-BASE-PASS. Every head-only id is an equal-rate flake or composition artifact with standing QUARANTINE rows (29/103/191/69/145/62/156/184); `git diff 8894f86a..21eb5bc3a -- <file>` **EMPTY for all 8 net-new files** (no lane reach). The lone base-only engine diff is `test_engine_diff_audit.py` itself: base-RED → head-GREEN = the lane's pin re-anchor (a pin FIX — the documented census/count-pin caveat).
- Projection: probe 598 n/min ⇒ projected 135-min breach (advisory-only) → shard-0 re-projection 1,258 n/min ⇒ 75.5 min ⇒ within budget; **actual end-to-end ≈95 min — ADVISORY BREACHED end-to-end, reported loudly** (sweep+ladder alone = 80.65 min, within).
- Shard transcripts `/tmp/pilot-tier2-{base,head}-shard-{00..08}-133787.txt`; ladder TSV + canonical digests + verdict log committed under `raw/…/pilot-tier2-*`.

## 6. Greenlight scorecard (phase2-pilot-recommendation §5 — 10 criteria; the mission's 1-8 emphasized)

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | Shadow maturity (≥2 independent boots; ≥2× interval per target; ≥48h live soak) | **PARTIAL — boot legs SATISFIED; soak OPEN (post-promote by definition)** | Boots A+B (distinct boot_ids), each ≥2×90 s shadow on ALL 12 targets incl. the orphan; the staged-release-boot-smoke leg belongs to the release lane; long-cadence for the wide-interval targets rides the **leader-supplied live v0.31.27 evidence (15/15 SKIPPED_SHADOW ≥2× interval)** |
| 2 | Zero false shadow-dead (CS-9 carve-out) | **SATISFIED** | dead=0 on all 12 specs in all 4 boots; continuous SKIPPED_SHADOW (3-4 rows/boot); live evidence: WatchReconcile never dead |
| 3 | Skip-reason distributions sane | **SATISFIED (observed windows)** | all outcomes SKIPPED_SHADOW/SHADOW only; zero non-skip outcomes anywhere; no skip-storms |
| 4 | Boot wiring assertions for every adopter | **SATISFIED** | posture lines present in all boots; OrphanWatcher's dependency late-bound getter resolved at tick (exec-leg cancel proves the bus resolved); dormant-posture 3/3 + wiring pins green at the tip |
| 5 | Fast-lane fix legacy-side; runs_total>0 under engine → post-flip | **SATISFIED (split form)** | fix-landed verification is the fast-lane lane's own evidence; **this gate ALREADY shows runs_total>0 under engine execution** (3 SUCCEEDED) on a fenced boot — ahead of the post-flip expectation |
| 6 | Boot-SLO ≤120 s with engine registered-dormant | **SATISFIED (fenced boots)** | 26.77 / 24.69 / 24.07-31.07 / 25 s — all ≥3.8× under 120 s, host-frame, one frame |
| 7 | Watchdog DRILL evidence | **COVERED-BY-EXISTING** | the P1 drill artifact stands; the boot pack's drill pins green (26/26 ceremony); no live drill re-captured in this gate (not in the mission's leg list) |
| 8 | Kill switches armed + boot-scoped (**R5-narrowed**) | **SATISFIED (narrowed form)** | flag-off boots prove legacy-owns/rollback-is-one-flag-at-boot; shadow leg proves the first switch inert; exec leg proves the second activates; FAIL-TO-LEGACY never fired. The FULL "all functional" reading stays unsatisfiable until the leader-filed engine-core inertness defect lands (restoration condition in the R5 bracket) |
| 9 | Mutation-verified pins registered + pack green | **SATISFIED** | the pilot pack's registered row carries 8 mutation legs (all RED in disposable extracts); ceremony 59/59 |
| 10 | Promote ceremony (tag/4-way sha/rollback_safe + soak) | **OPEN** — the release lane's own gate | out of tester-gate scope |

## 7. Findings (product-side; reported, NOT fixed — none blocks FLAG-OFF)

- **F1 (🟠 staging-evidence gap, criterion-8 adjacent):** the boot posture line is **flag-blind** — a static literal prints `USE_ENGINE all unset` even with the flag ON (`api.py:3996`). An operator cannot distinguish flag-off from flag-on from the posture surface; the two-switch flip's first switch is invisible there. Cheap fix candidate for the flip lane.
- **F2 (🟠 telemetry vs documented bound):** `block_elapsed_ms_gated` = **266–308 ms across ALL FOUR boots** vs the documented SLO-1 dormant bound ≤250 ms (`phase4-plan.md` SLO-1; `api.py:3988`). NOT adjudicated here — the owning gate (`engine_boot_slo_gate`) is method-wiring-only by its standing P7 disclosure, so no registered gate currently owns this number. Cross-boot-consistent ⇒ worth a deliberate measurement ruling (fenced-boot block cost vs the SLO-1 frame) before the flip.
- **F3 (🟢 phase info):** engine orphan ticks sit +3.2 s after legacy ticks in the shadow boots — harmless while single-executor; note for flip-day observability.
- **F4 (🟢 pre-existing):** the `Starting Ensemble` banner logs twice per boot (`create_app()` executed at `api.py:7230` AND `:7233`).

Harness-side (mine, disclosed): boot-C's readyz-green instant is a log-derived bound (its poll loop bug — upper bound 31.07 s, still 3.8× under SLO); boot-C's first `pg_ctl` start needed the socket dir mkdir'd; the Tier-2 end-to-end advisory breach (§5); the two `/tmp/pilot-{base,head}-*` inspection worktrees are removed at commit time.

## 8. QUARANTINE cross-check

Zero pilot-diff test files appear in QUARANTINE.md (preflight-verified); the regression's 17 sweep-level ids all map to STANDING rows (29/103/191/69/145/62/156/184) — no new rows required; no rising-quarantine signal from this lane.

## 9. Bottom line for the leader

- **FLAG-OFF release (dormant ship): NOT BLOCKED** by anything this gate found.
- **Config-only flip later:** the boot-scoped greenlight is fully evidenced (criteria 1-boots/2/3/4/5-split/6/8-narrowed/9 SATISFIED); remaining OPEN: the ≥48h live soak + promote ceremony (criterion 1 tail, 10), and — before the flip — I recommend ruling F1 (make the posture line flag-aware) and F2 (own or re-baseline the SLO-1 block number), since both become operator-facing the day the switches flip.
