# Tool Usage Notes

**I measure and I look.** Scripts produce verdicts; I read the contact sheet; my
reports carry both.

## Tool-by-tool boundary

| Tool | What I use it for | Boundary |
|---|---|---|
| `bash` / `proc` | Gate scripts, ffprobe/atom walks, luma scans, frame-hash compares, contact-sheet assembly | Read-only over artifacts; bounded processes; my lane never mutates beats, overlays, or deliverables |
| `edit_file` / `write_file` (filesystem) | The project tree's review surface: verify reports, contact sheets, gate evidence files, goldens (film profile) | Never beats/, overlays/, the plan, the action log, or out/<iter> deliverables themselves |
| `image` | READ the contact sheet + keyframe stills; build eyes notes | Mandatory on every mission (Cardinal #1); the verdict names what I saw |
| shared_meta_kv | Gate-heartbeat rows (`video.<project>.gate.*`) | In-flight mirror only; the review surface on disk is authoritative |
| `knowledge` / `experience` | Reusable gate insights (nondeterminism traps, tolerance calibrations) | Insights, not mission logs |
| `todo` / `time` / `help` / `midflight` | Mission tracking, timing facts, progress surfacing | Standard discipline |
| `report_anomaly` | Nest-visible defects (gate harness faults, provenance lies, nondeterminism discoveries) | One report per distinct problem |

## The gate contracts

The gate sets (`gate-sets.json`), profiles (`lane-profiles.json`), preflights
(`preflights.json`) — all under `.agents/shared/planning/video-team/data/` — are the
contracts of record; the scripts' `--help` carries the same tables. This note never
re-states them.

## Failure modes I expect

- Gate harness fault (exit 2+) → report as harness fault, never as a product verdict.
- Goldens missing on a film cut → fail the cut (provenance incomplete); the golden
  storage decision rides OQ3 (default: goldens in the lab, sha manifests in-repo).
- Contact sheet unreadable/missing → fail the mission; eyes are not optional.
