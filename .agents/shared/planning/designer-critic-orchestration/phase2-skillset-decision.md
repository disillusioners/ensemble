# Phase 2 — skill-set.yaml decision

**Decision: NO.** `agents/critic/` ships without a `skill-set.yaml` manifest.

**Rationale.** The critic's read surface is straight off its tool grant — reads of the artifact/spec/brief paths, image substrate queries, and the design-category comparator. No new skill content is required to produce a verdict block; the review posture lives in the agent's own prompt files, and the auto-loaded dynamic-skill machinery (innate `dynamic-skill`) covers bank lookups if a review needs design-system depth. Authoring a manifest with no skill behind it would be an empty versioning contract.

**Re-evaluate when:** a future commission lands `design.capture_mockup` (spec pinned at `design-capture-mockup-spec.md` in this directory) — if the capture flow gains its own procedural skill, critic re-evaluates a manifest entry at that point.

Recorded: 2026-10-10, phase 2 T8 (designer-critic-orchestration).
