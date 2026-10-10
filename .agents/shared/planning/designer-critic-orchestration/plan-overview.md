# Designer-Critic Orchestration — Plan Overview

**Branch:** `feature/designer-critic-orchestration`
**Worktree:** `/home/nea/ensemble-src-wt-designer-critic-orchestration` (mandatory write boundary)
**Directive of record:** user via Discord 2026-10-10 (designer-system rework; see `research-findings.md` §1 for the three-line verbatim)
**Adjudication source of truth:** `architecture-recommendation.md` in this directory (ratified 2026-10-10). **Phase plans consume its verdicts verbatim — DO NOT re-litigate D1–D7 in the phase plans.**
**Decision-history file:** `open-design-decisions.md` (D1–D7 cluster analysis — written by the planning worker; preserved as the historical decision record; not the binding text)
**Evidence base:** `research-findings.md` (this directory)

---

## 1. Objective

**Make sketcher the sole OD generation lane and add a `critic` design-QA gate that sits between designer's brief-dispatch and designer's accept-and-save step.** The current designer's toolset exposes `od.generate` (used for both direct "single-page one-off" escapes AND multi-page runs before sketcher shipped v0.18.5); both escape hatches are killed. The current Phase-4 "dual-run pilot" deprecation gate (`stage2-addendum.md`) is **superseded outright** by this commission — the directive explicitly lifts the pilot's deprecation gate because the dual-run machinery itself is dead.

This commission closes the recurring root-cause behind two failed designer test commissions (`Snapshots v2 redesign`, 2026-10-09 + 2026-10-10) without requiring a fix to the upstream llm-supervisor-proxy 120s window (a separate, scoped future commission per constraint **C1**).

**One-sentence outcome.** A designer's run on any brief resolves through exactly one generation path — `designer → sketcher → critic → designer accept/save` — never through `designer → od.generate`.

---

## 2. Scope

### 2.1 In scope

- **Designer surgery** — strip all four `od.*` tokens from `agents/designer/meta.json:18` (D1=B, broad removal); rewrite the seven sections of `agents/designer/workflow.md` to reframe as orchestrator (sketcher dispatch + critic gate); re-key `rule.md` Cardinal #7 binding (D5 re-keyed) + add the three architect-mandated controls (KV round-counter, verbatim-lift re-dispatch brief, [VISUAL-QA-DEFERRED] marker) — encoded as **Guidelines (e)–(g)**, not numbered Cardinals (cap-7; see phase 3); rewrite `soul.md` identity (compression-survival) lines `:7`/`:16`/`:18`/`:24`/`:26`/`:29`/`:55`/`:64`; update `tools_note.md` (drop `:43`/`:49`; rewrite the whole OD section `:37-51` per D1=B; add `critic` to spawn list; execute the Q1.2 capture-recipe migration — the `## Capture Procedure` section, header-delimited `:55` → `---` at `:140` (Pass-6 re-pin of the stale `:55-110` cite) → `agents/sketcher/tools_note.md`); lockstep-move `skills-template/design-strategy.md` (sections that reference the kill chain).
- **Critic agent build** — new directory `agents/critic/` per the writing guide (`docs/agent-prompt-writing-guide.md`) with `meta.json` (canonical D6 form: `tools.allow: ["read_file", "image", "design"]`; `image_save` explicitly **denied** to enforce read-only leaf purity), `soul.md`, `rule.md` (verdict-protocol Cardinal + malformed-verdict discipline + regex parse + `pinned_spec_sha` mandate per ratified D3 amendments), `workflow.md` (Review block = canonical home of the verdict schema), `tools_note.md` (declared vs effective team note + D6 deny rationales); leaf posture; hardened `tools.deny`.
- **Pipeline orchestration wiring** — extend `workflow.md` Orchestration section `:103+` to include critic dispatch (async by default, sync via `invoke_agent_and_wait` only with ≥400s floor per `workflow.md:116`); iteration cap ≤3 rounds per page with severity-gated two-branch terminal (D4 ratified form); text-fallback interaction under the new binding (D5 re-keyed verbatim); report-integrity scrutiny preserved; final `designer → sketcher → critic → designer accept/save` shape committed. **Delete the Dual-Run Pilot section** (`:133-145`) under phase 3's extended ownership boundary.
- **Test update task (OWNED)** — phase 4 carries a single owned test-edit task that reworks `tests/unit/agents/test_sketcher_agent.py` (parity pins → critic-pipeline pins; lane enum v2; HTML-comment-line skip) and `tests/unit/plugin_subsystem/test_designer_rewire.py` (invert od.* call-site pins to zero; SPEC rewire → sketcher call shape). Hard-escalates non-workflow edits elsewhere.
- **Planning-doc reconciliation** — supersede `stage2-addendum.md`'s deprecation gates (the dual-run is dead); record which deferred items carry into the next commission; untracked-docs handling per **D7** (Option C — ~30-line context pointer for main-checkout prior art).
- **Verification surface for the tester** — concrete checks the parallel tester instance can execute (phase 4 expanded T9 to the architect's Nit-2 list: critic_meta, critic_tools_resolve, critic_deny_wins, critic_team_implied, designer_od_generate_removed, parity_runs_v2_schema, agent_registry_scan; SC-9 specifies the registry-scan pattern; SC-12 re-points to the reworked rewire test; SC-15 = `jq '.tools.allow | index("design")'` on critic — view-views is **deferred per Q6.1**).

### 2.2 Out of scope (explicit)

- **Fixing the llm-supervisor-proxy 120s read window** (constraint **C1**) — separate commission.
- **DB schema changes** (constraint **C2**) — none expected; flagged in phase 1 risks if discovered.
- **Promote ceremony / live deployment** (constraint **C3**) — out of scope; the agents-tree ships at the next `stage + promote` ceremony (3-factor nonce gate, user-side). Phase 4 stages the worktree commit as the release vehicle and journals pending-promote status.
- **The in-flight A/B judge job** (constraint **C4**) — do not touch, do not block.
- **Adapted-from provenance** on the critic's `soul.md` (writing guide `:257`).
- **Adding `view-views` to critic (or any other agent)** — **deferred per Q6.1** (user policy decision; directive does not authorize a 4th commissioned user). Freeze-set `_tool_registry.py:194-220` unchanged.
- **MCP-era cleanup** — no legacy `od_generate_design` refs in designer; nothing to delete.
- **The 12 pre-existing base-proven test failures** (carried forward from `stage2-addendum.md:96-100`) — listed in phase 4 close-out as out-of-scope debt.
- **`caller_model_overrides` adoption** by designer/critic — unused today; future option, not this commission.

---

## 3. Components (what ships when this plan executes)

| Component | Path | Phase | Owner |
|-----------|------|-------|-------|
| Designer `meta.json` surgical edit (all four `od.*` tokens + team_members add `critic`) | `agents/designer/meta.json` | 1 | developer |
| Designer `soul.md` rewrite | `agents/designer/soul.md` | 1 | developer |
| Designer `rule.md` re-key + new controls (D5 re-key + KV round-counter + verbatim-lift brief + [VISUAL-QA-DEFERRED] marker — encoded as Guidelines (e)–(g), cap-7) | `agents/designer/rule.md` | 1 + 3 | developer |
| Designer `workflow.md` sections 1-102 rewrite + post-save QA loop severity-gated rewrite + Dual-Run Pilot section DELETE | `agents/designer/workflow.md` | 1 + 3 (sections `:1-102` and `:103+~148`) | developer |
| Designer `tools_note.md` full OD section `:37-51` rewrite (D1=B — no `od.*` left) + spawn list add `critic` | `agents/designer/tools_note.md` | 1 | developer |
| Q1.2 capture-recipe migration (arch §2 :109 — sketcher captures + saves, critic reads + compares) | `agents/sketcher/tools_note.md` (from designer `tools_note.md` `## Capture Procedure`, header-delimited `:55` → `---` at `:140` — Pass-6 re-pin) | 1 | developer |
| Designer skill lockstep move | `agents/designer/skills-template/design-strategy.md` | 1 | developer |
| New critic agent dir (canonical D6 form) | `agents/critic/` (all files) | 2 | developer |
| Test-edit task (OWNED, single phase) | `tests/unit/agents/test_sketcher_agent.py` + `tests/unit/plugin_subsystem/test_designer_rewire.py` | 4 (tester-owned edit) | tester |
| Pipeline orchestration section (D4 severity-gated + D5 re-key + async dispatch) | `agents/designer/workflow.md` (orchestration block + post-save QA loop + section-cursor `:103+`) | 3 | developer |
| Planning-doc supersession + SUPERSEDED marker on `stage2-addendum.md` | `.agents/shared/planning/od-generate-agent-lane/stage2-addendum.md` (single-line HTML-comment append) | 1 (marker) + 4 (close-out) | developer + planner |
| Untracked-doc context pointer (D7 = Option C — ~30 lines) | `.agents/shared/planning/designer-critic-orchestration/context-pointer-architecture-recommendation.md` | 4 | developer |
| `parity-runs.jsonl` schema v2 header | `.agents/shared/planning/od-generate-agent-lane/parity-runs.jsonl` (single-line HTML-comment append) | 4 | developer |
| `parity-runs-schema-v2.md` (D7=C) | `.agents/shared/planning/designer-critic-orchestration/parity-runs-schema-v2.md` | 4 | developer |
| `design-capture-mockup-spec.md` (Q7.2 spec-only) | `.agents/shared/planning/designer-critic-orchestration/design-capture-mockup-spec.md` | 2 | developer |
| `verdict-block-schema` planning-dir doc (canonical pin per D3) | `.agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md` | 1 | developer |
| Verification surface (Nit-2 pattern list) | phase 4 AC table + `verification-surface.md` | 4 | tester |

---

## 4. Phases

| # | Name | File | Objective | Depends on (D#) | Depends on (phase) |
|---|------|------|-----------|-----------------|--------------------|
| 1 | Designer `od.*` surgery | `phase1-plan.md` | Strip all four `od.*` tokens (D1=B broad removal); reframe designer as orchestrator across `meta.json`/soul/rule (D5 re-key + new controls — KV/verbatim-lift/[VISUAL-QA-DEFERRED] — encoded as Guidelines (e)–(g), cap-7)/workflow (`:1-102` + tools_note (drop `:43`/`:49` + rewrite OD `:37-51` per D1=B + Q1.2 capture-recipe migration → `agents/sketcher/tools_note.md`)/skills-template; pin the D3 verdict-block schema planning-dir doc; add SUPERSEDED marker to `stage2-addendum.md`. | **D1=B**, **D3** (schema pinning), **D5** (text-lane re-key), **D7=A** (SUPERSEDED marker) | — |
| 2 | Critic agent build | `phase2-plan.md` | Create `agents/critic/` per the writing guide: leaf, vision model, canonical D6 tools (`["read_file","image","design"]` with `image_save` explicitly denied for read-only leaf purity), hardened `tools.deny`, verdict-protocol Cardinal with regex parse + malformed-verdict discipline + `pinned_spec_sha` mandate (D3 ratified amendments); declared-vs-effective-team doc note; Review block = canonical home. | **D3** (verdict contract + 4 amendments), **D6** (canonical form) | 1 (critic reads the new orchestrator prompts) |
| 3 | Pipeline orchestration wiring | `phase3-plan.md` | Extend designer's `workflow.md` Orchestration + post-save QA + DELETE Dual-Run Pilot `:133-145` (D7 ratification; phase 3 extends ownership to `:148`); async dispatch by default; severity-gated two-branch terminal per D4 (advisory-with-disclosure / critical-escalate-only); text-fallback rebinding per D5 (re-keyed); KV round-counter Cardinal (designer-side, critic read/denied); report-integrity scrutiny preserved; final `designer → sketcher → critic → designer accept/save` shape committed. | **D2** (async by default), **D3** (verdict handling), **D4** (severity-gated terminal), **D5** (re-keyed), **D7** (Dual-Run delete) | 1 + 2 |
| 4 | Test updates + planning-doc reconciliation + verification | `phase4-plan.md` | **OWNED test-update task** (rework the two test files for the new pipeline shape); supersede `stage2-addendum.md` close-out (marker was added in phase 1); carry-forward deferred items; settle untracked-doc handling per **D7=Option C** (context pointer, NOT copy); commit the branch; produce tester-verification surface — Nit-2 pattern list incl. `agent_registry_scan`; resolved-tool-set equality check (5 tools; `image_save` denied); `parity_runs_v2_schema` (drop `direct`, add `critic`); designer's `meta.json` empty-of-`od.*` check. | **D7** (Option C + close-out); all prior phases | 1 + 2 + 3 |

---

## 5. Coupling map

Coupling is two-axis: file × phase. Cells = strength `← strong dependency | -- neutral | ~ soft hint`.

| File / system | P1 Surgery | P2 Critic build | P3 Orchestration | P4 Reconcile | Notes |
|---------------|------------|-----------------|------------------|--------------|-------|
| `agents/designer/meta.json` | ← | -- | -- | -- | Single-action lockstep anchor — all four `od.*` removed at `:18` (D1=B); `:22` adds `critic` to `team_members` IN PHASE 1 (default per finding 20; phase 2 verifies). |
| `agents/designer/soul.md` | ← | ~ | -- | -- | Phase 1 rewrites; phase 2 informs phase 1 prose but doesn't edit `soul.md`. |
| `agents/designer/rule.md` | ← (D5 re-key + Cardinal #7 verbatim) | -- | ← (KV round-counter Cardinal + verbatim-lift brief Cardinal + [VISUAL-QA-DEFERRED] marker Cardinal; Guideline (e) accept-with-disclosure — NOT a Cardinal — cap is 7) | -- | **Cap on numbered Cardinals = 7** (writing guide). Phase 3 adds ≤3 numbered Cardinals + 1 Guideline. |
| `agents/designer/workflow.md` | ← (sections 1-3, Phase 4 mockup lane, no-Dual-Run) | -- | ← (orchestration block, post-save QA severity-gated, Dual-Run Pilot DELETE `:133-145`, fenced to `:103+~148`) | -- | Phase 1 and phase 3 OWN different sections; phase 1 owns `:1-102`; phase 3 owns `:103+~148` (extended boundary per finding 4 to reach the Dual-Run Pilot delete). |
| `agents/designer/tools_note.md` | ← (full OD section `:37-51` rewrite per D1=B; spawn list add `critic`) | -- | -- | -- | Phase 1 only. |
| `agents/sketcher/tools_note.md` | ← (Q1.2 capture-recipe migration from designer `tools_note.md` `## Capture Procedure`, header-delimited `:55` → `---` at `:140` — Pass-6 re-pin + latency line) | -- | -- | -- | Phase 1 only (T7 cross-file write; arch §2 :109 binding — sketcher captures + saves, critic reads + compares). |
| `agents/designer/skills-template/design-strategy.md` | ← | -- | -- | -- | Phase 1 only. Lockstep with `workflow.md` Phase 4 + Mockup Lane. |
| `agents/critic/*` (new dir) | -- | ← | -- | -- | Phase 2 only. Canonical D6 form. |
| `tests/unit/agents/test_sketcher_agent.py` | -- | -- | -- | ← (OWNED test-edit task; supercedes phase-3 `R3`-rebind escape) | Single owned edit in phase 4. |
| `tests/unit/plugin_subsystem/test_designer_rewire.py` | -- | -- | -- | ← (OWNED test-edit task; inverse the four od.* call-site pins) | Single owned edit in phase 4. |
| `tests/unit/probe_designer_od_lane_binding.py` | ←- (must pass after phase 1 with new binding) | -- | -- | -- | SC-12 re-points to the reworked rewire test (finding 14); this seam probe remains a daemon-side supplement. |
| `tests/unit/test_report_integrity_prompts.py` | ~ | ←- (NEW agent must not introduce REPORT-SANITY violations) | -- | ←- (tester gate) | Writing guide contract. |
| `daemon/registry.py` scan surface (`agent_registry_scan`) | -- | ~ (critic registered) | -- | ←- (tester confirms discovery — Nit-2 pattern) | No code change; verification gate (replaces SC-9's nonexistent `python -m daemon.registry`). |
| `daemon/tools/_tool_registry.py` freeze-set | -- | -- | -- | -- | Unchanged; triple-pin tests must NOT fire. |
| `.agents/shared/planning/od-generate-agent-lane/stage2-addendum.md` | ← (SUPERSEDED marker, single-line HTML-comment append) | -- | -- | -- | Marker added in phase 1 T12; phase 4 does not add another edit. |
| `.agents/shared/planning/od-generate-agent-lane/parity-runs.jsonl` | -- | -- | -- | ← (v2 schema header, single-line HTML-comment) | Phase 4. |
| Main-checkout-only untracked docs (`architecture-recommendation.md`, `approach-comparison.md`) | -- | -- | -- | ← (per **D7=Option C**, context pointer — NOT copy) | Phase 4. |
| `INSTALL_DIR` / `~/agents-ensemble*` (live) | -- | -- | -- | ← (NOT TOUCHED — flagged for next promote) | Constraint **C5**. |

**Strong cross-phase couplings flagged for the developer:**

1. **`workflow.md` lockstep between phase 1 and phase 3** — the section cursor must be honored. Conflict if phase 1 edits bleed past `## Orchestration` or phase 3 starts before `## Orchestration`. **Phase 3 owns `:103+` through approximately `:148`** (extended to capture the Dual-Run Pilot `:133-145` delete; per finding 14, T7b may also delete `:132-147` inclusive to clean up the orphaned `---` at `:147`). Phase 3's boundary stops at `:148`; **`:149+`** (`## Phase 5 — Self-Review` through `## Dispatch: Workers Only` at `:225`) is **not** claimed by any phase and stays untouched by this commission.
2. **Cardinal #7 enum immutability** — phase 1's re-key may NOT paraphrase the `fallback_reason` tokens; phase 3 inherits the verbatim binding.
3. **`team_members` add** — phase 1 T3 (default per finding 20) adds `"critic"` to `meta.json:22`; phase 2 verifies.
4. **D3 amendments propagation** — phase 1 T11 pins the schema at the planning-dir doc (`critic-verdict-schema.md`); phase 2 inherits (Cardinal #1 cites the schema + enforces malformed-verdict discipline + regex parse + `pinned_spec_sha`); phase 3 enforcement is in the dispatch envelope.
5. **D6 canonical form propagation** — phase 2 T3 ONLY (`["read_file","image","design"]` + `image_save` denied); phase 4's resolved-tool-set equality check pins the 5-tool surface.
6. **KV round-counter lane resolution (binding, finding 8a)** — counter is pinned from **designer's** side (designer holds `shared_meta_kv`; designer's Cardinal); critic stays read/denied `shared_meta_kv`.

---

## 6. Risks

| ID | Risk | Impact | Phase | Mitigation |
|----|------|--------|-------|------------|
| **R1** (infra flag) | `llm-supervisor-proxy` 120s read window vs OD 130-170s budget = the root cause of the `od.generate` 524s (Cloudflare) and likely the same ceiling sketcher hits via the failover lane. NOT in scope to fix in this commission. | medium (recurring) | runtime | Forward-pinned in phase 4 retrospective as "recommend its own commission." Sketcher's existing ≥400s sync floor (workflow `:116`) remains the design-time defense; no runtime change in this plan. |
| **R2** | Cross-file drift in designer's mockup-lane prose — `workflow.md` Phase 4 + Mockup lane + `skills-template/design-strategy.md` move at three different line ranges; missing any one re-creates the "stated once, drifted twice" trap. | high | 1 | Phase-1 task table treats the three as one atomic edit; tester gates with a 3-way diff. |
| **R3** | Cardinal #7 enum paraphrase risk during rule.md re-key (any string drift breaks the tester fallback_reason gate). | high | 1 | Phase-1 rule.md task includes a guardrail test diff: enum tokens must match the literal string set. |
| **R4** | Orchestration cursor mismatch — phase 1 edits bleed past `## Orchestration`, conflicting with phase 3's start. | medium | 1, 3 | Developer handoff doc lists the section-cursor line (`workflow.md:103`) explicitly; phase 3's boundary is extended to `:103+~148`; tester scans for out-of-section edits. |
| **R5** | `view-views` exposure — DEFERRED per Q6.1 (user policy decision; does not authorize a 4th commissioned user). Freeze-set unchanged. **SC-15 now checks critic's `design` allow entry, not view-views.** | low (config-only — deferred) | 4 | Plan reflects deferral; SC-15 = `jq '.tools.allow | index("design")'` non-null. |
| **R6** | Designer/sketcher/critic model parallelism — should critic share `llm_model: "vision"` or use a different vision lane? | low (advisory) | 2 | Default to `"vision"` matching sketcher (proven, allowlisted); phase 2 sub-task optionally re-evaluates `caller_model_overrides` (out-of-scope-out, future commission). |
| **R7** | Span of iteration cap (**D4**): severity-gated two-branch (≤3 rounds per page; at cap, advisory-only → accept-with-disclosure, any critical → escalate-only). | medium | 3 | Re-authored to ratified form in phase 3 plan. |
| **R8** | Spawn-cap behavior between designer and critic — `invoke_agent_and_wait` ≥400s floor is correct for sketcher; for critic (no generation, just verdict), is the same floor needed? | low (orchestration-quirk) | 3 | Critic's `watchover.timeout_seconds` is its own knob (DROPPED per §5.4 — inert on a non-watcher; if kept for sketcher symmetry, document as inert); sync-critic-calls inherit the ≥400s floor because of the semaphore queue stall risk during parallel generator-critic pairs. Documented in phase 3. |
| **R9** | Untracked docs (`architecture-recommendation.md`, `approach-comparison.md`) — missing in worktree, only in main checkout. Per D7=Option C, author a ~30-line **context pointer** in the worktree; do NOT copy. | low | 4 | Default per **D7=Option C**; no main-checkout copy. |
| **R10** | Parallel-worker `open-design-decisions.md` arrival — decision-history (not binding) lands before phase 1. | high (sequencing) | 1, 4 | The binding text is `architecture-recommendation.md`; `open-design-decisions.md` is the historical decision record (now stamped with adjudication pointer at top). Phase 1's task list opens with a pre-condition "verify `architecture-recommendation.md` is present and ratified." |
| **R11** | Parallel in-flight A/B judge job (third designer on two snapshot designs) — service for the user MUST continue. | low (housekeeping) | 4 | Plan records "do not block" annotation; does not touch the A/B judge's worktree. |
| **R12** | Promoted-agents-tree blast radius — phase 1/2/3 changes go live only at the next promote ceremony (constraint **C3**). NOT this build. | medium (release-process) | 4 | Phase 4 close-out stages the worktree commit + journal entry for the next promote. No live edits. |
| **R13** | Repeated recurring agent self-modification-write incident (v0.16.8 + v0.17.2 — writes hit `INSTALL_DIR/releases/<ver>/agents/**` and integrity gate rejected). | high (operational safety) | all phases | Constraint **C5** reiterated as a hard rule. Worktree-only writes. |
| **R14** | DB schema changes in a file-based agent edit — research says NONE expected. | low (research says so) | 1, 2 | If a discovery surfaces one, phase 1/2 AC says "ESCALATE LOUDLY" — not silently added. |
| **R15** | Lifecycle inconsistency: sketcher is `skill_injection: true`; designer is not. Critic's `skill_injection` is a **D6** input. | low | 2 | Default `true` in phase 2 if verdict flow uses any skill content; tester confirms by inspecting `meta.json`. |
| **R16** | Closure-grep failure (writing-guide `:91-122`) — system-internals tokens in critic prose fail the prompt-integrity test. | medium | 2 | Phase 2 sub-task includes a closure-grep check (matches the writing guide's pre-commit `:244-258`). |
| **R17** | `view_link` helper existence — research OQ-B says unverified; critic's verdict URL needs a real mint path. **Now moot:** view-views is deferred (Q6.1); critic returns `path + sha256` directly via the verdict schema; the design-review URL rides through the spec's `view_url` (designer's existing exposure). | low | 2 | Resolved by Q6.1 deferral; designer's existing view-views exposure is unchanged. |
| **R18** | `parity-runs.jsonl` disposition (1 historical row of mechanism smoke). | low (hygiene) | 4 | Update schema to v2 in phase 4 (drop `direct`, add `critic`; smoke row stays valid); add SUPERSEDED-style header line at top. |
| **R19** (architect §5.1) | Critic's `tools.allow` reverts to category form on a future edit — re-grants write access (`write_file`/`edit_file` via `filesystem` category). | high (write leak) | 2 + 4 | **Mitigation shipped via D6 canonical form + Tester AC on resolved-tool-set equality** (4 + 1 = 5 tools: `read_file` + `image_get` + `image_list` + `explain_image` + `compare_images`; `image_save` explicitly DENIED in `tools.deny` for read-only leaf purity). |
| **R20** (architect §5.2) | Brief drift across rounds — designer paraphrasing critic findings into re-dispatch briefs burns iterations without progress. | high | 3 | **Cardinal required** (re-dispatch briefs lift `critical_findings` lines VERBATIM; no paraphrase) — mirrors sketcher's verbatim-codes discipline. |
| **R21** (architect §5.3) | Designer revival round-counter bleed — `send_message`-driven revival preserves durable `internal_report` queue but NOT in-memory round counters. Subsumes the pause/resume verdict-loss mitigation. | high | 3 | **Cardinal required:** pin `round_count` per page in `shared_meta_kv` at sketcher dispatch (record `(critic_instance_id, verdict_sha)` per iteration). **Lane resolution (binding):** counter pinned from **designer's** side (designer holds `shared_meta_kv`; critic stays read/denied `shared_meta_kv`). On revival, re-bind from KV — never reset. |
| **R22** (architect §5.9) | Screenshot capture absent until `capture_mockup` ships — critic PASS without a `screenshot_capture:` line = HTML-only QA. | medium | 3 | Record `[VISUAL-QA-DEFERRED]` in the page-handoff memo; full visual QA lands with the future tool (Q7.2 spec, see `design-capture-mockup-spec.md`). **Cardinal required** (not a Guideline). |
| **R23** (architect §5.11) | New-agent boot-scan discoverability — sketcher needed a tier-1 boot-scan fix (`efc460262`); include `agent_registry_scan` in the phase-4 smoke so a critic-dir discovery regression fails loudly. | medium | 4 | **Nit-2 pattern list expansion:** add `agent_registry_scan` to `tests/unit/agents/test_sketcher_agent.py` (reworked to assert critic-dir discovery + a `/home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"` probe — uses the real `daemon.registry.AgentRegistry.exists()` at `daemon/registry.py:1191`; execute as `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && …` for a copy-pasteable form). The venv interpreter prefix is REQUIRED on this host (system `python3` lacks `pydantic`; the worktree has no `.venv`). **Expected stderr noise:** the probe may print `Agent 'maintenancer': deny entry 'git_commit'…` — pre-existing daemon config-scan warning, not a gate failure. |

---

## 7. Success criteria (measurable)

Every criterion below is verifiable by the parallel tester instance. **SC-3 and SC-18 are rewritten** to use backtick-aware greps (the existing text uses backticks around `od.generate` and capital "S" at `workflow.md:105`/`soul.md:55`); a secondary signal checks for the `single-page one-offs stay on my own direct od.generate` pattern lowercased / without the capital "S" / without backticks. SC-9 re-points to the `agent_registry_scan` probe. SC-12 re-points to the reworked rewire test. **SC-15 replaces view-views (deferred per Q6.1) with `design` allow entry.**

| ID | Criterion | Verification path |
|----|-----------|-------------------|
| **SC-1** | Designer file `agents/designer/meta.json` no longer contains any `"od.<token>"` entry (D1=B — all four removed). | `grep -nE '"od\.[a-z_]+"' agents/designer/meta.json` returns 0 hits. |
| **SC-2** | Designer's `team_members` array contains `critic` (added in phase 1 by default per finding 20). | `jq '.team_members | index("critic")' agents/designer/meta.json` returns non-null. |
| **SC-3** | Designer's `workflow.md` contains no occurrence of the kill target (backtick + capital-S form OR lowercase form; both fail). | `grep -ni "single-page one-offs" agents/designer/workflow.md` returns 0 hits. **Secondary signal (broader, per finding 7):** `grep -niE "stay on my own direct|run it on my own direct" agents/designer/workflow.md` returns 0 hits (catches both `Single-page one-offs stay on my own direct` at :105 AND `run it on my own direct od.generate` at :112). **Positive control (today):** `grep -nE "stay on my own direct|run it on my own direct" agents/designer/workflow.md` returns 2 lines (105 + 112); post-phase-3 it must return 0. |
| **SC-4** | Designer's `workflow.md` contains no `## Dual-Run Pilot` section header (deleted by phase 3). | `grep -nE '^## Dual-Run Pilot' agents/designer/workflow.md` returns 0 hits. |
| **SC-5** | Cardinal #7 enum tokens in `rule.md` are verbatim against `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>`. | `grep -nE "tool-not-bound|call-error|timeout|daemon-unavailable|other:" agents/designer/rule.md` returns the exact five tokens (no paraphrase). **Positive control:** `grep -cE "tool-not-bound\|call-error\|timeout\|daemon-unavailable\|other:" agents/designer/rule.md` returns 0 (literal pipes absent — proves the `\|` form is false-green); `grep -cE "tool-not-bound|call-error|timeout|daemon-unavailable|other:" agents/designer/rule.md` returns 2 (lines 15 + 38, today, pre-edit). |
| **SC-6** | `agents/critic/meta.json` exists, is valid JSON, contains `"id": "critic"`, `team_members: []` (DECLARED), hardened `tools.deny`, canonical D6 form `tools.allow: ["read_file", "image", "design"]`, and `image_save` explicitly listed in `tools.deny` (read-only leaf purity). | `/home/nea/ensemble-src/.venv/bin/python3 -c 'import json; d=json.load(open("agents/critic/meta.json")); assert d["id"]=="critic" and d["team_members"]==[] and d["tools"]["allow"]==["read_file","image","design"] and "image_save" in d["tools"]["deny"] and all(k in d["tools"]["deny"] for k in ["bash","proc","instance","service","midflight","shared_meta_kv","infra","mcp"])'` returns 0. |
| **SC-7** | `agents/critic/soul.md` first-person, no system-internals tokens (closure-grep per writing guide `:91-122`). | `grep -nE 'meta\.json|tools\.allow|daemon/|skill-set\.yaml|seeder|version registry|test paths' agents/critic/soul.md` returns 0 hits. **Positive control:** the same pattern returns 0 against `agents/designer/soul.md` today (the closure-grep is currently clean — proves the pattern is not false-green). |
| **SC-8** | `agents/critic/rule.md` ≤7 Cardinals (writing guide cap), contains Cardinal #1 verdict protocol binding + malformed-verdict discipline + regex parse rule + `pinned_spec_sha` mandate (D3 amendments). | Manual review + `grep -cE '^[0-9]+\.' agents/critic/rule.md` returns ≤7 numbered Cardinals; `grep -nE "verdict block|prev_attempt_unparseable|pinned_spec_sha|verdict:\s*\((pass|needs-revision)\)" agents/critic/rule.md` returns ≥4 hits. **Positive control:** the count grep returns 7 against today's `agents/designer/rule.md` (lines 9-15), proving the cap is not false-green; the four-hit grep is fixture-tested against the planned critic rule.md text. |
| **SC-9** | Registry discovery scans `agents/critic/meta.json` and returns the new agent (`agent_registry_scan` pattern — replaces the nonexistent `python -m daemon.registry --list-agents`). | `/home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('critic')"` returns 0 (counterpart to the tier-1 boot-scan fix `efc460262`; uses the real `get_registry()` facade at `daemon/registry.py:1207` with `exists()` at `:1191` — NOT the `daemon.plugin_subsystem.plugin_registry.load_registry()` path which requires positional `plugins_root` and scans plugins not agents). The venv interpreter prefix is REQUIRED on this host (system `python3` lacks `pydantic`; the worktree has no `.venv`). **Positive control (copy-paste form):** `cd /home/nea/ensemble-src-wt-designer-critic-orchestration && /home/nea/ensemble-src/.venv/bin/python3 -c "from daemon.registry import get_registry; assert get_registry().exists('sketcher')"` returns 0 (sketcher exists today); the same command with `'critic'` is expected to FAIL until phase 2 lands (today's failure mode proves the gate is real — expected stderr shape: an `AssertionError` traceback). **Expected stderr noise (both probes):** `Agent 'maintenancer': deny entry 'git_commit'…` — pre-existing daemon config-scan warning, not a gate failure. |
| **SC-10** | Reporter integrity test passes for critic. | `pytest tests/unit/test_report_integrity_prompts.py -k critic` returns PASS. |
| **SC-11** | Designer's `workflow.md` post-orchestration block contains the explicit pipeline shape: `designer → sketcher → critic → designer accept/save`. | `grep -nE "sketcher.*critic|critic.*sketcher" agents/designer/workflow.md` returns ≥1 hit in the Orchestration section. |
| **SC-12** | The reworked `tests/unit/plugin_subsystem/test_designer_rewire.py` runs clean against the new binding (inverse the od.* call-site pins to ZERO; SPEC-style rewire pins the four new Port-style tools in `sketcher/` not designer/). | `pytest tests/unit/plugin_subsystem/test_designer_rewire.py` returns PASS. |
| **SC-13** | Worktree branch `feature/designer-critic-orchestration` contains one clean commit per phase (four commits expected). | `git log feature/designer-critic-orchestration --oneline -n 10`. |
| **SC-14** | No writes outside the worktree occurred during this commission. | `sha1sum /home/nea/ensemble-src/agents/designer/meta.json` shows the original (unmodified) hash; `git diff feature/designer-critic-orchestration -- agents/designer/meta.json` shows the diff is in the worktree. |
| **SC-15** | `design` is in `agents/critic/meta.json` `tools.allow` (view-views is **deferred per Q6.1** — replaces the prior view-views check). | `jq '.tools.allow | index("design")' agents/critic/meta.json` returns non-null. **Cross-check:** `jq '.tools.allow | index("view-views")' agents/critic/meta.json` returns null (proves view-views is NOT granted — deferral honored). |
| **SC-16** | Freeze-set `_tool_registry.py` is unchanged. | `git diff feature/designer-critic-orchestration -- daemon/tools/_tool_registry.py` returns empty. |
| **SC-17** | The `## Dual-Run Pilot` section reference in `stage2-addendum.md` is journaled-superseded via the **single-line HTML-comment append** added by phase 1 T12 (not a phase-4 edit). | `head -1 .agents/shared/planning/od-generate-agent-lane/stage2-addendum.md` shows the `<!-- SUPERSEDED 2026-10-10 by designer-critic-orchestration ... -->` marker; line count = original + 1. |
| **SC-18** | `agents/designer/soul.md` does not contain the phrasing "single-page one-offs stay on my own direct od.generate" (mirror kill from `workflow.md:105`). | `grep -ni "single-page one-offs" agents/designer/soul.md` returns 0 hits. **Secondary signal (broader, per finding 7):** `grep -niE "stay on my own direct|run it on my own direct" agents/designer/soul.md` returns 0 hits. **Positive control (today):** `grep -nE "stay on my own direct|run it on my own direct" agents/designer/soul.md` returns 1 line (:55); post-phase-1 it must return 0. |
| **SC-19** | Workflow Review block = canonical home of the verdict schema. The planning-dir `critic-verdict-schema.md` is a frozen pin; critic's `workflow.md` Review block must quote the schema verbatim (by section reference, not filename). | `grep -nE "^## Review|^## Verdict|verdict:\s*(pass|needs-revision)" agents/critic/workflow.md` returns ≥3 hits (heading + verdict line + content tie); `cat agents/critic/workflow.md | wc -l` ≤ 60. |
| **SC-20** | Designer `rule.md` contains the three architect-mandated controls (KV round-counter + verbatim-lift brief + [VISUAL-QA-DEFERRED] marker — encoded as Guidelines (e)–(g) per cap-7, NOT numbered Cardinals; see phase 3); ≤7 numbered Cardinals (cap from writing guide). | `grep -cE '^[0-9]+\.' agents/designer/rule.md` returns ≤7; `grep -nE "shared_meta_kv|verbatim|VISUAL-QA-DEFERRED|round_count" agents/designer/rule.md` returns ≥3 hits (one per control). **Positive control:** the count grep returns 7 today (lines 9-15 of designer's `rule.md`); the three-keyword grep returns 0 today (architect-mandated controls land in phase 3). |
| **SC-21** | Verdict schema doc pins all four D3 amendments. | `grep -cE "\[BRIEF-LEVEL\]|prev_attempt_unparseable|verdict:\s*\((pass|needs-revision)\)|pinned_spec_sha" .agents/shared/planning/designer-critic-orchestration/critic-verdict-schema.md` returns ≥4 hits (one per amendment). **Positive control:** the same pattern returns 6 against `architecture-recommendation.md` (lines 3, 47, 51, 75, 124 — proves the four-keyword alternation is not false-green and the third alternative's paren-escapes work). |

---

## 8. Decision-gate map (cross-reference to `architecture-recommendation.md`)

Every phase cites the architecture doc sections it consumes. The binding text is `architecture-recommendation.md`; the planner's `open-design-decisions.md` is the historical record. Phase tables include a `Depends on (D#)` column; the sections consumed are surfaced here.

| Decision key | Phase using it | Architecture-recommendation.md section consumed |
|--------------|----------------|--------------------------------------------------|
| **D1=B** (broad removal — designer holds zero `od.*`) | 1, 2, 3 | §1 verdict table D1 row + §2 D1 ruling (:29-35) |
| **D2** (async by default, ≥400s sync floor only) | 3 | §2 D2 ruling (:37-41) |
| **D3 + 4 amendments** (verdict contract; [BRIEF-LEVEL] tier; malformed-verdict→critic re-dispatch; regex parse; `pinned_spec_sha`) | 1, 2, 3 | §1 verdict table D3 row + §2 D3 ruling (:43-52) |
| **D4** (≤3 rounds per page, severity-gated: advisory→disclose, critical→escalate; truncated→sketcher-internal) | 3 | §1 verdict table D4 row + §2 D4 ruling (:54-60) |
| **D5 + 4 refinements** (text lane re-key: `lane_preference: text-native` OR same-error-code-class exit; structured `other:user-requested-text-only` / `other:proxy-ceiling-N`) | 1, 3 | §1 verdict table D5 row + §2 D5 ruling (:62-72) |
| **D6** (canonical `allow: ["read_file","image","design"]`; `image_save` explicitly DENIED for read-only leaf purity; `mcp` deny mandatory; innate-skill auto-grant via `INNATE_SKILL_TOOL_CATEGORIES`) | 2, 4 | §1 verdict table D6 row + §2 D6 ruling (:74-104) |
| **D7** (pilot gates superseded-in-place by phase 1 T12; parity-runs.jsonl schema v2; ~30-line context pointer = Option C; `design.capture_mockup` promoted spec-only; cap-math stays deferred) | 1, 3, 4 | §1 verdict table D7 row + §2 D7 ruling (:105-109) |

---

## 9. Assumptions and open questions

### 9.1 Assumptions (recorded; not blocking)

- **A1.** `architecture-recommendation.md` is the binding text; `open-design-decisions.md` is the historical decision record. Both exist; `architecture-recommendation.md` is in the worktree.
- **A2.** Sketcher v0.18.5 is already shipped live and the worktree version matches live. No sketcher source change is in this plan's scope.
- **A3.** The next promote ceremony is OUT of scope; the worktree commit becomes the release vehicle.
- **A4.** The writing guide (`docs/agent-prompt-writing-guide.md`) is normative and unchanged for this commission.
- **A5.** The `team_members` gate at `daemon/tools/instance.py:2597-2617` is unchanged; the gate's only edit is the value of the team's array contents.
- **A6.** Phase ordering IS phase 1 → phase 2 → phase 3 → phase 4 (sequential).
- **A7.** Designer's existing Cardinal #5 ("End turn after `send_message`") survives unchanged.
- **A8.** KV round-counter is pinned from **designer's** side (designer holds `shared_meta_kv`; designer's Cardinal); critic stays read/denied `shared_meta_kv`. (Council lane resolution binding per finding 8a.)

### 9.2 Open questions (escalation by phase)

- **OQ-A.** View-views for critic = DEFERRED per Q6.1 (user policy decision; directive does not authorize a 4th commissioned user). Resolved.
- **OQ-B.** Image comparator (`design` category) = EXISTS per architect verification (`compare_tools.py:1217`; `_auth.py:50`). Approved: expose via `design` allow.
- **OQ-C.** Design-category comparator symbol at the daemon-side = YES (architect-verified). Approved: include via `tools.allow: [..., "design"]`.
- **OQ-D.** Iteration cap value for **D4** = ≤3 per page (architect verdict). Resolved: severity-gated terminal.
- **OQ-E.** Fan-in shape = page-at-a-time (architect verdict). Resolved: critic's verdict feeds the next brief; parallel N×(sketcher+critic) loses the feedback chain and multiplies semaphore pressure.

---

## 10. Verification handoff to the tester (parallel tester instance)

The tester instance runs the verification surface in `phase4-plan.md` + the **success criteria** above (SC-1 through SC-21). The developer and tester instances work the same branch and may iterate on remediation commits. Expected remediation cycle is ≤2 (per designer's conformance-loop pattern, ≤3 reserved).

The tester does NOT re-derive decisions; if a decision conflict surfaces, the tester records it as a `BLOCKER` and routes to the planner. **Tester-owned edits** to the two test files (rework the parity pins + the rewire pins) are explicitly assigned to phase 4 — non-workflow edits elsewhere trigger hard escalation to the planner, NOT silent edits.

---

**End of plan overview.** See `phase1-plan.md` next.
