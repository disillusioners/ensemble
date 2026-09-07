# LCA conditional attestation gate — E2E + artifact findings (2026-09-07)

Captured from the `feature/leader-completion-attestation` cond-attestation + masquerade gate at `9d042c88` (delta `26f908a4..9d042c88`).

---

## 1. Artifact lane drift: literal → constant-parity import

**File:** `.agents/tester/RESULTS/2026-09-06-lca-independent-e2e-testfile.py` (line 46, module-level constant).

**Symptom:** hardcoded `NUDGE_TEXT = "The work is not yet finished — check current progress and continue."` — the OLD single-line nudge literal. Flagged twice by the operator across prior gates; picked up this gate.

**Fix (commit `9d042c88`):** `from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT`. The new nudge is 1872 chars:
- `[SYSTEM CONTEXT: Completion Check Nudge]` header
- conditional body
- two-step teaching
- ` ```mermaid ` block with `flowchart TD`

`daemon/graph.py:3344` consumes the constant verbatim (no `.format()`), so `==` equality with the rendered content survives. The conditional amendment also widened the gate — every S1/S2/S3 scenario now opens with a `send_message` tool call so the conditional gate is armed (otherwise the gate stays OFF and S-scenarios hit the `decision=allowed` branch, exercising nothing).

**Lesson:** any tester artifact that asserts on nudge text MUST import from `daemon.graph` (the canonical home at `graph.py:2834`). When the nudge text is reworked, also verify every `==`/`in` assertion still tracks the real injected bytes. For LCA-style deltas, add this import-convention check to the gate's artifact-fix checklist.

---

## 2. Manager stub must expose `count_live_descendants`

**File:** same artifact's `_manager` helper (`_manager()` factory at ~lines 132-164 of the fixed artifact).

**Symptom:** the gate's third R2 input is `live_descendants`. Without a facade on the manager stub, the gate's DB-seam guard fires `leader_completion_gate_db_error → ALLOWED` and EVERY deny assertion in S1/S2/S3 is unsatisfiable (deny path never exercised). This silently turns a deny-test into an "always-allowed" test — high signal-to-noise failure that hides the bug the test exists for.

**Fix (commit `9d042c88`):** add a real-BFS `count_live_descendants(target_instance_id)` over `repo.get_tree_ids_permanent` — same algorithm as `InstanceManager.count_live_descendants` (`manager.py:8601-8655`). NOT a MagicMock.

**Lesson:** any tester artifact that drives `build_instance_graph` with a custom manager MUST wire all R2 inputs (`count_pending_children`, `get_queued_or_expected_wakeups`, `count_live_descendants`) via real facade methods or real BFS — never MagicMock — or the gate cannot exercise deny. A future LCA artifact that skips this facade will silently PASS while testing nothing. A simple smoke check during artifact construction: drive an un-attested END with a delegating scripted model; if `decision != "denied"`, you have a stub gap.

---

## 3. Repository status writes: `update_status` is not allowed

`repository.update(instance_id, status=...)` rejects writes with `ValueError` directing callers to `transition_status_if(..., allowed_from=tuple)`. Use the atomic `WHERE status IN (:allowed_from)` transition for any test that simulates child lifecycle changes — production-correct path. The atomic guard also defends against the test's status change happening between read and write (race-free).

**Lesson:** for any test that mutates `instance.status`, the API surface is `transition_status_if(..., allowed_from=(<from-states>,))`. The previous free-form `update_status` is no longer the contract.

---

## 4. Brief drift: "17-field log row" is actually 23 fields

The LCA-cond-attestation task brief called for a "17-field" gate log row. Canonical format at `daemon/services/attestation_gate.py:815-844` has 23 substituted fields. The ship-mode invariant — `attestation_required` is among them (position #13, between `live_descendants` and `last_real_user_found`) — holds. Brief is stale.

**Field order (canonical, source-of-truth):** `event, decision, instance_id, gate_location, leader_prompt_version, mode, attestation_present, denied_count, next_denied_count, pending_children, queued_or_expected_wakeups, live_descendants, attestation_required, last_real_user_found, last_real_user_index, first_delegation_after_last_user_index, delegation_tool_call_total, attest_seen_outside_window, messages_scanned, scanned_window_size, scanner_window_truncated, scanner_summary_seen, should_inject_nudge`.

**Lesson:** the LCA phase-6 plan doc (`phase6-fastfollow-plan.md`) still references "17-field" — same drift pattern as the task brief. Flag for the planner/author of the phase-6 plan to update. Any LCA-style gate brief that quotes a field count MUST be re-checked against `attestation_gate.py:815-844` at dispatch time.

---

## 5. Token naming: `delegation_since_last_user` is NOT in the log-row format

The brief referenced `delegation_since_last_user=False/True` as a log-row token. That token IS in the gate's Python `Decision` object (it's a runtime predicate used by the gate logic), but it is NOT in the canonical log-row format string. The log-row fields that survive into logs are: `last_real_user_found`, `last_real_user_index`, `first_delegation_after_last_user_index`, `delegation_tool_call_total` — same predicate intent, different surface.

**Lesson:** the Python `Decision` object carries more fields than the log row. Assertions on the Python decision object can use `delegation_since_last_user`; assertions on log lines MUST use the format-string field names. Don't conflate the two surfaces.

---

## 6. Pre-existing failure hygiene: A/B attribution IS the gate

The 7 FAILED + 16 ERROR families in the messages-neighborhood partition were pre-existing per A/B evidence (HEAD `7be6b1d8` byte-identical to base `26f908a4`). The 16 ERROR class is the documented fresh-SQLite migration trap (`migration 20260714_000001` uses PG-only `DROP CONSTRAINT IF EXISTS`; SQLite OperationalError; documented in critical notes).

**Lesson:** when a delta is small, the A/B attribution is the ONLY rigorous pre-existing-introduced proof. The developer's "27 failures byte-identical at f965345a" claim was evidence of pre-existence within the delta, but my A/B at the delta BASE (`26f908a4`) proved pre-existence relative to the WHOLE delta. Both are useful; the latter is the gate-level truth. Document both layers when reporting.

**Follow-up:** the 23 pre-existing failures should be added to `.agents/tester/QUARANTINE.md` as family-level rows in a future tester session — not this gate (the gate's verdict didn't depend on them).
