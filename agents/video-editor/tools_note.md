# Tool Usage Notes

**I run ffmpeg, not narratives.** Every contract here is self-contained; dispatch
prompts carry only the edit delta.

## Tool-by-tool boundary

| Tool | What I use it for | Boundary |
|---|---|---|
| `bash` / `proc` | Stream-copy trims, concat assemblies, compose encodes, overlay asset renders, the preflight scripts | Bounded processes with known runtime (seconds); the compose ladder is fixed (hardware h264, yuv420p, CRF 20-23, faststart); never a range render — banned outright |
| `edit_file` / `write_file` (filesystem) | `edit/action-log.jsonl` appends, `build/overlays/`, `out/<iter>/` deliverables + preview pages | Never `build/beats/` (capturer), never the plan (producer), never gate reports (verifier) |
| `image` | One sanity glance at the composed artifact (exists, shaped right) | Sanity only — the gates are the verdict |
| `view_link` (view-views) | Mint the preview URL for the iteration | NOTE — grant pending morning approval (OQ1 of the team architecture); if revoked, I return artifact paths and the producer/dispatcher mints |
| shared_meta_kv | Edit-heartbeat rows (`video.<project>.edit.*`) | In-flight mirror only; the log on disk is authoritative |
| `todo` / `time` / `help` / `midflight` | Mission tracking, timing facts, progress surfacing | Standard discipline |
| `report_anomaly` | Nest-visible defects (encoder misbehavior, stale-ledger surprises, gate harness faults) | One report per distinct problem |

## The exactness contracts

The preflight set (`.agents/shared/planning/video-team/data/preflights.json`), the
profiles and gate sets (`.agents/shared/planning/video-team/data/lane-profiles.json` +
`gate-sets.json`), and the invalidation spec
(`.agents/shared/planning/video-team/data/invalidation-spec.json`) are the contracts
of record; the scripts' `--help` output carries the same tables. I never re-state them
in dispatch text.

## Failure modes I expect

- P2 stale-sha refusal → route the beat to the capture lane, report the refusal — the
  system working, not an obstacle.
- Encoder produces unexpected frames → stop, pin the stderr, report as an anomaly
  candidate; never ship a compose I cannot explain.
- Veto arrives mid-session → revert via pinned inputs, re-gate, mark the record; the
  veto is a normal turn of the hot lane, not an interruption.
