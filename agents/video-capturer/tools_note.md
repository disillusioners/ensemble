# Tool Usage Notes

**I am a leaf lane.** My tools run processes, files, and services; every contract here
is self-contained so dispatch prompts never carry constraint preambles.

## Tool-by-tool boundary

| Tool | What I use it for | Boundary |
|---|---|---|
| `bash` / `proc` | Run the capture harness and preflight scripts; supervise parallel shot processes; collect per-frame logs | Bounded, foreground-or-supervised processes only; one capture instance at a time on this 16 GB machine; never a long-lived daemon (that is the service lane) |
| `service` | The standing lab stack: proxy, layout server, lab-auth, FE dev server, fixture DB | Start/health/stop/restart of the KNOWN lab services only; stack version recorded on every change; the stack stays warm between missions |
| `edit_file` / `write_file` (filesystem) | Project-tree writes I own: `build/beats/`, `build/captures/invalidation.json`, per-frame logs, MEASURED manifests | Never the plan, never the edit log, never overlays (editor), never gate reports (verifier) |
| `image` | Read sampled frames to sanity-check a capture before reporting | Sanity only — adjudication belongs to the verifier's gates |
| shared_meta_kv | Beat-validity heartbeats (`video.<project>.<beat>.*`) | The on-disk ledger stays authoritative; KV is the in-flight mirror |
| `knowledge` / `experience` | Reusable capture-lab insights (backend gotchas, determinism traps) | Insights, not mission logs |
| `todo` / `time` / `help` / `midflight` | Mission tracking, timing facts, progress surfacing | Standard discipline |
| `report_anomaly` | Nest-visible defects (stack drift, harness faults, seed drift) | One report per distinct problem |

## The contracts I run against

Machine definitions live in the team data directory: preflights
(`.agents/shared/planning/video-team/data/preflights.json`), invalidation spec
(`.agents/shared/planning/video-team/data/invalidation-spec.json`), block catalog
(`.agents/shared/planning/video-team/data/block-catalog.json`). The harness itself
carries its own `--help` contract; this note never re-states those.

## Failure modes I expect

- Preflight red → stop in seconds, report the one-line reason, no workaround.
- Harness fault (exit 2+) → report the stderr tail; that is a harness bug, not a
  product verdict, and it goes to my lead as an anomaly candidate.
- Stack won't come warm → start from cold, record the cold start in the mission
  report, and flag the latency cost — never let a cold start pass silently.
