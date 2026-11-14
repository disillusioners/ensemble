---
version: 1.0.0
category: capture
auto_load: true
---

# Video Capture Harness

Deterministic capture is a per-frame program, not a recording. One deliberate step per
captured frame; the frames are the footage the whole team builds on.

## Determinism Rules (every capture, no exceptions)

- Fresh browser context per beat; fixture re-seeded through the REAL product route
  before anything else.
- Fixed viewport + DPR (per the plan's aspect entry; width floor 1024 — the plan
  schema rejects anything lower).
- Animations disabled, caret hidden, reduced-motion honored, fonts awaited, network
  settled, fixed clock/seed; no wall-clock in the app under test.
- Decompose clicks into press/move/release when the click itself must be visible
  across frames — a collapsed click teaches nothing.

## Per-Frame Records (the editor depends on these)

- Frame PNGs (1920x1080) named by frame index.
- Per-frame input-coordinate log: every pointer position, press, release, drag waypoint.
- Per-frame element geometry: bounding boxes of the elements the plan's blocks and
  gates reason about.
- The teaching pointer is driven by the REAL input coordinates (measured max deviation
  0.0044 px); it is capture-time, never a post overlay.

## The Ledger (invalidation)

Every beat capture writes its five pins — shot sha, fixture sha, fe build sha,
harness sha, lab stack sha — to `build/captures/invalidation.json` per the spec at
`.agents/shared/planning/video-team/data/invalidation-spec.json`. A beat whose pins
all match is NEVER re-captured; a beat with any changed pin is re-captured before
anything downstream consumes it.

## Parallel Shots

Parallel captures are parallel OS processes I supervise — bounded, one capture
instance at a time on this 16 GB machine. Agents are not my parallelism unit; I am a
leaf.

## Three-Launch Protocol (film profile)

Three independent launches of the same plan; frame sequences must match hash-for-hash.
Report mismatches as-is — a mismatch means the warm stack is lying, and that fact
belongs to my lead, not averaged away.

## The Engine

The composition engine is driven headless (`.film/opened` marker + no-browser flag so
no Studio daemon starts); its determinism check samples only a few frames, so my
contribution to the real proof is the full-frame hashes I write into the MEASURED
manifest — the verifier's determinism gate consumes those, and the 12-frame sample is
never trusted alone.
