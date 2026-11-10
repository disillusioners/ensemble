# Phase 1 Plan: agents-ensemble skill-system-revival (PM ENSEMBLE-27 / board `wi-4d1fa093`)

Date: 2026-11-09
Author: worker (plan-creation lane) — sole author, one voice for plan + decisions
Status: **Draft — ready for the plan gate**
Base sha: `ae5bbafedde90c7af1065b9660196bbffbbbf9c1` (= `origin/latest` == the Phase-1 base leg)
Worktree: `/home/nea/ensemble-src-wt-skill-revival-phase1` (branch `feature/skill-system-revival-phase1`)
Interpreter: `<worktree>/.venv/bin/python` — CPython **3.14.7** (matches live v0.29.5/6/7 = cpython-314)
Base leg (read-only reference): `/home/nea/ensemble-src-wt-skill-revival-base1` (detached @ `ae5bbafed`, own venv 3.14.7, own fenced `.env` mode 600, `POSTGRES_DB=ensemble_dev`)

**Inputs read (in the commission's order, end-to-end):**
`.agents/shared/planning/skill-system-revival/phase0-facts-extract.md` (724 lines — the fact base) ·
`.agents/shared/planning/skill-system-revival/phase0-investigation.md` (context/severity only) ·
`.agents/shared/planning/skill-keeper-confinement/decisions.md` (D1–D8, prior art) ·
`.agents/tester/rules/ensure.md` (gate tiers, proof form) · `.agents/tester/PACKS.md` (registration standard) ·
`.agents/tester/QUARANTINE.md` (rows 45 / 56 / 89 / 90, quoted in §11) ·
`test/packs/{context_skills,inner_soul_memory_skill_metrics,skill_capture_killswitch,skill_evolution_pg,skill_evolution,skill_id_verbatim_contract,skill_services_gzip_regression}_unit_test.sh`.

**Anchor rule (dispatcher contract §3).** The facts extract is authoritative on FACTS; the worktree is authoritative on LINE NUMBERS. Every `file:line` below was opened in the worktree at `ae5bbafed`. Where a commission/facts anchor had drifted, the corrected anchor is cited and the drift is DISCLOSED in §17.

---

## 1. Goals

1. **Make the weird metrics honest.** The metrics door currently reports `dark / capture_off` and `dark / census_gated` for families that are publishing live series; the capture attribution gate misreads ~3.8k instances; the `total_applied` counter drifts +480 away from its own ledger. Phase 1 makes each of these **true**, with a discriminating pin per metric.
2. **Close the one reachable mutation path** the confinement lane left open (inline `skill_evolve`) and **prove behaviorally** that the `b7da3ed0` shape (24× `edit_file` + `git commit`) is refused.
3. **Prove the brake** works without engaging it, and record the exact OFF/ON asymmetry the Phase-2 runbook depends on.
4. **Land capture-lane observability** that would have caught the Oct-22 deviation.
5. **Stop the re-fix burst class**, **build and prove the retirement mechanism (zero flips in Phase 1 — DS-44)**, and fix the skill-tools display hygiene.
6. **Wire the injection gate** for the agents that actually need it — staged payload only.
7. **Stabilize the flakiness** the PM ledger names, with the root cause already proven by the base-leg experiment.
8. **Stage a promote-ready release** (no promote) and specify the Phase-2 switch-flip runbook's required content.

**Definition of done:** a staged release exists whose (a) metrics verdicts are true — including the A4 gauge **redefined to measure within-identity-key divergence `(name, project_id)`, anchored in code with its home recorded, reporting 32 with the 27-pair cause named, and pinned against silent regression; the 0 target is explicitly BLOCKED-ON the A/B resolution decision** (not global-name divergence, which is unsatisfiable) — with the transition-based bump pin and the audited reconcile proven; (b) keeper confinement is behaviorally proven, (c) net-new reds = 0 against the measured base protocol, (d) every touched metric carries a non-vacuous mutation-proven pin in a REGISTERED pack, and (e) the Phase-2 runbook content spec is approved — **with both kill-switches still unset and no promote performed.**

**A4 exit criterion (verbatim — the tester inherits this wording and must not paraphrase it):**
> *"Metrics are honest when (i) the wedge-capture families report `live` wherever they publish series, (ii) the census-polarity families report `live` wherever the census is running and publishing, (iii) the capture-attribution gate honors the three-conjunct OUTCOME-FIELDS predicate `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL` (method `has_genuinely_evolved_for_instance`) so the false-eligible population is 0 post-fix (3457 -> 0, measured), (iv) the `total_applied` counter equals its `applied=True` ledger within-identity-key, and (v) the A4 gauge measures **within-identity-key divergence `(name, project_id)` and reports 32 today** (caused by 27 stranded A/B pairs), has its **home anchored in code with the anchor recorded** (an honest FIND-IT — no such aggregation exists in `daemon/` today), is pinned so it can never silently regress, and its **0 target is explicitly marked BLOCKED-ON the A/B resolution decision** (Ruling E). Global-name divergence is NOT a defect and is never reported as one. A4 and E4 are coupled on one root cause."*

## 2. Non-goals (encoded as hard constraints)

| # | Non-goal | Why it is a non-goal in Phase 1 | Enforcement |
|---|---|---|---|
| N1 | Do not flip `ENSEMBLE_SKILL_CAPTURE_ENABLED` or `ENSEMBLE_SKILL_EVOLUTION_ENABLED` | Capture and evolution come up SEPARATELY in Phase 2; capture proves clean first | No `.env` edit, no `config.yaml` edit; **§14.4 staged-verification checklist item 4** asserts their absence from the **env SURFACES only** (`.env`, the effective environ, `config.yaml`) — **NOT a whole-tree grep**: the two names are **compiled-in string literals** (`SKILL_CAPTURE_KILL_SWITCH_ENV` at `daemon/services/skill_job_dispatcher.py:72`; `SKILL_EVOLUTION_KILL_SWITCH_ENV` at `:202`), so a tree grep ALWAYS hits the binary by necessity |
| N2 | Do not promote | The 3-factor nonce ceremony is the Emperor's | Plan stops at "staged"; the promote command is *documented*, not run |
| N3 | Do not engage the queue brake | Queue `bd075f0b` carries all job traffic | C is **verify + document only** — every C command is a SELECT / a GET |
| N4 | Do not work in `/home/nea/ensemble-src` | Canonical tree is DETACHED ~2wk behind, carries a 6-entry staged index + 15 files of unstaged WIP | Every command in this plan is `cd /home/nea/ensemble-src-wt-skill-revival-phase1` first; **§14.4 staged-verification checklist item 7** (payload source-less / 4-way sha / sidecar / migration census / prompt baseline) is the gate that asserts no canonical-tree artifact reached the payload |
| N5 | Do not hard-delete any skill row | Usage history must stay queryable; the rows may be needed for the A/B adjudication | E4 deactivates (`is_active=False`, `status='inactive'`) only; the reversal path is specified |
| N6 | Do not fabricate capture→evolution diversity | 116/116 evolution rows are `evolution_type='FIX'` — a real, honest zero | E2 makes the path ready and says so; no synthetic DERIVED/CAPTURED rows |
| N7 | Do not touch `scripts/upgrade/**` unless a workstream strictly requires it | The promote executes the ceremony code from the **CWD** — a modified scripts tree would run UNCOMMITTED ceremony code (§14.4) | No workstream requires it; §14.4 states the scripts-pin sync consequence if that changes |
| N8 | Do not hard-delete the keeper transcripts / instance rows | They are the Oct-22 evidence; retention TTL 168h already destroyed them once | D proposes retention-shaped observability, never deletion |

## 3. Commission premise corrections

The commission's summary is authoritative on scope but partly wrong on facts. Corrections are recorded here with the evidence; **the plan is written against the corrected facts.** (P1–P3 were supplied by the commissioning authority; **P4–P8 were found by this lane** and are marked as such.) The premise corrections **P1-P4** (plus P9) are retained verbatim — no revision edits them.

| # | Commission says | Truth at `ae5bbafed` | Evidence | Plan consequence |
|---|---|---|---|---|
| **P1** | 16/24 agents declare `skill_injection` | **25 declaring: 15 true / 10 false**; `blueprinter` + `tester` flipped to false, `aegis` added true. 31 of 46 agents cannot capture (10 false + 21 absent → default False) | facts-extract A2; re-verified in the worktree: `aegis=True, approver=True, approver[v2]=True, architect=True, critic=True, doc-maintainer=True, maintenancer=True, planner=True, planner[v2]=True, reviewer=True, reviewer[v2]=True, sketcher=True, tidier=True, tidier[v2]=True, worker=True` (15) / `blueprinter=False, coder=False, designer=False, developer=False, developer[v2]=False, devops=False, project-manager=False, tester=False, wanderer=False, watcher=False` (10) | F's agent set is **recomputed from `origin/latest`** (§10) |
| **P2** | Stale-skill id list incl. `76e3e1a7a` | `76e3e1a7a` **does not exist anywhere in the repo** (0 hits at `ae5bbafed`). `08a4332f` + `fd1e01dc` are **two ACTIVE rows of ONE never-resolved A/B lineage** (`regression-ab-partition-run`, shared group `2600329e-…`); gen-0 is `978ae5cc` (inactive) | facts-extract A8 (source: `.agents/tester/RESULTS/2026-11-01-psl-phase5-tier2-commission-gates.md:109-114`) | E4 **must not** deactivate a row it cannot first identify → E4 ships a **rule-based** predicate, not an id list |
| **P3** | The 3 confinement pieces are "landed-but-unwired" | **All three are NOT BUILT / DEFERRED with recorded reasons**: `filesystem_scope` NOT BUILT (D8 res.9), master switch DEFERRED (D7/HANDOVER P3), direction quarantine DEFERRED (D6) | `skill-keeper-confinement/decisions.md:95-100`, `:112-138`; facts-extract A7 | §6 states the true per-piece status and reconciles each explicitly |
| **P4** *(found)* | The base red is a "dup-prefix (`20260714_000001`)" defect | **Not a duplicate prefix.** `daemon/migrations/versions/` contains exactly ONE `20260714_000001` file, and `_assert_no_duplicate_versions` (runner.py:364-420) passes. The real defect is a **SQLite-incompatible DDL**: `20260714_000001_widen_job_queue_type_constraint.sql:35` runs `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …`, which SQLite does not support. The file's own "DUAL-DRIVER NOTES" (:24-27) assert "SQLite supports `ALTER TABLE ... DROP CONSTRAINT` since 3.35.0" — **that claim is false** | reproduced in the worktree: `MigrationError: Migration 20260714_000001 failed: (sqlite3.OperationalError) near "EXISTS": syntax error`; 3/3 reds in `tests/manager/test_skill_service_init.py`; already a known quarantine family (QUARANTINE.md:89, :76, :74) | §9 (E4) uses this: the `.sql` runner is **doubly** the wrong vehicle (SQLite-only AND SQLite-boot-broken at `20260714_000001`) |
| **P5** *(found)* | The read-reach residual is "the four read tools" | **Two** of the four *appeared* already fenced — **RE-ADJUDICATED: THE CONCLUSION DRAWN FROM THAT IS FALSIFIED** (see the consequence cell + §17 #28). The MECHANICS below remain true, but they bound the walk against the **agent-supplied `workdir`**, not a daemon-bound anchor: `glob_files` / `grep_files` resolve their root through `_resolve_search_root` (filesystem.py:252-283), which refuses an absolute root outside **that** workdir/temp dirs (the 2026-09-23 16 GB RAM-walk fix); `read_file` (:670) and `list_directory` (:596) honour the absolute-path skip in `_resolve_within_workdir` (:197-225). A scoped agent defeats all four by supplying its own `workdir` | `daemon/tools/filesystem.py:142-147` (the module docstring says so explicitly) | B3 scope is **FOUR call sites** — `read_file`, `list_directory`, `glob_files`, `grep_files` — under ONE identical policy; the earlier **"2 call sites"** narrowing and the "already fenced" premise behind it are **WITHDRAWN** (§17 #28) |
| **P6** *(found)* | E1 needs a rate/cap guard "so a skill cannot be re-FIXed in a tight loop" | The Oct-21 burst is **already blocked** by D2's shipped controls: the 1800 s per-`(project, skill)` cooldown (`skill_job_dispatcher.py:641-657`) exceeds both observed gaps (21:26:09→21:49:02 = 1373 s; →22:03:13 = 851 s). What is missing is (a) **human-visible signalling** (today: one WARNING log line), (b) **durability** (the cooldown is RAM-only), (c) a **hard cap** (a sliding window still lets a slow trickle accumulate) | dispatcher.py:327 (`fix_cooldown_seconds: int = 1800`), `:641-657`, `:678` | E1 is re-scoped to a **durable, capped, human-visible** guard — not a duplicate of D2 |
| **P7** *(found)* | The brake is the OFF lever; "arm it first" | The brake is **currently NOT engaged**: `bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2` (`system_parallel_queue`) has `is_paused=false`, `updated_at=2026-10-26T21:50:04.439103+00:00` — the Oct-22 containment pause was **lifted on 2026-10-26**. The standing project critical note claiming "system_parallel_queue PAUSED until confinement fix promoted" is **STALE** | facts-extract findings `lane1-runtime-db.md:46,:601,:604` and `lane3-flakiness-evidence.md:36,:155`; `charter-original-diagram.md:92` | C must **record the current state**, not assume "armed"; §15's runbook arms the brake as a *Phase-2 action*, not a Phase-1 premise |
| **P8** *(found)* | The three pieces "make the keeper genuinely CANNOT write into the project" | The keeper **already cannot**: the shipped allow-list strip (D3, live v0.29.7) grants no write-capable tool. `edit_file`/`write_file`/`bash`/`proc`/`service`/`inner_soul` are all denied (`agents/skill-keeper/meta.json` tools.deny) | `agents/skill-keeper/meta.json`; D3 decisions.md:51-73 | B's deliverable is the **behavioral proof** (B1) — the three named pieces are NOT what closes the `b7da3ed0` shape; the plan says so plainly |
| **P9** *(found, census)* | E3 folds six display-hygiene items (8-hex `:1260`, 8-hex `:1491`, `pf-` pseudo-id `:402`, `:1213` refusal gap, `to_summary()` dead surface, P7 citation re-derive) | The real board item `wi-d340f16c` (status **todo**, priority 2, body_len 1037) contains **EXACTLY THREE** bullets: the two 8-hex echoes (`:1260`, `:1491`) and the `pf-` pseudo-id echo (`:402`). The other three items are **ABSENT**. The item also states the echoes are **DISPLAY-ONLY** (*"skill_view consumption is NOT affected"* — the v0.28.10 fix already covered every consumable surface) and that **none block the promote** | `grep -rn "wi-d340f16c" .agents/` returns only title-level mentions; the body was fetched for the census | §E3 is planned against the **3 real bullets**. The `:1213` refusal-shape change, `to_summary()` wiring, and P7 citation re-derive are carried as **leader-judgment cleanups, clearly labeled NOT-from-board** (or dropped). The full-uuid/address echo treatment still applies to the 3 real bullets. **E3's previously-planned full-uuid/address echo treatment still applies to the 3 real bullets; the `:1213` refusal-shape change is no longer board-mandated — if kept at all, label it leader-judgment, NOT-from-board.** |

---

## 4. The constraint envelope (what may and may not change in Phase 1)

- **Code** may change in the worktree; **nothing is committed** (the dispatcher's caller arranges the commit point).
- **No env switch** is set, unset, or edited. No `config.yaml` edit.
- **No DB write** to the live/dev DB from this lane. Every DB-touching item is a **script or service that ships with the release** and is executed only after a promote (or by an operator, in Phase 2, with a receipt).
- **No queue pause.** The brake is *documented*, never engaged.
- Every pin lives in a **REGISTERED pack** (`.agents/tester/PACKS.md` entry) with a **mutation leg** that proves it non-vacuous.
- Every metric touched carries a **RED→GREEN** proof.

---

## 5. Workstream A — METRICS / TELEMETRY REPAIR

**Why first:** the whole commission is "make the weird metrics honest first". Every other workstream's acceptance criterion is stated in metrics; if the metrics lie, the acceptance is unverifiable.

### A1 — Wire `capture_active` (honest source of truth)

**Where.**
- `daemon/api.py:2571-2576` — the `RegistryMetricsQueryProvider(...)` construction. `capture_active=` is **never passed** (verified at plan time; **AS BUILT the kwarg is at `:2584-2586`**). The sibling line `:2574` already passes the *process fact* `listener_enabled=app.state.metrics_listener is not None`.
- `daemon/services/metrics_query/provider.py:67` (ctor kwarg), `:75` (stored), `:147` (published) — the flag is hard-`False` today.
- `daemon/services/metrics_query/availability.py:61-62` — the field's own docstring: *"Whether the wedge capture is active."*
- The fact source: `daemon/api.py:3236-3258` — `app.state.wedge_capture_watchdog` is set to the watchdog when `WedgeCaptureConfig.enabled` and started, else `None` (`:3252`), and `None` on failure (`:3258`).

**Ruling (this lane's own decision — open question 20 is RESOLVED here).**
The honest source of truth is the **wedge-capture PROCESS FACT** — `app.state.wedge_capture_watchdog is not None` — *not* a switch value and *not* the skill-capture kill-switch:

- The field's own contract (`availability.py:61-62`) says **wedge capture**. Wiring it to `ENSEMBLE_SKILL_CAPTURE_ENABLED` would be a **category error** (different subsystem: skill capture ≠ wedge capture).
- Wiring it to `ENSEMBLE_WEDGE_CAPTURE_ENABLED` would be a **switch** fact, and the house already rejected switch-facts for this exact slot: `listener_enabled` is documented as *"Deliberately NOT the switch state … this fact is switch-INVARIANT"* (`provider.py:89-96`).
- The process fact is the only one that is true at the moment the door is asked, and it is the same shape as the existing `listener_enabled` fact.

**Implementation shape.** The provider is constructed at `api.py:2571`, but the watchdog is only created at `api.py:3236-3258` — i.e. **the fact does not exist yet at construction time**. Therefore `capture_active` must be **late-binding**, exactly mirroring the house's existing late-binding `publisher_cadence: Callable[[], float]` kwarg (`provider.py:70`, stored at `:78`):
- `provider.py:67` — change the kwarg to `capture_active: bool | Callable[[], bool] = False` (keep the bool form working so existing callers/tests are unaffected), store a resolver at `:75`, and resolve it per read at `:147`.
- `api.py:2571-2576` — pass a closure reading `app.state.wedge_capture_watchdog`.
- `availability.py:61-62` — docstring corrected to name the wedge-capture process fact (the tokens the doc-pins require stay).

**Acceptance.**
1. With a live wedge watchdog, the F1/F3 families (`SOURCE_IDIOM_WEDGE_CAPTURE`) report `{"state":"live"}` when they publish series — **not** `dark/capture_off`.
2. With no watchdog, they report `dark/capture_off` (the refusal direction is preserved).
3. `activation.capture_active` in the snapshot body equals the watchdog's liveness, and flips when the watchdog dies (it is a *live* fact, not a boot snapshot).

**Pins + mutation legs (registered pack `metrics_capture_active_wiring_unit_test`, NEW).**
- **M-A1a**: mutate `api.py` to drop the `capture_active=` kwarg → the pin must go RED (family reports `capture_off` while the watchdog is live).
- **M-A1b**: mutate `provider.py:75` to hard-code `False` → RED.
- **M-A1c**: mutate the closure to read a *switch* instead of the watchdog → RED (the pin asserts the source is `app.state.wedge_capture_watchdog`).
- **M-A1d**: make the resolver a boot-time snapshot (evaluate once) → RED (the pin asserts the fact tracks a watchdog that starts **after** the provider was constructed — this is the leg that makes the late-binding requirement non-vacuous).

### A2 — Fix the double inversion at the resolution layer + correct the swapped labels

**The chain (all anchors verified).**

| # | Site | Code | Effect |
|---|---|---|---|
| 1 | `daemon/services/metrics_census.py:322-335` | `@property def gated(self)` — docstring *"Whether the census sampler's DB work is currently gated **OFF**"* — body `return self._gate()` | returns the **ENABLED** sense |
| 2 | `daemon/services/metrics_census.py:347-355` | `_gate()` → `metrics_gate.metrics_enabled()` | `True` = census ENABLED |
| 3 | `daemon/services/metrics_query/provider.py:117-122` | `census_enabled = not bool(self._census.gated)` | `False` while the census is running |
| 4 | `daemon/services/metrics_query/provider.py:145` | `census_gated = not self.census_enabled` | `True` while the census is running |
| 5 | `daemon/services/metrics_query/availability.py:91-92` | `if facts.census_gated: return {"state":"dark","reason":"census_gated"}` | **F6 dark while publishing 15 live series** |

**Ruling — fix the POLARITY AT THE RESOLUTION LAYER.** The resolution layer is `CensusSampler.gated` (the public read-through of the private `_gate()` — `availability.py:83-88` explicitly refuses to fix it in-lane because *"the two-check-sites invariant"* forbids adding a check site). The fix is **one line**:

```
daemon/services/metrics_census.py:335   return not self._gate()
```

- It adds **no new check site** (the property still calls the existing `_gate()`), so the behavioural check-site census stays at exactly **2** (`tests/unit/metrics/test_metrics_query_switch_invariance.py:189-196`).
- It makes the property agree with its own docstring **and** with every consumer — including the `_FakeCensus` stand-in in the existing tests, whose `gated=True` means *"the census is gated OFF"* (`test_metrics_query_switch_invariance.py:51`, `:237-242`; `test_metrics_query_provider.py:319-327`).
- ⇒ **Exactly ONE existing pin flips (corrected — the earlier "No existing pin flips" claim was FALSE).** The `_FakeCensus`-based fakes already encode the *corrected* contract, but the ONE pin driven by the REAL sampler does not: `tests/unit/metrics/test_metrics_query_provider.py::TestAvailabilityOrderIsLaneOwned::test_a_real_running_census_reports_the_gate_state` seeds `CensusSampler(gate_enabled=lambda: True)` with `_started=True` and asserted `dark/census_gated` — which IS the inversion, through the real class. It was REPURPOSED in the same change into the enabled-case `live` pin, a disabled-case `census_gated` sibling pin, and the order pin (whose fixture now seeds the corrected `gated` sense): **+1 node, file count 70 → 71** (re-measured `--collect-only`, 2026-11-09; the `metrics_query_provider` PACKS row is corrected with it). The provider-side fix (`:117-122` / `:145`) would still have required **rewriting every** `_FakeCensus` fake and its pin, which is why the resolution-layer fix remains the right site.

**Also in A2 — the swapped labels.** `availability.py:83-88` labels the census-polarity defect **R-i**, while the disclosure (`docs/architecture/metrics-promote-disclosure.md:86-95`) assigns **R-i = wire `capture_active`** and **R-ii = census `gated` polarity**. The code comment is the swapped one. Fix: `availability.py:83-88` → **R-ii**, and state that R-i is now FIXED (not "routed out of lane").

**The disclosure document** (`docs/architecture/metrics-promote-disclosure.md:82-98`) must be updated in the same change:
- the api.py anchor `daemon/api.py:2528-2533` → **`daemon/api.py:2571-2576`** (drift, §17);
- *"The code fix is correctly routed OUT of this lane"* → *"FIXED in <release>"*;
- the required token set **`R-i`, `R-ii`, `capture_active`, `census inverted`** must **survive** (DONE — the disclosure was rewritten in the same change and the five files below re-run green, 172/172) — five test files assert their presence (`tests/unit/test_metrics_read_registration.py:525`, `tests/unit/test_anomaly_report_tool_registration.py:1013`, `tests/unit/metrics/test_metrics_read_docs_surface.py:126`, `tests/unit/metrics/test_metrics_read_tools.py:251`, `tests/unit/metrics/test_metrics_read_personas.py:359`). Rewriting the sentence is allowed; dropping a token is not.

**Acceptance.**
1. With the census ENABLED and publishing, the F6 family reports `{"state":"live"}`.
2. With the census DISABLED, F6 reports `dark/census_gated` (the refusal direction is preserved).
3. The check-site census stays at exactly 2.
4. `availability.py`'s comment and the disclosure agree on R-i/R-ii.

**Pins + mutation legs (registered pack `metrics_census_polarity_unit_test`, NEW).**
- **M-A2a**: revert `metrics_census.py:335` to `return self._gate()` → RED (F6 live → dark while publishing).
- **M-A2b**: make `provider.py:120` use `bool(self._census.gated)` (the "fix at the consumer" mutation) → RED on the *consumer-side* pins (the pin asserts the resolution layer, not the consumer).
- **M-A2c**: swap the labels back in `availability.py:83-88` → RED (a label-agreement pin: code and disclosure must use the same R-id for the same defect).
- **M-A2d**: delete the R-i/R-ii tokens from the disclosure → **BLOCKED / non-discriminating, and REPLACED**: the five tokens tests assert their ABSENCE from SHIPPED strings, not their presence in the disclosure, so no test reads the disclosure at all (the architect's R1.4 correction). The disclosure half is nonetheless DONE (tokens kept verbatim, 172/172 green across the five files); the label-agreement half of the acceptance is carried by **M-A2c** (a real label-agreement pin on `availability.py`). The two A2 mutation legs are M-A2a and M-A2b.

### A3 — Fix the attribution seam (only genuinely-evolved skills count)

**Where.**
- `daemon/repositories/skill/repository.py:1550-1573` — `has_applied_for_instance()` gates on `SkillUsageRecord.feedback_applied == True` and **ignores** `selected` / `applied` / `task_succeeded`.
- Consumer 1: `daemon/services/skill_metrics_service.py:642-662` — **Gate 4** of the capture eligibility check.
- Consumer 2: `daemon/services/skill_evolution_service.py:1647-1653` — the service-seam twin of the same gate (docstring at `:1595-1598`).
- Population caveat (measured, `facts-extract` §A3): `record_task_completion` early-returns at `skill_metrics_service.py:413-414` (`if not injected_ids: return 0`) **before** the gate at `:488`, so instances with zero injected skills never reach Gate 4. ⇒ **3,782 is an UPPER BOUND, not a capture forecast**, and the exact population is *"succeeded instances with ≥1 selected/injected usage row AND zero `applied=True` rows"*.

**Census confirmation (A3 is now EXACT).** `skill_usage_records` has four boolean outcome fields: `selected`, `applied`, `task_succeeded`, `feedback_applied` (plus `superseded`, `feedback_usefulness`, `feedback_improvement`). NULL census over 10,108 rows: `selected`/`applied`/`task_succeeded` = **ZERO NULLs**; `feedback_applied` NULL=3,547 / TRUE=6,091 / FALSE=470. TRUE counts: `selected`=10,108, `applied`=6,095, `task_succeeded`=9,311.
⇒ The false-eligible window is **exact as measured**: `selected AND task_succeeded AND feedback_applied IS NULL` = **3,457 (3457) rows**, measured at **2026-11-09 (the Addendum-3 census; the earlier facts-extract row-level figure was 3,450 at 06:11–06:14Z on 2026-11-09 — the two differ because the population moved between measurements; the tester's re-measure must record its own timestamp to be comparable)**. **Pin the exact predicate, the exact count boundary (3,457 (3457) at the Addendum-3 census timestamp), AND the census timestamp in the plan and the test.** **Record the census timestamp next to the pinned boundary** (approver minor 6): the tester's re-measure must record its own timestamp to be comparable. The fix is concrete: honor `feedback_applied` (and the outcome fields) so those 3,457 stop counting as genuinely-evolved.
**LEADER RULING Q1 (framing (a) — the OUTCOME-FIELDS predicate; supersedes the approver's three options).** A skill counts as **GENUINELY EVOLVED only if it has at least one usage record with `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL`** — the three-conjunct outcome-fields form. `applied == True` remains a NON-predicate (its only writer is the one-way latch at `repository.py:1308-1318`; `update_completion` (`repository.py:1339-1436`) never sets it, despite `models.py:311-315` declaring it) — but `feedback_applied IS NOT NULL` ALONE is also not the gate: the approver proved that a bare `IS NOT NULL` flip is a ≤4-row no-op against the 3,457-row window (every window row has `feedback_applied IS NULL`, so `IS NOT NULL` is false for exactly those rows and the window's verdict is unchanged). The window is moved by putting the **outcome fields in the predicate**, which is what the original diagnosis pointed at (facts-extract §A3 defect 1: *"gates on `feedback_applied==True` and ignores `selected`"*).

**Implementation shape.** Do **not** mutate `has_applied_for_instance`'s meaning in place — it has a second consumer class and a public name. Instead:
- Add a sibling predicate on the repository, e.g. `has_genuinely_evolved_for_instance(instance_id)` → `∃ SkillUsageRecord WHERE instance_id = ? AND selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL` (the **three-conjunct outcome-fields predicate**; `applied` stays out of the predicate — it is a projection, not a signal).
- Repoint the **two** gate call sites (`:648` and `:1648`) to the new predicate.
- Keep `has_applied_for_instance` intact, marked *"feedback-based; NOT a capture-gate predicate"* — do **not** delete it (unknown external consumers).

**DISCLOSED RESIDUAL — the missing completion-hook `applied` writer (NOT BUILT in Phase 1).** The lifecycle gap is real: `update_completion` (`repository.py:1339-1436`) never sets `applied`, contradicting `models.py:311-315`. Building that writer would be **NEW WRITE BEHAVIOR** in a phase whose mandate is *"make metrics honest FIRST"* — so it is **NOT built here**. It is disclosed, named, and routed (a later release may add the writer; only then would an `applied`-based gate become meaningful). Phase 1's gate uses `feedback_applied IS NOT NULL` precisely because that field is populated today.

**ACCEPTANCE FORM (chosen): the DELTA form — "records counted as genuinely-evolved MINUS records with feedback evidence = 0".** Used consistently in §1 DoD, §5 A3, §18, and §19.1. (The equivalent population form — "the false-eligible population is 0 by construction" — is the same statement; the DELTA form is the one this plan uses.)

**Acceptance.**
1. **Measured delta = 0**: records the gate counts as **genuinely-evolved** MINUS records with **feedback evidence** (`feedback_applied IS NOT NULL`) **= 0**, measured on the post-early-return population — not asserted, and not the 3,782 upper bound. The measured count moves **3,457 (3457) → 0** *as a delta* — i.e. the rows the gate refused-for-lack-of-feedback-evidence go from 3,457 to 0, by construction (the predicate now **requires** feedback evidence).
2. **A/B proof (corrected)**: a row in the census window — `selected=True, task_succeeded=True, feedback_applied=NULL` — is **INELIGIBLE** for the genuinely-evolved count (it has no feedback evidence). The approver's contradiction is resolved: the window rows are no longer counted as genuinely-evolved; they were the population the delta measures.
3. **SEPARATE DISCLOSED BAND — the 470 `feedback_applied = FALSE` rows** are honest negatives (feedback evaluated, not applied). They are **excluded from genuinely-evolved** but are **NOT false-eligible** — they are reported **separately**, never conflated with the 3,457. (The 3,457 window lacks feedback evidence; the 470 band carries evaluated-negative feedback. Different populations, different disclosures.)

**Pins + mutation legs (registered pack `skill_capture_killswitch_unit_test` — the EXISTING registered pack, PACKS.md:2993; the new pin file `tests/unit/test_skill_capture_attribution_seam.py` goes inside it as an implementation step — see §13.3).**
- **M-A3a**: neutralize the **feedback-evidence conjunct** (`feedback_applied IS NOT NULL`) from the three-conjunct predicate → the NULL-feedback rows **re-enter** the genuinely-evolved count → the measured delta goes from **0 to 3,457 (3457)** → RED. ⚠ **If the mutation does not move the measured count, the pin is VACUOUS — DELETE it, do not ship it.** (This is the mutation that must MOVE the observed value: the delta.)
- **M-A3b**: widen the predicate to `selected == True` (over-broad) → RED (a selected-but-never-fed-back instance is wrongly ineligible).
- **M-A3c**: drop the early-return population caveat from the metric → RED (the metric's population no longer matches the code's).
- **M-A3d**: leave one of the two gate call sites on the old predicate → RED (the twin-gate parity pin).

### A4 — The counter drift: the gauge measures a NON-DEFECT as a defect (reframe)

**The census derived the two gauges exactly.**
- **"48/628 vs 43/529"** = `names-with-DIVERGING-CONTENT / total skills` — the **same** gauge at two snapshots. Total grew 529 → 628 → 630; numerator grew 43 → 48. (The 43/529 leg is arithmetic-consistent *inference*; the 48/630 leg is **FACT**.)
- **"+423 → +480"** = rows inside **ALL duplicated-name groups**: ~423 five days ago → 481 now (+57/5d, matching the commission).

**The problem — `skills.name` is INTENTIONALLY NON-UNIQUE.** Every project gets its own `bank_clone` generation-0 row, so **one name spans many `project_id`s BY DESIGN**. Shape proof: `code-fix` = 9 rows — one `bank_clone` gen-0 per project (7 distinct `project_id`s) + evolved gen-1 rows in project `83da04de` (status `ab_testing`). Current census: **85 names have >1 row**; **48 names have >1 distinct content** over 630 rows; **481 rows in duplicated-name groups**; **234 in content-diverging groups**. Top collision names: review-strategy 12, test-pack-execution 12, code-review 11, dev-strategy 11, explore-for-incremental 10, plan-review 10, pr-review 10, security-review 10, code-fix 9, git-commit 9.

**Consequence (a) — "A4 at 0 mismatch" as GLOBAL name divergence is UNSATISFIABLE without destroying the per-project clone model.** Name-level deduplication would be **catastrophic** — it destroys the per-project clone model. **Do NOT plan dedupe.** The commission's "CONVERGE TO 0 mismatches" cannot be met by this gauge as currently defined, and the plan must say so rather than pretend.

**Consequence (b) — the honest target is the gauge that measures the ACTUAL defect.** The defect is divergence **WITHIN one identity key** — within `(name, project_id)` and/or within a single lineage family — plus the specific known-nasty cases. The fix redefines the gauge to measure what its **name claims** (a metrics-honesty fix, which is exactly what A is for). **Ruling D (Addendum 5): the "is 0" claim is FALSE today** — within-identity-key divergence on prod = **32**, caused by **27 stranded A/B pairs** (Ruling E). The Phase-1 deliverable is the **honest gauge + the measured baseline (32) + the named cause (27 pairs)**. Reaching 0 requires the A/B backlog to be resolved, which is a data-lifecycle decision (Ruling E). **A4 and E4 are now COUPLED on one root cause** — the metrics weirdness and the skill hygiene problem are the same 27 pairs. Say so in both sections and in decisions.md.

**Consequence (c) — the exit criterion is REWRITTEN (below) to name the corrected metric and the measured baseline.** The proposed wording is the "A4 exit criterion" block in §1 (Definition of done); the tester inherits it and must not paraphrase it. The 0 target is explicitly marked **BLOCKED-ON** the A/B resolution decision.

**The leak (code) — still real and separate from the gauge.** `daemon/services/skill_metrics_service.py:1170-1186` bumps `total_applied` on **every** `record_feedback(applied=True)` call for a `(skill, instance)` pair — including repeated calls on a row that is *already* `applied=True`. The ledger counts **rows**; the counter counts **calls**. ⇒ Every duplicate feedback filing adds +1 to the counter and +0 to the ledger: exactly the observed monotonic, never-shrinking drift.
**Fix:** make the bump **transition-based**, with the **on-miss insert case handled explicitly** (the council's F-4 trap: a naive `prev_applied = bool(record.applied)` would either crash on `record is None` or **suppress the FIRST legitimate bump**). The service reads the row before updating it (`record = self.usage_repo.get_latest_for_skill_instance(...)` at `:1027-1029`), so:
- **on-miss insert path** (`:1030-1111`, `record is None`): the row is inserted with `applied=False` (`:1051-1063`) and then `update_feedback(applied=True)` is called (`:1081-1088`) ⇒ `prev_applied` is **False** (there was no prior row) ⇒ the bump **DOES** happen — exactly one bump for the row. The implementation must therefore read `prev_applied = False` when `record is None`, **not** `bool(getattr(record, 'applied', False))` folded the wrong way.
- **existing-row path** (`:1113+`, `record` present): `prev_applied = bool(record.applied)` ⇒ the bump happens only on the **False→True transition** ⇒ a repeat filing of the same feedback bumps **+0**.
⇒ Exactly one bump per usage row, and the first legitimate bump is never suppressed.

**The residue (data).** The already-accumulated +480 is a **historical** divergence. "Converge to 0" therefore requires **three** steps, not one:
- **(i) stop the leak** (the transition-based bump above);
- **(ii) reconcile the existing gap** — a **one-time, audited, reversible** counter repair. **AS BUILT (2026-11-09): `scripts/audit/skill_counter_reconcile.py`** — required `--db-url`, dry-run by default, `--apply` writes + emits a receipt carrying every touched row's before/after (the reversal input), `--revert <receipt>` restores the pre-values, `--measure` emits the canonical reading, and `--compare A B` evaluates the ≥ 24 h convergence leg (exit 3 `insufficient_elapsed` rather than a false claim). PINNED by `tests/unit/test_skill_counter_transition_and_reconcile.py` (19 nodes; M-A4e/f/g). **The mechanism is **ruled in §9 (E4)**: NOT a `.sql` migration (P4: the runner is SQLite-only, the live DB is PostgreSQL where the runner is a documented no-op, and a fresh-SQLite boot dies at `20260714_000001` before reaching any new file). The repair is an operator-run, dry-run-first script that recomputes each skill's `total_applied` from its own `applied=True` usage rows, writes an **audit receipt** (every row touched, before/after), and is **reversible** (the receipt carries the pre-values);
- **(iii) prove convergence** — the drift is re-measured at two points **≥ 24 h apart** and must be **0** at both — against the **corrected metric** (within-identity-key divergence), not the old global-name gauge.

**The bespoke gauge — an HONEST FIND-IT (no code site exists today).** The counter is **NOT** in the metrics registry (18 live families, none skill-*), not in `anomaly_clusters`/`dead_letter_items`/`snapshot_usage_counters`. A sweep of `daemon/` finds **NO** duplicate-name / content-divergence aggregation: `grep -rn "GROUP BY name HAVING COUNT" daemon/` → **0 hits** (the named pattern returns nothing). A **broader** `grep -rn "HAVING COUNT" daemon/` returns 5 unrelated sites (`daemon/manager.py:8609`/`:8662` — the obligation triple; `daemon/checkpoint_adapter.py:1198`/`:1898`; `daemon/services/maintenance.py:1477`). (`daemon/repositories/anomaly/repository.py:708` is a DIFFERENT construct — the SQLAlchemy ORM `.having(func.count() > int(max_rows_per_tree))` — and contains no `HAVING COUNT` token, so neither grep returns it.) ⇒ **The gauge does not exist in code.** The redefinition is therefore a **FIND-IT plus a new computation**, and acceptance #3 is **downgraded to the honest form**: *locate the site where the gauge will live (the implementer adds it — most plausibly beside the skill repositories' stats surface, `daemon/repositories/skill/repository.py`, since that is where the per-skill aggregation already lives) OR, if a home is found elsewhere under `daemon/`, anchor it there with `file:line`.* The implementer records the chosen anchor in the PR. **Until then the gauge is NOT wired, and the plan does not claim it is.**

**Within-identity-key divergence baseline.**
- **Dev (ensemble_dev): 0** — 15 rows, 15 identity keys, no divergence (verified by a read-only SELECT on 2026-11-09).
- **Production (ensemble_prod, 630 rows): 32** — caused by **27 stranded A/B pairs** (Ruling E). **NOT 0.** The 0 target is **BLOCKED-ON** the A/B resolution decision (Ruling E). **Do NOT claim 0 as a Phase-1 achievement.**

**Routed residual (disclosed, not planned).** 1,846 usage records land on INACTIVE skills (+60 since 11-04, ~11/day) — something is still selecting inactive skills. This is the same class as P13 (inactive `cb1813ea` served to a live `coder` agent). It is a **selection defect**, not a metrics-honesty defect; it is routed to E4 (the injection-side defence, E4.3) and to a follow-up selection-hygiene item. It is **not** in A's scope.

**Acceptance.**
1. The transition-based bump pin proves: first `feedback(applied=True)` bumps +1; a second identical filing bumps **+0**.
2. The gauge is **redefined** to measure within-identity-key divergence (and/or within a single lineage family) — the global-name gauge is retired, and the redefinition is **documented as a metrics-honesty fix** (this is what A is for).
3. The gauge's **home is chosen and recorded** (an honest FIND-IT — **no duplicate-name aggregation exists in `daemon/` today**; `grep -rn "GROUP BY name HAVING COUNT" daemon/` → 0 hits). The implementer anchors the new computation with `file:line` (most plausibly beside the per-skill aggregation in `daemon/repositories/skill/repository.py`) and records the anchor in the PR. **Acceptance is the anchor, not a claim that the gauge pre-existed.**
4. The gauge **reports 32** on prod (the measured baseline), **names the 27-pair cause**, and is **pinned so it can never silently regress**.
5. The **0 target is explicitly marked BLOCKED-ON the A/B resolution decision** (Ruling E) — do NOT claim 0 as a Phase-1 achievement.
6. After the reconciled repair, `Σ total_applied − ledger` = **0**.
7. The per-skill **counter-drift** set — the set of skills where `Σ total_applied` ≠ the ledger sum of acceptance 6, i.e. the SAME set acceptance 6 measures — is **empty** against the corrected identity key, proven in the **pack environment** (a live re-measure with the code fix deployed is impossible in Phase 1: no promote, no live DB write). The tester's re-measure records its own timestamp and uses the same canonical instrument as acceptance 8. This is NOT the divergence gauge of acceptance 4 (which reports 32 and is BLOCKED-ON). **AS BUILT: the pack proves convergence to `drift == 0` on a seeded scratch SQLite (M-A4e/f); the ≥ 24 h SECOND reading cannot exist inside Phase 1 by construction — `evaluate_convergence` returns `insufficient_elapsed` (exit 3) for two readings closer than 24 h, so the leg is PROVEN CAPABLE and left UNSATISFIED-BY-DESIGN, to be satisfied by the tester's post-promote re-measure with the SAME script.**
8. The drift measurement is **published by the same instrument** that reported +480 (otherwise the comparison is not apples-to-apples) — the instrument is the canonical query in `facts-extract` §A3 (`select clock_timestamp(), …`) plus the per-skill mismatch query.

**Pins + mutation legs (registered pack `skill_counter_drift_unit_test`, NEW).**
- **M-A4a**: remove the `prev_applied` guard → RED (duplicate filing bumps the counter again).
- **M-A4b**: bump on `applied_bool is True` regardless of transition (the original) → RED.
- **M-A4c**: leave the gauge defined as global-name divergence → RED (the redefinition pin: the corrected metric must be the one the acceptances read).
- **M-A4d**: make the within-identity-key filter a no-op (global again) → RED (the "stays corrected" pin).
- **M-A4e**: make the reconciliation script a no-op (dry-run only) → RED on the "repair must be applied and receipted" pin.
- **M-A4f**: make the reconciliation write a *different* value than the ledger (off-by-one) → RED.
- **M-A4g**: skip the ≥24 h second measurement → RED.


### A5 — The pin standard for every metric touched

Every A-pin lands in a **registered** pack (a `.agents/tester/PACKS.md` row with: name, invocation, description, mutation matrix, date, verdict) and satisfies the house non-vacuity bar:
> *"a pin that stays green with the guard removed is vacuous and worthless"* — the v0.28.6 lane's F-1 artifact-regeneration trap class.

Therefore every A-pin ships with: (1) a RED→GREEN proof (pristine-sha capture → plant the defect → RED → restore sha-identically → GREEN), (2) a **named mutation leg** (above), and (3) a **registered pack row** — a pin in no registered pack cannot be gate-verified and is not accepted. The A-pack registry additions are listed in §13.3.

**A residual — CLOSED (was disclosed, now fixed):** the metrics *page* rendered F1/F3 from `frontend/src/app/pages/metrics/tabs.manifest.ts` with the comment *"dark until R-ii lands"*. Both frontend files (`tabs.manifest.ts` and `tabs/runtime-tab.component.ts`) now state the TRUE availability post-fix, keeping the four tokens (`R-i`, `R-ii`, `capture_active`, `census inverted`) so the FE spec's assertion stays green. Verification: `tsc --noEmit -p tsconfig.app.json` exit 0; the metrics jest suite **495/495** across 30 suites.

---

## 6. Workstream B — CONFINEMENT COMPLETION

### B0 — The true status of the three named pieces (P3 reconciliation)

| Piece | True status at `ae5bbafed` | Prior-art ruling | Phase-1 ruling |
|---|---|---|---|
| `filesystem_scope` (per-agent read scope) | **NOT BUILT** — `filesystem.py:207-208` absolute-path skip unchanged | D8 res.9: *"a filesystem-layer feature affecting ALL agents: it needs its own commission (config shape, default scope semantics, backward-compat policy)"* | **PARTIALLY BUILT, opt-in, default-OFF** (B3). Honors the deferral's reason — the *fleet-wide default* semantics stay deferred; the opt-in slice has no backward-compat exposure because **no other agent changes behavior**. |
| master skill-system switch | **DEFERRED** — `grep` → 0 hits in `daemon/` | D7 / HANDOVER P3 | **STILL DEFERRED** (B4). The one load-bearing behavior it was supposed to carry — gating the inline mutation path — is instead delivered at the lane gate (B2), which is *where* the gate belongs. The Emperor owns the Phase-2 go/no-go on the switch's UX. |
| Tier-3 direction quarantine / approval | **DEFERRED** — free-text direction is still a verbatim executor prompt | D6: *"There is no approval surface in this lane (who approves, via what channel, with what storage) — a regex content-scan would be a false-safety half-measure"* | **STILL DEFERRED** (B5). The *approval* half needs an authority the plan cannot invent. The *observability* half (the deviation detector) ships under D + E. **Explicitly stated: this plan does NOT overturn D6.** |

### B1 — Prove the `b7da3ed0` shape is REFUSED, behaviorally (not by inspection)

**The shape.** Run `b7da3ed0` @ 2026-10-21 17:23:02Z made **24 logged `edit_file` calls across 10 files** (9 `daemon/` + 1 `tests/`), then a git stash dance, then `ruff check`, `pytest`, then `git add` + `git commit` → commit `660e73453`, +476/−1, UNPUSHED (facts-extract A9).

**What already refuses it.** The **tool allow-list** (`agents/skill-keeper/meta.json` `tools.allow`/`tools.deny`, enforced by `resolve_tool_filter` — `daemon/tools/instance.py:430`, deny-after-allow) plus the fact that the keeper holds **no** write-capable tool. The keeper's resolved surface today:
`allow = [read_file, list_directory, glob_files, grep_files, self, help, knowledge, dynamic-skill, skill-evolution, shared_meta_kv, pok, nest-report]` ·
`deny = [bash, proc, service, write_file, edit_file, inner_soul]`.

**What Phase 1 adds — the behavioral proof.** The shipped evidence is an *allow-list inspection*; the commission asks for a **behavioral** refusal. Two legs:
- **B1a — resolver leg**: build the skill-keeper's tool set through the **production** resolver (`resolve_tool_filter`, `instance.py:430`) against the **production registry map** (`daemon/tools/_tool_registry.py`) and assert the TRUE property: **NO tool in the resolved set writes the PROJECT surface**, and **NO** tool spawns a process, runs a command, starts a service, or writes agent memory. Assert by **capability**, not by name (a future rename must not slip through) — the capability set is derived from the tool's IMPLEMENTATION (reachability through its same-module helpers, including helpers passed to `asyncio.to_thread`) plus the registry's own registration metadata.

  ⚠ **CORRECTED (authorised §17 amendment).** This bullet originally read *"**no** tool in the resolved set can write a file"* — that literal sentence is **FALSE of the shipped surface**, and shipping it as the acceptance criterion would have pinned a lie. TWO tools on the keeper's bound surface write files: `explore` and `experience`, both into the SYSTEM TEMP dir (`<tempdir>/ensemble/context/…`, the house's shared-context save). They are pinned as the separate `writes_temp_file` class and **ENUMERATED BY NAME**. The true, pinned property is: **zero PROJECT-surface writers; the temp-dir writers are enumerated and pinned by name.**
- **B1b — invocation leg**: drive the **tool factory** for a skill-keeper instance and attempt the `b7da3ed0` sequence — `edit_file` ×24, `bash` with `git add`/`git commit`, `write_file`, `inner_soul` — and assert each attempt is **refused at the tool layer** (the tool is not in the resolved set → the call cannot reach a handler) and that **no file was created or modified** (a directory digest before/after).

**Acceptance.** The `b7da3ed0` shape is refused; the project tree digest is unchanged; the keeper still holds its read + skill-evolution + report surfaces.

**Pins + mutation legs (registered pack `skill_keeper_confinement_behavioral_unit_test`, NEW).**
- **M-B1a**: add `"edit_file"` to the keeper's `tools.allow` → RED (the resolved set contains a write-capable tool).
- **M-B1b**: move `edit_file` from `tools.deny` to `tools.allow` *and* keep the deny → the deny-wins pin must still hold → RED if deny-after-allow ordering breaks.
- **M-B1c**: swap the resolution to allow-after-deny → RED (the structural deny pin).
- **M-B1d**: stub the tool factory to return a permissive set → RED (the invocation leg must catch a resolver that is not the production one).
- **M-B1e**: make the "no write-capable tool" check name-based instead of capability-based → RED (the capability pin must catch a renamed write tool).

### B2 — Close the inline `skill_evolve` hole (the ONE reachable mutation path)

**The hole.** `daemon/tools/skill_evolution_tools.py:230-252` — `skill_evolve` calls `SkillEvolutionService.evolve_skill(...)` **inline**, with **no lane check**. The only gate is `is_pf_address` refusal (`:243-244`). The keeper's allow-list **includes** `skill-evolution` (`agents/skill-keeper/meta.json`), so a running keeper instance can perform a Tier-3 mutation **while both kill-switches are OFF** — which is the live state today (the evolution lane has been OFF since 2026-10-22).

D1's own producer table (`skill-keeper-confinement/decisions.md:31`) records this as *"NOT gated (deliberately) … the job-level gate stops ADMISSION and queued-EXECUTION, but cannot reach an inline tool call on an instance that is already running"*, and D8 item 3 routes it to the master switch — **which is itself deferred**. The hole is therefore open and unowned by any shipped control.

**Ruling.** Implement the master switch's **only load-bearing behavior** at the lane gate: put the **same** lane check the job-level path uses in front of the inline path.
- `daemon/tools/skill_evolution_tools.py:230-252` — before `_invoke_service(...)`, consult `manager.is_skill_evolution_lane_enabled()` (`daemon/manager.py:7992-8004`, the *single resolution point* used by the dispatcher, the metric-scan fallback, and the job processor's execution-time check). If the lane is OFF → return the **same honest refusal envelope** shape the `skill_fix` tool already returns (`daemon/tools/skill_tools.py:1306-1313`, the message text at `:1308-1312`: "NO skill-keeper job was created" — not a fake success). (the range shifted **+11 lines** with the E3 display-hygiene commit `90eeefa22`; at the plan-time tree `ae5bbafed` the same block sat at `:1295-1302`. Lint this cite against the CODE — **do not re-fix it back** to a plan-time number).
- `skill_analyze` (Tier-2) stays **ungated** — D1's deliberate carve-out (`decisions.md:31`).
- **`skill_create` is a ROUTED RESIDUAL to the Phase-2 master-switch decision** (creating a skill row is bank AUTHORING, not Tier-3 evolution of an existing skill, so the lane gate does not apply). **F-12 citation CORRECTION (authorised §17 amendment):** `plan-review-council.md:119` was cited as the disclosure covering this — that text is about the **INJECTION** flag (`skill_injection` does not gate `skill_create`). It does **NOT** cover the evolution lane and therefore never disclosed this path. The "disclosed" label was imprecise; `skill_create` is **open and routed**, not disclosed-and-closed.
- Do **not** add a new env var and do **not** add a new switch: reuse `is_evolution_lane_enabled` + `ENSEMBLE_SKILL_EVOLUTION_ENABLED`.

**Why this is safe in Phase 1.** The lane has been OFF for 18 days; no live traffic is served by the inline path today; the change only moves behavior **toward refusal**. It also does not flip anything: with the switch unset, the lane is OFF, so the inline path refuses — exactly as the job-level path already does.

**Acceptance.** With the lane OFF, `skill_evolve` returns a truthful refusal and **no** skill row is created/modified. With the lane ON (Phase 2, or a unit-test env override), the inline path behaves exactly as today.

**Pins + mutation legs (registered pack `skill_id_verbatim_contract` — the EXISTING registered pack, PACKS.md:867; **VERIFIED 2026-11-09: it DOES run `tests/unit/test_skill_evolution_lane_gate.py`** — the pack script carries `LANE_GATE_FILE="tests/unit/test_skill_evolution_lane_gate.py"` and executes it in phase 1 plus four mutation legs on it; the wiring landed in commit `c26b8ea10` ("run the lane-gate pin and add the B2 mutation legs"). The approver's "no pack currently runs it" observation was TRUE when the plan was written and is now **STALE** — the B2 pin goes inside it as an implementation step — see §13.3).**
- **M-B2a**: delete the lane check from `skill_evolution_tools.py` → RED.
- **M-B2b**: invert the check (`if lane_enabled: refuse`) → RED.
- **M-B2c**: make the check read the switch directly instead of `manager.is_skill_evolution_lane_enabled()` → RED (the single-resolution-point pin).
- **M-B2d**: make the refusal envelope claim success → RED (the truthfulness pin).

### B3 — Opt-in read scope for the keeper (the `filesystem_scope` slice)

**Scope, corrected (P5) — anchors RE-VERIFIED against the code (the plan's earlier walk-tool cites were WRONG — they pointed inside `write_file` = pre-PR def `:1107`, and `edit_file` = pre-PR def `:1510`, so those two anchors are DELETED).** **SHIPPED (re-adjudicated 2026-11-09): all FOUR read tools carry the scope** — `read_file` (call `:1007`), `list_directory` (call `:925`), `glob_files` (call `:1104`) and `grep_files` (call `:1670`) — through ONE resolver, `_resolve_within_scope` (`daemon/tools/filesystem.py:389-462`), reached from the scope hook in `_resolve_within_workdir` (`:485`, scope branch `:505-511`) and its walk-root twin `_resolve_search_root` (`:555`, scope branch `:595-599`); the walk results are additionally filtered by `_scope_filter_candidates` (`:465-482`, called at `:1119` and `:1698`). **The keeper's residual read reach is therefore EMPTY for the credential-hazard class** — relative-only paths, the factory-bound trusted anchor, strict containment, and the `.env`-family deny (`_SCOPE_DENIED_BASENAMES` `:352` via `_is_scope_denied_basename` `:355`) refuse the repo `.env`, `~/.ssh/*`, `/proc/self/environ` and another agent's directory alike. **SUPERSEDED — HISTORY ONLY, not a live claim (kept so the earlier adjudication stays auditable, and explicitly WITHDRAWN as a scope statement):** the pre-B3 reading held that the absolute-path skip in `_resolve_within_workdir` (`:197-225` at plan time) affected only `read_file` (`:670`) and `list_directory` (`:596`), while `glob_files` (def `:754` → `_resolve_search_root` call `:764`) and `grep_files` (def `:1309` → `:1322`) “already resolved” through `_resolve_search_root` (`:252-283` at plan time) — i.e. that the residual was two tools. **That reading was REFUTED**: the 2026-09-23 RAM-walk fix bounded the walk against the **AGENT-SUPPLIED `workdir`**, so for a scoped agent the walk could be pointed outside its anchor and CONTENT exfiltration was proven. Every line number in this HISTORY sentence is a plan-time anchor and has since drifted.

  ⚠ **CORRECTED (authorised §17 amendment).** The premise *"glob_files / grep_files are already fenced"* was **FALSE for a scoped agent**, and this paragraph was the source of the error: `_resolve_search_root` bounded the walk against the **AGENT-SUPPLIED `workdir`** — the same anchor bug the file tools had. The re-adjudication PROVED content exfiltration on the keeper's own bound surface (`grep_files(pattern=<canary>, path=<out-of-anchor>, workdir="/")` returned the canary). B3 now fences **all four read tools identically**, through the SAME factory binding and the SAME `_resolve_within_scope` policy.

**Write-path scope — EXPLICITLY OUT OF SCOPE.** B3 changes **read** resolution only. No write-path behavior changes: `write_file` / `edit_file` / `inner_soul` resolution is untouched, and the keeper holds **no** write-capable tool (the allow-list strip, §B1), so a write-path fence would defend a surface the agent cannot reach.

**Ruling — opt-in, default-OFF, keeper-only in Phase 1.**
- Add a per-agent **read scope** declaration, read from `agents/<name>/meta.json` (the house's existing per-agent config surface), e.g. `"read_scope": "project"`.
- Honor it in the **four read tools** (`read_file`, `list_directory`, `glob_files`, `grep_files`) with ONE policy, at ONE definition site:
  - **RELATIVE paths only.** An **absolute** path argument is REFUSED unconditionally for a scoped agent (the acceptance list below requires `read_file("<repo>/.env")` to be refused even though the repo IS the workdir, so "outside the workdir" is NOT the rule — "relative only" is);
  - the base is the **daemon-resolved trusted workdir** (the instance's project `main_directory`, bound at the tool factory) — **NEVER** the tool-call's `workdir` ARGUMENT, which is agent-supplied and would let the agent move its own boundary; for a scoped agent a supplied `workdir` is **REFUSED** outright;
  - a relative path that escapes the anchor is REFUSED (strict containment — the guard's temp-dir allowance is NOT applied to a scoped agent);
  - the **`.env` family is REFUSED at any depth under the anchor** (read, listed and searched). The anchor IS the project repo, so relative-only alone still permitted exactly the file DS-15 names.

**RECONCILIATION (fix round 1 + 2, authorised §17 amendments).** This rule line originally read *"an absolute path OUTSIDE the workdir is REFUSED"* — which CONTRADICTS the acceptance list three paragraphs below (`<repo>/.env` refused, where the repo IS the workdir). **The acceptance list governs.** The shipped semantics are STRICTER than either line: relative-only + trusted anchor + the `.env`-family basename rule. The implementation and its pins are the authority; this paragraph is corrected to say so. **The shipped rule in one line:** **RELATIVE paths only** — anchored at the **daemon-resolved trusted workdir** (factory-bound), with strict containment and the **`.env`** family refused at any depth under the anchor.
- **Default = absent ⇒ today's behavior for every other agent** ⇒ zero fleet-wide exposure ⇒ the backward-compat question D8 res.9 lists as the blocker **does not arise in Phase 1**. The fleet-wide default (and the MCP/remote-hands interaction D8 res.9 also lists) stays deferred with the Emperor's Phase-2 go/no-go.
- Declare `"read_scope": "project"` for **skill-keeper only** in Phase 1.

**Acceptance.** With the keeper's scope declared, `read_file("/etc/passwd")`, `read_file("<repo>/.env")` and `read_file("/proc/self/environ")` are **refused**; `read_file("<repo>/agents/skill-keeper/meta.json")` (relative, in-workdir) still works; **every other agent's read behavior is byte-identical to base** (proved by a base-vs-head behavior digest over the 4 read tools).

**Refusal-text cite (corrected).** The refusal text the keeper sees is the `skill_fix` refusal envelope — *"NO skill-keeper job was created"* — at `daemon/tools/skill_tools.py:1306-1313` (the message text is `:1308-1312`). (The plan's earlier cite pointed at the `dispatched_job_id` outcome **COMMENT**, not the refusal text; that cite is DELETED.) (Cite drifted **+11 lines** with the E3 display-hygiene commit `90eeefa22`: the plan-time tree `ae5bbafed` carried the same refusal block at `:1295-1302`, and the plan-time cite `1290-1300` was already an approximate range over it (it began at the `elif` guard and ended mid-message). **Do not re-fix this back** — lint it against the CODE, not against the old number.)

**Pins + mutation legs (registered pack `skill_keeper_read_scope_unit_test`, NEW).**
- **M-B3a**: remove the scope check from `_resolve_within_workdir` → RED.
- **M-B3b**: make the scope check apply to *relative* paths too → RED (relative in-workdir reads must stay legal).
- **M-B3c**: make the default scope `"project"` for every agent → RED (the no-other-agent-changes pin).
- **M-B3d**: exempt the keeper from the scope → RED.

### B4 — Master switch: still deferred (explicit)

Recorded, not built. The Phase-2 runbook (§15) must therefore specify the switch flip as **two independent env flips** (capture, then evolution) rather than one master lever, and must state that the master switch remains an open Emperor decision.

### B5 — Direction quarantine: still deferred (explicit) — but the *observable* half ships

D6's reason stands and this plan does not overturn it. What Phase 1 ships instead is the **observable** half of the quarantine, under D: the direction text is recorded with provenance, and a keeper that deviates from the sanctioned tool surface is **detected and announced** (D5/D6). The **approval** half (who approves, through what channel, into what storage) is named as an open Emperor decision in §16.

---

## 7. Workstream C — BRAKE VERIFICATION (verify, never engage)

**The brake.** `job_queues.is_paused` on `bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2` (`system_parallel_queue`, `agents-ensemble` project).

**Census resolution (resolves the apparent contradiction).** The census reports "queue `bd075f0b` has no matching `job_queues` NAME (none `ILIKE '%skill%'`)". That is **NOT evidence the queue row is missing** — the filter was `name ILIKE '%skill%'` and the queue is very likely named **`system_parallel_queue`** (critical note `ed6da139` records *"system_parallel_queue PAUSED"* AND *"Queue bd075f0b is_paused=true since 19:54:26Z (verified in DB)"*). ⇒ **C's brake lever is almost certainly `job_queues.queue_id = 'bd075f0b…' → is_paused`, and the name simply does not contain "skill".** The plan must resolve by **ID, not by name LIKE**, and the runbook must not carry a "the queue row may not exist" uncertainty — that would make the primary kill-switch look unusable.

**Verification read (exact, by ID — EXECUTED against `ensemble_dev` on 2026-11-09; both reads parse and run cleanly, 0 rows expected on dev because that queue/project exist in `ensemble_prod`). `job_queues` has **`queue_name`**, NOT `name` (`daemon/repositories/job_queue/models.py:208`); the PK is **`queue_id`** (`:201-204`). `projects` has **`project_id`** (PK, `daemon/repositories/project/models.py:381`) and a UNIQUE **`name`** (`:389`) — `'agents-ensemble'` is a NAME, not an id.
**C1:** `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';`
**C2:** `SELECT project_id, name, job_queue_paused FROM projects WHERE name='agents-ensemble';`
**EXECUTED OUTPUT (read-only SELECTs on `ensemble_dev`):** C1 → header `queue_id | queue_name | is_paused | updated_at`, `(0 rows)` — SQL valid, no column error. C2 → header `project_id | name | job_queue_paused`, `(0 rows)` — SQL valid, no column error. (A0-row dev result is the EXPECTED outcome: the plan is verifying that the SQL RUNS, and the row lives in `ensemble_prod`.)

**Current state (P7 — DISCLOSED, and it contradicts the standing critical note).**
`is_paused = false`, `updated_at = 2026-10-26T21:50:04.439103+00:00` — the Oct-22 containment pause was **lifted on 2026-10-26**. The standing project critical note (*"system_parallel_queue PAUSED until confinement fix promoted"*) is **STALE**. `facts-extract` findings `lane1-runtime-db.md:46` record **0 of 132** `job_queues` rows paused in `ensemble_prod`, all 26 `system_parallel_queue` rows `is_paused=false`.

⇒ **The brake is a Phase-2 action, not a Phase-1 premise.** Phase 1 verifies and documents it; the runbook arms it.

**Mechanism (verified in code).**
`daemon/services/job_processor.py:926-930` — per scan iteration, `if queue.is_paused: continue`. The queue row is re-read **every poll cycle** ⇒ the brake takes effect **without a restart**. The poll cadence: `JobProcessor(poll_interval: float = 30.0)` (`job_processor.py:118`, default) — the *worker* poll (`SERVICES_WORKER_POLL_INTERVAL`, `daemon/config.py:1912`, default 0.5 s) is a different loop and is **not** the job-queue poll.

**Verification procedure (READ-ONLY — every step is a SELECT or a GET; no step writes):**

| # | Step | Command | Expected |
|---|---|---|---|
| C1 | Read the queue row | `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';` | `is_paused=false` (as of the census); record `updated_at` |
| C2 | Read the project-level brake too | `SELECT project_id, name, job_queue_paused FROM projects WHERE name='agents-ensemble';` (`projects` PK is `project_id`; `name` is UNIQUE) | `false` (level 1) — the brake has **two** levels: project `job_queue_paused` and queue `is_paused` |
| C3 | Confirm the read path is per-iteration | `grep -n "is_paused" daemon/services/job_processor.py` | `:929` inside the per-iteration loop |
| C4 | Confirm the second line of defence | `grep -n "job_queue_paused" daemon/services/job_queue_service.py` | the project-level start-job re-check at `daemon/services/job_queue_service.py:4516` and `:5468` (`if project and project.job_queue_paused:`) — the earlier `≈:2976-2982` cite was WRONG (that range is a mission-list filter docstring) and is DELETED |
| C5 | **Latency derivation** (no execution) | poll interval (30 s default) + the in-flight job's own runtime | a job **admitted before** the pause still runs to completion; a job **not yet admitted** does not start for up to **one poll interval** |
| C6 | Record the brake's blast radius | 25 `skill_capture` + 114 `skill_evolution` rows live on this queue; it also carries **all** other parallel-queue traffic for the project | the brake is **broad**, not skill-specific — DISCLOSED as an operational cost of the emergency lever |

**Acceptance.** C1–C6 are executed and their output is recorded in the Phase-1 evidence with timestamps; the runbook's OFF lever cites the exact command; the "expected latency" is a **derived** number (≤ 1 poll interval for a not-yet-admitted job; unbounded for an in-flight one) — not a guess.

**Pins (no production code changes ⇒ no mutation legs are possible in C).** The C workstream is **documentary**: the verification is a script (`scripts/audit/skill_lane_brake_verify.py`, dry-run-by-default, read-only, no UPDATE path at all) whose **own** pack proves it cannot write: a mutation leg that injects an `UPDATE` into the script must make the pack RED (the pack greps the script for write verbs).

---

## 8. Workstream D — MONITORING (capture-lane observability)

**Commissioned explicitly.** The Oct-22 deviation was invisible because the capture lane left **no residue** on refusal and no audit trail on success (report §2.8: *"the admission-refusal mechanism leaves no residue"*; §6.2: keeper transcripts are destroyed by the 168 h checkpoint TTL).

### D1 — The refusal-semantics rule (do not conflate)

| Kind | Residue | Consequence for observability |
|---|---|---|
| **Refused ADMISSION** | **NO ROW AT ALL** — the producer mints nothing (`skill_metrics_service.py:595-596` returns before any enqueue; `skill_job_dispatcher.py:720-727` returns `None`; `skill_evolution_tools.py:442-455` returns a `skipped` envelope) | A row-counting monitor **cannot** see it. It needs a **counter** (or a log-derived metric). |
| **Refused EXECUTION** (an already-queued row) | **A TERMINAL ROW IS WRITTEN** — `atomic_transition(from_status='queued', to_status='done', terminal_reason='lane_disabled')`, return checked; race → `complete_job(FAILED)`; no `failed_at` (`job_processor.py:738-844`) | Already observable via `job_queue_items.terminal_reason='lane_disabled'`. |

⇒ **D must not report one as the other.** Two distinct signals with distinct names, and the runbook must say which to read.

### D2 — What lands (per capture run)

| Signal | Source | Where it lands | Observable today? |
|---|---|---|---|
| **Run id** | minted at admission (`skill_job_dispatcher.py:730-735` → `job_id`) + a `capture_run_id` in the job metadata | `job_queue_items.job_metadata` | **PARTIAL** — `job_id` exists; a stable per-run id does not |
| **Originating agent** | `task_details['agent_id']` (already in the metadata) | `job_queue_items.job_metadata.task_details` | **YES** (but only for admitted runs) |
| **Prompt source** | `task_details['task_message']` + the capture flow's origin (completion hook vs `skill_execute_capture` tool) | new metadata field `prompt_source` | **NO** — the tool path and the hook path are indistinguishable after the fact |
| **Prompt-content shape** | non-empty `task_message` (the P0 gate: `skill_evolution_service.py:1628-1629`) + a **shape** descriptor (length bucket + presence of the agent id) — **never the raw prompt text** (credential hygiene: the F7 lesson, `deferred_key_rotation_f7_addition`) | new metadata field `prompt_shape` | **NO** — the raw text is stored today (`task_message` is in `task_details`), and the F7 finding is exactly that raw prompt content leaks |
| **Admission vs execution outcome** | admission refusal = *no row* ⇒ a counter; execution refusal = `terminal_reason='lane_disabled'` ⇒ a row | counter + row | **PARTIAL** |
| **Refusal counts** | a counter incremented at every refusal site (`skill_metrics_service.py:595`, `skill_job_dispatcher.py:720`, `skill_evolution_tools.py:442`, `job_processor.py:738`) | a metrics gauge (the metrics registry is the house's counter surface) | **NO** — today only log lines |
| **Keeper deviation detection** | the Oct-22 cause = *legitimate feedback → Tier-3 FIX → keeper deviation at the A/B guard*. Detection = the keeper attempts an action outside its sanctioned surface (a forced resolve, a direct-SQL landing, a write) | a daemon-side detector + an anomaly row | **PARTIAL** — the forced-resolve guard exists (`skill_evolution_service.py:1381-1388` → HTTP 400) and the allow-list exists, but nothing *announces* a deviation |
| **Re-fix rate** | FIX admissions per `(project, skill)` per window | counter (the same gauge family) | **NO** — this is E1's durable ledger (§9) |
| **Anomaly hook** | `report_anomaly` — the daemon-side `AnomalyService.accept_report(agent_id, instance_id, category, description, severity, suspected_cause)` (`daemon/services/anomaly_service.py:692-718`); the keeper already holds the `nest-report` category (`agents/skill-keeper/meta.json` allow) | `anomaly_reports` rows | **YES** — the surface exists and the keeper may use it; nothing *triggers* it automatically |

### D3 — The design rulings

1. **The anomaly hook is a DAEMON-SIDE trigger, not a hope that the agent reports.** The Oct-22 keeper did not announce itself. D must make the *daemon* announce a detected deviation (an automatic `accept_report` call from the detector), with the `nest-report` tool remaining the agent-initiated path. The category vocabulary is closed — **`ANOMALY_CATEGORIES` at `daemon/services/metrics_query/wiring/vocab.py:92`** (imported into the anomaly service at `daemon/services/anomaly_service.py:147`); the range the plan cited earlier inside the anomaly service is FENCE documentation, not the vocabulary — the auto-trigger must fold into the canonical tuple (`other` is the reserved fold target; `queue-scheduling` and `tool-failure` are the honest candidates).
2. **Never store raw prompt text in the new observability fields.** The capture path already carries `task_message` (the P0 gate's subject); D adds a **shape**, not a copy. The F7 lesson (`deferred_key_rotation_f7_addition` in the project metadata) is the standing precedent: a name + a sha256 prefix, never the value.
3. **What is observable TODAY vs what Phase 2 turns on** (the commission asks for this marking explicitly):

| | Observable today (no switch flip) | Turns on in Phase 2 |
|---|---|---|
| Capture admission counters | **YES** — a counter increments even when the switch is OFF (the refusal path is where the counter lives) | the counter's *non-zero* admission rate |
| Execution-refusal rows | **YES** — `terminal_reason='lane_disabled'` is written regardless | the population size |
| Deviation detection + auto-anomaly | **YES** — the detector reads tool-call records, not the lane state | the anomaly *rate* |
| Re-fix rate | **YES** — counts admissions (which are zero while the lane is OFF) | the burst signature |
| Keeper transcript retention | **NO** — 168 h TTL destroys it (report §6.2) | Phase 2 must extend/harvest retention **before** the first flip |

⇒ **D4 — retention (DECIDED — the council's F-10: the release gate must not assume an undetermined design).** The commission's D is worthless without a retention answer, because the Oct-22 evidence died twice. **Phase 1 ships the structured-log sink**, not a new table:
- **The sink**: one structured log record per capture run — run id → agent → prompt source → prompt shape → admission/execution outcome → refusal count → deviation flag — emitted by the daemon's existing logger to a dedicated file under the daemon's data dir, with a **retention window ≥ 30 days** (the Phase-2 runbook's observation window; ≥ 90 days preferred).
- **Why the sink, not a table:** a new table means a `.sql` file **plus** an idempotent statement in `_ensure_postgres_columns` (one transaction — any entry's failure aborts boot, critical note 48a7f77f) — a schema risk this release does not need. The sink is **additive, schema-free, and read-side restart-free**.
- **The durable-table variant is a LATER release** (it needs the retention sweep + the query surface). It is **explicitly OUT of Phase 1 scope**, and the release gate therefore gates on the **sink's presence + the retention policy being written**, not on a table.
- **The read side must not require a restart** (the observer reads the sink file directly).

**Acceptance.** Every D-signal has a named producer site, a named read site, and a pin. The "observable today" column in the table above is *proven*, not asserted: the pins demonstrate that each counter/row/detector fires with the switches **unset**.

**Pins + mutation legs (registered pack `skill_capture_observability_unit_test`, NEW).**
- **M-D1**: remove the refusal counter increment at one site → RED (the "every refusal site increments" pin).
- **M-D2**: make the auto-anomaly trigger a no-op → RED.
- **M-D3**: store the raw `task_message` in the new `prompt_shape` field → RED (the hygiene pin — a raw prompt in a new field is RED).
- **M-D4**: merge the admission-refusal and execution-refusal signals into one counter → RED (the do-not-conflate pin).

---

## 9. Workstream E — SKILL-FIX SYSTEM IMPROVEMENT

### E1 — Re-fix burst guard (re-scoped per P6)

**What already exists (D2, shipped 2026-10-22, live v0.29.7).** `skill_job_dispatcher.py`:
- `idempotency_key = "skill_fix:<normalized_project>:<skill_id>"` on every FIX admission (`:669-671`) — dedups onto a live non-terminal row (partial unique index `idx_job_idempotency`, `job_queue/models.py:300-306`).
- an in-process per-`(project, skill)` cooldown, default **1800 s** (`:327`, `:641-657`, stamped at `:678`) — RAM-only by design.

**What is missing.** (a) The refusal is a single WARNING log line — **no human-visible signal**; (b) the cooldown is RAM-only — it **drains on restart**; (c) a sliding window still permits a **slow trickle** (one admission per 30 min, forever).

**Ruling — a durable, capped, human-visible guard, keyed on SKILL IDENTITY, with NO new table.**

**⚠ The cap CANNOT be keyed on `idempotency_key` — that guard is dead by construction.** `JobQueueService.enqueue` gives a re-admission a **unique synthetic key**: `idempotency_key = f"{idempotency_key}#{uuid.uuid4().hex[:8]}"` (`daemon/services/job_queue_service.py:1596-1608`), under a **partial UNIQUE** index (`idx_job_idempotency`, `daemon/repositories/job_queue/models.py:300-306`). A count over `idempotency_key` therefore matches **at most one** row ever ⇒ the cap can **never trip**. (The council proved this; it is F-9.)

**The correct key is the SKILL IDENTITY, read from the job metadata.** The census confirmed `metadata` carries `skill_id` and `direction` on all 116 evolution job rows. ⇒
- **Add** a repository count: `count_fix_admissions_since(skill_id, since_iso)` over `job_queue_items`, filtering on `job_type = 'skill_evolution'` AND the **dialect-split** JSON path: **PG** `metadata->>'skill_id' = :skill_id`; **SQLite** `json_extract(metadata, '$.skill_id') = :skill_id` (per `job_queue/repository.py:3249-3250`, `task/repository.py:2410-2422`) AND `metadata->>'evolution_type' = 'FIX'` (PG) / `json_extract(metadata, '$.evolution_type') = 'FIX'` (SQLite) AND `created_at >= :since`. (A **dialect-split** JSON-path filter — `metadata` is `JSONBType`, plain JSON/TEXT on SQLite where raw `->>` breaks. ⚠ **The filter is UNINDEXED**: there is **no `created_at` index** on `job_queue_items` (indexes: instance, project, queue, admission_state, idempotency_key, terminal_reason, deleted_at), and the JSON path has no index at all. The scan is bounded by the window + `job_type`, not by an index — acceptable at the FIX-admission volume (the census counts 116 lifetime rows) and pinned as such.)
- **The cap**: refuse a FIX admission when the count of FIX admissions for the same **skill id** within the **window** exceeds **N**. **Default: 3 FIX admissions per skill per 24 h** (the Oct-21 burst was 3 in 37 min; 3/24 h is the human-visible trip point). Configurable via `SkillEvolutionConfig` — the config fields `fix_burst_cap` / `fix_burst_window_seconds`. ⚠ **CORRECTED (2026-11-10, M5): the earlier wording here said "no new env var", and that was FALSE.** There is still **no new `ENSEMBLE_*` FLAG** (the house rule "no new `ENSEMBLE_*` flags without owner authorization" is respected: the cap's brake is a CONFIG field). But the env surface is NOT empty — pydantic's `env_prefix` exposes EVERY field of `SkillEvolutionConfig` under `SKILL_EVOLUTION_`, so the two knobs resolve as `SKILL_EVOLUTION_FIX_BURST_CAP` / `SKILL_EVOLUTION_FIX_BURST_WINDOW_SECONDS` (the SAME alias surface every sibling field already has, not a new flag), and both are documented in `.env.example` / `.env.prod.example`. ⚠ The E4 stale-deactivation brake `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` IS a genuinely new env var — a DIFFERENT lever, mandated by **DS-54** (a PLAN AMENDMENT, Leader-ratified 2026-11-09), and it is documented too.
- **Human-visible signal**: on a cap refusal, write an **anomaly row** via the same daemon-side `AnomalyService.accept_report` path D uses (one mechanism, two callers) + a structured counter. The refusal envelope returned to `skill_fix` must say **"refused: burst cap"** — not the generic lane-off text (truthfulness).
- The RAM cooldown **stays** as the fast path; the durable cap is the backstop across restarts.

**Acceptance.**
1. The cap **trips**: a 4th FIX admission for the same skill id inside the window is **refused** with the `"refused: burst cap"` envelope, an anomaly row, and a counter increment.
2. The refusal is **durable across a restart** (the count is read from committed job rows, not RAM) — a test that restarts the dispatcher between admissions still refuses the 4th.
3. **Different skills are independent**: a FIX admission for skill B is NOT refused because skill A tripped the cap.
4. The count is keyed on `metadata->>'skill_id'` — **not** on `idempotency_key` (the dead-guard shape).

**Pins + mutation legs (registered pack `skill_evolution_unit_test` — the EXISTING registered pack, PACKS.md:3702; the new pin file `tests/unit/test_skill_fix_burst_guard.py` goes inside it as an implementation step — see §13.3).**
- **M-E1a**: revert the count to key on `idempotency_key` → RED (the 4th admission is admitted ⇒ the count returns to 1 and the cap never trips).
- **M-E1b**: remove the `metadata->>'skill_id'` filter → RED (the count matches the wrong rows).
- **M-E1c**: drop the window filter (`created_at >= since`) → RED (old admissions outside the window are counted).
- **M-E1d**: make the cap refuse the **wrong** skill (ignore the skill id in the comparison) → RED (acceptance #3).
- **M-E1e**: remove the anomaly write on a cap refusal → RED (the human-visible signal is gone).
- **M-E1f**: make the refusal envelope claim success → RED (truthfulness).

**Coupling with G — the deliberate ordering (the addendum's explicit requirement).**
G and E1 touch the **same file** (`daemon/services/skill_evolution_service.py` / the dispatcher path) but **different seams**:
- **E1** = the **FIX admission** path (`skill_job_dispatcher.enqueue_evolution` → `dispatch_fix` → `skill_fix` tool). Its guard is a **count over historical rows** — a read of committed rows, NOT a concurrent-write-sensitive operation.
- **G** = the **A/B resolution** path (`skill_evolution_service.check_ab_test_resolution` → the concurrent `asyncio.gather` fan-out at `:1448-1457` / `:1474-1483`). Its defect is a **shared-connection transaction interleaving**.

**Order: G FIRST, then E1.** Rationale: G's fix removes a shared-session concurrency hazard in the very file E1 will touch. Doing E1 first would place a new read-of-committed-rows call adjacent to an unfixed shared-connection race, and E1's guard would then be **sensitive to the same concurrency conditions** — exactly the interference the addendum warns about.
**Non-interference proof (required, not asserted):**
1. E1's guard reads **committed** `job_queue_items` rows only (`created_at` on committed rows) — it never touches the skill tables and never shares the A/B transaction. It is therefore **immune** to the StaticPool race by construction.
2. G's fix (fixture/session isolation) touches only the **test fixture / session provisioning** for the concurrent fan-out — it must **not** change the production fan-out's concurrency semantics. If G's fix requires changing production code, it MUST be a **separate session per operation** (not a lock, not a serialization) — a serialization would mask the very defect the burst guard is meant to expose.
3. The tester's gate must run the AB-resolution node **with E1's guard present** and prove the node stays green — proving the burst guard does not depend on the (now-isolated) shared session.

### E2 — Make the capture→evolution diversity path ready; do NOT fabricate diversity

**The premise is UNVERIFIABLE at column level (census).** There is **no `skill_evolution` table** — the 116 rows are **JOB rows** (108 done on queue `bd075f0b`, 7 done on `cd02c5d2`, 1 dead on `bd075f0b`). No row carries an action/verdict key in metadata (`metadata->>'action'` = none for 116/116; the sample is `{skill_id, direction}`), and `skill_lineage` (60 rows = the 60 `'evolved'` skills) has **no verdict column** either. ⇒ **"116/116 are FIX" is job-message-text inference, NOT a DB fact.** E2 must say that plainly and must **not** claim to have measured it.

- **Ruling: no synthetic rows, ever.** No test fixture may write a `DERIVED`/`CAPTURED` row into the **live** DB, and no "diversity demonstration" may be reported as if it were evidence of a working path.
- **What Phase 1 delivers (BLOCKER 5 — restated against the shipped code):** the path is **READY in the sense that NON-CAPTURED evolution types (FIX / DERIVED) are exercisable end-to-end**, and the **CAPTURED-origin refusal is DOCUMENTED behavior, not a gap**: `SkillEvolutionService.evolve_skill` (`daemon/services/skill_evolution_service.py:452-457`) **raises `ValueError`** for CAPTURED-origin skills, and the docstring (`:419`) says the CAPTURED path is "intentionally NOT routed through this method". The plan **does NOT reverse that refusal** and does **NOT** claim a CAPTURED dispatch path exists. (b') the **capture admission path** produces a `skill_capture` JOB with `metadata={"task_details": …}` and `job_type='skill_capture'` (`skill_job_dispatcher.py:683-735`) — the shipped shape; **no `evolution_type` key exists anywhere on the capture path** (verified against the census: the 116 job rows carry `{skill_id, direction}`).
- **The statement is written into the plan, the decision log, the release notes, and the Phase-2 runbook**: *"the capture→evolution diversity remains UNEXERCISED; Phase 1 makes it reachable, not exercised; the first live CAPTURED row is the Phase-2 proof. (The '116/116 are FIX' premise is job-message-text inference, not a DB-measured fact.)"*
- **Disclosed**: the diversity gap is **not** closed by Phase 1. The PM ledger's "re-enable skill capture" goal depends on Phase 2 producing the first live CAPTURED row. **What would make the premise verifiable:** a verdict column on `skill_evolution` (or `skill_lineage`) populated by the keeper at execution time — a schema change that is out of Phase 1's scope (and would need its own `.sql` file + `_ensure_postgres_columns` statement).

**Acceptance (BLOCKER 5 — restated; CAPTURED dropped from the tool-routing acceptance because the shipped code REFUSES it by design).
1. **FIX / DERIVED are exercisable end-to-end**: the `skill_evolve` tool's `evolution_type` argument routes `FIX` and `DERIVED` to `SkillEvolutionService.evolve_skill` — a unit-level proof, in a pack environment, never against the live DB.
2. **The CAPTURED-origin refusal is DOCUMENTED behavior, not a gap**: `evolve_skill` raises `ValueError` at `daemon/services/skill_evolution_service.py:452-457`; the pin PROTECTS that refusal.
3. The **capture admission path** produces a `skill_capture` JOB with `metadata={"task_details": …}` and `job_type='skill_capture'` (`skill_job_dispatcher.py:683-735`) — the shipped shape; **no `evolution_type` key exists anywhere on the capture path**.
4. **No synthetic diversity row exists anywhere**: a census assertion that no `DERIVED`/`CAPTURED` row was written to the live DB by Phase 1.

**Pins + mutation legs (registered pack `skill_capture_killswitch_unit_test` — the EXISTING registered pack, PACKS.md:2993; the new pin file `tests/unit/test_skill_evolution_type_dispatch.py` goes inside it as an implementation step — see §13.3).**
- **M-E2a**: remove `DERIVED` from the dispatch's accepted `evolution_type` set → RED (acceptance #1).
- **M-E2b**: remove the CAPTURED-origin `ValueError` guard at `skill_evolution_service.py:452-457` → RED (acceptance #2 — the pin PROTECTS the documented refusal).
- **M-E2c** *(re-placed — `evolution_type` does NOT exist on the capture path)*: mutate the **`job_type` stamp** on the capture admission (`JOB_TYPE_CAPTURE` → `JOB_TYPE_EVOLUTION`) → RED (acceptance #3 — the pin asserts the shipped `skill_capture` job shape, not a nonexistent key).
- **M-E2d**: make the dispatch fall through to the FIX branch for an unknown `evolution_type` → RED (the routing is type-driven, not fall-through).

### E3 — Fold the skill-tools display-hygiene follow-ups (board `wi-d340f16c`)

**Board-text status: NOW LANDED (census).** The real item (status **todo**, priority 2, body_len 1037) contains **EXACTLY THREE** bullets:
1. `skill_fix` echo at `daemon/tools/skill_tools.py:1260` prints a bare 8-hex token
2. `skill_feedback` echo at `:1491` prints a bare 8-hex token
3. `_render_pf_view` at `:402` prints the internal `pf-` pseudo-id

The item also states the echoes are **DISPLAY-ONLY** (*"skill_view consumption is NOT affected"* — the v0.28.10 fix already covered every consumable surface) and that **none block the promote**.

**Three items in the commission's summary are ABSENT from the real item** and are carried as **leader-judgment cleanups, clearly labeled NOT-from-board** (or dropped):
- `:1213` skill_fix refusal gap for file-skill ids → **NOT-from-board.** If kept at all, label it leader-judgment, NOT-from-board.
- `to_summary()` dead surface (`project_skill_source/model.py:199-220`) → **NOT-from-board.**
- P7-family citation re-derive → **NOT-from-board.**

| # | Item | Anchor (verified) | Fix shape |
|---|---|---|---|
| E3.1 | bare 8-hex token echo (board bullet 1) | `daemon/tools/skill_tools.py:1260` (`short_id = skill_id[:8]` → the confirmation line at `:1262`) | echo the **full uuid4** (the wi-76d89e11 contract: every emitted id must be `skill_view`-consumable **verbatim**); the 8-hex form is NOT `skill_view`-consumable |
| E3.2 | bare 8-hex token echo (board bullet 2, feedback path) | `daemon/tools/skill_tools.py:1491` | same |
| E3.3 | `pf-` pseudo-id echo (board bullet 3) | `daemon/tools/skill_tools.py:402` (`- id: {getattr(file_skill,'id','')}` → the **internal sha16 pseudo-id**) | echo the **address** `pf:<ns>:<name>` (the agent-facing id, per `project_skill_source/model.py:176-197`), with the pseudo-id available additively under `pseudo_id` (the existing additive pattern) |

**Acceptance (BOARD scope only — the three real bullets).** Every id the skill tools emit is `skill_view`-consumable verbatim (the wi-76d89e11 contract); **the three board bullets (`:1260`, `:1491`, `:402`) are closed**. The NOT-from-board leader-judgment cleanups (`:1213` refusal shape, `to_summary()` wiring, P7 citation re-derive) are **NOT part of this acceptance** — they are tracked separately under their own labels and their own pins (M-E3d/e/f), so the "3 real bullets" closure is **falsifiable on its own**.

**Pins + mutation legs (registered pack `skill_id_verbatim_contract` — the EXISTING registered pack, extended; plus `skill_tools_display_hygiene_unit_test`, NEW).**
- **M-E3a**: revert `skill_tools.py:1260` to `skill_id[:8]` → RED (the verbatim contract pack must catch it).
- **M-E3b**: revert `:1491` → RED.
- **M-E3c**: revert `:402` to the pseudo-id → RED.
- *(NOT-from-board, if kept)* **M-E3d**: narrow `:1213` back to the `pf:`-only refusal → RED.
- *(NOT-from-board, if kept)* **M-E3e**: reintroduce a second, divergent P7 refusal string → RED.
- *(NOT-from-board, if kept)* **M-E3f**: delete `to_summary()`'s caller → RED.


### E4 — Retire the stale/shadow DB skill set (DEACTIVATE, never hard-delete)

**The lever is now KNOWN — two columns that can DESYNC.** `skills.is_active` (bool) + `skills.status` (varchar). Census: `(True,'active')=538 · (True,'ab_testing')=52 · (False,'inactive')=39 · (False,'ab_testing')=1` — that last group is the **desync proof**. Any deactivation MUST set **BOTH** (`is_active=false, status='inactive'`); setting one leaves a half-dead row. **No tombstone column exists** on `skills`. `SkillRepository.deactivate` (`repository.py:383-396`) is the sanctioned writer — it sets **both** — and is the writer E4.1 must use.

**The 6 captured-origin skills are ALREADY RETIRED.** `lineage_origin='captured'` = 6 rows, all `(False,'inactive')`. ⇒ "retire the 6 inactive never-used captured-origin skills" is **ALREADY DONE at the state level**. E4's substantive work is **NOT** the 6. They are a **no-op re-affirmation** (belt-and-braces at most); do not let E4's apparent size rest on them. R2's job is to *identify* them and confirm they are inactive, and to ensure they are never re-activated. Exact ids are needed only if belt-and-braces re-affirmation is kept (C-1 answered-by-census).

**Adjacent hazard.** **52 ACTIVE rows carry `status='ab_testing'`**. Deactivating those **STRANDS OPEN A/B TESTS**. Before any deactivation sweep, check `skill_ab_tests.resolved_at IS NULL`. Plan the guard (E4.1 must join against `skill_ab_tests` and skip any row whose group is unresolved).

**Routed residual (disclosed, not planned).** 1,846 usage records land on INACTIVE skills (+60 since 11-04, ~11/day) — something is still selecting inactive skills. This is the same class as P13 (inactive `cb1813ea` served to a live `coder` agent). It is a **selection defect**, not a metrics-honesty defect; it is routed to E4 (the injection-side defence, E4.3) and to a follow-up selection-hygiene item. It is **not** in A's scope.

**Corrected target set (P2).** The commission's id list is partly wrong:
- `978ae5cc-413b-4c03-a17c-d50e4fe58f56` — gen-0, `lineage_origin=imported`, **already inactive**, group `b9422dc3-…`.
- `08a4332f-c059-4886-92d2-9d4aea191ff8` — **gen-1**, `lineage_origin=evolved`, **`status=ab_testing`, `is_active=true`**, group `2600329e-…`.
- `fd1e01dc-37d5-4a0b-a05e-1933d18a006d` — **gen-2**, `lineage_origin=evolved`, **`status=ab_testing`, `is_active=true`**, **the SAME group** `2600329e-…`.
- `76e3e1a7a` — **does not exist** anywhere in the repo (0 hits).
- The 3 legacy DB gate-runner copies (`9e79ccad`, `fd1e01dc`, `76e3e1a7a`) must be **re-identified against the DB** before any deactivation. `9e79ccad` is not among the four rows the Phase-0 artifacts enumerate → **it must be identified by query, not by assumption**.

⇒ **HARD RULE for E4: no row is deactivated until it is FIRST identified by query against the live DB.** An id that cannot be identified is not a target.

**The stale set, by RULE (not by id list).** The rule must be **data-driven** so it survives id drift:
- **R1 (same-name active shadowing)**: two or more rows sharing `(project_id, name)` with `is_active=true` and a shared `ab_test_group`, never A/B-resolved (`skill_ab_tests.resolved_at IS NULL`). ⇒ the `regression-ab-partition-run` pair is R1's canonical instance.
- **R2 (captured-origin, never used)**: `lineage_origin='captured'` AND `is_active=false` AND `last_used_at IS NULL` — the 6 aggregate-only rows. (These are **already inactive**; R2's job is to *identify* them and confirm they are inactive, and to ensure they are never re-activated.)
- **R3 (the 3 legacy gate-runner copies)**: identified by a **content/name signature** query, never by the commission's ids.

**Ruling E — E4's real target is the 27-pair backlog (the R4 census).** The 27 multi-active (name,project_id) families are CONFIRMED, and they are not arbitrary shadows — they are **STRANDED A/B PAIRS**:
- 27 families, 54 active rows, every family EXACTLY 2 active members, 26 distinct names (test-pack-execution spans two projects).
- **26 of 27 pairs carry a LIVE unresolved `skill_ab_tests` row.** ZERO families are explained by a RESOLVED A/B (no "newer gen won, loser inactive" case exists).
- **21 of 27 are actually being SERVED to both variants** — code-fix 410 records across 2 members, dev-strategy 383, plan-creation 204, git-commit 182 — all used within ~40 h.
- 25 of 34 duplicated families have DIFFERING content; **2 have IDENTICAL content** (dev-strategy, staged-binary-boot-smoke) = harmless duplicates, must NOT be remediated by content logic.
- **Within-(name,project_id) divergence on PROD = 32**, NOT 0. The dev measurement of 0 does not transfer.
- Causal INFERENCE (label it as inference): the pairs are stranded because the evolution lane has been dark since 2026-09-01, so nothing ever ran the A/B resolution.

**E4 is reshaped from "deactivate ~10 rows" to "resolve or refuse, per pair, through the A/B resolution path":**
- The mechanism must be a **PAIR-RESOLVER**, not a flat flip: for a pair, declare the winner on `skill_ab_tests` (`resolved_at` + `winner_skill_id`) and deactivate the loser, as **ONE reversible unit** with a receipt recording the prior state of **BOTH** the test row and the skill rows.
- **NO winners are declared in Phase 1.** Choosing which variant of `code-fix` / `dev-strategy` / `git-commit` / `plan-creation` survives is a **quality decision with real consequences**; the commission's E4 (~10 rows) did not anticipate 54. Carry it as an **OPEN QUESTION for the orchestrator** (new open item below).
- **Recommended default rule (RECOMMENDATION ONLY, not an executed action):** the newer evolved generation wins (the lineage child) — the test never achieved power to reject it and the parent is superseded by construction.
- E4's acceptance (unchanged from Ruling B): the mechanism **REFUSES** an A/B-guarded row unless it is resolving the pair; the refusal is **PROVEN**; the receipt enumerates refused rows; R1 (08a4332f + fd1e01dc) is verified **STILL ACTIVE** after the run.
- R2 (6 captured) = already inactive, **NO-OP**. R3 (gate-runner copies) = **DO NOT EXIST (0 rows)** — the commission's premise is refuted, state it. `9e79ccad` = active, no A/B group, no duplicate evidence → **NOT stale, no flip**. `76e3e1a7a` = **NOT FOUND**.
- The 2 IDENTICAL-content families (identical content): no content-logic remediation — only a duplicate-row decision.
- **Timestamp caveat.** Two families' `updated_at` is stale relative to their challenger's `created_at` (e.g. plan-creation `0ab11975` updated 2026-11-09 while challenger `a4734a1c` dates 10-04). Any winner rule stated in the plan is based on **LINEAGE GENERATION** (i.e. lineage generation), not timestamps — a naive "newest `updated_at` wins" rule would pick **WRONG**.

**Mechanism — the ruling (this is the E4 decision the commission asked for).**
Options weighed: **(a)** a new `daemon/migrations/versions/*.sql` file; **(b)** a boot-time/idempotent reconciliation in code; **(c)** an operator-run DB update with a recorded receipt.

| Option | Verdict | Why |
|---|---|---|
| (a) `.sql` migration | **REJECTED — provably the wrong vehicle (P4)** | The house migration runner is a **documented no-op on PostgreSQL** (`runner.py:837-842`: *"the runner is intentionally a NO-OP for non-SQLite engines"*; PG schema evolution rides `_ensure_postgres_columns`). A `.sql` file therefore **never reaches the live PG DB**. Worse, a fresh-SQLite boot dies at `20260714_000001` (a SQLite-incompatible `ALTER TABLE … DROP CONSTRAINT IF EXISTS`) **before** any new file is reached. Both roads lead nowhere. |
| (b) boot-time reconciliation in code | **ADOPTED (as the shipped, self-healing half)** | It ships with the release and self-heals the live DB at the next boot **after** the promote; it is idempotent by construction (a rule, not a list); it writes an audit receipt. Cost: it must be ordered so it completes **before** any injection can occur (§10) and it must be safe to run on every boot. |
| (c) operator-run DB update with a receipt | **ADOPTED (as the pre-flight + reversal vehicle)** | The house precedent is exactly this shape (`scripts/remediate_pause_report_orphans.py` — dry-run by default, `--apply` to commit, prints a receipt; `scripts/migrate_agent_id.py`; `scripts/migrate_memory_to_rag.py`). It is the operator's **verification** tool and the **reversal** path. |

**Census re-confirmation of the mechanism.** The census makes the operator-script vehicle **even more clearly right**: the lever is a dual-column atomicity concern (set both `is_active` and `status` in one transaction) — a **code-level** concern, not a migration concern. A `.sql` file cannot express a transactional multi-column update that must also be reversible and idempotent across boots. The mechanism ruling stands (DS-10 / DS-24).

**⇒ E4 ships BOTH, with a clear division of labour:**
- **E4.1 — the shipped reconciliation** (code, in the release): an idempotent boot-time reconciliation that applies rules R1/R2/R3 plus `R5` (the normalize-only desync rule) and **deactivates** matching rows (`is_active=False`, `status='inactive'` — `daemon/repositories/skill/repository.py:383-396` `deactivate()` is the sanctioned writer), writing an **audit receipt** (row id, name, rule id, before/after `is_active`/`status`, timestamp). The boot pass is gated by `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` (default 0 / off; named Phase-2 lever armed alongside capture switch); when OFF, the pass is fully inert (no receipt, no row change, no refusal enumeration, deactivator not constructed). It **never hard-deletes** and **never re-activates**. It is a **no-op** when no row matches (the steady state after the first pass).
- **E4.2 — the operator script** (`scripts/audit/skill_stale_deactivate.py`): dry-run-by-default; `--apply` to execute; `--reactivate <receipt.json>` to **reverse** (the receipt carries the pre-values). It is the pre-flight verification (run before the promote) and the reversal (run after).
- **E4.3 — the injection-side defence** (the "before F can inject" guarantee): the search/injection path **excludes** rows that match R1 (same-name active shadowing in an unresolved A/B group) — so even if the reconciliation has not run yet, **nothing stale can be injected**. This is the belt; E4.1/E4.2 are the braces. (The injection path is `daemon/services/skill_injection_service.py:371-431` → `SkillSearchService.search` → the candidate selection; the exact filter site is the implementer's to choose, with a pin that proves an R1-shaped row is never injected.)
- **E4.4 — the reversal record**: every row touched is recorded (§16's evidence list).

**Idempotence + timing.** E4.1 must be idempotent (running twice changes nothing the second time) and must **complete before the first injection can occur** (§10 orders it ahead of F). E4.3 makes the timing non-critical — the guarantee holds even if E4.1 is late.

**Acceptance.**
1. The mechanism **REFUSES** an A/B-guarded row unless it is resolving the pair; the refusal is **PROVEN** (a real attempted flip → refused, with the receipt showing the refusal reason).
2. The receipt **ENUMERATES R1 as refused-by-guard** with both ids (`08a4332f`, `fd1e01dc`) and the test id (`083d5a58`).
3. R1 is **VERIFIED still active** after the run (unchanged) — 08a4332f + fd1e01dc remain `(is_active=true, status=ab_testing)`.
4. No row was deleted; every touched row has a receipt entry; the reversal path re-activates them from the receipt (proven by a round-trip pin).
5. An R1-shaped row is **not injected** even with E4.1 not run (the E4.3 defence pin).
6. R2 (6 captured) = already inactive, **NO-OP** (confirmed, not flipped). R3 (gate-runner copies) = **DO NOT EXIST (0 rows)** — the commission's premise is refuted and stated. `9e79ccad` = active, no A/B group, no duplicate evidence → **NOT stale, no flip**. `76e3e1a7a` = **NOT FOUND**.
7. The 2 IDENTICAL-content families (dev-strategy, staged-binary-boot-smoke): **no content-logic remediation** — only a duplicate-row decision.
8. The pair-resolver **handles the 27 pairs**: for a pair, declare the winner on `skill_ab_tests` (`resolved_at` + `winner_skill_id`) and deactivate the loser, as **ONE reversible unit** with a receipt recording the prior state of **BOTH** the test row and the skill rows. **NO winners are declared in Phase 1** — the winner decision is an open item for the orchestrator. C-6 is **ANSWERED** (27 families / 54 rows / 32 divergence); the remaining open item is the **WINNER DECISION**.
9. The mechanism never sits inside the `_ensure_postgres_columns` one-transaction ensure list (critical note 48a7f77f: any entry's failure aborts boot) — it runs as a **separate, idempotent boot step** at the resolved site `daemon/manager.py:2786` (after `_wire_skill_injection_capability_gate()`, strictly after `_ensure_postgres_columns()` at `:1034` and after `run_pending_migrations()` at `:1017`). (E-7 resolved and answered).
10. The boot pass is gated by `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` (default 0 / off; absent=off): when OFF, the pass is fully inert (no receipt, no row change, no refusal enumeration, deactivator not constructed); when ON (1/true/yes/on), it executes; invalid env value renders the pass inert and boot continues (fail-soft; M-E4k).

**Pins + mutation legs (registered pack `skill_stale_deactivation_unit_test`, NEW).**
- **M-E4a**: make the reconciliation a no-op → RED (the **refusal-receipt discriminator** pin — it asserts the receipt shows R1 REFUSED-BY-GUARD and still ACTIVE, not a deactivation).
- **M-E4b**: make it **delete** instead of deactivate → RED (the no-hard-delete pin).
- **M-E4c**: make it re-activate a row it deactivated → RED (the reversal pin).
- **M-E4d**: remove the E4.3 injection filter → RED (the "stale cannot be injected" pin).
- **M-E4e**: make the script's `--apply` path available without a dry-run → RED (the dry-run-default pin).
- **M-E4f**: drop a row from the receipt → RED (the receipt-completeness pin).
- **M-E4g**: make the reconciliation run twice and change state the second time → RED (the idempotence pin).
- **M-E4k**: flip the resolver default to ON → RED (the inert-by-default brake pin; assert no receipt and deactivator not constructed when flag absent).

---

## 10. Workstream F — INJECTION GATE (P12)

**The gate.** `skill_injection` is read from `agents/<name>/meta.json` with a **fail-closed default**:
`daemon/registry.py:690` and `:756` — `skill_injection=meta.get("skill_injection", False)`. Absent ⇒ `False`.

**What the flag actually gates (precision, from facts-extract A2 + the code).**
- **Automatic capture**: Gate 3 of the capture eligibility check — `daemon/services/skill_metrics_service.py:610-640` (`skill_injection_enabled` resolved via the manager's `agent_id_resolver`, then `if not skill_injection_enabled: return None`).
- **The manual capture tool** (`skill_execute_capture`, `daemon/tools/skill_evolution_tools.py:392-500`) has **NO** `skill_injection` check — only the kill-switch at `:442`.
- **Dynamic injection (the auto-inject lane)** IS gated by the flag: `daemon/services/instance_messaging.py:4311` (`if agent_meta and getattr(agent_meta, "skill_injection", False):`) and `daemon/services/context_messages.py:2536` (`skills_enabled_only = bool(getattr(agent_meta, "skill_injection", False))`). ⇒ **`skill_injection: false` DOES prevent dynamic injection** — the council corrected this plan's earlier claim, which was wrong.
- **Project-file / auto-load skills** (the `auto_load` path, `skill_injection_service.py`'s `_build_auto_load_block`) are gated by `auto_load` on the skill row and by `skill_injection` at the call site's `is_completion_report` check — a separate axis. The P13 observation (an inactive skill served to a live `coder` agent on 2026-11-09) is therefore **not** explained by `skill_injection: false` gating injection; it is a separate **selection defect** (routed residual, §17.21 / E4).
- **Injection itself** is gated at the **call site** in `instance_messaging` (per `skill_injection_service.py:58-66`).

⇒ **F's scope must state this precisely**: wiring `skill_injection: true` **enables capture AND dynamic injection** for an agent; the "capture only fires on skill_injection=true agents" premise is true **only for the automatic path** (the manual tool is kill-switch-gated, not flag-gated).

**The roster — recomputed from `origin/latest` (P1).** 25 declaring agents: **15 true** (aegis, approver, approver[v2], architect, critic, doc-maintainer, maintenancer, planner, planner[v2], reviewer, reviewer[v2], sketcher, tidier, tidier[v2], worker) / **10 false** (blueprinter, coder, designer, developer, developer[v2], devops, project-manager, tester, wanderer, watcher). 21 of 46 agents declare nothing ⇒ `False` ⇒ **31 of 46 agents cannot capture**.

**The F decision.** Wiring `skill_injection: true` for MORE agents is a **behavioral** decision with a blast radius (each `true` agent becomes capture-eligible the moment Phase 2 flips the capture switch). Phase 1 therefore:
- **recomputes** the roster from `origin/latest` (done; §10 above) and records it;
- **does NOT flip any agent to `true` in Phase 1** — the roster change is a **staged payload** that ships at promote and is **armed only when the capture switch is flipped** in Phase 2. ⇒ The payload must be **conditional on the capture lane being ON**, not unconditional.
- **must** fix the roster drift that made the census stale: `blueprinter` and `tester` flipped to `false` and `aegis` was added `true` — none of which was communicated to the skill lane. F ships a **roster manifest** (the authoritative list, in the planning dir) so the next census is a diff against a known state, not a re-derivation.
- **must** reconcile the `skill_injection` flag's SEMANTICS: because the flag gates capture (and, at the call site, injection), a `true` on an agent is a **capture-enablement** decision. The manifest must say, per agent, *"capture-enabled"* and separately *"injection-enabled"* — so the Emperor's Phase-2 decision is per-axis.

**E4 before F — the ordering guarantee.** E4 (stale retirement + the E4.3 injection defence) lands **before** F's payload. Concretely: F's staged payload must not arm an agent's capture until E4.1 has run once in the target environment, and E4.3 makes even that unnecessary for the *injection* safety property. §12's dependency graph states this as a hard edge.

**Injection budget verification (the commission's explicit requirement).** `max_inject_skills` defaults to **2** (`daemon/services/skill_injection_service.py:413`: `getattr(self._config, "max_inject_skills", 2)`), and the search returns `{"injected": [...], "low_match": [...]}` (`:433-434`). F must **prove with real content** that:
1. the injected count never exceeds `max_inject_skills` (2);
2. the `low_match` list is present and bounded;
3. the rendered `[System Inject]` block (`:1201` `_format_injection`) is **honest** — a skill that was NOT injected is never rendered as injected;
4. with E4.3's filter active, no R1-shaped row appears in `injected` or `low_match`.

**Identity dedupe at the injection boundary (Ruling F).** The 27 stranded A/B pairs (Ruling E) mean two divergent variants of `code-fix` / `dev-strategy` / `git-commit` / `plan-creation` can both be in the candidate pool. **Shipping injection over an ambiguous pool without determinism would make the reported "flaky / misreading instructions" symptom WORSE** — two divergent variants of `code-fix` must never both reach the same agent in one injection.
- The injection path must serve **at most ONE variant per `(name, project_id)`** to a given agent.
- **Deterministic pick rule (stated explicitly, reproducible rather than arbitrary): highest generation (newest lineage child), ties broken by id.**
- **Pin.** Construct an agent whose resolved skill set contains **BOTH** variants of one identity; assert only **ONE** is injected, and assert **WHICH** one. Mutation leg: remove the dedupe → both appear → RED.
- **Hypothesis (flagged, not proven):** the 21 pairs currently served to both variants are a plausible contributor to the "flaky / misreading instructions" symptom. This is a **hypothesis**, not a proven cause — do not report it as a finding.

**Acceptance.** The recomputed roster is published in the manifest; no `meta.json` `skill_injection` value is changed in Phase 1 (the payload is staged, not applied); the injection budget holds against **real** skill content (the pack must use the live-shaped corpus, not synthetic one-word skills); E4.3's filter is proven; **the identity-dedupe pin is proven** (one variant per identity, deterministic pick, mutation leg RED).

**Pins + mutation legs (registered pack `context_skills_unit_test` — the EXISTING registered pack, extended; plus `skill_injection_budget_unit_test`, NEW).**
- **M-F1**: raise `max_inject_skills` to 3 → RED (the budget pin).
- **M-F2**: drop the `low_match` bound → RED.
- **M-F3**: remove the E4.3 filter → RED (an R1-shaped row is injected).
- **M-F4**: change the roster manifest without re-deriving it → RED (the manifest-diff pin).
- **M-F5**: make the staged payload apply unconditionally (not gated on the capture lane) → RED.
- **M-F6**: remove the identity dedupe → RED (both variants of one identity are injected).

---

## 11. Workstream G — FLAKINESS STABILIZATION (mandatory)

**Why it is in scope.** The PM ledger item is titled *"re-enable skill capture, fix flakiness, stabilize metrics"* — flakiness is ledger scope, and the base-leg experiment has **proven** the mechanism, so this is concrete work.

**The node (and its sibling).** Both nodes of the pair are on the named quarantine list and both are in scope for G:
- `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met`
- `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_force_resolve` (QUARANTINE.md **row 56** — the sibling)

**Measured flake rates (the authority for G's acceptance — context matters):**
- **15% ISOLATED** (7 of 46 runs)
- **38% IN FULL-FILE-SET CONTEXT** (3 of 8 runs)

⇒ The full-file-set context is **2.5× more likely to fire**. G's acceptance is therefore stated in the **full-file-set** context (§Acceptance) — an isolated green would have passed at 15% **by luck**.

**The proven root cause.**
1. The test engine is `create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)` — `tests/services/conftest.py:42-46` (verified) — **ONE shared DBAPI connection for all threads**.
2. `check_ab_test_resolution` fans its resolve-path side effects out **concurrently** via `asyncio.gather(asyncio.to_thread(self._skill_repo.deactivate, loser_id), asyncio.to_thread(self._ab_test_repo.resolve, …), asyncio.to_thread(self._skill_repo.update, winner_id, …))` — `daemon/services/skill_evolution_service.py:1448-1457` (Path 2, threshold met) and `:1474-1483` (Path 3, force-resolve) (verified).
3. ⇒ one session's COMMIT/ROLLBACK interleaves with another's transaction **on that single connection** ⇒ `SkillRepository.update`'s `session.get(Skill, skill_id)` returns `None` ⇒ `daemon/repositories/skill/repository.py:331-336` logs *"Skill not found for update"* and returns `None` ⇒ **the loser is never deactivated** ⇒ `assert old_after.is_active is False` fails.
4. **Decisive experiment** (same tree, same interpreter 3.14.7, same command; only the thread hop differs): normal (`asyncio.to_thread` → thread pool) = **11 passed / 1 failed**; `asyncio.to_thread` monkey-patched inline = **12 passed / 0 failed**. ⇒ the thread hop over a shared connection is the mechanism, and the failure is a **test-infrastructure** defect, not a production defect.

**The quarantine rows this touches (quoted from `.agents/tester/QUARANTINE.md`):**

| Row | Node | Current text (verbatim, trimmed) | Status |
|---|---|---|---|
| **45** (2026-09-23) | the same node (as item 2 of a 3-node flake set) | *"xdist/class-level shared-SQLite fixture pollution (`sqlite3.InterfaceError: bad parameter or other API misuse`; both `threshold_met` and `force_resolve` fail together in full-class runs, pass solo) … **Durable fix = fixture isolation for `TestCheckABTestResolution`**"* | QUARANTINED (flake-family; sweep-visible; **test-side isolation follow-ups recommended**) |
| **56** (2026-08-30) | `…::test_ab_resolution_force_resolve` | *"**Flaky**: `sqlalchemy.orm.exc.StaleDataError: UPDATE statement on table 'skills' expected to update 1 row(s); 0 were matched` — non-deterministic shared-state/timing in A/B force-resolve path …"* | QUARANTINED (flaky, family-level entry) |
| **90** (2026-08-30) | `…::test_ab_resolution_threshold_met` (`tests/services/test_skill_evolution_service.py:1875`, pack `skill_services_gzip_regression_unit_test`) | *"Pre-existing red at clean base `459557b2`: `AssertionError: assert True is False` @ :1884 (old-skill `is_active` not deactivated by threshold-met path) … `sqlalchemy.exc.InvalidRequestError: Could not refresh instance '<Skill …>'` @ `daemon/repositories/skill/repository.py:351` (same InvalidRequestError class as the 2026-08-29 dependency_bus StaticPool row — timing-sensitive cross-thread session shape) …"* | QUARANTINED (pre-existing, base-evidenced; **signature-drift follow-up open**) |

**The named quarantine list (ONE list — both legs are checked against it).**
- node: `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met`
- rate: **15% ISOLATED** (7/46 runs); **38% IN FULL-FILE-SET CONTEXT** (3/8 runs)
- sibling: `test_ab_resolution_force_resolve` (QUARANTINE.md **row 56**) — **also on the list**
- mechanism: StaticPool single shared connection → concurrent `asyncio.to_thread` fan-out (`skill_evolution_service.py:1449-1451`, `:1475-1476`) → `session.get` returns `None` → `repository.py:333` *"Skill not found for update"* → the loser is never deactivated
- decisive experiment (**verbatim in the pack**): normal **11P/1F** vs inline-monkey-patch **12P/0F** — same tree, same interpreter 3.14.7, same command, **ONLY the thread hop differs**
- ⇒ this **confirms AND NOW EXPLAINS** pre-existing QUARANTINE.md rows **45, 56, 90** (their prior diagnosis was hypothesis-only)

⇒ **Ruling: the fix should let all three rows be REMOVED from QUARANTINE.md.** Rows 45 and 56 are the same mechanism (shared-SQLite fixture / cross-thread session shape); row 90 is the same node with a **signature drift** (its own text says *"signature-drift follow-up open"*). The measured evidence has now converted the hypothesis into an **explained mechanism**, which is exactly what the quarantine rows were waiting for. **Desired outcome (stated explicitly):** after the fix, all three rows are re-adjudicated and **removed** (or, if any row's node still flakes for a *different* reason, that row is re-scoped with the new evidence — never silently kept).

**The fix shape — fixture isolation.** The durable fix named by row 45 is *"fixture isolation for `TestCheckABTestResolution`"*. Concretely: give the concurrent fan-out **its own session/connection** instead of sharing the StaticPool one. Two acceptable implementations:
- **(i) fixture-level**: the `engine` fixture (`tests/services/conftest.py:39-46`) provides a **non-shared** pool for the tests that need concurrency — e.g. `poolclass=QueuePool` with `pool_size` ≥ the fan-out width, or `NullPool` with a **file-backed** SQLite (so each connection sees the same DB) instead of `:memory:`. *Caveats:* (i) `:memory:` + `NullPool` gives each connection a **different** database — the DB must be file-backed (or a shared-cache URI) for the isolation to preserve the data; (ii) a **file-backed** SQLite serving three concurrent WRITE transactions (`deactivate` + `resolve` + `update`) needs **`PRAGMA journal_mode=WAL` + `PRAGMA busy_timeout`** — the fixture sets only `PRAGMA foreign_keys=ON` (`tests/services/conftest.py:32-36`), so without those pragmas the isolation fix can trade the old flake for `database is locked`. State both pragmas, or accept option (ii).
- **(ii) seam-level**: the production fan-out at `skill_evolution_service.py:1448-1457` / `:1474-1483` obtains a **separate session per operation** rather than sharing one. ⚠ **This is a production change** — it must be done ONLY if (i) is impossible, and if done it must **not** serialize the fan-out (a lock would **mask** the defect, not fix it — see the E1 coupling rule).

**Preferred: (i) fixture-level** — the defect is in the test harness; the production code's concurrency is correct against a real (PostgreSQL) deployment where each connection is a distinct session.

**Acceptance (the commission's explicit criteria).**
1. **The node is green in ≥ 3 consecutive FULL-FILE-SET runs, not merely in isolation.** ⚠ The flake rate is **context-dependent** — 15% isolated vs **38% in the full-file-set run** — so an isolated green would have passed **by luck** at 15%. The **full-file-set** context is the acceptance context. The sibling `test_ab_resolution_force_resolve` (row 56) must be green in the same runs.
2. The **normal-vs-monkey-patch experiment no longer differs**: the normal run and the inline-monkey-patched run produce the **same** result set (both fully green), so the thread hop is no longer the discriminator.
3. The full-class run (both nodes together) is green — the class-level pollution (row 45) is gone.
4. QUARANTINE.md rows 45 / 56 / 90 are re-adjudicated and **removed** (or re-scoped with new evidence — never silently kept).
5. The acceptance runs are classified with the **pinned classifier** (`grep '^FAILED'` only — §13.2) so the acceptance evidence itself cannot be corrupted by the duplicate-line artifact.

**Pins + mutation legs (registered pack `skill_evolution_unit_test` — the EXISTING registered pack, extended).**
- **M-G1**: revert the fixture to `StaticPool` + `:memory:` → RED (the isolation pin must catch the shared-connection shape).
- **M-G2**: revert to a shared `:memory:` with `NullPool` (each connection gets its own DB) → RED (the "the DB must be shared" pin).
- **M-G3**: serialize the fan-out with a lock (the masking anti-pattern) → RED (the "no masking" pin — a serialized fan-out hides the race).
- **M-G4**: delete the "≥ 3 consecutive FULL-FILE-SET greens" assertion helper → RED (the acceptance-context pin — an isolated-only assertion must not satisfy it).
- **M-G5**: narrow the acceptance helper to a single isolated run → RED (the context pin — the helper must refuse an isolated-only proof).

**Sequencing with E1 (the addendum's requirement), stated once more:**
**G BEFORE E1.** G removes a shared-connection transaction hazard in the file E1 touches; E1's guard is a **count over committed job rows** and is therefore **immune** to the hazard by construction (it never touches the skill tables and never shares the A/B transaction). The tester's gate must run the AB-resolution node **with E1's guard present** to prove no interference.

---

## 12. Dependency order & sequencing

```
                       ┌───────────────────────────────────────────┐
   G (flakiness) ──────┤ FIRST: removes the shared-connection       │
   (fixture isolation) │ hazard in the file E1 will touch           │
                       └───────────────────┬───────────────────────┘
                                           │
   E4.1/E4.2/E4.3 ─────► E3 (display hygiene) ─► E1 (burst guard) ─┐│
   (stale retirement)    (same file, no overlap with E1's seam)      ││
                                                               ┌───▼▼▼┐
   B1 (proof) ─► B2 (inline gate) ─► B3 (read scope)          │ E1   │
   (independent; B2 closes a live hole, so it is early)        └───┬──┘
                                                                   │
   A1 ─► A2 ─► A3 ─► A4 (metrics honesty, sequential: each pin  ┌──▼───┐
   depends on the previous being true)                          │ A4   │
                                                             ───┴─────┘
   C (verify-only, parallel with everything)
   D (observability, parallel; D's auto-anomaly reuses E1's signal path)
                                                                   │
                       E4 (stale retirement) MUST precede F ──────┤
                       (F's payload must not arm an agent before  │
                        the stale set is retired)                  │
                                                                   ▼
                                    ┌──────────────────────────────┐
                                    │ tester functional gate        │
                                    │ (base-leg protocol, §13)       │
                                    └──────────────┬───────────────┘
                                                   ▼
                                    ┌──────────────────────────────┐
                                    │ code council (3×)              │
                                    └──────────────┬───────────────┘
                                                   ▼
                                    ┌──────────────────────────────┐
                                    │ STAGED release (no promote)   │
                                    └──────────────────────────────┘
```

**Hard edges.**
- **E4 before F** (commission-mandated).
- **G before E1** (same file, different seams — §E1's coupling rule).
- **A1 → A2 → A3 → A4** (each metric's acceptance is stated against the previous being true; A2's pin asserts the census verdict, A3's asserts the gate, A4's asserts the counter).
- **B2 before the Phase-2 runbook** (the runbook's "evolution lane ON" step depends on the inline path being gated).
- **C, D** may run in parallel with everything (C is read-only; D adds observability).

---

## 13. The gate chain and the base-leg protocol

### 13.1 The chain (house standard)

`plan gates → code council → tester functional gate → staged verification`

| Gate | Owner | Scope | Exit criterion |
|---|---|---|---|
| **Gate 0 — plan gate** | planner/leader | this document + `decisions.md` | approved; every ruling has a recorded alternative; every open item is marked CONFIGURE-ONCE / to-confirm |
| **Gate 1 — workstream plan gates** | per-workstream reviewer | each workstream's own acceptance + pins | the workstream's pins are RED→GREEN proven with their mutation legs, in a REGISTERED pack |
| **Gate 2 — code council (3 rounds)** | council | the full diff | no blocking finding; every deviation + routed residual is DISCLOSED in the plan |
| **Gate 3 — tester functional gate** | tester | §13.2's protocol | **net-new reds = 0**; every mutation leg RED→GREEN; every new pin registered in `PACKS.md` |
| **Gate 4 — staged verification** | tester + leader | the staged artifact | the staged artifact passes the §14 gate items; **no promote** |

### 13.2 THE NET-NEW-REDS PROTOCOL (verbatim — this supersedes the commission's and Addendum 1's looser wording)

> **BASE LEG.** `/home/nea/ensemble-src-wt-skill-revival-base1` — detached at `ae5bbafedde90c7af1065b9660196bbffbbbf9c1` (== the Phase-1 base sha == `origin/latest`), own venv, **CPython 3.14.7** (matches live v0.29.5/6/7 = cpython-314; the canonical checkout's 3.13 venv is a stale Aug-24 outlier and is **not** used), own fenced `.env` (mode 600, `POSTGRES_DB=ensemble_dev`), status clean. Read-only reference — **it must stay clean; a dirty base is a broken comparison.**

> **THE BASE LEG IS NOT STABLE.** Observed per run (raw capture, 3 consecutive full runs):
> - run 1: **4 failed / 455 passed** (32.87 s) — the AB node RED
> - run 2: **3 failed / 456 passed** (27.03 s) — the AB node green
> - run 3: **4 failed / 455 passed** (28.13 s) — the AB node RED
>
> A single-run base measurement yields a 3-red OR a 4-red base set **purely by chance**. The earlier "3× stable at 3/456" reading came from a batch where the flake happened not to fire. This is a **stronger** argument for the ≥3-run set-diff protocol than the earlier framing: the protocol is mandatory **because** the base is unstable, not despite it.

> **THE PROTOCOL IN ONE LINE (the §19.1 contract string):** classify with `grep '^FAILED'` only; the TRUE base red set is the **intersection of the three red sets** across >=3 runs; NET-NEW MUST BE 0.
>
> **THE TRUE BASE RED SET = the 3 reds that appear in EVERY run (×3 each)** — the intersection of the three red sets:
> `FAILED tests/manager/test_skill_service_init.py::test_skill_services_have_correct_dependencies` ·
> `FAILED tests/manager/test_skill_service_init.py::test_skill_services_initialized_when_config_present` ·
> `FAILED tests/manager/test_skill_service_init.py::test_skill_services_none_when_skill_evolution_disabled`
> Cause (all three, one): the pre-existing SQLite migration defect `20260714_000001` — `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS` is invalid SQLite and the runner is SQLite-only so PostgreSQL never hits it. **NOT Phase-1.**

> **⛔ CRITICAL — THE RED-LIST CLASSIFIER.** There is a **second artifact defect** that would corrupt the net-new-reds math if the tester copies the wrong grep: pytest `--tb=short` prints the same migration-failure line in **BOTH** the traceback and the summary, so it appears **3× per run (×9 across 3 runs)**. A naive `grep -E '^(FAILED|ERROR)'` over raw pytest output counts it as a **4th red**.
>
> **CLASSIFIER RULE (pinned house critical note — the tester MUST NOT write its own classifier):**
> ```
> grep '^FAILED'   # ONLY. Never '^(FAILED|ERROR)'. Never raw-count.
> ```
> This rule goes in the tester-gate section **verbatim**; the tester inherits the classifier, it does not author it.

> **QUARANTINE CLASSIFICATION (separate from the base set).** The AB-resolution node (`tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met`) is **EXCLUDED from the base red set** and **recorded with its flake signature**, so that a future head-leg red on it is **CLASSIFIED, not counted as net-new**. Both legs are checked against the **ONE** named quarantine list below.

> **THE RULE (verbatim — superseding the commission's and Addendum 1's looser wording):**
> `NET-NEW = (head red set, classified with \`grep '^FAILED'\`, ≥3 runs, diffed as SETS) MINUS (base red set of 3) MINUS (named quarantine list).`
> **NET-NEW MUST BE 0. Base-proven debt is DISCLOSED, never hidden.**

**Named quarantine list (ONE list — both legs checked against it).**
- node: `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_threshold_met`
- rate: **15% ISOLATED** (7/46 runs); **38% IN FULL-FILE-SET CONTEXT** (3/8 runs)
- sibling: `test_ab_resolution_force_resolve` (QUARANTINE.md **row 56**) — **also on the list**
- mechanism: StaticPool single shared connection → concurrent `asyncio.to_thread` fan-out (`skill_evolution_service.py:1449-1451`, `:1475-1476`) → `session.get` returns `None` → `repository.py:333` *"Skill not found for update"* → the loser is never deactivated
- decisive experiment (**verbatim in the pack**): normal **11P/1F** vs inline-monkey-patch **12P/0F** — same tree, same interpreter 3.14.7, same command, **ONLY the thread hop differs**
- ⇒ this **confirms AND NOW EXPLAINS** pre-existing QUARANTINE.md rows **45, 56, 90** (their prior diagnosis was hypothesis-only)
- At minimum also on the list: QUARANTINE.md rows **89, 76, 74, 82** (the `20260714_000001` SQLite-migration family — the same family as the 3 base reds). *After G lands, rows 45/56/90 are re-adjudicated and removed* (§11) — the list is re-versioned at that moment, and the removal is recorded in `PACKS.md`/the tester evidence.
- **ADDITION (2026-11-09, workstream A / round-3 implementation evidence) — a SECOND StaticPool flake node:**
  - node: `tests/integration/test_skill_evolution_e2e.py::TestSkillABTesting::test_ab_test_resolves_when_threshold_met`
  - mechanism: **that file's OWN engine fixture** at `tests/integration/test_skill_evolution_e2e.py:91-102` — `create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)` — i.e. **exactly the class G fixed in `tests/services/conftest.py`** (DS-29: one shared DBAPI connection + the concurrent `asyncio.to_thread` resolve fan-out → `session.get` returns `None` → the loser is never deactivated). G's fix was applied to `tests/services/conftest.py` **only**; this file carries its own fixture and was never in G's scope.
  - evidence: **isolated base 3-failed / 10 runs vs head 1-failed / 10 runs** (same command, same interpreter 3.14.7, same fence) — the **BASE rate is HIGHER than the head rate**, so the node is **pre-existing** and is NOT net-new for this lane. Failing assertion: `assert loser.is_active is False` (`assert True is False`).
  - disposition: **QUARANTINE CANDIDATE** — classify it, never count it as net-new. The formal `.agents/tester/QUARANTINE.md` row is the **TESTER's** call (that file is the tester lane's, not this lane's); this entry records the classification + evidence so it is not silently dropped.
  - what would remove it: applying G's file-backed / non-shared-pool fixture to `tests/integration/test_skill_evolution_e2e.py` (a test-harness fix, not a production change).

**Evidence pack (durable, committed — the tester lane inherits it; do NOT re-create or duplicate it).**
- Primary (raw of record, outside the repo): `/home/nea/skill-revival-base-evidence-20261109-0813Z/`
- Committed copy (in the **feature** worktree — never the base worktree): `.agents/tester/RESULTS/2026-11-09-skill-revival-base-evidence/`
- **Rationale for the dual location:** this project has **already lost evidence to `/tmp` sweeps** — the Oct-22 keeper transcripts were swept (0 rows in instances/checkpoints/messages/tasks; `/tmp/skillfix` gone). Evidence living only outside the repo can be swept again. The copy in the feature worktree is the durable record.
- The pack also contains the **interpreter provenance** (live binaries, venvs, repo pins, the manifest gap) and the 3.13-vs-3.14 differential leg, plus the two `grep` commands for the staged artifact's interpreter (§14.2.9), ready to run.
- The base worktree (`/home/nea/ensemble-src-wt-skill-revival-base1`) **must stay clean** — it is the comparison reference; a dirty base is a broken comparison. The evidence copy goes in the **feature** worktree only.



**Named quarantine list (head-leg classification list).** At minimum: QUARANTINE.md rows **45** (the AB node, item 2), **56** (`test_ab_resolution_force_resolve`), **90** (the same node under `skill_services_gzip_regression_unit_test`), **89** + **76** + **74** + **82** (the `20260714_000001` SQLite-migration family — the same family as the 3 base reds). *After G lands, rows 45/56/90 are re-adjudicated and removed* (§11) — the list is re-versioned at that moment, and the removal is recorded in `PACKS.md`/the tester evidence.

### 13.3 The pack registry additions (every pin lands in a REGISTERED pack)

| Pack | Status | Covers |
|---|---|---|
| `metrics_capture_active_wiring_unit_test` | NEW | A1 |
| `metrics_census_polarity_unit_test` | NEW | A2 |
| `skill_counter_drift_unit_test` | NEW | A4 |
| `skill_keeper_confinement_behavioral_unit_test` | NEW | B1 |
| `skill_id_verbatim_contract` | **EXISTING — extend** (PACKS.md:867 — **VERIFIED 2026-11-09** that it runs `tests/unit/test_skill_evolution_lane_gate.py`: `LANE_GATE_FILE` in phase 1 + 4 mutation legs, wired by commit `c26b8ea10`; the approver's "no pack runs it" note is **STALE**) — B2's inline-gate pin goes here as `tests/unit/test_skill_evolution_lane_gate.py` | B2 |
| `skill_keeper_read_scope_unit_test` | NEW | B3 |
| `skill_capture_observability_unit_test` | NEW | D |
| `skill_evolution_unit_test` | **EXISTING — extend** (PACKS.md:3702) — E1's burst-guard pins go here as `tests/unit/test_skill_fix_burst_guard.py` | E1 |
| `skill_capture_killswitch_unit_test` | **EXISTING — extend** (PACKS.md:2993) — A3's attribution-seam pins (`tests/unit/test_skill_capture_attribution_seam.py`) and E2's dispatch pins (`tests/unit/test_skill_evolution_type_dispatch.py`) go here | A3, E2 |
| `skill_tools_display_hygiene_unit_test` | NEW | E3 |
| `skill_stale_deactivation_unit_test` | NEW | E4 |
| `skill_id_verbatim_contract` | **EXISTING — extend** | E3.1–E3.4 |
| `context_skills_unit_test` | **EXISTING — extend** | F |
| `skill_evolution_unit_test` | **EXISTING — extend** | G |
| `skill_services_gzip_regression_unit_test` | **EXISTING — extend** (the row-90 node) | G |

Each registration follows the house shape (name · invocation command · description · mutation matrix · date · verdict), and every mutation leg is **proven RED→GREEN with a sha-verified restore** — the `skill_id_verbatim_contract` pack's phase-2 pattern is the template (`PACKS.md:900`).

⚠ **Implementer note (pack count drift).** The EXISTING registered packs carry an `EXPECTED test count` comment in their pack scripts (e.g. `skill_capture_killswitch_unit_test`, `skill_evolution_unit_test`). When the new pins land inside those packs, the implementer **must update the expected count** in the pack script and in the `PACKS.md` row — otherwise the pack fails on its own count assertion. There is **ONE destination per pin** (see the table above): A3's pins → `skill_capture_killswitch_unit_test`; E1's → `skill_evolution_unit_test`; E2's → `skill_capture_killswitch_unit_test`.

---

## 14. The staged release plan (NO PROMOTE)

### 14.1 Version strategy

- The next free slot after the live `v0.29.7` is **`v0.29.8`** (the staged `v0.29.5`/`v0.29.7` lanes are already tagged; the project metadata's `next_free_version_slot` after the v0.29.0 ceremony reads `v0.29.1`, superseded by later lanes — **the implementer re-derives the next free slot at staging time and records it**).
- **Release discipline:** the release contains the code for A, B, D, E, F, G. It contains **no** switch flip (the env vars stay unset in the staged tree) and **no** queue-pause write.

### 14.2 Staging steps

1. **Cut** the release tree from the feature worktree HEAD (the dispatcher's caller arranges the commit point; this lane does not commit).
2. **Build** the artifact (`ensemble-prod`) with the scripts-pin toolchain (`stage.sh:215` — `uv run python -m PyInstaller ensemble.spec`; the existing-binary path is `stage.sh:169`; the binary provenance sidecar is written by `_provenance_write` at `stage.sh:227` / `scripts/upgrade/lib.sh:3225`).
3. **Stage** via `scripts/upgrade/stage.sh` — producing `releases/<version>/` + `manifest.json` (the heredoc at `stage.sh:585-604`) + the `<artifact>.build-provenance.json` sidecar. The exact-tag guard is `stage.sh:146-150` (exit 78 when `VERSION` != the exact tag at HEAD).
   **⚠ THE STAGING COMMAND MUST CARRY `ENSEMBLE_ROLLBACK_SAFE=1` EXPLICITLY** (the derivation at `stage.sh:325-351` is **full-history**, not delta-scoped: it trips on destructive DDL applied *before* the previous release tag, and **refuses with exit 78** at `stage.sh:344` when the switch is unset). The one-sentence rationale: **the migration delta between the previous release tag and this version is EMPTY** (Phase 1 adds NO `.sql` file — §E4/P4), so the destructive DDL the full-history grep found was already applied before this version and this release introduces no schema drift. ⇒ the explicit `=1` is legitimate **because the assertion is about the migration DELTA, not about the code**.
   ```
   ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh live --version v0.29.8 --allow-stale-stage
   ⚠ **The TARGET TOKEN `live` is a POSITIONAL and must come FIRST** (the `stage.sh` arg parser at `stage.sh:91-125` accepts only `demo|live|sandbox` positional + `--version <v>` + `--skip-build` + `--allow-stale-stage` + `-h|--help`; **any other token → `*)` → exit 78**; **no target token → defaults to demo**). `--version v0.29.8` (not a bare `v0.29.8` positional — that hits the same `*)` → exit 78). `ENSEMBLE_UPGRADE_LIVE=1` is the **live guard** (`require_live_guard`, `scripts/upgrade/lib.sh:1363-1376`) required for live staging and absent from earlier revisions.
   ```
4. **4-way sha — with the INDEPENDENCE requirement stated (the council's F-20).** `release dir == dist == manifest == sidecar` must agree on the binary sha256. The manifest's `binary_sha256` is computed **FROM THE COPY** (`stage.sh:437` — `BIN_SHA="$(_sha256 "$STAGE_TMP/ensemble-prod")"`), so the manifest is **not** an independent witness of the copy — the **independent witness is the build-time provenance sidecar** (`_provenance_write` at `stage.sh:227` / `scripts/upgrade/lib.sh:3225`), which hashed the **source** artifact before the copy. ⇒ The tester **recomputes** the sha of the staged copy **independently** (`sha256sum releases/<version>/ensemble-prod`) and asserts it equals BOTH the manifest value and the sidecar's recorded value. `binary_version` is the tag minus `v` (`stage.sh:361` — `BINARY_VERSION="${ENSEMBLE_BINARY_VERSION:-${VERSION#v}}"`).
5. **Migration census — METHOD + OWNER stated (the council's F-19; BLOCKER 3 correction).** The census is **TESTER-OWNED**, and its **PRIMARY method is the BINARY-TOC read**: `CArchiveReader` TOC on the frozen `ensemble-prod` + per-entry extract + content sha, filtered to the `daemon/migrations/versions/**` entries, with full `YYYYMMDD_HHMMSS` prefixes and `uniq -d` empty. **Raw `grep -a` is BLIND on PyInstaller 6.19.0 builds — do not use it.** The staged payload contains ONLY `ensemble-prod`, `agents/`, `frontend/dist/frontend/browser/`, `plugins/`, `config.yaml`, `launcher.sh` (`stage.sh:395-425`) — there is **NO `daemon/` tree in the payload**, so a `diff -r` against `<staged>/daemon/migrations/versions` is invalid (exits 2). The **SOURCE-TREE census** (count `.sql`, full-prefix `uniq -d` empty) runs against the **STAGE WORKTREE**, not the payload. The **cheap secondary** (valid, kept): `git diff --name-only <previous-tag>..HEAD -- daemon/migrations/versions/` → **EMPTY** (the migration DELTA is empty — the same fact that legitimises the `ENSEMBLE_ROLLBACK_SAFE=1` in step 3). **E4 adds NO `.sql` file** (P4/§E4). The manifest's **`known_schema_gen`** field is a corroborating signal.
6. **PROMPT-BASELINE — a TESTER-OWNED, BOTH-TREE 181/181 GATE.** `stage.sh`'s own prompt-artifact drift leg (`stage.sh:671-716`) is **NON-BLOCKING BY DESIGN** ("drift is VISIBLE at stage — logged + journaled — and NEVER gates staging"), so the plan must NOT rely on it. ⇒ The **tester** runs the generator against **BOTH** trees and asserts **181/181** on each: (a) the **stage worktree** (`agents/<name>/*.md` vs `agents/.prompt-artifacts.sha256`) and (b) the **release payload** (`releases/<version>/agents/<name>/*.md` vs the baseline shipped inside the payload). ⚠ **F touches `agents/<name>/meta.json`, NOT the prompt artifacts** ⇒ the prompt baseline is unaffected. (If any workstream is found to touch a prompt artifact, the baseline must be re-stamped and disclosed.)
7. **FE provenance sidecar — present from SOURCE, ABSENT from PAYLOAD.** (a) **Assert the FE sidecar EXISTS** in the **source** tree (written by `_build_frontend.sh` → `_provenance_write`); (b) **assert its ABSENCE from the release payload is CORRECT** — `stage.sh:418` explicitly removes `frontend/dist/frontend/browser/index.html.build-provenance.json` from the staged payload; (c) **DISCLOSE the `build_tool` deviation**: `_build_frontend.sh:85` runs `npm run build`, while `_build_frontend.sh:105` stamps the **hardcoded literal** `"npm run build:frontend"` — and **`package.json` has no such script**. ⇒ **DO NOT gate on `build_tool == "npm run build"`** — the stamp is a known-deviating literal; record the deviation, do not gate on it.
8. **Payload source-less**: the staged payload must carry no absolute source paths (the release is relocatable).
9. **rollback_safe adjudication**: the release is **additive + fix-loss-only** — no schema change, no data-shape change, no irreversible mutation. ⇒ `rollback_safe=true`, source `explicit` (the `v0.29.0` adjudication shape) — carried by the explicit `ENSEMBLE_ROLLBACK_SAFE=1` in step 3.
10. **Interpreter disclosure (the addendum's release-gate item).** The interpreter is an **UNRECORDED variable** in every staged release: the manifest heredoc (`stage.sh:585-604`) and the provenance sidecar (`lib.sh:3225`) are **both shell-side**, and **the daemon never reads the sidecar** (0 hits for `build-provenance` across `daemon/`). There is **no daemon-side release-composition record or table**; a daemon-side fix would land only from the release **after** this one (the promote is driven by the currently-live daemon binary). ⇒ **For THIS release the interpreter is disclosed by MEASUREMENT, not by a code change:**
   ```
   grep -aoE 'libpython3\.[0-9]+' <staged>/ensemble-prod | sort -u   # → libpython3.14
   grep -aoE 'cpython-3[0-9]+'   <staged>/ensemble-prod | sort -u   # → cpython-314
   ```
   run against the **staged** artifact and **compared against live v0.29.7's same measurement**. The disclosure lands in **THREE** places: (i) the tester evidence, (ii) the staged-release composition metadata, (iii) project history. **This is a release-gate item: it may not be skipped.**

### 14.3 The promote command (DOCUMENTED, NOT RUN)

```
ENSEMBLE_UPGRADE_LIVE=1 VERSION=v0.29.8 bash scripts/upgrade/promote.sh live --f2-verified-closed
```
run from the **SCRIPTS-PIN worktree**: `/home/nea/ensemble-src-wt-scripts-pin` @ `84df6b55c`.
**CWD selects the executing script** — so the promote must NOT be run from the feature worktree (which would execute modified ceremony code if Phase 1 touched `scripts/upgrade/**`) and MUST NOT be run from the canonical dir.

**Why the canonical dir is arm-INELIGIBLE (three independent counts).**
(a) The daemon-side arm-time guard `scripts_dir_provenance_verdict()` (`daemon/tools/upgrade_tools.py:3988`, called at `:3725` in the arm preflight) requires `PIPELINE_SCRIPTS_FLOOR_COMMIT` (`daemon/tools/upgrade_tools.py:3923` = `2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa`) to be a **merge-base ancestor** of the executing scripts dir's HEAD — it is not, in the canonical dir.
(b) The working-tree stamp is `953603108f…` ≠ floor, and at HEAD there is **no stamp at all**.
(c) The dirty 6-entry staged index would execute **uncommitted ceremony code**.

⇒ **Design consequence:** this is an argument for **Phase 1 NOT touching `scripts/upgrade/**`** — and **no workstream proposes touching it**. If any workstream ends up requiring a change under `scripts/upgrade/**`, the plan must call out the **scripts-pin sync consequence** explicitly (the scripts-pin worktree must be synced with the change before the promote, or the promote executes stale ceremony code).

### 14.4 The staged-verification checklist (Gate 4)

1. The staged artifact boots in a **scratch** `ENSEMBLE_DATA_DIR` on a **free scratch port**, with `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` **pinned explicitly** (never `env -u` — the ambient guard must be *set*, not stripped), with a **per-variable** PG scrub (`env -u POSTGRES_DB -u POSTGRES_USER …` — the `POSTGRES_*` glob form is a silent no-op) and `POSTGRES_DB=ensemble_dev` asserted ≠ prod.
2. `livez` + `readyz` green; `/api/agents` census matches the base; **zero** `Failed to parse meta.json`.
3. The metrics door answers with the **corrected** verdicts: F1/F3/F6 `live` where they publish, `capture_off`/`census_gated` only when genuinely off.
4. Both kill-switch env vars are **absent** from the staged tree and from the effective env surfaces.
5. The queue brake is **untouched** (`is_paused` unchanged, `updated_at` unchanged).
6. The interpreter measurement (§14.2.9) is recorded in all three places.
7. The 4-way sha, sidecar, migration census (no-diff), prompt baseline (181), and payload source-less checks all pass.
8. **Live-install mutation disclosure (the approver's B2 note).** Staging to the **live** target writes the `ENSEMBLE_SELF_ENV` marker into the live `.env` and chmods it (`stage.sh:718-760`) — this is an **expected live-install mutation**, not a defect. Record it in the tester evidence (which file, which mode change) so the operator is not surprised by the diff.
9. **No promote.**

---

## 15. The Phase-2 switch-flip runbook — REQUIRED CONTENT SPEC

The runbook document itself (`phase2-runbook.md`) is written **after** implementation, when real behavior is known. **This plan specifies its required content** so nothing is forgotten:

1. **Preconditions.** The brake is armed and **verified engaged** (`is_paused=true` on `bd075f0b`, with the read-back); the release is promoted; the metrics door is honest (A landed); E4 has run (stale retired); F's payload is staged and its activation is gated on the capture lane; the Phase-2 observer is in place (D landed, retention ≥ 30 days).
2. **The order — EXACT.**
   **Step 0:** arm + verify the brake.
   **Step 1:** flip **CAPTURE only** (`ENSEMBLE_SKILL_CAPTURE_ENABLED=1`) → **restart the daemon** (the env is parsed at boot only) → **do not touch the evolution switch**. Observe for a **full observation window** (≥ 24 h; the runbook names the window and the signals).
   **Step 2 (only if Step 1 is clean):** flip **EVOLUTION** (`ENSEMBLE_SKILL_EVOLUTION_ENABLED=1`) → **restart** → observe for a second window.
   ⇒ **Capture first, evolution only after capture proves clean.**
3. **The asymmetry — stated explicitly and prominently.** **ON requires a daemon restart** (`.env` is parsed only at boot; `Restart=on-failure` means an un-restarted edit can apply itself on the next crash without anyone asking — the safe ordering is *brake first, switch second*). **OFF is instant** via the queue pause (read per poll, no restart) — with the two documented limits: (i) the brake is **broad** (it stops all parallel-queue traffic for the project, not just skill jobs), and (ii) a job **already admitted** runs to completion (≤ 1 poll interval to stop *new* admissions; unbounded for an in-flight job).
4. **Go/no-go criteria for each flip** — measurable, with thresholds: capture admission rate within the expected band; **zero** keeper deviations; zero cap refusals; the false-eligible population stable; the counter drift **0**; no new `dark`-while-publishing verdicts; the anomaly surface quiet.
5. **Monitoring checks BEFORE and AFTER each flip** — the §D.2 signal table, with the exact read command/query per signal, and the **two refusal kinds kept distinct** (§D.1).
6. **Rollback levers, per step** — instant: the queue pause; slow: the switch + restart; and the *pre-promote* lever: revert + re-promote the release.
7. **The A/B backlog precondition** — the 27 unresolved A/B groups (10 at `comparisons=0`) must be dispositioned **before** the evolution flip (P4). The `regression-ab-partition-run` pair (E4's R1 rule) is the canonical instance.
8. **The first-live-CAPTURED-row proof** (E2) — the runbook names the exact observation that proves the capture→evolution path works in production for the first time, and states that until then the diversity gap is open.
9. **The abort criteria** — the exact conditions that stop the ladder and return to the brake.
10. **The audit trail** — every step's timestamps, the operator, the reads taken, and the receipt ids.

---

## 16. Open items

### 16.1 CONFIGURE-ONCE-CENSUS-LANDS (a pending DB census supplies VALUES, not design)

| # | The census must supply | Which plan decision would CHANGE if it contradicts the plan |
|---|---|---|
| **C-1** | The exact ids of the **6 inactive never-used captured-origin skills** (aggregate-only in the report) | **ANSWERED (census).** The 6 captured are **ALREADY `(False,'inactive')`** at the state level. §E4's R2 rule is unchanged (id-agnostic); the ids are needed only if belt-and-braces re-affirmation is kept. **No plan change.** |
| **C-2** | The exact **mismatch set** behind "48/628 vs 43/529" and the exact growing-counter series | **SUPERSEDED by the A4 rewrite.** The "mismatch" was names-with-diverging-content over total skills — a gauge that measures a **non-defect** (per-project `bank_clone` clones make `name` intentionally non-unique). §A4 now redefines the gauge to within-identity-key divergence and rewrites the exit criterion. The transition-based bump (DS-08) is unchanged. |
| **C-3** | The exact **per-agent `skill_injection` roster** at `origin/latest` | **ANSWERED (this plan, §10).** Recomputed from the worktree: 15 true / 10 false out of 25 declaring; 31 of 46 agents cannot capture. If the census disagrees, §F's manifest is corrected — **the plan's ruling (do not flip any agent in Phase 1) is unchanged.** |
| **C-4** | The `skill_evolution` table's **outcome-field population** (`selected`/`applied`/`task_succeeded`) and the **schema of the skill tables** (is there an `is_active`/`enabled` column, and who else writes it) | **ANSWERED (census).** `skill_usage_records`: `selected`/`applied`/`task_succeeded` = **ZERO NULLs** over 10,108 rows; `feedback_applied` NULL=3,547/TRUE=6,091/FALSE=470. `skills`: `is_active`+`status` exist (`models.py:171-172`); sanctioned writer is `SkillRepository.deactivate` (`:383-396`, sets **both** columns); **no other daemon writer** of `is_active=False` for skills. `skills` schema: `lineage_origin` (bank_clone=407, imported=157, evolved=60, captured=6), `ab_test_group`, no tombstone column. **No plan change** — §A3's predicate and §E4's mechanism stand. |
| **C-5** | Within-identity-key divergence baseline (production, 630 rows) | **ANSWERED / superseded by C-6 — production within-identity-key divergence = 32** (27 stranded A/B pairs, R4 census). Dev = 0 (15 rows, 15 identity keys, no divergence — verified by read-only SELECT on 2026-11-09). The 0 target is **BLOCKED-ON the A/B resolution decision (Ruling E)**. The query below is the **reproduction recipe** (not a placeholder): `WITH g AS (SELECT name, project_id, count(DISTINCT content) AS dc FROM skills GROUP BY name, project_id) SELECT count(*) FILTER (WHERE dc > 1) AS diverging_keys, coalesce(sum(cnt) FILTER (WHERE dc > 1),0) AS rows_in_diverging_groups FROM (SELECT name, project_id, dc, count(*) OVER (PARTITION BY name, project_id) AS cnt FROM g JOIN skills USING (name, project_id)) x;` |
---
| **C-6** | The 27-pair stranded A/B backlog (R4 census) | **ANSWERED (census).** 27 families / 54 active rows / 26 distinct names / 32 within-identity-key divergence on prod. **26 of 27 pairs carry a LIVE unresolved `skill_ab_tests` row; zero are explained by a resolved A/B. 21 of 27 are being served to both variants. 2 families have IDENTICAL content (harmless duplicates).** E4's mechanism must be a **pair-resolver** (declare winner on `skill_ab_tests` + deactivate loser, one reversible unit with a receipt). **NO winners are declared in Phase 1** — the winner decision is a new open item (see §16.3 E-6). C-6's remaining open sub-item is the **WINNER DECISION**. |

### 16.2 TO-CONFIRM (an input that may land later)

| # | Item | Handling |
|---|---|---|
| **T-1** | The **full text of board `wi-d340f16c`** (skill-tools display hygiene) | **ANSWERED (census).** The body is 3 bullets (8-hex `:1260`, 8-hex `:1491`, `pf-` pseudo-id `:402`). The commission's 3 extra items are **NOT in the real item** and are carried as **leader-judgment cleanups, labeled NOT-from-board**. No re-run needed. |
| **T-2** | The **quarantine re-adjudication** of rows 45/56/90 after G | §G states the desired outcome (removal). If the tester finds a *different* residual cause, the rows are re-scoped with the new evidence — never silently kept |
| **T-3** | The **next free version slot** | §14.1 says `v0.29.8` as the expected slot; the implementer re-derives it at staging time |

### 16.3 Emperor decisions this plan explicitly does NOT make

| # | Decision | Why it is not mine |
|---|---|---|
| **E-1** | The **master skill-system switch** (one lever for capture+analysis+evolution) | §B4 — D7/HANDOVER P3 deferred it; the Emperor's Phase-2 go/no-go |
| **E-2** | The **Tier-3 direction quarantine's approval surface** (who approves, through what channel, into what storage) | §B5 — D6's reason stands: a regex scan is a false-safety half-measure, and the approval authority does not exist yet |
| **E-3** | The **fleet-wide default** for the per-agent read scope (making it default-ON, and the MCP/remote-hands interaction) | §B3 — D8 res.9's own blocker; Phase 1 ships the opt-in slice only |
| **E-4** | **Which agents** get `skill_injection: true` when F's payload is armed | §F — the manifest is per-axis and per-agent; the flip is a Phase-2 act |
| **E-5** | Whether the **capture→evolution diversity gap** is acceptable to close in Phase 2 | §E2 — Phase 1 makes the path ready; only a live run proves it |
| **E-6** | **Which variant wins** in each of the 27 stranded A/B pairs (code-fix, dev-strategy, git-commit, plan-creation, …) | §E4 (Ruling E) — the recommended default (newer evolved generation wins) is a **RECOMMENDATION ONLY**, not an executed action. The winner decision has real quality consequences and belongs to the orchestrator. |
| **E-7** | **E4.1's exact hook point + process-once suppression** | **ANSWERED (resolved in code).** Resolved wiring site is `daemon/manager.py:2786` (after `_wire_skill_injection_capability_gate()`, strictly after `_ensure_postgres_columns()` at `:1034`, outside the one-transaction ensure list, fail-soft); suppression is file-backed via receipt in `<data_dir>` (reconciliation skips processed `(skill_id, rule_id)` pairs). |

---

## 17. Disclosures

1. **Anchor drift (corrected in this plan).** The following commission/facts anchors were verified against the worktree and **drifted**; the corrected anchor is used:
   `api.py:2571-2576` (facts' `2528-2533` in the disclosure doc) · `daemon/services/skill_evolution_tools.py` (the facts say `daemon/services/skill_evolution_tools.py` — the real path is **`daemon/tools/`**) · `daemon/tools/instance.py:430` (`resolve_tool_filter`; the facts say `:392`) · `daemon/tools/filesystem.py` read-tool call sites — **SUPERSEDED plan-time anchors** `:596` (list_directory) / `:670` (read_file) and walk tools `:754`/`:764` (glob_files), `:1309`/`:1322` (grep_files) have ALL drifted and describe the pre-B3 two-site shape; **AS BUILT (re-verified 2026-11-09):** `:925` (list_directory) / `:1007` (read_file), `:1091`+`:1104` (glob_files), `:1654`+`:1670` (grep_files); the plan's earlier walk-tool cites pointed inside `write_file` (pre-PR def `:1107`) and `edit_file` (pre-PR def `:1510`) — **WRONG**, and DELETED (the facts say `:584/657/748/1040`) · `daemon/config.py:4186-4188` (the facts cite this for the inline `skill_evolve`; it is actually the **live-view reserved-root guard** — the inline path is `daemon/tools/skill_evolution_tools.py:230-252`) · `daemon/repositories/skill/repository.py:1550-1573` (the commission's "~:1550-1573" — **exact**) · `daemon/services/skill_metrics_service.py:642-662` (Gate 4; the facts say `:648` — the call is at `:648` inside `:642-662`) · `daemon/services/skill_job_dispatcher.py:641-657` / `:327` / `:678` (the facts cite `:180-183` for the burst comment — **exact**).
2. **The base-red mechanism is not a "dup-prefix"** (P4) — it is a SQLite-incompatible `ALTER TABLE … DROP CONSTRAINT IF EXISTS`. The migration file's own DUAL-DRIVER NOTES claim SQLite supports it; that claim is false. **This is a separate, pre-existing defect; Phase 1 does NOT fix it** (it is disclosed base debt and out of the commission's scope) — but it **is** the reason §E4 cannot use the `.sql` vehicle.
3. **The read-reach residual is all FOUR read tools, not two** (P5, re-adjudicated) — the 2026-09-23 fix bounded the `glob_files`/`grep_files` walk against the **agent-supplied `workdir`**, so it was effective only for the default workdir and **ineffective for a scoped agent**; B3 now fences all four read tools at the daemon-bound trusted anchor (§6 B3, §17 #28).
4. **The brake is NOT currently engaged** (P7) — the standing project critical note claiming "system_parallel_queue PAUSED" is **stale** (the pause was lifted 2026-10-26T21:50:04Z). The Phase-2 runbook arms it; Phase 1 does not.
5. **E1's premise was partly already satisfied** (P6) — D2's 1800 s cooldown already blocks the observed Oct-21 burst; E1's gap is visibility + durability + a hard cap.
6. **The `b7da3ed0` shape is already structurally refused** (P8) — by the shipped allow-list strip (live v0.29.7). B's contribution is the **behavioral proof**, not a new control.
7. **The 3 base reds are already a known quarantine family** (QUARANTINE.md rows 89 / 76 / 74 / 82) — they are disclosed, not new.
8. **The base leg is not single-run stable** — run1 4F/455P, run2 3F/456P, run3 4F/455P; the delta is exactly the AB node. ⇒ §13.2's ≥3-run red-set-diff protocol is mandatory. **The TRUE base red set is the intersection (3 reds), never a single run.** The raw runs are in the evidence pack (§13.2, `skill-revival-base-evidence-20261109-0813Z` + the committed copy under `.agents/tester/RESULTS/2026-11-09-skill-revival-base-evidence/`).
9. **The red-list classifier is a pinned house critical note** — `grep '^FAILED'` only; a naive `grep -E '^(FAILED|ERROR)'` double-counts the migration failure (3× per run × 3 runs = 9 hits masquerading as a 4th red). The tester inherits the classifier; it must not author its own.
10. **The base worktree must stay clean** — it is the comparison reference; a dirty base is a broken comparison. The evidence copy lives in the **feature** worktree only.
11. **The A/B backlog (27 unresolved, 10 at `comparisons=0`) is a Phase-2 precondition**, not a Phase-1 deliverable — but E4's R1 rule acts on the one concrete instance the Phase-0 artifacts name.
12. **`to_summary()` has zero callers** — it is a **dead surface**, verified at `daemon/services/project_skill_source/model.py:199-220`.
13. **The `skill_evolution` outcome-field population is MEASURED (C-4 ANSWERED)** — `selected`/`applied`/`task_succeeded` = **ZERO NULLs** over 10,108 rows; `feedback_applied` NULL=3,547/TRUE=6,091/FALSE=470; TRUE counts selected=10,108/applied=6,095/task_succeeded=9,311. The false-eligible window `selected AND task_succeeded AND feedback_applied IS NULL` = **3,457 (3457)** rows. §A3's predicate is pinned against this census, not against the model's documented contract alone.
14. **The interpreter is an unrecorded variable in every staged release** (§14.2.9) — disclosed by measurement for this release; a daemon-side record would only land from a later release.
15. **No workstream touches `scripts/upgrade/**`** — so the scripts-pin sync consequence does not arise for this release (§14.3).
16. **The capture→evolution diversity gap is NOT closed by Phase 1** (§E2) — the first live CAPTURED row is the Phase-2 proof.
17. **P9 — E3's scope is smaller than the commission claimed.** The real board item `wi-d340f16c` contains **EXACTLY THREE** bullets (the two 8-hex echoes at `:1260`/`:1491` and the `pf-` pseudo-id echo at `:402`). The `:1213` refusal gap, `to_summary()` dead surface, and P7 citation re-derive are **ABSENT** from the real item and are carried as **leader-judgment cleanups, clearly labeled NOT-from-board** (or dropped). The item also states the echoes are **DISPLAY-ONLY** (*"skill_view consumption is NOT affected"*) and that **none block the promote**.
18. **A4 is a gauge redefinition, not a "converge to 0" fix.** The "48/628 vs 43/529" gauge measures `names-with-diverging-content / total skills` — a **non-defect**, because `skills.name` is intentionally non-unique (per-project `bank_clone` clones make one name span many `project_id`s by design). Global name divergence is **unsatisfiable** without destroying the per-project clone model. The honest target is within-identity-key divergence `(name, project_id)`; the exit criterion is rewritten accordingly. Within-identity-key divergence is **0 on dev** (15 rows, 15 identity keys); **production = 32, determined by the R4 census (C-6)**, caused by 27 stranded A/B pairs. The 0 target is **BLOCKED-ON the A/B resolution decision (Ruling E)**.
19. **A3 is now exact.** `selected`/`applied`/`task_succeeded` = **ZERO NULLs** over 10,108 rows; `feedback_applied` NULL=3,547/TRUE=6,091/FALSE=470; TRUE counts selected=10,108/applied=6,095/task_succeeded=9,311. The false-eligible window `selected AND task_succeeded AND feedback_applied IS NULL` = **3,457 rows** (matches the commission's ~3,450). The predicate is pinned against 3,457 at census time.
20. **E2's premise is unverifiable at column level.** There is **no `skill_evolution` table** — the 116 rows are **JOB rows** (108 done on `bd075f0b`, 7 done on `cd02c5d2`, 1 dead on `bd075f0b`); no row carries an action/verdict key in metadata; `skill_lineage` (60 rows) has no verdict column. "116/116 are FIX" is **job-message-text inference, not a DB fact**. The path is made ready; the diversity gap is **not** closed by Phase 1.
21. **E4's lever is two desyncable columns.** `skills.is_active` (bool) + `skills.status` (varchar): census `(True,'active')=538 · (True,'ab_testing')=52 · (False,'inactive')=39 · (False,'ab_testing')=1` — the last group is the **desync proof**. Any deactivation MUST set **both**. **No tombstone column exists.** The 6 captured-origin skills are **ALREADY `(False,'inactive')`** — so "retire the 6" is a no-op re-affirmation, not substantive work. 52 ACTIVE rows carry `status='ab_testing'` — deactivating them strands open A/B tests (guard: `skill_ab_tests.resolved_at IS NULL`). 1,846 usage records land on INACTIVE skills (+60 since 11-04, ~11/day) — a **selection defect**, routed to E4 (injection-side defence) + a follow-up; not in A's scope.
22. **Workstream C resolves by ID, not by name LIKE.** The census's "queue `bd075f0b` has no matching `job_queues` NAME" was a **filter artifact** (`name ILIKE '%skill%'`); the queue is almost certainly named **`system_parallel_queue`** (critical note `ed6da139`). C's verification read is `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';` (the `job_queues` PK is `queue_id`, `daemon/repositories/job_queue/models.py:201`; the earlier `WHERE id = …` read was WRONG and is DELETED). The runbook must not carry a "the queue row may not exist" uncertainty.
23. **The `skill_evolution` table does not exist.** The 116 rows are **JOB rows**. E2's premise ("116/116 are FIX") is job-message-text inference; E4's mechanism ruling is re-confirmed (the dual-column lever makes the operator-script vehicle even more clearly right).
24. **Ruling D — A4's "is 0" claim is FALSE today (Addendum 5).** Within-identity-key divergence on prod = **32**, caused by **27 stranded A/B pairs**. The exit criterion is rewritten to report 32 and name the 27-pair cause; the 0 target is marked **BLOCKED-ON** the A/B resolution decision. A4 and E4 are **coupled on one root cause** — the metrics weirdness and the skill hygiene problem are the same 27 pairs.
25. **Ruling E — E4's real target is the 27-pair backlog.** E4 is reshaped from "deactivate ~10 rows" to "resolve or refuse, per pair, through the A/B resolution path" — a **pair-resolver**, not a flat flip. **NO winners are declared in Phase 1**; the winner decision is an open item for the orchestrator (§16.3 E-6). Recommended default (RECOMMENDATION ONLY): the newer evolved generation wins (lineage child), not timestamps — two families have stale `updated_at` relative to their challenger's `created_at`, so a "newest updated_at wins" rule would pick WRONG. R2 (6 captured) = already inactive, NO-OP. R3 (gate-runner copies) = **DO NOT EXIST (0 rows)** — the commission's premise is refuted. `9e79ccad` = active, no A/B group, not stale, no flip. `76e3e1a7a` = NOT FOUND. 2 identical-content families: no content-logic remediation.
26. **Ruling F — F (injection) must dedupe by identity at the injection boundary.** At most **ONE variant per `(name, project_id)`** per agent; deterministic pick = highest generation (newest lineage child), ties by id. **Pin** + mutation leg added. The 21 pairs currently served to both variants are a plausible contributor to the "flaky / misreading instructions" symptom — flagged as a **HYPOTHESIS**, not a proven cause.
27. **E4.1's wiring/boot site — LOCATED from the code (council F-16 / F-18).** `InstanceManager.__init__` orders: `run_pending_migrations()` (`daemon/manager.py:1017`) → `self._ensure_postgres_columns()` (`daemon/manager.py:1034`, inside `if ... is_postgres:`) → the **skill-services init block** (`daemon/manager.py:2686` `SkillInjectionService(...)` … `daemon/manager.py:2759` `self._wire_skill_injection_capability_gate()`). ⇒ **E4.1's candidate wiring site is `manager.py:2759`-adjacent** — the last call of the skill-services block, guarded by the same "skill repos present" condition, i.e. **strictly AFTER** the ensure list. It therefore **never sits inside the `_ensure_postgres_columns` one-transaction ensure list** (critical note 48a7f77f: any entry's failure aborts boot). ⚠ **The exact hook point and the process-once suppression mechanism are an IMPLEMENTATION decision**, resolved by the developer at implementation start (an OPEN ITEM — §16.3 E-7); the plan fixes the CONSTRAINTS: after `:1034`, before the first injection can occur, idempotent, never inside the ensure list.
28. **B3's read scope covers ALL FOUR read tools — and the write-path scope is stated.** ⚠ **CORRECTED.** This item previously read that B3's fence touches `read_file` (`filesystem.py:670`) and `list_directory` (`:596`) only and that `glob_files`/`grep_files` **"are already fenced"** — **that claim was WRONG and is withdrawn.** The pre-existing `_resolve_search_root` fence (`:252-283`) bounded the walk against the **AGENT-SUPPLIED `workdir`**, so it held only for the default workdir: a scoped agent could point the walk outside its anchor and exfiltrate out-of-anchor file **CONTENT** (proven by re-adjudication — `grep_files` against an out-of-anchor root returned the canary). **The SHIPPED truth:** B3's scope fence covers **all four read tools** — `read_file`, `list_directory`, `glob_files`, `grep_files` — with the **IDENTICAL** policy: relative-only paths, the factory-bound trusted anchor, the agent-supplied `workdir` refused, strict containment, and the **`.env`-family deny**. See the corrected §6 B3 paragraph (the "Scope, corrected (P5)" statement, its Ruling policy block, and the RECONCILIATION paragraph). No write-path change is in scope (the keeper holds no write tools), so the council's "write-path scope unstated" finding stays closed.
29. **E4 mechanism re-confirmation.** With the lever now known (dual-column), the operator-script vehicle is even more clearly right — a `.sql` file cannot express a transactional multi-column update that must also be reversible and idempotent across boots.
30. **§17 coverage is part of the cross-check discipline.** §19.1's protocol is extended to include §17's disclosure items (each disclosure that asserts a code fact must carry the anchor and be re-verified by grep on every revision) — the approver found §17 #13 contradicting C-4, which the cross-check missed because §17 was not in its scope.
31. **IMPLEMENTATION-FOUND DEFECT (I5) — the REST PATCH manufactured a MIRROR desync.** *Found by the E1/D lane during implementation; fixed by Dev-A (workstream A, I5).* `daemon/routers/skills.py:931-933` translated the public `is_active` flag by **POPPING** it and writing **ONLY** `status` — `fields["status"] = "active" if fields.pop("is_active") else "inactive"` — so the row's `is_active` **column never moved**. Every `PUT /api/skills/{id}` carrying `is_active` therefore produced `(is_active=True, status='inactive')`: the **MIRROR** of the real production desync row `(is_active=False, status='ab_testing')` this workstream exists to reconcile. **Class:** the same dual-column desync the E4 writer guards against, reached through a DIFFERENT door (the REST surface, not the deactivation mechanism) — so fixing E4 alone would NOT have closed it. **Fix (Dev-A, `daemon/routers/skills.py:929-960`):** the handler now keeps `fields["is_active"]` AND derives the paired `fields["status"]`, so `store.update_skill` persists BOTH in ONE repository call (`SkillRepository.update` writes every field it is handed in a single transaction). **REST acceptance semantics are UNCHANGED**: the same `is_active` field is accepted, `status` was never an accepted body field, and no new refusal can fire. The E4 writer (`deactivate_with_receipt`, commit `5e7834dd4`) was deliberately **NOT** reused here — it refuses on an open A/B test, which would have turned an existing 200 into a refusal (a semantic change, not a desync fix). **Pin:** `tests/unit/routers/test_skill_patch_dual_column.py` (9 nodes) + the pre-existing router pin `tests/unit/routers/test_skills.py::TestUpdateSkill::test_update_translates_is_active_to_BOTH_columns`, whose OLD form asserted `assert "is_active" not in kwargs` — i.e. it **pinned the defect** — and was corrected, not loosened. **Mutation leg `M-I5-a`** restores the pop-and-translate shape ⇒ 4 REST pins RED. Registered in `skill_dual_column_deactivation_unit_test` (22 nodes / 4 legs).

32. **E3's scope is EXACTLY the three board bullets — and the display-hygiene echoes are now CLOSED (final pass).** `daemon/tools/skill_tools.py`: the `skill_fix` echo and the `skill_feedback` echo now print the **full id** (the bare 8-hex `skill_id[:8]` token is gone — it is NOT `skill_view`-consumable, so an agent that copied it back failed the wi-76d89e11 verbatim contract); `_render_pf_view`'s agent-facing `- id:` line now prints the **ADDRESS** `pf:<ns>:<name>` and the internal `pf-<sha16>` pseudo-id moved to the ADDITIVE `- pseudo_id:` line (the internal key stays reachable for the injection path and the embedding rows). **DISPLAY-ONLY, as the board item states** (*"skill_view consumption is NOT affected"*) — the `skill_id_verbatim_contract` pack is the regression net and stays green, and one pin proves the emitted id ROUND-TRIPS back through `skill_view`. The `:1213` refusal-gap extension, the `to_summary()` dead surface and the P7 shared-refusal-helper re-derive remain **NOT-from-board leader-judgment cleanups, OUT of this lane** (they are NOT implemented and NOT pinned here).
33. **D-N7 — `list_directory` was the sibling surface the `.env` fix left open.** The read tools refused `.env`, but `list_directory(show_hidden=True)` still ENUMERATED the denied basenames (names only, never content — but the NAME is itself the hazard: it tells the agent the credential file exists and where). The listing now filters entries through `_is_scope_denied_basename` for a SCOPED agent only; **undeclared agents are byte-identical to base**. Pinned both directions plus a mutation leg.
34. **D-N8 — the `CATEGORY_DOC` text contradicted the shipped behaviour (DOC-ONLY fix).** It said `workdir` is REQUIRED for absolute-path walks. The real contract is now stated: **scoped agents** get relative-only reads anchored at the daemon-resolved trusted workdir, the `workdir` argument is NOT accepted for them, absolute paths and anchor-escaping relative paths are refused, and the `.env` family is refused at any depth; **undeclared agents** keep the legacy `workdir` semantics unchanged. Zero behaviour change — the existing pins are unchanged and green.

35. **IMPLEMENTATION-FOUND RESIDUAL (F-4) — a `daemon.tools.*` import still registers metric families IN-PROCESS; the exposition artifact is NOT reachable, so the trap is INERT. — ⚠ STATUS AMENDED 2026-11-10 (round 7): THE TRAP STILL FIRES (re-probed; the E1/D lane's M9 hardening did NOT close it).** *Found by the council (F-4, MINOR); MEASURED by Dev-A (workstream A, round 5) — **NO code change made, by the dispatched verdict rule.** **The residual link.** `daemon/tools/nest_report.py:159` does `from daemon.services.anomaly_service import (… ANOMALY_SEVERITIES …)`; that is an ATTRIBUTE import, so it fires the module's PEP-562 `__getattr__` (`daemon/services/anomaly_service.py:247`) → `_resolve_anomaly_severities()` (`:217`) → `from daemon.services.metrics_query.wiring.vocab import …` — and importing ANY submodule of the wiring PACKAGE executes its `__init__.py`, which registers the families at import time. **Proof of the link (fresh interpreter):** `import daemon.services.anomaly_service` alone ⇒ **15** registered metrics, wiring NOT in `sys.modules`; the ATTRIBUTE import ⇒ **67** (wiring in `sys.modules`). Every `daemon.tools.*` module shows the same **+52** delta (`daemon.tools`, `daemon.tools.nest_report`, `daemon.tools.nest_report_read`, `daemon.tools.skill_evolution_tools`, `daemon.tools.instance` — each 15 → 67). **Three measurements ⇒ the trap is INERT.** (i) **PROBE:** the registration is REAL and in-process (67 vs 15). (ii) **ISOLATION:** the committed exposition artifact is rendered in a **SUBPROCESS** — `tests/helpers/metrics_exposition_baseline.py:118-136` spawns a FRESH interpreter whose import set is ONLY `daemon.checkpoint_metrics` + `daemon.services.metrics_listener` — so an in-process registration cannot reach it. (iii) **CO-RESIDENCY (decisive):** ONE pytest invocation with a `daemon.tools`-importing file FIRST (`tests/unit/test_nest_report_read_tools.py`) and the exposition structural-identity node SECOND (`tests/unit/metrics/test_metrics_query_routes.py`) ⇒ **58 passed, 0 failed** — the node stays GREEN. Widened to the whole `tests/unit/metrics/` set: the red set is **IDENTICAL** with and without the co-resident file (2 reds, both **base-identical** and unrelated: `test_metrics_family_adjudication.py::…test_the_daemon_diff_is_confined_to_the_allowed_files` and `test_metrics_family_w3.py::…test_the_conditionality_evidence_is_recorded_in_the_plan`). **⇒ VERDICT: INERT (subprocess-isolated + co-residency green) ⇒ NO code change.** The M-1b-era deferral in `anomaly_service.py` cleaned the MODULE import; what remains is the ATTRIBUTE import path. **Residual risk (disclosed, not built):** an in-process render DOES diverge (29 → 81 `# HELP` lines after a `daemon.tools` import) — so any FUTURE test that compares an IN-PROCESS render against the committed artifact would be the first live victim. The durable hardening (a lazy/canonical import in `nest_report.py` + extending the import-purity pin to a `daemon.tools.*` module) is **ROUTED, not built** — the dispatch's verdict rule says INERT ⇒ record, do not change code, and the F-4 remediation would touch a file this lane's fence opens ONLY for a LIVE verdict.
   **⚠ AMENDMENT (2026-11-10, round 7 — RE-PROBED, STILL OPEN).** The E1/D lane has since landed the **M9 vocabulary consolidation**: a stdlib-only leaf `daemon/services/anomaly_vocab.py` (ONE vocabulary, ONE canonicaliser) with `daemon/services/anomaly_service.py` importing the leaf at module level. **The dispatch required a RE-PROBE, not a belief — the probe says the trap STILL FIRES, with numbers IDENTICAL to the recorded baseline:** a fresh interpreter importing each of `daemon.tools`, `daemon.tools.nest_report`, `daemon.tools.nest_report_read`, `daemon.tools.skill_evolution_tools`, `daemon.tools.instance` registers **15 → 67 metric families (+52)** with `daemon.services.metrics_query.wiring` **in `sys.modules`** — the same +52 and the same wiring import as the 2026-11-09 baseline. **Why the M9 hardening did not close it:** the leaf is import-PURE (importing `daemon.services.anomaly_service` alone registers **15** and does NOT import the wiring), but its point-of-use resolvers still reach the wiring package — `daemon/services/anomaly_vocab.py:140-146` (`resolve_anomaly_categories`) and `:149-156` (`resolve_reserved`) import `daemon.services.metrics_query.wiring.vocab` at the point of use (live vocabulary preferred, frozen fallback otherwise), and `daemon/services/anomaly_service.py:210-218` (`_resolve_anomaly_severities`) does the same for the severities. **The trigger is unchanged: the ATTRIBUTE import at `daemon/tools/nest_report.py:159`** (`from daemon.services.anomaly_service import ANOMALY_SEVERITIES, CATEGORY_RAW_MAX_CHARS`) fires the PEP-562 `__getattr__`, and the attribute-by-attribute probe shows **each** of `ANOMALY_CATEGORIES`, `ANOMALY_SEVERITIES` and `RESERVED` still pulls the wiring in (+52 each). **⇒ STATUS: STILL OPEN (no closure claim).** What the M9 work DID achieve is recorded for fairness: the DUPLICATED vocabulary copy is gone (one vocabulary, one canonicaliser) and the module import is pure — the residual is now precisely the **point-of-use wiring import in the three resolvers**, not a duplicated list. **The residual RISK this item warned about therefore still stands:** an in-process render still diverges (29 → 81 `# HELP` lines after a `daemon.tools` import), so an in-process-vs-artifact comparison remains the first live victim. **The durable hardening (a frozen, import-pure vocabulary with NO point-of-use wiring import, or a lazy import in `daemon/tools/nest_report.py` + extending the import-purity pin to a `daemon.tools.*` module) remains ROUTED, not built** — and the files that would carry it (`daemon/services/anomaly_vocab.py`, `daemon/services/anomaly_service.py`, `daemon/tools/nest_report.py`) are OTHER LANES' files, read-only in this round.
36. **IMPLEMENTATION-FOUND METHODOLOGY GAP — touched-file red accounting is structurally BLIND to pins in registered-pack files the diff never touches (F-2 is the case study).** *Found by the council (F-2, MAJOR); recorded by Dev-A (workstream A, round 5) — **the fix is ROUTED TO THE TESTER'S BRIEF, NOT BUILT in this lane** (per the dispatch). **What happened.** The capture-lane read route (`daemon/routers/metrics.py:818-889`, commit `a67b74066`) moved the declared-route census **113 → 114** — a NEW scope-decorated route on the SAME metrics router. That broke pins in **TWO REGISTERED PACKS whose files the diff never touched**: `tests/unit/metrics/test_metrics_query_scope_cage.py` (`metrics_query_scope_cage`, PACKS.md:1058) and `tests/unit/test_router_scope_manifest.py` (`metrics_scope_cage_dual_side` D2 leg, PACKS.md:1035) — **3 net-new reds, both files outside the lane's touched-file set**, so the lane's §13.2 red accounting never saw them and its "NET-NEW = 0" claim was falsified at the gate. **The structural gap.** A lane's red-set accounting scoped to the files it TOUCHED is blind to a pin that lives in a registered-pack file the diff does not touch — and the registry is exactly where the gate will look. The honest LANE CLAIM is therefore: **"NET-NEW=0 across touched files AND the registered-pack registry"** — the first half is what a lane can measure for itself; the second half is what the GATE measures, and only the gate can see it. **Durable fix (ROUTED TO THE TESTER'S BRIEF, explicitly NOT built here):** the gate-time red accounting is scoped to the **registered-pack registry** (every pack the lane's diff touches, PLUS every pack whose file set the diff changes a shared surface of), and the lane's own claim is stated in the two-scope form above. A lane-local substitute would be a false assurance — the blind spot is the whole point. **Status in this round:** the two stale census pins are CORRECTED (113 → 114, both files + the PACKS rows) and both packs re-run green; the METHODOLOGY change itself is a **tester-brief item**, not a code change, and is not implemented in this lane.
---

## 18. File-by-file change list (every proposed edit, with anchors)

| # | File | Anchor | Change | Workstream |
|---|---|---|---|---|
| 1 | `daemon/api.py` | `2571-2576` (**AS BUILT: the `capture_active=` kwarg lands at `:2584-2586`**, inside the construction block that now spans `:2571-2588`) | pass a **late-binding** `capture_active` resolver, read via the house pattern `getattr(app.state, "wedge_capture_watchdog", None)` (`daemon/routers/wedge_capture.py:102`, `daemon/services/metrics_query/wiring/sources.py:233`) — the attribute may not exist before `api.py:3250` and a direct read would raise | A1 |
| 2 | `daemon/services/metrics_query/provider.py` | `67`, `75`, `147` | accept `bool \| Callable[[], bool]`; resolve per read | A1 |
| 3 | `daemon/services/metrics_query/availability.py` | `61-62` | docstring → name the wedge-capture process fact | A1 |
| 4 | `daemon/services/metrics_census.py` | `335` | `return not self._gate()` — the polarity fix | A2 |
| 5 | `daemon/services/metrics_query/availability.py` | `83-88` | R-i → **R-ii**; note R-i is fixed | A2 |
| 6 | `docs/architecture/metrics-promote-disclosure.md` | `82-98` (**AS BUILT: the rewritten block spans `:68-100`**) | anchor `2528-2533`→`2571-2576` — the disclosure names `daemon/api.py:2571-2586`, the AS-BUILT construction block that contains the plan-time `2571-2576`; "routed OUT"→"FIXED" for BOTH defects (R-i at `daemon/api.py:2571-2586`, R-ii at `daemon/services/metrics_census.py:343`); keep the 4 tokens (VERIFIED: 172/172 across the five token files) | A2 |
| 7 | `frontend/src/app/pages/metrics/tabs.manifest.ts` + `frontend/src/app/pages/metrics/tabs/runtime-tab.component.ts` | `tabs.manifest.ts:233-239`, `:259-263`, `:276-280`; `runtime-tab.component.ts:13-17` | the *"dark until R-ii lands"* comments + the F1/F3/F6 bound text → the TRUE state (R-i and R-ii FIXED; F1/F3 live iff the wedge-capture watchdog is live, else `dark/capture_off`; F6 live iff the census is enabled AND running AND publishing, else `dark/census_gated`). The four tokens survive verbatim (lower-case in the FE spec). **DONE** — `tsc --noEmit` exit 0 and the metrics jest suite 495/495 green | A2 |
| 8 | `daemon/repositories/skill/repository.py` | new sibling method near `1500-1573` (AS BUILT: `1699-1758`) | **`has_genuinely_evolved_for_instance`** — the **three-conjunct OUTCOME-FIELDS predicate `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL`** (LEADER RULING Q1, character-for-character with §5 line 170 and §19.1 C-1; the earlier `has_feedback_for_instance` / `feedback_applied IS NOT NULL`-only wording was SUPERSEDED and is corrected here). NOT `applied == True` — that field has no writer outside the one-way latch at `repository.py:1308-1318`, and `update_completion` at `:1339-1436` never sets it despite `models.py:311-315` declaring it | A3 |
| 9 | `daemon/services/skill_metrics_service.py` | `648` (AS BUILT: the Gate-4 block in `_check_capture_eligibility`) | Gate 4 → **`has_genuinely_evolved_for_instance`** (the three-conjunct `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL` form) | A3 |
| 10 | `daemon/services/skill_evolution_service.py` | `1647-1649` (AS BUILT: `1707-1714`) | the service-seam twin → the same **`has_genuinely_evolved_for_instance`** three-conjunct predicate | A3 |
| 11 | `daemon/services/skill_metrics_service.py` | `1027-1029`, `1170-1186` (AS BUILT: the `_do_feedback` return tuple + the `if applied_bool is True and not prev_applied:` guard) | capture `prev_applied` from the SAME single row fetch (no second query); bump `total_applied` only on the False→True transition; the on-miss insert path returns `prev_applied = False` so the FIRST legitimate bump still happens | A4 |
| 11b | `daemon/repositories/skill/repository.py` | **CHOSEN ANCHOR (back-filled 2026-11-09): `daemon/repositories/skill/repository.py:799-912`, `SkillRepository.within_identity_key_divergence()`** — the per-skill aggregation area, deliberately OUTSIDE the A3 gate region. The FIND-IT stands as history: no anchor existed at plan time (`grep -rn "GROUP BY name HAVING COUNT" daemon/` → 0 hits; a broader `grep -rn "HAVING COUNT" daemon/` returns 5 unrelated sites: `manager.py:8609`/`:8662`, `checkpoint_adapter.py:1198`/`:1898`, `maintenance.py:1477`; `anomaly/repository.py:708` is the ORM `.having(func.count() …)` construct — a different construct neither grep returns)) | the **NEW** within-identity-key divergence gauge: `count(DISTINCT content) > 1` grouped by `(name, project_id)`. The implementer anchors it with `file:line` and records the anchor in the PR (acceptance = the anchor, not a claim that it pre-existed) | A4 |
| 12 | `daemon/services/skill_job_dispatcher.py` | `641-657` | add the **durable** windowed FIX cap + the human-visible signal | E1 |
| 13 | `daemon/repositories/job_queue/repository.py` | near `437` (`create_or_get_by_idempotency_key`) | `count_fix_admissions_since(skill_id, since_iso)` — filtering `job_type='skill_evolution'` AND `metadata->>'skill_id' = :skill_id` (PG) / `json_extract(metadata,'$.skill_id')` (SQLite) AND `metadata->>'evolution_type'='FIX'` (PG) / `json_extract(metadata,'$.evolution_type')='FIX'` (SQLite) AND `created_at >= :since` (dialect-split per `job_queue/repository.py:3249-3250`). **NOT** keyed on `idempotency_key`: `job_queue_service.py:1596-1608` suffixes re-admissions with `#<uuid8>` under a partial UNIQUE index (`job_queue/models.py:300-306`), so a job-keyed count matches ≤1 row and the cap can NEVER trip. **UNINDEXED** scan (no `created_at` index; the JSON path has no index) — bounded at the FIX-admission volume, pinned as such | E1 |
| 14 | `daemon/tools/skill_evolution_tools.py` | `230-252` | gate `skill_evolve` on `manager.is_skill_evolution_lane_enabled()` | B2 |
| 15 | `daemon/manager.py` | `7992-8004` | (no change — the accessor is the single resolution point; **read-only consumer**) | B2 |
| 16 | `daemon/tools/filesystem.py` | **AS BUILT (re-verified 2026-11-09):** `_SCOPE_DENIED_BASENAMES` `:352` · `_is_scope_denied_basename` `:355` · `_trusted_scope_anchor` `:371` · `_resolve_within_scope` `:389-462` · `_scope_filter_candidates` `:465-482` · `_resolve_within_workdir` scope hook `:485`, `:505-511` · `_resolve_search_root` scope branch `:555`, `:595-599` · `list_directory` call `:925` + D-N7 entry filter `:942` · `read_file` call `:1007` · `glob_files` `:1104`, filter `:1119` · `grep_files` `:1670`, filter `:1698` · `CATEGORY_DOC` `:93`, scoped rules `:97-106` (the plan-time `197-225`, `596`, `670` anchors described the pre-B3 two-site shape and are SUPERSEDED) | the opt-in read scope, **all FOUR read tools under ONE policy** (`read_file`, `list_directory`, `glob_files`, `grep_files`): relative-only paths, the **factory-bound trusted anchor** (never the agent-supplied `workdir`, which is refused), strict containment, and the **`.env`-family deny** at any depth; plus the `list_directory` NAME filter (**D-N7**) and the `CATEGORY_DOC` contract (**D-N8**). The factory binding itself is row 17b (`daemon/tools/instance.py`) | B3 |
| 17 | `agents/skill-keeper/meta.json` | `tools` | add `"read_scope": "project"` (and the version bump) | B3 |
| 17b | `daemon/tools/instance.py` | **AS BUILT (re-verified 2026-11-09):** `_make_read_scope_aware` **`:2466`** (the scope-aware factory wrapper; `_scope_refusal` inner helper `:2516`, binding blocks `:2552-2609`); the caller identity it consumes is `get_current_caller_agent_id` **`:3224`** (over `caller_agent_id: str = agent_id or ""` **`:3217`**) and the trusted anchor `get_trusted_workdir` **`:3232`**; the wiring onto all four read tools is **`:6513-6533`**. Pre-B3 plan-time anchors `6342-6343` (per-tool `_make_workdir_aware(...)` lines) and `:3069` (`caller_agent_id`) have both drifted and are SUPERSEDED — at `ae5bbafed` those were the only sites that existed | **NEW row (M2)**: a scope-aware wrapper/closure binding that threads the calling agent's identity into the read resolver, so the declared `read_scope` is READABLE at the enforcement site (without it, the scope is unreadable) | B3 |
| 18 | `daemon/registry.py` | `690`, `756` | (no change — the flag's default is already fail-closed; **read-only**) | F |
| 19 | `daemon/tools/skill_tools.py` | `1260`, `1491` | echo the **full uuid4**, not the 8-hex prefix | E3 |
| 20 | `daemon/tools/skill_tools.py` | `402` | echo the **address** `pf:<ns>:<name>`; keep `pseudo_id` additive | E3 |
| 21 | `daemon/tools/skill_tools.py` | `1213` | extend the refusal to the `pf-<sha16>` pseudo-id form | E3 |
| 22 | `daemon/services/project_skill_source/model.py` | `199-220` | wire `to_summary()` into the list surface (or delete it) | E3 |
| 23 | `daemon/tools/skill_tools.py`, `skill_evolution_tools.py` | `1202-1214`, `242-244`, `480-560` | one shared P7 refusal helper + a verbatim-agreement pin | E3 |
| 24 | `daemon/repositories/skill/repository.py` | `383-396` | (no change — `deactivate()` is the sanctioned writer; **read-only**) | E4 |
| 25 | `daemon/services/skill_stale_reconciliation.py` | **NEW** | the idempotent boot-time reconciliation (rules R1/R2/R3) + the audit receipt | E4 |
| 25b | `daemon/services/skill_deactivation.py` + `daemon/manager.py` | `daemon/services/skill_deactivation.py:126`, `daemon/manager.py:5731` | the `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` brake (default 0/off; off-by-absence; fully inert when off; named Phase-2 lever) | E4 |
| 26 | `daemon/services/skill_injection_service.py` / `skill_search_service.py` | `371-431` (the candidate selection) | exclude R1-shaped rows from `injected`/`low_match` | E4.3 |
| 27 | `scripts/audit/skill_stale_deactivate.py` | **NEW** | the dry-run-first operator script + the `--reactivate` reversal | E4.2 |
| 28 | `daemon/services/skill_capture_observability.py` (or an extension of the existing observability surface) | **NEW** | the D signals + the daemon-side anomaly trigger | D |
| 29 | `daemon/services/skill_job_dispatcher.py` | `683-735` | stamp the run id + prompt source + prompt **shape** into the job metadata | D |
| 30 | `daemon/services/skill_metrics_service.py` | `595`, `610-640` | the refusal counters + the prompt-source stamp | D |
| 31 | `daemon/services/job_processor.py` | `738-844` | the execution-refusal counter (distinct from the admission counter) | D |
| 32 | `daemon/services/anomaly_service.py` | `692-718` | (no change — the auto-trigger **calls** `accept_report`; **read-only consumer**) | D / E1 |
| 33 | `daemon/config.py` (`SkillEvolutionConfig`) | near the `fix_cooldown_seconds` field | the windowed FIX cap — the config fields `fix_burst_cap` / `fix_burst_window_seconds` (**no new `ENSEMBLE_*` flag**; the env surface is the config's existing `env_prefix` alias `SKILL_EVOLUTION_FIX_BURST_CAP` / `SKILL_EVOLUTION_FIX_BURST_WINDOW_SECONDS`, M5-corrected — the earlier "no new env var" wording was false) | E1 |
| 34 | `tests/services/conftest.py` | `39-46` | the fixture isolation (non-shared connection, file-backed/shared-cache DB) | G |
| 35 | `tests/services/test_skill_evolution_service.py` | `1875-1884` (the node) | the **full-file-set** acceptance pins (≥ 3 consecutive full-file-set greens + the experiment-equivalence pin) | G |
| 36 | `.agents/tester/QUARANTINE.md` | rows **45**, **56**, **90** | re-adjudicate → **remove** (or re-scope with evidence) | G |
| 37 | `.agents/tester/PACKS.md` | new rows + extended rows | register every new/extended pack | all |
| 38 | `.agents/shared/planning/skill-system-revival/` | `roster.md` (new), `decisions.md`, `phase1-plan.md`, `phase2-runbook.md` (after implementation) | the planning artifacts | all |
| 39 | `tests/unit/test_skill_evolution_type_dispatch.py` | **NEW** | E2's FIX/DERIVED dispatch pins (M-E2a..d); test-only; CAPTURED refusal unchanged; see §9 E2 | E2 |
| 40 | `test/packs/skill_capture_killswitch_unit_test.sh` | extended (PACKS.md:2993) | register the E2 pins + update the packs' EXPECTED test count | E2 |

**Not touched (explicit):** `scripts/upgrade/**` · `.env` · `config.yaml` · any `agents/<name>/*.md` prompt artifact · any migration `.sql` file · the live/dev DB · the job queue's `is_paused`.

---

## 19. Council findings disposition (F-1..F-27) — **COUNCIL F-IDs RESTORED**

> **The BODY IS AUTHORITATIVE.** §19 and `decisions.md` MIRROR the body exactly. If they ever disagree, **the BODY wins and the ledger is wrong**. Every gated predicate string in this table appears **character-identical** in the body section that implements it — §19.1 is the cross-check, and it is re-verified by grep on every revision.
>
> **F-ID provenance.** The F-IDs below are the **council's ORIGINAL ids** from `plan-review-council.md` (F-1 … F-27). Rev 4 had renumbered them; that silent renumbering is itself a finding (F-27's class) and is **corrected here**. No council finding can be silently de-tracked: an id may not be reused, dropped, or re-pointed without a new row stating the change.

| # | Council finding | Disposition | Body location (verified) |
|---|---|---|---|
| **F-1** | A3: wrong premise, no-op fix, vacuous pins | **FIXED** — predicate repointed to the three-conjunct OUTCOME-FIELDS form `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL` (method `has_genuinely_evolved_for_instance`; the earlier `feedback_applied IS NOT NULL`-only shorthand was SUPERSEDED by LEADER RULING Q1 and is corrected here); acceptance = measured 3,457 → 0; M-A3a neutralizes THAT predicate | §5 A3 (LEADER RULING Q1 block); §18 rows 8-10 |
| **F-2** | E4: R1 unsatisfiable against its own A/B guard | **FIXED** (rev 4) — R1 = refused set, receipt enumerates, R1 verified still active | §9 E4 acceptance 1-3 |
| **F-3** | Anchor misidentification: the walk-tool cites pointed inside write_file/edit_file | **FIXED** — the wrong anchors DELETED; `glob_files` def `:754` → call `:764`, `grep_files` def `:1309` → call `:1322` | §6 B3 scope paragraph; §17 #1 |
| **F-4** | A4: the fix misstates the on-miss insert and would suppress the first legitimate bump | **FIXED** — the on-miss path (`record is None` ⇒ `prev_applied = False`) is stated explicitly; exactly one bump per row | §5 A4 "The leak (code)" block |
| **F-5** | A4: exit criterion is a dangling cross-reference; §16.1 C-2 asserts the old one | **FIXED** — the exit criterion lives in §1 (Definition of done / "A4 exit criterion"); C-2 marked SUPERSEDED | §1; §16.1 C-2 |
| **F-6** | A4: acceptance 3-6 not demonstrable in Phase 1, yet the DoD claims them | **FIXED** — DoD rewritten; Phase-2-undemonstrable items removed | §1 Definition of done |
| **F-7** | A4: the gauge is an unanchored FIND-IT with no change-list row | **FIXED** — acceptance #3 downgraded to an **honest FIND-IT** (no such aggregation exists in `daemon/`; `grep -rn "GROUP BY name HAVING COUNT" daemon/` → 0 hits) + a §18 row added | §5 A4 FIND-IT paragraph; §18 row 11b |
| **F-8** | E1 has no acceptance criteria and no mutation legs (E2 likewise) | **FIXED** — E1 gains an Acceptance block + M-E1a..f; E2 gains an Acceptance block + M-E2a..d | §9 E1; §9 E2 |
| **F-9** | E1: the durable cap cannot count what it claims to count | **FIXED** — cap re-keyed on `metadata->>'skill_id'` within a window (the `idempotency_key` shape is dead by construction: `job_queue_service.py:1596-1608` suffixes with `#<uuid8>`) | §9 E1 "Ruling"; §18 row 13 |
| **F-10** | D: the retention design is undetermined, but the release gate assumes it is decided | **FIXED** — retention **DECIDED**: Phase 1 ships the **structured-log sink** (schema-free, no `_ensure_postgres_columns` risk); the durable-table variant is explicitly a later release | §8 D4 |
| **F-11** | D: the daemon-side deviation detector has no producer or read site | **FIXED** — the producer is the daemon-side detector calling `AnomalyService.accept_report`; the read site is the anomaly row | §8 D3 rule 1; §8 D2 table |
| **F-12** | F: the claim `skill_injection:false` does not prevent serving is wrong for the dynamic lane | **FIXED** — corrected with `instance_messaging.py:4311` and `context_messages.py:2536` | §10 "What the flag actually gates" |
| **F-13** | `rollback_safe` mis-specified; the staging steps omit the required override | **FIXED** — `ENSEMBLE_ROLLBACK_SAFE=1` is INSIDE the staging command line, with the delta-empty rationale; the derivation is `stage.sh:325-351` (exit 78 at `:344`) | §14.2 step 3 |
| **F-14** | PROMPT-BASELINE under-specified: no second tree, no owner, trusts a non-blocking leg | **FIXED** — TESTER-OWNED **BOTH-TREE 181/181**; `stage.sh:671-716` explicitly NON-BLOCKING so it is not relied on | §14.2 step 6 |
| **F-15** | The FE provenance-sidecar gate + the `build_tool` label deviation are missing | **FIXED** — sidecar present-from-SOURCE, absence-from-PAYLOAD asserted correct (`stage.sh:418`), `build_tool` deviation disclosed (`_build_frontend.sh:85` vs `:105`), **not** gated on `"npm run build"` | §14.2 step 7 |
| **F-16** | E4.1 has no wiring site | **FIXED (site LOCATED from the code)** — candidate site `daemon/manager.py:2759`-adjacent (skill-services init block), strictly after `_ensure_postgres_columns()` at `:1034` and `run_pending_migrations()` at `:1017`; the exact hook + process-once suppression are an implementation decision (open item E-7) | §9 E4 acceptance 9; §17 #27; §16.3 E-7 |
| **F-17** | B3: scope (and the write path) ambiguous | **FIXED (re-stated 2026-11-09 to the shipped four-tool shape)** — read scope covers **all FOUR read tools** (`read_file`, `list_directory`, `glob_files`, `grep_files`) under the identical policy (relative-only, factory-bound trusted anchor, agent-supplied `workdir` refused, strict containment, `.env`-family deny), and the **write path is EXPLICITLY OUT OF SCOPE**; the refusal-text cite corrected to `skill_tools.py:1306-1313` (the prior cite was an outcome COMMENT — DELETED; the range shifted +11 lines with the E3 display-hygiene commit `90eeefa22`) | §6 B3 "Scope, corrected (P5)" (**`:310`**) + the Ruling policy bullets (**`:319-322`**) + RECONCILIATION (**`:324`**) + the write-path paragraph (**`:314`**) + the refusal-text paragraph (**`:330`**) |
| **F-18** | E2 has no acceptance criteria and no pack | **FIXED** — E2 gains an Acceptance block, M-E2a..d, and names the EXISTING registered pack `skill_capture_killswitch_unit_test` (PACKS.md:2993) | §9 E2; §13.3 |
| **F-19** | Migration census: method and ownership missing | **FIXED** — TESTER-OWNED, method + the byte-identity premise stated + the TOC fallback named | §14.2 step 5 |
| **F-20** | 4-way sha lacks the independence requirement | **FIXED** — the manifest sha is computed FROM THE COPY (`stage.sh:437`) so the **build-time sidecar** is the independent witness; the tester recomputes independently | §14.2 step 4 |
| **F-21** | C's "exact, by ID" read names a non-existent column | **FIXED** — the wrong `WHERE id = …` read is DELETED; the PK is `queue_id` (`job_queue/models.py:201`); the `job_queue_paused` re-check cite corrected to `:4516`/`:5468` | §7 verification read + C1/C4 rows; §17 #22 |
| **F-22** | D's vocabulary anchor is wrong (the cited range is fence documentation, not the vocabulary) | **FIXED** — the closed vocabulary is `ANOMALY_CATEGORIES` at `daemon/services/metrics_query/wiring/vocab.py:92`, imported at `anomaly_service.py:147` | §8 D3 rule 1; `decisions.md` DS-17 |
| **F-23** | E4 contains a duplicated block | **FIXED** — the duplicate "Corrected target set (P2)" + "HARD RULE" copy is removed | §9 E4 |
| **F-24** | Dangling cross-references in §2 | **FIXED** — N1 now points at §14.4 checklist item 4; N4 at §14.4 checklist item **7** | §2 N1, N4 |
| **F-25** | E3's Acceptance still bundles NOT-from-board items | **FIXED** — the board acceptance covers the **3 real bullets only**; the leader-judgment cleanups are labeled and pinned separately | §9 E3 Acceptance |
| **F-26** | §16.2 T-1 is stale (says the board body "may land later") | **FIXED** — T-1 marked ANSWERED against the fetched body | §16.2 T-1 |
| **F-27** | Process: the plan was edited during the review | **CLOSED (process)** — this table + §19.1 are the traceability artifacts; the F-IDs are the council's, not renumbered | §19 + §19.1 |

**Summary: 27 council findings. Closed: 27. Open: 0.** (F-27 is a review-process note, not a plan defect.)

37. **TIDIER M1 — the ORPHANED dual-column writer is DELETED; the codebase keeps exactly ONE writer of the pair.** *Found by the tidier (M1, duplication); actioned by Dev-A (workstream A, round 6).* `daemon/repositories/skill/repository.py` carried a SECOND dual-column writer — `deactivate_with_receipt` plus its two value-object types `DeactivationResult` and `SkillDeactivationRefused` (the typed `reason = "open_ab_test"` refusal) and its private `_open_ab_test_blocking` helper — alongside the E4 mechanism's sanctioned writer `daemon/services/skill_deactivation.py::write_skill_pair`. **ZERO-CALLER PROOF (repo-wide greps, each symbol separately):** `deactivate_with_receipt` → its own test file + one prose comment (`daemon/routers/skills.py:945`) + the pack's mutation templates; `DeactivationResult` → `daemon/repositories/skill/repository.py` itself + the pack templates; `SkillDeactivationRefused` → its own test file only. **NOTHING in `daemon/` (production) called any of them.** **Action:** the method, both types, the private helper and the now-unused `dataclass` import were DELETED; the pin file `tests/unit/test_skill_dual_column_deactivation.py` (13 nodes) was DELETED with it; the prose comment that named the deleted symbol (`daemon/routers/skills.py`) was rewritten to name the LIVE writer instead. **COVERAGE MAPPING (every invariant the deleted writer pinned, and where it lives now):** the pair-moves-as-a-pair, the A/B refusal and the prior-pair/reversal invariants are covered by the E4 lane's OWN pins (`tests/unit/test_skill_stale_deactivation.py`: the sanctioned writer sets both columns / one repository call / no E4 write path writes one column without the other / a real attempted flip is REFUSED / the receipt enumerates the refusal with the blocking test id / the receipt entry carries the prior pair / reactivate restores the exact prior pair), mutation-proved by THAT lane's pack (`skill_stale_deactivation_unit_test`: `M-E4h` single-column, `M-E4c` canonical-pair reversal); the TWO invariants E4 does NOT cover (no THIRD column moves; a missing row returns `None`) were RE-POINTED onto the LIVE writer in the new `tests/unit/test_skill_dual_column_live_writer.py` (2 nodes), each with its own mutation leg (`M-E4w-d` drops the `status` write inside `SkillRepository.update`; `M-E4w-e` makes a missing row return something other than `None`). **No invariant was lost; nothing was kept without a reason.**
38. **TIDIER M2 — the A4 divergence gauge is WIRED to the counter-drift surface (LEADER RULING: the gauge SURVIVES; it is the live A4 deliverable).** *Found by the tidier (M2, dead code); ruled by the leader (wire it, never delete it); actioned by Dev-A (workstream A, round 6).* `SkillRepository.within_identity_key_divergence` had no production consumer (only its definition + `tests/unit/test_skill_counter_drift_gauge.py`). **FEASIBILITY CHECK FIRST (the dispatch required it before any change):** (i) **session/engine compatibility** — the gauge is pure ORM and runs over the script's own `sqlalchemy.Engine` (probed on a scratch SQLite: 1 divergent key / 2 rows / 3 skills); (ii) **no import cycle**; (iii) **no daemon bootstrap side effect on the other paths** — the import is DEFERRED INTO the reading function, so the dry run, `--apply` and `--revert` never pay it (measured: the daemon-package import is ~2.6 s / ~1.9k modules, which is why it is confined to the read-only `--measure` path); (iv) the F-4 import trap is NOT triggered (no `metrics_query.wiring` in `sys.modules` after the import). **The script did NOT compute the divergence itself** (no duplicate SQL existed), so the resolution was to ADD the gauge reading — DELEGATED, never re-implemented: `scripts/audit/skill_counter_reconcile.py` gained `divergence_reading`, which returns `SkillRepository.within_identity_key_divergence`'s payload verbatim, and `measure_snapshot` now carries it under `divergence`. **Pins:** three new nodes in `tests/unit/test_skill_counter_transition_and_reconcile.py` (the reading carries the gauge; the reading IS the repository gauge — a monkeypatched gauge is followed, and the script carries no `group_by`/`func.count`/`count(distinct` of its own; the payload is the repository payload verbatim), with the mutation leg `M-A4h` (the script stops delegating ⇒ all three RED). **The gauge and its own pin file are untouched.**
39. **TIDIER L3 — the stale `has_applied_for_instance` NARRATIVE is reworded; a narrative must not teach a dead predicate.** *Found by the tidier (L3); actioned by Dev-A (workstream A, round 6).* The capture gate asks the OUTCOME-FIELDS predicate (`has_genuinely_evolved_for_instance`), but the retargeted integration files still narrated the retired feedback-only predicate as if it were the gate. **Reworded:** `tests/integration/test_skill_cross_phase_flow_c.py` (the module docstring's Phase 4/5 bullet now names `has_genuinely_evolved_for_instance` and states the three-conjunct form; the `instance_repo` fixture docstring now names the live predicate) and `tests/integration/test_skill_capture.py` (three comments). **Renamed (the names taught the dead predicate):** `test_skill_already_applied_skips_capture` → `test_gate4_refuses_when_the_outcome_fields_predicate_is_true`; `test_no_capture_when_skill_applied` → `test_no_capture_when_the_skill_genuinely_evolved`; `test_check_and_capture_returns_none_when_skill_already_applied` → `test_check_and_capture_returns_none_when_the_skill_genuinely_evolved`. **KEPT (already unmistakably negative, or an intentional keep the tidier honoured):** the retired predicate's own unit tests, the seam pins that assert the predicate STAYS re-documented, the gate-4 consumer pins and the twin-parity pins (all assert the retired predicate is NEVER consulted), the production comments in `daemon/services/skill_metrics_service.py` and `daemon/services/skill_evolution_service.py` (both already say "NOT the feedback-only …" — read-only this round, out of the fence), and the mutation-leg plant templates in `test/packs/skill_capture_killswitch_unit_test.sh` (their whole point is to plant the retired predicate).
40. **TIDIER M3 — the unused imports in this lane's OWN test files are removed (4 files, 5 names).** *Found by the tidier (M3, file hygiene); actioned by Dev-A (workstream A, round 6).* Removed: `AsyncMock` (`tests/unit/routers/test_skill_patch_dual_column.py` — `MagicMock` stays, it is used), `pytest` (`tests/unit/metrics/test_metrics_capture_active_wiring.py`), `pytest` (`tests/unit/metrics/test_metrics_census_polarity.py`), and `json` + `TestClient` (`tests/unit/metrics/test_metrics_capture_lane_read.py`). **The `# noqa: F401` model imports were NOT touched** (deliberate side-effect imports). **Two corrections to the finding, both verified before acting:** the tidier listed `json` as unused in `tests/unit/metrics/test_metrics_capture_lane_read.py` and that is CORRECT — the only `json` occurrences are `.json()` RESPONSE METHODS, not module use; and `pytest` in that same file IS used (`@pytest.fixture(autouse=True)`, `@pytest.mark.parametrize`) even though the tidier did not list it — a removal attempt was reverted before the commit.
41. **TIDIER L10 — the raw-text anchor: ROUTED to Dev-B (not this lane's file), and this lane's own raw-text audit found ONE true raw-text-anchor pin, which was RE-AIMED at behaviour.** *Found by the tidier (L10); actioned by Dev-A (workstream A, round 6).* **ROUTED (no action here):** `tests/unit/test_skill_keeper_read_scope.py:417` asserts the literal `"current_trusted_workdir()" in anchor_source` — satisfiable by a docstring edit. That file is the KEEPER READ-SCOPE test (Dev-B's lane); the tidier's suggested fix (use the AST walk the neighbouring `:405-414` assertions use) is reported to Dev-B. **THIS LANE'S OWN AUDIT:** every source-reading pin in this lane's pin files was classified. `tests/unit/metrics/test_metrics_census_polarity.py::TestTheLabelsAgreeWithTheDisclosure` was the ONE pin that used a raw string equality as a BEHAVIOUR proxy — it asserted that the string `R-ii` appeared on a comment line and that the disclosure's `R-i:` line mentioned `capture_active`, which a PROSE EDIT alone could satisfy. **RE-AIMED:** the class now asserts WHICH DEFECT each label names BY BEHAVIOUR — the R-ii site (the census-polarity fact) must report the GATED-OFF sense (a closed gate ⇒ `gated` is True; an open gate ⇒ False) and the R-i site (the `capture_active` wiring) must report the LIVE per-read process fact (a flipping resolver is followed per read; an absent or raising resolver degrades to False) — with the label attachment asserted per SITE (the label must ride the source of the site it names, on the same line) instead of a whole-file string search. **THE MUTATION LEG STILL DISCRIMINATES:** `M-A2c` (swap the labels back in `availability.py`) still REDs the re-aimed class (observed in the pack run: `M-A2c: RED as intended` → `TestTheLabelsAgreeWithTheDisclosure::test_availability_labels_the_polarity_r_ii`), and a prose edit can no longer make the class green on its own. **The remaining source-reading pins are NOT behaviour proxies** and were deliberately kept: the gauge's ORM-only / grouping guards and the A1 wiring pins each have BEHAVIOURAL siblings in the same file (and their mutation legs red behavioural nodes), while the docstring pins in `tests/unit/test_skill_capture_attribution_seam.py` (the 3,457 boundary / the 470 honest-negative band / the upper bound) are DOC-CONTENT requirements by nature — the prose IS the thing under test.

42. **TIDIER H4 — `SkillStaleDeactivator.reconcile` is a 356-line method: DISCLOSED as a FAST-FOLLOW, NOT refactored in this lane (LEADER RULING, 2026-11-10: FAST-FOLLOW, DO NOT REFACTOR).** *Found by the tidier (H4, SRP / maintainability ceiling); ruled by the leader.* `daemon/services/skill_deactivation.py::SkillStaleDeactivator.reconcile` (`:874`) owns SIX inline sections in one method — R1 (`:906`), R2 (`:956`), R3 (`:1003`), R5 (`:1080`), the DECLARED disposition (`:1091`) and the APPLY block (`:1165`) — five of them repeating the same four-step template (predicate filter → suppression check → record construction → `_candidate_row` append), as five ~40-line copies that ALREADY diverge (R1/R3 build a `RefusalEntry`, R2/R5 build an `ObservationEntry`; inconsistent `before=` usage), plus the receipt write, the manifest write and the run bookkeeping. **THE VERDICT: a MAINTAINABILITY CEILING, NOT A BEHAVIOR DEFECT** — the tidier itself classed it "recommend only", no pin fails, and the rule semantics are correct. **THE SEAM (named, for the fast-follow):** extract `_apply_r1` / `_apply_r2` / `_apply_r3` / `_apply_r5` / `_apply_declared` (one method per rule block) + a shared `_apply_pass` (the predicate → suppression → record → candidate template the five blocks repeat) + a shared `_record_outcome(...)` (the ONE builder that appends the `RefusalEntry` / `ObservationEntry` / `DeactivationEntry` and the matching `_candidate_row`, so the R1/R3-vs-R2/R5 divergence is collapsed ON PURPOSE rather than by accident). **The rule-block comments already mark the seams** (each block opens with its own banner), so the extraction is mechanical. **THE RATIONALE FOR DEFERRING (behavior-risk before promote):** (i) **the E-6 winner decision touches these rules anyway** — the 27-pair backlog is the Emperor's call (Ruling E) — so a refactor now would be reshaping code that is about to change under it; the extraction lands AFTER E-6, not before; (ii) a 356-line method with five near-identical blocks is exactly where a mechanical extraction introduces a behavior slip (a mis-ordered filter, a lost `before=`, a moved `_candidate_row`), and this lane's red-set accounting is the only net; (iii) the H3 change (persist-before-apply) has just rewritten the APPLY block, so the method's shape has already moved once in this lane. **⇒ RECORDED, NOT DONE.** *(A deferred refactor has no implementation and no gated criterion, so it claims NO §19.1 cross-check row — that table tracks gated criteria only.)*
43. **TIDIER H1 / H3 / M5 / M9 / M11 / M18 / M19 / L7 — this lane's tidier dispositions, all LANDED.** *Found by the tidier (skill-system-revival Phase 1, code-quality pass, reviewed SHA `cfe4480be`); actioned by the coder lane, 2026-11-10.* **H1 (blocking)** — both the manifest module docstring and the deactivator's constructor docstring told the operator to pass `--allow-declared-artifact`, a flag the operator script never registered (the override is the Python-API keyword `allow_declared_artifact=True` only). **REWORDED, DO NOT ADD THE FLAG** (the leader ruling — a CLI escape would contradict the pinned "no winner declared by any shipped call site" property); the script still registers no such option, and a pin asserts that. **H3 (blocking)** — the apply loop flipped every row and only THEN wrote the receipt; the receipt is the ONLY reversal + suppression record (DS-10), so a mid-apply disk failure left flipped rows with NO recorded prior pair. **PERSIST-BEFORE-APPLY** now: the receipt is written first carrying the PLANNED entries (each with its prior `(is_active, status)` pair) and rewritten atomically as each row flips — **no flipped row can exist without a recorded prior pair**; a mid-apply failure is logged at CRITICAL with the run id + every claimed row and its prior pair. **M11** — the three refusal envelopes (a repository class, the pair resolver, the burst cap) were shaped three different ways and two of them named ONE refusal with two literal tokens; the repository class was DELETED by the metrics lane (zero production callers), so the envelope now unifies the TWO live refusals (`PairResolutionRefused`, `SkillFixBurstCapRefused`) on a shared `SkillLaneRefused` base, the REST route READS `e.reason` instead of hard-coding it, and the agent-facing message prefixes are byte-identical. **M18** — two of the burst cap's three degraded branches were SILENT (the two PERMANENT ones were quieter than the transient one); both now write `append_run_record(outcome="cap_degraded")` — the posture stays fail-open for AVAILABILITY, the RECORDING is unconditional (see DS-20's addendum). **M19** — a NEGATIVE `fix_burst_cap` was clamped to 0, and 0 DISABLES the durable cap; the config now rejects a negative cap (`ge=0`) and a non-positive window (`ge=1`) at load, and the documented explicit-0-disable semantics still survive (pinned). **M9** — the anomaly category vocabulary + canonicalisation existed TWICE with DIVERGENT behaviour (the lane's own copy neither lowercased nor applied the synonyms); both services now delegate to ONE import-pure module (`daemon/services/anomaly_vocab.py`) and the M-1b parity pin compares canonicalisation BEHAVIOUR, not just list equality. **M5** — the three new knobs are documented in `.env.example` / `.env.prod.example` (default + unset-by-absence + the empty-value warning), and the plan's "no new env var" claim was CORRECTED (the config's `env_prefix` alias IS an env surface; the E4 brake is a genuinely new env var per DS-54). **L7** — the burst-guard pins wrote their observability rows into the checkout's REAL `data/` tree (gitignored, so invisible to `git status`); the autouse scratch-sink fixture is now in place and the two leaked files are DELETED. **Every pin carries its OWN named mutation, proven RED → GREEN with a sha256-verified restore** (the E4 pack runs 16 mutation legs; the burst-guard / observability / wave-2 pins carry theirs).

44. **ROUND-8 CONTRADICTION RESOLVED AGAINST THE CODE — the ``capture_lane_off`` ADMISSION counter is COMPLETE in production today; the tester gate's §7 "that ONE counter is partial today" was TRUE OF THE WORDING, NOT OF THE CODE.** *Contradiction between the E1/D lane ("live at BOTH production refusal sites") and the tester gate's §7 ("the production ``capture_lane_off`` refusal site in the metrics lane returns before the dispatcher gate, so that ONE counter is partial today; … Phase-2 wires it") — named by the leader as the gate's #2 production risk; resolved by Dev-A (workstream A, round 8) against the CODE.* **THE ENUMERATION (driven from the code, not from prose — the capture kill-switch is READ in exactly four places in ``daemon/``; three refuse, one reads):** (i) ``daemon/services/skill_metrics_service.py:659`` — the METRICS seam: Gate 0 → the recorder fires BEFORE the ``return None`` → **COUNTED**; (ii) ``daemon/tools/skill_evolution_tools.py:522`` — the TOOLS seam: the recorder fires BEFORE the ``skipped`` envelope → **COUNTED**; (iii) ``daemon/services/skill_job_dispatcher.py:1126`` — the DISPATCHER gate: ``record_admission_refusal("capture_lane_off")`` + the sink record, BEFORE the no-op return → **COUNTED**; (iv) ``daemon/services/skill_roster.py:382`` — a READ (the roster REPORTS ``skill_capture_enabled``), it refuses nothing ⇒ not a refusal site. **ALL THREE move the SAME counter key** (``admission_refusals['capture_lane_off']``) — the recorder increments it directly, the dispatcher gate reaches it through ``record_admission_refusal("capture_lane_off")`` ⇒ the counter is COMPLETE. **THE TWIN SEAM IS NOT A FOURTH SITE:** ``SkillEvolutionService.check_and_capture`` has NO capture-enable check at all (its gates are task-success, non-blank message, non-blank agent, the trivial filter, and the A3 evolved-predicate) ⇒ it never refuses *because the capture lane is off* ⇒ there is no uncounted refusal there (the dispatch's open question is DISPROVED, and the fact is now PINNED so it cannot drift back). **⇒ VERDICT: the counter is COMPLETE; the E1/D lane's claim was RIGHT IN SUBSTANCE (its "live at both production refusal sites" understated the count — it is live at THREE), and the tester gate's §7 was RIGHT ABOUT THE MECHANISM ("the metrics lane returns before the dispatcher gate" — true, and irrelevant to completeness) but WRONG ABOUT THE OUTCOME ("that ONE counter is partial today" — it is not) and WRONG ABOUT THE REMEDY ("Phase-2 wires it" — it is already wired, commit ``ae99a5be9``).** **ROOT CAUSE OF THE CONFUSION (stale wording, both lanes):** the tester gate's §7 says "**The lane's own disclosure stands**" — it INHERITED a disclosure instead of re-deriving the call site. Two stale statements existed: (a) THIS lane's comment/docstring in ``daemon/services/skill_metrics_service.py`` (present tense: "the counter is PARTIAL (it moved only for the tools lane)") — **FIXED in this round** (the comment now states the CURRENT truth and marks the gap as historical/closed); (b) the observability module's recorder docstring (``daemon/services/skill_capture_observability.py`` — the E1/D lane's file, READ ONLY here) still says the metrics call site "cannot be edited from the observability lane; this helper is the ready-to-serve counterpart" and cites the stale anchor ``:595-596`` ⇒ **REPORTED for that lane**, with the minimal change named in §19.1's row. **NO code change was needed in any production file** — every site already counted; the work was the ENUMERATION, the disclosure correction and the PINS. **THE PIN (every path, not one):** ``tests/unit/test_skill_capture_lane_off_cross_path.py`` (**8 nodes**) drives ALL THREE refusal paths (the metrics seam, the tools seam, the dispatcher gate — the two latter READ-ONLY, in this lane's test) and asserts the counter moves ON EACH ONE; an AST census pins the SITE SET (a new or moved guard REDs it; a docstring that merely QUOTES a guard does not count); a vacuity control proves each path's assertion depends on the counting (with the counting neutralised in-test, the counter does not move). **Mutation leg `M-C2e`:** remove the metrics seam's recorder call ⇒ ONLY the metrics path's cross-path assertion REDs while the tools seam and the dispatcher gate stay GREEN (the path-sensitivity proof — observed in the pack run: the cross-path metrics node FAILED, the tools/dispatcher nodes did not appear in the FAILED list).
45. **F-10 BOUNDARY FACT (the tester closed the F-10 gap; this lane records the invariant it establishes).** The tester added ``tests/unit/routers/test_skill_patch_dual_column_e2e.py`` (3 nodes — real store + repository through the real PATCH route, persistence read back on a FRESH engine, counter-mutation-proven), commit ``58c8cb655cfc67fd1e73ca72f6de53a8d88c14c7``. **THE BOUNDARY FACT:** the both-columns guarantee lives at the **ROUTER** — ``SkillUpdateRequest`` declares no ``status`` field, so a REST caller CANNOT move one column alone (Pydantic drops a caller-supplied ``status``) — while the **REPOSITORY alone CAN write one column** (its ``update`` sets any existing column it is handed). **That is an invariant statement, not a defect**: the REST surface is the only place a caller controls the pair, and it derives ``status`` from the public ``is_active`` flag; a repository-level single-column write remains possible by design (the repository is a general-purpose writer, and the E4 mechanism's sanctioned writer is the one that keeps the pair). **The evidence anchor:** the real-route e2e pin (the REST caller cannot set one column alone) + the repository's own contract (``daemon/repositories/skill/repository.py`` — ``update`` writes every field it is given in ONE transaction). **This closes the round-6 question** ("can the pair desync?") with the precise answer: **not through the REST surface; yes, by a direct repository call** — which is why the sanctioned writer, not the REST handler, is the pair's single writer.

### 19.1 BODY-LEDGER CROSS-CHECK TABLE (the gate that failed twice — now explicit)

**How to read this table.** For each gated criterion: the **exact predicate/anchor string**, the **body section:line** where it is implemented, the **§19 row**, the **`decisions.md` ruling**, and **IDENTICAL?** — i.e. does the §19/§17/§16/§13.3 text state the SAME string as the body? Every row must be **IDENTICAL = Y**; if a row is **N**, **the BODY is corrected to match** (never the reverse). The line numbers below are the rev-4 line numbers; the STRING is the contract, the line numbers move with edits. **The table carries 44 rows (C-1..C-44), independently measured by extracting the `C-<n>` row ids from the table itself.** A lower figure quoted in an earlier review artifact is an undercount of this ledger and is superseded by the measured 38.

| # | Gated criterion | Exact string (the contract) | Body section:line | §19 row | decisions.md ruling | IDENTICAL? |
|---|---|---|---|---|---|---|
| C-1 | A3 gate predicate | `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL` | §5 A3, LEADER RULING Q1 block + Implementation shape | F-1 | DS-43 | **Y** |
| C-2 | A3 acceptance (DELTA FORM — chosen) | `3,457 (3457) → 0` | §5 A3 Acceptance 1 (measured, post-early-return) | F-1 | DS-43 | **Y** |
| C-3 | A3 mutation | neutralize the feedback-evidence conjunct -> the NULL-feedback rows re-enter -> the measured delta goes from 0 to `3,457 (3457)` -> RED | §5 A3 M-A3a | F-1 | DS-43 | **Y** |
| C-4 | A3 disclosed residual | `NOT BUILT in Phase 1` (the completion-hook `applied` writer) | §5 A3 "DISCLOSED RESIDUAL" + §17 | F-1 | DS-43 | **Y** |
| C-5 | E1 cap key | `metadata->>'skill_id'` within a window | §9 E1 Ruling + §18 row 13 | F-9 | DS-46 | **Y** |
| C-6 | E1 dead-guard proof | `job_queue_service.py:1596-1608` | §9 E1 Ruling | F-9 | DS-46 | **Y** |
| C-7 | E1/E2 packs | `skill_evolution_unit_test` / `skill_capture_killswitch_unit_test` | §9 E1 + §9 E2 + §13.3 | F-8, F-18 | DS-46 | **Y** |
| C-8 | staging override | `ENSEMBLE_ROLLBACK_SAFE=1` | §14.2 step 3 | F-13 | DS-47 | **Y** |
| C-9 | prompt baseline gate | `BOTH-TREE 181/181` | §14.2 step 6 | F-14 | DS-47 | **Y** |
| C-10 | FE sidecar gate | sidecar present-from-source / absent-from-payload (`stage.sh:418`) + `build_tool` deviation (`_build_frontend.sh:85` / `:105`), NOT gated on `"npm run build"` | §14.2 step 7 | F-15 | DS-47 | **Y** |
| C-11 | walk-tool anchors | `glob_files` (def `:754` → `_resolve_search_root` call `:764`) and `grep_files` (def `:1309` → `_resolve_search_root` call `:1322`) — **plus the CORRECTED premise — the "already fenced" reading is WITHDRAWN/REFUTED for a scoped agent** (the fence was anchored at the agent-supplied `workdir`); **all four read tools now carry the SAME scope policy** | §6 B3 scope paragraph + §17 #33 | F-3 | DS-15 | **Y** |
| C-12 | B3 write path | `Write-path scope — EXPLICITLY OUT OF SCOPE` | §6 B3 "Write-path scope" | F-17 | DS-15 | **Y** |
| C-13 | B3 refusal-text cite | `skill_tools.py:1306-1313` | §6 B3 "Refusal-text cite (corrected)" | F-17 | DS-15 | **Y** |
| C-14 | C kill-switch read | `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';` | §7 verification read + C1 | F-21 | DS-41 | **Y** |
| C-15 | C defence cite | `job_queue_service.py:4516` / `:5468` | §7 C4 row | F-21 | DS-41 | **Y** |
| C-16 | A4 gauge home | honest FIND-IT; `grep -rn "GROUP BY name HAVING COUNT" daemon/` -> 0 hits; anchor recorded by the implementer — **BACK-FILLED: `daemon/repositories/skill/repository.py:799-912`, `SkillRepository.within_identity_key_divergence()`** | §5 A4 FIND-IT + Acceptance 3 + §18 row 11b | F-7 | DS-38 | **Y** |
| C-17 | A4 measured truth | within-identity-key divergence `production = 32`; 0 target `BLOCKED-ON` the A/B resolution decision | §1 DoD + A4 exit criterion + §16.1 C-5 + §17 #18 | F-5, F-6 | DS-38, DS-49 | **Y** |
| C-18 | A4 leak fix | transition-based bump; on-miss (`record is None`) => `prev_applied = False` (first legitimate bump NOT suppressed) | §5 A4 "The leak (code)" | F-4 | DS-08 | **Y** |
| C-19 | D vocabulary | `daemon/services/metrics_query/wiring/vocab.py:92` (`ANOMALY_CATEGORIES`), imported at `anomaly_service.py:147` | §8 D3 rule 1 | F-22 | DS-17 | **Y** |
| C-20 | D retention | `structured-log sink` | §8 D4 | F-10 | DS-19 | **Y** |
| C-21 | D detector producer | daemon-side detector -> `AnomalyService.accept_report` (`anomaly_service.py:692-718`) | §8 D3 rule 1 + §8 D2 | F-11 | DS-17 | **Y** |
| C-22 | F flag precision | dynamic lane gated: `instance_messaging.py:4311`, `context_messages.py:2536` | §10 "What the flag actually gates" | F-12 | — | **Y** |
| C-23 | E4.1 wiring site | resolved site `daemon/manager.py:2786` (after `_wire_skill_injection_capability_gate()`, strictly after `_ensure_postgres_columns()` at `:1034`; never inside the ensure list) | §9 E4 acceptance 9 + §17 #27 + §16.3 E-7 | F-16 | DS-51 | **Y** |
| C-24 | E4 acceptance | REFUSES an A/B-guarded row (proven) + receipt enumerates `refused-by-guard` (both ids + test id) + R1 verified still active | §9 E4 acceptance 1-3 | F-2 | DS-44 | **Y** |
| C-25 | E4 pair-resolver | `resolved_at` + `winner_skill_id`; deactivate loser as ONE reversible unit with a receipt; NO winners in Phase 1 | §9 E4 Ruling E + Acceptance 8 | F-2 | DS-44 | **Y** |
| C-26 | F identity dedupe | at most ONE variant per `(name, project_id)`; highest generation, ties by id | §10 "Identity dedupe at the injection boundary" + M-F6 | — | DS-48 | **Y** |
| C-27 | G acceptance | `FULL-FILE-SET` | §11 Acceptance 1 | — | DS-37 | **Y** |
| C-28 | net-new-reds protocol | `grep '^FAILED'` only; `intersection of the three red sets` over >=3 runs; NET-NEW MUST BE 0 | §13.2 | — | DS-26, DS-35 | **Y** |
| C-29 | premise corrections | `P1-P4` | §3 | — | — | **Y** |
| C-30 | I5 REST PATCH dual-column | `daemon/routers/skills.py:931-933` (the pop-and-translate site) / M-I5-a (the mutation leg) | §17 item 31 (numbered list), **:1028** — **NOT** `§17 #31`, which collides with §18 row 31 (`job_processor.py`, the D lane) | — (implementation-found, not a plan item) | DS-53, DS-56 | **Y** |
| C-31 | E4 stale-deactivation brake | `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED` (default 0 / off; absent=off; fully inert when off) | §9 E4 Acceptance 10 | — | DS-54 | **Y** |
| C-32 | E4 R5 desync rule | `R5` (the normalize-only desync rule) | §9 E4.1 | — | DS-55 | **Y** |
| C-33 | B3 rule reconciliation | `RELATIVE paths only` anchored at the `daemon-resolved trusted workdir`; the `.env` family refused at any depth | §6 B3 "Ruling — opt-in, default-OFF, keeper-only" policy bullets, **:319-322**, + the RECONCILIATION paragraph **:324** | — | DS-15 | **Y** |
| C-34 | B1a true property | `zero PROJECT-surface writers; the temp-dir writers are enumerated and pinned by name` | §6 B1a "CORRECTED (authorised §17 amendment)" note, **:274** | — | — | **Y** |
| C-35 | skill_create disposition | `ROUTED RESIDUAL to the Phase-2 master-switch decision` + the F-12 correction (`plan-review-council.md:119` is the INJECTION flag) | §6 B2 "Ruling" bullet 3, **:295** | — | — | **Y** |
| C-36 | E3 display hygiene | full id (not the bare 8-hex) on both echoes; the ADDRESS `pf:<ns>:<name>` on `- id:` with the pseudo-id ADDITIVE | §9 E3 E3.1/E3.2/E3.3 + §17 #32 | — | — | **Y** |
| C-37 | D-N7 listing filter | `_is_scope_denied_basename` in the list_directory entry loop (scoped agents only) | §17 item 33 (numbered list), **:1031** | — | DS-15 | **Y** |
| C-38 | D-N8 category doc | **scoped agents** get relative-only reads; the `workdir` argument is NOT accepted for them; **undeclared agents** keep the legacy `workdir` semantics unchanged | §17 #34 | — | DS-15 | **Y** |
| C-39 | F-4 import-trap residual (STILL OPEN — re-probed 2026-11-10: 15 -> 67, +52, wiring imported) | `daemon/tools/nest_report.py:159` (the attribute import that fires the PEP-562 `__getattr__`) | §17 item 35 (numbered list) | — (implementation-found, not a plan item) | DS-57 + DS-59 | **Y** |
| C-40 | M1 — the orphaned dual-column writer (DELETED) | `daemon/repositories/skill/repository.py` (`deactivate_with_receipt` + `DeactivationResult` + `SkillDeactivationRefused` — zero production callers) | §17 item 37 (numbered list) | — (tidier M1, implementation-found) | DS-58 | **Y** |
| C-41 | M2 — the A4 divergence gauge's consumer (WIRED) | `scripts/audit/skill_counter_reconcile.py` (`divergence_reading` → `SkillRepository.within_identity_key_divergence`) | §17 item 38 (numbered list) | — (tidier M2, implementation-found) | DS-58 | **Y** |
| C-42 | L3 / L10 — the stale narrative + the raw-text label pin | `tests/integration/test_skill_capture.py` + `tests/integration/test_skill_cross_phase_flow_c.py` + `tests/unit/metrics/test_metrics_census_polarity.py` | §17 items 39-41 (numbered list) | — (tidier L3/L10, implementation-found) | DS-58 | **Y** |
| C-43 | the capture-lane-off refusal sites (all three counted) | `daemon/services/skill_metrics_service.py:659` + `daemon/tools/skill_evolution_tools.py:522` + `daemon/services/skill_job_dispatcher.py:1126` | §17 item 44 (numbered list) | — (round-8 contradiction resolution, implementation-found) | DS-60 | **Y** |
| C-44 | the dual-column guarantee boundary (F-10) | `daemon/routers/skills.py` (the REST surface cannot move one column alone) | §17 item 45 (numbered list) | — (tester F-10 closure, commit `58c8cb655`) | DS-60 | **Y** |

**Re-verification protocol (mechanical).** On every revision, run, per row: extract every backticked token from the "exact string" cell, then for EACH token run `grep -nF "<token>" phase1-plan.md` and `grep -nF "<token>" decisions.md`; each token must return >=1 hit in **BOTH** files, and the §19 row's disposition must match the body. A token whose only plan hit is the ledger row itself is **self-referential and proves nothing** — the contract string must also appear on a **body** line. (Stronger form, used by the independent verifier: the tokens of a row should co-occur on at least one body line and on at least one `decisions.md` line — the plan holds that stricter form too.) **A row whose string is absent from the body is OPEN, regardless of what §19 claims.** This is the gate that was missing in rev 3 and rev 4. **The house rule this enforces:** a ledger must never assert more than the body supports, and a gated predicate string must match its body occurrence **character-for-character** — token presence alone is the weaker test, never the acceptance test.

---

*End of Phase 1 plan (rev 4). The decision log is `decisions.md` in the same directory.*
