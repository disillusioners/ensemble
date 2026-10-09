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
