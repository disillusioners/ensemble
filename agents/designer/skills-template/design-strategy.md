---
version: 1.3.0
category: planning
auto_load: true
---

# Design Strategy

> **Canonical home.** This skill (auto-loaded at runtime) is the single source for the Intake Contract, the Spec Authorship checklist, the Conformance Review procedure, and the Re-Conformance flow. My soul, workflow, and tools files reference it rather than restating it — one edit, one propagation.

Convert a leader brief into a pinned design spec, then guard implementation against it. I work directly or shard to workers; judgment work never leaves my hands.

---

## Intake (Run First, Always)

Validate the brief BEFORE designing — a spec built on a soft brief fails at conformance, three loops later.

**Mandatory fields:**

| Field | Purpose |
|---|---|
| `task_id` | Handle for shared meta KV keys (`design.<task-id>.*`) |
| `phase` | `new` \| `amend` \| `re-conformance` |
| `files` | In-scope paths or page list |
| `notes` | Architectural context, prior decisions, ACs from the parent plan |
| `plan_ref` | Parent planning doc for the feature |
| `conventions` | Project conventions that apply |
| `escalation_path` | Where diffs go when the conformance budget exhausts |

- **Missing field on `new`** → ask before guessing. Never invent silent defaults.
- **`re-conformance`** adds `pinned_spec_sha`, sent verbatim from the parent's frozen reference — treat it as ground truth, never re-derive it. The brief is the spec + parent diff.
- **`amend`** → confirm what changed and whether it warrants a new spec SHA or an amendment file.
- **Vague or unverifiable ACs** → return `NEEDS MORE INFO` listing each gap concretely. Every accepted AC is observable, testable, and mapped to a `Validation:` block (`Validation: pack <name>; static: grep <pattern>`) so developer and tester can pick it up directly.

---

## Design-Spec Authorship

Open a fresh `design-spec.md` from the canonical template. Front-matter carries `spec_id`, `status: draft`, and advisory fields. **`pinned_spec_sha` is set ONLY at `status: approved` — never earlier.**

**Body sections, in order.** Empty sections get a one-line skip-with-reason in the spec body — silent omission is not acceptable:

1. **IA** — information architecture: pages, hierarchy, navigation.
2. **Components** — each names purpose / behavior / states / a11y / wireframe path.
3. **Tokens** — every color/space/typography reference traces to the design-token canonical path; no bare hex or pixel sizes.
4. **A11y** — roles, labels, focus order, contrast. Even a "standard" note is required.
5. **Wireframe** — wireframe artifacts come from the **mockup lane** (see Mockup Lane). OD is the default; text-native is the last-effort fallback only when OD genuinely fails or is verifiably unavailable (see Mockup Lane). The repo copy at `.agents/shared/planning/{feature}/design/mockups/{page}.{ext}` is the developer deliverable in either lane.
6. **Tradeoffs** — alternatives considered + the decision + the reason.
7. **Design artifacts** — table mapping each renderable artifact (page → canonical path → kind → AC refs → OD-UI URL → lint). Lane marker (`mockup_lane: opendesign | text`) declares which lane actually shipped. Whenever any row is `mockup_lane: text`, the spec MUST also record `fallback_reason` with one of the exact tokens `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` (Cardinal #7; tester gates on these exact strings). A text-lane spec missing `fallback_reason` is SPEC INCOMPLETE — conformance MUST reject it.

**Self-review (five passes, no second agent needed) before pinning:**

- [ ] Components covered — every scope item has a spec section or a deferral note
- [ ] AC testable — observable + measurable, each with its `Validation:` block
- [ ] Tokens named — every reference traces to the token canonical path
- [ ] Wireframe present — every layout-bearing component
- [ ] A11y + tradeoffs captured
- [ ] Design artifacts table filled in with concrete canonical paths under `mockups/`
- [ ] Lane marker (`mockup_lane: opendesign | text`) recorded
- [ ] If `mockup_lane: text` for any row: `fallback_reason` recorded with one of `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` (Cardinal #7)

---

## Mockup Lane (OD-first; text only as last-effort fallback)

The Wireframe section and the Design artifacts table are fed by one of two lanes. **The repo copy under `.agents/shared/planning/{feature}/design/mockups/` is the contract of record** — the developer reads from disk, not from prose or OD-UI. The lane marker records which lane actually shipped; conformance treats the lane + lint verdict as the quality bar.

**Concept.** OD-first: the OpenDesign lane is the default whenever the MCP is registered, licensed, and reachable. Text-native / hand-authored / self-do is a last-effort fallback only — permitted when OD has genuinely failed or is verifiably unavailable (probe not bound, call error, daemon unreachable, BYOK/timeout/lane-ceiling failure). The workflow never blocks on OD availability, but a text-lane fallback MUST record `fallback_reason` (see Cardinal #7) with one of the exact tokens `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` — a text-lane fallback without `fallback_reason` is SPEC INCOMPLETE and conformance MUST reject it. Per the v0.16.1 capability ceiling, OD produces exactly one HTML per call, inline, at generation time — tokens, component scaffolds, and TS templates remain v0.17.0 scope and are not promised.

**Design-artifacts table contract.** Each row in the spec's Design artifacts table carries the full edge shape developer consumes: `path` (the canonical repo-relative path under `mockups/`, daemon-independent), `kind` (`html-mockup` | `text-mockup` | `render`), `ac_refs` (the AC IDs that row serves), `od_url` (OD-UI provenance — reference only, present only on `mockup_lane: opendesign` rows; `render` rows carry an `od_url` and no mockup path), `lint` (`pass` | `fail-N` | `n/a`), `mockup_lane` (`opendesign` | `text`), and `fallback_reason` (one of `tool-not-bound | call-error | timeout | daemon-unavailable | other:<detail>` — REQUIRED when `mockup_lane: text`, must be `n/a` when `mockup_lane: opendesign`).

**Procedure (full step list).** The five-step procedure — Step 0 lane-start probe (`od_list_projects`) → Step 1 OD lane (`od_compose_brief` → `od_generate_design` → `od_lint_artifact` → write-through to canonical `mockups/` path → record OD-UI provenance) → Step 2 text fallback (with `fallback_reason`) — lives canonically in **Mockup lane** of the designer's workflow file. See Mockup lane for the executable steps.

**Implement-brief relay.** The developer's implement-brief carries the `design_artifacts` list (one row per artifact with concrete path, kind, AC refs, OD-UI URL, lint) plus the `mockup_lane` marker — see architecture §4.5 for the full edge field shape. v1 developer reads each path from disk and ports the DOM/structure/CSS intent into components; v2 developer relays the field verbatim to the executor.

**Freeze:** set `status: approved` and record the file's git SHA in `pinned_spec_sha`. From this point the spec is immutable — later changes ride a new spec (new SHA) or an amendment file. Surface the approved spec + SHA to the leader so the parent plan freezes the conformance reference.

---

## Conformance Review (against pinned_spec_sha)

Runs after the developer reports `implemented`. **Every verdict cites the `pinned_spec_sha` it was checked against — a verdict without the SHA is void.**

1. **Scope** — read the diff (`commit_sha`, `pages_changed`, `blast_radius`) and the in-scope ACs. Blast radius sets the review boundary.
2. **Inspect** — code reading (components, templates, tokens) + optional vision input (substrate path → `explain_image` text-out, or direct base64 dispatch).
3. **Emit `design-review.md` verdict:**

   | Verdict | When |
   |---|---|
   | PASS | zero actionable diffs vs the pinned spec |
   | CONDITIONAL_PASS | minor / nit diffs only, none block an AC |
   | FAIL | blocking diffs — list each with evidence + fix suggestion |

4. **Loop budget ≤3 iterations.** Iteration 3 FAIL → stop the loop and escalate to the leader with the remaining diffs via `escalation_path`. Leader decides: re-spec (new SHA) or re-implement.

State lives in shared meta KV: `design.<task-id>.{phase, artifact_path, pinned_spec_sha, conformance_iter, heartbeat_at}`. Update at every transition; heartbeat ≤15 min during long phases.

---

## Re-Conformance Flow

Triggered when a settled change must be re-checked against a previously pinned spec: tester visual-drift failure, phase-boundary audit, pre-release sweep, or leader request.

1. **Resolve the reference** — take `pinned_spec_sha` verbatim from the brief. Do NOT read working-tree spec state or recompute a SHA; the pinned SHA is the only ground truth.
2. **Re-derive the change set** — fresh diff since the last verdict, scoped to the drifted surfaces the trigger names.
3. **Re-run the conformance review** — same procedure, same verdict grammar, citing the SAME `pinned_spec_sha`; reset `conformance_iter` for the new cycle.
4. **Route the verdict** — PASS → close and mirror state to KV; FAIL within budget → focused amendment to developer; budget exhausted → escalate via `escalation_path` with remaining diffs. **Never re-pin a SHA mid-loop to turn a FAIL into a PASS.**

---

## Work Direct or Shard

- Shard to `worker` only when the partition clears the offload gate: bulk + low-coupling + no-judgment + disjoint files. Coupled or judgment work stays mine.
- One skill per worker via `send_message(..., load_skill="...")`; **end turn after every dispatch** — the runtime resumes me when the worker reports back. Holding the turn deadlocks delivery.
- Failed worker partition → I take it back by hand; never re-dispatch.
- PAUSED workers resume via the job-queue resume lane, not `send_message`.
