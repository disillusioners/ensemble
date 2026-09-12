# Empty-Response-Guard Gate — Lane-Name Matching vs Subclass Contract (2026-09-12)

## Context
Final merge gate for `feature/fix-empty-response-guard` @ `97b45ba1` (base `03ee192c`). The guard's
design (spec §3, ADR-0001) leans on a typed-subclass contract: `EmptyLLMResponseError ⊂
LLMResponseValidationError` was claimed to make the existing error-lane mapping apply "with zero
seam edits."

## Finding
The claim is HALF-true — it holds everywhere the code uses `except LLMResponseValidationError`
(retry scope, agent_node catch tuple, TRANSIENT membership) but BREAKS at the one place that
matches by **class NAME string**:

`daemon/services/message_processing_errors.py:131-133`:
```python
if exc_name in ("LLMResponseValidationError", "APIResponseValidationError"):
    return "validation_error"
```
`exc_name` is the concrete class name → `EmptyLLMResponseError` falls through to the default
`execution_error` lane (line ~157). Consequences: error event DB row + parent `_send_error_report`
carry `error_type="execution_error"`; the Phase-2 `validation_error → max_retries_exceeded`
promotion hook will never see the empty class. Instance still transitions to ERROR (loudness
intact — verified by the FE-SSE trace); this is a **label/observability** defect, not a
protection break.

**Repro:** `daemon.services.message_processing_errors._classify_error_type(EmptyLLMResponseError("x"))`
→ `"execution_error"`, expected `"validation_error"`.

**Fix (1 line, routed to leader):** widen the tuple with `"EmptyLLMResponseError"`, or switch to
`isinstance(e, LLMResponseValidationError)` so ALL current and future subclasses route correctly.

**Pinned:** 2 strict-xfail lane tests in `tests/integration/test_empty_guard_error_lineage.py`
(commit `2a8dc71e`) — XPASS-strict will force marker removal when the fix lands.

## Generalized rule (the transferable lesson)
**Name-string matching silently voids subclass contracts.** Whenever a new exception type is
introduced "for free" via inheritance, grep the codebase for the PARENT's name in string-tuple
memberships (`in ("...", "...")`, `== "..."`, `exc_name`, `type(e).__name__`) — the except-clause
machinery honors subclasses; string comparisons do not. Same class of hazard as the documented
"test-mock ripple" tripwires: invisible to the daemon/ call graph, lives in the seams.

## Second lesson — stale pins vs intentional kwarg additions
The branch's ONLY full-suite red (235 pre-existing + 1) was a pre-existing pin
(`test_attestation_in_graph_nudge_flow.py:161`) asserting nudge `additional_kwargs` by exact dict
equality. The branch intentionally added `injected_message: True` (spec scenario-d requirement —
boundary scan must skip attestation nudges). **Exact-dict-equality pins on payload dicts break on
any intentional marker addition.** Fixed test-side (`d6300a5f`) by extending the expected dict —
correct here because the stamping is spec-mandated; consider superset/`in`-based assertions for
forward-tolerant pins on extensible payloads.

## Gate artifacts
- RESULTS/2026-09-12-empty-response-guard-verification.md (this gate's report)
- New on-branch test commits: `b3c0120a` (lineage e2e), `2a8dc71e` (strict-xfail pins),
  `5898457c` (title-gen + compaction fallbacks), `d6300a5f` (attestation pin update)
