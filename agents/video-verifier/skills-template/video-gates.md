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
G-GOLDEN (three-tier dispatch per the beat's `comparison` field — TIER E exact /
TIER P perceptual / TIER R representative, with per-beat thresholds at mint time).

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

## Golden Discipline (film profile — three-tier, per the user ruling)

G-GOLDEN is ONE gate id; its dispatch is per-beat via the plan's `comparison` field:

- **TIER E (exact)** — deterministic regions: on-screen text, indicator pills,
  overlay chrome, UI we control. Masked-region per-frame hash, **zero tolerance**.
  Whole-frame allowed when a frame is fully deterministic. Keeps the blank-frame +
  range-render-nondeterminism catches.
- **TIER P (perceptual)** — generated/AI footage + legitimate render variance.
  SSIM mean >= X **AND/OR** pHash/dHash Hamming <= N on representative frames.
  Thresholds are **CONFIGURABLE per beat, stored in the beat's golden provenance AT
  MINT TIME** (lane defaults: ssim_min 0.97, dhash_hamming_max 6 on 64-bit). Never
  global magic numbers; never tuned per-run to make a specific cut pass.
- **TIER R (representative)** — adaptive sampling: dense around scene boundaries
  (frame-diff/SSIM-drop) + forced sample points at action windows (pointer travel /
  press / drag / drop / popup open / row edits, with pad_frames) + uniform baseline
  between them; reduced long edge <= 480 px. Default minimum coverage; E/P layered
  where warranted.

A perceptual FAIL reports the **MEASURED score vs the threshold** plus the **first
representative frame that flagged** — never a bare boolean.

**Re-mint flow (missing or stale golden = BLOCKED-WITH-A-PATH, never a permanent
blocker):** the verifier PROPOSES a re-mint with a reason class
(`lab_binary_lost` | `approved_plan_delta` | `threshold_change`), the human APPROVES,
and the re-mint records generation N+1 + the approval reference in the beat's
provenance (fields: generation, reason_class, reason, approved_by, approval_ref, at).
A threshold change or re-mint is APPROVAL-WORTHY — recorded with reason + approver,
never silent. Retry-until-green is FORBIDDEN (falsification).

## Amendment 2 — five implementation requirements

**1. TIER R adaptive sampling (replaces uniform every-N as the default):** dense around
scene boundaries (frame-diff / SSIM-drop detection) + forced sample points at action
windows from the capture harness action log (pointer travel / press / drag / drop /
popup open / row edits, with pad_frames) + uniform baseline between them. Contract:
`comparison_thresholds.representative` gains `mode: "adaptive"`, `boundary_detector
{metric, threshold}`, `action_windows {source, types, pad_frames}`,
`baseline_interval_frames`.

**2. TIER S semantic spot-check:** perceptual != semantic (SSIM/pHash do not
understand content). Representative frames through the vision channel (image-reader /
explain_image) against the per-beat `semantic_checklist {must_include, must_exclude}`.
HONEST FRAMING: Tier S output is **EVIDENCE FOR HUMAN APPROVAL** — a written verdict
PER KEYFRAME recorded in the verify report — NOT an auto pass/fail threshold; but a
miss must be SURFACED LOUDLY, never buried.

**3. Calibration:** per-beat thresholds DERIVED from an APPROVED SAMPLE SET — min 5
samples; threshold = mean − k·σ, k = 3, recorded per beat; store sample ids +
distribution stats (mean/σ/min/max per metric) + resulting thresholds in golden
provenance. NO calibration data ⇒ STRICTER fail-closed defaults (ssim_min 0.995,
dhash_hamming_max 2) — never the looser lane defaults. Arbitrary global constants
disallowed. `approval_record.reason_class` gains `calibration_update`.

**4. Mask versioning + validation:** masks are versioned artifacts — provenance records
`{mask_id, sha256, regions, beat_id}`. Shot-plan per-beat gains `critical_regions`
(text/overlay/action areas — derived from action log + declared list),
`mask_area_budget_pct` (default 30), `masks: []`. MANDATORY assertions in the exact
tier (all three): (a) EVERY declared critical region is INSIDE the exact-compare area
— an exclusion = FAIL; (b) masked-out area <= budget; (c) a mask change is
APPROVAL-WORTHY (reason_class `mask_change`) with reason + approver recorded. This
closes the mask-away-the-hard-parts abuse path.

**5. Overhead measurement:** per-beat, per-tier wall times — Tier E hashing, Tier P
metric, Tier R sampling, Tier S vision-call, total verification — recorded in the
verify report vs that beat's render wall time; budget <= 15% of render time; on
exceedance report plainly + name the dominant cost. Reporting contract in
`data/gate-sets.json` `overhead` block.

## Golden Regression (film profile)

Three-tier dispatch per the beat's `comparison` field (TIER E exact / TIER P
perceptual / TIER R representative), with per-beat thresholds at mint time. Storage:
Option A APPROVED — lab binaries + in-repo manifests (`goldens/<beat-id>/` inside the
project tree, section 8.1 layout). Re-mint follows the approval-recorded flow above
(generation N+1 + approval reference).
