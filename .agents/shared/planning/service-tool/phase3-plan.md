# Phase 3: Tests + Docs

Date: 2026-09-15 (amended per leader ratification 2026-09-15)
Author: planner[v2] via plan-creation worker
Parent plan: `.agents/shared/planning/service-tool/plan-overview.md`
Decisions anchor: `decisions.md` (D2, D5, D7 in particular); OQ#3–7 dispositions already recorded in `decisions.md` per leader-ratification fold-in
Phase 2 dependency: this phase consumes the working `ServiceManager` + `ServiceReconciliationService` + 13-site exemption matrix; the suite extends the work Phase 1.A + Phase 2 produced.

**BRANCH NOTE (amended per leader ratification 2026-09-15):** `feature/service-tool` is being synced to current `latest` by `giter` in parallel. Implementation targets the **synced branch**, NOT the stale dirty worktree. **A10 — anchor refresh:** plan-time file:line anchors have drifted on the dirty worktree; all implementers MUST re-locate by **SYMBOL**, not line number. Architect's corrected anchor list is in `plan-overview.md` BRANCH NOTE block.

---

## Objective

Land the comprehensive test surface (unit + integration + PG smoke + flag-OFF pin), the agent-facing tool docs (`_full_doc_` finalization + cold-boot doc test), the `docs/architecture/instance-lifecycle.md` exemption-invariant documentation, the `decisions.md` close-out (OQ#1–7 dispositions), and the RESULTS template that the tester will use to certify the feature. The single sentence that marks this phase complete: **the feature ships behind a single PR whose test pack passes 100% (unit + integration + PG smoke + flag-OFF pin), whose agent-facing docs cover every tool's behavior under all documented states (running / exited / not_found / pid_recycled / cap_exceeded), whose architecture docs name the registry-scoped invariant, and whose decisions.md closes out every OQ with a disposition.**

---

## Background

Phases 1 and 2 land the buildable surface and prove the kill-site exemption by test. Phase 3 closes the loop: full test coverage for ops, agent-facing documentation for the eventual end-user (the LLM agent invoking `service_*`), architectural docs so future contributors don't accidentally break the exemption, and a RESULTS template that aligns with the project's tester workflow (`agents/tester/`). This phase also addresses the per-flag-OFF byte-identical pin requirement (D7 / SC-10) and the create_all-vs-migration-chain gap (per `tests/unit/test_ensure_deferred_schema_pin.py:477`).

---

## Shared Context (what every implementer MUST know)

- **D2 (decisions.md L64-173 — amended per A11 + A12 + A13):** 3-site index registration is mandatory AND `idx_service_tracking_name_active` MUST carry `unique=True` (A11); timestamps are TEXT ISO-8601 (A12); atomic-guard UPDATEs (A13). Schema-pin test mirrors `tests/unit/test_ensure_deferred_schema_pin.py:477`.
- **D5 (decisions.md L373-413):** abuse guards: cap=10, name-unique partial UNIQUE INDEX (A11), PID-reuse defense, stop-idempotent, NO command allowlist. All enforced.
- **D7 (decisions.md L560-627 — amended per A8):** per-call INFO logs at `[ServiceTool] service_started/stopped/reaped/swept`; boot probe at `load_config` resolution time; OFF = byte-identical no-op; config key `service_tool_reconcile_interval_seconds` (A8 — real `ServicesConfig` `Field(ge=1)`).
- **A4 — `_full_doc_` exit_code docs:** `exit_code` is `None` for deaths not observed via `service_stop` (no `wait()` handle is retained after spawn). Document in `service_status` + `service_stop` `_full_doc_`.
- **A7 — CI grep-gate:** PRs introducing new `os.killpg(` / `os.kill(` / `killpg(0)` / `/proc/[0-9]` / `pkill` / `process_iter` sites in `daemon/` OUTSIDE the allowlisted trio (`daemon/tools/bash.py`, `daemon/tools/proc_tools.py`, `daemon/services/vscode_server_manager.py`) MUST fail CI. Mechanic owned by Phase-3 task author per `architecture-recommendation.md` §3 A7.
- **A14 — TRIPLE-pin D4 same-PR rule:** `tests/unit/tools/test_upgrade_registration.py:105` + `tests/unit/tools/test_attestation_registration.py:158` + `tests/integration/test_maintenancer_spawn_resolves_tools.py:292`. All three in same PR as the frozenset add.
- **Phase 1 contracts consumed:**
  - 5 tools (`service_start`, `service_stop`, `service_status`, `service_list`, `service_logs`) at `daemon/tools/service_tools.py`.
  - `_full_doc_` attrs per tool (Phase 1.B.4); Phase 3 FINALIZES the docstrings to cover all result shapes (incl. A4 `exit_code=None` note).
- **Phase 2 contracts consumed:**
  - `ServiceReconciliationService.sweep_once` returns `{alive, reaped, errors, starting_reaped, disabled?}` counters (Phase 2.A.2 — amended A3 + A9).
  - `mark_exited` atomic + idempotent with row-count return (Phase 2.A.4 — amended A13).
- **Concurrency limit:** Phase 3 is two concurrent workers (W3.A tests, W3.B docs). The `_full_doc_` finalization (W3.B) does NOT change the tool API surface — only docstrings — so W3.A's pack can be developed without re-test.
- **Tester workflow precedent:** `.agents/tester/RESULTS/2026-09-XX-<topic>-verification.md` is the canonical RESULTS template (per `.agents/tester/`); a tester agent fills in the SHIP/FAIL/conditional matrix.
- **Plan-doc access (caller-pinned):** the plan directory exists ONLY on `feature/service-tool` (synced to current `latest` by `giter` in parallel). Reference from absolute path.

---

## Touched Files (with verified anchors at `f6ca8791`; re-locate by symbol before editing)

| File | Symbol / anchor | Reason | Sub-track |
|------|----------------|--------|-----------|
| `tests/unit/repositories/test_service_tool_repository.py` (Phase 1.A.7 stub) | full file | full file-backed SQLite suite (insert / get / list / update / partial-index / transitions) | 3.A |
| `tests/unit/tools/test_service_tools.py` (Phase 1.B.4 stub) | full file | schema + `_full_doc_` + decorator order + category-attr survival | 3.A |
| `tests/unit/test_service_spawner.py` (Phase 1.B.3 stub) | full file | cross-platform `get_process_start_time` + `stop` SIGTERM/SIGKILL escalation | 3.A |
| `tests/unit/services/test_service_reconciliation.py` (Phase 2.A.2 stub) | full file | full sweep unit suite | 3.A |
| `test/packs/service_tool_pg_smoke_integration_test.sh` (NEW) | next to `test/packs/ensure_deferred_pg_smoke_integration_test.sh` | PG smoke pack | 3.A |
| `tests/integration/test_service_tool_cap_enforcement.py` (NEW) | next to `tests/integration/checkpoint_prune_real_saver.py` | cap=10 enforcement + counter edge cases | 3.A |
| `tests/integration/test_service_tool_flag_off_byte_identical.py` (NEW) | next to `tests/integration/test_empty_response_guard_real.py` | flag-OFF byte-identical pin (SC-10) | 3.A |
| `tests/unit/test_ensure_deferred_schema_pin.py` | at `:477` | add create_all-vs-migration-chain pin for `service_tracking` | 3.A |
| `daemon/tools/service_tools.py` (Phase 1.B.4) | each `@tool` `_full_doc_` string | docstring finalization (run states, OOM cap, kill-switch behavior) | 3.B |
| `docs/architecture/instance-lifecycle.md` (or canonical lifecycle doc) | new section | "services are kill-exempt" invariant + registry-scoped fence | 3.B |
| `decisions.md` | append new "Close-out" section | OQ#1–7 dispositions + Phase ownership | 3.B |
| `.agents/tester/RESULTS/2026-09-15-service-tool-verification.md` (NEW template) | next to other 2026-09-XX RESULTS | tester fills in SHIP/FAIL matrix | 3.B |
| `agents/tester/PACKS.md` (if exists) or `.agents/tester/PACKS.md` | new entry | `service_tool_pg_smoke_integration_test.sh` registration | 3.B |
| `.agents/tester/RESULTS/PACKS.md` (or `test/packs/PACKS.md`) | full inventory of new packs | for the tester's discovery | 3.B |

**Do NOT modify:** the actual `service_tracking` schema (Phase 1.A.3 + Phase 1.A.5 + Phase 1.C.13b are frozen — the `_ensure_postgres_columns` block was REASSIGNED from 1.C.6 to 1.C.13b per F3); `instance_lifecycle.py` + `manager.py` shutdown body (Phase 2.MG.4 invariant); `agents/tester/soul.md` (the tester owns their own workflow; we provide the RESULTS template only).

---

## Tasks

### Phase 3.A — Tests pack (≤1 worker, or 2 sub-workers if cleanly partitioned)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.A.1 | Read `decisions.md` lines 373-413 (D5), 560-627 (D7), and the test-strategy-shape section at `:702-713`. | Phase 2 complete | Implementer can articulate the test surface: unit (repo/spawner/tools/reconcile), integration (kill-site matrix from Phase 2 + cap + flag-OFF), PG smoke (schema-pin + boot-probe), plus the create_all-vs-migration-chain pin. |
| 3.A.2 | Expand `tests/unit/repositories/test_service_tool_repository.py` to the full suite. Cases: (a) insert + get_by_id; (b) get_by_name active_only=True excludes exited rows; (c) list_active vs list_all; (d) mark_exited transition + exit_code preservation; (e) partial unique index — second insert with same name + status='starting' raises IntegrityError; (f) name reuse after EXITED — second insert with same name + status='starting' succeeds (exited row stays); (g) cross-file name-pin (Phase 1.A.7) asserts `.sql` + `models.py` + `manager.py` index names match. File-backed SQLite per `test_chart_tools_reuse_integration.py:80-109` (tmp_path + NullPool + WAL + busy_timeout=10000). | Task 3.A.1 | All 7 cases pass; fresh-SQLite boot regression test passes. |
| 3.A.3 | Expand `tests/unit/tools/test_service_tools.py` to full coverage. Cases: (a) factory returns `[]` for falsy `current_instance_id`; (b) factory returns 5 tools for valid inputs; (c) `_full_doc_` present on every tool (mirror `proc_tools.py:2154-2169`); (d) decorator order verified (mirror `test_attestation_registration.py:128-140`); (e) category-attr survival through the langchain `@tool` wrap (mirror `:174-177`); (f) name-pattern validation `r"^[a-zA-Z0-9_-]+$"` rejects empty / too-long / special chars; (g) `service_start` schema: name, command (list[str], min_length=1), cwd (Optional[str]); (h) `service_stop` schema: name, force (default False); (i) `service_logs` schema: name, tail_lines (default 200, ge=1, le=10000). | Task 3.A.1 | All 9 cases pass. |
| 3.A.4 | Expand `tests/unit/test_service_spawner.py`. Cases: (a) `spawn([sys.executable, "-c", "import time; time.sleep(10)"], log_path, cwd)` returns `(pid, start_time)` with `os.getpgid(pid) != os.getpgid(os.getpid())` (verifies new session); (b) `get_process_start_time(pid)` returns matching value; (c) `stop(pid, force=False)` SIGTERM → 5s → SIGKILL kills the child within 6s; (d) `stop(pid, force=True)` kills within 1s; (e) `get_process_start_time(dead_pid)` returns `None`; (f) cross-platform helper returns `NotImplementedError` on Windows. | Task 3.A.1 | All 6 cases pass; cross-platform helper gated. |
| 3.A.5 | Expand `tests/unit/services/test_service_reconciliation.py` to full sweep suite. Cases: (a) sweep kills nothing (NO signals sent — sweep is mark-only); (b) mark_exited atomic + idempotent (calling twice is no-op); (c) start-time mismatch marks EXITED + WARNING log; (d) live PID + matching start-time leaves row unchanged; (e) `errors` counter increments on per-row exception; (f) `disabled=True` early-return when kill-switch OFF; (g) `sweep_once` returns `{alive, reaped, errors, disabled?}` counters; (h) periodic loop runs 3 ticks in <1s wall time with mocked wait; (i) `stop()` exits within 5s. | Task 3.A.1, Phase 2.A.2 | All 9 cases pass. |
| 3.A.6 | Create `tests/integration/test_service_tool_cap_enforcement.py`. Cases: (a) spawn N services up to cap=10 — all succeed; (b) `service_start` for the (N+1)th returns `{"status": "cap_exceeded", "reason": "max_concurrent_reached"}` with NO PID created and NO DB row inserted; (c) `service_stop` of one service frees the slot for a new `service_start`; (d) cap counter is computed from `list_active()` (per-daemon, not per-instance); (e) cap=10 enforced across multiple distinct instance_ids (services are NOT instance-owned, but started_by_instance_id is recorded for attribution). | Task 3.A.1 | All 5 cases pass; cap counter survives restart (Phase 2.C.5). |
| 3.A.7 | Create `tests/integration/test_service_tool_flag_off_byte_identical.py`. Cases: (a) `ENSEMBLE_SERVICE_TOOL_ENABLED=0` + boot → boot probe shows `service_tool_enabled=False`; (b) `service_*` tools absent from any agent's resolved tool list (privilege-strip identical to `system_upgrade`); (c) `ServiceReconciliationService` NOT started in lifespan boot; (d) migration still applies (schema is independent of flag); (e) setting the env to ON after migration = feature becomes available (no re-migration needed); (f) `service_status` inline reconciliation is also no-op when OFF (defensive). | Task 3.A.1, Phase 1.C.10 | All 6 cases pass; OFF state is byte-identical to pre-Phase-1 behavior (no `service_*` references in resolved tools). |
| 3.A.8 | Create `test/packs/service_tool_pg_smoke_integration_test.sh`. Mirror `test/packs/ensure_deferred_pg_smoke_integration_test.sh` shape: provisions a disposable PG, runs `_ensure_postgres_columns` twice (idempotency), creates a service row + sweeps, drops the DB. Cases: (a) `_ensure_postgres_columns` is idempotent on a PG with the new `service_tracking` table; (b) the partial unique index `idx_service_tracking_name_active` is created on PG; (c) cross-platform `get_process_start_time` works on PG-spawned processes; (d) reconcile sweep marks dead rows EXITED on PG. | Task 3.A.1 | PG smoke pack passes on disposable PG; no regressions in pre-existing PG packs. |
| 3.A.9 | Augment `tests/unit/test_ensure_deferred_schema_pin.py`. Add a pin at `:477` shape that asserts the create_all-vs-migration-chain difference is documented for `service_tracking`: fresh DBs (PG + SQLite) get CURRENT model schema via `SQLModel.metadata.create_all`; existing PG DBs get evolution from `_ensure_postgres_columns`. The test must document the gap explicitly so future contributors don't assume parity. | Task 3.A.1 | Pin test passes; the gap is documented in the test docstring. |
| 3.A.10 | Augment `tests/unit/tools/test_frozen_tool_name_discovery.py` (if needed — KNOWN_TOOL_NAMES regen in Phase 1.C.4 should have already passed this pin). Re-run to confirm. | Task 3.A.1 | Pin passes. |

### Phase 3.B — Docs + decisions close-out (≤1 worker, parallel with 3.A)

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.B.1 | Read `decisions.md` lines 631-668 (OQ#1–7) and the open-question framing. | Phase 2 complete | Implementer can summarize each OQ's disposition from the Phase 0 architect gate + Phase 1–2 decisions. |
| 3.B.2 | Finalize `_full_doc_` for each of the 5 tools. Cover: (a) `service_start` — return shapes (running, name_in_use, cap_exceeded, invalid_name, kill_switch_off); (b) `service_stop` — 4-result-tuple (running, exited, not_found, pid_recycled); **A4 — `exit_code` is `None` for deaths not observed via `service_stop`** (no `wait()` handle is retained after spawn); document this in `service_status` + `service_stop` `_full_doc_`; (c) `service_status` — return shapes (running, exited, not_found); (d) `service_list` — return shape (list of dicts, sorted by created_at DESC, with inline liveness reconciliation); (e) `service_logs` — return shape (string, last N lines, OOM-capped to 10MB). **F10 — STATE `exit_code=None` AFFIRMATIVELY in `_full_doc_` spec** (not just implied): the docstring for `service_status` MUST include the literal line `"exit_code: int \| None. None when the service exited without `service_stop` observing the death (no `wait()` handle retained); an int (the observed exit code) only when `service_stop` signals the process AND the process exits before the grace period escalates. See decisions.md D3 — the spawner does NOT retain a wait handle after Popen returns, so any death not observed via `service_stop` returns `exit_code=None`."` The docstring for `service_stop` MUST similarly call out that `exit_code=None` in its return shape for `status: "exited"` paths is the expected default (the process exited during the grace period or the pid was recycled). The case where `exit_code=int` (e.g. `137` for SIGKILL) is the exception, not the rule. Each full_doc should fit in ~30 lines and use the precedent at `proc_tools.py:2154-2169` and `bash.py:415-433`. | Task 3.B.1, Phase 1.B.4 | All 5 `_full_doc_` strings updated; `service_status` + `service_stop` document the `exit_code=None` limitation per A4; F10 verified: `grep -A5 "exit_code" daemon/tools/service_tools.py` shows the affirmative documentation in BOTH tools' `_full_doc_`; the cold-boot doc test in `TestMaintenancerToolsDocColdBoot` still passes. |
| 3.B.3 | Create `docs/architecture/instance-lifecycle.md` exemption-invariant section. Content: (a) name the "services are kill-exempt" invariant with citations to `research-lifecycle-killsites.md`; (b) document the registry-scoped fence — any new `os.killpg` / `os.kill` / `killpg(0)` / `/proc`-walk call site MUST respect the registry-scoped invariant; (c) **A7 — CI grep-gate enforcement (NOTE: gate moved to Phase 1 merge gate per F5 — this task covers the DOC + the LINK from the architecture doc to the Phase 1 gate):** document that the CI guard at `test/packs/service_tool_kill_site_invariant.sh` (owned by Phase 1.MG.1a) fails PRs introducing new kill primitives in `daemon/` outside the allowlisted trio (`daemon/tools/bash.py`, `daemon/tools/proc_tools.py`, `daemon/services/vscode_server_manager.py`); (d) link to the Phase 2 parametric test as the regression net; (e) cite `upgrade_journal.spawn_executor` as the precedent; (f) cite `bash.py:74-79` and `proc_tools.py:1636-1643` as the documented limitation that `service` solves. | Task 3.B.1 | Doc section exists; CI grep-gate section cites the Phase 1.MG.1a gate (enforcement lives in Phase 1 per F5); doc serves as the architectural rationale + linkage; the CODEOWNERS fence is a TODO today — the Phase 1 gate is the enforceable fence. |
| 3.B.3a | **A7 — CI grep-gate (MOVED to Phase 1 merge gate per F5):** see `phase1-plan.md` task `1.MG.1a` for the script mechanics, allowlist, fail condition, and pass condition. **Phase 3 owns the ARCHITECTURAL DOC (3.B.3 above) linking to the Phase 1 gate; Phase 3 does NOT own the script itself.** The CODEOWNERS fence is a TODO today (no file exists in the repo) — the Phase 1 gate is the enforceable fence. | Task 3.B.1 | Phase 3 doc links to Phase 1 gate; no script duplication. |
| 3.B.3b | **Innate-skill negative pin (architect rider):** add a unit test asserting no `INNATE_SKILL_TOOL_CATEGORIES` value (`instance.py:157-164`) ever intersects `PRIVILEGED_TOOL_CATEGORIES`. The innate-skill helper (`expand_allow_for_innate_skills` at `instance.py:186-197`) appends categories to `allow` regardless of frozenset membership — a pre-existing bypass seam that `service` makes worth pinning. | Task 3.B.1 | Test passes; the negative pin catches any future regression. |
| 3.B.4 | Append a "Close-out" section to `decisions.md`. (NOTE: the leader-ratification fold-in already populated this in `decisions.md` §"Open Questions (architect escalation — ALL RESOLVED...") — Phase 3 verifies the fold-in is intact and adds only any Phase-2/Phase-3-specific follow-ups. For each OQ#1–7, record: (a) disposition (per Phase 0 architect gate + Phase 1–2 decisions); (b) which Phase owns the resolution; (c) follow-up PR if deferred. Phrasing examples: "OQ#1: Resolved by leader-ratified Option A 2026-09-15; see Phase 1.C.3 + 1.C.7. Follow-up PR (per-category `default_open`) NOT pre-committed (architect §1.5)." | Task 3.B.1 | `decisions.md` §"Open Questions" header confirms 7/7 OQs RESOLVED; Close-out (if any Phase-2/3 follow-ups) lists them. |
| 3.B.5 | Create `.agents/tester/RESULTS/2026-09-15-service-tool-verification.md` template. Mirror the structure of existing RESULTS files (e.g., `2026-09-14-spawn-intelligence-verification.md`). Sections: (a) Header — Date / Author / Branch / SHA; (b) Scope — what was tested (5-tool surface, 13-site exemption, reconcile, cap, flag-OFF, fresh-SQLite boot, PG smoke); (c) Test matrix — tabular PASS/FAIL per case; (d) Acceptance criteria verification — SC-1 through SC-12 with pass/fail; (e) Out-of-scope confirmations — no OS service integration, no re-spawn, no command allowlist, etc.; (f) Restart-survival evidence — log excerpts from cold-boot `[ServiceTool] reconcile_swept` line; (g) Verdict — SHIP / FAIL / conditional; (h) Follow-ups (if any). | Task 3.B.1 | Template file exists at the expected path; tester can fill in the matrix. |
| 3.B.6 | Register the new PG smoke pack. If `.agents/tester/PACKS.md` exists, add a `service_tool_pg_smoke_integration_test.sh` entry pointing to the new pack file. Mirror the entry shape used for `ensure_deferred_pg_smoke_integration_test.sh`. | Task 3.B.1 | Entry exists; tester can discover the pack. |
| 3.B.7 | Write the OPS note (matches project convention per critical-notes). Content: (a) kill-switch `ENSEMBLE_SERVICE_TOOL_ENABLED` default ON, flip via env + restart; (b) knob envs `ENSEMBLE_SERVICE_TOOL_MAX_CONCURRENT` (default 10) and **`ENSEMBLE_SERVICE_TOOL_RECONCILE_INTERVAL_SECONDS`** (default 90s, A8 renamed); (c) restart-pending semantics: schema migration requires daemon restart; flag flips require restart; (d) verification recipe — `grep '\[ServiceTool\]' data/logs/ensemble.log` to confirm boot probe + per-call logs; (e) operator recovery for cap-exceeded — stop some services, raise cap, restart. **F4 — Deployment / Activation section:** the OPS note MUST include an explicit "Deployment / Activation" section documenting that **frozen-PyInstaller prod activation** requires (i) **FE rebuild + restart** for new modules visible to the FE (`daemon/tools/service_tools.py` appears in `tool_help` + cold-boot doc test), and (ii) **daemon rebuild + restart** for backend changes (the `daemon/` changes — repo, manager, lifespan, config). Daemon restart alone is INSUFFICIENT for new code paths that ship in a frozen binary. The activation sequence is the same precedent the project's main `frozen-ensemble-prod` binary follows: rebuild the binary, restart, verify boot-probe log line appears, then optionally also rebuild the FE. Cite the existing daemon-frozen-PyInstaller precedent; mirror the FE rebuild steps already documented in the project's build pipeline. **F4 — boot-probe verification recipe:** the OPS note's "verification" step explicitly tells the operator to grep `data/logs/ensemble.log` for `[ServiceTool] service_tool_enabled=` at the FIRST boot after rebuild — absent this line, the feature is NOT active even if `service_*` tool names appear in `tool_help` (the FE-side warm-list is built at boot and may show surface names without the backend being active). | Task 3.B.1 | OPS note exists; discoverable for future ops; A8 rename reflected; F4 Deployment / Activation section present; F4 boot-probe verification recipe present. |

### Phase 3 merge gate

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 3.MG.1 | Run the FULL test surface from a clean worktree: `uv run python -m pytest tests/unit/tools/test_service_registration.py tests/unit/repositories/test_service_tool_repository.py tests/unit/tools/test_service_tools.py tests/unit/test_service_spawner.py tests/unit/services/test_service_reconciliation.py tests/test_loader.py::TestMaintenancerToolsDocColdBoot tests/unit/tools/test_frozen_tool_name_discovery.py tests/unit/tools/test_upgrade_registration.py tests/unit/tools/test_attestation_registration.py **`tests/integration/test_maintenancer_spawn_resolves_tools.py`** (A14 third pin) `tests/unit/test_ensure_deferred_schema_pin.py tests/integration/test_service_tool_kill_site_exemption.py tests/integration/test_service_tool_cap_enforcement.py tests/integration/test_service_tool_flag_off_byte_identical.py tests/integration/test_service_reconciliation_real_pg.py` (if added). | All 3.A.* and 3.B.* tasks | ALL tests pass; no regressions; the parametric 13-site matrix reports 13/13 (K12 SKIP on darwin); cap enforcement passes; flag-OFF pin passes; PG smoke pack passes; **A14 third pin passes**; **A7 CI grep-gate passes on clean worktree**. |
| 3.MG.2 | Boot the daemon in a test worktree (`dev.sh`) with `ENSEMBLE_SERVICE_TOOL_ENABLED=1` (default); spawn a real service via the API; trigger `manager.shutdown()`; restart; call `service_status(name)`; verify the row state matches the OS state and the boot INFO log line is emitted. | Task 3.MG.1 | End-to-end restart-survival verified in a real daemon boot; SC-2 demonstrably met. |
| 3.MG.3 | Boot the daemon with `ENSEMBLE_SERVICE_TOOL_ENABLED=0`; grep `data/logs/ensemble.log` for `[ServiceTool] service_tool_enabled=False`; verify zero `service_*` tool references in any agent's resolved tool list (via `GET /agents/{id}/tools`); verify `ServiceReconciliationService` not started. | Task 3.MG.1 | Flag-OFF state byte-identical to pre-Phase-1 behavior; SC-6 + SC-10 demonstrably met. |
| 3.MG.4 | Verify `git diff --stat` matches the Touched Files table; verify NO edits to `daemon/services/instance_lifecycle.py` or `daemon/manager.py` shutdown body beyond Phase 1's `_ensure_postgres_columns` block; verify NO additions to `daemon/tools/bash.py`, `daemon/tools/proc_tools.py`, or `daemon/services/vscode_server_manager.py`. | Task 3.MG.1 | Diff matches expected scope; exemption invariant preserved. |
| 3.MG.5 | Run the project's standard "ready to merge" gate: `uv run python -m pytest tests/` (full suite) + the boot-probe pack + the PG smoke pack. Verify no regression. | Task 3.MG.1 | Full suite green; ready to merge. |

---

### 3.MG.1 Runbook (assembled 2026-09-15)

Caller-pinned fact: `pyproject.toml` line 80 sets `addopts = "-m 'not integration and not postgres'"`, so integration-marked tests are deselected by default and a bare invocation would collect zero and pass vacuously. Integration files (e.g. `tests/integration/test_service_tool_kill_site_exemption.py` carries `@pytest.mark.integration` on the parametric 13-site cases — verified at `:182`, `:236`, `:282`, `:328`, `:382`, `:432`, `:472`, `:528`, `:583`, `:608`, `:649`, `:922`) MUST be invoked with an explicit `-m integration`. The runbook below therefore splits the `3.MG.1` file list into a UNIT block (plain `pytest`, harmless under the default `addopts` because these files carry no `integration` / `postgres` mark) and an INTEGRATION block (must carry `-m integration`). Each command line is independently copy-pasteable.

Status legend: `[exists]` = file present in the worktree, runnable today; `[done 3.A.x — <sha>]` = Phase-3 test landed, runnable today (sha = landing commit); `[pending 3.A.x]` = file does not exist yet, will be created by the cited Phase 3.A task and is NOT runnable until that task lands.

#### UNIT block (plain invocation)

```
[exists]    uv run python -m pytest tests/unit/tools/test_service_registration.py
[exists]    uv run python -m pytest tests/unit/repositories/test_service_tool_repository.py
[exists]    uv run python -m pytest tests/unit/tools/test_service_tools.py
[done 3.A.4 — d617bd5b]  uv run python -m pytest tests/unit/test_service_spawner.py
[exists]    uv run python -m pytest tests/unit/services/test_service_reconciliation.py
[exists]    uv run python -m pytest tests/test_loader.py::TestMaintenancerToolsDocColdBoot
[exists]    uv run python -m pytest tests/unit/tools/test_frozen_tool_name_discovery.py
[exists]    uv run python -m pytest tests/unit/tools/test_upgrade_registration.py
[exists]    uv run python -m pytest tests/unit/tools/test_attestation_registration.py
[exists]    uv run python -m pytest tests/unit/test_ensure_deferred_schema_pin.py
```

#### INTEGRATION block (each line MUST carry `-m integration`)

```
[exists]    uv run python -m pytest tests/integration/test_maintenancer_spawn_resolves_tools.py -m integration -q
[exists]    uv run python -m pytest tests/integration/test_service_tool_kill_site_exemption.py -m integration -q
[done 3.A.6 — bf0b6590]  uv run python -m pytest tests/integration/test_service_tool_cap_enforcement.py -m integration -q
[done 3.A.7 — 68680519 + ebd8a5e6 (W2)]  uv run python -m pytest tests/integration/test_service_tool_flag_off_byte_identical.py -m integration -q
[pending — conditional per plan row]  uv run python -m pytest tests/integration/test_service_reconciliation_real_pg.py -m integration -q
```

#### A7 grep-gate (kill-site invariant — killsite regression net)

```
[exists — landed d687bbde]  bash test/packs/service_tool_kill_site_invariant.sh
```

#### PG smoke pack (Phase 3.A.8)

```
[done 3.A.8 — c11529df]  bash test/packs/service_tool_pg_smoke_integration_test.sh
```

#### Notes

- All `pytest` invocations use `uv run python -m pytest` from the worktree root. Bare `pytest` is forbidden — Homebrew PATH trap per Testing & QC Conventions §Execution gate.
- 3.MG.5 (the FULL `uv run python -m pytest tests/` sweep) belongs to the tester agent / merge-gate owner — it is explicitly OUT OF SCOPE for this 3.MG.1 runbook and is not duplicated above.
- `tests/unit/services/test_service_tool_manager.py` exists in the worktree (Phase 1 stub expanded under 3.A.5 scope) but is NOT enumerated in the 3.MG.1 row's file list; it carries no `pytest.mark` decorator and is covered by 3.MG.4's `git diff --stat` scope gate. It is therefore not added to the UNIT block above to avoid drift with the plan row.
- A14 third pin (`tests/integration/test_maintenancer_spawn_resolves_tools.py`) sits in the INTEGRATION block above; without `-m integration`, the pin is silently skipped and the frozenset-add validation cannot be exercised.

---

## Coupling (Phase 3 internal)

- **Loose:** 3.A (tests) ↔ 3.B (docs) — `_full_doc_` finalization (3.B.2) does NOT change tool API; tests can develop against the Phase 1 stub first.
- **Tight:** 3.A.2 ↔ Phase 1.A — the repo suite tests the schema Phase 1.A wrote.
- **Tight:** 3.A.5 ↔ Phase 2.A — the reconcile suite tests the sweep Phase 2.A implemented.
- **Tight:** 3.A.8 ↔ Phase 1.C.13b — the PG smoke pack tests the `_ensure_postgres_columns` block Phase 1.C.13b added (REASSIGNED from 1.C.6 per F3).
- **Independent of:** other phases.

## Risks (Phase 3-specific; cross-phase risks in `plan-overview.md`)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1.P3 | Tester's RESULTS verification reveals a test that passes Phase 1 + 2 gates but fails a hidden acceptance criterion (e.g., one of the 12 SC's is under-tested) | Medium | Low | The Phase 3 merge gate (3.MG.1) runs the FULL suite including the Phase 2 13-site matrix + Phase 3 cap + Phase 3 flag-OFF; the 12 SCs are each backed by at least one test case. If a gap is found, add the missing test in 3.A. |
| 2.P3 | `_full_doc_` finalization (3.B.2) drifts from actual tool behavior — LLM agent mis-diagnoses a `pid_recycled` result, etc. | Medium | Low | (a) Each `_full_doc_` is reviewed against the implementation in Phase 1.B; (b) the `tool_help` cold-boot test verifies docs surface; (c) the agent-facing tool docs are also covered by `tests/unit/tools/test_service_tools.py` case (c) — `_full_doc_` present on every tool. |
| 3.P3 | `docs/architecture/instance-lifecycle.md` does not exist at the assumed path | Low | Low | Locate the canonical lifecycle doc first (likely `docs/architecture/instance-lifecycle.md` or `docs/architecture.md`); if neither exists, create the file with the new section. |
| 4.P3 | OPS note in 3.B.7 references a different kill-switch style than the project convention (e.g., documents `ENSEMBLE_*` env naming without acknowledging the `_resolve_*` resolver pattern) | Low | Low | The OPS note explicitly cites `config.py:2702-2770` (resolver pattern) and `config.py:3581-3591` (boot probe) so the note references the actual implementation rather than fabricating a different convention. |
| 5.P3 | Foreign session's dirty worktree still blocks PR creation at Phase 3 (per caller note) | Medium | Medium | Per blueprint recipe: implementer reconciles foreign dirty state (`write-tree == HEAD^{tree}`) before creating a fresh `feature/service-tool` worktree. The Phase 1 + 2 PRs (if split) handle their own reconciliation; Phase 3's PR is the final commit. |

## Rollback / Kill-switch

- **Phase 3 ships NO new code paths that affect runtime behavior.** All Phase 3 tasks are tests + docs + decisions close-out. Rollback is `git revert` of the Phase 3 commits — runtime is unaffected.
- **Phase 3 docs (`docs/architecture/instance-lifecycle.md`):** if the architect decides the registry-scoped invariant should be documented elsewhere (e.g., a CONTRIBUTING.md), the file can be moved without affecting runtime.
- **Phase 3 RESULTS template:** purely documentation; rollback = delete the template.

---

## Exit Criterion

Phase 3 is complete when ALL of the following are true:

1. `uv run python -m pytest tests/` full suite passes (no regressions, all new tests green).
2. `test/packs/service_tool_pg_smoke_integration_test.sh` passes on a disposable PG.
3. The 12 success criteria (SC-1 through SC-12) from `plan-overview.md` are each demonstrably met by at least one passing test or one cited file:line.
4. `docs/architecture/instance-lifecycle.md` exemption-invariant section exists and cites the Phase 2 parametric test as the regression net.
5. `decisions.md` Close-out section lists all 7 OQs with dispositions.
6. `.agents/tester/RESULTS/2026-09-15-service-tool-verification.md` template exists, ready for the tester to fill in.
7. The feature is READY for the tester to verify and SHIP.

Phase 3 → tester verification → SHIP. The implementation is complete.