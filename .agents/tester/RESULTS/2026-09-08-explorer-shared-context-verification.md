# Test Report: Explorer shared-context migration + messaging parent-resolution — independent verification

Date: 2026-09-08
Branch: `feature/explorer-shared-context-injection` @ `b2e44d1e` (80bb61dd core fix + b2e44d1e migration); base `fd582efd` (= latest at branch point)
Workers: A=d69d3a41 (regression-proof-prefix), B=5b61d82c (groundtruth-suite), C=0eb3ac8f (flagon-paths-integration)
Mode: independent verification, throwaway worktrees only (`--detach` at fixed SHAs), shared reviewer worktree `agents-ensemble-wt-explorer-ctx` untouched; no branch modifications, no commits, no push, no daemon restart.

### Summary
- All 4 acceptance items: **PASS** (item 2 carries a count-claim discrepancy flag — not a code defect)
- Failures attributed to the branch: **0** (every suite green at `b2e44d1e`)
- New evidence test written by tester: 5 integration tests (PASS 5/5) — `/tmp/tester-evidence-explorer-ctx/test_explorer_shared_context_paths.py`
- Quarantined tests skipped/avoided: `tests/unit/tools/test_archive_lifecycle.py` (QUARANTINE.md, excluded per protocol)
- Scope: leader-supplied verification protocol (items 1–5). ensure.md validated scoped (below).

### Diff ground truth (`fd582efd..b2e44d1e`) — 7 files, 869+/219−
- Source (3): `agents/explorer/meta.json`, `daemon/services/instance_messaging.py`, `daemon/tools/knowledge_tools.py`
- Test (4): `tests/services/test_instance_messaging_parent_resolution.py` (NEW, 323L), `tests/unit/test_explorer_agent.py` (NEW, 149L), `tests/unit/test_context_messages.py`, `tests/unit/tools/test_knowledge_tools.py`
- Note: task brief said "8 touched files" — ground truth is **7**.

### Item 1 — Pre-fix regression proof @ fd582efd — ✅ PASS (CONFIRMED)
Worktree `agents-ensemble-wt-tester-prefix` (detached fd582efd), isolation proven (`daemon.__file__` inside worktree), guards `HEAD/fd582efd` per invocation.

- **Mispartition**: `tests/services/test_instance_messaging_parent_resolution.py` → `1 failed, 2 passed` (by design). Exact shape:
  `AssertionError: messaging path must thread the row's parent_id into the orchestrator … got parent_id=None` → `assert None == 'caller-tree-root'`
  (child rows carry `parent_id="caller-tree-root"`; base threads `None` → `_resolve_tree_root_id` returns child's own id = own-partition mispartition.)
- **Manual-attach pins**: `TestExploreAutoInjection` (lives in `tests/unit/tools/test_knowledge_tools.py`, NOT test_explorer_agent.py as the brief guessed) → `4 failed, 116 passed` full-file. All four no-attach pins fail at base with exact shapes:
  1. `# Shared Context` payload still appended to dispatched message (`assert "# Shared Context" not in message` fails)
  2. `get_tree_root_id` still called (`assert_not_called` fails, called 1×)
  3. attach still happens even without `project_id`
  4. `asyncio.to_thread(get_shared_context, …)` still used (`assert func is not get_shared_context` fails)
  Positive pin (`test_explore_message_carries_query_mode_and_project_line`) passes at base as designed.
- Cleanup verified (`git worktree list` — throwaway gone; shared reviewer worktree intact).

### Item 2 — Ground-truth suite @ b2e44d1e — ✅ PASS (suites green) + ⚠️ count-claim mismatch
Worktree `agents-ensemble-wt-tester-fix` (detached b2e44d1e), isolation proven, guards `HEAD/b2e44d1e` per batch (main repo HEAD moved externally mid-run; no drift in test worktree).

| Scope | Collected | Passed | Failed | Skipped | Runtime |
|---|---|---|---|---|---|
| Invocation A — 4 touched test files | 206 | 205 | 0 | 1 | 4.4s |
| Invocation B — A + 3 broader files (context_injection_integration, context_in_graph, blueprint_injection) | 252 | 251 | 0 | 1 | 4.2s |

- Skip (pre-existing, env): `test_context_messages.py:1877` "langgraph.graph.message unavailable in this env".
- **Dev claim 293P/1S NOT reproducible** from touched scope (205P/1S) or broader surface (251P/1S). Δ 42–88 tests unaccounted — the dev's run scope is unknown/wider. All prescribed scopes are fully green, so this is a **reporting-accuracy finding, not a code defect**. (QC convention: test counts from ground truth `pytest`, never implementer reports.)
- Zero deterministic failures anywhere → base-reproduction step (item 5) not triggered.

### Item 3 — Flag-ON real-service path (kill-switch convention) — ✅ PASS
- **Gating key**: `agents/explorer/meta.json` → `"context_injection": {"heuristic_match_shared_md_files": true}`; resolved via real AgentRegistry into `AgentMetadata.context_injection` (default **False**), consumed at `daemon/services/context_messages.py` ~:1364-1368.
- **Partition semantics** (`daemon/services/instance_messaging.py` ~:3683-3694): messaging path reads `parent_id` from the PERMANENT instance row (spawn-shaped child carries `parent_id=caller`), threads into `assemble_context_messages(parent_id=…)`; `_resolve_tree_root_id` walks `instances.parent_id` chain via `instance_repository.get_tree_root_id(parent_id)` to the tree root (None/exception → fallback to parent_id; root → own id).
- **Coverage adjudication**: dev's meta.json tests = config-loading only (correctly scoped, pass); the end-to-end enqueue-path coverage was a genuine GAP → filled by tester integration test.
- **New integration pack** `tests/integration/test_explorer_shared_context_paths.py` (evidence: `/tmp/tester-evidence-explorer-ctx/`): REAL InstanceMessagingService + real registry/meta.json + real `assemble_context_messages`/`_resolve_tree_root_id`/`get_shared_context` filesystem match + real SQLite repos (file-backed tmp_path, NullPool, WAL, busy_timeout — no StaticPool/WriteGuard); mocked only fake graph `astream`/checkpoint probe/SSE. **RESULT: PASS — 5 passed in 1.22s**:
  - ON child (opted-in explorer, parent_id set, project-linked, FIRST enqueued message): exactly 1 `[SYSTEM CONTEXT: Shared Context]` block, body carries `context_key: {root_id}` (tree-root partition proven; child/mid keys absent); row → RUNNING.
  - OFF negative (real `developer` meta, no opt-in; matchable content PRESENT in partition): 0 blocks — the gate, not content absence, suppresses.

### Item 4 — Three explorer paths, no double-injection / no drop — ✅ PASS (with 2 documented caveats)
- **(a) explore-tool dispatch**: branch pins (all pass @ b2e44d1e, re-confirmed 5/5 in clean venv): dispatched message carries `Query (mode=…)` + `Project:` line, NO `# Shared Context`, no `get_shared_context`/`to_thread` call. Block-fires side = same spawn shape as 3-ON integration test. ✅
- **(b) spawn + send_message**: covered at the enqueue→first-turn seam (spawn-shaped child `parent_id=caller`, first enqueued message) → 1 block from tree root. *Caveat: not a literal `spawn_instance` tool invocation — the injection seam under test is identical.*
- **(c) revive/terminal→enqueue**: terminal child revived via enqueue → block fires exactly ONCE (no-drop; tree-root context_key); revived child WITH prior injection (`project_injected=True`) → 0 NEW blocks (no-duplicate). *Caveat: no-duplicate asserts 0 new blocks on turn 2 (once-per-instance contract); fake-graph harness cannot introspect the turn-1 checkpoint block.*
- **Root unchanged**: root instance resolves own partition, exactly 1 block (real-DB leg in new pack). ✅

### Item 5 — Pre-existing failure hygiene — ✅ N/A (clean)
No deterministic failures encountered at `b2e44d1e` in any prescribed scope → no base-reproduction needed. Known quarantine `test_archive_lifecycle.py` excluded and untouched.

### ensure.md Validation (scoped to change blast radius)
- **Critical — no regressions in changed scope**: PASS (Invocation A/B green).
- **Critical — dev.sh `--timeout-graceful-shutdown 10`**: PASS (`dev.sh:102`).
- **Critical — deadlock/concurrency pack**: out of blast radius (no locking changes in the 3-file source diff) — not run, scope decision below.
- **Important — callers of converted async fns awaited**: PASS — 8 real call sites (`routers/instances.py:375,525`, `routers/messages.py:757`, `tools/instance.py:2929`, `manager.py:8551`, `instance_messaging.py:1116,1146,1357`), all properly awaited; rest of grep hits = defs/docstrings/logs.
- Release Gate: not applicable (not a release/big-architecture change per diff).
- Contradictions found: none. Improvement notices: none.

### Scope Decision
> Leader protocol named exact test files + verification steps; ran exactly those as scoped ad-hoc packs (dual-layer timeout, rev-parse guards) in throwaway detached worktrees. ensure.md scoped to the change's blast radius; concurrency pack excluded (no lock-surface changes: diff = meta.json + injection assembly + explore tool). Runs recorded here, not as PACKS.md pack-script rows (no new pack scripts created; ad-hoc file-list invocations per ensure.md "ad-hoc pack" allowance).

### Findings / Follow-ups
- 🟠 **Important — implementer count claim unreliable**: "293 passed / 1 skip" not reproducible from any prescribed scope (205P touched / 251P broader); "8 touched files" is actually 7. All green → does not block, but the review record should not carry the 293P figure.
- 🟢 Nice-to-have — upstream `/tmp/tester-evidence-explorer-ctx/test_explorer_shared_context_paths.py` (5 tests) into the branch: it is the only end-to-end (real-service, real-DB, tree-root-assertion) coverage of items 3/4; branch tests are unit/kwargs-level for these rows.
- 🟢 Nice-to-have — literal `spawn_instance`-tool-level variant of path (b) and a checkpoint-introspectable no-duplicate assertion (needs real-graph harness).
- 🟢 Note — `TestExploreAutoInjection` lives in `tests/unit/tools/test_knowledge_tools.py` (task brief guessed `test_explorer_agent.py`); brief's file-location assumption corrected by grep before scoping.

### Gaps
None — all 3 workers reported; no incomplete nodes.

### Documentation Updated
- [x] RESULTS/2026-09-08-explorer-shared-context-verification.md — this report
- [ ] PACKS.md — no pack-script changes (ad-hoc runs; see Scope Decision)
- [ ] QUARANTINE.md — no changes (no new flaky/failing tests)

### Code Changes Summary
None to production or test trees of the branch (report-only arc). Tester-authored evidence test lives OUTSIDE the repo at `/tmp/tester-evidence-explorer-ctx/test_explorer_shared_context_paths.py` (throwaway worktree removed after evidence copy). No commit required/possible per "no modifications to the branch" constraint.

### Overall Status
- Item 1 regression proof: ✅ CONFIRMED
- Item 2 ground truth: ✅ PASS (⚠️ dev count claim unreproducible — flagged)
- Item 3 flag-ON real path: ✅ PASS
- Item 4 three paths: ✅ PASS (2 documented caveats, neither blocking)
- Item 5 hygiene: ✅ clean
- **Verdict: PASS — fix verified against the original goal (system injection on all invocation paths, no manual attach, no double-injection, tree-root child partition).**
