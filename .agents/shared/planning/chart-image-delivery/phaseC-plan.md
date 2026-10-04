# Phase C: Agent Guidance — Chat-Channel Chart Delivery

**Date:** 2026-10-04 (revised R2)
**Author:** planner[v2] via plan-creation worker
**Status:** Draft R3 — Revision loop 3 FINAL (TrueAuto)
**Branch base:** `latest` @ `cf8efbeff9d932a6d01d7cbb2411836057e7b099`
**Plan output dir:** `.agents/shared/planning/chart-image-delivery/`

> **Precedence:** `architecture-recommendation.md` §3 governs over phase-plan prose on conflict.

> **Phase C** of the `chart-image-delivery` commission. When an agent generates a Mermaid chart for a user on a chat channel (Discord / Slack / Telegram / any external source context), the user must receive the rendered image in the channel — not a wall of Mermaid code. Phase C teaches chart-capable agents to ALWAYS use `generate_chart` (never hand-write Mermaid) when the user is on a chat source or asks for a visual; pure-text contexts may keep self-generation.

---

## Objective

Update the innate `chart` skill and each chart-capable agent's canonical guidance to add a single cardinal rule: when the user is on a chat source or asks for a visual, agents MUST call `generate_chart` — never hand-write Mermaid blocks. Pure-text contexts (internal planning, HTTP-API callers, no user-visible chat) may keep self-generation. Implementation: a new "Chat Delivery" section in the canonical innate-skill home, plus a one-line cardinal reference in each chart-capable agent's canonical guidance home (no duplicate rule text).

**Testable sentence:** *Any chart-capable agent that produces a ` ```mermaid ` block in response to a chat-source user (or in response to a "draw me a chart" request) instead of calling `generate_chart` is a Phase C regression; any agent that self-generates a trivial diagram for a pure-text planning context is NOT a regression.*

---

## Scope

### In Scope

1. **Innate chart skill** (`agents/_prompt_system/innate-skills/chart/skill.md`): add a new "Chat Delivery" section with the MUST-use rule, channel-detection wording, and the over-deliver-when-uncertain default. Extend the existing "Self-generate vs. Delegate" table with a new row. The `_BUSY_STRING` and `_PAUSED_STRING` pin sites, the "Wedged-Charter Recovery" section heading, and the `# Output Format` fenced example all stay byte-identical — verified via content-addressable greps (see Tasks §2).
2. **Per-agent cardinal reference** in each chart-capable agent's canonical home (rule.md, or tools_note.md if that's the existing home). One-line reference to the chart skill's new "Chat Delivery" section. No full duplicate of the rule text.
3. **Audit existing chart references** in agents that already mention charts (`doc-writer`, `project-manager`, `developer[v2]`, `planner[v2]`, `leader`, `ari`, `coder`, `wanderer`): verify their existing guidance does not contradict the MUST-use rule. Add a clarifying note if a contradiction is found (no rewrite).
4. **Channel-detection wording**: the rule must be robust when the agent does not reliably know it is on a chat source. Word: "if the system context indicates a chat source (per Phase A marker), or the user asked for a visual, or you are uncertain — use `generate_chart`."
5. **Cross-reference hygiene** (per `docs/agent-prompt-writing-guide.md` §3 v2 form): all in-prompt cross-references to the chart skill's new section use `See Chat Delivery in the chart skill` (no `.md` path token).

### Out of Scope

- Changes to `daemon/tools/chart_tools.py` (Phase A's lane).
- Changes to `agents/charter/*` (Phase A's lane — the specialist itself).
- Changes to chat adapters / image delivery (Phase B's lane).
- New test files (Phase D's lane).
- Changes to busy/paused/fallback semantics in the chart skill — keep `_BUSY_STRING`, `_PAUSED_STRING`, and "Wedged-Charter Recovery" section byte-identical.
- Decision on whether to add a `source_hint` system-context injection (Phase B enrichment candidate, flagged as an open question — Phase C plans the wording, Phase B may own the implementation). Close-out (R4): the deferral is NOT open-ended — Phase D's release cut opens the follow-up ticket (deferred-ledger row b).

---

## Components (Exact Files + Sections)

### 1. `agents/_prompt_system/innate-skills/chart/skill.md` — single canonical home

**Insertion point:** immediately after the "Self-generate vs. Delegate" section (currently followed by "How to use `generate_chart()`"). Implementation finds the insertion site via heading text, not line number.

**New section: "Chat Delivery"** (content outline, final wording at implementation time per `docs/agent-prompt-writing-guide.md` §2 voice rules):

> ## Chat Delivery
>
> When the user is on a chat source (Discord, Slack, Telegram, or any external chat adapter) OR asks for an image / diagram / chart visual, you MUST call `generate_chart()` — never hand-write a ` ```mermaid ` block in your response. The user receives the rendered image directly in the channel; a code block is the failure mode.
>
> - Use `generate_chart()` even for diagrams you could self-generate (the simple ones) when the source is a chat adapter or the user asked for a visual.
> - Pure-text contexts (internal planning, HTTP-API callers, no user-visible chat surface) may still self-generate trivial diagrams.
> - When in doubt about the source, prefer `generate_chart()` — over-delivering an image is safer than shipping a code block the user cannot render.
> - The result is a single ` ```mermaid ` block, already validated, followed by a `<!-- ens-img:chart-render:<id> -->` marker. Paste both into your response verbatim — do not re-wrap, re-tag, or strip the fence. **Do NOT strip the trailing marker** — the dispatcher (Phase B) reads it to extract the image id and upload the PNG to your channel; stripping it silently downgrades the user to a wall of Mermaid code.
> - Charter renders the PNG and saves it under `provenance.feature="chart-render"`; the dispatcher extracts the marker and resolves the image bytes; the chat adapter uploads the PNG natively. Pasted-by-you, extracted-by-dispatcher, uploaded-by-adapter — three different components, the marker is the handoff.
> - Existing rules (self-generate for trivial cases in pure-text contexts, busy/paused error handling, "Wedged-Charter Recovery", busy/paused string pins) apply unchanged.

**Also:** extend the "Self-generate vs. Delegate" table with one new row. Locate the table by its existing header (e.g. `| Situation | Action |`) and append:

| Situation | Action |
|---|---|
| User is on a chat source OR asks for an image/visual | Use `generate_chart()` (override the simple-vs-not-simple decision — chat source wins) |

**Constraints (verified by content-addressable greps — see Tasks §2):**
- `_BUSY_STRING` text `"Error: Charter busy; pass fresh=True for parallel charts."` appears in skill.md and MUST be byte-identical to the same text in `tests/test_chart_tools.py:296` (the `_BUSY_STRING` test pin).
- `_PAUSED_STRING` text `"Error: Charter is paused; resume it or pass fresh=True for a new charter."` appears in skill.md and MUST be byte-identical to the same text in `tests/test_chart_tools.py:298` (the `_PAUSED_STRING` test pin).
- The "Wedged-Charter Recovery" section heading and body stay unchanged.
- Both strings' site-of-truth in `daemon/tools/chart_tools.py` is the `_BUSY_MSG` constant at `:55` (busy) and the literal at `:222` (paused) — the skill text is documentation of these sources, not the source itself.

### 2. Per-agent canonical-home edits (20 agents — verified by grep)

**Verified count = 20 (reviewer-corrected; charter is the specialist, not a caller, so it is NOT in this list).** Set enumeration (sorted; `agents/<name>/meta.json` `innate_skills` contains `"chart"`):

> approver, approver[v2], architect, ari, coder, developer, developer[v2], devops, doc-writer, governor, leader, maintenancer, planner, planner[v2], project-manager, reviewer, reviewer[v2], tidier, tidier[v2], wanderer

Each of the 20 gets a one-line cardinal reference. **Phase D's coverage pin #17 (20-agent coverage) was updated to 20 in R2 — keep it in lockstep with the verified set above** (it false-fails if either side drifts). [stale line-pinned cross-ref corrected R3]

| Agent | Existing chart references (line numbers) | Canonical home for new reference | Edit shape |
|---|---|---|---|
| approver | (none) | `rule.md` | Add cardinal reference |
| approver[v2] | (none) | `rule.md` | Add cardinal reference |
| architect | (none) | `rule.md` | Add cardinal reference |
| ari | `rule.md:90, :180`; `soul.md:125, :128`; `workflow.md:59, :127, :135, :161, :337, :340, :341` | `rule.md` (top of "Must" cardinals) | Add cardinal reference; audit existing guidance for contradiction (likely none — ari dispatches chart to worker via Mode 3) |
| coder | `soul.md:112` | `rule.md` | Add cardinal reference; verify `soul.md:112` is consistent (it just says "small visualizations" — no contradiction) |
| developer | (none) | `rule.md` | Add cardinal reference |
| developer[v2] | `soul.md:85`; `tools_note.md:75, :78` | `tools_note.md` (the existing home for chart guidance) | Audit `tools_note.md:78` (existing trigger: "≥2 parallel instances or ≥2 modules") — add clarifying note that the chat-source trigger is a SEPARATE condition that wins over the size heuristic |
| devops | `workflow.md:262, :267, :317` (only `helm chart` — false positive) | `rule.md` | Add cardinal reference; no chart-skill contradiction |
| doc-writer | `rule.md:7, :16, :39`; `soul.md:6, :22, :33, :63`; `workflow.md:14, :19, :20, :21, :37` | `rule.md` (existing home — rule.md:7 already mandates `generate_chart`) | Audit existing `rule.md:7` — if it already says "always use generate_chart for important sections", add a one-line cross-ref to "Chat Delivery" for chat-source clarification. The existing rule is consistent; no rewrite. |
| governor | (none) | `rule.md` | Add cardinal reference |
| leader | `rule.md:177`; `soul.md:96` | `rule.md` (cardinal section) | Add cardinal reference; audit `rule.md:177` ("Plain answers, charts, quick follow-ups..." — about `attest_completion`, not the chart skill) — no contradiction |
| maintenancer | (none) | `rule.md` | Add cardinal reference |
| planner | (none) | `rule.md` | Add cardinal reference |
| planner[v2] | `soul.md:105`; `tools_note.md:140, :143, :146` | `tools_note.md` (existing home) | Audit `tools_note.md:143-146` — currently describes chart for the planning workflow (sequence, dependency graphs, swimlane). Add a one-line cross-ref to "Chat Delivery" so the chat-source override is visible. |
| project-manager | `soul.md:5, :76`; `workflow.md:140, :170, :178, :180, :182, :188` | `workflow.md` (the existing home — extensive chart usage) | Audit existing guidance — already uses `chart` tool explicitly (`workflow.md:140, :178`). Add a one-line cross-ref to "Chat Delivery" so the chat-source override is visible. |
| reviewer | (none) | `rule.md` | Add cardinal reference |
| reviewer[v2] | (none) | `rule.md` | Add cardinal reference |
| tidier | (none) | `rule.md` | Add cardinal reference |
| tidier[v2] | (none) | `rule.md` | Add cardinal reference |
| wanderer | `soul.md:116`; `workflow.md:46, :47` | `soul.md` (existing home — `soul.md:116` mentions "render small data visualizations") | Audit `soul.md:116` — add a one-line cross-ref to "Chat Delivery" for chat-source override. |

**Edit shape (canonical form, 1 line):**

> When the user is on a chat source or asks for a visual, use `generate_chart` — see Chat Delivery in the chart skill.

**If the agent's `rule.md` already has 7 cardinal rules** (per `docs/agent-prompt-writing-guide.md` §3 budget), the new line goes into the "Guidelines" section instead of the "Cardinal Rules" section — survival in context compression comes from the auto-loaded skill, not the rule.md entry.

### 3. Files NOT touched (Phase A / B / D scopes)

- `agents/charter/*` — Phase A.
- `daemon/tools/chart_tools.py` — Phase A.
- `daemon/sources/adapters/{discord,slack,telegram}/*` — Phase B.
- `tests/test_chart_tools.py` — Phase D (regression).
- `daemon/services/instance_messaging.py` — Phase B scope (outbound delivery chain / progressive-lane dispatch; NOT Phase A — Phase A is agent-prompt only). [CORRECTED R3, approver iteration-001 blocking #2]
- `daemon/services/instance_lifecycle.py` — Phase B scope (instance_metadata source_type write site; NOT Phase A — Phase A is agent-prompt only). [CORRECTED R3]

---

## Tasks (Ordered, Each With Verification Step)

1. **Verify Phase A marker contract** (`decisions.md` in the same plan dir).
   - Read the canonical marker spec authored by the Phase A worker.
   - Confirm: the marker format, the meaning of "external chat source context", and the exact byte-form the chart skill should cross-reference.
   - If `decisions.md` is not yet present at task start, fall back to the robust over-deliver-when-uncertain default and flag the gap (open question #1).
   - **Verification:** a sentence in the implementation commit message saying "marker spec read from decisions.md" (or "fallback wording used; Phase A marker still pending").

2. **Edit `agents/_prompt_system/innate-skills/chart/skill.md`** (single canonical home).
   - Insert "Chat Delivery" section immediately after "Self-generate vs. Delegate", before "How to use `generate_chart()`" (find by heading text).
   - Extend the "Self-generate vs. Delegate" table with one new row (find by `| Situation | Action |` header text and append).
   - Keep the `_BUSY_STRING` and `_PAUSED_STRING` text sites in skill.md byte-identical to their test pins (per Constraints in §Components #1).
   - Keep the "Wedged-Charter Recovery" section heading and body unchanged (find by heading text).
   - **Verification (content-addressable — line numbers WILL drift; do not pin to lines):**
     - `grep -nF 'Error: Charter busy; pass fresh=True for parallel charts.' agents/_prompt_system/innate-skills/chart/skill.md` returns at least 2 hits (Best Practices + Wedged-Charter Recovery) — the busy string is preserved.
     - `grep -nF 'Error: Charter is paused; resume it or pass fresh=True for a new charter.' agents/_prompt_system/innate-skills/chart/skill.md` returns ≥1 hit (Wedged-Charter Recovery footnote) — the paused string is preserved.
     - `grep -qF 'Error: Charter busy; pass fresh=True for parallel charts.' tests/test_chart_tools.py` succeeds (the test pin file is the source of truth).
     - `grep -qF 'Error: Charter is paused; resume it or pass fresh=True for a new charter.' tests/test_chart_tools.py` succeeds.
     - `grep -nF '## Wedged-Charter Recovery' agents/_prompt_system/innate-skills/chart/skill.md` returns exactly 1 hit (the section heading is intact).
     - String-to-site mapping (the byte-identity claim, asserted via content match — line numbers not pinned):
       - `_BUSY_STRING` ("Error: Charter busy; pass fresh=True for parallel charts."): `daemon/tools/chart_tools.py:55` (constant `_BUSY_MSG`) ↔ `tests/test_chart_tools.py:296` (test pin `_BUSY_STRING`) ↔ `agents/_prompt_system/innate-skills/chart/skill.md` (Best Practices + Wedged-Charter Recovery prose).
       - `_PAUSED_STRING` ("Error: Charter is paused; resume it or pass fresh=True for a new charter."): `daemon/tools/chart_tools.py:222` (literal return value) ↔ `tests/test_chart_tools.py:298` (test pin `_PAUSED_STRING`) ↔ `agents/_prompt_system/innate-skills/chart/skill.md` (Wedged-Charter Recovery footnote).

3. **Add cardinal reference to each of the 20 chart-capable agents** (per the table in §Components). Pre-impl (R4/N1): re-run the meta.json grep and confirm the 20-agent set BEFORE editing — if the count changed, reconcile the plan + Phase D pin #17 first. Read-before-edit (R4/N2): for every cited agent-file line in §Components #2, read the actual section before editing — anchors are content, not line numbers.
   - For agents with no existing chart reference: insert the one-line reference into the cardinal section of `rule.md` (or the "Guidelines" section if the cardinal budget is full — per the guide's ≤7 rule).
   - For agents with existing chart guidance: add the one-line cross-reference to the existing canonical home (per the table), and audit the existing guidance for contradictions (per the table's notes).
   - **Verification:**
     - For each of the 20 agents, `grep -nF 'Chat Delivery' agents/<name>/*.md` returns ≥1 hit in the canonical home.
     - The total count of agent files carrying a `Chat Delivery` cross-reference is exactly 20 (one per agent — no duplicates of the rule body in any agent file).

4. **Audit for duplication and cross-reference hygiene.**
   - The MUST-use rule body appears in exactly ONE place: the chart skill. Agent files contain only the one-line cross-reference.
   - No `.md` path token in the new content of any `rule.md`, `soul.md`, or `tools_note.md`.
   - **Verification:**
     - `grep -rniE 'MUST.*generate_chart|when.*chat.*generate_chart' agents/*/rule.md agents/*/soul.md agents/*/tools_note.md agents/*/workflow.md | grep -v 'agents/_prompt_system/innate-skills/chart/skill.md'` returns 0 hits. (R5: `-i` — case-insensitive; exact-cardinal wording varies by agent file, case-sensitive greps false-pass.)
     - `grep -nE '\.md\b' agents/<name>/rule.md` (per the v2 form audit) returns 0 hits in the new content.

5. **Verify no edits to Phase A / B / D files.**
   - `git diff --stat <pre-Phase-C-commit>..<Phase-C-head> -- agents/charter/ daemon/tools/chart_tools.py daemon/services/instance_messaging.py daemon/sources/ tests/` returns empty (R5: diff the Phase C COMMIT RANGE — diffing against the branch tip is vacuous once Phase C commits land on that branch).
   - `git diff --stat <pre-Phase-C-commit>..<Phase-C-head> -- agents/_prompt_system/innate-skills/chart/skill.md agents/*/rule.md agents/*/tools_note.md agents/*/soul.md agents/*/workflow.md` returns the expected file list (the chart skill + 20 agent files).
   - **Verification:** explicit `git status` / `git diff --stat` in the implementation commit message.

6. **State the restart/promote property in the implementation report.**
   - `agents/*.md` and `meta.json` edits are picked up at the next instance spawn — no daemon restart needed for Phase C.
   - **Verification:** the implementation report's "Restart" section says "no restart; pick up at instance spawn".

7. **Implementation dispatch shape** (developer note).
   - One developer instance, one reviewer instance. Sequential: developer → reviewer.
   - The developer reads `decisions.md` (Phase A's marker contract) before finalizing the chart skill wording.
   - The reviewer cross-checks: (a) both `_BUSY_STRING` and `_PAUSED_STRING` strings still byte-identical via content-addressable grep (no line-number pins), (b) no `.md` path tokens introduced, (c) the 20 agent files all carry the cardinal reference, (d) no edits outside Phase C's scope.
   - **Verification:** reviewer checklist in the review report — must tick all 4 boxes.

---

## Dependencies

### Upstream (verification only)

- **Phase A marker contract** in `.agents/shared/planning/chart-image-delivery/decisions.md` (authored by the Phase A worker in parallel). Phase C reads this file before finalizing the chart skill wording; if the file is not yet present, Phase C uses a robust over-deliver-when-uncertain default and flags the gap as an open question.

### Downstream

- **Phase D** (tests / version / docs): consolidates the test pack, the prompt-audit checklist, the restart-prompt matrix, and the version-bump CHANGELOG entry. Phase C hands off the prompt-audit checklist (Task 4 verification) to Phase D for inclusion in the regression suite.

### Cross-Phase Coupling

| | Phase A | Phase B | Phase D |
|---|---|---|---|
| **Phase C** | verification-only (read `decisions.md` marker spec) | none (Phase C is planning only) | hands off the prompt-audit checklist |

---

## Test Strategy

1. **Prompt-audit checklist** (run as a grep script — included verbatim in the implementation report, then handed to Phase D for the regression suite):
   - [ ] Every chart-capable agent (20 of them, per `innate_skills: ["chart"]` — verified set in §Components #2) has a reference to "Chat Delivery" in at least one prompt file.
   - [ ] The reference uses the form "See Chat Delivery in the chart skill" (no `.md` path token).
   - [ ] No agent prompt contains the full MUST-use rule body (single canonical home = the chart skill).
   - [ ] `grep -F` for the busy string and the paused string returns hits in `agents/_prompt_system/innate-skills/chart/skill.md` AND in `tests/test_chart_tools.py` (string-to-site byte-identity via content match — no line numbers pinned, because both Phase A and Phase C insert content into skill.md and line numbers will drift).
   - [ ] `grep -nF '## Wedged-Charter Recovery' agents/_prompt_system/innate-skills/chart/skill.md` returns exactly 1 hit (section heading intact).
   - [ ] No edits to `agents/charter/*` (Phase A's scope).
   - [ ] No edits to `daemon/` (Phase A/B's scope).
   - [ ] No edits to `tests/` (Phase D's scope).
   - [ ] No `.md` path tokens introduced in any new content of `agents/*/rule.md`, `agents/*/soul.md`, `agents/*/tools_note.md`, `agents/*/workflow.md`.

2. **Existing chart tool tests** (regression safety — no changes by Phase C):
   - `tests/test_chart_tools.py` — both `_BUSY_STRING` (test pin at `:296`) and `_PAUSED_STRING` (test pin at `:298`) are tested (asserts at `:540`, `:611`, `:809`); charter-reuse logic; generate_chart signature. Phase C makes ZERO changes to this file.
   - Charter internal tests (if any) — Phase C makes ZERO changes.

3. **Existing prompt-audit tests** (if any — e.g., the convention-grep tests in the v2 prompt-writing-guide audit):
   - The cross-reference hygiene check (no `.md` tokens in agent prompts) is a CI gate. Phase C's changes must not break it.

4. **End-to-end smoke** (Phase D owns this, but Phase C states the property):
   - A user sends "draw me a flow chart" on Discord. A chart-capable agent must call `generate_chart` (verified via daemon logs / charter spawn count). The PNG reaches the user as a chat attachment (Phase B's adapter upload); the marker is extracted and stripped from the visible text by the dispatcher before the adapter sends. **The HTML-comment marker is NOT invisible to chat clients — it renders literally as one line of text on Discord / Telegram / Slack**; the dispatcher strips it, the sweeper (Phase A) strips near-misses, and what the user sees is just the Mermaid + the attached image.
   - A user sends "design a state machine" on HTTP API (no chat source). The agent may self-generate OR call `generate_chart` — both are acceptable (pure-text carve-out). The marker is preserved verbatim in the API response (HTTP-API callers can parse it and GET the PNG themselves).
   - A user sends "give me a brief project status" on Discord (no chart request). The agent should NOT call `generate_chart` (no visual requested) — but if it does, the result is over-delivery, not a regression.

---

## Acceptance Criteria (Checkboxable)

- [ ] `agents/_prompt_system/innate-skills/chart/skill.md` has a new "Chat Delivery" section stating the MUST-use rule, the channel-detection wording, and the over-deliver-when-uncertain default.
- [ ] The chart skill's "Self-generate vs. Delegate" table has a new row: "User is on a chat source OR asks for an image/visual → Use `generate_chart()`".
- [ ] All **20** chart-capable agents (per `innate_skills: ["chart"]` in their `meta.json` — verified set in §Components #2) have a one-line cardinal reference to the chart skill's "Chat Delivery" section in their canonical home. Phase D's coverage pin #17 (content-addressable, 20-agent coverage) is in lockstep with §Components #2 to avoid false-fail. [stale line-pinned cross-ref corrected R3]
- [ ] No prompt has a `.md` path token introduced by Phase C edits (cross-reference hygiene, per `docs/agent-prompt-writing-guide.md` §3 v2 form).
- [ ] The MUST-use rule body appears in exactly one place: the chart skill. Agent-level rule.md / tools_note.md entries are short references only.
- [ ] The `_BUSY_STRING` text ("Error: Charter busy; pass fresh=True for parallel charts.") appears in skill.md and is byte-identical to the same text in `tests/test_chart_tools.py:296` — verified by `grep -F` content match (no line-number pin).
- [ ] The `_PAUSED_STRING` text ("Error: Charter is paused; resume it or pass fresh=True for a new charter.") appears in skill.md and is byte-identical to the same text in `tests/test_chart_tools.py:298` — verified by `grep -F` content match (no line-number pin).
- [ ] The chart skill's "Wedged-Charter Recovery" section heading (`## Wedged-Charter Recovery`) and body are unchanged — verified by `grep -F` content match.
- [ ] No edits to `agents/charter/*` (Phase A's scope).
- [ ] No edits to `daemon/` (Phase A/B's scope).
- [ ] No edits to `tests/` (Phase D's scope).
- [ ] Existing chart references in `doc-writer`, `project-manager`, `developer[v2]`, `planner[v2]`, `leader`, `ari`, `coder`, `wanderer` are audited and either consistent with the MUST-use rule or carry a clarifying note (no contradictions).
- [ ] `docs/agent-prompt-writing-guide.md` conventions followed: cardinal/guideline split, one canonical home per artifact, no path tokens, cardinal budget respected (≤7 cardinal rules per agent).

---

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Phase A marker contract (`decisions.md`) not yet written when Phase C implementation starts | High | High | Task 1 verification step reads the file first; if absent, use the over-deliver-when-uncertain default and flag as an open question |
| 2 | Cardinal budget exhaustion — some agents (e.g., leader, doc-writer) may already have 7 cardinal rules, leaving no room for the new one | Medium | High | Per `docs/agent-prompt-writing-guide.md` §3: when cardinal budget is full, the new line goes into the "Guidelines" section — survival in context compression comes from the auto-loaded skill, not the rule.md entry |
| 3 | Channel-detection gap — agents may not reliably know they are on a chat source (system context does not currently expose `source_type` to the agent prompt) | Medium | High | Word the rule with the over-deliver-when-uncertain default; flag as an open question for a small Phase B enrichment (`source_hint` system-context injection) |
| 4 | Cross-reference hygiene violation — agents or the developer forget the "no path token" rule | Medium | Medium | Task 4 verification: explicit grep for `.md` tokens in the new content; convention is the only thing standing between this Phase and a prompt-writing-guide violation |
| 5 | Over-delivery when uncertain — the "use generate_chart when uncertain" default may cause agents to call it in pure-text contexts where self-generation is fine | Low | Medium | The rule explicitly carves out pure-text contexts (internal planning, HTTP-API callers, no user-visible chat surface). The audit step verifies the carve-out is in the wording. |
| 6 | Conflict with developer[v2]'s existing chart trigger — current wording says "≥2 parallel instances or ≥2 modules"; the new chat-source trigger may seem to conflict | Low | Low | The chat-source trigger is a SEPARATE condition that wins over the size heuristic. The implementation adds a clarifying note (not a rewrite) in `tools_note.md`. |
| 7 | The "always use generate_chart for chat" rule interacts badly with the busy-reject / `fresh=True` semantics — agents may spawn many parallel charters in response to a single chart request | Low | Low | The chart skill's existing "Wedged-Charter Recovery" section and busy/paused string pins handle this. Phase C makes no changes to those semantics. |
| 8 | 20-agent fan-out is large for one developer instance — risk of inconsistent wording across agents | Low | Medium | Phase C hands the developer a single canonical wording (the chart skill section) and a one-line reference template. The reviewer cross-checks the wording. |

---

## Open Questions

1. **Channel detection reliability** — Can agents reliably know they are on a chat source? System context injections do not currently expose `source_type` to the agent prompt. Candidates for a small Phase B enrichment:
   - A `source_hint` system-context injection: "this turn arrived via Discord/Slack/Telegram" appended to the system context, visible to the agent.
   - **Close-out (R4):** not open-ended — Phase D's release cut opens the follow-up ticket (see phaseD deferred-ledger row b).
   - An `instance_metadata.source_type` field the agent can introspect.
   - **Phase C does NOT plan the daemon change** — Phase C only words the rule robustly (over-deliver-when-uncertain). Phase B may own the implementation as a follow-up enrichment; flag in Phase B's plan as a possible add-on.
   - **Decision needed by:** Phase B planning, before Phase B implementation starts.

2. **Phase A marker spec** — what is the exact byte-form of the marker that tells the agent "this is a chat-source turn"? Phase A is authoring this in `decisions.md`. Phase C reads it before finalizing the chart skill wording. If Phase A's marker is not yet present at Phase C's implementation start, Phase C uses a robust over-deliver-when-uncertain default and flags the gap.
   - **Decision needed by:** Phase A planning, before Phase C implementation starts.

3. **Per-agent cardinal budget** — for agents whose `rule.md` already has 7 cardinal rules (e.g., `leader`, `doc-writer`), should the new line go into the "Cardinal Rules" section or the "Guidelines" section? Phase C's plan: if the cardinal budget is full, the new line goes into "Guidelines" — survival in context compression comes from the auto-loaded skill, not the rule.md entry. **Decision needed by:** none — Phase C resolves this at implementation time per the existing `rule.md` count.

4. **HTTP-API / pure-text carve-out wording** — what is the exact wording for "pure-text context"? Candidates: "no user-visible chat surface", "internal planning or HTTP-API caller", "non-chat contexts (HTTP API, internal planning, scripted)".
   - **Decision needed by:** none — Phase C uses the most precise wording available at implementation time, consistent with `docs/agent-prompt-writing-guide.md` §2 voice rules.

---

## Cross-Reference to Phase A / B / D

- **Phase A** (render-at-validation capture + marker contract): authors the canonical image-reference marker in `decisions.md` (lockdown: `<!-- ens-img:chart-render:<id> -->`). The HTML comment is **NOT invisible to chat clients — it renders literally on Discord/Telegram/Slack**; the dispatcher (Phase B) strips it, the near-miss sweeper (Phase A, amendment #3) strips malformed variants, and the Mermaid block + attached image is what the user sees. Phase C's chart-skill wording cross-references this marker by name. Phase C plans no daemon changes; `daemon/services/instance_messaging.py` and `daemon/services/instance_lifecycle.py` belong to Phase B's scope (delivery chain), not Phase A's (agent-prompt only). [CORRECTED R3]
- **Phase B** (chat-adapter delivery): owns the per-platform native image upload. The CORRECT delivery attribution (per `architecture-recommendation.md` §1) is three-step: **charter renders the PNG at validation time → the dispatcher extracts the marker and resolves the image bytes → the chat adapter uploads the PNG natively** (Discord attachment, Telegram `sendPhoto`, Slack `files_upload_v2`). Phase C's "Chat Delivery" section wording reflects this — do not reintroduce the earlier "delivery layer renders the diagram" phrasing. **Amendment #22 (delete-after-chat-upload, narrowing deferred item (c) to the chat path) is ADOPTED — Phase B's concern, not C's**; Phase C's plan does not contradict it (no reference to the 30-day GET window or a "deferred image_delete" anywhere in Phase C).
- **Phase D** (tests / version / docs): owns the regression test pack, the version-bump CHANGELOG entry, the docs/CHANGELOG update, and the restart-prompt matrix. Phase C hands off the prompt-audit checklist (Test Strategy §1) for inclusion in the regression suite. **Phase D's coverage pin #17 asserts the same **20**-agent set (updated in R2)** — keep in lockstep with the verified set in §Components #2. [stale line-pinned cross-ref corrected R3]

---

## Restart / Promote Note

Phase C edits are agent-prompt files (`agents/*/rule.md`, `agents/*/soul.md`, `agents/*/tools_note.md`, `agents/*/workflow.md`, and the canonical `agents/_prompt_system/innate-skills/chart/skill.md`). Per the project's instance-spawn refresh model, agent-prompt edits are picked up at the next instance spawn — **no daemon restart is required for Phase C**.

(Phase A's daemon-code changes and Phase B's adapter changes DO require restart/promote; Phase D consolidates the restart-prompt matrix.)
