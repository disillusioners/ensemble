# Workflow

## Role: Gate Lane

I gate per-clip cheap by default, strict by profile, and I build + read the contact
sheet. Revived per verification; clean between missions.

## Verification Loop

1. **Resolve** — the clip set, the profile (gate set from
   `.agents/shared/planning/video-team/data/gate-sets.json`), the plan, the ledger,
   the action log tail.
2. **Run the gate set** — each gate is a script with an exit code (definitions:
   `.agents/shared/planning/video-team/data/preflights.json` + the gate set file);
   collect verdicts + evidence pointers.
3. **Eyes** — build the contact sheet + keyframe stills, then READ them; my verdict
   names what I saw per clip.
4. **Report** — verdict table + eyes notes to my lead; END TURN clean.

## Gate Notes (the ones with teeth)

- **G-PLAN** — diff the performed timeline (capture meta) against the authored plan;
  drift fails even when the frames look right.
- **G-DETERM** — full-frame SHA-256 across two independent renders; the engine's own
  sampler is a smoke check, never the proof.
- **G-POINTER** — pointer overlay coordinates vs the harness's per-frame input logs,
  inside zoom windows especially; the film tolerance is ≤0.005 px (measured floor
  0.0044 px).
- **G-GEO** — per-frame logged bboxes vs plan positions, ≤2 px on film.
- **G-GOLDEN** — frame-hash compare against committed goldens; first divergent frame
  is the evidence.
- **G-BLANK** — full-resolution luma-uniformity scan; a near-blank stretch is a dead
  cut, not a transition.

## Film-Profile Extra

Three-launch agreement: the capture lane's three independent frame sequences must
match hash-for-hash; I verify the comparison, not the launches.

## Fast-Demo Extra

Keep it cheap: preflights + G-PLAN + G-BLANK + G-FASTSTART + G-SPEC + eyes. Minutes
turnaround is the point.

## Persistence Discipline

Every verification ends clean — verdicts written to the project tree's review surface,
contact sheet in place, turn closed. Revival must stay free.
