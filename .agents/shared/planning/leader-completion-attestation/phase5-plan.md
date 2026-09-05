# Phase 5: Test Matrix (MVP — In-Graph Nudge)

Date: 2026-09-05
Author: planner[v2] via plan-creation worker (test-layer reconciliation pass; reviewer CHANGED_REQUESTED)
Branch: `feature/leader-completion-attestation`
Companion: [`plan-overview.md`](./plan-overview.md), [`phase1-plan.md`](./phase1-plan.md), [`phase2-plan.md`](./phase2-plan.md), [`phase3-plan.md`](./phase3-plan.md), [`phase4-plan.md`](./phase4-plan.md), [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md), [`architecture-recommendation.md`](./architecture-recommendation.md), [`research-findings.md`](./research-findings.md)

---

## Objective

Ship the full test matrix for the **MVP deny path = in-graph checkpoint-durable `HumanMessage` nudge** (R1, canonical per `architecture-recommendation.md` §3, §5 Phase-5 row). Tests verify the gate denies → injects nudge → routes back into the SAME execution → leader continues → eventually attests → allowed END. **NO `manager.enqueue_message` on deny, NO revive on deny** (C5 ruling — explicit negative assertion per task). Mode is the **tri-state env `ENSEMBLE_LEADER_ATTESTATION_MODE ∈ {off, dry, enforce}`** (default `dry`); each test pins its mode in its docstring. Recovery-injection, JAFP no-JobItem, and facade-forwarding tests for the durable `manager.enqueue_message` path are **RELOCATED** to [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) (post-soak backstop — out of MVP scope).

Entry criterion: Phases 1–4 are merged; D1–D10 are decided; D1=B (in-graph pre-END interception) is wired in both `create_should_continue` branches (graph.py:2718–2721 and live wiring at graph.py:6459–6484); the deny-path nudge node exists and routes back to `agent`.

Exit criterion: All unit + integration tests pass; AC-E2E-1 through AC-E2E-8 (including the AC-E2E-6 recorded dry-log corpus adjudication) pass under the **in-graph nudge** deny path; facade-forwarding for the MVP deny path is N/A by construction (no new kwarg added to `enqueue_message` in MVP); the documented Phase-6 recovery injector remains out of MVP; quarantine-free (no new pre-existing failures introduced).

---

## Entry Criteria

- Phases 1–4 are merged to `feature/leader-completion-attestation`
- D1=B implemented and wired in BOTH branches of `create_should_continue(language_check_enabled)` (graph.py:2718–2721, graph.py:6459–6484)
- The in-graph nudge node exists and routes the deny path back to `agent` (same execution; no `manager.enqueue_message`, no revive)
- All MVP dependencies (tool, scanner, gate, ledger, resolver, observability) are merged
- Default mode at boot is `dry` (`ENSEMBLE_LEADER_ATTESTATION_MODE` blank → `dry` per WC-wake resolver precedent; the resolver code in Phase 4 task 4.1 mirrors that shape)

---

## Mode-Pinning Convention (applies to every test in this file)

Every test in this file MUST declare its mode in a one-line module-level docstring header or `@pytest.mark.parametrize` decorator on the `MODE` env. The tri-state semantics are **not** boolean — they are three distinct behavioral modes:

| `ENSEMBLE_LEADER_ATTESTATION_MODE` | Gate evaluated? | Side effects on deny? | END allowed? | Test footprint |
|---|---|---|---|---|
| `off` | **No** — gate is bypassed entirely | None (no DB writes, no logs, no nudges) | Yes, unconditionally | Tests 5.10 (off = no evaluation), 5.11 (must-not-break surfaces in off) |
| `dry` | **Yes** — gate evaluates; decision is `dry_log` per the canonical `Decision` enum (Phase 4 task 4.5) — `dry_log` carries the deny predicate (`pending_children == 0 AND queued_or_expected_wakeups == 0`) in the canonical log schema, not in the decision value | None (zero side effects; no counter increment, no nudge, no escalate) | Yes, unconditionally | Tests 5.4 (dry decision-logging), 5.10 (dry = evaluated + logged, END allowed), 5.11 (must-not-break in dry) |
| `enforce` | **Yes** — gate evaluates; decision per the canonical enum: `allowed`, `denied`, `terminal_after_bound`, or `allowed_legitimate_pending_wakeup` (R2-allow) | **Full** (only on `denied`/`terminal_after_bound`) — nudge injected + counter incremented + escalation flag on bound | Yes **only** when decision is `allowed` or `allowed_legitimate_pending_wakeup`, OR after `terminal_after_bound` | Tests 5.5 (E2E flagship), 5.6 (bound escalation), 5.7 (delegation), 5.8 (fail-open), 5.11 (must-not-break in enforce) |

**Tests MUST NOT mix modes** — a single test pins one mode and asserts behavior under that mode only. Parameterized matrices in test 5.10 / 5.11 sweep modes as separate sub-cases.

**Forbidden phrase** (per canonical design, R1): any test that asserts `deny ⇒ manager.enqueue_message` was called. The MVP deny path is in-graph nudge ONLY. The relocated C fast-follow tests live in [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) §6.1–§6.3.

---

## Tasks

### 5.1 — Unit test: scanner

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/test_attestation_scanner.py` (new) |
| **Description** | Cases: (a) attested within window → True; (b) attested outside window → False; (c) text-only claim → False (AC-2.3 — leader says "attesting" but tool_calls is empty); (d) non-attestation tool calls (subtree_status, instance, etc.) → False; (e) window bounds with 1000-message state — scanner must inspect only last N=3 AIMessages (AC-2.5); (f) summary message handling (compaction-folded tool_call — D10(b) edge case, see also task 5.12 below); (g) `additional_kwargs` markers excluded (e.g., `language_check_reminder=True`). Pure-function tests; no fixtures beyond synthetic message lists. **Scanner diagnostics surfaced**: `window_truncated`, `summary_seen`, `messages_scanned` (count of AIMessages inspected) — required for the dry decision-logging schema (test 5.4) and observability (test 5.16). |
| **Decision tags** | [D10(a), D10(b)] |
| **Test notes** | All cases from `requirements.md` §AC-2.1..AC-2.5. |

### 5.2 — Unit test: gate decision (pure function — mode-aware)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/test_attestation_gate.py` (new) |
| **Description** | Pure-function tests of `(scanner_result, pending_children, queued_or_expected_wakeups, denied_count, mode, scope) → Decision` — the R2 inputs (`pending_children: int` and `queued_or_expected_wakeups: int`) are part of the signature per Phase 2 task 2.2 and Phase 4 task 4.5 (canonical schema, single source of truth). Cases: (a) `mode="off"` → `{allowed, mode_off=True}` regardless of scanner; (b) `mode="dry"` + missing attestation + R2-deny-predicate satisfied → `{dry_log, allow_end=True, side_effects=False}`; (c) `mode="dry"` + attestation present → `{allowed, side_effects=False}`; (d) `mode="enforce"` + scope inapplicable (non-leader parent per D3) → `{allowed}`; (e) `mode="enforce"` + attested → `{allowed, reset_denied_count=True}`; (f) `mode="enforce"` + missing + pending_children=0 + queued_or_expected_wakeups=0 + denied=N + N+1 ≤ bound → `{denied, next_denied_count=N+1, nudge_message=<server-authored constant>}`; (g) `mode="enforce"` + missing + pending_children=0 + queued_or_expected_wakeups=0 + denied=bound → `{terminal_after_bound, set_escalated=True}`; (h) **R2-allow** `mode="enforce"` + missing + (pending_children > 0 OR queued_or_expected_wakeups > 0) → `{allowed_legitimate_pending_wakeup, side_effects=False, reset_denied_count=False}` (per Phase 4 task 4.5 canonical enum and requirements AC-3.3 / AC-E2E-1b); (i) boundary cases (`denied_count=0` → first deny; `denied_count=bound` → terminal; `denied_count=bound-1` → last nudge). NO test asserts `enqueue_message` is called — that contract lives at the gate-call-site (task 5.5), not the pure function. |
| **Decision tags** | [D2] (tri-state), [D3], [D5] |
| **Test notes** | AC-3.1, AC-3.2, AC-11.1 verified. |

### 5.3 — Unit test: in-graph nudge injection (graph seam, not `enqueue_message`)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/test_attestation_nudge_inject.py` (new) |
| **Description** | The deny path is implemented as a graph node that mutates `state.values["messages"]` to append a `HumanMessage` with `additional_kwargs={"attestation_nudge": True, "attestation_nudge_denied_count": N}` and returns the plain state-update dict `{"messages": [nudge], "attestation_nudge_denied_count": N}` (mirroring the in-file `language_check` plain-dict routing-return precedent at `daemon/graph.py:2666-2685`; NO `Command` import in `graph.py` — the route-back to `agent` is wired at graph-build time via `add_conditional_edges("attestation_gate", should_end_attestation, {"retry": "agent", END: END})`, mirroring `add_conditional_edges("language_check", should_end_language_check, {"retry": "agent", END: END})` at `daemon/graph.py:6473-6476`). Tests assert: (a) the returned `HumanMessage` content equals the server-authored constant text from NFR-6 (canonical: `"The work is not yet finished — check current progress and continue."`); (b) `additional_kwargs` carries the marker; (c) the returned state's `"messages"` list contains exactly the nudge and the conditional-edge routing sends execution back to the `agent` node (route-back equivalence to `Command.goto == "agent"`, asserted by stubbing `should_end_attestation` to return `"retry"` and asserting the graph engine dispatches the `agent` node next); (d) NO call to `manager.enqueue_message` (mocked and `assert_not_called`); (e) NO instance-row mutation outside the gate's column writes (mocked instance repo, `increment_attestation_denied_count.assert_called_once_with(...)`); (f) NO call to any `revive`/`send_message` helper. **This is the MVP's negative assertion test** — it locks the in-graph-only contract at the unit seam. |
| **Decision tags** | [AC-4.1], [D5] (counter increment ordering), [C5 ruling] |
| **Test notes** | AC-3.1 verified at the nudge-injection seam. The recovery-injection / durable-path test (AC-4.5 — RELOCATED to Phase 6; requirements.md:300-305) lives at [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) §6.2. |

### 5.4 — Unit test: dry-mode decision logging (no side effects)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/test_attestation_dry_logging.py` (new) |
| **Description** | Asserts the dry-mode log shape — references the canonical schema at Phase 4 task 4.5 VERBATIM (do not restate field-by-field). For a `mode="dry"` evaluation with missing attestation, the gate emits **exactly one** structured log entry with the canonical fields populated, including `decision: "dry_log"` (canonical enum) and `pending_children: int`, `queued_or_expected_wakeups: int` (R2 inputs). Diagnostic flags `scanner_window_truncated: bool` and `scanner_summary_seen: bool` are populated from the scanner's run. Assertions: (a) all canonical schema keys are present and types match (asserted via `isinstance` per field; see Phase 4 task 4.5 for the field list); (b) `messages_scanned > 0` is always populated (O8 — per the post-reconciliation fix that dropped the `aget_state` thread-id-only discipline from MVP per Phase 2 task 2.3 / CR-1 in-node seam); (c) `pending_children: int` and `queued_or_expected_wakeups: int` are populated from the two NEW manager facade methods (`manager.count_pending_children` + `manager.get_queued_or_expected_wakeups`, per Phase 2 task 2.3 — CR-1); (d) `attest_seen_outside_window` is `True` when the scanner inspected >N AIMessages and DID find a stale `attest_completion` outside the window (per R2 — this is a separate flag, NOT a deny trigger, but the dry log carries it for operator adjudication); (e) `decision == "dry_log"` (canonical enum) and `dry_no_side_effects=True` is hard-coded in the log entry (the canonical schema does NOT carry a separate `dry_no_side_effects` key — the no-side-effects contract is the behavior, asserted by test 5.10 case (b) below); (f) zero DB writes (mock instance repo and `assert_not_called`); (g) zero `enqueue_message` calls (mocked and `assert_not_called`). |
| **Decision tags** | [D8] (dry observability), [O8] (messages_scanned field), [R2] (pending_children / attest_seen_outside_window) |
| **Test notes** | The decision-logging schema is the operator's adjudication source for dry→enforce promotion (`architecture-recommendation.md` §1 D2). |

### 5.5 — Integration test: flagship E2E — deny → nudge → route-back → attest → allow

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_in_graph_nudge_flow.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | AC-E2E-1 (rewritten per R1). Leader instance with a child that has just sent an "in progress" report. Leader LLM hallucinates a final assistant message and routes END without calling `attest_completion`. Sequence under `ENSEMBLE_LEADER_ATTESTATION_MODE=enforce`: (1) gate evaluates, scanner returns False; (2) `attestation_denied_count` 0→1 (DB column); (3) **gate node injects in-state `HumanMessage` nudge with `attestation_nudge=True` marker and returns the plain state-update dict `{"messages": [nudge], "attestation_nudge_denied_count": N}`** (in-graph, same execution — mirrors the `language_check` plain-dict routing-return precedent at `daemon/graph.py:2666-2685`; route-back to `agent` is wired via `add_conditional_edges("attestation_gate", should_end_attestation, {"retry": "agent", END: END})` at graph-build time, NOT via `Command`); (4) NO `manager.enqueue_message` call — **explicit negative assertion: `manager.enqueue_message.assert_not_called()` AND query the `message_queue` table to assert NO new row was created during the deny window**; (5) NO `send_message` / `revive` call — same explicit negative assertion; (6) leader resumes inside the same execution (turn boundary, not instance boundary); (7) leader's prompt contract instructs it to continue; (8) leader completes remaining work and calls `attest_completion`; (9) gate re-evaluates, scanner returns True; (10) `attestation_denied_count` reset to 0 on allow (per R2 + O2); (11) terminal-status write proceeds. Assertions (strengthened per yellow note on snapshot read-back): (a) exactly one canonical log entry with `decision: "denied"` (canonical enum per Phase 4 task 4.5) AND exactly one canonical log entry with `decision: "allowed"` (canonical enum); (b) no `terminal_after_bound` log; (c) instance ends COMPLETED; (d) **the nudge `HumanMessage` is the ONLY message added to `state.values["messages"]` during the deny window** — asserted by snapshotting `len(state["messages"])` pre-deny and post-nudge and asserting `len(post) == len(pre) + 1` AND asserting the appended message is a `HumanMessage` AND asserting `content == "The work is not yet finished — check current progress and continue."` (NFR-6 verbatim) AND asserting `additional_kwargs.get("attestation_nudge") is True` (mirror `language_check` precedent); (e) **`message_queue` DB read-back**: count rows in `message_queue` table before the deny cycle and after the cycle ends; assert `count_after == count_before` (no `MessageQueue` row for the deny); (f) **`task_queue` DB read-back**: same count comparison for the `task_queue` (or equivalent) table; assert `count_after == count_before`; (g) the canonical schema fields are populated per Phase 4 task 4.5 — assert `pending_children: int == 0`, `queued_or_expected_wakeups: int == 0`, `attestation_present: bool == False`, `messages_scanned: int > 0`, `mode: str == "enforce"`. |
| **Decision tags** | [AC-4.3], [AC-4.4], [D1=B], [R1], [C5 ruling], [O2] |
| **Test notes** | File-backed SQLite + NullPool + WAL per `research-findings.md` §16. LLM scripting via the **scripted fake-chat-model seam** (task 5.0 — early-ship infrastructure task) — leader plays a scripted sequence of tool_calls across two turns (turn 1: hallucinated END; turn 2: real work + `attest_completion`). |

### 5.6 — Integration test: bound-exceeded escalation (terminal-after-bound)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_bound_escalation.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | AC-E2E-2. Leader LLM cannot be induced to call `attest_completion` even after `bound` nudges. Sequence: (1) first `bound` attempts → gate denies → in-state nudge + counter increments; (2) `(bound + 1)`-th attempt → gate returns `terminal_after_bound` → instance row carries `completion_gate_escalated=True` AND `attestation_denied_count=0` (reset-on-terminal per O2) AND the gate routes to END with the escalation flag set; (3) mission finalizes; (4) escalation event `gate_terminal_after_bound` is emitted **exactly once** (uniqueness); (5) **NO `manager.enqueue_message` call across the whole sequence** (negative assertion); (6) **NO nudge `HumanMessage` injected on the bound+1'th attempt** (the decision is `terminal_after_bound`, not `deny` — verifies that nudge injection is conditional on `Decision.deny`, not on `Decision.terminal_after_bound`). |
| **Decision tags** | [D5], [R2], [O2] |
| **Test notes** | AC-E2E-2 verified. |

### 5.7 — Integration test: enforce-mode delegation E2E (R2 — pending_children / wakeup allow un-attested)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_delegation_allow.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | R2: gate denies ONLY when (attestation missing in window) AND (no pending children) AND (no queued/expected wakeups). Tests verify the **allow-un-attested** branch in two scenarios. **Four reset triggers — reconciled per the leader ruling (CLOSED-by-leader 2026-09-05, supersedes prior N5 wording)** — `attestation_denied_count` resets to 0 on EXACTLY these four events (the in-graph deny-nudge is NOT a reset): (1) **attested allow** (canonical `allowed` per Phase 4 task 4.5 enum) — exercise path in this task 5.7; (2) **`terminal_after_bound` finalization** (escalation path — the reset prevents insta-escalation on the next mission) — exercised by Phase 5 task 5.6 (bound-exceeded escalation); (3) **revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode)** — exercised by Phase 5 task 5.14 (revive-after-escalation via user `send_message` re-dispatch per `daemon/services/instance_messaging.py:1867-1909`); (4) **instance creation** — column default fires at instance-row creation (per Phase 3 task 3.2 migration). Task 5.7 intentionally uses an R2-allow with a nonzero counter to prove that this path is **not** a reset trigger; the attested-allow reset is exercised by 5.5/5.13. All four are documented in Phase 3 task 3.3; this task is the R2-allow path test and confirms the R2 **non-reset** invariant. **Drift disclosure (per leader ruling)**: the previous N5 enumeration named "instance-revive-from-TERMINATED" as trigger #4 — REMOVED. Instance creation is ADDED as trigger #4. Counter does NOT auto-reset on PAUSED → RUNNING or on checkpoint reload (per leader ruling). **Scenario A — child delegation:** leader spawns a worker child via the dispatch tool (which commits the watcher PENDING row at `daemon/tools/instance.py:604-699` BEFORE the tool result returns — see Phase 2 task 2.3.1 TOCTOU contract) and ends its turn WITHOUT calling `attest_completion`. State carries `pending_children > 0` (verified via DB read-back of `dependency_watchers` BEFORE the gate evaluates — the test MUST assert the PENDING row exists before invoking the gate; this is the DB-read-back guard that closes the Phase 2 task 2.3.1 residual silent-registration-failure window). Assertions: (1) gate evaluates → canonical decision `allowed_legitimate_pending_wakeup` (per Phase 4 task 4.5 canonical enum); (2) `attestation_denied_count` NOT incremented (zero side effects on R2-allow); (3) gate routes to END; (4) mission does NOT finalize cleanly (finalize waits on the child per the existing project convention) — but the gate did NOT deny the leader's turn-end. **Scenario B — wakeup delegation (real mechanism, NOT the phantom `enqueue_message(scheduled_at=...)`):** leader schedules a wakeup via `TaskRepository.schedule_retry(task_id, max_retries>=1, next_retry_at=now+Δ)` (def at `daemon/repositories/task/repository.py:3206`; one txn: cancels parent `retry_scheduled=True` + INSERTs PENDING child with `next_retry_at`; fires when `claim_pending_task` (def at `daemon/repositories/task/repository.py:1243`) passes `next_retry_at<=now`) — OR, for sub-second timing, `requeue_task_with_backoff(task_id)` at `daemon/repositories/task/repository.py:1754` (jittered 0.5–2.0 s). The leader then ends its turn WITHOUT calling `attest_completion`. **There is NO scheduled-message API anywhere** — `enqueue_message` at `daemon/services/instance_messaging.py:1960-1973` has NO `scheduled_at` kwarg (positional: instance_id, message, source, priority, images, metadata; kw-only: is_deferred, is_background, work_id, work_id_required); every scheduled/expected wakeup is a `next_retry_at` row + due-gate filter across THREE tables (Task at `daemon/repositories/task/models.py:158`; message_queue at `models.py:69` via `message_queue/repository.py:374` with due-gates `:144/:171/:241/:300-306`; job_queue_items at `job_queue/models.py:378` with RETRY transition `job_queue/repository.py:1546-1688`) PLUS the expected-not-scheduled wakeups (PAUSED-held PENDING Tasks at `instance_messaging.py:2005-2012`; `is_deferred=True`-held PENDING Tasks at `:1995-2003`). The `manager.get_queued_or_expected_wakeups(instance_id)` facade method (NEW per Phase 2 task 2.3 — CR-1) sums these. State carries `queued_or_expected_wakeups > 0` (verified via DB read-back of the relevant tables BEFORE the gate evaluates). Same assertions as Scenario A: gate evaluates → `allowed_legitimate_pending_wakeup`; `attestation_denied_count` NOT incremented; gate routes to END. **Both scenarios assert NO nudge injected AND NO `enqueue_message` called** — the leader does NOT call `enqueue_message` for the wakeup either (the wakeup is the DB row from `schedule_retry` / `requeue_task_with_backoff`, not an `enqueue_message` call). |
| **Decision tags** | [R2], [O2] |
| **Test notes** | The "delegating leader turn-end" case is the primary risk surface for false positives — a leader who correctly delegates and ends its turn MUST NOT be coerced into calling `attest_completion` while work is in flight. The dry log still records the canonical `dry_log` decision with `pending_children: int > 0` so operators see the R2-allow path was taken (per task 5.4 schema and Phase 4 task 4.5 canonical schema). |

### 5.8 — Unit + integration test: fail-open on scanner/gate exception (C3)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/test_attestation_fail_open.py` (new); `tests/integration/test_attestation_fail_open.py` (new) |
| **Mode pinned** | Unit tests: `enforce` (the fail-open contract is most consequential under enforce); Integration tests: `enforce`. |
| **Description** | C3: any exception in scanner/gate ⇒ allow + structured error log; except `Exception` at the attestation_denied_count ledger DB seam (W4's narrow set at graph.py:2663–2688 does NOT cover `sqlalchemy.exc.OperationalError`). Cases — unit: (a) scanner raises `ValueError` → gate returns `{allowed, fail_open_reason: "scanner_exception", error_class}` (canonical `Decision.allowed` per Phase 4 task 4.5); (b) scanner raises `sqlalchemy.exc.OperationalError` (simulated) → gate returns `{allowed, fail_open_reason: "scanner_exception", error_class: "OperationalError"}`; (c) scanner raises `BaseException` (`KeyboardInterrupt`) → **NOT caught** (fail-closed for cancellation — same precedent as pause-cancellederror fix 2026-07-12). Cases — integration: (d) leader mission under `enforce` mode completes successfully when the scanner throws — leader reaches terminal COMPLETED with the structured error log line and NO escalation flag set; (e) the attestation_denied_count ledger DB seam test: mock `instance_repo.increment_attestation_denied_count` to raise `sqlalchemy.exc.OperationalError("database is locked")` mid-deny → gate catches the narrow `except Exception` (NOT `BaseException`), returns `{allowed, fail_open_reason: "ledger_db_seam", error_class: "OperationalError"}`, emits an error log, and does NOT re-raise — the leader mission completes successfully. The narrow except at the ledger seam is asserted by a focused micro-test (`test_attestation_ledger_db_fail_open.py`) that monkey-patches the seam and asserts the caught exception is logged. |
| **Decision tags** | [C3], [W4 precedent] |
| **Test notes** | The fail-open path is an exception-path EVENT, not a decision value — per the canonical 5-value enum at Phase 4 task 4.5 (`allowed | denied | terminal_after_bound | dry_log | allowed_legitimate_pending_wakeup`). The fail-open case logs `event=leader_completion_gate_error` (scanner/gate exception) or `event=leader_completion_gate_db_error` (ledger DB seam), each carrying `error_class: str` and the canonical schema fields, so operators can grep the event name post-incident; the gate returns `allowed` and the leader's terminal-status write proceeds normally. AC-3.1 + NFR-10 verified. |

### 5.9 — Integration test: must-not-break regression suite (parameterized over mode × surface)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_must_not_break.py` (new) |
| **Mode pinned** | Parameterized: `mode ∈ {"off", "dry", "enforce"}` × (must-not-break surface) |
| **Description** | AC-E2E-3 (normal attested completion unaffected) + AC-E2E-5 (must-not-break surfaces). Parameterize over `mode × surface`. Surfaces: (a) normal attested completion — leader calls `attest_completion` then ENDs → all three modes allow terminal with byte-equivalent state (the `enforce` mode additionally resets `attestation_denied_count=0` on the allow path — this is a DB-only difference, asserted by querying the instance row); (b) mission finalize path (observer `_finalize_job` Step 2 runs normally — the MVP does NOT touch this seam, but the test verifies the gate did not introduce a finalize regression); (c) WC-wake routing lanes (`ENSEMBLE_WC_WAKE_ENQUEUE` default OFF — boot log coexistence); (d) report-delivery recovery sweeps (`ReportDeliveryRecoveryService`, `WaitingChildrenWatchdog`); (e) report-injection claim machine (atomic PENDING→INJECTED rowcount-guard at `daemon/graph.py:416-490`); (f) non-leader agents (developer, reviewer, tidier, approver, architect, tester, giter, devops, explorer, wanderer, kb-writer, doc-writer) — their graphs are untouched and AC-11.1 is verified at this surface too. **Revive semantics** (which were previously a must-not-break surface for the old durable-delivery design) are **relocated** to [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) §6.4 (the MVP deny path does NOT trigger revive). Assertions: each surface behaves IDENTICALLY across `off` and `dry`; under `enforce`, surfaces (b)-(f) are byte-equivalent and surface (a) differs only in the `attestation_denied_count` reset write. |
| **Decision tags** | [AC-11.1], [D2], [D3] |
| **Test notes** | AC-E2E-3, AC-E2E-5 verified across 3 modes × 6 surfaces = 18 sub-cases. NFR-11 verified. |

### 5.10 — Integration test: tri-state mode semantics (C1a)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_mode_tri_state.py` (new) |
| **Mode pinned** | Parameterized: `mode ∈ {"off", "dry", "enforce"}` × (case) |
| **Description** | C1a: tri-state semantics (replaces the old boolean-mode test). Three sub-cases: (a) `mode="off"` — leader hallucinates an END without attestation → gate is BYPASSED (the conditional-edge table routes END→END directly without the `attestation_gate` node hop) → terminal-status write proceeds immediately → NO `leader_completion_gate` log entries → behavior byte-equivalent to a pre-feature baseline reference; (b) `mode="dry"` — same scenario → gate EVALUATES → emits canonical `dry_log` log entry with full schema (per task 5.4 and Phase 4 task 4.5 canonical schema) → `attestation_denied_count` NOT incremented → nudge NOT injected → terminal-status write proceeds → behaviorally identical to a pre-feature baseline EXCEPT for the log line; (c) `mode="enforce"` — same scenario → gate EVALUATES → returns `denied` (canonical enum per Phase 4 task 4.5) → nudge injected → counter incremented → leader resumes same execution → no terminal-status write yet. The "boolean-mode test" that lived at old phase5-plan.md:126 is REPLACED by this 3-case parameterized test. |
| **Decision tags** | [D2] (tri-state), [AC-E2E-4] |
| **Test notes** | The off-mode case asserts the gate node is not even on the routing path (verified by inspecting the LangGraph compiled graph's conditional-edge table — the `agent → end_candidate → attestation_gate → ...` chain is absent). |

### 5.11 — Unit test: authz fail-closed

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/unit/tools/test_attestation_registration.py` (new — extends `test_upgrade_registration.py`); `tests/unit/test_attestation_authz.py` (new) |
| **Description** | Cases: (a) leader has `attestation` in `tools.allow` (AC-9.1); (b) all non-leader agents (developer, reviewer, tidier, approver, architect, tester, giter, devops, explorer, wanderer, kb-writer, doc-writer) do NOT have `attestation` in `tools.allow` (AC-9.2); (c) `KNOWN_TOOL_NAMES` includes the new tool; (d) `CATEGORY_MODULES` entry exists; (e) `DYNAMIC_TOOL_NAMES` includes it; (f) `PRIVILEGED_TOOL_CATEGORIES` does NOT include `attestation` (NOT privileged per D7). Uses `get_version(id, tag) → get_resolved() fallback` (`daemon/tools/instance.py:4475-4477`). |
| **Decision tags** | [D3], [D7] |
| **Test notes** | AC-1.3, AC-9.1, AC-9.2, AC-9.3 verified. Drift test enforcement via `tests/unit/tools/test_upgrade_registration.py`. |

### 5.12 — Integration test: compaction preservation + N/min_recent_window coupling (O1)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_compaction.py` (new); `tests/unit/test_attestation_resolver_window_floor.py` (new) |
| **Mode pinned** | `enforce` (integration); unit test is mode-agnostic |
| **Description** | Two standing tests from research-findings §11. **Compaction preservation**: simulate context pressure that triggers compaction with `recent_message_window=10` (default); assert the post-compaction state preserves the last 10 boundary groups verbatim AND the scanner still finds `attest_completion` in the tail. **N/min_recent_window coupling (O1)**: (a) default `ENSEMBLE_LEADER_ATTESTATION_WINDOW=3` and default `min_recent_window=3` → boot succeeds, no warning log; (b) operator sets `ENSEMBLE_LEADER_ATTESTATION_WINDOW=4` (above `min_recent_window`) → resolver at boot **rejects** with a clear error log (`O1 boot assert N ≤ min_recent_window`); (c) operator sets `ENSEMBLE_LEADER_ATTESTATION_WINDOW=5` while also raising `min_recent_window=5` → boot succeeds, no warning. The (b) case is the O1 mitigation — the resolver refuses an unsafe configuration at boot, before any leader mission runs. **Negative attestation after compaction (cliff characterization)**: simulate compaction reducing the tail to `min_recent_window=3` AND the attestation falling outside the 3-group floor → scanner returns False; this is the severity=high cliff and the test characterizes behavior (per `research-findings.md` §11 recommended posture — the architect picks the D10 mitigation only if this test reveals a gap, which it WILL, but the MVP ships with the cliff characterized and the O1 boot assert preventing the common operator misconfiguration that triggers it). |
| **Decision tags** | [D4] (window N), [D10(b)], [O1] |
| **Test notes** | AC-2.5 + D10(b) edge case verified. The cliff test is documentary — it does NOT gate the MVP. |

### 5.13 — Integration test: reset-on-allow + DB-column semantics (C1c — replaces dropped legacy cleanup framing)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_ledger_reset.py` (new); `tests/unit/test_attestation_ledger_reset.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | C1c: the old legacy cleanup framing (which mirrored the `_loop_breaker_state.pop` pattern) is **DROPPED** because the actual `_loop_breaker_state.pop` sites are only 3 (manager.py:3734, :3798, :8548 — not 5), and the `attestation_denied_count` is a DB column on the instance row, not an in-memory dict. The replacement is **bounded reset semantics**: attested allow resets, terminal escalation atomically sets the flag and resets, a fresh mission boundary resets, and instance creation starts at zero. **An R2-allow is deliberately non-reset** (leader ruling 1): the un-attested allow with pending children or wakeups keeps the prior counter so a later bare completion can still escalate. Tests: (a) deny → deny → allow → assert `attestation_denied_count=0` (reset-on-allow); (b) deny → deny → deny (bound reached) → `terminal_after_bound` → assert `attestation_denied_count=0` AND `completion_gate_escalated=True` (reset-on-terminal AND escalation flag set); (c) revive-from-COMPLETED → next mission begins with `attestation_denied_count=0` — clarified: this is verified by asserting that a fresh `send_message` dispatch to a revived leader (per `daemon/services/instance_messaging.py:1867-1909`) starts with `attestation_denied_count=0` on the instance row (the counter is per-row, and revive reuses the row; the Phase 6 backstop tests this — see `phase6-fastfollow-plan.md` §6.4); (d) parallel two-mission test — mission A denies twice, mission B (same leader instance, second mission boundary) denies twice → counters are independent per-mission and mission B's first deny sees `attestation_denied_count=0` (NOT mission A's residue). |
| **Decision tags** | [C1c], [D5], [O2], [R2] |
| **Test notes** | AC-6.4 verified. The "legacy cleanup framing" phrase is FORBIDDEN in test docstrings (per canonical design — self-grep guard). |

### 5.14 — Integration test: revive-after-escalation (next mission not insta-escalated)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_revive_after_escalation.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | Leader mission 1: bound reached → `terminal_after_bound` → `completion_gate_escalated=True` → mission finalizes COMPLETED. Leader mission 1 instance then `revived` (re-dispatched via `send_message` per `daemon/services/instance_messaging.py:1867-1909`) — mission 2 begins. Assertion: mission 2's FIRST deny sees `attestation_denied_count=0` (NOT pre-burdened with the bound from mission 1) AND `completion_gate_escalated=False` at start. The escalation flag and counter must reset on the revival boundary; without reset, a revived leader's next mission starts pre-burdened and insta-escalates on a single deny. (This is the bug class the architecture-recommendation §1 D5 calls out as a row-vs-dict asymmetry hazard.) |
| **Decision tags** | [O2], [R2] |
| **Test notes** | LLM scripting via the **scripted fake-chat-model seam** (task 5.0). |

### 5.15 — Integration test: stale-attestation-watermark (pre-revive attestation does NOT satisfy post-revive gate)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_stale_watermark.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | Leader mission 1: leader calls `attest_completion` (the tool_call is in mission 1's state.values["messages"]) and ENDs → COMPLETED. Leader mission 1 is then revived for mission 2. Mission 2 begins with a clean state but the checkpoint may carry the mission 1 tool_call at the tail (compaction may or may not fold it — the test verifies both branches). Assertion: when the leader in mission 2 attempts END without a FRESH `attest_completion` in mission 2's last-3-AIMessages window, the gate denies — **the stale pre-revive attestation does NOT satisfy the post-revive gate**. The window scan is self-correcting (per architecture-recommendation §2 row "D — tool-as-only-trigger — pre-rejection confirmed unanimously: a sticky checkpoint flag survives revive → a revived instance ENDs on a stale pre-revive attestation — the very bug class this feature targets; window scanning is self-correcting, the flag is not"). |
| **Decision tags** | [D — pre-rejection], [R2] |
| **Test notes** | The test runs two sub-cases: (i) post-revive state has 0 messages → trivially denies; (ii) post-revive state has ≥N messages where the OLDEST tool_call is the stale attestation → scanner correctly scopes to the LAST N AIMessages and finds no fresh attestation → denies. |

### 5.16 — Integration test: structured logging schema

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_observability.py` (new) |
| **Mode pinned** | Parameterized: `mode ∈ {"off", "dry", "enforce"}` × (case) |
| **Description** | AC-10.1: 1000 leader missions complete; daemon log has the expected gate decision entry count per mode (off=0, dry=1000, enforce=1000). AC-10.2: exactly one `gate_terminal_after_bound` event exists per escalation instance. Schema check (per task 5.4 and Phase 4 task 4.5 canonical schema): every gate decision entry has all canonical schema keys populated (`mode`, `decision` ∈ canonical enum, `instance_id`, `messages_scanned`, `pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `scanner_window_truncated`, `scanner_summary_seen`, etc.); `messages_scanned > 0` is hard-asserted (O8 — Phase 2 task 2.3 in-node seam + `messages_scanned>0` in canonical log schema). Boot log assertion: single line at startup with all three resolved values (mode, window, deny_bound; `attestation_recovery_enabled=False` at MVP is the C backstop flag and is logged at MVP only by Phase 6, not by Phase 4 boot log — three knobs is canonical). |
| **Decision tags** | [D8], [O8] |
| **Test notes** | AC-10.1, AC-10.2 verified. |

### 5.17 — Corpus replay driver and recorded dry-log fixtures (AC-E2E-6 / ruling 3)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/support/recorded_corpus_replay.py` (new); `tests/fixtures/recorded_leader_missions/*.jsonl` (new); `tests/integration/test_attestation_corpus_replay.py` (new) |
| **Mode pinned** | `dry` (fixtures are recorded dry-mode gate decisions) |
| **Description** | The checked-in JSONL corpus contains complete `event=leader_completion_gate`, `decision=dry_log` entries with all canonical fields, including the R2 inputs, diagnostics, and stale-attestation flags. The driver validates each entry, derives `dry_log_total`, `dry_log_deny_predicate_total`, and `enforce_denied_total`, calculates the false-positive adjudication rate, and writes per-mission `_runs/<mission_id>.jsonl` audit files. Integration coverage asserts the full schema, the 4-record fixture counts, the 2/4 deny-predicate split, the two R2-allow records, and the 50% rate. |
| **Decision tags** | [AC-E2E-6], [R2], [NFR-16], [ruling 3] |
| **Test notes** | No new production recovery behavior is implied. The driver records replay output; Phase 6 must not implement durable recovery here. |

### 5.18 — Integration test: nudge-survives-SIGKILL chaos test (AC-4.1+AC-4.2 — CR-3)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/integration/test_attestation_nudge_chaos.py` (new) |
| **Mode pinned** | `enforce` |
| **Description** | **CR-3 — owns AC-4.2** (the MVP nudge durability by LangGraph checkpoint — `requirements.md:282-286`). The previous plan set had no owning test for AC-4.2 (the AC-numbering collision between MVP-nudge AC-4.1–4.4 and durable-path AC-4.5 — the renumbering fix in this revision makes AC-4.5 the durable-path AC, leaving AC-4.1 + AC-4.2 as the MVP nudge ACs; AC-4.1 is owned by Phase 5 task 5.3 unit test; AC-4.2 is owned by THIS task). Sequence: (1) leader LLM hallucinates an END without `attest_completion`; (2) gate denies under `mode=enforce` → injects the nudge `HumanMessage` with `additional_kwargs={"attestation_nudge": True}` and `content="The work is not yet finished — check current progress and continue."` (NFR-6 verbatim) and returns the plain state-update dict `{"messages": [nudge], "attestation_nudge_denied_count": N}` (mirrors the in-file `language_check` plain-dict routing-return precedent at `daemon/graph.py:2666-2685`; route-back to `agent` wired via `add_conditional_edges` at graph-build time, NOT via `Command`); (3) gate increments `attestation_denied_count`; (4) gate returns control to the in-graph `agent` node; (5) **SIGKILL the daemon mid-execution** (after the nudge has been checkpointed by LangGraph at the node boundary); (6) restart the daemon; (7) resume from the leader's LangGraph checkpoint; (8) **assert `state.values["messages"]` contains the nudge `HumanMessage`** (asserted by exact equality on `content` and `additional_kwargs`); (9) leader continues inside the resumed execution and eventually calls `attest_completion`; (10) gate allows → terminal-status write proceeds; (11) `attestation_denied_count` resets to 0 on the allow (per O2). The test is the OWNING test for AC-4.2 and the only place the post-crash checkpoint state is asserted to contain the nudge. It depends on the scripted fake-chat-model seam (Phase 5 task 5.0) for the multi-turn scripting across the SIGKILL/restart boundary. |
| **Decision tags** | [AC-4.1], [AC-4.2], [R1], [CR-3] |
| **Test notes** | AC-4.1 (in-state HumanMessage injection) is owned by Phase 5 task 5.3 unit test; AC-4.2 (nudge durability by LangGraph checkpoint) is owned by THIS task — the previous plan's missing-owning-test gap is closed here. The durable-path AC-4.5 (formerly the mislabeled AC-4.1–4.4) lives at `phase6-fastfollow-plan.md` §6.2. |

---

## Coupling

- **Tight with:** Phases 1–4 (this phase verifies the entire MVP feature); Phase 6 (relocated tests — `phase6-fastfollow-plan.md` §6.1–§6.4 — must NOT duplicate AC-E2E-1's in-graph nudge assertion; the phase6 AC-E2E-1 is the durable-path variant).
- **Loose with:** none.
- **Independent of:** none.

---

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | **Test infrastructure race with production code** (AsyncMock blinds tests to real linkage contract) | High (false-positive pass) | Test-blindness fix pattern: real-dispatch integration tests with DB read-back + kwargs-CONTENT assertions per `research-findings.md` §16 |
| 2 | **Flaky E2E (timing-dependent)** | Medium (operator mistrust) | Use file-backed SQLite + deterministic fixtures; the scripted fake-chat-model seam (task 5.0) avoids LLM-based tests where scripted mocks suffice |
| 3 | **Mock-migration tripwire** (escalating to `enforce=True` breaks mocks) | Medium | Mock-migration checklist per `research-findings.md` §16 |
| 4 | **Drift test fails unexpectedly on tool registration** | Low (drift test is the canonical check) | Run `tests/unit/tools/test_upgrade_registration.py` after every Phase 1 commit |
| 5 | **Order-sensitive test results** (solo-vs-context divergence) | Medium (broken test infra) | Full-partition attribution per `research-findings.md` §16: batch node-run at base + context-matched re-run |
| 6 | **Negative-assertion leakage** — `manager.enqueue_message.assert_not_called()` fails if a future refactor accidentally adds a `manager.enqueue_message` call on deny (the double-delivery class per `architecture-recommendation.md` §3 C5 ruling) | High (re-introduces the bug class) | Tests 5.3, 5.5, 5.6 carry explicit `assert_not_called` AND a DB read-back (`message_queue` table query — no new row during deny window). The two assertions are independent — either alone is a partial guard; both together are the lock. |
| 7 | **Scripted fake-chat-model seam not yet existing** — task 5.5 / 5.14 / 5.15 require a deterministic in-process chat-model that scripts a sequence of `tool_calls` across two turns. The test suite currently has only `make_chat_response` (MagicMock-shaped OpenAI response, single-turn) and `mock_llm_server.py` (full HTTP server). See task 5.0 below for the new seam. | High (5.5/5.14/5.15 BLOCKED without the seam) | Spec and ship the seam in Phase 1 OR Phase 5 entry — see task 5.0. |

---

## Rollback Story

This phase is reversible per test file:

1. **Unit test rollback:** delete `tests/unit/test_attestation_*.py` and `tests/unit/tools/test_attestation_registration.py`. Production code unaffected.
2. **Integration test rollback:** delete `tests/integration/test_attestation_*.py`. Production code unaffected.
3. **Drift test rollback:** delete `tests/unit/tools/test_attestation_registration.py`. The new tool is no longer drift-tested; registration discipline is silently weakened. **Recommend: do not roll back drift test unless Phase 1's tool is also rolled back.**

**Restart-read:** tests run in CI; no daemon restart required for test-only changes.

---

## Exit Criterion

This phase is done when:

- [x] All unit tests pass (scanner — 5.1; gate decision — 5.2; nudge injection — 5.3; dry logging — 5.4; fail-open unit — 5.8(a-c); authz/registration — 5.11; resolver window floor — 5.12(b-c); ledger reset unit — 5.13; corpus replay — 5.17)
- [x] All integration tests pass (flagship E2E in-graph nudge — 5.5; bound escalation — 5.6; delegation allow — 5.7; fail-open integration — 5.8(d-e); must-not-break — 5.9 × {off, dry, enforce} × 6 surfaces; tri-state mode — 5.10; compaction + N floor — 5.12(a); ledger reset integration — 5.13; revive-after-escalation — 5.14; stale watermark — 5.15; observability — 5.16; recorded dry-log corpus — 5.17; nudge-survives-SIGKILL chaos — 5.18)
- [x] AC-E2E-1 (full hallucination → in-graph nudge → continue → attested → finalize) verified at the in-graph-nudge seam with explicit `manager.enqueue_message.assert_not_called()` AND DB read-back
- [x] AC-E2E-2 (bound-exceeded escalation) verified
- [x] AC-E2E-3 (normal attested completion unaffected) verified across all three modes
- [x] AC-E2E-4 (off-mode disables the feature entirely) verified
- [x] AC-E2E-5 (must-not-break regression) verified across 3 modes × 6 surfaces (revive relocated to phase6)
- [x] AC-E2E-6 (recorded dry-log corpus replay: canonical metrics and false-positive adjudication) verified
- [x] AC-E2E-7 (fail-open on scanner exception — gate catches W4 set, allows completion, `gate_exception` log per AC-10.4, `gate_exception_seen=true`, mission finalizes) verified at the unit + integration seam
- [x] AC-E2E-8 (phase6 backstop NOT in MVP — `attestation_recovery.py`, D6 source mapping, facade-forwarding + JAFP no-JobItem tests live in `phase6-fastfollow-plan.md`; MVP deny path is in-graph only) verified via reviewer checklist (no test code in MVP)
- [x] AC-4.2 (nudge durability by LangGraph checkpoint) verified at Phase 5 task 5.18 (nudge-survives-SIGKILL chaos test) — CR-3 owning-test fix
- [x] No new pre-existing failures introduced (quarantine-free)
- [x] NFR-1 verified: gate decision overhead ≤ P95 20 ms
- [x] Self-grep clean: zero hits for the retired kill-switch token, denied-delivery label, and old cleanup framing (and case variants) across this file and `phase6-fastfollow-plan.md`; zero hits for the legacy decision-literal patterns killed by CR-4 (canonical enum per Phase 4 task 4.5 supersedes all prior literals); verify by `grep -rn` for the legacy pattern set and confirm ZERO hits plan-dir-wide.
- [x] The scripted fake-chat-model seam (task 5.0) is shipped and the tests that depend on it (5.5, 5.14, 5.15, 5.18) are green

The phase is the final verification; the feature is merge-ready after Phase 5.

---

## Test Strategy Per Project Convention

Per `research-findings.md` §16:

| Convention | Application |
|---|---|
| **Worktree-based regression proof** | If the in-graph nudge regression breaks normal END routing, copy `test_attestation_in_graph_nudge_flow.py` to pre-fix worktree; expect the original END-bypass failure mode |
| **File-backed SQLite** | All integration tests use file-backed SQLite at `tmp_path` with `NullPool`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=10000` |
| **Full-partition attribution** | Any pass-at-base test must re-run FULL partition in context-matched scratch worktree at base; 3× solo determinism budget at HEAD |
| **Mock-migration checklist** | If any linkage-contract tripwire escalated to `enforce=True`, grep for mocked `enqueue_message` returns feeding recovery path; **for the MVP, there is no such feeding path** — the MVP deny path does NOT call `enqueue_message`. The mock-migration checklist here is reduced to: grep for `enqueue_message` in MVP test files and assert each is a `mock.assert_not_called()` (the negative assertion lock) |
| **Test-blindness fix pattern** | Real-dispatch integration test with DB read-back + kwargs-CONTENT assertions closes the AsyncMock-blind gap. For the MVP, this is the `message_queue` table query in test 5.5 — the DB read-back is the **primary** assertion, the mock assertion is secondary |

---

## New Test Infrastructure (task 5.0 — ships FIRST; blocks 5.5 / 5.14 / 5.15 / 5.18)

The flagship E2E (test 5.5) and the revive-after-escalation + stale-watermark tests (5.14, 5.15) require an in-process chat-model that scripts a deterministic sequence of `tool_calls` across two turns. The existing test suite has two partial seams:

| Existing seam | Location | Limitation |
|---|---|---|
| `make_chat_response(content)` (MagicMock-shaped OpenAI response) | `tests/integration/test_skill_cross_phase_flow_a.py:334` | Single-turn, single-content; cannot script a multi-turn sequence with interleaved `tool_calls` |
| `mock_llm_server.py` (full FastAPI HTTP server) | `tests/mock_llm_server.py:1-80+` | Full HTTP round-trip; suitable for E2E but too heavy for the gate's unit seam; not in-process |

**No `FakeListChatModel` / `FakeMessagesListChatModel` subclass exists in the test suite today** (verified by grep — `langchain_core.language_models.fake.FakeListChatModel` is available as a dependency but unused). O7 mandates naming the seam.

### Task 5.0 — Spec the in-process scripted fake-chat-model seam (O7)

| Aspect | Detail |
|---|---|
| **Files touched** | `tests/support/scripted_chat_model.py` (new); `tests/support/conftest.py` (new — fixture `scripted_chat_model`); uses `langchain_core.language_models.fake.FakeListChatModel` (already a dependency) |
| **Description** | New seam: a `ScriptedChatModel` subclass of LangChain's `FakeMessagesListChatModel` (`langchain_core.language_models.fake_chat_models`) wrapped in a thin **scripted driver** that plays a turn-by-turn script. The script is a list of `BaseMessage` instances (typically `AIMessage` with `tool_calls=[...]`) indexed by call count. Each call to the chat model consumes the next entry from the script; once exhausted, raises `IndexError` (loud failure — a test that under-scripts its turns must surface loudly). The seam is wired into the leader-graph build by patching the real seam `daemon.graph.build_instance_llms` (the plan's former `daemon/services/llm_helpers.py` and `daemon/llm_helpers.py` citations do not exist). |
| **Decision tags** | [O7] |
| **Test notes** | Tests 5.5, 5.14, 5.15 use this seam. The seam is also reusable for any future leader behavior test (the gate's interactions with the LLM are now deterministically scriptable). |
| **Out-of-MVP scope** | A richer seam (streaming, function-calling retries, vision) — the current spec covers the leader's needs only. |

### Why this seam (and not `mock_llm_server.py`)

- **In-process** — no socket, no port allocation, no FastAPI lifecycle, no `httpx` round-trip. Each test runs in <100ms of LLM-time.
- **Multi-turn by construction** — a list of messages indexed by call count; the leader-graph's `agent_node` calls the model once per turn, the script advances.
- **Fail-loud on under-scripting** — `IndexError` surfaces the moment the test author forgot a turn. `mock_llm_server` cycles through canned responses and silently mis-scripts.
- **Already available as a dependency** — `langchain_core.language_models.fake.FakeMessagesListChatModel` is in `.venv/lib/python3.13/site-packages/langchain_core/language_models/fake_chat_models.py`. No new dependency, no version pinning.

---

## Pointer to Phase 6 (relocated tests — DO NOT DUPLICATE HERE)

The following test categories are **RELOCATED** to [`phase6-fastfollow-plan.md`](./phase6-fastfollow-plan.md) because they cover the durable `manager.enqueue_message` recovery path, which is out-of-MVP scope:

| Test category | Phase 6 section | Why relocated |
|---|---|---|
| Durable recovery injection (AC-4.5 — RELOCATED to Phase 6; per requirements.md:300-305, AC-4.5 has always been the durable-path AC; the disambiguation in this revision resolves the prior numbering collision with MVP-nudge AC-4.1–4.4 — AC-4.1–4.4 remain MVP nudge ACs, AC-4.5 remains the durable-path AC) | §6.2 | The MVP deny path is in-graph nudge ONLY; durable recovery is the C fast-follow backstop |
| JAFP no-JobItem for recovery messages (AC-4.5 JAFP clause — RELOCATED) | §6.3 | The MVP does not create JobItems on deny (no `enqueue_message` call); this test only applies to the durable path |
| Facade-forwarding discipline for recovery kwarg (C-7) | §6.5 | The MVP does not add a new kwarg to `enqueue_message`; this duty only applies if the phase6 backstop adds one |
| Revive re-arm after terminal-after-bound (gate/ledger re-arm after revival) | §6.4 | The MVP does not trigger revive on deny; the race with observer Step 2 (`job_feedback_observer.py:3703-3758`) only exists when the durable path re-arms |
| Observer-vs-revive race (architecture-recommendation §6 correction #3b) | §6.6 | Pre-existing race, exists independently of this feature; file separately per architecture-recommendation §7 watch-list item 8 |
| Dry→enforce soak promotion criteria (rate-thresholds, observation window) | §6.7 | Operator-flipped, post-soak; not testable as a unit |
