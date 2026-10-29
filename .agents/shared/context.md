# Project Context — agents-ensemble

## Chart-image-delivery — ari pre-warm (2026-10-04)

`ari` / `commissioner` MUST invoke the `install-mermaid-cli` skill as a
deploy step on every fresh host (mirrors the `install-opendesign`
deploy-step execution). This pre-warms the `mmdc` + puppeteer +
chromium toolchain that charter uses to render validated Mermaid to
PNG; without it, charter's first chart on a cold host returns
text-only Mermaid (degrade path; rule.md Must rule). The one-line
reminder lives here; the commissioning note lives in `ari`'s
workflow.

## Agent Rename: coder → developer (2026-06-25)
The "coder" agent was renamed to "developer". Historical docs in
.agents/ and docs/bugs/ may still reference "coder" — these are
intentional historical records.

## Current State (2026-06-20)

### Decouple Architecture Migration
**Status**: Phase A COMPLETE, Phase B ready to start.

**Branch**: `feature/decouple-architecture`

**Phase A (Authority & Visibility) — COMPLETE ✅**
- All `waiting_for` control-flow gated behind `USE_LEGACY_WAITING_FOR_CASCADE` flag (default OFF)
- CM (CorrelationManager) is the SOLE completion authority when flag OFF
- `FOR UPDATE` gate in `job_feedback_observer.py` replaced with `cm.is_complete()` when flag OFF
- A8: Hard RuntimeError when flag OFF + CM None (not graceful degradation)
- A0a: `rebuild_from_db()` fixed — top-level OVERWRITE + per-parent MERGE
- `DEBUG_COMPLETION_INVARIANT` logs `CM_WAITING_FOR_DIVERGENCE` on mismatch
- 18 files audited, all control-flow reads gated
- 160 Phase A tests pass (123 SQLite + 37 PostgreSQL)

**Phase A Commits** (on `feature/decouple-architecture`):
- `a8c8a1fb` — A0a: rebuild_from_db() MERGE fix
- `a0db4a50` — A2: config flags
- `c2b17761` — A3-A6: gate waiting_for control-flow
- `0fa74efc` — A12: shadow test pack (register-window proof)
- `afbb35ec` — A7-A8: FOR UPDATE gate replacement + hard error
- `eee8efd9` — A1, A9, A14: authority doc, audit, kill-switch tests
- `5f9ee985` — A10, A11, A13, A15: invariant, regression, crash-recovery tests
- `ef147bfa` — Reviewer fixes: C1 (cross-thread race), C2 (threading test), W1 (re-entrancy guard), W2 (RuntimeError propagation)
- `9414a17f` — Reviewer fix v2: post-commit orphan race closed via generation counter + re-arm mechanism
- `272fd840` — Reviewer fix v3: return after re-arm to prevent outbox fall-through

**Phase B (Close the Bug Class) — COMPLETE ✅**
- `bad3bea3` — B1-B4: pending_jobs in CM, register_job_send/resolve_job, watch_job routing, observer terminal resolution
- `3ae8a72e` — B5: watch_job integration test pack (10 tests, Variant B regression)

**Phase B Status**: All 3 premature completion repro variants structurally impossible. 260 tests pass.

**Phase B (Close the Bug Class) — COMPLETE ✅**
- Route `watch_job`/`job_continue` through CM via `pending_jobs`
- `ParentCorrelation` now tracks `pending_jobs: set[str]` alongside `pending`
- `is_complete()` returns True only when BOTH are empty
- 260 tests pass (223 SQLite + 37 PostgreSQL)

**Phase C (Single Dispatcher) — COMPLETE ✅**
- C-M4: Deprecation log + path tests (C1-C4)
- C-M5: Route JobQueue through observer (C4.5-C11) — `USE_LEGACY_JOBQUEUE_DISPATCH` flag
- C-M6: Collapse gate to asyncio.Lock (C12a-C18) — 707→268 lines, net -914 lines
- 160+ Phase C tests pass
- Unify enqueue to WorkerPool-only
- JobQueue is scheduling-only
- Est. 2.5 weeks

### Key Config Flags
| Flag | Default | Purpose |
|------|---------|---------|
| `USE_LEGACY_WAITING_FOR_CASCADE` | `False` | Kill switch for legacy waiting_for cascade. OFF = CM authoritative. |
| `DEBUG_COMPLETION_INVARIANT` | `False` | Logs CM_WAITING_FOR_DIVERGENCE on mismatch. ON recommended in dev/CI. |

### Key Architecture Docs
- `docs/architecture/completion-authority.md` — Three authorities, invariant, call sites
- `docs/configuration/completion-flags.md` — Flag interaction matrix, triage runbook
- `docs/plans/decouple-execution-plan.md` — Full 3-phase execution plan
- `docs/plans/decouple-review.md` — Review findings (round 1 + round 2)

## wc-wake-report-integrity Phase 1 (2026-08-30)

**Status**: Phase 1 COMPLETE (T1-T7 landed; T6b fixture migration; T8 docs).

**Branch**: `feature/wc-wake-report-integrity` @ `cf210e32+`

**Lock-in**: kill-switch `ENSEMBLE_WC_WAKE_ENQUEUE` (default OFF at code-land; flag-ON ships the new routing per D2.5-FLIP).

**Key changes**:
- `INJECTION_ELIGIBLE_STATUSES` shrunk to `frozenset({"running"})` (T2)
- HTTP / agent-tool / job_inject routing pivots behind the kill-switch (T3+T4+T7)
- `_heal_poisoned_checkpoint_tail` closes the poisoned-tail → LangGraph 2013 exposure at the enqueue seam (T6)
- D2 seam drain moves parked FIFO leftovers into graph_input (T5)
- `Manager.send_message` and `InstanceMessagingService.send_message` DELETED (T6b / D7 LOCKED) — surviving production traffic must use `enqueue_message` (durable wake) or `set_injection` (mid-turn injections on RUNNING)

**Flag states**:
- OFF (default): legacy FIFO injection for WC targets (revert path)
- ON: WC → `enqueue_message` (durable wake, first-class turn)

**Tests affected by T6b deletion**: ~13 test files skipped (TestSendMessage, TestThinkTagParsing, TestInstanceMessagingTriggerTitleGeneration send_message subset, test_question_deferred_pause_*, test_inner_soul*, etc.). All skipped tests assert behavior of the deleted methods and are pending a Phase-2 rewrite against `MessageProcessingPipeline`.

**Docs**:
- `docs/setup.md` documents the env var
- `docs/features/job-queue.md` example updated to `manager.enqueue_message`
- `daemon/tools/instance.py` `_full_doc_` documents both flag states + D6 busy-gate consequence
- `daemon/tools/job_queue.py` `_FULL_DOCS["job_inject"]` rewritten
- `daemon/routers/messages.py` routing-table docstring updated

## Live-view subsystem Phase 1 (2026-10-07)

**Status**: Phase 1 SHIPPED on `feature/live-views` (not yet merged to `latest`). The next release vehicle post-v0.18.1 picks it up.

**Surface**:
- `GET/HEAD /views/<root>/<rel>` route family. Read-only. Uniform 404 envelope on every miss (no path disclosure, nosniff on every response).
- `view_link` tool — agent-facing URL minter. **RESTRICTED first-release visibility (REWORK 2026-10-07, M1, user refinement #1):** the `view-views` category is in `PRIVILEGED_TOOL_CATEGORIES`; only ari + leader + designer opt in via `tools.allow: ["view-views"]`. Empty-allow agents do NOT get the tool. Path-relative URLs by default; fully-qualified when `live_views.external_base_url` is set.
- Three seed roots: `designer-artifact` (REWORK M2: project-scoped via shortname, mockups-subtree-enforced per M3), `planning` (project-scoped via shortname), `tmp-images` (sidecar-MIME, delegates to `TmpImageStore`).
- Designer write-through: the design artifacts table contract (`agents/designer/skills-template/design-strategy.md:71`) gains a `view_url` column populated via `view_link('designer-artifact', f"{shortname}/{<row.path>}")` (REWORK M2 — the first URL segment is the project shortname). LLM writes the column; the tool is the surface.

**Key docs**:
- `docs/runbooks/live-views.md` — operator runbook (edge rule, config knobs, troubleshooting, security notes).
- `daemon/services/live_views.py` — registry + resolver (single source of truth).
- `daemon/routers/live_views.py` — HTTP route family.
- `daemon/tools/live_views.py` — `view_link` tool factory.

**Security**: no auth at the daemon. Edge guard is the OAuth proxy. URL is "public-by-obscurity" per `.agents/shared/conventions.md`. **Do not expose `/views/*` to the internet without edge auth.**

## Sketcher lane — Stage 1 + Stage 2 (2026-10-09)

**Status**: BUILT on `feature/sketcher-agent` @ `ca80a0f88` (+ this docs close-out).
Pilot mechanism PROVEN live (re-smoke PASS: `compose_brief` -> `generate` -> `lint` ->
`od.save` correctly SKIPPED per bounded logic; earlier hang signature did NOT recur —
transient upstream, not branch). Campaign GO post-promote.

**Stage 1 — `od.generate` on the ensemble LLM lane** (commit `e69669937`):
- `daemon/tools/od_generate.py` is now a fail-over facade over the ensemble LLM lane.
  `ThinkingChatOpenAI`/`llm_failover` semantics, retry classification, and
  completeness-gate `marker_pass` / `finish_reason` surface are inherited; the upstream
  tool-call is the inner step.
- Additive typed 400 envelope codes (upstream-only, design-lane surface):
  `upstream_bad_request` and `context_length_exceeded` belong to the OVERFLOW class
  (zero retries) per Cardinal #3 retry envelope. `truncation_detected` and
  `missing_artifact_marker` remain TRUNCATION class (one bounded retry).

**Stage 2 — sketcher generation worker + designer orchestration** (commit `7f03b37a4`):
- New agent `agents/sketcher/` is a leaf generation worker: vision pin
  (`llm_model=vision`), closed `tools.allow` fence (od.* + image + dynamic-skill;
  NO bash/filesystem/write/instance, no `tools.deny`), empty `team_members`,
  `recursion_limit_multiplier=12`, `default_queue=system_parallel_queue`,
  `skill_injection=true`, `watchover.timeout_seconds>=60`.
- `agents/designer/` orchestration surface gains a `team_members=["sketcher", ...]`
  and a `Dual-Run Pilot` block in `workflow.md` that emits parity rows matching the
  schema in `.agents/shared/planning/od-generate-agent-lane/stage2-addendum.md`.
- Pilot rows land in `parity-runs.jsonl` (one per lane per page); the schema is the
  sole contract and the addendum is the sole gate source. The first row
  (`run_id=sketcher-resmoke-20261009-140826`) is a mechanism-smoke row; campaign
  aggregation MUST filter it out (rule recorded in the addendum).

**Pilot knobs (campaign, not smoke)**: `max_tokens >= 16000` (8K saturates the
vision lane — `finish_reason=length` both attempts on the smoke row). Median latency
budget <= 1.5x direct; tokens/page <= 1.3x direct; truncated <= direct+5pp;
marker_pass >= 95% AND within 5pp of direct; N >= 10 dual-run pages.

**Known deferrals**: `_OD_GENERATE_WALL_CLOCK_CAP_S=420` cap-math gap (fires only
between attempts; observed 489s at 32K max_tokens — pre-existing, faces designer
equally); review-green #4 sketcher pipeline paraphrase tightening (not load-bearing);
`design.capture_mockup` tool follow-up slice; 12 pre-existing test failures
(4 plugin_subsystem + 8 agent files, base-proven 2026-10-07/09) remain upstream
debt. All recorded in the addendum.

**Review/test status**: APPROVED (governor council, 0 critical / 0 warning);
PASS-with-preexisting (1 branch regression tier1 boot-scan fixed `efc460262`; 12
pre-existing base-proven). Awaiting user promote ceremony (3-factor nonce gate) —
NO live promote performed.

## Skill System Audit — incident 2026-10-21

**Full forensic report**: `.agents/shared/planning/skill-system-audit/incident-2026-10-21.md`

**Key state facts (pending disposition):**
1. **Unpushed commit `660e73453` pending disposition** — skill-keeper instance
   b7da3ed0 committed 10 files (9 daemon/ + 1 tests/) (+476/−1, owner-private skill-search
   filter) directly to `latest`. Keep-after-review or revert-and-reimplement =
   USER decision.
2. **Uncommitted `agents/wanderer/skill-set.yaml` edit pending disposition** —
   run 1e3792d4 flipped auto_load true→false + version 1.0.0→1.0.1 via bash
   workdir-bypass. USER decision (may be superseded by the search-layer fix).
3. **skill-keeper allowlist over-grant (bash + filesystem-write)** — a DB-only
   agent holds full repo-write capability; soul.md prose prohibitions are NOT
   enforcement. Root cause L1 in the report.
4. **skill_fix lane ungated — no kill-switch/dedup** — skill_fix → dispatch_fix
   mints immediate Tier-3 mutation jobs with agent-authored free-text
   directions; no cooldown (same skill re-FIXed 3× in 38 min). Root cause L2.
5. **Capture was NEVER re-enabled — user's re-activation hypothesis DISPROVEN**
   — `ENSEMBLE_SKILL_CAPTURE_ENABLED` absent from live daemon env, all
   set-sites, and code history; zero `skill_capture` jobs since 2026-09-17. The
   ten runs were all `skill_evolution` FIX-lane jobs.
6. **Burst runs wrote ZERO `skill_usage_records` rows** (census verdict);
   capture-era A/B `7c9440b3` still open (resolved_at NULL) — cleanup candidate.

**Disposition (user-decided, executed 2026-10-21/22):** commit 660e73453 KEEP (unpushed; two-lens review = correct + sabotage-clean; P1 test-stub fix + security merge conditions logged as follow-ups); agents/wanderer/skill-set.yaml auto_load flip REVERTED via git restore (auto_load is owner-scoped; the observed leak was search-driven — deactivations were the real barrier; fleet convention = own planner auto_load true). DB cleanup + fix sequencing deferred.

## db-pool idle-in-txn lane — implementation landed, gate re-verification pending (2026-10-29)

**Status**: the transaction-hygiene fixes for the 2026-10-26 incident are **landed on the lane
branch and independently gated — NOT promoted**. The live daemon still runs the pre-fix tree, so
the incident stays **OPEN** as a symptom class.

- **Branch**: `fix/db-pool-idle-txn` · base `19fc11c8b` (staged v0.27.11 tree) · HEAD `812ceca60`
  · **82 commits** (incl. this docs commit) · worktree `/home/nea/ensemble-src-wt-dbpool-idletxn` · **not pushed to origin**.
- **Scope**: **11 daemon files** (+1096/−207), 30 test/script files, **0 new `.sql`** (migration
  delta vs the live release is EMPTY; `agents/`, `frontend/`, `plugins/` payload trees unchanged).
- **Plan of record**: `.agents/shared/planning/db-pool-idle-txn/` (BASELINE.md,
  final-implementation-spec.md, plan-overview.md, code-review.md, tidier-report.md,
  delta-tidy-verify.md, FOLD-MAP.md, expected-collected.md).
- **Incident record**: `.agents/shared/incident-20261026-db-pool-idle-in-transaction.md` — the
  "FIXES LANDED" section records what shipped **and which of the original RCA claims the
  measurement refuted** (the pool was never exhausted; the `idle in transaction` census is blind to
  the dominant occupancy shape; the probe does not leak per tick; the "aborted connection handed
  back out" quote is a same-connection cascade in wedge-capture; the F8 flip condition is NOT met
  ⇒ F8 stands; OQ-1 closed as a concurrent double-mint).

**What landed** (per-file detail in the incident record):

- `daemon/manager.py` — **F8**: the deferred-report reconcile split into **P0/P1/P2** in both
  twins; no manager-engine transaction is open across an await; the async fetch is bounded at 8.0 s;
  the (c) branch consumes the carrier flag **strictly** (`live_carrier_exists is False`, :11226
  sync / :12049 async at HEAD).
- `daemon/tools/instance.py` — **F8.5**: the `await _bus.watch(...)` moved out of the
  `WriteGuardSession` scope.
- `daemon/tools/ens_db_tools.py` — **F3**: the explicit `text("BEGIN")` deleted in both branches
  (`grep 'text("BEGIN")' daemon/` → 0) + unconditional `finally`-rollback; the repair engine got its
  own `connect_args` + explicit `pool_reset_on_return="rollback"`.
- `daemon/services/readiness.py` — **F4-A** admission pre-check (+ its non-QueuePool shape-guards)
  and **F4-B** `SET LOCAL statement_timeout`; the probe docstring was corrected.
- `daemon/repositories/factory.py` — **F2**: explicit knobs (`pool_reset_on_return="rollback"`,
  `pool_recycle=3600`, `pool_timeout=30`; sizing 5+10 unchanged), conninfo `application_name`,
  and the engine-level idle-in-txn backstop **defined but DISABLED** with a boot log that says so.
- `daemon/constants.py` (Δ3 constants + honesty blocks) · `daemon/persistence.py` (asyncpg adapter
  DSN + `server_settings`) · `daemon/repositories/mission_record/repository.py` +
  `daemon/services/mission_record_service.py` (**F6** idempotent mint) ·
  `daemon/services/wedge_capture/db_capture.py` (**F5** `tasks`→`task`) · `daemon/routers/queues.py`
  (**F9.1**, comment-only).

**Gate record**:

- Code council: **APPROVE-WITH-ADVISORIES, 0 CRITICAL** (`code-review.md`; W-1/W-2 since folded).
- Quality pass: `tidier-report.md` (6 High / 17 Medium / 14 Low) → `delta-tidy-verify.md`
  **TESTER-GATE-READY, 0 conditions**.
- Independent plan approval: `.agents/approver/db-pool-idle-txn-tracking.md` — **APPROVED**, 0
  blocking.
- Tester functional gate (`tester/dbpool-functional-gate` @ `148a7b30d`): **VERDICT FAIL on one
  gate — G3.5, 11 net-new reds, ONE root cause** (the F4-A pre-check crashed on non-QueuePool
  engines at `readiness.py:1512`). Everything else PASSed: ladder 17/17 (155 tests, 0 failed, 0
  skipped, exactly 1 xfailed), J1 R-1 35/35, J2 Δ3 measured, J4 mutations 9/9, J5 six
  base-vs-HEAD scenarios, J6 live boundary untouched, count identity +109 exact.
  The lane's last commit `441a1657d` fixes exactly that root cause and the F4-a mutation leg was
  re-proven RED→GREEN on the merged tester tree `3d835d8dd` — **but the re-verification is not yet
  written up as a RESULTS doc, so the tester's FINAL verdict still reads FAIL pending that
  write-up.**

**Open blockers** (all lane-side, all resolvable without touching the live daemon):

1. **R-1 — the tester's `expected-collected.md` fill has not landed in the lane tree.** The lane
   copy still carries the `TBD (R-1)` placeholder (**38 occurrences** — the 35 count cells plus 3
   prose mentions), so every ladder row FATALs by design on the lane tree; the 35/35 measurement
   landed on the **tester** branch (`47ff2f5ff`) and must be merged in.
   (This "R-1" is the **tester's count-fill task**, not the routed R-1 event-loop item below.)
2. **The Δ3 measured constant (0.25 s) is likewise tester-side only** (`8cc538a71`); the lane tree
   still ships the sentinel `MANAGER_ENGINE_LEGITIMATE_HOLD_BUDGET_S = -1.0`
   (`daemon/constants.py:353`), which scopes the F7 residence assertion out.
3. **The origin/latest merge is DONE** (2026-10-29): merge commit `79bf44936`
   (parents `aac505291` lane + `69d767b2a` origin/latest — the 9-commit move incl. the
   manager-pool-size knob hotfix), **3 conflicts resolved as unions**: `daemon/repositories/factory.py`,
   `tests/unit/repositories/test_pg_engine_utc_session.py`, `.agents/shared/context.md`. The merge's
   semantic fallout was fixed in the merge-semantics cycle: the hard-coded
   `MANAGER_POOL_CONFIGURED_CAPACITY = 15` became the DEFAULT-TIER declaration (**25** = 15 pool +
   10 overflow) with the admission pre-check's refusal threshold DERIVED from the engine's own pool
   at call time (a static 15 would refuse while overflow slots are servable), and the
   capacity-equality + sizing pins were re-derived for the config-driven
   `pool_size=manager_pool_size` construction (`limits.manager_pool_size`, default tier 15,
   floor 15). The union had also silently dropped one test body (restored). Per §15.3 every anchor
   must be re-verified before a stage — a stale stage is refused by the L1 tip-identity guard
   (exit 78), and that refusal must not be overridden.
4. **The version slot moved:** `v0.27.12` is **taken** (live `current -> releases/v0.27.12`).
   `v0.27.13` is free as of this note, but the slot is re-derived from the live census at stage
   time (W9) and is never hard-coded in an artifact.

**Routed out of this lane** (see `plan-overview.md` §12): **R-1** event-loop sync-DB (the dominant
untreated driver — `daemon/routers/instances.py:476`, `daemon/routers/queues.py:654`), **R-2**
BFS depth-loop transaction span, **R-3** default-executor ceiling, **R-4/R-7** missed-finalize
family (a finalize that *fires* now commits; a finalize that *never fires* is untouched),
**R-9** asyncpg adapter idle-in-txn hold (attribution shipped; the hold is the follow-up's, under
the D-47 enablement gate), **R-10** wedge-capture store-census defect (`n_live_tup` read from
`pg_class` instead of `pg_stat_user_tables` — born-broken at base, a SECOND root of the aborted
cascade that this lane does **not** close), **R-11** `instance.py:1346` unreachable check.

**Promote boundary: STAGE ONLY.** No promote, no daemon restart, no `.env` touch, no production DB
contact; the live daemon, the live symlink and the promote journal are untouched
(`state.json`: current `v0.27.12`, previous `v0.27.11`, `in_flight=None`). The promote flip is the
**user's 3-factor nonce ceremony** — never performed by this lane or by an agent
(`plan-overview.md` §15.1).

## Manager DB-engine pool knob — LIMITS_MANAGER_POOL_SIZE (2026-10-28, devops incident fix item #7)

Managed knob `limits.manager_pool_size` / env `LIMITS_MANAGER_POOL_SIZE`, ladder **operative end-to-end**: DB > env > yaml > default → boot-apply mutates the YAML Config (`config.limits.manager_pool_size`) → `daemon/manager.py` threads `manager_pool_size=self.config.limits.manager_pool_size` into `create_postgres_engine` (the ensemble.json `EnsembleConfig` has NO `.limits`, so the factory takes the value as an explicit kwarg; other callers default to the constant). Bounds [15, 30] fail-loud (floor 15 = default, ceiling 30 = connection budget 30+10 manager + 2×20 checkpoint = 80 ≤ max_connections 100) — out-of-range values are rejected (422 / ValidationError), never clamped. Activation = next daemon restart (engine built once at boot).
