# Context Pointer — od-generate-agent-lane Architecture Prior Art (D7 = Option C)

**Purpose.** This commission (designer-critic-orchestration) is the next step on the orchestrator analysis that the od-generate-agent-lane commission started: sketcher = the leaf generation lane, critic = the design-QA gate, designer = the orchestrator that dispatches both. The full investigation that shaped the sketcher decision lives in TWO UNTRACKED docs on the MAIN CHECKOUT ONLY — deliberately NOT copied into this worktree (worktree-only-writes; the pointer replaces a copy).

**Prior-art location (read there, do not copy here):**

- `/home/nea/ensemble-src/.agents/shared/planning/od-generate-agent-lane/architecture-recommendation.md`
- `/home/nea/ensemble-src/.agents/shared/planning/od-generate-agent-lane/approach-comparison.md`

## Axis: A-orchestrate (dedicated agent pipelining the od.* tools)

The main-checkout recommendation's chosen shape: the generation LLM call stays inside `od.generate` (wired onto the ensemble LLM lane first), and a dedicated child agent pipelines the od.* tools — in-loop lint → regenerate, per-page dispatch, checkpointed pipeline. This commission's sketcher lane is exactly that shape, shipped v0.18.5; phase 3 of this commission adds critic as the second orchestration child.

## Axis: A-direct (agent's own turn generates)

Rejected in the prior art: the agent lane passes no per-call output ceiling and wraps OD-tuned prompts in agent framing — runaway/compaction-mid-HTML hazard plus prompt-envelope drift. This commission inherits that verdict unchanged: generation stays tool-internal to sketcher.

## Axis: B (on-lane tool wiring)

The prerequisite stage: `invoke_raw_with_failover` + bounded retry inside the od.generate adapter. Shipped before sketcher; this commission consumes it as-is (the ≥400s sync floor and 130–170s latency records descend from it).

## Axis: C (status-quo single-shot raw SDK)

The baseline the investigation retired: no retry ladder, no classification, no telemetry. Recorded here so the lineage is traceable; nothing of C survives.

## Axis: D-shim / C-resource-ize (wrapper alternatives)

The prior art also weighed host-shim adapter and resource-ize-only wrapper shapes for the OD integration; both were set aside in favor of the plugin-subsystem Port family. This commission does not reopen that question — the three-tier plugin direction (native core / universal wrapper / plugins) governs.

**Deliberate disconnect.** This pointer summarizes; it does not replace. Any read that needs the full evidence tables (V1–V10 verified facts, the five-axis comparison table) follows the paths above on the main checkout. The binding adjudication for THIS commission is `architecture-recommendation.md` in this directory (D1–D7); the main-checkout docs are prior-art context only.
