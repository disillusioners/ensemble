# Independent Verification — send_message Context-Inject Fallback (2026-09-25)

**Commit under test:** `6620cb7f` (amend of `2dbc71dd`) on `feature/send-message-context-inject-fallback`, base `75d7e2d1`, single commit.
**Fix:** agent-tool `send_message` in `daemon/tools/instance.py` — on the queue-busy rejection path, when the target re-verifies injection-eligible (fresh status == `running` AND `manager.has_live_graph_task(iid)`) AND send bore non-empty `context` AND NOT `load_skill` → prepend `[SYSTEM CONTEXT: Task Context]` block to the message body and deliver via `manager.set_injection(iid, flattened, source="internal_agent:{caller}")`.
**Incident to close (c3d1a722, 2026-09-25):** leader send with `context={'notes': …}` to a running + live-graph + processing child was rejected `ERROR: … already has a message in progress. Pending: 0, Processing: 1`. Must now INJECT with the context block flattened as prefix.

**Method:** 8 workers (5 packs + 3 read-only audits), every pytest invocation drift-pinned (`git rev-parse` pre-flight), env-scrubbed (`env -u POSTGRES_HOST -u POSTGRES_PORT -u POSTGRES_DB -u POSTGRES_USER -u POSTGRES_PASSWORD -u POSTGRES_URL -u DATABASE_URL`), dual-layer timeout (`timeout 300` outer + pyproject per-test), `uv run python -m pytest` from checkout root, no `-x`, no xdist, no deselects. Report-only gate — zero quick fixes, zero file modifications by any worker. Main checkout stayed on `feature/send-message-context-inject-fallback` @ `6620cb7f…`, clean tree, throughout.

**Worker instances:** W1 `6145ac1a` (touched-file pack) · W2 `979d1654` (tools-dir pack) · W3 `22836021` (fidelity audit) · W4 `cad3bbdd` (diff+discovery) · W5 `6076b92f` (base leg) · W6 `06ef27e7` (deleted-lines audit) · W7 `0d06842b` (b1-wc cross-suite) · W8 `ccb125f5` (status-guard cross-suite).

---

## Section 1 — Touched-file re-run: ✅ PASS

`tests/unit/tools/test_instance_tools.py` @ 6620cb7f: **207 passed / 0 failed / 0 errors / 0 skipped** in 14.11s (exit 0). Exact match to the task expectation (207/0). 35 pre-existing sqlmodel SAWarnings (mocked-engine fixtures, assertion-free — environmental noise).

## Section 2 — Mock fidelity audit (TrueAuto careful-mock): ✅ PASS

Read-only audit against REAL signatures (all citations from `daemon/manager.py`, `daemon/repositories/message_queue/repository.py`, `daemon/tools/instance.py`, `daemon/constants.py` @ 6620cb7f):

| Axis | Real behavior | Test mock | Verdict |
|---|---|---|---|
| `set_injection` | SYNC `def set_injection(instance_id, content, source=None, echo_id=None, image_refs=None)` (manager.py:2766-2773); production call = 2 positional + `source=` kwarg only (instance.py:3423-3427) | sync `MagicMock`; test 3 pins `args == ("target-id", ANY)` + `kwargs == {"source": "internal_agent:parent-instance"}` | FIDELITY-OK |
| Flattened payload | `_format_task_context(context).rstrip("\n") + "\n\n" + message`; header `[SYSTEM CONTEXT: Task Context]` hardcoded (instance.py:108) | test 2 hardcoded expected value matches actual byte-for-byte: `"[SYSTEM CONTEXT: Task Context]\n## Files\n- daemon/auth.py\n\n## Notes\nkeep API\n\nplease refactor the auth module"` | FIDELITY-OK |
| Provenance source | `f"internal_agent:{current_instance_id}"` | exact string pin `internal_agent:parent-instance` | FIDELITY-OK |
| Queue counting | `pending = READY ∪ (RETRYING ∧ next_retry_at ≤ now)`, `processing = PROCESSING` (repository.py:1000-1021); `get_queue_stats` is `async def` (manager.py:8968) | `AsyncMock` with realistic splits across all 10 new tests (0+1, 1+0, 2+1) — no impossible shapes | FIDELITY-OK |
| `has_live_graph_task` | SYNC, returns bool, docstring contract "MUST NOT raise" (manager.py:3973) | sync `MagicMock(return_value=True/False)`; no test relies on raising | FIDELITY-OK |
| await-ness across new tests | `enqueue_message` async → `AsyncMock` + `assert_not_awaited()`; `get_instance_info` sync → `MagicMock` (KeyError side_effect for the race test) | all pairings correct | FIDELITY-OK |
| Kwarg-pin rot | none of the 10 new tests use `assert_called_once_with`; only exact dict-equality on the current kwarg surface | intentional contract pin (TestReviveOnceGuard convention) | FIDELITY-OK |

**Log anchors (locked in task brief) — both verbatim in production code:**
- `instance.py:3428-3441`: `"agent_send_message busy-guard fallback routed via injection (context flattened inline)"` + `extra={… "routed_via": "injection_busy_fallback", "fresh_status": _fresh_status, …}` ✅
- `instance.py:3454-3465`: `"agent_send_message busy-guard rejection (target busy)"` + `extra={… "routed_via": "busy_reject", pending/processing counts …}` ✅ (new observability — base had no log on this path; additive only)

**Incident-closure proof at tool layer:** `TestBusyGuardInjectionFallback` tests 1-3 pin the exact c3d1a722 shape (busy running+live-graph target + context-bearing send → `set_injection` fires with flattened prefix, `enqueue_message` NOT awaited). The original rejection symptom is structurally gone for this configuration.

## Section 3 — Edge cases (a)–(e): ✅ PASS — 5/5 COVERED (ad-hoc verification not required)

| Edge | Covering test (node) | Mechanism |
|---|---|---|
| (a) `context={}` falsy → plain send, direct inject, fallback unreachable | `TestEnqueueOverrideForContext::test_empty_context_dict_still_injects` (:1768) | `if context:` short-circuit keeps `task_context_text=None` → fallback precondition `task_context_text is not None` (instance.py:3408) fails |
| (b) context + load_skill → load_skill wins, NO flatten/inject | `TestBusyGuardInjectionFallback::test_context_plus_load_skill_keeps_busy_rejection` (:5813) | `task_context_text is not None and not load_skill_requested` False → busy_reject |
| (c) KeyError cache-race on fresh status → fail-closed rejection | `TestBusyGuardInjectionFallback::test_status_race_keyerror_fails_closed_to_rejection` (:5880) | KeyError caught → `_fresh_status=None` → `None in INJECTION_ELIGIBLE_STATUSES` False → busy_reject, no crash |
| (d) status flips to `waiting_children` between guard and fallback → fallback does NOT fire | `TestBusyGuardInjectionFallback::test_non_running_target_keeps_busy_rejection` (:5859) | `INJECTION_ELIGIBLE_STATUSES = frozenset({"running"})` (constants.py:385-387) → not eligible |
| (e) free target (0/0) + context → durable enqueue with `task_context` metadata, fallback silent | `TestEnqueueOverrideForContext::test_context_routes_via_enqueue_with_metadata` (:1710, parametrized running+waiting_children) | free-target enqueue short-circuits with `metadata={"task_context": …}` before busy guard |

Mild non-blocking observation: test (b) could add explicit `enqueue_message.assert_not_awaited()` (result-text assertion already pins the path).

## Section 4 — Regression sweep (scoped): ✅ PASS — 0 branch-caused failures

**4a. `tests/unit/tools/` dir @ 6620cb7f:** **2904 passed / 5 failed / 0 errors / 5 skipped** in 54.09s. The ONLY failures are the 5 ledger `TestAccessMemoryArchive` nodes (`test_access_archive_valid_path`, `test_access_archive_path_traversal_rejected`, `test_access_archive_invalid_format_sanitized`, `test_access_archive_nonexistent_returns_not_found`, `test_access_normal_file_still_works`) — all `'Access denied'` class (`daemon/tools/access_memory.py` archive-deny defect; QUARANTINE family since 2026-08-20).

**4b. Independent base proof @ 75d7e2d1** (throwaway detached worktree `ae-w5-base-75d7e2d1`, main checkout untouched, worktree removed clean): `test_archive_lifecycle.py` = **26 passed / 5 failed** in 1.08s — the SAME 5 node ids, SAME error class → base-pre-existing **proven by this gate's own leg** (independent of the dev's claim). Branch-caused: **0**.

**4c. Cross-suite routing sweep (grep discovery + MUST-RUN packs):** the literals `agent_send_message` / `injection_busy_fallback` / `busy_reject` are pinned OUTSIDE `tests/unit/tools/` by exactly 2 files; both run green:
- `tests/unit/services/test_b1_wc_durable_send.py`: **11 passed / 1 failed** — the 1 failure is node- AND signature-identical to the active QUARANTINE.md 2026-09-12 env-defect row (`TestB1ConstitutionStatic::test_no_new_admission_state_writer`, FileNotFoundError on hardcoded `agents-ensemble-wt-wc-wake-resilience` cwd). WC durable-send routing pins all green.
- `tests/tools/test_send_message_status_guard.py`: **6 passed / 0 failed** (0.72s) — every legacy lane intact (enqueue-revive ×3, idle-enqueue, RUNNING→injection, direct `routed_via=="enqueue-revive"`); zero fallback-lane surprises.
- Remaining grep matches classified ADJACENT with evidence (HTTP router / facade / job-lane / FIFO-mechanics / chat-source / attestation lanes — none invoke the agent-tool send_message path). Notably `test_manager_enqueue_message_work_id_required.py`, `test_job_driven_enqueue_work_id_facade.py`, `test_api.py` = facade/HTTP lanes, unaffected (and manager.py + routers/messages.py diffs are EMPTY — see §5). PG-marked matches: 1 (`tests/postgres/test_wanderer_completion_reporting_pg.py`, wanderer lane — out of surface, not run).

## Section 5 — Diff hygiene: ✅ PASS (one acknowledged, non-blocking deviation)

- `git diff 75d7e2d1 6620cb7f --numstat`: **exactly 2 files** — `daemon/tools/instance.py` **+140/−9**, `tests/unit/tools/test_instance_tools.py` **+323/−1** (task expectation ~+150/−1 and ~+324/−1; insertion deltas within tolerance).
- `daemon/manager.py`, `daemon/routers/messages.py`, `daemon/tools/job_queue.py` diffs: **EMPTY** (explicitly verified).
- Commit list `75d7e2d1..6620cb7f`: single commit `6620cb7f` (clean amend; no `2dbc71dd` ancestor in range).
- **Deviation (−9 vs −1), enumerated line-by-line by W6:** 8/9 deletions are docstring prose (`_route_send_message` routed_via pseudo-value paragraph ×4; `create_instance_tools` Routing section ×1; Args `context` description ×1; Example outputs ×2) — doc-truth-mandated updates documenting the new fallback. **1/9 is a user-visible string-literal change**: the busy-guard rejection return-text appends `" Plain sends can inject mid-turn; load_skill requires the target to be free."` (214→290 chars; old sentence preserved verbatim as strict prefix — old substring matchers unaffected; all suites pinning the old text pass). Contract-positive routing guidance, but "Everything else unchanged" should be amended to name it.

## Verdict

| Section | Verdict |
|---|---|
| 1. Touched file re-run (207/0) | ✅ PASS |
| 2. Mock fidelity | ✅ PASS |
| 3. Edge cases (a)–(e) | ✅ PASS (5/5 covered) |
| 4. Regression sweep (dir + base leg + cross-suites) | ✅ PASS (0 branch-caused) |
| 5. Diff hygiene | ✅ PASS (1 acknowledged additive text deviation) |

### FINAL VERDICT: ✅ SHIP — no blockers

**Scope Decision:** verification scoped to the change surface (touched file + containing dir + 2 discovered MUST-RUN routing suites + base leg for the known-failure family). Full suite NOT warranted: surgical 2-file diff, no facade/HTTP/job-lane/manager changes (diffs empty), no PG surface.

**Non-blocking follow-ups:**
1. 🟢 Amend the fix's contract line to name the busy-reject result-text append (exact sentence above) — cosmetic/documentation.
2. 🟢 Post-merge, update the project critical-note TRAP "send_message with context or load_skill NEVER injects — mid-turn follow-ups must be PLAIN text" (incident c3d1a722): context-bearing sends to re-verified injection-eligible busy targets now INJECT via `injection_busy_fallback`; load_skill sends remain enqueue-only/rejectable.
3. 🟢 Optional test-strengthening: add `enqueue_message.assert_not_awaited()` to `test_context_plus_load_skill_keeps_busy_rejection`.
4. 🟢 `TestAccessMemoryArchive` ×5 remains quarantined (access_memory archive-deny defect, owner follow-up — unchanged by this branch).

**Documentation updated:** RESULTS/ (this file), PACKS.md (commission + outcome). QUARANTINE.md unchanged (family already ledgered; base-proof at 75d7e2d1 recorded here). LESSONS/: none warranted (no new defect class; numstat-vs-brief deletion audit followed existing DOC-TRUTH doctrine).
