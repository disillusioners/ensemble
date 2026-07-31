# Review: `planner[v2]` Agent Prompt & System

**Subject:** `agents/planner[v2]/` — `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`, `skills-template/*.md`
**Date:** 2026-07-31
**Status:** Review only — no changes applied
**Scope:** Two POV audit (Agent Master / system architect, and the Agent itself running a real task)

---

## POV 1 — Agent Master (system architect)

### What I like
- **Clean two-channel dispatch model is genuinely coherent.** Research → `explorer`, plan creation → `worker` with one skill, fallback = worker-no-skill. Boundary stated early (`soul.md:5-7`) and reinforced consistently. More disciplined than `developer[v2]`'s dispatch story.
- **`tools_note.md` is the best-calibrated tool doc of the v2 set so far.** Registry-validation note (`tools_note.md:66-87`) explains *why* each category is in the allow list and adds a fail-fast contract ("Adding a non-existent category is a fail-fast — preferable to silently losing a tool"). Real system thinking.
- **Four execution skills are symmetrically structured** — each has Pre-Execution Self-Check → Execution Contract → Focus Areas → Mandatory Format. A worker reading any one gets the same shape, making the dispatcher's "one skill per worker" rule enforceable.
- **`planning-strategy.md` as a self-loaded meta-skill is a smart separation.** The dispatcher keeps its own scope/research/fan-in playbook while workers get artifact-producing skills. Directly addresses the "skill-ownership boundary" theme better than developer[v2].
- **Fallback channel is explicit and named**, not implicit (`rule.md:27`, `workflow.md:69-89`, `planning-strategy.md:61`). Developer[v2] left this fuzzy.

### What I don't like
- **Heavy duplication across files — the single biggest issue.** Same content restated 3–4 times with no canonical source:
  - Skill Selection Guide table: `workflow.md:128-138`, `planning-strategy.md:51-61`, referenced from `soul.md:70`.
  - Scope tiers (TINY/SMALL/MEDIUM/LARGE/HUGE): `soul.md:51`, `workflow.md:148`, `workflow.md:250-259`, `workflow.md:264-272`, `planning-strategy.md:20-27`. Five restatements, and they **don't even agree** — `workflow.md:148` adds `TINY` while `planning-strategy.md:20` starts at `SMALL`.
  - Dispatch channel table: `soul.md:17-20`, `workflow.md:9-16`, `tools_note.md:9-54`, `planning-strategy.md` prose.
  - Two-channel code blocks are **literally copy-pasted** between `workflow.md:28-89` and `tools_note.md:11-54`.
  - "Planning Plan" output template: `soul.md:156-177` and `planning-strategy.md:122-146` near-identical.
  - END TURN rant: `soul.md:25`, `workflow.md:91-96`, `tools_note.md:56`, `planning-strategy.md` prose, `rule.md:8`.
- **`rule.md` has 30 continuously-numbered rules across 8 sections, diluting cardinal ones, and contains a literal duplicate.** `rule.md:14` ("Hand coding work back to the caller") and `rule.md:30` are the **same rule word for word**. No cardinal-vs-guidelines split — rules 1 and 29 ("Never write plans directly") are nearly identical too. "Hard" rules buried among soft style advice.
- **Version mismatches between `skill-set.yaml` and skill frontmatter — a real bug.** `skill-set.yaml` declares `planning-strategy` v1.1.0 and the four execution skills v1.2.0, but every `skills-template/*.md` frontmatter says `version: 1.0.0` (e.g. `plan-creation.md:2`, `roadmap-strategy.md:2`, `planning-strategy.md:2`). Either the loader reconciles silently (bad — which is canonical?) or it doesn't and one is dead metadata.
- **Auto-load mechanism is contradicted across files.** `meta.json:8` `innate_skills` = `["todo","chart","dynamic-skill"]` — `planning-strategy` is **not** listed. Yet `skill-set.yaml:3-7` marks `planning-strategy` `auto_load: true`, and `tools_note.md:147` *claims* "The planner's own auto-loaded skill is `planning-strategy` (see `meta.json` `innate_skills`...)" — a false cross-reference. An implementer reading `meta.json` alone would think the planner has no auto-loaded planning skill.
- **`shared_context` is allowed and documented for a use case the workflow never actually uses.** `tools_note.md:83` says `shared_context_*` is for "piping research findings to planning workers," but every dispatch example embeds findings in the `message=` string instead (`workflow.md:49-67`, `:173-221`, `tools_note.md:24-43`). Aspirational tool→use mapping; real workflow doesn't reference it. Either delete the tool or wire it in.
- **`proc` and `mcp` are in the allow list with weak justification.** `tools_note.md:74` ("Reserved for long-running helpers (rare for a planner)") and `:81` ("Auxiliary MCP servers where configured") are both "we might need it someday" entries, undermining the "intentional and minimal" claim on `:68`.
- **Hardcoded project metadata in `soul.md:136`.** `project_id="83da04de-..."` / `project_name="agents-ensemble"` baked into the agent persona. Either couples this agent to one project (bad for a shipped template) or stale copy-paste from debugging.
- **No v1→v2 migration story.** `version: 2.0.0`, `(v2)`, but nothing describes what changed vs `agents/planner/` (v1) or whether v1 plans under `.agents/shared/planning/` are still valid for the v2 reader.
- **Tone directive missing.** `soul.md:34` gives personality ("Analytical, structured, systems-thinker, progressive") but no tone guidance — how terse/formal should the planner's voice be to the caller vs. in dispatch prompts to workers?

### Improvements (master)
1. **Pick one canonical home per repeated artifact and link to it.** Scope tiers + Skill Selection Guide live **only** in `planning-strategy.md` (auto-loaded, always present); everything else links via `→ see planning-strategy §Scope`. Drop duplicated tables from `workflow.md` and `soul.md`.
2. **Split `rule.md` into "Cardinal Rules" (3–5: never write plans, never spawn coder, one skill per worker, END TURN after dispatch, research-first-when-unfamiliar) + "Guidelines".** Delete the `rule.md:14`/`rule.md:30` duplicate.
3. **Reconcile versions: one source of truth.** Either `skill-set.yaml` is canonical (delete frontmatter `version`) or vice versa. Add a lint/test asserting they match.
4. **Fix the `planning-strategy` auto-load story:** add it to `meta.json` `innate_skills` (making `tools_note.md:147` correct) OR rewrite that sentence to point at `skill-set.yaml`.
5. **Either use `shared_context` in the pipeline pattern or remove it from the allow list.** Wire it into "Research → Planning Pipeline" (`workflow.md:234-244`) or drop the tool.
6. **Add a tone directive block** (voice to caller: concise, evidence-cited; voice in dispatch prompts: imperative, self-contained).
7. **Move the hardcoded `project_id` out of `soul.md`** into config/instance metadata.
8. **Tighten the allow list:** drop `proc` and `mcp` unless there's a concrete planning use case.

---

## POV 2 — Agent Itself running a real task

*(Scenario: "Plan adding OAuth2 login to the existing session system." Scope it MEDIUM, spawn an explorer for the auth/session area, END TURN, get findings, spawn a `plan-creation` worker, END TURN, aggregate, deliver.)*

### What I like
- **The dispatch loop is unambiguous to execute.** `workflow.md:28-89` gives copy-pasteable prompts with `load_skill` in the right slot. I never have to improvise the call shape.
- **Fan-in tracking is concrete enough to actually do.** `workflow.md:104-124` shows a real `todo_graph_create(...)` with node IDs and the `todo_graph_update(..., status="done")` rhythm. For a 3-explorer + 2-worker LARGE plan I know exactly when to aggregate (`todo_view()` all done). More executable than most agent workflows.
- **Worker dispatch prompts are self-contained** — they tell the worker where to write (`.agents/shared/planning/<feature>/`), which skill, and the verbatim-report + END TURN contract.
- **Per-skill Mandatory Formats mean my aggregate output is predictable.** When `plan-creation` reports back, I know it produced `plan-overview.md` with Objective/Scope/Phases/Coupling/Risks/Success Criteria sections (`plan-creation.md:113-171`). Aggregation is stitching known shapes, not parsing freeform.
- **The fallback channel genuinely saves me** when the ask is "produce a compliance checklist" — no skill matches, dispatch a worker with no `load_skill` and a detailed prompt.

### What I don't like
- **The worker contract is inconsistent between the dispatcher prompt and the worker's own skill.** My dispatch prompt tells the worker: *"Call `skill_feedback(...)` as a TOOL CALL ONLY first, then deliver your full plan as your FINAL message and end your turn"* (`workflow.md:59-63`, repeated at `:181-185`, `:197-200`, `:213-215`). But the worker's own skill contract — `plan-creation.md:52`, `requirements-analysis.md:53`, `roadmap-strategy.md:51`, `technical-analysis.md:56` — only says *"Deliver your full deliverable as your FINAL message... End your turn"* and **never mentions `skill_feedback`**. So which contract wins? If the worker reads its own skill (likely), it may skip `skill_feedback` entirely, breaking the 1:1 attribution the whole system depends on (`rule.md:7`).
- **No escape valve when a worker/explorer never reports back.** `rule.md:24` and `workflow.md:124` say "Aggregate only when ALL nodes are done." But if one worker crashes, errors, or hangs, my `todo_view()` will forever show one node not-done and I'll never aggregate. No timeout, no partial-delivery path. For a dispatcher that exclusively fans out, a single stalled worker deadlocks the entire plan.
- **Scope calibration is fuzzy at exactly the decision point I need.** "MEDIUM = single module / feature, light research needed" (`planning-strategy.md:24`). The OAuth2 example touches auth, session, api, db — MEDIUM or LARGE? `workflow.md:148` lists TINY; `planning-strategy.md:20` starts at SMALL. "Be progressive / scope down" (`rule.md:9`) pulls one way; "Research FIRST when unfamiliar" (`rule.md:19`) pulls the other. No worked examples to anchor.
- **The pipeline heuristic is a vibe.** `workflow.md:240`: "typically the first exploration is sufficient" to start planning workers. How do I know "enough" has arrived? No signal defined. If the auth explorer returns but the session explorer hasn't, do I start the plan-creation worker (risking a plan blind to session coupling) or wait? Undefined.
- **`rule.md:6` tells me to cite research findings (file:line) for non-obvious decisions, but I only aggregate — the *workers* are the ones who cite.** I can't cite `file:line` for a plan I didn't write. Either drop this rule or redirect it to the worker dispatch prompt.
- **"Worker reuse" contradicts "one skill per worker" attribution.** `tools_note.md:135`: "a worker can be re-dispatched with a new `load_skill`." But `rule.md:7` builds 1:1 skill attribution stats on "each worker loads exactly ONE planning skill." If I reuse worker W with plan-creation then again with roadmap-strategy, is that one attribution record or two? Skill-evolution story is ambiguous about reuse.
- **The two output templates disagree on what the planner delivers.** `soul.md:179-203` (Final Plan Delivery) wants phases + research insights + risks summarized by me. But the actual content lives in the worker-written files. Am I transcribing, paraphrasing, or linking? `workflow.md:225-231` says "Stitch together... confirm the files... surface" — so I'm a summarizer, but `rule.md:29` ("never write plans directly") might forbid me writing `plan-overview.md` even though aggregation arguably requires it.
- **The END TURN discipline has no exception for the multi-instance case.** `rule.md:8` says END TURN after every dispatch. But for LARGE scope I dispatch 3 explorers + 3 workers across a pipeline — do I END TURN after each `send_message`, or after batching? Mermaid chart (`soul.md:99-114`) shows separate `EndTurn1/2/3` nodes suggesting per-dispatch; `workflow.md:171` ("Spawn 1–3 workers in parallel") implies batched then one END TURN. The rule doesn't say batching is allowed.

### Improvements (runtime)
1. **Reconcile the `skill_feedback` contract:** add the "skill_feedback tool call first, then final message" step to each skill's Execution Contract, OR drop it from the dispatch prompts. Right now the two layers disagree.
2. **Add a fan-in escape valve:** a timeout/heuristic like "if a node is not `done` after N inter-turns (or the instance reports `error`/`crashed`), mark it `failed`, deliver partial, and flag the gap in the Final Plan Delivery status." Without this, one dead worker = no plan.
3. **Add a worked scope example per tier** (especially MEDIUM-vs-LARGE boundary) with a realistic multi-module case.
4. **Define the pipeline "enough research" signal** — e.g. "start planning workers when ≥1 explorer has reported AND its findings cover the primary module touched by the first phase."
5. **Clarify the aggregator's write boundary:** may the planner write a top-level `plan-overview.md` that synthesizes worker outputs, or must that file only ever come from a `plan-creation` worker? State it explicitly in `rule.md`.
6. **Explicitly permit batched dispatch + single END TURN** for parallel fan-out, or require per-dispatch END TURN — pick one and document it.

---

## Top 3 fixes to ship first
1. **Collapse the duplication into canonical references.** Make `planning-strategy.md` the single home for scope tiers + Skill Selection Guide + output template; `workflow.md` keeps the dispatch *code* and fan-in; `soul.md` keeps identity only. Reconcile the TINY-vs-SMALL inconsistency (`workflow.md:148` vs `planning-strategy.md:20`). Highest-leverage — removes ~40% of the prompt tokens and eliminates the drift causing contradictions.
2. **Split `rule.md` into Cardinal Rules (≤5) vs Guidelines, and delete the `rule.md:14`/`rule.md:30` duplicate.** Cardinalize: never-write-plans, never-spawn-coder, one-skill-per-worker, END-TURN-after-dispatch, research-first-when-unfamiliar. Makes load-bearing rules survive context compression.
3. **Add a fan-in escape valve for stalled instances and reconcile the `skill_feedback` contract.** Without #3 the dispatcher deadlocks on any failed worker; the `skill_feedback` mismatch silently breaks the attribution system the whole skill-evolution story depends on. Both are correctness bugs, not polish.

*(Honorable 4th: fix the `meta.json`/`skill-set.yaml`/frontmatter version + auto-load contradictions — concrete bugs a test could catch.)*

---

## Open questions
- Which file is canonical for skill versions — `skill-set.yaml` or skill frontmatter? (Currently `skill-set.yaml:4` v1.1.0 vs `planning-strategy.md:2` v1.0.0.) Does the loader assert equality?
- Is `planning-strategy` auto-loaded via `meta.json` `innate_skills` (not listed) or via `skill-set.yaml` `auto_load: true`? `tools_note.md:147` claims the former — is that doc wrong or is `meta.json` missing an entry?
- May the planner itself write `.agents/shared/planning/<feature>/plan-overview.md` as an aggregate, or must that file always originate from a `plan-creation` worker? `rule.md:29` vs `workflow.md:225-231` are in tension.
- For the four execution skills declared in `planner[v2]`'s `skill-set.yaml` but loaded onto `worker` instances — do workers have read access to the planner's skill bank? If unavailable, what's the mid-dispatch fallback beyond "use the no-skill variant"?
- What terminates a planning cycle if every node except one reports `done` and the last instance is `crashed`? Daemon-level timeout, or indefinite wait per `rule.md:24`?
- Is the hardcoded `project_id`/`project_name` in `soul.md:136` intended (project-scoped planner) or leakage from debugging? Should it move to config so the v2 template is reusable?
- "Worker reuse with a new `load_skill`" — does this produce one skill-attribution record or two? How should `skill_feedback` attribute across a reused worker's multiple skill loads?
- For LARGE scope with batched parallel dispatch, is the END TURN after each `send_message` or once after the batch? `rule.md:8` reads per-dispatch; `workflow.md:171` reads batched. Which does the daemon's async-resume semantics actually require?
