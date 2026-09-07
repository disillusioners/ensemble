# VERIFICATION GATE — LCA conditional attestation + masquerade fix @ 9d042c88

Date: 2026-09-07 · Tester lane
Branch: `feature/leader-completion-attestation` @ `9d042c88` (base `26f908a4`; delta 4 commits: `53baef57` / `f965345a` / `e321bdb3` / `7be6b1d8`)
Blast radius: 26 files (attestation conditional-gating + enqueue-lane stamp + nudge enlargement + 2 new test files + docs/setup.md)
Worktree: `/Users/nguyenminhkha/All/Code/opensource-projects/ens-lca-cond` (CLEAN pre-gate; 2 tester-doc commits added during gate: `8eace79e`, `9d042c88`)
Workers: 6 dispatched (1 recon + 5 execution). 0 retries. 0 escape-valve re-dispatches. 0 production-code changes. Main worktree untouched (`192dee4e [latest]`).

## VERDICT: ✅ PASS — merge green-lit

---

## Scope Decision
Full-repo regression NOT warranted — the branch already carried a 15,950-test full gate at `6e679c16` (LCA MVP) plus re-gates at `8b083522` (live-descendants fix) and `42cb9518` (LCA × ReviveGuard merge). This gate covers the DELTA `26f908a4..9d042c88` with the 7 jobs the user defined. ensure.md Release Gate (slow / E2E) NOT triggered — no architecture change beyond the gated branch's follow-up delta.

---

## Job results

| # | Job | Result | Evidence | Runtime |
|---|-----|--------|----------|---------|
| 1 | Attestation matrix (37 files / 4 glob families + migration) | ✅ **PASS 398/398** | developer-reported 398 ground truth exact; 0 fail, 0 error, 0 skip | 8.28s |
| 2+6 | Independent E2E (a)-(e) + artifact fix | ✅ **PASS 10/10** | new `2026-09-07-lca-cond-e2e-scenarios.py` (5/5) + fixed `2026-09-06-lca-independent-e2e-testfile.py` (5/5); commit `9d042c88` | 1.33s combined scratch run |
| 3 | Neighborhood A/B (instance_messaging + messages + compaction; 58 files / 900 tests) | ✅ **PASS (0 introduced)** | HEAD `872P/7F/5S/16E` ≡ base `26f908a4` `872P/7F/5S/16E` byte-identical (FAILED+ERROR section diff = 0 lines); commit `8eace79e` for filelist + FAILED-ID evidence | 205s HEAD · 208s BASE |
| 4 | Real-PG execution (conditional scanner + gate outcomes + masquerade + revive) | ✅ **PASS** | 50/50 pure-function at HEAD + 3/3 integration executed-not-skipped on real PG; dialect canary `engine.dialect.name == 'postgresql'` GREEN; `current_database() == 'ensemble_lcacond_7be6b1d8'` asserted on every engine build; disposable DB created→force-dropped, zero leftovers; `ensemble_prod` untouched | 0.78s PG run |
| 5 | Boot smoke (enforce default, real daemon, disposable PG) | ✅ **PASS-WITH-NOTES** | boot log line 9881: `Leader completion attestation resolved: mode=enforce ... (env unset, source default applied)`; canonical log format at `daemon/services/attestation_gate.py:815-844` carries 23 substituted fields including `attestation_required` at #13 (brief said 17 — stale; ship-mode invariant holds); `ATTESTATION_NUDGE_TEXT` 1872 chars, header `[SYSTEM CONTEXT: Completion Check Nudge]` + mermaid both True; prior LCA-gate workers drove identical HEAD and produced `decision=denied mode=enforce attestation_required=True` rows in `ensemble.log:5,8,10,...,9641`; live LLM-based deny skipped per "if cheap" escape clause | ~240s wall |

---

## Independent E2E detail (Jobs 2 + 6)

Scenario file: `.agents/tester/RESULTS/2026-09-07-lca-cond-e2e-scenarios.py` (709 lines, 2 new file on branch).
Each scenario driven through the REAL `build_instance_graph` with ONLY the LLM seam scripted (`ScriptedChatModel` patching `build_instance_llms`), real resolver path (env-pin `ENSEMBLE_LEADER_ATTESTATION_MODE`), real repo rows.

| Scenario | Verdict | Key evidence |
|---|---|---|
| (a) quick-question | PASS — FR-3 conditional OFF for non-delegating turns | gate row `decision=allowed … attestation_required=False last_real_user_found=True delegation_tool_call_total=0`; nudges `[]`; model.calls 1; ledger 0 |
| (b) chart tool call | PASS — non-delegation tool does NOT arm the gate | gate row `decision=allowed … attestation_required=False delegation_tool_call_total=0 first_delegation_after_last_user_index=-1`; chart tool executed; nudges `[]`; calls 2 |
| (c) delegated, un-attested END | PASS — deny emits exact nudge + kwargs; attest reset verified | `decision=denied … attestation_required=True delegation_tool_call_total=1` ⇒ `decision=allowed … attestation_present=True`; nudge `content == ATTESTATION_NUDGE_TEXT` exactly (header + ```mermaid``` + `flowchart TD`); kwargs `{"attestation_nudge": True, "attestation_nudge_denied_count": 1}`; `repo.get_attestation_denied_count(LEADER_ID) == 0` (R1 reset); calls 4 |
| (d) MASQUERADE end-to-end | **PASS — the headline fix bites** | Phase 1 parked `decision=allowed_legitimate_pending_wakeup … pending_children=1 live_descendants=1 attestation_required=True`. Phase 2 child→completed (atomic `transition_status_if`, FIRED watcher). Phase 3 enqueue-lane drive: real `MessageQueue` row `source="internal_report:{CHILD_ID}"` → `_build_graph_input(...)` carries stamp `{"injected_message": True, "source": "internal_report:{CHILD_ID}"}`; controls: `source=None` → bare, `source="api"` → bare. Stamped window → `find_last_real_user_index==0` (anchor held); bare window → `find_last_real_user_index==2` (masquerade); stamped wake via `graph.ainvoke(stamped_input, thread_id=LEADER_ID)` → checkpoint-durable; gate row `decision=denied … attestation_required=True` then `decision=allowed`; kwargs denied_count=1; ledger 0 |
| (e) self-reference trap | PASS — nudge does NOT reset the window | three gate rows: deny×2 `attestation_required=True` then allowed `attestation_present=True`; two nudges with `attestation_nudge_denied_count` 1 AND 2; `find_last_real_user_index` still points at the ORIGINAL "Delegate and push through to the end." HumanMessage — anchor never moved |

---

## Findings

🟠 **MEDIUM (artifact lane drift) — RESOLVED in `9d042c88`**: the tester-lane artifact at `.agents/tester/RESULTS/2026-09-06-lca-independent-e2e-testfile.py:46` hardcoded the OLD single-line nudge literal (flagged twice by the operator). The artifact's S1/S2/S3 scenarios also needed to be re-armed because the conditional gate is OFF unless a delegation happens after the last real user message. Both fixed: constant-parity import + opening `send_message` tool call per scenario + enqueue-lane stamp on the S1 child-report input. The constant-import was the cosmetic part of the flagged drift; the scenario re-arm was the substantive fix.

🟠 **MEDIUM (manager stub seam) — RESOLVED in `9d042c88`**: the artifact's `_manager` stub was missing a `count_live_descendants` facade. Without it the gate's DB-seam guard fires `leader_completion_gate_db_error → ALLOWED` and EVERY deny assertion in S1/S2/S3 is unsatisfiable (deny path never exercised). Added real-BFS over `repo.get_tree_ids_permanent` (same algorithm as `InstanceManager.count_live_descendants` at `manager.py:8601-8655`). High-value drift finding: any future tester artifact that exercises the gate must wire this facade.

🟢 **INFO (brief drift)**: the task brief called for a "17-field" gate log row; the canonical format at `daemon/services/attestation_gate.py:815-844` has 23 substituted fields. The key ship-mode invariant — `attestation_required` is among the substituted fields (at position #13) — holds. Brief correction surfaced by the boot-smoke recon; no code change required. The LCA phase-6 plan docs still reference "17-field" (also a drift per the established critical-note convention).

🟢 **INFO (status mutation API)**: `repository.update_status` rejects writes (ValueError directing callers to `transition_status_if` with `allowed_from` tuple). E2E scenario (d) uses the atomic `WHERE status IN (:allowed_from)` transition — the production-correct path. Any test that simulates child lifecycle changes MUST use the atomic form.

🟢 **INFO (token naming)**: the brief's `delegation_since_last_user=False/True` token is in the gate's Python `Decision` object but NOT in the canonical log-row format string. Log fields that survive into logs are `last_real_user_found`, `last_real_user_index`, `first_delegation_after_last_user_index`, `delegation_tool_call_total`, plus `attestation_required` (#13). The E2E worker adapted assertions to the existing tokens — assertion intent preserved.

---

## ensure.md scoping (Core, scoped to delta)

- **No regressions in changed packs** (Critical): ✅ scoped packs all PASS / 0-introduced (matrix 398/398, neighborhood 0-introduced, PG 53/53, E2E 10/10)
- **deadlock / concurrency pack** (Critical): not in scope (no deadlock work in this delta)
- **sync-DB-on-asyncio** (Critical): covered by concurrency pack; not in scope
- **dev.sh `--timeout-graceful-shutdown 10`** (Critical): ✅ present at `dev.sh:102` (verified in prep)

**ensure.md Core scoped: 4/4 in-scope Critical PASS.** Release Gate (E2E / full non-integration suite) not warranted — no architecture change in this delta; the branch's E2E surface is covered by the in-graph scripted-model seam (Jobs 2+6).

---

## Commits landed on branch (tester-doc-only)

- `8eace79e` — `test: LCA cond-attestation neighborhood A/B evidence (filelist + FAILED-ID diff)` (`.agents/tester/RESULTS/2026-09-07-lca-cond-neighborhood-{filelist,failedids}.txt`)
- `9d042c88` — `test: LCA cond-attestation — import ATTESTATION_NUDGE_TEXT in lane artifact + independent scenarios (a)-(e)` (`.agents/tester/RESULTS/2026-09-06-lca-independent-e2e-testfile.py` + `2026-09-07-lca-cond-e2e-scenarios.py`)

`git diff --stat HEAD~2..HEAD` scoped to the 4 tester artifact files; zero `daemon/` or `tests/` mutations.

---

## Follow-ups (not blockers)

- The 23 pre-existing failures this gate surfaced (7 FAILED + 16 ERROR families in the messages-neighborhood partition, all byte-identical at base `26f908a4`) should be added to `QUARANTINE.md` as family-level rows to keep the hygiene going. The 16 ERROR class is the documented fresh-SQLite migration trap (PG-only `DROP CONSTRAINT IF EXISTS`; SQLite OperationalError) — environmental, not a real failure. Recommended for the next tester session.
- The canonical log format grew from 16 to 23 fields during this delta; the LCA phase-6 plan doc (`phase6-fastfollow-plan.md`) still references "17-field" — same drift pattern as the task brief. Flag for the planner/author of the phase-6 plan to update.

---

## Documentation Updated

- [x] `.agents/tester/RESULTS/2026-09-07-leader-completion-attestation-cond-merge-gate.md` — this report
- [x] `.agents/tester/PACKS.md` — new top-of-file gate entry
- [x] `.agents/tester/LESSONS/2026-09-07-lca-cond-attestation-e2e-findings.md` — drifts + remediation captured
- [ ] `.agents/tester/QUARANTINE.md` — follow-up (see above)
- [ ] `.agents/tester/rules/ensure.md` — no changes (user-maintained, read-only)

---

## Overall verdict: ✅ **PASS — merge green-lit.**
