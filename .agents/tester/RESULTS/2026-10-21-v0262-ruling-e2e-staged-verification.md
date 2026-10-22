# Staged Verification — v0.26.2 (ari-tools post-ruling remediation + delivery mechanism)

**Commission:** ruling-e2e — independent verification of the user-ruling remediation (merged `--no-ff` @ `33e1ef513`, now `origin/latest`^) and the re-staged v0.26.2
**Date:** 2026-10-21 (UTC, ~04:49–09:3x)
**Staged artifact:** `/home/nea/agents-ensemble/releases/v0.26.2` (staged_at 2026-10-21T04:24:35Z)
**Build tree:** `33e1ef513f3504b4c5edf11b085087cf977029c3` (tag `v0.26.2` = annotated `b7b949a4a3d85dbc…` → `33e1ef513`, LOCAL-ONLY)
**Binary sha256 (3-way):** `4e5ab92998e3e600153427f781857725826526ec20724f1a3f93d3106b7dcc87` (disk == manifest == sidecar, recomputed)
**Mode:** read-only + sanctioned scratch only (per-lane fresh scratch PG role+DB; isolated boot smoke under its own systemd transient unit). 9 workers + 1 harvest + 1 base spot-check, 0 promotes, 0 production-code changes. NO promote performed — the user 3-factor nonce ceremony owns it.

^ At verify start `origin/latest == 33e1ef513`; during the run the leader merged the earlier question-hide evidence branch → `4c888fbbb` (docs-only). GA pinned the drift as benign (see §GA item 8).

---

## VERDICT: 🟢 **STAGED-VERIFIED (PASS) — with ONE LOW doc-only defect (D-V2) and one disclosed suite-scope gap (GT-b base-diff infeasible)**

| # | Mission item | Verdict | Lane |
|---|---|---|---|
| V1 | Ruling #1 — ari-only allow-lists via REAL resolution path | ✅ **CONFIRMED** (6/6) | `654ee291` skill `privileged-tool-gate-real-service-verification` |
| V2 | Ruling #2 — wake-the-caller rows + zero fresh-instance text | ✅ behavior CONFIRMED (items 1-3) · ❌ item 4 text sweep = **defect D-V2 (LOW)** | `b51775f0` skill `real-engine-real-pg-tool-verification` |
| V3 | Ruling #3 — delivery mechanism + engine narrowing + census | ✅ **CONFIRMED** (4/4 + census) | `c76960bb` skill `integration-test` + census (tester RO-DB + `8181cf36` GET-only) |
| GA | Artifact/trees/tip-identity | ✅ PASS 9/9 | `0c22b07a` skill `independent-staged-release-integrity-verification` |
| GB | Isolated boot smoke | ✅ PASS 8/8 | `b9fa5eb2` skill `staged-binary-boot-smoke` |
| GS | Seal gates | ✅ PASS 3/3 | `3af9dda3` skill `test-pack-execution` |
| GT-a | Scoped post-ruling corpus + integration pin | ✅ PASS (410/412; 2 base-proven; net-new ∅) | `fb3c904b` skill `test-pack-execution` |
| GT-b | Whole-tree + base-diff | ⚠️ **PARTIAL-CLOSED** — unfiltered HEAD census harvested (28,933 nodes); ruling-adjacent red base-proven pre-existing; 7 timeout-ids = load contamination (base + head-clean double-proof); full 297-red base-diff NOT run (orphaned driver completing in /tmp) | `70e6fb44`+`a0dd66c4` (wedged, terminated) + harvest `cc470e3f` + spot-check `26420388` + re-check `1e3d29d2` |
| W0 | Worktree bootstrap | ✅ 7/7 | `fb62ca35` |

**Promote-readiness:** staged v0.26.2 verified promote-ready on all independently-verified surfaces. `current → releases/v0.25.7` untouched; live daemon MainPID 2296104 continuous since 2026-10-20 06:48:44Z throughout.

---

## Ruling #1 — ari-only allow-lists (V1): CONFIRMED

1. **Constants** (from the tree at `33e1ef513`): `BOARD_AUTHORIZED_AGENTS` (board_tools.py:104), `VIEW_WRITE_AGENTS` (views_auth.py:67), `BOARD_LINK_AGENTS` (views_auth.py:77) each EXACTLY `frozenset({"ari"})`; sibling scalars `SELF_ALARM_AUTHORIZED_AGENT = "ari"` / `SELF_ALARM_TARGET_AGENT = "ari"` (self_alarm.py:115/:121). No other agent-set constant in the modules.
2. **`agents/leader/meta.json`**: 23 allow entries — NONE of the 7 tool names, NONE of the 4 categories; `view-views` (category granting `view_link`) PRESENT. Only `agents/ari/meta.json` carries the four categories (cross-tree grep).
3. **REAL resolution** (real `create_instance_tools` + `_apply_tool_filter`, real manager, scratch PG, real meta.json copies byte-identical): leader resolves 80 tools — `view_link` present, all 7 absent; ari resolves 82 — all 7 present (`board_create, board_update, board_list, board_get, self_alarm, view_write, board_link`). Resolved-set diff puts all 7 entirely on the ari side.
4. **Literal-tool-name bypass closed**: all 7 constructed directly with `agent_id="leader"` → typed refusal (never raise), **0 engine calls** (closure-cell spy-in-path proven per tool), **0 row deltas** across the 7 tables, gate-named error text. Two house error-envelope prefixes coexist by documented convention (`ERROR:` for board/self_alarm, `Error:` for view_write/board_link) — not a defect.
5. **Fail-closed**: `agent_id=""` and `None` both refuse identically (factories normalize `None→""`).
6. **Control arms (non-vacuity, all three engine families)**: ari happy-path through the same spies → real engine calls + real rows (board item; view_write/board_link passcodes + HTML; self_alarm schedule + mapping), all prefix-cleaned, scratch DB+role dropped with catalog counts 0/0.

## Ruling #2 — wake-the-caller (V2): behavior CONFIRMED; D-V2 (LOW) on the text sweep

- **Create path**: `self_alarm` via the real factory seam → `source_configs.config->>'instance_mode' = 'reuse_instance'` (written `scheduling_service.py:433`; supplied `self_alarm.py:866`), `instance_mappings.agent_instance_id` = the CALLER's id (mapping metadata `bound_to_caller: true, caller_instance_id: <caller>`).
- **Re-arm path**: `_bind_schedule_to_caller` @ `self_alarm.py:870`, call sites `:1033` (create) and `:1148` (re-arm). Same-caller re-arm keeps the binding (1 mapping row, no duplicates); **different-session re-arm re-points** at the new caller — the code-comment-anticipated case. Zero `instances` rows created.
- **Message**: verbatim fired payload carries label + linked board item id + `Action:` line + "firing into THIS session" framing (composition @ `self_alarm_service.py:603`, THIS-session line `:648-651`). The engine's generic `[CONTINUATION - Run #1]` wrapper rides OUTSIDE the alarm text (disclosed, generic engine framing).
- **❌ D-V2 (LOW, doc-only)**: 3 stale pre-ruling text locations in `daemon/tools/self_alarm.py` — `:483-484` (`link_board_item` Field description "the fresh instance is told to board_get it"), `:528-529` (signature copy), `:558-560` (`_full_doc_`: "hands a SELF-CONTAINED message to a **FRESH Ari instance** — an instance with no memory of this request"). Agent-visible via `tool_help("self_alarm")` + `args_schema`; lines 10-12 of the same runtime doc assert the OPPOSITE (internal contradiction). **Zero behavioral impact** (items 1-3 fully green); no test pins assert the stale text (the ruling pack pins message+mapping only — the gap that let this through). Suggested fix: reword to "THIS session"/"the woken session" + add a doc-text pin. Blast radius ≈3 text locations.

## Ruling #3 — delivery mechanism (V3 + census): CONFIRMED

- **Engine seam (code)**: exactly ONE `enqueue_message_job` on the scheduled-fire path (`adapters/scheduler.py:1116`; the `:1021` else-branch is the manual-trigger path). `source="scheduler"` → matches no internal prefix → `MessageType.HUMAN` (`instance_messaging.py:2102-2105`) → `role="user"` (`:2464`). Reuse branch cannot reach the spawn site: `force_new=False` → mapping found → `mapper.py:351` returns the mapped instance; spawn (`mapper.py:362`) sits past the fall-through. Messaging seam contains ZERO `Instance` inserts (3 `session.add` calls only: message/task/event rows).
- **Engine seam (behavioral, scratch PG, 26/26)**: ONE-SHOT and RECURRING both → exactly ONE `message_queue` row (`source='scheduler'`, `type='human'`, event `role:"user"`) into the MAPPED caller; ZERO `instances` Δ; every job row `job_type='message'` (no spawn shapes); recurring second fire `_run_counter` 1→2 (no collapse). Busy-skip guard (fire into still-RUNNING caller → SKIPPED) is correct-by-design, disclosed.
- **Narrowing**: the diff is ONE hunk adding a single boolean conjunct (`and instance_mode_str != "reuse_instance"`, adapters/scheduler.py:~163-180); `else` branch untouched. Real-row pins in 5 shapes: explicit-reuse one-shot → `reuse_instance` ✅; no-mode one-shot → `new_instance` (DEFAULT UNCHANGED) ✅; explicit-new one-shot → `new_instance` ✅; no-mode recurring → `new_instance` (DEFAULT UNCHANGED) ✅; explicit-reuse recurring → `reuse_instance` ✅. 180 corroborating tests green (`test_scheduler_instance_mode` 53, wake_semantics 7, self_alarm service 65 + tools 55).
- **Disclosed edge (pre-existing, outside diff)**: `mapper.py:336-344` — if the mapped caller row was deleted out-of-band, reuse falls through to spawn. Shared by all source modes; "NEVER spawns" is conditional on the caller row existing.
- **Live census (read-only)**: tester via `ensemble_prod_ro` — `instance_mappings` 23 rows, **0 carry any `instance_mode`**; worker via GET-only API on :9797 — **11 scheduler rows (10 stopped + 1 cancelled), every one `new_instance`, ZERO `reuse_instance`**. Count reconciliation: the commission's "14" = TOTAL source_configs (11 scheduler + 3 chat-platform); the scheduler subset is 11. The in-code comment's "14 rows total" repeats the same imprecision (cosmetic, comment-only). **Nothing in the reuse-instance surface can attach to any live schedule at promote.**

## GA — artifact / trees / tip-identity: PASS 9/9

Binary 3-way == expected; both sidecars `git_head=33e1ef513`/`git_dirty=false` (FE sidecar sha == fe_index `a017dd21…`); tag local-only (`ls-remote` 0 matches; peeled `b7b949a4a→33e1ef513`); manifest fields exact (v0.26.2/0.26.2/rollback_safe=true/staged_at/known_schema_gen board migration); full per-file re-hash **339+92+4930 = 5,361 keys, 0 mismatch/missing/extra**; root non-keyed set exact; in-payload seal 161 OK / 0 FAILED; tree hashes recomputed via the house algorithm — self-validated against v0.25.7's manifest 3/3 + git-blob materialization at both revs (v0.25.3 payload is retention-evicted; its published agents constant is stale-not-wrong, 9 files legitimately changed by v0.25.4-7); agents_tree `2990c41160cd58d5e8c9fdaf01a2dda64a94462b35ef587d19b27fac74dca866` CHANGED as claimed, frontend `8d6afeaa…`/plugins `4715cf64…` unchanged.
**Tip-identity pin (ceremony-critical):** `origin/latest` at `4c888fbbb` = exactly 2 changed paths vs `33e1ef513`, both `.agents/tester/**`; shipped subtree tree-objects (agents/frontend/plugins/daemon) IDENTICAL at both revs → **docs-only drift, promote staged artifact AS-IS** (a re-stage would be refused exit 78 `non-tip-tree`).
Migration registry: 106/106 distinct prefixes at BOTH worktree and tag level (the semantically correct 15-char `date_sequence` token; a 13-char truncation collides — measurement note); delta vs live v0.25.7 (`1ebdf469f`) = EXACTLY one `A` row (board migration) with `-M` and no-`-M` control, board blob `f7033377…` IDENTICAL @ 33e1ef513 and @ 14a7f3762. Zero writes (porcelain 25→25; releases/ and stage worktree untouched by `find -newermt`).

## GB — isolated boot smoke: PASS 8/8

Boot inside its OWN systemd transient unit (`/user.slice/.../ensemble-tv62smoke-a3d56a.service` — cgroup-proven vs live `/system.slice/ensemble-main.service`; the worker's own shell WAS inside the live cgroup, confirming the hazard was real and avoided). Both kill-switches asserted pre-exec. Port 19110 (skill-default 8079 forbidden by commission; range proven free). Banner `Starting Ensemble v0.26.2` ×2 (quoted); livez 200 `version 0.26.2`; readyz 200 `database+checkpoint_saver:true`; board tables via privilege-independent pg_class (scratch-role owned, 77 tables); baseline `161 versioned artifact(s)` at boot; ZERO forbidden patterns (42804/DatatypeMismatch/startup-failed/DuplicateMigrationVersionError); 7 ERROR lines all known-benign computer-use MCP. Teardown: 3-marker ownership proof pre-signal, SIGTERM-only (no SIGKILL), port released, DB+role dropped with both catalog counts 0, scratch removed under prefix guard, transient unit collected. Staged payload 0 modifications (sha re-verified). LIVE FENCE: :9797 livez 0.25.7, MainPID 2296104 NRestarts=0 epoch unchanged, `current → v0.25.7`, state.json sha unchanged. Two harness defects (worker-side, self-corrected, scratch re-provisioned) disclosed; skill defects filed (POSTGRES_URL DSN scheme; cgroup-isolation mandate).

## GS — seal gates: PASS 3/3

House baseline (authority script `scripts/protection/generate_prompt_artifact_baseline.py --check`): `PROMPT-BASELINE: OK — 161 versioned artifact(s)`. Tripwire: 35 pins / **0 stale / 0 missing** — independently recomputed, not just test-green; the single re-pinned row (`agents/ari/workflow.md` ← `4f07f3270…`, content-sha) matches the file. Second adjudicated re-pin provenance: lives IN THE TRIPWIRE FILE (`tests/test_chart_image_delivery_e2e.py:1402-1411`, verbatim block quoting the user ruling + "only this ONE row changed") + commit `f3378abdc` message — location differs from the brief's wording ("in workflow.md"); substantively the requirement holds. Chart audit: `46 passed, 0 deselected`.

## GT-a — scoped post-ruling corpus: PASS

Derived corpus = exactly the 12 test files changed in `463583800..33e1ef513` — **412 collected / 410 passed / 2 failed / 0 errors**; both reds (`test_api_router_extraction.py::test_http_methods_are_correct` [/api/schedules {GET} vs {GET,POST}], `::test_api_module_is_small` [4686<1600]) **base-proven at 463583800 with the identical command — FAILED-list diff EMPTY, net-new ∅**. Integration pin: `tests/integration/test_service_tool_flag_off_byte_identical.py` `-m integration` → **9/9 PASS** (the cited 9-node file; out-of-diff-range, run to satisfy the count). Its 13-node pair-mate shows only the documented inherited maintenancer `system_restart` 3-red (`ff27305c4`, pre-existing, `agents/maintenancer/` untouched by the ruling). Inversion spot-proof: leader-DENY is a set-intersection over all 7 ruled tools through the real path; `BOARD_AUTHORIZED_AGENTS`/`BOARD_LINK_AGENTS`/`VIEW_WRITE_AGENTS` each pinned `== frozenset({"ari"})` in 4 files; nothing skipped/weakened. The "~692" figure reconciled = the 26-file ari-tools surface (692/701 under default addopts), not the 12-file derived corpus (412) — no shortfall. Runner self-correction disclosed (parallel runner silently dropped 1/12 packs; re-ran sequentially with completeness assertion).

## GT-b — whole-tree + base-diff: PARTIAL (disclosed)

**Framing discovery (material):** `tests/` collects **~28,931 nodes** (tests/unit alone 17,858). The dev gate figure "1114P/2F" is a bounded dev-side selection, NOT the whole tree — the commission's "whole-tree 1114P/2F" expectation was unfalsifiable as stated. Both GT-b workers wedged (see §Incidents); the run itself survived on disk via incremental persistence and **COMPLETED the unfiltered HEAD leg**: 397 progress lines, all 367 shards + 36 recursive splits accounted — **28,933 node-slots → 28,290 P / 297 F / 25 E / 8 timeout-class / 2 legacy-form ids unsharded** (harvested read-only; 429 shard outputs, aggregates recomputed from progress.tsv).

- **What the census proves:** every ruling-surface family is GREEN in it (scheduler_instance_mode, self_alarm service+tools, board ×3 files, view_write, allow_sets, wake_semantics — consistent with GT-a). All 322 FAILED/ERROR ids fall OUTSIDE the ruling surface, in environment-sensitive suites no green gate runs unfiltered (builtin-MCP bootstrap, governor integration, memory-system, progressive-dispatch, spawn/team-members, e2e answer/pause flows, performance, etc.). The 2 api_router reds appear (base-proven via GT-a).
- **Targeted BASE adjudication (identical command shape, worktree 463583800):**
  - Ruling-adjacent red `test_scheduling_service.py::TestUserTimezoneRung::test_user_setting_rung_covers_once_anchor_site` → **FAIL AT BASE TOO** (`assert None == '2026-10-04T23:00:00+00:00'`, 1.42s) — **pre-existing at 463583800, NOT ruling-attributable** (user-timezone feature lane, not instance_mode).
  - The 7 timeout-class ids → **ALL PASS at BASE (1-3s each; perf cell 277.8s)** AND on isolated HEAD re-run (idle worktree): **the HEAD-side rc=124s were environmental load contamination, NOT regressions** — 6 of 7 complete in 1-3s of test time (physically incapable of hanging); the perf cell's ~281s baseline vs the 300s cap (~19s headroom) makes it a **timeout false-positive generator under load**. Harness implication recorded: scale perf-matrix cell timeouts ≥600s or gate on load quiescence.
  - Legacy-form id (`test_instance_tools.py:308`) rejected by pytest (`file or directory not found`, RC=4) — the 2 legacy-form ids stay disclosed-unsharded.
- **What it does NOT prove (disclosed gap):** the full 297-red mass was NOT base-diffed (a 29k-node base re-run is a multi-hour exercise with no pack discipline). No net-new claim is made for the unfiltered corpus. The ruling-surface net-new proof stands on GT-a's base-diff + V1/V2/V3 behavioral evidence, not on this census. NOTE: an orphaned `run_base.sh` driver (launched by the terminated GT-b worker's detached shell chain — `terminate_instance` does not reap setsid'd descendants) is currently completing the full BASE leg into `/tmp/gtb2/base/`, write-confined to /tmp, base worktree clean; if left to finish it makes the full 297-red base-diff harvestable the same way. Disclosed, not awaited.
- 6 dead shards died to the documented collection/probe traps and were recursively split to completion (all children ran); rc=4 ×2 and rc=124 ×1 recorded in progress.tsv; the ERROR-recovery rerun (`-rEf`) recovered the 25 ERROR ids stably.

## Live census artifacts

Tester (RO role `ensemble_prod_ro`): `instance_mappings` schema + 23 rows + 0 `instance_mode`. Worker (GET-only, :9797): `/openapi.json` 173 paths → `/api/schedules?include_cancelled=true` (unbounded route confirmed at `routers/schedules.py:90-97` — census complete, not truncated) → 11 scheduler rows table + `/api/sources` reconciliation (14 total = 11 scheduler + 3 chat). Live daemon continuity verified before/after (uptime monotone, v0.25.7).

## Incidents & operational notes (this commission)

1. **GT-b worker wedges ×2** — `70e6fb44` (terminated; later shown by KMS-audit timestamps to have run until 05:50Z then gone silent) and `a0dd66c4` (terminated; its background scripts KEPT WORKING to completion while the instance itself went silent ≥1h22m — the driver finished, an ERROR-recovery pass ran, a resolve phase started). Pattern: long background bash + silent instance = watchdog hang-class, work not necessarily lost — **harvest `/tmp` artifacts before re-dispatching**. Both terminations were correct (dead-air instances); the salvage was the harvest.
2. **Collection traps hit**: `tests/packs/*` module-level `sys.exit` (INTERNALERROR) and `tests/e2e/test_context_injection_hybrid.py` import-time GET :8079 (collection imports run even for marker-deselected tests). `--ignore=` both when collecting the broad tree.
3. **Task-framing error (mine)**: "whole-tree 1114P/2F" — 1114 was the dev's bounded selection. Future dispatches: derive the corpus from the diff (GT-a's method), not from an inherited expectation figure.
4. **Worktree side-effect**: the GT-b lane appended to `.agents/shared/planning/designer-agent/install-audit.jsonl` in wt-tv62e (restored via `git checkout --`; pre-lane copy preserved at `/tmp/gtb2/install-audit.pre-restore.jsonl`).
5. Watchdog + escape-valve followed: 1 re-dispatch max per node; GT-b closed via harvest + targeted spot-check instead of a third attempt.

## Worker roster

| Lane | Instance | Flavor | Result |
|---|---|---|---|
| W0 bootstrap | `fb62ca35` | no skill | PASS 7/7 worktrees (Python 3.13.15 pinned; false-import-proof caught) |
| GA integrity | `0c22b07a` | integrity skill | PASS 9/9 |
| GB boot smoke | `b9fa5eb2` | boot-smoke skill | PASS 8/8 |
| V1 allow-lists | `654ee291` | gate skill | PASS 6/6 |
| V2 wake-the-caller | `b51775f0` | real-engine skill | PASS 1-3, FAIL item 4 → D-V2 |
| V3 engine seam | `c76960bb` | integration-test skill | PASS 4/4 |
| GT-a scoped corpus | `fb3c904b` | test-pack-execution | PASS (net-new ∅) |
| GT-b #1 / #2 | `70e6fb44` / `a0dd66c4` | test-pack-execution | wedged ×2 → terminated; HEAD census completed on disk |
| GT-b harvest | `cc470e3f` | no skill (read-only) | HARVEST COMPLETE |
| GT-b base spot-check | `26420388` | no skill | 9/9 adjudicated (adjacent red base-proven; timeouts base-green) |
| GT-b head re-check | `1e3d29d2` | no skill | 7/7 PASS — load contamination confirmed, no regression |
| Census | `8181cf36` | no skill (GET-only) | 11 scheduler rows / 0 reuse |

ensure.md scope: artifact/staged gate — Core packs relevant to the change set were exercised through the lanes above (concurrency pack not in blast radius; Release Gate not applicable — no `./dev.sh` prerequisite path touched). QUARANTINE: no flaky tests encountered; no updates applicable.

**Bottom line:** all three ruling verdicts CONFIRMED on independent real-path/real-DB evidence; staged v0.26.2 artifact integrity, boot, and seal gates green; docs-only tip drift pinned benign (promote AS-IS); ONE LOW defect D-V2 (3 stale doc-text locations) for leader adjudication (fix-lane vs ride-with-disclosure); GT-b unfiltered census disclosed without base-diff. The user 3-factor nonce ceremony owns the promote.
