# Plan Overview: Service Tool Category

Date: 2026-09-15
Author: planner[v2] via plan-creation worker
Status: **Phase-0 gate PASSED — D4 RESOLVED (Option A, leader-ratified 2026-09-15)**; 14 architect amendments folded in (see `architecture-recommendation.md` §3 + `decisions.md`); A1/A2/A5 BLOCKING for Phase 1
Verified at SHA: `f6ca8791` (branch `latest`; `feature/service-tool` branched from this commit)

**BRANCH NOTE (amended per leader ratification 2026-09-15):** `feature/service-tool` is being synced to current `latest` by `giter` in parallel. Implementation targets the **synced branch**, NOT the stale dirty worktree. Plan-time file:line anchors have drifted on the dirty worktree — implementers MUST re-locate every anchor by **SYMBOL**, not line number (architect A10). Verified file:line ranges in this plan reflect `latest` at `f6ca8791`. Anchor corrections are folded into the affected phases; the architect's corrected `api.py` anchors are: eligible `:636-682`, orphan `:698-733`, JobLockSweep `:735-780` (new), shutdown `:1512-1556`, vscode boot `:1167-1230`; strip helper `~:4743-4760` (plan's `:4667-4684` is stale); manager wiring `:1257`/`:6367` (plan's `:690-693` is a local probe in restart-wipe).

Companion artifacts (read ENTIRELY before executing any phase):
- `decisions.md` — binding D1–D7 + OQ#1–7 (all RESOLVED per `architecture-recommendation.md` §4)
- `technical-analysis.md` — 5-dimension deep-dive + alternatives considered
- `research-tools-subsystem.md` — 3-step registration seam + 10-step new-category checklist + privilege-pin details
- `research-lifecycle-killsites.md` — 13-site kill inventory + exemption proof sketch
- `research-persistence-migrations.md` — dual-dialect migration rules + 3-site index pattern + 11-step new-table + boot-service checklist
- `architecture-recommendation.md` — architect's Phase-0 gate verdict + 14 amendments (A1–A14) — **READ ENTIRELY**; this is the binding amendment source

Sibling plan precedent: `.agents/shared/planning/spawn-intelligence-override/plan-overview.md` (style template).

---

## Objective

Add a new daemon tool category `service` that lets agents start long-lived processes (dev servers, databases, watchers) which **(1) live OUTSIDE instance lifecycle** — termination, cancellation, cleanup, and GC never kill or reap them; **(2) survive daemon restart** via boot-time reconciliation; **(3) are NOT OS services** — never registered with launchd/systemd/launchctl, purely daemon-managed detached processes owned by the kernel after `setsid`. The category ships 5 tools (`service_start`, `service_stop`, `service_status`, `service_list`, `service_logs`), persists state in a new `service_tracking` table, and gates behavior behind `ENSEMBLE_SERVICE_TOOL_ENABLED` (default ON) plus a max-concurrent cap of 10 and a 90-second periodic reconcile sweep.

A single sentence that, when true, marks the feature complete: **an agent with `tools.allow=["service"]` can call `service_start(name, command)` and the resulting process survives instance termination, pause/cancel, daemon graceful shutdown, and daemon hard restart; on the next agent call to `service_status(name)` after restart, the row returns `status="running"` with a live PID that matches the original `(pid, start_time)`.**

---

## Background

agents-ensemble has no daemon-side mechanism for long-lived processes today. Both spawn surfaces — `bash` (`daemon/tools/bash.py:111-119`) and `proc_run` (`daemon/tools/proc_tools.py:1838-1852`) — hold in-memory registries that:
- (a) **are killed on instance termination** — `instance_lifecycle.py:2330-2354` walks `get_background_process_manager().cleanup_instance(...)` then `get_bash_process_registry().cleanup_instance(...)` and SIGKILLs every tracked group.
- (b) **are killed on daemon shutdown** — `manager.py:10922-10952` runs the matching `cleanup_all` for every instance bucket.
- (c) **are documented crash-recovery leaks** — `bash.py:74-79` and `proc_tools.py:1636-1643` explicitly note that nothing survives a daemon restart, including for hard SIGKILL.

There is exactly **one prior art** for daemon-managed detached processes: `upgrade_journal.spawn_executor` (`daemon/tools/upgrade_journal.py:1010-1034`), which uses `subprocess.Popen(start_new_session=True, close_fds=True, stdin=DEVNULL, stdout/stderr→log file)` and **never registers** the child in any teardown registry. Its docstring states the child "must survive BOTH tool-harness teardown and daemon death" — that is the binding precedent for `service` (D1).

The kill-site inventory (`research-lifecycle-killsites.md`) shows all 13 process-kill primitives in the daemon are **registry-scoped**: no `/proc` walk, no `killpg(0)`, no ppid traversal, no session-wide sweep anywhere in `daemon/`. Exemption is therefore provable by construction: a process that is **never registered** and that **never joins an existing process group** is unreachable by every kill site found (K1–K13).

The 3-step tool registration seam (`research-tools-subsystem.md`) — decorator + `CATEGORY_MODULES` entry + factory call + `tools.extend(...)` — is mandatory; decorator-only registrations are silently invisible (precedent at `instance.py:4586-4592`). For a default-closed category the only mechanism today is `PRIVILEGED_TOOL_CATEGORIES` (`_tool_registry.py:124-128`), which mandates **same-PR pin updates** in THREE test files (D4 + A14 — architect's triple-pin discovery at `architecture-recommendation.md` §1.3).

---

## NON-Goals

The following are **deliberately excluded from v1** and must not be scope-crept into the implementation:

- **No OS services** — `service` never registers with launchd, systemd, launchctl, or any init system. Detached processes only. The hard constraint is documented in the category's module docstring + `CATEGORY_DOC`.
- **No command allowlist / sandboxing** — agents pass any argv; OQ#4 deferred. Adding an allowlist to `service` while `bash`/`proc_run` remain unrestricted would be security theater (D5 rationale).
- **No re-spawn of dead services** — `ServiceReconciliationService` marks rows `EXITED`; it never signals or respawns. Re-spawn is racy (old process may still be alive; side effects; cwd/env may have changed; agent may not exist post-revive).
- **Not a `bash`/`proc_run` replacement** — both remain default-open with their current instance-bound lifecycle. `service` is a sibling category with orthogonal semantics.
- **No log rotation owned by the tool** — operator-side (OQ#3); the v1 cap is `MAX_LOG_TAIL_BYTES = 10MB` in `service_logs`.
- **No multi-daemon coordination** — single-daemon-only constraint documented in v1 (OQ#2).
- **No per-category `default_open: bool` attribute** — OQ#1 follow-up PR if architect approves; v1 uses the existing `PRIVILEGED_TOOL_CATEGORIES` mechanism.
- **No telemetry counters / dashboards** — per-call INFO logs are sufficient for v1.

---

## Scope

### In Scope (files / modules touched)

- **New files**:
  - `daemon/tools/service_tools.py` — `@register_tool_category("service")` + 5 `@tool` definitions + factory + `CATEGORY_NAME`/`CATEGORY_DOC` + per-tool `_full_doc_`.
  - `daemon/tools/service_spawner.py` — thin module-level `spawn(argv, log_path, cwd) -> (pid, start_time)` + `get_process_start_time(pid) -> int | None` + `stop(pid, force=False)` + `kill_log_path(name)`. ~80 LOC.
  - `daemon/repositories/service_tool/__init__.py`, `models.py`, `repository.py` — SQLModel table + sync `sqlmodel.Session` repository (per-domain pattern at `daemon/repositories/task/`).
  - `daemon/services/service_reconciliation.py` — periodic service (template: `daemon/services/eligible_pending_sweep.py:60-215`).
  - `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_service_tracking.sql` — dual-dialect `.sql` (NO `DROP CONSTRAINT`, NO PG-only DDL per the 20260714 trap).
  - `tests/unit/tools/test_service_registration.py` — registration seam + privilege-pin pin.
  - `tests/unit/repositories/test_service_tool_repository.py` — file-backed SQLite fixture (per `test_chart_tools_reuse_integration.py:80-109`).
  - `tests/unit/services/test_service_reconciliation.py` — sweep kills nothing; idempotent start/stop.
  - `tests/unit/tools/test_frozen_tool_name_discovery.py` (or augment existing) — KNOWN_TOOL_NAMES regen pin.
  - `tests/integration/test_service_tool_kill_site_exemption.py` — site-by-site exemption matrix vs the 13 kill sites.
  - `test/packs/service_tool_pg_smoke_integration_test.sh` — PG smoke pack mirroring `ensure_deferred_pg_smoke_integration_test.sh`.
- **Modified files**:
  - `daemon/tools/_tool_registry.py` — `CATEGORY_MODULES["service"]` entry; `DYNAMIC_TOOL_NAMES` entries; `PRIVILEGED_TOOL_CATEGORIES` frozenset add (D4).
  - `daemon/tools/instance.py` — factory call + `tools.extend(...)` block before `scan_tools_for_full_docs` (`instance.py:4659`).
  - `daemon/loader.py` — warm-list factory import + `None`-manager stub (`loader.py:58-62, :79-90`).
  - `daemon/manager.py` — `InstanceManager.__init__` constructs `ServiceManager` + `ServiceRepo` (mirroring `manager.py:690-693`); `_ensure_postgres_columns` extension for `service_tracking` + 2 indexes (`manager.py:4787+`, pattern at `:5409-5412`); expose `self._service_tool_manager` for lifespan boot.
  - `daemon/api.py` — lifespan boot hook for `ServiceReconciliationService` (insert after OrphanWatcherSweep at `api.py:656-696`, before vscode `:1400-1406`); lifespan shutdown getattr-guarded stop (`:1367-1397` pattern).
  - `daemon/config.py` — `_resolve_service_tool_enabled` resolver; `_resolve_service_tool_max_concurrent`; `_resolve_service_tool_reconcile_interval` (renamed `service_tool_reconcile_interval_seconds` per A8 as a real `ServicesConfig` `Field(ge=1)`); boot-probe log line at `load_config` (style: `config.py:3513-3591`); `ServiceToolConfig` block.
  - `tests/unit/tools/test_upgrade_registration.py` — frozenset pin update at `:105` (D4 same-PR rule, A14 third pin).
  - `tests/unit/tools/test_attestation_registration.py` — frozenset pin update at `:158` (D4 same-PR rule, A14 third pin).
  - `tests/integration/test_maintenancer_spawn_resolves_tools.py` — frozenset pin update at `:292` (A14 third pin — **architect's missed-pin discovery**).
  - `tests/test_loader.py` — `TestMaintenancerToolsDocColdBoot` augmentation at `:966-1042` (add `service` category section).
- **Docs**:
  - `docs/architecture/instance-lifecycle.md` (or canonical lifecycle doc) — document the "services are kill-exempt" invariant so future contributors don't accidentally add a service-cleanup step to `cleanup_all` (Technical-Debt item 5).

### Out of Scope (with reason)

- **OS service integration** — caller-stated hard constraint ("NOT OS services"). Implementing launchd/systemd registration would violate it.
- **Per-command sandboxing** — OQ#4 deferred; v1 risk surface is bounded by the cap=10 abuse guard, not a per-command filter.
- **Re-spawn on boot** — D6 explicitly rejects; racy and side-effectful.
- **Multi-daemon against the same DB** — OQ#2 deferred; document single-daemon-only in v1.
- **Log rotation owned by the tool** — OQ#3 deferred; operator-side.
- **Migration of bash/proc semantics** — orthogonal feature; both remain instance-bound.
- **Per-category `default_open` flag** — OQ#1 follow-up; v1 uses privileged frozenset (functionally identical for our purposes).
- **Telemetry counters / dashboards** — deferred to v2; per-call logs are sufficient.

### Adjacent features deliberately excluded

- **Generic detach helper** — `service` is the only consumer of the spawn pattern. If a future feature needs detachment, it can copy `service_spawner.spawn` (or import it).
- **`proc_run` → `service` upgrade path** — out of scope; if a feature wants survival, the agent must call `service_*` instead.

---

## Phases

| Phase | Name | Objective | Primary files | Independent test gate | Status |
|-------|------|-----------|----------------|------------------------|--------|
| **0** | Architect sign-off gate | **PASSED 2026-09-15** (leader-ratified) — D4 = Option A; 14 amendments folded in (`decisions.md`); implementers proceed under amended contracts | none (planning only) | Architect sign-off recorded in `decisions.md` §D4 + OQ dispositions | **RESOLVED** |
| **1.A** | Store + migration (**SEQUENTIAL first per F3**; lands before 1.B/1.C; freezes ServiceRepo API incl. A13 `mark_exited` row-count contract via 1.A.0 frozen-interface block) | SQLModel table + sync repository + dual-dialect `.sql` migration; frozen-interface test gates 1.B's start | `daemon/repositories/service_tool/{__init__.py, models.py, repository.py}` (new); `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_service_tracking.sql` (new); `tests/unit/repositories/test_service_tool_repository.py` (frozen-interface + name-pin) | `uv run python -m pytest tests/unit/repositories/test_service_tool_repository.py` passes (incl. `test_repo_contract.py` from 1.A.0) + fresh-SQLite boot regression test (`tests/unit/test_ensure_deferred_schema_pin.py` mirror) | pending |
| **1.B** | Manager + spawner + tools (**parallel** after 1.A frozen; ≤2 workers alongside 1.C) | `ServiceManager` facade + `service_spawner` module + 5 `@tool` definitions + factory; consumes 1.A's frozen ServiceRepo API (incl. A13 row-count contract); F1 + F2 + F8 + F7 folded in | `daemon/services/service_tool_manager.py` (new); `daemon/tools/service_spawner.py` (new); `daemon/tools/service_tools.py` (new); `daemon/manager.py` `InstanceManager.__init__` wiring | `uv run python -m pytest tests/unit/tools/test_service_tools.py` (factory returns `[]` for falsy `current_instance_id`; 5 tools schema-pinned; `_full_doc_` present; F2 concurrent-name test; F8 spawn_failed test; F1 grace-window-recycle test); `tests/unit/test_service_spawner.py` for cross-platform `get_process_start_time` | pending |
| **1.C** | Registry + privilege + probe (**parallel** after 1.A frozen; ≤2 workers alongside 1.B) | 3-step seam wiring + DYNAMIC_TOOL_NAMES + KNOWN_TOOL_NAMES regen + loader warm-list + cold-boot doc test + D4 frozenset add + **THREE** pin updates same-PR (A14; F6 corrected from "two" to "three") + kill-switch resolver + boot-probe log line + lifespan hook for `ServiceReconciliationService` (with A5 mount fix + A6 boot pass + A8 None-guard + A9 narrow-collaborator) + **1.C.13b reassigned `_ensure_postgres_columns` from 1.A.6** + **1.C.16 + 1.C.17 stale-docstring rider (F11)** | `daemon/tools/_tool_registry.py` (3-step seam + privileged + comment rewrite); `daemon/tools/instance.py` (factory call + extend); `daemon/loader.py` (warm list); `daemon/config.py` (resolver + boot probe + `service_tool_reconcile_interval_seconds` per A8); `daemon/api.py` (lifespan boot/shutdown — A5/A6 mount); `daemon/services/service_reconciliation.py` skeleton (A5/A6/A8/A9); `daemon/manager.py` (1.C.13 `InstanceManager.__init__` + 1.C.13b `_ensure_postgres_columns` — single writer per F3); `tests/unit/tools/test_upgrade_registration.py`; `tests/unit/tools/test_attestation_registration.py`; **`tests/integration/test_maintenancer_spawn_resolves_tools.py`** (A14 missed third pin); `tests/test_loader.py` cold-boot test augmentation | `uv run python -m pytest tests/unit/tools/test_service_registration.py` (decorator order, category-attr survival, frozenset pin, registration seam greps); `uv run python -m pytest tests/test_loader.py::TestMaintenancerToolsDocColdBoot`; `uv run python -m pytest tests/unit/tools/test_frozen_tool_name_discovery.py`; `[ServiceTool] service_tool_enabled=` boot-probe regex in cold-boot log; `uv run python -m pytest tests/unit/services/test_service_reconciliation.py` skeleton (start/stop idempotent); **`tests/integration/test_maintenancer_spawn_resolves_tools.py`** pin test (A14); SC-6 behavioral test (default-configured agent resolves zero `service_*` tools — architect rider); **1.MG.1a CI grep-gate (F5)** | pending |
| **2** | Lifecycle exemption + boot reconciliation | Site-by-site exemption proof vs the 13 kill sites (terminate/cancel/pause/shutdown/GC each leave services running); reconcile sweep marks EXITED on liveness/start-time mismatch; periodic 90s tick; **F9 — full-cascade case** (terminate_instance on a service-owning instance; optional pause-cascade + GC-sweep) | `daemon/services/service_reconciliation.py` (full implementation); `daemon/services/service_tool_manager.py` (lifecycle methods); `tests/integration/test_service_tool_kill_site_exemption.py` (NEW — site-by-site matrix) + **F9 2.B.15 full-cascade case** (terminate_instance; 2.B.16 pause-cascade optional; 2.B.17 GC-sweep optional) | Integration test: for each kill site K1–K13, spawn a service, fire the trigger, assert `service.status == "running"` and `service.pid` still alive; **F9** full-cascade test (2.B.15) triggers `terminate_instance` and asserts the same invariants; reconcile sweep test: kill PID externally, advance time, sweep, assert `status="exited"` and `[ServiceTool] reconcile_reaped` log line | pending |
| **3** | Tests + docs | File-backed SQLite test suite + PG smoke pack + agent-facing tool docs + RESULTS template + decisions close-out (DONE — leader-ratification fold-in) + ops note + **F4 Deployment / Activation section in OPS note** + **F10 affirmative `exit_code=None` docs in `_full_doc_`** (NOTE: A7 CI grep-gate moved to Phase 1 merge gate per F5; Phase 3 owns the architectural doc linking to the gate) | `tests/unit/repositories/test_service_tool_repository.py` (full); `test/packs/service_tool_pg_smoke_integration_test.sh`; `daemon/tools/service_tools.py` `_full_doc_` finalization (A4 + F10); `.agents/tester/RESULTS/2026-09-15-service-tool-verification.md` template; `docs/architecture/instance-lifecycle.md` exemption-invariant doc (with link to Phase 1 CI gate); OPS note (3.B.7) with F4 Deployment / Activation section | `uv run python -m pytest tests/` full run + `test/packs/service_tool_pg_smoke_integration_test.sh` + flag-OFF byte-identical pin test + schema-pin test (create_all-vs-migration-chain gap) + Phase 1 CI grep-gate (1.MG.1a, F5) + Phase 1 boot-probe verification recipe (F4) | pending |

### Phase dependency graph

```
Phase 0 (architect gate, no code)
   │
   ▼
Phase 1.A (store+migration — SEQUENTIAL, lands first per F3)
   │
   ▼
Phase 1.B (manager+spawner+tools) ─┐
Phase 1.C (registry+privilege+probe) ─┤── parallel (≤2 workers per F3)
   │  (Phase 1.C wires 1.A's repo into manager + 1.B's factory into seam;
   │   F3 freezes 1.A's ServiceRepo API surface including the A13 mark_exited
   │   row-count contract before 1.B starts)
   ▼
Phase 2 (exemption proof + reconciliation, single worker)
   │
   ▼
Phase 3 (tests + docs, ≤2 parallel workers: tests pack + docs)
```

**F3 — Phase 1 parallelism contract (CORRECTED, leader-disposed 2026-09-15):** the previous claim of "3-way parallel ≤3 workers" was FALSE. The corrected contract is: **Phase 1.A first (sequential, single worker)** — lands the `ServiceRepo` frozen interface (incl. A13 `mark_exited` row-count contract). Then **Phase 1.B and Phase 1.C in parallel (≤2 workers)** — 1.B consumes 1.A's repo API; 1.C extends 1.A's repo wiring AND edits `daemon/manager.py` (the `_ensure_postgres_columns` block originally at 1.A.6 is REASSIGNED to 1.C.13b per F3 to keep `manager.py` under a single writer). The one-PR merge-shape (≥2 ordered commits `1.A+1.B / 1.C+2 / 3`) carries the contract: commit 1.A must freeze the repo API before commit 1.B starts. Phase 1.C MUST land same-commit-as or after Phase 1.B (the extend in `instance.py` references 1.B's factory; landing 1.C before 1.B produces a missing-import build break). **F3 also fixes the stale "1.C.6" citation wherever it appears** — the `_ensure_postgres_columns` block lives at 1.C.13b, not 1.C.6 (which was the original plan's mis-numbering).

**Merge shape:** one PR preferred (mirrors `spawn-intelligence-override` A7 lesson), with ≥2 ordered commits: `1.A+1.B / 1.C+2 / 3`. Phase 1.C MUST land same-commit-as or after Phase 1.B (the extend in `instance.py` references 1.B's factory; landing 1.C before 1.B produces a missing-import build break).

---

## Coupling Map

| | Phase 1.A | Phase 1.B | Phase 1.C | Phase 2 | Phase 3 |
|---|-----------|-----------|-----------|---------|---------|
| **Phase 1.A** | — | tight (1.A frozen-interface gates 1.B start per F3) | tight (1.A frozen-interface gates 1.C; 1.C.13b references 1.A's index names) | tight (1.A schema required by reconcile reads) | tight (1.A schema required by PG smoke pack) |
| **Phase 1.B** | tight (depends on 1.A) | — | tight (1.C extends 1.B's factory; same merge-commit `1.A+1.B` then `1.C+2`) | tight (1.B ServiceManager holds 1.A repo) | loose (1.B tool API documented by 3) |
| **Phase 1.C** | tight | tight | — | loose (1.C's lifespan hook starts 2's sweep) | tight (1.C's pin tests + 1.MG.1a CI gate belong to 1; 3.B.3 links to them) |
| **Phase 2** | tight | tight | loose | — | loose (2 tests extend 3's pack; F9 2.B.15 cascade test) |
| **Phase 3** | tight | loose | tight | loose | — |

**F3 — Tight couplings re-emphasized:**
- **1.A → 1.B / 1.C:** Phase 1.A is **SEQUENTIAL** (single worker, lands first). 1.B and 1.C cannot start until 1.A's frozen-interface test (`test_repo_contract.py` per task 1.A.0) passes. This is the binding F3 contract; the merge shape (commit `1.A+1.B` then `1.C+2`) carries the freeze.
- **1.B ↔ 1.C:** 1.C's factory call site in `instance.py:4392-4649` references 1.B's `create_service_tools` symbol. Landing 1.C before 1.B = missing-import build break.
- **1.C ↔ 1.A (single writer):** 1.C.13b (the `_ensure_postgres_columns` extension — REASSIGNED from 1.A.6 per F3) lands in the same `daemon/manager.py` commit as 1.C.13's `InstanceManager.__init__`. **No other worker edits `manager.py`** during Phase 1 — single-writer invariant per F3.
- **1.A → 1.B (constructor signature):** `ServiceToolManager` (1.B) holds `ServiceRepo` (1.A) as `self._repo`; constructor signature must match before 1.B's manager can be unit-tested against 1.A's repo.
- **Phase 2 ↔ 1.B:** the exemption test matrix (now extended per F9 with 2.B.15 full-cascade case) triggers `terminate_instance`, `pause_instance_cascade`, `manager.shutdown` — the test fixtures depend on 1.B's `ServiceManager` exposing cap-counter and stop-idempotency methods.

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **D4 / OQ#1 mechanism gap unresolved at architect gate** — Phase 0 fails to produce a decision, blocking Phase 1 indefinitely | High | Medium | Phase 0 pre-task gate is **non-negotiable**: do NOT start any Phase 1 code without an architect-approved D4 disposition (recorded in `decisions.md` OQ#1 close-out). If architect wants OQ#1 follow-up first, ship the `default_open` attribute PR before Phase 1 — adds ~1 PR but unblocks the principled path. |
| 2 | **3-site index drift** — 1.A writes `.sql` index name ≠ `models.py` index name ≠ `manager.py:_ensure_postgres_columns` index name → prod PG table misses index, sweep degrades to seqscan at scale | High | Medium | Add an explicit cross-file name-pinning test in `tests/unit/repositories/test_service_tool_repository.py` (1.A task) that grep-asserts all three names match byte-for-byte; runs as part of 1.A's independent gate. |
| 3 | **Fresh-SQLite boot broken on PG-only DDL** — repeating the `20260714` migration trap (`.agents/tester/LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only.md`) | High | Medium | 1.A constraint: `.sql` file MUST use only `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, and additive `ALTER TABLE ADD COLUMN`. NO `DROP CONSTRAINT`. NO PG-specific DDL. Add a fresh-SQLite-boot regression test mirroring the 20260714 LESSON shape. |
| 4 | **Kill-site inventory goes stale** — a future contributor adds a `killpg(0)` or `/proc` walk in `daemon/clients/` (UNVERIFIED region per `research-lifecycle-killsites.md:201-204`), breaking the service exemption silently | High | Low | (a) Document the registry-scoped invariant in `docs/architecture/instance-lifecycle.md` (Phase 3 task); (b) add a CODEOWNERS rule post-merge requiring daemon-architecture team review for any new `os.killpg`/`os.kill`/`killpg(0)` site; (c) the Phase 2 exemption test matrix serves as a regression net when run against a future PR. |
| 5 | **PID-reuse false-positive in `service_stop`** — kernel recycles PID before reconcile sweep; `(pid, start_time)` mismatch correctly detects, but a stale `service_stop` call between recycle and sweep might signal the wrong process | Medium | Low | 3-layer defense (D5): (i) `(pid, start_time)` triple verification in `service_stop` before any signal; (ii) reconcile sweep catches the residual window; (iii) `force=True` skips SIGTERM grace but still verifies `(pid, start_time)`. Integration test in 1.B validates all 3 layers. |
| 6 | **Boot-probe log line emitted lazily** — defeats the S13 reviewer gate (`config.py:3502-3509`); quiet-daemon cold-boot log greps false-fail; restart-pending forensics hours-long | Medium | Medium | 1.C constraint: emit at `load_config` resolution time, NOT lazily; mirror `config.py:3581-3591` verbatim style; add a boot-probe pack test in `test/packs/` mirroring `boot_probes_unit_test.sh` (1.C independent gate). |
| 7 | **Pydantic-settings init-kwarg > env inversion** — a `ServiceToolConfig` field without an explicit `_resolve_*` re-introduces the silent kill-switch-defeat trap (`config.py:3421-3444`) | Medium | Medium | 1.C constraint: every new boolean kill-switch has an explicit `_resolve_*` resolver called in `load_config`; pydantic field default uses `Field(default_factory=lambda: _resolve_*(...))` so the resolved value reaches pydantic as an init kwarg. Mirrors `_resolve_compaction_model`. |
| 8 | **Foreign session's dirty worktree blocks `feature/service-tool` PR** — the dispatch notes "worktree currently on `latest` with a foreign session's dirty state" | Medium | High | Plan is intentionally `write only under .agents/shared/planning/service-tool/` for this dispatch (no code yet). Before any Phase 1 execution, the implementer must reconcile the foreign dirty state (per project blueprint "occupied-main phantom-mods healed per recipe" precedent, `write-tree == HEAD^{tree}`) before creating a fresh `feature/service-tool` worktree. |
| 9 | **`start_new_session=True` semantics differ on Windows / non-darwin targets** — OQ#5 | Low | Low | v1 targets darwin + Linux only (matches existing daemon target matrix); document Windows as out of scope in `CATEGORY_DOC`; helper `get_process_start_time` raises `NotImplementedError` on Windows to surface the gap. |
| 10 | **Cap=10 too low for a power-user daemon** — operator hits the cap, no error recovery path documented | Low | Low | D5 decision: cap is configurable via `ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT` (default 10, `Field(ge=1)`). Phase 3 docs the operator-facing recovery path: stop some services, raise the cap, restart. No auto-eviction. |
| 11 | **Reconcile sweep misses a transient pid-recv child** — sweep runs in 90s ticks; between ticks, a recycled PID could be mis-classified | Low | Low | The `service_stop` PID-reuse defense runs at CALL time (immediate); the 90s sweep is a backstop for callers who don't call `service_stop`. Worst-case 90s lag on dead-state visibility is documented in `service_status` full_doc (D6 explicit). |
| 12 | **Loader cold-boot test fails because factory returns `[]` for `None`-manager stubs** — W1 precedent (`research-tools-subsystem.md:51-57`) | Medium | Low | 1.B constraint: factory returns `[]` when `current_instance_id` is falsy (mirror `proc_tools.py:1872-1900`); manager is dereferenced only at CALL time, not at construction. 1.C's `TestMaintenancerToolsDocColdBoot` augmentation verifies. |
| 13 | **Same-PR pin update missed in one of the THREE test files** — silent partial registration, build red or worse, the privileged-strip silently bypassed | High | Low | 1.C constraint (A14 corrected): the **THREE** pin updates (`test_upgrade_registration.py:105` + `test_attestation_registration.py:158` + `test_maintenancer_spawn_resolves_tools.py:292`) MUST be in the same commit as the frozenset change at `_tool_registry.py:124-128`; the existing `assert PRIVILEGED_TOOL_CATEGORIES == frozenset({...})` exact-equality check turns red on mismatch — the build itself is the safety net. |
| 14 | **Plan-time anchor drift on dirty worktree** — A10; several file:line anchors verified on `latest`@`f6ca8791` have drifted on the dirty worktree (`api.py`, `instance.py`, `manager.py` wiring sites) | Medium | Medium | (a) BRANCH NOTE at plan top — implementation targets the synced branch, not the dirty worktree; (b) all implementers MUST re-locate by **SYMBOL** not line number (architect's A10 caveat); (c) architect's corrected anchor list is in the BRANCH NOTE block; (d) the corrected anchors are folded into the affected phase tasks. |
| 15 | **A1 (single-pid os.kill) orphans fork-children** — naive `os.kill(row.pid, SIGTERM)` in `service_stop` leaves a real-world service's fork workers (`npm run dev`, webpack) alive | High | Medium | A1 BLOCKING for Phase 1: `service_stop` MUST signal the process GROUP via `os.killpg(row.pid, sig)` (pgid == pid for setsid leaders; zero added reachability). Folded into Phase 1.B.6 acceptance. |
| 16 | **A5 (phantom mount) — reconciliation receives `None` and never runs** — the original mount pseudocode read `getattr(app.state, "_service_tool_manager", None)`, but `app.state` is never populated with manager attrs; the sweep would silently do nothing while appearing wired | High | High | A5 BLOCKING for Phase 1: mount fix uses `getattr(manager, "_service_tool_manager", None)` and stores the **service** (not the manager) on `app.state` for shutdown. Folded into Phase 1.C.14 acceptance. |
| 17 | **A3 eternal-`starting` rows leak cap slots** — a crash between INSERT and Popen leaves a `status='starting'` + `pid IS NULL` row that counts against the cap forever and is invisible to the operator | Medium | Medium | A3 SHOULD-FIX in Phase 2: reconcile sweep reaps such rows after `service_tool_starting_grace_seconds` (default 30s) with `reason=spawn_failed_or_interrupted`. Folded into Phase 2.A.2. |
| 18 | **F4 — Frozen-PyInstaller activation gap** — new modules require **FE rebuild + restart** for the FE-side tool surface (cold-boot doc test, `tool_help` filter), and **daemon rebuild + restart** for the backend (the `daemon/` changes — repo, manager, lifespan, config). Daemon restart alone is insufficient for new code paths in a frozen-PyInstaller prod binary; the same precedent the project's `frozen-ensemble-prod` binary follows applies. | High | Medium | (a) **OPS note (Phase 3.B.7) includes an explicit "Deployment / Activation" section** documenting that frozen-PI activation = rebuild + restart, with separate steps for FE rebuild and daemon rebuild; (b) boot-probe verification recipe (`grep "[ServiceTool]" data/logs/ensemble.log` at first boot after rebuild) verifies the backend is active; absent this line, the feature is NOT active even if `service_*` tool names appear in `tool_help`. |
| 19 | **F1 — PID recycle during grace window escalates to stray SIGKILL** — `service_stop` verifies ownership pre-SIGTERM but never re-verifies during the 5s grace poll or before SIGKILL escalation; a recycled PID in the grace window would receive a stray SIGKILL against an unrelated user process | High | Low | F1 BLOCKING: each poll re-reads `get_process_start_time(row.pid)` and compares EQUALITY with `row.start_time`; mismatch ⇒ mark EXITED, never escalate. Second F1 layer: re-verify `(pid, start_time)` immediately before SIGKILL escalation. Precedent `proc_tools.stop_process:1380-1386`. Folded into Phase 1.B.6 acceptance (grace-window-recycle test + pre-kill-recycle test). |
| 20 | **F2 — Concurrent same-name `service_start` leaks an orphan OS process** — precheck → Popen → INSERT lets the IntegrityError loser keep a live, untracked, kill-exempt child | High | Low | F2 BLOCKING: wrap INSERT in `try/except IntegrityError`; on race-lost, `os.killpg(child_pid, SIGKILL)` the just-spawned process (it's an orphan otherwise — setsid'd, never registered, kill-exempt). Folded into Phase 1.B.5 acceptance. |
| 21 | **F3 — Phase-1 parallelism contract false** — three-part defect: (a) 1.A must land AND freeze the ServiceRepo API (incl. A13 mark_exited row-count contract) BEFORE 1.B consumes it; (b) 1.A.6 collided with 1.C's manager.py edit; (c) "3-way parallel" claim was false | High | Closed | F3 BLOCKING: added 1.A.0 frozen-interface block; 1.A.6 REASSIGNED to 1.C.13b; concurrency rewording ("1.A first sequential, then ≤2 parallel"); stale "1.C.6" citation fixed throughout. Verified by `test_repo_contract.py` (1.A.0) and the merge-shape rule. |
| 22 | **F8 — Synchronous spawn failure blocks cap slot for 30s** — on Popen `OSError`, an EXITED row is only written by the A3 reaper after grace, holding the cap slot needlessly | Medium | Low | F8: write EXITED row IMMEDIATELY on synchronous spawn failure via `repo.insert_with_status(status="exited", reason="spawn_failed")`; return `spawn_failed`. The A3 reaper covers only crash/interrupt windows (client-side code cannot close those). Folded into Phase 1.B.5 + 3.B.2 (`_full_doc_` documents `exit_code=None` for deaths not observed via `service_stop`). |
| 23 | **F9 — Kill-site matrix missing full-cascade case** — per-site K1–K13 cover individual kill primitives, but a `terminate_instance` cascade triggers ALL of K3/K4/K6/K8 in one call; without that case, a future contributor adding a sweep that doesn't fire under any single K1–K13 trigger could break the exemption silently | Medium | Low | F9 SHOULD-FIX in Phase 2.B.15: full-cascade case (terminate_instance on a service-owning instance). Optional Phase 2.B.16 (pause-cascade) + 2.B.17 (GC-sweep) are design-statement pins. |
| 24 | **F11 — Stale-docstring rider** — `_strip_privileged_category_tools` (~instance.py:4747) and `help.py:41` docstrings conflate "privileged" with "daemon-internal"; the comment rewrite at 1.C.7a creates a comment / docstring drift hazard | Low | Low | F11 SHOULD-FIX in Phase 1.C.16 + 1.C.17: opportunistic docstring updates to match the behavioral criterion. Grep-pin acceptance criterion: `grep -n "PRIVILEGED_TOOL_CATEGORIES" daemon/tools/help.py` shows the updated docstring. No behavioral code change. |

---

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| **SC-1** | **Kill-site exemption provable site-by-site** | Integration test matrix `tests/integration/test_service_tool_kill_site_exemption.py` triggers each of the 13 kill sites (K1–K13) while a service is running and asserts `service.status == "running"` and the PID is still alive post-trigger. | 13/13 sites pass; PID alive at every site; row state unchanged in `service_tracking`. |
| **SC-2** | **Restart survival** | Daemon graceful shutdown → restart, then `service_status(name)` for a service started before shutdown. Assert `status="running"`, PID matches stored value, `(pid, start_time)` matches the stored triple, log line `[ServiceTool] reconcile_swept alive=N reaped=0 errors=0` emitted. | Single restart: 100% of pre-shutdown services reconciles to RUNNING. Daemon hard SIGKILL → restart: same threshold (PID + start_time verification is restart-agnostic). |
| **SC-3** | **No OS-service registration** | (a) `grep -rn "launchd\|systemd\|launchctl\|init.d" daemon/tools/service_tools.py daemon/tools/service_spawner.py daemon/services/service_tool_manager.py daemon/services/service_reconciliation.py` returns zero matches; (b) `CATEGORY_DOC` and module docstring explicitly state "NOT an OS service". | Zero matches; explicit exclusion statement in module docs. |
| **SC-4** | **3-site index registration** | (a) `.sql` migration, (b) `models.py __table_args__`, (c) `manager.py:_ensure_postgres_columns` — all three contain `CREATE INDEX IF NOT EXISTS idx_service_tracking_name_active` and `idx_service_tracking_pid` with **byte-identical names**. | Grep pin test in `tests/unit/repositories/test_service_tool_repository.py` asserts all three names match. |
| **SC-5** | **Boot-probe at resolution time** | After daemon boot, `grep "^\[ServiceTool\] service_tool_enabled=" data/logs/ensemble.log` returns the boot-probe line emitted BEFORE the first tool call. | Line present in cold-boot log; not emitted lazily (verified by absence of post-boot emit timestamps). |
| **SC-6** | **Kill-switch works** | (a) `ENSEMBLE_SERVICE_TOOL_ENABLED=0` + restart → zero `service_*` tools in any agent's resolved tool list (privilege-strip identical to `system_upgrade` precedent); (b) default-ON state produces the full 5-tool surface for opted-in agents. | (a) `assert "service_start" not in resolved_tools` for default-configured agents; (b) `assert "service_start" in resolved_tools` for `tools.allow=["service"]` agents. |
| **SC-7** | **Stop idempotency** | Call `service_stop(name)` twice on the same row → first returns `{status: "exited", exit_code: ...}` or `{status: "running"→"exited"}`; second returns `{status: "exited"}` (no error, no double-signal). | Idempotent across N calls; second call never signals the PID. |
| **SC-8** | **PID-reuse defense** | Spawn service A; externally kill PID A; spawn service B which inherits PID A; call `service_stop(A.name)`. Assert: `service_stop` returns `reason=pid_recycled`; does NOT signal PID A (B continues running); A's row is marked EXITED. | A's row EXITED; B's row RUNNING; PID A still alive after the call. |
| **SC-9** | **Fresh-SQLite boot regression** | Boot a fresh SQLite DB (no migrations applied) → run all migrations including the new one → boot succeeds; no `OperationalError: no such table`, no `DROP CONSTRAINT` errors. | Fresh-SQLite boot clean; mirrors the 20260906 task-claim migration's dual-dialect claim. |
| **SC-10** | **Flag-OFF byte-identical pin** | `ENSEMBLE_SERVICE_TOOL_ENABLED=0` → boot probe shows `service_tool_enabled=False`; `service_*` tools absent from all agent universes; `service_reconciliation` periodic service NOT started; migration still runs (schema is independent of flag). | OFF = zero behavioral surface; schema persists (so flag-flip to ON doesn't require migration replay). |
| **SC-11** | **Max-concurrent cap enforced** | Spawn 10 services (cap=10); call `service_start(name11, ...)` → returns `{"status": "cap_exceeded", "reason": "max_concurrent_reached"}`; no PID created, no DB row inserted. | 11th call rejected pre-spawn; counter state intact. |
| **SC-12** | **All OQ#1–7 dispositions recorded** | `decisions.md` close-out section lists each OQ with: (a) disposition, (b) which Phase owns the resolution, (c) follow-up PR if deferred. | 7/7 OQs closed; no OQ left "open" at end of Phase 3. |

---

## Research Insights (do not re-derive)

All evidence base in `.agents/shared/planning/service-tool/`:

1. **`research-tools-subsystem.md`** — 3-step registration seam with worked example (`system_upgrade` at `:27-99`); the `DYNAMIC_TOOL_NAMES` + `KNOWN_TOOL_NAMES` regen commands at `:544-548`; the 10-step new-category checklist at `:395-450`; the privilege-pin exact-equality assertions at `:256-280`; the `loader.py` warm-list contract at `:57-70`.
2. **`research-lifecycle-killsites.md`** — 13-kill-site inventory table at `:28-43` (all registry-scoped); per-path narratives at `:55-180` (terminate, cancel, shutdown, GC); exemption-proof sketch at `:208-229` (3-step argument: spawn-unregistered + setsid + reparent-to-launchd).
3. **`research-persistence-migrations.md`** — dual-dialect migration rule + the 20260714 trap; 3-site index pattern at `:35-49`; 11-step new-table + boot-service checklist at `:141-152`; kill-switch `_resolve_*` recipe at `:113-118`; boot-probe log convention at `:87-95`.
4. **`technical-analysis.md`** — 5-dimension deep-dive (Architecture / Integration Points / Trade-offs / Scalability / Technical Debt); the module-boundary diagram at `:46-83`; comparison tables for spawn mechanism, tracking store, security model, abuse guards, restart reconciliation, observability.
5. **`decisions.md`** — D1–D7 binding + OQ#1–7 (all RESOLVED per `architecture-recommendation.md` §4). D1: Popen precedent. D2: `service_tracking` table schema + 3-site indexes (A11 partial UNIQUE + A12 TEXT ISO-8601 + A13 atomic-guard). D3: 5-tool surface + signatures + async PID-reuse defense (A1 killpg + A2 async polling). D4: **RESOLVED Option A** (leader-ratified 2026-09-15); **THREE** pin files same-PR (A14). D5: cap=10 + name-unique partial UNIQUE (A11) + PID-reuse + atomic-guard (A13) + stop-idempotent + NO command allowlist. D6: boot sweep + periodic reconcile (A3 eternal-`starting` reaper + A5 mount fix + A6 boot pass + A8 config naming + A9 narrow collaborator). D7: per-call INFO logs + boot probe at resolution time + `ENSEMBLE_SERVICE_TOOL_ENABLED` kill-switch + knob envs (`service_tool_reconcile_interval_seconds` per A8).
6. **`architecture-recommendation.md`** — architect's Phase-0 gate verdict + 14 amendments (A1–A14). **Binding amendment source**. D4 Option A + behavioral comment rewrite + triple-pin discovery (A14) + amendments folded into `decisions.md` + per-phase plans.

---

## Open Questions (architect dispositions — ALL RESOLVED per `architecture-recommendation.md` §4)

All OQ#1–7 are now RESOLVED. Dispositions recorded in `decisions.md` §"Open Questions" header (each marked **RESOLVED** with the architect's verdict). The Phase-0 gate is closed; OQ close-out is **NOT** open work for implementers. Summary:

| OQ | Disposition | Phase ownership |
|---|---|---|
| **OQ#1** | Option A — frozenset add + triple-pin (A14). `default_open` refactor NOT pre-committed (architect §1.5 overrules planner lean). | Phase 1.C.3 + 1.C.7 |
| **OQ#2** | Document single-daemon-only in v1; `started_by_instance_id` + `started_by_agent_id` audit columns suffice; add `daemon_instance_id` only if multi-daemon becomes real. | Phase 3.B.7 (OPS note) + `_full_doc_` of every tool |
| **OQ#3** | Operator-side log rotation; cap `MAX_LOG_TAIL_BYTES=10MB` in `service_logs`; document disk-fill risk. | Phase 3.B.2 (`_full_doc_`) + 3.B.7 (OPS note) |
| **OQ#4** | Out of scope v1; risk bounded by cap=10 + default-closed (D4); revisit alongside broader sandbox story. | non-work for v1 |
| **OQ#5** | `ps`-based macOS parse acceptable (precedent `_verify_pid_ownership`); document locale-format risk; `NotImplementedError` on Windows. | Phase 1.B.3 (helper implementation) |
| **OQ#6** | Keep 5s SIGKILL grace even on `force=True`; implemented async per A2 (no busy-wait). | Phase 1.B.6 (`service_stop` async body) |
| **OQ#7** | Plain text tail; no format-aware parsing v1. | Phase 1.B.7 (`service_logs` body) |

**SC-12** (success criterion: all OQ#1–7 dispositions recorded) is **MET by the fold-in itself** — `decisions.md` now carries all 7 dispositions; no separate close-out section needed.

---

## Deferred / Backlog (council findings 12–22; leader-disposed 2026-09-15)

**F12 — explicit Phase-1 gate decision (planner disposition, recorded verbatim per caller):** "Reaper skeleton stays in P2, NOT pulled into P1 — deferred-with-rationale: (a) one-PR merge shape (ordered commits 1.A+1.B / 1.C+2 / 3) means no production window where P1 runs without P2's reaper; (b) the synchronous spawn-failure path is closed client-side in P1 (spawn_failed → immediate EXITED row write, no slot block); (c) the A3 reaper exists for crash/interrupt windows that client-side code cannot close; (d) pulling it into P1 would couple 1.B/1.C to sweep scaffolding, deepening the very parallelism contract F3 tightens."

**F13–F22 — council findings 13–22 deferred to backlog (per caller; classifications populated below):**

| # | Finding | Description |
|---|---------|-------------|
| **F13** | (non-blocking; informational; grep-verified) | FE impact = NONE (grep-verified) — no frontend surface references the service tool or `service_tracking`. |
| **F14** | (deferred follow-up; OQ#1 trigger) | Record OQ#1 re-evaluation trigger — revisit `default_open` mechanism if a second category ever needs "default-closed but non-privileged" semantics. |
| **F15** | (should-fix; doc propagation) | Grandchild-setsid escape — a service process that itself spawns setsid grandchildren escapes `killpg`; propagate this limitation into `service_stop` `_full_doc_`. |
| **F16** | (accepted; already named in Risk #17) | Crash between Popen-success and pid-UPDATE → A3 reaper reports false-EXITED; accepted misreport, named in Risk #17. |
| **F17** | (doc-hygiene backlog; cosmetic) | Doc-hygiene cluster — `ServiceManager` name drift; pre-A14 "double-pin" remnants; OQ#1 tracker status; intra-plan anchor drift; `test_service_reconciliation.py` unowned; SC-1 "13/13" vs K12 darwin-SKIP wording. |
| **F18** | (accepted; advisory cap) | Cap-check TOCTOU (9→11 concurrent under cap=10) — advisory cap only; document as accepted. |
| **F19** | (should-fix; implementation rule) | `service_logs` must read `log_path` from the `service_tracking` row, never re-derive from `name` (path safety). |
| **F20** | (should-fix; pin/pattern) | Pin the `_install_*` resolver+install pairing pattern (config resolver registered alongside its install site). |
| **F21** | (should-fix; test fixture strategy) | 2.C.4 — in-test `manager.shutdown()` isolation needs one sentence of fixture strategy (dedicated manager instance or monkeypatched shutdown). |
| **F22** | (accepted for v1) | `start_time` capture window under slow macOS `ps` — acceptable for v1. |

Each row above carries its classification in the "Finding" column (`non-blocking` / `deferred follow-up` / `should-fix` / `accepted` / `doc-hygiene backlog`) and a verbatim one-liner in the "Description" column per the council review. Implementers MUST NOT re-derive content for F13–F22; these are the council's binding dispositions. Any future re-scoping of an accepted or non-blocking row requires a fresh council pass.

---

## Concurrency Note

Per the caller's constraint: "implementation is capped at 3 concurrent instances; phases with shared context are grouped so each phase is executable by ≤3 parallel workers without cross-phase file contention".

### Phase 1 — **F3 corrected: 1.A sequential first, then ≤2 parallel workers**

**F3 — corrected parallelism contract:** the previous "3-way parallel ≤3 workers" claim was FALSE. The corrected contract is:

| Worker | Step | Touched files (no overlap with other workers) |
|--------|------|-------------------------------------------------|
| **W1.A** (sequential first) | 1.A: store + migration (incl. 1.A.0 frozen-interface block) | `daemon/repositories/service_tool/__init__.py` (NEW); `daemon/repositories/service_tool/models.py` (NEW); `daemon/repositories/service_tool/repository.py` (NEW); `daemon/migrations/versions/YYYYMMDD_HHMMSS_create_service_tracking.sql` (NEW); `tests/unit/repositories/test_service_tool_repository.py` (frozen-interface test, name-pin test) |
| **W1.B** (parallel) | 1.B: manager + spawner + tools (consumes 1.A's frozen ServiceRepo API) | `daemon/services/service_tool_manager.py` (NEW); `daemon/tools/service_spawner.py` (NEW); `daemon/tools/service_tools.py` (NEW) |
| **W1.C** (parallel) | 1.C: registry + privilege + probe (incl. 1.C.13b reassigned from 1.A.6 — `_ensure_postgres_columns` lives here) | `daemon/tools/_tool_registry.py`; `daemon/tools/instance.py`; `daemon/loader.py`; `daemon/config.py`; `daemon/api.py`; `daemon/services/service_reconciliation.py` (NEW skeleton); **`daemon/manager.py`** (1.C.13 `InstanceManager.__init__` + 1.C.13b `_ensure_postgres_columns` — single writer); `tests/unit/tools/test_upgrade_registration.py` (D4 pin); `tests/unit/tools/test_attestation_registration.py` (D4 pin); **`tests/integration/test_maintenancer_spawn_resolves_tools.py`** (A14 third pin); `tests/test_loader.py` |

**File contention rule (F3):** 1.A lands FIRST (single worker, sequential) and freezes the ServiceRepo API surface (incl. A13 `mark_exited` row-count contract) via the 1.A.0 frozen-interface test. Then 1.B and 1.C run in parallel: 1.B imports 1.A's frozen symbols; 1.C extends `daemon/manager.py` with both `InstanceManager.__init__` (1.C.13) and `_ensure_postgres_columns` (1.C.13b — REASSIGNED from 1.A.6 to keep `manager.py` under a single writer). The merge-shape rule (commit `1.A+1.B` then `1.C+2`) ensures 1.A's symbols are frozen before 1.C edits `manager.py` to import them.

### Phase 2 — single worker (sequenced integration tests)

The exemption matrix test inherently serializes: each of the 13 kill-site triggers must observe a known pre-state and assert a known post-state. Splitting into 2-3 parallel workers risks cross-test contamination (instance 1's service status interfering with instance 2's reconcile). Single-worker is correct.

### Phase 3 — two concurrent workers

| Worker | Phase | Touched files |
|--------|-------|----------------|
| **W3.A** | Tests + PG smoke pack | `tests/unit/repositories/test_service_tool_repository.py`; `test/packs/service_tool_pg_smoke_integration_test.sh`; `.agents/tester/RESULTS/2026-09-15-service-tool-verification.md` template |
| **W3.B** | Docs + decisions close-out | `daemon/tools/service_tools.py` (`_full_doc_` finalization); `docs/architecture/instance-lifecycle.md`; `decisions.md` close-out section |

The `_full_doc_` finalization (W3.B) does NOT change the tool API surface — only the docstring — so 1.B's prior commit can be augmented without re-test in 1.A's pack.