# Compaction OUTPUT-STRUCTURE Final Gate — Lessons (2026-09-01, 7d215e32)

Gate: feature/compaction-output-structure @ 7d215e32 → SHIP, 0 blockers, 3 should-fix.
Full report: RESULTS/2026-09-01-compaction-output-structure-7d215e32.md
Adjudication detail: /tmp/tester-evidence/compaction-output-structure/adjudication/Q1-Q3-verdicts.md

## Root-caused follow-ups (should-fix — small-surface, none land wrong data)

1. **W3 conversation-time provenance is dead code in production.** The clause
   (`| conversation time {t0} → {t1}`) is implemented (daemon/compaction.py:724-725)
   and unit-tested BOTH ways (presence :3511-3544, omission :3546-3575), but
   `build_compaction_doc(msg_timestamps=…)` is called from all 3 production sites
   (compaction.py:2901/:3059/:3205) WITHOUT the argument; `CompactionContext`
   (:1084-1116) has no such field; all 4 constructors (compact_executor.py:1063,
   instance_messaging.py:1163, graph.py:3504, watchover_service.py:1573) omit it.
   **Lesson: a conditional feature whose unit tests stub the map BOTH ways proves
   the branch, not the wiring.** Live-run confirmation was the only way to catch it
   — the section header landed as `### SECTION 1/1 — messages #1–#39` with no time
   clause. Mitigating: omission is the spec's degrade mode (never falsified) — no
   generation timestamps landed either.

2. **Empty instance_id → doc id `compaction-global--1` + latent seq collision.**
   `CompactionContext.instance_id: str = ""` default (compaction.py:1106); nobody
   passes it → id f-string (:908) renders double-dash. Seq parser (:156-165) on the
   empty-iid form parses suffix `-1` → int("-1") → max_seq stays 0 → SECOND
   compaction mints the SAME id. Channel stays consistent (sentinel rewrite removes
   the old doc with the span); FE merge-by-id (message-merge.util.ts:126-133)
   silently replaces the fold-card body. **Lesson: default-empty string fields in
   context dataclasses poison downstream f-string ids — verify at the boundary or
   assert non-empty in the id mint.**

3. **tokens_saved asymmetry: injected messages counted in `after` but not `before`.**
   `_partition_injected_messages` excludes 21 `[SYSTEM CONTEXT]` injections from
   tokens_before (compaction.py:1705-1707, :1732) but `replacement.extend(injected)`
   re-attaches them into tokens_after (:1992, :2105). Reported −9,845 vs real
   +1,292 savings (tiktoken reconstruction). Already parked as N1
   (compact_executor.py:1315-1317). **Lesson: any before/after metric computed at
   different pipeline stages must be checked for set-symmetry of what's counted.**

## Test-quality debt (from mock audit — flag to skill-keeper/reviewer)
- Site-level disjointness asserts (graph.py:3551, compact_executor.py:1625-1629,
  instance_messaging.py:1228-1232) + helper assert (compaction.py:399-401) have ZERO
  test coverage.
- Emergency path has no landed-channel pin (StubGraph bypasses the real reducer).
- No "trailing system-block count" / "exactly N system messages landed" pin.
- e2e canaries (revive_brick) use real graphs but never read back the messages channel.
- `result.replacement_messages`-only assertions pass even if the sentinel is dropped
  (they pin the doc builder, not the landing).

## Live-run operational gotchas (agents-ensemble dev daemon)
- **Empty-assistant placeholders**: every real reply is preceded by an empty
  `role=assistant, content=""` placeholder sharing the run-id prefix. Poll for
  `content != ""`, never for role presence.
- **Command-state polling**: response field is `data.command.phase`, not `data.state`.
- **elapsed_ms in command final state** measures command-creation → last event, not
  accept → terminal (reported 328833ms for a 39s compaction).
- **Compacted span is non-contiguous by design**: protected `[SYSTEM CONTEXT]`
  injections inside the span are relocated to the head group ahead of the doc
  (`[injected…][doc][tail…]`). Do NOT assert strict contiguous prefix/suffix on the
  pre-compact id order — assert per-segment relative order + single-doc + tail-ids
  instead.
- **GET /messages (wire) filters tool messages** that the checkpoint keeps — wire
  and checkpoint views differ by exactly the tool-message set.
- **LLM HA failover** (localhost:4123 → llm.daoduc.org) adds 1-3s per swap; live
  e2e polling budgets must absorb it.
- FE fold-card verification without agent-browser skill works via system Chrome +
  `frontend/node_modules/playwright` with NODE_PATH set.

## Gate-mechanics notes
- Revive-once guard bit us once: a completed worker could not be revived a second
  time ("already been revived once") — spawn fresh workers for 3rd tasks instead.
- The RED-check recipe (GREEN unmodified + hardened-mutation must FAIL + independent
  AST-extracted replay) is the anti-tautology pattern for regression-pin proofs —
  now also captured as a worker skill by the RED worker.
