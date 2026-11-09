# Decisions — agents-ensemble skill-system-revival, Phase 1

**Lane:** plan-authoring (sole author; the plan and this log share one voice)
**Date:** 2026-11-09
**Base sha:** `ae5bbafedde90c7af1065b9660196bbffbbbf9c1` (= `origin/latest`)
**Worktree:** `/home/nea/ensemble-src-wt-skill-revival-phase1`
**Companion:** `phase1-plan.md` (the plan) · `phase0-facts-extract.md` (the fact base) · `skill-keeper-confinement/decisions.md` (prior art, D1–D8)

Each entry: **Context** → **Options considered** → **Ruling** → **Why** → **What would reverse it**.
Anchor rule: every `file:line` was opened in the worktree at the base sha; drifted anchors are corrected and disclosed in `phase1-plan.md` §17.

---

## DS-01 — The Phase-1 constraint envelope

**Context.** The commission's hard rules: do not flip `ENSEMBLE_SKILL_CAPTURE_ENABLED` / `ENSEMBLE_SKILL_EVOLUTION_ENABLED`, do not promote, do not engage the queue brake, do not work in `/home/nea/ensemble-src`. The Phase-0 report's own ladder (§6.3) puts "ARM the brake" at Phase 0 and "REPAIR" at Phase 1 — so the ladder's Phase 0 could be read as being inside this commission.

**Options.** (a) Take the report's phase numbering literally and arm the brake now. (b) Take the commission's hard rules literally: the brake is verified and documented, never engaged.

**Ruling.** (b). The commission's hard rules are the authority; the report's phase numbering is a *recommendations-only* ladder (§6.3 header: *"RECOMMENDATIONS ONLY — NOT YET IMPLEMENTED, NOT SCHEDULED, NO DECISION TAKEN"*). The brake is a **Phase-2 operational action**, recorded in the runbook's Step 0.

**Why.** The brake is *broad* — it carries all parallel-queue traffic for the project, not just skill jobs. Engaging it inside a plan-authoring lane would be an unrequested live-behavior change. It is also **not currently engaged** (DS-16), so "arming" would be a new action, not a restoration of a prior state.

**What would reverse it.** An explicit Emperor instruction that the brake be armed as part of this commission — it is a single UPDATE and the plan already contains the exact command.

---

## DS-02 — `capture_active`: the honest source of truth (resolves Phase-0 open question 20)

**Context.** `daemon/api.py:2571-2576` never passes `capture_active=` to `RegistryMetricsQueryProvider`, so `provider.py:67/75/147` keeps it permanently `False`, and `availability.py:93-95` therefore reports `dark / capture_off` for every wedge-capture-idiom family (F1 publishes 9 live series). The facts extract records open question 20 as *"the intended fact source for `capture_active` is not stated in any artifact"*.

**Options.** (a) `ENSEMBLE_SKILL_CAPTURE_ENABLED` (the skill-capture kill-switch). (b) `ENSEMBLE_WEDGE_CAPTURE_ENABLED` (the wedge-capture switch). (c) the **wedge-capture process fact** — `app.state.wedge_capture_watchdog is not None`. (d) a new dedicated switch.

**Ruling.** (c).

**Why.**
- The field's own contract (`availability.py:61-62`) says *"Whether the **wedge capture** is active."* Option (a) is a **category error** — the skill-capture lane is a different subsystem with a different lifecycle.
- The house already rejected switch-facts for this exact slot: `listener_enabled` is documented as *"Deliberately NOT the switch state … switch-INVARIANT"* (`provider.py:89-96`) and is sourced from `app.state.metrics_listener is not None` (`api.py:2574`, set at `:2481-2486`). Option (b) would repeat the mistake the sibling fact avoids.
- (d) adds an `ENSEMBLE_*` flag without owner authorization — the standing policy the confinement lane explicitly honoured.
- The process fact is the only one that is true **at the moment the door is asked**.

**What would reverse it.** A house ruling that availability facts must be switch-derived (which would contradict the `listener_enabled` precedent), or a change to the `capture_active` field's documented meaning.

---

## DS-03 — `capture_active` must be **late-binding**, not a boot snapshot

**Context.** The provider is constructed at `api.py:2571`; the wedge watchdog is created at `api.py:3236-3258` — **665 lines later**. The fact does not exist at construction time.

**Options.** (a) move the provider construction below the watchdog start. (b) a `Callable[[], bool]` resolver (the house's existing late-binding pattern for `publisher_cadence`, `provider.py:69/:78`). (c) a boot-time snapshot with a refresh thread.

**Ruling.** (b).

**Why.** (a) moves a block that `tests/unit/metrics/test_metrics_query_api_wiring.py:74-94` pins by **source order** (`MetricsListener(` < `CensusSampler(` < `MetricsSampler(`) and would risk the census-publishes-before-sampler invariant (`:89-94`). (c) adds a thread and a staleness class for a fact that is already available synchronously. (b) mirrors an existing, accepted house pattern and keeps the change local to two files.

**What would reverse it.** A pin proving the app-state read is safe on the request path (it is: `app.state` attribute access).

---

## DS-04 — The census polarity is fixed at the **resolution layer**, not the consumer

**Context.** The double inversion: `metrics_census.py:322-335` (`gated` returns the ENABLED sense) → `provider.py:117-122` (`census_enabled = not gated`) → `provider.py:145` (`census_gated = not census_enabled`) → `availability.py:91-92` (F6 dark while publishing 15 live series). `availability.py:83-88` explicitly refuses to fix it in-lane (the two-check-sites invariant).

**Options.** (a) fix `CensusSampler.gated` to `return not self._gate()`. (b) fix the provider to `census_enabled = bool(census.gated)`. (c) fix the availability reason strings.

**Ruling.** (a).

**Why — the decisive argument.** The **existing test suite already encodes the corrected contract**: the `_FakeCensus` stand-in's `gated=True` means *"the census is gated OFF"* (`tests/unit/metrics/test_metrics_query_switch_invariance.py:51`, `:237-242`; `test_metrics_query_provider.py:319-327`). The production class was the only thing out of contract. Option (a) therefore fixes the defect with **zero pin flips**; option (b) would require rewriting every fake and pin in the metrics suite; option (c) only changes the label and leaves the inversion live.

Option (a) also adds **no new check site** — the property still calls the existing `_gate()` — so the behavioural check-site census stays at exactly 2 (`test_metrics_query_switch_invariance.py:189-196`).

**What would reverse it.** A house ruling that `gated` must mean "enabled" — which would make the property's own docstring (`:324`) and every consumer wrong.

---

## DS-05 — The R-i / R-ii label swap is corrected in **code**, and the disclosure is updated in the same change

**Context.** `availability.py:83-88` labels the census-polarity defect **R-i**; `docs/architecture/metrics-promote-disclosure.md:86-95` assigns **R-i = wire `capture_active`** and **R-ii = census polarity**. The code comment is the swapped one. The commission asks for "code and disclosure to agree".

**Options.** (a) correct the code comment to R-ii and update the disclosure to "FIXED". (b) correct the disclosure to match the code. (c) leave both.

**Ruling.** (a).

**Why.** The disclosure is the operator-facing artifact; the code comment is the developer's. The disclosure's assignment (R-i = `capture_active`, R-ii = census polarity) is the one the Phase-0 artifacts and the fix plan were written against, and it is the one that **the five doc-pin tests assert** (`test_metrics_read_registration.py:525`, `test_anomaly_report_tool_registration.py:1013`, `test_metrics_read_docs_surface.py:126`, `test_metrics_read_tools.py:251`, `test_metrics_read_personas.py:359` — they require the tokens `R-i`, `R-ii`, `capture_active`, `census inverted` to be present).

**What would reverse it.** A house decision to rename the ids entirely (in which case all five doc pins move together).

---

## DS-06 — The honest attribution predicate is the three-conjunct OUTCOME-FIELDS form `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL`, not `applied == True` (SUPERSEDED-TITLE NOTE: this entry was written when the predicate was stated as the shorter `feedback_applied IS NOT NULL`; LEADER RULING Q1 — DS-43 — made it the three-conjunct form, which is what A3 actually shipped as `has_genuinely_evolved_for_instance`)

**Context (Ruling A).** `SkillRepository.has_applied_for_instance` (`repository.py:1550-1573`) gates on `feedback_applied == True` and ignores `selected` / `applied` / `task_succeeded`. The council found that `applied` is a **monotonic, one-way projection** of `feedback_applied` (`update_feedback` mirrors only the positive direction; `update_completion` never sets `applied` at all — the completion-hook writer is **missing**). Repointing the gate onto `applied` would be a ≤4-row no-op with no mutation. **Ruling A: repoint the gate onto `feedback_applied IS NOT NULL`** — the field ACTUALLY POPULATED and INFORMATIVE (census: NULL=3,547, TRUE=6,091, FALSE=470; false-eligible window `selected AND task_succeeded AND feedback_applied IS NULL` = **3,457 (3457)** rows). Requiring `feedback_applied IS NOT NULL` alongside the existing outcome fields moves 3,457 rows — a REAL change. The missing completion-hook `applied` writer becomes a **DISCLOSED ROUTED RESIDUAL** (new behavior, not Phase-1 work) — name it explicitly; do NOT build it.

**Options.** (a) `∃ row with feedback_applied == True` (today). (b) `∃ row with selected == True`. (c) `∃ row with applied == True` (the outcome field). (d) `∃ row with applied == True AND task_succeeded == True`. (e) `∃ row with selected == True AND task_succeeded == True`.

**Ruling (Ruling A).** Repoint the gate onto `feedback_applied IS NOT NULL` **as one conjunct of the three-conjunct OUTCOME-FIELDS predicate `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL`** (LEADER RULING Q1 / DS-43; shipped method `has_genuinely_evolved_for_instance`). Do NOT build the missing completion-hook `applied` writer — it is **NOT BUILT in Phase 1** (it is new write behavior in a "make metrics honest FIRST" phase). A3 acceptance = false-eligible count **3,457 (3457) -> 0 post-fix, measured, not asserted**. M-A3a rewritten with a real mutation: neutralize the `feedback_applied IS NOT NULL` predicate -> count returns to 3,457 (3457) -> pin RED. If the mutation does not move the count, the pin is vacuous — DELETE it, do not ship. Delete any acceptance criterion that is already true (the council flagged acceptance #2 as proving nothing).

**Why.** The model contract (`models.py:333-350`): `selected` = *offered*; `applied` = *actually loaded into the context*; `task_succeeded` = *the task* succeeded; `feedback_applied` = *the agent volunteered feedback saying it applied*. The gate's purpose is *"don't create a sibling capture for a success already attributed to another skill"*. **The predicate is `feedback_applied IS NOT NULL`** because that is the field that is ACTUALLY POPULATED and INFORMATIVE (census: NULL=3,547, TRUE=6,091, FALSE=470; the false-eligible window `selected AND task_succeeded AND feedback_applied IS NULL` = **3,457 (3457)** rows). `applied == True` is **not** a usable predicate: its only writer is the one-way latch at `repository.py:1308-1318` (inside `update_feedback`), and `update_completion` (`repository.py:1339-1436`) never sets it at all — so `applied` carries the census's 3,547 NULL-window rows as `applied=False`, and gating on it would leave the window untouched.

Option (b)/(e) are over-broad (they would refuse capture whenever any skill was merely *offered*). Option (c) (`applied == True`) is **REJECTED as the predicate**: it is a monotonic projection of `feedback_applied` written by nobody outside the latch, so it cannot move the 3,457-row window. Option (d) is defensible but couples the gate to task outcome, which the capture flow already requires separately (`skill_evolution_service.py:1619-1620`) — it is adopted only if the census shows non-succeeded rows are a material share of the false-eligible population.

**What would reverse it.** A later release that adds the **completion-hook `applied` writer** (`update_completion` at `repository.py:1339-1436` setting `applied`, as `models.py:311-315` declares) — then an `applied`-based predicate becomes meaningful and the gate could be re-evaluated against it with a real mutation. Until then, `feedback_applied IS NOT NULL` is the predicate.

---

## DS-07 — `has_applied_for_instance` is **not** mutated in place

**Context.** The obvious fix is to change the method's predicate. It has two known consumers and a public name that other code may call.

**Options.** (a) change the predicate in place. (b) add a sibling method and repoint the two gate call sites. (c) rename the method.

**Ruling.** (b).

**Why.** (a) silently changes the meaning of a public repository method under a name that now lies ("has_applied" would mean something else), for an unknown set of external consumers. (c) breaks any external caller outright. (b) makes the gate's contract explicit at the call site, keeps the feedback-based predicate available for whatever legitimately wants it, and lets the old method be marked *"feedback-based — NOT a capture-gate predicate"* rather than deleted.

**What would reverse it.** A census of all callers showing `has_applied_for_instance` has **only** the two gate consumers — in which case (a) becomes safe and (b) becomes over-engineering.

---

## DS-08 — The `total_applied` leak is fixed by making the bump **transition-based**

**Context.** `skill_metrics_service.py:1170-1186` bumps `total_applied` on **every** `record_feedback(applied=True)` call for a `(skill, instance)` pair, including repeats on a row that is already applied. The ledger counts **rows**; the counter counts **calls**. The drift is monotonic and never shrinks: +423 → +479 → +480 over 5 days, while the ledger grew +421.

**Options.** (a) transition-based bump (bump only when the row's `applied` flips False→True). (b) de-duplicate by counting rows at read time (make the metric derived). (c) a periodic counter rebuild.

**Ruling.** (a).

**Why.** (a) fixes the **cause** at the only place the counter is written; the service already holds the row before the update (`record = get_latest_for_skill_instance(...)` at `:1027-1029`), so `prev_applied` is free. The on-miss insert path (`:1030-1111`) inserts `applied=False` then updates ⇒ the transition holds ⇒ exactly one bump per row. (b) changes the *shape* of every consumer of `total_applied` (the trigger engine's denominator, the skill stats) — a far larger blast radius for the same effect. (c) is a mitigation, not a fix, and it re-introduces the drift between rebuilds.

**What would reverse it.** A census showing the drift is dominated by a *different* mechanism (e.g. counters bumped for deleted skills) — then (a) is necessary but not sufficient, and the reconciliation (DS-09/DS-10) carries the rest.

---

## DS-09 — "Converge to 0" = stop the leak **+** an audited reconciliation **+** a two-point proof

**Context.** The commission requires the counter drift to *"CONVERGE TO 0 mismatches, not merely shrink"*. The already-accumulated +480 is historical; a code fix alone cannot remove it.

**Options.** (a) code fix only, and report the drift shrinking. (b) code fix + one-time reconciliation + convergence proof. (c) reconcile only.

**Ruling.** (b).

**Why.** (a) fails the commission's explicit criterion. (c) leaves the leak live and the drift re-accrues. (b) is the only reading of "converge" that is honest: the leak stops, the residue is repaired with an audit trail, and the result is *proved* by two measurements ≥ 24 h apart (a single measurement cannot distinguish "converged" from "has not yet re-drifted").

**What would reverse it.** A house ruling that the counter is a best-effort telemetry field that need not equal the ledger — in which case the per-skill mismatch metric becomes a *disclosed known gap* and this workstream shrinks to the leak fix alone.

---

## DS-10 — The E4 reconciliation is **not** a `.sql` migration

**Context.** The commission asks which vehicle E4's deactivation should use, given that *"the house migration runner is SQLite-only and fresh-SQLite boot is blocked by a pre-existing dup-prefix (20260714_000001)"*.

**Options.** (a) a new `daemon/migrations/versions/*.sql` file. (b) a boot-time/idempotent reconciliation in code. (c) an operator-run DB update with a recorded receipt.

**Ruling.** (a) is **rejected**; **both (b) and (c) are adopted**, with a division of labour.

**Why — (a) is provably the wrong vehicle.**
1. The runner is a **documented no-op on PostgreSQL** (`daemon/migrations/runner.py:837-842`: *"the runner is intentionally a NO-OP for non-SQLite engines"*; PG schema evolution rides `_ensure_postgres_columns`). The live DB is PostgreSQL ⇒ **a `.sql` file never reaches it**.
2. On SQLite, a fresh boot **dies at `20260714_000001`** — `20260714_000001_widen_job_queue_type_constraint.sql:35` runs `ALTER TABLE job_queues DROP CONSTRAINT IF EXISTS …`, which SQLite does not support (the file's own "DUAL-DRIVER NOTES" at `:24-27` claim otherwise — **that claim is false**). Since `20260714_000001` sorts before any new file, a new migration is **never reached** either.
3. ⇒ Both roads lead nowhere. (This also corrects the commission's "dup-prefix" framing: there is **one** file with that prefix and the duplicate guard passes; the defect is SQLite-incompatible DDL.)

(b) ships with the release and self-heals the live DB after the promote; it is idempotent by construction (a rule, not a list) and writes a receipt. (c) is the house's operator-script precedent (`scripts/remediate_pause_report_orphans.py` — dry-run by default, `--apply` to commit, printed receipt; also `scripts/migrate_agent_id.py`, `scripts/migrate_memory_to_rag.py`) and is the operator's **verification** and **reversal** vehicle.

**What would reverse it.** A house decision to migrate the runner to PostgreSQL (which would make (a) viable) — or a change making the boot-time reconciliation unacceptably risky, in which case (c) alone becomes the vehicle and the "self-heal" property is lost.

---

## DS-11 — E4's target set is **rule-based**, never an id list

**Context.** The commission's id list is partly wrong (P2): `76e3e1a7a` exists nowhere; `08a4332f` + `fd1e01dc` are two ACTIVE rows of one never-resolved A/B lineage; `9e79ccad` was never enumerated by Phase 0.

**Options.** (a) deactivate the commission's four ids. (b) deactivate the two Phase-0-verified ids plus the 6 captured-origin rows. (c) deactivate by **rule**, with ids as the census's output only.

**Ruling.** (c), with a hard rule: **no row is deactivated until it is first identified by query against the live DB.**

**Why.** (a) would deactivate a row that does not exist and miss the real shadowing pair. (b) still hard-codes ids that drift (the 6 captured-origin ids are **not recoverable** from the Phase-0 artifacts — aggregate only). A rule survives id drift, is idempotent, and produces the same answer on the dev and live DBs. The "identify first" rule is what keeps a typo'd id from silently deactivating an unrelated skill.

**What would reverse it.** A house ruling that the registry must be curated by explicit id (a curation-ledger model) — in which case the rule becomes a *candidate generator* feeding a human-curated list.

---

## DS-12 — The three "unwired" confinement pieces: their TRUE status is stated, and each is reconciled explicitly

**Context.** The commission calls `filesystem_scope`, the master switch, and direction quarantine *"landed-but-unwired"*. The prior art says otherwise (P3): `filesystem_scope` is **NOT BUILT** (D8 res.9), the master switch is **DEFERRED** (D7/HANDOVER P3), direction quarantine is **DEFERRED** with a recorded reason (D6). The commission forbids silently re-opening a deferred ruling.

**Options.** (a) build all three. (b) honor all three deferrals. (c) honor the deferrals' *reasons* while building the load-bearing slice of each.

**Ruling.** (c) — and the plan states the true status of each piece before any proposal.

**Why.**
- **`filesystem_scope`** → build the **opt-in, default-OFF** slice (DS-15): the deferral's blocker was the *fleet-wide default* semantics and the backward-compat policy. An opt-in slice with **no other agent's behavior changed** has no backward-compat exposure, so the deferral's reason is not re-opened — it is narrowed. The fleet-wide default stays deferred with the Emperor's Phase-2 go/no-go.
- **Master switch** → **still deferred** (DS-13). Its one load-bearing behavior — gating the inline mutation path — is delivered at the lane gate where it belongs (DS-14); the switch's *UX* (one lever for three lanes) remains an Emperor decision.
- **Direction quarantine** → **still deferred**; D6's reason stands and is not overturned. A regex content-scan would be a false-safety half-measure, and the approval authority does not exist. The *observable* half ships under D.

**What would reverse it.** An Emperor ruling that Phase 1 must build the master switch or the quarantine's approval surface (both are design commissions, not Phase-1 work).

---

## DS-13 — The master switch stays deferred

**Context.** One switch covering capture + analysis + evolution would be the natural Phase-2 lever, and D8 item 3 routes the inline `skill_evolve` gap into it.

**Options.** (a) build the master switch in Phase 1. (b) keep it deferred; implement the inline gate at the lane gate instead.

**Ruling.** (b).

**Why.** The master switch is a *UX/operations* construct; the thing it was supposed to protect against (an ungated mutation path) is a **control** and belongs at the control's own chokepoint. Building the switch would also mean a **third** env surface and a resolution-order question (capture vs evolution vs analysis precedence) that no artifact has settled. Implementing the inline gate at the lane gate closes the hole with **no new switch** and no resolution-order invention.

**What would reverse it.** A Phase-2 requirement for a single lever, or a house ruling that analysis must also be master-gated.

---

## DS-14 — The inline `skill_evolve` path is gated at the lane gate

**Context.** `daemon/tools/skill_evolution_tools.py:230-252` calls `SkillEvolutionService.evolve_skill(...)` inline with **no lane check**; the keeper's allow-list includes `skill-evolution` (`agents/skill-keeper/meta.json`), so a running keeper can mutate a skill **while both kill-switches are OFF** — the live state. D1 records this as deliberately not gated; D8 item 3 defers it to the (deferred) master switch.

**Options.** (a) leave it open (honor D1's "NOT gated (deliberately)"). (b) gate it with the **same** accessor the job-level path uses. (c) build the master switch and gate through it.

**Ruling.** (b): consult `manager.is_skill_evolution_lane_enabled()` (`daemon/manager.py:7992-8004` — the *single resolution point* already used by the dispatcher, the metric-scan fallback, and the job processor's execution-time check) and return the honest "no job exists" refusal envelope.

**Why.** (c) is ruled out by DS-13. (a) leaves the **only reachable mutation path** open with no shipped control owning it — a hole that D1 disclosed and D8 deferred to something that does not exist. (b) closes it with **one** call, **no new env var**, and **no change to the existing resolution order**. It is safe in Phase 1: the lane has been OFF since 2026-10-22, no live traffic is served by the inline path, and the change moves behavior only **toward refusal**. `skill_analyze` (Tier-2) stays ungated per D1's deliberate carve-out.

**What would reverse it.** A house ruling that the inline path is a sanctioned operator escape hatch (it is not: it is agent-reachable).

---

## DS-15 — The read scope is **opt-in**, default OFF, keeper-only in Phase 1

**Context.** D8 residual 9 owns "per-agent read scoping (`filesystem_scope`)" as a NOT-BUILT, fleet-wide filesystem-layer feature needing its own commission (config shape, default semantics, backward-compat policy, MCP/remote-hands interaction).

**Options.** (a) build the fleet-wide feature. (b) build an opt-in per-agent scope, default OFF, applied to skill-keeper in Phase 1. (c) leave it open.

**Ruling.** (b).

**Why.** (a) re-opens the deferral and risks the whole fleet for one agent's residual. (c) leaves the keeper able to read the repo `.env`, `~/.ssh/*` and `/proc/self/environ` — the credential-hazard class D3 explicitly records as *"residual (read side)"*. (b) closes that residual for the one agent that needs it **with zero exposure for anyone else** (absent ⇒ byte-identical behavior), and it does so on a **declared, inspectable** surface (`agents/<name>/meta.json`).

Scope correction (P5): the open call sites are `read_file` (`filesystem.py:670`) and `list_directory` (`:596`) — **not** all four read tools. `glob_files` (def `:754` → call `:764`) and `grep_files` (def `:1309` → call `:1322`) already resolve through `_resolve_search_root` (`:252-283`), which refuses an absolute root outside the workdir/temp dirs (the 2026-09-23 RAM-walk fix). The plan cites the corrected scope.

**Anchors (re-verified; the plan's earlier walk-tool cites were WRONG — they pointed inside `write_file` (def `:1107`) and `edit_file` (def `:1510`) — and are DELETED).** `glob_files` def `daemon/tools/filesystem.py:754` -> `_resolve_search_root` call `:764`; `grep_files` def `:1309` -> `_resolve_search_root` call `:1322`; the two OPEN read call sites are `read_file` `:670` and `list_directory` `:596`. **Write-path scope — EXPLICITLY OUT OF SCOPE** (the keeper holds no write-capable tool; a write-path fence would defend a surface the agent cannot reach). The refusal-text cite is `daemon/tools/skill_tools.py:1290-1300` (the *"NO skill-keeper job was created"* envelope); the plan's earlier `:1233-1242` cite is an outcome COMMENT and is DELETED.

**What would reverse it.** A house decision to make the scope default-ON fleet-wide (which would then require the full default-semantics design D8 res.9 lists) — that is the Emperor's Phase-2 decision, recorded as open item E-3.

---

## DS-16 — The brake is **verified**, not engaged — and its current state is **not** "armed"

**Context.** The commission names queue `bd075f0b` as the instant-OFF lever and forbids engaging it. The Phase-0 facts' §6.3 lists "Phase 0 — ARM" as a recommendation. A standing project critical note claims *"system_parallel_queue PAUSED until confinement fix promoted"*.

**Options.** (a) treat the brake as already armed. (b) verify and record its actual state; arm it as a Phase-2 step.

**Ruling.** (b) — the brake lever is `job_queues.queue_id = 'bd075f0b…' → is_paused`, and the name simply does not contain "skill". The exact verification read (the `job_queues` PK is `queue_id`, `daemon/repositories/job_queue/models.py:201`) is `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';`; the project-level start-job re-check that backs it is `job_queue_service.py:4516` / `:5468`. And the plan discloses that the critical note is **stale**: `bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2` has `is_paused=false`, `updated_at=2026-10-26T21:50:04.439103+00:00` — the Oct-22 containment pause was **lifted on 2026-10-26**; `findings/lane1-runtime-db.md:46` records **0 of 132** `job_queues` rows paused in `ensemble_prod`.

**Why.** (a) would put a false premise into the Phase-2 runbook — the most dangerous kind of error in a kill-switch document ("the brake is armed" when it is not). The runbook must **arm and verify** the brake as Step 0, not assume it.

Also recorded: the brake is **broad** (all parallel-queue traffic for the project — 25 `skill_capture` + 114 `skill_evolution` rows live on it) and its effect is bounded by the poll interval (`job_processor.py:929` re-reads `is_paused` every iteration; `JobProcessor.poll_interval` default 30.0 s at `:118`).

**What would reverse it.** A live re-read showing the queue paused — in which case the plan's "verify" step becomes a "confirm still engaged" step and the runbook keeps its Step 0.

---

## DS-17 — The keeper-deviation detector **announces itself** from the daemon side

**Context.** The Oct-22 deviation was invisible: the keeper force-resolved a live A/B experiment and began a direct-SQL landing; nothing announced it (report §4.5). The commission asks for "an anomaly hook (report_anomaly) so a misbehaving keeper announces itself".

**Options.** (a) rely on the agent to call `report_anomaly` (the keeper already holds `nest-report`). (b) a daemon-side detector that writes the anomaly row automatically. (c) both.

**Ruling.** (c), with (b) as the load-bearing half.

**Why.** The Oct-22 keeper did **not** announce itself — that is the whole reason the incident needed a forensic reconstruction from a code comment. An agent-hoped hook would have failed exactly as it failed then. The daemon-side detector reads the tool-call record and writes through the existing `AnomalyService.accept_report` (`daemon/services/anomaly_service.py:692-718`; the closed vocabulary is `ANOMALY_CATEGORIES` at `daemon/services/metrics_query/wiring/vocab.py:92`, imported at `daemon/services/anomaly_service.py:147` — the range the plan cited earlier in the anomaly service is FENCE documentation, not the vocabulary; the `other` reserved fold target; `queue-scheduling` / `tool-failure` are the honest categories for a keeper deviation).

**D4 retention — DECIDED (council F-10: the release gate must not assume an undetermined design).** Phase 1 ships the **structured-log sink** (one structured record per capture run under the daemon's data dir, retention >= 30 days) — schema-free, additive, restart-free on the read side. The durable capture-lane **table** variant is a LATER release (it needs a retention sweep + a query surface, and a new table would mean a `.sql` file plus an idempotent `_ensure_postgres_columns` statement in a one-transaction ensure list). The release gate therefore gates on the **sink's presence + the written retention policy**, not on a table.

**What would reverse it.** A house ruling that anomalies may only be agent-authored (it would make the anomaly surface unauthenticated-by-construction and lose the detector).

---

## DS-18 — New observability fields carry a **shape**, never raw prompt text

**Context.** The capture path already carries `task_message` (the P0 gate's subject) into `task_details`. The F7 finding (2026-11-05) recorded a live key written into 4 scratch files at mode 644 for ~25 minutes, with the ruling *"capture a redacted block (name + value sha256 prefix), never the raw value"*.

**Options.** (a) store the raw prompt in the new observability fields. (b) store a shape descriptor (length bucket + presence flags + a sha256 prefix).

**Ruling.** (b).

**Why.** The observability goal is *"was the prompt non-empty and what did it look like"*, not *"re-read the user's words"*. A shape answers the question the Oct-22 investigation needed (the blank-`task_message` gate) without creating a second copy of sensitive content in a new place — which is precisely how the F7 exposure happened.

**What would reverse it.** A ruling that the capture transcript must be recoverable for audit (then it needs a retention-scoped, access-controlled store — a different design, not a field).

---

## DS-19 — Admission-refusal and execution-refusal are **two** signals, never merged

**Context.** The house rule: refused **admission** writes **no row**; refused **execution** of a queued row writes a **terminal** row (`terminal_reason='lane_disabled'`).

**Options.** (a) one "refusals" counter. (b) two distinct signals with distinct names and distinct read commands.

**Ruling.** (b).

**Why.** A single merged number is unreadable: "3 refusals" could mean 3 attempts that never produced a row (admission) or 3 rows that were killed mid-flight (execution) — completely different operational stories. The Phase-2 go/no-go criteria depend on telling them apart (an execution refusal means a row was **already queued** before the flip — a signal that the flip's ordering was wrong).

**What would reverse it.** Nothing — this is a house rule, not a preference.

---

## DS-20 — The re-fix burst guard is **durable**, **capped**, and **human-visible**, with no new table

**Context.** The Oct-21 burst: skill `20f916b3` re-FIXed 3× in 37.1 min. D2 already ships an idempotency key + a 1800 s per-`(project, skill)` cooldown — and the 1800 s window **exceeds both observed gaps** (1373 s, 851 s), so the shipped control already blocks that burst. What is missing is visibility, durability (the cooldown is RAM-only and drains on restart) and a hard cap (a sliding window still permits a slow trickle).

**Options.** (a) add a new durable ledger table. (b) use the **existing** `job_queue_items` rows as the ledger, counting FIX admissions per **`metadata->>'skill_id'`** within a window. (c) key the count on `idempotency_key` — **REJECTED (dead guard)**: `daemon/services/job_queue_service.py:1596-1608` suffixes a re-admission with `#<uuid8>` under the partial UNIQUE index `idx_job_idempotency` (`daemon/repositories/job_queue/models.py:300-306`), so a job-keyed count matches at most one row and the cap can **never** trip. (d) raise the RAM cooldown.

**Ruling.** (b).

**Why.** (a) would need a new table ⇒ the house's `.sql`-file rule **plus** an idempotent statement in `_ensure_postgres_columns` — and the PG ensure list is **one transaction** (any entry's failure aborts boot), so a schema change here is a boot-risk for a rate-limiter. (b) needs **one count keyed on the SKILL IDENTITY** — `count_fix_admissions_since(skill_id, since_iso)` over `job_queue_items`, filtering `job_type='skill_evolution'` AND `metadata->>'skill_id' = :skill_id` AND `metadata->>'evolution_type'='FIX'` AND `created_at >= :since`; the census confirmed `metadata` carries `skill_id` and `direction` on all 116 evolution job rows — and it survives restarts for free. (c) is the **REJECTED dead guard** above. (d) does not fix visibility or durability and does not stop a slow trickle.

The human-visible signal reuses the **same** daemon-side anomaly mechanism as D (DS-17) — one mechanism, two callers — and the `skill_fix` refusal envelope must say *"refused: burst cap"* rather than the generic lane-off text.

**What would reverse it.** A house ruling that the `job_queue_items` table is not an acceptable audit substrate (e.g. a retention policy that prunes job rows) — then (a) becomes necessary.

---

## DS-21 — **G before E1**, and the two changes must not interfere

**Context.** The addendum requires an explicit order for the two changes that touch `skill_evolution_service.py`: E1's guard (the FIX re-entry path) and G's fix (the A/B-resolution concurrency path).

**Options.** (a) E1 first. (b) G first. (c) either, with a non-interference proof.

**Ruling.** (b) **G first**, plus a mandatory non-interference proof.

**Why.** G removes a shared-connection transaction hazard in the file E1 touches; doing E1 first would place a new read adjacent to an unfixed race. E1's guard reads **committed `job_queue_items` rows only** — it never touches the skill tables and never shares the A/B transaction — so it is **immune to the hazard by construction**. The tester's gate must run the AB-resolution node **with E1's guard present** to prove no interference. G's fix must additionally **not** serialize the fan-out: a lock would **mask** the defect rather than fix it, and would hide what the burst guard is meant to expose.

**What would reverse it.** If E1's guard were found to require a read inside the A/B transaction (it must not), the order would flip.

---

## DS-22 — No fabricated capture→evolution diversity

**Context.** 116/116 `skill_evolution` rows are `evolution_type='FIX'`; the CAPTURED/DERIVED path has never been exercised.

**Options.** (a) demonstrate diversity with synthetic rows. (b) make the path reachable and prove it at the unit/pack level only; state the gap in writing everywhere.

**Ruling.** (b).

**Why.** The commission explicitly says *"Do NOT fabricate diversity"*. A synthetic row in the live DB would be a false signal in exactly the ledger that is meant to track whether the capture lane works. The honest deliverable is a **reachable, unit-proven** path plus a written statement that the first live CAPTURED row is the Phase-2 proof.

**What would reverse it.** Nothing. This is a house honesty rule.

---

## DS-23 — The display-hygiene fix echoes **verbatim-consumable** ids, and refuses **both** file-skill id forms

**Context.** Board `wi-d340f16c`'s body has **not landed** (only title-level mentions exist in the repo) — so the commission's summary is the working text, marked to-confirm. The four concrete defects: an 8-hex token echo at `skill_tools.py:1260` and `:1491`, a `pf-` pseudo-id echo at `:402`, and a `skill_fix` refusal gap at `:1213`.

**Options.** (a) fix only what the board text says (unavailable). (b) fix the four defects on the commission's summary, using the **wi-76d89e11** contract already shipped as the governing rule.

**Ruling.** (b). Specifically: echo the **full uuid4** (not the 8-hex prefix) and the **`pf:<ns>:<name>` address** (not the sha16 pseudo-id); extend the `skill_fix` refusal to the **`pf-<sha16>` pseudo-id form** as well as the `pf:` address form; make the P7 refusal text identical across the three sites via one shared helper; wire or delete `to_summary()`.

**Why.** The governing rule already exists and is registered: the wi-76d89e11 contract — *"every agent-facing skill id the skill tools EMIT must be an id `skill_view` accepts VERBATIM"* (`PACKS.md:900`). The 8-hex prefix and the sha16 pseudo-id are **not** `skill_view`-consumable, so both are contract violations, not cosmetics. The `skill_fix` gap is real: `:1213` refuses only `ADDRESS_PREFIX` (`"pf:"`), while `skill_feedback` handles the pseudo-id form separately (`:540-560`) — so `skill_fix` accepts a form it cannot honour.

`to_summary()` (`project_skill_source/model.py:199-220`) has **zero callers** — a dead surface. It is **wired** rather than deleted because the model's own docstring says it exists *"for list surfaces"*.

**What would reverse it.** The fetched `wi-d340f16c` body contradicting the summary — the implementer re-runs the grep at implementation start and reconciles; a superset is acceptable.

---

## DS-24 — E4 deactivates by **rule**, and the injection path refuses R1-shaped rows independently

**Context.** The commission requires the stale set retired *"before anything can inject it"*, while F's payload is staged and the release (and hence the boot-time reconciliation) only lands at the promote.

**Options.** (a) rely solely on the boot-time reconciliation (timing-dependent). (b) rely solely on the injection-side filter (the stale rows remain active in the registry). (c) both, with an explicit ordering.

**Ruling.** (c): the boot-time reconciliation (E4.1) + the operator script (E4.2) + an **injection-side filter** (E4.3) that excludes R1-shaped rows (two active rows sharing a name in an unresolved A/B group) from `injected` and `low_match`.

**Why.** (a) has a timing hole — between the promote and the reconciliation's completion, a stale row could be served. (b) leaves the registry lying (the shadowing pair stays "active"), which is the P13 hygiene defect. (c) makes the **safety property** independent of the **timing** (E4.3 holds even if E4.1 never ran) while still converging the registry (E4.1) and giving the operator a verification + reversal path (E4.2). The commission's "before F can inject it" is then satisfied *structurally*, not by sequencing luck.

**What would reverse it.** A house ruling that the injection path must not carry defense-in-depth filters (it would move the whole burden onto E4.1's timing).

---

## DS-25 — F: recompute the roster from `origin/latest`; flip **no** agent in Phase 1; the manifest is **per-axis**

**Context.** The commission's 16/24 split is stale (P1): at `origin/latest` it is 15 true / 10 false out of 25 declaring, and 31 of 46 agents cannot capture. Also: `skill_injection` gates **automatic capture** and (at the call site) injection — but **not** the manual `skill_execute_capture` tool (`skill_evolution_tools.py:392-500` has no `skill_injection` check, only the kill-switch at `:442`), and `skill_injection: false` does **not** stop a skill from being **served** (open question 39 / P13: an inactive skill was served to a live `coder` agent, and `coder` is `skill_injection: false`).

**Options.** (a) flip the intended agents to `true` now. (b) publish the recomputed roster, ship the change as a **staged payload** armed only when the capture lane is on, and keep the manifest per-axis.

**Ruling.** (b).

**Why.** (a) is a behavioral change with a blast radius that only materialises when Phase 2 flips the capture switch — flipping it in Phase 1 would make the roster change invisible at the moment it matters and untestable. (b) keeps the roster change **co-located with the switch that activates it**, and the per-axis manifest (*capture-enabled* vs *injection-enabled*) surfaces the semantic confusion the commission's one-line premise hides.

**What would reverse it.** An Emperor ruling to arm specific agents in Phase 1 (the manifest already names them, so the change is a one-line edit).

---

## DS-26 — The net-new-reds protocol is a **red-set diff over ≥ 3 runs**, not a single run (Addendum-2 correction)

**Context.** The base leg is **NOT stable**: run 1 = 4F/455P, run 2 = 3F/456P, run 3 = 4F/455P (the AB node fires in 2 of 3). A single-run base measurement yields a 3-red OR a 4-red base set **purely by chance**. The earlier "3× stable at 3/456" reading came from a batch where the flake happened not to fire. There is a **second artifact defect**: pytest `--tb=short` prints the same migration-failure line in **both** the traceback and the summary, so it appears 3× per run (×9 across 3 runs); a naive `grep -E '^(FAILED|ERROR)'` counts it as a 4th red.

**Options.** (a) single-run base comparison. (b) ≥ 3 runs per leg, diff **red sets**, plus a named quarantine list. (c) let the tester author its own classifier.

**Ruling.** (b), plus a **classifier single-source rule**. The TRUE base red set is the **intersection** across the runs (the 3 reds that appear in every run). The classifier is `grep '^FAILED'` **only** — never `^(FAILED|ERROR)`, never raw-count — and the TRUE base red set is the **intersection of the three red sets** (the 3 reds that appear in every run). The rule goes in the plan **verbatim** (§13.2) and the tester inherits it; it must not author its own.

**Why.** (a) mis-classifies a flake as net-new (or hides a real red behind a lucky run). (c) is the corruption vector: the duplicate-line artifact would re-introduce a phantom 4th red and silently inflate every net-new-reds verdict. The intersection definition + the single-source classifier together make the math **reproducible** rather than interpretive.

**The rule (verbatim):** `NET-NEW = (head red set, classified with \`grep '^FAILED'\`, ≥3 runs, diffed as SETS) MINUS (base red set of 3) MINUS (named quarantine list).` **NET-NEW MUST BE 0.** Base-proven debt is **DISCLOSED, never hidden.**

**Evidence pack.** Raw of record (outside the repo): `/home/nea/skill-revival-base-evidence-20261109-0813Z/`. Committed copy (in the **feature** worktree only — never the base worktree): `.agents/tester/RESULTS/2026-11-09-skill-revival-base-evidence/`. Rationale: this project has **already lost evidence to `/tmp` sweeps** (Oct-22 keeper transcripts: 0 rows in instances/checkpoints/messages/tasks; `/tmp/skillfix` gone). Evidence living only outside the repo can be swept again. The pack also carries the interpreter provenance (live binaries, venvs, repo pins, the manifest gap), the 3.13-vs-3.14 differential leg, and the two staged-artifact interpreter `grep` commands (§14.2.9). Reference the paths only — do not re-create or duplicate the pack. The base worktree must stay clean (a dirty base is a broken comparison).

**What would reverse it.** Nothing — this is the house's measurement standard, now made exact.

---

## DS-27 — The interpreter is disclosed by **measurement** for this release, not by a code change

**Context.** The interpreter is an **unrecorded variable** in every staged release: the manifest heredoc (`scripts/upgrade/stage.sh:585-604`) and the provenance sidecar (`_provenance_write`, `scripts/upgrade/lib.sh:3225`) are **both shell-side**, and the daemon **never reads the sidecar** (0 hits for `build-provenance` across `daemon/`). There is no daemon-side release-composition record or table.

**Options.** (a) add a daemon-side release-composition record in this release. (b) disclose by measurement in this release; fix the record in a later one.

**Ruling.** (b).

**Why.** The promote is driven by the **currently-live daemon binary** — a daemon-side fix shipped in this release could only take effect from the release **after** it. So (a) would deliver nothing for this release while adding a schema/table change (⇒ the `.sql`-file rule + the PG ensure-list transaction risk) to the very release that is supposed to be low-risk. The measurement is cheap and reproducible:
`grep -aoE 'libpython3\.[0-9]+' <staged>/ensemble-prod | sort -u` → `libpython3.14` ·
`grep -aoE 'cpython-3[0-9]+' <staged>/ensemble-prod | sort -u` → `cpython-314`,
compared against live v0.29.7's same measurement, disclosed in **three** places (tester evidence, staged-release composition metadata, project history).

**What would reverse it.** A house decision that the interpreter must be **recorded** (not merely disclosed) before the next promote — that would force (a) into a later release, with the schema cost accepted.

---

## DS-28 — The promote runs from the **scripts-pin** worktree, and Phase 1 touches nothing under `scripts/upgrade/**`

**Context.** The promote executes the ceremony code from the **CWD**. The canonical dir is arm-**ineligible** on three independent counts: (a) `scripts_dir_provenance_verdict()` (`daemon/tools/upgrade_tools.py:3988`, called at `:3725`) requires `PIPELINE_SCRIPTS_FLOOR_COMMIT` (`:3923` = `2147c0298a09a5d7c5a0caeea04cdc51ed1ebcaa`) to be a merge-base ancestor of the executing scripts dir's HEAD — it is not, there; (b) the working-tree stamp is `953603108f…` ≠ floor, and at HEAD there is **no stamp**; (c) the dirty 6-entry staged index would execute uncommitted ceremony code.

**Options.** (a) promote from the feature worktree. (b) promote from the scripts-pin worktree `@ 84df6b55c`. (c) modify `scripts/upgrade/**` as part of Phase 1 and promote from the feature worktree.

**Ruling.** (b), and **no workstream touches `scripts/upgrade/**`** (the plan lists the touched files exhaustively; `scripts/upgrade/**` is on the "not touched" list).

**Why.** (a) executes modified ceremony code if the feature worktree's scripts differ from the pin — and the pin is precisely the frozen, floor-compliant toolchain. (c) is the trap the addendum names: it would make the promote run **uncommitted/unpinned** ceremony code, which the arm-time guard exists to prevent. Because no workstream needs `scripts/upgrade/**`, the scripts-pin sync consequence does not arise for this release.

**What would reverse it.** A workstream that strictly requires a `scripts/upgrade/**` change — then the scripts-pin worktree must be **synced** with that change before the promote, or the promote executes stale ceremony code. The plan names this explicitly so it cannot be forgotten.

---

## DS-29 — G's fix is **fixture-level**; a production change is the fallback and a lock is forbidden

**Context.** The proven root cause is a **test-infrastructure** defect: `tests/services/conftest.py:42-46` builds `sqlite:///:memory:` with `check_same_thread=False` and `poolclass=StaticPool` — **one shared DBAPI connection** — while `skill_evolution_service.py:1448-1457`/`:1474-1483` fans the resolve-path side effects out concurrently via `asyncio.to_thread`. The decisive experiment (normal = 10P/2F; `to_thread` monkey-patched inline = 12P/0F) proves the thread hop over the shared connection is the mechanism.

**Options.** (a) fixture-level isolation (the concurrent fan-out gets its own session/connection). (b) production-level session separation in the fan-out. (c) serialize the fan-out with a lock.

**Ruling.** (a); (b) only if (a) is impossible; **(c) is forbidden**.

**Why.** The defect is in the harness; production runs on PostgreSQL, where each connection is a distinct session, so the production concurrency is correct. (c) would **mask** the defect — and DS-21 requires that G not hide what E1's burst guard is meant to expose. (a) has a sharp edge the plan calls out: `:memory:` + `NullPool` gives each connection a **different database**, so the isolated fixture must be **file-backed** (or a shared-cache URI) to keep the data shared. (b), if it is ever needed, must be a **separate session per operation**, not a lock.

**What would reverse it.** Evidence that the race is reachable against PostgreSQL too (then (b) becomes the fix, and the production change carries its own blast radius).

---

## DS-30 — G should let QUARANTINE rows 45 / 56 / 90 be **removed**

**Context.** Rows 45 (2026-09-23), 56 (2026-08-30) and 90 (2026-08-30) all cover the same AB-resolution family, and each records a *hypothesis* diagnosis ("shared-state/timing", "cross-thread session shape", "fixture isolation for TestCheckABTestResolution"). Row 45 already names the durable fix (*"fixture isolation for TestCheckABTestResolution"*); row 90 flags a *"signature-drift follow-up open"*. The measured flake rate is **context-dependent**: **15% ISOLATED** (7/46 runs) vs **38% IN FULL-FILE-SET CONTEXT** (3/8 runs). The decisive experiment: normal **11P/1F** vs inline-monkey-patch **12P/0F** (same tree, same interpreter 3.14.7, same command, **ONLY the thread hop differs**). The mechanism is now **explained**, not merely hypothesised.

**Options.** (a) keep the rows (the fix is a test-side change). (b) re-adjudicate and remove them once the mechanism is proven and the node is stable.

**Ruling.** (b) — with the stated fallback: if a node still flakes for a *different* reason after the fix, that row is **re-scoped with the new evidence**, never silently kept.

**Why.** The measured evidence has done exactly what those rows were waiting for: it converted the hypothesis into an **explained mechanism** with a decisive A/B (the thread hop is the discriminator) and a named durable fix. Leaving a proven-mechanism row quarantined trains the gate to ignore quarantine, which is the house's stated failure mode. The removal is also the *proof* that the fix worked, not a side effect of it. The acceptance is **context-sensitive**: G's node must be green in **≥ 3 consecutive FULL-FILE-SET runs**, not merely in isolation — an isolated green would have passed at 15% by luck.

**What would reverse it.** The tester's re-adjudication finding a residual cause — then the row is re-scoped with the new evidence (recorded, dated, and attributed), which is the same standard the other rows meet.

---

## DS-53 — I5: the REST PATCH must write BOTH columns (an implementation-found mirror desync; NOT a plan item)

**Context.** The E1/D lane found, while implementing E4, that the REST PATCH handler
(`daemon/routers/skills.py:931-933`) translated the public `is_active` flag by **popping** it and writing **only**
`status` — `fields["status"] = "active" if fields.pop("is_active") else "inactive"`. The row's `is_active` COLUMN
therefore never moved, so every `PUT /api/skills/{id}` carrying `is_active` MANUFACTURED `(is_active=True,
status='inactive')` — the **mirror** of the production desync row `(is_active=False, status='ab_testing')` that E4
reconciles. Fixing E4 alone would not have closed it: the desync was reachable through a second door.

**Options.** (a) reuse `SkillRepository.deactivate_with_receipt` (the E4 dual-column writer, commit `5e7834dd4`) at
the route. (b) inline the both-columns translation at the handler and keep the REST contract exactly as it is.
(c) leave the route alone and reconcile downstream.

**Ruling.** (b) — INLINE the translation (`fields["is_active"] = value` kept, `fields["status"]` derived), one
`store.update_skill` call carrying both columns.

**Why.** (a) is a SEMANTIC CHANGE dressed as a fix: the E4 writer REFUSES on an open A/B test, so wiring it into an
existing public REST path would turn a today-200 into a refusal for every guarded skill — new refusals on a shipped
surface, which the leader explicitly fenced. (c) leaves a live producer of the desync class, which is exactly the
bug E4 exists to repair. (b) fixes the desync without touching what the endpoint accepts OR refuses: the same
`is_active` field is accepted, `status` was never an accepted body field, and no new error path is introduced.

**Pin.** `tests/unit/routers/test_skill_patch_dual_column.py` (9 nodes) asserts BOTH columns are forwarded in ONE
call for the `false` and `true` directions, that a metadata-only PATCH touches neither, and that the REST contract is
unchanged (a row guarded by an open A/B test is STILL updatable — the assertion that proves the E4 writer was not
reused). The pre-existing pin `tests/unit/routers/test_skills.py::TestUpdateSkill::
test_update_translates_is_active_to_BOTH_columns` used to assert `assert "is_active" not in kwargs` — it **pinned the
defect** and was CORRECTED, not loosened. Mutation leg **M-I5-a** restores the pop-and-translate shape ⇒ 4 REST pins
RED (the status-only translation still passes, so the pin catches the DESYNC rather than the translation).

**What would reverse it.** A decision that the REST PATCH SHOULD refuse on an open A/B test (which would make (a)
viable) — that is a product decision on a public endpoint, not an implementation choice.

---

## DS-31 — Every new pin ships with a mutation leg **and** a registered-pack row

**Context.** The house rule: *"a pinned condition whose pins live in no registered pack cannot be gate-verified, and a conditionally-default-ON feature must ship default-OFF if that is not met"* — plus the v0.28.6 lane's F-1 artifact-regeneration trap class (*"a pin that stays green with the guard removed is vacuous and worthless"*).

**Options.** (a) a new standalone pack per workstream. (b) extend the existing registered packs where the surface already has one. (c) unregistered ad-hoc test files.

**Ruling.** (a)+(b), with (c) rejected outright. Seven new packs and five existing packs extended; every entry gets a `.agents/tester/PACKS.md` row (name · invocation · description · mutation matrix · date · verdict) and every pin a named mutation leg proven RED→GREEN with a sha-verified restore.

**Why.** Extending an existing registered pack (`skill_id_verbatim_contract` for E3's id contract, `skill_evolution_lane_gate` for B2, `context_skills_unit_test` for F, `skill_evolution_unit_test` for G, `skill_services_gzip_regression_unit_test` for the row-90 node) keeps the corpus coherent — and the B2 half is now **VERIFIED WIRED** (2026-11-09, commit `c26b8ea10`): `skill_id_verbatim_contract` carries `LANE_GATE_FILE="tests/unit/test_skill_evolution_lane_gate.py"` and runs it in phase 1 plus four mutation legs, so the approver's "no pack currently runs it" observation is **STALE** and reuses the proven phase-1/phase-2 template at `PACKS.md:900`. A pack per workstream keeps each gate leg under the 5-minute cap.

**What would reverse it.** A pack-size or runtime constraint discovered at implementation time (then a pack is split, not dropped).

---

## DS-32 — The release ships **no** `.sql` migration and **no** prompt-artifact change

**Context.** The house release standard requires a migration census and a PROMPT-BASELINE check. F touches `agents/<name>/meta.json` (not the prompt artifacts); E4 adds no table and therefore no `.sql` file.

**Options.** (a) add a `.sql` file for E4's audit receipt. (b) ship with a **no-diff** migration census and an unchanged prompt baseline.

**Ruling.** (b).

**Why.** E4's receipt is a **runtime artifact**, not a schema change (DS-10 rejects the `.sql` vehicle outright). And because F's roster change touches `meta.json` — not `soul.md`/`rule.md`/`workflow.md`/… — the prompt baseline stays at **181** and the generator `--check` exits 0 with **no** re-stamp. A no-diff migration census is a **strong** release-gate item precisely because it is free to verify.

**What would reverse it.** A workstream that genuinely needs a schema change (then the house rule requires its own `.sql` file **plus** an idempotent `_ensure_postgres_columns` statement — and the PG ensure list's single-transaction semantics must be respected).

---

## DS-33 — The version slot is `v0.29.8`, re-derived at staging time

**Context.** Live is v0.29.7; `v0.29.5` and `v0.29.7` are already staged/tagged; the project metadata's `next_free_version_slot` (from the v0.29.0 ceremony) reads `v0.29.1`, superseded by later lanes.

**Options.** (a) pin `v0.29.8` in the plan. (b) leave the slot to the implementer.

**Ruling.** (a), with the implementer re-deriving and recording the actual slot at staging time (open item T-3).

**Why.** A plan that names the slot is executable; a plan that leaves it open is a decision deferred to the executor. Re-deriving at staging time protects against a slot taken by a parallel lane between plan approval and staging.

**What would reverse it.** A parallel lane claiming `v0.29.8` — the implementer takes the next free slot and records it.

---

## DS-34 — The A/B backlog (27 unresolved, 10 at `comparisons=0`) is a **Phase-2 precondition**, not a Phase-1 deliverable

**Context.** P4 of the Phase-0 preconditions: *"Unblock or clean the A/B backlog"* before evolution re-enables. The backlog is 27 unresolved groups, 10 at `comparisons=0`; 15 `skill_evolution` rows carry "skill already in active A/B testing".

**Options.** (a) disposition all 27 in Phase 1. (b) disposition only what Phase 1 touches (E4's R1 pair) and make the rest a Phase-2 precondition.

**Ruling.** (b).

**Why.** Dispositioning 27 A/B groups is an **operational** act (each needs a winner determination against real comparison data) and most of them have no data at all (`comparisons=0`) — they can only be **closed or discarded**, which is a decision with product consequences that belongs to the Emperor, not to a code lane. Phase 1 touches exactly one instance (the `regression-ab-partition-run` pair, E4's R1 rule), and the plan makes the rest an explicit, named Phase-2 precondition in the runbook (§15.7).

**What would reverse it.** An Emperor instruction to disposition the backlog in Phase 1 — the machinery (the forced-resolve guard at `skill_evolution_service.py:1381-1388`, already live) is in place for it.

---

## DS-35 — The red-list classifier is a **pinned single-source rule**

**Context.** pytest `--tb=short` prints the same migration-failure line in **both** the traceback and the summary, so it appears 3× per run (×9 across 3 runs). A naive `grep -E '^(FAILED|ERROR)'` over raw pytest output counts it as a **4th red**, corrupting every net-new-reds verdict.

**Options.** (a) let each lane write its own classifier. (b) a single pinned house rule: `grep '^FAILED'` only.

**Ruling.** (b). The rule is stated **verbatim** in the plan §13.2 and goes into the tester-gate section verbatim. The tester **inherits** the classifier; it does not author its own.

**Why.** (a) is the corruption vector: the duplicate-line artifact would re-introduce a phantom 4th red and silently inflate every verdict. A single source of truth is what makes the math **reproducible** rather than interpretive. This is now a **pinned house critical note**.

**What would reverse it.** Nothing — a classifier is either shared or it is not trustworthy.

---

## DS-36 — The evidence pack lives in **two** places (raw + committed copy)

**Context.** The base leg's raw evidence lives at `/home/nea/skill-revival-base-evidence-20261109-0813Z/` (outside the repo). This project has **already lost evidence to `/tmp` sweeps** — the Oct-22 keeper transcripts were swept (0 rows in instances/checkpoints/messages/tasks; `/tmp/skillfix` gone). Evidence living only outside the repo can be swept again.

**Options.** (a) keep the evidence outside the repo only. (b) copy it into the feature worktree only. (c) dual location — raw outside + committed copy inside.

**Ruling.** (c).

**Why.** (a) loses the evidence on the next sweep. (b) loses the raw provenance (the outside copy is the "raw of record"). (c) gives both: the raw of record survives outside, and the committed copy (` .agents/tester/RESULTS/2026-11-09-skill-revival-base-evidence/` in the **feature** worktree) survives any sweep. The copy goes in the **feature** worktree only — the **base** worktree must stay clean (it is the comparison reference; a dirty base is a broken comparison).

**What would reverse it.** A house ruling that the committed copy is unnecessary — then the outside copy must be mirrored to a durable store, which is out of Phase 1's scope.

---

## DS-37 — G's acceptance is **full-file-set**, not isolated

**Context.** The flake rate is **context-dependent**: **15% ISOLATED** (7/46 runs) vs **38% IN FULL-FILE-SET CONTEXT** (3/8 runs). An isolated green would have passed at 15% **by luck**.

**Options.** (a) accept on isolated greens. (b) require ≥ 3 consecutive **full-file-set** greens.

**Ruling.** (b).

**Why.** (a) is a **1-in-7 chance of a false green** on every run — exactly the kind of acceptance that trains the gate to ignore flakiness. (b) makes the acceptance context match the context in which the flake actually fires (38%), so a green run is a real signal and not a lucky draw. The sibling `test_ab_resolution_force_resolve` (row 56) must be green in the same runs.

**What would reverse it.** Evidence that the full-file-set context itself introduces the flake (in which case the isolation fix must be re-scoped to the context, not the node).

---

## DS-38 — A4's gauge is **redefined**, not "converged to 0"

**Context.** The census derived the two gauges exactly: "48/628 vs 43/529" = `names-with-diverging-content / total skills`; "+423 → +480" = rows inside all duplicated-name groups. The problem: `skills.name` is **intentionally non-unique** — every project gets its own `bank_clone` generation-0 row, so one name spans many `project_id`s by design (`code-fix` = 9 rows across 7 distinct `project_id`s). Global name divergence is **unsatisfiable** without destroying the per-project clone model (85 names have >1 row; 48 names have >1 distinct content over 630 rows).

**Options.** (a) keep the global-name gauge and try to reach 0. (b) redefine the gauge to within-identity-key divergence `(name, project_id)` (and/or within a single lineage family). (c) drop the gauge entirely.

**Ruling.** (b).

**Why.** (a) is **model-destroying** — name-level dedup would collapse every project's clone into one row. (c) loses the only signal that catches a genuine within-project content divergence (the real defect). (b) makes the gauge measure what its name claims: divergence within one identity key. Within-identity-key divergence is **0 on dev** (15 rows, 15 identity keys) but **production = 32** (27 stranded A/B pairs) — the 0 target is **BLOCKED-ON the A/B resolution decision (Ruling E)**. The "fix" is the redefinition + the measured baseline (32) + a pin that keeps it from silently regressing, not a data repair in Phase 1.

**The gauge's home — an honest FIND-IT (council F-7).** No duplicate-name / content-divergence aggregation exists in `daemon/` today: `grep -rn "GROUP BY name HAVING COUNT" daemon/` → **0 hits**; the only `HAVING COUNT` sites are unrelated (`daemon/manager.py:8609`/`:8662`, `daemon/repositories/anomaly/repository.py:708`, `daemon/services/maintenance.py:1477`, `daemon/checkpoint_adapter.py:1898`). ⇒ acceptance #3 is **downgraded to the honest form**: the implementer CHOOSES the home, anchors it with `file:line`, and records the anchor in the PR. **Acceptance is the anchor, not a claim that the gauge pre-existed.** **ANCHOR BACK-FILLED 2026-11-09:** the home is `daemon/repositories/skill/repository.py:799-912` — `SkillRepository.within_identity_key_divergence()`, in the per-skill aggregation area and deliberately OUTSIDE the A3 gate region.

**What would reverse it.** A house ruling that global-name uniqueness is actually the desired property (it is not — the per-project clone model is load-bearing) — in which case (a) becomes the fix and the clone model must be re-designed.

---

## DS-39 — E4's lever is a **dual-column atomicity** concern, re-confirming the operator-script vehicle

**Context.** The census answered the lever: `skills.is_active` (bool) + `skills.status` (varchar), plus `skills.lineage_origin` (bank_clone=407, imported=157, evolved=60, captured=6). Census: `(True,'active')=538 · (True,'ab_testing')=52 · (False,'inactive')=39 · (False,'ab_testing')=1` — the last group is the **desync proof**. Any deactivation MUST set **both** (`is_active=false, status='inactive'`); setting one leaves a half-dead row. **No tombstone column exists.** `SkillRepository.deactivate` (`repository.py:383-396`) is the sanctioned writer and sets both. 52 ACTIVE rows carry `status='ab_testing'` — deactivating those strands open A/B tests (guard: `skill_ab_tests.resolved_at IS NULL`). The 6 captured-origin skills are **ALREADY `(False,'inactive')`** — so "retire the 6" is a no-op re-affirmation, not substantive work. 1,846 usage records land on INACTIVE skills (+60 since 11-04) — a **selection defect**, routed to E4 (injection-side defence) + a follow-up; not in A's scope.

**Options.** (a) keep the (b)+(c) mechanism from DS-10. (b) switch to a `.sql` migration now that the lever is known. (c) build a master reconciliation in the daemon's lifecycle.

**Ruling.** (a) stands — and is **re-confirmed** by the census. The lever is a **transactional multi-column update** that must also be **reversible** and **idempotent across boots**. A `.sql` file cannot express that (and the runner is a no-op on PG anyway). The operator-script vehicle (dry-run by default, `--apply`, `--reactivate`) + the boot-time reconciliation + the injection-side defence is the right shape.

**What would reverse it.** A house decision to add a tombstone column (`deleted_at`) to `skills` — then the lever becomes a single-column write and the audit trail becomes schema-native (a later release).

---

## DS-40 — E2's "116/116 are FIX" premise is **unverifiable at column level**

**Context.** There is **no `skill_evolution` table**. The 116 rows are **JOB rows** (108 done on queue `bd075f0b`, 7 done on `cd02c5d2`, 1 dead on `bd075f0b`). No row carries an action/verdict key in metadata (`metadata->>'action'` = none for 116/116; sample = `{skill_id, direction}`), and `skill_lineage` (60 rows = the 60 `'evolved'` skills) has **no verdict column**. ⇒ "116/116 are FIX" is **job-message-text inference, not a DB fact**.

**Options.** (a) claim the diversity gap is closed by measuring FIX rows. (b) state plainly that the premise is unverifiable, make the path ready, do not fabricate diversity. (c) add a verdict column to `skill_lineage` in Phase 1.

**Ruling.** (b). (c) is a schema change that needs its own `.sql` file + `_ensure_postgres_columns` statement — out of Phase 1's scope, and it would only start producing data after the first post-promote run anyway.

**What would make the premise verifiable:** a verdict column on `skill_evolution` (or `skill_lineage`) populated by the keeper at execution time. That is a later-release schema change; Phase 1 does not pretend to have measured what it cannot.

**What would reverse it.** The keeper being instrumented to write a verdict column (a later release) — then the premise becomes a measurement, not an inference.

---

## DS-41 — Workstream C resolves by **ID**, not by name LIKE

**Context.** The census reports "queue `bd075f0b` has no matching `job_queues` NAME (none `ILIKE '%skill%'`)". That is a **filter artifact** — the filter was `name ILIKE '%skill%'` and the queue is almost certainly named **`system_parallel_queue`** (critical note `ed6da139` records *"system_parallel_queue PAUSED"* AND *"Queue bd075f0b is_paused=true since 19:54:26Z (verified in DB)"*). The brake lever is `job_queues.queue_id = 'bd075f0b…' → is_paused` (the `job_queues` PK is `queue_id`, `daemon/repositories/job_queue/models.py:201`); the name simply does not contain "skill".

**Options.** (a) carry a "the queue row may not exist" uncertainty into the runbook. (b) resolve by ID and state the likely name.

**Ruling.** (b).

**Why.** (a) would make the primary kill-switch look unusable — the single most dangerous thing a runbook can do. (b) is the honest reading: the census's filter was wrong for this question, and the critical note supplies the name. C's documented verification read is `SELECT queue_id, queue_name, is_paused, updated_at FROM job_queues WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2';` (the `job_queues` PK is `queue_id`, `daemon/repositories/job_queue/models.py:201` — the `id` column does not exist, so the plan's earlier read would have ERRORED at runtime).

**What would reverse it.** A live SELECT returning no row for that id — then the lever is genuinely missing and the runbook must be re-written around the project-level `job_queue_paused` flag (level 1) instead.

---

## DS-42 — E3's real board scope is **3 bullets**, with the rest labeled NOT-from-board

**Context.** The real board item `wi-d340f16c` (status todo, priority 2, body_len 1037) contains **EXACTLY THREE** bullets: the two 8-hex echoes (`:1260`, `:1491`) and the `pf-` pseudo-id echo (`:402`). The commission's summary added three more (`:1213` refusal gap, `to_summary()` dead surface, P7 citation re-derive) that are **ABSENT** from the real item. The item also states the echoes are **DISPLAY-ONLY** (*"skill_view consumption is NOT affected"*) and that **none block the promote**.

**Options.** (a) plan all six as board-mandated. (b) plan the three real bullets as board-mandated and carry the other three as leader-judgment cleanups labeled NOT-from-board (or drop them).

**Ruling.** (b).

**Why.** (a) would attribute work to a board item that does not contain it — a provenance error in the release notes and the PM ledger. The full-uuid/address echo treatment still applies to the 3 real bullets; the `:1213` refusal-shape change, `to_summary()` wiring, and P7 citation re-derive are carried as **leader-judgment cleanups, clearly labeled NOT-from-board**, with their own pins (M-E3d/e/f) marked as such.

**What would reverse it.** The fetched `wi-d340f16c` body contradicting the census (a superset is acceptable; a different three is a re-plan).

---

## DS-43 — Ruling A: A3 repoints onto the three-conjunct OUTCOME-FIELDS predicate `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL` (method `has_genuinely_evolved_for_instance`; not `applied`)

**Context.** The council found that `applied` is a monotonic, one-way projection of `feedback_applied` (`update_feedback` mirrors only the positive direction; `update_completion` never sets `applied` at all — the completion-hook writer is **missing**). Repointing the gate onto `applied` is a ≤4-row no-op whose pin has no mutation. The council's recommended alternative (build the missing completion-hook `applied` writer) is **NOT Phase-1 work** — new write behavior in a "make metrics honest FIRST" phase.

**Ruling.** A3's gate repoints onto the **three-conjunct outcome-fields predicate: `selected = TRUE AND task_succeeded = TRUE AND feedback_applied IS NOT NULL`** (framing (a) — the LEADER's authoritative wording; supersedes the approver's three options). The false-eligible population is measured by the DELTA form — **"records counted as genuinely-evolved MINUS records with feedback evidence = 0"** — and the measured delta is **3,457 (3457) → 0**, measured on the post-early-return population. `feedback_applied IS NOT NULL` alone is NOT the gate (the approver proved a bare flip is a ≤4-row no-op against the 3,457-row window); the **outcome fields** are what move the window. `feedback_applied` is the field ACTUALLY POPULATED and INFORMATIVE (census: NULL=3,547, TRUE=6,091, FALSE=470); the 470 `feedback_applied = FALSE` rows are a **SEPARATE DISCLOSED BAND** (honest negatives — feedback evaluated, not applied; excluded from genuinely-evolved but NOT false-eligible; reported separately, never conflated). The missing completion-hook `applied` writer stays a **DISCLOSED RESIDUAL, NOT built** (new write behavior in a "make metrics honest FIRST" phase).

**What would reverse it.** A later release that adds the completion-hook `applied` writer — then the gate could be re-evaluated against `applied` with a real mutation.

---

## DS-44 — Ruling B: E4's R1 is "excluded by guard, enumerated and untouched"

**Context.** The council found a logically unsatisfiable triad: R1 is DEFINED as unresolved-A/B rows; the guard SKIPS unresolved groups; acceptance #2 requires R1 EMPTY — unprovable by construction.

**Ruling.** R1 is NOT a deactivation target. It is a **REFUSED SET**. R1 = the 2 ACTIVE arms (`08a4332f` gen1, `fd1e01dc` gen2) of unresolved test `083d5a58`. They stay ACTIVE. Acceptance: (i) the mechanism REFUSES an A/B-guarded row and the refusal is PROVEN (a real attempted flip → refused, with the receipt showing the refusal reason); (ii) the receipt ENUMERATES R1 as refused-by-guard with both ids and the test id; (iii) R1 is VERIFIED still active after the run (unchanged). Delete "R1 empty".

**E4's honest deliverable** is: the mechanism (dual-column `repository.deactivate()` + `<data_dir>` file receipt + process-once suppression + A/B-strand refusal) is built, guarded, reversible, and PROVEN to refuse, with a correctly identified target set — **NOT "10 rows retired"**. State that in the plan and in §17 in those words.

**Sizing fixes.** R2 (6 captured) = already inactive, NO-OP (confirm, don't flip). R3 (gate-runner copies) = **DO NOT EXIST (0 rows — the commission's premise is refuted; say so)**. `9e79ccad` = active, no A/B group, no duplicate evidence → NOT stale, needs its own ruling, do not flip. `76e3e1a7a` = NOT FOUND. Substantive flips = 0 pending a ruling on R4.

**R4 CENSUS LANDED:** 27 families / 54 active rows / 32 within-identity-key divergence on prod / 26 of 27 live unresolved A/B / 21 served to both variants / 2 identical-content families. The pair-resolver declares the winner on `skill_ab_tests` (`resolved_at` + `winner_skill_id`) and deactivates the loser as ONE reversible unit with a receipt — **NO winners are declared in Phase 1** (the winner decision is the open item E-6). The remaining open item is the **WINNER DECISION** (E-6), recommended default = newer lineage child (NOT timestamps — two families have stale `updated_at` relative to their challenger's `created_at`, so a "newest updated_at wins" rule would pick WRONG).

**What would reverse it.** A ruling that R1 should be deactivated (it would require resolving the A/B test first — which is Phase 2).

---

## DS-45 — Ruling C: G lands first, then E1

**Context.** G fixes the measurement instrument (AB-resolution concurrency / fixture isolation); E1 adds a guard the instrument must validate. A rate/cap guard built on top of a 15%-38% flaky concurrency path makes its own tests unreliable and any failure ambiguous between "guard logic wrong" and "flake fired".

**Ruling.** **G FIRST, THEN E1.** Stabilize the instrument, then add the guard. BOTH are in `skill_evolution_service.py` → ONE OWNER, G first, E1 second, in that order, one lane. State explicitly in the plan AND decisions.md.

**What would reverse it.** Evidence that G's fix requires changing production code that E1 also touches (then the two must be sequenced by seam, not by workstream).

---

## DS-46 — E1 re-design: cap keyed on SKILL IDENTITY, not the job key

**Context.** The council proved **E1's durable cap can never trip** because re-admissions get `#<uuid8>`-suffixed UNIQUE keys (`job_queue_service.py:1596-1608`) — a cap keyed on the job key is dead by construction. The census confirmed `metadata` carries `skill_id` and `direction` on all 116 job rows.

**Ruling.** Re-design the cap to key on the **SKILL IDENTITY**: count FIX admissions per `metadata->>'skill_id'` within a window, from the job rows. State the new key, the window, and the mutation that turns the pin RED. E1 must have acceptance criteria + mutation legs (the council found it had none). E1's pins go into the **EXISTING** registered pack `skill_evolution_unit_test` (PACKS.md:3702); E2's pins go into the **EXISTING** registered pack `skill_capture_killswitch_unit_test` (PACKS.md:2993).

**What would reverse it.** A house ruling that the job key (with its suffix) is the correct dedup identity — it is not; the suffix exists precisely to avoid collisions.

---

## DS-47 — F-13..F-16 staging gate fixes

**Context.** Four required release-gate items missing/under-specified:
- (a) `rollback_safe`: the FULL-HISTORY derivation grep trips on 103 of 126 migration files, so `stage.sh` exits 78 unless `ENSEMBLE_ROLLBACK_SAFE=1` — and that env var is ABSENT FROM EVERY COMMAND in the plan. Put it in the staging command line EXPLICITLY and explain in one sentence why it is legitimate (the DELTA vs the previous tag is empty; the assertion is about the migration delta, not the code).
- (b) PROMPT-BASELINE must be stated as a **TESTER-OWNED, BOTH-TREE 181/181** gate (stage worktree AND release payload), because `stage.sh`'s own drift leg is non-blocking by design (`stage.sh:671-716`).
- (c) The **FE provenance sidecar + the hardcoded `build_tool` deviation** are MISSING from the gate. Add: assert the FE sidecar EXISTS and is read from the SOURCE tree; assert its ABSENCE from the release payload is CORRECT (`stage.sh:418`); record the deviation that its `build_tool` names a non-existent npm script (`_build_frontend.sh:85` runs `npm run build`; `:105` stamps the literal "npm run build:frontend"; `package.json` has no such script). Do NOT gate on `build_tool=="npm run build"`.
- (d) §7 line 330 (now corrected) names a non-existent `id` column — **the `job_queues` PK is `queue_id`**. Use `WHERE queue_id = 'bd075f0b-1f3b-49ef-b0c3-50e31d9b55a2'`.

**Ruling.** All four fixed. (a) `ENSEMBLE_ROLLBACK_SAFE=1` in the staging command + one-sentence rationale. (b) tester-owned, both-tree 181/181. (c) FE sidecar existence (source tree) + absence-from-payload correctness + `build_tool` deviation recorded. (d) `queue_id` correction.

**What would reverse it.** Nothing — these are house-standard gate items. The premise corrections **P1-P4** are retained verbatim and are not touched by this pass.

---

## DS-48 — Ruling F: F (injection) must dedupe by identity at the injection boundary

**Context.** The safety measure that lets F ship WITHOUT resolving 27 A/B tests: the injection path must serve **at most ONE variant per `(name, project_id)`** to a given agent. Two divergent variants of `code-fix` must never both reach the same agent in one injection. State the deterministic pick rule explicitly (recommended: highest generation / newest lineage child, ties broken by id) so behaviour is reproducible rather than arbitrary. Add a pin: construct an agent whose resolved skill set contains BOTH variants of one identity; assert only ONE is injected, and assert WHICH one. Mutation leg: remove the dedupe -> both appear -> RED. Note: the 21 pairs currently served to both variants are a plausible contributor to the "flaky / misreading instructions" symptom — flagged as a HYPOTHESIS, not a proven cause.

**Ruling.** F's injection path dedupes by identity at the boundary. Deterministic pick: highest generation (newest lineage child), ties by id. Pin + mutation leg added. The flakiness link is a hypothesis, not a proven cause.

**What would reverse it.** Evidence that injection over ambiguous pools is not a real-world problem (it is — the symptom is reported).

---

## DS-49 — Ruling D (A4 restate) and Ruling E (E4 pair-resolver) and Ruling F (F dedupe)

**Context.** Addendum 5's Rulings D, E, F restate A4's exit criterion (32, not 0), reshape E4 into a pair-resolver, and add F's identity dedupe. These are recorded as DS-38..DS-42 (Rulings D/E/F) already in this log; this entry confirms they supersede any earlier "A4 is 0" / "target ~10 rows" / "no dedupe" records coherently.

**Ruling.** A4's exit criterion reports 32 (BLOCKED-ON the A/B resolution decision). E4's acceptance is refusal-proven + receipt-enumerated + R1-still-active. F's dedupe pin is proven. No "A4 is 0" / "R1 empty" / "~10 rows" wording survives unmarked.

**What would reverse it.** Nothing — these are the final rulings.

**Ledger/body cross-check (the gate that failed twice).** The plan's §19.1 BODY-LEDGER CROSS-CHECK TABLE carries one row per gated criterion (criterion id | the exact predicate/anchor string | body section:line | §19 row | decisions.md ruling | IDENTICAL?). Every row must read IDENTICAL = Y; where a row is N, **the BODY is corrected to match** (never the reverse). The re-verification protocol is mechanical: for each row, `grep -nF "<exact string>" phase1-plan.md` AND `grep -nF "<exact string>" decisions.md` must both return >=1 hit. **C-17** (the 27-pair stranded A/B backlog) is answered by the R4 census; **C-4** (`NOT BUILT in Phase 1`), **C-12** (`Write-path scope — EXPLICITLY OUT OF SCOPE`) and **C-29** (`P1-P4`) are the other rows that both files must carry verbatim.

**Traceability markers (for the grep-verify gate).** `4311` (`instance_messaging.py:4311` — dynamic injection gated by `skill_injection`) · `2536` (`context_messages.py:2536` — `skills_enabled_only`) · `F-27` (review-integrity note, closed as the disposition table) · `27-pair` (the stranded A/B backlog) · `winner_skill_id` (the open winner decision) · `lineage generation` (the winner rule is based on lineage generation, NOT timestamps — two families have stale `updated_at`) · `identical content` (2 families with identical content = harmless duplicates, no content-logic remediation) · `hypothesis` (the flakiness link is a hypothesis, not a proven cause).
---

## DS-50 — A4's transition bump must not suppress the FIRST legitimate bump (council F-4)

**Context.** The transition-based bump reads the usage row before updating it (`daemon/services/skill_metrics_service.py:1027-1029`). In the **on-miss insert path** (`:1030-1111`) there is **no prior row** (`record is None`): the row is inserted with `applied=False` (`:1051-1063`) and then `update_feedback(applied=True)` runs (`:1081-1088`).

**Ruling.** `prev_applied = False` when `record is None` ⇒ the first legitimate bump **does** happen (exactly one bump per row); on the existing-row path `prev_applied = bool(record.applied)` ⇒ a repeat filing bumps **+0**. A naive fold that reads `False` when a row is absent (or that crashes on `None`) would **suppress the first legitimate bump** — the council's F-4 trap.

**What would reverse it.** Nothing — the on-miss path is the only path with no prior row.

---

## DS-51 — E4.1's wiring site is LOCATED from the code (council F-16)

**Context.** The council could not verify whether E4.1 (the shipped boot-time reconciliation) has a wiring/boot-order site. The leader's Q3: if it cannot be established from the code, mark it an OPEN ITEM with the candidate site and the role/person that resolves it.

**Ruling.** It **can** be established from the code. `InstanceManager.__init__` orders: `run_pending_migrations()` (`daemon/manager.py:1017`) → `self._ensure_postgres_columns()` (`daemon/manager.py:1034`, inside `if ... is_postgres:`) → the **skill-services init block** (`daemon/manager.py:2686` `SkillInjectionService(...)` … `daemon/manager.py:2759` `self._wire_skill_injection_capability_gate()`). ⇒ **E4.1's candidate wiring site is `daemon/manager.py:2759`-adjacent** — the last call of the skill-services block, guarded by the same "skill repos present" condition — i.e. **strictly AFTER** the ensure list, so it can **never** sit inside the `_ensure_postgres_columns` one-transaction ensure list (critical note 48a7f77f: any entry's failure aborts boot).

**What remains an implementation decision (open item E-7):** the exact hook point and the process-once suppression mechanism, resolved by the **developer** at implementation start. The plan fixes the constraints: after `:1034`, before the first injection can occur, idempotent, never inside the ensure list.

**Resolved site (E-7 answered):** resolved site `daemon/manager.py:2786` (after `_wire_skill_injection_capability_gate()`, strictly after `_ensure_postgres_columns()` at `:1034`; never inside the ensure list).

**What would reverse it.** A boot-order change that moves the skill-services block earlier than the ensure list — then the site must move with it.

---

## DS-52 — Staging-gate ownership and independence (council F-19 / F-20 / F-24 / F-25)

**Context.** Four council findings touch the release gate and two touch cross-references.

**Ruling.**
- **F-19 (migration census):** TESTER-OWNED, method = a source-tree no-diff assertion, valid **because the plan states the premise** that the staged tree is byte-identical to the feature tree for `daemon/migrations/versions/**`; the fallback (if that premise is false) is the binary `CArchiveReader` TOC read with full `YYYYMMDD_HHMMSS` prefixes and `uniq -d` empty — **never** a raw `grep -a`.
- **F-20 (4-way sha independence):** the manifest sha is computed **FROM THE COPY** (`scripts/upgrade/stage.sh:437`), so the manifest is not an independent witness — the **build-time provenance sidecar** is, and the tester **recomputes** the staged copy's sha independently.
- **F-24 (dangling §2 refs):** N1 now points at §14.4 checklist item 4; N4 at §14.4 checklist item **7**.
- **F-25 (E3 acceptance bundling):** the board acceptance covers the **3 real bullets only**; the leader-judgment cleanups are labelled and pinned separately so the board closure is falsifiable on its own.

**What would reverse it.** Nothing — these are house-standard gate items.

---

## DS-55 — R5 ratified: normalize-only desync rule

**Context.** E4's implementation disclosed `R5` (the normalize-only desync rule: normalize the single `(False,'ab_testing')` desync row; never flip an active row) beyond the plan's R1/R2/R3.

**Ruling.** RATIFIED as a disclosed E4 rule — the desync row is a real integrity defect, normalize-only cannot strand an A/B test (it touches no active row), and it was disclosed in the module docstring + PACKS.md rather than smuggled in. Ratified by: the Leader (2026-11-09).

**What would reverse it.** Evidence that a normalize-only write can strand an A/B test or that R5 ever matches an active row.

---

## DS-54 — The brake amendment: ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED (a PLAN AMENDMENT)

**Context.** The plan ruled "no gate" for the E4 boot pass. NEW information the ruling did not account for: shipping the pass ungated means it runs on LIVE's next boot at promote — writing a receipt into live's data_dir (~52 refused entries) and normalizing 1 row in the live `skills` table with nobody having armed anything. A boot-time write to production data absent explicit enablement violates the lane's core discipline, even though it deactivates nothing.

**Ruling.** `ENSEMBLE_SKILL_STALE_DEACTIVATION_ENABLED`, default 0/off, absent=off, fully inert when off (no receipt, no row change, no enumeration); it becomes a named Phase-2 lever armed alongside the capture switch. Ratified by: the Leader (2026-11-09), explicitly overturning the plan's no-gate ruling.

**What would reverse it.** A ruling that boot-time data-dir writes and row normalization are acceptable in the absence of enablement (it is not expected).

---

## DS-56 — Round-3 additions: the I5 REST citation form and the second StaticPool quarantine node

**Context.** The consolidated review of rounds 2+3 raised two documentation defects that are pinned here so they
cannot be re-dropped: (i) §17 now carries BOTH a numbered LIST whose item 31 is the I5 disclosure AND a change-list
TABLE whose row 31 is `daemon/services/skill_job_processor.py` (the D lane's execution-refusal work) — a bare
"§17 #31" therefore names two different things; (ii) a flake observed while exercising the workstream-A touched set
(`tests/integration/test_skill_evolution_e2e.py::TestSkillABTesting::test_ab_test_resolves_when_threshold_met`, failing
assertion `assert loser.is_active is False` → `assert True is False`) was classified in a chat report only, i.e. a
**silently dropped classification** — the §13.2 quarantine list must carry it durably or the next reader will count it
as net-new.

**Ruling.** (i) Every citation of the I5 disclosure uses the unambiguous form **`§17 item 31 (numbered list)`** — the
table row is cited as `§18 row 31`. (ii) The node is added to `phase1-plan.md` §13.2's **named quarantine list** as a
**QUARANTINE CANDIDATE** with its mechanism (`tests/integration/test_skill_evolution_e2e.py:91-102` — that file's OWN
`StaticPool` + `:memory:` engine fixture, i.e. the exact class G fixed in `tests/services/conftest.py`, never applied
to this file) and its evidence (isolated **base 3-failed / 10 runs vs head 1-failed / 10 runs** — the base rate is
HIGHER, so it is pre-existing). The formal `.agents/tester/QUARANTINE.md` row remains the **TESTER's** call.

**Why.** (i) An ambiguous anchor is an unverifiable anchor: the §19.1 protocol greps the cited location, and "#31"
would send a reader to the D lane's file row. (ii) The house rule is "base-proven debt is DISCLOSED, never hidden" —
a classification that lives only in a chat message is not a disclosure. Adding it to the existing list adds **no
§19.1 row** (it is a classification entry, not a gated criterion) — re-running the mechanical protocol after this
edit reports an unchanged row count, `ALL_IDENTICAL=YES`.

**What would reverse it.** (i) A §17 restructure that removes the numbering collision (then the plain anchor becomes
unambiguous again). (ii) A test-harness fix that gives `tests/integration/test_skill_evolution_e2e.py` a file-backed,
non-shared pool — the flake then disappears and the entry is removed with it.

---

*End of the decision log. Companion plan: `phase1-plan.md`.*
