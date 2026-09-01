# Lesson: Verbatim plan-string grep → false-negative "feature absent" findings

**Date:** 2026-08-31
**Gate:** slash-commands /compact e2e gate @ `235650f1` (branch feature/slash-commands)
**Severity:** process lesson (caused a wrong interim finding, corrected before final report)

## What happened

While verifying the truncation-marker requirement (plan WS-4.1: *"ONE id-deterministic marker line `truncation-marker-{uuid4()}` inside `_truncate_fallback`"*), a pack worker grepped for the plan's literal string and reported **"truncation-marker-* not found anywhere in `daemon/` — suspected plan-vs-impl deviation."**

That finding was **wrong**. The marker exists:

- Helper `_append_truncation_marker` — `daemon/compaction.py:105-133`
- Marker minted as `SystemMessage(content="[Earlier messages trimmed to fit context]", id=f"truncation-marker-{uuid.uuid4()}")` (`:128-133`)
- Both call sites route through it: partial path `:1481`, truncate fallback `:1537`; exactly-once pinned by tests (`tests/unit/test_compaction.py:1461/:1485/:1918-1931/:1933/:2084`)

## Root cause

The plan text contains a **formatted-string template** (`truncation-marker-{uuid4()}`), but the code renders it with `uuid.uuid4()` — a verbatim grep for the plan's placeholder spelling (`uuid4()` vs `uuid.uuid4()`, or `{...}` braces) false-negatives. Compounding it, the worker's search was scoped to `daemon/services/` + `daemon/` filename-level patterns and missed `daemon/compaction.py` body matches for the plain prefix.

## Rule of thumb

1. When a plan/spec names a **generated identifier or formatted string**, grep for the **stable prefix** (`truncation-marker-`), never the full template with its placeholder.
2. When a feature commit message says it added the thing (`59951b8f feat(compaction): … unified truncation marker`), let the commit be the pointer: `git show <sha> --stat` then inspect the touched file directly.
3. Treat any "CONFIRMED ABSENT" claim from a single worker as **interim** until a second, independent check (different worker or different method) agrees. In this gate the compaction pack + mock audit both independently confirmed EXISTS, and the deviation was closed before the final report.

## Correction protocol used

- Interim finding flagged in dispatcher notes as "suspected deviation, needs closure"
- Two independent workers tasked to verify (coverage grep + source audit)
- Final report states the corrected fact + why the first grep missed
- KB correction recorded (`experience()`) so the wrong claim doesn't propagate
