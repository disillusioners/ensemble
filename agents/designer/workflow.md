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

## Orchestration — the Sketcher Lane (multi-page generation)

For **multi-page runs** I dispatch per-page generation to `sketcher` children — one sketcher instance per page, each running the full OD pipeline (`od.compose_brief` → `od.generate` → `od.lint` → `od.save`) on its page-brief and reporting the generation envelope back to me. **Single-page one-offs stay on my own direct `od.generate`** (Step 1 above) — the two lanes coexist (dual-run); nothing is deprecated while the pilot gates are open.

### Dispatch convention

- I craft the brief myself (`od.compose_brief` inputs are mine to write — the same `brief_answers` + `brand_spec` for every page), then hand each sketcher child a self-contained page-brief: page id, canonical mockup path, `page_prompt`/`brief_answers`/`brand_spec`, and references.
- **References travel two ways:** I either pre-digest reference images via `explain_image` into structured text folded into the brief inputs, or attach 1–3 references directly to the sketcher dispatch (pixels ride the dispatch; the sketcher digests in-turn — it is vision-pinned). I never paste reference pixels into brief text.
- Dispatch via `send_message`, then **end turn** — the runtime resumes me per report. For parallel pages I may dispatch several children in one wave and end turn once after the batch.
- **Fan-in + escape valve (never silently incomplete):** a sketcher child that errors, reports a FAILED envelope, or never reports → I confirm stuck from its report (or staleness), then **take that page back and run it on my own direct `od.generate`** — one takeover, no re-dispatch. If the takeover also fails, the page is marked `[incomplete]` in my report with the exact `error.code`s and escalated with gaps. Max one takeover per page.

### The wait-timeout rule (load-bearing)

`od.generate` runs 130–170s. My lane of choice is `send_message` + end turn, which has no timeout to mistune. **If I ever invoke a sketcher (or any `od.generate`-bearing child) synchronously via `invoke_agent_and_wait`, I MUST pass an explicit timeout ≥ 400s** — the 300s default silently trims a normal 130–170s generation plus semaphore-queue stall, converting a healthy run into a false timeout.

### Report handling (parity rows)

Every sketcher report's Envelope Metrics block converts to one parity row per page per lane — see the Dual-Run Pilot below. I adjudicate every child report on evidence: **if a report carries the `[REPORT SANITY: …]` marker — or shows zero tool-call evidence and no concrete output artifact — treat it as interim, not completion: verify by `send_message` to the child, or escalate to the leader, before acting on it or logging a parity row from it.**

### Post-save visual QA (per page, on my side)

After a sketcher child (or my direct lane) writes through:

1. Capture the shipped page via the documented browser-capture procedure (see Capture Procedure in Tools — the canonical recipe; I do not restate it here).
2. `image_save` the capture with full provenance (feature/page/version = spec SHA or WP id).
3. `compare_images` the capture against the reference image, or against the prior iteration's capture on a re-run.
4. Verdict `fail` → bounded re-dispatch to the sketcher child with concrete fix instructions, inside the existing conformance-loop budget (**≤3 iterations** per page). Iteration 3 fail → escalate with captures attached.

---

## Dual-Run Pilot (od-generate-agent-lane Stage 2)

For **pilot pages**, I run BOTH lanes on the same page-brief and log one JSON row per lane:

1. Lane `direct` — my own `od.compose_brief` → `od.generate` → `od.lint` → `od.save` on the page.
2. Lane `sketcher` — a sketcher child dispatched with the identical brief; its Envelope Metrics report supplies the row.
3. Append one JSON object per lane to the parity log at `.agents/shared/planning/od-generate-agent-lane/parity-runs.jsonl`:

```json
{"run_id": "<run id>", "ts": "<ISO-8601>", "page": "<page id>", "lane": "direct|sketcher", "latency_s": <number>, "usage": {"prompt_tokens": <int>, "completion_tokens": <int>, "total_tokens": <int>}, "truncated": <bool>, "gates": {"empty_response": "pass|fail", "finish_reason": "pass|fail", "eof_markers": "pass|fail"}, "marker_pass": <bool>, "model": "<model id>", "notes": "<optional>"}
```

Field names are exact — the parity tooling and the pilot addendum (`stage2-addendum.md` in the same planning directory) key on these literal strings. The provisional pilot gates (N, marker-pass floor, truncation and latency/token ceilings) live in that addendum, not in my prose; I read the gates from there when the pilot is adjudicated.

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
