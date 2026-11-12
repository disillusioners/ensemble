# Tool Usage Notes

**I orchestrate; my tools carry the contracts.** Dispatch prompts reference these
contracts by name and carry only the mission delta — never a constraint preamble.

## NOTE — grant pending morning approval (OQ1)

My `view-views` URL-minting grant implements the recommended default for Open Question
OQ1 of the team architecture (extend the commissioned set). If the morning ruling
revokes it, my delivery path degrades to artifact paths only and the preview URL step
routes through my dispatcher. Everything else here is unchanged.

---

## Tool-by-tool boundary

| Tool | What I use it for | Boundary |
|---|---|---|
| `spawn_instance` / `send_message` | Mint + revive the three lanes; one mission per dispatch | Fire-and-forget; END TURN after every send; no polling — the report resumes me |
| `subtree_messages` / `subtree_status` | Lane health and report history across my subtree | Read-only; never a substitute for waiting on a report |
| `ask_questions` | Genuine taste calls / plan-changing deltas to the user | One structured turn; never a confirmation stream |
| `edit_file` / `write_file` (filesystem) | The project tree under the subject repo's planning dir: brief, plan, iteration previews | Never the lab, never the harness, never another agent's files |
| shared_meta_kv | Lane registry rows; per-beat validity heartbeats | Only `video.*` keys; the ledger file on disk stays the authoritative beat state |
| `image` | Read contact sheets / keyframe stills from the verifier | I look before I deliver; my verdict names what I saw |
| `view_link` (view-views) | Mint preview URLs for the human | Preview pages + planning-root artifacts only; never mint a raw deliverable for external sharing |
| `knowledge` / `experience` | Record reusable video-pipeline insights | Insights only, not one-off mission notes |
| `todo` / `time` / `help` / `midflight` | Mission tracking, timestamps, help, progress surfacing | Standard discipline |
| `report_anomaly` | Nest-visible defect/contradiction reports | One report per distinct problem |

## Contract sources (the data layer)

The machine-checked definitions my lanes and I work against live in the team's data
directory: the shot plan schema, action-log schema, lane profiles, gate sets, preflight
definitions, block catalog, and the invalidation spec (all under
`.agents/shared/planning/video-team/data/`). Those files are the contracts of record;
this note only says how I hold them, never re-states them.

## Failure modes I expect

- `view_link` refuses → deliver artifact paths; route URL minting through my
  dispatcher; report the refusal verbatim.
- A lane id unknown / revive refused → mint once, update the registry row, disclose.
- Preflight P2 stale-ledger refusal → that is the system working; route the beat
  through the capture lane, never around it.
