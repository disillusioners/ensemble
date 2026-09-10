# W2 Gate Regression — maintenancer-agent branch (skills + workers)

**Date:** 2026-09-09
**Branch:** `feature/maintenancer-agent` @ `2f17e823` (LOCAL commit, not pushed; parent `28efe5c4` = W1 gate-PASSED)
**Base:** `a6b0ac0f` (`latest`, v0.12.4) — inherited via W1
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-maintenancer-a` (clean; drift-pinned branch+HEAD before AND before each invocation; `daemon.__file__` verified worktree-local on every pack)
**Diff under test:** `28efe5c4..2f17e823` — **15 files, +905/−59 (verified exact)**; file list: M `agents/maintenancer/{memory,rule,workflow}.md` + `skill-set.yaml`; A 6× `skills-template/*.md` (bug-advisory, ens-db-repair, health-check, job-mission-repair, log-forensics, restart-upgrade-ops); A 4× `tests/unit/test_maintenancer_*.py`; M `tests/unit/tools/test_ens_db_repair_idempotent.py`. **Zero files outside `agents/maintenancer/` + `tests/`** (verified twice, independently).
**Prior art inherited:** RESULTS/2026-09-09-w1-maintenancer-full-regression.md (W1 PASS @ 28efe5c4 + appendix) — base-attribution table NOT re-litigated.
**Workers:** 7 — static `7253142c` (skill integrity) / `845f5434` (rule-workflow-memory) / `0bf4d3a3` (tautology); packs `70d7c1ee` (new-pin) / `d1dae58a` (integrity gate) / `cbc1a915` (affected) / `c8ffa94e` (rider-a PG). All report-only; no edits, no commits, no pushes anywhere.

## W2 GATE VERDICT: ✅ PASS — 0 branch-caused failures across the entire W2 surface

| # | Node | Worker | Result |
|---|------|--------|--------|
| 1 | Static: skill-system integrity (8 checks) | `7253142c` | ✅ PASS 8/8 |
| 2 | Static: rule.md/workflow.md/memory.md deltas (6 checks) | `845f5434` | ✅ PASS 6/6 |
| 3 | Tautology audit — 4 new pin suites (33 tests) | `0bf4d3a3` | ✅ CLEAN-with-notes (31/33 genuine, 0 hard tautologies) |
| 4 | Pack: 4 new pin suites | `70d7c1ee` | ✅ PASS **33/33**, 0 skip (0.17s) |
| 5 | Pack: section-reference integrity gate | `d1dae58a` | ✅ PASS **1,323/1,323**, 0 fail, 0 skip (1.28s) |
| 6 | Pack: affected existing suites (4 files) | `cbc1a915` | ✅ PASS **251 collected / 0 failed** |
| 7 | Rider-a PG re-verification | `c8ffa94e` | ✅ **VERIFIED** — clean 3/3 + dirty rerun 3/3 |

**W2 pack totals: 1,607 collected / 1,590 passed / 0 failed / 17 skipped** (+ rider-a: SQLite 1P/2S; PG clean 3P; PG dirty-rerun 3P).

## Item 1 — New pin suites (4 files, 33 tests): ✅ PASS + genuinely assert

- **Run:** single invocation, drift-pinned @ 2f17e823, `timeout 300` outer + pyproject 30s inner, `.venv/bin/python` (no `uv run`). `--collect-only` ground truth = **33**; run = **33 passed / 0 failed / 0 skipped, 0.17s, exit 0**.
- Per-file composition: `test_maintenancer_skill_versions_consistent.py` 11 (incl. 7 parametrized manifest↔frontmatter version rows) · `test_maintenancer_report_scrutiny.py` + `test_maintenancer_fan_in_valve.py` 13 combined (workers' per-file transcriptions differ by a 6/7 swap on these two; single-invocation total authoritative) · `test_maintenancer_team_members_within_team.py` 9 (incl. 7 parametrized fallback rows).
- **Tautology audit (independent worker, suites NOT executed by it):** 31/33 assertions GENUINE with verified teeth — the load-bearing pins are all real: manifest↔filesystem **two independent sources** (set equality both directions + `version` cross-check + simulated-bump fires), `auto_load == [kb-curator]` literal, `len(skills) == 7` literal (closes the empty-manifest vacuity hole), fan-in cap regex `max\s+re[-\s]?dispatch\s*=\s*1` (softening to "= 2" fails), 5 multi-word ladder markers, `team_members == {explorer, worker, coder}` literal frozenset vs real meta.json. No `assert True`, no empty bodies, no try/except-pass, no self-deriving expectations anywhere.
- 2 weak-teeth findings (below) — neither a hard tautology, neither failing.

## Item 2 — Skill-system integrity: ✅ PASS 8/8

1. **Diff surface:** 15 files, +905/−59 exact; nothing outside `agents/maintenancer/` + `tests/`.
2. **Manifest:** `skill-set.yaml` = exactly 7 entries, all `name`+`version`; `auto_load: true` on **exactly one: kb-curator** (other 6 explicit `false`).
3. **Frontmatter byte-consistency: 7/7 MATCH** on all shared fields (`version` 1.0.0 / `category` / `auto_load`); `name` is filename-stem by design, `description` manifest-only — matches the repo's own pin contract in the new suite.
4. **Sizes:** 6 new files 1,929–1,998 bytes (`wc -c`) — all within the plan bound (`detail-plan.md:824` "Each skill ≤2k chars"); authored to the bound. **Note:** `kb-curator.md` 3,644 B exceeds that bound as written — W1 pre-existing, NOT in the W2 diff, already logged as a green follow-up by the W2 reviewer (not counted against W2).
5. **A3 boundary sentence:** exact-once (case-sensitive machine match) in `bug-advisory.md:13` AND `ens-db-repair.md:13` (source: `detail-plan.md:803-804`, Leader Adjudication #3); cross-contamination check: neither file contains the other's sentence.
6. **Guardrails:** `ens-db-repair.md` — 3 gates enumerated (:19-21 Confirm/Audit/Dry-run), DO$$-only idempotent shape + refusal of non-idempotent transforms, self-surgery refusal, pause-first, nonce relay, kill-switch awareness. `restart-upgrade-ops.md` — pause-first quiesce, dry-run-only, "Live arms ONLY via the 3-factor nonce gate", never-execute-live, missing-nonce refusal. (No literal `ens_db_repair_execute` token / `ensemble_prod` string required by plan — semantics carried by the enumerated gates; noted for the record.)
7. **Bare .md path tokens:** 6 new files = **zero hits**. All 9 corpus hits are in W1's kb-curator.md and exempt under the gate's own §12.5 #0 rules; static simulation of the gate's exact regex+exemption pipeline over all 7 templates = 0 test-visible violations (corroborated by the 1,323/1,323 run).
8. **+36 arithmetic STRUCTURALLY PROVEN:** the gate has exactly **6 parametrize-per-file functions** (`:282`, `:298`, `:329`, `:377`, `:412`, `:659`, all over `_iter_prompt_files()`); in-scope `.md` count under `agents/` went 245 → 251 (+6 = the new templates). 6 × 6 = +36; 1,287 + 36 = **1,323**; `--collect-only` ground truth = **1,323**. Gate run: **1,323/1,323 PASS, 0 failed, 0 skipped** — no new skips vs W1 terminal state.

## Item 3 — rule.md / workflow.md / memory.md deltas: ✅ PASS 6/6

1. **Cardinal #4 == D8:** D8 extracted from `origin/plan/maintenancer-agent:.agents/shared/planning/maintenancer-agent/decisions.md` (blob 41e82ad4; worktree-local planning dir has no decisions.md). **Substance verbatim**; char-level diff = **1 trailing period** added in rule.md (283→284 chars, `(per OPEN A).` vs `(per OPEN A)`) — cosmetic; byte-exactness would be a 1-char fix if demanded.
2. **Cardinal count: exactly 7** (rule.md:5-11), titles enumerated.
3. **Dispatch discipline:** one-skill-per-spawn (workflow.md:24), max-3-concurrent-workers (workflow.md:31), fan-in valve / END-TURN-no-polling (workflow.md:71 + :27 + §Fan-In Escape Valve :36-45) — all quoted, all located.
4. **Rider-b INDEX trigger words:** `orphan ACTIVE` + `DLQ replay` both present on the 05-repair-runbooks INDEX line (memory.md:10). Source of rider-b = **W2-P4 commit message @ 2f17e823** (planning docs silent on it — provenance note, requirement met).
5. **D11 budget:** canonical unit per the repo's own test = **chars** (`len()`, `test_maintenancer_kb_coverage.py:208-223`): combined **11,751 / 12,000** (97.9%, headroom 249); per-doc all ≤2,185 ≤ 2,400. The task's "11,887" = the **byte** total (`wc -c`) — exact match, 0 delta; both readings green.
6. **Knowledge docs untouched:** `git diff --stat 28efe5c4..2f17e823 -- agents/maintenancer/knowledge/` = EMPTY.

## Item 4 — Rider-a PG re-verification: ✅ VERIFIED on real PG (NOT deferred)

- **Docker still down** (`docker info` exit 1) → predecessor's path B replicated: local PG 14 binaries (homebrew `postgresql@14`), disposable cluster `/tmp/w2-pgdata`, loopback **port 15432**, db `ensemble_test`; `POSTGRES_*` scrubbed before every command (inherited env pointed at LIVE PROD `ensemble_prod` — never contacted); driver `psycopg2 2.9.12` already present from W1 (no install needed).
- **Fix-diff:** hunk confirmed — `CREATE TABLE IF NOT EXISTS upsert_t` + `INSERT ... ON CONFLICT (id) DO NOTHING`; sibling setup tables `idemp_t` / `idemp_do` also `IF NOT EXISTS`.
- **SQLite-default:** 1 passed / 2 skipped (0.32s), engine-conditional skip messages — unchanged default path.
- **PG clean run:** **3/3 PASSED** (0.39s), 0 skips.
- **PG DIRTY RERUN (the rider-a acceptance):** same command, SAME database, no reset → **3/3 PASSED** (0.30s). The W1 `DuplicateTable: relation "upsert_t" already exists` failure **did not recur**. Corroboration: `repair_log` 12 → 24 (+12 audit rows across the rerun's 3 tests); `upsert_t` stayed at **1 row** (true idempotency, not re-insert).
- **Teardown proven:** server stopped, `/tmp/w2-pgdata` + log removed, port 15432 listener empty, 8088 untouched, worktree clean.

## Item 5 — Affected existing suites: ✅ PASS, no regression vs W1

| File | Collected | W1 | Result |
|------|----------:|----|--------|
| tests/unit/test_maintenancer_kb_coverage.py | 41 | 41 | ✅ 41/41 |
| tests/unit/test_maintenancer_agent.py | 41 | 41 | ✅ 41/41 |
| tests/unit/test_report_integrity_prompts.py | 67 | 67 | ✅ 50P + 17S (design-skips), 0F |
| tests/test_registry.py | 102 | 102 | ✅ 102/102 |
| **Total** | **251** | 251 | **0 failed**, exit 0, 2.19s |

**Adjudication — the 17 skips are a W1 REPORTING artifact, not a W2 regression.** W1's table recorded "67 passed / 0 skipped"; the 17 are design-conditional parametrize skips (10× agents with no `team_members`, :242; 7× grandfathered pre-Wave-1 parents, :244) whose predicates read OTHER agents' metas — and the W2 diff provably touches nothing outside `agents/maintenancer/` + `tests/` (verified independently by two workers). Zero failures either way; collected counts identical. W1 record corrected here (see LESSONS).

## Item 6 — Pre-existing families: INHERITED, not re-run

Per instruction, W1 base-evidenced attributions are inherited unchanged: archive-lifecycle ×5, devops ×3 + coder ×1 (meta-drift), wanderer ×2, migration ×5, watcher ×9 (quarantined family). None of the W2 packs touches those files; no new failures appeared anywhere in the W2 surface. Re-check condition (new failure in those files) did not trigger.

## Findings (all NON-BLOCKING)

| # | Severity | Locus | Finding | Suggested action |
|---|----------|-------|---------|------------------|
| F1 | 🟠 important (non-failing) | `tests/unit/test_maintenancer_team_members_within_team.py:103-106` | Fallback regex `(?:fall\s+back\s+to\s+a|spawn\s+a|fall\s+back\s+to)\s+(...)` has **zero hits across all 7 real skill templates** — the 7 parametrized rows pass trivially; the org-chart invariant it claims to guard is effectively untested (pattern misses "delegate to", "escalate to", "ask the …"). Not a false pass — the invariant holds today — but a detection-power gap. | Widen the alternation (delegate/escalate/ask/hand to …) so the pin has corpus bite; add one negative-fixture self-test. |
| F2 | 🟢 nice-to-have | `tests/unit/test_maintenancer_report_scrutiny.py:88` | Parametrize case `"1"` is vacuous — digit 1 occurs 5× in workflow.md independent of the cap; passes even with the cap deleted. Redundant: the real cap pin with teeth is the regex in fan_in_valve (`:78-91`). | Drop the `"1"` param or fold into a single regex case. |
| F3 | 🟢 cosmetic | `agents/maintenancer/rule.md:8` | Cardinal #4 = D8 + 1 trailing period (283→284 chars). Substance verbatim. | 1-char fix if byte-verbatim is required. |
| F4 | 🟢 pre-existing (W1) | `agents/maintenancer/skills-template/kb-curator.md` (3,644 B) | Exceeds plan:824 ≤2k bound; NOT in W2 diff; already a logged green follow-up. | Existing follow-up queue. |
| F5 | 🟢 process | W1 RESULTS file | `report_integrity_prompts` recorded 67P/0S vs actual 50P/17S (lumped skips). Corrected here; LESSONS entry written. | Separate passed/skipped in future summaries. |

## Scope Decision

Caller's enumerated W2 surface executed in full (items 1-6) — no reduction (this IS the scoped gate: 7 nodes, 1,607 collected tests + statics). No expansion beyond the enumerated surface; pre-existing families deliberately NOT re-run (inherited; trigger condition unmet). Full suite not warranted — W2 diff confined to one agent's directory + tests, confirmed by diff-surface verification.

## Action Needed

- [ ] 🟠 F1 — widen team-members fallback regex (follow-up; non-blocking for W2 merge)
- [ ] 🟢 F2/F3 — cosmetic test/char cleanups (optional)
- [ ] Rider-a is CLOSED (verified); docker remains down — nothing pending on this gate

## Documentation Updated

- [x] RESULTS/2026-09-09-w2-maintenancer-gate-regression.md (this file)
- [x] PACKS.md — W2 gate ad-hoc pack row
- [x] LESSONS/2026-09-09-w2-passed-skipped-lumping-artifact.md
- [ ] QUARANTINE.md — no change (no new flaky/family)

---

### Overall Status — W2 GATE

- New pin suites (33): ✅ PASS + genuinely assert (31/33 strong teeth, 2 weak-teeth findings)
- Section-integrity gate: ✅ PASS 1,323/1,323 (+36 arithmetic structurally proven)
- Skill-system integrity: ✅ PASS 8/8
- rule/workflow/memory deltas: ✅ PASS 6/6
- Rider-a PG: ✅ VERIFIED (clean + dirty rerun, real PG via local-binary fallback — docker still down)
- Affected existing suites: ✅ PASS 251 collected / 0 failed (W1 skip-reporting artifact adjudicated)
- Pre-existing families: inherited, untouched

**W2 GATE: ✅ PASS @ 2f17e823 — 0 branch-caused failures; merge-ready from the testing side.**
