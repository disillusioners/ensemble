# Deferred Debt — carry-forward from designer-critic-orchestration

Carry-forward inventory recorded at phase 4 close-out so the next commission starts from a clean backlog. None of these block this branch; each names its own trigger and owner-commission shape.

## (a) cap-math-gap — `_OD_GENERATE_WALL_CLOCK_CAP_S=420` retry ceiling

The wall-clock cap fires only BETWEEN attempts; worst case ~4× the inner per-request timeout. Affects sketcher's bounded regenerate-once path and is a shared failover concern, not critic-specific. Next-commission action: when OD-side retry analysis opens, revisit the cap math (phase 4 hand-off "Future commission B"). Source: stage2-addendum.md deferred (a); architecture-recommendation.md §2 :107 (stays deferred, Q7.4).

## (b) 12 pre-existing test failures — base-proven, unrelated

4 in `tests/unit/test_plugin_subsystem*.py` + 8 agent-file failures; base-proven 2026-10-07 + 2026-10-09. Not introduced by this commission. Next-commission action: own commission "Future commission E" fixes them against the base, not against this branch.

## (c) Sketcher pipeline paraphrase tightening

Review-green #4 from the stage-2 close-out: sketcher's report prose rewrites envelope facts instead of copying them. Next-commission action: cleanup pass on sketcher reporting discipline ("Future commission C"). Source: stage2-addendum deferred (b).

## (d) Trap note — model attribution

Read the spawn-log `model=` line, not `OPENAI_MODEL`, when attributing generation provenance (occ-3 family). Critic records `model` in its verdict block (schema field), so the trap applies to critic unchanged. Next-commission action: none — standing discipline note; carry into any future lane-debug runbook.

## (e) `design.capture_mockup` tool — spec-only, follow-up commission

Promoted to spec in THIS commission (`design-capture-mockup-spec.md`, same directory) per D7/Q7.2; the tool itself is the deliverable of "Future commission A". Until it ships, `[VISUAL-QA-DEFERRED]` is the canonical page-handoff marker (critic schema `screenshot_capture` field + designer Guideline (g)).

## (f) parity-runs.jsonl + v2 tooling end-state ownership undefined post-dual-run

The parity log and its schema-v2 validation tooling outlived the dual-run pilot that created them: the pilot is retired (sketcher sole lane), yet `parity-runs.jsonl` + `test_parity_rows_validate_against_schema` stay in CI with no owner commission for the end-state (maintain as historical record vs archive both file and test). Load-bearing wrinkle: the `assert validated >= 1` at `tests/unit/agents/test_sketcher_agent.py:495` makes the single historical smoke row REQUIRED for CI — archiving or rewriting the jsonl without relaxing that assertion breaks the suite. Next-commission action: decide the end-state owner ("Future commission G") — either freeze the file as an immutable historical record with the assertion documented, or archive file + test together in one commit.

## (g) capture-availability dialect tension — `agents/sketcher/tools_note.md:51`

Sketcher's Capture Procedure states the capture lands unconditionally "as part of my write-through (the capture lands right after my save passes the gate check)" — plan-rooted in the phase-1 T7 migration wording. The phase-3 side of the same plan (designer Guideline (g) + critic schema `screenshot_capture`) treats the capture as possibly ABSENT (`[VISUAL-QA-DEFERRED]` until `design.capture_mockup` ships, deferred-debt (e)). Both readings cannot be true at once: either sketcher always captures (tools_note as written) or capture is a deferred capability the schema must tolerate missing. Next-commission action: upstream wording decision — reword one side to match the other once "Future commission A" (the capture tool) lands; do NOT resolve by silent edit inside this branch.

## (h) plan-fencing gap — non-owned test file slipped the commission's test surface

`tests/unit/plugin_subsystem/test_tier1_wiring.py` was never in the plan's owned-files/test-surface inventory, so its two stale designer-od.* asserts (:215/:337) went red when phase 1 removed designer's od.* allow entries — the plan's hard-escalation clause covered the file in principle, but no worker owned it and no deferred-debt entry recorded it until the final gate adjudication (tester N1/N2; fixed post-ruling in this branch's close-out commit). Next-commission action: when deriving a plan's owned-test inventory, grep repo-wide for the killed architecture's symbol co-occurrences (e.g. `designer` + `od.` in tests/) instead of listing only the files each phase task touches.

## Related out-of-scope threads (recorded, not debt of this branch)

- **120s llm-supervisor-proxy read window** vs 130–170s generation budget — root cause of 524-class escalations; its own commission ("Future commission D", constraint C1). Every 524-driven round-3 escalation under this architecture is an infra escalation, not a design failure.
- **view-views for critic** — deferred per Q6.1 (user policy; does not authorize a 4th commissioned user). "Future commission F" — user-policy decision required first.
