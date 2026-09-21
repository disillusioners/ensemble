# result_summary Seam: Test-Passes / Demo-Null Divergence (2026-09-21)

Discovered in the Part B re-run on the STABLE demo daemon (worker dbd2abb2, 16:22–16:28 UTC). Evidence: RESULTS/2026-09-21-demo-v0.13.9-final-verification.md (RE-RUN section).

## Finding
On a verified-stable v0.13.9 demo daemon, two clean public-API `job_type=task` completions (one substantive answer, junk-classifier refuted) both produced:
- terminal SSE event with `result_summary: null` (and `error_message/outcome` null),
- final job record `status=completed, result_summary=null`,
- NO `job-completed` row in the DB `event` table (only `message_received` + `instance_lifecycle`),
- ZERO notifications on the global `/api/notifications/stream`.

Meanwhile the premature-terminal arm of the same fix IS live and working (single terminal event at actual completion). So the v0.13.9 fix is HALF-live on demo's real path.

## Divergence point
`child_reports` (instance COMPLETED) → observer `finalized job … status=completed, released 1 lock(s)` → streaming router emits terminal event. Nothing between observer finalize and emission stamps `result_summary`, and no `job-completed` event kind is persisted.

## Why the gate missed it
Phase-D acceptance pack `job_completion_acceptance_test` (29/29 PASS) proves the seam under its own harness (real resolver/repos but tool-layer conditions) — it does not drive the actual streaming-router emission + DB persistence path demo uses. A test that passes while the production path emits null = the acceptance test must be extended to drive the REAL emission path (SSE payload + `event`-table row for a public-API task job).

## Lessons
1. Acceptance packs for event-payload fixes must assert on the REAL emission surface (SSE payload + persisted event row), not only on repository/publisher seams.
2. Null-valued fields in a terminal event are indistinguishable from "feature absent" — when verifying an event-payload fix, assert each fixed field non-null on the emitted artifact itself.
3. Controlled-retry-as-experiment (substantive vs junk answer) cleanly refuted the junk-classifier-gate hypothesis in one extra job — cheap discriminator, worth reusing.
