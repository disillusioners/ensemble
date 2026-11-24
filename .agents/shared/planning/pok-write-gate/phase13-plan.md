# Wave 2 · Phase 13: Staged Release

## Objective
Stage the release: bump version (slot derived LIVE at bump time), stage with `ENSEMBLE_ROLLBACK_SAFE=1`, tag-to-tag delta disclosure, push tags latest-first (f13a4f59e convention), NO promote. Add the Emperor-required HONEST DISCLOSURE about kb-writer's verify family.

## Tasks
| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Bump version (`scripts/bump_version.py`, slot derived LIVE) | All phases | Version bumped |
| 2 | Stage (`scripts/upgrade/stage.sh` with `ENSEMBLE_ROLLBACK_SAFE=1`, tag-to-tag delta) | T1 | stage.sh success |
| 3 | Push tags latest-first (f13a4f59e convention) | T2 | Tags pushed |
| 4 | HONEST DISCLOSURE in release notes: kb-writer's verify family gets no NEW captures from that path; capture moves to the 4 operational agents | T2 | Release-note text present |
| 5 | NO promote — disclose staged state; Emperor's 3-factor ceremony owns the flip | T2 | Disclosure note |

## Coupling
- **Depends on:** ALL phases.

## Risks
- Version slot derived LIVE; rollback-safe explicit; tags latest-first; NO promote.
- Fence carve-out: the staged-release tag push is SANCTIONED STAGING under ruling (7) — push = sanctioned staging, PROMOTE stays the Emperor's ceremony.
- Zero POK-schema migration preference — verify no new `.sql` unless argued (ledger additive table).

## Exit Criterion
Version bumped; release staged with rollback-safe; tags pushed latest-first; honest disclosure present; no promote; migration preference honored.
