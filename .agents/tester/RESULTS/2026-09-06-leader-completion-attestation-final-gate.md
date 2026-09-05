# FINAL VERIFICATION GATE — Leader Completion Attestation (LCA) MVP

Date: 2026-09-06 (local +07; 2026-09-05 ~21:20–22:10 UTC)
Branch: `feature/leader-completion-attestation` @ `6e679c16` (base `e866c116`, 9 commits: a6001bf4/4958f1d0/871da567/249a7d1b/3da78c32/88f15535/fd23587c/e58e1ba3/6e679c16 — verified)
Blast radius: 71 files, +11,410/−115 (14 production + 3 agent-def + 49 test + 13 docs/planning)
Working tree at gate: 3 pre-existing dirty `.agents/` files (approver ×2, tidier) — untouched by gate; no commits, no pushes, no production-code changes by any gate worker.

## VERDICT: ✅ PASS (flipped 2026-09-06 — see §10 Re-Verification) — the sole merge-blocking defect (PG boolean default) is fixed at hotfix `6ab16261` and independently verified at every layer. Original gate at `6e679c16`: ❌ FAIL (§6). Everything else was already green; 0 unattributed failures across 15,950 executed tests; zero regressions vs base in the whole-repo unit regression (worktree A/B proven).

---

## 1. Job 1 — Full attestation matrix: ✅ PASS 313/313

- 32-file matrix (`tests/unit/test_attestation_*.py` ×9, `tests/unit/tools/test_attestation_*.py` ×3, `tests/integration/test_attestation_*.py` ×20): **296 passed / 0 failed in 7.32s** (`-o addopts=""`, unfiltered).
- 33rd file `tests/migration/test_attestation_migration.py` (17 tests, outside every `test_attestation_*` glob — lives in `tests/migration/`): **17 passed / 0 failed in 0.18s**.
- Reconciliation: 296 + 17 = **313 = developer-reported count, exact**. Prior "developer-reported" runs are now independently executed.
- Worker: lca-p1-matrix (7c3e4ae9), test-pack-execution skill.

## 2. Job 2 — Whole-repo unit regression: ✅ 0 branch-caused regressions (A/B proven)

Scope decision: full suite **warranted** (final merge gate, 71-file blast radius). Executed as 12 parallel ad-hoc partitions (each `timeout 300`, default addopts, no `-x`). Excluded with reason: `tests/e2e` (61 tests — live-daemon release-gate class + 1 pre-existing collection error needing external HTTP; LCA acceptance ran in-graph via scripted model instead), FE (zero UI surface on this branch), 214 marked-integration tests in `tests/integration` (live OpenCode server class, deselected by default addopts — standard posture).

| Partition | Scope | Result | Classification |
|---|---|---|---|
| U1 | tests/unit loose a–d (1,208) | 1175P/10F/2S/21E | all baseline (sealed 10F + 21E families) — exact |
| U2 | tests/unit loose e–l (1,116) | 1105P/11F | 11/11 pre-existing (base A/B R3) |
| U3 | tests/unit loose m–r (1,850) | 1843P/7F/40S | 1 baseline + 6 pre-existing (base A/B R8) |
| U4 | tests/unit loose s–z (1,035) | 974P/58F/11S/2E | **sealed 58F+2E fingerprint node-for-node EXACT** (watchover 47 + task_reconciliation 6 + 5 singles + webfetch 2E) |
| U5a | tests/unit subdirs non-tools (1,943) | 1936P/7F | 7/7 pre-existing (base A/B R2 — `_STATUS_CANONICAL_MAP` gap cluster) |
| U5b | tests/unit/tools (1,146+6d) | 1146P/0F | clean; TestAccessMemoryArchive deselected (quarantined, task-mandated exclusion) |
| U6 | services+repos+mqr+property+lint (1,468) | 1439P/2F/27S | 2/2 pre-existing (base A/B R1/R4); 3 known flakes all passed |
| U7 | tests/job_queue (1,674) | 1629P/7F/38S/2d | 7/7 = quarantined mission settled-rename family, exact |
| U8 | tests/ root loose a–g (848) | 795P/5F/48S | 4 baseline + 1 pre-existing (base A/B R4) |
| U9 | tests/ root loose h–q (1,504) | 1445P/59F/59S | 1 sealed + 19 fresh-SQLite migration trap (documented) + 39 pre-existing (base A/B R1/R6) |
| U10 | tests/ root loose r–z (1,542) | 1530P/12F/20S/12d/5x | 9 trap + 2 config drift + 1 pre-existing (base A/B R7) — all git-forensics + base evidenced |
| U11 | tests/integration non-attestation (303+311d) | 266P/20F/1S/16E | 23 SSL-env artifacts (cleared by unset, §5) + **2 BRANCH-CAUSED (PG defect §6)** + 11 pre-existing (base A/B R5) |

**Totals: ~15,637 partition tests + 313 attestation = 15,950 executed; 198F + 39E observed; attribution complete — 2 failures branch-caused (both the §6 defect), 0 unattributed.**

### Attribution evidence (three independent layers, all converging)
1. **Git forensics** (lca-attr-gitdiff): zero branch-plausible clusters — every failing test file AND its production counterpart untouched by the 9 LCA commits (manager.py/instance_messaging.py/repository.py hunks confined to attestation ledger/gate/resolver).
2. **Base worktree A/B** (lca-attr-baseworktree-2, house Worktree-Based Regression Proof; isolation proven via `daemon.__file__` resolving inside `/tmp/lca-base-e866c116`): R1–R9, **79/79 suspect nodes FAIL-AT-BASE with node-level signature identity** vs HEAD.
3. **Env adjudication** (lca-attr-env): the 23-node httpx-TypeError class = documented SSL-env artifact (stale PyInstaller certifi paths in shell); `unset SSL_CERT_FILE SSL_CERT_DIR` clears 23/23 of that class; 1 solo-persisting vscode C1 failure also fails identically at base (R9).

### New pre-existing baseline registered (QUARANTINE.md addendum this gate)
messages.py:258 MagicMock-await class (32 nodes), proxy-map 7, hide_kb 5, job_processor_status_guard 4, llm_allowed_models 2, title-gen 1, turn-state abort 1, U11 behavioral 11, config/memory 13, terminal_orphan 1, phase4 cascade_to_root 1, vscode C1 1, SSL-env httpx class 23, plus fresh-SQLite trap 28 (9 U10 + 19 U9). These predate e866c116 — latest-side debt, NOT LCA's.

## 3. Job 3 — Original-symptom E2E acceptance: ✅ PASS (independently constructed)

Worker: lca-e2e-acceptance (e3efc5c7), integration-test skill. New file `tests/integration/test_attestation_independent_verify.py` built from spec (NOT copied), scripted-chat-model seam (patches real `build_instance_llms`), real resolver (env-pinned, never patched), file-backed SQLite+WAL. **5/5 PASS in 0.64s**:
- `test_s1_hallucinated_end_denied_nudged_in_graph_then_attests_allow` — terminal child, no wakeups, hallucinated END → denied, nudge HumanMessage in state (`attestation_nudge=True`, exact NFR-6 text), NO enqueue + no new message_queue row, same-execution continue, attest → completed, counter reset 0. **Adds the instance-status read-back the flagship lacks (leader row stays non-terminal through the deny window).**
- `test_s2_active_child_allows_without_nudge_or_counter_reset` — RUNNING child + real PENDING watcher row → `allowed_legitimate_pending_wakeup`, no nudge, counter stays pre-seeded 2 (R1 non-reset live proof).
- `test_s3_bound_exhaustion_escalates_exactly_once_and_terminates` — 3 denies → `terminal_after_bound` on 4th, `completion_gate_escalated=True`, counter 0, no 4th nudge, exactly-one escalation event, terminal not hung (R2).
- `test_mdry_dry_log_on_dirty_row_zero_side_effects` — dry_log decision logged; pre-dirtied row (counter=2, escalated=True) BOTH unchanged → zero side effects, strongest form.
- `test_moff_gate_not_wired_end_unconditionally_allowed` — mode=off: `[AttestationGate] mode=off` build log, no gate evaluation, END allowed, dirty row untouched.
- Existing 5 files re-run: **13/13 PASS**; Phase A audit: all non-vacuous (real graph, real repo read-backs, negative asserts). Minor gaps noted (flagship lacks instance-status assert — closed by independent test; dry_mode ledger test structural-only — closed by M-dry).
- Rulings: R1 ✅ (gate docstring + graph ALLOWED-branch-only reset + S2 live), R2 ✅ (single atomic UPDATE, S3 live), R3 ✅ (driver + 4 fixtures exist, corpus test in matrix), R4 ✅ (resolver fail-open + boot log, §4).
- Evidence artifact: `.agents/tester/RESULTS/2026-09-06-lca-independent-e2e-testfile.py` (working tree restored; file deleted from tests/).

## 4. Job 4 — Mode matrix + boot log: ✅ PASS

- Tri-state semantics: covered in matrix (296) + independent M-off/M-dry re-proofs (§3).
- Boot-log resolved-mode line (daemon booted from this branch, port 8079 verified free first): VERBATIM —
  `Leader completion attestation resolved: mode=dry window=3 deny_bound=3 attestation_enabled=true N_le_min_recent_window=PASS (env ENSEMBLE_LEADER_ATTESTATION_MODE=<unset>, ...). Restart required to flip.`
  Defaults correct (dry/3/3); ruling 4 satisfied.

## 5. Job 5 — API smoke: ✅ PASS (optional, executed)

Boot 5s to healthy; `/docs` 200; `GET /api/instances?limit=5` 200 (7 rows, read-only); `/livez` → v0.12.0 alive; shutdown clean (own PID tree only, port freed, 8088 never touched).

## 6. 🔴 THE DEFECT (branch-introduced, merge-blocking): PG-invalid boolean default on `completion_gate_escalated`

**Three registration sites, two wrong, one right:**
| Site | Content | Effect |
|---|---|---|
| `daemon/repositories/instance/models.py:112` | `server_default=text("0")` on `Column(Boolean)` | **LIVE PG BREAK** — `metadata.create_all()` on fresh PG emits `BOOLEAN NOT NULL DEFAULT 0` → `psycopg.errors.DatatypeMismatch` |
| `daemon/migrations/versions/20260905_000001_attestation_ledger_columns.sql:54` | `BOOLEAN NOT NULL DEFAULT 0` | latent (file dialect-gated SQLite-only per header) but contradicts its own "PG+SQLite portable" claim |
| `daemon/manager.py:4774` | `BOOLEAN NOT NULL DEFAULT FALSE` | ✅ correct |

**Empirical proof:** `tests/integration/test_message_metadata_send_message_revive.py` 2 nodes fail deterministically on real PG: `DatatypeMismatch: column "completion_gate_escalated" is of type boolean but default expression is of type integer`. These nodes PASS at base (column is branch-added code — models.py is in the branch diff). SQLite's loose typing masked it in all 313 green matrix tests.

**Guard vacuity (test gap, same root):** `TestMigrationIsPgSqliteSafe` (17 params, all green) is a 7-substring PG-only-syntax grep — cannot detect int-literal-default-on-BOOLEAN; `test_completion_gate_escalated_default_is_false` asserts only `default is not None` on a SQLite inspector. Green-but-vacuous for this class.

**Minimal repro:** fresh PG DB → `SQLModel.metadata.create_all()` (or boot daemon with `POSTGRES_URL` to a fresh PG DB; or run the 2 revive-harness nodes).
**Fix direction (2 lines):** `server_default=text("false")` (or `false()`) at models.py:112; `DEFAULT FALSE` at migration :54. Recommended follow-up: extend the migration guard with a default-literal/type-agreement check (or compile DDL on a PG dialect in-test).

## 7. Failure classification table (branch-caused only)

| Node | Error | Class |
|---|---|---|
| `test_message_metadata_send_message_revive.py::TestSendMessageRevive::test_send_message_revives_completed_instance_and_read_stays_aget_only` | `psycopg.errors.DatatypeMismatch` (completion_gate_escalated boolean/int) | **introduced-by-branch** (§6) |
| `...::test_send_message_on_running_instance_does_not_fire_revive_branch` | same | **introduced-by-branch** (§6) |

Every other observed failure (196F+39E total): pre-existing-baseline (sealed fingerprints, quarantined families, base-A/B-proven, env-artifact, or documented fresh-SQLite trap). Full per-node tables in partition worker reports (preserved in this gate's worker transcripts; base logs at `/tmp/lca_pack_R1..R9.log`).

## 8. Action needed

- [ ] 🔴 Fix models.py:112 + migration :54 boolean defaults (2 lines) — blocks merge
- [ ] 🟠 Harden TestMigrationIsPgSqliteSafe against default-literal/type mismatch (guard gap)
- [ ] 🟠 Register the 33rd attestation file in the attestation pack glob (`tests/migration/test_attestation_migration.py` — currently outside every glob)
- [ ] 🟢 Latest-side debt backlog: messages.py:258 MagicMock-await class (32 nodes), proxy-map 7, and ~40 other base-evidenced pre-existing failures (new QUARANTINE rows) — none LCA's
- [ ] 🟢 Flagship test could adopt the independent file's instance-status assertion (S1 (i))

## 9. Documentation updated this gate

- [x] RESULTS/2026-09-06-leader-completion-attestation-final-gate.md (this file)
- [x] RESULTS/2026-09-06-lca-independent-e2e-testfile.py (evidence artifact)
- [x] PACKS.md — gate block + partition/pack registration
- [x] QUARANTINE.md — 13 consolidated pre-existing baseline rows (base-evidenced this gate)
- [x] LESSONS/2026-09-06-lca-pg-boolean-default-defect.md
- [x] LESSONS/2026-09-06-sealed-baseline-coverage-gap.md
- rules/ensure.md — untouched (user-owned)

**Overall: Unit/regression ✅ (0 branch regressions) · Attestation matrix ✅ 313/313 · E2E acceptance ✅ · Mode matrix ✅ · Boot smoke ✅ · PG-compat ❌ CRITICAL → original verdict at 6e679c16: FAIL.**

---

## 10. RE-VERIFICATION — hotfix `6ab16261` (2026-09-06, scoped): ✅ VERDICT FLIPPED TO PASS

Chain: …6e679c16 → `6ab16261` (`fix(lca): PG-valid boolean defaults on attestation ledger columns`). Scope per re-verification contract: the 5 fix-surface items only; the prior 15.6k-test attribution stands (hotfix touches nothing outside the 3 fix files — verified below). 3 fresh workers, verification-only.

| # | Item | Result | Evidence |
|---|---|---|---|
| H1 | The 2 deterministic failing nodes under real PG | **2/2 PASS in 1.68s** (PG 14 @ localhost:5432, user `ensemble`) — `psycopg.errors.DatatypeMismatch` gone | `test_send_message_revives_completed_instance_and_read_stays_aget_only` + `test_send_message_on_running_instance_does_not_fire_revive_branch` |
| H2 | Original repro: fresh-PG `create_all()` | **CREATE_ALL_OK, zero DatatypeMismatch**; catalog: `completion_gate_escalated → data_type='boolean', column_default='false'`; `attestation_denied_count → 'integer', '0'`; 42 tables, instances present; disposable DB `lca_hotfix_verify_*` dropped clean | imports mirror `daemon/migrations/data_migrator.py:62-71`; script at /tmp (not repo) |
| H3 | Migration suite + guard non-vacuity | **18/18 at HEAD**; **mutation-proven**: new guard transplanted onto a `6e679c16` worktree (isolation verified via `daemon.__file__`) → **1 failed / 17 passed**, failing with the exact offender `20260905_000001: 'BOOLEAN NOT NULL DEFAULT 0'` — the guard WOULD have caught the original defect | regex `(?i)\bBOOLEAN\b[^;\n]*\bDEFAULT\s+0\b` (statement-bounded); 5-file legacy allowlist with per-file justification, files NEVER edited (checksum ledger respected) |
| H4 | Attestation matrix at 6ab16261 | **314/314 PASS in 7.35s** (296 matrix + 18 migration; developer's claim matched exactly; new guard node `TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default` collected=1, PASSED) | full 33-file glob incl. `tests/migration/` |
| H5 | Hotfix diff scope | **EXACT**: 1 commit; exactly 3 files (`models.py`, `20260905_000001.sql`, `test_attestation_migration.py`); +74/−3 matches; verbatim hunks confirmed (`text("0")`→`text("false")` @ models.py:112; `DEFAULT 0`→`DEFAULT FALSE` @ migration :54 + :19 header; guard +71) | `git diff --name-only 6e679c16..6ab16261` |

Notes (non-blocking): (a) table count 42 vs developer's 41 — support-table tally delta, orthogonal; (b) prior gate's "33rd file outside every glob" concern remains a pack-registration follow-up (the re-verification command now includes it explicitly).

### Final verdict

- **At 6e679c16: FAIL** (§6 defect — sole blocker).
- **At 6ab16261: ✅ PASS — merge green-lit.** The defect is fixed at both wrong sites, verified on real PG (nodes + original repro path + catalog), and the guard is mutation-proven to catch recurrence. All other gate results carry over (untouched code).
- Residual follow-ups (non-blocking, from §8): register the migration file in the attestation pack globs; latest-side pre-existing debt backlog (QUARANTINE.md 2026-09-06 addendum rows).
