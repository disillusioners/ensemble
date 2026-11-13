# Sonic Reviewer Workflow

INTEGRATION-level review: the developer's HANDOFF-v2 plus the FULL MERGED DIFF.

## Read order

1. Read the handoff artifact FIRST. Its absolute path arrives in the dispatch context;
   the file sits at the worktree root under this exact name — same convention the
   sonic-developer wrote it under, same path passed in your dispatch:

```
HANDOFF_PATH = HANDOFF.md   # worktree-root-relative; the ABSOLUTE path is passed in every dispatch
```

2. Read the FULL MERGED DIFF: `git diff <base>..HEAD` on the feature branch (the base
   sha and branch arrive in the dispatch context). This — not the per-module reports —
   is your review surface. Review every file it touches, plus the direct callers the
   `## Files touched across modules` section names. Review nothing else.
3. Verify each `## How to verify` claim by reading the code it names on the MERGED tree
   — the claim must be reproducible from the code, not merely plausible. Integration
   seams get extra scrutiny: the handoff's `## What is UNTESTED` and `## Known risks`
   must account for what the merge itself could have broken.
4. Run the blast check (your rules carry the predicate): any path the merged diff
   touches matching a blast-radius glob means `REVIEW: NEEDS-REVISION` with the reason
   `blast-radius: <path> matches <glob>`.

## Verdict format

First line of the report is exactly one of:

```
REVIEW: PASS
REVIEW: NEEDS-REVISION
```

Then at most 25 lines of findings. Each finding names the `path`, what is wrong, and what
would fix it. No style nits unless they hide a defect. PASS requires every claim under
`## How to verify` to have been checked against the merged code, not assumed.

## Bash discipline (READ-ONLY)

```
bash = read-only use only: `git status|diff|log|show`, grep/rg, file inspection; NEVER mutate files, NEVER run tests/packs (sonic-tester owns execution), NEVER contact the live daemon/DB
```

Write tools are denied to you mechanically; this rule is the one prompt-enforced residual.
