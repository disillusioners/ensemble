# W3 FINAL GATE — maintenancer-agent pre-merge verdict

**Date:** 2026-09-09
**Branch:** `feature/maintenancer-agent` @ `012de252` (LOCAL, not pushed; W3-P5 tip; lineage a6b0ac0f → spec 2cb8e672 → W1 … 28efe5c4 → W2 2f17e823 → 012de252)
**Base (whole branch):** `a6b0ac0f` (`latest`, v0.12.4)
**Worktree:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble-wt-maintenancer-a` (drift-pinned branch+HEAD before EVERY invocation on every worker; `daemon.__file__` verified worktree-local on every pack; `git status --porcelain` clean at every worker's exit)
**Prior gates inherited:** W1 PASS @ `28efe5c4` + appendix (RESULTS/2026-09-09-w1-maintenancer-full-regression.md), W2 PASS @ `2f17e823` (RESULTS/2026-09-09-w2-maintenancer-gate-regression.md) — base-attribution table and pre-existing families carried forward unchanged; re-check trigger (new failure in family files) NEVER fired.
**Workers:** 13 — statics `4e6e5c94` (st-w3diff) / `fe753721` (st-census) / `c2de014e` (st-docs); packs `fb0692ad` (p-agent) / `c74e3d54` (p-kb-pins) / `357fa56a` (p-ensdb-sqlite) / `d79f17ca` (p-ensdb-pg) / `d84cd2be` (p-integration) / `cd21bfcf` (p-affected) / `b9b078aa` (p-registration) / `a1d4c665` (p-integrity) / `1d7ba6e4` (p-e2e) / `841a3212` (p-leader). All verify-only: zero source/test edits, zero commits, zero pushes anywhere.

## FINAL PRE-MERGE VERDICT: ✅ PASS — merge-ready from the testing side

**Zero failures across the entire affected surface: 2,146 collected / 2,125 passed / 0 failed / 0 timeout / 21 skipped (all adjudicated-expected conditionals).** Zero branch-caused regressions. Both engines exercised (SQLite-default + disposable PG 14.22). Every count reconciled against ground-truth `--collect-only` taken before each run.

## Scope Decision

Full-surface final gate per caller's enumeration (this IS the scoped gate: W3 diff 2f17e823..012de252 + whole-branch census + leader-adjacent closure). Additions beyond the literal list, both blast-radius-derived and load-bearing: (a) 3 leader-structure pin suites discovered by forensics (attestation_prompt_contract 33 — pins the W3-edited `leader/workflow.md`; plane_domain_access 26; instance_tools 204); (b) corpus-exclusion proof via the integrity gate's own imported iterator. Release-Gate daemon-boot E2E (ensure.md §Release Gate) NOT run: this is a worktree pre-merge branch gate; per `agents/maintenancer/ROLLOUT.md` the daemon restart + live verification belongs to the Wave-3 deploy window — surfaced explicitly, not silently skipped.

## Item 1 — W3 diff surface (2f17e823..012de252): ✅ EXACT

- **Diff surface:** 10 files, +1340/−101 — matches caller claim digit-for-digit (`git diff --stat` verified). Every path ∈ {agents/leader/**, agents/maintenancer/**, tests/**}: M leader/{soul,tools_note,workflow}.md (+2/−1, +7/−3, +1/−1); A maintenancer/{README.md(+101), ROLLOUT.md(+450)}; M maintenancer/{memory.md(+8/−12), kb-curator.md(+18/−72)}; A tests/integration/test_maintenancer_end_to_end.py(+512); M tests/unit/{test_maintenancer_agent.py(+1/−1), test_maintenancer_team_members_within_team.py(+240/−11)}. Nothing to flag.
- **e2e suite 19: ✅ PASS 19/19 in 0.32s** (ground-truth collect = 19). Tier coverage verified live: Tier1 KB-INDEX ×6, Tier2 filesystem-read ×1, Tier3 RAG-mirror ×1, load_skill dispatch ×2, tool-resolution ×6, report-shape ×2, nonce-gate ×1.
- **Nonce-refusal NON-VACUOUS — proven statically AND live.** Static (`4e6e5c94`): assertions pin `"user-confirmation-missing"` + `"no nonce supplied"` present AND `"CONFIRMATION REQUIRED"` ABSENT; input drives F1+F2 satisfied with F3 (nonce) absent; refusal markers are emitted ONLY on the refusal path (`daemon/tools/upgrade_tools.py:1940` inside the nonce-missing branch, gated at :2040-41) — if the gate wrongly accepted, `out` lacks the markers and `:501` fails. No try/except-pass, no assert-True, no accept-path collision. Live (`1d7ba6e4`): `TestThreeFactorGateRefuses::test_system_upgrade_without_nonce_refused` PASSED individually with the genuine-refusal shape.
- **Rider-a (regex teeth): ✅ GAP CLOSED.** Widened alternation (hand-off/delegate/escalate/pass/forward/refer/ask-the-leader, long-before-prefix ordering); **1 real corpus hit** (`bug-advisory.md:19` "hand off") — W2's F1 zero-hit detection-power gap closed; 2 regression-fixture classes (must-NOT-match on old regex ×5 negative fixtures; must-match with out-of-team peer capture `group(1)=="developer"`-class) + 2 shape-regression fixtures (alternation-order, ask-the-leader capture).
- **Rider-b (PROMPT_FILES 6→13): ✅.** `PROMPT_FILES` = 6 canonical + `sorted(skills-template/*.md)` = 13, kb-curator in scope; agent suite 55/55 (growth 41→55 = +14, arithmetic verified: TestConventionCompliance 30 = 1 base + 13 parametrized non-empty + 3 standalone + 13 parametrized forbidden-token).
- **Rider-c (memory.md INDEX): ✅ substance; byte figure adjudicated.** All 6 KB doc names on INDEX lines 4-9 (matches knowledge/ exactly); rider-b trigger words survive ("orphan ACTIVE" ×1, "DLQ replay" ×3 in memory.md). Pinned green by kb_coverage 41/41 and e2e Tier1 ×6. **Byte figure:** whole file = 4,898B (blob-identical to committed state, zero drift); INDEX block (L2-L14, heading+table) = **918B** — closest reading to the claimed "921B" (Δ3 = measurement-boundary artifact; no reading yields exactly 921). No test asserts 921 anywhere. Claim imprecision recorded (F3), not a defect.
- **Rider-d (kb-curator): ✅.** 1,951B at HEAD (was 3,644B @2f17e823) — plan bound ≤2k MET; bare `memory.md` token count = 0.

## Item 2 — Leader prompt changes (HIGH attention): ✅ CLEAN

Leader surfaces composed into every leader prompt were checked by 4 packs:
- **Section-reference integrity gate: 1,323/1,323, 0F 0S, 1.32s** — corpus byte-identical to W2; leader edits + kb-curator rewrite + memory.md INDEX all pass; no path-token regressions, no splice corruption.
- **Corpus exclusion PROVEN by importing the gate's own iterator:** `_iter_prompt_files()` = **217 files** (caller claim exact); `_is_in_scope("agents/maintenancer/README.md")` → False, `ROLLOUT.md` → False (predicate = explicit `PROMPT_SURFACE_GLOBS` whitelist tails `*/{soul,rule,tools_note,workflow,memory}.md` + `*/skills-template/*.md`). Collect-ids grep for README|ROLLOUT = 0 hits.
- **Affected existing suites: 244 collected / 227P / 0F / 17S** — loader 69/69 (incl. TestMaintenancerToolsDocColdBoot), registry 102/102, frozen-tool-name 6/6, report-integrity-prompts 50P+17S (skip count EXACTLY the W2-adjudicated design-conditionals — no drift; leader-targeted scrutiny/opening-discipline cases pass against the W3-edited prompts).
- **Leader-structure pins (forensics-discovered): 263/263 in 11.11s** — attestation_prompt_contract 33/33 (rule.md + workflow.md LCA-absent + suppression-rule-present contracts hold against the W3-edited workflow.md), plane_domain_access 26/26 (leader tools.allow no-plane-writes), instance_tools 204/204 (leader subtree_messages). 32 sqlmodel SAWarnings = pre-existing infra noise.

## Item 3 — Docs factual integrity (independent spot-checks): ✅ with 3 minor flags

- **README:** 7 skills ✅ (auto_load=true on exactly kb-curator; 6× false); 6 KB docs ✅ (all 6 names in memory.md INDEX; README internal cross-consistency L28/L51/L66/L70 all verified). README makes NO total-file-count claim — the task-brief's "17 files" is a stale W1-era KV snapshot number (actual: 25 files, 23 excl. .gitkeep; W3-P5 added README+ROLLOUT+6 skills-template). Informational claim drift, not a doc defect (F4).
- **ROLLOUT anchor 1 (pause endpoint): ✅** — `POST /api/instances/{id}/pause` → `manager.pause_instance_cascade` verified at `daemon/routers/instances.py:666-688` (router prefix :306, api prefix api.py:1755/1925). ⚠️ Cited range "647-669" born-stale → actual block 665-694 (F1).
- **ROLLOUT anchor 2 (registry singleton/restart): ✅** — `_registry` global (registry.py:1157-58), single `discover()` site in daemon/ (registry.py:1175, grep-verified once-per-process), cited 1170-1175 range accurate; restart-required conclusion correct. ⚠️ "import-time" wording imprecise — lazy singleton on first `get_registry()` call (F2).
- **ROLLOUT anchor 3 (rollback list): ✅ EXACTLY 8** `git revert -m 1` operations (3 pre-P2 L338-340 + 5 post-P2 L349-358; alternative groupings converge on 8).
- **Kill-switch: ✅** — `ENSEMBLE_REPAIR_ENABLED` name match (`ens_db_tools.py:125`), default ON (`:154` `return True  # default ON`), fail-closed ValueError on invalid values, gate at :778 inside `ens_db_repair_execute` only (SELECT reads ungated — matches doc), `=0/false/no/off` OFF vocabulary.
- **Merge-order invariant verbatim: ✅** — canonical 477B text byte-matches the KV record; in-branch verbatim block at `detail-plan.md:347` is **byte-identical modulo the markdown blockquote prefix `> `** (normalized diff exit 0; joined md5 `b664c2f65d5c2dcc47f0176c0b5b1157` both sides). ROLLOUT.md:45 carries a 1-line pointer (canonical block lives in detail-plan.md + the PR description) — consistent with the KV record's own `written_for: "final PR description … not a codebase file"`: pointer-by-design, not a mismatch. Internal cite `daemon/registry.py:1178-1180` cross-verified accurate (validate_tool_configs + warning loop).

## Item 4 — Whole-branch final census (a6b0ac0f..012de252): ✅

- **Branch shape:** 65 files, +8,891/−78 — agents/maintenancer/** 25A, agents/leader/** 4M, other-agents/** 10M (developer 4 + developer[v2] 3 + wanderer 3), tests/** 18 (15A+3M), plus 8 paths outside agents/+tests/: 7 daemon/ files (ens_db repo + ens_db_tools + loader/manager/_tool_registry/instance wiring = W1-P2 deliverables, gated green by W1/W2 ens-db suites) + 1 planning doc. Adjudicated expected — no unauthorized scope.
- **De-scope HOLDS:** `system-log` holders = **exactly {maintenancer, worker}** (machine-parse of every meta.json). developer ✅ removed, developer[v2] ✅ removed, wanderer ✅ removed, watcher ✅ clean (`tools.allow=[]`, branch diff vs watcher EMPTY), leader ✅ no system-log (meta touched only for `team_members += maintenancer`).
- **Registry discoverable:** `AgentRegistry(Path('agents').resolve()).discover()` → **31 untagged agents (36 incl. tagged)**, `maintenancer` present; metadata exact-matches W1 baseline (tools.allow=[system-log, ens-db, knowledge, system_upgrade, db], skill_injection=True, team_members=[explorer, worker, coder]). (API note: `discover()` populates + returns None; enumerate via `list_all()`.)
- **All maintenancer suites green:** agent 55 ✅, kb_coverage 41 ✅, skill pins (W2 33 → 80-bundle incl. team_members 15) ✅, e2e 19 ✅, ens-db family ✅ both engines, killswitch r13 24/24 ✅ (OFF byte-identical pins), spawn-resolves 13 ✅ + upgrade-gate-refuses 2 ✅, loader cold-boot ✅ (within 69/69), both registration pins ✅ (upgrade 21 + attestation 24).
- **Dual-engine: BOTH ENGINES RUN (not the SQLite-fallback path).** SQLite-default: 82 collected / 78P / 4S (4 engine-conditional skips, messages verbatim-expected). PG 14.22 (disposable, local binaries, port 15432, POSTGRES_* prod scrubbed every command, prod never contacted): **20/20 clean + dirty rerun 3/3** (no `DuplicateTable` regression — W2 `IF NOT EXISTS` guard holds) + corroboration (+12 repair_log audit rows; upsert_t stayed 1 = true idempotency). Teardown verified (15432 free, worktree clean, 8088 untouched).

## Item 5 — Grand totals (ground-truth --collect-only per pack, drift-pinned @ 012de252)

| Pack | Suite(s) | Collected | Passed | Failed | Skipped | Runtime | Result |
|---|---|---:|---:|---:|---:|---|---|
| p-agent | test_maintenancer_agent.py | 55 | 55 | 0 | 0 | 0.14s | ✅ |
| p-kb-pins | kb_coverage + 4 pin suites (5 files) | 80 | 80 | 0 | 0 | 0.26s | ✅ |
| p-ensdb-sqlite | 6 ens-db files | 82 | 78 | 0 | 4 (PG-cond) | 1.55s | ✅ |
| p-ensdb-pg | select_only + idempotent (real PG) | 20 | 20 | 0 | 0 | 0.43s | ✅ |
| — | idempotent dirty rerun (rider-a, same DB) | (3) | 3 | 0 | 0 | 0.25s | ✅ non-additive |
| p-integration | spawn_resolves + upgrade_gate_refuses | 15 | 15 | 0 | 0 | 0.31s | ✅ |
| p-affected | loader + registry + frozen + report_integrity_prompts | 244 | 227 | 0 | 17 (design) | 3.49s | ✅ |
| p-registration | upgrade + attestation registration | 45 | 45 | 0 | 0 | 4.72s | ✅ |
| p-integrity | section-reference gate | 1,323 | 1,323 | 0 | 0 | 1.32s | ✅ |
| p-e2e | maintenancer_end_to_end | 19 | 19 | 0 | 0 | 0.32s | ✅ |
| p-leader | attestation_prompt_contract + plane_domain_access + instance_tools | 263 | 263 | 0 | 0 | 11.11s | ✅ |
| **TOTAL** | | **2,146** | **2,125** | **0** | **21** | ~25s pytest | **✅** |

Count reconciliations: p-kb-pins expected 74 (W2 baseline) → actual 80 (+6 = W3-intended `TestRegexWidening` regression fixtures, rider-a); every other pack matched expectation exactly. Statics: 3 workers, all checks green.

**Failure attribution:** trivially clean — zero failures. Pre-existing families (archive-lifecycle ×5, devops ×3 + coder ×1, wanderer ×2, migration ×5, watcher ×9): re-check trigger condition (new failure in those files) never fired; those files were not in this gate's run surface except as inherited adjudications.

## ensure.md Validation (scoped by blast radius)

- **Core C1 (no regressions in changed packs): ✅** — every pack in the change set PASS (table above).
- **Core C2/C3 (concurrency pack): scoped OUT** — W3 diff touches no daemon code; the branch's daemon-side changes (W1-P2 ens-db tools) were gated by the ens-db suites (both engines, green); concurrency Atomic pack covers daemon paths the branch did not modify.
- **Core C4 (dev.sh graceful-shutdown flag): scoped OUT** — dev.sh is NOT among the 65 branch files (whole-branch diff enumerated); inherits validated base state.
- **Release Gate: DEFERRED to the Wave-3 deploy window by design** (daemon-boot E2E requires `./dev.sh` + live LLM; ROLLOUT.md 3-wave rollout owns the restart verification). Explicitly surfaced, not skipped.

## Findings (all NON-BLOCKING)

| # | Severity | Locus | Finding | Suggested action |
|---|---|---|---|---|
| F1 | 🟢 nice-to-have | `agents/maintenancer/ROLLOUT.md:164` | Born-stale line ref: cites `daemon/routers/instances.py:647-669`; actual pause block = :665-694 | Refresh to `:665-694` in a docs touch-up |
| F2 | 🟢 nice-to-have | README.md:45 + ROLLOUT.md:229 | "import-time singleton" imprecise — `_registry` is lazy (first `get_registry()` call); once-per-process + restart-required conclusions correct | Reword to "process-lifetime singleton (first `get_registry()` call)" |
| F3 | 🟢 informational | task-brief / W3-P5 claim | "memory.md INDEX 921B" — INDEX block measures 918B at HEAD (Δ3 measurement-boundary artifact; whole file 4,898B; no reading = 921; no test asserts it); substance (6/6 docs + rider-b triggers) verified | Optionally re-measure at PR time; nothing to fix in-repo |
| F4 | 🟢 informational | task-brief / KV snapshot | "17 files" is the W1-era post-merge snapshot; current = 25 files (W3-P5 added README+ROLLOUT+6 skills-template). README itself makes no total-file-count claim | No action (README accurate as written) |
| F5 | 🟢 observation | host | p-ensdb-pg worker observed an `ensemble-prod` listener on port 9797 (never contacted; outside gate scope) | Record only; outside gate scope |

## Environment Notes

- PG path B (docker daemon down): disposable PG 14.22 via homebrew binaries, `/tmp/w3-pgdata`, port 15432; POSTGRES_* prod env scrubbed before every command; teardown verified. psycopg2-binary already venv-present from W1 (no installs this gate).
- No source/test edits, no commits, no pushes anywhere (all 13 workers verify-only; worktree clean at every exit).
- Dispatcher path typo caught by worker existence-check (report_integrity_prompts lives at `tests/unit/`, not `tests/unit/tools/`) — worker self-corrected before invocation; lesson recorded.

## Documentation Updated

- [x] RESULTS/2026-09-09-w3-maintenancer-final-gate.md (this file)
- [x] PACKS.md — W3 gate row finalized with verdict
- [x] LESSONS/2026-09-09-w3-gate-brief-numeric-claims-and-path-typos.md
- [ ] QUARANTINE.md — no change (no flaky tests, no new failures)

---

### Overall Status — W3 FINAL GATE

- W3 diff surface + e2e 19 + riders Ra/Rb/Rc/Rd: ✅ (Rc byte figure adjudicated 918B, substance green)
- Leader prompt changes (4-pack closure incl. 3 forensics-discovered pin suites): ✅
- Docs factual integrity (3 anchors + kill-switch + KV verbatim): ✅ (3 minor flags F1/F2/F3)
- Whole-branch census (de-scope, registry, all suites, dual-engine): ✅
- Grand totals: 2,146 collected / 2,125 passed / 0 failed / 0 timeout / 21 expected skips
- ensure.md Core: ✅ (scoped); Release Gate: deferred to deploy window (by design)

**W3 FINAL PRE-MERGE VERDICT: ✅ PASS @ 012de252 — 0 branch-caused failures; merge-ready from the testing side.**
