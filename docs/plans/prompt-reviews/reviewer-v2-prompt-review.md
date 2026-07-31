# Review: `reviewer[v2]` Agent Prompt & System

**Subject:** `agents/reviewer[v2]/` — `meta.json`, `soul.md`, `rule.md`, `workflow.md`, `tools_note.md`, `skill-set.yaml`, `skills-template/*.md`
**Date:** 2026-07-31
**Status:** Review only — no changes applied
**Scope:** Two POV audit (Agent Master / system architect, and the Agent itself running a real task). Cross-checked against loader/seeder (`daemon/manager.py:1843-1879`), council tool path (`daemon/tools/instance.py`), validation tests (`tests/unit/test_reviewer_v2_agent.py`), and the legacy `agents/reviewer/memory.md` the v2 skill templates cite.

---

## POV 1 — Agent Master (system architect)

### What I like
- **Clean identity contract.** `soul.md:5-6` nails the cardinal distinction ("dispatcher, NOT a direct reviewer") and repeats it as "Core Rule" (`soul.md:46-51`) and "Rule" at `workflow.md:293-295`. The invariant is stated three ways but always the same way — hard for the model to drift.
- **Asynchronous dispatch contract is explicit and consistent.** "END TURN after dispatch" in `rule.md §8/§16`, `workflow.md:45-50`, `tools_note.md:33-34`, `review-strategy.md:132`. Single most failure-prone behaviour in a fan-out agent; defended at every call site.
- **The council entry point is verified, not folklore.** `rule.md:32-35` and `workflow.md:150-162` cite `convene_council_with_skill`, explicitly forbid `spawn_councilor` (identity-guarded), forbid `reviewer` as a councilor (recursion), and give the real signature "verified from `daemon/tools/instance.py:901-956`". Best-documented tool contract in any v2 agent reviewed here.
- **1:1 skill↔worker attribution is a real design invariant.** Stated in `rule.md §7`, `workflow.md:90`, `review-strategy.md:108-112`, and enforced structurally by `load_skill` being a single string param on `send_message` (`daemon/tools/instance.py:1327-1330`). `review-strategy.md:11` explicitly forbids injecting `review-strategy` into workers — **the one place** the "my skills vs dispatched skills" boundary is drawn cleanly across all v2 agents.
- **Workers are read-only by skill design, not by hope.** Each execution skill has a dedicated "Read-Only Enforcement" block with concrete prohibited/allowed tool lists (`code-review.md:14-29`; `security-review.md` adds "no active probing" at `:20`). Layered defence: agent-level read-only discipline + skill-level enforcement.
- **Deep-Review escalate-without-permission is a brave, correct call.** `soul.md:27-33` + `rule.md §16`. Security/payment/data-integrity surfaces should not wait for a chat round-trip.
- **Severity calibration tables exist per skill** (`code-review.md:126-139`, `security-review.md:142-162`). Directly addresses the developer[v2] "calibration anchors missing" theme. Security even has a tie-breaker ("if unsure between 🟡 and 🔴, default to 🔴", `security-review.md:162`).

### What I don't like
- **Manifest/tests are out of sync and would fail if run.** `skill-set.yaml` registers **7** skills (incl. `business-logic-review`, lines 33-37), but `tests/unit/test_reviewer_v2_agent.py:219` asserts `len(skills) == 6`, and `SKILL_TEMPLATE_NAMES` (line 33-40) lists only 6 (no `business-logic-review`). The `business-logic-review.md` template exists on disk but has **zero test coverage** and its frontmatter isn't validated. Either the test is stale or the skill was added without updating the suite — both are shipping defects.
- **The Deep-Review trigger checklist is triple-duplicated.** `memory.md:7-63`, `review-strategy.md:56-71` (summary), `rule.md §17` (compact). `review-strategy.md:57` even says "The full checklist lives in `agents/reviewer/memory.md`" — but that file belongs to the **v1** agent, not `reviewer[v2]`, which has no `memory.md` of its own. Cross-version, undocumented dependency. This is the canonical-reference problem at its worst here.
- **Skill Selection Guide table is triplicated.** `soul.md:68-76` (text list), `workflow.md:81-88` (table), `review-strategy.md:97-104` (table with extra "Why" column). Three sources of truth → three drift surfaces. `workflow.md` hasn't been updated to mention `business-logic-review` in its `load_skill` examples while `review-strategy.md` has.
- **`rule.md` has 29 numbered rules with no cardinality.** Rules 25-29 ("Never…") restate rules 7, 8, 26-27, 20-21, and 2 respectively. No "cardinal rules" tier; rule §6 ("ALWAYS dispatch") is operationally *the* rule but is buried as item 6 among 29.
- **Fan-in verification has no escape valve / termination.** `rule.md §22` + `workflow.md:75` + `review-strategy.md:169`: "aggregate only when `todo_view()` shows all nodes done." Nothing specifies what happens if a worker never reports (crash, hang, skill-load failure). A single stuck worker dead-ends the whole review with no timeout, partial-aggregate policy, or re-dispatch path.
- **`skill_feedback` ordering contract is brittle.** `rule.md §23` demands workers call `skill_feedback` *as a tool call* THEN emit the final report as the FINAL message — repeated verbatim in dispatch messages (`workflow.md:33-39`, `tools_note.md:25-31`, `review-strategy.md:124-129`). Fragile prompt-level ordering constraint with no runtime guard; a worker that interleaves or appends a summary after the report corrupts downstream feedback + aggregation. Four copies = four drift sites.
- **Skill-bank failure mode is silently swallowed.** `daemon/manager.py:1878-1879` logs+swallows `seed_all` exceptions; if seeding fails, `worker` instances calling `load_skill="code-review"` hit the `skill not found in skills or skill_bank` path (`daemon/tools/instance.py:1231-1243`) and "councilors will run without skill injection." The agent prompt never documents this fallback or tells the reviewer how to detect/report a skill-load failure.
- **No v1→v2 migration story.** `reviewer[v2]` reuses `agents/reviewer/memory.md` (v1), references `.agents/reviewer/memories/` (`soul.md:95`, a runtime path shared with v1), yet is a separate `reviewer[v2]` directory with `id:"reviewer"`. Which agent owns runtime memory writes when both are registered? `meta.json:2` forces `id:"reviewer"` for both versions — registry collision risk, unmentioned.
- **`business-logic-review` is under-integrated everywhere except the manifest.** In `skill-set.yaml`, has a template, listed in two of three skill-selection tables, but absent from `meta.json`'s implied capability list, the v2 test suite, and `tools_note.md`'s "Innate Skills" discussion. Recently-added skill that missed the propagation sweep.
- **Tone/voice directive is absent.** `soul.md:41` says "Organized, directive, efficient" — but no tone directive for how findings should read to a human reviewer/PR author. Severity labels are precise; tone is not.

### Improvements (master)
1. **Make `memory.md` first-class under `reviewer[v2]/`** and have all three trigger-checklist sites point to it (or inline once in `review-strategy.md`). Stop depending on the v1 agent's memory file.
2. **Split `rule.md` into "Cardinal Rules" (≤5: dispatch, one-skill-per-worker, end-turn-after-dispatch, read-only, single-council) + "Guidelines"**; delete the "Never…" restatement block (§25-29) since each duplicates an earlier rule.
3. **Collapse the Skill Selection table to one canonical source** (`review-strategy.md`, already the richest); have `workflow.md` and `soul.md` reference it. Add `business-logic-review` to ALL three uniformly.
4. **Add a fan-in safety valve** in `rule.md §22` / `workflow.md`: per-worker timeout, "report missing after N turns → re-dispatch once, then aggregate partial with explicit `[incomplete]` markers", max-retry cap.
5. **Fix the test↔manifest sync** (see Top 3); add `business-logic-review` to `SKILL_TEMPLATE_NAMES` and bump the count assertion to 7.
6. **Document the skill-bank-load failure path**: what the reviewer should do when a worker's report says (or implies) the skill didn't load.

---

## POV 2 — Agent running a real task

*(Scenario: "Review the payment module at `src/payment/**` — deep check correctness and authz.")*

### What I like
- **I know exactly what to do first.** `review-strategy.md:13` ("Scope Assessment (Run First, Always)") + the "Before planning" Deep-Review scan (`workflow.md:101-109`) gives a deterministic entry sequence. For "payment" I immediately match trigger §4 "Business-Critical Logic / payment" and §1 "Data Integrity / auth" → Deep-Review. No guesswork.
- **The escalation announcement is one line** (`🔴 Deep-Review activated: [reason]`, `rule.md §18`) and I'm told to proceed without permission (`soul.md:32`). Keeps the user informed without stalling.
- **The council call is copy-pasteable.** `workflow.md:164-181` gives a complete worked example for *exactly* payment with `councilor_skill="security-review"`, `max_councilors=4`, `instance_name="review-council"`. The signature in `tools_note.md:48-57` matches what I'd actually call. Low cognitive load.
- **Output templates are concrete and fixed.** Both Review Plan (`soul.md:108-129`) and Review Summary (`soul.md:131-162`) are literal blocks. So is the worker-side Finding Report (`code-review.md:101-124`, `security-review.md:163-191` with an extra Threat-Model + CWE column). I will produce parseable output.
- **The END TURN rule is hammer-drilled.** Every dispatch example ends with `# END TURN — worker reports back asynchronously`, plus a *why* (`workflow.md:45-50`: "holding the turn open blocks report delivery and deadlocks the run"). That rationale is what makes me obey it rather than "helpfully" poll.

### What I don't like
- **I'm told to be read-only but I'm handed `bash` + `filesystem`.** `tools_note.md:99-115` says "sparingly, quick lookups only" — but the boundary is prose, not an allow-list. Compare to the worker skills, which ship *explicit prohibited/allowed tool lists* (`code-review.md:14-29`). The dispatcher has weaker permission hygiene than my workers.
- **When a worker's report never arrives, I'm stuck.** `rule.md §22` says aggregate only when `todo_view()` shows all done. If `review-worker-auth` crashes during skill load (swallowed-seed scenario), there's no instruction. Do I wait forever? Re-dispatch? Report partial? Undefined path for the highest-probability failure.
- **The Final-Message-After-`skill_feedback` ordering is on me to enforce on the worker.** I'm told to *tell* each worker to call `skill_feedback` as a tool first, *then* emit the report as the FINAL message, with nothing after. I have no way to verify the worker obeyed; if it appends a one-line summary, "that report is what I receive verbatim, so a trailing summary would erase the detail" (`rule.md §67`) — my aggregation silently loses the detailed report. A prompt-level contract enforced across a message boundary with no checksum.
- **`review-strategy.md` and `workflow.md` and `rule.md` keep restating the same dispatch snippet with subtle wording drift.** Near-identical but not byte-identical. When I assemble a real dispatch I have to pick one and trust it; if a worker behaves oddly I can't be sure which wording the prompt author intended as canonical.
- **`max_councilors` semantics took three readings.** `rule.md §15` + `workflow.md:185` + `tools_note.md:88` all repeat "it's NOT the number of councils, it's councilors WITHIN the council." The fact that it needs to be said three times signals the naming is bad. As a runner I'd value one sentence + a rename suggestion over three repetitions.
- **I reference `agents/reviewer/memory.md` for severity guidelines, but I'm `reviewer[v2]`.** `code-review.md:38` and `business-logic-review.md:40` say "(See `agents/reviewer/memory.md` for the full severity guidelines)" — that's the v1 dir. During a real run, is that file even in my context? My own `soul.md:95` says I use `.agents/reviewer/memories/` (runtime, plural). I genuinely don't know which severity table is authoritative for me.
- **No tone guidance for how hard to push a 🔴.** A blocking finding on payment authz needs to read differently than a 🟢 on naming. I have severity *icons* but no voice directive — risk of either too soft or too aggressive.
- **Read-only paradox at the dispatcher level.** `rule.md §20` says I'm read-only and may only touch `.agents/reviewer/`, `.agents/shared/`, and skill-bank introspection — but `tools.allow` in `meta.json:15` includes `bash`, `filesystem`, `image`, `mcp`, `shared_context`. The meta.json grant is broader than rule.md permits. They disagree.

### Improvements (runtime)
1. **Give the dispatcher the same explicit prohibited/allowed tool table the worker skills have** — replace prose in `tools_note.md:99-115` with a table; reconcile against `meta.json` `tools.allow`.
2. **Add a "worker didn't report" branch** to fan-in: timeout, one re-dispatch, then mark `[incomplete: worker timed out]` and aggregate.
3. **Deduplicate the dispatch-message boilerplate** into one canonical block (in `review-strategy.md`); stop maintaining 3 near-copies.
4. **Add 2-3 sentences of tone guidance** for 🔴/🟡/🟢 findings (e.g. "🔴 is non-negotiable, state the risk concretely; 🟢 invites, doesn't demand").
5. **Resolve the severity-reference path** — point skill templates at the v2-local `memory.md` (once it exists), not the v1 file.

---

## Top 3 fixes to ship first
1. **Fix the test↔`skill-set.yaml` mismatch.** `skill-set.yaml` declares 7 skills (incl. `business-logic-review`) but `tests/unit/test_reviewer_v2_agent.py:215-222` asserts exactly 6 and omits `business-logic-review` from `SKILL_TEMPLATE_NAMES` (line 33-40). Either `test_skill_set_registers_exactly_six_skills` is currently failing, or this suite isn't being run — either way it's a shipped regression. Add `business-logic-review` to `SKILL_TEMPLATE_NAMES` + `execution_skills` list + a frontmatter-`auto_load:false` param case, and bump the count assertion to 7.
2. **Eliminate the cross-version memory dependency + deduplicate the Deep-Review checklist.** Create `agents/reviewer[v2]/memory.md` (move/copy the v1 checklist + severity guidelines), then change `code-review.md:38`, `business-logic-review.md:40`, `review-strategy.md:57`, `review-strategy.md:65` to point at the v2-local file. Collapse the 5-category trigger list (currently triple) into one canonical location.
3. **Add the fan-in escape valve.** In `rule.md §22` and `workflow.md` "Collect Results", specify: per-worker report timeout, one re-dispatch attempt, then aggregate partial results with explicit `[incomplete: worker <id> timed out]` markers in the Review Summary. Without this, a single crashed/hung worker dead-ends every multi-worker review with no documented recovery.

---

## Open questions
1. **Registry collision on `id:"reviewer"`** — `meta.json:2` sets `id:"reviewer"` for `reviewer[v2]`, identical to the v1 `agents/reviewer/meta.json`. How does `daemon/registry.py` disambiguate two registered agents with the same `id` but different directories? Is `reviewer[v2]` a version tag (registry `get_version`) or a parallel agent? The v2 `meta.json` has no `version_tag`-style field beyond `"version":"2.0.0"`. Undocumented.
2. **Who owns the globally-loaded `code-review` skill?** `skill-set.yaml` lists `code-review` under `agent_id: reviewer`, and `daemon/manager.py:1867` seeds it into the global `skill_bank`. Other agents can load it via `load_skill="code-review"`. Is `reviewer` the canonical *maintainer* (its `skill_feedback`/evolution data feeds back to reviewer's metrics), or does any agent that loads it co-own its evolution? Only dispatch attribution is specified; ownership/maintenance boundary is not.
3. **`skill_bank` seed failure fallback.** `daemon/manager.py:1878-1879` silently swallows `SkillSeedService.seed_all` errors. If seeding fails on startup, `convene_council_with_skill` logs "councilors will run without skill injection" and proceeds. Should the reviewer detect this degraded mode (worker report missing the expected `skill_feedback` call) and refuse to aggregate, or treat skill-less outputs as low-confidence? Undefined.
4. **`business-logic-review` vs `code-review` overlap on permission/authorization logic.** `business-logic-review.md:103-107` covers "Permissions & Authorization Logic" (business-side); `security-review.md:89-95` covers "Broken Access Control / Authorization" (technical). For payment-authz review, which skill is "dominant" for `councilor_skill`? `review-strategy.md:51` maps "permission"→`business-logic-review` but `rule.md §17` maps "auth"→security/Deep-Review. Ambiguous routing.
5. **`max_councilors` naming.** Three prompt files spend three separate sentences clarifying it "is NOT the number of councils." Has a rename (e.g. `councilor_count`) been considered and rejected, and is that rationale recorded anywhere?
6. **Is `review-strategy` the only auto-loaded planning skill intended to evolve?** It's `auto_load:true` (`skill-set.yaml:5`) and the richest file. The skill-evolution feedback loop (`skill_feedback`) is mandated for execution skills dispatched to workers but the prompt never says whether `review-strategy` itself receives feedback (it runs in the reviewer's own context, not a worker's). Clarify the feedback path for the reviewer's own auto-loaded skill.
