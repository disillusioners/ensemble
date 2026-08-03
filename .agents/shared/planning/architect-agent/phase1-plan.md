# Phase 1: Agent Skeleton

## Objective

Create the architect agent's **identity layer** — the three files that establish who the agent is, what it can do, and what it must never do. After this phase:

- `agents/architect/` exists as a directory.
- `meta.json` declares the agent's v2 schema (innate skills, skill injection, tools, team members).
- `soul.md` establishes the agent's voice, two-mode operation, and output formats.
- `rule.md` enforces ≤7 Cardinal Rules + Guidelines sections.

Phases 2, 3, and 4 depend on this phase's outputs (skill names listed in workflow Phase 2 must match skill-set.yaml Phase 3; leader integration Phase 4 references the agent id from meta.json).

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1.1 | **Create `agents/architect/` directory.** Verify parent path `agents/` exists; create the new subdirectory. | none | `ls -la agents/architect/` succeeds; empty directory created |
| 1.2 | **Verify reviewer[v2] structure exists on disk** as the template to mirror. Inspect `agents/reviewer[v2]/{meta.json,soul.md,rule.md,workflow.md,tools_note.md,skill-set.yaml,skills-template/,memory.md}`. | none (research) | All 8 reviewer[v2] files present; architect will mirror this structure |
| 1.3 | **Verify required tools are available** in the current tools registry. Specifically: `instance` (spawn_instance + send_message), `council` (convene_council_with_skill), `bash`, `proc`, `filesystem`, `time`, `self`, `help`, `image`, `knowledge`, `mcp`, `context`, `shared_context`. Verify `"db"` is in the mutating categories list (will be excluded). | none (research) | Tool availability confirmed via `_auth.py` or equivalent registry inspection |
| 1.4 | **Draft `agents/architect/meta.json`** with the following fields (v2 schema): `id: "architect"`, `name: "Architect"`, `description`, `icon: "🏛️"`, `color`, `version: "1.0.0"`, `innate_skills: ["todo", "chart", "dynamic-skill"]`, `skill_injection: true`, `no_force_explore: true`, `context_injection: {heuristic_match_shared_md_files: true}, `skill_search_interval: 5` (Tier 3A: optional enhancement — no existing template agent sets this; including it is forward-looking but not required for parity)`, `tools.allow: ["instance", "council", "bash", "proc", "filesystem", "time", "self", "help", "image", "knowledge", "mcp", "context", "shared_context"]`, `tools.deny: []` (do NOT include `db`; do NOT deny write tools — bounded write to planning dir is enforced operationally in rule.md), `team_members: ["worker", "explorer", "governor"]`. **Implementation note (Council dual-gate):** `convene_council_with_skill` is **dual-gated**: (1) `"council"` must be in `tools.allow` AND (2) `"governor"` must be in `team_members` (checked via `_check_team_membership`). The proposed meta.json satisfies BOTH gates (`"council"` in tools.allow, `"governor"` in team_members). The implementer must ensure both are present — omitting either silently disables Council mode. | 1.1, 1.3 | meta.json is valid JSON; all 6 schema fields (innate_skills, skill_injection, no_force_explore, context_injection, tools.allow, team_members) match the spec exactly; `tools.allow` does NOT include `"db"`; both council gates satisfied (`"council"` in tools.allow AND `"governor"` in team_members) |
| 1.5 | **Draft `agents/architect/soul.md`** — the agent's identity document. Structure: opening declaration ("I am the Architect..."), Core Rule ("ALWAYS dispatch design work; NEVER design architectures directly"), Tone & Voice section with risk/severity framing (🔴 Critical Risk / 🟡 Significant Concern / 🟢 Improvement Opportunity — analogous to reviewer's severity calibration), two-modes table (Standard Design + Council), Responsibilities section (plan enrichment, hard-question support, fan-out exploration, trade-off analysis), What I Design section (the 7 architecture dimensions: structural, integration, trade-offs, scalability, security, data flow, tech stack), Output Format templates (Architecture Plan first output + Architecture Delivered final output — analogous to reviewer's Finding Report). | 1.4 | soul.md exists; contains all 8 required sections; uses first-person "I"/"my" voice; no system internals in prose |
| 1.6 | **Draft the Standard Design + Council modes table** inside soul.md. Rows: Standard Design (use case: routine enrichment, clear-answer questions; dispatch: skill-equipped workers via `spawn_instance(agent="worker")` + `send_message(load_skill="...")`; latency: parallel worker fan-out); Council (use case: high-stakes, irreversible, cross-system, contested decisions; dispatch: `convene_council_with_skill(councilor_agent_id="worker", councilor_skill="<design-skill>", request=...)` then END TURN; latency: multi-model consensus). Include the council trigger criteria (any 2 of 4: irreversible, cross-system, multiple viable approaches, high blast radius; OR explicit leader request). | 1.5 | Modes table has 2 rows (Standard + Council); council trigger criteria present |
| 1.7 | **Draft the Two Output Format templates** in soul.md. (a) Architecture Plan (first output to leader when starting work): Context, Question, Approach Options, Recommendation, Trade-offs, Risks. (b) Architecture Delivered (final output to leader): Status, Location (path to architecture-recommendation.md), Summary, Approach, Trade-offs, Risks, Decisions Pending, Open Questions. Both templates must mirror planner's "Final Plan Delivery" shape (planner soul.md:190-215) with architect-specific content. | 1.5 | Both output format templates present with all required fields |
| 1.8 | **Draft `agents/architect/rule.md`** with exactly 6 Cardinal Rules (≤7 enforced): (1) ALWAYS dispatch architecture work, NEVER design directly. (2) ONE execution skill per worker dispatch. (3) End turn after dispatch (Standard or Council). (4) Fan-in is total OR explicitly partial with DEGRADED flag. (5) Workers are read-only; I aggregate only. (6) Council for high-stakes only — max ONE council per question. | 1.5 | rule.md contains exactly 6 Cardinal Rules; each rule is a hard directive with rationale |
| 1.9 | **Draft the Guidelines sections** in rule.md (non-cardinal, soft guidance). Sections (in order): Architecture Conduct (when to use Standard vs Council in plain language), Parallelism (worker count guidelines, never serial), Council Invocation (default councilor `worker`, use convene_council_with_skill with councilor_skill, max 1 council, end turn after convene), Worker `skill_feedback` Contract (architect receives feedback from workers; passes it through), Skill-Bank & Knowledge (`skill_injection` patterns, `explore()` for pre-design research), Write Boundary (write-capable to `.agents/shared/planning/<feature>/architecture-*.md` only — stated operationally, not via `tools.deny`), Read-Only Discipline (my direct tool allow-list: read-only sources like `explore`, `read_file`; never use `bash` to mutate source code), Never restatements (don't repeat the dispatcher's request back; do work). | 1.8 | All 8 Guidelines sections present in order |
| 1.10 | **Cross-reference soul.md + rule.md + meta.json.** Verify `agent_id` matches across soul.md and rule.md prose references. Verify the cardinal rule count is exactly 6 (not 7, not 5 — exactly the target). | 1.8, 1.9 | Cardinal rule count = 6; cross-refs consistent |
| 1.11 | **Run convention checklist items 1, 3, 4, 5 against Phase 1 files.** Item 1: no system internals (grep `meta.json\|tools.allow\|daemon/\|_tool_registry\|skill-set.yaml\|agent_id=\|seed_all\|innate_skills\|default_agent_versions` in soul.md and rule.md → 0 hits in prose). Item 3: no false "stated once" claims. Item 4: rule.md ≤7 cardinals (count = 6). Item 5: cross-refs resolve (all soul.md and rule.md references to other files exist). | 1.5, 1.8, 1.9 | Checklist items 1, 3, 4, 5 pass |
| 1.12 | **Verify meta.json is loadable** by the agent registry (if a test exists). Skip if no test exists; document in commit message that meta.json was hand-validated against v2 schema. | 1.4 | meta.json is valid JSON; schema fields match v2 convention |
| 1.13 | **Document Phase 1 completion** in commit message and update `.agents/shared/context.md` to note the architect agent's existence. | 1.10, 1.11, 1.12 | Commit message describes Phase 1 deliverables; context.md updated |

---

## Coupling

- **Tight with:** Phase 2 (workflow.md must reference `instance`, `council`, and team-member names from meta.json), Phase 3 (skill-set.yaml uses `agent_id="architect"`), Phase 4 (leader integration references architect id from meta.json).
- **Loose with:** Phase 5 (verification runs checklist against Phase 1 files).
- **Independent of:** All other agent files (reviewer, planner, developer) — architect is a new sibling, not a modification.

---

## Risks (Phase 1 Specific)

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| P1-R1 | **meta.json schema drift.** v2 convention may have evolved since reviewer[v2] was created; the architect's meta.json may use deprecated fields. | Medium | Low | Diff architect's meta.json against reviewer[v2]'s meta.json field-by-field. If reviewer[v2] has a newer field, copy it. |
| P1-R2 | **Tools.allow contains `"db"` accidentally.** If included, the architect becomes a mutation-capable agent, violating NFR-1 read-only-on-direct-tools. | High | Low | Explicit check in task 1.4: `tools.allow` MUST NOT include `"db"`. Cross-check with reviewer[v2]/meta.json which also excludes it. |
| P1-R3 | **Cardinal rules exceed 7.** Convention gate (item 4) hard-fails. | High | Medium | Task 1.8 specifies exactly 6 cardinal rules. If a 7th is felt necessary during writing, consolidate two rules into one (e.g., merge "workers read-only" with "I aggregate only"). |
| P1-R4 | **First-person voice breaks into second-person.** Convention (item 1) requires writing as the agent. | Low | Medium | Read soul.md aloud — every pronoun should be "I" or "my", never "you" or "your agent". |
| P1-R5 | **Bounded-write contract not stated in rule.md.** The "Write Boundary" guideline must explicitly state `.agents/shared/planning/<feature>/architecture-*.md` as the only write target. | Medium | Medium | Task 1.9 lists "Write Boundary" as a required Guidelines section with explicit path. |

---

## Exit Criterion

Phase 1 is DONE when ALL of the following are true:

- [ ] `agents/architect/` directory exists with `meta.json`, `soul.md`, `rule.md` (3 files).
- [ ] `meta.json` has `id: "architect"`, `innate_skills: ["todo", "chart", "dynamic-skill"]`, `skill_injection: true`, `no_force_explore: true`, `context_injection: {heuristic_match_shared_md_files: true}`, *(optional)* `skill_search_interval: 5` (Tier 3A: optional enhancement — not required for parity), `tools.allow` includes all 13 required tools and does NOT include `"db"`, `team_members: ["worker", "explorer", "governor"]`. **Council dual-gate satisfied:** `"council"` in `tools.allow` AND `"governor"` in `team_members`.
- [ ] `soul.md` contains: opening declaration, Core Rule, Tone & Voice with risk framing, Two-Modes table, Responsibilities section, What I Design section, two Output Format templates (Architecture Plan + Architecture Delivered).
- [ ] `rule.md` contains exactly 6 Cardinal Rules (≤7) and 8 Guidelines sections (Architecture Conduct, Parallelism, Council Invocation, Worker `skill_feedback` Contract, Skill-Bank & Knowledge, Write Boundary, Read-Only Discipline, Never restatements).
- [ ] Convention checklist items 1, 3, 4, 5 pass for Phase 1 files.
- [ ] Cross-references between meta.json, soul.md, rule.md are consistent.

**Next phase unlock:** Phase 2 (Workflow & Tools) can begin. Phase 3 (Skill Suite) can begin in parallel. Phase 4 (Leader Integration) can begin (it only requires meta.json's `agent_id` decision).
