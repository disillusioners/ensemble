# 2026-08-15 — LLM HA Failover v2 Review (Deep-Review council)

**Target:** `feature/llm-failover-v2-sites` @ `c19e2a3d` — facade `daemon/services/llm_failover.py` + 10 secondary LLM sites.
**Verdict:** REQUEST_CHANGES (0🔴 / 5🟡 / 7🟢). Architecture approved by both councilors; four localized fixes gate the merge.

## Lessons

1. **Councilor severity calibration splits are common and adjudicable on evidence, not volume.** The `coding` councilor said APPROVED, `agentic` said REQUEST_CHANGES — identical facts. The governor sided with the councilor whose findings carried *mechanical verification* (`ainvoke` verified inert: sync tenacity returns un-awaited coroutine → zero retry) or *spec citations* (guard normalization was a spec-listed check that failed). Rule of thumb: when verdicts split, weight the finding backed by a reproducible mechanism or an explicit charter/spec quote.

2. **"Inert HA API" is a functional defect, not a doc note.** An exported `ainvoke` that appears to carry failover but performs none — during exactly the outage it exists for — rated 🟡 blocking, above test/doc gaps. Reviewer heuristic for future HA/resilience code: any exported path on the resilience component must be either verified-live or explicitly raise `NotImplementedError`.

3. **Retry-delta adjudication pattern (zero-drift invariant vs bounded-retry delta):** per-site table of "does the retry scope contain writes / is the single-shot side effect after success / idempotency". For this codebase the answer was clean (all sites: writes live in callers after LLM returns). The residual risk concentrated in *latency amplification*, not duplication: bounded retry × 610s `request_timeout` with no outer `wait_for` cap → ~20-min worst case; reactive compaction runs in-turn inside `agent_node`. Check every site lacking a sibling 30–40s `asyncio.wait_for` cap when adding retry layers.

4. **Spec-explicit verification points are findings when they fail.** Embedding guard used raw string `!=` (no trailing-slash/scheme/host-case normalization) — conservative failure direction, but the spec listed normalization as a check → 🟡, not 🟢.

5. **Test-pin taxonomy matters:** "rebind pin" (facade reads `base_url_backup` before cleaning) ≠ "non-mutation pin" (shared config dict unchanged after call). Safe behavior without the second pin is unpinned regression surface.

## Patterns to reuse

- Deep-Review council on `worker` agent + `code-review` skill, ≤4 councilors, works well for cross-cutting service-layer diffs (~2.7k lines incl. tests). Governor surfaced disagreement transparently + adjudicated with rationale — keep requesting that.
- `git diff --stat parent..commit` for orchestration-level scoping is sufficient pre-dispatch; no need for deeper direct inspection (read-only discipline held).
