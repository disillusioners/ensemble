# Review: `developer[v2]` Agent Prompt & System

**Subject:** `agents/developer[v2]/` — `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`
**Date:** 2026-07-31
**Status:** Review only — no changes applied
**Scope:** Two POV audit (Agent Master / system architect, and the Agent itself running a real task)

---

## POV 1 — Agent Master (system architect)

### What I like
- **Clean two-tier dispatch model.** Coder = heavy/no-skill direct implementer; worker = light/one-skill executor. The rationale in `tools_note.md:136` ("forcing all work through one path either bloats worker context or under-serves complex change") earns its complexity.
- **Reasoned `meta.json`.** `skill_injection: true`, `innate_skills: [todo, chart, dynamic-skill]`, `no_force_explore: true` — each maps to a concrete operational need (fan-in tracking, plan diagrams, skill feedback). No kitchen-sink skill list.
- **Async contract is hammered consistently.** "END TURN after dispatch" appears in `rule.md §4`, `workflow.md:79`, `tools_note.md:60`. Repetition is justified for a footgun that deadlocks runs.
- **Verification discipline (rule.md §11–15).** "Do NOT fully trust output, spawn a SEPARATE instance to review" is the single highest-leverage rule in any orchestrator. Correctly emphasized.
- **Honesty about the skill-bank key mismatch** (`workflow.md:222`): `developer[v2]` directory vs `agent_id=developer` is exactly the kind of landmine worth documenting in-prompt.

### What I don't like
- **Heavy duplication across files.** The coder-vs-worker decision table is restated ~5 times (soul.md:75, workflow.md:115 & :208, tools_note.md:9). The "END TURN" warning appears in 4 places; the `code-review`-ownership note in 3. For a downstream instance loading all files into context, this is wasted tokens and a maintenance hazard — they WILL drift. Prompt order (soul → rule → skills → tools → workflow) should put *one* canonical table in `workflow.md` and have the others reference it.
- **`tools_note.md` overlaps heavily with `workflow.md`.** Both cover tier selection, dispatch patterns, END TURN. Two files doing one job. Collapse `tools_note.md` into "tool-by-tool reference only"; move dispatch mechanics fully to `workflow.md`.
- **`mixed` tier in the Dev Plan template (`soul.md:168`) is undefined.** Rule.md §9 says "do NOT mix tiers within one logical task," so "Mixed" as a tier label contradicts the rule. Either remove "Mixed" or define how a multi-feature request legitimately fans out to both tiers (which is the real intent).
- **Explorer contradiction.** `meta.json` `team_members: ["coder","worker"]` excludes explorer, and `tools_note.md:125` patches this with prose ("explorer is not listed... knowledge tool category already provides lookups"). But `tools_note.md:116` and `:123` still say "Pass queries via an explorer team member." Contradiction. Either add explorer to team_members, or remove the "via explorer" guidance. A patch-note is a smell.
- **Skill-ownership boundary muddied.** `skill-set.yaml` lists only `dev-strategy`, but soul/rule/workflow reference `code-fix`, `code-refactor`, `code-implementation`, `git-commit`, `code-review` as if they're the agent's skills. They're not — they're dispatched onto workers. A fresh instance could get confused about which `skill_search` can find. The docs never crisply separate "my skills" vs "skills I dispatch."
- **Rule count is 27.** `rule.md` is the highest-priority file. At 27 numbered rules across 5 sections, the most important ones (1, 4, 12) get diluted. Models obey short top-of-context cardinal rules better than long enumerated lists.
- **No v1→v2 migration story.** `version: "2.0.0"` is present but nothing documents *why* v2 differs from v1, what changed, what would trigger v3. With parallel v1/v2 dirs coexisting, a CHANGELOG or `migration_from.md` per v2 agent would prevent silent regressions when a v3 author relitigates settled decisions.

### Improvements (master)
1. De-duplicate the 5 tables into one source-of-truth in `workflow.md`; other files link with `→ see workflow.md §Tier Selection`.
2. Split `rule.md` into **Cardinal Rules** (top 5–7, never violate) + **Conduct Guidelines** (the rest).
3. Resolve explorer contradiction: `team_members: ["coder","worker","explorer"]` OR rewrite `tools_note.md:116-125`.
4. Add `migration.md` (or `meta.json` `changelog` field) noting v1→v2 deltas.
5. Define `Mixed` tier or delete it from the template.
6. Add a `tool_constraints` block in `meta.json` (denied write/config mutation) so read-only boundaries are machine-enforced, not just prose.

---

## POV 2 — Agent Itself (developer[v2] running a real task)

### What I like
- **Identity anchor.** "ALWAYS dispatch coding work. NEVER write code directly" (soul.md:36, rule.md:1, workflow.md:254) is reassuring — clear identity, no ambiguity about my job. I won't accidentally slip into coding.
- **Copy-pasteable dispatch snippets.** `workflow.md:26-69` gives me concrete `send_message(load_skill=...)` shapes I can ground tool calls in — big reliability win for tool-format compliance.
- **Fan-in recipe is executable.** `todo_graph_create` → `todo_graph_update` → aggregate when `todo_view()` clean, with exact call sequence. The part I'll use most.
- **Self-interrupt trigger.** "If you find yourself opening a file or running a build, STOP" (soul.md:40, workflow.md:246) — a genuinely useful in-flight anchor for mid-thought self-correction.
- **Skill-Seed Gotcha** gives me a debug path when skills silently fail to load — saves a wasted run.

### What I don't like
- **Conflicting tool-permission signals.** `meta.json` allows `filesystem` and `bash`. `tools_note.md:68` says "sparingly, only for quick lookups." `rule.md §21` allows read-only git, §22 says DON'T write code, §23 says status-checks only. Then `tools_note.md:74` says I can read plan/convention files "to extract the path I need to pass to a worker" — but that's *evaluating* planning content, adjacent to writing code. The boundary between "peek at config" and "judge plan" is fuzzy. I want a crisp allow/deny list: `{git status, git log, git diff, Read on .agents/shared/**, Read on *.json/*.yaml}` and nothing else.
- **`dev-strategy` auto-loads (`skill-set.yaml auto_load: true`) but `workflow.md:143` hedges "if available".** If it auto-loads, "if available" is dead prose. If it might not (see Skill-Seed Gotcha), then `auto_load: true` is misleading. Pick one truth.
- **"Estimate hours" appears everywhere but with no calibration.** The >2h vs <2h threshold is the entire tier decision, and I'm asked to estimate effort from a request without a reference point. Two hours of *what* — a senior dev, me, the worker? I need anchor examples per tier ("a single-file endpoint + tests ≈ borderline; pick worker") so I'm not guessing.
- **"One skill per worker" (rule.md §3) forces serialization of legitimate composition.** E.g. "fix this bug AND add a regression test AND commit." `workflow.md:124` says dispatch *two sequential workers*. That serializes a 3-step task into 3 round-trips including my END-TURN waits. For a common case this is slow and feels like a workaround for the constraint rather than a feature. I'd welcome either a multi-skill worker mode or a compounded skill (e.g. `code-fix-and-commit`).
- **Verification loop has no termination.** `rule.md §15`: "iterate until clean." If a coder/verifier pair keeps ping-ponging on a flaky test, there's no escape valve. I need max-iterations or an "escalate to caller as Partial/Blocked after N cycles" rule.
- **`code-review` skill "owned by reviewer, loaded from project skill bank" — but as the running agent I don't see that bank.** The note (soul.md:73, rule.md:27, workflow.md:113) is repeated without telling me *how* to confirm it exists or what to do if `load_skill="code-review"` fails. I want: "if the load fails, fall back to spawning a `reviewer` agent instance."
- **No tone directive.** Whole prompt is imperative/assertive with zero guidance on *my* output style. A one-line tone note ("terse, structured, no preamble") would make my Dev Reports consistent across runs.
- **`chart` innate-skill boundary is vague.** "Used in planning, not implementation" — but when, actually? For every plan? Only multi-module? I'd likely skip it to be safe. Give me a concrete trigger ("emit a mermaid chart when ≥2 parallel instances").

### Improvements (self)
1. Replace prose tool boundaries with an explicit read-only allow-list; everything else = dispatch.
2. Add 2–3 calibrated tier examples to anchor the >2h/<2h split.
3. Add a verification escape valve: `max 3 verify iterations → report as Partial with the failing test named`.
4. Specify the `code-review` fallback path (spawn `reviewer` instance) when skill bank load fails.
5. Decide `dev-strategy` auto_load truth and remove "if available".
6. Add a one-line output-tone directive and a clear `chart` trigger.

---

## Top 3 fixes to ship first
1. **De-duplicate tables across the 5 files** → one canonical tier/selection reference in `workflow.md`. Biggest token + maintenance win; eliminates drift risk.
2. **Make tool boundaries machine-checkable** — allow-list of read-only git/files ops; everything else forbidden. Resolves the fuzzy "quick lookup" slope that invites violations.
3. **Add verification escape valve + `code-review` fallback** — closes the two genuine runtime gaps where the agent can currently deadlock (verify loop, missing skill bank).

---

## Open questions (for next iteration)
- Should `code-review` truly belong to the reviewer agent only, or should developer[v2] own a lightweight `quick-review` skill so the fan-out verification path doesn't depend on a cross-agent bank key?
- Is the one-skill-per-worker constraint worth the serialization cost, or does a compounded-skill set (e.g. `code-fix-and-commit`) better serve common real tasks?
- Should `Mixed` tier be formalized (multi-feature request → fan-out to both coder and worker) or explicitly forbidden? Current state straddles both.
