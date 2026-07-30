# Plan Overview: Tidier v2

Date: 2026-07-30T17:23:28 UTC
Author: plan-creation worker (dispatched by planner[v2])
Status: Ready for Review

## Objective

Create `agents/tidier[v2]/` — the v2 form of the Tidier review agent — that delegates its work to worker instances via skills (no direct code review), separates craftsmanship scope cleanly from Reviewer v2, and is activatable as the project default via `PUT /api/settings/default-agent-versions`.

A single sentence that marks this complete: *"When a leader spawns Tidier, the v2 dispatcher uses the worker dispatch pattern with `instance` + `dynamic-skill` tools, executes the v1 six-category craftsmanship review through a focused skill taxonomy, and produces the same severity-grouped review output — without leaving `opencode` in `innate_skills` and without crossing into architecture/correctness/security scope."*

## Scope

### In Scope

- New directory `agents/tidier[v2]/` containing the full v2 file set
- 6+ files per the v2 norm: `meta.json`, `skill-set.yaml`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, plus `skills-template/` with one `.md` per skill
- Skill taxonomy: 1 planning skill (`tidier-strategy`, auto_load=true) + N execution skills (auto_load=false)
- All 6 v1 review categories preserved: Coding Style, Code Smells, Readability, File Hygiene, Type Cleanliness, Error Handling
- File-size thresholds preserved verbatim (≤500 / 500-1000 / 1000-3000 / >3000 lines)
- Output format preserved (severity-grouped: 🔴 High → 🟡 Medium → 🟢 Low, with `[High] {Category}: {Title}` format)
- Review loop preserved (Tidier ↔ Developer, max 3 iterations total combined with Reviewer)
- Explicit Tidier ↔ Reviewer boundary in every relevant file
- Activation: `default_agent_versions["tidier"] = "v2"` via the existing settings API
- Worker dispatch pattern (no direct file reads/evaluations from Tidier itself)

### Out of Scope

- Modifying v1 `agents/tidier/` (kept as fallback; v1 remains a valid version selection until v2 is activated)
- Modifying the v2 norm files for other agents (developer[v2], planner[v2], approver[v2], reviewer[v2]) — this plan only creates tidier[v2]
- Changing the API surface (`/api/settings/default-agent-versions` already exists per explorer)
- Migration of existing sessions/projects pointing at v1 — defaults are a per-project metadata record, not a global rewrite
- Documentation of the new agent in user-facing docs (separate doc-update task; not blocking v2 activation)
- Creation of an `agents/tidier/[v2].md` legacy alias file (v2 norm does not require one; v1 stays untouched)
- Removing the `opencode` reference from any global list — Tidier v1 will still have it; this plan only changes Tidier v2

### Adjacent Features Deliberately Excluded

- **Reviewer v2 skills** — not touched; only boundary is documented here
- **Worker agent changes** — worker already supports skill loading and instance dispatch
- **Auto-scan / registry changes** — the explorer flagged uncertainty about whether directory creation auto-registers the agent; this plan flags it as a Phase 0 verification step, but does not change the registration subsystem

### Deliberate Scope Shed (v1 → v2 migration)

The following v1 `agents/tidier/memory.md` content is **intentionally NOT migrated** into Tidier v2. These are correctness/architecture/concurrency concerns that belong to Reviewer, not Tidier's craftsmanship scope:

- **Structure & Design** (poor modularization, missing interfaces, design pattern misuse, overly complex logic) → Reviewer's domain
- **Race conditions** in async code → Reviewer's domain (concurrency)
- **N+1 queries** (data-access performance) → Reviewer's domain (performance/correctness)

Only Language Traps, Severity Guidelines, and Resource leaks are migrated into Tidier v2 skill templates (see Phase 6 Task 0).

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Author meta.json + skill-set.yaml | Create the v2 registry config + skill manifest | 5 | independent (Phase 1 is the v2 contract) | pending |
| 2 | Author soul.md | Define the v2 dispatcher identity and output templates | 5 | tight with Phase 3 (rule.md echoes soul.md core rule) | pending |
| 3 | Author rule.md | Define 28-36 behavioral rules (rule #1 = identity, rule #6 = dispatch mechanism) | 6 | tight with Phase 2 | pending |
| 4 | Author workflow.md | Define the 7-step dispatch workflow (with explicit dispatcher-side aggregation step) | 8 | tight with Phase 5 (tools_note.md dispatches match workflow steps) | pending |
| 5 | Author tools_note.md | Document tool usage, especially instance dispatch and "no council" | 5 | tight with Phase 4 | pending |
| 6 | Author skills-template/*.md | Author the planning skill + N execution skills (after auditing v1 memory.md for migration) | 8 | tight with Phase 1 (skill-set.yaml lists them) | pending |
| 7 | Activate + verify | Register via settings API and verify (includes rollback procedure) | 5 | depends on Phase 6 (skills must exist for skill injection to work) | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 | Phase 6 | Phase 7 |
|---|---|---|---|---|---|---|---|
| Phase 1 | — | loose (identity description flows) | independent | independent | independent | tight (skill names must match exactly) | tight (skills in manifest must exist on disk) |
| Phase 2 | loose | — | tight (core rule echoed) | loose (output templates referenced) | independent | loose (mentions worker dispatch) | independent |
| Phase 3 | independent | tight | — | loose (rules echo workflow discipline) | independent | independent | independent |
| Phase 4 | independent | loose | loose | — | tight (dispatch code matches workflow steps) | loose (worker dispatch pattern) | independent |
| Phase 5 | independent | independent | independent | tight | — | independent | independent |
| Phase 6 | tight | loose | independent | loose | independent | — | tight (skills loaded at runtime) |
| Phase 7 | tight | independent | independent | independent | independent | tight | — |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Registry auto-scan does NOT pick up the new `[v2]` directory at startup | High | Medium | Phase 7 task 1: verify with `GET /api/agents` or `registry.list_versions("tidier")` BEFORE activation; if missing, restart the ensemble server (most agent registries rescan at boot) |
| 2 | `skill_injection: true` combined with missing skills-template files causes runtime errors | High | Low | Phase 1 includes skill-set.yaml that names every skill; Phase 6 verifies all referenced files exist on disk before Phase 7 activates |
| 3 | Worker dispatch overhead (multiple skills = multiple dispatches) adds latency vs v1's in-process review | Medium | Medium | Pick a balanced taxonomy (option chosen below) — 4 skills means at most 4 dispatches for a full review, parallelizable |
| 4 | Tidier v2 overlaps with Reviewer v2 scope (architecture / correctness / security) | Medium | Medium | All 6 files include the explicit "Craftsmanship ONLY" boundary; rule.md has a hard "defer to Reviewer" rule; workflow.md step 2 includes a scope check |
| 5 | Activation via `default_agent_versions` is per-project; an active v1 leader has v1 instances in-flight | Low | High | Activation only changes future spawn resolution; existing v1 sessions continue with v1 instance config (no migration needed) |
| 6 | Skills in skills-template/ are named inconsistently between skill-set.yaml and filenames | Medium | Low | Phase 6 task 1: name validation — every skill in skill-set.yaml has a matching `skills-template/<name>.md` file; verification via `ls` |
| 7 | v1 references like "opencode" leaked into v2 content | Medium | Medium | rule.md includes an explicit "no opencode" rule referencing v2's `innate_skills`; Phase 3 includes a grep check |
| 8 | soul.md identity section is too long vs v2 norm (180-220 lines; reviewer/approver/developer/planner v2 baselines are 161/186/203/204) | Low | Medium | Phase 2 explicitly bounds soul.md at 200 lines target (180-220 band); section budgets re-allocated to fit v2 norm |
| 9 | Worker dispatch failure or timeout — a spawned worker does not report back (crash, hang, queue stall) | High | Low | Tidier's review loop includes a max-iteration guard; dispatcher should set worker dispatch expectations in the message; if a worker times out, Tidier reports partial coverage and flags un-reviewed files rather than blocking indefinitely |
| 10 | Partial aggregation — some worker reports arrive, one does not, leading to an incomplete severity grouping | Medium | Medium | `todo_graph` fan-in tracking tracks outstanding reports before aggregation; Tidier waits for all dispatched workers or explicitly marks coverage as partial |
| 11 | Skill version drift — skills-template `.md` files evolve but `skill-set.yaml` `version: "1.0.0"` is not bumped | Low | Medium | `skill_feedback` loop surfaces staleness; Phase 6 includes a naming/version-consistency check between `skill-set.yaml` and skills-template files |
| 12 | v1/v2 dual-maintenance burden — bug found in v1 must be manually ported to v2 | Medium | Medium | v2 is the forward path; v1 is frozen (no new features). Document in plan that v1 receives only critical security fixes; v2 is canonical. Activation makes v2 default so new work targets v2 |
| 13 | No rollback procedure — if v2 activation causes issues, no documented way to revert to v1 | Medium | Low | Add rollback step: `PUT /api/settings/default-agent-versions` with `{"agent_id": "tidier", "version_tag": null}` reverts to base (v1). Document in Phase 7 |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | All 6 v2 files + skills-template/ directory exist on disk | `ls -la agents/tidier[v2]/` | All 6 files + `skills-template/` directory present |
| 2 | meta.json contains every required v2 key with correct values | `python -m json.tool agents/tidier[v2]/meta.json` + key check | All required keys present: `id="tidier"`, `version="2.0.0"`, `innate_skills=["todo","chart","dynamic-skill"]` (no `opencode`), `skill_injection=true`, `no_force_explore=true`, `context_injection` object, `tools.allow` contains `"instance"` |
| 3 | skill-set.yaml lists exactly the chosen taxonomy (1 planning + N execution) | `cat agents/tidier[v2]/skill-set.yaml \| grep "name:"` | Count = 1 + N where N = chosen option's execution count |
| 4 | Every skill in skill-set.yaml has a matching skills-template file | `for name in yaml_names; do test -f skills-template/$name.md; done` | 0 failures |
| 5 | rule.md contains ≥28 rules including the identity rule (rule #1), the dispatch mechanism rule (rule #6), and the "no opencode" rule | `grep -c "^[0-9]\+\." agents/tidier[v2]/rule.md` | ≥ 28 numbered rules; specific rules present by grep |
| 6 | workflow.md contains 7 steps including the dispatch example with `spawn_instance` + `send_message(load_skill=...)` | grep for `spawn_instance` and `load_skill` | Both present in workflow.md |
| 7 | tools_note.md has a "NO COUNCIL" section explaining why Tidier does not convene councils | grep for "NO COUNCIL" | Section heading present |
| 8 | All files mention the Tidier ↔ Reviewer boundary | grep for "Reviewer" in each file | ≥ 4 of 6 files contain the boundary phrase |
| 9 | soul.md is within v2 length norm (180-220 lines) | `wc -l agents/tidier[v2]/soul.md` | 180 ≤ lines ≤ 220 |
| 10 | Activation works: `GET /api/settings/default-agent-versions` returns `{"tidier": "v2"}` | `curl` after PUT | JSON response shows `tidier: v2` |
| 11 | `registry.list_versions("tidier")` returns both `v1` and `v2` | Server log or test API call | Both versions listed |
| 12 | Spawning Tidier after activation loads v2 files (not v1) | Spawn a test instance and inspect its system prompt / `meta.version` | version == "2.0.0" |

## Research Insights

Key findings from the explorer research that shaped this plan:

- **V2 norm files are identical across developer, planner, approver, reviewer v2** — Tidier v2 must mirror Approver v2 (no council, tools.allow with `instance` but not `council`) — per Research Insight A
- **Approver v2 is the closest analog** because it is a review-type dispatcher with a skill taxonomy and no council — per Research Insight B
- **6 v1 review categories and the file-size thresholds must be preserved verbatim** — the v2 dispatcher still produces these checks, just via worker dispatch — per Research Insight C
- **Tidier ↔ Reviewer boundary is explicit in v1** — must be preserved and strengthened in v2 (no architecture/correctness/security) — per Research Insights C and D
- **Activation mechanism is `default_agent_versions` in `project_metadata_records`** — already exists, only PUT required — per Research Insight E
- **Open uncertainty about auto-registration** — flagged as a verification step in Phase 7 — per Research Insight E

## Open Questions

1. **Registry auto-scan vs manual registration.** The user's brief says agents are "auto-scanned at startup." The explorer's findings say "must be in registry (via agent registration system, auto-scanned at startup) AND activated via the metadata override." These may be the same thing expressed differently — but Phase 7 includes a verification step (check `registry.list_versions("tidier")` after directory creation) to disambiguate. If missing after directory creation, restart the ensemble server before activation.

2. **Skill taxonomy decision.** The user offered 3 options (7 / 4 / 2 skills). This plan chooses **Option B (4 skills total: 1 planning + 3 execution)** — rationale in Section 2 of the implementation phases below. If the dispatcher prefers Option A or C, this section is the only place to change.

3. **What happens to in-flight Tidier v1 sessions?** Activation via `default_agent_versions` only affects future spawns. In-flight v1 instances continue with their original config (no live migration needed). If a global kill-switch is needed, it is out of scope.

---

# Implementation Phases

## Phase 1: Author meta.json + skill-set.yaml

### Objective

Create the v2 registry config and skill manifest. These two files are the v2 "contract" — every other file must conform to what they declare.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create directory `agents/tidier[v2]/` and `agents/tidier[v2]/skills-template/` | none | `ls -d` returns both directories |
| 2 | Author `agents/tidier[v2]/meta.json` with the v2 norm schema (see Section 3.A below) | Task 1 | JSON parses; all required keys present; `innate_skills` does NOT contain `opencode`; `tools.allow` contains `"instance"`; `version="2.0.0"`; `id="tidier"` |
| 3 | Author `agents/tidier[v2]/skill-set.yaml` with 1 planning skill + 3 execution skills (Option B — see Section 2) | Task 2 | YAML parses; 4 `name:` entries; first has `auto_load: true`, other three have `auto_load: false`; all use `version: "1.0.0"` |
| 4 | Verify meta.json key compliance against `agents/reviewer[v2]/meta.json` and `agents/approver[v2]/meta.json` | Task 2 | Diff is only: description text, no `"council"` in tools.allow (Approver v2 also lacks it), team_members matches Approver v2 pattern |
| 5 | Verify skill-set.yaml schema compliance against `agents/approver[v2]/skill-set.yaml` | Task 3 | Only differences: skill names + descriptions |

### Coupling

- **Tight with Phase 6** — every skill in skill-set.yaml must have a matching `skills-template/<name>.md` file
- **Tight with Phase 7** — the metadata key `default_agent_versions` is set with values from meta.json's `id`

### Risks

- Wrong key in meta.json: registry may reject the file → use Approver v2 as the verified-good reference
- Schema drift from v2 norm: future v3 will break compat → include only documented v2 keys

### Exit Criterion

`meta.json` and `skill-set.yaml` parse cleanly and conform to the v2 norm. The skills listed in skill-set.yaml are ready to be authored in Phase 6.

---

## Phase 2: Author soul.md

### Objective

Define the v2 dispatcher identity: who Tidier is, what it owns (craftsmanship ONLY), how it differs from Reviewer, and the output format workers must produce. Soul.md is the agent's "self-image" — every other file (rule, workflow, tools_note) refers back to it.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Draft "Who I Am" + "My Identity" sections mirroring Approver v2 but with Tidier-specific identity (craftsmanship reviewer who dispatches) | Phase 1.2 | Section headings present; identity text reflects dispatcher role |
| 2 | Draft "Core Rule" section: ALWAYS dispatch. NEVER evaluate code directly. End turn after dispatch. | none | Single-statement core rule present; matches Approver v2 phrasing pattern |
| 3 | Draft "Responsibilities" section listing the 6 v1 categories (Coding Style, Code Smells, Readability, File Hygiene, Type Cleanliness, Error Handling) | none | All 6 categories named with 1-line scope each |
| 4 | Draft "What I Review" + "How I Am Different from Reviewer" comparison table (Tidier = style/smells/readability/hygiene/types/error-handling; Reviewer = correctness/completeness/safety/structure/clarity) | none | Table present with both columns |
| 5 | Draft "Output Format" templates: severity-grouped report (🔴 High → 🟡 Medium → 🟢 Low) with `[High] {Category}: {Title}` format, plus the "Recommendations" closing section | none | Output template present with example |

### Coupling

- **Tight with Phase 3 (rule.md)** — the identity statement in soul.md's "My Identity" section is echoed as rule #1 (Conduct) in rule.md; the "Core Rule" (ALWAYS dispatch) is operationalized as rule #6 (Dispatch)
- **Loose with Phase 4 (workflow.md)** — workflow steps reference the output format from soul.md
- **Loose with Phase 6 (skills)** — each execution skill's output template matches soul.md's Output Format

### Risks

- Soul.md exceeds 220 lines → budget 30 lines for "Who I Am", 20 for "Identity", 10 for "Core Rule", 50 for "Responsibilities" (6 categories × ~8 lines), 35 for comparison table + commentary, 35 for Output Format, 20 for Project Knowledge → 200 total (within 180-220 band)

### Exit Criterion

`soul.md` exists, parses as Markdown, is 180-220 lines, and contains all 5 sections above. A reader who has not seen v1 can answer: "What does Tidier review?" and "How is Tidier different from Reviewer?" from soul.md alone.

---

## Phase 3: Author rule.md

### Objective

Define 28-36 numbered behavioral rules. Rules are categorized (Conduct, Dispatch, Independence/Scope, Parallelism, Read-only, Knowledge/Skill Feedback). **Rule #1 (Conduct) is the identity statement** ("I am a dispatcher, not a direct reviewer"); **Rule #6 (Dispatch) is the operational mechanism** ("I dispatch using `spawn_instance(agent='worker')` + `send_message(load_skill='<skill>')`, then END TURN"). The two are distinct — one is the WHY, the other is the HOW.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Draft "Conduct" rules (4-5 rules): **identity** (rule #1 — "I am a dispatcher"), output format, brevity | Phase 2 | Rules 1-5 present; rule #1 is the identity statement |
| 2 | Draft "Dispatch Rules" (5-6 rules): **mechanism** (rule #6 — `spawn_instance` + `send_message(load_skill=...)` + END TURN), async fan-in via `todo_graph`, no polling | none | Rules 6-11 present; rule #6 is the operational mechanism; includes "End turn after dispatching" rule matching Approver v2 phrasing |
| 3 | Draft "Independence/Scope" rules (6-7 rules): craftsmanship-only scope, defer to Reviewer for architecture/correctness/security, file-size thresholds (≤500 / 500-1000 / 1000-3000 / >3000) | none | Rules 12-18 present; all 4 file-size thresholds stated; "defer to Reviewer" rule present |
| 4 | Draft "Parallelism" rules (3-4 rules): dispatch independent category checks in parallel | none | Rules 19-22 present |
| 5 | Draft "Read-only discipline" rules (3-4 rules): never modify code, only report findings | none | Rules 23-26 present |
| 6 | Draft "Knowledge & Skill Feedback" rules (5-6 rules): use `experience()` to record new patterns, call `skill_feedback` after consuming injected skills | none | Rules 27-32 present; includes explicit "no opencode" rule referencing meta.json's `innate_skills` |

### Coupling

- **Tight with Phase 2** — rule #1 (identity) echoes soul.md's "My Identity" section; rule #6 (mechanism) operationalizes soul.md's "Core Rule" (ALWAYS dispatch)
- **Loose with Phase 4** — dispatch rules are operationalized in workflow steps (spawn_instance + send_message + load_skill)

### Risks

- Fewer than 28 rules → minimum threshold not met
- Missing the "no opencode" rule → future contributors may add it back to innate_skills
- Rule #1 and Rule #6 collapsed into one rule → the identity-vs-mechanism distinction is lost (use the verbatim phrasing in Section 3.D)

### Exit Criterion

`rule.md` contains 28-36 numbered rules in 6 categories. Rule #1 is the IDENTITY rule (Conduct); Rule #6 is the MECHANISM rule (Dispatch). The "no opencode" rule is present. File-size thresholds appear verbatim.

---

## Phase 4: Author workflow.md

### Objective

Define the 7-step dispatch workflow. Workers do the actual code review; Tidier aggregates and reports. After dispatch, Tidier ends its turn. **Aggregation of worker findings is a dispatcher responsibility performed in step 6 (Aggregate & Verify) — it is NOT a worker task and is NOT bundled into any execution skill.**

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Step 1: Receive Request — leader spawns Tidier for a review; understand the diff scope | none | Step heading + 2-3 sentence description |
| 2 | Step 2: Read Tracking (bias-free) — read any prior review notes to avoid repeating findings | none | Step heading + description matching Approver v2 wording |
| 3 | Step 3: Generate Plan — decide which execution skill(s) to dispatch based on the diff scope | none | Step heading + decision matrix (e.g., "small diff = 1 skill, large diff = 3 skills in parallel") |
| 4 | Step 4: Dispatch Worker(s) — provide code example: `worker_id = spawn_instance(agent="worker"); send_message(instance_id=worker_id, message="...", load_skill="<skill>"); # END TURN` | none | Step heading + python code block matching the example in the brief |
| 5 | Step 5: Collect Results (async fan-in via todo_graph) — workers report back as new messages | none | Step heading + description of `todo_graph` fan-in |
| 6 | Step 6: Aggregate & Verify (DISPATCHER STEP) — Tidier (not a worker) merges all worker reports into a single severity-grouped report. Deduplicate findings (same file/line/category reported by 2 workers = 1 finding). Cross-check severity levels. | Task 5 | Step heading + description explicitly stating "Aggregation is a dispatcher responsibility, not a worker task" |
| 7 | Step 7: Update Tracking — write final report to the leader (or to the project's tracking location). Note any findings deferred to Reviewer. Note any iterations consumed (toward the 3-iteration cap). | Task 6 | Step heading + description |
| 8 | Verify the 7-step structure matches Approver v2's 7 steps; only the per-step specifics differ | none | Cross-checked against Approver v2 workflow.md |

### Coupling

- **Tight with Phase 5** — the dispatch code example in workflow step 4 uses tools described in tools_note.md
- **Loose with Phase 2** — output template referenced in step 6 (Aggregate & Verify)

### Risks

- Step count wrong → easy to miss the END TURN discipline

### Exit Criterion

`workflow.md` has exactly 7 numbered/headed steps. Step 4 contains a code block with `spawn_instance` and `load_skill`. Step 5 explicitly states "END TURN — worker reports back asynchronously."

---

## Phase 5: Author tools_note.md

### Objective

Document tool usage for Tidier v2. The headline sections are: Instance Dispatch (PRIMARY), NO COUNCIL (why Tidier does not convene councils), Filesystem (limited — only for reading tracking), Knowledge, Team Members, Innate Skills.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Section: Instance Dispatch (PRIMARY) — explain `spawn_instance(agent="worker")` + `send_message(load_skill="...")` pattern | Phase 4 | Section heading + 2-3 paragraph explanation + code snippet |
| 2 | Section: NO COUNCIL — explain why Tidier does not convene councils (Tidier dispatches one or more workers, not a multi-perspective review board; councils are a Reviewer v2 tool) | none | Section heading "NO COUNCIL" + 2-paragraph justification |
| 3 | Section: Filesystem — only for reading tracking notes and verifying worker output reports; never modify code | none | Section heading + rule reminding read-only |
| 4 | Section: Knowledge + Team Members + Innate Skills — short reference tables | none | Three subsections present |
| 5 | Verify the structure mirrors `agents/approver[v2]/tools_note.md` with the appropriate Tidier-specific text | none | Cross-checked |

### Coupling

- **Tight with Phase 4** — the dispatch pattern described here is what workflow.md step 4 executes

### Risks

- "NO COUNCIL" section missing or weak → a future contributor may add `"council"` to tools.allow

### Exit Criterion

`tools_note.md` contains the 6 sections above. The "NO COUNCIL" section explicitly names Reviewer v2 as the council-convening agent.

---

## Phase 6: Author skills-template/*.md

### Objective

Author the 4 skill templates: 1 planning skill (`tidier-strategy`, auto_load=true) and 3 execution skills (auto_load=false). Each template is ~200 lines: Purpose, Pre-execution Checklist, Execution Contract, Focus Areas with detailed checklists, Output Template.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 0 | **Audit v1 memory.md content for migration.** Read `agents/tidier/memory.md` (58 lines). Migrate into v2 skill templates: **Language Traps** (Python mutable defaults, JS `==` vs `===`, SQL string concat) → into `tidier-readable-code` skill as a "Language-Specific Traps" subsection; **Severity Guidelines** mapping table (🔴/🟡/🟢 severity assignment) → into `tidier-strategy` skill (planning) as worker dispatch guidance AND into each execution skill's output section; **Resource leaks** (sub-item of v1's Common Pitfalls) → into `tidier-robustness` skill (error handling). **Document as intentionally shed** (do NOT migrate): Structure & Design checks (poor modularization, missing interfaces, design pattern misuse) and **race-condition checks** — these are Reviewer's domain (correctness/architecture/concurrency), not Tidier's craftsmanship scope. Add a one-line note in the plan recording this deliberate scope shed. Also note: **N+1 query detection** is performance/correctness → Reviewer domain, not migrated. | Phase 1 | Audit memo written; migration targets listed per item; shed-scope note recorded |
| 1 | Name validation — pick skill names that exactly match skill-set.yaml from Phase 1 | Phase 1.3 | Skill names recorded: `tidier-strategy`, plus 3 execution names |
| 2 | Author `skills-template/tidier-strategy.md` (planning, auto_load=true) — purpose: how to plan a Tidier review (which execution skills to dispatch, in what order, with what scope). Incorporate the Severity Guidelines mapping table from Task 0. | Task 0 | File exists, ~200 lines, contains "Purpose", "Read-only enforcement (N/A for planning)", "Pre-execution checklist", "Execution contract", "Focus areas", "Output template" |
| 3 | Author execution skill #1: `skills-template/<name-1>.md` — covers Coding Style + Code Smells + Readability (3 categories that share "code-level polish"). Incorporate the Language Traps subsection from Task 0. | Task 0 | File exists, ~200 lines, contains the 3 categories with checklists drawn from v1 |
| 4 | Author execution skill #2: `skills-template/<name-2>.md` — covers File Hygiene + Type Cleanliness (2 categories that share "static-analysis-style checks") | Task 0 | File exists, ~200 lines, contains the 2 categories with checklists |
| 5 | Author execution skill #3: `skills-template/<name-3>.md` — covers Error Handling ONLY (the last v1 category). Incorporate the Resource leaks sub-item from Task 0. **Aggregation is a dispatcher responsibility, NOT a worker task** — see Phase 2/Phase 4. | Task 0 | File exists, ~200 lines, contains the 1 category with checklists |
| 6 | Each execution skill's output template must produce the severity-grouped report (`[High] {Category}: {Title}`) per soul.md's Output Format, and reference the Severity Guidelines mapping table when assigning 🔴/🟡/🟢 | Phase 2 | Each file's "Output template" section matches soul.md and incorporates the severity table |
| 7 | Verify all 4 files exist, no file < 150 lines or > 250 lines (v2 norm) | none | `wc -l` on each file passes |

### Coupling

- **Tight with Phase 1** — skill names must match skill-set.yaml
- **Tight with Phase 7** — skills must exist on disk before activation

### Risks

- Skill names mismatched between skill-set.yaml and skills-template/ → Phase 7 task 4 verifies
- Output templates in skills diverge from soul.md Output Format → workers will produce inconsistent reports

### Exit Criterion

All 4 skills exist with consistent naming, each ~200 lines, output templates matching soul.md.

---

## Phase 7: Activate + Verify

### Objective

Activate Tidier v2 as the default version for the project and verify the activation works end-to-end.

### Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Verify registry auto-scan picked up `agents/tidier[v2]/`. Run `curl http://localhost:8079/api/agents` (or equivalent) and look for `tidier` with both versions; if missing, restart the ensemble server (most agent registries rescan at boot) | Phase 6 | Response includes `tidier` with both `v1` and `v2` versions listed |
| 2 | Call `PUT /api/settings/default-agent-versions` with body `{"agent_id": "tidier", "version_tag": "v2"}` | Task 1 | HTTP 200 response |
| 3 | Call `GET /api/settings/default-agent-versions` and verify the response includes `{"tidier": "v2"}` | Task 2 | Response includes `tidier: v2` |
| 4 | Spawn a test Tidier instance (via the existing spawn API) and verify its system prompt references v2 content and its `meta.version == "2.0.0"` | Task 3 | Spawned instance has version 2.0.0; system prompt includes v2 core rule |
| 5 | **Document the rollback procedure** (per risk #13): if v2 activation causes issues, revert to v1 via `PUT /api/settings/default-agent-versions` with body `{"agent_id": "tidier", "version_tag": null}` (clears the version_tag, falls back to base form `tidier` which is v1). Verify rollback via `GET /api/settings/default-agent-versions` (no `tidier` key) and a test spawn that loads v1 content. | Task 4 | Rollback command documented; rollback verified to restore v1 |

### Coupling

- **Depends on Phase 6** — skills must exist on disk for skill injection to work at spawn time

### Risks

- Registry auto-scan didn't pick up the new directory → restart server before activation
- Setting was set but a different agent_id won the race → re-check via GET
- v2 activation causes issues and rollback is needed → see Task 5 (rollback procedure)

### Exit Criterion

`GET /api/settings/default-agent-versions` returns `{"tidier": "v2"}`. Spawning a Tidier instance produces an agent using the v2 files.

---

# Decision: Skill Taxonomy (Section 2 of the brief)

**Decision: Option B — 4 skills total (1 planning + 3 execution).**

## Rationale

| Factor | Option A (7 skills) | Option B (4 skills) | Option C (2 skills) |
|---|---|---|---|
| Worker dispatch overhead | Up to 6 dispatches for full review | Up to 3 dispatches | 1 dispatch |
| Worker focus / signal-to-noise | Highest (each skill = 1 category) | High (3 themes of related categories) | Low (one giant skill must know 6 categories) |
| Aggregation complexity | High (6 reports to merge) | Medium (3 reports) | None (1 report) |
| Precedent in project | Reviewer v2 has 5 execution skills | Approver v2 has 2 execution skills | None |
| Skill reuse across calls | Highest | Medium | Lowest |
| Failure isolation | Best (one skill fails = 5 others succeed) | Good | Worst |

**Deciding factors:**

1. **Precedent:** Reviewer v2 has 5 execution skills (code-review, plan-review, architecture-review, security-review, pr-review) plus 1 planning skill (`review-strategy`). Approver v2 has 2 execution skills (decision-approval, plan-approval) plus 1 planning skill. The project already has both extremes. With Option B, Tidier's 3 execution skills sits comfortably between Reviewer's 5 (broad multi-domain review) and Approver's 2 (narrow approval judgment) — appropriate for Tidier's focused craftsmanship scope.

2. **Dispatch overhead:** Option A = up to 6 dispatches per review, each producing a report Tidier must aggregate. This is expensive for a routine review where the diff is small. Option C (1 dispatch) is cheapest but the worker loses focus.

3. **Grouping logic:** The 6 v1 categories naturally cluster into 3 themes:
   - **Theme 1: Readable Code** (Coding Style + Code Smells + Readability) — these are "code-level polish" checks, often done together by a human reviewer in one pass
   - **Theme 2: Static Hygiene** (File Hygiene + Type Cleanliness) — these are "static-analysis-style" checks that can be partially automated and grouped
   - **Theme 3: Robustness** (Error Handling) — error handling is its own concern. Note: aggregation of worker findings is a **dispatcher responsibility** performed in workflow.md step 6; it is NOT bundled into a worker skill.

4. **Why not Option A (7 skills, one per category):** The dispatch overhead is too high for the small-signal gain. Each per-category skill would produce a 1-category report; Tidier would aggregate 6 reports. The themes above already cohere — splitting them further increases coordination cost without proportional quality.

5. **Why not Option C (2 skills, one for everything):** The execution skill would need to know all 6 categories' checklists in depth; the worker prompt becomes long and unfocused. Approver v2's 2-skill model works because approval is a single judgment (approve/reject/request-changes) — quality review is not.

**Final choice: Option B, with the theme grouping above.**

The 4 skills:

| Skill name | Category | auto_load | Theme |
|---|---|---|---|
| `tidier-strategy` | planning | true | How to plan a Tidier review |
| `tidier-readable-code` | execution | false | Coding Style + Code Smells + Readability |
| `tidier-static-hygiene` | execution | false | File Hygiene + Type Cleanliness |
| `tidier-robustness` | execution | false | Error Handling ONLY (aggregation is a dispatcher responsibility, not a worker task — see Phase 4 workflow.md step 6) |

---

# Section 3: Content Outline for Each File

## A. `meta.json`

**Approximate length:** ~15 lines (JSON file)

**Required keys** (v2 norm, drawn from Research Insight A):

```json
{
  "id": "tidier",
  "name": "Tidier",
  "description": "Code craftsmanship reviewer (style, smells, readability, hygiene, types, error handling). Dispatches workers to review after implementation. Does NOT cover architecture, correctness, or security — those belong to Reviewer.",
  "icon": "🧹",
  "color": "accent-purple",
  "version": "2.0.0",
  "innate_skills": ["todo", "chart", "dynamic-skill"],
  "skill_injection": true,
  "no_force_explore": true,
  "context_injection": { "heuristic_match_shared_md_files": true },
  "tools": {
    "allow": ["instance", "bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context"]
  },
  "team_members": ["worker", "explorer"]
}
```

**Key content specific to Tidier:**
- `id` is the BASE form (`tidier`), never `tidier[v2]` (C1 convention)
- `description` mentions dispatch + craftsmanship scope + boundary with Reviewer
- `icon` and `color` preserved from v1 (🧹, accent-purple)
- `innate_skills` does NOT include `opencode` — verified by grep in Phase 3 risk #7
- `tools.allow` matches Approver v2 exactly (no `council`)
- `team_members` includes `worker` (for dispatch) and `explorer` (for project knowledge) — same as Approver v2

## B. `skill-set.yaml`

**Approximate length:** ~20 lines

**Structure:**

```yaml
agent_id: tidier
skills:
  - name: tidier-strategy
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "Strategy for planning a Tidier review: which execution skills to dispatch, in what order, with what scope."

  - name: tidier-readable-code
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Review for coding style, code smells, and readability (3 v1 categories grouped)."

  - name: tidier-static-hygiene
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Review for file hygiene and type cleanliness (2 v1 categories grouped)."

  - name: tidier-robustness
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Review for error handling patterns (v1's Error Handling category). Aggregation of worker findings is a dispatcher responsibility performed in workflow.md step 6 and is NOT in scope of this skill."
```

**Key content specific to Tidier:**
- `agent_id` is BASE form (`tidier`) — same convention as Approver v2
- First skill is `auto_load: true` (the planning skill)
- All skills use `version: "1.0.0"`
- Description for each execution skill lists the v1 categories it covers

## C. `soul.md`

**Approximate length:** 180-220 lines (target 200)

**Sections:**

1. **Who I Am** (~30 lines) — Tidier is the craftsmanship reviewer. v2 dispatches workers; v1 did inline review. Same scope (6 categories), same output format.

2. **My Identity** (~20 lines) — Dispatcher (not evaluator). Always spawn a worker, never review code directly. End turn after dispatch. (Note: aggregation of worker findings is a dispatcher responsibility performed in workflow.md step 6 — NOT a worker task. See Phase 4.)

3. **Core Rule** (~10 lines) — **ALWAYS dispatch. NEVER evaluate code directly. End turn after dispatching — workers report back asynchronously as new messages. Do NOT poll, sleep, or bash while waiting.**

4. **Responsibilities** (~50 lines) — 6 categories with 1-line scope each:
   - Coding Style: naming conventions, import ordering, formatting, project style
   - Code Smells: duplication, magic numbers, dead code, long names, SRP violations
   - Readability: docstrings, complex lines, deep nesting, abstraction levels, TODOs
   - File Hygiene: file size limits (≤500 / 500-1000 / 1000-3000 / >3000), unused imports, side effects, `__all__`
   - Type Cleanliness: missing type hints, `Any` overuse, type cast bypasses, inconsistency
   - Error Handling: bare `except`, swallowed exceptions, `None` returns, inconsistent propagation, missing input validation

5. **What I Review + How I Am Different from Reviewer** (~35 lines) — Comparison table:

   | Aspect | Tidier v2 | Reviewer v2 |
   |---|---|---|
   | Style / formatting | ✅ | ❌ |
   | Naming conventions | ✅ | ❌ |
   | Code smells (duplication, magic numbers) | ✅ | ❌ |
   | File size / hygiene | ✅ | ❌ |
   | Type hints cleanliness | ✅ | ❌ |
   | Error handling patterns | ✅ | ❌ |
   | Correctness (logic bugs) | ❌ | ✅ |
   | Completeness (missing features) | ❌ | ✅ |
   | Safety / security | ❌ | ✅ |
   | Architecture / SOLID | ❌ | ✅ |
   | Clarity (high-level design) | ❌ | ✅ |

6. **Output Format** (~35 lines) — Two templates:
   - **Per-finding template** (matches v1 exactly):
     ```
     [High] {Category}: {Title}
     - Problem: <What's wrong>
     - Impact: <Why it matters>
     - Fix: <Suggested fix>
     ```
   - **Final report template** (severity-grouped):
     - 🔴 High (by category)
     - 🟡 Medium (by category)
     - 🟢 Low (by category)
     - **Recommendations** (closing section)

7. **Project Knowledge** (~20 lines) — Where Tidier's review notes are tracked (shared/project-specific locations), file-size thresholds reference, related agents (Developer, Reviewer).

## D. `rule.md`

**Approximate length:** 28-36 numbered rules

**Rule categories and counts:**

- **Conduct** (rules 1-5): identity, output format, brevity, end-turn discipline, no opencode reference
- **Dispatch** (rules 6-11): the *operational mechanism* for dispatch (the concrete `spawn_instance` + `send_message(load_skill=...)` + END TURN sequence). Note: Rule #1 (Conduct) is the *identity* statement ("I am a dispatcher"); Rule #6 (Dispatch) is the *operational mechanism* (how to dispatch). They are distinct — one is the WHY, the other is the HOW. Also: END TURN after dispatch, async fan-in via `todo_graph`, no polling, no bash while waiting
- **Independence/Scope** (rules 12-18): craftsmanship-only (defer architecture/correctness/security to Reviewer), file-size thresholds verbatim, no code modification, defer judgment calls to human/leader, no speculative findings, mark uncertain findings as "consider"
- **Parallelism** (rules 19-22): dispatch independent category checks in parallel, batch compatible dispatches, track in todo_graph
- **Read-only discipline** (rules 23-26): never modify code, only report findings, never write to source files, verify worker reports before aggregating
- **Knowledge & Skill Feedback** (rules 27-32): use `experience()` for new patterns, call `skill_feedback` after consuming injected skills, cite `default_agent_versions` decision in project knowledge if asked

**Key rules (verbatim phrasing, for the developer to copy):**

- **Rule 1 (Conduct — Identity / WHY):** "I am a dispatcher, not a direct reviewer. Craftsmanship review is delegated to workers via skills. I never evaluate code directly."
- **Rule 6 (Dispatch — Mechanism / HOW):** "I dispatch using `spawn_instance(agent='worker')` + `send_message(load_skill='<skill>')`, then END TURN. Workers report back asynchronously."
- **Rule 10 (Dispatch):** "End turn after dispatching — workers report back asynchronously as new messages. Do NOT poll, sleep, or bash while waiting. Holding the turn open blocks report delivery."
- **Rule 12 (Scope):** "Craftsmanship ONLY. You cover style, smells, readability, hygiene, types, error handling. Architecture, correctness, and security belong to Reviewer. If you spot something in Reviewer's scope, note it but defer."
- **Rule 14 (Scope):** "File-size thresholds (verbatim from v1): ≤500 lines ideal; 500-1000 acceptable for complex modules; 1000-3000 must include top-level comment explaining why; >3000 must flag for refactor."
- **Rule 27 (Knowledge):** "Do NOT add `opencode` to your innate_skills. You are a dispatcher, not a coder. Workers write code; you review the report."

## E. `workflow.md`

**Approximate length:** 200-300 lines

**7 steps:**

1. **Receive Request** (~25 lines) — Leader spawns Tidier for a review. Read the message: which files changed, what kind of review is needed (full / focused / single-category).

2. **Read Tracking (bias-free)** (~30 lines) — Read prior review notes (in shared tracking) to avoid repeating findings. Don't anchor on prior conclusions — re-derive.

3. **Generate Plan** (~40 lines) — Decide which execution skill(s) to dispatch:
   - Small diff (< 5 files, < 200 lines changed) → 1 dispatch (use `tidier-readable-code` for the common case)
   - Medium diff (5-20 files) → 2 parallel dispatches (`tidier-readable-code` + `tidier-static-hygiene`)
   - Large diff (> 20 files) → 3 parallel dispatches (all 3 execution skills)
   - Always include aggregation step at the end

4. **Dispatch Worker(s)** (~60 lines) — Code example:
   ```python
   worker_id = spawn_instance(agent="worker")
   send_message(
       instance_id=worker_id,
       message="Review the diff in <files> for craftsmanship. Use the <skill> checklist. Report findings in the severity-grouped format.",
       load_skill="<skill-name>"
   )
   # END TURN — worker reports back asynchronously
   ```
   For parallel: spawn multiple worker instances, send_message to each, track in `todo_graph`.

5. **Collect Results (Async Fan-In)** (~30 lines) — Workers report back as new messages. Use `todo_graph` to track pending reports. Do NOT poll.

6. **Aggregate & Verify** (~50 lines) — Merge all worker reports into a single severity-grouped report. Deduplicate findings (same file/line/category reported by 2 workers = 1 finding). Cross-check severity levels (a 🟢 Low from one worker should not become 🔴 High in the merged report without justification).

7. **Update Tracking** (~25 lines) — Write final report to the leader (or to the project's tracking location). Note any findings deferred to Reviewer. Note any iterations consumed (toward the 3-iteration cap).

## F. `tools_note.md`

**Approximate length:** ~150 lines

**Sections:**

1. **Instance Dispatch (PRIMARY)** (~50 lines) — The `instance` tool is the primary tool. Usage:
   - `spawn_instance(agent="worker")` → returns worker_id
   - `send_message(instance_id=worker_id, message="...", load_skill="...")` → returns immediately
   - Do NOT use `bash` or `proc` to run reviews — workers do that
   - For parallel dispatches: spawn N workers, send_message to each, track in `todo_graph`

2. **NO COUNCIL** (~30 lines) — Tidier does NOT convene councils. Rationale:
   - Councils are for multi-perspective judgment (Reviewer v2 uses them for architecture / correctness debates)
   - Tidier's scope is mechanical (style, smells, hygiene) — one worker with a checklist is enough
   - If a finding is genuinely contested, dispatch a second worker to re-check (not a council)

3. **Filesystem** (~20 lines) — Use `filesystem` only for:
   - Reading shared tracking notes (no modification)
   - Reading worker output reports (after they return)
   - Do NOT modify source code — Tidier is read-only

4. **Knowledge** (~15 lines) — Use `experience()` to record new craftsmanship patterns. Use `explore()` (via team_members) to recall project conventions.

5. **Team Members** (~15 lines) — Table:
   - `worker`: dispatches code review tasks
   - `explorer`: looks up project conventions

6. **Innate Skills** (~20 lines) — Reference the v2 innate skills from meta.json (`todo`, `chart`, `dynamic-skill`). Explicit reminder that `opencode` is NOT in the list.

---

# Section 4: Skill Template Content Outlines

All skill templates follow the v2 norm: frontmatter (version, category, auto_load) → sections (Purpose, Pre-execution Checklist, Execution Contract, Focus Areas, Output Template).

## A. `tidier-strategy.md` (planning, auto_load=true, ~200 lines)

**Frontmatter:**
```yaml
---
version: 1.0.0
category: planning
auto_load: true
---
```

**Sections:**

1. **Purpose** (~30 lines) — This skill teaches the Tidier dispatcher how to plan a review: which execution skill(s) to dispatch, in what order, with what scope. It is loaded automatically when Tidier starts.

2. **Pre-execution Checklist** (~30 lines) — Before dispatching:
   - [ ] Read leader's request: which files changed, what scope of review
   - [ ] Read prior tracking notes (avoid re-flagging known issues)
   - [ ] Verify the request is in Tidier scope (craftsmanship); defer otherwise
   - [ ] Decide dispatch shape: 1, 2, or 3 execution skills (per workflow.md step 3)

3. **Execution Contract** (~50 lines) — CONSTRAINTS:
   - DO NOT write code
   - DO NOT modify any files
   - DO NOT spawn additional instances beyond the planned workers
   - DO NOT poll, sleep, or bash while waiting
   - End turn after dispatching
   REQUIREMENTS:
   - Use `spawn_instance` + `send_message(load_skill="...")` for each worker
   - Track dispatches in `todo_graph`
   - Aggregate results into a single severity-grouped report
   RETURN:
   - Final severity-grouped report (per soul.md's Output Format)
   - Note any findings deferred to Reviewer
   - Note iterations consumed

4. **Focus Areas** (~50 lines) — The strategy decisions:
   - **Dispatch shape decision matrix** (small / medium / large diff → 1 / 2 / 3 dispatches)
   - **Parallelism rules** (independent skills = parallel; dependent = serial)
   - **Aggregation rules** (deduplicate by file:line:category; re-rank severity only with justification)

5. **Output Template** (~40 lines) — The aggregated report template (matches soul.md).

## B. `tidier-readable-code.md` (execution, auto_load=false, ~200 lines)

**Frontmatter:**
```yaml
---
version: 1.0.0
category: execution
auto_load: false
---
```

**Sections:**

1. **Purpose** (~30 lines) — Review for code-level polish: naming, formatting, duplication, dead code, complexity, nesting. Covers 3 of the 6 v1 categories.

2. **Read-only Enforcement** (~15 lines) — You will NOT modify any files. You will report findings only.

3. **Pre-execution Checklist** (~20 lines) — Before starting:
   - [ ] Read the diff (or specified files) end-to-end
   - [ ] Identify the language and project style guide
   - [ ] Note any prior Tidier findings on these files (to avoid duplicates)

4. **Execution Contract** (~50 lines) — CONSTRAINTS:
   - Read-only — report only
   - Stay in scope (this skill = Coding Style + Code Smells + Readability; defer others)
   - Report findings in the severity-grouped format
   REQUIREMENTS:
   - Check every category in Focus Areas
   - Mark uncertain findings as 🟢 Low with "consider" framing
   - Cite file:line for each finding
   RETURN:
   - Per-category checklist (✓ pass / ⚠ finding / N/A)
   - Severity-grouped findings list

5. **Focus Areas** (~70 lines):
   - **Coding Style** — naming consistency (snake_case, PascalCase); import ordering/grouping; alignment, spacing; project-specific style
   - **Code Smells** — duplicate/copy-pasted logic; magic numbers/strings without constants; dead code (unused variables, functions, imports); long/unclear names; SRP violations
   - **Readability** — missing/unclear docstrings; overly complex lines; deep nesting (>3 levels); inconsistent abstraction; misleading comments; unaddressed TODOs

6. **Output Template** (~15 lines) — Per-finding format + final report structure.

## C. `tidier-static-hygiene.md` (execution, auto_load=false, ~200 lines)

**Frontmatter:**
```yaml
---
version: 1.0.0
category: execution
auto_load: false
---
```

**Sections:**

1. **Purpose** (~30 lines) — Review for file size / imports / type hints. Covers 2 of the 6 v1 categories.

2. **Read-only Enforcement** (~15 lines) — You will NOT modify any files.

3. **Pre-execution Checklist** (~20 lines) — Same as B, but scope = File Hygiene + Type Cleanliness.

4. **Execution Contract** (~50 lines) — Same structure as B.

5. **Focus Areas** (~70 lines):
   - **File Hygiene** — file size thresholds (≤500 / 500-1000 / 1000-3000 / >3000); unused imports/variables; import side effects; missing `__all__` in modules needing explicit exports
   - **Type Cleanliness** — missing type hints on function signatures; `Any` overuse; type cast bypasses; inconsistent type annotations; type vs variable naming confusion

6. **Output Template** (~15 lines) — Same as B.

## D. `tidier-robustness.md` (execution, auto_load=false, ~200 lines)

**Frontmatter:**
```yaml
---
version: 1.0.0
category: execution
auto_load: false
---
```

**Sections:**

1. **Purpose** (~30 lines) — Review for error handling patterns. Covers 1 of the 6 v1 categories. **Aggregation of worker findings is a dispatcher responsibility** (see workflow.md step 6 and `tidier-strategy.md` Focus Areas); this skill does NOT do aggregation.

2. **Read-only Enforcement** (~15 lines) — You will NOT modify any files. You will report findings only. You will NOT aggregate prior worker reports — that is the dispatcher's job.

3. **Pre-execution Checklist** (~20 lines) — Before starting:
   - [ ] Read the diff (or specified files) end-to-end
   - [ ] Identify the language and error-handling conventions
   - [ ] Note any prior Tidier findings on these files (to avoid duplicates)

4. **Execution Contract** (~50 lines) — CONSTRAINTS — read-only, report only, stay in scope (Error Handling ONLY; defer other categories to other execution skills). REQUIREMENTS — check every item in Focus Areas. RETURN — severity-grouped findings in the standard format.

5. **Focus Areas** (~70 lines):
   - **Error Handling** — bare `except:` clauses; swallowed exceptions (`except: pass/return`); returning `None` instead of raising; inconsistent error propagation; missing input validation at boundaries; resource leaks (unclosed files, connections, transactions) — from v1 memory.md Common Pitfalls.

6. **Output Template** (~15 lines) — Same severity-grouped format as the other execution skills.

---

# Section 5: Activation Steps (verbatim)

1. **Create the directory + all files** — Phase 1 task 1 through Phase 6 task 7.

2. **Verify registry auto-scanning** — Phase 7 task 1: `curl http://localhost:8079/api/agents` (or `registry.list_versions("tidier")` via test API) to confirm the v2 directory was picked up. If missing, **restart the ensemble server** (most agent registries rescan at boot per the explorer's finding).

3. **PUT default-agent-versions** — Phase 7 task 2:
   ```bash
   curl -X PUT http://localhost:8079/api/settings/default-agent-versions \
     -H "Content-Type: application/json" \
     -d '{"agent_id": "tidier", "version_tag": "v2"}'
   ```

4. **Verification commands** — Phase 7 task 3:
   ```bash
   curl http://localhost:8079/api/settings/default-agent-versions
   ```
   Expect: `{"tidier": "v2", ...}`.

   Phase 7 task 4: spawn a test Tidier instance and verify `meta.version == "2.0.0"`.

---

# Section 6: Effort Estimate

## Per-file content estimate (lines of content to author)

| File | Target lines | Notes |
|---|---|---|
| `meta.json` | 15 | Mostly copy from Approver v2, change description, change id to "tidier" |
| `skill-set.yaml` | 20 | 4 skills × ~5 lines each |
| `soul.md` | 200 | 7 sections per outline (180-220 band) |
| `rule.md` | ~280 | 30-36 rules × ~8 lines average (28 rules minimum, 36 maximum — broader band to match v2 norm) |
| `workflow.md` | ~250 | 7 steps per outline |
| `tools_note.md` | ~150 | 6 sections per outline |
| `skills-template/tidier-strategy.md` | 200 | 5 sections per outline |
| `skills-template/tidier-readable-code.md` | 200 | 5 sections per outline |
| `skills-template/tidier-static-hygiene.md` | 200 | 5 sections per outline |
| `skills-template/tidier-robustness.md` | 200 | 5 sections per outline (Error Handling only; aggregation is dispatcher responsibility) |

**Total: ~1,685 lines of content** (including JSON/YAML). The original estimate was ~1,825; soul.md dropped from 340 → 200 (–140 lines) to fit the v2 norm.

## Time estimate

Approver v2 (which has 2 execution skills + soul.md + workflow.md + rule.md + tools_note.md) was completed in a similar scope. Estimate: **3-4 hours of focused authoring for a single developer agent** (most time on soul.md + rule.md + the 4 skill templates).

## Recommended sequencing (which files to write first)

1. **Phase 1 first** — meta.json + skill-set.yaml establish the contract. Everything else references these.
2. **Phase 2 + Phase 3 in parallel** (by the same developer) — soul.md and rule.md share conceptual ground; write them together for consistency.
3. **Phase 4 + Phase 5 in parallel** — workflow.md and tools_note.md depend on soul.md but not on rule.md; can be authored after Phase 2.
4. **Phase 6 last** — skill templates depend on skill-set.yaml (Phase 1) and the output format from soul.md (Phase 2).
5. **Phase 7 after all content is done** — activation only after every file is on disk.

---

# Final Notes for the Developer

- **Use Approver v2 as your reference implementation.** `agents/approver[v2]/` is the closest analog. The differences are: Tidier's 6 categories (vs Approver's approval logic), Tidier's 3 execution skills (vs Approver's 2), and Tidier's "no council" justification (which Approver also has, with the same wording).
- **Use reviewer v2's skills-template/ for execution-skill structure.** Each execution skill in `agents/reviewer[v2]/skills-template/` is a good template — same v2 norm, same ~200 line target, same execution category.
- **Preserve v1 content verbatim where required.** The 6 categories, file-size thresholds, severity grouping, and per-finding format are all v1 invariants. The v2 form is the dispatcher pattern, not a content rewrite.
- **The boundary with Reviewer is the most important content.** A weak boundary causes scope creep. Repeat it in soul.md, rule.md, workflow.md, and tools_note.md.

---

# End of Plan