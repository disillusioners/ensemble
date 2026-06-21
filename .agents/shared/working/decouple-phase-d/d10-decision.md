# D10 Critical Decision — Column Drop Strategy

## The Tension
- Leader says: "Gate this migration behind USE_DEPENDENCY_BUS=ON so it only runs when the bus is active"
- D8 just flipped USE_DEPENDENCY_BUS default to ON
- Therefore: gating behind the flag = always-on = columns drop on EVERY deployment
- This DESTROYS the CM rollback path (USE_DEPENDENCY_BUS=false → CM path has no waiting_for data)

## Reviewer Guidance (§7.2)
"Make the column drop a two-step migration:
- D10a (Phase D): Add dependency_watchers table, run bus in shadow, but do NOT drop columns. Keep waiting_for as dead-but-present.
- D10b (follow-up release): After 2+ weeks of clean bus operation in production, drop the columns."

## My Decision (responsible engineering)
D10 will be implemented as:
1. A migration FILE (`20260621_000002_drop_legacy_completion_columns.sql`) — written but NOT auto-applied
2. A manual procedure documented in the migration header + docs
3. The `_ensure_postgres_drop_legacy_columns()` hook in manager.py will be a NO-OP with a log warning saying "column drop migration exists but is not auto-applied — run manually after verifying 2+ weeks of clean bus operation"
4. The SQLModel model columns (`waiting_for`, `children`) will be kept (dead-but-present) so the CM rollback path still works

This follows the reviewer's §7.2 two-step recommendation and preserves the rollback path. The actual column drop is deferred to a follow-up release (D10b).
