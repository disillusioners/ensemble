# Phase-1 Tier-1 Blast-Radius Gate — project skill source loader

- **Date:** 2026-10-31
- **Commission:** Phase-1 Tier-1 blast-radius gate for the `project-skill-loader` commission
- **Worktree:** `/home/nea/ensemble-src-wt-project-skill-loader` (`feature/project-skill-loader`)
- **HEAD:** `bb8b81f375ff78f2a9ec9f6dbc4b78d47683c7ec` · **BASE:** `b56444244fbf93814fbac3c71e2cded010f7d225`
- **Change set:** 5 commits, 32 files, **+7991 / −0** (purely additive; the non-additive check `awk '$2!=0'` returned empty)
- **Recipe:** `.agents/tester/skills/blast-radius-leg/` v1.0.0 (loaded deterministically, per the doctrine's mandatory recipe-load rule) + `.agents/tester/skills/failed-digest-equality-gate/`
- **Tier contract:** `.agents/tester/rules/ensure.md` § Gate Tiers
- **Gate workers:** `psl-recon` f9d583a2 (read-only recon) · `psl-gate-legs` bba587eb (legs) · `psl-gate-mutation` b1e8c8ad (mutation)

---

## 1. Tier decision (taken BEFORE the run)

**Tier 1 — default for feature lanes.** Escalation to Tier 2 is mechanical on the core-seam path-prefix list; none hit.

The entire scoping premise is that the lane does **not** touch the integration seams. That premise was independently re-derived, not trusted:

| Verification | Result |
|---|---|
| `git diff --stat b56444244..bb8b81f37 -- daemon/services/skill_search_service.py daemon/services/skill_injection_service.py daemon/manager.py daemon/config.py daemon/{services/,}readiness.py` | **empty** |
| `git log --oneline <range> -- <those paths>` | **empty** |
| `git diff --name-status <range> --diff-filter=RMD` | **empty** (no renames/deletes) |
| `git cat-file -e` at both revs | all present |
| Core-seam list (`daemon/graph.py`, `manager.py`, `api.py`, `services/instance_messaging.py`, `services/task_processor.py`, `services/job_queue_service.py`) | **0 hits** |

> **Commission note:** the task named `daemon/readiness.py`; that path does not exist. The real file is `daemon/services/readiness.py` — present at both revs, zero diff. Verified both spellings; no premise impact.

**Consequence:** the new package (`daemon/services/project_skill_source/`, `daemon/repositories/project_skill_file/`) is additive and **not wired into any existing seam** — i.e. the daemon-side search/injection fusion is *not* in this lane. That makes this a data-layer + repository + migration lane. Regression risk to the existing corpus is therefore structurally low, but it is *proven*, not assumed, by the D2 legs below.

---

## 2. Declared set (tester-owned)

The recipe's `(a)` derivation + `(b)` slicing proof assume one set that exists on **both** legs. For an **additive** lane that assumption breaks: the lane's own pins do not exist at BASE, so a literal reading either produces base collection errors or invites counting absent-at-base files as "net-new". Declaration is therefore split into two classes, with the asymmetry stated rather than hidden:

### D1 — lane-owned pins (exist ONLY at HEAD → HEAD-only MUST-PASS)
- `tests/unit/test_project_skill_source_model.py`
- `tests/unit/test_project_skill_source_manifest.py`
- `tests/unit/test_project_skill_source_resolver.py`
- `tests/unit/test_project_skill_source_embedding.py`
- `tests/unit/test_project_skill_source_index.py`
- `tests/unit/test_project_skill_source_migrations.py`
- `tests/postgres/test_project_skill_source_pg.py` (real PG, `-m postgres`, serial)

**Status rule:** must be 0 failed at HEAD. A base-side "red" is *impossible by construction* and is neither net-new nor fixed.

### D2 — dual-side comparable set (exists at BOTH revs), enumerated by rule, cap 12
Priority (i) > (ii) > (iii):
1. migration discovery/registry neighbours — `discover_migrations|DuplicateMigrationVersionError|RepoVersionsRegistry|schema_migrations|migrations/versions`
2. skill-surface neighbours — `tests/services/*skill*.py`, `tests/repositories/*skill*.py`, `tests/unit/*skill*.py`
3. metadata / table-census / column-ensure neighbours — `metadata.tables|SQLModel.metadata|_ensure_postgres_columns|create_all`

Any enumerated file added by the lane moves to D1. Overflow dropped by priority and recorded.

*Enumeration, per-file counts and dropped files: see §3.*

---

## 3. Slicing proof (executed == declared)

Two detached worktrees at the exact revs (`/home/nea/ensemble-src-wt-gate-psl-base` @ `b56444244`, `…-head` @ `bb8b81f37`), per-tree `.venv` via `uv sync`, and the mandatory assertion discharged: `daemon.__file__` printed inside each worktree (Python 3.14.7).

**D1 (lane-owned, HEAD only).** 7 files, **123 unit nodes** collected at HEAD — model 12, manifest 48, resolver 18, embedding 7, index 29, migrations 9 — plus **12 PG nodes** under `-m postgres`. At BASE these files do not exist; absence confirmed and recorded as *expected by construction*, not a failure.

**D2 (dual-side, 12 files, all present at both revs).** Per-file counts **byte-identical across trees**:

| File | BASE | HEAD |
|---|---|---|
| tests/postgres/test_report_deferred_migration_pg.py | 0 (deselected w/o `-m`) | 0 |
| tests/unit/test_sqlite_guards.py | 10 | 10 |
| tests/migration/test_migration_legacy_ledger_shadow.py | 2 | 2 |
| tests/migration/test_migration_dup_prefix_guard.py | 6 | 6 |
| tests/repositories/test_skill_repository.py | 103 | 103 |
| tests/services/test_skill_embedding_service.py | 50 | 50 |
| tests/services/test_skill_store_service.py | 30 | 30 |
| tests/services/test_skill_search_service.py | 43 | 43 |
| tests/unit/test_ensure_deferred_schema_pin.py | 7 | 7 |
| tests/migration/test_idle_gate_migration_parity.py | 1 | 1 |
| tests/migration/test_stored_watches_index_parity.py | 6 | 6 |
| tests/migration/test_board_index_parity.py | 15 | 15 |
| **D2 total** | **273** | **273** |

Summary lines matched exactly: `273 passed / 36 deselected` on both trees; node lists byte-identical (273/309 both).

**Dropped by the cap (recorded, not silent):** ~36 broader migration-system files → kept the 4 that directly exercise `discover_migrations`/`DuplicateMigrationVersionError`; 18 skill-surface files → kept `skill_repository`, `skill_embedding_service`, `skill_store_service`, `skill_search_service` (the surfaces a loader would integrate with) and dropped 18 (incl. `skill_injection_service`, `skill_evolution_service`, `skill_trigger_engine`, `skill_bank_repository`); hundreds of `metadata.tables`/`create_all`/`_ensure_postgres_columns` matches → kept the 4 schema-parity neighbours.

> **Declaration note (asymmetry, disclosed):** the HEAD leg runs D2 + D1 unit (396 nodes); the BASE leg runs D2 only (273). This is *not* a broken "identical commands" invariant — it is the D1/D2 split made explicit, because a file that does not exist at BASE cannot be collected there. Any D1 red would surface on the HEAD side regardless; D1 was green (§5).

---

## 4. Projection vs the 30-minute hard cap

Probe: `test_project_skill_source_model.py` (12 nodes) → **4.90 s** wall. Rate ≈ 2.45 nodes/s wall (33 nodes/s pytest-internal). Projected unit leg 16–165 s depending on which rate holds; + PG leg ≈ 60 s + probes.

**Verdict: well under 30 min → proceed.** Actual (§10) confirmed the projection by an order of magnitude — the §(g) prior ("a scoped leg is minutes, not hours") was accurate.

---

## 5. Legs (BASE first, then HEAD, sequential, identical command shape)

Command shape (both legs):
`timeout 600 env -u POSTGRES_USER -u POSTGRES_DB -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_PASSWORD -u DATABASE_URL -u PGPASSWORD .venv/bin/python -m pytest <explicit file list> -q --tb=short -p no:randomly -n 8`

| Leg | Nodes | Result | rc | Wall |
|---|---|---|---|---|
| **BASE** | 273 | **273 passed**, 0 failed, 9 warnings | 0 | 26 s |
| **HEAD** | 396 | **396 passed**, 0 failed, 9 warnings | 0 | 40 s |

**FAILED lines: empty on both legs.** All 6 lane-owned unit packs green at HEAD (model 12, manifest 48, resolver 18, embedding 7, index 29, migrations 9).

---

## 6. Real-PG evidence (mandatory — the index is DB-backed)

One scratch role+DB **per leg**, each created and dropped inside that leg (never shared — the DF-4 fabricated-red class).

| Leg | DB / role | File | Result |
|---|---|---|---|
| BASE | `ensemble_test_stage2` / `psl_gate_base` | `tests/postgres/test_report_deferred_migration_pg.py` | rc=1 — **5 failed, 5 passed, 2 xfailed, 24 errors** (54.23 s) |
| HEAD (lane's own) | `ensemble_test_stage3` / `psl_gate_head` | `tests/postgres/test_project_skill_source_pg.py` | **12 passed, 0 failed** (12.74 s) |
| HEAD (D2 PG file) | `ensemble_test_stage3` / `psl_gate_head` | `tests/postgres/test_report_deferred_migration_pg.py` | rc=1 — **5 failed, 5 passed, 2 xfailed, 24 errors** (54.33 s) |

**Pre-existing-red root cause (not lane-related):** the 24 errors are a base-tree fixture/proxy mismatch — `_MinimalManagerProxy` lacks `_migrate_overloaded_image_refs_rows` (`daemon/manager.py:9003`). It reproduces **identically at both revs**.

**Teardown:** all 3 scratch DBs (`ensemble_test_stage2/3/only`) + 3 roles (`psl_gate_base/head/extra`) dropped; absence verified via `\du` and `\l`.

> **Fence note (conftest-enforced):** `tests/postgres/conftest.py` refuses non-`ensemble_test*` database names, so the suggested `psl_gate_<leg>_db` names were rejected by design and allowlisted names used instead. Good fence — it makes "accidentally pointed at prod" structurally impossible rather than merely discouraged.

---

## 7. FAILED-list diff + pre-existing-red adjudication

**Unit corpus (D2, dual-side).** Canonical digest form — sorted node ids with the `FAILED `/`ERROR ` prefixes stripped:

| | sha256 | meaning |
|---|---|---|
| BASE `base.nodeids.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | empty (sha256 of empty input) |
| HEAD `head.nodeids.txt` | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` | empty |

`cmp` = **identical** · `comm -23` (base-only → fixed) = **empty** · `comm -13` (head-only → **NET-NEW**) = **empty**.
**Diff set = ∅**, therefore the 3× flaky probes are **N/A by construction** (no diff nodes to classify). Disclosed: the mutation lane was running concurrently on the host; with zero diff nodes there is nothing whose determinism could be load-smeared.

**Pre-existing PG red block — adjudicated at node-id granularity (not on counts).** `tests/postgres/test_report_deferred_migration_pg.py` was carried in D2, so its reds had to be attributed rather than waved through:

| | BASE | HEAD |
|---|---|---|
| Result | rc=1 — 5 failed, 5 passed, 2 xfailed, **24 errors** | rc=1 — 5 failed, 5 passed, 2 xfailed, **24 errors** |
| Node ids (FAILED+ERROR, sorted) | 29 | 29 |
| sha256 | `2fad8e8a775b66d7a35b4162e68f6627946d359e7dfe948fb8323b0658524c3d` | `2fad8e8a775b66d7a35b4162e68f6627946d359e7dfe948fb8323b0658524c3d` |

`cmp_rc=0`; `comm -23` empty; `comm -13` **empty** → **zero net-new**, base reds byte-identical at HEAD.

Attribution: `TestC4NullConsumerAudit` (13), `TestEnsurePostgresColumnsPath` (7), `TestSQLiteCompanionParity` (1), `TestW3DedupPreCheck` (5), `TestW8RollbackOrder` (4) — full list `/tmp/cmp-base.nodeids.txt`. Root cause of the 24 errors: `_MinimalManagerProxy` lacks `_migrate_overloaded_image_refs_rows` (`daemon/manager.py:9003`), a base-tree fixture/proxy mismatch. **Pre-existing ⇒ not a lane regression**, and per ensure.md § Quarantine-aware it should be quarantined rather than left to permanently red the gate (finding §9).

This closes the one methodological gap in the first pass: the legs lane had adjudicated these on matching *counts*; counts are not proof, node ids are.

---

## 8. Mutation verification of the pins relied on (independent)

A pin that cannot go RED is worthless. Every pin this gate leans on was mutation-verified in a **separate scratch worktree** (`/home/nea/ensemble-src-wt-mut-psl`) with its own throwaway PG role/DB. Gate worktree untouched.

**Baseline (green before mutation — otherwise nothing below is interpretable):** unit `test_project_skill_source_migrations.py` **9 passed** (2.20s) · PG `test_project_skill_source_pg.py` **12 passed** (11.22s), real PG16.

| # | Mutation (file:line, before → after) | Expected pin | Observed |
|---|---|---|---|
| M1 | `20261031_120001_create_project_skill_embeddings.sql:58` `skill_id TEXT NOT NULL,` → `skill_id TEXT NOT NULL REFERENCES project_skill_files(id),` | `TestFreshSqliteApply::test_no_foreign_key_clause_anywhere` + `::test_no_fk_constraint_materialized_on_sqlite` | **RED** both — `assert 'REFERENCES' not in ...`; `assert [{'constrained_columns': ['id'], ...}] == []` |
| M2 | `daemon/repositories/project_skill_file/repository.py` — `ProjectSkillEmbedding` swapped to `daemon.repositories.skill.models.SkillEmbedding` (dedicated path degenerates onto the FK-constrained legacy table) | `TestA1FkWallPin::test_dedicated_table_path_green` | **RED** — `psycopg.errors.ForeignKeyViolation: ... violates foreign key constraint "skill_embeddings_skill_id_fkey"`. `test_pf_insert_into_legacy_skill_embeddings_rejected` correctly stayed GREEN (it targets the legacy table directly, so it is *not* collateral-sensitive — a good sign it is not a duplicate pin). |
| M3 | `daemon/services/project_skill_source/service.py:1160` `description=meta.description,` → `description=_read_body_bytes(root_path, meta).decode(...)` | `TestP1SentinelPG::test_body_sentinel_never_persisted_on_real_pg` | **RED** — `P1 violated on PG: {violations}` (sentinel windows found in `project_skill_files.description`) |
| M4 | copy `20261031_120002_create_project_skill_usage_records.sql` → `20261031_120000_create_dup_probe.sql` (duplicate prefix) | `TestRepoVersionsRegistry::test_discovery_has_no_duplicate_prefix_and_carries_lane_files` | **RED** — `DuplicateMigrationVersionError: ... version 20261031_120000 is claimed by 2 files` |

Every mutation reverted (`git checkout -- <path>` / `rm` of the dup probe) with a **GREEN-after-revert** proof (2/3/1/1 passed respectively). No pin went red for the wrong reason; no vacuous pin found.

**One collateral, disclosed (not a defect):** M2 also reddens `test_gc_sweep_on_real_pg`, because that test's `insert_many` now hits the FK wall. That is inherent to the mutation, not pin over-reach.

**Teardown:** `psl_mut_db` dropped · role `psl_mut` dropped (absent in `\du`/`\l`) · scratch worktree removed (`worktree list | grep -c mut-psl` = 0). Gate worktree porcelain sha `dc9d2fdd4c6a5f35131cc3ec5c01e245cd440fdf15432b43c146ec269eb2b695` unchanged before/after. Main checkout sha `2c85a625…44f02` unchanged.

---

## 9. Findings by severity

### 🔴 Critical (gate-process, not a lane defect)
- **The ambient environment is prod-poisoned and env observation is unreliable per shell.** Recon reported *"No `POSTGRES_*` in ambient env"*; two independent later checks found the opposite: `POSTGRES_DB=ensemble_prod`, `POSTGRES_HOST=10.44.0.2`, `POSTGRES_USER=ensemble`, `PORT=5432`, `ENSEMBLE_SELF_ENV=live`. **Recon was wrong.** No prod contact occurred — but only because the legs and digest lanes re-ran the per-variable check *before every invocation* instead of inheriting recon's claim.
  **Why it matters:** the house rule (`env -u POSTGRES_*` is a silent no-op) is already known, but this adds a second, sharper failure mode — *a sibling worker's environment assertion is not evidence about your environment*. A lane that had trusted recon and invoked `pytest` unscrubbed would have pointed the PG suite at `10.44.0.2/ensemble_prod`.
  **Recommendation:** (a) never propagate an env assertion between workers — each invocation re-checks; (b) the recipe's Fences section should state this explicitly as a gate rule, not just "scrub per-variable"; (c) worth an incident note given the prod host is remote (unlike the local `ensemble_prod` shape the existing critical note describes).

### 🟠 Important
- **PACKS.md registration gap.** None of the 8 new test files is registered in `.agents/tester/PACKS.md` (the single grep hit for "blast-radius" is the unrelated `lcafc_delta_neighbors_unit`). Consequence: these pins are not reachable from any registered pack, so no ensure.md Core requirement resolves to them and no routine pack run exercises them — they only run when someone names the files. The pins are excellent (§8); they are just not wired into the harness. Suggested fix: register the unit set as one `project_skill_source_unit` pack and the PG file as a `project_skill_source_pg` pack. *(Not fixed in this gate: the lane is unmerged, so pre-registering its files in PACKS.md risks a merge collision. Routed to the lane.)*
- **Purely-additive ⇒ not wired.** Confirmed zero diffs in `skill_search_service.py` / `skill_injection_service.py` means the lane's own premise ("file-backed project skills into the skill system: **search fusion**, project-scope binding, file-read load path") is **not yet delivered by this lane**. This is a scope-of-claim finding, not a regression: if the commission's acceptance criteria include search fusion or the load path actually serving `skill_search`/injection, that work is absent. Needs adjudication against the commission's declared phase boundary (data layer only?). *A PASS here must not be read as "the loader works end-to-end."*
- **Pre-existing red block not quarantined.** `tests/postgres/test_report_deferred_migration_pg.py` carries 5 failures + 24 errors at BASE (fixture/proxy mismatch at `daemon/manager.py:9003`). Per ensure.md § Quarantine-aware, pre-existing failures must be quarantined rather than left to permanently red the gate. Suggested: add to `.agents/tester/QUARANTINE.md` with the 29 node ids and the root cause, so future PG legs don't re-litigate them.
- **PACKS.md registration gap.** None of the 8 new test files is registered in `.agents/tester/PACKS.md` (the single grep hit for "blast-radius" is the unrelated `lcafc_delta_neighbors_unit`). Consequence: these pins are not reachable from any registered pack, so no ensure.md Core requirement resolves to them and no routine pack run exercises them — they only run when someone names the files. The pins are excellent (§8); they are just not wired into the harness. Suggested fix: register the unit set as one `project_skill_source_unit` pack and the PG file as a `project_skill_source_pg` pack.
- **Purely-additive ⇒ not wired.** Confirmed zero diffs in `skill_search_service.py` / `skill_injection_service.py` means the lane's own premise ("file-backed project skills into the skill system: **search fusion**, project-scope binding, file-read load path") is **not yet delivered by this lane**. This is a scope-of-claim finding, not a regression: if the commission's acceptance criteria include search fusion or the load path actually serving `skill_search`/injection, that work is absent. Needs adjudication against the commission's declared phase boundary (data layer only?).

### 🟡 Minor
- Task-supplied path `daemon/readiness.py` does not exist (real: `daemon/services/readiness.py`).
- Gate worktree is not pristine: `.agents/shared/planning/designer-agent/install-audit.jsonl` (+9) is foreign WIP, unrelated to the lane. Evidence commit must be path-scoped.
- The gate worktree carries a `.venv` (house convention normally expects none at creation). Harmless here, but it means a shared-venv trap is *possible* for a future lane that trusts cwd inheritance instead of asserting `daemon.__file__`.

---

## 10. Wall-time accounting vs the 30-minute hard cap

| Phase | Wall | Notes |
|---|---|---|
| Recon (read-only) | ~2 min | `psl-recon` f9d583a2 |
| **Gate legs** (setup ≈3 min, slicing ≈30 s, probe 4.9 s, BASE 26 s, HEAD 40 s, PG 59/18/59 s, cleanup ~10 s) | **≈6 min** | `psl-gate-legs` bba587eb — measured by the lane |
| Mutation lane (setup + baseline + M1–M4 + teardown) | ≈12 min | `psl-gate-mutation` b1e8c8ad — ran **in parallel** with the legs lane |
| PG node-id digest closeout | ≈6 min 18 s | `psl-pgdigest` ef656131 (20:19:24 → 20:25:42 UTC) |
| **TOTAL gate window (dispatch → verdict-ready)** | **≈28 min** | inside the 30-min cap, but only by ~2 min |

**Reading of the budget:** the *gate run proper* (the two legs) was **≈6 min** — an order of magnitude inside the cap, exactly as the §(g) prior predicted. The 28-min total is the price of doing this doctrine's **first** outing properly: recon + a parallel mutation-verification lane + one discretionary closeout round (the PG node-id digest) that I chose to spend budget on rather than disclose as a limitation.

**Parallelism was load-bearing.** Legs and mutation overlapped, so the window is ≈ recon + max(legs, mutation) + closeout, **not** the sum (~26 min of serial work). Had I serialised, the same gate would have been ~26 min of pure work and one slow phase would have breached the cap.

**Disclosed coupling:** the mutation lane was executing on the same host during the legs. With zero diff nodes there was nothing for that load to smear (§7), but a gate whose legs *did* produce diff nodes would have needed the mutation lane serialised behind them.

---

## 11. Verdict

# ✅ PASS-WITH-NOTES

**Zero net-new failures. Zero net-new errors. Zero diff nodes, on both the unit corpus and the real-PG corpus, proven at node-id granularity.**

Five evidence lines that carry the verdict:

1. **Premise verified, not trusted** — `git diff`/`git log`/`--diff-filter=RMD` all empty over `skill_search_service.py`, `skill_injection_service.py`, `manager.py`, `config.py`, `services/readiness.py`; **0** core-seam hits → Tier 1 stands, no mechanical Tier-2 escalation.
2. **Dual-side D2 corpus: 273/273 passed at BASE, 273/273 at HEAD**, node lists byte-identical (273/309 both), FAILED lists **empty** on both legs; digest `e3b0c442…` (empty) on both sides, `cmp` identical, both `comm` directions empty.
3. **Real PG (mandatory): 12/12 lane PG tests green at HEAD** on a throwaway `ensemble_test_stage3` DB; the one PG file with pre-existing reds reproduced **29 identical FAILED+ERROR node ids** at both revs (`2fad8e8a…`, `cmp_rc=0`) → **zero net-new**, base reds byte-identical.
4. **Every pin the gate leans on is mutation-meaningful (4/4)** — M1 FK-wall (SQLite) RED, M2 legacy-table degeneration (PG, `ForeignKeyViolation` on `skill_embeddings_skill_id_fkey`) RED, M3 P1 sentinel persisted RED, M4 duplicate migration prefix RED, each with a GREEN-after-revert proof. No vacuous pin.
5. **Fences held by re-verification, not by assumption** — ambient env was in fact prod-pointing (`ensemble_prod` @ `10.44.0.2`) despite recon claiming otherwise; every invocation scrubbed per-variable, main-checkout porcelain `2c85a625…44f02` byte-identical before/after, all scratch DBs/roles/worktrees dropped with absence proof.

**Why not a clean PASS** — four notes, one of them critical:
- 🔴 the ambient env is prod-poisoned **and a sibling worker's env assertion was false** (fence held only via per-invocation re-checks);
- 🟠 the 8 new test files are unregistered in PACKS.md, so nothing routine exercises these pins;
- 🟠 the lane is purely additive and **not wired** — "PASS" here means *no regressions*, **not** *the loader works end-to-end*; search fusion / project-scope binding / the load path are absent from this diff;
- 🟠 the pre-existing PG red block (5F/24E, `_MinimalManagerProxy` at `daemon/manager.py:9003`) should be quarantined per ensure.md rather than left reddening future PG legs.

**Gate scope boundary:** this verdict covers regression safety of the lane's declared blast radius only. It is **not** an acceptance/functionality sign-off for the project-skill-loader feature, and it does not authorise merge or promote — those remain the leader's decision.

---

## 12. Recipe feedback — first real outing of `blast-radius-leg` v1.0.0

The recipe is **usable and materially better than the G2 alternative**, but it has one structural gap that forced a tester-side decision rather than a mechanical one:

- **GAP-1 (structural, needs a v1.0.1 fold): derivation assumes a non-additive diff.** `(a)` derives one candidate set and `(b)` demands `executed == declared` on both legs. For an additive lane the lane's own pins exist only at HEAD, so the literal recipe is un-runnable: base collection errors on absent files, or the absent files get counted as "net-new". The recipe needs an explicit **two-class declaration** rule (lane-owned pins = HEAD-only MUST-PASS; pre-existing blast radius = dual-side comparable), which is what this gate did. Without it, every purely-additive lane (a common shape) hits the same wall.
- **GAP-2 (minor): `(f)` pre-exclusion registry is silent on new-file collection errors at BASE.** A freshly added test file produces a collection *error* at BASE, not a red. The digest procedure's `grep '^FAILED '` silently omits ERROR lines, so an additive lane can look "digest-clean" while base was erroring. Recommend the digest step explicitly capture `ERROR` lines separately and require them to be explained.
- **FRICTION-1:** the recipe's budget table (§ c) sums to 10–15 + 2×(5–10) + 5 = **25–40 min**, which overruns its own 30-min hard cap for a two-leg run. Either the table needs a parallel-legs note or the leg budget needs a stated target (e.g. "scoped legs are minutes, not 10").
- **STRENGTH:** § (e) fail-loud projection guard and the DF-4 never-share-scratch-PG rule both earned their keep — one scratch PG per leg is cheap and removes an entire fabricated-red class.
- **STRENGTH:** § (g)'s G2 counterfactual gave an immediately usable prior ("scoped leg is minutes, not hours"), which is what justified running the mutation lane in parallel instead of serialising a 30-min budget away.

---

## 13. Fences honoured

- `env -u POSTGRES_*` never used as a glob; every scrub is per-variable and explicit.
- `ensemble_prod` / `:9797` / `ensemble-main` / `current` / `releases/` untouched. No daemon boots.
- Main checkout `git status --porcelain=v1 | sha256sum` **before and after** = `2c85a625efe03ffdad72e6457ea935cc52dd34fd2f32ba603a3ac58c99544f02` (byte-identical).
- One scratch PG role/DB per lane, each dropped with absence proof.
- No source, test, migration or PACKS.md file modified anywhere.
