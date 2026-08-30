# Governor Recursive-Spawn Guard — Final Verification Gate

- **Date:** 2026-08-30
- **Branch:** `feature/governor-recursion-guard` @ `ba0c340c` (feature tip) + tester test-only commit `a8d395ae` (acceptance walk; `git diff --stat ba0c340c..a8d395ae` = 1 file, +855, `tests/test_governor_recursion_acceptance_walk.py`; daemon/ identical)
- **Base:** `latest` 6ba8da82 (3 feature commits: 694b091c → e320119b → ba0c340c)
- **Role:** Final functional gate (reviewer-approved, tidier-clean; dev deferred full regression here)
- **Dispatches:** 13 workers (discovery, scoped unit, acceptance walk, council pack, 10 sweep partitions, ensure.md, PG slice, 2 triage + 1 follow-up), ≤3 concurrent throughout

## VERDICT: ✅ SHIP-WITH-NOTES

The original incident (small-model governor hallucinating "governor → governor → governor…" tree spam) is **dead at every vector**, with real components. Zero regressions across the full non-integration suite (~15,229 sweep tests + 982 targeted). Every observed failure is pre-existing (base-verified or quarantine-documented), flaky (quarantined), or a proven load-artifact of the sweep itself. Notes for the leader below — none block merge.

---

## 1. Acceptance Walk — V1–V6 + legit flows + kill-switch + fail-closed

New file `tests/test_governor_recursion_acceptance_walk.py` (commit `a8d395ae`), **16/16 PASS ×3 deterministic** (3.7s), independently re-verified 16/16 by a second worker. Real components: file-backed SQLite, real repositories/factory, real tool closures, real `Config()` env path. Mocks confined to true externals (graph/LLM assembly, prompt cache, MCP names, SSE hub) — the guard, chain walk, convene tools, spawn tools, `invoke_agent_and_wait` were NEVER mocked.

| Vector | Verdict | Evidence (one line) |
|---|---|---|
| V1 convene_council (governor caller) | ✅ REFUSED + HINT | `"convene_council refused: you are already a governor. … HINT: Spawn councilors via spawn_councilor(…)"`; zero DB rows inserted; same for `convene_council_with_skill` |
| V2 spawn_instance(governor) via tool | ✅ REFUSED by lifecycle | `"ERROR: Spawn refused: parent chain already contains 1 governor ancestor(s) (limit 1). Chain: … HINT: …"` — lifecycle message shape proves origin (spawn_instance tool has no governor scalpel) |
| V3 governor CHILD both vectors | ✅ REFUSED | convene refused + spawn `"Spawn refused"` with chain naming the child itself — parent-inclusive |
| V4a root governor spawns governor (must BITE) | ✅ REFUSED | chain names the root governor (`iid-root` in chain) — strict-ancestors-only (root ⇒ ∅) would wrongly allow; parent-inclusive counting proven |
| V4b root governor convenes | ✅ REFUSED | `"convene_council refused"` + HINT; no rows |
| V5 invoke_agent_and_wait routing | ✅ READABLE | guard leg: `"Error: Spawn refused: … Chain: … HINT: …"` (no traceback); contrast leg: exactly `"Error: Agent not found: nonexistent-agent-zzz"` (generic form) |
| V6 spawn_councilor(governor target) | ✅ REFUSED by lifecycle | identity gate passes (caller IS governor), then lifecycle `"Spawn refused … HINT"` re-raised verbatim |
| LEGIT-1 non-governor convenes | ✅ PASS | `{"status": "convened", "governor_instance_id": …}` + REAL DB row `agent_id="governor"` parented under architect |
| LEGIT-2 governor child spawns councilors | ✅ PASS | `"Councilor 1 of 50"` + REAL worker rows under the governor child |
| LEGIT-3 sibling governors, separate trees | ✅ PASS | both `convened`; two distinct governor rows; trees independent |
| LEGIT-4 terminated-governor ancestor | ✅ PASS | after real TERMINATED write, fresh convene succeeds (`gov2 != gov1`) |
| KILL-SWITCH env=0 | ✅ PASS (lifecycle vectors) | real `Config()` reads env; governor spawns governor REAL rows; tool + councilor spawn succeed. **Caveat → Note 1** |
| KILL-SWITCH K=0 | ✅ PASS | real `LimitsConfig()` → `max_governor_ancestors=0`; governor→governor→governor real rows |
| KILL-SWITCH default ON | ✅ PASS | no env → guard enabled, K=1, refusals fire |
| FAIL-CLOSED | ✅ CONFIRMED | unit test `test_governor_spawn_fail_closed_on_ancestor_walk_error` forces REAL raises in the **DB/repo layer** (`get_ancestor_ids.side_effect=RuntimeError("db down")`), not in the guard, and asserts `"Spawn refused"` + HINT + chain + error text |

### Notes for the leader (reported, NOT fixed — per mandate)

1. 🟠 **Kill-switch does not gate the tool-layer convene scalpel** — `daemon/tools/instance.py:2110-2121` and `:2258-2270` (`if caller_agent_id == "governor"`) consult neither `LIMITS_GOVERNOR_RECURSION_GUARD_ENABLED` nor K. With the guard disabled, lifecycle-mediated vectors (V2/V4a/V6) unblock — including governor→governor via spawn_instance — but `convene_council` stays refused. The task text said "V1-V6 spawn attempts succeed again (guard fully disabled)"; as built, restoration is partial (deliberate defense-in-depth or oversight — leader's call; a 2-line flag-check would align it with the spec text).
2. 🟢 **Label mismatch vs spec text** — `spawn_councilor` feedback is `"Councilor {n} of {limit}"` (instance.py:2036); `"Child {n} of {limit}"` is the `spawn_instance` tool's label (:1815). Both count-feedbacks exist; only the spec's wording mapped them to the wrong tool.
3. 🟢 **"leader" cannot convene in this repo** — `agents/leader/meta.json` lacks governor team-membership and council tool allow; the real convening agents are architect/reviewer[v2]. Legit flows proven with architect. (Spec example, not a defect.)
4. 🟢 **V1 contract nuance** — `"Spawn refused"` is the lifecycle prefix; the convene tools' byte-stable refusal is `"<tool> refused: you are already a governor. … HINT: Spawn councilors via spawn_councilor(…)"`. Each surface asserted against its own real contract.
5. ℹ️ **Branch touches more than the stated file list** — cosmetic `config.yaml` diff (guard env-var comments + line reorder; `allowed_models` default identical) and a 12-line governor boot-log block in `daemon/manager.py` (`emit_governor_recursion_guard_boot_log`). Neither overlaps any failing test's site (base-verified).

## 2. Suite results (exact counts)

| Run | Result | Counts | Runtime |
|---|---|---|---|
| Feature unit (`test_governor_recursion_guard.py` + `test_utils.py`) | ✅ PASS | 36P/0F/0S (23+13) | 2.14s |
| Council+spawn themed pack A+B (19 files incl. 12 grep-added invoke_agent_and_wait files) | ✅ PASS | 820P/0F/0S | 12.16s |
| Council pack C `test_spawn_limit_edge_cases.py` | ⚠️ 9F — pre-existing SQLite-migration family (base-verified; QUARANTINE row). "PG re-run" was a no-op: fixture hardcodes `db_path=":memory:"` (line 41) so PG env is never consumed | 0P/9F | 2.57s |
| **Full sweep SW1** `tests/unit/test_[a-h]*` (54 files) | FAIL (pre-existing only) | 1215P/15F/2S/21E | ~14s |
| **SW2** `tests/unit/test_[i-r]*` (103 files) | FAIL (pre-existing only) | 2601P/10F/23S | 105.4s |
| **SW3** unit [s-z] + graph/rag/repositories | FAIL (pre-existing only) | 1112P/50F/11S/2E | 17.5s |
| **SW4** `tests/unit/tools` | FAIL (quarantined archive ×5 only) | 1023P/5F/1S | 9.7s |
| **SW5** unit/services + unit/routers | FAIL (job_queue_proxy ×8 only) | 1001P/8F/0S | 7.4s |
| **SW6** `tests/test_[a-h]*` (36 files) | FAIL (2 quarantine rows + 1 enqueue row + 13 VOID WIP-race) | 870P/16F/48S | 13.8s |
| **SW7** `tests/test_[i-z]*` (98 files) | FAIL (pre-existing only) | 2893P/85F/50S/5xf | 29.8s |
| **SW8** `tests/job_queue` | ✅ PASS — exact baseline (1569P/0F/38S) | 1569P/0F/38S | 19.4s |
| **SW9** services+tools+api+manager+migration+property+lint+performance+static | FAIL (pre-existing only; run 2×) | 976P/15F/14S | 16.1s |
| **SW10** opencode+msgq_redesign+repositories+integration+e2e (`--ignore` daemon-dependent file) | FAIL (pre-existing + load-artifacts, see §3) | 1510P/16F/14S/10E | 79.9s |
| **Sweep totals** | — | **14,770P / 220F / 201S / 33E / 5xf (15,229 collected)** | — |
| ensure.md Core: `concurrency_atomic_unit_test.sh` | ✅ PASS — exact baseline | 98P/74S/0F | 7.6s |
| PG slice (12 instance/hierarchy files, serial, real PostgreSQL) | ✅ PASS | 86P/33S/0F | 8.1s |
| Acceptance walk (new, ×3 + independent re-verify) | ✅ PASS | 16P ×4 | 3.7s |

**ensure.md:** Core 4/4 Critical + 2/2 Important PASS (dev.sh:102 `--timeout-graceful-shutdown 10` verified; await-greps clean; zero method contradictions). Release Gate item 1 (full non-integration suite) = satisfied via the 10-partition sweep. Release-Gate live-LLM E2E items NOT triggered: change is spawn-path guard logic, not Job/Task/Queue (the E2E-mandatory surface), and the acceptance walk already exercises real-component spawn behavior; live-LLM E2E adds cost with low marginal signal here. `tests/e2e/test_context_injection_hybrid.py` excluded (requires live daemon; aborts collection by design).

**Exclusions (documented):** full `tests/postgres/` 270 (ran the 119-collected instance/hierarchy slice instead — blast-radius scoped; chain-walk SQL proven on real PG); frontend (backend-only change).

## 3. Regression triage — every failure class

**Raw sweep failures/errors: 253 (220F+33E). Attributed: 0 regressions.**

| Class (count) | Where | Attribution | Evidence |
|---|---|---|---|
| VOID — SW6 WIP race (13F) | SW6 | tester-process artifact | sweep glob raced the acceptance worker's in-flight uncommitted file; final committed file 16/16 ×4 |
| VOID — load-induced infra-flakes (10E + ~6F) | SW10 vscode cluster + update_activity | sweep-concurrency artifact | absent at HEAD isolated (3 runs, serial+parallel) AND at base isolated; see LESSONS/2026-08-30-sweep-load-confound.md |
| SQLite-migration `20260714_000001` (84: progressive_dispatch 18, manager 38, spawn_limit 9, migration_api 1, phase4_metrics 6, skill_service_init 3, + council C re-run 9) | SW7/SW9/council | PRE-EXISTING | base worktree verbatim (C7/C8); QUARANTINE row (family since 2026-08-14) |
| Watchover `default_streaming` (45F) | SW3 | PRE-EXISTING | QUARANTINE row (base-evidenced 2026-08-26); signatures match |
| Misc drift cluster rows (44F: hide_kb 5, coder_developer_migration 5, devops 3, coder_agent 1, api_router_extraction 1, job_queue_proxy_phase1 8, job_processor_status_guard 4, models_split 1, phase4 1, phase5 1, question_deferred 1, innate_skills 1, llm_load_balance 1, skill_evolution_config 2, terminal_orphan 1, validate_agent_id 1, wanderer 2, agents_api 2, enqueue_shared 1) | SW1/2/3/5/6/7 | PRE-EXISTING | QUARANTINE misc-cluster row (base-evidenced); SW5's 8 = leader's expected list |
| Blueprint-fixture mock family (6E: context7 4 + webfetch 2) | SW1/SW3 | PRE-EXISTING | QUARANTINE row; same `Mock has no attribute 'blueprint'` |
| builtin_mcp `request_gzip` mock-gap (15E) | SW1 | PRE-EXISTING | base: 17E same signature (C1); gzip-feature fixture gap, not branch |
| llm_allowed_models `coding2` (2F) | SW2 | PRE-EXISTING | base: 2F identical (C2); `allowed_models` default identical both trees (config diff cosmetic) |
| memory_integration (10F: 9 MagicMock + 1 archive) | SW7 | PRE-EXISTING | base: 10F identical signature-drift (C3) |
| `_ManagerStub` watchover-terminate (4F) | SW7 | PRE-EXISTING | base: 4F identical (C4); documented critical-note follow-up |
| `_ManagerStandin` search kwarg (2F) | SW9 | PRE-EXISTING | base: 2F identical (C5) |
| Static chokepoint callers (2F) | SW9 | PRE-EXISTING | base: 2F identical (C6); branch's 12-line manager.py boot-log block does NOT overlap violation sites; worker_pool.py untouched |
| Archive lifecycle (5F) | SW4 | PRE-EXISTING | QUARANTINE row (triple-attributed) |
| e2e stale asserts (3F) | SW10 | PRE-EXISTING | QUARANTINE rows (c171a289 semantic shift) |
| answer_dismiss lifecycle (1F) | SW10 | PRE-EXISTING | base: identical (D4) |
| complete_cancel_route transitions (4F) | SW10 | PRE-EXISTING | base: identical (D1) |
| nuclear_cleanup zombie reaper (6F) | SW10 | PRE-EXISTING | base: identical (D2) |
| pause_race_w7 MagicMock queue_type (1F) | SW10 | PRE-EXISTING | base: identical (D3) |
| property turn_state_machine (1F) | SW9 | PRE-EXISTING | QUARANTINE row (same test id; property-walk message text varies by design) |
| vscode deterministic C1 (1F) + SPA 404 (2F intermittent) | SW10/D5 | PRE-EXISTING | base: deterministic/intermittent identical (D5) |
| FLAKY — `test_ab_resolution_force_resolve` (1F) | SW9 | FLAKY → QUARANTINED | fail-in-pack + pass-in-re-run + 1F/3P solo; skill-evolution surface, zero branch overlap |
| FLAKY — `test_dequeue_concurrent_only_one_worker_wins` | SW10/D6 | FLAKY → QUARANTINED | 1F/8P solo at BASE (pre-existing) + 3P/0F HEAD isolated |

Positive delta: `test_v2_governor_team_members_authorize_convene_council` (explorer-flagged stale/broken) **passes** at HEAD — no expected-failure manifested.

## 4. Verdict rationale

- **Incident dead at every vector** (V1–V6), no false positives (LEGIT 1–4), parent-inclusive fix bites (V4a), fail-closed proven with real teeth, default ON, kill-switch works on lifecycle vectors (partial on convene scalpel → Note 1).
- **Zero regressions**: full non-integration suite (~15.2k) + PG slice + concurrency baseline — every failure base-verified pre-existing, quarantine-documented, flaky (now quarantined), or proven load-artifact.
- **Two new quarantine rows** added (both pre-existing-class flakes, branch-exonerated). Rising quarantine count = repo-hygiene signal, not a gate blocker.
- **SHIP-WITH-NOTES** rather than SHIP: Note 1 (kill-switch/convene divergence) is a spec-vs-build decision the leader should make explicitly (accept as defense-in-depth, or route the 2-line flag-check back through dev). Nothing else blocks.

## 5. Operational notes

- PG `public` schema GRANTs to `ensemble` had **reverted again** (third occurrence); repaired per LESSONS/2026-07-29; recommend the conftest-preflight/default-privileges fix finally lands.
- `test_spawn_limit_edge_cases.py` cannot be PG-tested via env (fixture hardcodes `:memory:`) — needs a `--db-url`/env hook if ever required.
- Sweep discipline: ≤3 concurrent workers held; embedded-daemon partitions are load-sensitive (LESSONS/2026-08-30-sweep-load-confound.md).

## Code changes summary
- `tests/test_governor_recursion_acceptance_walk.py` (NEW, +855) — commit `a8d395ae` (test-only; verified via `git show --stat`)
- `.agents/tester/` gate records (this file, PACKS.md, QUARANTINE.md ×2 rows, LESSONS ×1) — committed as gate records
- **Production code: ZERO changes by tester** (mandate honored; findings route to leader)
