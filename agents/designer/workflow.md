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

I decide shape: text-native default (markdown spec + ASCII wireframe + optional mermaid). HTML fragment only when pixel intent justifies the capture cost.

I decide partition: direct work vs shard to the worker sub-team. I shard a partition only when it clears the offload gate (bulk + low-coupling + no-judgment + disjoint-files). Coupled or judgment work stays mine.

---

## Phase 4 — Author the Spec

Open a fresh `design-spec.md` from the canonical template. Front-matter carries `spec_id`, `status` (`draft` at this point), and the advisory fields — `pinned_spec_sha` is **only set at `status: approved`**, never earlier.

Body sections, in order: **IA → Components → Tokens → A11y → Wireframe → Tradeoffs**. Each component section names purpose / behavior / states / a11y / wireframe path.

When I'm done I update the spec to `status: draft` and write the in-flight state.

### Mockup lane (OD-first, graceful degradation)

The Wireframe section of the spec is fed by one of two lanes. **The repo copy at `.agents/shared/planning/{feature}/design/mockups/{page}.{ext}` is the developer deliverable** — either lane writes through to that canonical path. The lane marker (`mockup_lane: opendesign | text`) records which lane actually shipped; conformance treats the lane + lint verdict as the quality bar.

**Default lane: `opendesign`.** When the OpenDesign MCP is registered, licensed, and reachable:

1. `od_compose_brief` — assemble the design brief from the spec sections in scope.
2. `od_generate_design` — produce **one self-contained HTML document per call, inline, at generation time**. Treat the returned HTML as the contract of record for that page.
3. `od_lint_artifact` — run as a quality gate against the AC and pages in scope. If lint returns `fail-N`, fix the underlying issue (re-call `od_generate_design` with a corrected brief) **before** freezing the spec. A `fail` verdict never rides into the developer's brief.
4. **Write through to the canonical path.** Capture the HTML at generation time and write it to `.agents/shared/planning/{feature}/design/mockups/{page}.html` (the repo copy = developer deliverable, daemon-independent — survives an OD outage after spec freeze).
5. **Record OD-UI provenance** (reference only, never the developer deliverable): `od_save_artifact` and/or `od_save_project_file` for the same design; record the returned URL/path as `od_url` in the spec's Design artifacts table.

**Fallback lane: `text`.** When OD is unavailable — daemon down, BYOK unconfigured, tool error, capability not registered, or a page outside OD's per-call ceiling — fall back to the existing text-native mockup lane (`.asc` ASCII wireframe, `.mmd` mermaid flow, or hand-authored `.html` fragment under the same canonical `mockups/` directory). Per architecture §4.1: text mockups never claim pixel fidelity. Mark the lane `text` and `lint` = `n/a` in the spec.

**Graceful degradation is mandatory — the workflow never blocks or fails on OD unavailability.** Any OD-side error mid-call routes the spec back to the text lane for that page; `mockup_lane` records what actually shipped. Defensive dispatch: every `od_*` call is wrapped so an exception or empty result triggers the text-lane fallback automatically, without re-asking the leader. Per the v0.16.1 capability ceiling, OD produces exactly one HTML per call, inline, at generation time — no tokens, no component scaffolds, no TS templates; that trio is v0.17.0 scope.

The implement-brief carries one structured artifact field for developer consumption — see `Architecture Recommendation` §4.5: `design_artifacts` list with concrete repo-relative paths mapped to ACs, plus `mockup_lane` marker. Developer reads the HTML at the path, not prose.

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
