# Architecture Recommendation: od.generate Execution Lane — Dedicated Agent vs On-Lane Tool

**Feature:** od-generate-agent-lane | **Date:** 2026-10-09T10:17Z
**Investigation instances:** `037b060e` (Approach A worker), `9e19f3e0` (Approach B worker), both `structural-design`
**Status:** Complete — investigation + recommendation, NO code changes. Prepared for user discussion.
**Siblings:** `approach-comparison.md` (five-axis table + variant map)

---

## 1. Question & Context

The user proposes retiring the "island" execution of `od.generate` (single-shot raw `openai.OpenAI` call inside the tool adapter, `daemon/plugin_subsystem/opendesign/generate.py:533-575,649-660`) in favor of running design generation through a **dedicated ensemble agent**, gaining real LLM-lane execution, checkpointing, telemetry, parallel generation, retry-on-truncation loops, and job-queue machinery. This matches the already-ratified plugin execution model (resources via skills, execution on the ensemble core, 2026-10-06). This document evaluates that proposal against two alternatives and recommends a staged path.

## 2. What Was Verified (file:line evidence in worker reports)

| # | Fact | Evidence |
|---|---|---|
| V1 | **Per-agent model pin works on the LLM lane** (the flagged critical unknown — RESOLVED ✔) | `meta.json llm_model` → `daemon/registry.py:636-668` discovery → `model_standard` at `daemon/graph.py:12759` → per-instance client construction. `agents/designer/meta.json` already pins `llm_model: "vision"` in production — the precedent is live. A dedicated child can pin vision while designer keeps its model; new agent needs `skill_injection: true` (designer currently `false`). |
| V2 | **The agent lane passes NO `max_tokens`** — no output ceiling, no knob | `graph.py:9083/9141` (`llm.invoke` without max_tokens); tenacity wiring at `graph.py:10153-10284` carries only `stop_after_attempt`, no wall-clock cap on the main lane. Reactive compaction is the only backstop, and a single 64K-token assistant message can blow the trigger window before compaction runs (`compaction.py:9246-9255`). |
| V3 | **`invoke_agent_and_wait` defaults to 300s** — tight against 130-170s generations | `daemon/utils.py:630`; worker-pool semaphore caps concurrency at 4 (`utils.py:591`, `WORKER_POOL_SIZE-1`); `max_children_per_instance=50` (`instance_lifecycle.py:2080-2088`, `config.py:562`). |
| V4 | **ToolNode runs tool calls sequentially** — one designer turn cannot issue N parallel `od.generate` calls | No parallel-execution config at the construction site (`daemon/services/long_tool_nudge.py:581-597`); traced at construction, not through langgraph internals (high confidence, not exhaustive). |
| V5 | **The on-lane tool wiring is S-effort with shipped precedents** | `invoke_raw_with_failover` (`llm_failover.py:700-859`) + module-level `_do_chat_call` factory pattern (`skill_embedding_service.py:117-160`); `wall_clock_cap_s` is a per-call kwarg ( precedent `keyword_extraction.py:387`), recommend 420s; existing inner per-request timeout (`generate.py:649`, `max(120, mt/370)`) coexists cleanly. |
| V6 | **Failover is inert in this deployment today** | No `OPENAI_BASE_URL_BACKUP` configured → `FailoverController.is_configured=False` (`graph.py:10238`). Retry = same primary, on both lanes, until a backup endpoint is configured. |
| V7 | **Raw-SDK facade path does not type context-overflow** | `_run_with_classification` is not wired on the raw path (`llm_failover.py:241-260`); overflow falls through as transient until cap. Agent lane types it properly (`graph.py:987-1009`). 🟡 v2 fix, low practical impact at 64K ≪ vision window. |
| V8 | **Prompt fidelity is preservable only if skills carry references, not text** | Correct shape: skill carries `vendored_references` paths (`daemon/plugin_subsystem/plugin_skill.py:34-37`); the child calls `od.compose_brief`, which evaluates the TS templates in code (`ts_prompt_eval.py`) — evaluator + drift-alarm provenance stay intact. Hand-copied prompt text in a skill body breaks the snapshot-with-drift-alarm contract. |
| V9 | **The OD resource-skill layer is mostly un-built** | Only `plugins/opendesign/skills/opendesign.list_systems.yaml` is vendored today — a generation-oriented skill over the vendored prompts/design-systems must be authored for Approach A. |
| V10 | **Checkpoint loss under B is a non-issue** | Tool result checkpoints atomically with the agent's tool-message at node boundary (`graph.py:12360-12364`). Mid-call cooperative cancellation is impossible under *both* approaches today (httpx transport won't see CancelledError mid-request); per-request `timeout=` remains the only mid-call bound. |

## 3. The Pivatial Decision: Where Does the Generation Call Live?

The user's phrase "run through an actual ensemble agent" admits two very different shapes, and the evidence separates them sharply:

- **A-orchestrate** — the agent *pipelines the od.\* tools*; the generation LLM call stays inside `od.generate` (which B wires onto the lane). Agent adds: in-loop lint → regenerate-with-adjustment, parallel children, model-pinned/skill-primed role, checkpointed pipeline, watch/notify. Viable **provided B lands first** (V5). The child can run on the standard model — the tool is model-self-contained via `OPENAI_MODEL_VISION` (post-incident fix 806ba42c9) — so the vision pin is optional here.
- **A-direct** — the agent's *own turn* generates the HTML on the pinned vision model; the call fully rides the LLM lane. Blocked by V2 (🔴 no `max_tokens` knob → runaway/compaction-mid-HTML), plus OD-prompt envelope drift (prompts tuned for direct single-shot calls get wrapped in system prompt + framing), plus permanent 64K-in-context bloat. **Not recommended until a per-call `max_tokens` seam exists and the trade is explicitly accepted.**

## 4. Recommendation: ADAPT (staged)

**Stage 1 — do regardless: B, on-lane tool wiring (S effort).**
Replace the bare client in `generate.py` with `invoke_raw_with_failover` + module-level factory (never a closure — stale-URL failure mode, `llm_failover.py:124-128`), RAW config dict (pre-cleaning strips `base_url_backup` and silently kills failover), `asyncio.to_thread` wrapper, proxy identity headers (`x-proxy-app`/`x-proxy-interleaved-thinking` — closes a known raw-SDK parity gap), `wall_clock_cap_s=420`, and exactly **1** in-adapter retry-on-truncation (same-prompt; more is diminishing returns atop HA retries). `od.*` API surface, error envelopes, and the designer workflow are unchanged. This is also the de-risking prerequisite for any A shape.

**Stage 2 — user-gated pilot: A-orchestrate dedicated agent.**
New short-named agent: `meta.json` (`skill_injection: true`, `tools.allow` = the four `od.*` ports, `default_queue: system_parallel_queue`, `recursion_limit_multiplier` raised to ~10-12), soul/workflow files encoding compose → generate → lint → save with bounded regenerate-on-truncation, and a **references-not-text** OD skill (V8/V9 — skill authoring is real work, budget it). Designer spawns it for multi-page runs; **direct `od.generate` stays available as fallback (dual-run)**. Pilot gates before any deprecation: latency, tokens/cost per page, truncation rate, HTML marker pass-rate parity.

**Parked — A-direct (agent turn generates HTML).** Requires building a per-call `max_tokens` seam in `build_instance_llms` (none exists, V2) and accepting envelope drift + context bloat. Revisit only on explicit user decision.

**Why staged and not straight-to-A:** every benefit on the user's list is delivered by B (retry ladder, classification-adjacent typing, telemetry, headers, bounded wall-clock) or by B+children/A-orchestrate on top (parallelism, retry-with-adjustment, checkpoints) — *except* "the generation call runs in the agent's own turn," which is precisely the part carrying both 🔴s (V2, V3). Risk and Complexity dominate; Cost agrees; the ratified end-state direction is honored without betting the lane on unbuilt seams. Honest counterpoint for discussion: if the user's core intent is A-direct ("generation literally on the agent lane"), the seam work is the price of admission — that is decision D1 below.

> **§4 close-out (2026-10-11).** The deferred async submit→poll recommendation is **superseded by Option C** — consumer-side streaming for `od.generate`, shipped at `latest` c63f93e30 (`feature/od-generate-async-poll`). Root cause was NOT a proxy timer: the CF edge window kills buffered zero-byte responses; the proxy's streamed path is CF-safe. Full decision + evidence: [`../od-generate-async-poll/architecture-decision.md`](../od-generate-async-poll/architecture-decision.md). Live proof: 130.7s streamed generation via the production path, zero CF-524 (probe: 154s).
>
> **Open follow-up (A1):** intermittent terminal usage-chunk drop on the inline-`<think>` wire profile — proxy-lane investigation recommended as its own commission; ensemble consumer exonerated.

## 5. Migration Path (sketch, no build)

1. **Slice 1 (B):** `OdGenerate.__init__(llm_config)` + factory + `plugin_tool_factory.build_tools_for_port` forwards config (`plugin_tool_factory.py:201-289`, manager blueprint at `manager.py:1307-1318`). Zero API change; ship behind existing tests + new failover-wiring pins.
2. **Slice 2 (A pilot):** author agent + OD references-skill; add to designer's `team_members` alongside `worker`; dual-run (pilot agent for multi-page, direct tool for one-offs). No deprecation.
3. **Slice 3 (decision point):** if pilot wins its gates → route generation runs through the agent; keep single-shot `od.generate` for quick one-offs indefinitely (it is now on-lane anyway). Deprecate the *generate port* only if the agent path strictly dominates — not assumed.
4. **A-direct:** parked; un-park = build `max_tokens` seam + explicit fidelity/context trade acceptance.

## 6. Naming Shortlist (user wants shorter than "designer-worker")

| Name | Length | Rationale |
|---|---|---|
| **drafter** | 7 | Role-descriptive of what it does (produces drafts/artifacts); reads as designer's downstream producer; my lead |
| **sketcher** | 8 | Design-domain flavor; pairs naturally with `designer`; slight ambiguity (sketch ≈ low-fidelity) |
| **maker** | 5 | Shortest; generic-builder vibe; weakest domain tie |

## 7. Risks (severity-ordered, deduped across workers)

- 🔴 **A-direct/A-lane output control:** no `max_tokens` on the agent lane (V2) — runaway output or compaction firing mid-HTML → malformed artifact, *with retries on top*. Mitigation only via a new seam (build it) or avoiding A-direct.
- 🔴 **A wait-timeout binding:** `invoke_agent_and_wait` 300s default vs 130-170s calls + semaphore stalls (V3) — silent trim unless callers pass an explicit larger timeout (~400s).
- 🟡 **A context/checkpoint bloat:** 64K-token HTML per child in context + checkpoints; compounds known instance-history-walk cost; vision-model context-limit resolution for the compaction trigger unverified (`compaction.py:1131`).
- 🟡 **A parallel ceiling:** semaphore caps at 4 concurrent children → ~37-min floor for a 50-page run; batches of 4, `max_children_per_instance=50`.
- 🟡 **Both — mid-call cancellation is impossible today:** pause cancels at node boundaries; a 170s call runs to completion or per-request timeout. The "cancellation" benefit is turn-level, not call-level, under every option including A.
- 🟡 **B overflow typing gap:** raw-SDK facade doesn't type `context_length_exceeded` (V7) — v2 wiring fix; low practical impact at current sizes.
- 🟡 **A fidelity discipline:** skills must carry `vendored_references`, never prompt text (V8); the drift-alarm contract dies silently if violated.
- 🟢 **B proxy identity headers:** raw-SDK sites omit them today — closing during Slice 1 is a free parity win.
- 🟢 **A watchover intercept:** default 15s audit interval adds latency to the child's tool chain — set `watchover.timeout_seconds ≥ 60`.

## 8. Open Decisions for the User (D1–D5)

- **D1 — Where does the generation call live in the end-state?** Tool-internal on-lane (B / A-orchestrate — recommended) vs the agent's own turn (A-direct — requires building the `max_tokens` seam + accepting envelope drift & context bloat). *This decision shapes everything else.*
- **D2 — Is N-page parallel generation a near-term requirement?** If not, Stage 2 can wait; B alone covers single-page resilience. If yes, note the 4-concurrent ceiling and decide batch expectations.
- **D3 — Configure a backup LLM endpoint?** All failover machinery is inert until `OPENAI_BASE_URL_BACKUP` exists (V6). Decides how much of the "failover" benefit is real now vs future-proofing.
- **D4 — Pilot gates for Stage 2:** accept dual-run + parity metrics (latency, tokens/cost, truncation rate, marker pass-rate) before any deprecation of direct `od.generate`?
- **D5 — Name + team shape:** `drafter` / `sketcher` / `maker`; and does the new agent join designer's `team_members` alongside `worker`, or does designer route generation-only spawns to it?

## 9. Confidence & Flip Assumptions

**Confidence: High on Stage 1 (B)** — three shipped facade callers, regression-pinned hazards documented, contained blast radius. **Medium on Stage 2 (A-orchestrate)** — structurally verified, but skill-injection end-to-end for vendored OD resources, the vision context-limit resolution, and the recursion-limit base remain unverified (below).

Recommendation flips if: (a) the user's core requirement is literally A-direct — then the seam becomes the work item and B is merely its companion; or (b) parallel multi-page generation is imminent AND the retry-with-prompt-adjust loop proves quality-critical — then Stage 2 accelerates and B+ (plain workers) is the interim parallel shape.

## 10. Gaps / Unverified (carried from workers)

- ToolNode sequential-default traced at construction site only, not through langgraph prebuilt internals (V4).
- Vision-model context-limit resolution end-to-end for the compaction trigger (`get_model_context_limit`, `compaction.py:1131`).
- Skill-injection sequence for vendored plugin resources on a fresh agent (only `opendesign.list_systems` exists to test with).
- `OdGenerate` constructor signature exactly (inferred from the `_blueprint_embedding_service` pattern).
- `recursion_limit_multiplier` base value in `daemon/constants.py` settings.
- Process note: both workers omitted the literal `Skill loaded:` first line but produced reports in the structural-design Mandatory Report Format section-for-section with dense file:line evidence — skill injection behaviorally confirmed; run NOT degraded.
