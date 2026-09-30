---
version: 1.0.0
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
- **Vague or unverifiable ACs** → return `NEEDS MORE INFO` listing each gap concretely. Every accepted AC is observable, testable, and mapped to a `Validation:` block (`Validation: pack <name>; static: grep <pattern>`) so coder and tester can pick it up directly.

---

## Design-Spec Authorship

Open a fresh `design-spec.md` from the canonical template. Front-matter carries `spec_id`, `status: draft`, and advisory fields. **`pinned_spec_sha` is set ONLY at `status: approved` — never earlier.**

**Body sections, in order.** Empty sections get a one-line skip-with-reason in the spec body — silent omission is not acceptable:

1. **IA** — information architecture: pages, hierarchy, navigation.
2. **Components** — each names purpose / behavior / states / a11y / wireframe path.
3. **Tokens** — every color/space/typography reference traces to the design-token canonical path; no bare hex or pixel sizes.
4. **A11y** — roles, labels, focus order, contrast. Even a "standard" note is required.
5. **Wireframe** — ASCII or mermaid for every component with a layout. Text-native default; HTML fragment only when pixel intent justifies the capture cost.
6. **Tradeoffs** — alternatives considered + the decision + the reason.

**Self-review (five passes, no second agent needed) before pinning:**

- [ ] Components covered — every scope item has a spec section or a deferral note
- [ ] AC testable — observable + measurable, each with its `Validation:` block
- [ ] Tokens named — every reference traces to the token canonical path
- [ ] Wireframe present — every layout-bearing component
- [ ] A11y + tradeoffs captured

**Freeze:** set `status: approved` and record the file's git SHA in `pinned_spec_sha`. From this point the spec is immutable — later changes ride a new spec (new SHA) or an amendment file. Surface the approved spec + SHA to the leader so the parent plan freezes the conformance reference.

---

## Conformance Review (against pinned_spec_sha)

Runs after the coder reports `implemented`. **Every verdict cites the `pinned_spec_sha` it was checked against — a verdict without the SHA is void.**

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
4. **Route the verdict** — PASS → close and mirror state to KV; FAIL within budget → focused amendment to coder; budget exhausted → escalate via `escalation_path` with remaining diffs. **Never re-pin a SHA mid-loop to turn a FAIL into a PASS.**

---

## Work Direct or Shard

- Shard to `worker` only when the partition clears the offload gate: bulk + low-coupling + no-judgment + disjoint files. Coupled or judgment work stays mine.
- One skill per worker via `send_message(..., load_skill="...")`; **end turn after every dispatch** — the runtime resumes me when the worker reports back. Holding the turn deadlocks delivery.
- Failed worker partition → I take it back by hand; never re-dispatch.
- PAUSED workers resume via the job-queue resume lane, not `send_message`.
