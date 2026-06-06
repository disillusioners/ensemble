# Code Review: Compaction `model_vision` Strip Fix

**Date:** 2026-06-06
**Reviewer:** Kilo (review pass on commit `4630b6f`)
**Commit:** `fix: strip model_vision from compaction summarization LLM call`
**Author:** Kha <khanguyenmail@gmail.com>
**Branch:** `fix/compaction-model-vision`
**Status:** ⚠️ **Fix is functionally correct, but it is a tactical band-aid on a real architectural smell. A follow-up is recommended.**

---

## 1. TL;DR

| Aspect | Verdict |
|--------|---------|
| Fixes the reported crash? | ✅ Yes |
| Consistent with sibling fixes (`title_generation.py`, `child_reports.py`)? | ✅ Yes |
| Correctly handles the **vision-active conversation** case? | ✅ Yes (but for a subtle reason — see §4) |
| Architecturally clean? | ❌ No — defensive filter masks a real design issue |
| Test coverage adequate? | ⚠️ Partial — image-content path is **not** covered |
| Should we merge as-is? | ✅ Yes (stops the bleed) |
| Should we stop here? | ❌ No — follow-up needed (see §6) |

---

## 2. The Symptom (What the User Saw)

```
19:52:24 - daemon.compaction - INFO - Compaction triggered: 144798 tokens (threshold: 144000)
daemon/compaction.py:724: UserWarning: WARNING! model_vision is not default parameter.
                model_vision was transferred to model_kwargs.
                Please confirm that model_vision is what you intended.
19:52:24 - daemon.compaction - WARNING - Summarization failed, falling back to truncation:
                Completions.create() got an unexpected keyword argument 'model_vision'
19:52:24 - daemon.services.instance_messaging - INFO - [Compaction] ... compaction_type=truncation
                messages_before=244 messages_after=244 tokens_before=144798 tokens_after=12256
                tokens_saved=132542 WARNING: summarization_error=...
```

Root cause: `model_vision` is smuggled into `llm_config` (a dict that's blindly splatted into `ThinkingChatOpenAI(**llm_config)`). `ThinkingChatOpenAI` accepts unknown kwargs into `model_kwargs`, which LangChain then forwards to the OpenAI client, which rejects them with `unexpected keyword argument 'model_vision'`. The summarization call falls back to truncation, losing ~132k tokens of potential savings.

---

## 3. What the Commit Does

```diff
--- a/daemon/compaction.py
+++ b/daemon/compaction.py
@@ -886,7 +886,10 @@ class ContextCompactor:
             }
         else:
             llm_config = self.llm_config_with_headers
-        
+
+        # Strip model_vision — compaction summarization is text-only, vision model is irrelevant
+        llm_config = {k: v for k, v in llm_config.items() if k != "model_vision"}
+
         llm = ThinkingChatOpenAI(**llm_config)
```

Plus two unit tests asserting that `model_vision` is not in the kwargs passed to the constructor, in both the default and `summarization_model` override paths.

The pattern matches what's already done in:

| File | Line | Purpose |
|------|------|---------|
| `daemon/graph.py` | 572 | `vision_config` for `llm_with_tools` |
| `daemon/graph.py` | 580 | `standard_config` for `llm_standard` |
| `daemon/services/title_generation.py` | 77 | Title generation |
| `daemon/services/child_reports.py` | 191 | Child report summarization |

So the patch is consistent. **Good.**

---

## 4. The Real Concern: "What about messages with images?"

This is the part that needs the most careful walk-through. The user's concern is: *"if the conversation was on the vision model, doesn't stripping `model_vision` break the flow?"*

### 4.1 Trace of the vision-active path

1. **User sends a message with an image.** `instance_messaging.py:49-54` and `manager.py:94-109` build a multimodal content list:
   ```python
   [{"type": "text", "text": "What is this?"},
    {"type": "image_url", "image_url": {"url": "<data-uri>"}}]
   ```

2. **`agent_node` detects images and routes to the vision model.** `graph.py:447-464`:
   ```python
   has_images = any block of type "image_url" in messages
   use_vision_model = has_images and model_vision and llm_standard is not None
   current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)
   ```
   The vision model (e.g., `gpt-4o`) is used for this turn.

3. **Compaction is later triggered** (threshold or reactive). `compact_state()` runs.

4. **The compactable groups are formatted for summarization.** `compaction.py:748-770` in `_summarize_single_batch`:
   ```python
   for msg in batch_groups...:
       content = _extract_text_from_content(msg.content)  # ← STRIPS images
       conversation_parts.append(f"User: {content}")
       ...
   ```
   The prompt fed to the summarization LLM is **text-only**. `image_url` blocks are silently dropped (`_extract_text_from_content`, lines 60-68).

5. **`_call_summarization_llm` invokes the LLM.** With the fix, it uses the **standard (non-vision) model** because:
   - `model_vision` is filtered out of kwargs
   - `model` stays as the standard model (e.g., `gpt-3.5-turbo` or whatever the user configured)
   - The vision model is **never** selected for summarization in this code path

6. **The summary comes back as plain text.** It contains the user's *text* ("What is this?"), the assistant's *textual response* ("It's a cat."), tool calls, etc. — but **no information about what the image actually showed** beyond what the assistant said about it.

### 4.2 Verdict on the fix

✅ **The fix does NOT break the vision-active case.** Here's why that's safe:

- The summarization LLM is never given an image. The prompt is text-only by design (built via `_extract_text_from_content`).
- Therefore, it doesn't matter whether we route this call to a vision-capable model — there's nothing for the vision model to "see".
- Using the cheaper/faster standard model is appropriate.

⚠️ **But there is a real semantic regression that the fix does NOT address** (and did not introduce — it predates this commit):

- **All image content is lost from summaries.** If the user uploaded 3 screenshots and the assistant analyzed them in detail, the summary will say "User sent 3 images" is **also** lost — the prompt only contains the user's text and the assistant's text. The fact that images existed is not represented at all in the summary that goes back into the context.
- For long sessions where most of the rich content was visual, this means compaction is a much bigger information loss than the user might expect.

This is a **pre-existing issue**, not something the fix made worse. The fix is fine. But we should call it out as a follow-up.

---

## 5. Tests: What's Good, What's Missing

### 5.1 What the commit added (good)

- `TestSummarizationLLMStripsModelVision` — two tests that inspect `mock_cls.call_args.kwargs` and assert `model_vision` is absent. These are **fail-before / pass-after verified** by the tester's results report at `.agents/tester/RESULTS/2026-06-06-compaction-model-vision-fix.md`.
- The `summarization_model` override test catches a real future regression: if someone moves the strip line to *before* the override merge, both tests would pass but the override path would regress. Good defensive coverage.

### 5.2 What's missing (minor but real)

| Gap | Why it matters |
|-----|----------------|
| No test that `_summarize_single_batch` correctly handles a `HumanMessage` whose `content` is a **list** containing `image_url` blocks | This is the exact path the user is worried about. The current strip-vision fix does **not** exercise this. If `_extract_text_from_content` were ever broken (e.g., a refactor returns `[None]` for image-only messages), the bug would slip through. |
| No test that the produced prompt contains a hint that images existed | This is a feature gap, not a bug — but documenting the **current** behavior in a test prevents future "improvements" from regressing it accidentally. |
| No test for the case where the **only** content in a message is an image (no accompanying text) | `_extract_text_from_content` would return `""` for such a message. The formatted prompt would say `User: ` (empty). Worth covering. |

Suggested additions to `tests/unit/test_compaction.py`:

```python
class TestSummarizationHandlesImageContent:
    """Verify image-containing messages are handled gracefully in summarization."""

    def test_human_message_with_image_is_stripped_to_text(self):
        msg = HumanMessage(
            content=[
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            ],
            id="h1",
        )
        assert _extract_text_from_content(msg.content) == "What is this?"

    def test_image_only_message_extracts_to_empty_string(self):
        msg = HumanMessage(
            content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}],
            id="h1",
        )
        assert _extract_text_from_content(msg.content) == ""

    def test_summarize_batch_formats_image_message_as_text_only(self):
        # Use a real _summarize_single_batch with a mocked LLM that
        # captures the prompt, then assert no image_url strings leak through.
        ...
```

---

## 6. The Real Issue: `model_vision` Should Not Be In `llm_config`

This is the **architectural** concern. The commit message says "Mirrors the filtering pattern used in title_generation.py, child_reports.py, and instance_messaging.py" — but mirroring a pattern is only praise if the pattern itself is right.

### 6.1 The smell

`model_vision` is **configuration metadata** about which model to use, not a parameter of the LLM call. It shouldn't be living inside a dict that's splatted into `ThinkingChatOpenAI(**llm_config)`. Yet today, **every** call site that builds an LLM has to remember to strip it:

| File | Strip site |
|------|-----------|
| `daemon/compaction.py:891` | New (this commit) |
| `daemon/graph.py:572, 580` | Pre-existing |
| `daemon/services/title_generation.py:77` | Pre-existing |
| `daemon/services/child_reports.py:191` | Pre-existing |

And four call sites stuff it in:

| File | Stuff-in site |
|------|---------------|
| `daemon/manager.py:466` | `model_vision` added to `llm_config` |
| `daemon/manager.py:1923` | Same |
| `daemon/services/instance_lifecycle.py:175` | Same |
| `daemon/services/instance_messaging.py:314` | Same |

The `LangChain UserWarning` we saw at `daemon/compaction.py:724` ("model_vision is not default parameter. model_vision was transferred to model_kwargs.") is the system **literally telling us** that we're using the wrong abstraction. The library is being polite about it; the OpenAI server is not.

### 6.2 Recommended follow-up

Refactor `model_vision` to be a sibling of `model` in the config, not a child of `llm_config`:

```python
# Today
llm_config = {
    "base_url": ..., "api_key": ..., "model": "gpt-4o",
    "model_vision": "gpt-4o-vision",  # ← looks like an LLM kwarg; isn't
    ...
}

# Proposed
@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    model_vision: str | None = None   # ← sibling
    temperature: float = 0.7
    ...

# Then in the call site
llm = ThinkingChatOpenAI(
    base_url=config.base_url,
    api_key=config.api_key,
    model=config.model_vision if use_vision else config.model,
    temperature=config.temperature,
    ...
)
```

This **eliminates** the need for the strip in all five places, removes the LangChain `UserWarning`, removes the `unexpected keyword argument` runtime error, and makes the vision-switching logic at `graph.py:447-464` self-documenting.

This is a bigger change than a hotfix though — it touches the LLM config schema and four call sites that build it. The current commit is the right **immediate** fix; the refactor is the right **structural** fix. Both are valuable.

### 6.3 Smaller alternative (lower risk)

If a full refactor is too risky right now, at minimum:

1. **Move the strip to one place.** A `clean_llm_config(cfg: dict) -> dict` helper in `daemon/graph.py` (next to `ThinkingChatOpenAI`) that all five sites call. Single point of truth.
2. **Add a unit test that asserts `clean_llm_config` strips `model_vision` and any other future non-kwarg fields** (a schema-validation test).

---

## 7. Other Observations

### 7.1 Trailing whitespace in the diff

The old code had `        ` (8 spaces) ending the `else` branch; the new code has nothing there. Minor, but the diff includes this purely cosmetic change. Not a problem, but worth knowing if the codebase is strict about diff noise.

### 7.2 The summarization_model override is tested

`test_summarize_strips_model_vision_with_summarization_model_override` correctly verifies that the `model` field in the kwargs reflects the override. This is non-obvious because the override and the strip are in adjacent lines — a future refactor that moves the strip to before the override would not break the basic test but would break this one. Good.

### 7.3 Defensive duplication

The fix is defensive: it strips on every call to `_call_summarization_llm`. This is the **right** thing to do here because the LLM config comes from outside the compactor (the manager builds it). But it's also a symptom that the data model is wrong — see §6.

---

## 8. Recommendation

**Approve and merge** — the fix is correct, minimal, tested, and consistent with established patterns. The summarization path will start working for vision-configured deployments immediately.

**Open a follow-up issue** to:
1. Refactor `model_vision` out of `llm_config` (the architectural fix). Tracked separately because it touches schema and 4+ call sites.
2. Add tests for the image-content path in summarization (§5.2).
3. Decide: should the summary note the **existence** of images, even if it can't describe them? If yes, that's a small enhancement to `_summarize_single_batch`. If no, document explicitly that image content is lost on compaction (the user should know).

---

## 9. Evidence

- Commit: `4630b6f` — `fix: strip model_vision from compaction summarization LLM call`
- Tester results: `.agents/tester/RESULTS/2026-06-06-compaction-model-vision-fix.md` (all green, 193/193 compaction-pack)
- Pattern sources: `daemon/graph.py:572,580`, `daemon/services/title_generation.py:77`, `daemon/services/child_reports.py:191`
- Image-content handling: `daemon/compaction.py:46-70` (`_extract_text_from_content`), `daemon/compaction.py:748-770` (`_summarize_single_batch`)
- Vision routing: `daemon/graph.py:447-464`
