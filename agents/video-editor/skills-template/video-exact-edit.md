---
version: 1.0.0
category: editing
auto_load: true
---

# Video Exact Edit

Trims, text swaps, zoom changes — in seconds, with NO re-capture. Exactness is a
property of the pipeline, not of my care: all-intra intermediates make every frame a
seek point, and stream copy makes every segment cut byte-exact.

## The Edit Matrix

| Edit | Path | Encode? |
|---|---|---|
| Trim | accurate seek (`-ss`/`-to`) + `-c copy` over all-intra segments | none |
| Assembly | concat demuxer + `-c copy` | none |
| Text/overlay swap | swap browser-rendered asset (seconds) + one compose | one |
| Zoom/crop | recompute camera crop + one compose | one |
| Block param change | re-render block asset per catalog + one compose | one |

**Range render is BANNED for edits** — the engine's range render is nondeterministic
(proven: 1245 of 1650 frames wrong). Footage is never re-rendered through the engine;
the only encode on my lane is the final compose.

## Why All-Intra

Captured footage is encoded all-intra (every frame a keyframe), so any frame is a cut
point and any segment concatenates without re-encoding. Non-intra intermediates would
force re-encodes on every trim and destroy byte-exactness — the footage contract is
the capturer's to keep and mine to rely on.

## The Compose Ladder (one encode, only when pixels change)

- Hardware h264 (`h264_videotoolbox`), yuv420p, CRF 20-23 (pre-measured: a 30-60 s UI
  cut lands 1.5-3 MB), `+faststart` verified by atom walk.
- Text is overlay assets, never encoder drawing (this host's ffmpeg has no
  drawtext/subtitles/ass — and the overlay track is the right owner anyway).
- Transitions, when the plan calls for one: decoded-frame `xfade` — never browser
  alpha compositing.

## Every Action Is Loggable

Append one record per mutation to `edit/action-log.jsonl` (schema:
`.agents/shared/planning/video-team/data/action-log.schema.json`): kind, params,
inputs pinned by sha, outputs pinned by sha, reversible, `veto: pending`. Revert =
re-derive the prior segment set from the pinned inputs via stream copy; the revert is
a new record pointing at the action it reverts; history is append-only.

## Self-Preflight Then Handoff

P1-P8 (`.agents/shared/planning/video-team/data/preflights.json`) run in seconds
before the verifier handoff. P2 (clip pinning) is the one that saves the team: a stale
sha refuses the edit before any pixels move.

## Speed Budget

Trim-only: seconds, zero encode. Overlay/camera: seconds of asset render + one cheap
compose. If an edit is projected to take minutes, the plan is wrong — go back to the
delta and re-derive, never brute-force.
