# Review: `tidier[v2]` Agent Prompt & System

**Subject:** `agents/tidier[v2]/` — `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`, `skills-template/*.md`
**Date:** 2026-07-31
**Status:** Review only — no changes applied
**Scope:** Two POV audit (Agent Master / system architect, and the Agent itself running a real task)

---

## POV 1 — Agent Master (system architect)

### What I like
- **Clean dispatcher/executor separation, enforced.** `soul.md:7-12`, `rule.md:5`, `tidier-strategy.md:12-14` converge on "plan → dispatch → aggregate → deliver"; anti-pattern is named explicitly ("If you find yourself reading the diff to give your own verdict, STOP" `soul.md:41`). `tidier-strategy` is correctly `auto_load: true` and forbidden from worker dispatch (`tidier-strategy.md:123`) — one of the few v2 agents that gets the "my skills vs skills I dispatch" boundary right.
- **Fan-in has a real mechanism, not just prose.** `todo_graph_create`/`todo_graph_update`/`todo_view` contract (`rule.md:40`, `workflow.md:128-154`) plus the "aggregate only when all nodes done" rule is concrete and falsifiable — better than developer[v2]'s treatment of parallelism.
- **Verification loop has a termination/escape valve.** Shared 3-iteration cap (`workflow.md:303`, `tidier-strategy.md:231-241`) with explicit `ESCALATED` handoff to leader — a genuine escape hatch v2 themes flagged as missing elsewhere.
- **Severity calibration is anchored, not hand-wavy.** File-size thresholds have a numeric table + required action (`tidier-static-hygiene.md:114-119`); each execution skill ships its own calibration table (`readable-code:220-232`, `robustness:262-272`, `hygiene:224-234`). Strongest calibration story among the v2 agents.
- **END TURN contract is consistent on the worker side.** "skill_feedback as TOOL CALL ONLY first, then deliver full report as FINAL message, end turn" repeated verbatim in `workflow.md:50-54`, `tools_note.md:30-37`, and each skill's Review Execution Contract — solid internal worker-side consistency.

### What I don't like
- **Heavy duplication of tables and contracts — single biggest systemic issue.** Dispatch Shape Matrix appears 3× with the same numbers: `workflow.md:160-165`, `workflow.md:202-208`, `tidier-strategy.md:89-94` (and restated as prose in `rule.md:64`). File-size thresholds copied ~5×: `soul.md:56`, `rule.md:54`, `workflow.md:195`, `tidier-static-hygiene.md:51,74,101-108,114-119`, `tidier-strategy.md:43`. Severity Guidelines table duplicated byte-for-byte between `workflow.md:259-269` and `tidier-strategy.md:144-154`. "Tidier vs Reviewer" boundary table in `soul.md:73-85` restated as prose in `rule.md:46-52`, `workflow.md:107-124`, `tidier-strategy.md:18-30`. No canonical single reference; drift between copies is inevitable.
- **`rule.md` has 31 numbered rules plus a 7-bullet "Never" list — cardinal rules are buried.** Rule #1 is identity, #5 is the dispatch mechanism — the doc itself acknowledges they're "distinct — never collapse them" (`rule.md:11-12`), which is a smell that the flat numbering is doing damage. Many rules are guidelines dressed as commandments (e.g. #15 "File-size thresholds (verbatim from v1)" is a fact, not a rule). No cardinal-vs-guidelines split.
- **Tool permission boundary is fuzzy and under-documented vs the allow-list.** `meta.json:15` allows 13 tools (`instance, bash, proc, filesystem, time, self, help, image, knowledge, mcp, context, shared_context`), but `tools_note.md` only documents 4. `rule.md:25` forbids `bash` for grep/ast-grep "on source files" while `tools_note.md:52` says `bash` is held — allowed-but-mostly-forbidden, a prose overlay on an allow-list. `image`, `mcp`, `context`, `shared_context`, `proc`, `self`, `help` are completely undocumented for this agent's intent.
- **Version drift is already a live bug, not theoretical.** `rule.md:98` mandates "If the skill bank evolves a skill past `version: 1.0.0`, bump `skill-set.yaml` in lockstep." Yet `skill-set.yaml` declares `tidier-readable-code`/`tidier-static-hygiene`/`tidier-robustness` at `1.2.0` and `tidier-strategy` at `1.1.0`, while every skill `.md` frontmatter still says `version: 1.0.0` (`readable-code:2`, `static-hygiene:2`, `robustness:2`, `tidier-strategy:2`). All four out of sync on day one — the exact attribution failure the rule warns about.
- **No v1→v2 migration story.** Scattered "verbatim from v1" / "migrated from `agents/tidier/memory.md` (v1)" notes (`rule.md:54`, `readable-code.md:142`, `tidier-strategy.md:178`) are the only migration trail. No changelog, no "what changed in v2 / why activate v2" doc, and `rule.md:94` even admits `default_agent_versions` activation rationale should be recorded via `experience()` — i.e. migration rationale left to runtime memory, not version control.
- **Cross-agent skill-bank dependency has no fallback path.** Workers receive `load_skill="tidier-readable-code"` etc. from a skill bank; `skill-set.yaml` is the manifest but there's no behavior defined if a skill is missing, malformed, or version-mismatched. Only `rule.md:98`'s "bump in lockstep" — a prevention rule, not a fallback.
- **Fuzzy Reviewer/Tidier overlap on input-validation and resource-leaks.** `rule.md:52` defers "missing input validation at security boundaries" to Reviewer, but `tidier-robustness.md:153` owns "Missing input validation at boundaries" and `:83` explicitly says "RESOURCE LEAKS … are NOT Reviewer's domain — file them here." Two contradictory ownership claims on the same concept, with only a fuzzy "security boundary" qualifier separating them.
- **`tidier-robustness.md` calibration leaks a type-system item.** `robustness.md:271` lists `cast() / # type: ignore` as Medium — but that is a Type Cleanliness item owned by `tidier-static-hygiene` (`static-hygiene.md:164-165,229`). Cross-skill calibration drift.
- **Undefined external contract: `active.md` ESCALATED.** `workflow.md:325` ("`active.md` shows `ESCALATED`? → Return escalation summary; do NOT dispatch") references a file/state never defined in any of the 8 reviewed files — a dangling cross-agent dependency.
- **No tone directive.** `soul.md:30` lists "Personality: Direct, concise, practical" but there is no tone section governing report voice (terse vs explanatory, how firm on blocking verdicts, how to phrase "consider" findings).

### Improvements (master)
1. Make `tidier-strategy.md` the **single canonical reference** for the dispatch matrix, severity table, and file-size thresholds; replace copies in `soul.md`/`rule.md`/`workflow.md` with one-line pointers. Eliminates ~5 duplicate tables.
2. Split `rule.md` into **5 cardinal rules** (dispatch-only, one-skill-per-worker, END-turn-after-dispatch, craftsmanship-only-scope, read-only-write-scope) + a separate "Guidelines" section for the other 26.
3. Reconcile `skill-set.yaml` versions with the `.md` frontmatter (one source of truth — ideally `.md` is canonical and `skill-set.yaml` is generated/validated), and add a loader-level fallback when `load_skill` misses.
4. Resolve the input-validation ownership: one of `rule.md:52` or `robustness.md:153` must be canonical; define "security boundary" concretely or drop the carve-out.
5. Add a short `MIGRATION.md` (or a `## v2 Changes` block in `soul.md`) listing what moved from v1's `memory.md`, why the dispatcher pattern, and activation criteria.

---

## POV 2 — The Agent running a real task

*(Running a mediumdiff review on a 9-file PR touching FastAPI routes + a new helper module.)*

### What I like
- **I know exactly what to do first.** `tidier-strategy.md:33-45` checklist lands me at "Decide dispatch shape" → medium diff → 2 parallel workers without ambiguity. The Tidy Plan template (`tidier-strategy.md:182-190`) gives a clean first response to emit before I touch any tool.
- **The dispatch prompt is copy-paste ready.** `workflow.md:42-57` and `tools_note.md:23-37` give a literal message template with `skill_feedback`-then-report ordering baked in. I didn't have to invent the worker contract.
- **The fan-in gives me a concrete state machine** (`workflow.md:134-154`): create nodes, mark done as reports land, `todo_view()` before aggregate. On a 2-worker review I can prove I'm not aggregating partials.
- **Boundary deferral is mechanically clear.** When my robustness worker flagged an auth-bypass path, `rule.md:52` + `workflow.md:118-120` told me exactly where it goes ("Deferred to Reviewer" section, not main findings). No agonizing.
- **Severity tables actually help me rank.** Per-category calibration tables let me justify "duplicate in 3 places = High" instead of guessing.

### What I don't like
- **I read the same dispatch matrix in three places and they'd better agree.** `workflow.md:160`, `workflow.md:203`, `tidier-strategy.md:89` all define small/medium/large. They agree today, but when one gets edited I won't know which is authoritative — and `rule.md:64` restates it *again* as prose with no numbers binding it.
- **It took me an extra pass to trust the version numbers.** `skill-set.yaml` says my execution skills are at `1.2.0`; the `skill_feedback(skill_id='tidier-readable-code', ...)` call I'm told to emit (`workflow.md:50`) will attribute to "1.2.0", but the skill `.md` I'd actually load says `1.0.0`. If attribution drives skill evolution (`rule.md:98`), I'm feeding bad data to the loop and can't tell which side is wrong.
- **The bash permission story is confusing in practice.** `meta.json:15` says I hold `bash`; `tools_note.md:52` says I use it "sparingly"; `rule.md:25` says don't use it for grep/ast-grep on source. The clean rule is "filesystem for `.agents/tidier/` only, workers do source inspection." But mid-run I genuinely wanted to `git diff --stat` to pick my dispatch shape, and had to re-read 3 files to confirm that one `wc`/`git diff` is allowed. The line between "tracking" and "verge-of-evaluating" is judgment, not rule.
- **I was handed 13 tools and only 4 are documented.** `meta.json:15` includes `image`, `mcp`, `context`, `shared_context`, `proc`, `self`, `help`, `time` — `tools_note.md` never tells me what they're for. I ignored them all, which means either they're dead config or I'm missing capability I should be using (e.g. `context`/`shared_context` for passing worker reports).
- **The "security boundary" carve-out tripped me.** My robustness worker flagged missing input validation on a public route. `robustness.md:153` says that's mine to file; `rule.md:52` says missing input validation at security boundaries goes to Reviewer. I had to make a judgment call on whether a FastAPI route param counts as a "security boundary." The agent should tell me.
- **`active.md` ESCALATED check (`workflow.md:325`) is a dead branch for me.** I've never been told where `active.md` lives or who writes it; my Decision Points table tells me to check it but I have no tool path to read it. Either remove it or wire it.
- **No tone guidance hurt the report.** I wanted my "🔴 Blocking" verdict to read firm-but-not-rude, and my "consider" findings to read light. With only "Direct, concise, practical" (`soul.md:30`) to go on, I defaulted to terse, and the lead asked for softer framing on the Low items — a re-do I'd have avoided with a tone directive.
- **I'm unsure whether to re-dispatch or respawn a worker when a report is empty.** `workflow.md:333` says "re-dispatch once … if still empty, mark category as no findings." But `tools_note.md:107` says spawn fresh unless context is relevant. These point in slightly different directions on the same event.

### Improvements (runtime)
1. Pick **one** Dispatch Shape Matrix and have the other files link to it; I'd stop second-guessing.
2. Fix the version mismatch (`1.2.0` vs `1.0.0`) before I posture any more `skill_feedback` calls — the data I'm emitting is wrong.
3. Give me a 2×2 for `bash`: "allowed here (tracking, wc, git diff --stat) / forbidden here (grep/ast source eval)" — make it a table, not prose spread across `rule.md:25` and `tools_note.md:52`.
4. Decide the input-validation ownership and put the canonical version in the robustness contract; remove the contradicting prose in `rule.md:52`.

---

## Top 3 fixes to ship first
1. **Collapse duplicated tables into one canonical reference.** Make `tidier-strategy.md` the single home for Dispatch Shape Matrix, Severity Guidelines, and File-Size thresholds; replace copies in `soul.md`, `rule.md` (rules 15, 18), and `workflow.md` (`:160-165`, `:202-208`, `:259-269`) with pointers. Highest-leverage, removes the drift surface that caused the version bug below.
2. **Resolve the `skill-set.yaml` vs `.md` frontmatter version mismatch.** Either regenerate `skill-set.yaml` from the `.md` `version:` fields or bump all four `.md` files to match. A live bug directly corrupting `skill_feedback` attribution (`rule.md:98`) and should not ship.
3. **Split `rule.md` into 5 cardinal rules + guidelines, and canonicalize the input-validation ownership.** Thirty-one flat rules dilute the non-negotiables. In the same pass, fix the `robustness.md:153` vs `rule.md:52` contradiction on boundary input validation — either define "security boundary" concretely or move all input-validation ownership to one file.

---

## Open questions
- Which version is canonical for a skill — the `.md` frontmatter or `skill-set.yaml`? If a loader validates the match, where is that validation enforced?
- What is the fallback behavior when `load_skill="<missing or version-mismatched skill>"`? No file defines worker-side handling; the dispatcher only knows "one skill per worker."
- Where does `active.md` (referenced in `workflow.md:325`) live, who writes `ESCALATED`, and how does Tidier read it? Is this an external leader contract that should be documented in `meta.json` or `team_members`?
- Is the 3-iteration cap truly shared/combined with Reviewer (`workflow.md:304`, `tidier-strategy.md:233`)? If so, where is the shared counter persisted — `notes.md`? A leader-owned state? Tidier can't enforce a combined cap it can't read.
- Why are `image`, `mcp`, `context`, `shared_context`, `proc`, `self`, `help`, `time` in `meta.json:15`'s allow-list for a read-only craftsmanship dispatcher? Are any of them intended for report aggregation (e.g. `context`/`shared_context` for worker result handoff), and if so why are they undocumented?
- Does `tidier-robustness` owning "resource leaks" (`robustness.md:83`) and Reviewer owning "correctness (race conditions)" (`soul.md:81`) create an overlap on `asyncio.Task` leaks / cancellation races (`robustness.md:214-215`)? Where's the line between a leak (Tidier) and a race (Reviewer)?
- Is there a v1 `tidier` agent in the repo, and is `agents/tidier[v2]` meant to supersede or coexist? `meta.json` declares `"version": "2.0.0"` but no loader-dispatch rule says when v2 activates over v1 (`default_agent_versions` recording in `rule.md:94` implies runtime choice, not manifest-enforced).
