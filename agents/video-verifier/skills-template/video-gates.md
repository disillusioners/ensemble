---
version: 1.0.0
category: verification
auto_load: true
---

# Video Gates

Gates that earned their keep in v1-v5, machine-checked in seconds, plus the eyes the
team was missing. The gate sets and profiles live in the team data directory
(`.agents/shared/planning/video-team/data/gate-sets.json` + `lane-profiles.json`);
this skill is how I run them and how I look.

## Cheap Set (fast-demo, every clip)

P1 duration cap · P2 clip pinning · P3 engines named · P4 viewport floor · P5
fixture-only tenants · P6 localhost-only DB · P7 sampled blank/luma scan · P8 deliverable size cap — then
G-PLAN (plan == performed), G-BLANK (full luma scan), G-FASTSTART (moov before mdat),
G-SPEC (codec/pixfmt/fps/duration), and eyes on the contact sheet.

## Strict Additions (film+goldens)

G-DETERM (full-frame SHA-256 across two renders — the engine's own sampler is a smoke
check, never proof) · G-3LAUNCH (three capture launches agree) · G-GEO (bbox vs plan,
≤2 px) · G-POINTER (pointer vs real input coords, ≤0.005 px, zoom windows included) ·
G-GOLDEN (frame-hash compare vs committed goldens; first divergent frame is the
evidence).

## The Gates With v1-v5 Scars

- The duration cap exists because an unpinned clip once rendered 60,300 frames
  (KICKOFF-BRIEF-SOURCED, UNVERIFIED against HANDOFF — the in-HANDOFF
  corroboration is the 83 s over-target v2 cut, HANDOFF §3).
- The blank/luma scan exists because a camera clamp once shipped dead frames.
- Stream-copy-only edits exist because range render shipped 1245/1650 wrong frames.
- The pointer gate exists because a decorative overlay taught nothing.

## Contact-Sheet Protocol (eyes are mandatory)

1. Build the contact sheet + keyframe stills into the review surface.
2. READ them (image category) — every clip, not a sample.
3. One honest sentence per clip naming what I saw; the verdict table carries the
   machine evidence, the eyes notes carry the judgment.

## Verdict Shape (gate lane)

Table: gate · clip · pass/fail · evidence pointer · tolerance vs measured. Machine
evidence first, eyes second, no prose padding. A red goes back with evidence; retry-
until-green is falsification.

## Golden Regression (film profile)

Compare at frame-hash level against `goldens/<beat-id>/` inside the project tree;
drift is a loud
regression with the first divergent frame named. Storage rides OQ3's default: goldens
in the lab, sha256 manifests in-repo.
