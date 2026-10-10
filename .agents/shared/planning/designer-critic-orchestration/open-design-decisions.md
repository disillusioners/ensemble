# Open Design Decisions — designer-critic-orchestration

**Feature:** `designer-critic-orchestration`
**Author:** planning worker (planner lane) on `feature/designer-critic-orchestration`
**Date:** 2026-10-10
**Status:** 🟢 RATIFIED 2026-10-10 (architect verdict) — D1–D7 adjudicated; all curator open questions resolved; phase plans consume architecture-recommendation.md verbatim. **Source of truth for D1–D7:** `architecture-recommendation.md` in this directory. **This file remains as the historical decision record** (the recommendation framing per decision was set here before architect enrichment and is preserved unchanged; treat this as the audit trail, not the binding verdict). Phase plans cite the architecture doc, not this file.

> **Adjudication pointer.** Every D1–D7 verdict, every open-question resolution (Q1.1, Q2.3, Q3.1–Q3.4, Q4.2, Q5.4, Q6.1–Q6.4, Q7.1–Q7.4), and every risk adjudication (5.1–5.15) lives in `architecture-recommendation.md` in this directory. Read it for the binding text. Do **not** re-litigate any D-cluster here — architecture-recommendation.md wins.
**Worktree-only writes:** All edits land in `/home/nea/ensemble-src-wt-designer-critic-orchestration`. The main checkout (`/home/nea/ensemble-src`) and any install dir (`~/agents-ensemble*`, `INSTALL_DIR/releases/**`) are read-only references; integrity gate rejects writes there (recurring incident family occ-1 / occ-2).

---

## Preamble

### Directive (verbatim, treated as fixed requirements)

Two designer test commissions failed `od.generate` in 24 hours (Snapshots v2 redesign, Oct 9 + Oct 10): the first hit the 173s cap timeout, the second hit Cloudflare 524 at the proxy's 120s read window. Both fell back to the text lane. User direction (Discord, 2026-10-10):

1. **REMOVE `od.generate` from designer entirely.** Sketcher is THE generation lane — always. The designer workflow's Phase-4 rule "single-page one-offs stay on my own direct `od.generate`" (`agents/designer/workflow.md:105`) is dead. This **supersedes** the sketcher pilot's dual-run / parity deprecation gate (the pilot gates in `stage2-addendum.md` are now moot — see D7).
2. **ADD a reviewer agent — design-QA gate.** Working name `critic` (user pre-approved default). Pattern: designer = orchestrator spawning sketcher instances (like leader → developer/tester); critic = output-review step.
3. **Target pipeline:** designer composes brief → spawns sketcher (generation; `od.*` stays tool-internal there per D1 compliance) → critic reviews sketcher output (verdict: pass / needs-revision with specific actionable feedback) → designer iterates or accepts/saves.

### Infra flag (carry prominently — NOT in scope to fix)

The llm-supervisor-proxy's 120s read window vs OD's 130–170s generation budget is the root cause of the `od.generate` 524s. The sketcher lane routes `od.generate` through the ensemble LLM lane (Stage-1 failover per `architecture-recommendation.md` §5, slice 1), so whether it hits the same ceiling is **UNRESOLVED** — treat as risk input to every timeout / iteration decision below and recommend its own future commission (proxy-key plumbing). The sketcher's bounded regenerate-once (Cardinal #3) does not collapse on this ceiling; it amplifies it — two sequential 130–170s calls = ~260–340s per round-trip. Plan impact: budget iterations and per-page review latency with that band in mind (D2, D4).

### Scope boundary (for this deliverable)

This document is the **decision framework** the architect enriches. It is NOT the implementation plan. Each decision is shaped as a recommendation + the open questions the architect must nail down. Safe defaults are specified for unresolved-at-build-time cases so the planner's phase tasks do not deadlock. **No agent prompt files, no code, no installs are modified** — that is the developer/tester lane.

---

## D1 — od.* tool distribution post-surgery

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- `od.generate`, `od.compose_brief`, `od.save`, `od.lint` are individually-named native Port tools, NOT a category. Binding chain: port declarations `daemon/plugin_subsystem/opendesign/ports.py:61-102` → `build_default_port_registry()` at `daemon/plugin_subsystem/port_registry.py:724-753` → `ADAPTER_CLASS_TABLE` at `daemon/plugin_subsystem/plugin_tool_factory.py:94-99` → `build_tools_for_port` sets `name = port_id` at `daemon/plugin_subsystem/plugin_tool_factory.py:250`. No `od.*` entry in `TOOL_REQUIRED_AGENTS` (`daemon/tools/_auth.py:35-51`).
- `od.generate` adapter method `execute_dict` lives at `daemon/plugin_subsystem/opendesign/generate.py:1071` — D1 compliance is structural for the agent that holds the tool.
- **Designer today** (`agents/designer/meta.json:18`): `tools.allow` carries all four `od.*` tokens + `view-views`; `team_members: ["worker", "sketcher"]` (`:22`); `llm_model: "vision"`; no `tools.deny` / `deny_spawn` / `caller_model_overrides` keys exist.
- **Sketcher today** (`agents/sketcher/meta.json:14-21`): `tools.allow` carries all four `od.*` + `image` + `dynamic-skill`; `team_members: []`; vision; `skill_injection: true`; `default_queue: "system_parallel_queue"`.
- **Compose / lint / save workflow today**: sketcher owns the entire `od.compose_brief → od.generate → od.lint → od.save` pipeline (Phases 3–4, `agents/sketcher/workflow.md:42-65`). Designer today crafts the brief at `agents/designer/workflow.md:109` ("I craft the brief myself … the same `brief_answers` + `brand_spec` for every page"). The lint verdict rides in sketcher's report already (Cardinal #3 verbatim codes).
- **od.save canonical path** is deterministic: `{project_root}/.agents/shared/planning/{feature_slug}/design/mockups/{page_slug}.html` (`daemon/plugin_subsystem/opendesign/save.py:67-72`).
- **Constraint (per dispatch)** — `od.generate` is **sketcher-only BY DECREE**. The remaining three tokens (`od.compose_brief`, `od.lint`, `od.save`) are NOT decreed; this is the open placement question.

**Options:**

**A. Designer keeps compose_brief + lint + save; sketcher keeps all four.**
- *Pros:* Minimal surgery (one-token removal from designer). Designer retains direct lint pass over the canonical artifact before its post-save visual QA (`workflow.md:122-129`); one tool the orchestrator can call to know "did the write-through happen?" without bouncing through sketcher's report. The sketcher side has zero change.
- *Cons:* Splits the `od.*` surface across two agents in a way that creates a confused agent-blast-radius — if `od.save` ever gains a flag (e.g. dry-run), the designer-side and sketcher-side exposures drift. Adds an `od.*` token to its blast radius that the directive explicitly does not require; the user's wording was "remove `od.generate` from designer", which can be read narrowly (token removal only) or broadly (stop generation-class work entirely).
- *Effort:* Trivial meta.json edit; workflow.md pair already supports both shapes (sketcher runs the pipeline; designer can still call `od.save` if it must pre-allocate or post-validate).

**B. Sketcher holds all four `od.*`; designer holds none.**
- *Pros:* Single owner of the OD lane — one place to evolve, one tool surface to test, zero confused-blast-radius. Matches the user's "sketcher is THE generation lane" language in spirit (all generation-adjacent tools stay together). Lint verdict already rides in sketcher's report envelope — designer reads it from there.
- *Cons:* Loses designer's option to re-lint a saved artifact on its own (today `workflow.md:124` uses its own post-save visual QA, not lint; so this is a paper capability, not an exercised one). Designer cannot call `od.compose_brief` directly to pre-build a brief before dispatch; today it builds the brief text by hand at `workflow.md:109` — switching to `od.compose_brief` is a workflow upgrade the directive does not authorize.
- *Effort:* Three-token removal from designer; zero changes to sketcher. Pair with workflow.md edits that remove the now-unreachable references.

**C. Hybrid: sketcher holds all four `od.*`; designer holds a read-only `od.lint_readonly` projection.**
- *Pros:* Designer gets a post-handoff verdict preview before integrating into its spec / conformance review.
- *Cons:* The Port system does not currently support role-scoped tool projections — this is a new feature, not a config edit. Adds a new seam the directive does not request. Defer-and-decline unless the architect surfaces a concrete need.

**Recommendation: B — sketcher holds all four; designer holds zero `od.*`.**

**Reasoning:**

1. The directive's framing ("sketcher is THE generation lane — always") is unambiguous when read alongside `od.save`'s role: write-through IS generation-class work (sketcher Cardinal #4: "Write-through or it did not ship. Every generated HTML is saved via `od.save` to the canonical mockup path"). Splitting `od.save` between sketcher and designer reintroduces the same blast-radius confusion the directive is removing.
2. Designer's current `od.lint` and `od.compose_brief` calls are not in any exercised code path: `workflow.md:124-129` runs the post-save visual QA via browser capture + `image_save` + `compare_images`, not lint. `workflow.md:109` builds briefs in hand-written text. So removing these tokens removes nothing the agent has been exercising.
3. Single ownership is the path of least surprise for the tester's conformance review — one agent to spot-check every `od.*` envelope.
5. Reversibility is high: re-adding tokens to designer is a meta.json edit, not a refactor.

**Assumptions:** (a) designer's brief-construction at `workflow.md:109` continues as plain text (not migrated to `od.compose_brief`); (b) the post-save visual QA at `workflow.md:122-129` continues to use the browser-capture procedure, not `od.lint`.

**Reversibility:** High — meta.json edit + workflow.md reference sweep.

**Architect questions:**

- **Q1.1 — Does the user's "remove od.generate from designer" read as narrow (one-token) or broad (zero-od.*)?** Recommendation assumes broad; if narrow, switch to A. The current two-file amendment restates B as the default but does not foreclose A if the user surfaces a use case.
- **Q1.2 — Does the post-save visual QA procedure (browser capture + image_save + compare_images) need a tool elevation (`design.capture_mockup`, deferred item (c) of `stage2-addendum.md`)?** If yes, scope creeps: a new tool category surfaces, and the critic's role may shift. Architect decision: keep on the deferred list OR promote to Phase-1.
- **Q1.3 — Does the brief-construction hand-typed at `workflow.md:109` get migrated to `od.compose_brief`?** If yes, designer needs `od.compose_brief` back in `tools.allow` (Option A). Architect recommendation: NO — designer-as-orchestrator crafting its own briefs is the cleaner split; if `od.compose_brief` ever gains features the orchestrator needs, add it back then.

**Plan impact:**

- Phase 1 (mechanic): one `meta.json` token strip on designer; zero on sketcher. If Q1.1 resolves narrow, the token strip is 4 edits deep instead of 1.
- Phase 2 (workflow): workflow.md re-keying on both agents to remove "my own direct `od.generate`" references (Phase 4 line at `workflow.md:105`) and any "I call `od.lint`" wording that no longer applies.
- Phase 3 (tester): conformance test must reject designer prose that references `od.generate`/`od.compose_brief`/`od.lint`/`od.save` as agent-callable surfaces; the agent's tool allow-list IS the gate.
- **Safe default if unresolved at build time:** **B** (designer holds zero `od.*`). The narrow-directive interpretation is a single-file edit at promotion time.

---

## D2 — critic invocation mechanics

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- Two dispatch lanes exist for child agents:
  - **SYNC** `invoke_agent_and_wait` (`daemon/utils.py:622-774`): params incl. `images` (base64 data-URIs → vision routing), `timeout` (default 300s, **300s default is tight against 130–170s sketcher generations + queue stalls**), `model` (spawn-time model override), `return_instance_id`. Child stamped `invoked_as_tool=True` (`daemon/services/instance_lifecycle.py:2327-2328`) which suppresses the async parent notification (`daemon/services/child_reports.py:3524-3535`); spawner receives the child's FINAL REPORT as the synchronous tool result (`utils.py:738`). Timeout → error string + best-effort orphan termination. **Semaphore capped `WORKER_POOL_SIZE - 1`** (1 slot always free so the fire-and-forget lane keeps draining).
  - **ASYNC** `send_message`: fire-and-forget; child completion enqueues `internal_report:{instance_id}:{completed_message_id}` back to the parent as a NEW parent turn with idempotency dedup (`child_reports.py:3499-3513`). Parent resumes on the next dispatch round; pipeline pauses cleanly between dispatch and report.
- **Precedent for sync timeout on `od.generate`-bearing children**: designer's existing rule at `agents/designer/workflow.md:116` mandates `timeout >= 400s` for synchronous invocations of any `od.generate`-bearing child.
- **Critic review depth (estimate)**: HTML review + provenance lookup + (optionally) screenshot-vs-baseline diff. Pessimistic upper bound: comparable to sketcher (130–170s) if pixel-comparison path is exercised; optimistic lower bound: ~30s for HTML+lint only.
- **Pipeline shape**: designer dispatches sketcher → designer dispatches critic (after sketcher writes through) → designer integrates verdict. This is two **separate child dispatches**, not one chained call.

**Options:**

**A. Sync `invoke_agent_and_wait` for critic.**
- *Pros:* Verdict in-turn: designer gets the verdict as a synchronous tool result, integrates it immediately, dispatches the next sketcher iteration or accepts/saves without a turn-cycle pause. Pipeline latency = dispatch + 1 turn-cycle, not 3. Tool surface mirrors `explore()` pattern (which is the shipped usage at `daemon/utils.py:622-774`).
- *Cons:* Holds a worker slot + semaphore (`WORKER_POOL_SIZE - 1` cap) for the entire critic review. Worst case: sketcher round-trip (130-170s) + critic round-trip (~30-170s) + designer integration turn ≈ 400+ seconds of single-threaded holding time per iteration. Multi-page runs serialize on the semaphore cap, not on the actual work. Timeout misuse reproduces the 300s default trim that designer's existing rule at `workflow.md:116` warns against.
- *Effort:* Add `invoke_agent_and_wait` to designer's `tools.allow`; document the ≥400s timeout rule for critic invocations.

**B. Async `send_message` for critic.**
- *Pros:* Parent (designer) free between dispatch and report; the runtime resumes designer on the next dispatch round (no timeout budget to misconfigure). Pipeline continues across the report turn — designer can be doing prep work in the parent's parallel lane while critic reviews. Survives long reviews cleanly; no orphan best-effort kill. Pattern matches designer's existing sketcher dispatch (which is async by design at `workflow.md:112`: "Send dispatch via `send_message`, then **end turn** — the runtime resumes me per report").
- *Cons:* Latency stack = dispatch + report-turn (parent turn cycle). For multi-page parallel runs, the fan-in needs an aggregation step. Designer must end turn after dispatch and resume on report — a pattern designers must consciously adopt (today the sketcher dispatch pattern is established; critic dispatch is the same shape).
- *Effort:* No new tool surface; designer follows the sketcher dispatch pattern. Add a Cardinal rule mirroring `workflow.md:113` for critic.

**C. Sync `invoke_agent_and_wait` with explicit timeout ≥ 400s for critic (defensive variant).**
- *Pros:* Same in-turn verdict benefit as A but with the existing precedent rule explicitly applied; the rule is already enforced on the worker-side by the same agent (`workflow.md:116`).
- *Cons:* All cons of A apply; this is A with a timeout fix, not a different shape.

**Recommendation: B — async `send_message` for critic.**

**Reasoning:**

1. The established pattern for in-flight writer/QA children in this design system is async fire-and-forget (see sketcher dispatch at `workflow.md:112`). Critic follows the same shape: dispatch, end turn, resume on report. The dispatcher agent (designer) already knows this pattern; the workflow rule surface needs zero new prose to teach it.
2. Async avoids the semaphore starvation risk that sync would impose on multi-page parallel runs. With 4 worker slots (default `WORKER_POOL_SIZE = 5`), a sync critic per page halves the available worker pool for the next sketcher dispatch; async critic does not consume a slot.
3. The 300s default timeout trap on sync is a documented recurring footgun (`workflow.md:116`); going async removes it from the surface entirely. The plan does not need a "remember to set timeout ≥ 400s" rule.
4. The async-report-turn latency cost (one parent turn cycle) is acceptable — designer MUST integrate verdict + iterate, which is a meaningful decision step anyway. The added latency is one turn boundary; the pipeline is not latency-critical (a 5-page run is multi-minute regardless).
5. Test gates from the existing pilot (`stage2-addendum.md` campaign notes) skew async-friendly: each row records `latency_s` per page; a per-page critic doesn't change that schema.

**Assumptions:**

- (a) Designer's spawn pattern for sketcher (async, multi-wave, end-turn-once) extends naturally to critic with no new gate; (b) the parent's runtime cost of "end turn after `send_message`" (Cardinal #5, `rule.md:15`) is acceptable for the critic dispatch.

**Reversibility:** Low — switching from async to sync mid-design is a workflow rewrite. Sync-to-async is trivial (remove the invoke call, replace with send_message + Cardinal #5). Direction of switch is the designer's call.

**Architect questions:**

- **Q2.1 — Does the critic verdict flow benefit from in-turn integration enough to outweigh the semaphore cost?** Recommendation says no; if the architect surfaces a use case where verdict must be in-turn (e.g. critic outputs HTML the designer then mutates and saves), revisit to A.
- **Q2.2 — Is there a critic-specific edge case where async's idempotency dedup (`child_reports.py:3499-3513`) might drop a verdict?** Probably no — verdicts carry envelope data, not user-typed content; idempotency dedup is by completed_message_id. Architect confirm.
- **Q2.3 — Is the per-page fan-in pattern (designer dispatches N sketcher + N critic, then integrates all) the right shape, or should it be page-at-a-time (one sketcher, wait, one critic, integrate, next)?** Recommendation: page-at-a-time is safer for a critic loop (the verdict feeds the next brief). Architect confirm.

**Plan impact:**

- Phase 2 (workflow): designer workflow.md gets a critic-dispatch pattern mirroring the sketcher pattern at `workflow.md:107-113`. Add a Cardinal rule: "End turn after critic `send_message`." Mirror the dispatch convention (page-id-keyed routing, return path, report integration).
- Phase 3 (tester): conformance test that a designer dispatch to critic follows the same async pattern as the sketcher dispatch (no sync `invoke_agent_and_wait` calls to critic; if the agent surface ever grows to need it, that's a separate architectural change).
- **Safe default if unresolved at build time:** **B** (async). Matches the established pattern; no new tool surface; no timeout footgun.

---

## D3 — critic verdict structure

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- Two existing verdict precedents in this design system:
  - **Tester** (`agents/tester/rule.md`): per-criterion tiered results (critical MUST pass / important / nice-to-have), evidence-cited, explicit pre-existing/quarantine class so old defects don't fail the artifact. Schema is a structured report the orchestrator can route on.
  - **Governor councils**: verdict token + severity counts, e.g. `"APPROVED (governor council, 0 critical/0 warning)"`, `"PASS-with-preexisting"` (referenced in `parity-runs.jsonl` mechanism smoke note and `stage2-addendum.md` close-out notes).
- **Sketcher envelope contract** (`agents/sketcher/workflow.md:80-90`): heading `## Sketch — <page> — SHIPPED|FAILED` + mandatory Envelope Metrics block (latency_s, usage, truncated, gates, marker_pass, finish_reason, error_code, model, attempts, lint, artifact). Verbatim codes, no paraphrase.
- **Iteration precedent**: designer's post-save visual QA at `workflow.md:122-129` uses concrete fix instructions in re-dispatch briefs; iterations 1–3 carry forward, iteration 4 escalates with captures.
- **Critic's review depth is HTML-level (and optionally pixel-level)**, not test-level. The verdict must distinguish "the page renders" from "the page conforms to the spec".

**Options:**

**A. Pass / Needs-revision (N critical / K advisory) — tiered severity, structured feedback list.**
- *Pros:* Mirrors the tester precedent (criterion-tiered, severity-bucketed). "Critical" = blocker (e.g. layout broken, brand mismatch, missing element); "Advisory" = nice-to-fix (e.g. copy tweak, spacing). Designer can route: needs-revision + any critical → iterate; needs-revision + advisory-only → accept-with-disclosure. Machine-parseable: a tagged block (`needs_revision:<verification>` × N) lets the orchestrator scan for `critical:` prefix without parsing prose. Aligns with the user directive's verdict language verbatim ("verdict: pass / needs-revision with specific actionable feedback").
- *Cons:* Requires the critic to classify findings into severity tiers — non-trivial judgment call for an HTML reviewer (what's critical? font-mismatch or off-by-one in spacing?). Pre-existing-vs-new distinction is a separate axis the tester uses (`rule.md` precedent) but the critic may not need — the artifact is freshly produced.
- *Effort:* New cardinal rule + heading + Verdict block schema. Workflow-level prompt.

**B. Pass / Fail with single feedback string.**
- *Pros:* Simplest possible shape. Critic returns a single string of prose; designer parses for "PASS" or "FAIL".
- *Cons:* No severity bucketing means the designer cannot triage — a one-pixel typo fails as hard as a layout collapse. Machine-parseability is poor; orchestrator must string-match. Diverges from the tester precedent the project has already ratified.
- *Effort:* Trivial.

**C. Rubric-scored (1–5 per criterion, pass threshold).**
- *Pros:* Quantifies quality; future analytics on critic outputs.
- *Cons:* New scoring discipline the agent has to internalize; rubric drift over time; harder to communicate to user than a verdict token. Diverges from both tester (tiered) and governor (token + counts) precedents.
- *Effort:* Schema + rubric authoring + drift-detection.

**Recommendation: A — Pass / Needs-revision (N critical / K advisory), with a structured feedback block.**

**Reasoning:**

1. The user's directive language is verbatim ("verdict: pass / needs-revision with specific actionable feedback"). Option A matches the wording without rebranding.
2. The tiered severity matches the tester's ratified precedent (which the project has been quoting across both Verifier and reviewer-council surfaces). Consistency across review surfaces is a non-trivial win for the agent pool — three agents (tester, governor, critic) all use severity-tiered structures.
3. The structured feedback block (one finding per line, severity prefix) is what makes iteration efficient: designer's next sketcher dispatch can lift the feedback lines verbatim into a re-dispatch brief, with no prose paraphrase that would risk drift. This is the same pattern as sketcher's "report exact `error.code` + `message` verbatim" (`workflow.md:89`).
5. Pre-existing classification is NOT included: the artifact is freshly produced; there are no pre-existing defects on a freshly generated HTML. (Tester uses pre-existing because it reviews code that has a history; critic reviews freshly generated artifacts.)
6. A finding's actionable shape ("change the hero copy from 'Welcome' to 'Sign in'", "increase CTA button contrast by 20%") is what makes it useful — vague findings ("could be better") are not actionable. The Cardinal rule should require actionable specificity, mirroring sketcher's Cardinal #5 ("No narrative padding. If a sentence does not carry evidence or a decision request, cut it").

**Verdict format (recommended exact shape):**

```
## Review — <page> — PASS | NEEDS-REVISION (N critical, K advisory)

verdict: pass | needs-revision
critical_findings:
  - [CRITICAL] <finding-1>
  - [CRITICAL] <finding-2>   # omitted on PASS
advisory_findings:
  - [ADVISORY] <finding-1>
  - [ADVISORY] <finding-2>   # omitted on PASS
artifact: <canonical mockup path>
screenshot_capture: <image_save id, if visual QA performed>
model: <model id from the spawn envelope>
notes: <optional, free-form>
```

**Architect questions:**

- **Q3.1 — Does "PASS with caveats" (verdict=pass, advisory_findings present) count as PASS for the iteration loop, or as needs-revision? Recommendation: PASS — the loop terminates; advisory lines ride into designer's spec as next-iteration-fix-up inputs.
- **Q3.2 — Does the critic's review cover the spec's acceptance criteria, the brief inputs, or both?** Recommendation: both (the spec is the source of truth; the brief inputs are the alignment check). Architect confirm.
- **Q3.3 — When the critic sees a pre-existing tier1 defect in the page (e.g. a known brand-spec gap that sketcher inherited from the brief), does it count against the verdict?** Recommendation: NO — the tier1 classification is for the reviewer (tester) reviewing cumulative code, not for critic reviewing one artifact. If a brief-level defect surfaces, the critic should flag it as `[BRIEF-LEVEL]` (a third tier, distinct from critical/advisory) so designer knows the fix is at brief level, not at artifact level.
- **Q3.4 — How are findings emitted when the critic has zero tool-call evidence or the artifact is missing?** Recommendation: emit `verdict: needs-revision` with `[CRITICAL] artifact not found at <expected path>` so the same machine-parsable shape carries the missing-artifact case.

**Plan impact:**

- Phase 1 (schema): pin the verdict block format above as a planning-dir doc (`.agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md`). Future-proofs agent-prompt prose so the conformance tester checks verbatim.
- Phase 2 (workflow): critic workflow.md emits the verdict block; designer workflow.md parses it (string scan for `verdict:` token, lift `critical_findings` lines into next-sketcher re-dispatch brief).
- Phase 3 (tester): conformance test asserts critic verdict block shape (heading + verdict line + critical_findings/advisory_findings arrays present + artifact path present).
- **Safe default if unresolved at build time:** **A** with the schema above. The directive's own language resolves to A; safe default is to ship A verbatim and refine in a follow-up if the architect surfaces a sharper shape.

---

## D4 — iteration limits

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- **Existing precedents for iteration budgets:**
    - Designer's post-save visual QA loop (`agents/designer/workflow.md:122-129`): ≤3 iterations per page; iteration 3 fail → escalate with captures attached.
    - Sketcher's internal regenerate-once (`agents/sketcher/rule.md` Cardinal #3): exactly 1 retry on truncation-class only, then ship; doesn't add rounds, only retries-within-one-round.
    - Tester's verification rounds (per project conventions; tier1/tier2 distinction): bounded rounds before escalating.
- **Pipeline shape (post-D2 decision)**: designer → sketcher → critic → designer (iterate or accept). A "round" = one sketcher pass + one critic review + one feedback application.
- **Critic dispatch cap**: a sketcher that fails twice in a row (truncation retry + the bounded once) means the artifact may be incomplete; the critic may flag it `[CRITICAL] artifact incomplete / missing markers` and round-trip again.
- **Infra flag — preamble amplification: sketcher regenerate-once under the proxy's 120s ceiling could mean each round-trip is 2× ceiling = ~260s wall + critic review time.**

**Options:**

**A. ≤3 rounds total (sketcher+critic), then accept-with-disclosure or escalate.**
- *Pros:* Mirrors the designer's existing visual QA budget (≤3 iterations) exactly. Familiar to designer. 3 rounds × worst-case latency = ~3 × (170s sketcher + 170s critic + integration) ≈ 1000+ seconds = ~17 minutes per page. Acceptable for a multi-page run (each page in parallel).
- *Cons:* At 3 rounds × worst-case 524 cadence from the proxy ceiling, a single page could blow past reasonable wall-clock. The soft-A-band disclosure of "we tried 3 times and shipped the third" may not match user's quality expectations if the third round is no better than the first.

**B. ≤2 rounds total, then escalate (no accept-with-disclosure).**
- *Pros:* Tighter budget; faster escalation; caps wall-clock to ~30s per page at 524 cadence.
- *Cons:* Half the existing visual QA budget; designer's existing conformance loop allows 3; reducing it may be too tight.

**C. Quality-bucketed rounds — 1 round for PASS verdict, ≤3 rounds for NEEDS-REVISION, then escalate.**
- *Pros:* Matches the variant spent on iteration: a page that passes first round costs one sketcher call only; a page that fails gets more.
- *Cons:* Two limits in one rule is harder to teach; designer may forget which bucket it's in.

**D. ≤3 rounds, with mid-budget escalation gate at round 2 (designer pauses to assess before round 3).**
- *Pros:* Adds a human-in-the-loop checkpoint without a hard cap reduction. Aligns with how leader → developer/tester handles mid-loop escalation.
- *Cons:* One more rule to encode in a workload standard.

**Recommendation: A — ≤3 rounds total (sketcher+critic), then accept-with-disclosure OR escalate.**

**Reasoning:**

1. Matches the existing visual QA budget at `workflow.md:122-129`. The same number (3) means the existing conformance-loop prose stays coherent; the addition is "the loop now has a critic review between rounds", not "the loop budget changed".
2. At round 3 with critical findings still present, designer has two legitimate paths:
    - **accept-with-disclosure** — designer overrides the critic and ships the third artifact, but the verdict (`needs-revision, N critical`) is recorded in the page-handoff memo and the spec gets a `[REVIEW-CAVEAT]` line. Acceptable when the artifact is "good enough" but critic surfaced a polish-class concern.
    - **escalate-to-caller** — designer ends turn with a structured gap report (artifact + 3 critic reports + the iterations tried) and asks the user / leader to scope-shrink or split the page.
3. The third-round-accept-with-disclosure is a new affordance designer doesn't have today (today's ≤3 iterations loop escalates-with-captures at the third fail; there is no accept-with-disclosure). It must be a Cardinal rule — surface the override explicitly so conformance can audit it.
5. Reconciles with sketcher's internal regenerate-once: each "round" from designer's perspective may consume 1 or 2 sketcher calls (sketcher's internal budget is independent). Worst case = 3 designer rounds × 2 sketcher attempts = 6 generation calls per page. At 524 cadence × 6 = ~52 minutes per page worst-case. The accept-with-disclosure at round 3 is the safety valve.

**Assumptions:**

- (a) The accept-with-disclosure affordance is acceptable to the user (it is a user policy decision, not a designer-only choice); (b) a 17-minute per-page worst case is acceptable for a multi-page run; (c) the round-3 escalation path is the designer's, not the critic's (the critic emits verdicts, not next-action decisions).

**Reversibility:** Medium — changing the round count mid-build is a workflow.md edit + a Cardinal rule. Changing accept-with-disclosure to escalate-only is one rule edit.

**Architect questions:**

- **Q4.1 — Is ≤3 rounds the right number, or should it be lower (≤2) given the proxy ceiling?** Recommendation assumes 3 with the ceiling treated as a risk-input to be revisited post-pilot (preamble infra flag).
- **Q4.2 — At round 3 with critical findings, is accept-with-disclosure acceptable to the user, or is escalate-to-caller the only path?** User directive says "designer iterates or accepts/saves" — both are listed. Recommendation treats them as designer judgment; architect confirm this is the user's intent.
- **Q4.3 — Should the round count be per-page or per-run?** Recommendation: per-page (matches existing visual QA budget). Per-run would be a tighter cap on total iteration spend across all pages in a run.
- **Q4.4 — Does the bounded regenerate-once inside sketcher count as one of designer's 3 rounds, or is it a transparent sub-step?** Recommendation: transparent sub-step (sketcher's budget is its own; designer's budget is independent).

**Plan impact:**

- Phase 2 (workflow): designer Cardinal rule + workflow.md loop section. Mirrors `workflow.md:122-129` with the critic inserted between sketcher dispatch and integration.
- Phase 3 (tester): conformance test that the round-3 escalation memo carries the 3 verdict blocks verbatim.
- **Safe default if unresolved at build time:** **A** (≤3 rounds, accept-with-disclosure OR escalate at round 3). The number is established precedent; the disclosure is a single Cardinal rule addition.

---

## D5 — Cardinal #7 text-fallback interaction

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- **Cardinal #7** (`agents/designer/rule.md:15`): text-lane specs MUST record `fallback_reason` with exact tokens `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`. Missing reason = SPEC INCOMPLETE, conformance rejects; **the tester gates on these exact strings**.
- **Lane-binding probe** (`agents/designer/workflow.md:72-80`): Step 0 `opendesign.list_systems` lookup; per-page fallback; graceful degradation mandatory (`workflow.md:97`).
- **Binding condition** in current prose (`agents/designer/rule.md:35`): "the `od.generate` Port tool is in the agent's toolset". After D1 surgery, the designer holds zero `od.*` tools — the literal binding condition becomes FALSE for designer.
- **Phase-4 rule being phased out** (`agents/designer/workflow.md:105`): "Single-page one-offs stay on my own direct `od.generate`" — this is the rule the directive says is dead.
- **What's left after surgery**: the text-lane is reachable only when the orchestrator (designer) makes a deliberate choice — there is no longer an automatic fallback. The text lane is no longer "OD failed, fall back", it is "user explicitly asked, architect judgment".
- **What's NOT changed**: sketcher still holds `od.*`; sketcher's Cardinal #4 still demands write-through. The text-lane audit-trail is per-spec, not per-sketcher-call.

**Options:**

**A. Re-key the binding condition to sketcher-dispatch outcomes; keep text-lane as legitimate last resort.**
- *Pros:* The audit trail (fallback_reason enum) stays authoritative; the tester gates on the same strings. Designer's choice to use text lane is now a deliberate decision, not an automatic fallback — the disclosure becomes: "I am using text lane because the user explicitly asked, and here is the reason sketcher is not the path".
- *Cons:* New binding condition must be authored; old prose at `rule.md:35` becomes stale and must be re-keyed. Adds a new rule to the prose surface.
- *Effort:* Cardinal rule + workflow.md re-keying.

**B. Eliminate the text lane entirely post-surgery.**
- *Pros:* Simplest mental model — designer always dispatches sketcher; text lane is gone.
- *Cons:* Some user requests are text-only (e.g. "draft a comparison spec between two systems" without a mockup). Removing the text lane would force a sketcher dispatch for non-mockup work, wasting a generation call. The text-lane is also the existing escape valve when sketcher itself is wedged (e.g. timeout at the proxy ceiling); eliminating it removes the safety net.
- *Effort:* Removes a Cardinal rule + workflow.md re-keying. Removes one of two design lanes.

**C. Keep text lane as legitimate last resort; re-key the binding condition to "designer judgment + user request".**
- *Pros:* Same as A but without the sketcher-dispatch outcome binding — the text lane is reachable by designer judgment alone. Looser.
- *Effort:* Cardinal rule + workflow.md re-keying.

**Recommendation: A — re-key binding condition to sketcher-dispatch outcomes; keep text-lane as legitimate last resort with explicit user-request disclosure.**

**Reasoning:**

1. The text lane is a deliberate architectural feature (text-native wireframes as layout-and-placement aids, `rule.md` guideline (c)), not a bug. The user's "sketcher is THE generation lane — always" ruling applies to mockup generation, not to text-only deliverables.
2. After surgery, the designer's text-lane path is reachable only when designer explicitly chooses it. The audit trail (`fallback_reason`) is still load-bearing for the tester (it gates on the strings).
3. The new binding condition should be: "designer chose text lane because (i) the deliverable is text-native per user request, OR (ii) sketcher dispatch failed for a reason that bypassed designer's judgment (e.g. consecutive 524 timeouts)" — case (ii) is the safety valve for the proxy ceiling. Both cases MUST carry a `fallback_reason` from the existing enum.
4. The `fallback_reason` enum stays authoritative (`tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`). Adding new tokens is a rejection of a test gate — the strings are tested verbatim. The enum can be EXTENDED with new tokens (e.g. `sketcher-wedge`, `user-requested-text-only`) but each extension is a planning-dir doc edit + conformance-test update.

**New binding condition (recommended phrasing, replaces `rule.md:35`):**

> "The text-native lane is reachable when (a) the user explicitly requested text-native output (no mockup needed), OR (b) the sketcher dispatch failed with `timeout`/`call-error` for ≥2 consecutive rounds and designer escalates to text. Both cases emit `fallback_reason` from the existing enum. A text-lane spec without `fallback_reason` is SPEC INCOMPLETE — Cardinal #7 applies unchanged."

**Architect questions:**

- **Q5.1 — Is the text lane legitimately reachable for non-mockup deliverables (specs, comparisons, audits), or does "sketcher is THE generation lane" extend to those too?** Recommendation assumes non-mockup deliverables may use text lane; architect confirm.
- **Q5.2 — Should `fallback_reason` gain new tokens (`sketcher-wedge`, `user-requested-text-only`) or should the existing enum be reused via `other:<detail>`?** Recommendation: prefer `other:<detail>` to avoid enum extension; the existing tokens don't capture these cases well but the `other:<detail>` form is the existing extension path. Architect confirm.
- **Q5.3 — Does Cardinal #7's "tester gates on these exact strings" survive surgery, or does it need re-keying?** Recommendation: survives unchanged (the enum stays authoritative). Architect confirm.
- **Q5.4 — When sketcher itself is wedged (proxy ceiling, daemon unavailable), does designer retry the dispatch or escalate?** Recommendation: 1 retry, then escalate. Matches designer's existing 1-takeover valve at `workflow.md:112` (which is dead post-surgery, but the pattern transfers).

**Plan impact:**

- Phase 2 (workflow): designer `rule.md` Cardinal #7 stays verbatim; guideline (c) the "OD-first" language is replaced with the new binding condition; `workflow.md:72-80` lane-probe is removed (no longer applies since designer holds no OD tool — the probe becomes a sketcher dispatch + watch-for-wedge).
- Phase 2 (tester): conformance test that text-lane specs carry `fallback_reason` from the existing enum. Existing tester gate stays authoritative.
- **Safe default if unresolved at build time:** **A** (re-key binding condition; keep text lane; reuse `other:<detail>` for new cases). The enum is the load-bearing contract; the binding-condition re-key is one rule edit.

---

## D6 — critic mechanics: tools / model / spawns

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- **Critic's review surface (ranked by research):**
    1. `read_file` (filesystem category, default-open, `daemon/tools/filesystem.py:656-658`) on the canonical mockup path returned by `od.save` (`daemon/plugin_subsystem/opendesign/save.py:67-72`: `{project_root}/.agents/shared/planning/{feature_slug}/design/mockups/{page_slug}.html`). Deterministic path.
    2. `image_list`/`image_get` with provenance filters feature/page/version/source_agent/retention_class (`daemon/tools/image_tools.py:841-931`) for screenshot-vs-baseline visual QA.
    3. `explain_image` delegation to image-reader agent (vision model).
    4. Design-category image comparator (blueprint-recorded; exact symbol unverified).
    5. `view-views`/`view_link` URL minting (config-only entry; only if verdicts need human-usable URLs — config-only per D7 of research).
- **view-views privilege** (`daemon/tools/_tool_registry.py:194-220`): freeze-set includes `view-views`; comment names `ari/leader/designer` as commissioned users. Grant path = explicit `tools.allow` entry (`daemon/tools/instance.py:476-482`). SAME-PR triple-pin tests fire only on frozenset changes (`_tool_registry.py:184-191`). Adding critic is CONFIG-ONLY (one JSON entry; no freeze-set change; the comment needs honesty edit + the 4th commissioned user = user policy decision).
- **Model routing** (`config.yaml:94` allowlist; already includes vision; `llm_model: "vision"` resolves via the allowlist):
    - Default global model if `llm_model` is omitted (inherits from config).
    - Per-agent override via `llm_model` in `meta.json` (live precedent: sketcher + designer both pin vision).
    - Per-spawn override via `invoke_agent_and_wait(model=...)` or `caller_model_overrides` (`registry.py:419`, `instance_lifecycle.py:1651-1677`); neither designer nor sketcher currently declares `caller_model_overrides`.
- **Spawn mechanics**: critic reviews one artifact, returns a verdict, ends. There is no proxy fan-out from the critic (it doesn't dispatch other agents).
- **Leaf precedent**: sketcher is `team_members: []` with Cardinal "I am a leaf. I do not spawn instances" (`agents/sketcher/rule.md:14`). Critic review is "one verifier" — same shape, no children.

**Options:**

**Tools — A. read_file + image_get + image_list + explain_image (no view-views, no design comparator).**
- *Pros:* All paths are default-open or config-only. `read_file` covers the canonical mockup path deterministically. `image_get`/`image_list` enable visual QA via substrate. `explain_image` enables the critic to digest reference images for "is the page on-brand". No new privileges; no freeze-set changes.
- *Cons:* No URL minter (critic can't link directly to a viewable mockup if a downstream user wants one). No image comparator (the blueprint-recorded tool is not verified to exist; skipping avoids scope creep).
- *Effort:* Single meta.json `tools.allow` entry on critic.

**Tools — B. A + view-views.**
- *Pros:* Critic can mint viewable URLs for any artifact it reviews — enables human-usable handoff if the orchestrator or downstream user wants to see what was reviewed.
- *Cons:* view-views is the 4th commissioned user — adds the user-decision to the surface; the comment at `_tool_registry.py:194-220` needs honesty edit. Adds a workflow affordance the directive does not require.
- *Effort:* One entry in meta.json + honesty edit in the freeze-set comment.

**Tools — C. A + design comparator (if it exists).**
- *Pros:* Image comparator is purpose-built for the visual-QA task.
- *Cons:* Blueprint-recorded but not verified — the comparator may not exist or may have a different shape. Scope creep risk.
- *Effort:* Unknown until verified.

**Model — A. Inherit global default (omit `llm_model`).**
- *Pros:* No model-pin means no model-pin risk; model can be re-routed centrally.
- *Cons:* Critic's review involves image digestion — vision-required. Without vision pin, a default-mistral proxy may not see pixels, defeating the visual-QA option. The reference-image-digestion path (`explain_image`) is still there, but inline image digest is broken.
- *Effort:* None.

**Model — B. Pin vision (mirror sketcher + designer).**
- *Pros:* Aligns with sketcher + designer precedent. Visual QA works.
- *Cons:* Vision model is more expensive per token; pins the critic to one model family.
- *Effort:* One `llm_model` line in meta.json.

**Spawns — A. No children (team_members: []).**
- *Pros:* Critic is a leaf — one verifier, one verdict, no proxy fan-out. Mirrors sketcher's leaf precedent. Simplifies the team-cap check at `daemon/tools/instance.py:2597-2617`.
- *Cons:* None apparent.
- *Effort:* Single empty list in meta.json.

**Spawns — B. Allow `sketcher` for re-dispatch iteration.**
- *Pros:* Critic could re-dispatch sketcher for "regenerate with these constraints" without going back to designer.
- *Cons:* Critic is a reviewer, not a controller — it shouldn't decide to iterate; that decision is the designer's. The whole point of the pipeline is designer orchestrates → child verifies. Letting critic re-dispatch breaks it.

**Recommendation:**

- **Tools: A** (read_file + image_get + image_list + explain_image; no view-views, no comparator)
- **Model: B** (pin vision)
- **Spawns: A** (leaf, `team_members: []`)

**Reasoning:**

1. **Tools**: The reviewer surface is exactly the four canonical read paths. `read_file` covers the artifact deterministically (canonical path is computed by `od.save`). `image_get`/`image_list` cover the visual QA surface (screenshot via image_save → critic reads via image_get). `explain_image` covers the reference-digestion path (the critic can digest a reference image into structured text for the next critical review). view-views is a user policy decision the directive does not authorize; the comparator is unverified. Skipping both preserves the scope and avoids comments drift on the freeze-set.
2. **Model**: Critic's review routinely involves pixel-level verdict (layout collapse, brand-color match, missing element). Vision pin is required. Mirroring sketcher + designer precedent keeps the agent-set coherent (three vision-pinned agents all reviewing/operating on visual artifacts).
3. **Spawns**: Critic is a leaf. The pipeline is "designer → child → child → designer" — the designer is the only orchestrator. Critic spawning sketcher re-dispatch would invert the pipeline.

**Architect questions:**

- **Q6.1 — Does the user pre-approve `view-views` as a 4th commissioned user, or is the comment edit + entry deferred?** Recommendation: deferred unless the user surfaces a need (verdict-line handoff that needs a URL).
- **Q6.2 — Is vision the right model pin for the critic, or should the critic use a different model (e.g. a code-reviewer model that is cheaper)?** Recommendation: vision, because the verdict routinely involves pixel-level decisions. If the critic's primary surface is HTML-only (no pixel diff), a cheaper model may suffice; but `explain_image` is already in the surface, so vision pin is the cleaner default.
- **Q6.3 — Does the image comparator exist (`design` category)?** Architect to verify before deciding whether to surface it; unverified is a Phase-2 scope question, not a Phase-1 decision.
- **Q6.4 — Does the critic need `filesystem` (the broader category) for read paths other than `read_file`?** Catalog: `read_file` is enough for the canonical mockup path. `filesystem` adds `glob_files`, `grep_files`, `list_directory` — none needed for the verdict surface.

**Plan impact:**

- Phase 1 (meta.json): critic `meta.json` with `tools.allow: ["read_file", "image_get", "image_list", "explain_image"]`, `team_members: []`, `llm_model: "vision"`, `skill_injection: true`, `recursion_limit_multiplier: 7` (mirror designer; critic is lighter than sketcher's 12).
- Phase 2 (workflow): critic `rule.md` mirrors sketcher's leaf Cardinal + adds the reviewer Cardinal (one verdict per dispatch, structured feedback, no narrative padding).
- **Safe default if unresolved at build time:** **Tools=A, Model=B, Spawns=A**. Mirrors established patterns; no new privileges; no freeze-set changes.

---

## D7 — pilot-gate & planning-doc reconciliation

**Status:** RECOMMENDATION — 🔧 ARCHITECT ENRICHMENT REQUIRED

**Context:**

- **Pilot gates in scope to supersede** (`.agents/shared/planning/od-generate-agent-lane/stage2-addendum.md`):
    - N ≥ 10 pages
    - marker_pass ≥ 95% AND within 5pp of direct
    - truncated ≤ direct + 5pp
    - median latency ≤ 1.5x direct
    - tokens/page ≤ 1.3x direct
    - The pilot's adjudication purpose was to decide whether the sketcher lane earns default routing for multi-page runs and whether to deprecate direct (`stage2-addendum.md` adjudication note). Post-D1 surgery, the sketcher lane is the only lane — there is no "default routing" to decide; there is no "deprecate direct" because direct is gone.
- **parity-runs.jsonl** (`od-generate-agent-lane/parity-runs.jsonl`): one mechanism-smoke row from 2026-10-09 (`sketcher-resmoke-20261009-140826`). The schema pins `lane: "direct" | "sketcher"` — `direct` rows can no longer be produced.
- **Max-tokens guidance (NOT to supersede)**: the close-out note that the first real campaign page must use `max_tokens >= 16000` because 8K saturates the vision lane (`stage2-addendum.md` campaign notes (1), `completion_tokens=8000` on both attempts). This is mechanically true regardless of lane — sketcher passes `max_tokens` to the generation call; the campaign notes apply to sketcher unchanged.
- **Deferred items in stage2-addendum.md** (NOT in Stage-2 scope, recorded for next commission):
    - (a) `_OD_GENERATE_WALL_CLOCK_CAP_S=420` cap-math gap (`daemon/services/llm_failover.py:848-852`) — pre-existing structural concern shared with designer's direct path. **CARRIES FORWARD** (applies to sketcher unchanged).
    - (b) Sketcher pipeline paraphrase tightening — **DEFER** (post-pilot cleanup, not in scope).
    - (c) `design.capture_mockup` tool — **ARGUBLY MORE RELEVANT for a critic** (the critic's visual QA needs the capture). Consider promoting from deferred to Phase-2.
    - (d) 12 pre-existing test failures — **CARRIES FORWARD** (not introduced by sketcher; not pilot gates).
- **Trap note**: model attribution is from the spawn log line, not `OPENAI_MODEL` from `.env`. **CARRIES FORWARD** (critic must record `model` in its report field; same trap family).
- **Worktree constraint**: `architecture-recommendation.md` and `approach-comparison.md` exist ONLY in the main checkout (`/home/nea/ensemble-src/.agents/shared/planning/od-generate-agent-lane/`), NOT in the worktree (`/home/nea/ensemble-src-wt-designer-critic-orchestration/.agents/shared/planning/od-generate-agent-lane/`). The worktree contains only `stage2-addendum.md` and `parity-runs.jsonl`. Worktree-only-writes rule applies.
- **What CAN be done in the worktree**: edit `stage2-addendum.md` to add a "Superseded by `designer-critic-orchestration`" line; create a new planning-doc for this feature. Cannot copy main-checkout docs INTO the worktree without violating the worktree-only-writes rule.
- **Which `parity-runs.jsonl` rows are valid post-surgery**: the single smoke row has `lane: "sketcher"` — still valid. The schema needs a `lane` enum update (`"direct"` is no longer reachable), but the smoke row data is fine.

**Options:**

**Pilot gates — A. Mark all gates SUPERSEDED; do not delete the file.**
- *Pros:* Audit trail preserved. Pilot-mechanism history (smoke row, mechanism-proven note) stays in the planning-dir as evidence. The superseded file is the marker for "this is how we got to sketcher-only".
- *Cons:* Confusion risk for future readers who don't read the full context. The pilot-gate file becomes a "do not use" marker.

**Pilot gates — B. Mark all gates SUPERSEDED; archive the file to `archive/`.**
- *Pros:* Clear separation of in-use vs archived planning. Future readers land in `archive/` first.
- *Cons:* Adds a path; the existing `archive/` convention is for completed projects, not superseded pilots.

**parity-runs.jsonl — A. Update schema to remove `direct`; add `critic` lane column.**
- *Pros:* New pilot (sketcher+critic) tracks under the same schema. Schema is forward-compatible.
- *Cons:* Schema drift from the original schema (`stage2-addendum.md` pins exact field names). Need to record the schema evolution.

**parity-runs.jsonl — B. Archive the file; create new file for sketcher+critic pilot.**
- *Pros:* Clean separation. Old schema not modified.
- *Effort:* Two files; one row of history.

**UNTRACKED docs (architecture-recommendation.md, approach-comparison.md) — A. Copy into worktree, then supersede.**
- *Pros:* Self-contained: the worktree has all relevant docs.
- *Cons:* Violates worktree-only-writes rule (these docs originate in main checkout; copying them is read-once-from-main + write-into-worktree, but the integrity gate may reject the copy as a tracked-file edit from main → worktree direction). Architect consult adds.

**UNTRACKED docs — B. Supersede by reference only.**
- *Pros:* Worktree-only-writes honored. The planner cites the main-checkout paths in the supersede marker; future readers consult main checkout for the prior art.
- *Cons:* Cross-checkout coupling. If main checkout is rebased / archived / cleaned, the references go stale.

**UNTRACKED docs — C. Author a brief "context pointer" in the worktree that summarizes the relevant findings from the main-checkout docs, then supersede.**
- *Pros:* Self-contained for the worktree; supersedes by reference for completeness. Honors worktree-only-writes.
- *Cons:* Requires synthesis — summarising the A-orchestrate vs B+ analysis from `architecture-recommendation.md` into a planning-dir doc.

**Recommendation:**

- **Pilot gates: A** (mark SUPERSEDED; do not delete; do not archive).
- **parity-runs.jsonl: A** (update schema to remove `direct`, add `critic`; record schema evolution in a planning-dir note).
- **UNTRACKED docs: C** (author a brief context pointer in the worktree summarizing the prior art; supersede by reference).

**Reasoning:**

1. **Pilot gates**: Audit trail. The pilot was the mechanism-prove that sketcher could generate; the new feature is what to do AFTER sketcher generates. The files stay as the historical record. Add a top-line "SUPERSEDED by `designer-critic-orchestration` (2026-10-10) — pilot gates no longer apply; sketcher is the only generation lane by user directive" to `stage2-addendum.md` and to `parity-runs.jsonl` schema header.
2. **parity-runs.jsonl schema**: Add a "schema v2" note documenting the new `lane` enum (`"sketcher" | "critic"`) and remove `"direct"`. Old rows still valid (smoke row had `lane: "sketcher"`). The schema-evolution note goes in `.agents/shared/planning/designer-critic-orchestration/parity-runs-schema-v2.md`.
3. **UNTRACKED docs**: The `architecture-recommendation.md` is the load-bearing context for why sketcher exists (A-orchestrate vs B+ analysis; the 5-axis comparison). The critic addition is the next step on that analysis. A brief context pointer (~30 lines) in the worktree summarises the relevant findings; the full doc is referenced by path. Self-contained for the worktree; honors worktree-only-writes.
4. **Carries-forward items**: (a) cap-math gap carries forward unchanged; (c) `design.capture_mockup` tool gets promoted from deferred to Phase-2 scope (the critic needs the capture for visual QA); (d) 12 pre-existing test failures carry forward unchanged; trap note carries forward unchanged (critic records `model` in its report envelope).

**Architect questions:**

- **Q7.1 — Does the parity-runs.jsonl survive as the pilot-tracking file, or should a new file be created for sketcher+critic pilot?** Recommendation: same file, schema evolution. Architect confirm.
- **Q7.2 — Does the `design.capture_mockup` tool get promoted from deferred (stage2-addendum.md (c)) to Phase-2 scope of this commission?** Recommendation: YES — the critic's visual QA is exactly the use case. Architect confirm.
- **Q7.3 — Is the context-pointer doc size reasonable, or should the full `architecture-recommendation.md` be re-authored from scratch in the worktree?** Recommendation: pointer (~30 lines summary + reference by path). Architect confirm.
- **Q7.4 — Is the `_OD_GENERATE_WALL_CLOCK_CAP_S=420` cap-math gap (deferred item (a)) in scope for this commission to fix, or stays deferred?** Recommendation: stays deferred (it's a shared structural concern; fixing it is its own commission; not blocking the critic addition).

**Plan impact:**

- Phase 1 (planning-dir): new `designer-critic-orchestration/` directory contains this `open-design-decisions.md` + a `parity-runs-schema-v2.md` + a `context-pointer-architecture-recommendation.md` (the 30-line summary of the prior art).
- Phase 1 (existing planning-dir edits): add "SUPERSEDED" markers to `stage2-addendum.md` and `parity-runs.jsonl` schema header. Schema update to `parity-runs.jsonl` adds `lane: "critic"` enum.
- Phase 2 (deferred-item promotion): `design.capture_mockup` tool moves from stage2-addendum.md (c) deferred to this commission's Phase-2 scope.
- **Safe default if unresolved at build time:** **Pilot gates=A, parity-runs.jsonl=A, UNTRACKED docs=C, capture_mockup=promote, cap-math gap=stay deferred**. All decisions are additive (mark, annotate, summarise); no destructive changes to either planning-dir.

---

## Summary table

| # | Decision | Recommendation (one-liner) | Plan impact (phase + safe default if unresolved) |
|---|---|---|---|
| **D1** | od.* tool distribution post-surgery | **Sketcher holds all four; designer holds zero `od.*`** | Phase 1: meta.json one-token strip × 4 on designer; Phase 2: workflow.md re-key on both. **Safe default: B** (designer zero od.*). |
| **D2** | critic invocation mechanics | **Async `send_message` for critic** (mirrors sketcher dispatch) | Phase 2: designer Cardinal + workflow.md dispatch pattern. **Safe default: B** (async; no timeout footgun). |
| **D3** | critic verdict structure | **PASS / NEEDS-REVISION (N critical, K advisory) + structured feedback block** | Phase 1: schema pinned in planning-dir; Phase 2: critic workflow emits; designer parses; Phase 3: conformance. **Safe default: A** (the directive's own language). |
| **D4** | iteration limits | **≤3 rounds total, accept-with-disclosure OR escalate at round 3** | Phase 2: designer Cardinal + workflow.md loop section mirroring visual-QA budget. **Safe default: A** (matches existing ≤3 precedent). |
| **D5** | Cardinal #7 text-fallback interaction | **Re-key binding condition to sketcher-dispatch outcomes; keep text lane as legitimate last resort with `other:<detail>` disclosure** | Phase 2: rule.md re-key; guideline (c) re-write; workflow.md probe removed. **Safe default: A** (enum stays authoritative). |
| **D6** | critic mechanics: tools / model / spawns | **Tools = read_file + image_get + image_list + explain_image; Model = vision; Spawns = leaf (no children)** | Phase 1: critic meta.json with `tools.allow` + `llm_model: "vision"` + `team_members: []`. **Safe default: A** (no new privileges, no freeze-set changes). |
| **D7** | pilot-gate & planning-doc reconciliation | **Pilot gates superseded (file kept); parity-runs.jsonl schema evolves (add critic lane, drop direct); UNTRACKED docs referenced by context-pointer; design.capture_mockup promoted from deferred to Phase-2** | Phase 1: supersede markers + new planning-dir docs; Phase 2: capture_mockup in scope. **Safe default: A+A+C, promote capture_mockup, defer cap-math** (additive; no destructive changes). |

---

## References (into existing planning + codebase)

- **Directive context**: User directive via Discord, 2026-10-10 (designer-system rework).
- **Pilot gates in scope to supersede**: `.agents/shared/planning/od-generate-agent-lane/stage2-addendum.md`
- **Pilot runs file in scope to evolve**: `.agents/shared/planning/od-generate-agent-lane/parity-runs.jsonl` (one smoke row from 2026-10-09, `lane: "sketcher"`)
- **Prior art (main checkout only — context pointer)**: `/home/nea/ensemble-src/.agents/shared/planning/od-generate-agent-lane/architecture-recommendation.md`, `/home/nea/ensemble-src/.agents/shared/planning/od-generate-agent-lane/approach-comparison.md`
- **Designer metadata**: `agents/designer/meta.json` (tools.allow includes od.* + view-views)
- **Sketcher metadata**: `agents/sketcher/meta.json` (tools.allow includes all four od.*)
- **Sketcher Cardinal / Workflow**: `agents/sketcher/rule.md` (Cardinals #1-6, leaf), `agents/sketcher/workflow.md:42-65` (Phases 3-4), `agents/sketcher/workflow.md:80-90` (report envelope contract)
- **Designer Cardinal / Workflow**: `agents/designer/rule.md:15` (Cardinal #7 fallback enum), `agents/designer/workflow.md:105` (Phase-4 single-page rule — DEAD post-surgery), `agents/designer/workflow.md:116` (sync timeout rule), `agents/designer/workflow.md:122-129` (≤3 iterations loop)
- **od.* tool binding**: `daemon/plugin_subsystem/opendesign/ports.py:61-102`, `daemon/plugin_subsystem/port_registry.py:724-753`, `daemon/plugin_subsystem/plugin_tool_factory.py:94-99` + `:201-251`, `daemon/tools/_auth.py:35-51`
- **od.generate execute_dict**: `daemon/plugin_subsystem/opendesign/generate.py:1071`
- **od.save canonical path**: `daemon/plugin_subsystem/opendesign/save.py:67-72`
- **invoke_agent_and_wait**: `daemon/utils.py:622-774`
- **Privileged categories (view-views)**: `daemon/tools/_tool_registry.py:194-220`
- **Cap-math gap (carries forward)**: `daemon/services/llm_failover.py:848-852`

---

## Open questions for caller (architect enrichment pass)

Beyond the per-decision questions (Q1.1–Q7.4 above), the architect should validate:

1. **D1 + D2 interaction**: Does D2's async critic dispatch require any schema change to the orchestrator's report-turn handling that D1 does not anticipate?
3. **D3 + D4**: Does `accept-with-disclosure` at round 3 require a new review-status field on the spec or the page-handoff memo, or is the existing `[REVIEW-CAVEAT]` line sufficient?
4. **D5 + D6**: Does the critic's reviewer surface (read_file + image_get + explain_image) need to verify the text-lane fallback path too, or only the sketcher-generated lane?
5. **D7 + capture_mockup promotion**: Does the promotion of `design.capture_mockup` from deferred to Phase-2 change the Phase-2 task budget enough to require re-scoping?

These five open questions do not block the safe defaults; they refine the architect enrichment pass into the implementation plan.