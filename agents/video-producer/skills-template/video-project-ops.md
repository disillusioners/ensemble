---
version: 1.0.0
category: workflow
auto_load: false
---

# Video Project Ops

A video is an openable project. I own its lifecycle end to end: tree layout, lane
registry, action-log governance, veto flow, and the delivery shape.

## Project Tree (canonical layout)

```
<subject-repo>/.agents/shared/planning/video/<project-id>/
├── brief.md                  # requirements + locked decisions
├── plan/                     # the shot plan (schema'd JSON)
├── edit/action-log.jsonl     # the undoable edit log (append-only)
├── build/                    # beats/, overlays/, captures/invalidation.json
├── out/<iter>/               # deliverable.mp4, preview.html, contact-sheet.png,
│                             #   verify-report.json, provenance.json
└── goldens/<beat-id>/        # film profile only
```

## Lane Registry

- Rows live in shared meta KV under `video.lanes.<project>.<role>`:
  `{instance_id, last_state, last_seen, lab_version}`.
- Mint each lane once; every later mission is a `send_message` revival of the SAME id.
- A lane resting state between missions is a clean terminal state — free revival.
  An ERROR-state lane consumes the one failure-revive; beyond that I mint a
  replacement and disclose.

## Action-Log Governance (the human veto)

1. Every editor mutation is one append-only record: seq, ts, actor, kind, params,
   inputs_sha, outputs_sha, reversible, veto (schema:
   `.agents/shared/planning/video-team/data/action-log.schema.json`).
2. Fast-lane edits land with `veto: pending` — deliver fast, disclose fully.
3. The preview page lists the iteration's actions with before/after stills.
4. On a veto: revert to the pinned prior state via stream copy (cheap because exact),
   re-run the cheap gates, re-mint the preview; the vetoed record stays in the log
   marked vetoed. History is never rewritten.

## Product-Change Flow

The user changed the product and wants the video re-captured against the new build:
route the capture lane at the invalidation set (spec:
`.agents/shared/planning/video-team/data/invalidation-spec.json`); re-judge the
plan's asserts; surface real deltas to the user — nothing else changes.

## Delivery Shape (what the user gets)

File paths + sizes + durations + ffprobe facts + the preview URL + provenance + the
veto window state. Honest artifacts included: the untouched-state stills, the
plan==performed evidence. Never a publish action — external use is the human's.

## Iteration Close

Record artifacts + gate verdicts + veto state in the project tree before ending the
turn, so any later revival inherits the truth.
