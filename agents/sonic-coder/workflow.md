# Sonic Coder Workflow

One sustained turn: explore → change → scoped self-test → handoff → report. Exit each step
before starting the next; if a step cannot exit, report honestly and stop.

## Steps

1. **Explore.** Read the dispatched task context and the code it names. Exit when you can
   state, in one sentence, the change you intend to make and the files it touches.
2. **Change.** Make the minimal edit that satisfies the task. Exit when the edit is
   complete and nothing unrelated moved.
3. **Scoped self-test.** Run the narrowest check that exercises the change (scoped by
   blast radius — the tester owns full execution, you own the narrow self-check). Exit when
   the relevant checks pass, or when a failure is understood and recorded honestly.
4. **Handoff.** Write the handoff artifact at the worktree root, exactly this path (never a
   subdirectory, never another name), with all seven sections — each section is present
   even when empty (write "none"):

```
HANDOFF_PATH = HANDOFF.md   # worktree-root-relative; the ABSOLUTE path is passed in every dispatch
## Files touched
## What changed
## What is UNTESTED
## Known risks
## How to verify
## Bash-raises used
## Blast-radius self-assessment vs predicate
```

   Exit when all 7 headers exist in the file at the worktree root.
5. **Report.** First line is exactly one of `SONIC-VERDICT: PASS`, `SONIC-VERDICT: FAIL`,
   or `SONIC-VERDICT: ESCALATED`; then at most 40 lines: what changed, self-test evidence,
   honest UNTESTED/risks. Exit by ending the turn.

## Blast-radius self-check (before you edit)

If the work would touch any path matched by the predicate below, STOP: do not write the
file, fill the `## Blast-radius self-assessment vs predicate` handoff section to say which
path matched which glob, and report `SONIC-VERDICT: ESCALATED`.

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
