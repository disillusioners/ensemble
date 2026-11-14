# findings-C-risk.md — Leg C: Risk/Quality Surface + Repo-Side Cost Inventory

Commission: ENSEMBLE-31 "sonic team" deep-investigation, Leg C.
Author: wanderer (sonic-inv-C-risk). Date: 2026-11-12.
Question set: blast-radius classification signals; quality-gates inventory; `.agents/` upkeep inventory; bash timeout/duration infra; measurement surface. READ-ONLY codebase investigation.

## Method & caveats

- Repo read surface: canonical checkout `/home/nea/ensemble-src` — **DETACHED HEAD ~2 weeks behind origin/latest**, carries a staged index + unstaged WIP. Git used READ-ONLY (`log/show/grep/diff`); no fetch. Current-code citations use `origin/latest:<path>` blobs; working-tree line numbers are labelled "wt".
- Window for history/inventory: 2026-08-12 → 2026-11-12 (history), 2026-10-22 → today (artifact mtime).
- **History staleness caveat**: local refs stop ~2 weeks before the window end (no fetch permitted). Defect counts for the last ~2 weeks are under-counted.
- **402 wall**: the provider coding-plan quota was exhausted mid-run (worker B died before its first tool call; worker A died after ~20 tool calls; provider resets 2026-11-17T11:44:37Z). Items 2 and the defect-concentration part of item 1 were **backfilled by the controller** with direct greps; item 1's semantic/call-graph blast-radius analysis was NOT performed. Marked 402-BLOCKED where applicable.
- Cross-leg supplement folded in from Leg A (prod-DB runtime evidence): bash exit_kind='timeout' rows at exactly 1800.0s; duration distribution; `bash_spawn_registry` reaped-row semantics. Source-labelled "[Leg A]".

---

## 1. BLAST-RADIUS CLASSIFICATION SIGNALS

### 1a. File-path taxonomy (what exists; is anything marked critical?)

Repo file counts (working tree, `find -type f`):

| Area | Files | Notes |
|---|---|---|
| `daemon/` | 1,767 | core runtime |
| `daemon/services/` | 555 (217 `.py`) | biggest production surface |
| `daemon/repositories/` | 316 | persistence |
| `daemon/tools/` | 203 (68 `.py`) | agent tool layer |
| `daemon/routers/` | 133 | HTTP API |
| `daemon/migrations/` | 131 | schema |
| `daemon/sources/` | 72 | chat adapters |
| `daemon/governor/` | 8 | council contracts only |
| `tests/` | 2,958 | pytest tree |
| `test/` | 445 | pack scripts (`test/packs` = 367 `.sh` on disk) |
| `frontend/src/` | 442 | UI |
| `agents/` | 338 | agent prompts (DB-versioned artifacts mirrored here) |
| `plugins/` | 4,930 | vendored plugin data (opendesign design systems) |
| `scripts/` | 44 | upgrade/protection shell |

Key-file size (origin/latest): `daemon/graph.py` = 13,584 lines; `daemon/manager.py` = 16,791 lines.

**Is there any explicit "load-bearing"/critical-path MARKER?** No formal marker, registry, or taxonomy in code. The phrase appears only as prose in docs and agent prompts, e.g.:
- `docs/llm-stream-stall-hardening.md:57` ("branch order load-bearing")
- `docs/setup.md:1284` ("judge load-bearing for EVERY delegated completion")
- `docs/hallucination-protection.md:101,129,156` ("load-bearing for §7")
- `docs/mcps-gateway.md:1052` ("Process isolation is load-bearing")
- `docs/job-task-system.md:811`; `docs/jev-system-one-integration-discussion.md:66` ("smallest blast radius")
- Agent prompts mentioning blast radius: `agents/leader/workflow.md`, `agents/ari/rule.md`, `agents/reviewer[v2]/soul.md`, `agents/reviewer[v2]/skills-template/pr-review.md`, `agents/planner/skills-template/planning-strategy.md`, `agents/devops/{soul,rule,workflow}.md`, `agents/charter/rule.md`.

`QUARANTINE*` exists only as `.agents/tester/QUARANTINE.md` (a test-debt ledger — see §2), not as a code-module quarantine list.
**Verdict**: path-based taxonomy is DERIVABLE-TODAY (directory globs); a semantic criticality marker does not exist and needs building if wanted.

### 1b. Migration touches — already a natural high-risk marker?

- Migration corpus: **114 `.sql` on disk vs 129 at origin/latest** (+15 in ~2 weeks — high churn).
- The upgrade ceremony already treats the migration set as risk-differentiated: `stage.sh` (wt ~:383-392) refuses to silently DERIVE `rollback_safe` when destructive DDL (`DROP TABLE|DROP COLUMN`) is detected in `daemon/migrations/versions` — requires an explicit `ENSEMBLE_ROLLBACK_SAFE={0,1}`; `known_schema_gen` = migration head stamped into the manifest at stage time (comment wt :92-96).
- Convention (blueprint): every new SQLModel table requires its own `*.sql` migration file — 129 files at latest, zero exceptions. Migration test packs exist (`tests/migration/`, `tests/e2e/test_migration_e2e.py`, `tests/integration/test_migration*.py`).
- **Verdict**: touching `daemon/migrations/versions/` is DERIVABLE-TODAY (one glob) and is already ceremony-coupled (rollback_safe/known_schema_gen). Strongest single auto-escalation signal available.

### 1c. Prompt-artifact edits — the one existing daemon-ENFORCED risk gate

- `daemon/tools/prompt_artifacts.py::check_write` (:265) returns a `GUARD_ERROR` refusal string (never raises) for writes targeting versioned agent prompt artifacts (`agents/<name>/*.md`).
- Wiring: imported as `_check_prompt_artifact_write` in `daemon/tools/filesystem.py:34`; the two post-resolve refusal sites are `write_file` (`:1561`) and `edit_file` (`:1978`): `guard_err = _check_prompt_artifact_write(file_path); if guard_err: return guard_err`. Reads are explicitly NOT guarded.
- Approved edits flow through the DB-versioned prompt-artifact state machine with a HUMAN-SESSION-ONLY approval lane (blueprint: `agent-prompt-versioning`).
- Stage-time drift leg: `scripts/upgrade/stage.sh` (wt ~:687-702) invokes `scripts/protection/generate_prompt_artifact_baseline.py` against `agents/.prompt-artifacts.sha256`; missing checker/baseline/python is "non-fatal skip".
- **Verdict**: DERIVABLE-TODAY and partly enforced; an auto-gate would only need path matching (`agents/**/*.md`).

### 1d. Defect concentration from local git history (2026-08-12 → 2026-11-12)

Baseline: **5,562 commits** on origin/latest in window. Subject-strict defect set (subjects matching `^(fix|hotfix)(\(|:)` or containing `revert`/`incident`): **1,399 commits (25%)**. File-touch aggregation across the defect set (a commit touching 3 test files counts 3; this is a *touch-share* ranking, not a defect-count-per-subsystem):

| Path prefix | touches | share of defect-set touches |
|---|---|---|
| `tests/` | 1,801 | test churn accompanies fixes (expected) |
| `daemon/services/` | 862 | **largest production cluster** |
| `agents/` | 533 | prompt artifacts churn in fixes |
| `frontend/` | 479 | |
| `.agents/` | 286 | planning/evidence churn around fixes |
| `daemon/tools/` | 272 | |
| `daemon/repositories/` | 214 | |
| `scripts/` | 163 | upgrade/protection scripts |
| `daemon/routers/` | 146 | |
| `daemon/manager.py` | 120 | |
| `test/` | 109 | |
| `docs/` | 81 | |
| `daemon/graph.py` | 62 | |
| `daemon/config.py` | 59 | |
| `daemon/api.py` | 57 | |
| `daemon/mcp/` | 36 | |

Caveats: subject-line method under/over-counts (docs commits mentioning "incident" are included; a "fix" inside a body is excluded — bodies are not scanned); ~2-week window-end truncation. Relative shape (services ≫ tools > repositories > routers; graph.py/manager.py material) matches the seeded critical-notes clusters.

### 1e. `.agents` LESSONS + RESULTS corpora as incident-density signal

Hit counts (case-insensitive, over `.agents/tester/LESSONS` + `.agents/tester/RESULTS` + `.agents/reviewer` + `.agents/coder/RESULTS`):

| keyword | hits | | keyword | hits |
|---|---|---|---|---|
| quarantine | 1,326 | | oauth | 268 |
| POSTGRES_ | 620 | | report_delivery_recovery | 252 |
| compaction | 499 | | dependency_bus | 164 |
| child_reports | 280 | | pairing | 141 |
| boot.sweep | 60 | | snapshot_search | 33 |
| FAKE_HOME | 26 | | mission_strand_healer | 11 |
| tag.collision | 0 (variant spelling) | | merge_conflict | 0 |

Corpus is a usable *relative* density signal (grep-only, cheap) but is noisy: it reflects tester documentation habits as much as incident counts.

### 1f. Candidate signals for an automated blast-radius gate

| Signal | Derivation | Status | Cost |
|---|---|---|---|
| Core-path glob match (`daemon/graph.py`, `manager.py`, `services/**`, `repositories/**`, `api.py`, `config.py`, `tools/**`) | path match on change set | **DERIVABLE-TODAY** | one glob |
| Migration-dir touch (`daemon/migrations/versions/*.sql`) | path match; already ceremony-coupled | **DERIVABLE-TODAY** | one glob |
| Prompt-artifact touch (`agents/**/*.md`, `agents/.prompt-artifacts.sha256`) | path match; daemon already refuses runtime writes | **DERIVABLE-TODAY** | one glob |
| Per-subsystem defect-history weight | precomputed map from `git log` aggregation (§1d) | **DERIVABLE-TODAY** (needs periodic rebuild) | cheap, precompute |
| Corpus incident density | keyword counts over `.agents` corpora (§1e) | **DERIVABLE-TODAY** (noisy) | cheap |
| Changed-module → affected-test count (test-blast estimate) | grep test imports referencing changed modules | **NEEDS-BUILDING** (scriptable, not built) | medium |
| Semantic/call-graph blast radius (who imports the changed symbol) | import/call graph analysis | **NEEDS-BUILDING** | expensive |
| Review-round / rework count as risk weight | persisted cycle counters | **NEEDS-BUILDING** (not persisted — see §5) | medium |

**Bottom line for the proposal's "auto-escalate when load-bearing code is touched"**: a *useful* gate is buildable today from **path globs + migration-dir + prompt-artifact + a precomputed per-subsystem defect map** — all one-grep class. Semantic blast radius is the only part that genuinely needs building.

---

## 2. QUALITY-GATES INVENTORY (daemon-enforced vs prompt-convention vs ceremony)

### 2a. Council machinery — EXISTS (real code, agent-invoked, advisory)

- Tool layer: `convene_council` / `convene_council_with_skill` implemented at `daemon/tools/instance.py:797-815` (both forms handled; refusal strings name both), registered in `daemon/tools/_tool_registry.py:1664`.
- Contracts: `daemon/governor/contracts.py:1-23` — `SpawnCouncilorInput` (councilor_agent_id, model, initial_message; `version_tag` deliberately NOT exposed) / `SpawnCouncilorResult`. `daemon/governor/` is contract-only (8 files).
- Entry discipline (agent convention): `agents/architect/rule.md:37-40` — "my only council entry point and spawns a governor child which convenes councilors, each loaded with the matched `councilor_skill`"; "Max ONE council per question"; "After `convene_council_with_skill`, END TURN". Meta allow includes `"council"` (`agents/architect/meta.json`).
- What it gates: high-stakes/contested decisions — `agents/architect/skills-template/architecture-strategy.md:48` ("High-stakes + contested (irreversible, cross-system, multi-approach, high blast radius) → Council mode").
- **Verdict**: real machinery, but invocation is agent judgment at the architect lane — it is NOT an automatic gate on change sets.

### 2b. Test packs — registry-by-convention, 469 scripts, no machine runner

- Corpus: **469 `.sh` under `test/packs/` at origin/latest** (367 on disk). Registry: `.agents/tester/PACKS.md` = **3,053 lines, ~950 KB**.
- Row format (sample `:1424`): `| Pack | Invocation (ad-hoc) | Scope | Last Run | Status |` — e.g. `embed_consumers_unit_test` runs `timeout 300 bash test/packs/embed_consumers_unit_test.sh` (inner 240 s), scope lists 7 test files (269 nodes), duration "~3–4 min", status "PASS — 269/269 in 5.19s". Registry rows are appended per gate campaign with a human-readable header block (PACKS.md head).
- **Who executes packs**: the TESTER AGENT by convention. No script consumes PACKS.md as a runner (repo-wide grep found only comments in `scripts/upgrade/lib.sh` referencing `tests/packs/preflight_*` scripts — the upgrade pipeline's own preflight pack family — and a denylist regex in `daemon/tools/inner_soul.py:235`). POK packconv-03 pins the consequence: "a script with no registry row never enters the pack runner" — the runner IS the tester's procedure.
- **Targeted-subset precedent — already doctrine**: `.agents/tester/rules/ensure.md` (53 lines) line 5: "*Scoped by blast radius: validate only requirements relevant to the change set. Never run the full list unless blast-radius determines the change is big/critical/architecture (then also run the Release Gate).*" Lines 11/15: "Validated on every test request, scoped to the change set's packs." So "test only the most-related things" is the EXISTING written rule; the gap is tooling/measurement, not doctrine.
- Duration hints: **366 of 367** on-disk pack scripts carry `timeout` (layered `timeout 300` outer dispatcher wrap; e.g. `test/packs/adjacent_chat_unit_test.sh:6`), i.e. single-pack wall budgets are minutes, and full-gate campaigns are documented in hours (PACKS.md head: 101 shards × 36,822 nodes full collection).

### 2c. Pinned tests + QUARANTINE

- 10 pin-suffixed unit files (`ls tests/unit | grep -i pin`): e.g. `test_board_gap_pins.py`, `test_defer_busy_witness_caller_pin.py`, `test_mapper_e1_stranger_spawn_refusal_pin.py`, `test_phase1_ledger_pins.py`, `test_scheduler_deferred_delivery_pin.py`.
- `SEALED_SHA_BASELINES: dict[str, str]` at `tests/test_chart_image_delivery_e2e.py:1425` — hash-pins agent prompt artifacts against drift (mutation-proven style pins also used across gates).
- `.agents/tester/QUARANTINE.md` = **176 lines / 167 KB** — ledger of pre-existing failures that are skipped and "do NOT count toward a pack's PASS/FAIL"; every row carries base attribution (sha/base-leg evidence) + retry budget + status. Load-bearing for gate adjudication (a gate can proceed with a documented red set) — but it is a tester-convention artifact, not machine-enforced.

### 2d. Boot smokes + promote gates (ceremony level — script-ENFORCED unless noted)

From `scripts/upgrade/promote.sh` (origin/latest):
- Gate sequence (header `:21-27`): `/livez` ≤180 s → `/readyz` ≤120 s → version verify (`/livez` version field) → adaptive soak, all inside a 900 s window; soak: `ENSEMBLE_PROMOTE_SOAK_S` default **300 s** (`:36-37`, `:158-162`), **live lane refuses >300** (`:207-218`, exit 78), sandbox up to 900.
- Plugin staleness: **advisory by default** — `--allow-stale-plugins` (:95-103) journals `plugin_staleness_observed` and the promote PROCEEDS; strict mode opt-in (`PROMOTE_STRICT_STALENESS=1`).
- Stage side (`stage.sh`): provenance sidecar with `artifact_sha256` (`lib.sh:3280-3356`; stage re-hashes, never trusts sidecar), L1 tip-identity guard (`lib.sh:2753`), scripts-coverage stamp gate (`stage.sh` origin/latest :684-699: refuses staging unless `PIPELINE_SCRIPTS_REV` contains every shipped executor path), prompt-artifact drift leg (wt :687-702, non-fatal skips), `rollback_safe` derivation w/ destructive-DDL refusal (wt :383-392), manifest writes `binary_sha256`/`launcher_sha256` (~:705+).
- Boot smoke: daemon boot sweep + `BOOT_GRACE_S=60` machinery (blueprint self-upgrade); frozen-binary boot smokes pinned to `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` (critical note).

### 2e. Daemon-enforced in-process gates (beyond tests)

- Prompt-artifact write refusal — §1c (the only *production write* gate found).
- Loop breaker: `LOOP_BREAKER_DEFAULT_THRESHOLD = 3`, `LOOP_BREAKER_SUMMARIZATION_TIMEOUT_SECONDS = 30`, `LOOP_BREAKER_REPAIR_PREFIX = "repair-"` (`daemon/graph.py:188-199`; header says "Phase 1 — detection only" but the middleware replaces looping history with a repair summary per blueprint).
- RAM guard: `RAM_GUARD_ENV_SWITCH = "ENSEMBLE_RAM_GUARD"` (`daemon/tools/bash.py:829`) — tree-kill above ceiling; code in origin/latest; STAGED v0.29.21 per project notes (promote is the Emperor's).
- Compaction budgets: runtime token-threshold logic in `daemon/compaction.py` (not a gate).
- **Secret scan**: the blueprint's "FAIL-CLOSED secret scan (needle `sk-(proj|ant)-`)" is **NOT machine-enforced in this repo** — needle-shaped patterns appear only in `.env*` files (values — not read), test mocks, and planning docs; `agents/tester/workflow.md:907` carries "No hardcoded secrets" as a review convention. Marked PARTIAL/unverified; the tester's evidence-pack scan is practice-level.

### 2f. KEEP vs DROP material (facts only, no decision)

CHEAP (seconds; one grep/script step): prompt-artifact path gate; loop-breaker threshold; path-taxonomy blast check; 10 pin files; `rollback_safe` derivation; provenance hash verify; SOAK skip on sandbox.
HEAVY (minutes–hours): full/default test collection (PACKS.md head: 36.8k nodes, 101 shards); per-pack runs (366/367 scripts have a 300 s outer timeout; registry-documented multi-minute pack durations); regression-diff dual-side legs (~19k nodes/side per the 2026-10-25 leg-runtime investigation); 300 s live soak; mutation matrices (11-RED positive-shape runs per PACKS.md row).
AUDIT-EVIDENCE producers (consumed later): tester RESULTS files, PACKS.md rows, QUARANTINE.md rows, upgrade journal entries, sealed SHA baselines.

---

## 3. `.agents/` UPKEEP INVENTORY (repo side; mtime window since 2026-10-22)

### 3a. Top level (working tree)

| Dir | Files | Size |
|---|---|---|
| `.agents/tester/` | 2,205 | 51 MB |
| `.agents/shared/` | 1,543 | 35 MB |
| `.agents/approver/` | 128 | 1.1 MB |
| `.agents/reviewer/` | 10 | 172 KB |
| `.agents/coder/` | 3 | 64 KB |
| others (designer, devops, tidier, worker, project-skills) | 1–2 each | ≤192 KB |

**Naming reality check**: there are NO `.agents/{developer,planner,giter,architect}` dirs (same at origin/latest — tracked set: approver, coder, designer, devops, project-skills, reviewer, shared, tester, tidier, worker). Developer-era artifacts live under legacy `.agents/coder/` (3 RESULTS files, none in-window); planner/architect work product lands in `shared/planning/`.

Tester breakdown: `RESULTS/` 1,614 files / 27.9 MB (**37 in-window**); `EVIDENCE/` 97 / 20.6 MB (0 in-window); `LESSONS/` 488 / 2.2 MB (15 in-window); `PACKS.md` 3,053 lines / ~950 KB (mtime 11-11); `QUARANTINE.md` 167 KB; `MOCK_TESTS.md` 65 KB; `rules/ensure.md` 12 KB. `tidier/notes.md` 185 KB (mtime 11-11).

### 3b. `shared/planning/` (the commission-artifact store)

Totals: **198 subdirs, 1,502 files, 34.5 MB, 1 root file**. Per-subdir distribution: **median 5 files**, p90 = 11, max = 268 (`agent-base-v2`, 3.6 MB). **Only 17 of 198 subdirs have any in-window file** (181 untouched).

In-window activity (files / KB new-mtime): `metrics-unification` 37 / 1,285; `mcps-gateway-interop` 11 / 570; `skill-system-revival` 9 / 549; `nest-ops-shakedown-defects` 16 / 526; `nest-ops` 5 / 285; `wedge-capture` 6 / 271; `scheduler-deferred-alarm-delivery` 3 / 255; `ari-notify-user` 6 / 168; `unit-secret-exposure` 9 / 160; `df4-provenance-closure` 3 / 128; remainder ≤64 KB each. Largest historical dirs: `agent-base-v2` 268 files/3.6 MB; `skill-evolution` 46/552 KB; `langgraph-checkpoint-perf-v2` 38; `metrics-unification` 37; `designer-critic-orchestration` 37/496 KB.

### 3c. Per-agent tracking / shared root files

- `.agents/approver/`: **127 `*-tracking.md`** (one per commission; mean ≈8.7 KB each; max `scheduler-deferred-alarm-delivery-tracking.md` 33 KB, `assent-p15-tracking.md` 24 KB). **19 of 127 touched in-window**. `active.md` 3.5 KB, mtime 2026-11-12 17:51 — consumed by approver prompts (`agents/approver/soul.md:91,139`; `workflow.md:68`; `skills-template/approval-strategy.md:21,37,89,241`).
- `.agents/shared/context.md` = 21,132 B, mtime 2026-10-31 (5 recent commits listed); `conventions.md` = 1,485 B, mtime 2026-09-21 (2 commits — near-static).
- Other shared: `working/` 20 files/516 KB; `skill-snapshots/` 7/88 KB; `ensemble-reviews/` 1/24 KB; `handoff/` 1/12 KB; incident post-mortems at shared root (5 `incident-*.md` + analyses, e.g. `incident-20261031-restart-livelock-journal-sweep.md`).

### 3d. LOAD-BEARING vs DROPPABLE (evidence-based classification)

LOAD-BEARING (machine/gate/ceremony-consumed — keep):
- `.agents/tester/PACKS.md` — the pack registry (3,053 lines); tests treat `.agents/tester/` as the historical contract record (`tests/integration/test_lcancheck_family_separation.py:937,1043`; `tests/integration/test_secrets_v1_endtoend.py:87`).
- `.agents/tester/rules/ensure.md` — cited by tests (`tests/e2e/test_result_summary_emission.py:10`) and core to gate adjudication.
- `.agents/tester/RESULTS/` — cited by daemon code and fixtures: `daemon/migrations/runner.py:343`, `daemon/routers/vscode_proxy.py:126,505`, `daemon/services/attestation_judge_timeout_resolver.py:4`, `tests/fakes/f1_premark_helper.py:12`, `tests/probe/*`.
- `.agents/tester/QUARANTINE.md` — gate adjudication ledger (base-attributed red set).
- Planning docs cited by docs/scripts: `self-restart-upgrade-phase2/decisions.md` (ADR-035 cited by `scripts/upgrade/stage.sh:343` wt / :386 origin-latest) and `risk-register.md:25`, `docs/setup.md:1180-1181,1275-1276`, `docs/self-upgrade-live-rung-parked-decision.md:6,71-75`; `lib.sh:253` cites `soak-gate-mcp-warmup/requirements.md`.
- `.agents/approver/active.md` — approver identity file (agent-prompt-consumed, updated per commission).

DROPPABLE-ceremony candidates (no machine consumer found; historical-audit value is a policy call):
- `.agents/approver/*-tracking.md` — 127 files / ~1.1 MB, 108 untouched in 3 weeks; only the approver lane reads them, and only for its own iteration history.
- `.agents/tester/EVIDENCE/` — 97 files / 20.6 MB, 0 in-window.
- `.agents/tidier/notes.md` — 185 KB scratch notes.
- 181/198 planning subdirs with zero in-window activity (mostly historical narration); `reviewer/` root reports (~77 KB, 2026-08/09-era), `MOCK_TESTS.md` 65 KB.
- Caution: "droppable" must not be read as "delete" — these are committed audit trails; the probe is whether SONIC lanes should *write* them, not whether the archive should exist.

### 3e. Per-commission upkeep overhead (proxy)

Typical commission: **median 5 planning files (~100–200 KB) + 1 approver tracking file (~9 KB) + 1–3 tester RESULTS files per gate** (RESULTS are 1,614 files across ~2 years ≈ several per commission). Large commissions: 37 files/1.3 MB (`metrics-unification`), 16/526 KB (`nest-ops-shakedown-defects`); small: 1 file/36 KB (`skill-keeper-confinement`). PACKS.md rows add ≈1 table row per gate (~0.3 KB each, but the file itself is 950 KB and grows monotonically).

---

## 4. BASH TIMEOUT + DURATION INFRA

### 4a. Where the timeout lives (code)

`daemon/tools/bash.py` (origin/latest):
- Tool signature: `timeout: int | float | None = 1800` (`:1238`, `:1250`); docstring: "Timeout is in seconds (0-1800, default 1800)" (`:1263`).
- Validation: rejects `<0` (`:1265-1267`) and **rejects `>1800`** — "Timeout must be ≤ 1800 seconds" (`:1268-1269`).
- It is a **per-call, LLM-visible argument**. No per-agent or per-instance knob exists in `bash.py` (no `instance_metadata`/agent-meta read for bash timeouts). Agent `meta.json` carries only tool-specific timeouts elsewhere (`agents/sketcher/meta.json:28` `timeout_seconds: 90`; `agents/watcher/meta.json:35` `builder_timeout_seconds: 300`).

**Observed in production [Leg A]**: `resource_pid_map` (kind='bash', 75,185 exited rows in window) shows `exit_kind='timeout'` rows landing at **exactly 1800.0 s** — the cap is real and fired ~**79×** in the window.

### 4b. What `bash_spawn_registry` records (schema)

`daemon/repositories/bash_spawn/models.py:105-175`; migration `daemon/migrations/versions/20261031_110000_create_bash_spawn_registry.sql`:
`id, pid, pgid, start_time, tracking_id, instance_id, command_sanitized, boot_id, daemon_pid, created_at, reaped_at, reaped_by, reap_reason`.
- **No duration column, no exit code, no agent_id** (agent only via `instance_id` join). `command_sanitized` = "Executable + argv-shape ONLY … NEVER the env overlay" (model docstring).
- Purpose: orphan-reaper tombstone registry (survives child exit / daemon restart).
- **[Leg A] crucial correction**: 108,342 rows, **99.98% gc-reaped** — `reaped_at` = sweep time, NOT exit; durations derived from this table are garbage. `resource_pid_map` is the real duration substrate.

### 4c. `set_instance_tunable long_tool_call_threshold_seconds`

- Per-child `instance_metadata["long_tool_call_threshold_seconds"]`; validation range **[60, 1800]**, no silent clamp (`daemon/tools/tunables.py:36-95`); read-side floor 60 (`daemon/services/long_tool_nudge.py:88-94`).
- Semantics: a **NUDGE** threshold — when a tool call exceeds it, the long-tool-nudge service delivers a "still running" nudge message to the agent (`long_tool_nudge.py` precedence chain AD-41; kill-switch `LONG_TOOL_NUDGE_ENABLED`; env default `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS`; hard clamp `HARD_MAX_THRESHOLD_SECONDS = 1800` at `:319`). It is **not an enforcement/kill**.
- Coincidence worth recording: its ceiling (1800) equals the proposal's 30-min hard max — but the key neither defaults to 10 min nor terminates anything.

### 4d. Verdict: "≤10 min default, 30 min hard max"

**PARTIAL.**
- 30-min hard max — **EXISTS** (tool-layer cap at 1800 s, fired ~79× in window [Leg A]).
- 10-min default — **MUST-BUILD** (today the default IS the hard max; no per-agent/per-instance default exists). Natural home: an agent-meta field (e.g. `bash_timeout_default_seconds`) consumed at tool construction (`daemon/loader.py:491` imports `bash`; per-instance tool factory) or an instance-metadata read inside the bash tool — matching the existing `long_tool_call_threshold_seconds` pattern.
- 10-min auto-kill — **MUST-BUILD** (only the 1800 s timeout and the RAM guard terminate today).
- Design constraint from measurement [Leg A]: **>10 min = 80 runs (0.106%); >30 min = 1**; duration p50 0.0 s / p90 5.0 s. A 10-min default truncates ~0.1% of runs — but that tail is exactly the coder/tester build/mutation/`journalctl` work that ships releases → the default must remain **per-call overridable up to the existing 1800 s cap**.

---

## 5. MEASUREMENT SURFACE (fields available for a future success/rework/cost loop)

### 5a. Token/cost — NOT PERSISTED anywhere in repository models

Repo-wide scan of `daemon/repositories/*/models.py` for `prompt_tokens|completion_tokens|total_tokens|usage|cost|price`: **no token/cost columns exist**. Token counts live only as in-RAM compaction estimates (`daemon/compaction.py:1281-1290` `system_prompt_tokens`; pre-call `(msg_count, total_tokens)` cache `:2292-2364`). No per-job/per-instance dollar or token ledger → a generic "measure sonic cost" loop **MUST-BUILD** (or read from provider/proxy logs, which are another lane).

### 5b. Rework/success signals that DO exist

| Model | Fields | Where |
|---|---|---|
| `Job` | `job_type`, `agent_id`, `instance_id`, `priority`, `created_at`, `updated_at`, `retry_count`, `next_retry_at`, `failed_at`, status (incl. `dead`/DEAD_LETTER) | `daemon/repositories/job_queue/models.py:323-383` |
| Job history rows | `instance_id`, `agent_id`, `priority`, `retry_count`, `failed_at` | same file `:474-559` |
| `Task` | `retry_count`, `next_retry_at`, `retry_scheduled`, status incl. suspension reasons | `daemon/repositories/task/models.py:222-231` |
| `SkillUsageRecord` | **richest per-task telemetry**: `selected`, `applied`, `task_succeeded`, `iterations`, `duration_seconds`, `fallback`, `feedback_usefulness` (1–10), `feedback_improvement`, `agent_id`, `instance_id`, `task_message`, `created_at` | `daemon/repositories/skill/models.py:300-380` (skill-usage events only) |
| `ProjectSkillUsageRecord` | mirror of the above without FK | `daemon/repositories/project_skill_file/models.py:187-197` |
| `SnapshotUsageCounter` | per-(scope,key) usage counter | `daemon/repositories/snapshot/models.py:366-389` |
| `MessageMetadata` | per-(thread_id,message_id) timestamp row + mission stamp | `daemon/repositories/message_metadata/models.py:40-79` |
| `Event` | kind + `created_at` | `daemon/repositories/event/models.py:69-92` |
| Designer review rounds | `round_count` pinned per page in **shared_meta_kv** (KV, not a table column) | blueprint / designer lane |

**Missing for a success/rework loop**: review-cycle counts (no reviewer-round persistence), per-instance wall-clock aggregates (derivable from message/event timestamps + `resource_pid_map`), token/cost. Instance `version` is optimistic-lock, not iteration count (`instance/models.py:70`).

---

## 6. SYNTHESIS

The repo already contains most of what a "sonic" proposal needs *as detection material*, and almost none of it as *automatic enforcement*. Blast radius is derivable today from one-glob path matching plus a precomputed per-subsystem defect map (services ≫ tools/repositories in defect-touch share; migrations churn hard — 129 files at latest, +15 in 2 weeks — and are already ceremony-coupled through `rollback_safe`/`known_schema_gen`); the single existing daemon-enforced risk gate is the prompt-artifact write refusal (`prompt_artifacts.check_write` + two `filesystem.py` sites), which the proposal's gate could simply reuse as a path class. The scoped-test doctrine the proposal is asking for **already exists verbatim** in `ensure.md:5` ("scoped by blast radius … never run the full list unless blast radius determines the change is big/critical/architecture") — the real cost is that the pack corpus (469 scripts, 3,053-line registry, ≈37k-node full collection, 300 s soak, dual-side regression legs) has no runner/tooling to scope it, and its evidence trail (PACKS.md/QUARANTINE.md/RESULTS) is where the tester's effort goes; `QUARANTINE.md` is load-bearing precisely because gates adjudicate against a base-attributed red set. A slim contract could plausibly keep: the cheap script/daemon gates (prompt-artifact path gate, loop breaker, pin files, provenance/rollback derivation, path-class blast check) and drop or mechanize: per-commission narration (181/198 planning dirs idle; 127 approver tracking files, 108 idle), EVIDENCE writes (20.6 MB, zero in-window), and full-suite campaigns in favor of pack-scoped ones — while `ensure.md`-style Release-Gate escalation stays for the load-bearing path classes. On infra: the bash 30-minute cap already exists and demonstrably fires (79 × 1800.0 s), the proposal's ≤10-min default is a buildable agent-meta default with a measured 0.1% truncation tail that must stay overridable to 1800 s, `bash_spawn_registry` must NOT be used as a duration source (99.98% sweep-reaped; use `resource_pid_map`), and no token/cost telemetry exists at all — only retry counters and skill-scoped usage records — so the "measure sonic success/cost" loop is a genuine build, not a query.

---

## 7. Gaps & reliability notes (402-BLOCKED)

- **402-BLOCKED**: the provider coding-plan quota exhausted mid-run (resets 2026-11-17T11:44:37Z). Worker B (gates inventory) died before its first tool call; Worker A (blast-radius signals) died after ~20 tool calls mid-investigation. Both were terminated by the provider wall — no running workers remain.
- **Backfill**: §2 (gates) and §1a/1b/1c/1d/1e (blast signals) were executed directly by the controller after the workers died. §1d's defect aggregation is the controller's *subject-strict approximation* (file-touch shares, 2-week window-end truncation, no body-text scan) — a bespoke worker-quality aggregation was not obtained.
- **Not attempted**: semantic/call-graph blast-radius analysis (imports/callers of changed symbols); per-pack runtime measurement from execution logs (would need tester-lane log forensics); a rigorous defect-commit classification (bugfix vs docs-mentioning-incident) beyond subject heuristics.
