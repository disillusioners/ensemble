# ensure-validation Lessons — Increment 2

Date: 2026-08-01

- The concurrency atomic pack remains the authoritative validation seam for both deadlock integrity and the no-sync-DB-on-event-loop requirement; one run produced 66 passes and 19 skips.
- Increment 2 removed `_admitted_task_carve_out_sql`; a repository-wide scoped grep confirmed no stale references.
- Broad suite reports retain pre-existing SQLite migration incompatibility failures. They must remain distinguished from new Increment 2 regressions; this run found no new failures attributable to Increment 2.
- `ensure.md` requires pack execution and timeout wrappers; the direct concurrency command was run with `timeout 300`, while static checks were appropriately performed as bounded shell checks.
