# GATE D — Tier-2 Whole-Tree Dual-Side Regression Leg (RESUMED)

**Lane:** plane-pm-issue-tools · **Gate:** D (Tier-2 whole-tree) · **Date:** 2026-11-02
**BASE:** `941cb6321` · **HEAD:** `31869158e` (branch `feature/plane-pm-issue-tools`, verified pre-run)
**Tier-2 trigger (mechanical):** lane diff touches core seam `daemon/manager.py` (+413/−20).
**Recipes:** `.agents/tester/skills/whole-tree-base-proven-diff/` + `.agents/tester/skills/failed-digest-equality-gate/` + `ensure.md` Gate Tiers — all read before execution.
**Verdict: PASS-WITH-NOTES — NET-NEW = ∅ after 3× adjudication (zero deterministic regressions).**

---

## RESUME section (predecessor state → this run)

The predecessor wedged mid-bash ~20:49Z and its work was PRESERVED; this gate RESUMED it — completed work was not redone.

**Predecessor completed:**
- Corpus derivation both legs (`base.nodes.txt` 32,450 ids; `head.nodes.txt` 32,728 ids) + collect files.
- BASE leg COMPLETE: all 11 shards tallied in `base.progress.tsv` (000–009 n=3000 + shard010 n=2450), closure exact, failed set extracted (`base.failed.txt`, 450 ids).
- HEAD leg shards 000–004 complete (15,000 nodes, tallies in `head.progress.tsv`), failed ids partial (344).
- shard005 id file written; pytest ran to ~79% then **DIED SILENTLY ~20:49Z** (partial `shard005.out`, 2,716 bytes, no RC line, no summary, no FAILED lines; no processes left; no auto-split artifacts; no hangs entry). Driver + pytest were killed together — an environmental death (wedged agent bash call), NOT the async-hang class (shard005 re-ran clean in 91s this run; see below).
- Worktrees `/home/nea/ensemble-src-wt-ppm-tier2-base` (@ 941cb6321) and `…-head` (@ 31869158e) registered; both reused after verification (correct SHAs; base porcelain clean; head porcelain = only the known `install-audit.jsonl` side effect; per-leg `cd <wt> && .venv/bin/python -c "import daemon"` resolves INSIDE each tree — PASS both).

**This run re-derived + reconciled BEFORE resuming (all PASS):**
- Fresh HEAD `--collect-only -q` re-run: **32,728 collected (1,145 deselected)**; sorted node list **byte-identical** to preserved `head.nodes.txt`.
- Preserved shard id files `shard000–005.txt` == `head.nodes.txt[0:18000]` byte-identical; base-era `shard006–010.txt` == `base.nodes.txt` slices byte-identical.
- Shard math: HEAD total 32,728 = 10×3000 + 2,728 (shard010 tail); BASE 32,450 = 10×3000 + 2,450. Both match the preserved node files exactly.

**This run executed:**
- HEAD shards **005–010** (same shard size 3000, same `run_shard.sh` command form, outer `timeout 1500`, pytest `--timeout=120 -q --tb=no -rEf`, `-n auto` within shard, per-var env scrubs, `HOME=$(mktemp -d)`, `-p no:randomly`, `-p no:cacheprovider`). **No shard died; NO bisection was needed; `hangs.head.txt` is EMPTY** — the feared async-hang class did not materialize on shard005 (91s, P=2996 F=4 — tallies identical to base shard005).
- Base-era artifacts overwritten during the HEAD resume were archived first under the scratch `archived.base-era/` (incl. the predecessor's partial `shard005.out`, preserved as evidence); base tallies + failed set were already extracted and were not touched.

## Slicing proof (executed == declared)

| Leg | Collected | Deselected | Executed (shard sum) | Closure P+F+E+S+xf+xp | UNPARSED |
|---|---|---|---|---|---|
| BASE | 32,450 / 33,595 | 1,145 | 32,450 (11 shards) | 31,662+425+25+332+6+0 = **32,450 exact** | **0** |
| HEAD | 32,728 / 33,873 | 1,145 | 32,728 (11 shards) | 31,947+418+25+332+6+0 = **32,728 exact** | **0** |

Exclusions applied (identical both legs): `--ignore=tests/packs/g7_unique_index_smoke_test.py` (g7 `sys.exit` collection trap; pin: packs run via registered scripts) + `--ignore=tests/e2e/test_context_injection_hybrid.py` (import-time `:8079` HTTP GET; pin: `e2e_workflows_ensure_test`). **Disclosure:** the corpus otherwise follows the predecessor's derivation, which did NOT exclude `tests/performance/test_message_api_cost.py` (registry says excluded-by-default, unschedulable >300s solo) — its 6 reds are byte-identical on both legs (pre-existing by digest) and were excluded from the 3× isolation per the registry; compensating pin: `regression_performance` pack.

## Shard table (both legs)

Per-shard tallies: `EVIDENCE/plane-pm-tier2-2026-11-02/{head,base}.progress.tsv`. HEAD 000–004 = predecessor; 005–010 = this run. Highlights: HEAD shard005 91s P=2996 F=4 (base shard005: 91.0s P=2996 F=4 — identical shape); HEAD shard006 F=38 E=21 (base: F=39 E=21).

## Digest gate

**Form: canonical — sorted bare node-ids (FAILED/ERROR prefix stripped, exception-repr suffixes stripped), one per line, trailing newline.**

| Artifact | sha256 |
|---|---|
| `base.ids.sorted.clean.txt` (450 ids) | `093e906e468a105d4c1c3d323c69893b523571d8b93fb2ea2acae904e3c43cbe` |
| `head.ids.sorted.clean.txt` (443 ids) | `2b349a4b3310955ebe836cdea41de090b8b133527ac9e73c88d7b235fbe1e706` |

`cmp`: differ (expected). `comm` both directions:
- **NET-NEW (head-only): 19 candidates** → adjudicated below ⇒ **∅ deterministic**.
- **FIXED (base-only): 26** (each named in `fixed.baseonly.txt`; symmetric artifacts — see adjudication).
- **PRE-EXISTING (intersection): 424**.

**Extraction correction (resume finding):** the predecessor's driver stripped the `FAILED `/`ERROR ` prefix but 8 head / 9 base summary lines carried ` - <exception repr>` suffixes (pytest prints the repr on some failure classes). Node ids never contain spaces, so `awk '{print $1}'` recovered the bare ids for both legs; counts were unaffected (443/450 = F+E both legs); UNPARSED = 0 after stripping. Raw per-shard outputs remain in the scratch for audit.

## Net-new adjudication — 19 candidates, ZERO deterministic regressions

TRUE solo isolation (1 node = 1 pytest process, 3 reps per leg, `timeout 120`, fenced env identical to shards; 114 measurements, `solo3x.results.tsv`):

- **16/19 pass 3/3 solo at HEAD** (critical_notes lazy_mint; chart T1 second+third; instance_list_order ×6; slash_commands ×6; auto_kill sibling_alive) — the shard-context reds were xdist parallelism/order artifacts.
- `job_feedback_observer::test_tier1_sibling_owned_row_skipped`: head solo **3/3 PASS** (base 2/3) — clean at head.
- `dependency_bus::test_per_parent_lock_serializes_db_insert`: head solo 2/3, base 3/3 — flaky at head (≥1 pass ∧ ≥1 fail ⇒ **FLAKY**).
- `manager_shutdown::test_durable_sweep_sibling_alive_skip_and_dead_writer_swept`: head solo 1/3, base solo 2/3 — flaky on BOTH legs ⇒ **FLAKY** (earlier batched 3× showed head 3/3-red / base 1/3-pass — intra-batch interference; solo runs supersede).
- **None** shows head-deterministic-red + base-deterministic-green under true solo isolation. **No production-code fixes made; nothing to fix.**

Batched 3× (45-node union, 17 file-batches, 3 reps × 2 legs, sequential no-xdist) is retained as auxiliary evidence; its node-level rows that disagree with solo runs are attributed to intra-batch module-state interference and superseded by the solo matrix for classification.

## FIXED (26) — base-only reds, all symmetric artifacts

`fixed.baseonly.txt` (committed). Classes: slash_commands TestSlashCommandIntercept ×6 + work_router ×7 + hide_kb ×1 + message_metadata ×2 + vscode ×3 + plane_sync ×2 + chart T3 + atomic_dequeue + skill_evolution + test_api roundtrip. Where stable in isolation, behavior is identical on both legs (red at base-shard = ordering artifact); where green in isolation on both legs (e.g. plane_sync ×2, chart T3), the base-shard red was a parallelism artifact. None is a lane-introduced fix claim; none is a regression.

## Pre-existing (424) — stable, symmetric, attributed

- **39 covered by Tier-1** (digest `65dca363…f4b828`, byte-identical to `.agents/tester/EVIDENCE/plane-pm-tier1-2026-11-02/preexisting.txt`; all 39 present in this intersection): Tier-1 proved stable 3× on BOTH legs — **cited, not re-proven** (21 configure-builtin-stub ERRORS + 18 failures).
- **385 uncovered → re-proven here, 3 reps × 2 legs (batched, sequential)**: per-leg rep sets byte-identical (`cmp` r1==r2==r3 both legs); head-vs-base rep digests **identical** (`dd166c19…79352`); the same 11 nodes pass every rep on both legs (order-sensitive pre-existing; symmetric — not net-new).
- **perf-6** (`test_message_api_cost.py`): excluded from 3× per pre-exclusion registry; red byte-identical both legs in the shard phase.

### Base-debt attribution (verify-at-base; NOT net-new)

| Family | Nodes | Evidence |
|---|---|---|
| configure-builtin-stub packs (routed) | 32 in intersection (`test_builtin_mcp_servers` 17 + `test_configure_builtin_idempotency` 15; 21 of them = the Tier-1 ERRORS) | Tier-1 §7 + this gate's uncovered 3× |
| TestPromptComposition ×2 (`test_project_manager_agent.py::test_soul_cites_rule_for_severity_framing`, `::test_workflow_cites_soul_and_rule`) | 2 | intersection both legs |
| 4-sealed-artifact chart red (`test_chart_image_delivery_e2e.py::TestPhaseDDoesNotModifySealedArtifacts`) | 1 | intersection both legs |
| agent-base-v2 family (`test_agents_api.py::test_list_agents_success`, `::test_list_agents_empty_directory`) | 2 | intersection both legs |
| 39 plane-family / Tier-1 blast radius | 39 | Tier-1 evidence cited |
| other pre-existing (spawn_team_members 21, progressive_dispatch 18, memory_integration 16, injection_api 16, governor_integration 16, defer_blocked_api 16, …) | remainder of 385 | this gate's 3× (stable, symmetric) |

## Budget + measured rates

Advisory ≤90 min/leg. Measured: BASE leg ≈31 min shards (predecessor) + ~30 min 3× isolation; HEAD leg ≈15 min shards (predecessor 000–004) + ≈11 min (this run 005–010) + ~30 min 3× isolation. Shard rates: head 005–010 ≈ 66–204s/3000 nodes (~17–45 nodes/s under concurrent host load from unrelated lanes); pre-existing batched rep ≈ 288–324s for 379 nodes. Budget breach is disclosed loudly per the recipe; no re-scope needed (all work completed within session).

## Verdict

**NET-NEW = ∅** (zero deterministic regressions; 19 shard-context candidates all adjudicated FLAKY/order-sensitive under solo isolation). **GATE D: PASS-WITH-NOTES** — notes = (a) FLAKY bucket members named above; (b) perf-6 exclusion per registry with compensating pin; (c) corpus inherited the predecessor's no-performance-exclusion derivation (disclosed); (d) FIXED(26) are symmetric artifacts, not fix claims. Raw artifacts: `EVIDENCE/plane-pm-tier2-2026-11-02/` (25-file sha256 manifest included).
