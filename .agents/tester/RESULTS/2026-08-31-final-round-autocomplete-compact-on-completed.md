# Final Test Round — Branch A (slash-command-autocomplete) + Branch B (compact-on-completed)

Date: 2026-08-31 (19:25–20:10 UTC)
Posture: repo READ-ONLY, report-only, zero commits, dual-layer timeouts on every pack/stage, HEAD gate before every run, port 8088 untouched throughout.
Worktree at end: `feature/compact-on-completed` @ `e7a8ba99390665d6cca8758494f5d25bfc2f46d9` (as instructed — merge flow continues from there).

## VERDICTS

| Branch | Commit | Verdict |
|---|---|---|
| A — `feature/slash-command-autocomplete` (command autocomplete dropdown) | `16eacfbb` | ✅ **SHIP** — no blockers |
| B — `feature/compact-on-completed` (/compact on COMPLETED instances) | `e7a8ba99` | ✅ **SHIP** — no blockers |

---

## Branch A — feature/slash-command-autocomplete @ 16eacfbb (chain 39f5d257 → 4f729f43 → 16eacfbb on latest b379e576)

HEAD gate: `16eacfbb6070466a65856104f3e99a7e94c1bce5` verified before AND after every run (no drift).

### A1. Full FE suite — PASS (exact)
- Command: `cd frontend && CI=1 timeout 240 npm test -- --no-cache` (GNU timeout 9.10 at /opt/homebrew/bin/timeout)
- Result: **64 suites / 2342 tests, 0 failed** — `Test Suites: 64 passed, 64 total / Tests: 2342 passed, 2342 total / Time: 9.401 s`
- Delta vs parent b379e576 baseline (62/2273): +2 suites / +69 tests — exactly the autocomplete scope (`message-input.component.autocomplete.spec.ts`, `slash-command-palette.util.spec.ts`).
- Evidence: `/tmp/tester-evidence/branch-a-fe/jest-full.log`

### A2. Playwright — PASS (exact)
- Command: `cd frontend && CI=1 timeout 280 npx playwright test slash-command-compact --reporter=line`
- Result: **16 passed (2.0m / 123 s)** — was 15 at parent; +1 = AC1.
- AC1 inclusion proven: run slot [2/16] = `e2e/slash-command-compact.spec.ts:260:7 › AC1: autocomplete — "/" opens palette; ArrowDown+Enter completes to /compact and submits`. Full 16-title list captured in report; webServer auto-start clean (no Errno 48).
- Evidence: `/tmp/tester-evidence/branch-a-fe/playwright.log`

### A3. Live dropdown smoke — PASS 5/5 (after adjudication; stack at 16eacfbb, real LLM)
| Check | Verdict | Evidence |
|---|---|---|
| SMK-1 '/' opens palette listing /compact **with description** | PASS | `/tmp/tester-evidence/branch-a-smoke/smk-1.png` — verbatim "Compact this instance's message history", aria-expanded/activedescendant correct, 0 POSTs |
| SMK-2 '/co' filters | PASS | `smk-2.png` — 1 option, 0 POSTs |
| SMK-3 ArrowDown+Enter sends /compact, card appears | PASS* | `smk-3-network.json` (exactly 1 POST `{"content":"/compact"}`) + `round2/rv1-card.png` — card "waiting → success" on RUNNING instance, command accepted, terminal success (noop/below_floor, 332 tok, 1.3s) |
| SMK-4 '//x' → NO dropdown, NO POST | PASS | `smk-4.png` — palette DOM count 0 |
| SMK-5 Tab-accept inserts WITHOUT sending | PASS* | `smk-5-diagnostic.json` — input becomes `/compact ` + palette closed + 0 POSTs |

*Adjudication of the two initial "fails" — both were TEST-DESIGN ARTIFACTS, FE correct:
- SMK-3: first attempt targeted an instance whose setup turn had already COMPLETED; on this branch (without compact-on-completed) terminal instances correctly reject /compact at ack (pinned by SC14a, green in A2). Re-run against a verified RUNNING instance (b66c514b): exactly 1 POST, ack `state=accepted`, command_id minted, waiting card → terminal success.
- SMK-5: trailing space is BY DESIGN — pinned at `message-input.component.autocomplete.spec.ts:211,214-215,233-234` (`expect(textarea.value).toBe('/compact ')` + `sent` length 0) and `slash-command-palette.util.spec.ts:147-148` (`slashAcceptText(...) === '/compact '`); source `slash-command-palette.util.ts:57-59`.

Non-blocking observations (pre-existing, both branches): NG0100 relative-time console noise; Plane iframe CSP violation; `ng serve` binds IPv6 `[::1]:4199` (probe with localhost, not 127.0.0.1).

---

## Branch B — feature/compact-on-completed @ e7a8ba99 (exactly 1 commit ahead of merge-base b379e576)

Production surface: 3 daemon files +126/−44 (`command_dispatcher.py`, `compact_executor.py`, `_checkpoint_utils.py`) + 4 test files. HEAD gate passed before/after every run.

### B1–B3. Scoped BE suites — 477/477, 0 failed (all counts EXACT)
| Suite | Command (repo root) | Result |
|---|---|---|
| routers | `timeout 120 .venv/bin/pytest tests/unit/routers/test_slash_commands_router.py --tb=short -q` | **40 passed** (1.69s) |
| dispatcher | `timeout 120 .venv/bin/pytest tests/unit/services/test_command_dispatcher.py --tb=short -q` | **76 passed** (1.92s) |
| executor | `timeout 120 .venv/bin/pytest tests/unit/services/test_compact_executor.py --tb=short -q` | **64 passed** (7.08s) — +23 vs prior gate (41) |
| revive-brick | `timeout 240 .venv/bin/pytest tests/unit/services/test_compact_executor_revive_brick_e2e.py --tb=short -q` | **7 passed** (1.61s) — +2 new canaries |
| lifecycle | `timeout 120 .venv/bin/pytest tests/unit/services/test_compact_executor_defect1_pause_resume_lifecycle.py --tb=short -q` | **3 passed** (1.20s) |
| compaction | `timeout 180 .venv/bin/pytest tests/unit/test_compaction.py --tb=short -q` | **92 passed** (2.80s); `git diff b379e576..HEAD -- daemon/compaction.py` EMPTY |
| instance-tools | `timeout 300 .venv/bin/pytest tests/unit/tools/test_instance_tools.py --tb=short -q` | **195 passed** (9.57s) |

Total: **477 passed / 0 failed / 0 timeout.** Note: the request said "~507 total"; the sum of its own listed per-suite counts is 40+76+64+7+3+92+195 = **477** — every per-suite number matched exactly; ~507 was an approximation in the request, not a gap.

Frozenset pin verified: `TestAuditBaseline::test_terminal_instance_statuses_constant_exists` at `tests/unit/tools/test_instance_tools.py:199-201` — `assert TERMINAL_INSTANCE_STATUSES == frozenset({"completed","terminated","error","failed"})` (+ module-local alias gone + identity assertion). Targeted `-v` re-run green.
Evidence: `/tmp/tester-evidence/branch-b/suites/`

### B4. Canary REAL-pin audit — both REAL PINS; canonical UNTOUCHED
New canaries (revive-brick suite, `TestExecutorCompactOnCompletedRealGraph`, lines 1131–1693):
1. `test_completed_instance_compacts_and_revive_send_runs_agent` — plain compile (production config)
2. `test_completed_compact_interrupt_before_no_as_node_immunity` — `interrupt_before=["agent"]` (brick-exposing config)

Audit matrix (evidence `/tmp/tester-evidence/branch-b/canary/analysis-notes.md`):
- Real LangGraph: YES both — `_RealLangGraph()` restores real langgraph modules, real `AsyncSqliteSaver` on file-backed `tmp_path/canary_completed.db`, real `ainvoke/aget_state/aupdate_state/astream`, real agent node.
- Variant A pinned: YES — `aupdate_state_call_count == 2` and `"as_node" not in call kwargs` asserted (matches production `compact_executor.py:1565-1577`).
- Revive completes: YES — post-compact `astream` runs the agent (`runs == ["ran"]`) = anti-brick.
- interrupt_before re-prime: YES — canary 2 asserts `st_primed.next == ("agent",)` after /compact, then runs, then `st_done.next == ()`.
- Honest disclosure: `status="completed"` is provided via a MagicMock of `mgr._lifecycle_service.get_instance_info` (file-wide O17 allowance, same discipline as the prior 5 canaries; production reads status through exactly that seam). **The live run below is the authoritative real-DB status proof.**
- Both pass solo: `2 passed in 1.10s`.

Canonical check: `git diff b379e576..HEAD -- daemon/constants.py` = **EMPTY**. `TERMINAL_INSTANCE_STATUSES` canonical def `daemon/constants.py:250-255` unchanged; all downstream consumers keep canonical imports. Branch adds isolated local `COMPACT_REJECT_STATUSES = {"terminated","error","failed"}` (`command_dispatcher.py:113/123-125`, defense-in-depth `compact_executor.py:512`) instead of mutating the canonical — clean isolation.

### B5. ensure.md Core — PASS (scoped; Release Gate NOT triggered: scoped feature branch, not release/architecture)
- C1 changed packs green (B1–B3 evidence) · C2+C3 `timeout 300 bash test/packs/concurrency_atomic_unit_test.sh` → **98P/0F/74S in 8.43s** (baseline-exact) · C4 dev.sh:102 `--timeout-graceful-shutdown 10` present · Important awaits **8/8** awaited. No contradictions; no Improvement Notices.
- Evidence: `/tmp/tester-evidence/branch-b/ensure/`

### B6–B8. LIVE verification (stack at e7a8ba99, real LLM, BE on 8079) — 3/3 PASS
- **LIVE-1 /compact ACCEPTED on COMPLETED**: instance 305e29f7 → real turn → `completed` (poll 27) → history BEFORE 4 rows → POST `/compact` → ack **`state="accepted"`**, command_id minted → terminal **`phase="success"`** (67 ms; `compacted_type="noop"`, `noop_reason="below_floor"`, est. 781 tok — VALID per known floor, prior finding #8 deferred by leader) → history AFTER identical (DELTA=0) → status **still `completed`**.
- **LIVE-2 revive-on-send anti-brick**: same instance, normal message → `completed → running → completed`, real reply "alive" delivered, synthetic-system message preserved with SAME message_id (pre-compact context not bricked). N2 pre-existing risk NOT observed this session.
- **LIVE-3 TERMINATED still rejects**: instance a113f607 (idle→DELETE→`terminated`) → POST `/compact` → verbatim `{"state":"rejected","reason":"terminal_instance","command_id":null,"detail":"Send a message to start a new turn, then /compact.",...}`, `commands/active` exists:false before AND after (no card minted), status unchanged.
- **error/failed live**: SKIPPED per task budget (no cheap reliable live driver) — covered by unit pins: dispatcher suite pins terminal-reject ×4 statuses (76/76 green); same single membership check vs `COMPACT_REJECT_STATUSES` (`command_dispatcher.py:123-125`).
- Cleanup: 4 instances DELETE'd (4×200), BE PID tree killed, port 8079 free, 8088 untouched, HEAD re-verified.
- Evidence: `/tmp/tester-evidence/branch-b/live/` (56 files; `L0-REPORT.md` index)

### Report-only anomalies (no action required)
- 🟢 A-1: DELETE on a `completed` instance returns `{"terminated":true}` but leaves status column `completed` (enumerate-first terminal-skip branch, `instance_lifecycle.py:1880-1904`) — documented; test shape adjusted (idle→DELETE) to land a true terminated row.
- 🟢 A-2: harness glitch in first LIVE-1 poll-parse (read wrong JSON key; recovered from captured raw responses; no verdict impact).
- 🟢 Pre-existing console noise (NG0100, Plane CSP) — present on parent.

## Session mechanics
9 dispatches (incl. 1 adjudication re-run), 8 worker instances, ≤4 concurrent, 1 revive-once refusal handled by replacement spawn. Zero code changes, zero commits. Pre-existing dirty files (.agents/* scratch, agents/ari/user.md) untouched throughout. Worktree left on `feature/compact-on-completed` @ `e7a8ba99`.
