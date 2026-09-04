# Phase 0 State — COMPLETE (T0.1–T0.7 done; T0.8 folded into Commit A)

> Date: 2026-09-03
> Branch: `feature/langgraph-checkpoint-perf-v2` @ `2f80d45b`
> Implementation phase: Phase 0 (Preflight)
> Historical stop reason (now resolved): `_build_pg_connection_string` resolves to `postgresql://.../ensemble_prod` via env override `POSTGRES_DB=ensemble_prod`. Per phase0-plan.md T0.2 HARD RULE — STOP IMMEDIATELY was correctly invoked; caller adjudication re-pinned to `ensemble_cpv2_test` (see §"T0.2 ADJUDICATION & EXIT PROOF").

## T0.1 (DONE — partial; sufficient for stop documentation)

| Field | Value |
|---|---|
| Branch | `feature/langgraph-checkpoint-perf-v2` |
| HEAD SHA (short) | `2f80d45b` |
| HEAD SHA (verified on entry) | `2f80d45b` (no checkout performed; the user owns the worktree) |
| Langgraph version (`uv pip show langgraph`) | `1.0.9` |
| `langgraph-checkpoint-postgres` | `3.1.0` (matches expected pin) |
| `langgraph-checkpoint` | `4.1.1` |
| `langgraph-checkpoint-sqlite` | `3.0.3` |

### Plan corpus self-sufficiency note (T0.1/T0.2 era)

The plan corpus is self-sufficient even if the external source doc `~/Downloads/langgraph-checkpoint-performance-discussion.md` disappears. All load-bearing content from it (§32 observability, §33 guardrail, Solutions A–U, the per-PR landing table's reference points, PERF-1..9 numeric anchors) is embedded in the in-repo planning corpus: `technical-analysis.md` (carry-over spec content), `requirements.md` (§32/§33 imperatives codified as FR-5/6/7), and `architecture-recommendation.md` (§7 Source-Doc Gap Triage absorbs Solution L via PR2; rejects B/F/G/H/O/P/R/S/T/U with rationale). No §1..§31 source-doc content is referenced by any Phase 0..5 task; if the external file is removed, the corpus remains complete.

### Working-tree state (verified on entry — `git status --short`)

```
 M .agents/approver/active.md
 M .agents/shared/planning/job-task-retrospective/decisions.md
?? .agents/approver/langgraph-checkpoint-perf-v2-tracking.md
?? .agents/shared/planning/defer-gate-fix/
?? .agents/shared/planning/langgraph-checkpoint-perf-v2/
```

Notes:
- `.agents/approver/active.md` is **NOT** in the plan's T0.1 expected-modifications list. The task brief acknowledges this and instructs: record it and flag in the report (approver live state; do NOT touch). **Confirmed: never staged.**
- `.agents/shared/planning/job-task-retrospective/decisions.md` — user live work, never staged.
- `.agents/shared/planning/defer-gate-fix/` — user live work, never staged.
- `.agents/approver/langgraph-checkpoint-perf-v2-tracking.md` — expected untracked (approver iteration 001 doc).
- `.agents/shared/planning/langgraph-checkpoint-perf-v2/` — the planning corpus I'm working in.

## T0.2 — STOP CONDITION FIRED (Disposable-PG guard)

### Env-path check (POSTGRES_URL / partial env vars)

| Env var | Value |
|---|---|
| `POSTGRES_URL` | unset |
| `POSTGRES_HOST` | `localhost` |
| `POSTGRES_PORT` | unset (fallback to config) |
| `POSTGRES_DB` | **`ensemble_prod`** |
| `POSTGRES_USER` | `ensemble` |
| `POSTGRES_PASSWORD` | unset (fallback to config) |
| `ENSEMBLE_CONFIG` | unset (default `./config.yaml`) |

### yaml-assembly path (resolved DSN via `_build_pg_connection_string` logic)

`_build_pg_connection_string` resolution order (per `daemon/persistence.py:66-93`):
1. `POSTGRES_URL` — unset → continue
2. `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` — env override config fields
3. `config.postgres` — values from `ensemble.json`

`config.yaml` at the repo root has **no** `postgres:` block (verified via `grep -n "postgres" config.yaml` → 0 matches). The actual postgres fields are loaded from `ensemble.json` (NOT `config.yaml`). The plan's "config.yaml:postgres.*" wording is inaccurate for this repo; the correct yaml-assembly target is `data/ensemble.json` (default) or `data_dev/ensemble.json` (dev target). Both contain disposable DB names; the env override `POSTGRES_DB=ensemble_prod` is what triggers the STOP, not the yaml file content.

**Resolved DSN (using env override → ensemble.json fallback):**

```
postgresql://ensemble:<password-from-ensemble.json>@localhost:5432/ensemble_prod
```

- `db` resolved from env: `ensemble_prod` ← STOP source
- `db` resolved from `data/ensemble.json` (yaml-assembly alone): `ensemble_dev`
- `db` resolved from `data_dev/ensemble.json` (alt yaml-assembly): `ensemble_test`

### Verdict

Per phase0-plan.md T0.2 HARD RULE:
> if either resolved DSN's current_database() contains "prod" or points at any non-disposable DB → STOP IMMEDIATELY, report, change nothing else.

**STOP. The env-override path resolves to `ensemble_prod`. No port-code touched; no commits made; no further Phase 0 work attempted.**

## T0.3..T0.8 — BLOCKED by T0.2 STOP

All downstream Phase 0 tasks are blocked because they reference disposable-PG state that has not been verified. Per the task brief, do NOT proceed.

## Phase 0 hygiene folds — BLOCKED

All 8 hygiene folds from the dispatcher brief (requirements.md FR-15/AC-13.3; line-anchor qualifications; phase4-plan.md Rollback precondition; "Option B" → "manual re-apply" replacement; GATE_SUITES.txt path fix; T3.3 wording; Status Draft → Approved; ~/Downloads file verification) are blocked by T0.2 STOP. They will be applied after the disposable-PG gate is resolved.

## Phase 1 (PR1 port) — BLOCKED

All T1.1..T1.8 are blocked.

## Commits made

**Zero commits made.** Per T0.2 STOP, do not stage or commit anything.

## T0.2 ADJUDICATION & EXIT PROOF (caller adjudicated, 2026-09-03)

### Adjudication record

Per caller decision (relayed via dispatcher, 2026-09-03):
- Dedicated disposable DB: **`ensemble_cpv2_test`** on `localhost:5432`, user `ensemble`, password from `POSTGRES_PASSWORD` env (same as the source the original T0.2 resolution used).
- DB did not exist at adjudication time → **created via maintenance DB** (`postgres`) with `CREATE DATABASE ensemble_cpv2_test OWNER ensemble;` — verified via `pg_database` lookup; size `8537 kB` (empty cluster init size).
- **Per-invocation pinning rule**: every DSN-resolving command (psql, pytest, scripts) MUST set BOTH `POSTGRES_URL` AND `POSTGRES_DB`. `persistence.py:87` reads `POSTGRES_DB` directly — overriding only `POSTGRES_URL` is insufficient. Per-invocation prefixing is the operative mechanism because the session env cannot be unset.
- Prod (`ensemble_prod`) and dev (`ensemble_dev`) untouched. `ensemble_cpv2_test` is dedicated to this port lifecycle.

### T0.2 exit proof (pinned DSN)

```
POSTGRES_URL=postgresql://ensemble:$POSTGRES_PASSWORD@localhost:5432/ensemble_cpv2_test
POSTGRES_DB=ensemble_cpv2_test

SELECT current_database(), current_user;
  current_database  | current_user
--------------------+--------------
 ensemble_cpv2_test | ensemble
```

- current_database = `ensemble_cpv2_test` — no "prod" substring — **T0.2 PASSES**.
- current_user = `ensemble` — matches session user.

### Hygiene fold #2 EXTENSION (per adjudication)

`phase0-plan.md` T0.2 text references `config.yaml:postgres.*` as the yaml-assembly target. The repo-root `config.yaml` has NO `postgres:` block (verified via `grep -n "postgres" config.yaml` → 0 matches). The actual yaml-assembly target in this repo is `data/ensemble.json` (db=`ensemble_dev`) or `data_dev/ensemble.json` (db=`ensemble_test`). The plan text is corrected post-hoc.

### Never-stage list (EXTENDED per adjudication)

The Commit A/B staged-set EXCLUDES:
- `.agents/approver/active.md` (live approver state, currently `M`)
- `.agents/shared/planning/job-task-retrospective/` (user live work)
- `.agents/shared/planning/defer-gate-fix/` (user live work)

## T0.3 — PG version capture + dialect parity pre-check

### PG version (both paths converge on `ensemble_cpv2_test` post-pinning)

- Parsed version: PostgreSQL **14.22** (Homebrew) on aarch64-apple-darwin23.6.0
- Threshold: ≥ 14.22
- Verdict: **PASS** (equality satisfies ≥)
- Captured to `phase0-pg-version.txt` (env-path DSN pin; yaml-assembly DSN resolves to the same DB post-pinning)

Binding-gate eligibility for Phase 5: **eligible** (PG 14.22 meets threshold; Phase 5 PR5 acceptance gate is a real-PG binding gate, not a "passes for degenerate reasons" run).

### Dialect parity pre-check — SKIP-LOUDLY with documented reason

Per T0.3: run v1's `tests/unit/repositories/test_message_metadata_repository.py` against v2 PG.

Result: **SKIP-LOUDLY** with documented reason.

- v2 tree does NOT contain `daemon/repositories/message_metadata/` (the implementation repo created by v1's PR2 `fa31a520`). Phase 2 (v2 PR2) is the deliverable for that directory.
- v2 `tests/unit/repositories/test_message_metadata_repository.py` does not exist; the file is part of v1's PR2 surface (not PR1's 13-file surface).
- The test's first import line (`from daemon.repositories.message_metadata.models import MessageMetadata`) raises `ModuleNotFoundError` against v2 — confirmed via `--collect-only`.
- v1 file content extracted to `tests/unit/repositories/test_message_metadata_repository.py` for collect-only verification, then deleted (NOT staged; v2 tree clean).

**Reason for SKIP-LOUDLY**: the test cannot run in v2 until Phase 2 lands the `message_metadata` repository implementation. The dialect-parity question (does v2 PG 14.22 support the SQLAlchemy features the test exercises?) is answered indirectly: PG 14.22 supports all standard SQL features (ON CONFLICT DO NOTHING, composite PK, indexes, nullable columns) the test relies on. No `alist`-style forbidden operations. Phase 2 will run the test natively after the implementation lands; the pre-check is structurally upstream-blocked.

### SKIP-LOUDLY disposition

- Captured here and in phase0-state.md.
- Phase 2 (PR2 port) will re-run this test against the pinned DSN after `daemon/repositories/message_metadata/` lands.
- No execution blocker for Phase 0 exit.

## T0.5 — WC-wake kill-switch state (per T0.5 acceptance)

| Source | Value |
|---|---|
| `ENSEMBLE_WC_WAKE_ENQUEUE` env | **unset** |
| `config.yaml` wc_wake_enqueue key | **absent** (no matches via `grep -rn "wc_wake\|ENSEMBLE_WC_WAKE" config.yaml`) |
| Resolver default (per `daemon/services/instance_messaging.py:121-128`) | **OFF** (legacy WC→injection; blanking the env mid-incident is the instant-revert path) |
| `daemon/constants.py:608` kill-switch | `WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED` — separate from wc-wake; default OFF, log-only soak |
| **Current state** | **OFF** (default — no env override active) |

Phase 5 T5.16 + drift-regression will verify this state is preserved across the port.

## T0.5 STOP — Phase 0 baseline reveals 29 NEW pre-existing failures NOT in QUARANTINE.md

Per T0.5 coupling in phase0-plan.md, the baseline run is a precondition gate. Full baseline result: `phase0-baseline.md`.

**STOP CONDITION FIRED**: 29 new pre-existing failures (test_settings_api.py × 12 + test_builtin_mcp_servers.py × 17) NOT documented in `.agents/tester/QUARANTINE.md`.

### Root cause attribution

1. **`tests/test_settings_api.py × 12`** — `psycopg.errors.InvalidSchemaName: no schema has been selected to create in`. The test file's `pg_engine` fixture builds its own DSN from `PG_TEST_*` env defaults (db `ensemble_test`), ignoring `POSTGRES_URL`/`POSTGRES_DB`; the connected DB `ensemble_test` is missing its `public` schema, so `SQLModel.metadata.create_all`'s unqualified `CREATE TABLE` fails with SQLSTATE 3F000 (`search_path=public` resolves to nothing there). `ensemble_cpv2_test` is NOT the implicated DB — its `public` schema exists and accepts DDL as role `ensemble` (probe-verified 2026-09-03; rider probe `phase0-rider-probe.md`). Last touched 2026-07-22 (`6ceb6c31`) — pre-dates this port branch.
2. **`tests/unit/test_builtin_mcp_servers.py × 17`** — `AttributeError: Mock object has no attribute 'slash_commands'`. The `mock_config` in the test lacks the `slash_commands` field added by the slash-commands subsystem (per project blueprint, 2026-09-01). Last touched 2026-08-30 (`694b091c`) — pre-dates slash-commands.

Both are pre-existing test infra / fixture drift. Neither root cause is on the checkpoint-performance port's code path (port touches `daemon/persistence.py` + `daemon/services/maintenance.py` + adds `daemon/checkpoint_perf.py` + test fixtures; no interaction with `mock_config.slash_commands` or `pg_engine` schema setup).

### Disposition (requires architect adjudication)

The plan requires architect adjudication before Phases 1..5 proceed on a contaminated signal. Per dispatcher / architect:

- **Option A** — Add a new QUARANTINE.md row covering the 29 failures with pre-existing attribution + no pack deselect. Allows Phase 0 to exit GREEN.
- **Option B** — Dispatcher / architect confirms the 29 are out-of-scope; port proceeds with the documented regression signal. Phase 1..5 are explicitly NOT expected to fix them.
- **Option C** — Dispatcher / architect fixes the root causes (e.g. add `search_path` to test_settings_api's pg_engine; add `slash_commands` to test_builtin_mcp_servers' mock_config) BEFORE the port continues. This is a separate workstream.

### Phase 0 state — COMPLETE (T0.5 adjudicated → OUT OF SCOPE ×29 + T0.6/T0.7 DONE; T0.8 folded into Commit A)

**T0.5 adjudication (caller, 2026-09-03):** 29 NEW failures adjudicated as **baseline signal, OUT OF SCOPE for the port** (the port's code path is in `daemon/persistence.py` instrumentation + `daemon/services/maintenance.py` timing + new `daemon/checkpoint_perf.py` + test fixtures — orthogonal to `pg_engine`'s DSN alignment AND to `mock_config.slash_commands`). Attribution preserved with probe-verified citations:
- `tests/test_settings_api.py × 12` — `pg_engine` fixture-DSN drift (rider-probe outcome A, `phase0-rider-probe.md`; `ensemble_test` public schema missing, NOT `ensemble_cpv2_test` — probe-verified public-schema-accepts-DDL)
- `tests/unit/test_builtin_mcp_servers.py × 17` — pre-existing mock-drift family (`mock_config.slash_commands`; analogous to row 17 archetype)

**T0.6:** **DONE** — per `phase0-t0607-results.md`: QUARANTINE.md rows 34-37 captured (`_ManagerStub` rows, 4 tests across `tests/test_injection_slot.py` + `tests/test_injection_cleanup.py` — manager.py:3488 `_cleanup_instance_state` calls `self._deferred_watchover_terminate.discard(instance_id)`); the files are NOT pack-deselected (the canonical `injection_unit_test` pack script is absent — only `blueprint_injection_unit_test.sh` and `context_injection_unit_test.sh` exist); isolation-run half **SKIP-LOUDLY** with documented reason (the two `tests/unit/services/test_message_tap_slot.py` + `tests/unit/repositories/test_message_tap_to_repo_liveness.py` files are v1-PR2 surface and do NOT exist at v2-base @ 2f80d45b — structurally upstream-blocked until Phase 2 per the T0.3 dialect-parity SKIP-LOUDLY precedent).

**T0.7:** **DONE** — per `phase0-grep-baseline.md`: 4 guards captured verbatim at v2-base:
1. `grep -rn "settled" docs/job-task-system.md` → 14 lines (canonical-vocabulary state; transport single-owner)
2. `grep -n "tap_node_return" daemon/graph.py daemon/services/instance_messaging.py` → 0 call sites at v2-base (PR2 surface absent; Phase 2 deliverable)
3. `ls daemon/migrations/versions/ | grep -E "20260" | sort | tail` → tail = `20260819_000001_report_injections_deferred_marker.sql` (v2 ordering anchor)
4. `grep -rn "atomic" daemon/services/checkpoint_prune.py daemon/checkpoint_adapter.py` → exit 2 (`daemon/services/checkpoint_prune.py` does NOT exist at v2-base — PR4 surface) + 0 in `daemon/checkpoint_adapter.py`

**T0.8:** **Folded into Commit A** — no separate commit needed; Commit A picks up the four new Phase 0 artifacts (`phase0-state.md`, `phase0-pre-counts.md`, `phase0-baseline.md`, `phase0-grep-baseline.md`) as part of its worktree-hygiene confirmation (per T0.8's acceptance criteria, with the explicit never-stage list preserved: `.agents/approver/active.md`, `.agents/shared/planning/job-task-retrospective/`, `.agents/shared/planning/defer-gate-fix/`).

**Phase 1:** **UNBLOCKED after Commit A** lands — Commit A green-lights Phase 1 (PR1 port).

- T0.1: DONE
- T0.2: DONE (caller adjudicated, ensemble_cpv2_test on pinned DSN; T0.2 STOP record preserved below)
- T0.3: DONE (PG 14.22, dialect parity SKIP-LOUDLY with reason)
- T0.4: DONE (v2-base pre-counts + addopts diff)
- T0.5: DONE (baseline run; 29 NEW pre-existing failures; **caller adjudicated OUT OF SCOPE for the port** — see root-cause attribution below)
- T0.6: DONE (QUARANTINE _ManagerStub rows captured; isolation-run SKIP-LOUDLY with reason — `phase0-t0607-results.md`)
- T0.7: DONE (4 guards captured verbatim at v2-base — `phase0-grep-baseline.md`)
- T0.8: folded into Commit A (pending)
- Commit A: BLOCKED (depends on T0.8 / pending dispatch)
- Phase 1: unblocked after Commit A

**Historical stop record (preserved per task instruction "append/update, don't delete history"):** T0.2 STOP was correctly invoked before adjudication; the adjudicated exit proof + the prior phase-by-phase progress tracker appear in §"T0.2 ADJUDICATION & EXIT PROOF" and §"Phase 0 state — awaiting adjudication" (now superseded by the COMPLETE status above).