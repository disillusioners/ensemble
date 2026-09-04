# queue-status decisions (retroactive, round-1)

Branch: `feature/queue-status-missions-badge` (`77e40310`). Retroactive
entry — written after the round-1 BE commit `6d021a8c` (defer-blocked
transparency surface) and the round-1 FE commit `77e40310` (badge
missions-N + defer-blocked warning) so the **why** behind the single-
branch scope is captured for the reviewer. Tree is intentionally
untracked; do NOT commit (the central file-explicit commit phase
follows separately).

## Decisions

### D-QS-1 — Two-change scope on ONE branch (badge truth + defer transparency)
Round-1 ships two changes on one branch: (a) the `missions-N` FE badge
that reads `liveMissionIds` from the mission projection (census frozen
at 23); (b) the `GET /api/queues/defer-blocked` BE surface that exposes
the gate's busy-set witnesses (a paused-instance case the live case
was missing). Both share the "what the operator sees on the queue
indicator" surface — splitting them across two branches would have
required two gate runs for what is one UI affordance. Trade-off
accepted: a single branch failure rolls back both changes; we accept
that cost to ship one cohesive surface.

### D-QS-2 — Display-truth == gate-truth invariant (derived, not reimplemented)
The witness enumeration on `defer-blocked` is composed from the SAME
exported statement builders in
`daemon/repositories/job_queue/_idle_predicate_sql.py` that the gate
path composes (`JobRepository.has_active_non_deferred_work`): the
witness body is **derived** from the gate body constants by unwrapping
the `SELECT EXISTS ( SELECT 1 … )` wrapper at module load time
(`_unwrap_exists_body` — fires a `ValueError` at import if the wrapper
shape ever drifts, blast-radius documented in the docstring). The
FROM/JOIN/WHERE busy-set text is byte-shared; re-implementing the
predicate independently anywhere is the defect this design forbids.
`defer_blocked` is computed from the enumerated witness rows
(`len(witnesses) > 0`), so the boolean agrees with the gate's admission
logic by construction, not by discipline.

### D-QS-3 — §8.5 is the canonical contract home (docs/job-task-system.md)
The FE render-gate (`pending_count > 0`), the holders ordering
(paused-first, ascending `instance_id`), the holder kind vocabulary
(`paused` / `live`), and the severity-shape reading (AMBER / INFO /
RED) are all defined in **`docs/job-task-system.md §8.5`**. Pydantic
schemas (daemon/routers/schemas.py) carry the field descriptions and
the type contracts; the resolver module
(daemon/services/defer_block_resolver.py) carries the implementation;
the route handler (daemon/routers/queues.py) carries the wiring. All
four layers reference §8.5 as the single source of truth for the
operational contract — drift between layers is detected by reviewer
cross-asserts in the docs/code seam.
