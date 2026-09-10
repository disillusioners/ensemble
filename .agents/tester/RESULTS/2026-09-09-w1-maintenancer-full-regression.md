# W1 Full Regression — maintenancer-agent branch

**Date:** 2026-09-09
**Branch:** `feature/maintenancer-agent` @ `c24e399d` (W1 integrated: P1 scaffold → P2 ens-db tools + de-scope → P3 KB → FIX-NOW batch)
**Base:** `a6b0ac0f` (`latest`, v0.12.4)
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-maintenancer-a` (clean, drift-pinned, `daemon.__file__` verified inside worktree on every pack)
**Spec:** `.agents/shared/planning/maintenancer-agent/detail-plan.md` (worktree-local)
**Workers:** 10 (1 discovery `807536bf`, packs `4e6c6b91`/`606bd908`/`601753d0`/`2dcfad83`/`5b684002`/`bbf50b79`/`16dea2a2`/`8c50f8a1`/`fe81b45d`)

## W1 GATE VERDICT: ❌ FAIL — 1 branch-caused failing locus (+1 important non-failing defect)

| # | Severity | Locus | Finding |
|---|----------|-------|---------|
| 1 | 🔴 critical (merge-blocking) | `tests/unit/tools/test_prompt_section_reference_integrity.py::test_no_bare_md_filename_tokens_in_prompts[kb-curator.md]` | **W1 branch-caused**: `agents/maintenancer/skills-template/kb-curator.md` (NEW in P3, merge `91651cb2`) contains **4 bare `memory.md` filename tokens** — violates the pure-section-references convention v2 (path tokens eliminated from agent prompts, permanent integrity gate, merged `50080e81`). File is branch-new → attribution certain, no base run needed. Merging W1 as-is reds the standing integrity gate on `latest`. Fix = rewrite the 4 references to section-name form (content decision: section refs vs rephrase — maintainer call, outside tester no-edit scope). |
| 2 | 🟠 important (not a failing test) | `tests/unit/tools/test_ens_db_repair_idempotent.py:33-51` fixture; gates at `:148-150`, `:181-183` | **2 structurally dead PG pins**: `TestIdempotentAcrossBeginCommit::test_ddl_with_do_block_runs_twice_with_no_net_change` + `TestIdempotentInsertOnConflict::test_upsert_idempotent_under_re_run` skip PG-only SQL with "SQLite cannot execute" **even when `ENSEMBLE_TEST_PG_URL` is set** — the `engine` fixture is hardcoded to `sqlite:///` and never wired to the env var (unlike `test_ens_db_tools_select_only.py::shared_engine` which calls `_maybe_pg_engine()`). The "4 PG-conditional skips convert to passes" expectation is structurally satisfiable for only 2/4. Dead acceptance tests in a branch-new file → W1 test-design shortfall (plan 2.10c dual-engine coverage half-unfulfilled). Fix = parametrize/idempotent-fixture refactor (test-code only, Test-Architecture-Fix class). |

**Everything else: 0 branch-caused failures.** All 20 agent-meta failures at HEAD are base-evidenced pre-existing (see Attribution).

## Scope Decision

Full regression per caller's enumerated surface (cross-module change: new agent + 17 files, ens-db tools, de-scope of 4 metas + 7 prompt files → full surface warranted). Additions beyond the literal list, both blast-radius-derived: `tests/unit/tools/test_prompt_section_reference_integrity.py` (1287 tests — the permanent integrity gate; W1 edited 7 prompt files + shipped new agent prompts) and `tests/test_registry.py` (102 — agent-registry discovery of the new agent). Both additions were load-bearing: the integrity gate produced the FAIL verdict; registry confirmed discovery.

## Per-Suite Results (ground-truth `--collect-only` counts, drift-pinned @ c24e399d, serial, dual-layer timeout: outer `timeout 300` + pyproject per-test 30s)

### New W1 suites

| Pack | Suite (file) | Collected | Passed | Failed | Skipped | Result | Runtime |
|------|--------------|----------:|-------:|-------:|--------:|--------|---------|
| P1 | tests/unit/test_maintenancer_agent.py (incl. folded prompt-compliance) | 41 | 41 | 0 | 0 | ✅ PASS | 4.75s* |
| P1 | tests/unit/tools/test_upgrade_registration.py (pin :100 updated) | 21 | 21 | 0 | 0 | ✅ PASS | — |
| P1 | tests/unit/tools/test_attestation_registration.py (pin :154 updated) | 24 | 24 | 0 | 0 | ✅ PASS | — |
| P2 | tests/unit/tools/test_ens_db_tools_select_only.py | 17 | 15 | 0 | 2 (PG) | ✅ PASS | 1.59s* |
| P2 | tests/unit/tools/test_ens_db_repair_audit.py | 3 | 3 | 0 | 0 | ✅ PASS | — |
| P2 | tests/unit/tools/test_ens_db_repair_idempotent.py | 3 | 1 | 0 | 2 (PG) | ✅ PASS | — |
| P2 | tests/unit/tools/test_ens_db_repair_refuses_non_idempotent.py | 19 | 19 | 0 | 0 | ✅ PASS | — |
| P2 | tests/unit/tools/test_privileged_category_system_log.py | 16 | 16 | 0 | 0 | ✅ PASS | — |
| P2 | tests/unit/tools/test_ens_db_repair_killswitch_r13.py (byte-identical OFF pins ✓) | 24 | 24 | 0 | 0 | ✅ PASS | — |
| P3 | tests/unit/test_maintenancer_kb_coverage.py | 41 | 41 | 0 | 0 | ✅ PASS | 0.29s* |
| P3 | tests/test_loader.py::TestMaintenancerToolsDocColdBoot | 2 | 2 | 0 | 0 | ✅ PASS | — |
| P4 | tests/integration/test_maintenancer_spawn_resolves_tools.py (real-meta) | 13 | 13 | 0 | 0 | ✅ PASS | 0.31s* |
| P4 | tests/integration/test_maintenancer_upgrade_gate_refuses.py | 2 | 2 | 0 | 0 | ✅ PASS | — |

*runtime is pack-level (multi-file single invocation). The 4 PG skips in P2 are the expected SQLite-env conditionals; kill-switch byte-identical pins (`TestRepairKillSwitchFlagOffByteIdentical` ×5 incl. parametrized short-circuit probes) all green.

### Affected existing suites

| Pack | Suite (file) | Collected | Passed | Failed | Skipped | Result |
|------|--------------|----------:|-------:|-------:|--------:|--------|
| P5a | tests/test_loader.py (whole file) | 69 | 69 | 0 | 0 | ✅ PASS |
| P5a | tests/test_registry.py | 102 | 102 | 0 | 0 | ✅ PASS |
| P5a | tests/unit/tools/test_frozen_tool_name_discovery.py — `test_known_tool_names_matches_source_exactly_no_drift` ✅ | 6 | 6 | 0 | 0 | ✅ PASS |
| P5b | tests/unit/test_report_integrity_prompts.py | 67 | 67 | 0 | 0 | ✅ PASS |
| P5b | tests/unit/tools/test_prompt_section_reference_integrity.py | 1287 | 1269 | **1** | 17 (pre-existing conditionals) | ❌ FAIL — locus #1 above |
| P6 | tests/unit/test_wanderer_agent.py | 37 | 35 | 2 | 0 | ⚠️ 2F pre-existing (see Attribution) |
| P6 | tests/unit/test_worker_agent.py | 22 | 22 | 0 | 0 | ✅ PASS |
| P6 | tests/unit/test_watcher_context_builder.py | 18 | 9 | 9 | 0 | ⚠️ 9F quarantined family |
| P6 | tests/unit/test_devops_agent.py | 63 | 60 | 3 | 0 | ⚠️ 3F pre-existing |
| P6 | tests/unit/test_coder_agent.py | 39 | 38 | 1 | 0 | ⚠️ 1F pre-existing |
| P6 | tests/unit/test_coder_developer_migration.py | 5 | 0 | 5 | 0 | ⚠️ 5F pre-existing residue |

**HEAD totals across all packs: 1941 collected / 1899 passed / 21 failed / 21 skipped.** 20 of 21 failures base-evidenced or quarantined pre-existing; 1 branch-caused (locus #1).

## PG-Branch Results (dual-engine)

Provisioned **disposable** PostgreSQL 14.22 via local binaries (`initdb`/`pg_ctl`, port **15432**, db `ensemble_test`) — docker daemon down, path B used. 🚨 `ENSEMBLE_TEST_PG_URL` is UNSET in env and shell carries `POSTGRES_DB=ensemble_prod` (PROD) — the disposable instance was mandatory; prod never touched. Note: worktree venv lacked `psycopg2` (SQLAlchemy default PG driver) — worker installed `psycopg2-binary 2.9.12` **venv-only** (`uv pip install`; no repo/git change; disclosed as environment side-effect).

| Node | SQLite env | PG env (ENSEMBLE_TEST_PG_URL set) |
|------|-----------|-----------------------------------|
| `test_ens_db_tools_select_only.py::TestPerTxSetLocalStatementTimeout::test_set_local_emitted_inside_tool_tx[pg-if-available]` | SKIP | ✅ **PASS** |
| `test_ens_db_tools_select_only.py::TestDualEngineCoverage::test_select_only_guard_runs_on_either_engine[pg-if-available]` | SKIP | ✅ **PASS** |
| `test_ens_db_repair_idempotent.py::TestIdempotentAcrossBeginCommit::test_ddl_with_do_block_runs_twice_with_no_net_change` | SKIP | ⚠️ SKIP — dead pin (locus #2) |
| `test_ens_db_repair_idempotent.py::TestIdempotentInsertOnConflict::test_upsert_idempotent_under_re_run` | SKIP | ⚠️ SKIP — dead pin (locus #2) |

PG run: 20 collected / 18 passed / 2 skipped / 0 failed. Teardown verified (port 15432 free, no stray postgres, `/tmp/w1-pgdata` removed, 8088 untouched).

## Attribution Verdicts (base `a6b0ac0f` throwaway worktree, drift-pinned, `daemon.__file__` verified; Step A 4F/4, addendum 2F/2, Step B full files 98P/9F)

**VERDICT RULE: FAIL@base → pre-existing confirmed. PASS@base → W1 regression.**

| Node | @HEAD | @BASE | Verdict |
|------|-------|-------|---------|
| test_devops_agent.py::TestDevopsMetaJsonValidation::test_skill_injection_enabled | FAIL | **FAIL** | ✅ PRE-EXISTING CONFIRMED (devops meta `skill_injection` False vs expected True) |
| test_devops_agent.py::TestDevopsPromptComposition::test_no_opencode_skill_content_in_system_prompt | FAIL | **FAIL** | ✅ PRE-EXISTING CONFIRMED (innate extras `{question, chart}`) |
| test_devops_agent.py::TestDevopsMetaJsonValidation::test_innate_skills_is_dynamic_skill_and_todo | FAIL | **FAIL** | ✅ PRE-EXISTING CONFIRMED (innate_skills 4-elem vs expected 2) |
| test_coder_agent.py::TestCoderPromptComposition::test_load_coder_prompts | FAIL | **FAIL** | ✅ PRE-EXISTING CONFIRMED (`workflow` in prompts) |
| test_wanderer_agent.py::TestWandererMetaJsonValidation::test_tools_allow_has_all_declared_categories | FAIL | **FAIL** | ✅ PRE-EXISTING CONFIRMED (base allow-list 16 entries incl. `db`,`infra`,`blueprint`,`system-log`; W1 removed only `system-log` → 15; mismatch pre-existed, W1 *shrank* it) |
| test_wanderer_agent.py::TestWandererMetaJsonValidation::test_tools_allow_does_not_contain_db | FAIL | **FAIL** | ✅ PRE-EXISTING CONFIRMED (`db` present at base) |

**The impl-a attribution ("4 pre-existing devops/coder, meta-drift, input-disjoint") is CORRECT** — all 4 fail identically at base a6b0ac0f (and were previously base-evidenced @ `feb5e915` per PACKS.md:186). The HEAD pack initially classified the 2 wanderer failures as NEW; base adjudication corrected this to pre-existing — HEAD-side "NEW" classifications without a base check are provisional by construction.

Remaining HEAD failures, attributed by family (no base run needed — documented quarantine rows):
- `test_watcher_context_builder.py` ×9 — watchover evaluator/LLM-stub cascade family (QUARANTINE.md 2026-08-26, 47-node family; signatures match: NoneType.args ×6, default_streaming AttributeError, invoke-call-count ×2).
- `test_coder_developer_migration.py` ×5 — mock-linkage residue (`get_version().path` MagicMock vs `/agents/coder|developer`; PACKS.md:186 residue family) — node-for-node identical to the 5 migration rows in the base signature.
- Base full-file signature (devops 3 + coder 1 + migration 5 = 9F) ≡ HEAD subset for the same 3 files — 0 divergence.
- `TestAccessMemoryArchive` ×5 (tests/unit/tools/test_archive_lifecycle.py) — known pre-existing, quarantined acceptance suite for the separate access_memory archive-deny defect (critical-notes); NOT in W1 surface; attributed, not run, not fixed.

## Registry Boot Sanity ✅ (task item 6)

`AgentRegistry(Path('agents').resolve())` import-time scan on the worktree: **31 agents discovered, `maintenancer` present**, `AgentMetadata` entry resolves (`tools.allow = [system-log, ens-db, knowledge, system_upgrade, db]`, `skill_injection: true`, `team_members = [explorer, worker, coder]`) — matches W1 spec. Trap logged: `AgentRegistry(agents_dir)` does NOT auto-wrap `str`→`Path` (registry.py:508/521 — str input raises AttributeError in `discover()`); pass a `Path`.

## Ground Truth: de-scope claim verified

`git diff a6b0ac0f..c24e399d --stat -- agents/` = **28 files, +792/−57**: 4 de-scoped metas (developer, developer[v2], leader, wanderer — 1-line `system-log` removals each), 7 de-scoped prompt files (developer/{soul,tools_note,workflow}.md, developer[v2]/{soul,tools_note}.md, wanderer/{soul,tools_note}.md), 17 new `agents/maintenancer/` files. Wanderer meta diff = exactly one `system-log` removal. Matches the task's claim.

## Action Needed

- [ ] 🔴 Fix `agents/maintenancer/skills-template/kb-curator.md` — replace 4 bare `memory.md` tokens with section references (or rephrase); re-run `tests/unit/tools/test_prompt_section_reference_integrity.py` — **blocks merge**
- [ ] 🟠 Wire `test_ens_db_repair_idempotent.py` engine fixture to `ENSEMBLE_TEST_PG_URL` (mirror `shared_engine`/`_maybe_pg_engine()`) so the 2 PG-only pins can actually execute; re-verify with disposable PG
- [ ] 🟢 Pre-existing (non-W1) cleanups, separate workflow: devops/coder meta-drift 4 (tests expect old innate_skills/skill_injection state), wanderer tools.allow count/db 2, coder_developer_migration mock-linkage 5 — all base-evidenced; sweep-visible in QUARANTINE.md

## Environment Side-Effects / Notes

- `psycopg2-binary 2.9.12` installed into the W1 worktree `.venv` (PG driver; venv-only, no repo change, nothing committed)
- Base throwaway worktree `agents-ensemble-wt-w1base` removed post-adjudication (cleanup worker confirmed)
- No source or test files edited anywhere; no commits; no pushes; W1 worktree left clean @ c24e399d

## Documentation Updated

- [x] RESULTS/2026-09-09-w1-maintenancer-full-regression.md (this file)
- [x] QUARANTINE.md — added W1-gate adjudication row (devops×3 + coder×1 + wanderer×2, base-evidenced @ a6b0ac0f)
- [x] PACKS.md — W1 regression ad-hoc pack row (this gate)
- [x] LESSONS/2026-09-09-w1-gate-pg-fixture-wiring-and-provisional-new.md

---

# APPENDIX — Re-Verdict @ gate-fix commit `28efe5c4` (2026-09-09, same day)

**Trigger:** gate fixes landed on `feature/maintenancer-agent` — commit `28efe5c4` (parent `c24e399d`, 2 files +68/−16: `agents/maintenancer/skills-template/kb-curator.md` + `tests/unit/tools/test_ens_db_repair_idempotent.py`). 3 workers (`7f8bf0c6` A, `7c2373a2` B, `cd584f7e` C), all drift-pinned @ `28efe5c4`, `daemon.__file__` verified per pack, serial, dual-layer timeout.

## Item 1 — 🔴 kb-curator section refs: ✅ RESOLVED

- `tests/unit/tools/test_prompt_section_reference_integrity.py`: **1287 passed / 0 failed / 0 skipped** (1.29s) — prior failing node `test_no_bare_md_filename_tokens_in_prompts[kb-curator.md]` **now passes**. Skip delta 17→0 vs the c24e399d run (skip→pass direction only; no new skips) — benign, plausibly content-conditional skips on kb-curator.md satisfied by the rewrite.
- **Token audit**: `grep -ni "memory\.md" agents/maintenancer/skills-template/kb-curator.md` → **0 hits** (case-insensitive). Exceeds the dev's own claim (1 exempt hit at :27) — the fix rewrote both offending lines to prose ("read the KB INDEX in Maintenancer's Memory section"), so the `OPERATIONAL_PATH_RE` exemption is **never invoked**. Smuggling is structurally impossible: no token remains to exempt.
- **Gate-tamper check: CLEAN.** `git show --stat 28efe5c4` = exactly the 2 declared files; `git diff c24e399d..28efe5c4 -- tests/unit/tools/test_prompt_section_reference_integrity.py` = **EMPTY**. Gate test + `OPERATIONAL_PATH_RE` (integrity file :526) untouched; exemption remains legitimately used corpus-wide (reviewer/developer/planner[v2]/tidier[v2] own-path refs).

## Item 2 — 🟠 idempotent fixture: ✅ RESOLVED (ground-truth note: suite = 3 pins, consistent)

- **Fixture diff review**: SQLite recipe preserved verbatim as `_sqlite_engine(tmp_path)` — file-backed `tmp_path/"ens_db_idem.db"` + `NullPool` + WAL + `busy_timeout=10000`; **zero** StaticPool/WriteGuardSession hits. `_maybe_pg_engine()` mirrors `test_ens_db_tools_select_only.py:65-70` exactly (reads `ENSEMBLE_TEST_PG_URL`, NullPool, None when unset). Skip gates now engine-conditional (:200-202, :233-235).
- **SQLite-default engine**: 3 collected / **1P + 2S**, skip messages **byte-identical** to pre-change — default path unchanged.
- **PG engine** (disposable PG 14.22, local binaries, port 15432, prod never touched): combined run **20/20 PASSED, 0 skips** — idempotent **3/3** (DO$$ + UPSERT pins genuinely executing) + select_only **17/17**. Independently reproduced, not taken from the dev's report.
- **Execution proof**: `repair_log` audit rows — 20 after the combined run; **+12 on a clean idempotent rerun** (3 tests × dry-run+commit × 2 runs), corroborating the dev's claim; sample rows show committed DO$$-block + `CREATE TABLE IF NOT EXISTS idemp_t` with nonce/sql_hash/target_table, plus one `outcome=error` row (fail-closed error auditing works).
- 🟢 **Non-blocking hygiene warning (new, from my re-run)**: re-running the PG-env idempotent suite against the SAME database fails on its own setup — `DuplicateTable: relation "upsert_t" already exists` (test setup :239 uses plain `CREATE TABLE upsert_t`, no `IF NOT EXISTS`; dirty rerun 1F/2P, clean-DB rerun 3/3). Fresh-disposable-DB convention masks it; SQLite unaffected (tmp_path). Suggest `IF NOT EXISTS` or fixture-teardown drop — developer follow-up, not gate-blocking.

## Item 3 — No-regression sweep on the 2-file diff: ✅ CLEAN

Touched-file suites + consumers all green @ `28efe5c4`: section-integrity 1287/1287 (A), idempotent both engines (B), select_only PG-env 17/17 (B), kb-curator.md's own agent suites — `test_maintenancer_kb_coverage.py` 41/41 + `test_maintenancer_agent.py` 41/41 (C, 82/82 in 0.22s).

## FINAL W1 GATE VERDICT: ✅ PASS @ `28efe5c4`

Both FAIL loci from the c24e399d verdict are resolved with independent evidence; no regressions introduced by the fix; the integrity gate itself verified untampered. Branch is clear from the testing side. Standing follow-ups (non-blocking, tracked above): PG-rerun hygiene on `upsert_t` setup (🟢), and the pre-existing families (unchanged, quarantined).
