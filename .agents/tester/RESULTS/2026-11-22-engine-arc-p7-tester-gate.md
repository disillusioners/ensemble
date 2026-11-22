# Engine Arc Phase 1 — P7 Test Pack Buildout: Tester Gate Report

- **Date:** 2026-11-22
- **Lane:** `feature/engine-arc-phase1` · **Worktree:** `/home/nea/ensemble-src-wt-engine-arc-p1`
- **Registration/ceremony tip:** `4fbcf53695f657c63bc2bca89eae43efcf5d40a2`
- **TRUE family base (regression leg):** `1a74b0493` (post-drift-merge) · **lane pre-P1 base:** `b71742fe3`
- **Phase authority:** `.agents/shared/planning/engine-arc-phase1/{phase7-plan.md, test-strategy.md, plan-overview.md §PREDICATE LEDGER}`
- **Role:** Test Leader (dispatch-only; all execution delegated to workers)

---

## 0. Scope decision

The commission is the accumulated **P7 intake (16 items)** from every phase stage review — the whole intake was in scope, and it is bounded to the engine/event-registry family's own packs plus the family's touched-scope regression. The estate-wide suite was **not** run: the family's regression is Tier-2 (core seams `daemon/graph.py`, `daemon/manager.py`, `daemon/api.py`, `daemon/services/instance_messaging.py` are touched — mechanical escalation), and the Tier-2 recipe's **mechanics** (sharding, FAILED-digest comparison, 3× isolation on diff nodes) were applied to the **bounded scope** the intake names (registered packs + the family's touched test scope) rather than the recipe's default whole-tree scope. This reduction is deliberate and is reported here per the scope-decision rule.

---

## 1. Per-item status (16 items)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | **W-2** — F2/F3/F4 engine-level behavioral pins | ✅ DONE | `c87c020cd` — 5 pins: F4 echo-revival (`dead=0` + version/`updated_at` untouched) + `revive_job` repo leg + unknown-id leg; F2 cancel-path cache refresh (`spec` + `persisted_dead`); F3 latch owned by the engine cache, registry writes none. Jobspec file 84→87, supervisor file 7→9. |
| 2 | **W-1** — SLO-3 derivation footer | ✅ DONE (companion-evidence closure) | `a9329c79c` → `RESULTS/2026-11-22-engine-arc-p7-w1-slo3-derivation-companion.md`. Finding: `23.966s` exists **only** in the subject of `82aa36862`; the transcript has **no host-frame start anchor**, so the figure is NOT derivable — 4 derivable deltas tabulated (19 s / 12 s / ≈8.41 s DB-frame telemetry-only / 28 s non-readiness), none equal to 23.966 s. |
| 3 | **S-2** — watchdog re-arm never re-arms F1-escalated rows | ✅ DONE | `934268d37` — 3-node `TestDirectDbRearmPredicate` over the real `make_direct_db_rearm` (`watchdog.py:63`): born-dead/no-stamp ⇒ re-armed; F1-escalated (`dead=1` + `last_outcome="SKIPPED_DEP_MISSING"`) ⇒ NOT re-armed (documented as INTENTIONAL at the pin); alive ⇒ untouched. Mutation: removing the `last_outcome` conjunct REDdens exactly the escalated leg. |
| 4 | **Registration batch** — 7 engine packs, current counts | ✅ DONE | `4fbcf5369` — 8 registrations (7 packs + the census leg) under `<!-- engine-arc-phase1-packs-registration-20261122-begin/end -->`; counts re-baselined mechanically at the freeze (**145/40/40/26/21/93/65**, census 22, AC10 gate 411). Plan counts had drifted (substrate 138→145). |
| 5 | **QUARANTINE ledger corrections** | ✅ DONE | `7be03a956a` — 1 new row (child_report ×3, kwarg-drift **processing-seam**), 1 dispute amendment (row 110), 1 documentary row (confinement enlargement). Adjudicated against standalone evidence — see §5. |
| 6 | **Function-scoped `create_event` pin** (GAP-15) | ✅ DONE | `5b97654d1` — `test_create_event_function_source_is_pinned_gap15`: sha256 of `ast.dump(create_event)` with the normalization named; sits side-by-side with the retained coupling-absence pin. Mutations: body edit ⇒ RED; **sibling `create_error_event` edit ⇒ GREEN (no sibling false-RED)**. |
| 7 | **Per-site census pins** | ✅ DONE | `5b97654d1` — 11 (file, kind)→count pins: room_delivery 4/2/1, message_processing_errors 2, job_recovery_service 1, job_feedback_observer 1, midflight_qa 1×4. Deleting one `room_tag_delivery` site ⇒ RED. |
| 8 | **Two regex tightenings** | ✅ DONE | `5b97654d1` — (a) exact-alias: `_registry_importing_files` now resolves canonical-vs-non-canonical aliases (absolute **and relative** import shapes — 4 of 15 bridge importers are relative-form; old-vs-new detector set equivalence proved), + a bypass pin; (b) D5.11: the registry-package import scan now catches `from daemon.services import engine` (the form the old legs missed). |
| 9 | **D5.4 second live source** | ✅ DONE (pin-side) | `5b97654d1` — dispatch fence: the implemented live-source set is locked to exactly `{"event_bus"}` (behavioral probe + catalog-declaration leg); a planted fake second branch ⇒ RED. **The second source's IMPLEMENTATION is production work outside P7's test-only scope** — named as the deliberate re-point trigger (see §10 Q3). |
| 10 | **S-3** — `TestZeroExecution` false-RED in `.git`-less clones | ✅ DONE | `c87c020cd` — `git ls-files` rc=128 ("not a git repository") now falls back to a sorted `rglob` walk; other failures keep the hard assert. Proof: before-fix RED (2 tests) / after-fix GREEN (3) inside a `git archive` extraction — the F8 disposable-clone mode is now safe. |
| 11 | **S-6** — router-manifest cage couples to ambient `ENSEMBLE_AUTH_*` | ✅ DONE | `aefe0f475` (script: 7-var auth + `POSTGRES_*` per-variable scrub on `test/packs/regression_integration_a_test.sh`) + `7be03a956a` (the registered `metrics_scope_cage_dual_side` row's invocation now carries the same scrub + a FENCE NOTE). Cage re-verified **12/12 PASS** under the scrub. |
| 12 | **FixtureModule relocation** | ✅ DONE | `aefe0f475` — moved verbatim from `daemon/services/engine/dualpath.py` into its sole consumer `tests/unit/services/test_engine_kill_switch_dualpath.py`; `__all__` entry removed; grep proves no other importer; kill-switch file 18/18 green, `TestZeroExecution` 3/3 green. |
| 13 | **§PREDICATE LEDGER attestation** | ✅ DONE — **2 DRIFTS (routed to planner)** | See §3. CS-1..CS-8 verbatim MATCH at every governing site; all 19 DELETION RECORD rows legal (12 ABSENT, 7 BRACKETED-RETAINED). CS-9 verbatim absent at 2 of 5 sites. |
| 14 | **Ceremony — clean-checkout pack re-runs** | ✅ DONE — 8/8 PASS | See §4. Every leg run in a sha-pinned clean checkout at `4fbcf5369` with tree-identity asserted (the shared venv's editable `.pth` did **not** win the import). |
| 15 | **Mutation recipes from disposable clones only** | ✅ DONE | Every mutation proof ran in a `git archive` extraction (`/tmp/p7w2_*`, `/tmp/p7pins_*`, `/tmp/p7s2_*`) — the live worktree was never mutated; extracts discarded after each proof. |
| 16 | **Family regression vs TRUE base `1a74b0493`** | ✅ COMPLETE after one fix cycle | Leg 1 (scoped 557): NET-NEW ZERO. Leg 2 (whole-tree 40,181/leg): **1 net-new candidate** — a genuine fence violation (`projection.py:56` docstring naming the banned `action_by`/`acted_at` tokens) — **fixed family-side** (docstring reword; the audit NOT relaxed), 3× re-verified green; final net-new ZERO. Conftest blast-radius: no effect outside the family. See §7. |

---

## 2. Registered pack table (ceremony tip `4fbcf5369`)

| Pack / leg | Invocation | Collected | Ceremony result |
|---|---|---|---|
| `engine_substrate_unit_test` | `cd $WT && timeout 300 bash test/packs/engine_substrate_unit_test.sh` | 145 (+ AC10 gate 411) | ✅ PASS 145/145 |
| `engine_execution_unit_test` | `cd $WT && timeout 300 bash test/packs/engine_execution_unit_test.sh` | 40 | ✅ PASS 40/40 |
| `engine_guardrails_unit_test` | `cd $WT && timeout 300 bash test/packs/engine_guardrails_unit_test.sh` | 40 | ✅ PASS 40/40 |
| `engine_boot_unit_test` | `cd $WT && timeout 300 bash test/packs/engine_boot_unit_test.sh` | 26 | ✅ PASS 26/26 |
| `engine_boot_slo_gate` | `cd $WT && timeout 300 bash test/packs/engine_boot_slo_gate.sh` | 21 | ✅ PASS 21/21 (method wiring only) |
| `engine_observability_unit_test` | `cd $WT && timeout 300 bash test/packs/engine_observability_unit_test.sh` | 93 | ✅ PASS 93/93 (confinement pin GREEN) |
| `event_registry_unit_test` | `cd $WT && timeout 300 bash test/packs/event_registry_unit_test.sh` | 65 | ✅ PASS 65/65 |
| `engine_census_unit_test` (NEW) | `cd $WT && timeout 300 env -u POSTGRES_* -u ENSEMBLE_AUTH_* … .venv/bin/python -m pytest tests/unit/services/event_registry/test_bridges.py -q --tb=short -rfE -p no:randomly --timeout=120` | 22 | ✅ PASS 22/22 |

**Not registered (disclosed):** the three cross-cutting legs of `phase7-plan.md` §4.1 #8–#10 — `engine_dormant_posture_test`, `engine_diff_audit_test`, `engine_mutation_matrix` — have **no authored script anywhere in the tree** (preflight-verified across `test/packs/`, `tests/packs/`, whole tree). Their substance is partially carried elsewhere (dormant posture ⇒ the boot pack's wiring-posture + shadow-row pins; diff audit ⇒ the daemon-diff confinement pin inside the observability pack; mutation recipes ⇒ `test/packs/engine_c1_mutation_recipe.sh`). **AC1 is therefore PARTIAL (8 of 10).** See §10 Q1.

---

## 3. Attestation verdict (§PREDICATE LEDGER, item 13)

**Verdict: DRIFT — 2 findings** (CS-1..CS-8 verbatim MATCH at every governing site; the entire DELETION RECORD is clean).

**Drift list (routed to the planner — planning docs were NOT edited):**

| # | CS | Site | Finding |
|---|---|---|---|
| D-1 | CS-9 | `phase2-plan.md:42` | Canonical string absent verbatim. Body carries: "for specs with `execute_fn` withheld, dead ⇔ zero `SKIPPED_SHADOW` rows in `2 × effective_interval` (the observable execution-substitute) — the zero-runs definition alone would hold every observer spec dead=true forever" — backticked variants + `2 × effective_interval` instead of `2× interval`; the "D3.4 recovery is N/A for observers…" clause is absent from this site. |
| D-2 | CS-9 | `phase3-plan.md:36` | Canonical string absent verbatim. Body carries the recovery clause embedded in a longer paraphrase ("recovery is N/A for observers (no run exists to recover) — an execute_fn-withheld spec never enters the recovery ladder; …") but not the "dead ⇔ zero SKIPPED_SHADOW rows in 2× interval (the observable execution-substitute)" clause. |

CS-9's canonical source site — `decisions.md:146` — **MATCHES** verbatim, as do `phase2-pilot-recommendation.md:80` and `plan-overview.md:134`. Body-wins discipline ⇒ this is a ledger-vs-body wording divergence at two body sites: either the body adopts the canonical form, or the ledger's canonical string is re-quoted from those sites. **Routed to the leader/planner; no planning file was touched by P7.**

DELETION RECORD: 12 rows ABSENT, 7 rows BRACKETED-RETAINED (each quoted in the worker's matrix) — no LIVE-DRIFT. `architecture-recommendation.md`'s three `registrations=0` occurrences are ruling-record quotations only (§PREDICATE LEDGER ruling-record note). Ledger section verified at `plan-overview.md:118-246` with subsections (i) 123 / (ii) 137 / (iii) 167; closing ATTESTATION = 68 entries.

---

## 4. Ceremony evidence (item 14)

Every leg: **sha-pinned clean checkout** at `4fbcf5369` (`git worktree add --detach`), pre-run `status --porcelain` empty, `rev-parse HEAD` == tip, **tree-identity asserted** (`daemon.__file__` under the checkout — the shared venv's editable `.pth` did not win), transcript captured, worktree removed, `$WT` untouched. Post-run dirt was `?? .venv` only (the procedure's own symlink).

| Leg | Result | Nodes | Wall | Transcript |
|---|---|---|---|---|
| engine_substrate_unit_test | ✅ PASS | 145/145 + AC10 411 | 31.8 s | `raw/2026-11-22-engine-arc-p7-ceremony/p7cer_substrate_ceremony.txt` |
| engine_execution_unit_test | ✅ PASS | 40/40 | 13 s | `…/p7cer_execution_w1.txt` |
| engine_guardrails_unit_test | ✅ PASS | 40/40 | 23.84 s | `…/p7cer_guardrails_2570093.txt` |
| engine_boot_unit_test | ✅ PASS | 26/26 | 2.40 s | `…/p7cer_boot_2567591.txt` |
| engine_boot_slo_gate | ✅ PASS | 21/21 | 7.79 s | `…/p7cer_slo_2567484.txt` |
| engine_observability_unit_test | ✅ PASS | 93/93 | 7.85 s | `…/p7cer_observability_2567882.txt` |
| event_registry_unit_test | ✅ PASS | 65/65 | 37 s | `…/p7cer_eventreg_2567328.txt` |
| engine_census_unit_test | ✅ PASS | 22/22 | 33 s | `…/p7cer_census_2569557.txt` |

**Totals: 8/8 legs PASS, 452 nodes, 0 failed, 0 errors, 0 deselected.** The only warning class is the pre-existing third-party `langchain_core` Pydantic-V1-on-Python-3.14 deprecation.

**Census ground truth recomputed from the clean checkout** (not re-derived from the lane): catalog kinds **30 = 23 bridged / 6 declared-silent / 1 deferred_v2**; `bridged ∩ BRIDGE_PENDING = ∅`; union == KIND_INDEX; **15 bridge-site files, 35 `record()` call sites**; per-site counts (room_tag_delivery 4, error 2, job_completed 2, room_proposal 2, attestation_epoch 5, mission_commission_transition 2, report_injection_obligation 2, + 1 each for the remaining 16 kinds).

---

## 5. QUARANTINE ledger corrections (item 5)

All three corrections were adjudicated against **standalone evidence** (POK rule: only a standalone run is admissible for pre-existing attribution), 2 runs each:

1. **NEW row** — `tests/unit/services/test_child_report_next_turn_execution.py::TestT7NextTurnExecution` ×3. Attribution: **kwarg-drift, PROCESSING seam** — `manager._process_message_with_tracking` (`daemon/manager.py:12140`, kw-only `dispatch_chain`, forwarded `:12212`) and `message_processing_pipeline.py:492` pass the kwarg, but `InstanceMessagingService._process_message_with_tracking` (`instance_messaging.py:3948`) never accepted it. Pre-existing: introduced by F11 `56bcaf67e` (2026-10-24), an **ancestor of the lane base**; lane-scoped log on the 3 files shows only `f31c43732` (6 P5-bridge lines, neither seam touched). Node-for-node identical to the 2026-10-29 whole-tree record. **The P6 intake label "enqueue-contract" was refined** — the enqueue surface carries `dispatch_chain` intact (`instance_messaging.py:3151`/`:3547`).
2. **DISPUTE amendment (row 110)** — `queue_routing` stays in the **MagicMock-await** family: the P6 re-attribution to kwarg-drift is **disproven** (2/2 standalone: the route dies at `await manager.command_dispatcher.dispatch` at `daemon/routers/messages.py:337` — the seam moved from the ledgered `:258`, Python 3.14 reworded the TypeError — BEFORE the enqueue assertion runs; `enqueue_message_job` signatures (service `:3137` ≡ manager `:12063`) carry `queue_id` intact). The stale `:258` anchor is refreshed.
3. **DOCUMENTARY row (new)** — `TestNoProducerInstrumentation` (4 nodes): the confinement pin's `PRE_EXISTING_DAEMON_OFFENDERS` was mechanically enlarged **12 → 65 (+53)** by the T6.3 census ceremony, group-attributed in-file (A=25 / B=14 / C=2 / D=12 — independently AST-counted at P7; the 65 = 12 pre-A + 53). The pin is **GREEN 2/2** — a latent pin + enlargement record, not an active red. *(The intake's "~19+" figure is stale; the real figure is +53.)*

---

## 6. Commits (lane, unpushed)

| Commit | Subject |
|---|---|
| `b6887c244` | `test(packs): complete F-21 fence preamble on engine pack wrappers` |
| `aefe0f475` | `test: S-6 auth-family scrub on router-manifest cage runner + FixtureModule relocation out of production dualpath` |
| `c87c020cd` | `test(engine): F2/F3/F4 behavioral pins (W-2) + S-3 gitless-clone fix for TestZeroExecution` |
| `934268d37` | `test(engine): S-2 watchdog rearm-predicate pin — F1-escalated exclusion documented as intentional` |
| `a9329c79c` | `docs(tester): W-1 SLO-3 derivation companion evidence (23.966s not derivable from transcript)` |
| `5b97654d1` | `test(event-registry): function-scoped create_event pin (GAP-15), per-site census counts, exact-alias + D5.11 from-import tightenings, D5.4 dispatch pin` |
| `7be03a956a` | `docs(tester): P7 ledger corrections — QUARANTINE kwarg-drift row + queue_routing dispute amendment + confinement enlargement record; S-6 fence amendment on metrics_scope_cage row` |
| `4fbcf5369` | `docs(tester): register engine-arc P1 engine/event-registry unit packs + census leg (8 registrations, mechanical counts)` |
| `e9c79e558` | `docs(tester): P7 tester gate report + ceremony clean-checkout evidence (8/8 legs PASS, attestation 2 drifts, scoped regression net-new ZERO)` |
| `625b808ff` | `fix(engine): P7 regression closure — projection docstring no longer names the dormant resource-monitor columns (resource-control-seam _NAME_RE fence)` |

No push was performed. `daemon/` was touched by exactly ONE commit (`aefe0f475`, the authorized FixtureModule relocation: class + one `__all__` entry removed) and by no other P7 change — every other P7 edit is test code or tester documentation.

---

## 7. Family regression (item 16)

**Method (Tier-2, recipe-driven).** `whole-tree-base-proven-diff` v1.0.1 + `failed-digest-equality-gate` v1.0.3 (project-file recipes, read by deterministic path). Both legs are clean clones with tree-identity asserted (`daemon.__file__` under the clone on BASE and HEAD); identical command bytes except CWD + shard file; per-variable env scrub (POSTGRES_* **scrubbed, not re-set** — the clones carry no `.env` and no `data/ensemble.json`, so the prod poison-vector is structurally absent) + the recipe's LLM fence (`OPENAI_BASE_URL=http://127.0.0.1:9/v1`, `OPENAI_REQUEST_TIMEOUT=5`, `QUEUE_LLM_RETRY_*=1` — the ambient shell carried a live provider URL + key and `ENSEMBLE_SELF_ENV=live`); `ENSEMBLE_BOOT_SWEEP_STALE_SUBPROCESSES=0`, `ENSEMBLE_SELF_ENV=dev`, `PYTHONDONTWRITEBYTECODE=1`; pytest `-p no:cacheprovider -p no:randomly -q --tb=line -rEf` over explicit node-id lists (no testpaths fallback). Serial (not xdist) to avoid the shard-composition red-set artefact the recipe documents. No daemon booted; ports 8079/8088 never touched; no live-install contact; no repo write.

**Scope (mechanically derived, not taken on trust).** Union of (a) the 8 registered packs' 45 pytest targets + the census file and (b) the family's touched test scope (`git diff --name-only 1a74b0493..HEAD -- tests/ test/` = 59 paths) ⇒ **60 paths**, split by base-existence: **31 BASE-EXISTING** (dual-side comparable — incl. `tests/conftest.py`, whose 2-line family edit is session-wide), **20 HEAD-ONLY** (no base counterpart), **9 shell wrappers** (not pytest nodes). Two reductions, both disclosed: the `.sh` wrappers were NOT executed (they resolve a bare `.venv/bin/pytest` whose `sys.path[0]` would make the editable `.pth` win → they'd measure the worktree, not the clone — the exact failure the tree-identity clause exists to catch; their test-bearing targets were run directly), and HEAD-ONLY files are excluded from the BASE leg (absent at base ⇒ `rc=4`, no comparable node ids).

**Leg 1 — SCOPED (557 nodes): COMPLETE, NET-NEW = ZERO.**

| Leg | Collected | Passed | Failed | Errors | Skipped | Unique red ids | Wall |
|---|---|---|---|---|---|---|---|
| BASE `1a74b0493` | 557 | 365 | 33 | 28 | 32 | 61 | 402 s |
| HEAD `4fbcf5369` | 557 | 367 | 31 | 28 | 32 | 59 | 402 s |
| HEAD-ONLY @ HEAD | 359 | 359 | 0 | 0 | 0 | 0 | 92 s |

Accounting closure: shard sums == collected per leg (139+139+140+139 = 557 both legs; 179+180 = 359). Collection slicing proof: 557 collected, **zero deselected**, node-id sets **byte-identical** between legs (`cmp` OK) — the comparison is symmetric. The two-form comparator (`.agents/tester/skills/whole-tree-base-proven-diff/sweep_digest_verdict.sh`, LC_ALL=C pinned) exited **0** and was **non-vacuous** (both digests non-empty): GREP-FULL base `617d80c1…` vs head `73ff9bbe…`; BARE-ID base `2b308312…` vs head `add568aa…` — **base-only 2, head-only (NET-NEW) 0 in BOTH forms**. The two-form discipline proved load-bearing: GREP-FULL carries 28 daemon LOG lines per leg (27× migration `near "EXISTS": syntax error` + 1× memoir-knob unreadable-tier) — symmetric, correctly excluded from BARE-ID.

**The 2 diff nodes are BASE-only reds the family REPAIRED** (deterministic: 3/3 identical each side, not flakes):

| Node | Lane reach | Classification |
|---|---|---|
| `tests/test_settings_resolution.py::TestManagedKeysConsistency::test_every_entry_is_consistent` | non-empty diff (+10/−1) | **Lane-FIXED pre-existing stale pin** — BASE signature `AssertionError: assert 22 == 19` (the literal was stale: the tool-usage-metrics lane added 3 managed keys before the base); the family re-baselined 19 → 22. |
| `tests/unit/metrics/test_metrics_family_adjudication.py::TestNoProducerInstrumentation::test_the_daemon_diff_is_confined_to_the_allowed_files` | non-empty diff (+108) | **Lane-FIXED pre-existing stale pin** — BASE signature: 12 drift-merged foreign-lane paths "is a NEW offender"; the family's T6.3 ceremony recorded them (12 → 65). Green at HEAD, and covered by QUARANTINE row 159 (documentary). |

Neither is a net-new candidate: the family **reduced** the red set by 2.

**Leg 2 — WHOLE-TREE (recipe default scope): COMPLETE. 40,181 nodes per leg, 1 net-new candidate found and FIXED.**

Scope: the whole `tests/` tree with the ensure.md pre-exclusion registry (each with its named compensating pin): `tests/packs/` (g7 `sys.exit(0)` collection trap — packs run via registered scripts), `tests/e2e/test_context_injection_hybrid.py` (module-import HTTP GET to a live `:8079` — pinned by the Release Gate's `e2e_workflows_ensure_test`), `tests/performance/test_message_api_cost.py` (>300 s solo — pinned by `regression_performance`). No other exclusion. Collection: BASE 40,181 node ids (41,459 items − 1,278 marker-deselected), HEAD 40,540; the BASE node set is a strict subset of HEAD (base-only 0, head-only 359 = the family's new files); the comparative run used the **same 40,181 ids on both legs** — symmetric inputs.

Projection (measured-rate guard): probes serial/`-n 8`/`-n 14`/unit-heavy ⇒ pre-run projection 42.9 min/leg (advisory breach, reported not stopped, per the recipe); shard-0 re-projection 24.1 min/leg ⇒ no breach survived; actual 29.4 + 29.8 min (66 % of the 90-min budget). Parallelism `-n 14` with `--junitxml` per shard (crash-resilient capture); accounting closure per leg (8/8 shards, sums == collected, no shard death).

| Leg | Collected | Passed | Failed | Errors | Skipped | Unique red ids | Wall |
|---|---|---|---|---|---|---|---|
| BASE `1a74b0493` | 40,181 | 39,133 | 431 | 28 | 403 | 567 | 29.4 min |
| HEAD `4fbcf5369` | 40,181 | 39,111 | 432 | 28 | 403 | 570 | 29.8 min |

Two-form comparator (LC_ALL=C pinned): GREP-FULL base-only 16 / head-only 19; **BARE-ID (canonical) base-only 10 / head-only 12**. The 7-line GREP-FULL gap is daemon LOG noise (6× symmetric `ScriptedChatModel exhausted` + 1× asyncio stray), not test failures — the form-tag trap the recipe warns about. All 22 diff node ids adjudicated on the 3× isolated sequential ladder per side:

- **BASE-only (10):** 2 lane-FIXED stale pins (the settings-resolution MANAGED_KEYS 19→22 re-baseline and the confinement-pin 12→65 offender enlargement — both already classified in Leg 1); 3 pre-existing reds transitively fixed at HEAD (prompt_version_resolution sqlite insert-retry, llm_allowed_models ×2 — empty lane diffs, isolated 3/3 green at HEAD); 1 known pre-existing flake family (atomic_status, row 29); 4 composition flakes (mixed 3×, all with standing QUARANTINE rows).
- **HEAD-only (12):** 1 **net-new candidate** (below); 1 known context-flake (atomic_dequeue, row 57); **10 composition artifacts** — 3/3 isolated PASS at HEAD, the documented shard-composition class (rows 62/156: "the lane's added test files re-balanced the greedy shard split" — exactly what happened; the row explicitly anticipates this noise).

**🔴 The 1 net-new candidate — `tests/test_resource_control_seam.py::TestDormancyWriteSiteAudit::test_no_daemon_file_names_action_by_acted_at`: deterministic (3/3 FAIL isolated at HEAD, 3/3 PASS isolated at BASE), empty test-file diff, and the trigger is the family's own `daemon/services/engine/projection.py:56` — a DOCSTRING naming the dormant `action_by`/`acted_at` columns.** This violates the documented HARD fence (`test-strategy.md` §7 `_NAME_RE` dormancy fence: only `daemon/models/resource_monitor.py` may name those columns; "Engine code/tests must not introduce these names"). The production code does not functionally use the columns — the prose alone trips the token-level audit. **Adjudication: family defect, fixed family-side** (docstring reworded to stop naming the banned tokens; the audit test is NOT relaxed — negative-guard fences are never weakened). Fix + 3× re-verification recorded below.

**Conftest blast-radius answer (the reason this leg was authorized): NO effect outside the engine family.** The session-wide `tests/conftest.py` change (removal of `checkpoint_interval: 1` and `config.services.worker_poll_interval = 0.5`) is the fixture half of the family's retired-knob removal; the static reader set is exactly ONE head-only family file (`test_engine_config_deadknobs.py`), and the 40,181-node empirical run attributes zero net-new failures to it. The single net-new candidate is triggered by `projection.py`'s docstring, not the conftest edit.

**Census/count-pin caveat (recorded):** 37 tree files read git history; 9 red in this run — 8 symmetric pre-existing (identical BASE/HEAD counts) + 1 lane-FIXED (the adjudication pin). The three HEAD-sensitive census pins (the daemon-diff confinement pin, the MANAGED_KEYS count, and the resource-control-seam write-site audit) answer differently depending on the checked-out commit — logged as future work (§9 Q6).

**Fix record (the one fix cycle of this gate):** commit `625b808ff` — `daemon/services/engine/projection.py` docstring reworded (3 lines, zero logic change; the F-3/F-11 fence-honored note now reads "no dormant resource-monitor column dims" instead of naming the banned columns). Proof: `grep -nE '\b(action_by|acted_at)\b' daemon/services/engine/projection.py` → ZERO hits, re-proved on the committed blob (`git show HEAD:… | grep -c` → 0; the audit's `rglob("*.py")` scope confirmed clean — the two `.sql` migration hits are outside it). Re-verification: `tests/test_resource_control_seam.py` **3/3 runs × 32/32 passed** (20.9/20.2/20.7 s, full env scrub, `-p no:randomly`) — including the audit node and the `:294-346` fence itself; and the P6 projection pack `tests/unit/services/engine/test_projection.py` **23/23 passed** (2.73 s), proving the docstring change disturbed nothing. **Final item-16 verdict: NET-NEW = ZERO** (scoped leg zero; whole-tree leg's single candidate fixed and re-verified; the audit test itself untouched — negative-guard fences are never weakened).

**Answers to the leg's own questions (adjudicated):** (1) reword the docstring, do NOT relax the audit — executed (`625b808ff`); (2) blocking YES under item 16's zero-net-new bar — fixed; (3) no QUARANTINE row changes for the two lane-FIXED pins (both green at HEAD; row 159 remains the documentary enlargement record); (4) of the 4 flakes, three are covered by standing rows; the chart-T3 node got a new consolidated row (committed with this report).

**Standing interpretability caveat (recorded for future gates).** Both diff nodes are *census/count* pins, not behaviour pins: `test_the_daemon_diff_is_confined_to_the_allowed_files` computes `git diff <LANE_BASE>..HEAD`, so its A/B outcome depends on **which commit is checked out**, not only on tree contents — a red in that class means "the pin's allowlist is stale for this commit range", never "the code regressed". That is why the ladder, not the sweep delta, decides. Logged as future work (Q6).

**Answers to the leg's own questions (adjudicated by the tester):** (1) whole-tree → **AUTHORIZED** (above); (2) the two re-baselined pins are now HEAD-sensitive → logged as future work Q6 (a test-debt commission to re-anchor them to a range-independent form is NOT in P7's scope); (3) "should the HEAD-ONLY corpus be pinned into a registered pack?" → **NO — not needed**: every one of the 20 HEAD-ONLY paths is already inside a registered pack's scope (substrate 3 + execution 3 + guardrails 3 + boot 3 + observability 2 + event_registry 6 = 20), and the ceremony ran them all green. A single "engine family" corpus invocation would be a convenience, not a coverage gap.

---

## 8. Worktree state

`/home/nea/ensemble-src-wt-engine-arc-p1` · branch `feature/engine-arc-phase1` · tip `625b808ff` (the fence-closure fix; the ceremony evidence was captured at `4fbcf5369`, the registration tip) · working tree clean after the final ledger/report commit · no push at any point · no live-install contact · no daemon boot · no port bound (port 8088 untouched throughout). `daemon/` was touched by exactly TWO commits, both mission-directed single-purpose edits: `aefe0f475` (FixtureModule relocation) and `625b808ff` (the docstring fence closure) — every other P7 change is test code or tester documentation.

---

## 9. Open questions for the leader

1. **AC1 is PARTIAL — the three cross-cutting legs are unauthored.** `engine_dormant_posture_test` (Leg D, release-blocking dormant-posture), `engine_diff_audit_test` (Leg E, release-blocking daemon-diff ownership-map), `engine_mutation_matrix` (Leg F, ≈34 mutations) have **no script in the tree**. The mission's registration batch named only the 7 unit packs, so P7 registered those (+ the census leg) and disclosed the gap in the PACKS.md block — but a release-gate reader should know Legs D/E/F are **not** independently gated by a registered pack, and their substance currently rides inside the boot/observability packs and the C-1 recipe. **Decision needed:** authorize a follow-up commission to author + register the three standalone legs (my recommendation: yes, before P8), or accept the coverage-as-carried.
2. **CS-9 attestation drift (D-1, D-2)** — body-vs-ledger wording divergence at `phase2-plan.md:42` and `phase3-plan.md:36`; routed to the planner per the P7 mandate (planning docs untouched). Body-wins ⇒ the planner decides whether the body adopts the canonical string or the ledger is re-quoted.
3. **The P6 intake's `queue_routing` re-attribution was disproven** (§5 item 2) — the node remains MagicMock-await (the seam moved `:258` → `:337`). If the P6 review's claim was based on something I could not see, the leader may want the P6 reviewer to re-check; otherwise the ledger now carries the standing attribution plus the dispute evidence.
4. **D5.4's second live source** — P7 delivered the **pin-side** fence (the implemented dispatch set is locked to `{"event_bus"}`); the **implementation** of the second source is production work outside P7's test-only discipline. Named as the deliberate re-point trigger in the pin. Confirm the implement-side owner.
5. **SLO-3's rehearsal figure is still unestablished** (item 2 closed by companion evidence, not by a derivation). A compliant rehearsal needs a host-frame start anchor captured (`ActiveEnterTimestamp` / launcher `starting:` line) — the next rehearsal must capture it, or the SLO-3 gate row must be restated as method-only.
6. **Two re-baselined pins are now HEAD-sensitive (test debt, not a regression).** `test_the_daemon_diff_is_confined_to_the_allowed_files` (computes `git diff <LANE_BASE>..HEAD`) and `test_every_entry_is_consistent` (a managed-keys count) answer differently depending on which commit is checked out — the family re-baselined both, and both are green at HEAD. Any future gate re-running this scope at a different tip will see a different answer for the first. **Recommendation:** a small test-debt commission re-anchors them to a range-independent form (e.g. assert the offender-set size against a recorded baseline rather than a live diff). Out of P7's scope (P7's mandate was the pin-strength batch, not a debt refactor).

---

## 10. Gaps / incomplete

- **Item 16 whole-tree leg: COMPLETE** — 1 net-new candidate (the `projection.py:56` docstring fence violation) found, fixed family-side, and 3× re-verified; final net-new ZERO. The scoped 557-node leg also stands (net-new ZERO).
- **The three unauthed cross-cutting packs** (§2, Q1) — a scope gap in the family, not a P7 failure.
- The first attestation worker (`67eb22d6`) **wedged** mid-run — its last tool call was 01:23:57 and it produced no report (verified via `subtree_messages`: tool-call turns only, no report); it held no partial output worth keeping, so after the escape valve's single re-dispatch delivered the complete matrix (§3) it was **TERMINATED** (watchdog hang notice: 1h4m idle). Item 13 is fully evidenced by the replacement's matrix; no duplicate or conflicting attestation exists.
- **Pack wrappers were not executed inside the regression leg** (their bare-`.venv/bin/pytest` invocation would measure the worktree instead of the clone). The registered invocations WERE executed in the ceremony (§4, 8/8 PASS with tree-identity asserted) — so wrapper correctness is evidenced by the ceremony, and the regression leg's contribution is the file-target comparison.
- **One daemon-file fix landed post-ceremony** (the `projection.py:56` docstring reword, item 16) — the ceremony evidence was captured at `4fbcf5369`, one commit before the fix; the fix touches NO test file and NO pack scope (its verification is the 3× standalone fence run + the projection pack re-run recorded in §7).
