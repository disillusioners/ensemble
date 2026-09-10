# Approach Comparison: `generate_chart` Charter Reuse Mechanisms

Date: 2026-09-10
Author: Architect (controller) — synthesis of 3 competitive fan-out worker reports (skill: `structural-design`, one approach each)
Instances: `ada9f025` (A: revive-seam), `87cd8bbb` (B: persistent charter), `40ef3421` (C: stateless handoff)
Status: ADJUDICATED — feeds `architecture-recommendation.md`

## Question

Which mechanism delivers "successive `generate_chart` calls from the SAME caller reach the SAME charter — small-fix iterative refinement actually works, with minimal new lifecycle machinery"?

## Approaches Compared

- **A — revive-seam chart_tools-local helper** (planner default): discover prior terminal charter child → `manager.enqueue_message` (service-side revive terminal→RUNNING + checkpoint reload, `instance_messaging.py:1931-1966`) → CompletionRegistry wait. Fresh spawn on miss / `fresh=True`.
- **B — persistent long-lived charter**: one charter per caller that never goes terminal; alive (RUNNING) across calls; no revive needed.
- **C — stateless re-dispatch with explicit context handoff**: fresh disposable charter every call (spawn path unchanged); message carries prior mermaid + refinement instruction; no instance reuse.
- **Hybrids**: evaluated post fan-in (see below).

## Five-Axis Comparison

| Axis | A: revive-seam helper | B: persistent charter | C: stateless handoff |
|------|----------------------|----------------------|---------------------|
| **Complexity** | **Low** — ~30 LOC helper + local counter in one file; no schema, no manager methods | **High** — no per-turn completion primitive exists; `CompletionRegistry.complete()` fires only at 4 terminal sites (`child_reports.py:3733,3829,3946`; `error_reporting.py:718`); must invent turn-boundary signal (est. ≥2 days design+test) | **Low** — ~30 LOC stash + message delta; spawn path unchanged |
| **Scalability** | **Med** — one indexed `get_children` per call; checkpoint grows ~linearly (~2 msgs / 0.5–3 KB per refine); compaction fidelity cliff only at ~50+ refines (25–150 KB vs 560k L1 threshold) | **Med-Low** — permanent in-memory graph residency (`manager.py:4378-4441` releases only non-active); permanent `instance_hierarchy` cap slot (row never deletes while RUNNING, `repository.py:251-253`); no reaper | **Med** — N× spawn per refine loop; `_invoke_semaphore` slot per call (cap 3) — exhausted under fan-out at concurrency 3; stash row growth per call |
| **Maintainability** | **High** — mirrors documented patterns (`utils.py:672-740` wait block, `manager.py:773` counter precedent); chart-local blast radius; sister tools untouched | **Med** — novel pattern, zero precedent; fights three deliberate designs (terminal-only completion, active-children defer `child_reports.py:2869-2888`, no reaper) | **High** — cleanest mental model ("always fresh, remember last diagram") |
| **Risk** | **Med** — dominant: wedged charter after true hang (orphaned RUNNING, no reaper; operator escape `fresh=True` + manual `terminate_instance`); latent two-waiter event-coalescing trap if busy guard misordered (fixable by ordering requirement) | **High** — dominant: **cascade-wedge of ancestor chain** (permanently-RUNNING child → caller's every ancestor stuck `WAITING_CHILDREN` → mission tree never finalizes; orphan-ACTIVE sweep explicitly skips healthy-shaped rows, `job_recovery_service.py:958-962`); permanent cap consumption | **Med** — dominant: **silent refinement-quality regression** on multi-round-clarification callers (NEEDS MORE INFO rounds, rejected intermediates, mmdc retry history all lost — invisible, no error, no recovery signal) |
| **Cost** | **Low** — 1 spawn + N revives (enqueue-only); no schema/infra | **Med** — new completion machinery effort + permanent RAM/cap residency; after every hard restart secretly needs A's revive seam anyway | **High** — N× spawn + N× full-context LLM call + N× stash write + semaphore pressure; gap widens with fan-out |

## Deciding Criterion: does small-fix iterative refinement ACTUALLY work?

| Approach | Verdict | Evidence |
|----------|---------|----------|
| **A** | **Yes** | Full checkpointed history reload: literal prior mermaid, charter reasoning, NEEDS-MORE-INFO Q&A, mmdc validation warnings, cumulative style memory. Worker A: "quality signal improves dramatically over fresh spawn (blind to prior diagrams entirely)." |
| **B** | **No — at the protocol layer** | Per-turn completion signal does not exist; `wait_for(instance_id)` can never resolve for a never-terminal instance. The tool call cannot be awaited correctly without new machinery. |
| **C** | **For ~80% of cases; silently degrades on the rest** | Local edits ("make X red") re-derivable from description + prior mermaid; multi-round-clarification history lost → charter re-litigates decisions the caller already made, possibly re-triggers NEEDS MORE INFO loop. Failure is invisible. |

## Recommendation

**A wins decisively** — dominant on Complexity, Cost, and the deciding criterion (refinement quality via checkpoint reuse); Risk (Med) is mitigable and observable (busy-reject is an audible signal, unlike C's silent degradation); the Med Scalability ceiling sits ~50 refines beyond any realistic loop.

- **B: REJECT.** The only approach requiring NEW lifecycle infrastructure. Cascade-wedge is a designed-against bug class in this codebase. B's one pro-grain fact (PAUSED revival, `instance_messaging.py:1939-1943`) morphs B into A anyway.
- **C: REJECT as primary.** Genuine advantage — eliminates the entire wedged/busy/revive-counter failure-mode pile — but trades audible failures for one silent quality regression, at N× cost. C's analysis CONTRIBUTES two load-bearing facts: (1) A's compaction risk is ~zero at realistic sizes (`compaction.py:160-219` — mermaid AIMessages are selectable, but thresholds are far away); (2) the failure modes C eliminates are real and must be documented as A's residual risks.
- **Hybrids: NONE for v1.** A+C (prior-mermaid handoff on reuse calls) is redundant under A — the checkpoint already carries the literal prior mermaid; its only residual value is a refinement-intent signal, which is exactly planner's gated P6=(b) post-merge follow-up. B+anything collapses to A.

## Confidence

**High.** Flipping assumption: if real traffic routinely produced 50+ iteration refine loops (C's bounded-context advantage would re-enter) or per-call `get_children` cost proved material at scale (KV-pointer cache would re-enter — it does not today; see P2 in the recommendation).
