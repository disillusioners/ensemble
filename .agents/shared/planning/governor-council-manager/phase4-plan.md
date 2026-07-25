# Phase 4: Integration, Wiring & Testing

> **Revision 3 (2026-07-25):** D9 tests added — degraded quorum (1 result → degraded notice), extension past 30min logged to manifest, 1h hard kill → partial result counted. Scenario C/D adjusted for degraded quorum.
> **Revision 2 (2026-07-25):** Assertion bug fixed (suggestion #1). Integration tests added for C5, C6, C1, W7. E2E scenarios updated with D4 (max 4) and C1 clearing.

## Objective

Verify Phases 0-3 work together end-to-end. Wire meta.json flags to the tool category + appender, then run integration tests covering all C1-C6 fixes.

## Coupling

- **Depends on**: Phases 0, 1, 2, 3
- **Coupling type**: tight — verifies actual code working together

---

## Tasks

### Task 1: Verify meta.json Wiring

```raw
[ ] agents/governor/meta.json contains:
    - "tools": { "allow": ["council", "instance", "self", "project", "help", "question", "shared_context", "knowledge", "time"] }
    - "context_injection": true
    - "inject_allowed_models": true
    - "team_members": [valid agents]

[ ] "council" category exists in CATEGORY_MODULES
[ ] resolve_tool_filter(allow=["council"]) returns ["spawn_councilor", "clear_councilor_errors"]
```

### Task 2: Verify spawn_councilor Strict Validation

**⚠️ Suggestion #1 FIX — assertion bug corrected:**

```python
# OLD (BUG — always truthy because "spawn_councilor" is a non-empty string):
# assert "spawn_councilor" or "model" in str(e).lower()  # ← WRONG

# FIXED:
try:
    await spawn_councilor(councilor_agent_id="developer", model="nonexistent-model")
    assert False, "Should have raised on invalid model"
except ValueError as e:
    text = str(e).lower()
    assert "model" in text, f"Error should mention 'model': {e}"
    assert "allowed_models" in text, f"Error should mention 'allowed_models': {e}"
```

| Scenario | Input | Expected |
|----------|-------|----------|
| Valid spawn | `councilor_agent_id="developer"`, `model="gpt-4o"` | Success |
| Invalid model | `model="nonexistent"` | **RAISES** ValueError with "not in allowed_models" |
| Invalid agent (C4) | `councilor_agent_id="nonexistent"` | **RAISES** ValueError with "not a valid agent" |
| Non-team-member (C3) | `councilor_agent_id="leader"` (not in team_members) | **RAISES** ValueError (team membership) |
| Missing model | `model=None` | **RAISES** (Pydantic required field) |
| Empty model | `model=""` | **RAISES** (field_validator) |

### Task 3: CRITICAL Integration Tests (C5, C6, C1, W7)

#### C5 Test: Tool Bound on Real Instance (NOT just unit test)

```python
# C5 INTEGRATION TEST: spawn_councilor is actually bound on a governor instance
# This catches the "standalone factory never called" bug that unit tests miss.

from daemon.tools.instance import create_instance_tools

# Create tools for a governor instance (real factory call)
tools = create_instance_tools(
    manager=test_manager,
    current_instance_id="gov-test-123",
    agent_id="governor",
)

tool_names = [t.name for t in tools]
assert "spawn_councilor" in tool_names, (
    "C5 REGRESSION: spawn_councilor not in created tools! "
    "It must be defined INSIDE create_instance_tools(), not a standalone factory."
)
assert "clear_councilor_errors" in tool_names, "clear_councilor_errors missing"

# Verify it survives filtering
from daemon.tools.instance import _apply_tool_filter
filtered = _apply_tool_filter(tools, "governor", [])
filtered_names = [t.name for t in filtered]
assert "spawn_councilor" in filtered_names, "spawn_councilor filtered out — check tools.allow"
```

#### C6 Test: Flag Survives Loading

```python
# C6 INTEGRATION TEST (from Phase 3)
from daemon.registry import AgentRegistry
from pathlib import Path

registry = AgentRegistry(Path("agents"))
registry.discover()

gov = registry.get("governor")
assert gov is not None
assert gov.inject_allowed_models is True, (
    "C6 REGRESSION: flag silently discarded — need BOTH field + loader line"
)
```

#### C1 Test: Error Propagation Mitigation

```python
# C1 INTEGRATION TEST: governor with 1 errored councilor + 3 successful
# → terminal status COMPLETED (not ERROR) after clear_councilor_errors

# Setup: spawn governor, spawn 4 councilors, make 1 error
# 1. Governor spawns 4 councilors (3 succeed, 1 errors)
# 2. Governor collects results (3 results available — NORMAL synthesis path)
# 3. Governor synthesizes successfully
# 4. Governor calls clear_councilor_errors()
# 5. Governor delivers

# Verify:
bus = get_dependency_bus()
# Before clear: had_parent_error(governor_id) should be True (1 councilor errored)
assert bus.had_parent_error(governor_id) is True, "Bus should have recorded the child error"

# Governor calls clear_councilor_errors
await clear_councilor_errors()

# After clear: had_parent_error should be False
assert bus.had_parent_error(governor_id) is False, "clear_councilor_errors should have cleared the flag"

# Governor finalizes → status should be COMPLETED, not ERROR
# (The _apply_parent_error_override at job_feedback_observer.py:148 checks had_parent_error)
```

#### W7 Test: Model Canonicalization

```python
# W7 TEST: case-insensitive input normalizes to canonical name
# Spawn with "GPT-4O" → should use canonical "gpt-4o" (or whatever's in allowed_models)

allowed = ["gpt-4o", "claude-3-5-sonnet"]
# ... setup config with these allowed models ...

result = await spawn_councilor(councilor_agent_id="developer", model="GPT-4O")
# Should succeed (case-insensitive match) and use canonical "gpt-4o"
assert "gpt-4o" in result.lower(), "Should normalize to canonical lowercase name"

# Dedup test: spawning "GPT-4O" then "gpt-4o" should be the SAME canonical model
# (governor workflow checks manifest for duplicates)
```

#### D9 Test 1: Single Result → Degraded Notice (NEW)

```python
# D9 REV 3 TEST: 1 result available → degraded synthesis with confidence notice

# Setup: spawn governor with 4 councilors, 3 fail, 1 succeeds
# Action: governor collects 1 result, calls degraded synthesis path
# Verify output starts with degraded notice

gov = spawn_governor_instance()
# ... mock 3 councilors to fail, 1 to succeed ...
collected = await gov.collect_results(timeout=30min)
assert len(collected) == 1, "Should have 1 result"

result = await gov.synthesize()
assert result.startswith("⚠️ Confidence Notice:"), (
    "D9 REGRESSION: degraded notice missing for single result"
)
assert "single councilor source" in result, "Notice should mention single source"
assert "confidence is reduced" in result, "Notice should mention reduced confidence"
assert "COMPLETED" in result, "Notice should mention the councilor status"
```

#### D9 Test 2: Extension Past 30min Logged to Manifest (NEW)

```python
# D9 REV 3 TEST: governor extends a councilor's deadline past 30min soft limit
# Manifest must reflect deadline_extended=true and updated deadline

# Setup: spawn governor with 1 councilor, councilor is RUNNING at 30min
# Action: governor calls get_instance_info, decides to extend, updates manifest
# Verify manifest shows extended deadline

gov = spawn_governor_instance()
councilor = await spawn_councilor(councilor_agent_id="developer", model="gpt-4o")

# Mock time = 31min (past 30min soft limit)
with mock_time(minutes=31):
    # Governor checks status — RUNNING
    status = await get_instance_info(councilor.instance_id)
    assert status == "RUNNING"

    # Governor decides to extend (long-running task)
    await gov.extend_councilor_deadline(councilor.instance_id, new_minutes=45)

# Verify manifest
manifest = await shared_context_metadata.get("council_manifest")
councilor_entry = next(c for c in manifest["councilors"] if c["instance_id"] == councilor.instance_id)
assert councilor_entry["deadline_extended"] is True, (
    "D9 REGRESSION: deadline_extended not set after extension"
)
assert councilor_entry["deadline"] != councilor_entry["deadline_hard_cap"], (
    "Soft deadline should be < hard cap"
)
assert councilor_entry["deadline_hard_cap"] is not None, "Hard cap must be set"
```

#### D9 Test 3: 1h Hard Kill → Partial Result Counted (NEW)

```python
# D9 REV 3 TEST: at 1h hard limit, force-kill councilor, capture partial result
# Partial result counts as 1 degraded result

# Setup: spawn governor with 1 councilor, councilor is RUNNING at 1h
# Action: governor calls terminate_instance, captures partial output
# Verify: partial result included in synthesis, status = PARTIAL_TIMED_OUT

gov = spawn_governor_instance()
councilor = await spawn_councilor(councilor_agent_id="developer", model="gpt-4o")

# Mock time = 60min (1h hard limit)
with mock_time(minutes=60):
    # Governor hits hard limit → terminate and capture partial
    partial = await gov.harvest_partial_result(councilor.instance_id)
    assert partial.status == "PARTIAL_TIMED_OUT"
    assert partial.content is not None, "Partial result must have content"

# Verify synthesis uses the partial
result = await gov.synthesize()
assert result.startswith("⚠️ Confidence Notice:"), (
    "1 partial result = 1 degraded result → notice expected"
)
assert "PARTIAL_TIMED_OUT" in result, "Notice should mention partial status"
```

#### D9 Test 4: No Extension Past 1h Hard Cap (NEW)

```python
# D9 REV 3 TEST: governor cannot extend past 1h hard cap

# Setup: councilor at 30min, governor tries to extend to 90min
# Verify: extension rejected, deadline stays at 1h hard cap

gov = spawn_governor_instance()
councilor = await spawn_councilor(councilor_agent_id="developer", model="gpt-4o")

# Mock time = 31min
with mock_time(minutes=31):
    # Governor tries to extend to 90min (past 1h hard cap)
    with pytest.raises(ValueError, match="hard cap"):
        await gov.extend_councilor_deadline(councilor.instance_id, new_minutes=90)

# Manifest unchanged
manifest = await shared_context_metadata.get("council_manifest")
councilor_entry = next(c for c in manifest["councilors"] if c["instance_id"] == councilor.instance_id)
assert councilor_entry["deadline_extended"] is False, "Should not extend past 1h"
```

### Task 4: Backward Compatibility

```raw
[ ] spawn_instance still has optional model (default None)
[ ] spawn_instance still does silent fallback on invalid model
[ ] leader agent still works (spawns developer, etc.)
[ ] wanderer agent still works (spawns coder)
[ ] No existing tests broken
[ ] Run: pytest tests/test_instance_tools.py tests/test_instance_lifecycle.py -v
```

### Task 5: E2E Council Workflow Tests (REVISED — D4 max 4, C1 clearing)

#### Scenario A: Happy Path
```raw
1. Spawn governor
2. send_message: "Use councilor_agent_id 'developer'. Implement fibonacci."
3. Governor should:
   a. Read <allowed_models> from system prompt
   b. Write council manifest to shared_context_metadata (W4)
   c. Spawn ≤4 councilors (one per model) (D4)
   d. Dispatch request to each (W1/W2 structured)
   e. Collect results (D9 tiered deadline + degraded quorum)
   f. If ≥1 result → synthesize (2+ normal with no notice; 1 with degraded notice)
   g. Call clear_councilor_errors() (C1/D7) ← NEW
   h. Deliver final answer (with degraded notice if 1 result)
4. Verify:
   - ≤4 councilors spawned (D4)
   - Governor terminal status = COMPLETED (not ERROR) (C1)
   - clear_councilor_errors was called before delivery
   - If 1 result: output starts with "⚠️ Confidence Notice:" block
```

#### Scenario B: Missing councilor_agent_id
*(Same as Rev 1 — STOP, ask for valid agent_id.)*

#### Scenario C: Partial Failure (C1 focus)
```raw
1. Spawn governor with 4 councilors
2. Force 1 councilor to error (e.g., invalid task)
3. Governor should:
   a. Collect 3 successful + 1 failed results
   b. 3 results available → NORMAL synthesis (no degraded notice)
   c. Call clear_councilor_errors() ← clears sticky flag
   d. Deliver synthesized answer
   e. Terminal status = COMPLETED (NOT ERROR) ← C1 mitigation working
4. This is the KEY test: without clear_councilor_errors, status would be ERROR.
```

#### Scenario D: All Councilors Fail
```raw
1. All 4 councilors error
2. Governor should:
   a. Collect 0 results (all FAILED)
   b. 0 results → synthesis impossible → report failure
   c. Do NOT call clear_councilor_errors() ← let ERROR propagate
   d. Terminal status = ERROR ← correct behavior
```

#### Scenario C2: NEW — Single Result (D9 degraded quorum)
```raw
1. Spawn governor with 4 councilors
2. 3 councilors fail or time out, 1 succeeds
3. Governor should:
   a. Collect 1 result (COMPLETED)
   b. 1 result → DEGRADED synthesis (with degraded-confidence notice)
   c. Call clear_councilor_errors() ← synthesis succeeded
   d. Deliver with degraded notice prepended
4. Verify:
   - Output starts with "⚠️ Confidence Notice:" block
   - Notice mentions "single councilor source" and "confidence is reduced"
   - Terminal status = COMPLETED (C1 cleared)
   - Notice includes the model name and the status (COMPLETED)
```

#### Scenario D2: NEW — 1h Hard Kill with Partial Result (D9)
```raw
1. Spawn governor with 4 councilors
2. 3 councilors complete quickly; 1 councilor is mid-execution at 30min
3. Governor extends the 1 councilor (judges it RUNNING + long-running task)
4. At 1h mark, hard kill the councilor (call terminate_instance)
5. Capture partial result from the killed councilor
6. Governor should:
   a. Have 3 complete + 1 partial (PARTIAL_TIMED_OUT)
   b. 4 results available → NORMAL synthesis (2+ results, no degraded notice)
   c. Note the partial result limitations in synthesis
   d. Terminal status = COMPLETED
7. Verify:
   - Manifest shows deadline_extended=true and deadline_hard_cap honored
   - Partial result was included in synthesis
   - No degraded notice (because 4 results ≥ 2)
```

#### Scenario D3: NEW — 1h Hard Kill, Only Partial Result (D9)
```raw
1. Spawn governor with 4 councilors
2. All 4 councilors hit 1h hard limit; all PARTIAL_TIMED_OUT
3. 3 of 4 partials are empty; 1 has usable partial output
4. Governor should:
   a. Have 1 usable partial result (count as 1 degraded result)
   b. 1 result → DEGRADED synthesis with degraded notice
   c. Notice should mention status=PARTIAL_TIMED_OUT
   d. Terminal status = COMPLETED
5. Verify:
   - Output starts with degraded notice
   - Notice mentions "PARTIAL_TIMED_OUT" status
```

#### Scenario E: No Models Configured
*(Same as Rev 1 — governor sees "No model restriction", asks user.)*

#### Scenario F: Iteration Cap (D5)
```raw
1. Councilors disagree
2. Governor should: max 2 refinement rounds, then MUST deliver
3. Verify: total rounds ≤ 2
```

#### Scenario G: Crash Recovery (W4/D8)
```raw
1. Governor spawns 2 councilors, writes manifest
2. Simulate crash (kill daemon mid-council)
3. Restart daemon
4. Governor resumes:
   a. Reads council_manifest from shared_context_metadata
   b. Checks councilor statuses via get_instance_info
   c. Collects available results
   d. Proceeds with synthesis or waits for remaining
```

### Task 6: Record Knowledge

```python
experience(text="Governor council-manager implemented: spawn_councilor + clear_councilor_errors tools inside create_instance_tools() (council category). C1 mitigation: clear_councilor_errors clears sticky _parent_errored before delivery. inject_allowed_models flag requires BOTH field on AgentMetadata AND loader line (extra='ignore'). append_allowed_models uses manager.config (no underscore). Max 4 councilors (WorkerPool=4).")
```

---

## Test Strategy Summary (REVISED — Rev 3 adds D9)

| Test Type | Scope | C-Fix Covered |
|-----------|-------|---------------|
| Unit: tool validation | spawn_councilor raises on invalid inputs | C3, C4 |
| Unit: appender | append_allowed_models in isolation | C2, W8 |
| **Integration: C5** | Tool bound on real governor instance | C5 |
| **Integration: C6** | Flag survives meta.json loading | C6 |
| **Integration: C1** | clear_councilor_errors clears sticky flag | C1 |
| **Integration: W7** | Model canonicalization + dedup | W7 |
| **Integration: D9-1** | 1 result → degraded notice | D9 |
| **Integration: D9-2** | Extension past 30min logged to manifest | D9 |
| **Integration: D9-3** | 1h hard kill → partial result counted | D9 |
| **Integration: D9-4** | No extension past 1h hard cap | D9 |
| E2E: happy path | Full council workflow | D4, W1/W2, W4 |
| E2E: partial failure | 1 error + 3 success → COMPLETED | C1 |
| E2E: all fail | All error → ERROR (correct) | C1 |
| E2E: 1-result degraded | 1 result → degraded notice prepended | D9 |
| E2E: 1h hard kill with partial | Partial result counts in synthesis | D9 |
| E2E: crash recovery | Resume from manifest | W4/D8 |
| Regression | spawn_instance backward compat | — |

## Deliverables

- [ ] meta.json wiring verified
- [ ] **Assertion bug fixed** (suggestion #1) — `text = str(e).lower(); assert "model" in text`
- [ ] C5 integration test: spawn_councilor bound on real instance
- [ ] C6 integration test: inject_allowed_models survives loading
- [ ] C1 integration test: partial failure → COMPLETED after clear
- [ ] W7 test: model canonicalization + dedup
- [ ] spawn_instance backward-compatible
- [ ] **D9-1:** 1 result → degraded notice present in output
- [ ] **D9-2:** extension past 30min logged in manifest (deadline_extended=true)
- [ ] **D9-3:** 1h hard kill → partial result included in synthesis
- [ ] **D9-4:** extension past 1h hard cap rejected
- [ ] E2E Scenarios A-G pass
- [ ] Knowledge recorded
