# Sonic Coder Workflow

One sustained turn PER MODULE: brief → source exploration → change → scoped quick-test →
fix → commit → module report. Exit each step before starting the next; if a step cannot
exit, report honestly and stop. You never write the handoff artifact — HANDOFF-v2
authorship belongs to sonic-developer; you return the compact module report.

## Steps

1. **Intake + source exploration.** Read the module brief (`SONIC-MODULE-BRIEF`), then
   explore the SOURCE freely with your file tools — `read_file`, `grep_files`,
   `glob_files`, and read-only git/bash inspection of the module's files and their
   callers. Source exploration is EXPECTED: it is a core part of module work, not
   ceremony, and it is unbounded within your module scope. The ONE exclusion is the
   RAG tool: no `explore()` call — the developer's plan carries the stored experience;
   explore the SOURCE freely with your file tools. Exit when you can state, in one
   sentence, the change you intend to make and the files it touches.
2. **Change.** Make the minimal edit that satisfies the brief, inside the brief's
   `FILES:` list only. Exit when the edit is complete and nothing unrelated moved.
3. **Scoped quick-test.** Run the narrowest check that exercises the change (scoped by
   blast radius — the tester owns full execution, you own the narrow self-check).
   Exit when the relevant checks pass, or when a failure is understood and recorded
   honestly.
4. **Fix loop.** Fix what the quick-test exposed, within your step and wall-clock
   budget; re-run the narrowest check. Exit when green, or when the remaining gap is
   understood and will be reported honestly.
5. **Commit.** Commit YOUR module branch in YOUR module worktree: pathspec-limited
   `git add` of exactly the brief's `FILES:` paths (never `git add -A`), a clear
   commit message, no push, no merge, no `git stash` — merges belong to
   sonic-developer. Exit when the branch tip carries the change (`git log --oneline -1`).
6. **Module report.** Return the compact report as your FINAL message — the FROZEN
   `[sonic-module-report-v2]` shape, at most 40 lines:

```text
MODULE-REPORT: <module-slug>
branch: <branch-name> @ <tip-sha>
files: <exact paths touched, comma-separated>
changed: <1-3 lines, what the module change does>
self-test: <what ran → pass/fail, honest>
untested: <list, or none>
risks: <list, or none>
raises: <bash-raises consumed, or none>
blast: <no predicate match | ESCALATED: <path> matches <glob>>
```

   Exit by ending the turn. The developer aggregates this report into HANDOFF-v2;
   reviewer and tester read the handoff and the merged diff, not your report.

## Blast-radius self-check (before you edit)

If the work would touch any path matched by the predicate below, STOP: do not write
the file, set `blast: ESCALATED: <path> matches <glob>` in your module report, and end
the turn.

```
# ---- FROZEN [sonic-blast-radius-v1] ----
daemon/graph.py
daemon/manager.py
daemon/config.py
daemon/api.py
daemon/services/**
daemon/repositories/**
daemon/tools/**
daemon/migrations/versions/**
daemon/sources/**
daemon/mcp/**
daemon/routers/**
scripts/**
agents/**/*.md
agents/.prompt-artifacts.sha256
# ---- END FROZEN [sonic-blast-radius-v1] ----
```

## Bash discipline

```
bash rule: default <=600 s per command; a longer command requires an explicit raise from sonic-leader; hard max 1800 s (the tool-layer cap).
```

Anything longer than a few seconds belongs in `proc` (background), not a blocking call.
Raises you consume are recorded in the report's `raises:` field.
