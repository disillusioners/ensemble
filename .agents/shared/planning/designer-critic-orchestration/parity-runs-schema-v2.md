# parity-runs.jsonl Schema v2

**Status:** schema-evolution record (phase 4 T2, D7 — architecture-recommendation.md §2 :105-109). The dual-run mechanism is dead; the log file stays as historical evidence.

## v1 schema (from stage2-addendum.md)

Lane enum: `{"direct", "sketcher"}`. One JSON row per page per lane. Fields: `run_id`, `ts`, `page`, `lane`, `latency_s`, `usage` (`prompt_tokens` / `completion_tokens` / `total_tokens`), `truncated`, `gates` (`empty_response` / `finish_reason` / `eof_markers`), `marker_pass`, `model`, `notes` (optional). The pilot gates those rows fed (N ≥ 10, marker-pass floor, truncation/latency/token ceilings) are superseded — see the SUPERSEDED marker on `stage2-addendum.md` (added phase 1 T12).

## v2 schema (this record defines schema v2)

Lane enum: **`{"sketcher", "critic"}`** — `"direct"` is dropped (no longer reachable: designer holds zero `od.*` tokens; the direct lane is deleted from the designer workflow). All other v1 field names are unchanged, so historical rows parse under v2.

## What this means

The schema evolves because the direct lane is dead: any future pipeline-comparison rows would compare the sketcher generation lane against the critic review lane (per-page latency/verdict pairs), not a direct-call baseline. New rows under the designer-critic pipeline would carry `lane: "sketcher"` for generation-envelope rows; a future commission may add review-lane rows.

## Smoke row compatibility

The file's single historical smoke row (mechanism-prove, 2026-10-09) carries `lane: "sketcher"` — valid under both v1 and v2. It is untouched; the v2 header comment is prepended above it as line 1.

## `critic_verdict` is REJECTED as a parity-runs field

Planner decision (finding 8): the critic's verdict lives on the review itself — the verdict block is critic's deliverable and designer's parse surface — NOT as a field appended to `parity-runs.jsonl` rows. A row carrying `critic_verdict` is malformed under v2; the schema validator (and the Nit-2 `parity_runs_v2_schema` test) rejects it. Do not extend rows with verdict payloads; link by `run_id`/`page`/`ts` instead.
