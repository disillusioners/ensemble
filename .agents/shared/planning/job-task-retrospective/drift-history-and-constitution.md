# Drift History & Constitution — Job-Task System

**Date:** 2026-09-01 · **Supersedes:** `architecture-recommendation.md` §4 (solution path refined below; verdict stands)
**Evidence base:** 8 read-only workers across two waves (git archaeology ×2, prod frequency measurement, trajectory assessment, structural seam eval, deep history, enforcement census, constitution draft, governance design). Line pins: wave-1 on `latest @7d215e32`, wave-2 on `latest @940e88b7` — where pins moved, both are given.

---

## 1. THE DRIFT LEDGER (condensed)

Class legend (rubric C1–C5, decidable from diff): **ACC** = acceptable feature-evolution · **SPLIT** = necessary-but-should-have-split-semantics · **BREAK** = invariant-breaking.

| Date | Change (sha) | Class | Invariant bent/broken | Bug family traced |
|---|---|---|---|---|
| 03-15 | JobItem born `TaskQueueItem` (1350a646) — mission work-order + execution state, single writer | — baseline | — | — |
| 04-08 | Instance-completion→job callback (dfc9b974) — job becomes instance-follower | ACC | none (this IS the proxy wiring) | — |
| 04-11 | StaleTaskRecovery (0cf80785) — 2nd authority, **for Tasks** | ACC (different state machine) — but an early precedent normalizing recovery sweeps | Task-side only | — |
| 04-19 | Observer spawn-flow consolidation (134bd782) — dispatch call site born sans `work_id` | **BREAK** (C5/D4) — latent; param didn't exist yet | I1 planted | A (enabler) |
| 05-24 | Message jobs = HTTP receipts in the SAME table (914adaae, 827f2213; prod first row same day) | **SPLIT** (C3) — kind split never shipped; Task side got kinds later, JobItem side never | I3 born broken | B, Z, V |
| 06-19 | Step-2 dual-write single-txn (21f6e95f; fossil at job_feedback_observer.py:2898) | ACC (TOCTOU-motivated consolidation) — proxy by construction | — | — |
| 06-22 | Observer completion gate consults bus_pending (ee3899b0) | SPLIT-lean (ordering legit; unbounded divergence planted) | D3 seed | B |
| 06-27 | `work_id` foundation (d058314f) — contract stated, **not enforced** (3 of 4 sites) | ACC content, **BREAK enforcement** (C5) | I1 convention-from-birth | A |
| 06-28 | job-as-queue-proxy doctrine (7a1d5027) — collapse **half-landed** (execution cols kept) | **SPLIT** — declared doctrine, incomplete execution | I3 half-state | V |
| 07-02 | F10 linkage fix (645656e2) — **1 of 4** sites | partial fix; census would still fail D4 | I1 still broken | A |
| 07-03 | Message-jobs POC + **auto-mint** (7d42f6b5) | **BREAK** (C5 — fail-open handle, still alive instance_messaging.py:1593-1603) | I1 | A |
| 07-06/07 | JAFP (c894ee01, 2c86e43c, e495cc3) — every public action a Job; kind split on Task side only | **SPLIT** (C3) — the undeclared split, at scale | I3 | B, Z |
| 07-11 | First independent writer: manual `force_finalize_orphan` (93b10484) — receipt non-self-finalization **known and patched around, not modeled** (commit also documents the two-answers read model) | **BREAK-lean** (D2) — first compensator-as-policy | I2 erodes; I3 documented-broken | B, Z |
| 08-01 | Autonomous `orphaned_no_task` stamps (e8ff8861) — first **autonomous** secondary terminal writer (prod 08-03; flat 1–3/day) | **BREAK** (D1/D2 — unregistered polling writer) | I2 dies | (benign so far) |
| 08-19/20 | RDRS 5 lanes (1434b0ed, incident 6c631666) | SPLIT (real recovery need; +writers +5 lane flags) | I2 worsens | — |
| 08-25 | Pattern e drift sweep (b571a7eb) | **BREAK** (D1 — another autonomous writer) | I2 | — |
| 08-28/29 | WC watchdog (ea902bb8) + Pattern d (23fc5e2d) | SPLIT (real hang/wedge classes; guidance + writers) | I2 | — |
| 08-30 | Pattern f1+f2 **default-ON** (44d5b4cf, dee03665) — sweep as primary transition | **BREAK** (D2 — killer; predicate assumed I1, false for 4.5 months) | I2 weaponized | **A (08-31)** |
| 09-01 | f1 fix chain (04fd0c52→a6a54ce4) | mint repair = **invariant-restoring**; guard/kill-switch = subordinates (registerable) | I1 BENT→ enforceable | — |

**Proxy-lens verdict (user's model, adjudicated):** mechanism CONFIRMED, timing REFINED. JobItem was *not* born a proxy (03-15: work-order + execution state); proxy-by-construction 06-19, doctrine explicit 06-28 — and the receipts that broke one-meaning-per-state predate both the doctrine and the POC (**05-24**, not 07-03). Wave 2 (read-model divergence) CONFIRMED — first documented in a commit message 07-11, 8 weeks before it alarmed anyone. Wave 3 (sweep proliferation) CONFIRMED — 22 writers total today, 9 uncoordinated, 8 bypassing `validate_transition`, 5 bypassing every guard (census: W6).

## 2. THE THREE BIGGEST TIPPING POINTS

1. **TP1 — 05-24: receipts entered the table without a kind split.** Every later meaning-ambiguity (mirror lag, zombie semantics, derived-vs-raw divergence) descends from this C3 violation. An alternative existed at the time — a separate receipt table; the repo later used exactly that pattern for turn mirrors (8 tables, e8ff8861) — but no proxy-vs-receipt discussion appears in any commit or planning doc until the 09-01 retrospective.
2. **TP2 — 07-11: the first known two-answers defect was answered with a reaper instead of a model.** This is the moment feature-driven evolution crossed into compensator-driven drift: the commit *documents* the read-model divergence and ships a manual sweep past it. It normalized "a polling predicate may finalize rows" (→ 08-01 autonomous), while the enabling contract (work_id, 06-27) stayed stated-not-enforced and the proxy collapse (06-28) stayed half-landed.
3. **TP3 — 08-30: sweep promoted to primary transition authority, default-ON.** The weaponization: f1's `task is None` predicate silently assumed I1 — false since 04-19 — and Incident A fired on the first cycle (08-31 11:38). Under the constitution below, D2 rejects this at design time and D4 blocks it until I1 is TRUE.

**When did single-writer actually die?** Three steps, ranked by consequence: 08-01 (autonomous stamps — precedent), 08-30 (killer — blast), 07-11 (manual — the crossing point). The proxy's single-writer property was dead in practice by 08-01 and weaponized 08-30.

## 3. THE CONSTITUTION

### NEVER-DRIFT invariants (status from census, current `latest @940e88b7`)

| ID | Statement | Status | Evidence |
|---|---|---|---|
| I1 | `Task.work_id == JobItem.job_id` at every job-driven dispatch; handles mint fail-closed | **BENT** | 4 sites tripwired WARN-only (messaging_types.py:41-77); auto-mint alive (instance_messaging.py:1593-1603); no FK |
| I2 | One transition authority per admission_state class; others idempotent-readers or **declared** subordinates | **BROKEN** | 22 writers; 9 uncoordinated; 8 bypass `validate_transition` (only W1 repository.py:1236 enforces); W5 writes illegal `paused→done` (job_feedback_observer.py:3403-3456); no precedence — per-writer SQL guards only |
| I3 | Proxy-per-kind: missions proxy instance lifecycle, mirrors are receipts; one meaning per state per kind | **BROKEN** | Mirrors have no event-time terminal write (task_processor.py:859-876); two read answers (work_status.py:209-268 receipt-truthmaker; work_resolver.py:1294-1310 liveness-only-when-active) |
| I4 | Internal paths never create JobItems (JAFP boundary) | **BENT** | Boundary HELD today (4 public message callers; zero internal creators — census C2–C9) but convention-only; mirrors regime means the boundary's meaning split by kind |
| I5 | DEAD is terminal; corrections additive | **TRUE** + hazard | dead→queued only via DLQ replay (dead_letter_service.py:370-449); no path re-opens wrongly-DONE rows (operator SQL proven: the `manual-unstick` row); revived-instance-under-DEAD-job unguarded |

### EVOLUTION-ALLOWED seams (no amendment needed)

New job_types **with** creation-time kind declaration (truthmaker + event-time writer + read branch — a type missing the writer is precisely the 07-03 mistake); new queues; read projections declaring authority + divergence bound; tunables on registered subordinates; new mirror kinds under the same rule. **Constitutional (amendment required):** any new admission_state writer; linkage-semantics changes; terminal-state meaning changes; re-scoping an existing sweep's *predicate* (the 08-30 lesson).

### "HOW MUCH DRIFT IS TOO MUCH" — the checkable criterion (D1–D4)

Drift is too much the moment **any** of these is red, each mechanically checkable in CI/review:
- **D1 WRITER REGISTRY** — every `SET admission_state` site resolves to a registered owner; ≤2 per class (owner + declared backstop). *Today: fails — 9 unregistered.*
- **D2 EVENT-TIME TERMINAL RULE** — every stateful row has an event-time terminal writer; a sweep is never primary, only loss-recovery for stale *unlabeled* rows. *Today: fails — mirrors.*
- **D3 ONE-ANSWER RULE** — every derived status names truthmaker + direction + bounded divergence. *Today: fails — derived-vs-raw unbounded (7 h).*
- **D4 FAIL-CLOSED HANDLES** — no path fabricates work_id/job_id on None. *Today: fails — auto-mint.*

Retro-validation: D1–D4 catch every historical drift event at landing (05-24 by D2; JAFP by D1+C3; auto-mint by D4; Pattern-f by D2+D4; derived status by D3) and correctly *pass* the clean 08-11 Step-4 reconcile. **By 08-30 all four were red; they had been red since 07-03 at the latest.** That is the operationalized answer: the line isn't a drift *amount*, it's these four booleans — and the system sat at 4/4 red for two months before anyone felt it.

## 4. GOVERNED SOLUTION PATH (supersedes prior §4 ordering detail)

**Structural fixes (unchanged core, one tightening):** **A** fail-closed linkage (~1 d) → **B** inline idempotent mirror transition + liveness-gated sweep predicate (2–4 d; retires f2's mirror slice + zombies) → **C** read-model truth + mission/mirror rendering split (1–2 d) — **C strictly AFTER B, never alone** (C-without-B converts unbounded divergence into invisible divergence; this is now a rule, not a preference). **D** (mirrors-as-projections, unified reconciler) deferred with trigger: subordinate count >4 or family regrowth.

**Governance layer (new — phased, each independently shippable, kill-switched per repo resolver pattern):**

| Phase | Mechanism | Enforces | Cost |
|---|---|---|---|
| 0 (immediate) | `daemon/job_state/constitution.py` static sets (`KNOWN_ADMISSION_STATE_WRITERS` / `KNOWN_JOBITEM_CREATORS` / `KNOWN_MINT_SITES`) + bidirectional AST census tests (tool-name drift precedent, tests/unit/tools/test_frozen_tool_name_discovery.py:223 — scanner MUST raise on zero-source-readable) + `test/packs/constitution_drift_test.sh` + regen one-liner | D1, D4, JAFP counts | ~½ d, pure add |
| 1 | `ENSEMBLE_ADMISSION_GATE_PHASE` staged gate (0=log-only); W4/W20 registered subordinates | D1 | ~1 d |
| 2 | Route W2/W3/W5 through W1 `validate_transition`; W5 `paused→done` → explicit `PAUSED_TO_DONE_LEGACY` disposition | I2 | ~2 d |
| 3 | Route W6/W7 (lifecycle cascades) | I2, D2 | ~2 d |
| 4 | Route W13/W14 (DLQ); `terminal_reason` StrEnum + CHECK (8 values + versioning: enum bump + migration + doc = 3 forcing touchpoints) | I3, D4 | ~2 d + migration |
| 5 | FK `task.work_id→job_queue_items.job_id` two-phase (`NOT VALID` → audited `VALIDATE`; `legacy_linkage_orphans` audit; soft-delete semantics) | I1, I5 | ~1 d + purge audit |
| 6 (gated) | D projection rewrite; f-family collapses to one liveness-gated safety net | all | high — only on trigger |

Review gate: PR-template checkboxes mapped to D1–D4; amendments as ADR-style entries in this directory's `decisions.md`; code constant is registry source-of-truth, doc generated/asserted equal by the census test.

### Anti-recommendations (explicit)

**Don't** revert receipts-in-table (load-bearing: 39→49% of rows) · **don't** re-litigate JAFP (net −1004 lines; boundary held) · **don't** add kill-switches as governance substitutes (they're incident-response tools; one-per-bug-class inverts the trajectory) · **don't** freeze the evolution seams (new job_types/queues/projections stay cheap; only new *writers* are constitutional) · **don't** big-bang D (family is starving: flat 1–3/day, 0 on 09-01).

## 5. CONFIDENCE + GAPS

**High:** ledger classifications (commit-quoted motivations), census counts, invariant statuses, D1–D4 retro-validation, tipping-point ordering. **Medium:** phase-cost estimates; D-trigger threshold (>4 subordinates — team to ratify). **Open:** W5 `paused→done` disposition (route-vs-register — team call); mint-idiom completeness for the D4 scanner (`uuid4` vs `token_hex`/`uuid7`); FK-vs-retention-purge interaction (needs orphan audit before VALIDATE); CHECK-enum value completeness (8 known + `manual-unstick`); no terminal-transition timestamps in DB (historical rates remain inferred). **Record corrections:** "6+ authorities" → precisely 9 uncoordinated / 22 total; the "message-metadata placement test" cited in the task does not exist on this branch — the tool-name drift test is the precedent; auto-mint pin drifted :1593-1602 → :1603 across checkouts (same site).
