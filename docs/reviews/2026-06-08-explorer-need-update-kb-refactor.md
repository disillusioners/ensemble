# Code Review: Explorer `Need Update KB` Refactor

**Date:** 2026-06-08
**Reviewer:** Kilo (review pass on commit `910c5ffc`)
**Commit:** `refactor(explorer): derive Need Update KB from read_file tool call`
**Author:** Kha <khanguyenmail@gmail.com>
**Branch:** (working tree, committed)
**Status:** ⚠️ **The refactor is well-motivated and the implementation is clean, but it removes an explicit guard against a known failure mode (RAG errors triggering spurious KB updates). Decide on a remediation before merge.**

---

## 1. TL;DR

| Aspect | Verdict |
|--------|---------|
| Architectural direction correct? | ✅ Yes — replace agent-self-reported flag with deterministic tool-call signal |
| Implementation clean? | ✅ Mostly — shared scan helper is a good factoring |
| Test coverage adequate? | ✅ Good — extensive cases for new helper, backward-compat tests present |
| Behavior fully equivalent to old? | ❌ No — RAG-error path now enqueues kb-importer jobs (regression) |
| Minor nits? | ⚠️ One whitespace glitch in test, one minor duplication, one missing comment |
| Should we merge as-is? | ⚠️ Only if RAG-error regression is accepted (or mitigated) |
| Follow-ups needed? | ✅ Yes — see §6 |

---

## 2. What Changed

**Before:**
- Explorer agent emitted a `## Need Update KB: true|false` heading in its response (mandatory per `rule.md`).
- Daemon parsed the heading with regex (`_SHOULD_UPDATE_KB_PATTERN` + `_parse_should_update_kb`).
- Daemon stripped the heading from the response before returning to the caller.
- `true` → enqueue kb-importer job (fire-and-forget).

**After:**
- Explorer no longer emits or is required to emit the heading.
- Daemon inspects the child's LangGraph checkpoint for any `read_file` tool call via a new helper (`_check_read_file_called_via_checkpoint`).
- `read_file` present → enqueue kb-importer job.
- Response is returned verbatim (no stripping).

**Files changed (7):**

| File | Type of change |
|------|----------------|
| `agents/explorer/rule.md` | Removed `## Need Update KB:` mandate; updated forbidden-tools reason |
| `agents/explorer/soul.md` | Removed heading from identity traits |
| `agents/explorer/workflow.md` | Removed heading from format spec; removed guidance section |
| `daemon/mcp/kb_server.py` | Replaced parse-and-strip with checkpoint-based detection |
| `daemon/tools/knowledge_tools.py` | Same swap; introduced `_scan_checkpoint_for_tool_match` helper |
| `tests/unit/test_mcp_kb_server.py` | Rewrote tests around new mechanism |
| `tests/unit/tools/test_knowledge_tools.py` | Same + added `TestCheckReadFileCalledViaCheckpoint` suite |

---

## 3. What Works Well

### 3.1 Removes LLM unreliability from the signal path
The previous `## Need Update KB:` heading was a magic string the agent had to remember to emit. The LLM could:
- forget the heading,
- emit it with bold/italic markers,
- emit malformed values,
- include the wrong value (high false-positive or false-negative rate).

The new mechanism reads directly from the LangGraph checkpoint — a deterministic, source-of-truth record of what the agent actually did. This is the right architectural direction.

### 3.2 Smaller agent contract
- Explorer drops one mandatory heading.
- Fewer output tokens per response.
- One less thing for the model to format correctly.
- The forbidden-tools reason for `rag_insert_text` becomes more natural ("Experiencer handles knowledge upserts, not Explorer" reads better than the old "flag gaps via `## Need Update KB:` heading instead").

### 3.3 Shared scan helper is a clean abstraction
`_scan_checkpoint_for_tool_match(checkpointer, instance_id, matches, log_label)` is a nice factoring. The two callers become thin wrappers:

```python
_check_rag_queried_via_checkpoint(...)        # matches=RAG_TOOL_NAMES, label="RAG"
_check_read_file_called_via_checkpoint(...)   # matches=KB_GAP_TOOL_NAME, label="read_file"
```

This is a good demonstration of "extract the common scan, specialize the call sites." The log line and exception behavior are uniform.

### 3.4 Comprehensive test coverage for the new helper
`TestCheckReadFileCalledViaCheckpoint` covers 10+ scenarios:
- positive case (read_file present),
- negative case (other tools only),
- exception during `aget` (graceful degradation),
- `aget` returns `None`,
- empty messages,
- `list_directory`/`grep_files`/`glob_files` (other filesystem tools),
- mixed tools with one read_file,
- object-style `tool_calls` (not just dicts),
- `KB_GAP_TOOL_NAME` constant value,
- non-dict checkpoint state,
- `channel_values` without `messages` key,
- empty `tool_calls` list,
- message without `tool_calls` attribute,
- `CheckpointerAdapter` unwrapping (`raw_saver.aget` is called, not `adapter.aget`).

This is genuinely thorough. The dict-vs-object tool_call coverage and the adapter-unwrap regression test are particularly good — they protect against the two places where a refactor of LangGraph internals would silently break us.

### 3.5 Backward-compatibility is explicit
`test_explore_ignores_legacy_heading_when_no_read_file` and the renamed `test_explore_returns_response_with_legacy_heading_intact` explicitly nail down the precedence: the system check wins, legacy headings are inert. This is the right call — anyone who has a cached prompt or downstream prompt-template referencing the heading is unaffected by a behavior change.

### 3.6 `await_count == 2` test update
`TestExploreCheckpointIntegration` was updated from `assert_called_once()` to `await_count == 2`, which is a clean signal of the new double-checkpoint-inspection behavior. The test docstring is updated to explain why.

---

## 4. Concerns

### 4.1 ⚠️ Behavioral regression in the RAG-error path

**Severity:** Medium-to-high depending on kb-importer behavior.

**Old contract (per `rule.md`):**
> Set `## Need Update KB: false` when RAG returned an error — timeouts, connection failures, 504s, or any RAG error mean you cannot assess KB state. Only set `true` when RAG returned successfully but with missing information.

**Old behavior:**
- RAG errors → explorer emits `## Need Update KB: false` → daemon does NOT enqueue kb-importer.

**New behavior:**
- RAG errors → explorer falls back to `read_file` (per workflow.md: "Always try RAG first, browse files on weak confidence") → `read_file_called = True` → daemon DOES enqueue kb-importer.

The new behavior treats a RAG outage the same as a KB gap. A kb-importer job is enqueued to "fill" a gap that doesn't exist — the KB may already contain the information; we just couldn't reach it.

**Impact depends on kb-importer idempotency:**
- If kb-importer is fully idempotent (the `_generate_idempotency_key` helper suggests it is — `f"explorer-kb-update:{project_id}:{query.lower().strip()}"` is deterministic), the worst case is wasted compute.
- If deduplication is content-based and the explorer re-summarizes the same content with a slightly different phrasing, we could end up with duplicate knowledge graph nodes.

The old explicit guard existed for a reason. We should either:
- **(a) Accept the regression and document it** in changelog / `rule.md`.
- **(b) Mitigate in code** by gating `read_file_called` on `rag_queried` — only treat it as a KB gap if RAG was actually queried (regardless of outcome). This restores the spirit of the old guard without depending on a magic heading.
- **(c) Mitigate in the agent** by updating `workflow.md` to say "if RAG returned an error, do not browse files for the same query — just report the error." This re-aligns the agent's behavior with the new signal.

Recommendation: option (b) is the closest to the original intent and is a one-line change.

### 4.2 Heuristic breadth — `read_file` is a general-purpose tool
The new signal fires on **any** `read_file` call, regardless of intent. If the explorer ever uses `read_file` for reasons other than KB fallback (confirmation, citation, gathering context that RAG already provided well, reading a file mentioned in the user's question), we will spuriously enqueue a kb-importer job.

The previous heading was narrower by design — "true ONLY if RAG returned successfully AND file browsing found information that RAG did not return." A comment in the new code acknowledging this trade-off would help future maintainers:

```python
# Heuristic: read_file implies the KB lacked the answer and the
# explorer fell back to filesystem. May over-trigger if read_file
# is used for non-fallback reasons in future explorer behaviors.
```

Consider a tighter signal if it becomes a problem (e.g., `read_file` after a RAG miss, or a dedicated "explore" tool).

### 4.3 Light duplication between `kb_server.py` and `knowledge_tools.py`
Both files repeat the manager/checkpointer guard:

**`daemon/mcp/kb_server.py:280-286`:**
```python
read_file_called = False
if child_instance_id and hasattr(_manager, "_checkpointer") and _manager._checkpointer:
    read_file_called = await _check_read_file_called_via_checkpoint(
        _manager._checkpointer, child_instance_id
    )
```

**`daemon/tools/knowledge_tools.py:500-507`:**
```python
if child_instance_id and hasattr(manager, "_checkpointer") and manager._checkpointer:
    rag_queried = await _check_rag_queried_via_checkpoint(
        manager._checkpointer, child_instance_id
    )
    read_file_called = await _check_read_file_called_via_checkpoint(
        manager._checkpointer, child_instance_id
    )
```

A small helper would deduplicate:

```python
async def _check_tool_called(manager, instance_id, tool_name: str | Iterable[str], label: str) -> bool:
    checkpointer = getattr(manager, "_checkpointer", None) if instance_id else None
    if not checkpointer:
        return False
    return await _scan_checkpoint_for_tool_match(checkpointer, instance_id, tool_name, label)
```

Then each call site becomes a single line. Minor, but worth doing while the duplication is fresh.

### 4.4 Response is no longer stripped — caller contract change
The old code stripped `## Need Update KB: ...` from the response before returning. The new code returns the response verbatim. `test_explore_returns_response_with_legacy_heading_intact` codifies this.

Callers that previously got a clean answer section will now see a `## Need Update KB: true|false` line in their response (if the explorer's prompt template still includes it, e.g., for older prompt caches). This is probably fine — most callers ignore the heading — but worth a mention in changelog/release notes.

### 4.5 Whitespace glitch introduced by the commit
`tests/unit/tools/test_knowledge_tools.py:411`:
```python
        result =         await experience_tool.ainvoke({"text": "Test knowledge"})
```
`git blame` confirms this was introduced by `910c5ffc`. Spurious extra spaces — likely a botched find/replace during the test rewrite. Pre-existing lint config won't catch it (project has no ruff/black/mypy per AGENTS.md), but it's a one-second fix.

### 4.6 Docstring nit
`_scan_checkpoint_for_tool_match`'s docstring says:
> matches: Either a single tool name (str) or a collection of names (any container supporting `in`).

Worth noting that the existing call sites pass either a `frozenset` (`RAG_TOOL_NAMES`) or a single string (`KB_GAP_TOOL_NAME = "read_file"`). Future readers may wonder why `KB_GAP_TOOL_NAME` isn't a set. A small example in the docstring or an explicit "single-tool call sites pass a str" note would help.

---

## 5. What I'd Like to See Before Merge

1. **Address §4.1 (RAG-error regression).** The cheapest mitigation is option (b) — gate `read_file_called` on `rag_queried` so the new signal is "RAG was queried AND explorer still had to read a file." This restores the spirit of the old guard with one extra check.
2. **Fix the whitespace glitch in `test_knowledge_tools.py:411`.**
3. **Add a one-line comment near the new heuristic** acknowledging the read_file-equals-KB-gap assumption and how to relax it.

If §4.1 is accepted as a known behavioral change, document it in `rule.md` (Restore the explicit "RAG error → no KB update" guidance, redirected at the agent: "if RAG errors out, do not browse files; just report the error").

---

## 6. Follow-ups (Not Blocking)

- **Extract the duplicate manager/checkpointer guard** into a small helper (§4.3).
- **Consider tightening the heuristic** if `read_file` over-triggers in practice (§4.2).
- **Add a regression test for the RAG-error + read_file path** specifically, regardless of which option is chosen for §4.1. The current test matrix doesn't have a "RAG errored, explorer fell back to files, what happens?" case.

---

## 7. Detailed File-Level Notes

### `agents/explorer/rule.md`
- ✅ Removed the heading from the "Must" list.
- ✅ Updated the "Immutable" list accordingly.
- ✅ Updated the `rag_insert_text` forbidden reason to be more accurate.
- ℹ️ The previous "Set `## Need Update KB: false` when RAG returned an error" rule is gone. The intent of that rule needs a new home (see §4.1).

### `agents/explorer/soul.md`
- ✅ Step 4 of the operating principles updated.
- ✅ "Disciplined Formatter" line updated.

### `agents/explorer/workflow.md`
- ✅ Step 6 format spec updated.
- ✅ Second example block updated.
- ℹ️ The "Guidance" subsection (`Set ## Need Update KB: to true ONLY if...`) is removed. The new mechanism handles the equivalent decision automatically, but the agent's understanding of "when to browse files" should still distinguish RAG-success-with-weak-signal from RAG-error (see §4.1).

### `daemon/mcp/kb_server.py`
- ✅ Imports cleaned up.
- ✅ `invoke_agent_and_wait` now called with `return_instance_id=True` and tuple-unpacked.
- ✅ Read-file check uses the new helper.
- ✅ No more response-stripping.
- ℹ️ Duplicates the checkpointer guard from `knowledge_tools.py` (see §4.3).

### `daemon/tools/knowledge_tools.py`
- ✅ Pattern + parse function removed.
- ✅ Shared scan helper introduced.
- ✅ Two thin wrappers added.
- ✅ `KB_GAP_TOOL_NAME` constant added.
- ✅ Explore tool uses the new signal.
- ℹ️ Same duplication of checkpointer guard as above.

### `tests/unit/test_mcp_kb_server.py`
- ✅ Tests for the old behavior (`TestExploreReturnsResultWithKbHeadingStripped`, `TestExploreTriggersKbUpdateWhenFlagTrue`) replaced with new mechanism equivalents.
- ✅ New `test_explore_ignores_legacy_heading_when_no_read_file` nails down precedence.
- ✅ `invoke_agent_and_wait` return-tuple updated consistently across all test sites.

### `tests/unit/tools/test_knowledge_tools.py`
- ✅ `TestParseShouldUpdateKb` removed (function no longer exists).
- ✅ `TestCheckReadFileCalledViaCheckpoint` added with broad coverage.
- ✅ `TestExploreJobEnqueue` rewired around the new signal.
- ✅ `mock_manager_with_checkpoint` fixture is well-designed (default = no read_file, override per-test).
- ❌ Whitespace glitch on line 411.
- ℹ️ No test for the RAG-error + read_file combination (see §4.1).

---

## 8. Final Verdict

**Conditional approve.** The refactor is the right direction and the implementation is mostly clean. The main blocker is the RAG-error regression (§4.1) — please pick one of the three options and update either code or `rule.md` accordingly. The other items are quality-of-life improvements that can land in a follow-up.
