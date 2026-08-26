# PR4 — C3 checkpoint_blobs prune (External Gate) — NEEDS_CHANGES

Date: 2026-08-26 · Branch: feature/langgraph-checkpoint-perf · Range: 80c84219..e3c69b48
Mode: 🔴 Deep-Review (council, 2 councilors: agentic + coding — only 2 canonical models exist; independent item-1 derivation satisfied)
Verdict: **NEEDS_CHANGES** (narrow) — 1 🔴 / 2 🟡 / 7 🟢. FIRST non-approve of the series. Anti-join predicate itself APPROVED by double derivation; the block is a false safety claim + runbook disclosure gap on the concurrency race, not the delete logic.

## THE PROVEN-CLEAN CORE (do not re-review)
Anti-join = EXACT complement of the installed saver's reader keyset, derived independently twice from
langgraph-checkpoint-postgres 3.1.0 source with zero divergence, including the subtle cases:
- Reader keyset = ∪ jsonb_each_text(checkpoint->'channel_versions') per remaining checkpoint
  (postgres/base.py:93-118, aio.py:101-109); _DeltaSnapshot stage-2 seeds identical four-tuple (base.py:280-288);
  delta walk seeds the ANCESTOR's own channel_versions entry (base.py:230, 404-405) and BREAKS at deleted
  ancestors (base.py:400-401) → ancestor-referenced blobs provably covered, nothing reachable past a pruned row.
- ns correlated both sides; thread scoping pinned; version keys always TEXT (f"{next_v:032}.{next_h:016}") — no cast drift.
- NULL/malformed channel_versions catastrophic case (NOT EXISTS matches everything) prevented by jsonb_typeof
  guard → normalizes to 0 refs → ZERO_REFS ERROR outranks DESTRUCTIVE=1, verified pre-arming both paths.
- Structural unreachability (AST dominance + single call site + runtime sentinel + 8-combo matrix), fail-safe
  ordering, E/D isolation, rollback rehearsal (md5 byte-equality), flag call-time reads — all confirmed by both.

## THE BLOCKER (🔴) — aput non-atomicity race, undisclosed + falsely claimed closed
FACT (read from installed source, both councilors): AsyncPostgresSaver.aput default PG14+ path
(psycopg autocommit=True + pipeline, aio.py:82, 280-304) commits blob upsert and checkpoint upsert as SEPARATE
implicit transactions — µs-scale gap. The non-pipeline fallback (aio.py:393-399) wraps conn.transaction() and IS
atomic — hazard is default-path-specific.
Constructible scenario: flags armed → maintenance passes idle gate (precondition, not lock) → task claims mid-job →
graph aput node-boundary commit → E's DELETE snapshots between blob-commit and checkpoint-row-commit → blob deleted
→ checkpoint lands referencing missing (channel, version) → every subsequent aget reconstructs SILENTLY without that
channel — permanent unless restored from the runbook §6 backup.
Why it blocks: checkpoint_prune_real_saver.py:28-30, 524-531 docstring claims aput is "atomic" — FACTUALLY FALSE,
and it is the gate's stated safety argument; runbook §7 frames the race as multi-process only, sending a
single-process operator into an undisclosed data-loss window. Severity split (coding 🔴 vs agentic 🟡) resolved
🔴 at this gate: data-integrity finding with constructible scenario + false safety documentation on a
data-destruction feature; per gate policy (unsure 🟡/🔴 on data integrity → default 🔴).
Required folds: (1) retract docstring claim, cite aio.py:82, 280-304, 393-399; (2) runbook §7 intra-process race
sentence + backup-covers-recovery note; (3) close structurally (PREFERRED: SERIALIZABLE-wrapped DELETE in
delete_blobs_anti_join with serialization-retry — rw-antidependency forces abort, retry sees referencing row)
OR deterministic race demonstrator. Arming blocked UNCONDITIONALLY until folds land (both councilors agree).

## Secondary follow-ups (🟡)
- One-directional concurrency coverage: only asserts NEW blob survives; add pre-existing-referenced-blob survival
  across interleaved multi-turn aputs + destructive prune (byte-equality) — reliably surfaces the race.
- Harness topology: single psycopg conn vs prod asyncpg-pool-vs-psycopg; add separate-pool fixture variant.

## Patterns learned
- Councilor budget note: only 2 canonical models in allowed_models — "up to 4" resolves to 2; governor flagged it.
- Severity-split resolution on data-destruction findings: facts converged, impact constructible, mitigations
  procedural-not-structural, safety claim false → 🔴 regardless of compound probability. Probability arguments
  downgrade severity only when the disclosed controls actually close the mechanism.
- KEY REPO FACT for all future checkpoint work: aput blob+checkpoint commits are NON-atomic on the default
  pipeline path (atomic only on non-pipeline fallback). Any checkpoint-adjacent destructive read/scan must treat
  the µs window as real.
