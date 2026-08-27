# Lesson: Post-patch real-factory calls on contaminated inputs — the singleton-pollution test smell

**Date**: 2026-08-27
**Context**: agent-instance-tools Phase 2 gate (`feature/agent-instance-tools` @ e15be0e2)

## The pattern that bit us (twice-relevant)

A test builds data (here: a `tools` list) INSIDE a mock-patch scope (`_patch_heavy_helpers()` returns `MagicMock()` for 3 factory helpers), then — after the patches are torn down — passes that contaminated data to a REAL factory function (`create_help_tool`). The real function's internal scan (`scan_tools_for_full_docs`) writes `getattr(tool, 'name')` / `getattr(tool, '_tool_category')` — MagicMock on a mock, auto-attr — into a **module-level singleton** (`daemon.tools._tool_registry._tool_metadata`) that the test never restores. A later test in the same process re-bootstraps the registry, inherits the MagicMock keys, and dies on `sorted()` with `TypeError: '<' not supported between 'MagicMock' and 'str'`.

**Why it's sneaky**: isolated runs PASS (the victim only fails after the polluter); the polluter itself PASSES (it asserts on the real tool it cares about); MagicMock auto-attributes mean no AttributeError ever fires. Standard per-file runs and CI shards can hide it depending on order.

## Detection recipe (worked in 12 bisection steps, ~20s)

1. Pack run fails, isolated victim run passes → order-dependent → suspect pollution.
2. Bisect: victim + each suspect FILE → then class → then test. The pair `pytest <fileA> <victim-id>` reproduces cheaply.
3. Confirm the singleton: import the module-level dict before/after the polluter, count non-str keys.
4. NEW-vs-pre-existing: `git show <base>:<polluter-file> | grep <test-name>` — if absent at base, the branch owns it.

## Fix shape (1 line, test-only)

Filter mocks out of contaminated collections before any post-teardown REAL call:
`tools = [t for t in tools if not isinstance(t, MagicMock)]`
Do NOT add isinstance guards to the production scan — production never receives mocks; the singleton is rebuilt at registry boot in real processes (no pollution cycle exists there).

## Generalization for this repo

Any test that (a) uses a broad patch-scope helper returning MagicMocks AND (b) hands the results to a real function that caches into module state (`_tool_metadata`, `_registry`, doc caches) is a pollution candidate. Audit rule for future suites: after `with patch(...)` blocks, never pass patched-scope artifacts into real factories. Related Phase-2 audit finding: MagicMock auto-attr also silently absorbs `aget_state` runtime regressions — static source-grep guards are the only reliable pin for "must never call X" contracts.

## Token-delta measurement nuance (from the same gate)

"~80% reduction" claims must state their baseline. Measured with tiktoken: summary-vs-RAW = 73-90% reduction (claim holds), but summary-vs-FULL = **negative** (−12% to −41%) because per-line metadata (timestamp + tools=) costs ~6 tokens while the content cap only drops 200→80 chars. When a tool offers a "cheap mode," measure BOTH framings before promising savings to agents.
