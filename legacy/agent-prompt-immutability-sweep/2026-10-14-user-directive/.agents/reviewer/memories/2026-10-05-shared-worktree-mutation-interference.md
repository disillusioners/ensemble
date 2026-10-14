# Shared-Worktree Mutation Interference (2026-10-05, chart-render-opt-in review)

**Lesson:** When multiple review workers share ONE worktree and one is authorized to do
break→RED→revert mutation spot-checks, concurrent workers can observe the mutation as
transient "test flakiness" (false alarm).

**Incident:** w-core saw a single pytest run with 6 failures (all default-timeout /
`render_image: bool = False` assertions), then 4+ clean 94/94 runs. w-audit's Mutation B
(flip default False→True) produced the IDENTICAL 6-test failure set in the same window.
Root cause: overlap of w-audit's mutation window with w-core's run — not infra, not product.

**Rule going forward:** In dispatch plans that authorize mutation spot-checks in a shared
worktree, either (a) serialize: dispatch the mutation-authorized worker alone, or
(b) brief sibling workers: "transient failures whose set matches another worker's
declared mutation targets are expected — re-run before flagging."

**Silver lining:** the same mutation was caught independently by two workers' runs —
good evidence the suites genuinely discriminate.
