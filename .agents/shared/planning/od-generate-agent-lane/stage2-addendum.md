# Stage 2 Addendum — Sketcher Lane Dual-Run Pilot (od-generate-agent-lane)

**Status:** PROVISIONAL GATES — leader-set, verbatim from the Stage 2 dispatch (2026-10-09).
**Scope:** adjudication criteria for the dual-run pilot only. Nothing here deprecates either
lane; direct `od.generate` stays available regardless of pilot outcome.

## What the pilot is

For pilot pages, the designer runs BOTH lanes on the same page-brief:

- `direct` — designer's own `od.compose_brief` → `od.generate` → `od.lint` → `od.save`
- `sketcher` — one sketcher child per page, identical brief, envelope report back

One JSON row per lane per page is appended to `parity-runs.jsonl` in this directory
(schema pinned in `agents/designer/workflow.md` § Dual-Run Pilot and asserted by
`tests/unit/agents/`).

## Row schema (exact field names)

```
run_id      str
ts          str (ISO-8601)
page        str
lane        "direct" | "sketcher"
latency_s   number
usage       {prompt_tokens: int, completion_tokens: int, total_tokens: int}
truncated   bool
gates       {empty_response: "pass"|"fail", finish_reason: "pass"|"fail", eof_markers: "pass"|"fail"}
marker_pass bool
model       str
notes       str (optional)
```

## Provisional pilot gates (VERBATIM from the leader dispatch)

- **N >= 10 pages**
- **marker_pass >= 95% AND within 5pp of direct**
- **truncated <= direct + 5pp**
- **median latency <= 1.5x direct**
- **tokens/page <= 1.3x direct**

These gates live HERE, not in agent prose. Agent prompt surfaces carry the schema and the
pointer to this addendum only. Any gate change is an edit to this file, journaled in the
feature's planning directory.

## Adjudication note

The pilot compares lane parity, not absolute quality: `od.lint` verdicts and the post-save
visual QA loop (designer-side, conformance-loop budget <=3 iterations) remain the quality
bar for shipped pages under either lane. The gates above decide whether the sketcher lane
earns default routing for multi-page runs; deprecation of any lane is a separate decision,
out of Stage 2 scope.
