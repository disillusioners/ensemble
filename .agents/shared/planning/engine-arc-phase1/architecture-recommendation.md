# Engine Arc Phase-1 — Architecture Recommendation (Architect Stage)

**Date:** 2026-11-21 · **Pipeline stage:** 2 of 4 (planner → **architect** → reviewer → approver)
**Plan under review:** `.agents/shared/planning/engine-arc-phase1/` @ `9ff3cb3a5` (branch `feature/engine-arc-phase1`, base `b71742fe3` / v0.31.23)
**Binding inputs:** `../central-polling-engine-study/decision-report.md` §B (frozen) · decisions.md Parts A–E · Emperor rulings 2026-11-21 (BUILD, event registry, phasing, boot SLO 120s, self-describing addendum)
**Method:** Standard Design (council score 0–1 of 4 — macro approach Emperor-ruled in Phase 0; dormant posture + kill-switch reversibility neutralize irreversibility/blast-radius). Three parallel analysts, one skill each, all anchors re-grepped at the worktree tree:
- Worker A `structural-design` (`5a75d1e1`) — §B.1/§B.2/§B.3 fidelity, substrate house rules, event registry v1, api.py blast radius, forward-compat
- Worker B `resilience-design` (`aeabde88`) — dormant-boot guarantee, kill-switch/fail-to-legacy, watchdog independence, failure-mode sweep
- Worker C `trade-off-analysis` (`dd1b9aa4`) — Part E (a)–(d), P4∥P5, D-8, D-11

---

## 0. Status & Verdict Summary

**Status: COMPLETE — AMEND.** The plan is structurally faithful to frozen §B.2, forward-compatible with the Emperor addendum, and its parallelism/release claims hold. Four red findings — all with bounded, in-scope fixes — must be carried as binding addenda before the reviewer stage. **Architect verdict: READY FOR REVIEWER, conditional on AMD-1..AMD-4 (must-carry).**

| # | Focus area | Verdict | One-line basis |
|---|---|---|---|
| 1 | Part E adjudications (a)–(d) | **AMEND** | All four ruled below; criteria-1 wording + D-3 rationale need correction, not redesign |
| 2 | §B.2 fidelity + subsystem architecture | **AMEND** | FAITHFUL core (every frozen component maps, mechanisms intact); evidence-gap + ownership + identity addenda |
| 3 | Forward-compat (self-describing JobSpec) | **ACCEPT** | Durable columns, open TEXT category, additive GETs; no breaking-change vector found; UNIQUE hardening (AMD-7) |
| 4 | Dormant-boot + kill-switch posture | **RISK (bounded)** | Execution-impossibility holds by construction and *strengthens* under AMD-1; single-executor is convention at mid-transition — routed to Phase-2 (AMD-12) |
| 5 | Watchdog independence | **RISK (bounded)** | Build/test contradiction on the watchdog's shape; independence asserted in prose, not wiring — closed by AMD-2 |
| 6 | Risk pass (P4∥P5, D-8, D-11) | **ACCEPT** | P4∥P5 genuinely parallel; D-8 atomicity stands (mechanism corrected); D-11 adopted (machinery fix AMD-4) |

---

## 1. Part E Rulings (explicit, as commissioned)

### (a) Shadow duration before Phase-2 flip — RULED: reword criterion 1; early flip enabled when ALL greenlight criteria hold

The plan's "≥2 release cycles" (greenlight criterion 1) is **ceremony-gated, not falsifiability-gated**: the engine lane stages but never promotes (phase8-plan.md:1; decisions.md:100), so "release cycles" depend on Emperor promote cadence the lane cannot manufacture. What cycle-2 actually adds over cycle-1 is (i) a second boot's wiring-order assertions and (ii) long-cadence targets reaching the 2×interval dead threshold (SourcesGc 3600s→2h; maintenance 24h-class→48h; census-periodic.md:44,82,128) — both are wall-clock/boot-count properties, not ceremony properties. Criterion 2 (WatchReconcile false-dead, healthy 65/65 @300s) is decidable in ~10 minutes of shadow evidence.

**Ruling:** reword criterion 1 to — *"≥2 independent boots of shadow evidence on the priority targets (one of which is the staged-release boot smoke), AND long-cadence maturity: every registered target has been shadow-evaluated through ≥2× its interval, AND a ≥48h live soak post-promote before the pilot flip."* Flip when ALL greenlight criteria hold; no artificial release-cycle count. Risk asymmetry supports this: the pilot is one module, one flag, fail-to-legacy, and watcher-orphan coverage is already dead in live (liveness-evidence row 3) — marginal live cost of either waiting or flipping early is ≈0. The conservative literal-2-ceremony reading remains the **Emperor's** call (decision-report §E.4 Decision 2); carried under §6 Decisions Pending, not as a blocker.

### (b) Second-migrant ordering — RULED: JobLockSweep (PEXIT-class), per §C.3's own tail ordering; framing corrected

The plan's Part E framing ("healthy-sweep vs PEXIT-class") is internally inconsistent: **JobLockSweep appears in both options** (census #1; PEXIT verified intact :241-252; "healthy=invisible"), and *every* named "healthy" candidate also carries a PEXIT (TmpImage :343-348, SourcesGc :256-262). The real distinction is the **primitive proven second**, not the module: parity (engine≡legacy equivalence) vs supervised-recovery (PEXIT → restart-with-backoff + kill-switch gain).

**Ruling:** second migrant = **JobLockSweepService**. It is what decision-report §C.3 itself orders immediately after the OrphanWatcher pilot; it carries the headline PEXIT specimen the `supervised()` primitive exists to convert (permanent-exit → supervised restart, strictly additive on a defective baseline); and it gains a kill switch it never had. Condition: capture a pre-flip shadow baseline of its legacy tick cadence during its shadow-spec window (AMD-1 makes this possible) so "engine runs it" vs "engine broke it" stays answerable for a dark module. TmpImage remains third (observable-output parity). Phase-2+ concern; registry evidence informs at commission time — this ruling fixes the default the plan carries.

### (c) D-3 polarity — RULED: **ADOPT, no veto** (`enabled=true` + `shadow_mode=true` ships), with rationale corrected and three addenda

All three analysts converge on adopt:
- **Write volume is bounded:** shadow appends ONE `engine_runs` row (`outcome=SKIPPED_SHADOW`) per due job per interval and never calls `execute_fn` (phase2-plan.md:40,57); projection is in-process, zero new metrics family (D-6). No churn, no family ceremony.
- **The "OBSERVING" pillar and greenlight criteria 1–3 are unsatisfiable under `enabled=false`** (no tick loop → no `would_fire`, no `dead`, no skip distributions).
- **Execution stays impossible by construction:** every `SERVICES_<MODULE>_USE_ENGINE` defaults OFF (phase3-plan.md:40).

**Correction carried (Worker A 🔴1):** D-3's rationale argued the wrong axis. With `registrations=0` (D4.11) the observatory observes nothing under EITHER polarity — the binding constraint is the **registration axis**, fixed by AMD-1. `enabled=true`+`shadow=true` is necessary-not-sufficient for OBSERVING; AMD-1 makes it sufficient. Veto cost is slightly understated in decisions.md:55 (also re-points greenlight 1–3 and the D8.4 disclosure — still hours, not days); moot under adopt. Addenda paired with this ruling: test-env shadow-off override for canary/drill (AMD-3), T8 "default OFF" wording reconciliation (AMD-3c), release-notes "dormant ≠ absent" disclosure stands.

### (d) Start-anchor emit ownership — RULED: **RATIFY D-2** (harness/promote-lane owns the host-frame anchor); daemon-side emit stays named fallback, not built

- **A-fortiori argument verified sound:** `capture_boot_epoch` (api.py:969-971) is stamped after `manager.initialize()` (:958) — re-grepped by two workers independently. Unit-start precedes the stamp; both frames end at the same readyz-green; host-frame pass ⇒ boot_epoch-frame pass regardless of clock skew.
- **Record corrected:** decisions.md:48's claim that test-strategy §5 is "already (b)-shaped" is **false** — test-strategy.md:138 defaults to start = `boot_epoch` stamp (DB clock) minus end = `/readyz` poll (host wall clock), the exact mixed-frame subtraction D-2 forbids (~4.0–4.7s skew). AMD-6 rewrites the probe to a host-frame start anchor.
- **Blind spot named:** unscripted `systemctl restart`/auto-restart has no harness owner (boot-slo-measurement.md:181). Mitigation: capture `ActiveEnterTimestamp` (systemd) in the gate pack for that class; the daemon-side readyz-green emit (~5-line deferred-task stamp, api.py:1063-1099 re-grepped) stays the fallback if promote-lane capture proves brittle or the host is non-systemd.

---

## 2. Focus-Area Verdicts (evidence)

### FA-2 · §B.2 fidelity + subsystem architecture — **AMEND**

**FAITHFUL core:** every frozen §B.2 component maps to a phase task with its mechanism intact — stamp-on-every-terminal-transition incl. skips, jitter, single-flight→`SKIPPED_OVERLAP`, getter-at-first-tick deps + loud boot assertion, fail-to-legacy, DB-source-of-truth (`engine_runs` append-only + `engine_jobs` mutable) + RAM cache, 12-knob `SERVICES_*` continuity; both tables get their own `.sql` with `IF NOT EXISTS` mandated (134 files counted, prefixes `20261121_120000/120001` free, dup-prefix guard exists) **[release-B figure fold — 2026-11-21: 134 → 137 at tree (verified); the 134 was the iteration-001 correction, itself superseded by the lanes' subsequent migrations]**; event registry v1 = classes 1–4 exactly, class 5 deferred_v2, GAP-14 seam-named, per-kind postures labeled, no general fan-out API. **The ONE api.py edit is honest as disclosed:** P4 = one insertion block + one shutdown block inside the same lifespan function; P6 = one `include_router` line; 3 textual sites program-wide, all disclosed, boot-order correct and readyz-safe (engine block sits after `readiness_task = asyncio.create_task(` :3117 — SLO-2's structural pin is real). Fast-lane disjoint regions verified at tree (:1226-1229, :3018-3022, :4532-4536).

Amendments required: the shadow-evidence gap (🔴-1 → AMD-1), unclaimed edits (🟡-9 → AMD-5), service_tag identity (🟡-10 → AMD-7), frozen-scope bookkeeping (🟡-11, 🟢-18 → AMD-10).

### FA-3 · Forward-compat (Emperor addendum) — **ACCEPT**

P1 schema carries the addendum's full self-describing set as **durable columns** — service_tag/service_name, category, module_info, description, extensible metadata, plus `registered_at`/`updated_at`/`version`. Category is open TEXT vocabulary — adding categories later forces **no** migration. P6's three GETs serve the future maintenancer tool (inspect/query/steer) and `/metrics` UI table/chart **additively**; metrics labels service_tag+category have bounded cardinality. No closed DB enums, no fixed-column-where-dict-promised, no label explosion found. One hardening: `service_tag` should be UNIQUE from day one (AMD-7) — a shared tag would corrupt the UI table and steer semantics, and adding UNIQUE later is the breaking change this review exists to prevent.

### FA-4 · Dormant-boot + kill-switch posture — **RISK (bounded; closable in Phase 1, one residual routed)**

- **Execution-impossibility holds by construction:** three-axis gate (zero real registrations + `shadow_mode=true` + all `USE_ENGINE` unset); boot classes/FIRST_TICK are capability-only; the ~29 awaited estate passes untouched. **AMD-1 strengthens it**: observer specs carry **no `execute_fn`**, so dispatch becomes structurally impossible even if shadow were off and a flag hypothetically on — impossibility no longer depends on the registration axis.
- **One-flag rollback is REAL at boot granularity:** fail-to-legacy polarity, tick-time no-restart flip, per-job `kill_switch_key` well specified (phase3-plan D3.6/D3.7).
- **Risks found:** (i) 🔴 canary/drill unreachable under shipped defaults (AMD-3); (ii) 🟡 single-executor across engine/legacy paths is enforced by flag-read-at-start **convention**, no structural lock, and mid-transition double-run is unspecified — acceptable for Phase 1 (nothing migrates; M-12 mutation tests the invariant), but the **Phase-2 pilot commission must specify the transition handshake** (AMD-12, routed); (iii) 🟡 resolver ambiguity: absent-vs-malformed env on a zero-env boot could silently disable the observatory (AMD-8).

### FA-5 · Watchdog independence — **RISK (bounded; closed by AMD-2)**

- 🔴 **Shape contradiction:** phase4-plan.md:37 (D4.6) pins `threading.Thread(daemon=True)` — "NEVER a coroutine", matching the existing repo watchdog (`daemon/services/wedge_capture/watchdog.py:1-13`) and §A.6.2 (a starved event loop cannot run a coroutine) — while test-strategy.md:123 drills "a plain asyncio loop owning its own task". The drilled shape is blind to the exact engine-tick wedge the watchdog exists to catch.
- 🟡 **Recovery semantics undefined:** the build only logs CRITICAL + counts (D4.6/D4.7 — "must NOT be able to kill the engine"); the drill asserts an engine-independent recovery re-arm that **no phase owns** — if implemented via `engine.register()` it would depend on the wedged engine, the opposite of independence.
- 🟡 **Startup coupling:** T4.4 puts watchdog start inside the engine-construction `try` — an engine-construction exception suppresses its own watcher. State-sampling channel (shared atomic vs engine API call) unspecified.
- **Boot window:** consistent with the 120s SLO (drill precedent T_restart→T_readyz 13/14/15s; ~5× headroom on live class); **D-2 host-frame gate RATIFIED** (see ruling (d)). Watchdog-of-the-watchdog residual (no production monitor of the thread itself) accepted and stated, not implicit.

### FA-6 · Risk pass — **ACCEPT (with AMD-4/AMD-9)**

- **P4∥P5 parallelizable: HOLDS.** Disjoint file ownership (P4: `engine/boot.py`+`engine/watchdog.py`+api.py lifespan block; P5: `event_registry/*` + four ≤5-line bridge edits); no shared new symbol (P5 imports only P1 symbols, explicitly no EngineService import — D5.11; P4 declares the registry Out); no P4↔P5 edge in the dependency graph. The real api.py serialization is P4 + fast lane + P6 — already fenced by explicit-pathspec commit rules.
- **D-8 atomic 26-site cleanup: STANDS, mechanism corrected.** `MagicMock(spec=...)` **allows SET** (only `spec_set=` refuses) — F-29's stated mechanism is wrong; the real explosion vectors are the 21 pydantic `PersistenceConfig(checkpoint_interval=…)` constructor kwargs plus GET-on-spec-mock of a deleted field **[CORRECTED — P1-review 2026-11-21: census corrected to 20 `checkpoint_interval=` kwargs (18 non-pack + 2 pack); the "21" here was the pre-correction count]**. Site census re-verified: 5 `worker_poll_interval` files + 21 `checkpoint_interval` files = 26 **[CORRECTED — P1-review 2026-11-21: kwargs count 20, not 21]**; `lease_heartbeat_interval_seconds` = 0 test hits. One-task atomicity is still correct (partial cleanup fails at global-conftest collection ⇒ essentially all tests); ≈5h ≈ 11 min/site is sane. Implementer guidance: clean the 21 kwargs files, not just the 5 SETs.
- **D-11 two staged releases, defects-first: ADOPTED, machinery fixed.** Coupling facts verified (api.py regions disjoint; pilot needs the fix in latest only by Phase-2 time). 🔴 phase8-plan D8.6 stages the full branch as ONE artifact and T8.1–T8.9 enumerate only the engine branch — the adopted two-release strategy has **no stage path for the fast-lane fix**. AMD-4 enumerates two stage invocations (per-release slot derivation, rollback adjudication, disclosure).

---

## 3. Consolidated Findings Register (deduped; highest severity + most specific variant kept)

| # | Sev | Finding | Evidence | Fix |
|---|---|---|---|---|
| 1 | 🔴 | Dormant-shadow evidence gap: `registrations=0` (D4.11 no-op; AC8 "registers nothing") makes greenlight 1/2/5 unsatisfiable — engine can't compute `would_fire`/`dead` for unregistered jobs; `job_metrics()` covers maintenance tier only; D-3 argued the wrong (enabled) axis | phase1-plan.md:32,166,187; phase4-plan.md:42,194; phase2-plan.md:38,40; decision-report.md:108,162-168; phase2-pilot-recommendation.md:78-82,89 | AMD-1 |
| 2 | 🔴 | Watchdog shape contradiction: built = daemon thread, drilled = asyncio coroutine (blind to loop starvation) | phase4-plan.md:37; test-strategy.md:123; wedge_capture/watchdog.py:1-13; decision-report.md §A.6.2 | AMD-2 |
| 3 | 🔴 | Canary boot + drill legs require real `execute_fn` invocation; shipped `shadow_mode=true` forbids it everywhere; zero shadow-off overrides anywhere in the plan | phase2-plan.md:40 (D2.13); test-strategy.md:114,127-129; decisions.md:52-55 | AMD-3 |
| 4 | 🔴 | D8.6 one-artifact staging contradicts adopted D-11 — no stage machinery for the fast-lane fix release | phase8-plan.md:36,45-53; decisions.md:99-104 | AMD-4 |
| 5 | 🟡 | Watchdog recovery path unowned/contradictory (drill asserts what no phase builds; `engine.register()` on a wedged engine = dependence); startup inside engine-construction try; sampling channel unspecified | phase4-plan.md:37,94,96; test-strategy.md:129 | AMD-2 |
| 6 | 🟡 | Single-executor across engine/legacy = flag-read convention, no structural lock; mid-RUNNING `kill_switch_key` semantics unstated | phase3-plan.md:38-39,54; test-strategy.md:90 (M-12) | AMD-12 (Phase-2) |
| 7 | 🟡 | test-strategy §5 timed probe defaults to the mixed-clock frame D-2 forbids; decisions.md:48 claims it is "(b)-shaped already" (false) | test-strategy.md:138; boot_epoch.py:132; decisions.md:47-48; boot-slo-measurement.md:161-167 | AMD-6 |
| 8 | 🟡 | Fail-to-legacy resolver: "unreadable ⇒ engine_enabled=False" vs absent-env ⇒ pydantic default `true` — absent vs malformed never disambiguated; zero-env boot could silently kill the observatory | phase1-plan.md:316; phase3-plan.md:53 | AMD-8 |
| 9 | 🟡 | Unclaimed edits under the plan's own REJECT-grade single-ownership invariant: `set_event_sink()` composition line + canary `register()` site have no owner row | phase5-plan.md:69; phase4-plan.md:96; phase1-plan.md:85-114 | AMD-5 |
| 10 | 🟡 | `service_tag` identity contradiction (D1.4: id=PK, tag=ops-identity vs risk-table: tag=PK) and no UNIQUE on tag | phase1-plan.md:161,201,231,305 | AMD-7 |
| 11 | 🟡 | Frozen §B.2 "each [estate] awaited pass gets a deadline" deferred only implicitly (engine-internal classes only; F-13 forbids estate wiring) — absent from Part D routed follow-ups | decision-report.md:126; phase4-plan.md:33,147,194; decisions.md:108-116 | AMD-10 |
| 12 | 🟡 | Shadow-duration criterion is ceremony-gated, not falsifiability-gated | phase2-pilot-recommendation.md:78; phase8-plan.md:1 | Ruling (a) |
| 13 | 🟡 | Second-migrant framing internally inconsistent (options differ by primitive, not module; every "healthy" candidate carries PEXIT) | decision-report.md:171,175; census-periodic.md:128,184 | Ruling (b) |
| 14 | 🟡 | Boot start-anchor blind spot: unscripted `systemctl restart` has no host-frame owner | boot-slo-measurement.md:181 | AMD-6 |
| 15 | 🟡 | F-29 mechanism imprecise (spec= allows SET; real vectors = 21 pydantic kwargs + GET-on-spec-mock **[CORRECTED — P1-review 2026-11-21: kwargs count = 20, not 21]**) — cleanup could target wrong sites | phase1-plan.md:275-277; tests/conftest.py:535; tests/test_manager.py:77 | AMD-9 |
| 16 | 🟡 | D-3 veto cost understated (also greenlight 1–3 re-point + D8.4 disclosure) — moot under adopt | decisions.md:55 | Ruling (c) note |
| 17 | 🟡 | NEW under AMD-1: shadow-row write volume needs a retention bound (~15–25 priority specs × per-due-interval rows ≈ 10⁵–10⁶ rows/yr append-only) | synthesis over phase2-plan.md:40 (D2.13) | AMD-1e |
| 18 | 🟢 | Skip-enum grows beyond frozen §B.1 six (+SHADOW, +TIMEOUT_PENDING_EXTEND, +NOT_DUE, +SKIPPED_REREGISTERED; none dropped) — sanctioned, record the delta | decision-report.md:99; phase2-plan.md:36; phase3-plan.md:56 | AMD-10 |
| 19 | 🟢 | Boot-phase classes built but exercised only against fixtures in Phase 1 (N=0→N=observer specs under AMD-1 improves this) | phase4-plan.md:42,93 | note |
| 20 | 🟢 | No production monitor of the watchdog itself — accepted residual, state it | test-strategy.md:130 | AMD-2 note |
| 21 | 🟢 | D8.3 "zero hits" rollback grep imprecise (phrase-level hits at tests/unit/test_sqlite_guards.py:84,138) — make it table/filename-specific | phase8-plan.md:33 | AMD-11 |
| 22 | 🟢 | Stale line anchors (include_router ~:6359 vs actual catalogue :6359+/insertion :6493+; shutdown :4585; deferred-task :1063-1099; config intervals +~60 lines) + ServicesConfig absolute count 70 vs recounted 69 (delta-not-absolute rule: drop the number) | workers C/A re-greps; phase1-plan.md:121 | AMD-11 |
| 23 | 🟢 | Ram-guard cadence: plan's 1000ms correction vs census 200ms verified right at tree (config.py:1222/:1264) | config.py | none (record) |

---

## 4. Plan Amendments (ADDENDUM PATTERN — planner docs are NOT rewritten; these are architect-stage directives the reviewer verifies and the implementation lane applies)

> **Must-carry (block reviewer sign-off): AMD-1..AMD-4. Should-carry: AMD-5..AMD-10. Bookkeeping: AMD-11. Routed (out of Phase-1 scope): AMD-12.**

**AMD-1 — Shadow-spec registration (fixes 🔴-1; pairs with ruling (c) and criterion rewording in ruling (a)).**
Amend D4.11 from `registrations=0` to `registrations=N observer specs`: P4's wiring block registers Interval specs for the §A.5 priority targets (JobLock, SourcesGc, maintenance tier, bash reaper, RDRS, strand-healer/drift, healthy tail; legacy cadences read via the T1.11 continuity table) with:
  (a) **`execute_fn` withheld** (spec carries no executable) — dispatch becomes structurally impossible regardless of shadow/flag state; the dormant gate strengthens;
  (b) all `SERVICES_<MODULE>_USE_ENGINE` stay unset — registration-for-observation is NOT migration (pilot-doc :12 compatibility preserved);
  (c) greenlight criteria reworded to the observe-model ceiling: criterion 1 → per ruling (a); criterion 2 → "zero false **shadow-dead** on healthy mechanisms" (dead-flag for observer specs = shadow-evaluation liveness: continuous `SKIPPED_SHADOW` evidence; a false dead on a healthy mechanism falsifies the engine's scheduling, blocks flip); criterion 5 → split: fix-landed verification stays legacy-side (fast-lane lane's own evidence), "runs_total>0 for OrphanWatcher **under engine execution**" moves to the pilot's post-flip acceptance evidence;
  (d) would_fire cadences double as live validation of the T1.11 continuity mapping (engine-read cadence vs legacy log cadence divergence = config-wiring bug caught pre-flip);
  (e) P2 specifies a **retention bound for shadow rows** (house TTL/sweep pattern; bounded append-only growth — finding 17).

**AMD-2 — Watchdog shape + independence (fixes 🔴-2, 🟡-5, 🟢-20).**
Pin the daemon-thread shape (D4.6 / wedge_capture precedent); rewrite test-strategy §4 to drill that exact built shape; define a genuinely engine-independent recovery action (direct DB re-arm / fresh supervisor — never `engine.register()` on the wedged engine) and give it an owning task in P4; move watchdog start OUT of the engine-construction `try` block (T4.4); specify the state channel as a shared atomic (engine writes last-tick monotonic; thread reads lock-free — never an engine API call); state the watchdog-of-watchdog residual as accepted.

**AMD-3 — Canary/drill shadow override (fixes 🔴-3).**
The canary activation boot and both watchdog drill legs set `ENSEMBLE_ENGINE_SHADOW_MODE=false` explicitly in their test env (alongside the existing explicit `=0` pins), with ALL real `SERVICES_*_USE_ENGINE` unset and the canary spec the ONLY executable registration; (c) reconcile T8's "default OFF semantics" wording to the D-3 adopt.

**AMD-4 — Two-release stage machinery (fixes 🔴-4).**
Amend phase8 D8.6: enumerate TWO stage invocations — release A = `fix/phase1-live-defects` (slot derived live per W9, own rollback adjudication, own disclosure), release B = engine branch P1–P7; keep per-release delta-only `rollback_safe` adjudication and dormant-disclosure notes; either merge order auto-merges (regions disjoint, verified).

**AMD-5 — Claim the unclaimed edits (fixes 🟡-9).**
Ownership map additions: `set_event_sink(...)` composition line → P4 T4.4 (named in task text); synthetic canary `register()` call site + `SERVICES_<CANARY>_USE_ENGINE` fixture → P4 (or P7 pack, one owner). Two lines; prevents a REJECT-grade invariant catch at implementation.
> Supersession note (review fold F-10, 2026-11-21): FA-6's "P4 declares the registry Out" and this amendment's `set_event_sink` composition line in T4.4 are not in conflict — the sink install is a guarded composition inside D4.10's try/except wiring block (logs + continues, never aborts boot), not a P4→P5 structural dependency; the P4∥P5 "no shared new symbol" claim holds with the sink install as the named exception.

**AMD-6 — Clock-frame + anchor record (fixes 🟡-7, 🟡-14).**
Rewrite test-strategy §5's timed probe to a harness-owned host-frame start anchor (unit `ActiveEnterTimestamp` / launcher line); `boot_epoch`→first-ready DB stamp = telemetry only; correct decisions.md D-2's "(b)-shaped already" claim; gate pack captures `ActiveEnterTimestamp` for the bare-restart class; daemon-side readyz-green emit stays named fallback.

**AMD-7 — service_tag identity + UNIQUE (fixes 🟡-10).**
Rule: `engine_jobs.id` = stable slug TEXT PK (rename = re-registration); `service_tag` = NOT NULL **UNIQUE** (1 tag ↔ 1 registered service — protects the future UI table + maintenancer steer semantics; cheap now, breaking later). Add UNIQUE to the P1 DDL; resolve the D1.4-vs-risk-table wording in `spec.py`'s contract note.

**AMD-8 — Resolver disambiguation (fixes 🟡-8).**
"Unreadable" = malformed/missing VALUE ⇒ fail-to-legacy (`engine_enabled=False`); env var ABSENT ⇒ pydantic default (`enabled=true`, observing). State it in P1 config + P3 T3.2.

**AMD-9 — D-8 mechanism correction (fixes 🟡-15).**
Correct F-29's rationale: real vectors = 21 `PersistenceConfig(checkpoint_interval=…)` pydantic kwargs + GET-on-spec-mock; spec= allows SET. Atomicity + one-task + ≈5h unchanged; implementer guidance updated.

**AMD-10 — Frozen-scope bookkeeping (fixes 🟡-11, 🟢-18).**
decisions.md Part D additions: (i) estate boot-pass deadlines → deferred to each adopter's migration commission (engine-internal classes proven in P4); (ii) skip-enum extension record (+SHADOW, +TIMEOUT_PENDING_EXTEND, +NOT_DUE @P2, +SKIPPED_REREGISTERED @P3; no frozen member dropped).

**AMD-11 — Anchor/count hygiene (fixes 🟢-21, 🟢-22).**
Make the D8.3 rollback grep table/filename-specific; drop the absolute ServicesConfig count (delta-not-absolute rule); line anchors listed in finding 22 are indicative only — string-pin at implementation (house rule).

**AMD-12 — ROUTED to Phase-2 pilot commission (fixes 🟡-6).**
Specify the single-executor transition handshake (flag transitions effective at boot, or unregister→legacy-start ordering under the single-flight lock — no mid-transition double-run window) and mid-RUNNING `kill_switch_key` semantics (natural reading: in-flight run completes, not cancelled — timeout policy is separate). Phase-1 dormant posture unaffected; M-12 mutation remains the test invariant.

---

## 5. Risks (post-amendment)

- 🟡 **Greenlight evidence ceiling is real even after AMD-1** — observer specs prove engine-side liveness, wiring, cadence-mapping; they cannot see legacy actual-runs (that arrives with each module's migration). The study itself set this ceiling ("a dormant shadow's evidentiary ceiling is low", decision-report.md:167). Mitigated by criterion rewording (honest about what Phase 1 can falsify) — residual accepted.
- 🟡 `daemon/api.py` triple-lane concurrency (fast lane + P4 + P6) — textually disjoint today; explicit-pathspec commits + merge-order check at finalize (house rules, already in plan §7.3).
- 🟢 Boot-phase classes exercised only on fixtures in Phase 1 (partially improved by AMD-1's observer registrations exercising due-evaluation paths).
- 🟢 Line-anchor drift (18 census + the finding-22 set) — process risk only; string-pin rule covers it.

## 6. Decisions Pending (Emperor; none block the reviewer)

1. **Shadow-flip conservatism** — ruling (a) adopts the evidence-based early flip; the literal "two promote ceremonies" reading remains the Emperor's values call (study §E.4 Decision 2). Default carried: evidence-based.
2. **Phase-2 pilot go** — "pick one by one" is the Emperor's; OrphanWatcher post-fix is the recommended first (unchanged), JobLock second (ruling (b)).
3. **Deferred rotation standing item** — unrelated to this plan; remains user-gated (critical-note reminder stands).

## 7. Open Questions

None blocking. Non-blocking notes: (i) include_router insertion-point line number differs between analysts (:6359 catalogue start vs :6493+ insertion) — immaterial, string-pinned; (ii) P5 consumer-liveness verification (D5.4) runs in P5's own pack — no hidden P4 dependency found.

## 8. Verdict

**READY FOR REVIEWER — conditional on AMD-1..AMD-4 carried as binding addenda.** The plan's architecture is sound: faithful to frozen §B.2, forward-compatible with the Emperor's self-describing addendum, genuinely parallelizable where claimed, and releasable in two staged releases. The four reds are consistency defects (an unsatisfiable evidence criterion, a build/test shape contradiction, an unreachable drill under shipped defaults, and missing machinery for the adopted release strategy) — every one has a bounded in-scope fix that strengthens rather than reshapes the design.

*Provenance: synthesized by the architect from three worker reports (IDs above), all read-only, anchors re-grepped at the worktree tree per house rule. No planner doc was modified; this file is the sole architect-stage artifact.*
