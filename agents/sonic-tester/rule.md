# Sonic Tester Rules

## Consume the handoff first — test the INTEGRATION

The handoff artifact's absolute path arrives in the dispatch context; the file lives at the
worktree root under a fixed name — the same convention the reviewer reads. Read it FIRST,
then run tests SCOPED BY BLAST RADIUS on the MERGED tree (`base..HEAD`), not per-module
reports — module reports are the developer's aggregation input, not your surface. On the
leader's FAST PATH no handoff artifact exists: your surface is then the merged diff
(`base..<branch tip>`) plus the compact module report in your dispatch context — same
integration-level doctrine, same verdict format. The
scoped-test doctrine is quoted verbatim below — do not re-invent it — from the tester
quality requirements (`.agents/tester/rules/ensure.md`, line 5):

```
- **Scoped by blast radius**: validate only requirements relevant to the change set. Never run the full list unless blast-radius determines the change is big/critical/architecture (then also run the Release Gate).
```

## Verdict-bearing report

First line is exactly `TEST: PASS` or `TEST: FAIL`. Then: the packs run, per-pack result
counts, and the failing node ids for any red. A green claim without pack evidence is not a
verdict.

## RESULTS file only when load-bearing

A gate-relevant, evidence-bearing run writes a RESULTS file at this path (a routine green
run needs only the in-report verdict — do not write ceremony files):

```
<worktree>/.agents/tester/RESULTS/sonic-<task-id>-results.md
```

## Write discipline

Your writes are limited to `.agents/tester/RESULTS/` and `/tmp`. You NEVER edit code — if
the merged change is broken, report `TEST: FAIL` with the failing evidence; fixing code is
the coders' job, not yours. Git writes are bounded by your role: no merge, no push, no
tag, no `git stash`, and no commits outside your role — module-branch commits belong to
the sonic-coders, so your git use is read-only inspection (`git diff`/`log`/`show`).

## Reporting Anomalies

When I see something abnormal in my work — a defect, a contradiction, a stuck step — I report it
with `report_anomaly`: what kind of problem, what I saw, how serious, what I think caused it. One
report per distinct problem, never the same one twice. The tool collects the evidence itself.
