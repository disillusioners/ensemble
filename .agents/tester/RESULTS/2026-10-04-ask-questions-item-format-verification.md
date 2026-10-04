# Test Report: ask_questions `{"item":[...]}` provider-wrap fix — functional verification + original-symptom closure

Date: 2026-10-04
Target: commit `b925e98d` ("fix: ask_questions accepts {\"item\":[...]} provider-wrapped array params"), branch `feature/ask-questions-item-format`, worktree `/home/nea/ensemble-worktrees/ask-questions-item-format`, base `2ae91046`. Diff verified: exactly 2 files (`daemon/tools/question_tools.py` +119/−2, `tests/test_question_tools_validation.py` +375 new), worktree porcelain-clean.
Reviewer status pre-existing: APPROVED (0 critical). This commission = functional verification + closing the ORIGINAL production symptom.
Worker instances: A `929bf8ad` (discovery, no skill) · B1 `b4b58252` (test-pack-execution) · B2 `69360b3b` (mock-test) · B3a `7e194f20` (test-pack-execution) · B3b `ec64c8d7` (test-pack-execution). Zero repo modifications, zero commits, live daemon (9797) untouched by all workers.

## VERDICT: 🟢 PASS — original symptom CLOSED, zero regressions attributable to b925e98d

## 1. Environment verification (STEP 0 — worktree editable-install trap)

Every counted run proved `daemon.__file__` resolves INSIDE the worktree:

| Worker | Interpreter | cwd | `daemon.__file__` |
|---|---|---|---|
| B1 | `/home/nea/ensemble-src/.venv/bin/python` (CPython 3.13.15, `PYTHONPATH=$PWD` defensive) | worktree | `…/ask-questions-item-format/daemon/__init__.py` ✅ |
| B2 | worktree `.venv/bin/python` (CPython 3.14.7) | worktree | `…/ask-questions-item-format/daemon/__init__.py` ✅ |
| B3a | worktree `.venv/bin/pytest` (pack script as-is) | worktree | `…/ask-questions-item-format/daemon/__init__.py` ✅ |
| B3b | `/home/nea/ensemble-src/.venv/bin/python` | worktree | `…/ask-questions-item-format/daemon/__init__.py` ✅ |

- **Trap mechanics confirmed**: main `.venv`'s `_editable_impl_ensemble.pth` pins `/home/nea/ensemble-src`; cwd-first sys.path resolution overrides it when cwd = worktree. All four workers kept cwd = worktree; each verified resolution empirically.
- **Worktree `.venv` provenance (disclosed)**: did not exist at recon time; B2 created it via `uv sync` (invocation option explicitly granted in its dispatch; `.venv/` is gitignored, `git status --porcelain` empty throughout, no tracked file touched). Versions: Python 3.14.7, langgraph 1.0.9, langchain-core 1.2.16, pydantic 2.12.5 — exactly the project's pinned stack (deferred-upgrade decision 2026-09-25). Side benefit: the 61 validation tests are green on BOTH 3.13 (B1, main venv) and 3.14 (B3a, worktree venv) — dual-interpreter confirmation.
- B2's post-hoc speculation that main-venv workers "would have picked up the MAIN checkout's daemon" was **refuted by direct evidence** from B1/B3b STEP-0 outputs.

## 2. Targeted suite (STEP 1)

- Command: `timeout 300 env PYTHONPATH="$PWD" /home/nea/ensemble-src/.venv/bin/python -m pytest tests/test_question_tools_validation.py -q --tb=short` (cwd = worktree)
- **RESULT: PASS — 61 passed / 0 failed / 0 errors / 0 skipped in 1.25s** (exit 0). Exactly the expected 48 pre-existing + 13 new.

## 3. Original-symptom closure (STEP 2) — the EXACT production zombie payload, byte-for-byte

Driven through **real `langgraph.prebuilt.ToolNode`** (`handle_tool_errors=True`, mirroring `long_tool_nudge.py:576` production wrapper) → real `StructuredTool` (built via `daemon.tools.question_tools.create_question_tools`) → real `QuestionManager` store. NOT a pre-parsed direct call. Script `/tmp/qa-zombie-2026-10-04/driver.py`, log `run.log`, runtime 3.3s, 0 ports bound.

| # | Verdict | Concrete evidence |
|---|---|---|
| (a) stored pack | ✅ PASS | direct store query: `id='zombie'`, `options_count=3`, `status='pending'`, `instance_id='test-instance-zombie-prod'` |
| (b) descriptions | ✅ PASS | `option_descriptions` in order: `['Finalize zombie + census (recommended)', 'Finalize zombie only', 'Defer to f2 investigation']`; desc[0]="One guarded pass: finalize b639aaf8 (done/completed) then lo…", desc[1]="Only the finalize. Skip the census check.", desc[2]="Leave b639aaf8 active and slot 1 held. No urgency (3/5 free,…" |
| (c) pause exactly once | ✅ PASS | `set_question_pause_requested.call_count=1`, arg `test-instance-zombie-prod`, pause flag `True` (dict-stub mirroring real `InstanceManager._question_pause_requested` shape at manager.py:3979) |
| (d) SSE once, list[str] | ✅ PASS | `stream_question_pack.await_count=1`; payload `options` = Python `list`, 3 elements, all `str`: `['Finalize zombie + census (recommended)', 'Finalize zombie only', 'Defer to f2 investigation']` — FE contract unchanged |
| (e) rejects, zero side effects | ✅ PASS ×3 | `{"item":"x"}` / `{"item":[…],"extra":1}` / `{"items":[…]}` → identical deterministic error `ERROR: Invalid ask_questions payload — questions[0].options: must be a list of strings or {label, description} objects.`; after EACH: direct store query → `None`; `pause_call_count=0`, pause flag `False`; `sse_await_count=0` |

Reject-path note: errors surface as `ERROR:` string in `ToolMessage.content` with `status='success'` — by design ("the tool itself never raises", question_tools.py:700); rejection proven by field-path error + zero side effects, matching the reviewer's byte-identical-reject claim.

## 4. Mock-vs-real discipline (STEP 3)

Harness reality (B2 disclosed): REAL = ToolNode executor, tool body (`.coroutine` verified a real function), `QuestionManager` store (in-memory dict + threading.Lock, production class), SSE payload (`pack_to_dict(real_pack)` at the real hub call site). MOCKED = SSE transport (`AsyncMock` on `hub.stream_question_pack`), instance-manager body (`MagicMock`; pause flag = dict-stub mirroring real shape). **Zero-persistence proof is real**: post-reject direct store queries returned `None` — not mock-log assertions. **Disclosure**: durability-metadata write (`instance_metadata.question_pack_payload`) and eventbus `QUESTION_REQUESTED` lanes were no-ops in the harness (unbound `getattr` on the MagicMock) — NOT asserted; both are downstream of the already-proven store/pause/SSE-call sites. No assertion binds to a stub that would pass regardless of real behavior.

## 5. Regression sanity (STEP 4) — targeted, not full suite

**Pack (registered)** `question_validation_targeted_unit_test` — 10 question-primary files: **PASS 140/140 in ~10.1s** @ b925e98d (Python 3.14). Drift vs 126 baseline = +13 new validation tests + 1 pre-existing (`test_question_manager.py` 17→18). Per-file: validation 61, tools 4, manager 18, api 4, dismiss 15, untested_paths 9, graph 10, deferred_pause_callback 6, deferred_pause_edge_cases 5, pause_completion_guard 8 — all green.

**Adjacent ask_questions surface** (8 enumerated unit files, each `timeout 300`, main venv):

| File | Result |
|---|---|
| tests/test_tool_filter.py | ✅ 55/55 |
| tests/unit/test_answer_gate_resume_chain.py | ✅ 10/10 |
| tests/unit/test_graphless_running_durable_dispatch.py | ✅ 10/10 |
| tests/unit/test_pause_never_dispatched_ghosts.py | ❌ 4P/4F — PRE-EXISTING |
| tests/unit/test_paused_instance_ttl.py | ❌ 25P/1F — PRE-EXISTING |
| tests/unit/test_streaming_none_node_update.py | ✅ 4/4 |
| tests/unit/test_turn_handle_transitions.py | ✅ 21/21 |
| tests/job_queue/test_midflight_qa.py | ✅ 42/42 (predicted 3.13 collection rot did NOT hit — single-file collection doesn't pull sibling rot) |

**The 5 failures are NOT caused by b925e98d** (causation check NEGATIVE): identical `TypeError: …_mock() got an unexpected keyword argument 'originator_instance_id'` — test-local mocks predate the kwarg added to `_pause_cascade_db_sync` by `4442ed90` (qa-channel), an ancestor commit; b925e98d's diffstat touches neither `instance_lifecycle.py` nor either test file; no failure message mentions ask_questions/question_tools/unwrap. **Flagged for follow-up by the qa-channel / pause-cascade owner (4442ed90)** — documented, not fixed, per commission constraints. These files are outside the changed-pack set (the question pack is green), so they do not red this verification's gate.

**Intentionally skipped** (out of blast radius, release-gate tier): `tests/e2e/test_full_chain_turn_reconciler.py`, `tests/e2e/test_answer_dismiss_flow.py`, `tests/e2e/test_e2e_workflows.py`, `tests/e2e/test_pause_during_report_turn_then_resume.py`, `tests/integration/test_spawn_pause_resume_dispatch_durable.py` — require `./dev.sh` + real LLM calls.

## 6. ensure.md status (blast-radius scoped)

- Core Critical #1 (no regressions in changed packs): **PASS** — `question_validation_targeted_unit_test` (the changed file's pack) 140/140; adjacent-set failures proven pre-existing/unrelated.
- Core Critical #2/#3 (concurrency pack / sync-DB): out of blast radius — change touches tool-input validation only.
- Core Critical #4 (dev.sh static grep): dev.sh untouched by the 2-file diff; requirement state inherited.
- Release Gate: NOT triggered (small isolated change, no architecture impact).

## 7. Scope Decision

> Verification commission scoped to: 1 targeted file + 1 registered pack + 1 real-path symptom harness + 8 adjacent unit files. Full suite NOT warranted: 2-file diff, single module (question tools), no architecture impact, reviewer pre-approved. E2E/integration skipped (daemon+LLM tier). Scope matches the leader's STEP 1–4 instructions.

## 8. FE contract statement

No web automation run — not needed: FE contract verified at the payload boundary. SSE `options` proven `list[str]` ×3 at the real hub call site (§3d); reject-error byte-identity across variants confirmed (§3e); reviewer had already verified byte-identical reject paths.

## Gaps / disclosures

- SSE transport itself and the two unbound lanes (durability metadata, eventbus) not exercised end-to-end — disclosed in §4; downstream of proven call sites.
- Worktree gained a gitignored Python 3.14 `.venv` (B2 `uv sync`, authorized invocation option; tracked tree untouched).
- 5 pre-existing adjacent failures flagged to 4442ed90 owner (not fixed, per constraints).

## Documentation Updated

- [x] PACKS.md — `question_validation_targeted_unit_test` Last Run → 2026-10-04 PASS 140/140 @ b925e98d (prior run preserved)
- [x] RESULTS/2026-10-04-ask-questions-item-format-verification.md — this report
- [ ] rules/ensure.md — no changes (user-maintained)
- [ ] MOCK_TESTS.md / QUARANTINE.md — no changes (no durable mock spec added; failures are deterministic pre-existing, not flaky)

## Code Changes Summary

None. Verification-only commission — zero modifications, zero commits, zero pushes by tester or any worker.
