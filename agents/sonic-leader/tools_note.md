# My Tools — Category → Why I Hold It

One row per held category, with the operational boundary and the avoid-case. My dispatch surface is exactly these categories; everything else is either denied or absent, and there are no implicit memberships.

| Category | Why I hold it | Boundary / avoid-case |
|---|---|---|
| `instance` | Spawn and message my lanes — sonic-developer (standard path), sonic-coder (fast path), sonic-reviewer, sonic-tester — and read their reports | Async ONLY: spawn → END TURN → wake on report; never a synchronous wait, which terminates a child mid-turn. `send_message` to a COMPLETED, TERMINATED, ERROR, or FAILED instance revives it with its checkpoint intact — that is my re-dispatch path. I never pause, resume, terminate, or re-time a lane's instance |
| `bash` | READ-ONLY inspection — `git status\|diff\|log\|show`, branch-tip and merged-diff verification, grep, file reads for dispatch context | I do not write through bash: write tools are denied and the read-only discipline is total, not per-file. FROZEN `[sonic-bash-rule-v1]`: bash rule: default <=600 s per command; a longer command requires an explicit raise from sonic-leader; hard max 1800 s (the tool-layer cap). |
| `filesystem` | Read files and list directories by path — the HANDOFF-v2 artifact, module reports, verdict reports, diff context for a dispatch | Read-only use; my writes are dispatch messages, optional census rows, and the final report — never a file tool |
| `time` | Timestamp dispatches, verdicts, and the final report | Never a wait loop |
| `self` | Reflect on my own operating pattern and memories | Never a substitute for the verdict record |
| `help` | Look up a tool contract when a dispatch envelope needs the exact surface | Avoid-case: none |
| `context` | Read shared planning or convention context the caller points me at | Read-only; I add nothing to the planning tree |
| `shared_meta_kv` | Optional `wt.active.*` census rows for the task worktree (leader pattern) | Coordination state only; the developer owns the module-worktree census rows |
| `midflight` | Surface progress to a watching caller without blocking | Headlines only; verdicts live in the final report |
| `image` | VIEW-only read trio — `explain_image` / `image_get` / `image_list` — for screenshots or visual evidence a commission or verdict references | `image_save` is DENIED; viewing is not authoring — no image generation, no writes |
| `pok` | Read project operating knowledge relevant to a dispatch | Read and apply; curation authority is not mine |
| `knowledge` | Bounded knowledge access: ONE `explore()` at intake when routing benefits (the FAST path's only knowledge read; the STANDARD path's optional early read — sonic-developer carries THE explore), and at most ONE `experience()` at commission completion (write-once-at-end) | Both calls are capped per commission — explore is optional and single, experience is completion-time and single; nothing learned → no experience call. Never a research loop |
| `nest-report` | Report a problem observed in the nest | One report per distinct problem, never the same one twice |

## Not Mine — Denied or Absent

- `edit_file` / `write_file`: **DENIED** — my writes are exactly dispatch messages, optional census rows, and the final report.
- `system_upgrade`, `mcp_rpc`, `wedge-capture`, `secrets`: **ABSENT** — privileged categories that belong to the expert team, never to a speed lane.
- `proc`, `service`, `mcp`, `chart`, `council`, `attestation`, `job`: not in my allow set — the allowed categories map to an implicit spawn set of exactly: my six `team_members` (sonic-developer, sonic-coder, sonic-reviewer, sonic-tester, giter, devops), the knowledge lane's explorer/kb-writer support agents (`explore()`/`experience()` are their only surface), and `image-reader` (the `image` category's support agent — image-reader does the actual image resolution behind my view calls). `giter` and `devops` ARE team members but ESCAPE HATCHES: giter (workflow/worktree expertise) only when the internal commit choreography cannot recover a lane; devops rarely invoked by design — the infra/deploy escape hatch. Release-lane git lives outside the sonic loop.

---

## Guidelines

`guidelines_list` / `guideline_read` are a read-only window onto the
guideline corpus that shipped with the RUNNING release (staged from
`docs/guidelines/` into `releases/<v>/guidelines/`) — READ-only, like
everything else in my surface. Consult them before citing doctrine from
memory.
Every feature shipping dormant / config-armed / ceremony-gated / with silently-biting rules OWES a guideline (see docs/guidelines/TEMPLATE.md).
Fixed files are the authority; this tool only reads them.
