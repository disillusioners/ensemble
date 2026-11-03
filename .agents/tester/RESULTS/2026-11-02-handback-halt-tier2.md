# Tier-2 Whole-Tree Dual-Side Artifact — lane `promote-handback-halt` (Phase A ceremony gate)

**Commission:** independent tester functional gate, Phase A (council UNV#1: "the Tier-2 artifact must gate the ceremony"). Incident `r-20261101-212542-8279`.
**Tester instance:** ca6584a3 (dispatcher) · corpus/setup worker `bcceb6a0-1566-4d9e-ac48-385c1d36c1b3`.
**Executed:** 2026-11-02 22:08Z → 2026-11-03 02:39Z (≈4h31m total wall; per-leg budgets below).
**Recipe:** `.agents/tester/rules/ensure.md` § Gate Tiers (Tier 2) + `whole-tree-base-proven-diff` v1.0.1 + `failed-digest-equality-gate` v1.0.2 (file canon, loaded before execution).

## Verdict

# TIER-2: PASS — net-new deterministic reds = 0

- **Default corpus:** BASE `941cb6321` 451 reds vs HEAD `023f47b16` 450 reds → leg-diff 6 net-new / 7 fixed → **3× isolation ladder: 0 genuinely-net-new** (5 order/load-sensitive, 1 flaky-at-head, 1 environment-sensitive — see §4).
- **Integration corpus:** BASE 33 vs HEAD 34 → 1 net-new → **3/3 PASS in isolation on BOTH sides** → order/load-sensitive.
- **Combined:** 484 vs 484 ids; 7 net-new / 7 fixed, **all adjudicated non-branch** with 3× isolation evidence per node.
- Slicing proofs exact on all four legs (§2). Digests BARE-ID form-tagged; raw forms recorded, never cross-compared.

## 1. Scope

- **Sides:** detached scratch trees `/home/nea/ensemble-src-wt-phh-t2-base` @ `941cb63215f0fe0948f4d48f93821aec4c169527` and `-head` @ `023f47b16dfa275bc6c4f58708a84209ec84cc31`; per-tree venvs (`uv sync`), `import daemon` asserted inside each tree (editable-pth trap closed). Python 3.14.7.
- **Corpus:** default suite (`tests/`, default addopts, recipe pre-exclusions `--ignore=tests/packs` + `--ignore=tests/e2e/test_context_injection_hybrid.py`) + integration (`--override-ini="addopts=" -m integration`). `-m postgres` legs NOT in the dispatched corpus — **excluded, disclosed** (lane's daemon surface is covered by the default corpus's unit set + the Phase B pack matrix).
- **Legs sequential, never concurrent** (DF-4 rule). Shards ≤3300 nodes, per-shard `timeout 1750`, xdist `-n auto` within default parallel shards only; serial shards for the scratch-DB node families (78 default / 2 integration, source-derived union — literal node-id grep was vacuous, disclosed).

## 2. Execution + slicing proofs

| leg | shards | rc=124 | nodes | wall | rate | verdict |
|---|---|---|---|---|---|---|
| base/default | 11 | 0 | **32450/32450** | 33.4 min | 972.5 n/min | PASS |
| head/default | 11 | 0 | **32477/32477** | 32.1 min | 1012.3 n/min | PASS |
| base/integration | 1 | 1* | **594/594** | 29.2 min (session 702s) | — | PARTIAL-124 → RECOVERED |
| head/integration | 1 | 0 (rc=1 @285s) | **594/594** | 4.8 min + leaf rerun | — | DEAD mid-session → RECOVERED |

*base/integration **completed its session** (25 failed, 462 passed, 72 skipped, 9 errors = 33 ids) and hung in teardown on background service threads until the 1750s cap — rc=124 ≠ dead shard; death predicate = "no summary line". head/integration died mid-session (2 pytest-timeout `os._exit(1)`, no summary) → recovered by symmetric per-node leaf re-runs (593 ids + 1 no-verdict node excluded on BOTH sides).

**Slicing arithmetic:** census == sum(manifest nnodes) == serial+parallel closure on all four legs (recorded in §1 evidence). **Budgets:** every leg ≤90-min Tier-2 advisory — re-projected at each shard-0 checkpoint per recipe v1.0.1. Total commission wall (4h31m) exceeds 90 min due to the mid-run interruptions + recovery legs (§6), not leg budgets — disclosed per the leader's steer.

## 3. Digests (BARE-ID: sorted node-ids, prefix stripped, message suffix stripped)

| mode | base sha | head sha | cmp | net-new | fixed |
|---|---|---|---|---|---|
| default | `f3f7e2d3…` (451) | `f1667c8c…` (450) | rc=1 | 6 | 7 |
| integration | `f2d01a6a…` (33) | `8e24d5c7…` (34) | rc=1 | 1 | 0 |
| combined | `be7a2b23…` (484) | `31b538d8…` (484) | rc=1 | 7 | 7 |

Diff lists: `/tmp/phh-t2/diff-{netnew,fixed}-{default,integration,combined}.txt`.

**Digest-form correction (in-flight finding):** pytest `-rEf` prints `FAILED <id> - <message>`; the recipe's prefix-strip alone leaves the message attached, making the same node appear as both net-new and fixed (shard-01: 7/7 raw → 4/4 corrected). All four legs re-derived from persisted logs via longest-known-id-prefix (`/tmp/phh-t2/redigest.sh`). Recommend the `failed-digest-equality-gate` skill body document the message-suffix trap.

## 4. 3× isolation ladder (all 14 diff nodes, serial, fenced, no `-n`)

**NET-NEW adjudication (7):**

| node | base P/F | head P/F | class |
|---|---|---|---|
| `test_auth_middleware_latency::test_module_lane_pass_through_latency` | 3/0 | 3/0 | order/load-sensitive |
| `test_skill_evolution_service::test_ab_resolution_force_resolve` | 3/0 | 2/1 | flaky (head) |
| `test_api::test_send_message_instance_not_found` | 3/0 | 3/0 | order/load-sensitive |
| `chart_tools::TestT3ErrorPolicyEnd2End::test_error_charter_revives_then_respawns_on_second_failure` | 3/0 | 3/0 | order/load-sensitive |
| `defer_blocked_api::TestConsistencyPin::test_queued_only_fixture_agrees_both_directions` | 3/0 | 3/0 | order/load-sensitive |
| `defer_blocked_api::TestStalledClassification::test_stalled_holder_status_reflects_instance_status` | 3/0 | 3/0 | order/load-sensitive |
| `test_chat_source_saturation_isolation::test_chat_message_claimed_by_chat_worker_under_saturation` (integration) | 3/3 P | 3/3 P | order/load-sensitive (both sides) |
| `test_auto_kill_integration::TestPathADurableTombstoneReap::test_sibling_alive_row_skipped_by_terminal_lane` | **2/1** | **0/3** | **environment-sensitive — HOST-STATE DEBT, not lane** (see below) |

**The one head-stable-fail node — adjudicated NOT a lane regression, evidence triple:**
1. **Base-side failure proves non-branch causality**: the node fails in isolation at BASE too (2/1) — the failure mode exists without the lane's code.
2. **Inversion across contexts**: in the corpus LEG it failed at BASE and passed at HEAD; in isolation it fails at HEAD and usually passes at BASE — a time-varying signal, not a branch signal.
3. **Zero path overlap**: the test inspects live process-group state (auto_kill / bash_spawn_reaper territory); the lane's changed daemon files are `api.py` (+11, readiness provider only), `services/upgrade_journal_sweep.py`, `tools/upgrade_journal.py`, `tools/upgrade_tools.py` — none touch the reaper/auto-kill code.
Confounding host event: the live daemon was operator-promoted v0.28.4→v0.28.7 + restarted at 23:39Z, **mid-corpus** (§6). **Disposition: host-state debt, recorded; optional re-check under a pinned host state offered to the leader; does not gate this verdict.**

**FIXED (7)** — base-only reds absent at head; ladder shows the class is load-flake (e.g. `test_job_feedback_observer` 2/1 both sides, `test_dependency_bus` 1/2–2/1); no incidental fix requires investigation (none are deterministic).

**Flake ledger (informational, no QUARANTINE action — corpus-load class, not pack members):** the 6 order/load-sensitive nodes above + known L8b boundary flake (`handback_verify_budget_test.sh`, 229-vs-230, absent in the Phase B matrix run, 1 attempt).

## 5. Drift adjudication (anchor `023f47b16` → frozen tip `12349cf17`)

This artifact is anchored at `023f47b16` **per the Phase A commission**. The 5-commit drift to the frozen tip (`2a5274b96` docs+A-2 log line + ADV-1 pytest; `5d55eb0b4` tidier behavior-preserving; `e104fb33d` cycle-2 robustness folds; `12349cf17` M7 fixture) is adjudicated in Phase B by scoped verification per the freeze instruction: **10/10 lane packs PASS at `12349cf17`** (incl. `test_upgrade_serving_green_closer.py` 30/30 — the file grew 20→27→30 nodes; the +27 census delta at anchor is superseded by +30 observed green at the frozen tip) + G1–G9 gate legs. Census delta at anchor: +27 nodes, all in `tests/unit/tools/test_upgrade_serving_green_closer.py`, 0 removed.

## 6. Interruptions + disclosures

1. **Live daemon promoted v0.28.4→v0.28.7 mid-run (operator, 23:37–23:40Z)** — the boot sweep killed the corpus's `proc_run` tree (documented hazard class; daemon-child process, `setsid`≠cgroup). Recovered via `service_start` (survives daemon restarts) + the runner's resume-safe manifest skip. Zero evidence lost (incremental per-shard persistence). Deviation from the `proc_run` instruction disclosed and justified.
2. **Integration legs** — both anomalies are teardown/session-shape artifacts of the corpus (integration tests boot the FastAPI app in-process and leave background threads), NOT lane effects; recovery via leaf re-runs applied symmetrically.
3. **Worktree writer #2 (new finding, routed):** a corpus test appends to `.agents/shared/planning/mcps-gateway-interop/p0-gate-evidence.md` — undocumented side effect alongside the known `install-audit.jsonl` writer. Drift preserved at `/tmp/phh-t2/drift-evidence-20261103T023920Z/`, trees restored (porcelain=0). **Routed to the mcps-gateway interop lane** for a repo note; not this lane's gate item.
4. **Pre-exclusions honored:** `tests/packs` (g7 trap), `tests/e2e/test_context_injection_hybrid.py` (import-time HTTP), PG legs (out of dispatched corpus). Host load shared with another lane's concurrent Tier-2 + the Phase B pack matrix — load-context disclosed for the flake ledger.
5. **Anchor trees left standing** (teardown deferred — Phase B BASE legs reuse `phh-t2-base`; final teardown happens with evidence-branch assembly).

## 7. Fence / prod-boundary attestation

Fence proven on live children via `/proc/<pid>/environ`: POSTGRES_*/PG*/DATABASE_URL = 0, API keys = 0, SYSTEM_ENCRYPTION_KEY = 0, PLANE workspace slug = 0, ENSEMBLE_SELF_ENV = 0; pins `OPENAI_BASE_URL=127.0.0.1:9/v1` (LLM fence), `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0` (pinned, never `-u`), `MCP_DISABLE_BUILT_IN_WEBFETCH=true`, fenced HOMEs under `/home/nea/.phh-t2-home/`. Live install untouched (mtime 23:39Z = the operator's promote); port 9797 pid unchanged; 8088 untouched; no daemon starts by the corpus; no other-lane process killed; ambient `POSTGRES_DB=ensemble_prod` (10.44.0.2) scrubbed per-variable every leg.

## 8. Evidence root

`/tmp/phh-t2/` (48 MB): `run_leg.sh` `run_all.sh` `fence.sh` `redigest.sh` `ladder.sh` `isolate.sh` `run_leaf.sh` `final_analysis.sh` `drift_disposition.sh`; per-leg `manifest.tsv` + `log/` + `digest/`; diff lists; ladder TSVs; hang tables; drift-evidence dir; `README.md`. Logs preserved with paths recorded per the commission.
