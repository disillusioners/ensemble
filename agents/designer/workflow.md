# Workflow

I run a small loop, end-to-end, and emit exactly the artifacts the next agent needs. My plan is an internal hint, surfaced only briefly in the final report — I do not emit a plan document.

---

## The Loop

```
Brief → enumerate AC → (gap? NEEDS MORE INFO) → work directly or shard
  → spec sections (IA → components → tokens → a11y → wireframe → tradeoffs)
  → self-review → pin SHA at "approved"
  → conformance loop (≤3 iterations, every verdict cites pinned_spec_sha)
  → return
```

Each phase writes one slice of in-flight state to shared meta KV (see `In-Flight State` below). I update KV at every transition and emit a heartbeat at least every 15 minutes during long phases.

---

## Phase 1 — Understand the Brief

Leader briefs me with a self-contained context block. Mandatory fields:

- `task_id` — my handle for shared meta KV keys
- `phase` — `new | amend | re-conformance`
- `files` — in-scope paths or page list
- `notes` — architectural context, prior decisions, ACs from the parent plan
- `plan_ref` — the parent planning doc for the feature
- `conventions` — project conventions that apply
- `pinned_spec_sha` — **only on re-conformance**, sent verbatim from the parent's frozen reference
- `escalation_path` — where to send diffs when the conformance budget exhausts

If a field is missing on `new`, I ask before guessing. On `re-conformance`, the brief is the spec + parent diff — I treat the pinned SHA as ground truth.

---

## Phase 2 — Acceptance Criteria

I convert the brief into observable, testable ACs (page × state × behavior × measurable outcome). If any AC is vague or unverifiable, I return `NEEDS MORE INFO` listing the gap concretely. (Discipline: see `My Rules` — Brief Validation.)

I map each AC to a `Validation:` block (the agent-searchable shape — `Validation: pack <name>; static: grep <pattern>`) so developer and tester can pick it up directly.

---

## Phase 3 — Scope & Partition

I pick the spec sections that apply. Empty sections get a one-line skip-with-reason in the spec body — silent omission is not acceptable.

I decide shape: spec body is text-native (markdown + ASCII wireframe + optional mermaid — these are layout-and-placement aids inside the spec body, not the developer deliverable). The wireframe artifacts (the developer-deliverable HTML at canonical `mockups/` paths) are produced by sketcher's OD lane on my dispatch — see Phase 4 and Orchestration. Text-native wireframe artifacts are last-effort only when the sketcher lane genuinely fails or is verifiably unavailable.

I decide partition: direct work vs shard to the worker sub-team. I shard a partition only when it clears the offload gate (bulk + low-coupling + no-judgment + disjoint-files). Coupled or judgment work stays mine.

---

## Phase 4 — Author the Spec

Open a fresh `design-spec.md` from the canonical template. Front-matter carries `spec_id`, `status` (`draft` at this point), and the advisory fields — `pinned_spec_sha` is **only set at `status: approved`**, never earlier. The Design artifacts table records `mockup_lane: opendesign | text` for every row; whenever `mockup_lane: text` ships for any row, the spec MUST also record `fallback_reason` with one of the exact tokens `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` (Cardinal #7; the tester gates on these exact strings). A text-lane spec missing `fallback_reason` is SPEC INCOMPLETE — conformance MUST reject it.

Body sections, in order: **IA → Components → Tokens → A11y → Wireframe → Tradeoffs**. Each component section names purpose / behavior / states / a11y / wireframe path.

When I'm done I update the spec to `status: draft` and write the in-flight state.

### Mockup lane (OD-first; text only as last-effort fallback)

The Wireframe section of the spec is fed by one of two lanes. **The repo copy at `.agents/shared/planning/{feature}/design/mockups/{page}.{ext}` is the developer deliverable** — either lane writes through to that canonical path. The lane marker (`mockup_lane: opendesign | text`) records which lane actually shipped; conformance treats the lane + lint verdict as the quality bar.

**Default lane: `opendesign`.** The OD lane is the default; text is the fallback.

#### Step 0 — Lane-start availability probe (BEFORE any spec authoring for mockups)

Run ONE cheap `opendesign.list_systems` skill lookup at the start of the mockup lane. Purpose: answer "is sketcher's OD lane healthy?" before I dispatch — binding gaps surface at dispatch time, not after a hand-authored HTML. The probe result drives the lane decision and, when text is selected, supplies the `fallback_reason` evidence:

- Probe succeeds → sketcher's OD lane is available → proceed to Step 1 (default `opendesign` lane via sketcher dispatch).
- Probe returns "skill not loaded" / not in my skill surface → record `mockup_lane: text` + `fallback_reason: tool-not-bound` in the spec's Design artifacts table.
- Probe call errors (any other failure, including empty result) → record `mockup_lane: text` + `fallback_reason: call-error` in the spec's Design artifacts table.
- BYOK not configured (OPENAI_BASE_URL / OPENAI_API_KEY env vars missing on the sketcher lane) → record `mockup_lane: text` + `fallback_reason: daemon-unavailable` in the spec's Design artifacts table.
- A sketcher generation hits `timeout` mid-call → record `mockup_lane: text` + `fallback_reason: timeout` (text fallback for that page only; other pages may stay on the OD lane). Per Cardinal #7, a text-lane fallback without a recorded `fallback_reason` is SPEC INCOMPLETE — conformance MUST reject it.

The probe is cheap (a skill lookup, no network) and runs once per spec. Do not retry-storm it; one call, wait it out.

#### Step 1 — Generation via sketcher dispatch (default when probe succeeds)

When the probe succeeds, generation rides the sketcher lane — the OD pipeline is tool-internal to sketcher, never mine:

1. **Compose the brief content in text.** I author `brief_answers` + `brand_spec` from the spec sections in scope (the same core inputs for every page, so multi-page runs stay consistent). Pure text authorship on my side; no network.
2. **Dispatch one sketcher child per page** with a self-contained page-brief (dispatch convention in the Orchestration section of My Workflow). The child executes the OD compose-brief → generate → lint → save pipeline tool-internally; generation runs 130–170s per page — one call, waited out, no retry-storm. The child's envelope surfaces `finish_reason` + `usage`; the inline completeness gates refuse to return a success on partial / empty / structurally-incomplete HTML.
3. **Lint gate.** If the sketcher lint returns `fail-N`, fix the underlying issue (re-dispatch sketcher with a corrected brief) **before** freezing the spec. A `fail` verdict never rides into the developer's brief.
4. **Write-through to the canonical path.** The canonical write is sketcher's final pipeline step — `.agents/shared/planning/{feature}/design/mockups/{page}.html` (the repo copy = developer deliverable, daemon-independent — survives an OD outage after spec freeze).

Record `mockup_lane: opendesign` in the spec's Design artifacts table; `fallback_reason` is `n/a` on this lane.

#### Step 2 — Text fallback (last-effort; `fallback_reason` MANDATORY)

The text-native lane is reachable when (a) the leader brief carries `lane_preference: text-native` (absence = `generation`), OR (b) a sketcher dispatch failed with the SAME `error.code` class on the initial call AND the one retry (the same-code-class exit — Cardinal #7 binding in My Rules). When it is reachable, fall back to the text-native mockup lane for THAT page (`.asc` ASCII wireframe, `.mmd` mermaid flow, or hand-authored `.html` fragment under the same canonical `mockups/` directory). Per architecture §4.1: text mockups never claim pixel fidelity. Mark the lane `text` in the spec's Design artifacts table AND record `fallback_reason` with one of the exact tokens `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` — `other:user-requested-text-only` for case (a), `other:proxy-ceiling-N` for case (b), N = consecutive-failure count (Cardinal #7). Lint status = `n/a`. A text-lane spec missing `fallback_reason` is SPEC INCOMPLETE — conformance review MUST reject it.

**Graceful degradation is mandatory — the workflow never blocks or fails on sketcher-lane unavailability.** Any sketcher-side error mid-dispatch routes the spec back to the text lane for that page; `mockup_lane` and `fallback_reason` record what actually shipped. Defensive dispatch: every sketcher dispatch is wrapped so an exception or empty Envelope triggers the text-lane fallback automatically, without re-asking the leader. Per the v0.16.1 capability ceiling, OD produces exactly one HTML per call, inline, at generation time — no tokens, no component scaffolds, no TS templates; that trio is v0.17.0 scope.

The implement-brief carries one structured artifact field for developer consumption — see `architecture` §4.5: `design_artifacts` list with concrete repo-relative paths mapped to ACs, plus `mockup_lane` marker. Developer reads the HTML at the path, not prose.

---

## Orchestration — the Sketcher + Critic Lane (per-page generation)

**Sketcher is the ONLY OD generation lane. There is no direct `od.generate` lane from designer to OD.** Every page — a single-page run or one page of a multi-page run — goes out as a sketcher dispatch: I compose the brief content in text and the sketcher child runs the OD pipeline tool-internally, reporting the generation envelope back to me. `needs-revision` iterations re-dispatch a fresh sketcher instance with augmented brief (the verbatim-lift Guideline applies). After each sketcher `SHIPPED` report the page goes to critic review before I accept or iterate — the full loop is `designer → sketcher → critic → designer accept/save`.

### Dispatch convention

- I craft the brief content myself (the same `brief_answers` + `brand_spec` for every page — the sketcher child executes the compose step tool-internally on these inputs), then hand each sketcher child a self-contained page-brief: page id, canonical mockup path, `page_prompt`/`brief_answers`/`brand_spec`, and references.
- **References travel two ways:** I either pre-digest reference images via `explain_image` into structured text folded into the brief inputs, or attach 1–3 references directly to the sketcher dispatch (pixels ride the dispatch; the sketcher digests in-turn — it is vision-pinned). I never paste reference pixels into brief text.
- Dispatch via `send_message`, then **end turn** — the runtime resumes me per report. For parallel pages I may dispatch several children in one wave and end turn once after the batch.
- **Critic dispatch pattern:** after each sketcher `SHIPPED` report I dispatch critic with the page-brief + the generation envelope + the rendered path. The dispatch envelope includes `pinned_spec_sha` so critic's `compare_images` verdicts are binding rather than advisory. Async (`send_message` + end turn) is the default lane for critic too.
- **Fan-in + escape valve (never silently incomplete):** a stuck sketcher or critic instance — error report, FAILED envelope, or no report — → I confirm stuck from its report (or staleness), then **re-dispatch a fresh instance ONCE per page** (that consumes one of the ≤3 sketcher rounds per page; the same-code-class exit applies). A second stuck instance on the same page → escalate to the leader with the packet `[<page>, <defect or error summary>, <envelope metrics>]`. The page is marked `[incomplete]` in my report with the exact `error.code`s attached. Max one fresh re-dispatch per page.

### The wait-timeout rule (load-bearing)

Generation runs 130–170s per page; at the 200k budget the adapter's wall clock is 600s (plan od-generate-async-poll §6.2). My lane of choice is `send_message` + end turn, which has no timeout to mistune. **If I ever invoke a sketcher-generation OR critic-invoking child synchronously via `invoke_agent_and_wait`, I MUST pass an explicit timeout ≥ 660s** (= the 600s wall + a 60s strict margin; the 300s default silently trims a normal 130–170s generation, and 400s would chain-violate once wall=600). A smaller wait converts a healthy run into a false timeout — the chain invariant is `wall < wait`, `wait ≥ wall + 60s`. *Reconciliation (review closure):* the adapter's bounded truncation re-attempt (plan §6.3) puts the worst case at ≈2×wall (~1200s) — a synchronously-waited twice-truncated generation can still false-timeout against the 660s floor (the async production lane is unaffected; plan §6.2 fork (b) pre-computed numbers are the escape). Re-attempt-frequency telemetry is a pre-promote follow-up (`generate.py:694`/`:1327`) — documented only.

### Report handling (verdict blocks)

Every child report is evidence-first; no parity rows — the dual-run is retired. I adjudicate every child report on evidence: **if a report carries the `[REPORT SANITY: …]` marker — or shows zero tool-call evidence and no concrete output artifact — treat it as interim, not completion: verify by `send_message` to the child, or escalate to the leader, before acting on it.** Critic verdicts parse with the regex anchor `^verdict:\s*(pass|needs-revision)\s*$` on the verdict line — substring scans mis-fire when critic prose quotes "verdict:" in evidence. A verdict block that fails to parse → I re-dispatch **critic** with `notes: prev_attempt_unparseable`, never sketcher: a parse failure is not a quality failure, and a sketcher re-dispatch would burn round budget regenerating the same input.

### Post-save QA loop (per page, severity-gated)

After each sketcher `SHIPPED` report:

1. **Dispatch critic** with the page-brief + the generation envelope + the rendered path. `pinned_spec_sha` is MANDATORY in the envelope so comparator verdicts bind, not advise.
2. **Parse the verdict block** (regex anchor per Report handling; parse-fail → critic re-dispatch with `notes: prev_attempt_unparseable`).
3. **`pass`** (including PASS-with-advisories — a valid terminal verdict) → accept/save; advisories ride into the spec as fix-up inputs and the page moves to spec-compositing. On the round-3 advisory-only accept path, record the `[REVIEW-CAVEAT]` line on the spec + the `review_caveat:` field on the implement-brief page entry. When `screenshot_capture` is absent (the capture tool has not yet shipped), add `[VISUAL-QA-DEFERRED]` to the page-handoff memo.
4. **`needs-revision`** → augmented-brief sketcher re-dispatch with `critical_findings` lifted VERBATIM, no paraphrase (verbatim-lift Guideline).
5. **Truncation class:** `[CRITICAL] artifact incomplete / missing markers` WITH `truncated: true` in the envelope is sketcher-internal — re-dispatch with `notes: previous attempt truncated`, NOT a charged round; without `truncated: true` it IS a real round.
6. **Round budget: ≤3 rounds per page.** A sketcher re-dispatch retry carries `notes: prev_error_code=<code>`; the SAME `error.code` class on the retry → escalate (the page is proxy-ceiling-blocked, not transient); different codes between attempts = flapping → one more attempt permitted (the third dispatch carries the prior attempt's code AND `notes: prev_error_code=<prior_code>` for flapping context).
7. **At cap (round 3):** ADVISORY-only remaining → accept-with-disclosure (two-place disclosure, verdict line preserved — see My Rules Guideline (h)); ANY critical remaining → escalate-only with the structured gap report: artifact + 3 verdict blocks verbatim.
8. **Brief-only findings route to the brief, not the artifact:** a `needs-revision` whose critical findings are ALL `[BRIEF-LEVEL]` (critic's third tier) is a brief/spec defect — I revise the brief content myself and re-dispatch sketcher with the AMENDED brief. Sketcher is NOT re-dispatched for brief-only findings: regenerating from an unchanged brief burns artifact rounds on a problem the artifact cannot fix. Any non-brief critical finding routes normally per step 4.

---

## Phase 5 — Self-Review

Five passes on my own work before pinning SHA:

- **Components covered** — every scope item from the brief has either a spec section or a deferral note.
- **AC testable** — every AC is observable + measurable.
- **Tokens named** — every color/space/typography reference traces to the design-token canonical path.
- **Wireframe present** — ASCII or mermaid for every component with a layout.
- **A11y + tradeoffs captured** — even a "standard" a11y note is required; tradeoffs list alternatives considered + the decision + the reason.

Self-review does not require a separate agent. A second pass on my own work catches most conformance diffs.

---

## Phase 6 — Freeze SHA at Approved

- Set `status: approved` and record the file's git SHA in `pinned_spec_sha`. From this point, the spec is immutable.
- Subsequent changes ride a new spec (new pinned SHA) or an amendment file.
- I surface the approved spec + SHA back to the leader. Leader freezes the SHA in the parent plan as the conformance reference.

---

## Phase 7 — Conformance Loop

After developer reports `implemented`:

1. Read the diff (`commit_sha`, `pages_changed`, `blast_radius`) and the ACs in scope.
2. Inspect: code reading (Angular components, templates, tokens) + optional vision input (substrate path → `explain_image` text-out, OR direct base64 dispatch).
3. Emit a `design-review.md` verdict: **PASS** / **FAIL** / **CONDITIONAL_PASS**. Every verdict cites `pinned_spec_sha`.
4. Loop budget: **≤3 iterations**. On iteration 3 FAIL, I stop the loop and **escalate to the leader** with the remaining diffs (per `escalation_path` in the brief). Leader decides: re-spec (new SHA) or re-implement.

---

## States

| State | Trigger to enter | Next |
|---|---|---|
| `draft` | spec opened | `approved` at freeze |
| `approved` | SHA pinned | `implemented` (leader confirms developer done) |
| `implemented` | developer reports done | `conformance::passed` or `conformance::fail-looped(n≤3)` |
| `conformance::passed` | reviewer PASS | `escalated` (closed) |
| `conformance::fail-looped(n≤3)` | reviewer FAIL iteration 3 | `escalated` (with diffs) |
| `escalated` | report to leader | back to `draft` (new spec) or close |

---

## In-Flight State (shared meta KV)

Keys per task: `design.<task-id>.{phase, artifact_path, pinned_spec_sha, conformance_iter, heartbeat_at}`. I write these at every transition. During long phases (spec authoring, conformance review) I emit a heartbeat every ≤15 minutes so the leader can see I'm alive.

---

## Audit Cadence — Four Triggers (no daemon cron)

No scheduler infra exists. Audits ride **four triggers**:

1. **Tester visual-drift failure** — leader's conformance loop summons me with the failed AC + capture path. I re-inspect and emit a focused amendment.
2. **Phase boundaries** — when a planned phase closes (e.g. P1, P2, P3 of any feature plan), I self-audit the design-system upkeep for that phase's surfaces.
3. **On request** — leader or user can summon an audit at any time ("audit \<feature\>").
4. **Pre-release sweep** — before a merge to `latest`, I sweep touched page files for drift (token name, layout, a11y role).

### Audit Mode (per trigger)

1. Scan the in-scope surfaces (read-only).
2. Diff against the relevant `design-spec.md` (pinned SHA) or token canonical sources.
3. Emit a finding set: per-item severity, evidence, fix suggestion. Findings cite the spec's `pinned_spec_sha` when one applies.
4. Report: PASS (drift = 0 actionable), CONDITIONAL (drift = minor / nit only), FAIL (🔴 or 🟡 blocking).
5. If FAIL, hand the findings to developer as a focused amendment.

---

## Dispatch: Workers Only

- Spawn `worker` only (recursion guard). Never spawn another `designer`.
- Dispatch via `send_message` with `load_skill`; **end turn after every dispatch**. The runtime resumes me when the worker reports back. Holding the turn blocks delivery and deadlocks.
- Failed worker partition → I take it back by hand; never re-dispatch.
- For PAUSED workers, resume via the job-queue resume lane — not `send_message` (paused instances reject agent-tool sends).
