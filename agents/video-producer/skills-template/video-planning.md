---
version: 1.0.0
category: planning
auto_load: true
---

# Video Planning

Decide WHAT the video says and encode it as data. The plan is schema'd JSON validated
against the published schema (`.agents/shared/planning/video-team/data/shot-plan.schema.json`);
review is a one-turn diff of that data. Markdown round-trips are banned.

## Template Start

1. Start from the project template set (beat shapes for: open-surface, feature-drag,
   zoom-click affordance, popup session, instant after-summary) — never a blank page.
2. Fill the locked frame first: customer-facing, silent, 1920x1080 16:9 only, no
   viewport below 1024, hard cap 1:30, fixture-only tenant. These are plan constants,
   not decisions.
3. Cast small: 4-5 surface elements, default colors at start so a color change pops,
   one continuous story with camera moves — not cuts.

## Delta Intake (planning skill)

1. Diff the request against the current plan beat by beat.
2. Present ONLY the delta: new/changed beats, block params, camera windows, captions,
   profile changes — each as a plan fragment, not prose.
3. Ask the user only for genuine taste calls or plan-changing deltas (one structured
   question turn).

## Beat Authoring Rules

- Every action gets an absolute `at_frame`; the compiler rejects overlaps and disorder.
- Every beat's `state.asserts` is its intent — the gate re-judges these after any
  product change, so write them as observable outcomes, not implementation details.
- Overlay text is terse and benefit-first ("Your dashboard. Your tabs." / "Drag to
  reorder."); the palette stays in the teal family.
- Blocks come from the catalog (`.agents/shared/planning/video-team/data/block-catalog.json`);
  a block that does not exist there is a catalog proposal, not an ad-hoc effect.

## The Plan Gate

No capture runs on an unapproved plan. I present the diff; approval is recorded in the
project brief; then, and only then, does the capture lane get a mission.

## Profile Selection

Default `fast-demo` (cheap gates, minutes turnaround). `film+goldens` only for
customer-facing cuts — stricter gates, determinism CI, 3-launch re-capture (see the
gate sets at `.agents/shared/planning/video-team/data/gate-sets.json`).
