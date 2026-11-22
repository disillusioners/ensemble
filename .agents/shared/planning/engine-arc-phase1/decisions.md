# Engine Arc Phase 1 — Decisions Record

**Lane:** engine-arc-p1 · **Tree:** `/home/nea/ensemble-src-wt-engine-arc-p1` (branch `feature/engine-arc-phase1`, HEAD `aed3eabce`) · **Date:** 2026-11-21
**Role:** decisions-record + commit worker (LAST planner-stage worker). Part A is untouchable verbatim; Parts B–E record commission facts, this planning stage's design decisions, routed follow-ups, and open questions carried upward. Pipeline stage 1 of 4 (planner → architect → reviewer → approver).

---

## Part A — Emperor rulings (VERBATIM, untouchable)

Copied exactly as delivered. Do not paraphrase, do not fix typos. Source for all five: **Discord 2026-11-21**.

1. **BUILD ruling.** "Build the engine." — BUILD ruling, overriding the Phase-0 gate-hold recommendation (scores: gate-hold 3.80 vs build 2.65, BOTH recorded on ledger ENSEMBLE-38). *(Source: Discord 2026-11-21.)*

2. **Event registry ruling.** "For no 3: event register is need. Why: it help on central loging, debug management, metric..." — event registry ruling; his stated purpose is BINDING scope guidance: central logging, debug management, metrics. *(Source: Discord 2026-11-21.)*

3. **Phasing ruling.** "we build the engine first but don't use yet, then pick one by one" — phasing ruling (dormant build, then one-by-one migration). *(Source: Discord 2026-11-21.)*

4. **Boot SLO ruling.** "Boot wall clock is 2 minutes" — boot SLO, decision CLOSED: `boot_epoch` → readyz-green ≤ 120s; binding for the boot-phase acceptance gate, BOOT_SWEEP deadline design, watchdog boot-window expectations. *(Source: Discord 2026-11-21.)*

5. **Addendum (binding spec addition).** "Note: the register function should include like service tag name, category, module information (use for metric, management,..debug easier). Tldr: each service bring it info/meta when register. Later we may even have tool for maintenancer agent to use or even a table chart on metrics tabs" — self-describing `register()`: service_tag/service_name, category, module_info, description, extensible metadata dict; metrics labels service_tag+category; anticipates maintenancer-agent tool + /metrics UI tab. *(Source: Discord 2026-11-21.)*

---

## Part B — Commission facts

- **Census base:** 66 distinct periodic mechanisms / 13 families + 59 event mechanisms + 33 live-liveness verdicts @ live v0.31.22.
- **Census anchors:** `@ bf4bfde6e` (canonical checkout, detached HEAD). 18 anchors drifted bf4bfde6e → b71742fe3; re-grep at use time (house rule).
- **Implementation base:** `b71742fe3` (v0.31.23).
- **Study committed:** `@ aed3eabce` (Phase-0 census/design study artifacts; = base + study docs; current planning HEAD).
- **Two parallel worktree lanes from the same base** (`b71742fe3`): `feature/engine-arc-phase1` (this plan — engine B + event registry) and `fix/phase1-live-defects` (OrphanWatcher born-dead fix via late-bind getter; bash-reaper retention/stall fix). File overlap between lanes is `daemon/api.py` only, textually disjoint.
- **Pipeline:** planner (this stage) → architect → reviewer → approver. All planner workers: planning only — zero code, zero test runs, zero git mutations; this decisions lane performs the single in-branch commit of the planning directory.
- **Reconciliation — the commission's "migrations deferred" wording (approver iteration-001, 2026-11-21):** "deferred" covers MODULE migrations only (the estate-wide per-module `.sql` tail — Part D item 6, one-by-one gated commissions); the engine's OWN two additive `.sql` files (`engine_jobs.sql`, `engine_runs.sql`) land IN P1 per decision-report §B.2 (D1.6/D1.7; research-anchors §1 two-separate-files house rule). No tension — different migration populations.

---

## Part C — Design decisions (this planning stage)

Each entry: ID · decision · rationale · owner · alternatives rejected. Provenance: `plan-overview.md` §8 (D-1..D-5) + phase docs + dispatcher adjudications.

**D-1 — Test authority.**
- *Decision:* `test-strategy.md` owns test FILE names (its §1.1 = stable contract) and the mutation matrix (23 legs = authority).
- *Rationale:* `test-strategy.md` §1.1 explicitly states the file names are "the stable contract; import paths adjust to wherever the implementation lands the engine module"; phase-plan paths are indicative. The 23-leg matrix is the detail authority; phase7 Leg F's "≈34 per-AC" is the upper envelope, satisfied by the 23 documented legs + per-phase extras, reconciled at implementation to the deduplicated union.
- *Owner:* `test-strategy.md` (planner-stage test worker); implementation lane authors the named files.
- *Alternatives rejected:* phase-plan file paths (`tests/unit/services/engine/test_<module>.py`) as authoritative — rejected; "≈34 mutations" count as authority — rejected (envelope only).

**D-2 — Boot-SLO gate frame.**
- *Decision:* host-frame gate — start anchor owned by whatever launches the boot (test harness / promote lane; `boot_smoke.sh:496-524` already polls, raise `POLL_TIMEOUT` 45→≥120s) → poll `/readyz` `status=="ready"` → delta ≤120s. Because unit-start precedes the `boot_epoch` stamp, a host-frame pass implies the Emperor's `boot_epoch`-frame SLO **a fortiori**. Literal-`boot_epoch` binding = routed follow-up (add `first_ready_db_clock` to the boot record, `boot_record.py:269`).
- *Rationale:* `capture_boot_epoch` is stamped at `daemon/api.py:969-971` — after `manager.initialize()` — so the boot_epoch-frame silently excludes interpreter start, imports and `manager.initialize()` (the largest boot segments), and it mixes a DB clock with a host clock (~4–4.7 s measured offset, unstable across pod moves). The host frame is the honest, complete wall clock; with ~5× headroom (live max 23.2 s) neither frame false-fires on any recorded boot class except the DB-outage class (fails closed by design).
- *Owner:* dispatcher ruling; Phase 4 wires SLO-1/2/3 so EITHER frame is computable from the same evidence. **[CORRECTED — approver iteration-001, 2026-11-21:** the closing clause here ("test-strategy §5's timed-probe carrier is already (b)-shaped") was FALSE (architecture-recommendation.md §1(d) "Record corrected") — §5's probe was MIXED-FRAME (DB-clock start → host-clock end) until this fold applied AMD-6 (fold item B-1); §5 now presents the host-frame anchor.**]**
- *Alternatives rejected:* (c) `boot_epoch`-frame timed probe as the primary gate — retained only as durable telemetry, not the gate; mixing DB/host clocks in one subtraction — rejected (clock-frame trap inflates ~4 s, false-fails near the bar).

**D-3 — `ENSEMBLE_ENGINE_ENABLED` polarity.**
- *Decision:* ship `enabled=true` + `shadow_mode=true`. `ENSEMBLE_ENGINE_ENABLED=false` stays the one-flag full-off. **FLAGGED: architect may veto in review.**
- *Rationale:* Pillar 4 verbatim is "REGISTERED, OBSERVING, EXECUTING NOTHING" — observing requires the tick loop to run and record shadow evidence (`would_fire` computed). Default-OFF would make the Phase-2 greenlight criteria (≥2 cycles shadow, zero false-dead) unsatisfiable and violate the pillar's "OBSERVING". Execution remains impossible by construction: no module carries `SERVICES_<MODULE>_USE_ENGINE=1` in Phase 1.
- *Owner:* dispatcher ruled (plan-overview §8 D-3; phase1 §12.4); architect review may veto.
- *Alternatives rejected:* `enabled=false` as the literal default (test-strategy T8's "default OFF semantics") — rejected: engine constructs/registers/exposes inventory but computes no due-ness → no `would_fire`, no `dead` detection, no shadow evidence, pilot greenlight unsatisfiable. Cost of a veto is a P3/P4 one-line defaults change + re-derivation of phase2 D2.13's shadow path. [RESOLVED-BY-RULING (c): ADOPT, no veto — the "architect may veto" caveat and the veto-cost note are discharged; registration-axis rationale corrected per architecture-recommendation.md §1(c)]

**D-4 — Numbering.**
- *Decision:* Emperor frame adopted — build = Phase 1, first migration (pilot) = Phase 2; maps study §C.2.
- *Rationale:* study §C.2's "Phase 1 — pilot" predates the BUILD ruling; the module and criteria are identical either way (phase2-pilot-recommendation §1 framing note).
- *Owner:* dispatcher ruling.
- *Alternatives rejected:* study §C.2 numbering (pilot labelled Phase 1) — rejected to avoid downstream label drift across phaseN plans and PACKS.md.

**D-5 — RDRS path correction.**
- *Decision:* RDRS interval read site is `pool_orchestrator.py:370`; there is no `worker_pool/` package.
- *Rationale:* anchor re-verification at b71742fe3 corrected the path; the referenced `daemon/services/worker_pool/` package does not exist.
- *Owner:* planner (anchor re-verification, research-anchors.md).
- *Alternatives rejected:* `daemon/services/worker_pool/...` path — rejected (nonexistent; house rule: never cite anchors from memory).

**D-6 — Metrics-family.**
- *Decision:* the engine registers ZERO families in Phase 1. `/api/engine/*` read substrate + read-time union with `job_metrics()` instead; family ceremony GATED, default deferred (P6 T6.3), unfencing path written out.
- *Rationale:* the metrics-family census is a closed fence (`tests/unit/metrics/test_metrics_family_adjudication.py:415-453/:873-906`; 28 verdicts / 18 registered / 13 chart-primary **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): 28 → 29 verdicts / 18 → 19 registered; chart-primary 13 STANDS (verified at tree 2f36e8aed94c); planning-time figures predate the landed family extension (F24)]**); the `job_metrics()` byte-shape pin forces read-time union, never an edit (F-2). The addendum is satisfied by a labelled, attributed inventory (D6.4) without a family; a dormant empty engine would make a chart of nothing dead surface. Unfencing path (recorded): one change carrying verdict row + registry member + band + allowlist record + the `28 → 29` pin edits, mutation-verified. **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): transition consumed — census already 29 (verified at tree 2f36e8aed94c); a future registration bumps 29 → 30]**
- *Owner:* P6 T6.3 (census ceremony + diff-census record).
- *Alternatives rejected:* registering a chart-primary family as "obviously needed" work — rejected (widens a closed census silently, the exact defect class the pack catches); editing `job_metrics()` — forbidden by F-2; never deciding — rejected (deferral must be recorded so the next lane does not re-litigate).

**D-7 — Kill-switch tier.**
- *Decision:* env/config tier in Phase 1 (`MANAGED_KEYS == 19`, pinned at `tests/test_settings_resolution.py:676`; duplicate-literal AST guard `tests/test_daemon_settings_api.py:713-760`). DB-tier switch = routed follow-up with the 19→20 bump.
- *Rationale:* `MANAGED_KEYS` live = 19 at `settings_service.py:253` (AST literal count == runtime len); a DB-tier switch requires the pin bump + duplicate-literal guard update, which is churn outside the dormant-substrate scope.
- *Owner:* P1 (config surface); DB tier routed to follow-up.
- *Alternatives rejected:* DB-tier switch in Phase 1 — rejected (pin churn, no Phase-1 consumer).

**D-8 — Dead-knob cleanup atomicity.**
- *Decision:* all dead-knob call sites cleaned in ONE task (P1 T1.12), not two — the **FULL 28-site set** (CS-8: 21 pydantic `PersistenceConfig(checkpoint_interval=…)` kwargs + 5 `worker_poll_interval` SET files + 2 registered pack sites). **[CORRECTED — P1-review 2026-11-21: decomposition corrected per CS-8; headline 28 stands as the consumer-site count; the prior "21 kwargs + 5 SET + 2 pack" double-counted the 2 pack sites (they sit inside the 20); corrected census = 20 `checkpoint_interval=` kwargs (18 non-pack + 2 pack) / 28 consumer sites / 32 unique edit sites / 29 files; the dev's 34 double-counts the packs; the dev's "28 was a double-count artifact" claim = their off-by-2. Atomicity invariant now reads on 32 unique edit sites / 29 files (consumer sites 28; kwargs 20).]**
- *Rationale:* atomicity stands — a partial cleanup fails at global-conftest collection ⇒ essentially all tests. **[CORRECTED — AMD-9 (architect), approver iteration-002, 2026-11-21:** the pre-AMD-9 entry here read "all 26 `MagicMock(spec=ServicesConfig)` call sites" with the rationale "the fixture at `tests/conftest.py:535` explodes at construction unless all call sites are cleaned atomically". BOTH the count and the mechanism are superseded: `MagicMock(spec=…)` **allows SET** (only `spec_set=` refuses), so the fixture does NOT explode at construction — the real cleanup vectors are the 21 pydantic constructor kwargs plus GET-on-a-spec-mock of the deleted field; and the census was tests/-scoped, missing 2 registered pack sites (review fold F-2). Architecture-recommendation.md FA-6: "D-8 atomic 26-site cleanup: STANDS, mechanism corrected" (AMD-9 at :156). An implementer scoping T1.12 from the pre-AMD-9 text under-counts the set and uses the wrong failure model — the corrected entry above governs.**]**
- *Owner:* P1 T1.12.
- *Alternatives rejected:* splitting the cleanup across two tasks/phases — rejected (global-conftest collection failure under the corrected vectors — same atomicity logic, corrected mechanism).

**D-9 — F-23 diff-census record.**
- *Decision:* a ~27-entry enumerated record owned by P6, release-blocking.
- *Rationale:* `git diff --name-only` vs fixed LANE_BASE with enumerated allowlists; the record is maintained by the changing lane. It is the single biggest hidden cost the fence check surfaced; P7 Leg G re-runs the pack, so it is release-blocking. ≈27 added lines to one frozenset in ONE change, paid by the phase that first touches each path.
- *Owner:* P6 T6.3 (runs last before P7, records the union in one edit); each phase's contribution enumerated for auditability.
- *Alternatives rejected:* bolting the record on at release — rejected (must be paid by the touching phase, not discovered late).

**D-10 — Phase decomposition + band.**
- *Decision:* P1..P8, 136/222 h (≈176 mid), inside the 118–222 h band; excess vs the 162 h anchor priced: dead-knob cleanup 5 h, watchdog drill 4 h, diff-census record 1 h.
- *Rationale:* band check — sum lands ≈9% above the 162 h anchor (176/162 = +8.6%; recomputed — approver iteration-002 fold M-3, supersedes the stale ~11% figure; the 137/≈177 totals likewise superseded — phase7's low bucket sums to 12, not 13, so the program low total is 136). The excess is exactly the three items the brief's component table did not price separately. Test estimate cross-check reconciles test-strategy's 28/48 h against P7's 12/23 h + per-phase AC test effort (P7 = pack assembly; component tests ride each phase).
- *Owner:* planner (synthesis).
- *Alternatives rejected:* compressing the estimate to the 162 h anchor — rejected (would drop the three priced carry costs).

**D-11 — RELEASE CARRY RECOMMENDATION.**
- *Decision:* **TWO staged releases — defects-first.** Recorded as the planner-stage recommendation for the architect (a recommendation, not an Emperor ruling). **NO live promote** (Emperor ceremony owns the flip).
- *Rationale:* the two live-defect fixes (`fix/phase1-live-defects`: OrphanWatcher born-dead + 70 MB bash-reaper leak) are small, independently verifiable, and ready long before the 136–222 h engine build completes; holding them for one combined release delays live remediation for no coupling benefit. Coupling facts confirm no benefit: the `daemon/api.py` regions are disjoint (live-defects `:1226-1272`/`:3018-3041`/`:4532-4547` vs engine P4 lifespan ~`:3740` + shutdown <`:4600`, P6 `include_router` ~`:6359`), so either merge order auto-merges; no duplicate work (fast-lane late-bind fix = one service, engine `dead=true` contract = general detector — complementary); the Phase-2 pilot structurally needs the fix in `latest` but only by Phase-2 time, not by engine-merge time. Release slots derived LIVE at release-prep per W9.
- *Owner:* dispatcher recommendation; recorded here for architect adoption/refinement.
- *Alternatives rejected:* single combined release — rejected (delays live remediation with no coupling benefit).
- *Position recorded:* this decisions lane **adopts** the defects-first recommendation — the coupling facts above support it; no disagreement to flag. Architect may still refine staging mechanics.

---

## Part D — Routed follow-ups (explicitly NOT in Phase 1)

1. **DB-tier engine kill switch** — `MANAGED_KEYS` 19→20 bump + pin/guard update.
2. **Metrics-family registration ceremony / unfencing** — verdict row + registry member + band + allowlist + `28 → 29` pin edits **[TREE-TRUTH RECONCILE — 2026-11-21 (P6 review #3): transition consumed — census already 29 (verified at tree 2f36e8aed94c); a future registration bumps 29 → 30]** (P6 T6.3 default: deferred).
3. **`first_ready_db_clock` boot-record field** — literal `boot_epoch`-frame telemetry (`boot_record.py:269`).
4. **Fan-out unification** — GAP-15 (six incompatible primitives); Phase 5 names the seam only.
5. **Queue-depth triggers** — GAP-14 (no plumbing exists); seam named only.
6. **Migration tail** — ~20 eligible modules, one-by-one gated commissions (one-by-one rule).
7. **Wakeup churn** — §B.4 (worker-pool claim path, 4.6%/3.3%); separate commission.
8. **[OPEN-UNTIL-APPLIED — AMD-7 (architect); recorded approver iteration-001, 2026-11-21]** `engine_jobs.service_tag` UNIQUE index absent from the P1 DDL (the P1 `.sql`/model plan carries no `UNIQUE` on `service_tag`), and the PK-wording contradiction is live at `phase1-plan.md:305` (the risk-table row still asserts "`service_tag` is the ops identity and the PK" while D1.4 rules `engine_jobs.id` = stable slug TEXT PK). *Must land:* P1 DDL + model `__table_args__` UNIQUE on `service_tag`; resolve the `:305` row wording to the D1.4 ruling (rename = re-registration, documented in `spec.py`'s contract note). *Status:* OPEN-UNTIL-APPLIED (implementation lane).
9. **[OPEN-UNTIL-APPLIED — AMD-8 (architect); recorded approver iteration-001, 2026-11-21]** absent-vs-malformed env split missing from the acceptance wording — P1 AC4 and P3 T3.2 do not distinguish an ABSENT env var (registry default wins) from a MALFORMED value (validation-error path). *Must land:* `phase1-plan.md` AC4 + `phase3-plan.md` T3.2 acceptance cells. *Status:* OPEN-UNTIL-APPLIED (implementation lane).
10. **[OPEN-UNTIL-APPLIED — AMD-10 (architect); recorded approver iteration-001, 2026-11-21]** two ordered follow-ups absent from this Part D list: (i) estate boot-pass deadlines → adopter commissions; (ii) skip-enum extension record. *Must land:* THIS Part D list as proper routed follow-ups at implementation-lane intake. *Status:* OPEN-UNTIL-APPLIED (implementation lane).
11. **[OPEN-UNTIL-APPLIED — AMD-11 (architect); recorded approver iteration-001, 2026-11-21]** absolute counts / false literals in plan bodies: the "70-field class" absolute count at `phase1-plan.md:165` (D1.8 — rots on every `ServicesConfig` edit; D8.5's delta-only discipline applies; the brief's second pointer `phase1-plan.md:80` carries no "70 fields" instance — the effort TOTAL row lives there instead), and `phase8-plan.md:33` D8.3's "zero hits across `daemon/` and `tests/`" literally false vs `tests/unit/test_sqlite_guards.py:84/:138` (engine-adjacent guard-test sites already exist). *Must land:* reword `phase1-plan.md:165` to a non-absolute form; correct D8.3's zero-hits claim to name the two guard-test sites. *Status:* OPEN-UNTIL-APPLIED (implementation lane).

---

## Part E — Open questions carried to architect/reviewer (from `plan-overview.md` §12; none Emperor-blocking)

1. **Shadow duration before pilot flip.** Question: ≥2 release cycles (conservative) vs early flip on OrphanWatcher's own health? — *Option the plan currently assumes:* ≥2 release cycles of dormant shadow (greenlight criterion 1); `phase2-pilot-recommendation.md` presents both. [RESOLVED-BY-RULING (a) + review folds: criterion 1 reworded to CS-2; the "≥2 release cycles" framing is WITHDRAWN — see §PREDICATE LEDGER DELETION RECORD]
2. **Second migrant ordering.** Question: healthy-sweep (parity proof) vs PEXIT-class (JobLock/SourcesGc)? — *Option the plan currently assumes:* a healthy sweep (TmpImage or JobLock) as second for parity proof per decision-report §C.2; registry evidence will inform; Phase-2+ concern. [RESOLVED-BY-RULING (b): second migrant = JobLockSweepService (PEXIT-class); framing corrected to primitive-proven-second, not healthy-vs-PEXIT]
3. **D-3 polarity ratification.** Question: ratify `enabled=true` + `shadow_mode=true`, or architect veto to `enabled=false`? — *Option the plan currently assumes:* `enabled=true` + `shadow_mode=true` (D-3 above), stated in release notes so "dormant" is never confused with "absent". [RESOLVED-BY-RULING (c): ADOPT — ships enabled=true + shadow_mode=true; no veto]
4. **Start-anchor build-side emit.** Question: harness/promote-lane owns the host-frame start anchor (dispatcher ruling) or route a daemon-side emit? — *Option the plan currently assumes:* harness/promote-lane owns it; a build-side readyz-green emit via the `api.py:1082-1093` deferred-task pattern is the fallback only if promote-lane capture proves brittle. [RESOLVED-BY-RULING (d): D-2 RATIFIED — harness/promote-lane owns the host-frame anchor; daemon-side emit stays named fallback]

---

## Leader rulings (approver iteration-002, 2026-11-21)

Recorded VERBATIM as delivered by the leader at approver iteration-002. These two rulings adjudicate semantic defects in the iteration-002 package (pilot registration class; observer liveness semantics); the affected body sites are folded in the same pass and swept across the package (see `plan-overview.md` §PREDICATE LEDGER DELETION RECORD + ATTESTATION rows 36+).

**L-7 — Pilot registration class.**

> "RULING (leader, approver iteration-002): per-tick DB writes disqualify MAIN_LOOP under the P7 pin (phase2-plan.md:29/:116) — the pilot's recommended registration class is DB_OFFLOAD."

Factual basis (verified at this tree): `daemon/services/dependency_bus.py:3230-3255` — `_sweep_orphan_watchers` executes an `UPDATE dependency_watchers ...` inside `await asyncio.to_thread(_sweep_atomic)`, entered per tick from `daemon/services/orphan_watcher_sweep.py:229-236` (`await bus._sweep_orphan_watchers(...)`). `phase2-pilot-recommendation.md:32`'s claim ("pure in-memory… no DB touch on the hot path") is FALSE. Folded: pilot §1/§3/§4 class-fit + blast-radius + proven/not-proven lists, plan-overview §10, phase1 §12.3 — all MAIN_LOOP claims about OrphanWatcher swept to the ruled DB_OFFLOAD registration class.

**L-8 — CS-3 observer-liveness carve-out.**

> "RULING (leader, approver iteration-002): D2.15 gains an observer carve-out — for specs with execute_fn withheld, dead ⇔ zero SKIPPED_SHADOW rows in 2× interval (the observable execution-substitute); D3.4 recovery is N/A for observers (no run exists to recover). Greenlight criterion 2 reads on that definition."

Unsuperseded, criterion 2 (pilot:79, AMD-1(c)) was unsatisfiable: D2.15 (phase2-plan:42, dead = zero runs in 2×interval) + D3.4 (phase3-plan:36, recovery requires a successful RUN) made every observer spec dead=true forever. Folded: phase2-plan D2.15 + phase3-plan D3.4 + pilot criterion 2; the canonical carve-out string is ledgered as **CS-9** (supersession of CS-3's standalone predicate — see `plan-overview.md` §PREDICATE LEDGER).

**L-9 — P5 T5.4 class-2 durable-bus bridge (structural block resolved).** [Recorded 2026-11-21, post-approver-iteration-002 — a third ruling appended to this section; the section header's "these two rulings" framing still governs L-7/L-8 only.]

> "RULING (leader, L-9 — P5 T5.4 structural block resolved): T5.4 class-2 durable-bus bridge proceeds via PER-SITE bridges (option a) at the 10 census-enumerated emission sites; the EventBus.create_event tail-chokepoint alternative (option b) was REJECTED — create_event is the GAP-15 fan-out primitive Phase 1 is fenced from touching; fence revisions mid-implementation are the drift class the package discipline exists to prevent; the T5.9 bidirectional census leg pins drift for future unbridged sites."

Factual basis (re-verified by grep at this tree): the class-2 family's 10 emission sites sit across **7** files — `daemon/routers/answer_helper.py:419`, `daemon/services/job_recovery_service.py:4509`, `daemon/services/event_publisher.py:78`, `daemon/services/midflight_qa.py:505`/`:579`/`:766`/`:853`, `daemon/services/job_feedback_observer.py:2400`, `daemon/services/ram_guard_events.py:142`, `daemon/services/stale_task_recovery.py:945` — of which the census master table enumerates EV-30 (4 QA sites), EV-19 (JOB_COMPLETED), EV-17/EV-19 (INSTANCE_LIFECYCLE), EV-54 (RAM_LIMIT_EXCEEDED) and the §N line (task_recovery), while the `answer_helper` and `job_recovery_service` witness sites ride in on their consumer/periodic rows (`answer_helper.py:441` notify site; `census-periodic.md` row 6). Folded: `phase1-plan.md` §0.3 ownership map gains the 7-file / 10-site P5 row (the enumeration-gap closure, attested as row 66 of `plan-overview.md`). `phase5-plan.md` T5.4's existing wording ("inside/adjacent to `EventBus.create_event`'s **callers**, not inside `create_event` (which is shared)") already states option (a); the ruling CONFIRMS it and adds the enumeration the map was missing — no T5 body change.

**L-10 — P5 final bridge increment (producer-outside-map kinds).** [Recorded 2026-11-21 — fourth ruling appended to this section; the section header's "these two rulings" framing still governs L-7/L-8 only.]

> "RULING (leader, L-10 — P5 final bridge increment): the 8 producer-outside-map kinds are bridged per the same L-9 discipline (≤5-line tail-observe, `create_event` untouched) — message_received, error (INCLUDING the previously-excluded repo-fallback site :287→landed :299, a conscious L-10 inclusion overriding the earlier enumeration-round classification, disclosed in-code with source="event_repo_fallback"), room_join, room_leave, room_purged, room_tag_delivery, room_proposal, room_decision. Terminal-state contract: every catalog kind bridged-or-pinned — the bidirectional census leg enforces it; further map extension structurally impossible."

Factual basis: the 8 kinds' 13 landed sites span 5 files — `daemon/services/instance_messaging.py:2893` (message_received), `daemon/services/message_processing_errors.py:277`/`:299` (error; `:299` is the `event_repo` fallback previously excluded at `:287` by the L-9 enumeration round — L-10 consciously INCLUDES it, disclosed in-code with `source="event_repo_fallback"`), `daemon/services/room/membership.py:407`/`:531` (room_join/room_leave), `daemon/services/room/repository.py:1179` (room_purged), `daemon/services/room_delivery.py:879`/`:907`/`:918`/`:1033`/`:1105`/`:1203`/`:1241` (room_tag_delivery/room_proposal/room_decision). Folded: `phase1-plan.md` §0.3 gains the 5-file / 13-site P5 row (bracket **[L-10 extension — 2026-11-21: final bridge increment; see decisions.md §Leader rulings L-10]**); the class-2 emission-site tally is re-derived to **25 = 10 (L-9) + 2 (in-map child_reports producers) + 13 (L-10)** in `census-events.md`'s additive bracket (mirrored here). Telemetry note adjudicated: `room_join` has a **±1 over-count window** (caller-transaction rollback can over-count the bridge emission by one) — telemetry-accuracy only, zero delivery impact; carried in `plan-overview.md` ATTESTATION row 67 so phase7/auditors see it was ADJUDICATED, not missed.

**L-11 — zero-producer kinds stay DECLARED-SILENT.** [Recorded 2026-11-21 — fifth ruling appended to this section; the section header's "these two rulings" framing still governs L-7/L-8 only.]

> "RULING (leader, L-11 — zero-producer kinds stay DECLARED-SILENT): the 6 zero-producer kinds (processing_started/completed/failed, child_failed, message_completed, room_post) stay DECLARED-SILENT with pinned reasons — bridges would be fabricated telemetry (F-12/D5.12 class); the zero-producer register is diagnostic value (likely dead remnants of an older event model — flagged for the Phase-2+ substrate arc); enum retirement = separate future commission."

Factual basis: these 6 catalog kinds have no producer site in the event census (EV-01..EV-55 + §N); L-9/L-10 bridge only kinds with real producers, so bridging these would fabricate telemetry — the F-12 / D5.12 anti-fabrication class. Folded: the zero-producer register is retained as diagnostic value (pinned reasons), flagged for the Phase-2+ substrate arc; enum retirement is out of scope for Phase 1 (separate future commission). No `phase1-plan.md` §0.3 map row (no file edits), no census row rewrite — this ruling is recorded here and mirrored in `plan-overview.md` ATTESTATION row 67.

**L-12 — P4 OQ-4 flush disposition (event-registry periodic flush ships UNWIRED).**

> "RULING (leader, L-12 — P4 OQ-4 flush disposition): the event-registry periodic flush composition ships UNWIRED in Phase 1 — `daemon/services/event_registry/metrics.py` event_view projection (P6) is the documented composition point (read-driven drain; the emit ring is bounded deque(1024) + drop-counted, so an unwired flush cannot grow unbounded); binding flush cadence to engine ticks would need an explicit commission — deferred to the Phase-2 pilot IF its telemetry requires idle-period coverage."

Factual basis: the emit ring's bounded-deque(1024)+drop-count design bounds memory without a flush task; the P6 event_view projection drains on read; any engine-tick-bound flush is a new commission (dormant Phase 1 wires none).
