# Architecture Recommendation — designer-critic-orchestration (D1–D7 enrichment)

**Date:** 2026-10-10
**Architect instances:** 6c09f183 (data-flow-design), 97a0ad50 (resilience-design), f137d8ce (structural-design) + architect aggregation
**Plan base:** v0.18.5 @ `0c040e5f8` (sketcher merged), worktree `feature/designer-critic-orchestration`
**Status:** COMPLETE — all D1–D7 adjudicated, all curated open questions resolved
**Method:** 3-dimension competitive fan-out (pipeline data-flow / resilience / agent anatomy), architect adjudication on evidence, load-bearing claims spot-verified against code.

---

## 1. Verdict table (D1–D7)

| # | Decision | Verdict | One-line rationale |
|---|----------|---------|--------------------|
| **D1** | od.* tool distribution | **RATIFY — broad (Option B)** | Sketcher holds all four `od.*`, designer holds zero; "designer composes brief" = content authorship in text (workflow.md:109), sketcher executes `od.compose_brief` as formatter (sketcher/workflow.md:44) — single OD owner, no zombie tokens. |
| **D2** | critic invocation mechanics | **RATIFY — async `send_message`** | Removes the 300s sync-timeout footgun (utils.py:630) and the semaphore cap (utils.py:680-681) from the surface; mirrors the established sketcher dispatch pattern (designer/workflow.md:111). |
| **D3** | critic verdict structure | **RATIFY + 4 refinements** | Option A block is right (directive verbatim; tester/governor precedent), but needs `[BRIEF-LEVEL]` tier, malformed-verdict discipline, regex-anchored parse, and a `pinned_spec_sha` field (comparator binding requirement). |
| **D4** | iteration limits | **RATIFY + severity gate** | ≤3 rounds per page (matches workflow.md:122-129 precedent); at cap: advisory-only → accept-with-disclosure, any critical remaining → **escalate-only** (the precise reading of "iterates or accepts/saves"). |
| **D5** | Cardinal #7 text-fallback | **RATIFY Option A + 4 refinements** | Text lane survives as explicit last resort; binding re-keyed to sketcher-dispatch outcomes; enum stays verbatim via structured `other:<detail>`; same-error-code-class exit on retry; round-count pinned in shared KV. |
| **D6** | critic anatomy (tools/model/spawns) | **RATIFY intent — REPLACE both concrete tool specs** | Phase2 category form is structurally unsafe (`filesystem` expands to `write_file`/`edit_file`); D6 bare-name list misses the verified `design` comparator. Canonical: `allow: ["read_file", "image", "design"]`. Vision pin + leaf ratified. **Watchover row: drop** (semantics clarification §5.4). |
| **D7** | pilot-gate & doc reconciliation | **RATIFY all** | Supersede-in-place (file kept); parity-runs v2 schema; ~30-line context pointer; `design.capture_mockup` promoted **spec-only**; cap-math gap stays deferred. |

**No DB schema change is implied by any verdict.** All changes are agent prompt/meta files + planning docs. Zero migrations.

---

## 2. Per-decision rulings (evidence-cited)

### D1 — broad removal wins

- Designer exercises all four `od.*` today in the single-page path (`agents/designer/workflow.md:84-89`); narrow removal leaves three zombie tokens with no exercised code path (`workflow.md:124-129` uses browser-capture visual QA, not `od.lint`; `:109` hand-types briefs, not `od.compose_brief`).
- Sketcher Cardinal #4 ("write-through or it did not ship", `agents/sketcher/rule.md:12`) makes `od.save` generation-class work — splitting it reintroduces the blast-radius confusion the directive removes.
- **The pipeline phrase resolved:** "designer composes brief" = designer AUTHORS brief content (text); sketcher executes `od.compose_brief` tool-internally as the formatter. The alternative reading (designer holds `od.compose_brief`) is rejected — it would split the OD surface across two agents and re-expose designer to OD-lane drift.
- Reversibility high: re-adding tokens is a meta.json edit.
- **Phase impact:** designer `meta.json` strips all four tokens; `team_members` gains `critic` (`["worker", "sketcher", "critic"]`); workflow.md sweeps now-unreachable references.

### D2 — async dispatch

- Sync costs: 300s default timeout silently trims long reviews (the documented footgun `workflow.md:116` guards); semaphore `WORKER_POOL_SIZE - 1` halves the pool on multi-page runs; worst case 400s+ single-threaded hold per iteration.
- Async: designer ends turn after dispatch (Cardinal #5 shape), resumes on `internal_report:{critic_instance_id}:{completed_message_id}` — a distinct dedup key from sketcher reports, no cross-instance collision (`child_reports.py:3498-3510`).
- Critic Cardinal mirrors sketcher dispatch: "End turn after critic `send_message`."

### D3 — verdict contract (ratified shape + amendments)

Base schema as pinned in `open-design-decisions.md:188-204`, amended:

1. **Third tier `[BRIEF-LEVEL]`** for defects traced to brief inputs, not the artifact — prevents designer burning artifact-level iterations on a brief-level defect.
2. **Malformed-verdict discipline:** critic Cardinal makes the block MANDATORY; on parse-fail designer re-dispatches **critic** (with `notes: prev_attempt_unparseable`), NOT sketcher — a degraded re-dispatch to sketcher regenerates the same input and burns rounds on a parse failure, not a quality failure.
3. **Parse rule:** regex `^verdict:\s*(pass|needs-revision)\s*$` anchored on the verdict line — substring scans mis-fire when critic prose quotes "verdict:" in evidence.
4. **`pinned_spec_sha` field added** to the verdict block. `compare_images` requires `pinned_spec_sha` to make comparison verdicts binding rather than advisory (`daemon/tools/compare_tools.py:1265-1269`); without the field, critic's compare path is permanently advisory. Designer passes the spec SHA in the dispatch envelope.

Resolved sub-questions: **Q3.1** PASS-with-advisories terminates the loop (advisories ride into the spec as fix-up inputs; conformance tests must assert this is a valid terminal verdict). **Q3.2** review covers both spec acceptance criteria (source of truth) and brief inputs (alignment check). **Q3.3** brief-inherited defects → `[BRIEF-LEVEL]`. **Q3.4** missing artifact → `verdict: needs-revision` + `[CRITICAL] artifact not found at <path>` — same machine-parseable shape.

### D4 — ≤3 rounds, severity-gated terminal

- **Q4.2 ruling (the interpretation call):** the user's "designer iterates or accepts/saves" grants designer terminal agency, but NOT blanket override authority. At round 3:
  - **advisory-only remaining → accept-with-disclosure** (two-place disclosure: `[REVIEW-CAVEAT]` line on the spec + `review_caveat: <verdict text>` field on the implement-brief page entry so the developer consumes it directly);
  - **any critical remaining → escalate-only** (structured gap report: artifact + 3 verdict blocks verbatim).
- **Q4.3** per-page cap. **Q4.4** sketcher's regenerate-once is a transparent sub-step (independent budget). Refinement: `[CRITICAL] artifact incomplete / missing markers` **with** `truncated: true` in the envelope is sketcher-internal (re-dispatch with `notes: previous attempt truncated`), NOT a charged round; without `truncated: true` it is a real round.
- **Wall-clock record (corrects planner):** nominal worst case ≈ 3 rounds × (2 × 170s generation + 170s critic + integration) ≈ **~26 min/page**; under persistent-524 conditions the Q5.4 exit rule cuts the chain to ≤2 dispatches → **escalation within ~10 min**. The planner's "~52 minutes per page" (`open-design-decisions.md:263`) is a unit error (524 is the HTTP status code, not seconds) — strike it. See §5.3 for the adjudication of the two worker figures.

### D5 — text lane, re-keyed binding

New binding condition (replaces `rule.md:35`), amended from the planner draft:

> The text-native lane is reachable when (a) the deliverable is text-native per user request (`lane_preference: text-native` on the leader brief; absence = `generation`), OR (b) sketcher dispatch failed with the SAME `error.code` class on the initial call and the one retry (Q5.4 exit). Both cases emit `fallback_reason` from the existing enum. Cardinal #7 applies unchanged.

- **Q5.1** YES — text lane is legitimate for non-mockup deliverables (specs, comparisons, audits); "generation lane" means mockup artifacts. Codify via `lane_preference` flag (prompt-level field, no schema/DB change).
- **Q5.2** reuse the enum via **structured** `other:<detail>`: `other:user-requested-text-only` (case a) and `other:proxy-ceiling-N` where N = consecutive-failure count (case b). Enum tokens stay verbatim; tester gate untouched; detail stays parseable.
- **Q5.3** Cardinal #7's tester gate survives unchanged.
- **Q5.4** retry sketcher dispatch **once**, carrying `notes: prev_error_code=<code>`; **exit condition:** same `error.code` class on retry → escalate (the page is proxy-ceiling-blocked, not transient). Different codes between attempts = flapping → one more attempt permitted.
- Amendment to planner's "≥2 consecutive rounds" trigger: replace with the same-code-class condition above — it removes the ambiguity against D4's round budget (a round-2 `timeout` with a different round-1 code is flapping, not ceiling).

### D6 — critic anatomy (canonical spec)

**Both prior tool specs are superseded.** Verified mechanics: `tools.allow` entries matching a category key expand to EVERY tool in that category (`daemon/tools/instance.py:475-482`); `filesystem` carries `write_file` (`filesystem.py:880`) and `edit_file` (`:1233`) — the phase2 category form would grant the critic write access (🔴, §5.1). The comparator EXISTS: `compare_images` registered under `design` (`compare_tools.py:1217`), `TOOL_REQUIRED_AGENTS` maps `design → ["image-comparator"]` (`_auth.py:50`).

Canonical `agents/critic/meta.json` tools block:

```json
{
  "id": "critic",
  "llm_model": "vision",
  "innate_skills": ["dynamic-skill", "todo"],
  "skill_injection": true,
  "recursion_limit_multiplier": 7,
  "tools": {
    "allow": ["read_file", "image", "design"],
    "deny": ["bash", "proc", "instance", "service", "midflight",
             "shared_meta_kv", "infra", "mcp", "image_save"]
  },
  "team_members": []
}
```

- `read_file` bare (NOT `filesystem`) — blocks the write-tool expansion; canonical mockup path is deterministic (`save.py:67-72`), no glob/grep needed (**Q6.4**).
- `image` covers `image_get`/`image_list`/`explain_image` (all registered under `image`).
- `design` covers `compare_images` (**Q6.3 = exists**). Note: this auto-extends EFFECTIVE team membership with `image-comparator` for the spawn check (`_auth.py:155-161`) — document in critic's `tools_note.md` that `team_members: []` is the DECLARED list while the effective list is non-empty.
- `dynamic-skill` + `todo` auto-grant via `INNATE_SKILL_TOOL_CATEGORIES` (`instance.py:161-168`) — intentionally absent from allow.
- `mcp` deny is mandatory for a leaf (the mcp allowlist check fires only when `"mcp" in allow`, `instance.py:296-334`).
- `image_save` explicit deny entry (Pass-5 alignment — the executing phases already carry it, phase2 T3/AC2; the binding literal must not contradict its consumers): the `image` allow category resolves to FOUR tools including `image_save` (`image_tools.py:482/:669/:841/:933`); the bare-name deny strips it (deny subtracts bare tool names, deny-wins — `instance.py:379-393` docstring + `:484-492` subtraction loop). Read-only leaf purity per finding 9 adjudication.
- **Q6.1** view-views: DEFER (user policy decision; directive does not authorize a 4th commissioned user). **Q6.2** vision pin: RATIFIED.
- Phase2 locals: `recursion_limit_multiplier: 7` RATIFIED (critic loops zero times); `skill_injection: true` RATIFIED; `watchover.timeout_seconds` row **DROPPED** (§5.4 — inert on a non-watcher; if kept for symmetry with sketcher it must be documented as inert). `default_queue`: no entry (default `system_queue` is correct — critic is one-per-page, not parallel).
- **Tester AC (🔴 guard):** conformance must assert the resolved tool set equals the expected 4+1 tools — this is the gate that catches the filesystem-category write leak.

### D7 — doc reconciliation

All four sub-decisions RATIFIED: pilot gates superseded in-place (file kept, phase-1 T12 marker); `parity-runs.jsonl` schema v2 (`lane: sketcher|critic`, drop `direct`; smoke row stays valid); ~30-line context pointer for main-checkout prior art (Option C); cap-math gap deferred with carry-forward note. **Q7.1** same file. **Q7.2** YES — `design.capture_mockup` promoted **spec-only** into Phase 2 (`design-capture-mockup-spec.md`; tool implementation stays a future build). **Q7.3** 30 lines sufficient. **Q7.4** stays deferred.

**Q1.2 — capture responsibility placement:** **sketcher captures + saves** as part of its `od.save` write-through; **critic reads + compares**. The manual capture recipe migrates the `## Capture Procedure` section of `agents/designer/tools_note.md` (header-delimited — heading `:55` through the `---` delimiter at `:140`, incl. `### Provenance tag policy` (:124-130) + `### Failure modes I expect` (:132-138); Pass-6 alignment so the binding source matches the executing phases' T7/AC8/T12 form and never cites a mid-fence-cutting range) → `agents/sketcher/tools_note.md`. Designer drops the post-save visual QA section (workflow.md:122-129). No tool elevation this commission.

---

## 3. Curated open-question resolutions (leader's list)

| # | Question | Resolution |
|---|----------|------------|
| Q1.1 | Narrow vs broad od.generate removal | **Broad** — designer holds zero `od.*` (D1). |
| Q4.2 | Accept-with-disclosure vs escalate at round 3 | **Severity-gated:** advisory-only → accept-with-disclosure (`[REVIEW-CAVEAT]` + `review_caveat` field); critical remaining → escalate-only (D4). |
| Q5.1 | Text lane for non-mockup deliverables | **Reachable** — via `lane_preference: text-native` on the leader brief (default `generation`). |
| Q5.2 | Enum extension vs `other:<detail>` | **`other:<detail>`, structured:** `other:user-requested-text-only`, `other:proxy-ceiling-N`. Enum tokens verbatim-untouched; tester gate green. |
| Q2.3 / OQ-E | Fan-in shape | **Page-at-a-time** — the verdict feeds the next brief; parallel N×(sketcher+critic) loses the feedback chain and multiplies semaphore pressure. Revisit only if multi-page campaigns with independent pages dominate later. |
| Q7.2 | capture_mockup promotion | **YES, spec-only** into Phase 2; implementation future build (D7). |
| Nit 1 | D6 bare-name vs phase2 category phrasing | **Neither as-written — canonical hybrid** (D6 §2): `allow: ["read_file", "image", "design"]`. Category form is unsafe (write expansion); old bare list misses the comparator. |
| Nit 2 | Pre-promote `-k critic` smoke | **CONFIRMED.** Gate lives in **phase-4 AC** (not the promote checklist — promote already carries live-OD smoke). Unit-level only, zero live OD/LLM calls, minutes-bounded. Pattern: `pytest -k "critic_meta or critic_tools_resolve or critic_deny_wins or critic_team_implied or designer_od_generate_removed or parity_runs_v2_schema or agent_registry_scan" -x` — covering meta validation, resolved-tool-set equality (the write-leak guard), deny-strips-allow, implicit team expansion, designer token removal, schema v2 validity, and new-agent-dir discoverability. |

Remaining planner sub-questions resolved inline above: Q1.3 NO (no brief migration), Q2.1 NO in-turn case, Q2.2 no verdict-drop (pause/resume mitigation §5), Q3.1–Q3.4 (D3), Q4.1/Q4.3/Q4.4 (D4), Q5.3/Q5.4 (D5), Q6.1–Q6.4 (D6), Q7.1/Q7.3/Q7.4 (D7).

---

## 4. Infra flag: the 120s proxy ceiling (NOT in scope — carry prominently)

**Root cause.** `od.generate` routes through `invoke_raw_with_failover` with `wall_clock_cap_s=420` (`generate.py:630/:827`); per-request timeout = `max(120.0, max_tokens/370.0)` — 173s at default 64K (`generate.py:934`). The 120s ceiling lives on the **llm-supervisor-proxy** (Cloudflare 524 on read timeout) — outside ensemble's control surface; fixing it is a separate commission (constraint C1).

**Does the sketcher lane hit it? YES.** The stage-1 wiring moved the LLM call inside `od.generate` onto the ensemble LLM lane — same adapter, same facade, same proxy URL. Sketcher's regenerate-once does not bypass it; both attempts hit it.

**Failure taxonomy (3 classes):** client timeout (`APITimeoutError`, retried as transient) / proxy 524 (`APIStatusError`, retried, cap-bounded) / truncation at attempt boundary (3 completeness gates refuse — `generate.py:1032-1057` — typed envelope). Each failed attempt burns the full ceiling (~120s); ~3 attempts fit the 420s cap.

**Amplification:** sketcher regenerate-once (×2 calls) × designer ≤3 rounds — bounded by the D4 budget and cut hard by the Q5.4 same-code-class exit. **This commission ships the architecture; the ceiling stays a live risk on every generation call until the future commission lands.** Every 524-driven round-3 escalation under this architecture is an infra escalation, not a design failure.

**Future-commission sketch — async submit→poll wrapper (the likely fix shape; previously deferred 2026-10-03).**
- **Direction:** decouple the proxy ceiling from the caller's wait — submit returns a handle fast; generation runs under a daemon-side supervisor; caller polls.
- **Rejected alternatives:** raise proxy window per-route (outside our control), streaming keepalive (proxy-side), chunked generation (prompt duplication + coherence risk).
- **Touch surface:** `ports.py:61-102` (+`mode: sync|async` default sync), `generate.py` (+`execute_async`), NEW `poll.py` read side, sketcher workflow adoption (poll cadence 15s, 5-min total). Untouched: Cardinal #7 enum, regenerate-once budget, completeness gates. Reuse precedent: `question_manager.py` submit→poll supervision.
- **New risk to design for:** supervisor crash → typed `upstream_supervisor_unavailable`; 15-min TTL on stale handles.

---

## 5. Risks the planner missed (severity-ordered; includes adjudicated worker conflicts)

**🔴 5.1 Phase2 category form grants critic WRITE access.** `filesystem` in `tools.allow` expands to `write_file`/`edit_file` (`instance.py:475-482`; `filesystem.py:880/:1233`); the phase-2 deny list didn't catch them (deny DOES subtract bare tool names — deny wins over allow-expanded categories, `instance.py:379-393`, subtraction loop `:484-492` — so a per-write-tool bare deny WOULD strip them; the phase-2 deny list simply omitted `write_file`/`edit_file`, and the canonical bare-allow form removes the dependence on deny maintenance). Adopting phase2-plan.md's D6 row verbatim violates the reviewer posture. **Mitigation shipped in D6 canonical spec + tester AC on resolved-tool-set equality.** *(Architect-verified against code; parenthetical corrected Pass-5 — the prior "deny strips categories, not tools inside allow-listed categories" wording was wrong.)*

**🔴 5.2 Brief drift across rounds.** Designer paraphrasing critic findings into re-dispatch briefs burns iterations without progress. **Cardinal required:** "Re-dispatch briefs lift `critical_findings` lines verbatim; no paraphrase" (mirrors sketcher's verbatim-codes discipline).

**🔴 5.3 Designer revival round-counter bleed.** `send_message`-driven revival (`instance_messaging.py:~2224-2254` — the terminal-revival carve-out block; cite corrected Pass-5 from the stale `:1486-1510`, shared-blueprint drift) preserves the durable `internal_report` queue but NOT in-memory round counters. **Cardinal required:** pin `round_count` per page in shared KV at sketcher dispatch; on revival, re-bind from KV — never reset. (Subsumes the pause/resume verdict-loss mitigation: record `(critic_instance_id, verdict_sha)` per iteration.)

**🟡 5.4 Watchover row — semantics adjudication (worker conflict resolved).** The resilience worker's 🔴 ("90s watchover terminates a 170s pixel review") is **falsified in mechanism**: the `watchover` meta section configures the WATCHER agent's watchover-dialog (`graph.py:10751-10772` reads `agents/watcher/meta.json`), not a kill-timer on the carrying agent — sketcher's own 90s block coexisting with 130–170s generations is the live counter-proof. The structural worker's "90s is enough" reaches the right outcome but implies the knob bounds the review (it doesn't — it is inert on a non-watcher). **Ruling: drop the row from critic meta** (dead config on a leaf is drift bait); if kept for sketcher symmetry, document as inert. Downgraded to doc-hygiene.

**🟡 5.5 Wall-clock record (worker conflict resolved).** data-flow figure 17.25 min/page omits the regenerate-once multiplier; resilience figure 30–50 min stacks retry valves the Q5.4 exit rule terminates. Adjudicated record: **nominal worst ≈ 26 min/page** (3 rounds × (2 × 170s + 170s critic + integration)); **persistent-ceiling chains escalate within ~10 min**. Planner's 52-min figure struck (unit error).

**🟡 5.6 `pinned_spec_sha` gap in D3 schema** — without it the comparator verdicts are permanently advisory (`compare_tools.py:1265-1269`). Fixed in D3 amendment 4.

**🟡 5.7 Context growth across 3 rounds** — designer's multiplier 7 sized for 1-round cycles; ~30 messages on a 5-page run. Mitigate: in-flight state stores iteration count + verdict SHA, not full history; briefs reconstructed from spec + page list at dispatch.

**🟡 5.8 Truncation↔critic chaining** — handled in D4 (truncated + incomplete = sketcher-internal, not a charged round).

**🟡 5.9 Screenshot capture absent until capture_mockup ships** — critic PASS without a `screenshot_capture:` line = HTML-only QA; record `[VISUAL-QA-DEFERRED]` in the page handoff memo; full visual QA lands with the future tool (Q7.2 spec).

**🟡 5.10 Canonical mockup path contention** (pre-existing, not worsened) — two parallel designer commissions on one feature slug: last writer wins at `save.py:67-72`. Recommend KV `lock_owned_by:<instance_id>` around page dispatch. Flagged for traceability; owner: future hardening.

**🟡 5.11 New-agent boot-scan discoverability** — sketcher needed a tier-1 boot-scan fix (`efc460262`); include `agent_registry_scan` in the phase-4 smoke so a critic-dir discovery regression fails loudly.

**🟢 5.12** Effective-team-members doc note (D6). **🟢 5.13** Single-page one-off cost doubles (~340s vs 170s direct) — directive-accepted, recorded. **🟢 5.14** PASS-with-caveats conformance test must assert the terminal-verdict validity (Q3.1). **🟢 5.15** A/B judge job (C4): no collision — read-only surface, untouched; future note: record `lane_generated_under` when new-pipeline snapshots reach it.

---

## 6. Constraints compliance

- **C1 (120s proxy):** framed §4, not fixed here. ✔
- **C2 (no DB schema):** no verdict implies a migration — all prompt/meta/planning-doc level. ✔
- **C3 (promote-gated):** nothing here touches live; ships at next nonce ceremony. ✔
- **C4 (A/B judge):** untouched, no collision (§5.15). ✔
- **C5 (worktree-only):** this document is the only architect write; workers were read-only. ✔

## 7. Decisions pending (user/leader) & gaps

**Pending:** (1) severity-gated accept-with-disclosure (D4/Q4.2) is the architect's interpretation of "accepts/saves" — surface for user ratification at plan review; (2) view-views for critic (deferred, user policy); (3) scheduling the 120s-proxy future commission (§4 sketch is the input); (4) `lane_preference` flag adoption in leader briefs (prompt-level).

**Gaps/caveats:** Worker C omitted the `Skill loaded:` first-line confirmation (protocol deviation; its load-bearing claims were independently verified by the architect against code — filesystem expansion, comparator registration — and its D6 conclusion upheld with amendments). Workers A and B both confirmed skill load. No coverage gaps remain: all three dispatch scopes reported.

## 8. Confidence

**High.** Every verdict is code-cited; both worker conflicts (watchover semantics, wall-clock math) were adjudicated on direct code evidence and live counter-examples. **Flip assumption:** if `tools.allow` category-expansion semantics ever change to per-tool opt-in, D6's canonical form could revert to the simpler category list — re-verify at build time via the resolved-tool-set tester AC.
