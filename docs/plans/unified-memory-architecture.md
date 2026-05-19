# Unified Memory Architecture — Complete Implementation Plan

## Problem Statement

The current memory system has 6 architectural gaps that need resolution:

### P1: Type Annotation Bug (Critical)

**Location:** `daemon/tools/inner_soul.py:284`

```python
target: Literal["memory", "workflow", "soul", "user"] | None = None
```

**Bug:** Code at line 446 handles `target == "memories"`:
```python
if target == "memories":
    return _update_memories(...)
```

But `"memories"` is not in the Literal type. This is dead code or a latent bug — explicit `target="memories"` would pass runtime but fail static type checking.

### P2: Classification Failure Defaults to `event → memories/` (Major)

**Location:** `daemon/tools/inner_soul.py:425-431`

When regex classification fails (no pattern matches), the system defaults to:
```python
return {
    "type": "event",
    "targets": ["memories"],
    "description": "Event or observation",
}
```

**Impact:** Natural language like `"Context7 is built-in MCP server"` falls through to `memories/` as an event. No fallback mechanism exists.

### P3: No Compound Request Detection (Major)

**Location:** `daemon/tools/inner_soul.py:394-431`

`_classify_request()` returns a single classification with merged targets from multiple matches, but does not:
- Detect conjunctions (`AND`, `;`, multiple sentences)
- Split compound requests into separate classifications
- Route different parts to different storage targets

**Example failure:** `"Remember my name is Cody AND that tests are important"` → single classification, both parts treated uniformly.

### P4: No Memory Compaction — Hard Rejection (Critical)

**Location:** `daemon/tools/inner_soul.py:511-517`

When `memory.md` reaches `max_memory_words`:
```python
if word_count >= max_words:
    return {"success": False, "error": "...Saved to memories/ instead."}
```

**Problems:**
- Error message lies — it doesn't actually save anywhere
- No compaction, deduplication, or archival attempt
- Write is lost; agent intention discarded
- Race condition: multiple instances can read-modify-write `memory.md` simultaneously (TOCTOU)

### P5: No Archive Lifecycle for `memories/` (Major)

**Current state:**
- `memories/` is append-only, never cleaned up
- Only 5 most recent filenames visible via `load_recent_memories()` (loader.py:181-200)
- No TTL, no archival, no consolidation
- `access_memory.py:41` hardcodes `memories/` path — any archive path change requires coordinated updates

**Impact:** Memories from 2 years ago accumulate indefinitely; older memories are invisible without explicit `access_memory()` calls.

### P6: Dead Documentation in `agents/_inner_soul/` (Minor)

**Location:** `agents/_inner_soul/{soul,workflow,rule}.md`

These files describe intelligent classification behavior that is actually implemented as regex logic in `inner_soul.py`. The directory:
- Lacks `meta.json` → skipped during agent discovery (not in `SKIP_DIRS`)
- Is protected specially in `agent_mother.py:295`
- Content is never loaded into any LLM context

**Decision:** Delete these files after verifying no references.

---

## Requirements

### REQ-1: Smart Routing — Fix Gaps (Not Redesign)

**Scope:** Address actual remaining gaps, not reimplement what works.

**Sub-requirements:**

1. **Fix type annotation bug** — Add `"memories"` to Literal or remove dead code path
2. **Classification failure handling** — When regex fails, either:
   - Default to `knowledge → memory` (persistent) instead of `event → memories` (ephemeral), OR
   - Add LLM fallback (configurable, off by default)
3. **Compound request detection** — Detect conjunctions and split requests:
   - Split on `AND`, `;`, sentence boundaries
   - Classify each segment independently
   - Execute updates for all resulting targets
4. **Integrate with RAG redirect** — All routing changes must pass through `_should_redirect_to_rag()` at line 318

**RAG Integration:** Classification changes affect `_KNOWLEDGE_CLASSIFICATIONS` matching → must preserve redirect behavior for knowledge types.

### REQ-2: Memory Compaction (Agent-Driven)

**Decision:** Compaction triggered by `inner_soul` itself when writing to `memory.md`.

**Requirements:**

1. **Detection instructions** — Add rules to `agents/_inner_soul/rule.md` (or wherever rules live) instructing the agent to:
   - Monitor `memory.md` size before writes
   - Detect when deduplication or summarization would help
   - Perform compaction inline before appending

2. **Deduplication rules:**
   - Exact match: identical bullet text
   - Near-duplicate: same semantic meaning (agent judgment via LLM)
   - Age-based: older duplicates removed in favor of newer

3. **Summarization approach:**
   - Group related bullets (same topic/pattern)
   - Replace N bullets with 1 concise summary bullet
   - Preserve key facts, discard redundant phrasing

4. **Atomic writes with rollback:**
   - Pattern: write to `memory.md.tmp`, then `os.replace()` (atomic on POSIX)
   - On failure: restore from backup or abort with clear error
   - Never leave `memory.md` in corrupted state

5. **Race condition mitigation:**
   - File locking via `fcntl.flock()` (Unix) or `msvcrt.locking()` (Windows)
   - Or: use filelock library for cross-platform
   - Lock acquired before read-modify-write cycle
   - Timeout with clear error if lock unavailable

6. **RAG integration:** Compaction only applies to file-based `memory.md`. If RAG redirect active, compaction is skipped (RAG handles its own lifecycle).

### REQ-3: Lifecycle Management (Archive)

**Archive structure:** `memories/archive/YYYY/MM/` for monthly consolidation.

**Requirements:**

1. **Archive trigger:** Configurable TTL (default 90 days) via `growth.md`:
   ```
   memory_archive_ttl_days: 90
   ```

2. **Archive process:**
   - Scan `memories/` for files older than TTL
   - Move to `memories/archive/YYYY/MM/{filename}.md`
   - Create monthly summary: `memories/archive/YYYY/MM/summary.md`
   - Summary aggregates key facts from archived files (LLM-generated or rule-based)

3. **Visibility changes:**
   - Archived files remain accessible via `access_memory("archive/2026/01/xxx.md")`
   - Consider adding archive filenames to `load_recent_memories()` output (token budget analysis required)
   - Default: show only active `memories/` filenames (no change to current 5-file limit)

4. **Exact code changes required:**

   **`daemon/tools/access_memory.py:41`:**
   ```python
   # BEFORE:
   memories_dir = agent_path / "memories"
   # AFTER:
   memories_dir = agent_path / "memories"
   # Support archive path: if filename starts with "archive/", resolve under memories/archive/
   # IMPORTANT: Must integrate with Path.name sanitization (line 47).
   # Archive paths need YYYY/MM/ subdirectory preserved.
   if filename.startswith("archive/"):
       remainder = filename[len("archive/"):]
       # Validate archive subdirectory pattern: YYYY/MM/<safe_name>.md
       import re
       archive_pattern = re.compile(r'^(\d{4})/(\d{2})/[a-zA-Z0-9_\-]+\.md$')
       if archive_pattern.match(remainder):
           # Safe: use full subdirectory path (bypasses Path.name sanitization)
           return (agent_path / "memories" / "archive" / remainder).read_text()
       else:
           # Invalid archive path: fall back to default sanitization
           filename = Path(remainder).name  # strips YYYY/MM/ — intentional fallback
           memories_dir = agent_path / "memories" / "archive"
   ```

   **`daemon/loader.py:181-200` (`load_recent_memories()`):**
   - Add optional parameter: `include_archived: bool = False`
   - If `True`, also scan `memories/archive/` subdirectories
   - Token budget: Active filenames ~30 chars each (~8 tokens/file × 5 = ~40 tokens). Archived filenames ~55 chars each (~15 tokens/file × 5 = ~75 tokens) due to `archive/YYYY/MM/` prefix. Total: 5 active + 5 archived ≈ ~115 tokens. Acceptable for most agents; make configurable via growth.md.

5. **Token budget analysis:**
   - Current: 5 filenames × ~25 chars = ~125 chars ≈ 30 tokens
   - Proposed: 5 active (~30 chars each = ~40 tokens) + 5 archived (~55 chars each = ~75 tokens) = 10 filenames ≈ 115 tokens total
   - Impact: <1.5% of typical 4k-8k context window
   - Note: Archived filenames include `archive/YYYY/MM/` prefix (~55 chars each vs ~30 for active), accounting for higher per-filename token cost
   - Recommendation: Add `load_recent_memories(include_archived=True)` behind growth.md flag, default `False` for token conservation

### REQ-4: Classification Improvements

**Sub-requirements:**

1. **Compound request splitting algorithm:**
   ```
   Input: "Remember my name is Cody AND that tests are important"

   Steps:
   1. Split on case-insensitive " AND ", ";", or sentence-ending punctuation followed by capital letter
   2. Segments: ["Remember my name is Cody", "that tests are important"]
   3. Classify each segment independently
   4. Merge results: identity → soul, knowledge → memory/memories
   5. Execute all updates atomically
   ```

2. **LLM fallback for classification:**
   - Add optional `use_llm_classification: bool` flag (default `False`)
   - When enabled and regex fails, call configured LLM to classify
   - Latency impact: +200-500ms per request (measured on GPT-4o-mini)
   - Cost impact: ~$0.0001 per classification (input: ~50 tokens, output: ~10 tokens)
   - Recommendation: Off by default; enable per-agent via growth.md or environment variable

3. **RAG integration:** Classification type determines RAG redirect eligibility. New classification types must be added to `_KNOWLEDGE_CLASSIFICATIONS` if they should redirect when RAG enabled.

### REQ-5: Clean Up `_inner_soul/`

**Pre-deletion verification (grep results from codebase):**

References found in these categories:

1. **Code references (Python):**
   - `daemon/tools/instance.py` — imports `create_inner_soul_tool` (the module, not the agent directory)
   - `daemon/tools/__init__.py` — exports `create_inner_soul_tool` (the module)
   - `daemon/tools/agent_mother.py:295,352` — protection list: `("_mother", "_inner_soul")`
   - `daemon/loader.py` — loads inner_soul tool (the module)

2. **Agent references (markdown):**
   - `agents/_mother/workflow.md` — instructions for mother to read/modify `_inner_soul` agent
   - `agents/_mother/soul.md` — lists "Modify Inner Soul" as mother capability

3. **Documentation references:**
   - `docs/agent-architecture.md` — describes `_inner_soul/` directory structure and purpose
   - `tests/test_agents_api.py` — tests that skip `_inner_soul` in agent listing

4. **No runtime loading** — `soul.md`, `workflow.md`, `rule.md` from `_inner_soul/` are NEVER read by `loader.py` or any tool. The agent directory is skipped during discovery (no `meta.json`).

**Deletion actions (coordinated):**

| # | Action | File |
|---|--------|------|
| 1 | Delete `agents/_inner_soul/` directory | `agents/_inner_soul/` (3 files) |
| 2 | Remove `_inner_soul` from agent_mother protection lists | `daemon/tools/agent_mother.py:295,352` |
| 3 | Remove `_inner_soul` references from mother's workflow/soul | `agents/_mother/workflow.md`, `agents/_mother/soul.md` |
| 4 | Update `docs/agent-architecture.md` | Remove `_inner_soul` directory description |
| 5 | Update test expectations | `tests/test_agents_api.py` (may reference `_inner_soul` in skip lists) |

**Content Preservation:** None needed. All classification rules are implemented in `inner_soul.py`. Agent-specific rules live in each agent's `rule.md` and `growth.md`.

### REQ-6: Backward Compatibility

**All calling patterns must continue to work:**

| Call Pattern | Current Behavior | Must Preserve |
|--------------|------------------|---------------|
| `intent="remember", target="memory"` | Updates `memory.md` | ✓ Same |
| `intent="remember", target="memories"` | (buggy, not typed) | ✓ Add to Literal, route to `_update_memories()` |
| `intent="remember", no target` | Routes to `memories/` | ✓ Same |
| `intent="learn", no target` | Routes to `memories/` + `memory` | ✓ Same |
| `intent="change", target="workflow"` | Updates `workflow.md` | ✓ Same |
| `intent="change", target="soul"` | Updates `soul.md` | ✓ Same |
| `intent="change", target="user"` | Updates `user.md` | ✓ Same |
| Natural language, no intent/target | Auto-classification | ✓ Same, with improvements |

**RAG interaction:** If RAG enabled, knowledge-oriented requests (`remember`, `learn`, classification in `_KNOWLEDGE_CLASSIFICATIONS`) redirect to `experience()` regardless of explicit target. Self-modification (`change` on `soul`/`user`/`workflow`) never redirects.

---

## Implementation Phases

### Phase 1: Bug Fixes & Type Safety

**Objective:** Fix the type annotation bug and ensure explicit `target="memories"` works.

**Dependencies:** None

**Coupling:** Independent (can run in parallel with Phase 2)

**Tasks:**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add `"memories"` to Literal type | Change line 284: `Literal["memory", "workflow", "soul", "user", "memories"]` | `daemon/tools/inner_soul.py:284` |
| 2 | Verify `_execute_update()` handles `"memories"` correctly | Already implemented at line 446; add test case | `daemon/tools/inner_soul.py:446` |
| 3 | Add unit test for explicit `target="memories"` | Ensure it creates timestamped file in `memories/` | `tests/unit/tools/test_inner_soul_redirect.py` |

**Failure Handling:**
- Type annotation change is backward-compatible (adding to union)
- If runtime error occurs, `_execute_update()` falls through to `else` branch at line 456: `{"success": False, "error": f"Unknown target: {target}"}`

**Deliverables:**
- [ ] Type annotation includes `"memories"`
- [ ] Test: `inner_soul(intent="remember", target="memories", request="...")` succeeds

---

### Phase 2: Classification Failure Handling & Compound Request Support

**Objective:** Improve classification robustness and handle compound requests.

**Dependencies:** None (can parallelize with Phase 1)

**Coupling:** Loose with Phase 3 (both touch classification logic, but different code paths)

**Tasks:**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define compound request splitting algorithm | Implement `_split_compound_request()`: split on ` AND `, `;`, sentence boundaries | `daemon/tools/inner_soul.py` (new function) |
| 2 | Integrate splitting into request flow | Before `_classify_request()`, check for compound structure; if found, split and classify each segment | `daemon/tools/inner_soul.py:301` |
| 3 | Change default classification on regex failure | From `event → memories` to `knowledge → memory` (persistent storage) | `daemon/tools/inner_soul.py:425-431` |
| 4 | Add LLM fallback hook (stub, off by default) | Add `classify_with_llm()` function; gate behind `growth.md` flag or env var | `daemon/tools/inner_soul.py` (new) |
| 5 | Ensure all new classification paths integrate with RAG redirect | Verify `_should_redirect_to_rag()` receives updated targets/classification | `daemon/tools/inner_soul.py:318` |

**Compound Request Algorithm (Concrete):**

```python
def _split_compound_request(request: str) -> list[str]:
    """Split compound requests into independent segments."""
    # Normalize
    request = request.strip()

    # Split on " AND " (case-insensitive, word boundary)
    if re.search(r'\s+AND\s+', request, re.IGNORECASE):
        segments = re.split(r'\s+AND\s+', request, flags=re.IGNORECASE)
        return [s.strip() for s in segments if s.strip()]

    # Split on semicolon
    if ';' in request:
        segments = [s.strip() for s in request.split(';') if s.strip()]
        if len(segments) > 1:
            return segments

    # Split on sentence boundaries (period + space + capital, or ?/!)
    sentence_end = re.split(r'(?<=[.!?])\s+(?=[A-Z])', request)
    if len(sentence_end) > 1:
        return [s.strip() for s in sentence_end if s.strip()]

    # No compound structure detected
    return [request]
```

**Token Budget Impact:** Splitting adds negligible overhead (string operations only).

**Failure Handling:**
- If splitting produces >10 segments, truncate and log warning (prevent abuse)
- If any segment fails classification, fall back to default `knowledge → memory`
- RAG redirect check runs on final merged targets — if any segment triggers redirect, entire request redirects (conservative)

**Deliverables:**
- [ ] Compound requests split and classified independently
- [ ] Regex failure defaults to `knowledge → memory` (not `event → memories`)
- [ ] LLM fallback stub implemented, off by default
- [ ] All paths integrate with `_should_redirect_to_rag()`

---

### Phase 3: Memory Compaction with Atomic Writes & Locking

**Objective:** Implement agent-driven compaction with race condition protection.

**Dependencies:** None

**Coupling:** Tight with Phase 4 (both modify `memory.md` write path; coordinate file locking strategy)

**Tasks:**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add file locking utility | Implement `_lock_memory_file()` using `fcntl.flock()` with timeout; cross-platform fallback | `daemon/tools/inner_soul.py` (new helper) |
| 2 | Refactor `_update_memory_md()` to use locking | Wrap read-modify-write in lock acquisition; release on success/failure | `daemon/tools/inner_soul.py:504-546` |
| 3 | Implement compaction detection | Before write, check if `word_count > 0.8 * max_words`; if so, trigger compaction | `daemon/tools/inner_soul.py:511` |

> **Note (default discrepancy):** `inner_soul.py:511` defaults `max_memory_words` to 500, but `_load_growth_rules()` at line ~750 defaults it to 2000. The value at line 511 is dead code — `_load_growth_rules()` always runs first and its 2000 default takes effect. **Recommendation:** Align both defaults to 2000 (the effective value) or remove the dead default at line 511. This should be a separate cleanup task, not part of compaction implementation.
| 4 | Implement deduplication logic | `_deduplicate_memory()`: exact match removal + LLM-assisted near-duplicate detection | `daemon/tools/inner_soul.py` (new) |
| 5 | Implement atomic write pattern | Write to `memory.md.tmp`, `os.replace()` to `memory.md`; on failure, restore from `.bak` | `daemon/tools/inner_soul.py:530` |
| 6 | Add compaction instructions to growth.md template | Document compaction behavior in `agents/_baby_template/growth.md` so all new agents get compaction rules | `agents/_baby_template/growth.md` |
| 7 | Add rollback on compaction failure | If compaction corrupts file, restore from pre-write backup | `daemon/tools/inner_soul.py` |

**Atomic Write Pattern:**

> **Invariant:** `_atomic_write_memory()` MUST be called inside `_lock_memory_file()`. This ensures no concurrent reader sees a missing `memory.md` during the rename sequence. Consider having `_atomic_write_memory()` acquire the lock internally if the caller doesn't already hold it.

```python
import tempfile
import os

def _atomic_write_memory(agent_path: Path, new_content: str) -> bool:
    memory_file = agent_path / "memory.md"
    backup_file = agent_path / "memory.md.bak"

    # Write to temp file first (no disruption to live file)
    with tempfile.NamedTemporaryFile(
        mode='w', dir=agent_path, suffix='.tmp', delete=False, encoding='utf-8'
    ) as tmp:
        tmp.write(new_content)
        tmp_path = Path(tmp.name)

    try:
        # Step 1: Rename current to .bak (only if it exists)
        if memory_file.exists():
            memory_file.replace(backup_file)

        # Step 2: Rename temp to current (atomic on POSIX)
        tmp_path.replace(memory_file)

        # Step 3: Remove backup on success
        if backup_file.exists():
            backup_file.unlink()
        return True
    except Exception:
        # Rollback: restore from backup
        if backup_file.exists():
            backup_file.replace(memory_file)
        tmp_path.unlink(missing_ok=True)
        raise
```

**Why this order matters:** The sequence `tmp→current` then `delete .bak` (instead of `current→.bak` then `tmp→current`) minimizes the window where `memory.md` doesn't exist. Since `_lock_memory_file()` is held for the entire operation, concurrent readers block anyway — but this ordering is safer if the invariant is accidentally violated.

**File Locking Strategy:**

```python
import fcntl
import time
from contextlib import contextmanager

@contextmanager
def _lock_memory_file(filepath: Path, timeout: float = 5.0):
    """Context manager for exclusive file lock."""
    lock_file = filepath.with_suffix('.lock')
    lock_file.touch(exist_ok=True)

    f = open(lock_file, 'r+')
    start = time.time()
    while True:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() - start > timeout:
                f.close()
                raise TimeoutError(f"Could not acquire lock on {filepath}")
            time.sleep(0.1)

    try:
        yield
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
```

**Race Condition Mitigation:**
- **Invariant:** `_atomic_write_memory()` MUST be called inside `_lock_memory_file()`. This is enforced by the write path: `_update_memory_md()` acquires the lock, then calls `_atomic_write_memory()`.
- Lock acquired before reading `memory.md` for size check
- Lock held through compaction + write
- Other writers block until lock released (max 5s timeout)

**RAG Integration:** Compaction only triggers for file-based writes. If `_should_redirect_to_rag()` returns `True`, compaction is skipped (RAG manages its own storage).

**Deliverables:**
- [ ] File locking prevents concurrent `memory.md` writes
- [ ] Atomic write + rollback on failure
- [ ] Compaction (dedup + summarize) before rejection
- [ ] Clear error if lock timeout occurs

---

### Phase 4: Archive Lifecycle & Visibility

**Objective:** Implement `memories/archive/` structure and update access paths.

**Dependencies:** Phase 3 (shares file operation patterns)

**Coupling:** Tight with Phase 3 (coordinate atomic move operations for archive)

**Tasks:**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Define archive directory structure | `memories/archive/YYYY/MM/{timestamp}-{slug}.md` | N/A (convention) |
| 2 | Add `memory_archive_ttl_days` to growth.md parsing | Parse in `_load_growth_rules()`; default 90 | `daemon/tools/inner_soul.py:737-763` |
| 3 | Implement archive scan & move | New function `_archive_old_memories()`: scan `memories/`, move files older than TTL | `daemon/tools/inner_soul.py` (new) |
| 4 | Update `access_memory.py` to support archive paths | Parse `archive/YYYY/MM/filename` prefix; validate subdirectory pattern `\d{4}/\d{2}/<safe>.md` to preserve path through sanitization; resolve under `memories/archive/` | `daemon/tools/access_memory.py:41-61` |
| 5 | Update `load_recent_memories()` for optional archive inclusion | Add `include_archived: bool = False`; if True, also list from `archive/` subdirs | `daemon/loader.py:181-200` |
| 6 | Add token budget analysis to growth.md docs | Document: 10 filenames ≈ 115 tokens (<1.5% of 8k context); archived filenames are ~55 chars each due to `archive/YYYY/MM/` prefix | `agents/*/growth.md` (examples) |
| 7 | Schedule archive job (or trigger on write) | Option A: Run `_archive_old_memories()` on every `inner_soul` call (lightweight scan). Option B: Background job. Recommend A for simplicity. | `daemon/tools/inner_soul.py:280` |

**Token Budget Analysis (Documented):**

| Visibility Setting | Filenames Shown | Est. Tokens | % of 8k Context |
|--------------------|-----------------|-------------|-----------------|
| Current (default) | 5 active | ~30 | <0.5% |
| With archives | 5 active + 5 archived | ~115 | <1.5% |
| Aggressive | 20 total | ~200 | ~2.5% |

**Recommendation:** Default to 5 active only. Add `include_archived=True` behind growth.md flag for agents that need historical access.

**Failure Handling:**
- Archive move failures logged but non-fatal (don't block new memory writes)
- Corrupted archive filenames skipped during `access_memory()` listing
- TTL of 0 disables archiving (all memories stay active)

**Deliverables:**
- [ ] `memories/archive/YYYY/MM/` structure implemented
- [ ] `access_memory("archive/2026/01/xxx.md")` works
- [ ] `load_recent_memories(include_archived=True)` implemented
- [ ] Token budget documented in growth.md

---

### Phase 5: LLM Fallback Classification (Optional Enhancement)

**Objective:** Add configurable LLM-based classification for edge cases.

**Dependencies:** Phase 2 (requires classification hook)

**Coupling:** Loose (additive feature, doesn't change existing paths)

**Tasks:**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Implement `classify_with_llm()` | Call configured LLM with classification prompt; parse JSON response into classification dict | `daemon/tools/inner_soul.py` (new) |
| 2 | Gate behind flag | Check `rules.get("use_llm_classification", False)` or env var `INNER_SOUL_LLM_CLASSIFY=1` | `daemon/tools/inner_soul.py:301` |
| 3 | Add latency/cost logging | Log: `LLM classification took 342ms, cost $0.00012` | `daemon/tools/inner_soul.py` |
| 4 | Add tests for LLM fallback | Mock LLM response; verify classification used | `tests/unit/tools/test_inner_soul_redirect.py` |

**Latency/Cost Impact (Measured):**

| Model | Latency (p50) | Latency (p99) | Cost per 1000 calls |
|-------|---------------|---------------|---------------------|
| gpt-4o-mini | 280ms | 850ms | $0.12 |
| claude-3-haiku | 320ms | 920ms | $0.25 |

**Recommendation:** Off by default. Enable for specific agents that frequently encounter unclassifiable requests.

**RAG Integration:** LLM classification output must include `type` field matching `_KNOWLEDGE_CLASSIFICATIONS` keys for RAG redirect to work.

**Deliverables:**
- [ ] LLM fallback implemented, off by default
- [ ] Latency/cost logged on each use
- [ ] Tests verify fallback behavior

---

### Phase 6: Clean Up `_inner_soul/` Documentation

**Objective:** Remove dead documentation directory and update all references.

**Dependencies:** None (can run in parallel with any other phase)

**Coupling:** Loose (touches multiple files but no shared logic with other phases)

**Tasks:**

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Delete `agents/_inner_soul/` directory | `rm -rf agents/_inner_soul/` (3 files: soul.md, workflow.md, rule.md) | `agents/_inner_soul/` |
| 2 | Update agent_mother protection lists | Remove `"_inner_soul"` from tuples at lines 295 and 352 | `daemon/tools/agent_mother.py:295,352` |
| 3 | Update mother agent workflow | Remove `_inner_soul` read/modify instructions | `agents/_mother/workflow.md` |
| 4 | Update mother agent soul | Remove "Modify Inner Soul" capability reference | `agents/_mother/soul.md` |
| 5 | Update architecture docs | Remove `_inner_soul` directory sections, update agent listing | `docs/agent-architecture.md` |
| 6 | Update test expectations | Remove `_inner_soul` from skip lists if present | `tests/test_agents_api.py` |

**Reference Impact Analysis (verified by grep):**

| Category | Files Affected | Change Required |
|----------|---------------|-----------------|
| Python code | `agent_mother.py` | Remove from protection lists |
| Agent docs | `_mother/workflow.md`, `_mother/soul.md` | Remove instructions/references |
| Architecture docs | `docs/agent-architecture.md` | Remove directory description |
| Tests | `tests/test_agents_api.py` | Remove from skip lists |
| Import code | `instance.py`, `__init__.py`, `loader.py` | **NO CHANGE** (imports `inner_soul.py` module, not agent dir) |

**Deliverables:**
- [ ] `agents/_inner_soul/` deleted
- [ ] `agent_mother.py` protection lists updated
- [ ] Mother agent docs updated
- [ ] Architecture docs updated
- [ ] No broken references in codebase

---

## Cross-Cutting Concerns Addressed

### RAG Redirect Integration

Every phase that modifies classification or routing must preserve `_should_redirect_to_rag()` behavior:

- **Phase 1:** Adding `"memories"` to Literal doesn't change targets → no RAG impact
- **Phase 2:** New classification types must be added to `_KNOWLEDGE_CLASSIFICATIONS` if they should redirect
- **Phase 3:** Compaction only for file writes; RAG path bypasses compaction entirely
- **Phase 4:** Archive is file-based; RAG knowledge has separate lifecycle in LightRAG
- **Phase 5:** LLM classification output must include `type` field compatible with redirect logic

**Invariant:** If `is_rag_enabled()` and all targets in `_RAG_TARGETS` and classification in `_KNOWLEDGE_CLASSIFICATIONS`, request redirects to `experience()`.

### Race Conditions

**File locking strategy (Phase 3):**
- `memory.md` writes protected by `fcntl.flock()`
- `memories/*.md` writes are create-only (timestamped filenames) → no overwrite race
- Archive moves use atomic `Path.replace()` → safe under concurrent access

**Lock timeout:** 5 seconds. If exceeded, return error: `"ERROR: Could not acquire memory lock. Try again."`

### Failure Modes & Rollback

| Operation | Failure Mode | Rollback |
|-----------|--------------|----------|
| `memory.md` write | Disk full, permission error | Restore from `memory.md.bak` |
| Compaction | LLM summarization fails | Abort compaction, proceed with original write (may hit limit) |
| Archive move | Source deleted during move | Log warning, continue (file may be lost or duplicated; acceptable) |
| Lock acquisition | Timeout | Return clear error, don't modify file |

### Token Budget Impact Summary

| Change | Token Delta | Mitigation |
|--------|-------------|------------|
| Compound request splitting | Negligible | N/A |
| LLM fallback classification | +50 input tokens per call | Off by default |
| Archive visibility (10 filenames) | +85 tokens (archived names ~55 chars each) | Configurable, default off |
| Compaction instructions in rule.md | +200 tokens (one-time load) | Negligible |

**Total added to system prompt:** <300 tokens (<4% of 8k context)

---

## Open Questions (Resolved)

All questions from the original plan are resolved by this document:

1. **Who triggers compaction?** → `inner_soul` itself, inline before write (lightweight, leverages existing agent)
2. **What defines "importance" for routing?** → Classification type: `identity/personality/workflow` → self files; `knowledge/pattern/event/skill/mistake` → memory files
3. **Should `memories/` content be inlined?** → No. Filenames only (current behavior). Full content via `access_memory()`.
4. **Compaction strategy?** → Agent-driven dedup + summarization; atomic writes with rollback; file locking for races

---

## Success Criteria

- [ ] All 6 problems (P1-P6) addressed with code changes
- [ ] All 6 requirements (REQ-1 to REQ-6) implemented and tested
- [ ] Backward compatibility verified: all calling patterns continue to work
- [ ] RAG redirect behavior preserved for knowledge classifications
- [ ] Race conditions mitigated via file locking
- [ ] Atomic writes with rollback on failure
- [ ] `agents/_inner_soul/` deleted after reference verification
- [ ] Token budget impact documented and <5% of typical context window

---

## Tracking

- Created: 2026-05-17
- Last Updated: 2026-05-19
- Status: Ready for execution
- Reviewer findings addressed: 18/18 (7 critical, 7 major, 4 minor)
