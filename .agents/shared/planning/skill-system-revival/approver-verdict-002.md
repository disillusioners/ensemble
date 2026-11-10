# Approver Verdict — Skill-System Revival Phase 1 — iteration 002

**VERDICT: APPROVE-WITH-FINDINGS** — the decision is **APPROVED**. 0 blocking issues; 11 non-blocking findings (all NOTE severity) enumerated below.

- Plan: `.agents/shared/planning/skill-system-revival/phase1-plan.md` — 1,141 lines, sha256 `04bcbb0408834a059e7a7674b78e001c236d4a0b497969446b50f7fde7b4da1f`
- Worktree HEAD: `ae5bbafed` (branch `feature/skill-system-revival-phase1`)
- Iteration: **002** (001 REJECTED 2026-11-09 — all five blockers verified CLOSED, see §Blocker closure)
- Method: two cold-context workers, `plan-approval` skill each, partitioned by verification axis (doc-consistency; executable-claims), fan-in via `todo_graph`. The dispatcher introduced no findings and upgraded none.

---

## Reasoning

Both independent axes returned **APPROVED with zero blocking issues**:

1. **Doc axis** — the plan covers workstreams A–G end-to-end with explicit acceptance criteria, verification methods, rollback/recovery, sequencing (hard DAG edges G→E1, E4→F), and an explicit non-goal list (N1–N7). §19.1's 29 contract cross-checks hold character-for-character between the plan body, the ledger, and `decisions.md`; council findings F-1…F-27 map 1-to-1 to body sections.
    **Superseded figure (approver correction, 2026-11-09):** the §19.1 cross-check table was 29 rows at approval time; it is now 38 rows after implementation added the E4 brake (C-31), R5 ratification (C-32), the resolved E4.1 site (C-23) and three B-lane amendments. The verdict is unchanged.
2. **Execution axis** — every executable claim that could be run or read was run or read at worktree HEAD: SQL executed read-only against `ensemble_dev` (C-5 CTE → `(0,0)` with 15 skills; C1/C2 parse and return the documented rows); the three base-red `tests/manager/test_skill_service_init.py` tests reproduced live with the documented `MigrationError`; SQLite's rejection of `ALTER TABLE … DROP CONSTRAINT IF EXISTS` reproduced; the P1 roster recomputed by execution (46 agents → 15 true / 10 false / 21 absent → 31 cannot-capture); ~90 `file:line` anchors opened; pytest selectors collect-resolved; staging/promote script claims checked against `scripts/upgrade/*` source. Zero blocking defects.

Aggregation: the two reports are disjoint (no duplicates, no conflicts). All 11 findings are non-blocking as raised; per the aggregation judgment band the dispatcher neither upgraded a note nor introduced a finding. **Verdict: APPROVED.**

All caller ground rules were independently checked by both workers and hold: no skill capture/evolution env switch is flipped by the plan's own actions (names are compiled-in literals; N1's env-surface scoping is correct); no promotion is executed (§14.3 documents, never runs); the job-queue brake is not engaged (C is SELECT/GET only; the brake is Phase 2); no commits to the canonical checkout (every command `cd`s to the worktree; §14.3 bars staging from the canonical/feature tree); no live-install or production data mutation (no Phase-1 DB writes; reconciliation ships as release/operator scripts with dry-run-first + reversal receipts). `ENSEMBLE_ROLLBACK_SAFE=1` legitimacy re-verified: `git diff --name-only v0.29.7..HEAD -- daemon/migrations/versions/` is EMPTY, matching `stage.sh:325-351`'s own documented override rule.

---

## Numbered findings (all non-blocking — severity NOTE; remediate before or during implementation)

1. **[NOTE — operationally important] `v0.29.8` is TAKEN; the plan's concrete staging/promote version strings are stale.** §14.1 ("next free slot … v0.29.8"), §14.2 step 3 (`--version v0.29.8`), §14.3 (`VERSION=v0.29.8`), §16.2 T-3. Evidence: `git log -1 v0.29.8` → 2026-11-09 13:14:44Z "chore(release): bump version to 0.29.8" (c113531f2…), NOT an ancestor of `ae5bbafed`. Not blocking: the plan carries its own guard (T-3 = the implementer re-derives the slot at staging time) and the failure mode is loud (`stage.sh:146-150` exact-tag guard → exit 78). **Remediation:** update §14.1/§14.2/§14.3 to the re-derived slot (likely `v0.29.9`) or footnote that v0.29.8 was minted by a foreign lane at 2026-11-09T13:14:44Z; keep T-3 authoritative.
2. **[NOTE] B2/§13.3 claim "the pack already runs `tests/unit/test_skill_evolution_lane_gate.py`" is false.** Plan refs: §9 B2 pin block (line 299), §13.3 table row (`PACKS.md:867 — it already runs …`). Evidence: the pack script invokes pytest only on `$PIN_FILE` + `$SIBLING_FILE`; `grep test_skill_evolution_lane_gate` in pack scripts → 0 hits; PACKS.md:867 sits inside a `--collect-only` census listing, not an invocation. **Remediation:** correct the claim and add an explicit step: extend the pack script's pytest invocation to include the file + update the `EXPECTED` test count in both the script and the `PACKS.md` row.
3. **[NOTE] The "`76e3e1a7a` … 0 hits" string-level claim is refuted.** §3 P2, §9 E4 corrected-target-set bullet, §16.3. Evidence: `grep -rn "76e3e1a7a" .` → ≥5 hits in TRACKED planning docs (`project-skill-loader/requirements.md:49,:103,:345`, `phase2-plan.md:18`, `plan-overview.md:124`). The load-bearing consequence stands (no such skill ROW exists; E4 must be rule-based). **Remediation:** rephrase to "no such skill row exists; the token appears only in project-skill-loader planning history".
4. **[NOTE] `_build_auto_load_block` is attributed to the wrong file.** §10 cites `skill_injection_service.py`; the def is `daemon/services/context_messages.py:2024` (0 hits in the cited file). **Remediation:** fix the file attribution (symbol and gating claim unchanged).
5. **[NOTE] A4's 32-vs-27 arithmetic is never reconciled.** §1 A4 exit criterion, §5 A4 consequence (b)/acceptance 4, §16.1 C-5/C-6, §17 #24. The plan's own numbers give 27 families of which 2 are content-identical → 25 content-diverging within-identity keys, against a measured baseline of 32; the residual ~7 keys are never named, yet acceptance 4 requires the gauge to "report 32 … names the 27-pair cause". **Remediation:** add one sentence reconciling 32 with 27/25 (e.g. naming the residual non-A/B divergent keys) before the gauge is wired.
6. **[NOTE — minor] B3 wiring shape: the shared resolver also serves the write path.** §6 B3 + M-B3a ("remove the scope check from `_resolve_within_workdir`") + §18 row 16. Evidence: `_resolve_within_workdir` (`filesystem.py:197-225`) is called at 5 sites — reads `:596`/`:670` **and writes `:1114`/`:1518`**. Harmless today (default-absent scope; keeper holds no write tools) but the "byte-identical write path" acceptance should not rest on that. **Remediation:** wire the scope check at the two READ call sites (or give it an explicit read-only mode), not inside the shared resolver.
7. **[NOTE — minor] Input-list filename drift.** "Inputs read" (line 17) cites `test/packs/skill_evolution_pg_unit_test.sh`; the file is `test/packs/skill_evolution_pg_test.sh`. The other six cited pack scripts exist verbatim. **Remediation:** fix the name.
8. **[NOTE — minor] Parenthetical/index drift.** `deleted_at` is not a `job_queue_items` index (dev `pg_indexes`: instance, project, queue, admission_state, idempotency_key, terminal_reason ×2, pkey) — the load-bearing claim (no `created_at` index ⇒ unindexed window scan) is TRUE and correctly bounded; `stage.sh:215` (PyInstaller run is at :218); `test_metrics_query_switch_invariance.py:51` (fake's `gated` attr at :50); QUARANTINE.md row 90's `:1875` is 2026-08-30 vintage text — current def of `test_ab_resolution_threshold_met` is `:1882`. **Remediation:** adjust the citations/annotations.
9. **[NOTE] §18 row 8 disagrees with §5 line 170 on the A3 sibling predicate.** §5 introduces `has_genuinely_evolved_for_instance(instance_id)` with the three-conjunct outcome-fields predicate (LEADER RULING Q1); §18 row 8 labels the method `has_feedback_for_instance` and describes it as "the `feedback_applied IS NOT NULL` predicate". Both the NAME and the predicate DESCRIPTION should match the body. **Remediation:** standardize §18 row 8 to `has_genuinely_evolved_for_instance` + the three-conjunct description (character-for-character match) when the developer opens the PR.
10. **[NOTE] Staging touches the live `INSTALL_DIR/.env`.** §14.4 checklist item 8 / §14.2 step 3: `stage.sh:718-760` updates `ENSEMBLE_SELF_ENV` in `INSTALL_DIR/.env`. This is within the plan's disclosure, but tester evidence should record the modification and any mode/permission effects. **Remediation:** keep note 8 in §14.4 and have the tester record the `.env` touch (and mode) in staging evidence.
11. **[NOTE] E4.1 process-once suppression is left to implementation.** §16.3 item E-7 / §17 #27: the candidate boot hook is pinned strictly after `_ensure_postgres_columns()`, but the one-shot pattern is unspecified. **Remediation:** implement via an in-memory boolean latch on `InstanceManager` at initialization and state it in the plan.

---

## Iteration-001 blocker closure (post-verdict tracking comparison — the sanctioned use of tracking history)

| 001 blocker | Status in this cycle | Closing evidence |
|---|---|---|
| B1 C1/C2 SQL invalid (`job_queues.name`, `projects.id`) | **CLOSED** | Execution worker RAN the SQL: C-5 CTE executes on `ensemble_dev` → `(0,0)` with 15 skills; C1/C2 parse and return 0 rows exactly as documented |
| B2 staging command exit 78 + wrong install root | **CLOSED** | Plan now: `ENSEMBLE_UPGRADE_LIVE=1 ENSEMBLE_ROLLBACK_SAFE=1 bash scripts/upgrade/stage.sh live --version … --allow-stale-stage`; parser `*)`→78 (:125) and `require_live_guard` (`lib.sh:1363-1376`) verified against source |
| B3 migration census diff vs nonexistent staged dir | **CLOSED** | §14.2 step 5 is now BINARY-TOC-primary (`CArchiveReader`), states the payload has NO `daemon/` tree and `diff -r` exits 2; CArchiveReader importable from the pinned PyInstaller; payload contents verified (`stage.sh:395-425`) |
| B4 A3 acceptance 3457→0 unreachable + self-contradiction | **CLOSED** | LEADER RULING Q1 three-conjunct predicate (`selected ∧ task_succeeded ∧ feedback_applied IS NOT NULL`), DELTA acceptance form, 470-band separately disclosed, non-vacuous M-A3a; repository anchors verified (`:1550-1573`, `:1308-1318`, `:1339+`, gate sites `:648`/`:1648`) |
| B5 E2 acceptance false vs CAPTURED ValueError + zero §18 rows | **CLOSED** | E2 restated "BLOCKER 5 — restated against the shipped code": CAPTURED refusal documented as protected behavior (`skill_evolution_service.py:452-457`), M-E2c re-placed onto the `job_type` stamp, §18 rows 39/40 added |

Residuals from 001's major (non-blocking) set: M1 partially addressed (pack destination now named — `skill_capture_killswitch_unit_test`, PACKS.md:2993 — residue = finding 2); M3 partially (Ruling E BLOCKED-ON framing present — residue = finding 5); M4 partially (dialect-split design verified — residue = finding 8 index drift); M5 CLOSED (N1 env-surface scoping correct); M6 reframed (no "retire the stale/shadow rows" goal remains in the body; E4 delivers the dual-column deactivation lever + operator script, exec-verified).

---

## What the plan gets right (execution-verified — do not re-litigate on re-submit)

1. **Base-red mechanism reproduced live**: exactly the 3 named `tests/manager/test_skill_service_init.py` tests fail with `MigrationError … near "EXISTS"`; the classifier trap (ERROR line ×3/run) is visible in the committed raw evidence.
2. **SQLite DDL rejection verified**; the runner is a documented PG no-op; exactly ONE `20260714_000001` file exists.
3. **P1 roster recomputed by execution**: 46 agents; 15 true / 10 false / 21 absent → 31 cannot-capture; both name lists match character-for-character.
4. **A1/A2/A3 anchors exact**: `api.py:2571/2574/3250-3258`, `provider.py:67/75/147` (+late-binding template), `availability.py:61-62/83-88`; the five doc-token test files assert exactly the quoted tokens; `repository.py:1550-1573`, the one-way `applied` latch `:1308-1318`, `update_completion` `:1339+` never setting `applied`, the `:413-414` early return before Gate 4.
5. **A4 FIND-IT greps reproduce**: 0 hits for `GROUP BY name HAVING COUNT` in `daemon/`; broad `HAVING COUNT` → exactly the 5 cited sites.
6. **C's SQL runs**; C1/C2 read-only and return the documented rows.
7. **E1 dead-guard proof verified**: `job_queue_service.py:1596-1608` suffixes re-admissions `#<uuid8>` under `idx_job_idempotency` (`job_queue/models.py:300-306`) — a job-keyed cap can never trip; the dialect-split `metadata->>'skill_id'` design is implementable against the real metadata.
8. **B workstream exact**: keeper allow/deny matches the resolved surface verbatim; `resolve_tool_filter` (`instance.py:430`) is deny-after-allow; the `skill_evolve` inline hole is real; filesystem read-fences verified.
9. **G fully corroborated**: StaticPool `:memory:` (`conftest.py:42-46`), the concurrent `asyncio.gather`/`to_thread` fan-out (`skill_evolution_service.py:1448-1457`, `:1474-1483`), the "Skill not found for update" → `None` path; the decisive experiment reads NORMAL 11P/1F / INLINED 12P/0F.
10. **Staging/promote machinery anchors all verified** (arg parser, exact-tag guard, rollback-safe derivation, BIN_SHA, manifest heredoc with `known_schema_gen`, payload contents, FE-sidecar removal, non-blocking prompt leg, `ENSEMBLE_SELF_ENV`, `require_live_guard`, `_provenance_write`, `PIPELINE_SCRIPTS_FLOOR_COMMIT` = `2147c0298a…`; scripts-pin worktree at `84df6b55c`; the disclosed `_build_frontend.sh` literal-script deviation is real; `CArchiveReader` importable; all three operator-script precedents exist with dry-run/`--apply`).
11. **E4 levers verified on schema**: `SkillRepository.deactivate` sets both columns; `skill_ab_tests` carries `resolved_at` + `winner_skill_id`; no tombstone column; boot-order anchors exact.
12. **F anchors exact**: fail-closed default (`registry.py:690/:756`), dynamic-injection gates, `max_inject_skills` default 2 (`skill_injection_service.py:413`, `injected`/`low_match` `:433-434`).
13. **Ledger discipline**: §19.1's 29 contract strings verified present in BOTH plan and `decisions.md` on spot-checks; QUARANTINE.md/PACKS.md/evidence-pack claims check out (181-line prompt-artifact seal matches the 181/181 gate).
    **Superseded figure (approver correction, 2026-11-09):** the §19.1 cross-check table was 29 rows at approval time; it is now 38 rows after implementation added the E4 brake (C-31), R5 ratification (C-32), the resolved E4.1 site (C-23) and three B-lane amendments. The verdict is unchanged.

---

## Unverified items (disclosed — outside reach, not defects)

- Prod-DB census figures (32 divergence, 27 pairs / 54 rows, 26 live-unresolved A/B rows, 21 served-both, 116 job rows, 6 captured-origin rows, `(True,'ab_testing')=52`, 1,846 inactive-serving records, +480 counter drift): artifact-sourced; `ensemble_dev` cannot re-measure them (C-5's reproduction SQL itself verified valid, dev=0).
- P7 prod queue state (`is_paused=false`, `updated_at=2026-10-26T21:50:04Z`): artifact-sourced.
- Canonical-dir claims: operational context bars evaluating `/home/nea/ensemble-src`.

---

## Sessions

- Doc axis: `c7d08b87-e926-45b3-9eb4-5ee60401e87f` (`plan-approval`)
- Execution axis: `83eb859b-e16f-4e23-80f7-84580e02baa8` (`plan-approval`)

*Tracking: `.agents/approver/skill-system-revival-phase1-tracking.md`*
