# Designer-Critic Orchestration — Research Findings

**Purpose.** Condensed evidence base for the four-phase execution plan, gathered from three high-confidence explorer reports and direct spot-checks against the worktree (`/home/nea/ensemble-src-wt-designer-critic-orchestration/`, branch `feature/designer-critic-orchestration`). Use this file as the citation index — every file:line in the phase plans resolves here.

**Cross-references.** Open design decisions live in `open-design-decisions.md` in this directory (parallel worker owns D1–D7). Phase files reference decision keys (e.g., "gated on **D3 verdict contract**") but do not re-derive the analysis.

**Worktree commit hash baseline:** SHAs are not stamped on this file — the parallel-worker decisions and phase-1/2/3 prose are the binding content; the commit lands at the end of phase 4.

---

## 1. Directive of record (user, Discord 2026-10-10)

Three instructions, each fixed and non-negotiable in scope:

1. **REMOVE `od.generate` from designer entirely.** Sketcher is THE generation lane, always. Kills the Phase-4 "single-page one-offs stay on my own direct `od.generate`" escape hatch (current `workflow.md:105`). **SUPERSEDES** the Stage-2 dual-run pilot's deprecation gate — see §5 below.
2. **ADD a new reviewer agent `critic`** (working name, pre-approved) — design-QA gate. Pattern: designer = orchestrator spawning sketcher (analogous to `leader → developer/tester`); critic = output-review step.
3. **Pipeline.** `designer composes brief → spawns sketcher (generation; `od.*` tool-internal per **D1**) → critic reviews output (verdict: `pass` / `needs-revision` with specific actionable feedback) → designer iterates or accepts/saves.`

---

## 2. Designer surgery inventory (blast radius, ranked)

All line numbers verified against the worktree commit on `feature/designer-critic-orchestration`.

### 2.1 `agents/designer/meta.json` (23 lines total)

- `:18` — token `"od.generate"` is the kill target. The other three `od.*` tokens (`od.compose_brief`, `od.save`, `od.lint`) are **mechanically independent** — separable; deleting only `:18` is sufficient for the tool cut.
- `:14-19` — token list is a JSON array; no `tools.deny` / `tools.deny_spawn` / `caller_model_overrides` keys exist today. Re-key of Cardinal #7 (see §2.3) requires no meta edit, only rule-language.
- `:8` — `llm_model: "vision"` (already includes the OpenAI vision lane in the allowlist, `config.yaml:94`).
- `:22` — `team_members: ["worker", "sketcher"]`. After phase 2, must read `["worker", "sketcher", "critic"]` so the spawn gate accepts `critic` as a child target (hard-denies non-members pre-DB, `daemon/tools/instance.py:2597-2617`).
- `:18` — `od.generate` removal closes the **lane-binding** for `rule.md`'s Mockup Fidelity guideline and `design-strategy.md` skill — those three move in lockstep (see §2.5 below) to avoid the "stated once, drifted twice" trap.

### 2.2 `agents/designer/soul.md` (86 lines)

- `:7` — identity belief: "I default to the OpenDesign (OD) mockup lane … `od.compose_brief` → `od.generate` → `od.lint` → write-through to canonical `mockups/` path via `od.save`." **REWRITE** — designer no longer generates; designer orchestrates.
- `:16 / :18` — role/personality lines reference sketcher dispatch. ADD critic to sub-team identity lists.
- `:24` — Core Belief #1: "Specs are the contract." Stays.
- `:26` — Core Belief #3: "Pixels are earned. Vision assist is per-message and passive — OD-first with vision assist as the supporting lane …" **REWRITE** — replace OD-first-as-own-generation framing with OD-via-sketcher/lane-via-critic-QA framing.
- `:29` — Core Belief #6: "OD lane is the default; text is the fallback … `fallback_reason` enum …" **RE-WORD** — bound to the new orchestrator-via-sketcher framing; `fallback_reason` enum (Cardinal #7) survives unchanged (tester gates on the exact strings, see §4.1).
- `:55` — "Per-page **generation** is sketcher's lane — on multi-page runs I dispatch one sketcher child per page (see the Orchestration section of My Workflow); **single-page one-offs stay on my own direct `od.generate`**." **REWRITE** — same sentence, same compression-survival role, but the fallback clause is gone. Soul is compression-survival: **rewrite, do not delete**.
- `:64` — `team_members: ["worker","sketcher"]` — **add critic**.

### 2.3 `agents/designer/rule.md` (67 lines)

- **Cardinal #4** (`:12`) — "Sub-team: `worker` + `sketcher` only." **UPDATE** to add critic.
- **Cardinal #7** (`:15`) — "Reject text-lane specs missing `fallback_reason` …" with the exact enum `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`. **SURVIVES** but the binding condition in guideline (c) (`:35`) — "the `od.generate` Port tool is in the agent's toolset" — **becomes false** by phase 1's meta.json edit. Re-key the binding in `:35` to reference **sketcher dispatch** instead of **own toolset**.
- **Cardinal #7 enum — DO NOT CHANGE.** The tester gates on these exact tokens. Any paraphrase is a regression.
- **Guideline (c)** (`:35-39`) — Mockup Fidelity. Re-key the "OD plugin loaded" condition to reference designer-as-orchestrator (sketcher holds the binding, designer calls sketcher, sketcher's toolset carries `od.generate`). Keep the lane-start probe concept intact — see §3 below (sketcher holds the probe today; designer must add its own orchestrator-side probe BEFORE dispatching sketcher).
- `:67` — "My only sub-team targets are `worker` and `sketcher`; escalation across agents is the caller's job." **UPDATE** to add critic.

### 2.4 `agents/designer/workflow.md` (225 lines, 7 sections)

The largest blast radius. Phase 1 will rewrite in place — section-by-section anchors:

- `:50` — Phase 3 wireframe-lane statement. **UPDATE** (s/own wireframe/composed-and-dispatched).
- `:56-88` — Phase 4 (`Author the Spec`). Sub-anchors:
  - `:64-71` Mockup lane overview (OD-first; text only as last-effort). **REWRITE** — replace direct `od.generate` step with sketcher dispatch. Keep `fallback_reason` discipline.
  - `:70` Step 0 lane-start probe (`opendesign.list_systems` skill lookup). **MOVE CONCEPT INTO ORCHESTRATOR** — designer's pre-dispatch check becomes "is sketcher's lane healthy?" not "is `od.generate` bound?".
  - `:72-80` Step 1 OD-lane procedure (`:82-91` numbered list, `:87` HTML-of-record). **REPLACE** with sketcher dispatch + receive Envelope Metrics report + critic gate.
  - `:93-97` Step 2 text fallback. **SURVIVES** with rule rebinding (rule.md re-key) — when sketcher fails, designer falls back to text lane for that page; `fallback_reason` recorded in spec.
  - `:97` Defensive dispatch wrapper. **UPDATE** — every sketcher dispatch wraps so exception/empty Envelope triggers text-lane fallback automatically without re-asking leader.
- `:103-105` **Orchestration — the Sketcher Lane (multi-page generation)**. `:105` is **the kill target** — "Single-page one-offs stay on my own direct `od.generate` … dual-run … nothing is deprecated while the pilot gates are open." **DELETE** the single-page escape hatch and the dual-run sentence.
- `:109-111` Brief-crafting + dispatch conventions (`send_message` + END TURN; batch wave). **KEEP** — extend with critic dispatch.
- `:112` Takeover escape valve ("…take that page back and run it on my own direct `od.generate`"). **DEAD** by directive. **REDESIGN** — re-dispatch fresh sketcher OR escalate; **no direct lane exists**.
- `:114-116` Wait-timeout rule (≥400s sync floor for `od.generate`-bearing children). **KEEP PATTERN, RE-WORD** — same ≥400s floor for sketcher or critic invocation via `invoke_agent_and_wait`; reasoning is identical (130-170s generation budget plus semaphore queue stall risk; 300s default silently trims).
- `:118-120` Report handling (parity rows moot; `[REPORT SANITY]` scrutiny rule stays). **UPDATE** — parity row machinery is gone (phase 4 supersession). `[REPORT SANITY]` scrutiny stays (guide-mandated, `tests/unit/test_report_integrity_prompts.py` enforces).
- `:122-129` Post-save visual QA (≤3 iterations, escalate). **REFRAME** — becomes designer-side loop feeding critic. Per **D4** (iteration cap), the conformance-loop budget ≤3 stays but the QA step is critic's verdict, not designer's self-QA. Phase 3 rewrites this section.
- `:133-145` **Dual-Run Pilot section — DELETE / REPLACE.** References parity log at `.agents/shared/planning/od-generate-agent-lane/parity-runs.jsonl` + stage2 gates. Phase 4 supersedes (see §5).
- `:137` Lane `direct` definition (`:137` in the dual-run block). **DELETED.**
- **Phase-by-phase audit:** Phase 1-3 in `workflow.md` (Understand Brief, AC, Scope & Partition, Author) survive with no semantic change; the orchestrator pattern applies to Phase 4 + Orchestration section only.

### 2.5 `agents/designer/tools_note.md` (172 lines)

- `:37-51` — OD section. **REWRITE the whole section per D1=B** (architecture-recommendation.md §2 :29-35 — broad removal; designer holds zero `od.*`). The "retain three on designer" framing in this planner draft (D6 narrowscope) is SUPERSEDED. Drop `:43` `od.generate` row; drop `:49` latency discipline line `max(120.0, max_tokens/370.0)` — moves to sketcher context (and to critic's read-after-write context for ≥400s verification). No `od.*` tokens may remain in the OD section post-edit. Section reads: "OD lane is sketcher's; designer's role is orchestrator-side (composing the brief content in text; sketcher executes `od.compose_brief` as formatter per architecture-recommendation.md §2 :33)."
- `:166-168` — spawn list (`worker`, `sketcher`). **ADD** `critic` (default per finding 20 — happens in phase 1 T3, not deferred).

### 2.6 `agents/designer/skills-template/design-strategy.md` (119 lines, `auto_load: true` — ships in every designer context)

- `:48`, `:50` — Intake / design authorship sections reference `od.compose_brief` / `od.generate` as the mockup path. **LOCKSTEP MOVE** with `workflow.md` Phase 4 (`:82-91`) — same drift trap argued both ways.
- `:60-61`, `:65-69`, `:71` — Mockup Lane procedure (`Step 0` / `Step 1` / `Step 2`) chain. **MOVE LOCKSTEP** — "stated once, drifted twice" is the headline risk in the dispatch.
- `:75` — points at `workflow.md` Mockup lane as canonical. The two MUST move together or skill-body drift recurs.

**No legacy `od_generate_design` (the MCP-era name) refs exist anywhere in `agents/designer/`.** No MCP-era cleanup needed.

---

## 3. Sketcher (the generation lane, already shipped v0.18.5)

The worktree's `agents/sketcher/meta.json` is the canonical pattern for a **leaf pipeline participant** that the new `critic` will mirror:

- Leaf (`team_members: []`; rule Cardinal declares "I am a leaf").
- Holds all four `od.*` + `image` + `dynamic-skill` (`meta.json:15-22`).
- `skill_injection: true` (`meta.json:10`).
- `watchover.timeout_seconds: 90` (`meta.json:27-29`).
- `recursion_limit_multiplier: 12` (`meta.json:12`).
- `default_queue: system_parallel_queue` (`meta.json:13`).
- `llm_model: "vision"` (`meta.json:8`).

**D1 structural finding.** The LLM call sits inside `od.generate` — `OdGenerate.execute_dict` at `daemon/plugin_subsystem/opendesign/plugin_tool_factory.py:169`. Sketcher does NOT talk to the LLM directly on the OD lane; the Port tool does. This is the load-bearing reason sketcher holds the toolset and designer doesn't.

**Latency profile.** 130-170s, one `od.generate` call per dispatched page. **Waited out**, not retried storm-style. **Hard requirement** for the sync floor (workflow `:114-116`).

**`max_tokens` guidance.** Default 64000; max 200000 (`daemon/plugin_subsystem/opendesign/ports.py:88-93`). Carries into sketcher's per-call knob.

**Regenerate-EXACTLY-ONCE.** On truncation class (`workflow.md:55-59`), `attempts: <1|2>`. Lint fail does NOT block write-through.

**Report contract** (sketcher's `workflow.md:70-90`):

```
## Sketch — <page> — SHIPPED|FAILED
Envelope Metrics (latency_s, usage, truncated, gates, marker_pass,
                  finish_reason, error_code, model, attempts, lint,
                  artifact)
```

Designer consumes this contract when constructing the critic dispatch context.

---

## 4. Orchestration mechanics (the wiring that makes the pipeline real)

### 4.1 Sync facade — `invoke_agent_and_wait`

`daemon/utils.py:622-774`:

- Child's FINAL REPORT returns as the synchronous tool result (`:738`).
- `invoked_as_tool` stamp suppresses async parent notification (`daemon/services/child_reports.py:3524-3535`).
- `timeout` default 300s — the designer's ≥400s floor (workflow `:116`) MUST be passed explicitly.
- `images` param → vision routing for reference attachments.
- Semaphore `WORKER_POOL_SIZE - 1`.
- **Bypass detail:** the facade bypasses the `team_members` gate (`daemon/instance/tools/_auth.py:149-152`) — the gate acts through `tools.allow` exposure of `instance` tool family. So a critic call from designer via `invoke_agent_and_wait` works only because designer's `team_members` will list critic after phase 2.

### 4.2 Async facade — `send_message`

- Fire-and-forget; report returns as `internal_report:` new parent turn.
- Idempotent dedup (`daemon/services/child_reports.py:3499-3513`).
- No timeout to mistune (preferred lane for designer per current workflow).
- Both designer's `sketcher` dispatch pattern and designer's `critic` dispatch follow the same async convention.

### 4.3 Model routing — `llm_model`

- `llm_model: "vision"` resolves via `config.yaml:94` allowlist; vision already included.
- Omit → global default.
- `caller_model_overrides` mechanism exists (`daemon/registry.py:419`, `daemon/services/instance_lifecycle.py:1651-1677`) but is unused by designer/sketcher today. **Not in scope for this commission** — logged as a future option (e.g., critic on a different vision model than sketcher).

### 4.4 Spawn-cap and deadlock

- Designer's existing Cardinal #6 ("One shot per partition … never re-dispatch") survives.
- Critic's verdict of `needs-revision` triggers a fresh sketcher dispatch — this is NOT a re-dispatch of the same partition; it is a new dispatch with augmented brief. Conformance-loop budget ≤3 (per **D4**) binds the chain.
- **Fan-in + escape valve** (today's `:112`) redesigns: stuck sketcher/critic → re-dispatch fresh instance ONCE; second stuck → escalate to leader.

---

## 5. Critic — new agent infra requirements (per the writing guide)

Reference: `docs/agent-prompt-writing-guide.md` (342 lines, mandatory convention).

### 5.1 Required files under `agents/critic/`

- **`meta.json`** — `id` (`"critic"`), `name`, `description`, `icon`, `color`, `version` (start at `1.0.0`); `llm_model` (gated on **D6**); `innate_skills` (`dynamic-skill`, `todo` — `dynamic-skill` only if phase 2 verdict on read-surface drives it; `todo` matches sketcher's posture for a long-running review); `tools.allow` + `tools.deny` (per **D6**); `team_members: []` (leaf). `watchover.timeout_seconds` per **D6** (proportional to read surface size, not generation — likely 60-120s typical, ≤180s cap).
- **`soul.md`** — ~2k chars. Identity / personality / tone. Tone-directive block per guide `:144-152` (per-severity framing borrowed from designer — 🔴 blocking / 🟡 defer / 🟢 nit).
- **`rule.md`** — ≤7 Cardinals at top + numbered Guidelines, zero duplicates. **The verdict protocol itself is a Cardinal** (per **D3**): "I emit a verdict block on every review." Cardinal #2 (no app-code), Cardinal #3 (one-shot per dispatch — no iterative re-dispatch within one verdict cycle), Cardinal #4 (leaf).
- **`workflow.md`** — expected because the critic dispatches no children (it returns a verdict). Guide `:213` exemption list protects non-dispatching agents from the END-TURN contract enforcement; the file is a courtesy read for any future expansion and MUST exist per project convention even if thin. Phase 2 templates it.
- **`tools_note.md`** — operational allow-list table per **D6** read surface.
- **`skill-set.yaml`** — ONLY if genuinely warranted. **Decision deferred** to phase 2 sub-task; today's read surface is straight off `image` + `filesystem`, no new skill content required.
- **`skills-template/`** — folder MAY exist empty; the guide does not require a starter skill for leaf agents.

### 5.2 Cross-cutting constraints from the guide (must be observed)

- First-person voice throughout the agent's own prompts.
- **ZERO system-internals tokens in prose**: `meta.json`, `tools.allow`, `daemon/` paths, `skill-set.yaml`, `seeder`, `version registry`, `test paths`. Cross-references use SECTION NAMES, not filenames (guide `:91-122`).
- **Closure grep (guardrail):** `\.md` + bare `agents/` tokens in critic prose fail the prompt-integrity test.
- **Safety-critical prohibitions in `meta.json tools.deny`** — not in prose (guide `:139`). Phase 2 hard-refuses `bash`, `proc`, `instance` (no spawning), `dynamic-skill` if not warranted (likely warranted for read-time lookups against project conventions).
- **Defensive dispatch:** fallback stays within `team_members` (guide `:217-230`) — critic has none (leaf), so the safety is the empty `team_members` + `tools.deny` on spawn-shaped tools.
- **No "adapted-from" provenance** (guide `:257`); critic's soul records identity fresh.
- **Pre-commit checklist** (guide `:244-258`).

---

## 6. Critic-relevant infra verdicts

### 6.1 `view-views` expansion is CONFIG-ONLY

- Add `"view-views"` to critic's `tools.allow`.
- Freeze-set `_tool_registry.py:194-220` unchanged (the tool already exists; just exposing it to a new agent).
- Triple-pin tests fire only on the frozenset change (`:184-191`); this commission does not touch the freeze-set, so the tests stay green by construction.
- Residual: the existing stale comment in `_tool_registry.py` (recorded in research as "4th commissioned user" — designer + sketcher + now critic) requires an honesty edit; recorded as a phase-2 sub-task.

### 6.2 Critic's read surface (ranked)

1. **`read_file` on canonical mockups path** — `od.save` deterministically writes to `{project_root}/.agents/shared/planning/{feature_slug}/design/mockups/{page_slug}.html` and returns `path + sha256` (`daemon/plugin_subsystem/opendesign/save.py:67-83`).
2. **`image_list` / `image_get`** — provenance queries on `feature` / `page` / `version` / `source_agent` / `retention_class` (`daemon/tools/image_tools.py:841-931`).
3. **`explain_image`** — delegation, vision-routed.
4. **Design-category comparator symbol** — unverified; phase 2 sub-task confirms whether the symbol exists at the phase start (low risk: compare to sketcher's existing read surface to confirm).
5. **`view_link` minting** — for the design-review URL returned in the verdict (per **D3**).

### 6.3 Verdict precedents (precedent-binding)

- **Tester** — per-criterion tiered (`critical` / `important` / `nice-to-have`) + pre-existing/quarantine class.
- **Governor** — `APPROVED (N critical / M warning)` token + counts.

Critic's verdict shape (gated on **D3**) should resemble designer's existing "Submission shape" (`soul.md:86`) but reformulated as a verdict block; concrete fields deferred to **D3**.

---

## 7. Planning-doc reconciliation surface

Worktree's `.agents/shared/planning/od-generate-agent-lane/` contains ONLY:
- `stage2-addendum.md` (111 lines, committed)
- `parity-runs.jsonl` (1 row — mechanism smoke)

**Untracked docs in main checkout only** (NOT in worktree):
- `architecture-recommendation.md`
- `approach-comparison.md`

Per the directive, the dual-run pilot's deprecation gate is **SUPERSEDED**. Phase 4 reconciles:

### 7.1 Supersession hotspots in `stage2-addendum.md`

- `:3-5` — "Nothing here deprecates either lane; direct `od.generate` stays available regardless of pilot outcome." **DIRECT CONTRADICTION** with the 2026-10-10 directive. Phase 4 supersedes.
- `:9-16` — Dual-run machinery (`direct` + `sketcher` lane definitions, parity row append). Phase 4 supersedes the entire mechanism; the parity log file itself can stay (1 row historical record) but is no longer written to.
- `:34-40` — Pilot gates (N≥10; marker_pass ≥95% + within 5pp; truncated ≤ direct+5pp; median latency ≤1.5×; tokens/page ≤1.3×). **SUPERSEDED** — the gates existed to decide sketcher promotion. Sketcher is now the only generation lane; there is no comparator; the gates are void.
- `:42-52` — Gate registry + adjudication scope. **SUPERSEDED** with the gates.

### 7.2 Carry-forward (NOT superseded)

- **Deferred (a)** — `_OD_GENERATE_WALL_CLOCK_CAP_S=420` cap-math gap: fires only BETWEEN attempts; worst case ~4× inner timeout. Affects sketcher's bounded retry path. **Carry forward** as a future-commission note in phase 4 close-out.
- **Deferred (b)** — Sketcher pipeline paraphrase tightening (rewrite/rephrase vs copy). Cleanup pass. **Carry forward** to phase 4 close-out backlog.
- **Deferred (c)** — `design.capture_mockup` tool (manual recipe in `agents/designer/tools_note.md:55-139` (header-delimited `## Capture Procedure` → `---`; whole-section — re-pinned Pass-6) today). **MORE relevant** for a critic needing screenshots — critic does NOT generate, but critic COULD call `design.capture_mockup` if/when it ships. **Carry forward** as a separate commission.
- **Deferred (d)** — 12 pre-existing test failures (4 in `tests/unit/test_plugin_subsystem*.py` + 8 in agent files). Base-proven failures, unrelated. **List in phase 4 close-out** as out-of-scope debt.
- **`max_tokens ≥ 16000` guidance** (`:59-62`) — 8K saturates the vision lane (mechanism re-smoke row 2026-10-09 14:08, `completion_tokens=8000`). Carries into sketcher's per-call knob AND critic's read-after-write verification surface.
- **Trap note** (`:102-111`) — read the spawn log `model=` line, not `OPENAI_MODEL`. Carries forward; critic's read-time provenance checks are off the `model=` line.

### 7.3 Untracked docs handling (per dispatch constraint — worktree-only writes)

- Cannot copy from `main checkout` into the worktree without an explicit git operation. **Two options:**
  - **Option X — supersede-by-reference**: phase 4 records "untracked docs in main checkout are historical context for the sketcher decision; not part of the worktree commit; the canonical record is the consolidated supersession statement in `open-design-decisions.md` D4 + this `research-findings.md` §7.1" — and the developer leaves them in main checkout only.
  - **Option Y — copy-then-supersede**: developer fetches the untracked docs into the worktree (via `git show` against the commit that referenced them in chat history — NOT via direct copy from `/home/nea/ensemble-src`) and adds them to a `superseded/` subdirectory with a `SUPERSEDED 2026-10-10` marker. This is a multi-file write; needs explicit dispatch approval.
- **Recommendation:** default to **Option X** in phase 4 (no extra writes). **Escalate** if tester flags a missing-evidence gap.

---

## 8. Constraints to carry into all phases (verbatim intent from dispatch)

| # | Constraint | Phase that owns it | Notes |
|---|------------|--------------------|-------|
| C1 | INFRA FLAG (RISK-1): llm-supervisor-proxy 120s read window vs OD 130-170s budget = root cause of the `od.generate` 524s. Sketcher presumably hits the same ceiling (unresolved, routes through failover lane). **NOT in scope to fix; recommend its own commission.** | Risk register only | Phase 4 closes the open thread in retrospective. |
| C2 | No DB schema changes expected. If phase 1/2/3 concludes one IS needed, ESCALATE LOUDLY. | All phases | Agent definitions are file-based; registry discovery scans `meta.json` only (`daemon/registry.py:552-722`). |
| C3 | Agents-tree changes go LIVE only at next promote ceremony (3-factor nonce gate, user-side). NOT THIS BUILD. | Phase 4 | Flag as pending. |
| C4 | Do NOT touch or block the in-flight A/B judge job (third designer reviewing two snapshot designs). | Out-of-scope note | Plan files reference this via "do not block" annotation. |
| C5 | All writes ONLY inside the worktree. Never install dirs (`/home/nea/ensemble-src`, `~/agents-ensemble*`, `INSTALL_DIR/releases/**`). | All phases | Recorded recurring incident (v0.16.8 + v0.17.2). |
| C6 | Plan is consumed by a parallel developer instance + gated by a parallel tester instance. **Verification section per phase.** | All phases | See phase 4 AC table for tester-facing checks. |
| C7 | Cross-reference `open-design-decisions.md` (parallel worker) for D1-D7; do not re-derive. | All phases | Each phase table includes a `decision-gates` column. |

---

## 9. Suggestion: phase skeleton evidence-test (the "keep decision-gates visible" mandate)

The four-phase skeleton below matches the dispatch's "Suggested phase skeleton" but adds decision-gate visibility per phase — the constraint the dispatch specifically calls out ("DO structure phases so decision-dependent tasks are visibly gated on their D-number"):

| Phase | Name | Decision-gate keys visible in phase |
|-------|------|--------------------------------------|
| 1 | Designer `od.generate` surgery | **D5** (text-lane interaction re-key), **D6** (designer retain `od.compose_brief`/`od.save`/`od.lint`) **<!-- SUPERSEDED 2026-10-10 by D1=B (broad removal) — designer holds zero `od.*` per architecture-recommendation.md §2 :29-35; see plan-overview.md §2.1 in-scope -->**, **D7** (planning-doc supersession) |
| 2 | Critic agent build | **D3** (verdict contract), **D6** (read surface + model + `tools.deny`) |
| 3 | Pipeline orchestration wiring | **D2** (invocation lane sync vs async), **D3** (verdict handling), **D4** (iteration cap) |
| 4 | Planning-doc reconciliation + verification | **D7** (untracked-docs handling); depends on the worktree's pre-phase-4 status of `open-design-decisions.md` |

Each phase file renders this gate visibility in the task table's `Depends on (D#)` column.

---

## 10. Open questions (carried into the plan as AC items, not blocking)

- **OQ-A.** What does the critic's read surface look like at phase-2 kickoff if `od.save` has not yet landed a file for the current page? Phase 2 AC confirms the path-based gate (critic decides before reading, designer's pre-dispatch ensures the file exists).
- **OQ-B.** Does `view_link` minting require a view-views registration, or can critic call an existing helper? Phase 2 AC confirms the answer.
- **OQ-C.** Does the design-category comparator symbol exist at `daemon/tools/design/` or wherever the symbol was researched? Phase 2 AC confirms; if NOT, critic's role is non-comparator (verdict on mockup-relative-to-spec).

---

## 11. Citations — file inventory (worktree, this commit)

```
agents/designer/meta.json                     :18 (kill target), :8 (vision), :22 (team)
agents/designer/soul.md                       :7, :16, :18, :24, :26, :29, :55, :64
agents/designer/rule.md                       :12 (#4), :15 (#7 + enum), :35-39 ((c)), :67 (sub-team)
agents/designer/workflow.md                   :50, :56-88, :103-105 (kill), :109-111, :112 (dead),
                                               :114-116 (timeout), :118-120, :122-129, :133-145 (delete)
agents/designer/tools_note.md                 :37-51 (OD section), :43 (:49), :166-168 (spawn list)
agents/designer/skills-template/design-strategy.md   :48, :50, :60-61, :65-69, :71, :75 (lockstep)
agents/sketcher/meta.json                     :8 (vision), :10 (skill inject), :12 (multiplier),
                                               :13 (queue), :15-22 (toolset), :27-29 (watchover), :30 (leaf)
agents/sketcher/workflow.md                   :55-59 (regen), :70-90 (report contract)
.agents/shared/planning/od-generate-agent-lane/stage2-addendum.md   (111 lines, full file)
.agents/shared/planning/od-generate-agent-lane/parity-runs.jsonl    (1 row historical)
daemon/utils.py                               :622-774 (sync facade)
daemon/services/child_reports.py              :3499-3513 (async facade), :3524-3535 (invoked_as_tool)
daemon/tools/instance.py                      :2597-2617 (team_members gate)
daemon/instance/tools/_auth.py                :35-51 (no TOOL_REQUIRED_AGENTS), :149-152 (facade bypass)
daemon/registry.py                            :419 (caller_model_overrides), :552-722 (discovery scan)
daemon/services/instance_lifecycle.py         :1651-1677 (model overrides)
daemon/tools/image_tools.py                   :841-931 (image_list/image_get)
daemon/tools/_tool_registry.py                :184-191 (triple-pin), :194-220 (freeze-set)
daemon/plugin_subsystem/opendesign/ports.py    :88-93 (max_tokens), :61-102 (port chain)
daemon/plugin_subsystem/opendesign/port_registry.py            :724-753
daemon/plugin_subsystem/opendesign/plugin_tool_factory.py      :94-99, :169 (execute_dict), :201-251
daemon/plugin_subsystem/opendesign/save.py    :67-83 (mockup path)
daemon/services/llm_failover.py               :848-852 (wall-clock cap between attempts)
docs/agent-prompt-writing-guide.md            :91-122 (cross-ref style), :139 (tools.deny),
                                               :144-152 (tone-directive), :213 (END-TURN exemption),
                                               :217-230 (defensive dispatch), :244-258 (pre-commit),
                                               :257 (no adapted-from)
config.yaml                                   :94 (vision allowlist)
tests/unit/test_report_integrity_prompts.py (file presence confirmed)
tests/unit/probe_designer_od_lane_binding.py (file presence confirmed — relevant to phase 1 AC)
scripts/upgrade/stage.sh (clean-copy agent tree at next stage)
```

---

**End of evidence base.** See `plan-overview.md` next.
