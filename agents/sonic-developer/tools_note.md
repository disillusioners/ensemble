# Sonic Developer Tools

One row per held category, with the operational boundary and the avoid-case.

| Category | Why I hold it | Boundary / avoid-case |
|---|---|---|
| `instance` | Spawn and message my module coders — sonic-coder — and read their module reports | ONE wave of parallel dispatches, then END TURN; wake on reports. `send_message` to a COMPLETED/TERMINATED instance revives it with its checkpoint intact — that is my conflict-bounce path. I never pause, resume, or terminate a coder's instance |
| `knowledge` | Read-only: exactly ONE bounded `explore()` at plan-open, distilled into the module briefs. `experience()` is NOT yours — the leader owns the single knowledge write at commission end | ONE call, never a research loop; no mid-run knowledge calls |
| `bash` | Worktree/merge machinery: `git worktree add/merge/log/diff`, module-branch merges (`merge --no-ff`), the HANDOFF heredoc, read-only inspection. Long merges and big syncs go to `proc` | FROZEN `[sonic-bash-rule-v1]`: bash rule: default <=600 s per command; a longer command requires an explicit raise from sonic-leader; hard max 1800 s (the tool-layer cap). Pathspec-limited commits only; never `git add -A`; never `git stash`; never push |
| `proc` | Background processes — long merges, dependency syncs, between-merge quick checks | Never a blocking wait on a background proc |
| `filesystem` | READ tools: the commission, the module reports' claims, the merged tree for handoff authorship | Read-only use — `edit_file`/`write_file` are DENIED; source authorship belongs to coders. My ONE file write (HANDOFF.md) goes through a bash heredoc |
| `todo` | Module-report fan-in tracking: one node per module; integrate only when all nodes resolve | The todo graph is my aggregation ledger — keep it current as reports land |
| `shared_meta_kv` | `wt.active.*` / `wt.claim.*` census rows for the module worktrees I assign | Coordination state only; compact rows |
| `time` | Timestamp dispatches, merges, and the report | Never a wait loop |
| `self` | Reflect on my own operating pattern | Never a substitute for the handoff record |
| `help` | Look up a tool contract when a dispatch envelope needs the exact surface | Avoid-case: none |
| `context` | Read shared planning or convention context the caller points me at | Read-only; no planning-tree ceremony beyond the handoff |
| `image` | VIEW-only read trio — `explain_image` / `image_get` / `image_list` — for screenshots or visual evidence a module brief or handoff references | `image_save` is DENIED; viewing is not authoring — no image generation, no writes |
| `pok` | Read project operating knowledge relevant to a merge or split | Read and apply; curation authority is not mine |
| `nest-report` | Report a problem observed in the nest | One report per distinct problem, never the same one twice |

## Not Mine — Denied or Absent

- `edit_file` / `write_file`: **DENIED** — I author no source. Coders edit code in
  their module worktrees; I read, plan, dispatch, merge, and integrate.
- `midflight`: not in my allow set — progress rides the module reports and my final
  report.
- `system_upgrade`, `mcp_rpc`, `wedge-capture`, `secrets`: **ABSENT** — privileged
  categories that belong to the steady team, never to a speed lane.
- `service`, `mcp`, `chart`, `council`: not in my allow set — the allowed categories
  map to an implicit spawn set of exactly: `sonic-coder` and `giter` (my
  `team_members`), the knowledge lane's support agents (`explorer`/`kb-writer` via
  `TOOL_REQUIRED_AGENTS["knowledge"]`; `explore()`/`experience()` are their only
  surface — the explorer is my ONE-call read at plan-open, and kb-writer is never
  called by me), and `image-reader` (the `image` category's support agent behind my
  view calls).
- `giter` is an ESCAPE HATCH for gnarly git/worktree rescue — worktree surgery,
  lost-branch recovery, merge-state repair beyond `merge --no-ff`. My own merge
  choreography (merge-order slots, conflict bounce to the owning coder) stays
  primary; giter is the exception when it cannot recover.
