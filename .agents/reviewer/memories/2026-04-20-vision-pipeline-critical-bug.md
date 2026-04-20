# Vision Pipeline Review — Critical Bug Pattern

## Date: 2026-04-20
## Review: Backend Vision Pipeline (commit 8ec692c)

## Key Lesson
When implementing dual-LLM architecture (vision + standard), the `bind_tools()` call must be applied to BOTH LLM instances regardless of which branch is taken. The pattern:

```python
# WRONG (current bug)
if llm_with_tools is None:
    llm_with_tools = llm_standard.bind_tools(tools)
    # llm_standard stays UNBOUND!
else:
    llm_standard = llm_standard.bind_tools(tools)

# CORRECT
if llm_with_tools is None:
    llm_with_tools = llm_standard.bind_tools(tools)
llm_standard = llm_standard.bind_tools(tools)  # Always bind
```

## SVG MIME Type Gotcha
Base64 data URI validation must use an allowlist for MIME types, not just check for `image/` prefix. SVG (`image/svg+xml`) can contain JavaScript — XSS vector.

## DRY Pattern for Multimodal Content
When the same content construction block appears 3+ times (retry path, first-call path, pre-emit path), extract to a helper function immediately.
