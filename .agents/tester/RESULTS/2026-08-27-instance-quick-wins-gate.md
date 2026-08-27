# Independent Verification Gate — instance-quick-wins batch (#4 opt-in ×2, #7 revive-once guard, #1 injection provenance)

- **Branch**: `feature/instance-quick-wins` @ `8827063c` (range `0f27936e..8827063c`, 5 commits: 226ea122 → a4b1f1ee → a93d2596 → 6d6dc104 → 8827063c)
- **Date**: 2026-08-27
- **Diff verified**: 10 files, +1131/−18 — agents/{planner,tester}/meta.json (opt-in only), daemon/manager.py (counter + set_injection source), daemon/tools/instance.py, daemon/tools/job_queue.py, daemon/graph.py (drain stamping +30/−2), 4 test files. No out-of-scope touches.
- **Dispatches**: 20 workers (1 P0, 3 targeted, 1 mockfid, 1 probe, 1 ckpt-roundtrip, 5 committed packs, 7 sweeps, 1 quarantine-status), 0 direct executions.

## VERDICT: ✅ PASS (as of fix commit `d808297e`, 2026-08-27) — quick-wins gate CLOSED; batch CLEARED FOR MERGE to `latest`
*(Initial verdict @ 8827063c: ❌ FAIL — one 2-line stale-assertion pair. Fixed in `d808297e` and independently re-verified — see §6.)*

---

## 1. 🔴 BLOCKER: 2 NEW stale assertions in `test_spawn_team_members.py` (exact-match mocks vs new `source=` kwarg)

| Test | HEAD @ 8827063c | @ 1ae0acc0 | Cause |
|---|---|---|---|
| `tests/test_spawn_team_members.py:1352` `TestSendMessageTeamMembersGate::test_send_message_allows_pm_to_leader` | FAIL — `Expected: set_injection('target-leader-id', 'please execute task X') / Actual: …, source='internal_agent:parent-instance-id'` | PASS (44/44) | quick-win #1 (a93d2596) added `source=f"internal_agent:{caller}"` at the call site (instance.py:2418-2422); the Phase-1-updated assertions are `assert_called_once_with` = exact-match → new kwarg breaks them |
| `tests/test_spawn_team_members.py:1417` `test_send_message_target_without_agent_id_fails_closed` | FAIL — same signature | PASS | same |

- **Intended behavior** (probe-verified: FIFO entry keys `['content','source','timestamp']`, real drain stamps `additional_kwargs['source']`): the tests are stale, NOT a production bug.
- **Fix (NOT applied — report-only gate)**: add `source="internal_agent:parent-instance-id"` to both `assert_called_once_with` calls. 2 lines, test-only.
- **Narrow re-verify after fix**: `tests/test_spawn_team_members.py` → expect 44/44.
- **Pattern note (3rd recurrence)**: Phase-1 (routing change), Phase-2 (singleton pollution), now (call-contract kwarg). Recommended standing dev checklist: on ANY call-contract change (kwargs/refusal text), `grep -rn "assert_called_once_with.*<seam>" tests/` repo-wide before hand-off.

## 2. 🟠 IMPORTANT non-blocking: reviewer residual CONFIRMED — `source` invisible via `GET /instances/{iid}/messages`

- Real-checkpoint probe (3 angles, 16.9ms, file-backed AsyncSqliteSaver): **raw checkpoint PRESERVES source** (`saver.aget` returns `additional_kwargs: {injected_message: true, source: …}`); **real drain stamps correctly** (graph.py:2885-2894 via real `create_agent_node`); **`get_instance_messages` DROPS it** — `serialize_message` (daemon/utils.py:186-189) surfaces only `context_kind` from `additional_kwargs`, discards the rest. Same boundary `injected_message: True` itself has always had.
- **Not a blocker**: the batch contract ends at drain + log suffix (both verified); serialization surfacing was explicitly deferred by the Phase-2 D12 addendum freeze (instance.py:961-980). 1-line fix sketched (`serialized["source"] = additional_kwargs.get("source")`) + docstring gap noted (utils.py:94). **Freeze-lift is the approver's call** — recommended as an immediate follow-up since provenance is only half-visible until then.

## 3. Verification results (all GREEN)

### P0 statics — 16/16, all cited
Counter `_agent_tool_revive_counts: dict[str,int]` keyed by child id (manager.py:692), RAM-only documented (restart-loss accepted v1, no cleanup path — restart resets); increment strictly AFTER awaited enqueue on BOTH surfaces (instance.py:2511→2523; job_queue.py:1057→1074); busy-guard precedes revive check, zero counter access (instance.py:2466-2474; job_queue.py:1000-1005); COMPLETED-continue excluded in code + documented at 4 sites (job_queue.py:261-270, :1011-1014, :1037-1038, :1069-1070 + manager.py:2487-2490, :2529); refusal string byte-identical template both surfaces (instance.py:2500-2502 == job_queue.py:1047-1049; wrapper differs str vs {"error": str} — documented both docstrings); `set_injection(source=None)` byte-identical legacy entry via conditional attach (manager.py:2393-2398); drain stamping conditional (graph.py:2886-2894) + INFO suffix source_tag (:2929-2938), no-source log byte-identical (:2922-2927); user-API router call site untouched (messages.py:356, no source arg); opt-in exactly planner+tester, single-line in-place, no broadening; production get_version→get_resolved seam (instance.py:3646-3650). ensure.md statics: dev.sh:102 ✓, zero async-def changes ✓.

### Targeted
- **TestReviveOnceGuard 8/8** (incl. t5 busy-budget lock, t4 user-API-uncounted ×2, docstring tests). Refusal byte-identical across surfaces; COMPLETED-exclusion pinned in `TestJobContinueTool::test_w1_t3`; ordering (enqueue-fail no-burn) ABSENT in suites → probe covers.
- **TestInjectionSourceProvenance 4/4** — REAL drain via `create_agent_node`; byte-identity dual-pinned (drained kwargs `== {"injected_message": True}`; `"source=" not in` log).
- **#4 opt-in**: file 143/143; `test_planner_and_tester_resolve_subtree_messages_via_registry` REAL `AgentRegistry.discover()` + get_version/get_resolved; no-broadening pin present; leader unaffected.
- job_queue_tools pack: 80 collected/76 run/75P/1F = job_create-only (known). Count shift +3 = NEW W1 tests (t1/t2/t3), all passing.

### Behavioral probe (REAL factories + real unbound manager methods + real DB) — 10/10
(a) revive grant + counter 0→1; (b) byte-exact refusal + zero enqueue; (c) job_continue FAILED once-bound, **cross-surface shared once-bound proven on one child**; (d) RUNNING injection → FIFO source + real-drain kwargs `{'injected_message': True, 'source': 'internal_agent:…'}` + back-compat source-less; **(e) enqueue-failure no-burn CONFIRMED both surfaces** (counter 0 → retry grants); (f) busy no-consume + COMPLETED-busy precedes guard + grant survives queue-free; (g) **user-API uncounted via REAL `_prepare_enqueued_message` on real DB** (status flipped to running, counter 0, full agent grant after); (h) COMPLETED-continue with burned counter still allowed + documented; (i) planner ✓ tester ✓ developer ✗ across 3 seams. Non-defect observation: error-shape asymmetry (send_message propagates exception vs job_continue error dict) — contract holds either way.

### Mock fidelity — CLEAN
1 MEDIUM: W1 job_queue tests explicitly mock `has_inflight_task` where production calls `has_instance_busy` (job_queue.py:1002) — works today via fixture compensation, silently breaks if fixture trimmed; ~3-line hygiene fix. LOW gaps: increment-ordering not asserted in suites (probe covers); `set_injection(source=)` kwarg unasserted at call-site (the flip side of the blocker).

### Packs & sweeps
tools 991/986P/0F/5-deselect · api 213P/8S exact · concurrency 98P/74S exact · registry 140/140 exact · sweeps ×7: **all known families, ZERO unknown** except the §1 blocker (top-sz). job_create + injection_cleanup/_ManagerStub isolation confirmed as predicted.

### Quarantine progress (non-blocking)
The 4 quarantined job_continue IDs **all PASS** at 8827063c (run 1 of the 3× required; dev's has_instance_busy fixture fix effective for all 4). Un-quarantine path: 2 more clean runs + remove the 4 deselects from `test/packs/job_queue_tools_unit_test.sh`.

## 4. Required actions before merge
1. **[BLOCKER]** Add `source="internal_agent:parent-instance-id"` to the two `assert_called_once_with` in `tests/test_spawn_team_members.py` (:1352, :1417) → re-run file, expect 44/44.
2. **[Recommended]** Approver decision on the serialize_message freeze: 1-line source surfacing (utils.py:189) + docstring update — provenance is checkpoint-visible but API-invisible until lifted.
3. **[Recommended]** W1 mock hygiene (has_inflight_task → has_instance_busy, 3 lines).
4. **[Follow-up]** job_continue un-quarantine: 2× more clean runs + pack-script deselect removal.

## 5. Worker instances
3f826e91 (P0), 49818543 (revive), 345c0876 (provenance), 3e342d49 (optin), a05597ce (mockfid), bfef3fc2 (probe), 7b640619 (ckpt), eff7c6d3 (tools), 5ea37b73 (jqt), 671fac0e (conc), d5c52a5f (api), a6a27948 (registry), 657722f0/c57030ea/dc591859/37fa1413/6a94f0d6/4a949a53/e75cae6e (sweeps ×7), 8a217510 (jcon-status).

Re-verification @ d808297e (2026-08-27): a3d5195b (fix re-verify).

---

## 6. Re-verification @ d808297e — blocker CLOSED, verdict flipped to PASS

Fix commit `d808297e` (parent `8827063c`): exactly ONE file `tests/test_spawn_team_members.py` **+8/−2** — both exact-match asserts now carry `source="internal_agent:parent-instance-id"`, **derived-correct** (fixture `create_instance_tools(manager, "parent-instance-id", …)` at :1269; format matches production `internal_agent:{caller_id}` stamp). Dev's same-commit sweep confirmed exactly 2 exact-match asserts existed, both fixed. **Zero daemon/ diff** — utils.py untouched (D12 freeze honored), so all §2-§3 statics/probe/pack conclusions carry over unchanged.

| Check | Expected | Result |
|---|---|---|
| Diff audit | one file +8/−2, both asserts + source=, fixture-derived, daemon diff empty | ✅ exact (asserts + fixture line quoted verbatim in worker report) |
| Pack run | 44/44 incl. gate class 5/5 | ✅ 44 passed, exit 0, 3.01s |

**Quick-wins overall: ✅ PASS — batch CLEARED FOR MERGE to `latest`.** Branch chain: `0f27936e → 226ea122 → a4b1f1ee → a93d2596 → 6d6dc104 → 8827063c → d808297e`. Open non-blocking items remain documented in §2-§4 (serialize_message source surfacing = approver's freeze-lift call; W1 mock hygiene; job_continue un-quarantine 2-of-3 runs).
