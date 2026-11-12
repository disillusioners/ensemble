# vsss-20261112 — version-scoped snapshot spaces — FULL TEST VERIFICATION

**FINAL VERDICT (re-gate @ `7d96affaf`): 🟢 SHIP** — fix cycle 2 clears the NO-GO; all code/test gates green at the new tip (mutants **5/5 KILLED**, integrated green, migration green, seal tripwire pass, 3 former reds now pinning intended outcomes). **One documented gap: gate 7 (backfill scratch-DB matrix) — blocked by the wallet cascade; see §10-E.** Merge is not gated on it (script is dry-run default, explicit `--db-url`, opt-in `--apply`).

**VERDICT HISTORY:** 🔴 NO-GO at `48e0ab662` — the branch introduced **3 net-new test failures** in a touched test file (green at base `6b644f770`, red at `48e0ab662`), attributable to the consume-arm space re-check landed in `5c81fbbbc`. → Developer fix cycle 2 (`7d96affaf`, test-only, 2 files +79/−3, `daemon/` byte-identical) → re-gate (§10): NO-GO cleared.

- Date: 2026-11-12
- Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-vsss`
- Branch: `feature/version-scoped-snapshot-spaces` @ TIP **`48e0ab6623e30f05f78608fea629168ca1097c2a`**
- Base: **`6b644f7700f90ec930a41cfcacc990faf699eef5`** (= origin/latest tip)
- Commits: `ac19e40ce` (core+tests+backfill) → `a1f66da38` (docs+SHA re-pin) → `5c81fbbbc` (REVIEW-1 fix: SQLite migration + consume-arm re-check) → `48e0ab662` (post-approve follow-up)
- Evidence integrity: every leg ran through `scripts/run_tests_scrubbed.sh` with `PYTHONDONTWRITEBYTECODE=1 TZ=UTC`, dual-layer `timeout`, `-p no:cacheprovider`, no `-x`. A/B legs used fresh detached worktrees with per-tree `uv sync --frozen` venvs (Python 3.13.3) and dual-cwd import proofs (`daemon.__file__` inside each leg tree, neutral-cwd pin certified).

---

## 1. The blocking finding (gate 2a / gate 3 delta)

`tests/unit/tools/test_snapshot_tools.py` — **3 failed at TIP, 0 failed at base** (same 73 collected both legs):

| Node | Base `6b644f770` | TIP `48e0ab662` |
|---|---|---|
| `TestUnifiedSpawnSnapshot::test_expired_top_match_cold` (:837) | PASS | **FAIL** |
| `TestUnifiedSpawnSnapshot::test_internal_search_hit_warms` (:855) | PASS | **FAIL** |
| `TestUnifiedSpawnSnapshot::test_stale_warm_carries_drift_note_in_citation` (:938) | PASS | **FAIL** |
| `TestRegistrationChain::test_gate_flag_enabled_on_targets` (old pin) | FAIL (old per-dir expectation vs `['developer','developer','tester']`) | PASS (re-pinned) |
| Totals | 1F / 72P | **3F / 70P** |

**Mechanism (same for all three):** the vsss belt-and-braces re-check at `daemon/tools/snapshot_tools.py:951-969` (landed in `5c81fbbbc`) re-reads the top search hit and refuses **cold** when the re-read row's space differs from the spawn key. The tests feed a canned search hit whose re-read row has `agent_space_key=None` while the spawn key is `'worker'`, so the result becomes `top match <id> space mismatch on re-read (row=None != spawn='worker') — refused (fail-closed)` and the warm/expired citation paths never execute.

**Two admissible adjudications (owner/design call, not the tester's):**
1. **Intended fail-closed semantics + stale tests** (most likely): mission Option A says NULL-key rows never auto-warm; the tests bypass the SQL predicate with a canned fake and therefore still assert legacy warm behaviour. Remedy = test-side update of those 3 nodes to assert cold/no-hit (mirroring the `test_wrong_space_search_receives_caller_space_key` update already in `48e0ab662`). Small test-debt fix.
2. **Over-strict belt**: the re-check refuses a legitimate legacy row in the automatic path. Remedy = relax the re-check (e.g., accept `None` re-read rows) — but that would contradict Option A fail-closed and needs review.

Whichever the owner rules, **as committed the branch reds a previously-green touched test file** → gate 2a/3 cannot pass → NO-GO.

> Note: the developer's "21 files 566P/0F/4-deselected at `a1f66da38`" baseline PREDATES `5c81fbbbc` (the commit that introduced the re-check), so it cannot vouch for these nodes. Also, the explicit 21-file list was never preserved in the repo/commits (verified by grep over `.agents/` + all 4 commit bodies) — the integrated set therefore had to be derived (touched ∪ snapshot-family).

---

## 2. Gate-by-gate results

| Gate | Result | Evidence |
|---|---|---|
| **1. Scope freeze @48e0ab662** | ✅ **PASS** | HEAD/branch correct; `status --porcelain` empty at freeze; exactly 4 commits on 6b644f770; `diff --numstat 5c81fbbbc..48e0ab662` = exactly 2 files **+10/−5** (`…124200_add_snapshot_agent_space_key.sql` 1/1 header reword; `test_snapshot_agent_spaces.py` 9/4 wrong-space outcome asserts); `merge-base --is-ancestor` rc=0. Full mission diff = **23 files** (incl. `manager.py` +49, `registry.py` +45, snapshot repo/models/services, `tools/instance.py` +32/−7, `tools/snapshot_tools.py` +76/−8, `scripts/backfill_snapshot_agent_space.py` +262). |
| **2a. New pack** | ✅ **PASS** | `tests/unit/test_snapshot_agent_spaces.py` — **31/31 passed**, exit 0 (replacement run, fresh; predecessor output not trusted). |
| **2a′. OQ2 explicit-path shapes (gate 6)** | ✅ **PASS** | 2/2 passed: `test_explicit_id_reaches_null_space_row` (NULL-key legacy row → warm SUCCESS) and `test_explicit_id_reaches_foreign_space_row` (**v1-space row consumed by a v2 caller** → warm SUCCESS; the brief's "v2-stamped row from v1 spawn" phrasing is inverted — actual shape confirmed from source, lines 675-697 / 699-725). |
| **2b. Registration-chain pin** | ✅ **PASS** | `TestRegistrationChain::test_gate_flag_enabled_on_targets` — 1 passed solo; expectation `sorted(enabled) == ["developer","developer[v2]","tester"]` (:1847), derived per-DIRECTORY via `registry._parse_agent_dir_name`. |
| **2c. sqlite guards** | ✅ **PASS** | `tests/unit/test_sqlite_guards.py` 10/10, exit 0; census corroborated (10 methods, no parametrize). |
| **2d. mission-record migrations** | ✅ **PASS** | `tests/unit/test_mission_record_migrations_sqlite.py` 6/6, exit 0; `--collect-only` census identical. |
| **2e. concurrency (ensure.md Critical)** | ✅ **PASS (quarantine-aware)** | `test/packs/concurrency_atomic_unit_test.sh` 94P/5F/74S, exit 1 bare (registered gotcha). All 5 reds = **QUARANTINE row 134** `DetachedInstanceError` in `tests/test_instance_metadata_atomic.py` (pre-existing/parent-identical) → does not fail the requirement. |
| **2f. `test_snapshot_tools.py` full** | 🔴 **FAIL (blocking)** | 3F/70P — see §1. |
| **2g. `tests/migration/` lane @TIP** | ✅ (2 adjudicated reds) | shard A (10 files): 2F/86P — `test_attestation_migration.py::TestNoBooleanIntegerDefaultInShippedMigrations::test_no_boolean_int_literal_default` + `test_auth_tables_g2_2.py::TestPgCreateAllIndexParity::test_pg_tables_and_indexes_materialize` (PG `InvalidSchemaName` env issue). shard B (10 files): 97P/3 deselected, exit 0. **Identical to base** (see gate 5). |
| **2h. migration comprehensive / critical-notes** | ⛔ **NOT RUN** | Workers died on the wallet cascade (`insufficient_balance`); not retried after the ladder exhausted. |
| **3. Integrated regression re-baseline** | 🟡 **PARTIAL** | `test_snapshot_tools.py` portion executed (3F — §1); the remaining derived set (snapshot-family 13 files, spawn/instance/registry extensions) was **NOT RUN** (workers died). Explicit developer 21-file list unavailable (see §1 note). |
| **4. Mutation proof A–E** | ⛔ **NOT RUN** | Baseline gates are green (new pack 31P; pin solo 1P), specs prepared, but all mutant workers died on the wallet cascade (A errored twice incl. its single revive; D errored). No survivor/kill claims are made. |
| **5. A/B legs** | ✅ **PASS** | **12 known reds identical base vs tip** (governor-recursion 10 ids + instance-tools 2 ids — per-id identical) and migration-lane sets identical (shard A 2 ids; shard B clean). Full table §3. |
| **7. Backfill scratch-DB matrix** | ⛔ **NOT RUN** | Worker died. Script contract documented from source (§5). |

**No verdict is claimed for gates 4, 3 (remainder), 2h, 7 — they were blocked by an environmental LLM wallet exhaustion (`insufficient_balance` cascade killed 14+ worker turns across the session; anomaly `15daba64` / notification-loop anomaly `9d48080c`).** Those gates can only strengthen a SHIP; they cannot overturn the proven regression in §1.

---

## 3. Gate 5 — A/B evidence (fresh legs, env parity)

Both legs: detached worktree under `/tmp`, `uv sync --frozen` exit 0 (117 pkgs, `ensemble==0.29.21` from the tree path), Python **3.13.3**, import proofs from tree cwd AND neutral `/tmp` cwd both resolving `/private/tmp/<leg>/daemon/__init__.py`; trees removed + pruned after capture.

| Cell (identical commands both legs) | BASE `6b644f770` | TIP `48e0ab662` | Verdict |
|---|---|---|---|
| `tests/unit/test_governor_recursion_guard.py` | 10F/13P — all `VisionModelNotAllowedError` via `compare_tools._verify_vision_allowed` | 10F/13P — **same 10 ids, same signature** | ✅ IDENTICAL (pre-existing) |
| `tests/unit/tools/test_instance_tools.py` | 2F/207P — planner `meta.json tools.allow` missing `subtree_messages` / `subtree_status` | 2F/207P — **same 2 ids** | ✅ IDENTICAL (pre-existing) |
| `tests/migration/` shard A (10 files) | 2F/86P (BOOLEAN-INT-DEFAULT pin; `auth_sessions` PG `InvalidSchemaName`) | 2F/86P — **same 2 ids** | ✅ IDENTICAL (pre-existing) |
| `tests/migration/` shard B (10 files) | 97P/3 deselected, exit 0 | 97P/3 deselected, exit 0 | ✅ IDENTICAL (clean) |
| `tests/unit/tools/test_snapshot_tools.py` | 1F/72P (old pin only) | **3F/70P (3 net-new)** | 🔴 **BRANCH-CAUSED** |

---

## 4. Scope freeze + anchors (resume aid)

- Stage-0 predicate equality leg: `daemon/repositories/snapshot/repository.py:596-605` (threaded `list_active_by_project` :481-511 → `list_with_filters` :515+; NULL rows never match — fail-closed, no fallback tier).
- Consume-arm re-check: `daemon/tools/snapshot_tools.py:951-969` (automatic branch only; refusal = `cold_reason="no-hit"` + warning string).
- Canonical key: `compose_agent_space_key` def `daemon/registry.py:34`; callers `daemon/tools/instance.py:3519/3531` (spawn threading), `daemon/services/snapshot_executor.py:1252/1258` (capture stamping).
- SQLite migration: `daemon/migrations/versions/20261112_124200_add_snapshot_agent_space_key.sql` — `ALTER TABLE snapshots ADD COLUMN agent_space_key TEXT;` + `CREATE INDEX ix_snapshots_project_status_space ON snapshots (project_id, status, agent_space_key);` (ADDITIVE; PG via `_ensure_postgres_columns`).
- 4 SHA seals re-pinned (`daemon/constants.py`, `agents/ari/rule.md`, `agents/developer/rule.md`, `agents/leader/rule.md`) — validated by the **default-lane** tripwire `TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts` (35-entry baseline; NO marker override needed). That test's own run is inside the unrun integrated subset — **seal validation pending**.
- New pack was **unregistered** at mission start; tester registered it + the re-pinned snapshot_tools pin in `PACKS.md` (vsss-20261112 block).

---

## 5. Backfill script — contract documented (execution blocked)

`scripts/backfill_snapshot_agent_space.py` (262 lines): `--db-url` **required** (no env default), dry-run by default, `--apply` opt-in, `--batch` (default 200); exit 0 ok / 1 internal / 2 bad args or unreachable DB / **3 = `snapshots.agent_space_key` column absent (not activated)**; idempotent CAS stamp (`SnapshotRepository.stamp_agent_space_key`, only when still NULL); resolvable = `target_instance_id` joins a live `instances` row → `compose_agent_space_key(agent_id, agent_tag)`; deleted-instance rows stay NULL (still reachable by explicit snapshot_id). Matrix (dry-run / exit=3 / `--apply` / second `--apply` no-op / deleted-row NULL / PG boot-DDL) remains **unexecuted**.

---

## 6. ensure.md mapping (scoped, Tier doctrine)

- **Tier choice**: `daemon/manager.py` is a listed Tier-2 core seam (mechanical trigger), so a whole-tree dual-side leg would be the doctrine's default. This mission's gate list instead prescribed a **scoped dual-side** (touched∪family integrated set + A/B legs over the declared blast radius = Tier-1 shape, with per-cell identical-command comparison). Recorded as an explicit deviation: **NOT Tier-2 whole-tree**; the 12 known reds + migration reds were compared base-vs-tip instead.
- **Critical**: changed packs PASS — *partially met* (3 reds in a touched file → requirement FAILS until §1 adjudication/fix). Concurrency pack — PASS (quarantine-aware). `dev.sh` grep — out of blast radius (dev.sh untouched).
- **Quarantine-aware**: row 134 applied; no snapshot-family rows quarantined; no new quarantines created (the 3 reds are NOT quarantinable — they are branch-caused).
- **No `-x`** anywhere; every invocation timeout-wrapped.

---

## 7. Premise corrections discovered during verification

1. **"4 SHA-seal tests"** — does not exist: the seal surface is **ONE** default-lane test iterating 35 seals; the file's 4 *deselected* tests are the unrelated `@pytest.mark.integration` astream lane.
2. **Developer 21-file baseline list** — not preserved anywhere (grep over `.agents/` + all commit bodies). Integrated set must be derived.
3. **OQ2 second shape** — actual test is v1-space row consumed by a **v2 caller** (not "v2-stamped row from v1 spawn").
4. **New pack registration** — was missing; tester added it (PACKS.md).
5. **`test_sqlite_guards.py`** — not matched by the `*migration*` glob; path confirmed `tests/unit/test_sqlite_guards.py`.

---

## 8. What would upgrade this to SHIP

1. Resolve §1 (either update the 3 stale tests to the fail-closed semantics, or relax the belt — owner/review decision) and show `test_snapshot_tools.py` green at TIP with the pin still green.
2. Run the remaining gates (mutation A–E proving the new pins bite; integrated regression over the derived set incl. the SHA-seal tripwire; backfill matrix incl. exit=3/idempotency/deleted-row NULL; migration comprehensive + critical-notes).
3. Re-run the A/B comparator to confirm the delta set is empty after the fix.

## 9. Evidence index (worker instances)

Scope freeze/anchor map `255350bf` · new pack `8b7966d1` (31P) · pintools+pin `e1768c01` (3F/70P + pin 1P) · sqlite guards `782b2044` (10P) · mission-record mig `6b90f351` (6P) · concurrency `4d21e368` (quarantine-aware) · A/B base `91a4d59d` · A/B tip `60b9e445` · blocked/superseded workers: `a1fd14f1` `e895582d` `436e72b8` `815fb0ea` `5318800b` `d4291792` `e43ba97d` `a8d16695` `da92ee04e` `e02a36a7` `4dc6b9ba` `7713748f`.

> Supersedes the interim checkpoint `2026-11-12-version-scoped-snapshot-spaces-verification-BLOCKED-interim.md` (kept as evidence history; safe to delete).

---

## RE-GATE ATTEMPT — 2026-11-12 (later) — NEW TIP 7d96affaf — **BLOCKED, fix UNVERIFIED**

**Status: no re-gate gate was completed. The NO-GO above stands as the latest VERIFIED verdict (it applies to 48e0ab662). The fix cycle 2 at 7d96affaf is NOT verified — do not treat the regression as cleared.**

- Re-gate plan accepted and materialized (11-node graph): fix-cycle-2 verification, mutants A–E, gate-3 remainder (integrated + SHA-seal tripwire), gate 2h (migration comprehensive + critical-notes), gate 7 (backfill), then final verdict.
- Wave 1 dispatched (2 workers, single-slot discipline):
  - `ced74204-df78-4874-8ddd-65aa6000dab3` — vsss-r1-fixverify (scope freeze 48e0ab662..7d96affaf + 4 spot gates 73/32/10/6P + diagnosis audit) → **status `error`** (insufficient_balance), no report.
  - `8f5c4843-52b8-48df-8530-b98f8e6ff458` — vsss-r2-mut-d (mutant D at 7d96affaf) → **status `error`** (insufficient_balance) at 14:33Z, no report.
- Both died on the wallet wall (`insufficient_balance` cascade; anomalies `15daba64` / `9d48080c` / reported cluster `d25422f6`). Retry ladder not re-run per the orchestrator budget caveat (record and stand down; do not burn turns).
- Consequently: **zero re-gate evidence** — no scope-freeze output, no spot-gate counts, no mutant verdicts, no integrated run, no migration 2h, no backfill matrix at 7d96affaf. The developer's claims (2 files +79/−3, daemon/ empty, 73P/32P expected) remain **unverified by the tester**.

### Resume checklist (unchanged plan; one worker at a time proved most reliable)
1. `git rev-parse HEAD` = 7d96affaf + `git diff --numstat 48e0ab662..7d96affaf` = exactly 2 files +79/−3 + `git diff 48e0ab662..7d96affaf -- daemon/` EMPTY.
2. Spot gates: `tests/unit/tools/test_snapshot_tools.py` expect 73P; `tests/unit/test_snapshot_agent_spaces.py` expect 32P; `tests/unit/test_sqlite_guards.py` 10P; `tests/unit/test_mission_record_migrations_sqlite.py` 6P.
3. Diagnosis audit: repaired tests pin intended outcomes (expired→'expired', hit→warm, stale→drift-note) + new `test_legacy_null_key_canned_hit_refused_cold` pins refusal via the RUNTIME-CONCATENATED warning (not a source-literal grep).
4. Mutants A–E at 7d96affaf (D must redden BOTH belt pins).
5. Gate 3 remainder: integrated touched-set + single SHA-seal tripwire test.
6. Gate 2h: migration comprehensive + critical-notes.
7. Gate 7: backfill matrix (dry-run 0 + counts; column-absent 3 clean; --apply stamps resolvable only; second apply no-op; deleted rows NULL; PG if lane up).
8. Then and only then: flip the verdict to SHIP (if all green) in this file.

---

# §10 — RE-GATE @ `7d96affaf` (final; supersedes the §"RE-GATE ATTEMPT — BLOCKED" above)

**All legs below were executed on the HIGH-TIER lane (`model_tier='high'` → `model='agentic'`), one worker at a time, after the default lane died twice. Receipts per gate.**

## A. Fix cycle 2 — VERIFIED

- **Scope freeze**: HEAD `7d96affaf4a1a65355dbba74f50cad2c47b30470`, branch `feature/version-scoped-snapshot-spaces`; `git log --oneline 6b644f770..HEAD` = exactly **5 commits**; `git diff --numstat 48e0ab662..7d96affaf` = **exactly 2 files, +79/−3** (`tests/unit/test_snapshot_agent_spaces.py` 42/0, `tests/unit/tools/test_snapshot_tools.py` 37/3); `git diff 48e0ab662..7d96affaf -- daemon/` = **0 bytes** (production byte-identical).
- **Spot gates** (scrubbed, `TZ=UTC`, `PYTHONDONTWRITEBYTECODE=1`, no `-x`): `test_snapshot_tools.py` **73P** exit 0 · `test_snapshot_agent_spaces.py` **32P** (31+1 new pin) exit 0 · `test_sqlite_guards.py` **10P** exit 0 · `test_mission_record_migrations_sqlite.py` **6P** exit 0.
- **Diagnosis audit (not merely re-green)**: `test_expired_top_match_cold` pins `reason: expired`; `test_internal_search_hit_warms` pins `[snapshot] started: warm — Warm-started from snapshot snap-hit` (+ query/limit contract); `test_stale_warm_carries_drift_note_in_citation` pins warm + `snapshot is STALE` + `verify digest assumptions`; new `TestAutomaticPathFailClosed::test_legacy_null_key_canned_hit_refused_cold` pins `started=="cold"`, `cold_reason=="no-hit"`, `consumed is None`, and the **runtime-concatenated** warning `top match snap-legacy space mismatch on re-read (row=None != spawn='developer') — refused (fail-closed)` (source literal is f-string-fragmented; verified absent from `.py` by grep). All three repaired automatic-path tests now seed `agent_space_key="worker"` (shape ii) — no NULL reliance; the NULL-refusal class is deliberately pinned by the new test.

## B. Gate 4 — MUTATION PROOF: **5/5 KILLED** (scratch trees @ `7d96affaf`; each leg green→red→green, pristine blob restore, scratch removed)

| Mutant | Edit (production/test) | Kill surface observed | Verdict |
|---|---|---|---|
| **A** predicate leg drop | `repository.py:596` `if False and agent_space_key is not None:` | 2 reds: `TestPredicateIsolation::test_a_space_row_invisible_to_b_space_query` (`['snap-b','snap-a'] != ['snap-a']`), `::test_null_rows_invisible_to_every_space_query` (NULL leak) | **KILLED** (exit 1) |
| **B** canonical-key collision | `registry.py` `compose_agent_space_key` → `return base_agent_id` | 10 reds: `TestComposeAgentSpaceKey` ×6 (tagged composite, v1≠v2, no-meta-version, unresolvable-None, whitespace, parse-roundtrip) + spawn threading ×1 + `TestBackfill` ×3 | **KILLED** (exit 1). Deviation: enumeration-stability pins are structurally insensitive (they use a separate per-directory scan helper, never the composer) — honest note |
| **C** unkeyed fallback | `snapshot_tools.py` — removed the early fail-closed key-missing cold arm | `test_missing_space_key_is_no_hit_cold_without_searching` red: `assert 'warm' == 'cold'` (NULL-key row actually warm-starts; belt passes vacuously on `None != None`); second independent gate `search.calls == []` also fails | **KILLED** (exit 1) |
| **D** consume-arm belt removal | `snapshot_tools.py:951` `elif False and (…)` | **BOTH** belt pins red: `test_wrong_space_search_receives_caller_space_key` + `test_legacy_null_key_canned_hit_refused_cold` (each `assert 'warm' == 'cold'`), zero collateral (30P/2F) | **KILLED** (exit 1) |
| **E** registration-pin mapping revert | test-side: per-dir `_parse_agent_dir_name` → bare `meta["id"]` | exact base-RED drift reproduced: `['developer','developer','tester']` vs `['developer','developer[v2]','tester']` | **KILLED** (exit 1) |

Every leg: baseline green (32P; E: pin solo 1P), mutant red, restore sha-verified, re-baseline green. No survivors, no harness faults.

## C. Gate 3 remainder — integrated + seal tripwire

- **Subset A** (7 snapshot-core files): **229 passed / 0 failed**, exit 0 (10.4s).
- **Subset B** (8 files incl. `tests/test_chart_image_delivery_e2e.py` + `tests/unit/decision/test_registry.py`): **192 passed / 1 failed / 4 skipped / 4 deselected**, exit 1 — the single failure is the **pre-existing** byte-fidelity red (below).
- **SHA-seal tripwire**: `TestPhaseDDoesNotModifySealedArtifacts::test_phase_d_does_not_modify_sealed_artifacts` **RAN and PASSED** in the default lane (no marker overrides) — all 35 `SEALED_SHA_BASELINES` entries verified, incl. the 4 mission-re-pinned seals (`daemon/constants.py`, `agents/{ari,developer,leader}/rule.md`). Deselected = 4 (the `@pytest.mark.integration` astream lane), as expected.
- **Byte-fidelity red → PRE-EXISTING (proven)**: identical `assert 38 == 37` payload at base `6b644f770` and tip (diff = timing only); caused by **ancestor** commit `2753ee78d` (manifest 37→38 with stale constant 37 + stale message text "29/17 contracts"); `plugins/opendesign/snapshot_with_drift_alarm/**` has **zero intersection** with the mission's 23-file diff; manifest internally consistent (38 on-disk ↔ 38 recorded; hash-match test passes both legs).

## D. Gate 2h — migration comprehensive + critical-notes

- `tests/test_migration_system_comprehensive.py` + `tests/unit/test_critical_notes_migrations.py`: **37 passed / 0 failed / 0 skipped / 0 deselected** (22+15 census-matched), exit 0.

## E. Gate 7 — **BLOCKED (receipt)**

- Worker `748bcd1d-8eef-443b-a7f1-9626ea2bc53e` — backfill matrix: **`error` (insufficient_balance)**; single revive dispatched → **failed identically**. Per protocol: stopped, no further retries.
- Consequence: the matrix (dry-run exit 0 + counts · column-absent exit 3 clean · `--apply` stamps resolvable only · second apply no-op · deleted rows stay NULL · optional PG boot-DDL) is **unexecuted**. Script contract is documented from source (§5) and the script is **dry-run by default** with a **required explicit `--db-url`** and opt-in `--apply` — merge is not gated on it; **run the matrix before first operational use**.

## F. Final gate board @ `7d96affaf`

| Gate | Status |
|---|---|
| 1 scope freeze | ✅ (5 commits; fix = 2 files +79/−3; `daemon/` 0 bytes) |
| 2a new pack | ✅ 32P |
| 2a′ OQ2 shapes | ✅ 2/2 warm-SUCCESS (NULL-key legacy; v1-row-by-v2-caller) |
| 2b registration pin | ✅ 1P |
| 2c sqlite guards | ✅ 10P |
| 2d mission-record migrations | ✅ 6P |
| 2e concurrency | ✅ quarantine-aware (5 reds = QUARANTINE row 134) |
| 2f `test_snapshot_tools.py` full | ✅ 73P (former 3 reds fixed; fixed to intended outcomes) |
| 2g `tests/migration/` lane | ✅ 2 adjudicated reds, identical base vs tip |
| 2h migration comprehensive + critical-notes | ✅ 37P |
| 3 integrated + seal tripwire | ✅ 229P + 192P/1 pre-existing/4 deselect; **seal tripwire PASS** |
| 4 mutation proof A–E | ✅ **5/5 KILLED** |
| 5 A/B legs | ✅ 12 pre-existing reds identical base↔tip; migration sets identical |
| 6 OQ2 explicit shapes | ✅ |
| 7 backfill matrix | ⛔ **BLOCKED** (wallet; see §E) |

**Residual reds at tip — all PRE-EXISTING, none branch-caused:** governor-recursion 10 · instance-tools 2 · migration shard A 2 · byte-fidelity 1 · concurrency quarantined 5 (row 134) = 20 known reds, each with base-identical or quarantine receipts.

## G. Recommendation

**SHIP the branch.** Before first operational use of `scripts/backfill_snapshot_agent_space.py`, run the gate-7 matrix (8-step checklist in the BLOCKED section above). Separately (test-debt, pre-existing, non-blocking): the byte-fidelity constant at `tests/unit/plugin_subsystem/test_snapshot_class_byte_fidelity.py:184` should be bumped 37→38 and its message text corrected (29/17 → 38/18) — owned by the plugin lane, not this mission.
