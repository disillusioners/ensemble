# 2026-09-19 — chat-source live-injection council review + repro-dispatch gotcha

Verdict: APPROVED-WITH-NOTES (0 Critical / 9 Optional). Delta 306468f8..1e04af30 @ feature/chat-source-live-injection. Governor ebf23c79, 2 councilors (agentic + coding), round-0 convergence.

## Reusable lessons

1. **Repro-dispatch gotcha (general, this repo):** default `addopts = "-m 'not integration and not postgres'"` (pyproject.toml:80) means a literal `uv run python -m pytest <files> -q` dispatch SILENTLY DESELECTS `tests/integration/*` files. Council caught it: literal command → `34 passed, 7 deselected`; with `-m "integration or not integration"` → `41 passed`. Any dispatch asking a worker to reproduce integration tests must include the marker override (or explicitly request both runs), else the integration layer silently never runs.

2. **Predicate-order doc-drift class:** when an allowlist predicate short-circuits on empty input BEFORE the known-source check (registry.py:78-80), docstring, code, and unit test can each pin a DIFFERENT invariant — here the test pinned the looser shape. Rule: all three must agree on one stated invariant before merge; the test can codify the bug-shaped branch.

3. **Byte-identity claims need line-level evidence:** one councilor reported "zero diff outside the branch", the other found exactly one whitespace-only line differing (registry.py:915). Coarse claims hide cosmetic deltas; demand per-line diff proof for byte-identity gates.

4. **Council dispatch prompt pattern that worked:** include reference-lineage anchors (web/agent-tool guard sites with line refs), known traps (AsyncMock+getsource vacuousness), the literal repro command PLUS the marker-override alternative, and explicit read-only constraints. Both councilors independently reproduced and converged round 0.
