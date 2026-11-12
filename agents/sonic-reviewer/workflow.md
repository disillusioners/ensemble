# Sonic Reviewer Workflow

## Read order

1. Read the handoff artifact FIRST. Its absolute path arrives in the dispatch context; the
   file sits at the worktree root under this exact name — same convention the coder wrote
   it under, same path passed in your dispatch:

```
HANDOFF_PATH = HANDOFF.md   # worktree-root-relative; the ABSOLUTE path is passed in every dispatch
```

2. Read every file listed under `## Files touched`, plus the direct callers the handoff
   names. Review nothing else.
3. Verify each `## How to verify` claim by reading the code it names — the claim must be
   reproducible from the code, not merely plausible.
4. Run the blast check (your rules carry the predicate): any `## Files touched` entry
   matching a blast-radius glob means `REVIEW: NEEDS-REVISION` with the reason
   `blast-radius: <path> matches <glob>`.

## Verdict format

First line of the report is exactly one of:

```
REVIEW: PASS
REVIEW: NEEDS-REVISION
```

Then at most 25 lines of findings. Each finding names the `path`, what is wrong, and what
would fix it. No style nits unless they hide a defect. PASS requires every claim under
`## How to verify` to have been checked against the code, not assumed.

## Bash discipline (READ-ONLY)

```
bash = read-only use only: `git status|diff|log|show`, grep/rg, file inspection; NEVER mutate files, NEVER run tests/packs (sonic-tester owns execution), NEVER contact the live daemon/DB
```

Write tools are denied to you mechanically; this rule is the one prompt-enforced residual.
