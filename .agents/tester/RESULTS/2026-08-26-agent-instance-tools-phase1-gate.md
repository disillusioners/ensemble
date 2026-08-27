# Independent Verification Gate — agent-instance-tools Phase 1 (`send_message` tool upgrade)

- **Branch**: `feature/agent-instance-tools` @ `ab610121` (range `6ca9541c..ab610121`, 3 commits: 6253b6ba → ec8116ff → ab610121)
- **Date**: 2026-08-26
- **Role**: Independent verification gate (no prior run trusted; everything re-run from scratch)
- **Diff verified**: exactly 9 files — daemon/tools/instance.py, daemon/constants.py, daemon/routers/messages.py, daemon/tools/job_queue.py, tests/unit/tools/test_instance_tools.py (A), tests/unit/graph/test_injection_tool_pairing.py, tests/helpers/send_message_fixtures.py (A), tests/tools/test_send_message_status_guard.py, tests/tools/test_send_message_task_repo_guard.py. No extras.
- **Dispatches**: 24 workers (6 wave-1, 14 wave-2, 3 wave-3 base-evidence/deep-dive, 1 cleanup), 0 direct executions.

## VERDICT: ✅ PASS (as of fix commit `c2fde8f5`, 2026-08-27) — Phase 1 gate CLOSED for merge
*(Initial verdict @ ab610121: ❌ FAIL — 2 NEW stale-test failures + 1 narrow defect. Both fixed in `c2fde8f5` and independently re-verified — see §9. Merge itself deferred to post-Phase-2 per plan.)*

The feature itself is functionally validated end-to-end. The gate fails because two pre-branch tests in `tests/test_spawn_team_members.py` now fail and were not updated for the intended behavior change, and one narrow contract gap exists at `daemon/tools/instance.py:1959`. Everything else — ~11.6k test executions across 17 packs — is green or matches documented pre-existing baselines.

---

## 1. Blockers / defects

### 🔴 BLOCKER (fix-before-merge): 2 NEW stale-test failures in `TestSendMessageTeamMembersGate`

| Test ID | HEAD @ ab610121 | Base @ 6ca9541c | Classification |
|---|---|---|---|
| `tests/test_spawn_team_members.py:1347` `test_send_message_allows_pm_to_leader` | FAIL — `Expected 'enqueue_message' to have been called once. Called 0 times.` | **PASS** | NEW |
| `tests/test_spawn_team_members.py:1406` `test_send_message_target_without_agent_id_fails_closed` | FAIL — same signature | **PASS** | NEW |

- **Test file byte-identical** between base and HEAD (`git diff --stat 6ca9541c ab610121 -- <file>` empty; last touches 63067337/c3a520ee/7da44cb8, all pre-branch).
- **Root cause (deep-dive verified)**: STALE-TEST. Both tests stub `target_status="running"` with plain content → the NEW contract legitimately routes RUNNING → `manager.set_injection` (instance.py:2029-2065) — enqueue-override does not fire (no load_skill/context) → `enqueue_message` correctly never called. The tests still assert the OLD behavior (RUNNING→enqueue). The 3 gate-rejection tests in the class still pass (rejection precedes routing).
- **Not a production regression** — routing lands exactly where the phase contract says. But per gate rule "any NEW failure is a blocker", these block merge until the assertions are updated.
- **Minimal fix (NOT applied — report-only gate)**: in both tests replace `enqueue_message.assert_called_once()` with `set_injection.assert_called_once_with(<target_id>, <message>)` + `enqueue_message.assert_not_called()`; update test-2 docstring "gate skipped → enqueue_message runs" → "→ routing runs". ~5 lines, test-code only. (Alternative: pin the enqueue pipeline by stubbing status `idle` — changes coverage, less faithful.)
- **Do NOT quarantine** — deterministic, fix-pending, not flaky.

### 🟠 DEFECT (non-blocking, narrow): uncaught KeyError on split-cache not-found — `daemon/tools/instance.py:1959`

- The CR-2 team-membership gate calls `manager.get_instance_info(instance_id)` **unguarded**, AHEAD of `_route_send_message`. If `get_instance` (async, `_resolve_instance_id`) succeeds but `get_instance_info` (sync) raises KeyError (in-memory cache hit / lifecycle-store eviction race), the raw KeyError propagates to the agent instead of the friendly error.
- Violates plan delta-fix #1 contract on that path ("friendly error; NEITHER set_injection NOR enqueue_message called"). The helper's own KeyError-catch (instance.py:703-710) is correct but unreachable in this ordering.
- Repro (behavioral probe): `manager.get_instance = AsyncMock(return_value=MagicMock(...))`, `manager.get_instance_info = MagicMock(side_effect=KeyError)` → `await send_message(...)` raises KeyError instead of returning error string. Probe artifacts: `/tmp/ait-p1-verify/behavior_probe.py` (745-line REAL-tool harness), `probe_output.log`.
- Corroborated by mock-fidelity audit note N3. Suggested fix: wrap :1959 lookup in `try/except KeyError` returning the same `"Instance '<id>' not found; no message dispatched."` text.
- Primary unknown-id path (fully-missing instance) IS friendly — verified PASS (probe 11a).

### 🟢 Nits (non-blocking, cosmetic)
- Revive text interpolates raw status: "Instance was error — revived…" (grammatically awkward, contract-compliant).
- Bogus/unknown status string (e.g. "FLYING") silently falls to enqueue catch-all with no warning.

---

## 2. Targeted contract validation — ALL GREEN

| Suite | Result | Notes |
|---|---|---|
| `tests/unit/tools/test_instance_tools.py` | **81/81 PASS** (4.3s) | Per-class: RoutingHelper 12, ExhaustiveEnumRouting 12, InfoLogging 10, AuditBaseline 6, TrimCheck 5, EnqueueOverrideForLoadSkill 5, TerminalRevive 4, EnqueueOverrideForContext 4, NotFound 3, KConstantUniqueness 3, JAFP 3, EnqueueParity 3, DocstringParity 3, W3Stranding 2, RunningInjection 2, PausedReject 2, WaitingChildrenInjection 1, W4TerminalSurvive 1 |
| `tests/unit/graph/test_injection_tool_pairing.py` | **23/23 PASS** (0.47s) | **EXTENSION-NOT-REPLACEMENT PROVEN**: base(6ca9541c) = 16 functions, all 16 present unchanged (no rename, no parametrize edit); +5 new functions (+7 cases) = 23 |
| `tests/tools/test_send_message_status_guard.py` | **6/6 PASS** | NEW baseline (class TestSendMessageStatusGuard) |
| `tests/tools/test_send_message_task_repo_guard.py` | **5/5 PASS** | NEW baseline (class TestSendMessageTaskRepoGuard) |
| Full `tests/tools/` dir (cross-check) | **274/274 PASS** (17.6s) | Includes pre-existing `test_send_message_load_skill.py` 6/6 + `test_send_message_context_param.py` 20/20 — directly validates enqueue-override path |

**The "92" decomposition (resolved)**: the file collects **81**, not 92 — git history shows it never had 92 (6253b6ba=71 → ec8116ff=81 → ab610121=81; review batch added 0 tests, structural only). The leader's "92" = **81 (instance_tools) + 11 (two guard suites) = 92**, and 92 + 23 (pairing) = **115/115 = the council-review targeted set exactly**. Nothing lost; labeling artifact only.

## 3. Mock-fidelity audit (TrueAuto) — CLEAN

**0 MOCK-DIVERGENT findings.** Verified against real code: `set_injection` sync dict-return (manager.py:2342), `get_instance` async KeyError (lifecycle.py:2825), `get_instance_info` sync dict KeyError (lifecycle.py:3352), `enqueue_message` async AsyncMessageResult kwargs, `get_queue_stats` async dict, lowercase status casing consistent. Unknown-id exception design is two-layer (ValueError primary / KeyError defense-in-depth) — both layers correctly simulated with correct mock async-ness. No StaticPool in new fixtures; no test patches `_route_send_message` (not tautological); verbatim text asserts byte-exact.

Constants hoist: **PASS** — `INJECTION_ELIGIBLE_STATUSES` one definition (constants.py:218) + 3 import consumers, zero inline tuples, zero residue; `TERMINAL_INSTANCE_STATUSES` one public definition, old `_TERMINAL_STATUSES` local gone. (3 pre-existing private forks in unrelated services noted as future consolidation candidates.)

LOW notes: N1 `enqueue_message_job` mocked sync (inert — never called); N2 stale comment re `get_injection_count`. INFO notes: N3 = the :1959 defect above; N4 watcher happy-path untested; N5 no send→inject→drain integration test; N6 CR-2 gate structurally no-op in guard fixture.

## 4. Behavioral spot-check (REAL tool function, /tmp harness) — 34/35 PASS

Trim-check-first ordering (incl. PAUSED+empty → trim wins), RUNNING/WAITING_CHILDREN → injection (queue-busy guard correctly DROPPED, get_queue_stats not consulted), load_skill/context → enqueue-override with correct `<meta>`/task_context payloads, 4× terminal revive with prefix text, PAUSED verbatim byte-exact reject + zero state mutation, IDLE/WAITING/QUEUED enqueue parity + busy-guard fires after status check, unknown-id friendly error + zero dispatch calls (11a), 100k-char content no truncation/no exception, exhaustive 10-state sweep + bogus-status catch-all (no crash), exactly ONE INFO provenance log with all structured fields incl. `source="internal_agent:{caller}"`. The only FAIL is 11b = the :1959 defect above.

## 5. Full regression — pack table vs baseline

**Scope Decision**: full unit-tree regression run — warranted (core agent-messaging tool change + explicit independent-gate mandate; cross-cutting routing change). Excluded: Release-Gate e2e (live daemon + real LLM), `-m postgres`, frontend, bash ops packs (untouched subsystems). ~11.6k test executions at HEAD + 2 base-verification runs (~1,006 tests at 6ca9541c).

| Pack | Collected | Passed | Failed | Notes |
|---|---|---|---|---|
| tools_suite_unit_test.sh | 929 | 924 | **0** | 5 archive quarantines deselected; 23s |
| job_queue_tools_unit_test.sh | 77 | 72 | **1** | = leader-known family exactly (job_create source-override; job_continue ×4 deselected) — baseline envelope MATCH |
| instance_messaging_queue_routing | 16 | 16 | **0** | pack grew 8→16 upstream, all green |
| instance_messaging_regression | 28 | 28 | **0** | baseline exact |
| concurrency_atomic (ensure Critical) | 172 | 98 (+74S) | **0** | baseline 91P/74S + 7 upstream adds |
| api_unit_test.sh | 221 | 213 (+8S) | **0** | baseline exact |
| injection bundle (ad-hoc, 6 files) | 75 | 71 | **0** | 4 _ManagerStub quarantines deselected |
| unit_subdirs (services/routers/rag/repositories) | 1037 | 1029 | **8** | job_queue_proxy_phase1 — PRE-EXISTING (base-evidenced) |
| unit_[a-h] sweep | 1221 | 1202 | **15**+4E | ALL PRE-EXISTING (base-evidenced) |
| unit_[i-r] sweep | 2541 | 2510 | **8** | ALL PRE-EXISTING (base-evidenced) |
| unit_[s-z] sweep | 1022 | 957 | **50**+2E | ALL PRE-EXISTING (base-evidenced — watchover `default_streaming` cascade ×45 + webfetch ×2E + wanderer ×2 + validate_agent_id ×1) |
| top-[a-h] sweep | 918 | 867 | **3** | 2 agents_api isolation + 1 enqueue_shared title — ALL PRE-EXISTING |
| top-[i-r] sweep | 1612 | 1505 | **74** | 67 migration family (documented) + 1 job_create (known) + 4 _ManagerStub (quarantined) + 2 diff-attributed (innate_skills leader `question`, coder llm_models — zero `agents/` files in diff) |
| top-[s-z] sweep | 1423 | 1372 | **14** | 9 migration family + 3 pre-existing + **2 NEW stale** (Section 1) |

**NEW failures total: 2** (both Section-1 stale tests). All ~177 other failures+errors reproduce at base `6ca9541c` with identical signatures (byte-identical test files) or are documented quarantine families. The watchover `default_streaming` cascade (45 tests) is **pre-existing at base** — root: `ThinkingChatOpenAI.default_streaming` ClassVar + clean_llm_config injection interacting with watchover's LLM stubs; surfaced only because this gate swept the full tree.

## 6. ensure.md Core validation — PASS (scoped)

| Requirement | Result | Evidence |
|---|---|---|
| Critical: no regressions in changed packs | ✅ PASS | all changed-area packs 0F (jqt within documented family) |
| Critical: deadlock/concurrency integrity pack | ✅ PASS | 98P/74S/0F |
| Critical: no sync DB on event loop | ✅ PASS | covered by same pack |
| Critical: dev.sh `--timeout-graceful-shutdown 10` | ✅ PASS | dev.sh:99,102 |
| Important: async-await callers | ✅ N/A-verified | zero `async def` changes in daemon/ diff (grep) |
| Important: original deadlock scenario | ✅ PASS | test_deadlock_fix.py in concurrency pack |
| Nice-to-have: no dead code from fix | ✅ PASS | hoisted constants leave zero residue (grep-evidenced) |

Release Gate NOT triggered (not a release/big-architecture change; e2e requires live daemon).

## 7. Required actions before merge

1. **[BLOCKER]** Update the 2 stale assertions + 1 docstring in `tests/test_spawn_team_members.py` (TestSendMessageTeamMembersGate) to the new RUNNING→injection contract (~5 lines, test-only). Re-run the file: expect 5/5.
2. **[Recommended]** Guard `daemon/tools/instance.py:1959` `get_instance_info` with `try/except KeyError` → friendly not-found text (restores delta-fix #1 on the split-cache path).
3. **[Optional hygiene]** N1 AsyncMock for `enqueue_message_job`; N2 fix stale comment; revive-text status casing.

No re-run of the full gate needed after (1) — scope the re-verification to `tests/test_spawn_team_members.py` + (if (2) applied) `tests/unit/tools/test_instance_tools.py::TestNotFoundInstanceId` + a probe 11b re-check.

## 8. Worker instances

62eeb667 (P0), daafcf3f (instance_tools), ceb55f0a (graph), e5fe02e3 (guards), 02f05307 (mock-fidelity), c75ce1d2 (behavioral probe), f4639651 (tools_suite), 2617506c (jqt), bccefb2c (msg-routing), e0489a3f (msg-inj), 7392a555 (concurrency), 520216e2 (api), c316afd1 (inj-bundle), e477e38f (unit-subdirs), 73956831 (unit-ah), fffdb9e9 (unit-ir), 9c9296e6 (unit-sz), 0c073d3f (top-ah), 62311cd4 (top-ir), 1acb6658 (top-sz), f409cbb9 (base-watchover), 3538e046 (base-rest), c35ebf1a (deep-dive), b975c270 (cleanup).

Re-verification wave @ c2fde8f5 (2026-08-27): d76fb85b (stale-tests), 1155aba1 (CR-2 + fails-without-fix proof), 47c793f6 (blast sanity).

---

## 9. Re-verification @ c2fde8f5 — both blockers CLOSED, verdict flipped to PASS

Fix commit `c2fde8f5` (stacked on `ab610121`; chain `6ca9541c → 6253b6ba → ec8116ff → ab610121 → c2fde8f5`), exactly 3 files. Scope per my own §7 prescription — no full re-gate. 3 worker dispatches, 0 direct executions.

### Blocker 1 — stale tests: CLOSED ✅
- `tests/test_spawn_team_members.py`: **44/44 PASS** (3.25s); `TestSendMessageTeamMembersGate` **5/5** (both previously-failing tests green).
- Diff-hunk audit: ONLY the 2 named tests modified (+ trivial EOF newline) — no other test weakened. (Stat reads +34/−12 vs briefed +19/−13 — cosmetic; hunk/function scope verified identical.)
- Assertions now pin the Phase 1 contract: `set_injection.assert_called_once_with("target-leader-id", "please execute task X")` / `("target-id", "hello")` + `enqueue_message.assert_not_called()`; both docstrings reference routing/injection (stale "enqueue_message runs" gone).

### Blocker 2 — CR-2 gate defect: CLOSED ✅
- `daemon/tools/instance.py`: exactly ONE hunk (+14/−1) wrapping `get_instance_info` in `try/except KeyError → return f"Instance '{instance_id}' not found; no message dispatched."` — same text family as the routing-helper fallback (:703-710, :1978). Nothing else changed.
- `tests/unit/tools/test_instance_tools.py`: **82/82 PASS** (4.41s); delta = `TestNotFoundInstanceId` 3→4 (`test_split_cache_race_returns_friendly_error`, :488).
- **Fails-without-fix PROOF** (worktree: tests@c2fde8f5 + instance.py@ab610121): new test **FAILED-as-expected** — raw `KeyError: 'vanished-id'` propagated from instance.py:1959 instead of friendly text. True locking test, not a false positive. Worktree removed; main repo re-verified untouched.

### Blast-radius sanity: PASS ✅
4 direct send_message consumer suites (status_guard 6/6, task_repo_guard 5/5, context_param 20/20, load_skill 6/6) = **37/37** — the +14/−1 production guard regresses nothing.

### Final tally @ c2fde8f5
| Item | Status |
|---|---|
| Blocker 1 (stale tests) | ✅ fixed + verified (5/5 class, 44/44 file) |
| Blocker 2 (split-cache KeyError) | ✅ fixed + verified (82/82, fails-without-fix proven) |
| Blast radius (37 direct consumers) | ✅ 37/37 |
| Everything from §2-§6 (115/115 targeted, 0 mock-divergent, 34/35→35/35 behavioral, ensure.md Core, packs baseline-exact) | ✅ unchanged — fix touched only the two fixed areas |

**Phase 1 overall: ✅ PASS — gate closed for merge (merge deferred to post-Phase-2 per plan).** Remaining open items are all pre-existing-at-base families now documented in QUARANTINE.md (not this branch's responsibility).
