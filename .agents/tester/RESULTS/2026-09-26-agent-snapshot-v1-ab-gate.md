# Agent Snapshot v1 — A/B Acceptance Gate (2026-09-26)

**Branch:** `feature/agent-snapshot-v1` · **Feature tip:** `02b68247` (24 commits over base) · **Base:** `db71500a73da89159d3d3eaf48e748076a3c6fea` (v0.14.2 bump) · **Gate HEAD:** `79de1b5e` (= 02b68247 + 4 test-only gate commits: 2a691591 slice packs, d65982c7 FE spec, 0e249c42 spot pack, 79de1b5e PG smoke pack — production code verified UNCHANGED 02b68247→79de1b5e; zero daemon/agents/frontend-non-spec deltas)

**Method (house A/B):** 24 letter-range slice packs (zero-gap over 22,639 collected tests / 970 files; commit-stable globs; byte-identical invocation at both legs), run in per-worktree venvs (`daemon.__file__` containment proven per leg), env scrub (`POSTGRES*`/`ENSEMBLE*`/`DATABASE*`/`PG_TEST*` + `SSL_CERT_FILE`/`SSL_CERT_DIR`) baked into every script, dual-layer timeout (240s internal / 300s wrapper), junit per slice → mechanical per-node diff (parser: `/tmp/snapab/compare.py`; matrix: `/tmp/snapab/ab_matrix.md`). Base legs via `SNAP_WT=/home/nea/ensemble-worktrees/agent-snapshot-v1-base` (detached @ db71500a). Solo tiebreaks for every HEAD-only candidate.

**Scope decision:** Full suite warranted — 58-file cross-module feature (+13,448/−248) touching daemon core, new repo/service/tool surface, 2 migrations, FE. Exclusions (documented): `tests/e2e/` (daemon+real-LLM lane, judged per execution-lane rule, not suite-run); `tests/postgres/` (PG-server-gated; compensated by Gate 3 targeted PG smoke); `tests/performance/test_message_api_cost.py` (real-PG producer cells; env-class both legs); bash suite `tests/test_release_journal.sh` (host-arch quarantined row 87, outside pytest scope).

---

## 1. Gate 1 — Full-repo suite A/B

### Totals
| Leg | P | F | E | S | Total |
|---|---:|---:|---:|---:|---:|
| HEAD (79de1b5e) | 22,112 | 199 | 38 | 300 | 22,649 |
| BASE (db71500a) | 21,825 | 197 | 46 | 300 | 22,368 |

- **SHARED failures (both legs): 222** — all pre-existing; cross-checked against QUARANTINE.md consolidated rows (job_queue 13-node set incl. TestSite1InlineMirrorFinalize ×3 — **fence verified fail-identical**; enqueue_shared TestTitleGeneration fenced node — **fence verified**; 5 TestAccessMemoryArchive deselected identically by pack convention). Un-ledgered-but-base-identical families recorded in QUARANTINE.md (2026-09-26 consolidated row): originator_instance_id ×8, gaia ×3, governor ×3, service_tool pid_dead, proactive-compaction T6 ×2, compact_executor WS-2.4, context_injection, mcp refresh (+1 base cascade), chart_tools module.
- **BASE-only failures: 21** — HEAD passes them (fixture-isolation/mcp cascade, vscode httpx order-variance, lazy_mint, vscode SPA routes = row-10 family, slash-commands ack-ordering = row 28 load-flake @3.4s under 16-way load). No action.
- **NEW tests (absent at base): 281 — ALL PASS** (snapshot feature suites: unit_root_s 153, unit_tools 105, root_r_z 14, content-hardening 9; + gate spot pack 29/29).
- **HEAD-only failures: 15** — dispositioned 100%:

| # | Node(s) | Disposition | Evidence |
|---|---|---|---|
| 8 | `tests/test_loader.py::TestLoadToolsDocForAgent::*` | **DETERMINISTIC — feature-caused (Blocker 2)** | 8/8 FAIL solo ×2; attribution: loader.py warm-scan guard changed `if _tool_metadata:` → `>= 20 entries` (db71500a..HEAD, 49+/10−); test pre-populates 4 → warm scan runs → `_load_growth_rules(MagicMock)` TypeError @ inner_soul.py:1389; test file unchanged in range |
| 2 | `table snapshots already exists` setup-errors (test_builtin_mcp_servers, unit_root_a_b) | **feature-infra finding (Blocker 3)** | HEAD-only by construction (base has no snapshots table); shared-engine fixture vs new metadata |
| 1 | attestation `test_scenario_b_live_judge` worker-crash | family-attributed | 8/9 attestation crashes base-reproduced; remainder scheduling-sensitive (rows 84/85 live-judge stochasticity) |
| 1 | `test_ab_resolution_force_resolve` | CONTEXT-FLAKE | solo 1F/2P + full-class 6/6; signature differs per occurrence (rows 14/25) |
| 1 | `test_grace_period_zero` | LOAD-FLAKE | solo ×3 PASS, internal elapsed 0.008s = 15.9% of 0.05 bound → QUARANTINE new row |
| 1 | `test_compaction_empty_guard_fallback::test_empty_string_summary_falls_back_to_truncation` | NEW-TEST partition flake | feature-authored file; 3/3 solo PASS (base-worker's "P@BASE" was node-absence misread) |
| 1 | `test_worker_notification::test_multi_worker_notification` | CONTEXT-FLAKE | 3/3 solo PASS (row 70, 3rd manifestation) |
| 1 | chart_tools `test_second_call_revives_same_charter` | CONTEXT-FLAKE | solo ×2 PASS |
| 1 | mcp `test_list_servers` | CONTEXT-FLAKE | solo ×2 PASS (InvalidRequestError-refresh family; note: base leg worker's "shared" claim was contradicted by its own junit — XML diff authoritative) |
| 1 | vscode C4 `test_c4_vscode_status_alias` | CONTEXT-FLAKE | solo ×2 PASS (rows 41/66 httpx family) |

**Unexplained regressions: 0.**

### Per-slice totals (24 × 2)
See `/tmp/snapab/ab_matrix.md` (authoritative). Slices fully green both legs: top_opencode, top_tools, unit_small_subdirs, unit_tools_a_o. Largest red slices (top_misc 14F/1E, root_i_q 66F, root_r_z 13-14F, top_integration 8-10F+13-15E) are dominated by the documented migration-trap/Mock-await/growth-rules/attestation families — all base-identical.

---

## 2. Gate 2 — Execution-lane judgment + ensure.md in-scope validation

- **Lane verdict: execution-lane intersection EMPTY — job-queue e2e lane does NOT apply.** Static import scan (10 snapshot modules): zero task/job repo-processor references. Runtime audit: `import daemon` baseline = 0 modules; the task/job modules appearing in snapshot imports are package-`__init__` eager-load artifacts with ZERO symbol usage (cross-package symbol count: TaskProcessor/JobQueueService/JobItem/… = 0 across all snapshot modules). Reverse grep of lane modules: zero knowledge of snapshot modules. Capture = in-process `asyncio.create_task`/`asyncio.wait` only (snapshot_executor.py:1092-1094, :1155); boot sweep self-documents "zero job-system coupling".
- **ensure.md statics:** `dev.sh --timeout-graceful-shutdown 10` PASS at both legs (identical :102); await-caller check PASS (8/8 call sites of `_get_system_prompt_tokens`/`_compute_context_usage`/`get_queue_stats` awaited; zero snapshot-module callers).
- **Boot probe: FAIL — PRE-EXISTING-ENV, parity proven at base.** Scrubbed-env boot dies at migration `20260714_000001` (PG-only `DROP CONSTRAINT IF EXISTS` vs SQLite) — identical signature at HEAD and BASE (exit 3). Documented family (QUARANTINE rows 43/45/58). Deviation: dev.sh hardcodes PORT=8079 + requires OPENAI_API_KEY → probe ran direct uvicorn :18079.
- **ensure.md Core #2/#3:** `concurrency_atomic_unit_test` **PASS** 98P/74S @ HEAD (baseline-identical).

### ⚠️ INCIDENT (surfaced mid-flight, escalated)
First boot probe's env-scrub silently failed (`source` under `/bin/sh`/dash) → inherited ambient `POSTGRES_*` → **connected LIVE PG (ensemble_prod) and cleared 228 stale backlog messages + 228 tasks** (daemon's configured `discard_on_startup=backlog-clear`; in-flight/paused preserved per log — no active jobs lost). Second + base-parity probes ran via `#!/bin/bash` wrapper with verified-empty surviving vars (SQLite only). LESSONS/2026-09-26-boot-probe-scrub-sh-incident.md written (mandatory wrapper-file scrub pattern). Residual: `data_dev/` in HEAD worktree contains incident-time `ensemble.json` (PG-pointing) + `opencode_sessions.db` + IPC socket — left in place pending owner decision (reported, not deleted).

---

## 3. Gate 3 — First PG smoke: `filter_by_tags` dialect arm → 🔴 BLOCKER 1

Throwaway PG 16.15 cluster on :15432 (initdb trust, own PGDATA, isolation proven pre-write: `db=snap_smoke port=15432`; LIVE 5432 untouched — verified post-teardown). Migrations: project runner is a documented no-op on PG (runner.py:719-727 — .sql files are SQLite-only); PG schema = `SQLModel.metadata.create_all` → all 3 snapshot tables materialized; **GIN-free confirmed** (4 btree composites, 0 GIN; live PG indexes btree-only; `domain_tags` → jsonb).

**Result: FAIL/BLOCKER — PG arm broken.** `col(Snapshot.domain_tags).contains(wanted)` (repository.py:542,550) emits `LIKE '%' || :tags::JSONB || '%'`, NOT `@>` — `JSONBType` is a `TypeDecorator(impl=JSON)` so `Comparator.contains` routes to the JSON/LIKE path. PG raises `InvalidTextRepresentation: Token "%" is invalid` on **every non-empty-tag query** (matrix 8/10 cases PG-error vs SQLite-correct; empty-tags passthrough + tag_mode validation agree). Dialect-stripped proof: `cast(col, JSONB).contains(...)` emits `@>` and executes cleanly → fix = JSONBType comparator override (coerce_compared_value) or explicit cast at the two call sites. **Impact: every PG deployment crashes on first tag search** (unit CI never saw it — SQLite binds JSON-as-TEXT). Pack: `test/packs/ab/snapshot_pg_smoke_integration_test.sh` (self-provisioning, committed 79de1b5e). Rider (f) residual confirmed real, not theoretical.

---

## 4. Gate 4 — R14/R15/D8 behavior spot-checks → PASS

`tests/test_snapshot_behavior_spot.py` + pack (commit 0e249c42) — **14/14 PASS in 1.46s**, zero production defects:
- R14 warm/cold: 6-key result contract, `started`+`reason` always present, both explicit-id and internal-search paths (warm + all cold reasons: missing/expired/no-hit/verify-failed).
- R12: superseded never a candidate (explicit → verify-failed cold; internal-search top-superseded → no-hit cold — interpretation note: spawn re-verifies only the TOP candidate; search boundary owns superseded filtering; spec promises no fallback-through-list).
- R15: default OFF; `snapshot_create` clean-disable exact shape `{disabled, error}`; ON proceeds; **OFF does not break `spawn_hot_instance`** (cold fallback holds).
- D8: project-A caller + foreign-project snapshot id → cold (W1/acef1d3b path).

---

## 5. Gate 5 — FE Agent Snapshots settings toggle → PASS (gap flagged)

Jest 30 infra (npm ci 53s; node v22). Existing spec 82/82 → **90/90 after +8 focused tests** (commit d65982c7): renders (radiogroup aria-label + 2 radios + labels), initial state from GET, **fail-closed OFF on load error**, dirty-gate + Apply enable, save calls `setSnapshotCreateEnabled(true)` exactly once, server-echo re-sync, save-failure keeps saved value. `ng build` exit 0 (6 pre-existing SCSS budget warnings, none in settings). Contract verified structurally: `GET/PUT /api/settings/snapshot-create`, payload `{enabled}`, signal-bound radios, server-confirmed echo authoritative.
**AUTOMATION GAP (explicit):** HTTP mocked (jsdom) — no real daemon round-trip (endpoint existence/auth/restart-persistence unproven headlessly); no browser visual pass. Needs Playwright (ports 10000-19999) or manual pass on dev/demo.

---

## 6. ensure.md validation summary

| Requirement | Result |
|---|---|
| Core: changed packs green | Snapshot spot 14/14 ✓; new snapshot suites 281/281 ✓; **suite reds = Blockers 2+3 + fenced** |
| Core: concurrency_atomic_unit_test | **PASS** 98P/74S |
| Core: sync-DB-off-loop | covered by concurrency pack — PASS |
| Core: dev.sh graceful-shutdown grep | **PASS** both legs |
| Important: await callers | PASS 8/8 |
| Release Gate: full suite via packs | **DONE** (this A/B) — 0 unexplained regressions |
| Release Gate: E2E workflows (daemon+LLM) | **NOT RUN** — requires ./dev.sh + real LLM; deferred to main-checkout release ceremony (worktree boot hazard + LLM cost) |

### ensure.md Improvement Notices
- **The "4-line boot probe" citation is STALE**: `.agents/tester/rules/ensure.md` is a 53-line pack-mapped Core/Release-Gate doc. Commission/context notes citing "4-line probe" should be updated by the file's owner.
- Release-Gate E2E items mandate `./dev.sh` prerequisites that are unsafe in worktrees (hardcoded :8079 + OPENAI_API_KEY + the scrub incident mechanism). Suggested rewrite: mock-test pack variants (the file itself already prefers this) with worktree-safe boot instructions.

---

## 7. Verdict

### VERDICT: **BLOCKERS (3)** — feature otherwise clean

1. 🔴 **CRITICAL — PG `filter_by_tags` `@>` arm broken** (`daemon/repositories/snapshot/repository.py:542,550`): LIKE-instead-of-containment via `JSONBType(impl=JSON)`; every PG deployment crashes on first non-empty tag search. Fix isolated (cast at call sites or comparator override); reproducible pack committed.
2. 🟠 **DETERMINISTIC — loader warm-scan contract drift ×8** (`tests/test_loader.py::TestLoadToolsDocForAgent`): feature changed warm-scan-complete predicate to `>=20 entries` (loader.py, 49+/10−) without updating the test's 4-entry short-circuit premise. Test-side fix (pre-populate ≥20) or semantic reconsideration; small, must land before merge (suite red at HEAD).
3. 🟠 **Test-infra — snapshots-table fixture collision ×2** (`tests/unit/test_builtin_mcp_servers.py` shared-engine `get_router_engine`): `table snapshots already exists` setup-errors at HEAD by construction. Fixture engine-isolation fix needed (test-side) or the file stays red on every run.

**Non-blocking notes:** live-PG backlog-clear incident (owner assessment of the 228+228 discarded stale items recommended); `data_dev/` residue pending owner; attestation live-judge family (8/9 base-reproduced) and the 7 flake dismissals are quarantine-documented; FE real-daemon round-trip deferred; positive deltas: +281 new tests green, builtin_mcp −2F, service_tool gap −1E at HEAD.

### Code changes (all test-only, this gate)
- 2a691591 — 24 A/B slice packs + PACKS.md registration
- d65982c7 — FE settings spec +8 tests
- 0e249c42 — snapshot_behavior_spot pack + tests + PACKS.md
- 79de1b5e — PG dialect smoke pack + PACKS.md
- (this commit) — RESULTS, QUARANTINE.md (+2 rows), LESSONS (scrub incident), PACKS.md outcome line

### Documentation updated
- [x] RESULTS/2026-09-26-agent-snapshot-v1-ab-gate.md (this file)
- [x] QUARANTINE.md — grace_period_zero new row + 2026-09-26 consolidated adjudication row
- [x] LESSONS/2026-09-26-boot-probe-scrub-sh-incident.md
- [x] PACKS.md — A/B section + outcome
- [ ] rules/ensure.md — user-owned, read-only (Improvement Notices above)
