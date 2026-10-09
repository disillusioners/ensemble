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

## Campaign notes (2026-10-09 close-out)

Three pilot-runner rules land here, not in agent prose, to keep the agent surface clean
and the adjudication authoritative.

1. **First real campaign page must use `max_tokens >= 16000`.** 8K saturates the vision
   lane: `finish_reason=length` both attempts (mechanism re-smoke row 2026-10-09 14:08
   recorded `completion_tokens=8000` on both tries). The trigger is upstream-side; the
   pilot knob is `OPENAI_MODEL_VISION` family max-tokens, set per design-lane call.
2. **Campaign aggregation MUST exclude mechanism-smoke rows.** Filter rule:
   `run_id ~ 'sketcher-*smoke*'` OR `notes ~ 'smoke'`. The row committed in this close-out
   is smoke; it must never credit toward the N>=10 gate. Add new filter keys only with a
   planning-dir journal entry.
3. **Pilot gates restated verbatim (no semantic change; the body of this addendum above
   is authoritative).** `N >= 10 pages`, `marker_pass >= 95% AND within 5pp of direct`,
   `truncated <= direct + 5pp`, `median latency <= 1.5x direct`, `tokens/page <= 1.3x
   direct`. The addendum is the single source of truth — agent prompt surfaces carry the
   schema and pointer only, never the gate values.

## Deferred items (2026-10-09 close-out)

Four items are NOT part of the Stage 2 scope. They are recorded so the next commission
on this lane has a clean backlog and the campaign adjudicator is not surprised by them.

(a) **`_OD_GENERATE_WALL_CLOCK_CAP_S=420` cap-math gap** (EXTENDED from review-green #3).
The wall-clock cap fires only BETWEEN attempts (`daemon/services/llm_failover.py:848-852`),
not as an outer bound on the whole retry loop. Effective worst case is ~4x the inner
timeout (observed 489s at 32K max_tokens). This is a pre-existing structural concern
shared with the designer's direct `od.generate` path; the sketcher lane inherits it,
does not introduce it. Future-commission candidate; not a pilot gate.

(b) **Sketcher pipeline paraphrase tightening** (review-green #4). The pipeline prose
calls a few steps "rewrite" / "rephrase" where the current implementation copies. Not
load-bearing for the pilot (no semantic divergence, no tool-surface change); cleanup
pass for the post-pilot hardening cycle.

(c) **`design.capture_mockup` tool** (scoped follow-up slice, category `design`).
Wraps `agent-browser` open+screenshot into a single tool that drops the result into
`image_save` (or a designer-artifact row) and returns a `view_url`. Today the procedure
lives only in `agents/designer/tools_note.md:55-110` as a manual recipe; elevating it
to a tool is a small, well-bounded follow-up that the sketcher lane will not block on.

(d) **12 pre-existing test failures** (4 in `tests/unit/test_plugin_subsystem*.py` + 8
in agent files) remain upstream debt. These are base-proven failures recorded on
2026-10-07 and 2026-10-09; they are not introduced by the sketcher lane and are not
pilot gates. They are listed here so the next reviewer does not mistake them for
regressions from this branch.

## Trap note (2026-10-09 close-out, occ-3 family)

To attribute the model an agent-loop run used, **read the spawn log line**, not
`OPENAI_MODEL` from `.env`. The model knob on the spawn line is the authoritative
`model=<x> source=llm_model` record. `OPENAI_MODEL` is the default chat pool (currently
`agentic`); the design lane knob is `OPENAI_MODEL_VISION` (currently `vision`). Conflating
the two is the occ-3 family — the symptom is "the lane came back with the wrong model's
voice" and the cause is reading the wrong env var. The sketcher lane is a generation
worker, so it must record the spawn log line in the parity row (the `model` field) and
NOT the chat-pool default.
