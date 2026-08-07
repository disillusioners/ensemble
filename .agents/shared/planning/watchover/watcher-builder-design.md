# Architecture Recommendation: Watcher Context Builder

Date: 2026-08-07T13:41:10Z
Architect Instance: architect (standard design, competitive fan-out)
Worker Instances:
  - architect-watcher-dataflow (a767a5a7) — `data-flow-design` skill
  - architect-watcher-structural (3b82992d) — `structural-design` skill
Status: **COMPLETE** — both workers converged; no conflicts
Output Path: `.agents/shared/planning/watchover/watcher-builder-design.md`

---

## Executive Summary

The Watchover feature's context-building step is architecturally wrong: it
uses generic C3 Compaction (message compression) to produce a
`watchover_context`, yielding a conversation summary instead of
security-focused guardrails. This design replaces it with a dedicated
**LLM-based watcher context builder** — a single inline LLM call at
activation time that analyzes the instance's work and produces a structured
markdown guardrail document.

The design addresses four changes:
1. **New builder LLM call** at activation — the "prompt compiler" that
   transforms message history into a security profile.
2. **Context markdown contract** — the exact schema the builder produces and
   the evaluator consumes.
3. **Verdict format evolution** — from terse single-line (`Allowed` /
   `Deny: <reason>`) to first-line-parseable + optional markdown body.
4. **Prompt location & code organization** — a new agent artifact and a new
   service module.

**Key decisions:** the builder prompt lives in `agents/watcher/builder-prompt.md`
(mirrors the existing `soul.md` loading pattern). The builder code lives in a
new module `daemon/services/watcher_context_builder.py` (separate from the
713-line lifecycle service). The freshness refresh (T5.4) stays raw-tail —
NOT routed through the builder LLM. The requirement is a builder INPUT, not
a post-builder splice.

---

## Approach Comparison

| Approach | Complexity | Scalability | Maintainability | Risk | Cost | Recommendation |
|----------|------------|-------------|-----------------|------|------|----------------|
| **A: Data-flow** (builder pipeline design) | Low | High — activation-time only, no per-eval cost | High — pure function, testable | Med — activation latency | Low — one LLM call per activation | ✅ Adopted — defines the pipeline, message selection, and freshness strategy |
| **B: Structural** (prompt/code/verdict design) | Low | High — strategy interface, config-selectable | High — separate module, Protocol | Med — verdict format migration | Low — minimal new code | ✅ Adopted — defines code org, prompt location, and verdict contract |

Both approaches are **complementary, not competing**. Worker A designed the
data pipeline (message selection → LLM → markdown contract → freshness).
Worker B designed the code structure (new module, prompt artifact, verdict
format evolution, requirement handling). They converged on every
overlapping point:

| Decision Point | Worker A (data-flow) | Worker B (structural) | Converged? |
|---|---|---|---|
| Builder LLM call pattern | `asyncio.to_thread` + `wait_for(15s)` (LoopRepairer pattern) | Same — mirrors `_summarize_loop` | ✅ |
| Requirement as input | Yes — pass as input, not post-splice | Yes — first-class input to `compile()` | ✅ |
| Freshness refresh strategy | Keep raw-tail, NOT builder LLM | (not in structural scope) | ✅ |
| Verdict format | Free-form markdown context, evaluator reads as guidance | First-line-parseable + optional markdown body | ✅ |
| Prompt location | (not in data-flow scope) | `agents/watcher/builder-prompt.md` | ✅ |
| Code organization | (not in data-flow scope) | New module `daemon/services/watcher_context_builder.py` | ✅ |
| Fallback on builder failure | raw-tail + static security profile prefix | Compaction strategy as fallback | ✅ (complementary) |

---

## Design: Watcher Context Builder

### 1. Builder Data Flow

```
ACTIVATION (blocking — instance paused, UI frozen; latency acceptable)
  activate_watchover(instance_id, requirement=...)
    → quiesce → pause
    → _build_watchover_context(instance_id, requirement)       ← BUILDER CALL
        → aget_state → messages[]                              ← input
        → select_window(messages, max=40)                       ← message pipeline
        → asyncio.to_thread(llm.invoke, [
              SystemMessage(content=BUILDER_PROMPT),
              HumanMessage(content=JSON{messages, requirement, available_tools})
          ])
          └─ asyncio.wait_for(timeout=15s)
        → on success: markdown guardrail contract               ← OUTPUT
        → on timeout/failure: FALLBACK = raw-tail + static guardrail template
    → enable_watchover(context=markdown_contract)              ← atomic metadata write
    → resume → SSE emit
```

**Transformation points:**

| Location | Input Shape | Output Shape |
|----------|-------------|--------------|
| Message selection (NEW) | Full `messages[]` from checkpoint | Text-serialized window: system msgs (all) + last 40 msgs |
| Builder LLM call (NEW) | `JSON{messages, requirement, available_tools}` | Markdown guardrail contract (schema in §3) |
| Requirement merge (CHANGED) | `requirement: str\|None` | Builder INPUT field (not post-splice) |
| Freshness refresh (UNCHANGED) | current `messages[]` | raw-tail string via `_format_raw_tail` |
| Evaluator consume (UNCHANGED) | `context: str` in JSON payload | Free-form guidance to watcher soul (no parser) |

### 2. Message Selection Pipeline

**Window:** all system messages + last 40 non-system messages.

**Rationale:**
- System messages define the agent's role, tools, and constraints — essential for the builder's "what tools are available" and "what is the agent's task" sections.
- Last 40 messages ≈ 6-8K tokens, well within a builder LLM context budget.
- Reuse `_format_raw_tail(messages, 40)` for serialization — it already strips tool-call blobs to name + short arg-preview, keeping token cost bounded.

**Latency:** Activation is blocking (instance paused, UI frozen), so a 2-5s typical LLM call is acceptable. The 15s timeout is a hard ceiling. Typical LLM calls complete in 2-5s.

**Token budget:** One builder call per activation (not per evaluation). Negligible cost vs. the per-tool-call evaluator calls already in the design.

### 3. Context Markdown Contract

The builder must produce a markdown document with this exact structure:

```markdown
## Agent Activity
[1-2 sentence summary of what the instance is currently doing]

## Available Tools
[Comma-separated tool names the instance has access to]

## Allowed
- [specific permitted actions, e.g. "read files under /src", "run tests"]

## Forbidden
- [specific denied actions, e.g. "delete files outside /tmp", "write to .env"]

## Requirement
[The user-supplied requirement, verbatim, or "(none provided)"]
```

**How the evaluator consumes it:** The current `WatchoverEvaluator.evaluate`
(graph.py:3691) puts `"context": watchover_context` as a plain string field in
its JSON user payload. The watcher soul.md treats this as **free-form
guidance**, not a machine-parsed contract. **No evaluator parser change
needed** — the markdown is consumed as human-readable guidance that the
watcher LLM reasons over. The fixed `## Allowed` / `## Forbidden` headings
improve verdict consistency by giving the watcher structured anchor points,
but this is a quality improvement, not a schema dependency. The contract is
advisory, not machine-parsed.

### 4. Builder LLM Call Design

Follow the `_summarize_loop` pattern exactly (graph.py:1411):

```python
async def _call_builder_llm(
    messages_window: str,
    requirement: str | None,
    available_tools: list[str],
    llm_config: dict,
    builder_prompt: str,
    timeout_seconds: int = 15,
) -> str:
    config = clean_llm_config(llm_config)
    llm = ThinkingChatOpenAI(**config)

    user_payload = json.dumps({
        "message_window": messages_window,
        "requirement": requirement or "(none provided)",
        "available_tools": available_tools,
    }, ensure_ascii=False)

    response = await asyncio.wait_for(
        asyncio.to_thread(
            llm.invoke,
            [
                SystemMessage(content=builder_prompt),
                HumanMessage(content=user_payload),
            ],
        ),
        timeout=timeout_seconds,
    )
    return _extract_text_from_content(response.content)
```

**Fallback on timeout/failure:** raw-tail (via `_format_raw_tail`, last 10
messages) + a **static security profile** prefix — a canned guardrail template
covering common deny categories (system files, credentials, destructive
writes, etc.). This guarantees the watcher always has structured guidance,
even on LLM failure. The existing post-builder requirement splice is kept
ONLY in the fallback path (so the requirement always appears even when the
builder fails).

### 5. Builder Prompt Location

**Recommendation: `agents/watcher/builder-prompt.md`** — a new agent artifact
file.

**Rationale:**
- Mirrors the existing `_load_watcher_soul_prompt()` pattern (graph.py:3412)
  where `soul.md` is loaded and cached at module load as a system prompt string.
- The builder IS an LLM persona (a "security-profile compiler"), distinct from
  the evaluator (`soul.md` = tool-call classifier). Separate file = separate
  persona = the project's one-persona-per-file convention.
- Editable without code change; version-controlled; testable.

**Loading:** Add `_load_watcher_builder_prompt()` — same pattern as
`_load_watcher_soul_prompt()`, with a module-level cache and fallback string.

```python
_WATCHER_BUILDER_PROMPT_CACHE: str | None = None
_WATCHER_BUILDER_PROMPT_PATH = os.path.join(
    os.path.dirname(_WATCHER_SOUL_PROMPT_PATH), "builder-prompt.md"
)

def _load_watcher_builder_prompt() -> str:
    """Load and cache the watcher builder prompt. Mirrors _load_watcher_soul_prompt()."""
    global _WATCHER_BUILDER_PROMPT_CACHE
    if _WATCHER_BUILDER_PROMPT_CACHE is not None:
        return _WATCHER_BUILDER_PROMPT_CACHE
    fallback = "You are a security-profile compiler. ..."
    try:
        with open(_WATCHER_BUILDER_PROMPT_PATH, "r", encoding="utf-8") as f:
            _WATCHER_BUILDER_PROMPT_CACHE = f.read()
    except Exception as exc:
        logger.warning(f"[Watchover] Could not read builder prompt: {exc}")
        _WATCHER_BUILDER_PROMPT_CACHE = fallback
    return _WATCHER_BUILDER_PROMPT_CACHE
```

**Rejected alternatives:**
- **(a) Inline string constant** — the `soul.md` precedent argues against inlining LLM personas. An inline constant is hard to edit and test.
- **(c) New section in soul.md** — conflates two unrelated personas (compiler vs evaluator). The one-persona-per-file convention must be preserved.

### 6. Code Organization

**Recommendation: new module `daemon/services/watcher_context_builder.py`.**

**Rationale:**
- Different dependencies than the lifecycle service (LLM config + messages, not pause/resume cascade).
- The 713-line `WatchoverService` is a lifecycle coordinator; bolting on a transformation violates SRP.
- Mirrors existing symmetry: `WatchoverEvaluator` is a standalone class in `graph.py`; the builder is a standalone class in `services/`.

**Strategy pattern (optional, for rollout):**

```python
# daemon/services/watcher_context_builder.py

class WatchoverContextBuilder(Protocol):
    async def compile(
        self,
        messages: list[BaseMessage],
        requirement: str | None,
        user_context: str | None = None,
    ) -> str: ...

class LLMContextBuilder:
    """Strategy #2 — LLM-driven security-focused compiler."""
    def __init__(self, manager, llm_config, builder_prompt: str, timeout_seconds: int = 15): ...
    async def compile(self, messages, requirement, user_context=None) -> str: ...

class CompactionContextBuilder:
    """Strategy #1 — mechanical fallback (wraps existing _build_watchover_context body)."""
    def __init__(self, manager): ...
    async def compile(self, messages, requirement, user_context=None) -> str: ...
```

`WatchoverService.activate_watchover` reads `meta.json → watchover.context_builder`
to pick the strategy. The evaluator downstream stays oblivious to which
produced the context it consumes.

> **Implementation note:** The Protocol can be kept implicit (duck typing)
> during initial rollout — formalize only when a second builder ships. The
> primary deliverable is the `LLMContextBuilder` class. `CompactionContextBuilder`
> is the fallback path.

### 7. WatchoverService Integration

The `_build_watchover_context` method changes from calling compaction to
calling the builder:

```python
# BEFORE (current):
async def _build_watchover_context(self, instance_id: str) -> str:
    # calls manager._compactor.compact_state(ctx) → summary
    # falls back to _format_raw_tail
    return summary_text

# AFTER (proposed):
async def _build_watchover_context(
    self, instance_id: str, *, requirement: str | None
) -> str:
    # 1. Read messages from checkpoint state
    # 2. Select window (system msgs + last 40)
    # 3. Call LLMContextBuilder.compile(messages, requirement)
    # 4. On timeout/failure: fallback to raw-tail + static guardrail prefix
    return markdown_contract
```

The `activate_watchover` method no longer post-splices the requirement — it
passes it into `_build_watchover_context`:

```python
# BEFORE (current, line 459-471):
context_text = await self._build_watchover_context(instance_id)
if requirement:
    context_text = f"[Requirement] {requirement}\n\n[Recent activity]\n{context_text}"

# AFTER (proposed):
context_text = await self._build_watchover_context(
    instance_id, requirement=requirement
)
# No post-splice — requirement is woven into the builder output
```

**Fallback path (builder failure):** if the builder LLM times out or fails,
the fallback produces `raw-tail + static guardrail prefix`, and the requirement
IS spliced into the fallback output (so it always appears):

```python
# Fallback only:
fallback_context = _format_raw_tail(messages, DEFAULT_RAW_TAIL_MESSAGES)
if requirement:
    fallback_context = (
        f"[Requirement] {requirement}\n\n"
        f"[Recent activity]\n{fallback_context}"
    )
```

### 8. Freshness / Rebuild Integration

**Recommendation: keep raw-tail for T5.4 freshness. Do NOT route through the builder LLM.**

**Rationale:**
1. The freshness path runs inside `watchover_check` which gates every tool
   call — latency budget is sub-second, not LLM-grade.
2. The default `refresh_interval=1` means it would fire every turn — an LLM
   call per turn is unacceptable latency for a tool-gating path.
3. Raw-tail freshness is already adequate (the watcher sees recent activity
   + the stale guardrail). A stale guardrail is safer than a fast-but-generic
   raw-tail.
4. The builder is an **activation-time investment** — pay the LLM cost once,
   reuse the guardrail throughout the session.

**Optional future enhancement:** raise `refresh_interval` to 5-10 and add a
builder-refresh option gated behind a config flag, but default to raw-tail.

### 9. Requirement Incorporation

**Recommendation: pass `requirement` as a builder INPUT, not a post-builder splice.**

**Tradeoff analysis:**

| Approach | Pro | Con |
|----------|-----|-----|
| **Input to builder** | LLM can produce guardrails that *reference* the requirement ("Forbidden: destructive writes — operator requirement: 'read-only'"), producing a cohesive document | Requirement may be lost if builder times out |
| **Post-builder splice** | Requirement always appears verbatim | Creates a hybrid doc the watcher must reconcile |

**Resolution: pass as input + keep fallback splice.**
- If the builder succeeds: the requirement is woven into the guardrails
  semantically (the `## Requirement` section guarantees it appears).
- If the builder times out and falls back to raw-tail: the existing post-splice
  logic preserves the requirement.

Best of both: semantic integration on success, guaranteed presence on failure.

---

## Design: Verdict Format Evolution

### New Verdict Contract

**First line (machine-parseable — unchanged shape):**
- `Allowed` — bare token.
- `Deny: <reason-sentence>` — reason is non-empty.

**After a blank line (optional — human-readable markdown body):**
- Free-form markdown: paragraphs, bullets, fenced code, headings.
- Body is captured verbatim into `WatcherVerdict.body`.
- Capped at 1500 chars (truncated with `…(truncated)` marker) to prevent
  ToolMessage token bloat in the watched agent's context.

### Updated `WatcherVerdict`

```python
@dataclass
class WatcherVerdict:
    verdict: str  # "allow" | "deny"
    reason: str = ""
    body: str | None = None          # NEW — markdown body when present
    error_type: str | None = None    # "infra" | "judgment" | None
    tool_call_id: str = ""
```

### Updated `_parse_verdict`

```python
@staticmethod
def _parse_verdict(raw_text: str) -> WatcherVerdict | None:
    if not raw_text:
        return None
    text = raw_text.strip()
    if not text:
        return None

    # Parse ONLY the first non-empty line for the verdict.
    lines = text.splitlines()
    first_line = ""
    first_line_idx = 0
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped:
            first_line = stripped
            first_line_idx = idx
            break

    if first_line == "Allowed" or first_line.startswith("Allowed "):
        return WatcherVerdict(verdict="allow")

    if first_line.startswith("Deny:"):
        reason = first_line[len("Deny:"):].strip()
        if not reason:
            return None  # judgment error — fail-closed

        # Extract optional markdown body after the first blank line
        # following the verdict line.
        body = _extract_body(lines, first_line_idx)
        if body and len(body) > 1500:
            body = body[:1500] + "\n…(truncated)"
        return WatcherVerdict(verdict="deny", reason=reason, body=body or None)

    return None  # judgment error — fail-closed


def _extract_body(lines: list[str], verdict_line_idx: int) -> str:
    """Extract markdown body after the blank line following the verdict."""
    # Skip the verdict line, then find the first blank line, then
    # collect everything after it.
    for idx in range(verdict_line_idx + 1, len(lines)):
        if not lines[idx].strip():
            body_lines = lines[idx + 1:]
            return "\n".join(body_lines).strip()
    return ""
```

### Bifurcated Failure Handling — PRESERVED

The bifurcated failure handling (AD-6 / LD-2) is the single most important
resilience property of the watcher. The verdict format change **must not
break it**:

- **Judgment errors → fail-closed (Deny + count):** First-line parse still
  hits `None` for unparseable text. The body's presence is irrelevant to
  parse success — empty body is valid. The parser is strict on the first
  line, lenient on the body.
- **Infrastructure errors → fail-open (Allow + degraded SSE, no count):**
  `_INFRA_ERROR_TYPES` catch in `evaluate()` is unchanged. Infra errors are
  detected at the call surface, not from the output text.
- **Body absence is not an error** — `Allowed` stays bare with no body
  expected; `Deny` with no body is valid (the reason on the first line is
  sufficient).

### ToolMessage Injection Update

The denial ToolMessage (graph.py:4270-4297) changes to include the markdown
body when present:

```python
if verdict.verdict == "deny":
    parts = [f"Watchover denied this tool call: {verdict.reason}."]
    if verdict.body:
        parts.append("")  # blank line separator
        parts.append(verdict.body)
    parts.append("Please adjust your approach.")
    content = "\n".join(parts)
else:
    # Allow verdict, but batch was denied by another call.
    content = (
        "Watchover deferred this tool call: another call "
        "in this batch was denied. Please retry."
    )
```

The `additional_kwargs={"watchover_denial": True}` tag stays unchanged
(LoopDetector exclusion depends on it).

---

## Watcher Agent Prompt Updates

### `agents/watcher/soul.md`

**Lines 24-47 ("My Decision Contract")** — replace the strict "no preamble,
no markdown, no explanation" contract with:

> I always return my verdict on the **first line**, in one of two exact forms:
>
> ```
> Allowed
> ```
>
> or
>
> ```
> Deny: <one short sentence reason>
> ```
>
> After a `Deny:` verdict, I may add a **blank line** followed by a short
> markdown body (2-5 lines) that helps the watched agent adjust its approach.
> Bullets, fenced paths, and concise explanations are welcome in the body.
>
> `Allowed` is always a single line with no body.
>
> The first line is the machine-parseable verdict. Everything after the blank
> line is coaching. I do not repeat the reason in the body — the reason on the
> first line is sufficient.

**Lines 140-145 ("My Voice")** — relax "No markdown" to:

> My first line is terse: `Allowed` or `Deny: <reason>`. After a `Deny`, the
> optional markdown body is coaching — keep it short (2-5 lines), use markdown
> when structure helps (bullets for multi-reason concerns, fenced paths for
> file targets). Do not repeat the reason in the body.

### `agents/watcher/rule.md`

**No changes.** Cardinal rules and verb/target taxonomy are orthogonal to
verdict format. The decision matrix (`verb × target → Allow/Deny`) is
unchanged.

### `agents/watcher/workflow.md`

**Lines 22-24 ("Step 1: Read the contract")** — update to:

> Confirm the verdict contract: first line is the machine verdict (`Allowed`
> or `Deny: <reason>`); an optional markdown body after a blank line is
> encouraged on `Deny` when it helps the agent adjust.

**Lines 72-86 ("Step 7: Emit the verdict")** — add a note:

> On `Deny`, consider adding a brief markdown body after a blank line with
> concrete guidance on how to achieve the goal safely (e.g., "Use `--dry-run`
> first" or "Read from `/tmp` instead"). Keep it to 2-5 lines. The reason on
> the first line is mandatory; the body is optional coaching.

### `agents/watcher/meta.json`

Add builder config keys to the `watchover` section:

```json
{
  "watchover": {
    "llm_model": "quick",
    "timeout_seconds": 10,
    "max_denials_per_turn": 3,
    "mirror_message_count": 5,
    "failure_mode": "bifurcated",
    "context_refresh_interval": 1,
    "context_builder": "llm",
    "builder_llm_model": "quick",
    "builder_timeout_seconds": 15,
    "builder_message_window": 40
  }
}
```

| Key | Default | Purpose |
|-----|---------|---------|
| `context_builder` | `"llm"` | Strategy selector: `"llm"` (new builder) or `"compaction"` (fallback) |
| `builder_llm_model` | `"quick"` | Model for the builder call (falls back to `llm_model`) |
| `builder_timeout_seconds` | `15` | Builder LLM call timeout (higher than evaluator's 10 — builder produces more output) |
| `builder_message_window` | `40` | Number of trailing messages to feed the builder |

---

## Integration Points Summary

| Component | Change | Nature |
|-----------|--------|--------|
| `daemon/services/watcher_context_builder.py` | **NEW** module | `LLMContextBuilder` class + optional `CompactionContextBuilder` fallback |
| `daemon/services/watchover_service.py` | **MODIFY** `_build_watchover_context` | Replace compaction call with builder call; accept `requirement` param |
| `daemon/services/watchover_service.py` | **MODIFY** `activate_watchover` | Pass `requirement` to `_build_watchover_context`; remove post-splice |
| `daemon/graph.py` | **MODIFY** `WatcherVerdict` dataclass | Add `body: str \| None` field |
| `daemon/graph.py` | **MODIFY** `_parse_verdict` | Parse optional markdown body after first-line verdict |
| `daemon/graph.py` | **MODIFY** `watchover_check` node | Update ToolMessage injection to include markdown body |
| `daemon/graph.py` | **ADD** `_load_watcher_builder_prompt()` | Module-level cached loader (mirrors `_load_watcher_soul_prompt()`) |
| `agents/watcher/builder-prompt.md` | **NEW** artifact | Builder system prompt (security-profile compiler persona) |
| `agents/watcher/soul.md` | **MODIFY** lines 24-47, 140-145 | Relax verdict contract to allow optional markdown body on Deny |
| `agents/watcher/workflow.md` | **MODIFY** lines 22-24, 72-86 | Update contract reference + add body guidance |
| `agents/watcher/meta.json` | **MODIFY** `watchover` section | Add `context_builder`, `builder_llm_model`, `builder_timeout_seconds`, `builder_message_window` |

---

## Trade-offs

1. **Activation latency (+2-5s typical, +15s worst-case)** — the builder LLM
   call is a NEW blocking step in the activation lifecycle. The instance is
   paused and the UI is frozen during this call. The 15s timeout is a hard
   ceiling; typical calls complete in 2-5s. This is **neutral vs. the current
   compaction path** (which also does an LLM call). The FastAPI route timeout
   for `POST /instances/{id}/watchover` must be ≥ 20s to accommodate the
   builder + lifecycle steps — verify this during implementation.

2. **Builder prompt quality determines guardrail quality** — the watcher's
   verdict accuracy becomes coupled to the builder's ability to produce
   accurate "Allowed/Forbidden" lists. A bad builder prompt yields vague
   guardrails, and the watcher defaults to over-broad Deny (safe but
   high-friction). Iterate the builder prompt against test scenarios before
   merge.

3. **Verdict format migration risk** — changing from strict single-line to
   first-line + optional body increases the chance of the LLM producing
   unexpected first-line formats. Mitigation: the parser is strict on the
   first line (unchanged logic), lenient on the body. Explicit soul.md rule
   "first line is the verdict; everything after the blank line is body."

4. **ToolMessage token budget** — rich markdown bodies inflate the watched
   agent's context window. Mitigation: cap body at 1500 chars with truncation
   marker; log truncation.

5. **Strategy interface over-engineering risk** — if only one builder is ever
   used, the Protocol is unnecessary. Mitigation: keep the Protocol implicit
   (duck typing) during initial rollout; formalize only when a second builder
   ships.

---

## Risks

### 🔴 Critical

**R-1: Activation latency may exceed API route timeout.**
The builder LLM call adds up to 15s to the activation path. The FastAPI route
`POST /instances/{id}/watchover` may have a default timeout that is shorter.
If the route times out before activation completes, the instance is left in a
paused state with no watchover context (the rollback path handles this, but
the user sees a failure).
**Mitigation:** verify the route timeout is ≥ 20s. If not, either raise it
or make the builder call async-fire (activation completes immediately, builder
runs in background and updates the context when done — but this adds
complexity). The simpler path is to raise the route timeout.

### 🟡 Significant

**R-2: Verdict parseability regression.**
Changing the verdict contract from strict single-line to first-line + body
gives the LLM more freedom, which increases the chance of a stray `Deny:`
mid-body or an unexpected first-line format. This triggers the fail-closed
judgment-error path more often, causing spurious denials.
**Mitigation:** the parser parses ONLY the first non-empty line (unchanged
strictness); the body is never parsed for the verdict. Explicit soul.md rule
"first line is the verdict." The body cap (1500 chars) prevents runaway
output.

**R-3: Builder fallback quality.**
If the builder LLM consistently fails (provider outage, bad config), the
fallback (raw-tail + static guardrail prefix) is significantly lower quality
than the LLM-built context. The watcher would see generic guardrails instead
of task-specific ones.
**Mitigation:** the static guardrail prefix covers common deny categories
(system files, credentials, destructive writes). Document this as a degraded
mode. The SSE `watchover_event{status: "degraded"}` pattern can be reused to
signal the fallback to the frontend.

### 🟢 Improvement Opportunities

**R-4: `available_tools` source unverified.**
The builder needs the instance's tool list to populate `## Available Tools`.
The exact API to enumerate an instance's configured tools was not traced
during analysis. Likely `manager._instance_repository.get(instance_id)` →
instance metadata, or a tools-config lookup via the tool registry.
**Action:** confirm during implementation. If the tool list is not easily
available, the builder can omit the `## Available Tools` section (it's
advisory, not mandatory).

**R-5: Test fixture churn.**
224 watcher tests pass on the current contract. Changing the parser and
ToolMessage content requires updating tests that assert on raw verdict strings
or exact ToolMessage content. The volume of churn was not estimated.
**Action:** budget test-update time during implementation planning.

---

## Decisions Pending

| # | Decision | Options | Recommendation |
|---|----------|---------|----------------|
| D-1 | Builder prompt content | The exact text of `builder-prompt.md` | A prompt-engineering task — define the compiler persona, the markdown schema it must produce, and a few-shot example. The schema is defined in this document (§3); the prompt must instruct the LLM to follow it. |
| D-2 | API route timeout | Raise `POST /instances/{id}/watchover` timeout to ≥ 20s, or make builder async-fire | Raise the route timeout (simpler, avoids background-task complexity) |
| D-3 | Strategy Protocol formalization | Implicit (duck typing) vs explicit Protocol | Start implicit; formalize when a second builder ships |

---

## Open Questions

1. **Exact builder prompt content** — this design defines the schema the
   prompt must produce (§3), but the prompt text itself needs a
   prompt-engineering pass with test scenarios.

2. **`available_tools` enumeration API** — confirmed the builder has access
   to `manager` (via `self._manager`), but the exact API to enumerate an
   instance's configured tools needs tracing during implementation. Likely
   `manager._instance_repository.get(instance_id)` → instance metadata, or
   a tools-config lookup.

3. **Static guardrail prefix content** — the fallback's canned guardrail
   template needs to be authored. It should cover the common deny categories
   from `agents/watcher/rule.md` (system files, credentials, destructive
   writes, production surfaces).

4. **`builder-prompt.md` and `soul.md` cross-reference** — the builder prompt
   should include a header note clarifying its role ("this builder is the
   context compiler; soul.md is the tool-call evaluator — distinct roles") to
   prevent future editors from conflating them.

---

## Implementation Phasing (Suggested)

| Phase | Scope | Deliverable |
|-------|-------|-------------|
| **P1** | New module + builder class + prompt artifact | `watcher_context_builder.py`, `builder-prompt.md`, `_load_watcher_builder_prompt()` |
| **P2** | Service integration | Modify `_build_watchover_context` + `activate_watchover`; add fallback |
| **P3** | Verdict format evolution | `WatcherVerdict.body`, updated `_parse_verdict`, ToolMessage injection |
| **P4** | Agent prompt updates | `soul.md`, `workflow.md`, `meta.json` changes |
| **P5** | Tests + API timeout verification | Update 224 tests; verify route timeout ≥ 20s |

Each phase is independently testable. P1-P2 deliver the core feature (builder
replaces compaction). P3-P4 deliver the format evolution (markdown verdicts).
P5 is verification.
